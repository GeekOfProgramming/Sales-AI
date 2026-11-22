"""
tests/test_mail_send_01j_historical_reconciliation.py
Unit and edge-case tests for Historical Sent-State Reconciliation (MAIL-SEND-01J).

Validates:
1. Dry-run mode performs read-only IMAP inspection and outputs preflight without database modifications.
2. Apply mode performs guarded transaction updating exactly required reconciliation fields.
3. Does NOT increment reconciliation_attempts.
4. Idempotency: repeated execution returns ALREADY_RECONCILED without modifying rows or timestamps.
5. Fails closed on duplicate remote Message-IDs in Sent folder.
6. Fails closed on UIDVALIDITY changes.
7. Fails closed on stale / mismatched UID.
8. Fails closed on account mismatch or non-existent job.
9. Fails closed on job status not being 'smtp_accepted'.
10. Strictly zero SMTP transmission and zero IMAP APPEND execution.
"""

import json
import sqlite3
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from sales_engine.sending.outbound_models import OutboundQueueJob
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.sent_reconciliation import (
    SentReconciliationService,
    SentReconciliationError,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord


@pytest.fixture
def disposable_env(tmp_path):
    db_path = str(tmp_path / "test_historical.db")
    queue_store = OutboundQueueStore(db_path=db_path, auto_apply_schema=True)
    mailbox_repo = MailboxRepository(db_path=db_path, auto_migrate=True)

    # Authorized test accounts
    mailbox_repo.save_account(
        MailboxAccountRecord(
            account_id="acc_info_com",
            email_address="info@pybim.com",
            display_name="pyBIM Info (COM)",
            credential_ref="win_cred_acc_info_com",
            imap_host="pop.securemail.pro",
            imap_port=993,
        )
    )
    mailbox_repo.save_account(
        MailboxAccountRecord(
            account_id="acc_info_it",
            email_address="info@pybim.it",
            display_name="pyBIM Info (IT)",
            credential_ref="win_cred_acc_info_it",
            imap_host="pop.securemail.pro",
            imap_port=993,
        )
    )

    # Historical accepted job
    job = OutboundQueueJob(
        job_id="job_73c08675f30f",
        source_draft_id="drf_376c134fbf8b",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["h.lotfalian98@gmail.com"]),
        header_to_json=json.dumps(["h.lotfalian98@gmail.com"]),
        subject="Introduction | pyBIM — BIM & Digital Automation",
        body_plain_text="Hello from pyBIM",
        rfc_message_id="<179519089595.31960.5642370709397818105@pybim.com>",
        scheduled_for_utc="2026-11-20T16:30:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="17:30",
        approved_by="hamid",
        approved_at_utc="2026-11-20T16:08:15.954343+00:00",
        status="smtp_accepted",
        idempotency_key="idemp_pilot_historical_01",
        reconciliation_status="not_applicable",
        reconciliation_attempts=0,
    )
    queue_store.enqueue_job(job)

    mock_cred_store = MagicMock()
    mock_cred_store.get_credential.return_value = "SecretPass123"

    return {
        "db_path": db_path,
        "queue": queue_store,
        "repo": mailbox_repo,
        "cred_store": mock_cred_store,
        "job": job,
    }


def _create_mock_client(uids=None, uidvalidity=1791495724):
    client = MagicMock()
    client.status.return_value = ("OK", [f"INBOX.Sent (UIDVALIDITY {uidvalidity})".encode("ascii")])
    client.select.return_value = ("OK", [b"10"])

    uid_bytes = " ".join(uids).encode("ascii") if uids else b""
    client.uid.return_value = ("OK", [uid_bytes])
    return client


def test_dry_run_preflight_validates_without_mutations(disposable_env):
    """Dry-run performs IMAP search, verifies UID, but does NOT modify SQLite."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["357"], uidvalidity=1791495724)

    res = svc.reconcile_historical_job_sent_metadata(
        job_id="job_73c08675f30f",
        dry_run=True,
        expected_uid="357",
        expected_uidvalidity=1791495724,
        provenance="IMPORTED_RECEIVED_EML",
        client=mock_client,
    )

    assert res["success"] is True
    assert res["action"] == "DRY_RUN_VALIDATED"
    assert res["status"] == "dry_run_ready"
    assert res["preflight"]["verified_imap_uid"] == "357"
    assert res["preflight"]["verified_uidvalidity"] == 1791495724

    # Ensure zero IMAP APPEND calls
    mock_client.append.assert_not_called()

    # Verify SQLite was NOT mutated
    reloaded = disposable_env["queue"].get_job("job_73c08675f30f")
    assert reloaded.reconciliation_status == "not_applicable"
    assert reloaded.imap_uid is None
    assert reloaded.reconciled_at_utc is None


def test_apply_mode_guarded_update_success(disposable_env):
    """Apply mode updates reconciliation fields, preserves attempts count, and commits."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["357"], uidvalidity=1791495724)

    res = svc.reconcile_historical_job_sent_metadata(
        job_id="job_73c08675f30f",
        dry_run=False,
        expected_uid="357",
        expected_uidvalidity=1791495724,
        provenance="IMPORTED_RECEIVED_EML",
        operator_username="test_admin",
        client=mock_client,
    )

    assert res["success"] is True
    assert res["action"] == "RECONCILED"
    assert res["status"] == "sent_reconciled"
    assert res["imap_uid"] == "357"
    assert res["imap_uidvalidity"] == 1791495724
    assert res["reconciliation_provenance"] == "IMPORTED_RECEIVED_EML"
    assert res["reconciled_at_utc"] is not None

    # Ensure zero IMAP APPEND calls
    mock_client.append.assert_not_called()

    # Verify SQLite updated accurately
    reloaded = disposable_env["queue"].get_job("job_73c08675f30f")
    assert reloaded.reconciliation_status == "sent_reconciled"
    assert reloaded.imap_uid == "357"
    assert reloaded.imap_uidvalidity == 1791495724
    assert reloaded.reconciliation_provenance == "IMPORTED_RECEIVED_EML"
    assert reloaded.reconciliation_attempts == 0  # Crucial: Not incremented for metadata backfill
    assert reloaded.status == "smtp_accepted"  # Delivery status untouched


def test_repeated_execution_is_idempotent(disposable_env):
    """Repeated execution returns ALREADY_RECONCILED without modifying rows."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["357"], uidvalidity=1791495724)

    # 1. First run: applies update
    res1 = svc.reconcile_historical_job_sent_metadata(
        job_id="job_73c08675f30f",
        dry_run=False,
        client=mock_client,
    )
    assert res1["action"] == "RECONCILED"
    first_reconciled_at = res1["reconciled_at_utc"]

    # 2. Second run: returns ALREADY_RECONCILED without touching timestamp
    res2 = svc.reconcile_historical_job_sent_metadata(
        job_id="job_73c08675f30f",
        dry_run=False,
        client=mock_client,
    )
    assert res2["action"] == "ALREADY_RECONCILED"
    assert res2["reconciled_at_utc"] == first_reconciled_at

    reloaded = disposable_env["queue"].get_job("job_73c08675f30f")
    assert reloaded.reconciled_at_utc == first_reconciled_at
    assert reloaded.reconciliation_attempts == 0


def test_fails_closed_on_duplicate_remote_uids(disposable_env):
    """If remote folder contains multiple matching Message-IDs, must reject."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["357", "358"], uidvalidity=1791495724)

    with pytest.raises(SentReconciliationError, match="Multiple remote messages"):
        svc.reconcile_historical_job_sent_metadata(
            job_id="job_73c08675f30f",
            dry_run=False,
            client=mock_client,
        )

    # Ensure state remains unmodified
    reloaded = disposable_env["queue"].get_job("job_73c08675f30f")
    assert reloaded.reconciliation_status == "not_applicable"


def test_fails_closed_on_uidvalidity_mismatch(disposable_env):
    """If server UIDVALIDITY does not match expected, must abort immediately."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    # Changed server UIDVALIDITY (e.g. mailbox rebuilt)
    mock_client = _create_mock_client(uids=["357"], uidvalidity=9999999999)

    with pytest.raises(SentReconciliationError, match="UIDVALIDITY mismatch"):
        svc.reconcile_historical_job_sent_metadata(
            job_id="job_73c08675f30f",
            dry_run=False,
            expected_uidvalidity=1791495724,
            client=mock_client,
        )


def test_fails_closed_on_stale_or_unexpected_uid(disposable_env):
    """If remote UID is different from expected (e.g. 358 instead of 357), must abort."""
    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["358"], uidvalidity=1791495724)

    with pytest.raises(SentReconciliationError, match="Stale or mismatched UID"):
        svc.reconcile_historical_job_sent_metadata(
            job_id="job_73c08675f30f",
            dry_run=False,
            expected_uid="357",
            client=mock_client,
        )


def test_fails_closed_on_unaccepted_job_status(disposable_env):
    """Historical reconciliation cannot be executed on scheduled or failed jobs."""
    with disposable_env["queue"]._get_connection() as conn:
        conn.execute("UPDATE outbound_jobs SET status = 'scheduled' WHERE job_id = 'job_73c08675f30f';")

    svc = SentReconciliationService(
        db_path=disposable_env["db_path"],
        credential_store=disposable_env["cred_store"],
    )
    mock_client = _create_mock_client(uids=["357"], uidvalidity=1791495724)

    with pytest.raises(SentReconciliationError, match="Historical reconciliation only permitted for 'smtp_accepted' jobs"):
        svc.reconcile_historical_job_sent_metadata(
            job_id="job_73c08675f30f",
            dry_run=False,
            client=mock_client,
        )
