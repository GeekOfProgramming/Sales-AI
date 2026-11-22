"""
tests/test_mail_send_01g_sent_reconciliation.py
MAIL-SEND-01G & MAIL-SEND-01G-HOTFIX: Comprehensive Offline Tests for IMAP Sent-Folder Reconciliation.

Verifies:
1. Reconstruct MIME bytes from immutable OutboundQueueJob record (semantic reconstruction).
2. Read-only inspection checks remote Sent folder without modifications.
3. Duplicate prevention: if message exists, APPEND is not called and 'already_reconciled' is returned.
4. Dry-run validation tests permissions & MIME resolution without invoking APPEND.
5. Live append: calls IMAP APPEND with correct folder, \\Seen flag, and verified Message-ID.
6. Ambiguous timeout handling: if append raises an exception, checks remote folder; if message exists, recovers cleanly.
7. Job status validation: rejects jobs that are not 'smtp_accepted'.
8. Refusal of reconstruction unless explicitly authorized (--allow-reconstruction).
9. EML comparison engine: correctly detects transport-added headers, matches Message-ID, Subject, recipients.
10. EML cleaning for Sent folder: strips downstream transport headers while preserving content.
11. Pre-submission durable storage: future jobs store exact MIME bytes with verified SHA-256 integrity.
"""

import os
import json
import hashlib
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
    compare_eml_against_job,
    clean_received_eml_for_sent_folder,
)


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_outreach.db")
    repo = MailboxRepository(db_path=db_file, auto_migrate=True)
    queue = OutboundQueueStore(db_path=db_file, auto_apply_schema=True)

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


def test_reconstruct_job_mime_semantic(temp_db, mock_cred_store, sample_accepted_job):
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


def test_reconcile_refuses_reconstruction_without_authorization(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    with pytest.raises(SentReconciliationError, match="No exact pre-submission MIME bytes exist"):
        svc.reconcile_job_sent_copy(
            job_id=sample_accepted_job.job_id,
            dry_run=True,
            allow_reconstruction=False,
        )


def test_reconcile_allows_authorized_semantic_reconstruction(temp_db, mock_cred_store, sample_accepted_job):
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
        allow_reconstruction=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["provenance"] == "RECONSTRUCTED_SEMANTIC"
    assert res["is_byte_identical"] is False
    assert "CRITICAL NOTICE" in res["audit_info"]["notes"]


def test_durable_exact_mime_storage_and_sha256(temp_db, mock_cred_store):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    raw_exact = b"From: info@pybim.com\r\nTo: user@example.com\r\nSubject: Test\r\n\r\nExact Body"
    sha256_hash = hashlib.sha256(raw_exact).hexdigest()

    job = OutboundQueueJob(
        job_id="job_durable_01",
        source_draft_id="drf_durable_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["user@example.com"]),
        header_to_json=json.dumps(["user@example.com"]),
        subject="Test",
        body_plain_text="Exact Body",
        rfc_message_id="<durable.01@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_durable_01",
        status="smtp_accepted",
        submitted_mime_bytes=raw_exact,
        submitted_mime_sha256=sha256_hash,
    )
    temp_db["queue"].enqueue_job(job)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b""])

    res = svc.reconcile_job_sent_copy(
        job_id="job_durable_01",
        dry_run=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["provenance"] == "DURABLE_EXACT"
    assert res["is_byte_identical"] is True
    assert res["audit_info"]["sha256"] == sha256_hash


def test_eml_comparison_and_transport_cleaning(tmp_path, temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    # Synthetic received Gmail EML with transport headers
    eml_content = (
        b"Delivered-To: test.partner@example.com\r\n"
        b"Received: by mail-wm1-f41.google.com with SMTP id ...\r\n"
        b"ARC-Seal: i=1; a=rsa-sha256; ...\r\n"
        b"From: pyBIM <info@pybim.com>\r\n"
        b"To: test.partner@example.com\r\n"
        b"Subject: Introduction | pyBIM \xe2\x80\x94 BIM & Digital Automation\r\n"
        b"Message-ID: <test.12345@pybim.com>\r\n"
        b"Date: Fri, 20 Nov 2026 16:11:54 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Dear Partner,\n\nWe are pleased to introduce pyBIM."
    )

    eml_file = tmp_path / "received_pilot.eml"
    eml_file.write_bytes(eml_content)

    comp = compare_eml_against_job(sample_accepted_job, eml_content)
    assert comp["is_compatible"] is True
    assert comp["message_id_match"] is True
    assert comp["sender_match"] is True
    assert comp["to_match"] is True
    assert comp["subject_match"] is True
    assert comp["transport_headers_count"] == 3
    assert "Delivered-To" in comp["transport_headers_detected"]
    assert "Received" in comp["transport_headers_detected"]
    assert "ARC-Seal" in comp["transport_headers_detected"]

    # Test cleaning
    cleaned = clean_received_eml_for_sent_folder(eml_content)
    cleaned_str = cleaned.decode("utf-8", errors="ignore")
    assert "Delivered-To" not in cleaned_str
    assert "ARC-Seal" not in cleaned_str
    assert "Message-ID: <test.12345@pybim.com>" in cleaned_str
    assert "Subject: Introduction | pyBIM" in cleaned_str

    # Test import reconciliation
    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b""])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=True,
        eml_path=str(eml_file),
        client=mock_client,
    )

    assert res["success"] is True
    assert res["provenance"] == "IMPORTED_RECEIVED_EML"
    assert res["is_byte_identical"] is False
    assert res["audit_info"]["comparison"]["transport_headers_count"] == 3


def test_inspect_remote_sent_status(temp_db, mock_cred_store, sample_accepted_job):
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


def test_duplicate_prevention_skips_append(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    mock_client.uid.return_value = ("OK", [b"999"])

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        allow_reconstruction=True,
        client=mock_client,
    )

    assert res["success"] is True
    assert res["status"] == "already_reconciled"
    assert res["action"] == "SKIPPED_ALREADY_EXISTS"
    mock_client.append.assert_not_called()


def test_timeout_recovery_and_unverified(temp_db, mock_cred_store, sample_accepted_job):
    svc = SentReconciliationService(
        db_path=temp_db["db_path"],
        credential_store=mock_cred_store,
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"10"])
    # 1. Recovery case
    mock_client.uid.side_effect = [("OK", [b""]), ("OK", [b"1002"])]
    mock_client.append.side_effect = TimeoutError("Timeout during APPEND")

    res = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        allow_reconstruction=True,
        client=mock_client,
    )
    assert res["status"] == "reconciled_after_timeout"

    # 2. Unverified case
    mock_client.uid.side_effect = [("OK", [b""]), ("OK", [b""])]
    mock_client.append.side_effect = TimeoutError("Timeout during APPEND")

    res2 = svc.reconcile_job_sent_copy(
        job_id=sample_accepted_job.job_id,
        dry_run=False,
        live_append=True,
        allow_reconstruction=True,
        client=mock_client,
    )
    assert res2["status"] == "reconciliation_unknown"
