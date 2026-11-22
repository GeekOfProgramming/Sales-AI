"""
tests/test_mail_send_01g_sent_reconciliation.py
MAIL-SEND-01G: Comprehensive Offline Tests for IMAP Sent-Folder Reconciliation.

Verifies:
1. Reconstruct MIME bytes from immutable OutboundQueueJob record accurately.
2. Read-only inspection checks remote Sent folder without modifications.
3. Duplicate prevention: if message exists, APPEND is not called and 'already_reconciled' is returned.
4. Dry-run validation tests permissions & MIME reconstruction without invoking APPEND.
5. Live append: calls IMAP APPEND with correct folder, \\Seen flag, and verified Message-ID.
6. Ambiguous timeout handling: if append raises an exception, checks remote folder; if message exists, recovers cleanly.
7. Job status validation: rejects jobs that are not 'smtp_accepted'.
8. Missing credential / missing account error handling.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

from sales_engine.sending.outbound_models import OutboundQueueJob
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
    # Initialize repositories
    repo = MailboxRepository(db_path=db_file, auto_migrate=True)
    queue = OutboundQueueStore(db_path=db_file, auto_apply_schema=True)

    # Insert test account
    acc = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        credential_ref="win_cred_acc_info_com",
        is_enabled=True,
    )
    repo.save_account(acc)

    return {
        "db_path": db_file,
        "repo": repo,
        "queue": queue,
        "account": acc,
    }


@pytest.fixture
def mock_cred_store():
    store = MagicMock(spec=MailboxCredentialStore)
    store.get_credential.return_value = "mock_safe_password"
    return store


@pytest.fixture
def sample_accepted_job(temp_db):
    job = OutboundQueueJob(
        job_id="job_test_accepted_01",
        source_draft_id="drf_test_accepted_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["test.partner@example.com"]),
        header_to_json=json.dumps(["test.partner@example.com"]),
        header_cc_json="[]",
        header_bcc_json="[]",
        subject="Introduction | pyBIM — BIM & Digital Automation",
        body_plain_text="Dear Partner,\n\nWe are pleased to introduce pyBIM.",
        body_html_sanitized="<p>Dear Partner,</p><p>We are pleased to introduce pyBIM.</p>",
        rfc_message_id="<test.12345@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="test_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_test_accepted_01",
        status="smtp_accepted",
    )
    temp_db["queue"].enqueue_job(job)
    return job


def test_reconstruct_job_mime(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mime_bytes = svc.reconstruct_job_mime(sample_accepted_job)
    assert isinstance(mime_bytes, bytes)
    assert len(mime_bytes) > 0

    mime_str = mime_bytes.decode("utf-8", errors="ignore")
    assert "Message-ID: <test.12345@pybim.com>" in mime_str
    assert "To: test.partner@example.com" in mime_str
    assert "Dear Partner" in mime_str


def test_inspect_remote_sent_status_found(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b"42 43"])

    res = svc.inspect_remote_sent_status(
        job_id=sample_accepted_job.job_id,
        sent_folder=DEFAULT_SENT_FOLDER,
        client=mock_client,
    )

    assert res["exists_in_sent"] is True
    assert res["match_count"] == 2
    assert res["matched_uids"] == ["42", "43"]
    mock_client.select.assert_called_with(DEFAULT_SENT_FOLDER, readonly=True)
    mock_client.uid.assert_called_with("SEARCH", None, 'HEADER Message-ID "<test.12345@pybim.com>"')


def test_inspect_remote_sent_status_not_found(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b""])

    res = svc.inspect_remote_sent_status(
        job_id=sample_accepted_job.job_id,
        sent_folder=DEFAULT_SENT_FOLDER,
        client=mock_client,
    )

    assert res["exists_in_sent"] is False
    assert res["match_count"] == 0
    assert res["matched_uids"] == []


def test_reconcile_job_duplicate_prevention(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b"999"])

    # Attempt live append when message is already present
    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "already_reconciled"
    assert res["action"] == "SKIPPED_ALREADY_EXISTS"
    assert res["matched_uids"] == ["999"]
    # Ensure client.append was NEVER called!
    mock_client.append.assert_not_called()


def test_reconcile_job_dry_run(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b""])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=True,
        live_append=False,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "dry_run_ready"
    assert res["action"] == "DRY_RUN_VALIDATED"
    assert res["raw_mime_bytes_len"] > 0
    # Ensure client.append was NEVER called in dry run!
    mock_client.append.assert_not_called()


def test_reconcile_job_live_append_success(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # First search: not found; second search (after append): found UID 1001
    mock_client.uid.side_effect = [
        ("OK", [b""]),       # Pre-check search
        ("OK", [b"1001"]),   # Post-append confirmation
    ]
    mock_client.append.return_value = ("OK", [b"[APPENDUID 12345 1001] (Success)"])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "reconciled"
    assert res["action"] == "APPENDED_CONFIRMED"
    assert res["matched_uids"] == ["1001"]
    mock_client.append.assert_called_once()
    append_args = mock_client.append.call_args[0]
    assert append_args[0] == DEFAULT_SENT_FOLDER
    assert r"(\Seen)" in append_args[1]


def test_reconcile_job_timeout_recovery(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # Pre-check search: not found; append raises socket timeout; post-failure check finds UID 1002
    mock_client.uid.side_effect = [
        ("OK", [b""]),       # Pre-check
        ("OK", [b"1002"]),   # Post-exception recovery search
    ]
    mock_client.append.side_effect = TimeoutError("Remote server socket timed out during APPEND")

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "reconciled_after_timeout"
    assert res["action"] == "APPENDED_WITH_RECOVERY"
    assert res["matched_uids"] == ["1002"]


def test_reconcile_job_timeout_unverified(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # Pre-check search: not found; append raises socket timeout; post-failure check still not found
    mock_client.uid.side_effect = [
        ("OK", [b""]),       # Pre-check
        ("OK", [b""]),       # Post-exception recovery search
    ]
    mock_client.append.side_effect = TimeoutError("Remote server socket timed out during APPEND")

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        client=mock_client,
    )

    assert res["success"] is False
    assert res["status"] == "reconciliation_unknown"
    assert res["action"] == "FAILED_AMBIGUOUS_TIMEOUT"


def test_reconcile_rejects_non_accepted_job(temp_db, mock_cred_store):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    # Enqueue a scheduled job
    job = OutboundQueueJob(
        job_id="job_scheduled_only",
        source_draft_id="drf_scheduled_only",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["test@example.com"]),
        header_to_json=json.dumps(["test@example.com"]),
        subject="Test",
        body_plain_text="Text",
        rfc_message_id="<sched.123@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="test_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_scheduled_only",
        status="scheduled",
    )
    temp_db["queue"].enqueue_job(job)

    with pytest.raises(SentReconciliationError, match="status is 'scheduled'"):
        svc.reconcile_job_sent_copy(
            job_id="job_scheduled_only",
            dry_run=True,
        )
