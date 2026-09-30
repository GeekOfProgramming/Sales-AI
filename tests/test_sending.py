import os
import json
import uuid
import inspect
import threading
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    SendResult,
    compute_content_fingerprint,
)


@pytest.fixture
def test_db_path(tmp_path):
    return tmp_path / "test_outreach.db"


@pytest.fixture
def review_store(test_db_path):
    return ReviewStore(db_path=test_db_path)


@pytest.fixture
def suppression_store(review_store):
    return SuppressionStore(review_store=review_store)


@pytest.fixture
def sample_draft_dict():
    return {
        "draft_id": "draft:test-001",
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
# 1. IMPORT & QUEUE
# =========================================================================

def test_import_drafts_pending_review_default(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    imported = svc.import_drafts([sample_draft_dict], reviewer="test_importer")
    assert len(imported) == 1
    d = imported[0]
    assert d.draft_id == "draft:test-001"
    assert d.revision == 1
    assert d.approval_status == "pending_review"
    assert d.send_status == "not_sent"
    assert d.outreach_status == "draft_ready"
    assert d.approved_content_hash is None
    assert d.approved_at is None
    assert d.content_hash == compute_content_fingerprint(d)

    # Verify event logged
    events = review_store.list_events(d.draft_id)
    assert len(events) == 1
    assert events[0].action == "imported"


def test_review_queue_filtering(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    d1 = sample_draft_dict.copy()
    d1["draft_id"] = "draft:001"
    d1["recipient_email"] = "a@acme.com"

    d2 = sample_draft_dict.copy()
    d2["draft_id"] = "draft:002"
    d2["recipient_email"] = "b@other.com"
    d2["lead_id"] = "domain:other.com"

    svc.import_drafts([d1, d2])

    drafts_acme, cnt_acme = review_store.list_drafts(filters={"lead_id": "domain:acme.com"})
    assert cnt_acme == 1
    assert drafts_acme[0].draft_id == "draft:001"

    drafts_pending, cnt_p = review_store.list_drafts(filters={"approval_status": "pending_review"})
    assert cnt_p == 2


# =========================================================================
# 2. HUMAN EDITS & REVISION INTEGRITY
# =========================================================================

def test_human_edit_creates_new_revision_and_invalidates_approval(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    # Approve rev 1
    svc.approve_draft(draft_id="draft:test-001", revision=1, reviewer="hamid")
    rev1 = review_store.get_draft("draft:test-001", revision=1)
    assert rev1.approval_status == "approved"
    assert rev1.approved_content_hash is not None

    # Human edit on body
    edit_req = DraftEditRequest(
        body="Hi Jane,\n\nEdited body text here.\n\nOpen to a chat?",
        reviewer="hamid",
        note="Polished sentence structure",
    )
    rev2 = svc.edit_draft("draft:test-001", edit_req)

    # Invariants for new revision:
    assert rev2.revision == 2
    assert rev2.approval_status == "pending_review"
    assert rev2.send_status == "not_sent"
    assert rev2.approved_content_hash is None
    assert rev2.approved_at is None
    assert rev2.body == "Hi Jane,\n\nEdited body text here.\n\nOpen to a chat?"

    # Prior rev 1 remains intact in history
    rev1_reload = review_store.get_draft("draft:test-001", revision=1)
    assert rev1_reload.revision == 1
    assert rev1_reload.approval_status == "approved"


def test_human_edit_rejects_unresolved_placeholders(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    edit_req = DraftEditRequest(
        subject="Revit API for {{company_name}}",
        reviewer="editor",
    )
    with pytest.raises(ValueError, match="unresolved template placeholder"):
        svc.edit_draft("draft:test-001", edit_req)


def test_cannot_edit_sent_revision_in_place(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    review_store.update_draft_status("draft:test-001", revision=1, send_status="sent")

    edit_req = DraftEditRequest(subject="New subject", reviewer="editor")
    with pytest.raises(ValueError, match="Cannot edit a sent revision in place"):
        svc.edit_draft("draft:test-001", edit_req)


# =========================================================================
# 3. APPROVAL FINGERPRINT & SEPARATION
# =========================================================================

def test_explicit_approval_stores_fingerprint(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    approved = svc.approve_draft(
        draft_id="draft:test-001",
        revision=1,
        reviewer="hamid",
        note="Verified evidence and recipient",
    )
    assert approved.approval_status == "approved"
    assert approved.outreach_status == "approved"
    assert approved.approved_at is not None
    assert approved.approved_content_hash == compute_content_fingerprint(approved)


def test_approval_does_not_send_email(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender)
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")
    # Verify zero network transmissions happened
    assert len(mock_sender.sent_messages) == 0
    draft = review_store.get_draft("draft:test-001", revision=1)
    assert draft.send_status == "not_sent"


def test_stale_approval_fingerprint_mismatch_blocks_send(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender, email_send_enabled=True)
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    # Manually tamper with the draft body in DB behind the approval fingerprint
    with review_store._get_connection() as conn:
        conn.execute(
            "UPDATE drafts SET body = 'Tampered text after approval' WHERE draft_id = 'draft:test-001' AND revision = 1"
        )

    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "blocked"
    assert res.error_type == "approval_stale"
    assert len(mock_sender.sent_messages) == 0


def test_unapproved_draft_send_blocked(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender)
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "blocked"
    assert res.error_type == "not_approved"
    assert len(mock_sender.sent_messages) == 0


def test_rejected_and_changes_requested_draft_send_blocked(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(review_store=review_store, sender=mock_sender)
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])

    # Reject
    svc.reject_draft("draft:test-001", revision=1, reviewer="hamid", note="Not suitable")
    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "blocked"
    assert res.error_type == "not_approved"

    # Request changes
    svc.request_changes("draft:test-001", revision=1, reviewer="hamid", note="Shorten cta")
    res2 = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res2.status == "blocked"
    assert res2.error_type == "not_approved"


# =========================================================================
# 4. SUPPRESSION / DO-NOT-CONTACT
# =========================================================================

def test_suppressed_recipient_blocks_send(review_store, suppression_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    # Add recipient to suppression list
    suppression_store.add_suppression("jane@acme.com", reason="opt_out", source="manual")

    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "blocked"
    assert res.error_type == "suppressed_recipient"
    assert len(mock_sender.sent_messages) == 0

    # Draft outreach status updated to do_not_contact
    draft = review_store.get_draft("draft:test-001", revision=1)
    assert draft.outreach_status == "do_not_contact"
    assert draft.send_status == "blocked"


def test_suppression_store_crud(suppression_store):
    assert not suppression_store.is_suppressed("test@domain.com")
    entry = suppression_store.add_suppression("test@domain.com", reason="bounce")
    assert entry.email == "test@domain.com"
    assert suppression_store.is_suppressed("test@domain.com")
    assert suppression_store.is_suppressed("TEST@domain.com ")  # Case/trim normalization

    supp_list = suppression_store.list_suppressed()
    assert len(supp_list) == 1

    removed = suppression_store.remove_suppression("test@domain.com")
    assert removed is True
    assert not suppression_store.is_suppressed("test@domain.com")


# =========================================================================
# 5. DRY RUN & SENDING GATES
# =========================================================================

def test_dry_run_invokes_zero_network_calls(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=False,  # Even when sending disabled, dry_run succeeds!
    )
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    res = orch.send_draft("draft:test-001", revision=1, dry_run=True)
    assert res.status == "dry_run"
    assert res.dry_run is True
    assert len(mock_sender.sent_messages) == 0

    draft = review_store.get_draft("draft:test-001", revision=1)
    assert draft.send_status == "not_sent"  # Dry run leaves stored draft as 'not_sent'


def test_email_send_enabled_false_blocks_real_send(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=False,
    )
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "sending_disabled"
    assert len(mock_sender.sent_messages) == 0


def test_sender_identity_mismatch_blocks_send(review_store, sample_draft_dict, monkeypatch):
    monkeypatch.setenv("SMTP_FROM_EMAIL", "trusted@pybim.com")
    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "alternate@pybim.com")
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    orch.from_email = "trusted@pybim.com"

    d = sample_draft_dict.copy()
    d["sender_email"] = "alternate@pybim.com"  # Draft specifies alternate sender

    svc = ApprovalService(review_store)
    svc.import_drafts([d])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    res = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "sender_mismatch"
    assert len(mock_sender.sent_messages) == 0


# =========================================================================
# 6. IDEMPOTENCY & CONCURRENCY
# =========================================================================

def test_idempotent_already_sent_blocked(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    # 1. First send: succeeds
    res1 = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res1.status == "sent"
    assert len(mock_sender.sent_messages) == 1

    # 2. Second send for same draft revision: BLOCKED
    res2 = orch.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res2.status == "already_sent"
    assert res2.error_type == "already_sent"
    # Provider was NOT invoked again
    assert len(mock_sender.sent_messages) == 1


def test_concurrent_duplicate_send_prevention(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    results = []

    def send_task():
        r = orch.send_draft("draft:test-001", revision=1, dry_run=False)
        results.append(r)

    t1 = threading.Thread(target=send_task)
    t2 = threading.Thread(target=send_task)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # One succeeds ('sent'), the other is blocked by concurrency lock or already_sent
    statuses = [r.status for r in results]
    assert "sent" in statuses
    assert len(mock_sender.sent_messages) == 1


# =========================================================================
# 7. SENDER FAILURES & TAXONOMY
# =========================================================================

def test_mock_sender_failures_taxonomy(review_store, sample_draft_dict):
    svc = ApprovalService(review_store)
    svc.import_drafts([sample_draft_dict])
    svc.approve_draft("draft:test-001", revision=1, reviewer="hamid")

    # 1. Auth error
    mock_auth = MockEmailSender(simulate_auth_error=True)
    orch_auth = SendOrchestrator(review_store=review_store, sender=mock_auth, email_send_enabled=True)
    res = orch_auth.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "smtp_auth_error"

    # 2. Timeout
    mock_timeout = MockEmailSender(simulate_timeout=True)
    orch_timeout = SendOrchestrator(review_store=review_store, sender=mock_timeout, email_send_enabled=True)
    res = orch_timeout.send_draft("draft:test-001", revision=1, dry_run=False)
    assert res.status == "failed"
    assert res.error_type == "smtp_timeout"


def test_smtp_sender_auth_error_sanitized():
    smtp = SMTPEmailSender(
        smtp_host="localhost",
        smtp_port=587,
        smtp_username="user@domain.com",
        smtp_password="SuperSecretPassword123!",
    )
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.login.side_effect = Exception("535 5.7.8 Authentication credentials invalid for SuperSecretPassword123!")
        mock_smtp_cls.return_value = mock_server

        res = smtp.send_email(
            draft_id="d1",
            revision=1,
            send_key="k1",
            to_email="test@acme.com",
            from_email="from@domain.com",
            from_name="Sender",
            subject="Sub",
            body="Body",
        )
        assert res.status == "failed"
        assert "SuperSecretPassword123!" not in res.error_message
        assert "********" in res.error_message


# =========================================================================
# 8. RATE LIMITING & BATCH ISOLATION
# =========================================================================

def test_rate_limiter_enforces_limits():
    limiter = RateLimiter(max_per_minute=2, max_per_request=5)
    ok, err = limiter.check_rate_limit(1)
    assert ok is True
    limiter.record_send(1)

    ok2, err2 = limiter.check_rate_limit(1)
    assert ok2 is True
    limiter.record_send(1)

    # 3rd send exceeds max_per_minute=2
    ok3, err3 = limiter.check_rate_limit(1)
    assert ok3 is False
    assert "Rate limit exceeded" in err3


def test_batch_send_partial_failure_isolation(review_store, suppression_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    svc = ApprovalService(review_store)

    # Draft A: Valid and approved
    dA = sample_draft_dict.copy()
    dA["draft_id"] = "draft:A"
    dA["recipient_email"] = "a@acme.com"

    # Draft B: Suppressed
    dB = sample_draft_dict.copy()
    dB["draft_id"] = "draft:B"
    dB["recipient_email"] = "b@suppressed.com"
    suppression_store.add_suppression("b@suppressed.com", reason="opt_out")

    # Draft C: Unapproved
    dC = sample_draft_dict.copy()
    dC["draft_id"] = "draft:C"
    dC["recipient_email"] = "c@acme.com"

    svc.import_drafts([dA, dB, dC])
    svc.approve_draft("draft:A", revision=1, reviewer="hamid")
    svc.approve_draft("draft:B", revision=1, reviewer="hamid")
    # Draft C remains unapproved

    resp = orch.send_batch(["draft:A", "draft:B", "draft:C"], dry_run=False)
    assert resp.requested == 3
    assert resp.sent == 1
    assert resp.blocked == 2  # B is suppressed, C is unapproved
    assert resp.failed == 0

    # Verify Draft A successfully delivered and survived
    assert len(mock_sender.sent_messages) == 1
    assert mock_sender.sent_messages[0]["draft_id"] == "draft:A"
    draftA = review_store.get_draft("draft:A", revision=1)
    assert draftA.send_status == "sent"


# =========================================================================
# 9. STATIC / CODE-PATH INVARIANTS: NO LLM & NO AUTO-APPROVE
# =========================================================================

def test_no_llm_calls_in_sending_module():
    """Verify Phase 9 modules do not import or call BIMLLMClient or invoke any LLM."""
    import sales_engine.sending.review_store as rs
    import sales_engine.sending.approval_service as app_svc
    import sales_engine.sending.send_orchestrator as orch
    import sales_engine.sending.smtp_sender as smtp
    import sales_engine.sending.send_validator as val

    modules = [rs, app_svc, orch, smtp, val]
    for mod in modules:
        src = inspect.getsource(mod)
        assert "BIMLLMClient" not in src
        assert "generate_code" not in src
        assert "ollama" not in src.lower()


# =========================================================================
# 10. FASTAPI API INTEGRATION TESTS
# =========================================================================

def test_api_review_and_send_lifecycle(tmp_path, monkeypatch, sample_draft_dict):
    from fastapi.testclient import TestClient
    from backend.main import app

    test_db = tmp_path / "api_test.db"
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))

    client = TestClient(app)

    # 1. Import draft
    imp_res = client.post("/api/sales/drafts/import", json={"drafts": [sample_draft_dict]})
    assert imp_res.status_code == 200
    assert imp_res.json()["imported_count"] == 1

    # 2. Get draft
    get_res = client.get("/api/sales/drafts/draft:test-001")
    assert get_res.status_code == 200
    assert get_res.json()["approval_status"] == "pending_review"

    # 3. Edit draft
    edit_res = client.post(
        "/api/sales/drafts/draft:test-001/edit",
        json={"subject": "Updated subject line", "reviewer": "editor"},
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["revision"] == 2
    assert edit_res.json()["approval_status"] == "pending_review"

    # 4. Approve revision 2
    app_res = client.post(
        "/api/sales/drafts/draft:test-001/approve",
        json={"revision": 2, "reviewer": "approver", "note": "Approved"},
    )
    assert app_res.status_code == 200
    assert app_res.json()["approval_status"] == "approved"

    # 5. Send dry run
    send_res = client.post(
        "/api/sales/drafts/draft:test-001/send",
        json={"revision": 2, "dry_run": True},
    )
    assert send_res.status_code == 200
    assert send_res.json()["status"] == "dry_run"


# =========================================================================
# 11. CRITICAL REGRESSION: P9-REG-012
# =========================================================================

def test_p9_reg_012_sent_payload_hash_equals_approved_payload_hash(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-012: The exact payload (subject, body, recipient, sender) delivered to the provider
    MUST match the content protected by the approval fingerprint (approved_content_hash).
    Opt-out footers and signatures MUST be part of the payload BEFORE approval.
    Any post-approval alteration strictly causes approval_stale and blocks sending.
    """
    monkeypatch.setenv("OUTREACH_OPT_OUT_TEXT", "Reply unsubscribe to stop receiving outreach.")
    svc = ApprovalService(review_store)

    # 1. Import draft with opt-out footer configured
    imported = svc.import_drafts([sample_draft_dict], reviewer="importer")
    draft = imported[0]
    assert "Reply unsubscribe to stop receiving outreach." in draft.body

    # 2. Human reviews and approves the full payload including footer
    approved_draft = svc.approve_draft(draft.draft_id, revision=1, reviewer="compliance_officer")
    assert approved_draft.approval_status == "approved"
    assert approved_draft.approved_content_hash is not None

    # 3. Send using MockEmailSender
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"
    assert mock_sender.get_send_count() == 1

    # 4. Verify sent payload hash matches approved_content_hash exactly
    sent_delivery = mock_sender.sent_messages[0]
    assert sent_delivery["body"] == approved_draft.body

    sent_fingerprint = compute_content_fingerprint({
        "draft_id": approved_draft.draft_id,
        "revision": approved_draft.revision,
        "lead_id": approved_draft.lead_id,
        "contact_id": approved_draft.contact_id,
        "recipient_email": sent_delivery["to_email"],
        "sender_email": sent_delivery["from_email"],
        "subject": sent_delivery["subject"],
        "body": sent_delivery["body"],
    })
    assert sent_fingerprint == approved_draft.approved_content_hash

    # 5. Adversarial mutation: tamper with draft body in DB post-approval
    d2 = sample_draft_dict.copy()
    d2["draft_id"] = "draft:tamper-001"
    imported2 = svc.import_drafts([d2], reviewer="importer")
    approved2 = svc.approve_draft(imported2[0].draft_id, revision=1, reviewer="approver")

    # Manually tamper with body post-approval
    review_store.update_draft_status(approved2.draft_id, revision=1, send_status="not_sent")
    with review_store._get_connection() as conn:
        conn.execute(
            "UPDATE drafts SET body = ? WHERE draft_id = ? AND revision = ?",
            ("Tampered body after approval", approved2.draft_id, 1),
        )

    # Attempt to send tampered draft
    res_tampered = orchestrator.send_draft(approved2.draft_id, revision=1, dry_run=False)
    assert res_tampered.status == "blocked"
    assert res_tampered.error_type == "approval_stale"
    assert mock_sender.get_send_count() == 1  # No additional send!


# =========================================================================
# 12. ADVERSARIAL ATTACK PATH REGRESSIONS (Phase 9 Pre-Golden Hardening)
# =========================================================================

def test_adversarial_1_missing_sender_in_draft_blocks_or_matches_hash(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    Vulnerability 1: If sender inside Draft is empty/None, sending must either be blocked
    or resolved to an exact match where actual provider hash == approved_content_hash.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    svc = ApprovalService(review_store)

    # Test A: Import draft with explicit None sender -> resolved to trusted sender
    d1 = sample_draft_dict.copy()
    d1["draft_id"] = "draft:sender-none-01"
    d1["sender_email"] = None
    imported = svc.import_drafts([d1], reviewer="importer")
    draft = imported[0]
    assert draft.sender_email == "outreach@pybim.com"

    approved = svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"
    sent_delivery = mock_sender.sent_messages[0]
    assert sent_delivery["from_email"] == "outreach@pybim.com"

    actual_hash = compute_content_fingerprint({
        "draft_id": approved.draft_id,
        "revision": approved.revision,
        "lead_id": approved.lead_id,
        "contact_id": approved.contact_id,
        "recipient_email": sent_delivery["to_email"],
        "sender_email": sent_delivery["from_email"],
        "subject": sent_delivery["subject"],
        "body": sent_delivery["body"],
    })
    assert actual_hash == approved.approved_content_hash

    # Test B: If a draft in DB has an empty sender_email, SendValidator strictly blocks it
    d2 = sample_draft_dict.copy()
    d2["draft_id"] = "draft:sender-none-02"
    imported2 = svc.import_drafts([d2], reviewer="importer")
    draft2 = imported2[0]
    approved2 = svc.approve_draft(draft2.draft_id, revision=1, reviewer="approver")

    # Manually clear sender_email in DB
    with review_store._get_connection() as conn:
        conn.execute(
            "UPDATE drafts SET sender_email = NULL WHERE draft_id = ? AND revision = ?",
            (draft2.draft_id, 1),
        )

    res_blocked = orchestrator.send_draft(draft2.draft_id, revision=1, dry_run=False)
    assert res_blocked.status == "failed" or res_blocked.status == "blocked"
    assert res_blocked.error_type == "sender_config_missing"
    assert mock_sender.get_send_count() == 1  # No additional send dispatched!


def test_adversarial_2_obsolete_revision_cannot_be_approved_or_sent(
    review_store, suppression_store, sample_draft_dict
):
    """
    Vulnerability 2: Revision 2 is created, but someone attempts to approve or send Revision 1.
    Must be strictly blocked with stale_revision error.
    """
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:stale-rev-01"
    imported = svc.import_drafts([d], reviewer="importer")
    draft_r1 = imported[0]

    # Human edits draft -> Revision 2 created
    draft_r2 = svc.edit_draft(
        draft_r1.draft_id,
        DraftEditRequest(
            body="New updated body for revision 2.",
            reviewer="editor",
            note="Revision 2 update",
        ),
    )
    assert draft_r2.revision == 2

    # Attempting to approve obsolete revision 1 must raise ValueError (stale_revision)
    with pytest.raises(ValueError, match="stale_revision"):
        svc.approve_draft(draft_r1.draft_id, revision=1, reviewer="approver")

    # If revision 1 was somehow marked approved, orchestrator must still refuse to send it
    review_store.update_draft_status(
        draft_r1.draft_id, revision=1,
        approval_status="approved",
        approved_content_hash="dummy_hash",
        send_status="not_sent",
    )
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft_r1.draft_id, revision=1, dry_run=False)
    assert res.status == "blocked"
    assert res.error_type == "stale_revision"
    assert mock_sender.get_send_count() == 0


def test_adversarial_3_reimport_sent_draft_cannot_overwrite_or_resend(
    review_store, suppression_store, sample_draft_dict
):
    """
    Vulnerability 3: Draft sent at revision 1. Same draft_id / revision=1 is re-imported
    with a different recipient and body. Must NOT overwrite sent status, and cannot double-send.
    """
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:reimport-exploit-01"
    imported = svc.import_drafts([d], reviewer="importer")
    draft = imported[0]

    # Approve and send Revision 1
    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res1 = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res1.status == "sent"
    assert mock_sender.get_send_count() == 1

    # Adversarial re-import with new recipient and body
    exploit_dict = d.copy()
    exploit_dict["recipient_email"] = "other@acme.com"
    exploit_dict["body"] = "Different body to hijack delivery"
    svc.import_drafts([exploit_dict], reviewer="attacker")

    # Verify that existing draft is STILL sent and was NOT overwritten
    persisted = review_store.get_draft(draft.draft_id, 1)
    assert persisted.send_status == "sent"
    assert persisted.recipient_email == d["recipient_email"].strip().lower()
    assert persisted.body == draft.body

    # Attempting to re-send must be rejected as already_sent
    res2 = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res2.status == "already_sent"
    assert mock_sender.get_send_count() == 1  # Total deliveries strictly remains 1!

    # Direct save_draft on sent revision must raise ValueError
    with pytest.raises(ValueError, match="Cannot overwrite immutable sent draft revision"):
        review_store.save_draft(persisted)


def test_adversarial_4_edit_body_preserves_opt_out_footer(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    Vulnerability 4: Opt-out is configured. Human editor edits draft body and omits footer.
    The new revision MUST automatically preserve the opt-out footer.
    """
    footer_text = "Reply STOP to opt out of future communications."
    monkeypatch.setenv("OUTREACH_OPT_OUT_TEXT", footer_text)
    svc = ApprovalService(review_store)

    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:optout-preserve-01"
    imported = svc.import_drafts([d], reviewer="importer")
    draft_r1 = imported[0]
    assert footer_text in draft_r1.body

    # Unset env var temporarily to ensure footer is preserved from draft content
    monkeypatch.delenv("OUTREACH_OPT_OUT_TEXT", raising=False)

    # Human edits body and does NOT include the footer
    draft_r2 = svc.edit_draft(
        draft_r1.draft_id,
        DraftEditRequest(
            body="Hi Alex, here is our customized proposal for BIM engineering.",
            reviewer="human_editor",
            note="Updated pitch",
        ),
    )
    # The footer MUST still be present in the new revision!
    assert footer_text in draft_r2.body

    # Approve and send
    approved = svc.approve_draft(draft_r2.draft_id, revision=2, reviewer="approver")
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft_r2.draft_id, revision=2, dry_run=False)
    assert res.status == "sent"
    sent_msg = mock_sender.sent_messages[0]
    assert footer_text in sent_msg["body"]


def test_adversarial_5_provider_secrets_redacted_across_all_audit_logs(
    review_store, suppression_store, sample_draft_dict
):
    """
    Vulnerability 5: Provider returns error containing sensitive tokens/passwords.
    All secrets MUST be redacted in SendResult, send_attempts, and review_events.
    """
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:secret-leak-01"
    imported = svc.import_drafts([d], reviewer="importer")
    draft = imported[0]
    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")

    # Custom mock sender that simulates an error leaking credentials
    class LeakyMockSender(MockEmailSender):
        def send_email(self, *args, **kwargs):
            return SendResult(
                draft_id=args[0] if args else kwargs.get("draft_id"),
                revision=args[1] if len(args) > 1 else kwargs.get("revision", 1),
                send_key=args[2] if len(args) > 2 else kwargs.get("send_key", ""),
                status="failed",
                provider="mock_provider",
                error_type="auth_failure",
                error_message="Connection rejected: smtp_password=SuperSecretPassword123 with Bearer secret-token-xyz-987654321",
            )

    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=LeakyMockSender(),
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "failed"

    # 1. Check SendResult
    assert "SuperSecretPassword123" not in res.error_message
    assert "secret-token-xyz-987654321" not in res.error_message
    assert "********" in res.error_message

    # 2. Check send_attempts DB record
    attempts = review_store.get_send_attempts(draft.draft_id)
    assert len(attempts) >= 1
    assert "SuperSecretPassword123" not in attempts[0].error_message
    assert "secret-token-xyz-987654321" not in attempts[0].error_message

    # 3. Check review_events DB record
    events = review_store.list_events(draft.draft_id)
    fail_events = [e for e in events if e.action == "send_failed"]
    assert len(fail_events) >= 1
    assert "SuperSecretPassword123" not in (fail_events[0].review_note or "")
    assert "secret-token-xyz-987654321" not in (fail_events[0].review_note or "")


# =========================================================================
# 13. ROUND-2 REGRESSIONS: P9-REG-013 THROUGH P9-REG-017
# =========================================================================

def test_p9_reg_013_missing_trusted_sender_config_blocks_approval_send(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-013: When SMTP_FROM_EMAIL is unset:
    - Draft with sender_email=None cannot be approved or sent (sender_config_missing)
    - Draft with untrusted sender (attacker@example.com) cannot self-authorize or send
    - Zero provider calls in all failure paths
    """
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_ALLOWED_SENDERS", raising=False)
    svc = ApprovalService(review_store)

    # Sub-case A: Unset SMTP_FROM_EMAIL and draft.sender_email = None
    d1 = sample_draft_dict.copy()
    d1["draft_id"] = "draft:no-sender-01"
    d1["sender_email"] = None
    imported1 = svc.import_drafts([d1], reviewer="importer")
    draft1 = imported1[0]
    assert draft1.sender_email is None

    # Approval must be strictly blocked
    with pytest.raises(ValueError, match="sender_config_missing"):
        svc.approve_draft(draft1.draft_id, revision=1, reviewer="approver")

    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res1 = orchestrator.send_draft(draft1.draft_id, revision=1, dry_run=False)
    assert res1.status in ("failed", "blocked")
    assert res1.error_type == "sender_config_missing"
    assert mock_sender.get_send_count() == 0

    # Sub-case B: Unset SMTP_FROM_EMAIL and draft has untrusted sender
    d2 = sample_draft_dict.copy()
    d2["draft_id"] = "draft:untrusted-sender-02"
    d2["sender_email"] = "attacker@example.com"
    imported2 = svc.import_drafts([d2], reviewer="importer")
    draft2 = imported2[0]
    assert draft2.sender_email is None  # Not resolved to untrusted address!

    with pytest.raises(ValueError, match="sender_config_missing"):
        svc.approve_draft(draft2.draft_id, revision=1, reviewer="approver")

    res2 = orchestrator.send_draft(draft2.draft_id, revision=1, dry_run=False)
    assert res2.status in ("failed", "blocked")
    assert res2.error_type == "sender_config_missing"
    assert mock_sender.get_send_count() == 0

    # Sub-case C: Trusted sender is set, but draft sender does not match
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    with review_store._get_connection() as conn:
        conn.execute(
            "UPDATE drafts SET sender_email = ? WHERE draft_id = ? AND revision = ?",
            ("attacker@example.com", draft2.draft_id, 1),
        )
    with pytest.raises(ValueError, match="sender_config_missing"):
        svc.approve_draft(draft2.draft_id, revision=1, reviewer="approver")
    assert mock_sender.get_send_count() == 0


def test_p9_reg_014_sent_revision_cannot_be_rejected(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-014: A successfully sent draft revision is terminal and cannot be rejected.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:sent-immutable-01"
    imported = svc.import_drafts([d])
    draft = imported[0]

    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"
    assert mock_sender.get_send_count() == 1

    # Attempt to reject the sent revision must raise ValueError
    with pytest.raises(ValueError, match="invalid_state_transition"):
        svc.reject_draft(draft.draft_id, revision=1, reviewer="reviewer", note="Attempted reject")

    # Verify state remains completely intact
    persisted = review_store.get_draft(draft.draft_id, revision=1)
    assert persisted.send_status == "sent"
    assert persisted.approval_status == "approved"
    assert persisted.outreach_status == "sent"


def test_p9_reg_015_sent_revision_cannot_request_changes(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-015: A successfully sent draft revision cannot have changes requested.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:sent-immutable-02"
    imported = svc.import_drafts([d])
    draft = imported[0]

    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")
    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"

    # Attempt to request changes on the sent revision must raise ValueError
    with pytest.raises(ValueError, match="invalid_state_transition"):
        svc.request_changes(draft.draft_id, revision=1, reviewer="reviewer", note="Needs edit")

    persisted = review_store.get_draft(draft.draft_id, revision=1)
    assert persisted.send_status == "sent"
    assert persisted.approval_status == "approved"


def test_p9_reg_016_sent_revision_cannot_transition_back_to_not_sent(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-016: Direct store mutation cannot reset sent_status from 'sent' to 'not_sent'.
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:sent-immutable-03"
    imported = svc.import_drafts([d])
    draft = imported[0]
    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")

    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=MockEmailSender(),
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"

    # Direct store update_draft_status attempt to reset send_status to 'not_sent'
    with pytest.raises(ValueError, match="invalid_state_transition"):
        review_store.update_draft_status(
            draft.draft_id,
            revision=1,
            send_status="not_sent",
            approval_status="pending_review",
        )


def test_p9_reg_017_sent_revision_remains_unchanged_after_invalid_transition(
    review_store, suppression_store, sample_draft_dict, monkeypatch
):
    """
    P9-REG-017: Verifies that after any rejected mutation attempt, all sent revision fields
    remain strictly unchanged (approval_status=approved, send_status=sent, outreach_status=sent,
    sent_at unchanged, provider delivery count unchanged).
    """
    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
    svc = ApprovalService(review_store)
    d = sample_draft_dict.copy()
    d["draft_id"] = "draft:sent-immutable-04"
    imported = svc.import_drafts([d])
    draft = imported[0]
    svc.approve_draft(draft.draft_id, revision=1, reviewer="approver")

    mock_sender = MockEmailSender()
    orchestrator = SendOrchestrator(
        review_store=review_store,
        suppression_store=suppression_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    res = orchestrator.send_draft(draft.draft_id, revision=1, dry_run=False)
    assert res.status == "sent"
    initial_sent_draft = review_store.get_draft(draft.draft_id, revision=1)
    initial_sent_at = initial_sent_draft.sent_at
    assert initial_sent_at is not None

    # Try reject
    try:
        svc.reject_draft(draft.draft_id, revision=1, reviewer="hacker")
    except ValueError:
        pass

    # Try request changes
    try:
        svc.request_changes(draft.draft_id, revision=1, reviewer="hacker", note="hack")
    except ValueError:
        pass

    # Try edit in place
    try:
        svc.edit_draft(draft.draft_id, DraftEditRequest(body="Tampered body", reviewer="hacker"))
    except ValueError:
        pass

    # Verify everything remains exactly as originally sent
    current = review_store.get_draft(draft.draft_id, revision=1)
    assert current.approval_status == "approved"
    assert current.send_status == "sent"
    assert current.outreach_status == "sent"
    assert current.sent_at == initial_sent_at
    assert current.body == initial_sent_draft.body
    assert current.content_hash == initial_sent_draft.content_hash
    assert current.approved_content_hash == initial_sent_draft.approved_content_hash
    assert mock_sender.get_send_count() == 1


