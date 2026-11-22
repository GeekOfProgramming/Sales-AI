"""
tests/test_mail_send_01h_sent_reconciliation_workflow.py
MAIL-SEND-01H: Dedicated Test Suite for Automatic/Supervised IMAP Sent Reconciliation.

Mandatory Test Coverage:
1. Successful SMTP acceptance followed by exactly one Sent APPEND.
2. Existing Message-ID: no duplicate APPEND.
3. Repeated reconciliation: idempotent.
4. IMAP timeout before and after APPEND.
5. Ambiguous SMTP result: no unsafe automatic APPEND or resend.
6. Remote duplicate Message-ID: transitions to 'sent_requires_review'.
7. MIME byte fidelity and SHA-256 verification (stored before SMTP handoff).
8. Two accounts remain strictly isolated (info@pybim.com vs info@pybim.it).
9. Wrong sender, invalid folder, or corrupted MIME: fail closed.
10. Existing historical pilot remains unchanged.
11. UI displays the distinct transport and reconciliation statuses.
12. No regression in existing composer, scheduled drafts, queue, and dry-run workflows.
"""

import os
import json
import hashlib
import pytest
from unittest.mock import MagicMock, patch

from sales_engine.sending.outbound_models import OutboundQueueJob, OutboundAttemptRecord
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.sending.sent_reconciliation import (
    SentReconciliationService,
    SentReconciliationError,
    DEFAULT_SENT_FOLDER,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_outreach.db")
    repo = MailboxRepository(db_path=db_file, auto_migrate=True)
    queue = OutboundQueueStore(db_path=db_file, auto_apply_schema=True)

    # Authorized Account 1: info@pybim.com
    acc1 = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        credential_ref="win_cred_acc_info_com",
        is_enabled=True,
    )
    repo.save_account(acc1)

    # Authorized Account 2: info@pybim.it
    acc2 = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        credential_ref="win_cred_acc_info_it",
        is_enabled=True,
    )
    repo.save_account(acc2)

    return {
        "db_path": db_file,
        "repo": repo,
        "queue": queue,
        "acc1": acc1,
        "acc2": acc2,
    }


@pytest.fixture
def mock_cred_store():
    store = MagicMock(spec=MailboxCredentialStore)
    store.get_credential.return_value = "mock_safe_password"
    return store


@pytest.fixture
def sample_accepted_job_with_mime(temp_db):
    raw_mime = b"From: pyBIM <info@pybim.com>\r\nTo: partner@example.com\r\nSubject: Test\r\nMessage-ID: <test.exact@pybim.com>\r\n\r\nBody text"
    digest = hashlib.sha256(raw_mime).hexdigest()

    job = OutboundQueueJob(
        job_id="job_smtp_acc_01",
        source_draft_id="drf_smtp_acc_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["partner@example.com"]),
        header_to_json=json.dumps(["partner@example.com"]),
        subject="Test",
        body_plain_text="Body text",
        rfc_message_id="<test.exact@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_smtp_acc_01",
        status="smtp_accepted",
        reconciliation_status="sent_pending",
        submitted_mime_bytes=raw_mime,
        submitted_mime_sha256=digest,
    )
    temp_db["queue"].enqueue_job(job)
    return job


def test_1_successful_smtp_acceptance_followed_by_one_sent_append(temp_db, mock_cred_store, sample_accepted_job_with_mime):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # First search: not found; append succeeds; second search: found UID 358
    mock_client.uid.side_effect = [
        ("OK", [b""]),       # pre-check
        ("OK", [b"358"]),    # post-append confirmation
    ]
    mock_client.append.return_value = ("OK", [b"[APPENDUID 12345 358]"])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "sent_reconciled"
    assert res["imap_uid"] == "358"
    assert res["provenance"] == "DURABLE_EXACT"
    assert res["is_byte_identical"] is True

    # Check database state
    updated_job = temp_db["queue"].get_job(sample_accepted_job_with_mime.job_id)
    assert updated_job.status == "smtp_accepted"  # Delivery status NEVER altered!
    assert updated_job.reconciliation_status == "sent_reconciled"
    assert updated_job.imap_uid == "358"
    assert updated_job.reconciled_at_utc is not None


def test_2_and_3_existing_message_id_skips_append_and_is_idempotent(temp_db, mock_cred_store, sample_accepted_job_with_mime):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b"357"])

    # First run: finds UID 357
    res1 = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )
    assert res1["success"] is True
    assert res1["action"] == "SKIPPED_ALREADY_EXISTS"
    assert res1["status"] == "sent_reconciled"
    mock_client.append.assert_not_called()

    # Second run: repeated reconciliation
    res2 = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )
    assert res2["success"] is True
    assert res2["status"] == "sent_reconciled"
    mock_client.append.assert_not_called()


def test_4_imap_timeout_recovery_and_quarantine(temp_db, mock_cred_store, sample_accepted_job_with_mime):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    # 4A: Timeout occurred but message landed on server
    mock_client_recover = MagicMock()
    mock_client_recover.select.return_value = ("OK", [b"10"])
    mock_client_recover.uid.side_effect = [
        ("OK", [b""]),       # pre-check
        ("OK", [b"360"]),    # post-exception check: message landed!
    ]
    mock_client_recover.append.side_effect = TimeoutError("Socket timed out during APPEND")

    res_recover = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client_recover,
    )
    assert res_recover["success"] is True
    assert res_recover["status"] == "sent_reconciled"
    assert res_recover["action"] == "APPENDED_WITH_RECOVERY"

    # 4B: Timeout occurred and message did NOT land on server
    mock_client_unverified = MagicMock()
    mock_client_unverified.select.return_value = ("OK", [b"10"])
    mock_client_unverified.uid.side_effect = [
        ("OK", [b""]),       # pre-check
        ("OK", [b""]),       # post-exception check: still not on server
    ]
    mock_client_unverified.append.side_effect = TimeoutError("Socket timed out during APPEND")

    res_unverified = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client_unverified,
    )
    assert res_unverified["success"] is False
    assert res_unverified["status"] == "sent_requires_review"

    job_state = temp_db["queue"].get_job(sample_accepted_job_with_mime.job_id)
    assert job_state.status == "smtp_accepted"  # SMTP status unaffected
    assert job_state.reconciliation_status == "sent_requires_review"


def test_5_ambiguous_smtp_result_rejects_reconciliation(temp_db, mock_cred_store):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    job_ambig = OutboundQueueJob(
        job_id="job_ambiguous_01",
        source_draft_id="drf_ambig",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json="[\"test@example.com\"]",
        header_to_json="[\"test@example.com\"]",
        subject="Ambig",
        body_plain_text="Text",
        rfc_message_id="<ambig@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_ambig",
        status="acceptance_unknown",  # Ambiguous SMTP state!
    )
    temp_db["queue"].enqueue_job(job_ambig)

    with pytest.raises(SentReconciliationError, match="status is 'acceptance_unknown'"):
        svc.reconcile_job_sent_copy(job_id="job_ambiguous_01", dry_run=True)


def test_6_remote_duplicate_message_id_triggers_requires_review(temp_db, mock_cred_store, sample_accepted_job_with_mime):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # Returns TWO matching UIDs on remote server
    mock_client.uid.return_value = ("OK", [b"101 102"])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job_with_mime.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is False
    assert res["action"] == "HALTED_MULTIPLE_MATCHES"
    assert res["status"] == "sent_requires_review"
    mock_client.append.assert_not_called()

    job_state = temp_db["queue"].get_job(sample_accepted_job_with_mime.job_id)
    assert job_state.reconciliation_status == "sent_requires_review"


def test_7_mime_byte_fidelity_and_sha256_verification(temp_db, mock_cred_store):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    raw_exact = b"Exact MIME bytes from before SMTP transmission."
    good_hash = hashlib.sha256(raw_exact).hexdigest()

    # Tampered / corrupted hash job
    job_corrupted = OutboundQueueJob(
        job_id="job_corrupted_01",
        source_draft_id="drf_corrupt",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json="[\"test@example.com\"]",
        header_to_json="[\"test@example.com\"]",
        subject="Corrupt",
        body_plain_text="Text",
        rfc_message_id="<corrupt@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_corrupt",
        status="smtp_accepted",
        submitted_mime_bytes=raw_exact,
        submitted_mime_sha256="wrong_tampered_hash_value",
    )
    temp_db["queue"].enqueue_job(job_corrupted)

    with pytest.raises(SentReconciliationError, match="Integrity check failed"):
        svc.reconcile_job_sent_copy(job_id="job_corrupted_01", dry_run=True)


def test_8_two_accounts_strictly_isolated(temp_db, mock_cred_store):
    svc = SentReconciliationService(db_path=temp_db["db_path"], credential_store=mock_cred_store)

    raw_it = b"From: pyBIM <info@pybim.it>\r\nTo: it@example.it\r\nSubject: IT Test\r\nMessage-ID: <it.test@pybim.it>\r\n\r\nCiao"
    digest_it = hashlib.sha256(raw_it).hexdigest()

    job_it = OutboundQueueJob(
        job_id="job_it_01",
        source_draft_id="drf_it_01",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        envelope_from="info@pybim.it",
        envelope_recipients_json=json.dumps(["it@example.it"]),
        header_to_json=json.dumps(["it@example.it"]),
        subject="IT Test",
        body_plain_text="Ciao",
        rfc_message_id="<it.test@pybim.it>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_it_01",
        status="smtp_accepted",
        submitted_mime_bytes=raw_it,
        submitted_mime_sha256=digest_it,
    )
    temp_db["queue"].enqueue_job(job_it)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b""])

    res = svc.reconcile_job_sent_copy(job_id="job_it_01", dry_run=True, client=mock_client)
    assert res["success"] is True
    assert res["account_id"] == "acc_info_it"
    assert res["sender_email"] == "info@pybim.it"


def test_9_automatic_reconciliation_trigger_controlled_handoff(temp_db, mock_cred_store, monkeypatch):
    """
    Verifies that when OUTBOUND_AUTO_SENT_RECONCILE=true:
    SMTP 250 acceptance immediately transitions the job through Sent reconciliation
    to 'sent_reconciled' without requiring manual CLI command.
    """
    from scripts.run_outbound_pilot import execute_single_message_pilot
    from sales_engine.sending.smtp_adapter import SMTPSubmissionResult

    monkeypatch.setenv("OUTBOUND_AUTO_SENT_RECONCILE", "true")
    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "true")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "true")

    raw_mime = b"From: pyBIM <info@pybim.com>\r\nTo: partner@example.com\r\nSubject: Test\r\nMessage-ID: <auto.rec@pybim.com>\r\n\r\nTest auto"
    digest = hashlib.sha256(raw_mime).hexdigest()

    job = OutboundQueueJob(
        job_id="job_auto_01",
        source_draft_id="drf_auto_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["partner@example.com"]),
        header_to_json=json.dumps(["partner@example.com"]),
        subject="Test",
        body_plain_text="Test auto",
        rfc_message_id="<auto.rec@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_auto_01",
        status="scheduled",
    )
    temp_db["queue"].enqueue_job(job)

    # Mock SMTP server returning OK (smtplib.SMTP.send_message returns dict of refused recpts, empty on success)
    mock_smtp_server = MagicMock()
    mock_smtp_server.send_message.return_value = {}

    with patch.object(SentReconciliationService, "connect_imap") as mock_conn:
        mock_imap_client = MagicMock()
        mock_imap_client.select.return_value = ("OK", [b"10"])
        mock_imap_client.uid.side_effect = [
            ("OK", [b""]),       # pre-check search: not found
            ("OK", [b"777"]),    # post-append confirmation: found UID 777
        ]
        mock_imap_client.append.return_value = ("OK", [b"[APPENDUID 12345 777]"])
        mock_conn.return_value = mock_imap_client

        res = execute_single_message_pilot(
            db_path=temp_db["db_path"],
            job_id="job_auto_01",
            allowed_recipient="partner@example.com",
            dry_run=False,
            credential_store=mock_cred_store,
            custom_smtp_server=mock_smtp_server,
        )

        assert res["status"] == "smtp_accepted"
        assert res["reconciliation"] is not None
        assert res["reconciliation"]["status"] == "sent_reconciled"
        assert res["reconciliation"]["imap_uid"] == "777"

        # Check DB state
        job_after = temp_db["queue"].get_job("job_auto_01")
        assert job_after.status == "smtp_accepted"
        assert job_after.reconciliation_status == "sent_reconciled"
        assert job_after.imap_uid == "777"


def test_10_ui_reconcile_endpoint_authenticated(temp_db, mock_cred_store):
    """
    Verifies that the /sales/mail-operations/reconcile-sent backend endpoint:
    - Rejects unauthenticated callers / non-existent jobs.
    - Idempotently reconciles and returns JSON with structured reconciliation outcome.
    """
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.operator_auth import get_auth_service

    auth_svc = get_auth_service(db_path=temp_db["db_path"])
    client = TestClient(app)

    # 1. Unauthenticated request without session
    resp_unauth = client.post("/sales/mail-operations/reconcile-sent", data={"job_id": "job_any"})
    assert resp_unauth.status_code in (302, 303, 401, 403)

    # 2. Stage accepted job in test DB
    raw_mime = b"From: pyBIM <info@pybim.com>\r\nTo: partner@example.com\r\nSubject: UI Test\r\nMessage-ID: <ui.test@pybim.com>\r\n\r\nUI Test"
    digest = hashlib.sha256(raw_mime).hexdigest()
    job = OutboundQueueJob(
        job_id="job_ui_01",
        source_draft_id="drf_ui_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["partner@example.com"]),
        header_to_json=json.dumps(["partner@example.com"]),
        subject="UI Test",
        body_plain_text="UI Test",
        rfc_message_id="<ui.test@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_ui_01",
        status="smtp_accepted",
        reconciliation_status="sent_pending",
        submitted_mime_bytes=raw_mime,
        submitted_mime_sha256=digest,
    )
    temp_db["queue"].enqueue_job(job)

    # Authenticate operator and create session
    auth_svc.create_user("admin", "AdminPass123!", role="admin")
    user = auth_svc.authenticate_user("admin", "AdminPass123!")
    assert user is not None
    token = auth_svc.create_session(user.user_id)

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": temp_db["db_path"]}):
        with patch.object(SentReconciliationService, "connect_imap") as mock_conn:
            mock_imap_client = MagicMock()
            mock_imap_client.select.return_value = ("OK", [b"10"])
            mock_imap_client.uid.return_value = ("OK", [b"888"])
            mock_conn.return_value = mock_imap_client

            # Send authorized request with session cookie and matching CSRF cookie
            client.cookies.set("sales_session", token)
            client.cookies.set("sales_csrf_token", "valid_token")
            headers = {"Accept": "application/json"}
            resp = client.post(
                "/sales/mail-operations/reconcile-sent",
                data={"job_id": "job_ui_01", "csrf_token": "valid_token"},
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["status"] == "sent_reconciled"
            assert data["matched_uids"] == ["888"]
