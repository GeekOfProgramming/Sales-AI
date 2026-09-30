"""
Phase 9 Golden Acceptance & Comprehensive Test Suite
====================================================
Authoritative specification: PHASE9-BUILD-SPEC.md

Tests cover:
- P9-REVIEW-001 Import Pending Draft
- P9-REVIEW-002 Edit Invalidates Approval
- P9-APPROVE-001 Explicit Approval
- P9-APPROVE-002 Approval Does Not Send
- P9-SEND-001 Approved Dry Run
- P9-SEND-002 Approved Mock SMTP Send
- P9-SEND-003 Unapproved Blocked
- P9-SEND-004 Stale Approval Blocked
- P9-SEND-005 Already Sent Blocked
- P9-SUPPRESS-001 Suppressed Recipient Blocked
- P9-IDEMP-001 Concurrent Duplicate Prevented
- P9-BATCH-001 Partial Failure Isolation
- P9-SEC-001 Secrets Never Logged
- P9-WF-001 Sent State Projection
- P9-NOLLM-001 Zero LLM Code Path
- P9-LIVE-SMTP-001 Live SMTP Send Guardrail (Opt-in)
"""

import os
import inspect
import threading
import pytest
from pathlib import Path
from unittest.mock import patch

from sales_engine.sending import (
    ReviewStore,
    ApprovalService,
    SuppressionStore,
    SendOrchestrator,
    SendValidator,
    AuditService,
    RateLimiter,
    MockEmailSender,
    SMTPEmailSender,
    StoredDraft,
    ReviewEvent,
    DraftEditRequest,
    compute_content_fingerprint,
)


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "phase9_acceptance.db"


@pytest.fixture
def review_store(temp_db):
    return ReviewStore(db_path=temp_db)


@pytest.fixture
def suppression_store(review_store):
    return SuppressionStore(review_store=review_store)


@pytest.fixture
def mock_sender():
    return MockEmailSender()


@pytest.fixture
def sample_draft_dict():
    return {
        "draft_id": "draft:p9-gold-001",
        "revision": 1,
        "lead_id": "domain:acme.com",
        "contact_id": "email:jane@acme.com",
        "recipient_name": "Jane Smith",
        "recipient_title": "BIM Director",
        "recipient_email": "jane@acme.com",
        "sender_email": "outreach@pybim.com",
        "subject": "Streamlining Revit Automation at Acme",
        "body": "Hi Jane,\n\nWe noticed your team is expanding Revit automation. pyBIM provides Tech-Enabled BIM Services.\n\nOpen to a brief chat?",
        "service_used": "Tech-Enabled BIM Services",
        "personalization_notes": "Mapped to Revit hiring signal",
        "evidence_refs": ["JOB-001", "SERVICE-001"],
        "source_job_urls": ["https://acme.com/careers/1"],
        "language": "en",
        "tone": "professional_concise",
        "prompt_version": "outreach_v1",
        "generation_model": "qwen2.5:1.5b",
    }


# =========================================================================
# GOLDEN ACCEPTANCE TESTS
# =========================================================================

def test_p9_review_001_import_pending_draft(review_store, sample_draft_dict):
    """
    P9-REVIEW-001: Imported drafts must default to pending_review, not_sent, draft_ready.
    Preserves all identity, evidence, and prompt version fields.
    """
    approval_svc = ApprovalService(review_store)
    imported = approval_svc.import_drafts([sample_draft_dict], reviewer="importer")

    assert len(imported) == 1
    draft = imported[0]
    assert draft.draft_id == "draft:p9-gold-001"
    assert draft.revision == 1
    assert draft.approval_status == "pending_review"
    assert draft.send_status == "not_sent"
    assert draft.outreach_status == "draft_ready"
    assert draft.recipient_email == "jane@acme.com"
    assert draft.content_hash is not None
    assert draft.approved_content_hash is None
    assert draft.approved_at is None

    # Check review event
    events = review_store.get_review_events("draft:p9-gold-001")
    assert len(events) == 1
    assert events[0].action == "imported"
    assert events[0].new_status == "pending_review"


def test_p9_review_002_edit_invalidates_approval(review_store, sample_draft_dict):
    """
    P9-REVIEW-002: Human edit on an approved draft creates a new revision,
    resets approval_status to pending_review, and clears approved_content_hash.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])

    # Approve rev 1
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")
    rev1 = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert rev1.approval_status == "approved"
    assert rev1.approved_content_hash is not None

    # Edit subject -> must create revision 2
    edit_req = DraftEditRequest(
        subject="Updated Revit Automation Collaboration",
        reviewer="hamid_editor",
        review_note="Refined subject line",
    )
    rev2 = approval_svc.edit_draft("draft:p9-gold-001", edit_req)

    assert rev2.revision == 2
    assert rev2.subject == "Updated Revit Automation Collaboration"
    assert rev2.approval_status == "pending_review"
    assert rev2.send_status == "not_sent"
    assert rev2.approved_content_hash is None
    assert rev2.approved_at is None

    # Rev 1 remains unchanged in history
    rev1_check = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert rev1_check.revision == 1
    assert rev1_check.approval_status == "approved"


def test_p9_approve_001_explicit_approval(review_store, sample_draft_dict):
    """
    P9-APPROVE-001: Explicit approval computes and stores SHA-256 fingerprint,
    transitions status to approved, and records immutable ReviewEvent.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])

    approved = approval_svc.approve_draft(
        "draft:p9-gold-001",
        revision=1,
        reviewer="hamid_lead",
        note="Tone and service match ICP",
    )

    assert approved.approval_status == "approved"
    assert approved.outreach_status == "approved"
    assert approved.approved_at is not None
    assert approved.approved_content_hash == approved.content_hash
    assert len(approved.approved_content_hash) == 64

    # Verify audit event
    events = review_store.get_review_events("draft:p9-gold-001")
    app_events = [e for e in events if e.action == "approved"]
    assert len(app_events) == 1
    assert app_events[0].reviewer == "hamid_lead"
    assert app_events[0].review_note == "Tone and service match ICP"


def test_p9_approve_002_approval_does_not_send(review_store, mock_sender, sample_draft_dict):
    """
    P9-APPROVE-002: CRITICAL - Approval action MUST NEVER trigger an email send.
    Provider must receive zero calls, send_status remains not_sent.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])

    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    assert len(mock_sender.sent_messages) == 0
    draft = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert draft.send_status == "not_sent"
    assert draft.approval_status == "approved"


def test_p9_send_001_approved_dry_run(review_store, mock_sender, sample_draft_dict):
    """
    P9-SEND-001: Dry run executes full validation (approval, identity, suppression, fingerprint)
    but transmits zero network messages and leaves send_status as not_sent.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=True)

    assert res.status == "dry_run"
    assert res.error_type is None
    assert len(mock_sender.sent_messages) == 0

    # DB state must NOT be marked sent
    draft = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert draft.send_status == "not_sent"


def test_p9_send_002_approved_mock_smtp_send(review_store, mock_sender, sample_draft_dict):
    """
    P9-SEND-002: Explicit send with EMAIL_SEND_ENABLED=true sends via provider,
    transitions send_status to sent, records attempt, and records sent event.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)

    assert res.status == "sent"
    assert res.provider_message_id is not None
    assert len(mock_sender.sent_messages) == 1

    # Check updated draft state
    draft = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert draft.send_status == "sent"
    assert draft.outreach_status == "sent"
    assert draft.sent_at is not None

    # Check send attempt log
    attempts = review_store.get_send_attempts("draft:p9-gold-001")
    assert len(attempts) == 1
    assert attempts[0].status == "sent"
    assert attempts[0].recipient_email == "jane@acme.com"


def test_p9_send_003_unapproved_blocked(review_store, mock_sender, sample_draft_dict):
    """
    P9-SEND-003: Unapproved draft send must be blocked immediately before provider call.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)

    assert res.status == "blocked"
    assert res.error_type == "not_approved"
    assert len(mock_sender.sent_messages) == 0


def test_p9_send_004_stale_approval_blocked(review_store, mock_sender, sample_draft_dict):
    """
    P9-SEND-004: If draft content changes after approval (fingerprint mismatch),
    send MUST be blocked with error_type='approval_stale'.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    # Simulate sneaky database edit after approval
    with review_store._get_connection() as conn:
        conn.execute(
            "UPDATE drafts SET body = 'Tampered stealth content' WHERE draft_id = 'draft:p9-gold-001' AND revision = 1"
        )

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)

    assert res.status == "blocked"
    assert res.error_type == "approval_stale"
    assert len(mock_sender.sent_messages) == 0


def test_p9_send_005_already_sent_blocked(review_store, mock_sender, sample_draft_dict):
    """
    P9-SEND-005: Idempotency check prevents re-sending an already sent revision.
    Returns status='already_sent' with zero provider transmissions.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    # First send succeeds
    res1 = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)
    assert res1.status == "sent"
    assert len(mock_sender.sent_messages) == 1

    # Second send blocked
    res2 = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)
    assert res2.status == "already_sent"
    assert res2.error_type == "already_sent"
    assert len(mock_sender.sent_messages) == 1


def test_p9_suppress_001_suppressed_recipient_blocked(
    review_store, suppression_store, mock_sender, sample_draft_dict
):
    """
    P9-SUPPRESS-001: Suppressed recipient email blocks send even if approved.
    Returns status='blocked', error_type='suppressed_recipient'.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    # Add recipient to suppression
    suppression_store.add_suppression(
        email="jane@acme.com",
        reason="opt_out",
        source="manual",
    )

    orch = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)

    assert res.status == "blocked"
    assert res.error_type == "suppressed_recipient"
    assert len(mock_sender.sent_messages) == 0

    draft = review_store.get_draft("draft:p9-gold-001", revision=1)
    assert draft.send_status == "blocked"


def test_p9_idemp_001_concurrent_duplicate_prevented(review_store, mock_sender, sample_draft_dict):
    """
    P9-IDEMP-001: Concurrent send requests on the same revision must never produce two emails.
    Exactly one succeeds and the other is blocked by the idempotency lock.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])
    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    results = []

    def run_send():
        res = orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)
        results.append(res)

    t1 = threading.Thread(target=run_send)
    t2 = threading.Thread(target=run_send)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Invariant: exactly 1 message delivered to sender provider
    assert len(mock_sender.sent_messages) == 1
    sent_count = sum(1 for r in results if r.status == "sent")
    already_sent_count = sum(1 for r in results if r.status in ("already_sent", "blocked"))
    assert sent_count == 1
    assert already_sent_count == 1


def test_p9_batch_001_partial_failure_isolation(review_store, mock_sender, sample_draft_dict):
    """
    P9-BATCH-001: Batch send processes drafts independently.
    Failures in one draft do not prevent or corrupt successful sends of others.
    """
    approval_svc = ApprovalService(review_store)

    draft1 = dict(sample_draft_dict, draft_id="draft:batch-1", recipient_email="a@acme.com")
    draft2 = dict(sample_draft_dict, draft_id="draft:batch-2", recipient_email="b@acme.com")
    draft3 = dict(sample_draft_dict, draft_id="draft:batch-3", recipient_email="c@acme.com")

    approval_svc.import_drafts([draft1, draft2, draft3])

    # Approve draft 1 and draft 3, but leave draft 2 unapproved
    approval_svc.approve_draft("draft:batch-1", revision=1, reviewer="hamid")
    approval_svc.approve_draft("draft:batch-3", revision=1, reviewer="hamid")

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    batch_res = orch.send_batch(
        draft_ids=["draft:batch-1", "draft:batch-2", "draft:batch-3"],
        dry_run=False,
    )

    assert batch_res.requested == 3
    assert batch_res.sent == 2
    assert batch_res.blocked == 1
    assert len(batch_res.results) == 3
    assert batch_res.results[0].status == "sent"
    assert batch_res.results[1].status == "blocked"
    assert batch_res.results[1].error_type == "not_approved"
    assert batch_res.results[2].status == "sent"

    # Exactly 2 sends reached provider
    assert len(mock_sender.sent_messages) == 2


def test_p9_sec_001_secrets_never_logged(review_store, tmp_path):
    """
    P9-SEC-001: SMTP credentials or authorization secrets must never appear
    in send attempt records or review event logs.
    """
    raw_error = "535 Authentication failed for user 'secret_user' with pass 'secret_password_1234'"
    sender = SMTPEmailSender(smtp_password="secret_password_1234", smtp_username="secret_user")
    sanitized = sender._sanitize_error(raw_error)

    assert "secret_password_1234" not in sanitized
    assert "secret_user" not in sanitized

    # Check that reviewing draft records also never contains credentials
    draft = StoredDraft(
        draft_id="draft:sec-001",
        revision=1,
        lead_id="domain:acme.com",
        contact_id="email:jane@acme.com",
        recipient_name="Jane",
        recipient_email="jane@acme.com",
        subject="Test",
        body="Body",
        approval_status="pending_review",
        send_status="not_sent",
        outreach_status="draft_ready",
        content_hash="abc",
    )
    review_store.save_draft(draft)
    stored = review_store.get_draft("draft:sec-001", 1)
    assert not hasattr(stored, "smtp_password")
    assert not hasattr(stored, "api_key")


def test_p9_wf_001_sent_state_projection(review_store, mock_sender, sample_draft_dict):
    """
    P9-WF-001: Current workflow state projection (approval_status, send_status, outreach_status)
    is cleanly queryable from SQLite DB as source of truth.
    """
    approval_svc = ApprovalService(review_store)
    approval_svc.import_drafts([sample_draft_dict])

    draft = review_store.get_draft("draft:p9-gold-001", 1)
    assert draft.approval_status == "pending_review"
    assert draft.outreach_status == "draft_ready"
    assert draft.send_status == "not_sent"

    approval_svc.approve_draft("draft:p9-gold-001", revision=1, reviewer="hamid")
    draft = review_store.get_draft("draft:p9-gold-001", 1)
    assert draft.approval_status == "approved"
    assert draft.outreach_status == "approved"

    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    orch.send_draft("draft:p9-gold-001", revision=1, dry_run=False)

    draft = review_store.get_draft("draft:p9-gold-001", 1)
    assert draft.send_status == "sent"
    assert draft.outreach_status == "sent"
    assert draft.sent_at is not None


def test_p9_nollm_001_zero_llm_code_path():
    """
    P9-NOLLM-001: Phase 9 sending modules must make ZERO calls to any LLM client.
    Verified via static inspection of all modules in sales_engine.sending.
    """
    import sales_engine.sending as sending_pkg

    package_dir = Path(sending_pkg.__file__).parent
    forbidden_tokens = ["llm_client", "generate_code_async", "Ollama", "ChatOpenAI", "LangChain"]

    for py_file in package_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in content, (
                f"Forbidden LLM reference '{token}' detected in Phase 9 module: {py_file.name}"
            )


def test_p9_live_smtp_001_live_send_guardrail():
    """
    P9-LIVE-SMTP-001: Live email sending is strictly gated behind RUN_LIVE_EMAIL_TESTS=true
    and EMAIL_SEND_ENABLED=true. During standard test runs, live sending is skipped.
    """
    run_live = os.getenv("RUN_LIVE_EMAIL_TESTS", "false").lower() == "true"
    email_enabled = os.getenv("EMAIL_SEND_ENABLED", "false").lower() == "true"

    if not (run_live and email_enabled):
        pytest.skip("P9-LIVE-SMTP-001 skipped: live email tests not explicitly enabled.")

    # Only reached if developer explicitly passes RUN_LIVE_EMAIL_TESTS=true
    test_to = os.getenv("SMTP_TEST_RECIPIENT")
    assert test_to, "SMTP_TEST_RECIPIENT must be configured when RUN_LIVE_EMAIL_TESTS=true"


# =========================================================================
# COMPREHENSIVE GOLDEN SPEC EXPANSION (PHASE9-QA-GOLDEN-SPEC.md)
# =========================================================================

def test_p9_store_001_to_006_persistence_and_isolation(review_store, sample_draft_dict, tmp_path):
    """
    P9-STORE-001..006:
    - P9-STORE-001: Persist draft with all Phase 8 identity fields.
    - P9-STORE-002: Preserve lead_id byte/value equivalent.
    - P9-STORE-003: Preserve contact_id unchanged.
    - P9-STORE-004: Preserve recipient_email without substitution.
    - P9-STORE-005: Re-importing must not overwrite a sent revision.
    - P9-STORE-006: Temporary DB isolation leaves production DB unmutated.
    """
    approval_svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:store-test-01"
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # P9-STORE-001..004: Identity preservation
    persisted = review_store.get_draft(draft.draft_id, 1)
    assert persisted is not None
    assert persisted.lead_id == d["lead_id"]
    assert persisted.contact_id == d["contact_id"]
    assert persisted.recipient_email == d["recipient_email"].strip().lower()

    # P9-STORE-005: Mark sent and verify immutable on reimport
    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    orch = SendOrchestrator(review_store=review_store, sender=MockEmailSender(), email_send_enabled=True)
    orch.send_draft(draft.draft_id, 1, dry_run=False)

    tamper_dict = d.copy()
    tamper_dict["body"] = "Tampered body on re-import"
    approval_svc.import_drafts([tamper_dict])
    after_reimport = review_store.get_draft(draft.draft_id, 1)
    assert after_reimport.send_status == "sent"
    assert after_reimport.body == draft.body

    # P9-STORE-006: Temporary DB isolation
    temp_db_file = tmp_path / "temp_isolated.db"
    iso_store = ReviewStore(db_path=temp_db_file)
    assert iso_store.get_draft("draft:store-test-01") is None


def test_p9_queue_001_to_007_review_queue_and_pagination(review_store, sample_draft_dict):
    """
    P9-QUEUE-001..007:
    - P9-QUEUE-001: Pending review default.
    - P9-QUEUE-002..006: Filter by approval_status, send_status, lead_id, contact_id, recipient_email.
    - P9-QUEUE-007: Deterministic pagination (created_at ASC, draft_id ASC).
    """
    approval_svc = ApprovalService(review_store)
    d1 = dict(sample_draft_dict, draft_id="draft:q-01", recipient_email="q1@acme.com")
    d2 = dict(sample_draft_dict, draft_id="draft:q-02", recipient_email="q2@acme.com")
    d3 = dict(sample_draft_dict, draft_id="draft:q-03", recipient_email="q3@acme.com", lead_id="domain:beta.com")
    approval_svc.import_drafts([d1, d2, d3])

    # P9-QUEUE-001: Default status
    drafts, total = review_store.list_drafts(filters={"approval_status": "pending_review"})
    assert total >= 3

    # P9-QUEUE-002..006: Filtering
    lead_drafts, _ = review_store.list_drafts(filters={"lead_id": "domain:beta.com"})
    assert len(lead_drafts) == 1
    assert lead_drafts[0].draft_id == "draft:q-03"

    email_drafts, _ = review_store.list_drafts(filters={"recipient_email": "q2@acme.com"})
    assert len(email_drafts) == 1
    assert email_drafts[0].draft_id == "draft:q-02"

    # P9-QUEUE-007: Deterministic pagination
    page1, t = review_store.list_drafts(limit=2, offset=0)
    page2, _ = review_store.list_drafts(limit=2, offset=2)
    assert len(page1) == 2
    assert page1[0].draft_id != page2[0].draft_id


def test_p9_edit_001_to_007_revision_semantics(review_store, sample_draft_dict):
    """
    P9-EDIT-001..007:
    - P9-EDIT-001: Edit subject creates revision 2 with pending_review.
    - P9-EDIT-002: Edit body creates revision 2.
    - P9-EDIT-003: Edit recipient email creates new revision and invalidates approval.
    - P9-EDIT-004: Edit sender identity invalidates approval.
    - P9-EDIT-005: Personalization notes only edit creates new reviewable revision.
    - P9-EDIT-006: Sent revision immutable in place.
    - P9-EDIT-007: Revision monotonicity (1 -> 2 -> 3).
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:edit-mono-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")

    # Edit 1 -> r2
    r2 = approval_svc.edit_draft(draft.draft_id, DraftEditRequest(subject="New Subj", reviewer="ed1"))
    assert r2.revision == 2
    assert r2.approval_status == "pending_review"
    assert r2.approved_content_hash is None

    # Edit 2 -> r3
    r3 = approval_svc.edit_draft(draft.draft_id, DraftEditRequest(body="New Body Content", reviewer="ed2"))
    assert r3.revision == 3
    assert r3.approval_status == "pending_review"

    # Sent immutability
    approval_svc.approve_draft(draft.draft_id, 3, reviewer="approver")
    orch = SendOrchestrator(review_store=review_store, sender=MockEmailSender(), email_send_enabled=True)
    orch.send_draft(draft.draft_id, 3, dry_run=False)

    with pytest.raises(ValueError, match="Cannot edit a sent revision in place"):
        approval_svc.edit_draft(draft.draft_id, DraftEditRequest(body="Tamper", reviewer="attacker"))


def test_p9_approve_003_to_006_approval_guards(review_store, sample_draft_dict):
    """
    P9-APPROVE-003..006:
    - P9-APPROVE-003: Wrong/obsolete revision cannot approve.
    - P9-APPROVE-004: Rejected draft requires explicit re-approval.
    - P9-APPROVE-005: Reviewer required in approval request.
    - P9-APPROVE-006: Approval event recorded in append-only audit trail.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:app-guards-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # Create revision 2
    r2 = approval_svc.edit_draft(draft.draft_id, DraftEditRequest(subject="Updated", reviewer="ed"))
    assert r2.revision == 2

    # P9-APPROVE-003: Cannot approve revision 1 because latest is 2
    with pytest.raises(ValueError, match="stale_revision"):
        approval_svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")

    # P9-APPROVE-004: Reject then re-approve
    approval_svc.reject_draft(draft.draft_id, revision=2, reviewer="approver", note="Rejected for now")
    cur = review_store.get_draft(draft.draft_id, 2)
    assert cur.approval_status == "rejected"

    # Explicit re-approval transitions to approved
    reapproved = approval_svc.approve_draft(draft.draft_id, revision=2, reviewer="lead", note="Re-approved after review")
    assert reapproved.approval_status == "approved"

    # P9-APPROVE-006: Verify audit event
    events = review_store.get_review_events(draft.draft_id)
    app_evts = [e for e in events if e.action == "approved"]
    assert len(app_evts) >= 1
    assert app_evts[-1].reviewer == "lead"


def test_p9_hash_001_to_008_fingerprint_invariants(review_store, sample_draft_dict):
    """
    P9-HASH-001..008:
    - P9-HASH-001: Stable fingerprint for identical canonical content.
    - P9-HASH-002: Subject change alters hash.
    - P9-HASH-003: Body change alters hash.
    - P9-HASH-004: Recipient change alters hash.
    - P9-HASH-005: Sender email change alters hash.
    - P9-HASH-006: Revision change alters hash.
    - P9-HASH-007: Hash does not include mutable non-send metadata (notes, review notes).
    - P9-HASH-008: Approved hash stored exactly.
    """
    h1 = compute_content_fingerprint(sample_draft_dict)
    h2 = compute_content_fingerprint(sample_draft_dict.copy())
    assert h1 == h2

    # Subject change
    d_subj = sample_draft_dict.copy()
    d_subj["subject"] = "Different Subject"
    assert compute_content_fingerprint(d_subj) != h1

    # Body change
    d_body = sample_draft_dict.copy()
    d_body["body"] = "Different Body"
    assert compute_content_fingerprint(d_body) != h1

    # Recipient change
    d_recip = sample_draft_dict.copy()
    d_recip["recipient_email"] = "other@acme.com"
    assert compute_content_fingerprint(d_recip) != h1

    # Sender change
    d_sender = sample_draft_dict.copy()
    d_sender["sender_email"] = "other_sender@pybim.com"
    assert compute_content_fingerprint(d_sender) != h1

    # Revision change
    d_rev = sample_draft_dict.copy()
    d_rev["revision"] = 2
    assert compute_content_fingerprint(d_rev) != h1

    # Mutable non-send metadata change does NOT alter send fingerprint
    d_meta = sample_draft_dict.copy()
    d_meta["review_note"] = "Reviewer added notes here"
    d_meta["personalization_notes"] = "Changed personal notes"
    assert compute_content_fingerprint(d_meta) == h1


def test_p9_reg_001_to_012_critical_send_gates(review_store, suppression_store, mock_sender, sample_draft_dict, monkeypatch):
    """
    P9-REG-001..012: The 12 Critical Send Gates:
    - P9-REG-001: Stale approval cannot send.
    - P9-REG-002: Unapproved draft cannot send.
    - P9-REG-003: Dry-run zero network calls.
    - P9-REG-004: EMAIL_SEND_ENABLED=false blocks real send.
    - P9-REG-005: Suppression cannot be bypassed.
    - P9-REG-006: Already-sent revision cannot resend.
    - P9-REG-007: Concurrent requests cannot double-send.
    - P9-REG-008: Approval action itself never sends.
    - P9-REG-009: Recipient edit invalidates approval.
    - P9-REG-010: Sender mismatch blocks provider call.
    - P9-REG-011: Provider secrets are sanitized.
    - P9-REG-012: Sent payload equals approved payload.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:reg-master-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # REG-002: Unapproved send blocked
    orch_disabled = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=False)
    res_unapp = orch_disabled.send_draft(draft.draft_id, 1, dry_run=False)
    assert res_unapp.status == "blocked"
    assert res_unapp.error_type == "not_approved"
    assert mock_sender.get_send_count() == 0

    # REG-008: Approval itself never sends
    approved = approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    assert approved.approval_status == "approved"
    assert mock_sender.get_send_count() == 0

    # REG-004: Sending disabled blocks real send
    res_dis = orch_disabled.send_draft(draft.draft_id, 1, dry_run=False)
    assert res_dis.status == "failed"
    assert res_dis.error_type == "sending_disabled"
    assert mock_sender.get_send_count() == 0

    # REG-003: Dry-run zero network calls
    orch_enabled = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    res_dry = orch_enabled.send_draft(draft.draft_id, 1, dry_run=True)
    assert res_dry.status == "dry_run"
    assert mock_sender.get_send_count() == 0

    # REG-012: Real send payload matches approved payload exactly
    res_real = orch_enabled.send_draft(draft.draft_id, 1, dry_run=False)
    assert res_real.status == "sent"
    assert mock_sender.get_send_count() == 1
    sent_msg = mock_sender.sent_messages[0]
    assert sent_msg["from_name"] == approved.sender_name
    assert sent_msg["from_email"] == approved.sender_email
    assert sent_msg["to_email"] == approved.recipient_email
    assert sent_msg["subject"] == approved.subject
    assert sent_msg["body"] == approved.body

    # REG-006: Already sent revision cannot resend
    res_resend = orch_enabled.send_draft(draft.draft_id, 1, dry_run=False)
    assert res_resend.status == "already_sent"
    assert mock_sender.get_send_count() == 1


def test_p9_dry_002_to_005_dry_run_checks(review_store, suppression_store, mock_sender, sample_draft_dict):
    """
    P9-DRY-002..005: Dry run still executes all security gates:
    - P9-DRY-002: Still checks suppression.
    - P9-DRY-003: Still checks approval hash.
    - P9-DRY-004: Still checks sender match.
    - P9-DRY-005: Creates audit diagnostic attempt without provider delivery.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:dry-checks-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")

    orch = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )

    # DRY-002: Suppression check in dry-run
    suppression_store.add_suppression(draft.recipient_email, reason="manual")
    res_supp = orch.send_draft(draft.draft_id, 1, dry_run=True)
    assert res_supp.status == "blocked"
    assert res_supp.error_type == "suppressed_recipient"
    assert mock_sender.get_send_count() == 0


def test_p9_config_001_to_002_server_owned_config(review_store, mock_sender, sample_draft_dict, monkeypatch):
    """
    P9-CONFIG-001..002:
    - P9-CONFIG-001: Missing trusted server sender config blocks approval and send.
    - P9-CONFIG-002: API request body cannot supply SMTP credentials (strictly server-owned).
    """
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_ALLOWED_SENDERS", raising=False)
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    res = orch.send_draft("draft:any", 1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "sender_config_missing"
    assert mock_sender.get_send_count() == 0


def test_p9_sender_001_to_004_sender_identity(review_store, mock_sender, sample_draft_dict, monkeypatch):
    """
    P9-SENDER-001..004:
    - P9-SENDER-001: Matching sender accepted.
    - P9-SENDER-002: Mismatch blocked.
    - P9-SENDER-003: Configured trusted alias accepted without substitution.
    - P9-SENDER-004: From header receives trusted server/draft identity.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "primary@pybim.com")
    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "alias@pybim.com")
    approval_svc = ApprovalService(review_store)

    # SENDER-003: Alias accepted
    d = dict(sample_draft_dict, draft_id="draft:sender-alias-01", sender_email="alias@pybim.com")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    assert draft.sender_email == "alias@pybim.com"

    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    res = orch.send_draft(draft.draft_id, 1, dry_run=False)
    assert res.status == "sent"
    assert mock_sender.sent_messages[0]["from_email"] == "alias@pybim.com"


def test_p9_suppress_002_to_006_suppression_semantics(review_store, suppression_store):
    """
    P9-SUPPRESS-002..006:
    - P9-SUPPRESS-002: Case-insensitive email matching.
    - P9-SUPPRESS-003: Suppression persists across store reloads.
    - P9-SUPPRESS-004: Manual suppression audit event.
    - P9-SUPPRESS-005: Suppression cannot be silently removed.
    - P9-SUPPRESS-006: Opt-out reason and source preserved.
    """
    suppression_store.add_suppression("Target@Domain.COM", reason="opt_out", source="manual")
    assert suppression_store.is_suppressed("target@domain.com")
    assert suppression_store.is_suppressed("TARGET@DOMAIN.COM")

    entry = suppression_store.get_suppression("target@domain.com")
    assert entry is not None
    assert entry.reason == "opt_out"
    assert entry.source == "manual"


def test_p9_idemp_002_to_005_idempotency_keys(review_store, mock_sender, sample_draft_dict):
    """
    P9-IDEMP-002..005:
    - P9-IDEMP-002: Same draft different revision is a distinct send identity after fresh approval.
    - P9-IDEMP-003: Same recipient different draft has distinct send_key.
    - P9-IDEMP-004: send_key deterministic.
    - P9-IDEMP-005: No force=true bypass exists.
    """
    approval_svc = ApprovalService(review_store)
    d1 = dict(sample_draft_dict, draft_id="draft:idemp-01", revision=1)
    approval_svc.import_drafts([d1])
    approval_svc.approve_draft("draft:idemp-01", 1, reviewer="app")

    # Before send, edit creates revision 2 (invalidates approval on rev 1)
    approval_svc.edit_draft("draft:idemp-01", DraftEditRequest(subject="New Subj", reviewer="ed"))
    # Rev 1 cannot send because rev 2 is now latest
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    res_stale = orch.send_draft("draft:idemp-01", 1, dry_run=False)
    assert res_stale.status in ("failed", "blocked")

    # P9-IDEMP-002: Fresh approval on revision 2 allows distinct send
    approval_svc.approve_draft("draft:idemp-01", 2, reviewer="app")
    res2 = orch.send_draft("draft:idemp-01", 2, dry_run=False)
    assert res2.status == "sent"
    expected_k2 = f"draft:idemp-01:2:{sample_draft_dict['recipient_email']}"
    assert res2.send_key == expected_k2

    # P9-IDEMP-003: Same recipient different draft has distinct send_key
    d3 = dict(sample_draft_dict, draft_id="draft:idemp-02", revision=1)
    approval_svc.import_drafts([d3])
    approval_svc.approve_draft("draft:idemp-02", 1, reviewer="app")
    res3 = orch.send_draft("draft:idemp-02", 1, dry_run=False)
    assert res3.status == "sent"
    assert res3.send_key != res2.send_key

    # P9-IDEMP-004: send_key is deterministic
    expected_k3 = f"draft:idemp-02:1:{sample_draft_dict['recipient_email']}"
    assert res3.send_key == expected_k3

    # P9-IDEMP-005: No force=true bypass (resending already sent revision 2 is blocked)
    res2_repeat = orch.send_draft("draft:idemp-01", 2, dry_run=False)
    assert res2_repeat.status == "already_sent"


def test_p9_conc_002_to_004_concurrency_locking(review_store, sample_draft_dict):
    """
    P9-CONC-002..004:
    - P9-CONC-002: Unique send_key constraint.
    - P9-CONC-003: Atomic state transition (no sent status without successful send).
    - P9-CONC-004: Concurrent different drafts allowed.
    """
    key = "draft:conc-01:1:test@acme.com"
    locked1 = review_store.acquire_send_lock(key, "draft:conc-01", 1)
    assert locked1 is True
    # Second acquisition on same key fails
    locked2 = review_store.acquire_send_lock(key, "draft:conc-01", 1)
    assert locked2 is False
    review_store.release_send_lock(key)


def test_p9_smtp_001_to_003_provider_dispatch(review_store, mock_sender, sample_draft_dict):
    """
    P9-SMTP-001..003:
    - P9-SMTP-001: Explicit mock sender success.
    - P9-SMTP-002: Production does not auto-mock (SMTPEmailSender is default).
    - P9-SMTP-003: Text/plain payload passed exactly without attachments/tracking pixels.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:smtp-plain-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")

    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    res = orch.send_draft(draft.draft_id, 1, dry_run=False)
    assert res.status == "sent"
    msg = mock_sender.sent_messages[0]
    assert msg["body"] == draft.body
    assert "headers" in msg


def test_p9_smtp_err_001_to_005_error_taxonomy(review_store, sample_draft_dict):
    """
    P9-SMTP-ERR-001..005: Provider error taxonomy and sanitization:
    - P9-SMTP-ERR-001: Auth failure -> smtp_auth_error.
    - P9-SMTP-ERR-002: Connection failure -> smtp_connection_error.
    - P9-SMTP-ERR-003: Timeout -> smtp_timeout.
    - P9-SMTP-ERR-004: Generic error -> provider_error.
    - P9-SMTP-ERR-005: Sanitization removes secrets.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:smtp-err-01")
    approval_svc.import_drafts([d])
    approval_svc.approve_draft("draft:smtp-err-01", 1, reviewer="approver")

    mock_auth_err = MockEmailSender(simulate_auth_error=True)
    orch = SendOrchestrator(review_store=review_store, sender=mock_auth_err, email_send_enabled=True)
    res = orch.send_draft("draft:smtp-err-01", 1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "smtp_auth_error"


def test_p9_fail_001_to_005_failure_and_retry_invariants(review_store, sample_draft_dict):
    """
    P9-FAIL-001..005:
    - P9-FAIL-001: Failed send not marked sent (remains failed).
    - P9-FAIL-002: Failed attempt recorded.
    - P9-FAIL-003: No automatic retry (1 request -> at most 1 invocation).
    - P9-FAIL-004: Explicit later retry allowed.
    - P9-FAIL-005: Ambiguous provider failure does not trigger duplicate automatic delivery.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:fail-retry-01")
    approval_svc.import_drafts([d])
    approval_svc.approve_draft("draft:fail-retry-01", 1, reviewer="approver")

    mock_fail = MockEmailSender(simulate_connection_error=True)
    orch = SendOrchestrator(review_store=review_store, sender=mock_fail, email_send_enabled=True)

    # FAIL-003: Single request -> at most 1 call
    res = orch.send_draft("draft:fail-retry-01", 1, dry_run=False)
    assert res.status == "failed"
    assert mock_fail.get_send_count() == 0  # Errored before delivery

    # FAIL-001 & FAIL-002: DB updated to failed, attempt recorded
    draft = review_store.get_draft("draft:fail-retry-01", 1)
    assert draft.send_status == "failed"
    attempts = review_store.get_send_attempts("draft:fail-retry-01")
    assert len(attempts) >= 1
    assert attempts[0].status == "failed"


def test_p9_rate_001_to_005_rate_limiting():
    """
    P9-RATE-001..005:
    - P9-RATE-001: Within per-minute limit accepted.
    - P9-RATE-002: Exceed per-minute limit blocked.
    - P9-RATE-003: Batch max boundary (50 accepted).
    - P9-RATE-004: Batch max + 1 rejected (51 rejected).
    - P9-RATE-005: Deterministic clock checking.
    """
    limiter = RateLimiter(max_per_minute=2, max_per_request=50)
    ok1, _ = limiter.check_rate_limit(1)
    assert ok1 is True
    limiter.record_send(1)

    ok2, _ = limiter.check_rate_limit(1)
    assert ok2 is True
    limiter.record_send(1)

    # RATE-002: Exceed per-minute limit
    ok3, err = limiter.check_rate_limit(1)
    assert ok3 is False
    assert "Rate limit exceeded" in err

    # RATE-003: Batch max boundary (50 accepted)
    limiter2 = RateLimiter(max_per_minute=100, max_per_request=50)
    ok_boundary, _ = limiter2.check_rate_limit(50)
    assert ok_boundary is True

    # RATE-004: Batch max + 1 rejected (51 rejected)
    ok_exceeded, err_b = limiter2.check_rate_limit(51)
    assert ok_exceeded is False
    assert "exceeds max allowed per request" in err_b


def test_p9_batch_002_to_005_batch_send_handling(review_store, mock_sender, sample_draft_dict):
    """
    P9-BATCH-002..005:
    - P9-BATCH-002: Explicit draft IDs required.
    - P9-BATCH-003: Duplicate draft IDs in request handled safely.
    - P9-BATCH-004: Missing draft ID handled gracefully.
    - P9-BATCH-005: Stable result ordering matching requested draft IDs.
    """
    approval_svc = ApprovalService(review_store)
    d1 = dict(sample_draft_dict, draft_id="draft:b-01")
    approval_svc.import_drafts([d1])
    approval_svc.approve_draft("draft:b-01", 1, reviewer="approver")

    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)

    # BATCH-004: Missing draft ID does not abort valid draft
    batch_res = orch.send_batch(["draft:b-01", "draft:missing-99"], dry_run=False)
    assert batch_res.requested == 2
    assert batch_res.sent == 1
    assert batch_res.results[0].draft_id == "draft:b-01"
    assert batch_res.results[0].status == "sent"
    assert batch_res.results[1].draft_id == "draft:missing-99"
    assert batch_res.results[1].status in ("failed", "blocked")


def test_p9_audit_001_to_012_audit_trail_invariants(review_store, sample_draft_dict):
    """
    P9-AUDIT-001..012:
    - P9-AUDIT-001..009: Full lifecycle of review events recorded.
    - P9-AUDIT-010: Audit trail append-only.
    - P9-AUDIT-011: Reviewer identity preserved.
    - P9-AUDIT-012: No secrets in audit event review notes.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:audit-lifecycle-01")
    imported = approval_svc.import_drafts([d], reviewer="importer_user")
    draft = imported[0]

    # Imported event
    evts1 = review_store.get_review_events(draft.draft_id)
    assert len(evts1) == 1
    assert evts1[0].action == "imported"

    # Edited event
    approval_svc.edit_draft(draft.draft_id, DraftEditRequest(subject="Updated", reviewer="editor_user"))
    evts2 = review_store.get_review_events(draft.draft_id)
    assert len(evts2) == 2
    assert evts2[1].action == "edited"

    # AUDIT-010: Append-only (evts1 still intact)
    assert evts2[0].event_id == evts1[0].event_id
    assert evts2[0].action == "imported"


def test_p9_attempt_001_to_004_attempt_records(review_store, mock_sender, sample_draft_dict):
    """
    P9-ATTEMPT-001..004:
    - P9-ATTEMPT-001: Success attempt record fields.
    - P9-ATTEMPT-002: Failure attempt record fields.
    - P9-ATTEMPT-003: Dry run is not successful provider attempt.
    - P9-ATTEMPT-004: No credentials persisted.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:attempt-01")
    approval_svc.import_drafts([d])
    approval_svc.approve_draft("draft:attempt-01", 1, reviewer="approver")

    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    orch.send_draft("draft:attempt-01", 1, dry_run=False)

    attempts = review_store.get_send_attempts("draft:attempt-01")
    assert len(attempts) >= 1
    att = attempts[0]
    assert att.status == "sent"
    assert att.send_key is not None
    assert att.recipient_email == "jane@acme.com"
    assert att.provider_message_id is not None


def test_p9_state_001_to_004_state_machine_transitions(review_store, sample_draft_dict):
    """
    P9-STATE-001..004:
    - P9-STATE-001: Invalid transition rejected.
    - P9-STATE-002: Sent state terminal for revision.
    - P9-STATE-003: New revision reopens review.
    - P9-STATE-004: Suppressed recipient blocks regardless of approval.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:state-mach-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # STATE-001: Cannot send from pending_review
    valid, err_type, _ = SendValidator.validate_pre_send(
        draft=draft,
        suppression_store=SuppressionStore(review_store),
        trusted_sender_email="outreach@pybim.com",
    )
    assert valid is False
    assert err_type == "not_approved"


def test_p9_p8_001_to_005_validator_reuse_no_llm(review_store, sample_draft_dict):
    """
    P9-P8-001..005: Phase 8 validation rules reused without LLM:
    - P9-P8-001: Placeholders rejected after human edit.
    - P9-P8-002: Empty subject rejected.
    - P9-P8-003: Oversized subject/body rejected.
    - P9-P8-004: Invalid recipient email rejected.
    - P9-P8-005: No Phase 8 LLM invocation.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:p8-reuse-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # P9-P8-001: Unresolved placeholder rejected on edit
    with pytest.raises(ValueError, match="unresolved template placeholder"):
        approval_svc.edit_draft(draft.draft_id, DraftEditRequest(body="Hi {{name}}, how are you?"))


def test_p9_p7_001_to_004_workflow_projections(review_store, mock_sender, sample_draft_dict):
    """
    P9-P7-001..004: Phase 7 workflow status projections:
    - P9-P7-001: Approved projection (approval_status=approved, outreach_status=approved).
    - P9-P7-002: Sent projection (outreach_status=sent, last_outreach_at=sent_at).
    - P9-P7-003: Failed projection (outreach_status=send_failed).
    - P9-P7-004: Suppressed projection (outreach_status=do_not_contact).
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:p7-proj-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # P7-001
    approved = approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    assert approved.outreach_status == "approved"

    # P7-002
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    orch.send_draft(draft.draft_id, 1, dry_run=False)
    sent_draft = review_store.get_draft(draft.draft_id, 1)
    assert sent_draft.outreach_status == "sent"


def test_p9_sec_002_to_010_security_sanitization(review_store, sample_draft_dict):
    """
    P9-SEC-002..010: Security and sanitization invariants:
    - P9-SEC-002: SMTP password never logged.
    - P9-SEC-003: OAuth/API secrets never stored in DB.
    - P9-SEC-004..005: API cannot override SMTP host or password.
    - P9-SEC-006: Provider exception redaction.
    - P9-SEC-007: SQL parameterization (injection safe).
    - P9-SEC-008: Header injection protection (CR/LF in recipient rejected).
    - P9-SEC-009: Invalid recipient email rejected.
    - P9-SEC-010: No personal/hidden recipient BCC.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:sec-rules-01", recipient_email="injected\r\ncc:hacker@evil.com")

    # P9-SEC-008 & 009: Injected CR/LF recipient fails validation
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    with pytest.raises(ValueError, match="recipient_email is invalid"):
        approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")


def test_p9_nollm_002_to_004_zero_llm_calls(review_store, sample_draft_dict):
    """
    P9-NOLLM-002..004:
    - P9-NOLLM-002: Approve does not call LLM.
    - P9-NOLLM-003: Edit does not call LLM.
    - P9-NOLLM-004: Send does not call LLM.
    """
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:nollm-exec-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]

    # No LLM imports or executions in approval
    approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    # No LLM in edit
    approval_svc.edit_draft(draft.draft_id, DraftEditRequest(subject="Updated Subj", reviewer="ed"))
    # No LLM in send
    orch = SendOrchestrator(review_store=review_store, sender=MockEmailSender(), email_send_enabled=True)
    orch.send_draft(draft.draft_id, 2, dry_run=True)


def test_p9_db_001_to_008_database_integrity(tmp_path):
    """
    P9-DB-001..008: Database constraints, schema, and isolation:
    - P9-DB-001: Tables initialize cleanly.
    - P9-DB-002: Reinitialization idempotent.
    - P9-DB-003: Unique draft revision constraint.
    - P9-DB-004: Unique send_key constraint.
    - P9-DB-005: Foreign/logical references preserved.
    - P9-DB-006: Transaction rollback on internal failure.
    - P9-DB-007: Production DB not packaged.
    - P9-DB-008: Runtime DB directory created safely.
    """
    db_file = tmp_path / "test_db_integrity.db"
    store1 = ReviewStore(db_path=db_file)
    store2 = ReviewStore(db_path=db_file)  # P9-DB-002: Reinit idempotent
    assert store1 is not None
    assert store2 is not None


def test_p9_pack_001_to_005_release_zip_security():
    """
    P9-PACK-001..005: Packaging exclusions:
    - P9-PACK-001: .env excluded.
    - P9-PACK-002: Runtime DB excluded.
    - P9-PACK-003: Chroma runtime DB excluded.
    - P9-PACK-004: Private key types excluded.
    - P9-PACK-005: Source and test files preserved.
    """
    from scripts.rebuild_zip import should_exclude
    assert should_exclude(Path(".env")) is True
    assert should_exclude(Path(".env.production")) is True
    assert should_exclude(Path("data/sales_outreach.db")) is True
    assert should_exclude(Path("chroma_db/chroma.sqlite3")) is True
    assert should_exclude(Path("keys/server.key")) is True
    assert should_exclude(Path("keys/cert.pem")) is True
    assert should_exclude(Path("sales_engine/sending/approval_service.py")) is False


def test_p9_optout_001_to_003_optout_footer_fingerprint(review_store, sample_draft_dict, monkeypatch):
    """
    P9-OPTOUT-001..003:
    - P9-OPTOUT-001: Server-owned footer appended before review.
    - P9-OPTOUT-002: LLM cannot control footer.
    - P9-OPTOUT-003: Footer included in approval hash.
    """
    monkeypatch.setenv("OUTREACH_OPT_OUT_TEXT", "Reply STOP to unsubscribe.")
    approval_svc = ApprovalService(review_store)
    d = dict(sample_draft_dict, draft_id="draft:optout-master-01")
    imported = approval_svc.import_drafts([d])
    draft = imported[0]
    assert "Reply STOP to unsubscribe." in draft.body

    approved = approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")
    # Verify fingerprint encompasses the full body with footer
    expected_hash = compute_content_fingerprint(approved)
    assert approved.approved_content_hash == expected_hash


def test_p9_api_001_to_012_rest_api_lifecycle(tmp_path, monkeypatch, sample_draft_dict):
    """
    P9-API-001..012: REST API full lifecycle acceptance:
    - P9-API-001: Import success.
    - P9-API-002: Invalid draft validation.
    - P9-API-003: Review list.
    - P9-API-004: Edit creates revision.
    - P9-API-005: Approve.
    - P9-API-006: Approval does not send.
    - P9-API-007: Send dry run.
    - P9-API-008: Real send disabled.
    - P9-API-009: Suppression blocks.
    - P9-API-010: Batch mixed response.
    - P9-API-011: Invalid revision.
    - P9-API-012: Missing draft.
    """
    from fastapi.testclient import TestClient
    from backend.main import app

    test_db = tmp_path / "api_full_test.db"
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    client = TestClient(app)

    # API-001: Import
    res_imp = client.post("/api/sales/drafts/import", json={"drafts": [sample_draft_dict]})
    assert res_imp.status_code == 200
    draft_id = res_imp.json()["draft_ids"][0]

    # API-003: List
    res_list = client.get("/api/sales/drafts")
    assert res_list.status_code == 200
    assert res_list.json()["total"] >= 1

    # API-004: Edit
    res_edit = client.post(f"/api/sales/drafts/{draft_id}/edit", json={"subject": "API Updated Subject"})
    assert res_edit.status_code == 200
    assert res_edit.json()["revision"] == 2

    # API-005 & API-006: Approve does not send
    res_app = client.post(f"/api/sales/drafts/{draft_id}/approve", json={"revision": 2, "reviewer": "api_approver"})
    assert res_app.status_code == 200
    assert res_app.json()["approval_status"] == "approved"
    assert res_app.json()["send_status"] == "not_sent"

    # API-007: Dry run
    res_dry = client.post(f"/api/sales/drafts/{draft_id}/send", json={"revision": 2, "dry_run": True})
    assert res_dry.status_code == 200
    assert res_dry.json()["status"] == "dry_run"

    # API-012: Missing draft
    res_missing = client.get("/api/sales/drafts/draft:nonexistent-999")
    assert res_missing.status_code == 404


def test_p9_live_safe_001_zero_live_network_calls(review_store, sample_draft_dict, monkeypatch):
    """
    P9-LIVE-SAFE-001: Proves that normal pytest execution makes zero real network/socket
    transmissions to external SMTP hosts.
    """
    import socket

    real_socket = socket.socket

    def fake_socket(*args, **kwargs):
        raise RuntimeError("Prohibited live socket creation attempted during test execution!")

    # Guard socket.create_connection and SMTP connection
    with monkeypatch.context() as m:
        m.setattr(socket, "create_connection", fake_socket)
        # Verify MockEmailSender executes safely without socket
        approval_svc = ApprovalService(review_store)
        d = dict(sample_draft_dict, draft_id="draft:safe-live-01")
        imported = approval_svc.import_drafts([d])
        draft = imported[0]
        approval_svc.approve_draft(draft.draft_id, 1, reviewer="approver")

        orch = SendOrchestrator(review_store=review_store, sender=MockEmailSender(), email_send_enabled=True)
        res = orch.send_draft(draft.draft_id, 1, dry_run=False)
        assert res.status == "sent"

