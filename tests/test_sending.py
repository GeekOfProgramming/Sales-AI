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


def test_sender_identity_mismatch_blocks_send(review_store, sample_draft_dict):
    mock_sender = MockEmailSender()
    orch = SendOrchestrator(
        review_store=review_store,
        sender=mock_sender,
        email_send_enabled=True,
    )
    orch.from_email = "trusted@pybim.com"

    d = sample_draft_dict.copy()
    d["sender_email"] = "imposter@malicious.com"  # Draft specifies untrusted sender

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
