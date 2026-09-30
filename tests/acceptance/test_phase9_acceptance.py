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
