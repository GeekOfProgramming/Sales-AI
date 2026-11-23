"""
Regression test suite for MAIL-SEND-DRYRUN-STATE-FIX:
Ensures dry-run pilot sends and worker simulations never consume attempts,
never write simulated SMTP 250 to outbound_jobs, never mutate authoritative status,
preserve immutable pre-submission MIME, and safe metadata repair operates idempotently.
"""

import json
import os
import shutil
import sqlite3
import uuid
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sales_engine.sending.outbound_queue_store import (
    OutboundQueueStore,
    OutboundQueueJob,
    OutboundAttemptRecord,
)
from sales_engine.sending.outbound_worker import OutboundWorker
from sales_engine.sending.smtp_adapter import SMTPSubmissionResult
from scripts.run_outbound_pilot import execute_single_message_pilot


@pytest.fixture
def disposable_db(tmp_path: Path):
    """Creates a clean disposable SQLite database with migrations applied."""
    db_file = tmp_path / "test_outreach.db"
    store = OutboundQueueStore(db_path=str(db_file), auto_apply_schema=True)
    # Ensure tables exist
    with store._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mailbox_drafts (
                draft_id TEXT PRIMARY KEY,
                sender_email TEXT,
                sender_account_id TEXT,
                primary_recipient TEXT,
                status TEXT DEFAULT 'scheduled'
            );
        """)
        conn.commit()
    return str(db_file)


def create_sample_job(
    store: OutboundQueueStore,
    job_id: str = "job_test_dryrun_001",
    account_id: str = "acc_info_it",
    sender_email: str = "info@pybim.it",
    recipient: str = "h.lotfalian98@gmail.com",
    rfc_message_id: str = "<test.message.001@pybim.it>",
    status: str = "scheduled",
    attempt_count: int = 0,
    max_attempts: int = 3,
    submitted_mime_bytes: bytes = b"MIME-TEST-PAYLOAD-BYTES",
) -> OutboundQueueJob:
    """Helper to insert a test job into outbound_jobs."""
    import hashlib
    now_utc = datetime.now(timezone.utc).isoformat()
    sha256_hash = hashlib.sha256(submitted_mime_bytes).hexdigest() if submitted_mime_bytes else None
    job = OutboundQueueJob(
        job_id=job_id,
        source_draft_id=f"drf_{job_id}",
        account_id=account_id,
        sender_email=sender_email,
        envelope_from=sender_email,
        envelope_recipients_json=json.dumps([recipient]),
        header_to_json=json.dumps([recipient]),
        header_cc_json="[]",
        header_bcc_json="[]",
        subject="Test Outbound Subject",
        body_plain_text="Hello world test body",
        body_html_sanitized="<p>Hello world test body</p>",
        rfc_message_id=rfc_message_id,
        candidate_id=None,
        contact_id=None,
        scheduled_for_utc=now_utc,
        scheduled_local_date="2026-11-23",
        scheduled_local_time="09:00:00",
        scheduled_timezone="Europe/Rome",
        approved_by="operator_test",
        approved_at_utc=now_utc,
        status=status,
        version=1,
        idempotency_key=f"idem_{job_id}",
        worker_lease_id=None,
        worker_leased_at_utc=None,
        lease_expires_at_utc=None,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        last_attempt_at_utc=None,
        smtp_response_code=None,
        smtp_response_message=None,
        review_reason=None,
        submitted_mime_bytes=submitted_mime_bytes,
        submitted_mime_sha256=sha256_hash,
        reconciliation_status="not_applicable",
        reconciled_at_utc=None,
        imap_uid=None,
        imap_uidvalidity=None,
        reconciliation_provenance=None,
        reconciliation_attempts=0,
        reconciliation_last_error=None,
        created_at=now_utc,
        updated_at=now_utc,
    )
    return store.enqueue_job(job)


def test_dry_run_attempt_does_not_consume_attempts_or_write_smtp_250(disposable_db):
    """
    Test that recording a 'dry_run_simulated' attempt in OutboundQueueStore:
    1. Inserts the attempt into outbound_attempts for audit logging.
    2. Leaves outbound_jobs.attempt_count UNCHANGED (0).
    3. Keeps outbound_jobs.smtp_response_code as NULL (never writes 250).
    4. Keeps outbound_jobs.smtp_response_message as NULL.
    5. Keeps outbound_jobs.status as 'scheduled'.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(store, job_id="job_dryrun_test_1")

    assert job.attempt_count == 0
    assert job.smtp_response_code is None
    assert job.status == "scheduled"

    # Simulate an attempt record
    now_iso = datetime.now(timezone.utc).isoformat()
    att_record = OutboundAttemptRecord(
        job_id=job.job_id,
        attempt_number=1,
        worker_id="test_harness",
        status="dry_run_simulated",
        smtp_host="mail.register.it",
        smtp_port=587,
        smtp_code=250,
        smtp_message="DRY RUN: Message validated successfully (live send disabled).",
        duration_ms=45,
        started_at_utc=now_iso,
        completed_at_utc=now_iso,
    )

    updated_job = store.record_attempt(
        attempt=att_record,
        new_job_status="scheduled",
        smtp_response_code=250,
        smtp_response_message="DRY RUN: Message validated successfully (live send disabled).",
    )

    # Invariants verification
    assert updated_job.attempt_count == 0, "Dry run must NOT increment attempt_count!"
    assert updated_job.smtp_response_code is None, "Dry run must NOT set smtp_response_code on outbound_jobs!"
    assert updated_job.smtp_response_message is None, "Dry run must NOT set smtp_response_message on outbound_jobs!"
    assert updated_job.status == "scheduled", "Status must remain scheduled!"
    assert updated_job.submitted_mime_bytes == b"MIME-TEST-PAYLOAD-BYTES"

    # Verify audit record was created in outbound_attempts
    with store._get_connection() as conn:
        attempts = conn.execute("SELECT * FROM outbound_attempts WHERE job_id = ?;", (job.job_id,)).fetchall()
        assert len(attempts) == 1
        assert attempts[0]["status"] == "dry_run_simulated"
        assert attempts[0]["smtp_code"] == 250


def test_repeated_dry_runs_do_not_exhaust_max_attempts(disposable_db):
    """
    Simulating 10 consecutive dry-runs must NEVER exhaust max_attempts (3).
    attempt_count must remain 0 at all times.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(store, job_id="job_dryrun_repeat", max_attempts=3)

    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(1, 11):
        att_record = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=i,
            worker_id="test_harness",
            status="dry_run_simulated",
            smtp_host="mail.register.it",
            smtp_port=587,
            smtp_code=250,
            smtp_message="DRY RUN simulated",
            duration_ms=30,
            started_at_utc=now_iso,
            completed_at_utc=now_iso,
        )
        updated_job = store.record_attempt(attempt=att_record, new_job_status="scheduled")
        assert updated_job.attempt_count == 0
        assert updated_job.status == "scheduled"
        assert updated_job.smtp_response_code is None

    # Verify 10 audit attempts logged, but attempt_count still 0
    with store._get_connection() as conn:
        attempts = conn.execute("SELECT COUNT(*) as cnt FROM outbound_attempts WHERE job_id = ?;", (job.job_id,)).fetchone()
        assert attempts["cnt"] == 10

    refetched = store.get_job(job.job_id)
    assert refetched.attempt_count == 0
    assert refetched.status == "scheduled"


def test_dry_run_cannot_overwrite_existing_authoritative_status(disposable_db):
    """
    If a job is already 'smtp_accepted' (e.g. historical pilot), a simulated dry run
    attempt record must NEVER overwrite its status or erase its authoritative SMTP code.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(store, job_id="job_historical_accepted", status="smtp_accepted")
    # Manually set live accepted attributes
    with store._get_connection() as conn:
        conn.execute(
            "UPDATE outbound_jobs SET smtp_response_code = 250, smtp_response_message = '2.0.0 OK live' WHERE job_id = ?;",
            (job.job_id,),
        )

    # Execute simulation
    now_iso = datetime.now(timezone.utc).isoformat()
    att_record = OutboundAttemptRecord(
        job_id=job.job_id,
        attempt_number=2,
        worker_id="test_worker",
        status="dry_run_simulated",
        smtp_host="mail.register.it",
        smtp_port=587,
        smtp_code=250,
        smtp_message="DRY RUN simulated",
        started_at_utc=now_iso,
        completed_at_utc=now_iso,
    )
    updated_job = store.record_attempt(attempt=att_record, new_job_status="scheduled")

    # Authoritative status must be preserved
    assert updated_job.status == "smtp_accepted"
    assert updated_job.smtp_response_code == 250
    assert updated_job.smtp_response_message == "2.0.0 OK live"


def test_real_live_attempt_still_increments_attempts_and_records_codes(disposable_db):
    """
    Verify that live attempts (status='success' or status='failed') DO increment attempt_count
    and DO record authoritative smtp_response_code and last_attempt_at_utc.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(store, job_id="job_live_test")
    assert job.attempt_count == 0

    now_iso = datetime.now(timezone.utc).isoformat()
    att_record = OutboundAttemptRecord(
        job_id=job.job_id,
        attempt_number=1,
        worker_id="live_worker",
        status="success",
        smtp_host="mail.register.it",
        smtp_port=587,
        smtp_code=250,
        smtp_message="2.0.0 Mail accepted",
        duration_ms=450,
        started_at_utc=now_iso,
        completed_at_utc=now_iso,
    )
    updated = store.record_attempt(
        attempt=att_record,
        new_job_status="smtp_accepted",
        smtp_response_code=250,
        smtp_response_message="2.0.0 Mail accepted",
    )

    assert updated.status == "smtp_accepted"
    assert updated.attempt_count == 1
    assert updated.smtp_response_code == 250
    assert updated.smtp_response_message == "2.0.0 Mail accepted"
    assert updated.last_attempt_at_utc is not None
    assert updated.reconciliation_status == "sent_pending"


def test_pilot_send_dry_run_pipeline_leaves_job_clean(disposable_db, monkeypatch):
    """
    Test execute_single_message_pilot with dry-run mode:
    Ensures that the entire end-to-end pilot runner preserves job.attempt_count = 0
    and smtp_response_code = None.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(store, job_id="job_pilot_pipeline_test")

    # Mock SMTP adapter to return simulated success
    with patch("scripts.run_outbound_pilot.RegisterItSMTPAdapter") as MockAdapter, \
         patch("scripts.run_outbound_pilot.MailboxCredentialStore") as MockCreds:

        mock_adapter_inst = MagicMock()
        mock_adapter_inst.host = "authsmtp.register.it"
        mock_adapter_inst.port = 587
        mock_adapter_inst.send_message.return_value = SMTPSubmissionResult(
            success=True,
            status_code=250,
            response_message="DRY RUN: Message validated successfully (live send disabled).",
            duration_ms=12,
            is_simulated=True,
        )
        MockAdapter.return_value = mock_adapter_inst

        mock_creds_inst = MagicMock()
        mock_creds_inst.get_account.return_value = {
            "account_id": "acc_info_it",
            "email_address": "info@pybim.it",
            "credential_ref": "reg_info_it",
        }
        MockCreds.return_value = mock_creds_inst

        result = execute_single_message_pilot(
            db_path=disposable_db,
            job_id=job.job_id,
            allowed_recipient="h.lotfalian98@gmail.com",
            dry_run=True,
        )

        assert result["success"] is True
        assert result["mode"] == "DRY_RUN"
        assert result["status"] == "dry_run_simulated"

    # Verify job state in database
    refreshed_job = store.get_job(job.job_id)
    assert refreshed_job.status == "scheduled"
    assert refreshed_job.attempt_count == 0, "Pipeline dry run must leave attempt_count == 0"
    assert refreshed_job.smtp_response_code is None, "Pipeline dry run must leave smtp_response_code == None"
    assert refreshed_job.smtp_response_message is None


def test_repair_dry_run_job_metadata(disposable_db):
    """
    Test the repair method repair_simulated_dry_run_job_metadata:
    1. Creates a corrupted job simulating the bug (attempt_count=1, smtp_code=250, msg='DRY RUN...').
    2. Runs repair: verifies attempt_count -> 0, smtp_code -> None, msg -> None.
    3. Preserves immutable MIME bytes and SHA-256.
    4. Repeat repair is idempotent (ALREADY_REPAIRED).
    5. Rejects repair if any live attempt exists.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    job = create_sample_job(
        store,
        job_id="job_e2a78848156b_mock",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        recipient="h.lotfalian98@gmail.com",
        rfc_message_id="<179541133922.2276.15229845794957036866@pybim.it>",
        status="scheduled",
        attempt_count=1,
    )

    # Simulate the bug on the job row
    with store._get_connection() as conn:
        conn.execute("""
            UPDATE outbound_jobs
            SET attempt_count = 1,
                smtp_response_code = 250,
                smtp_response_message = 'DRY RUN: Message validated successfully (live send disabled).',
                last_attempt_at_utc = '2026-11-23T05:23:16.079566+00:00'
            WHERE job_id = ?;
        """, (job.job_id,))
        # And insert a simulated attempt into outbound_attempts
        conn.execute("""
            INSERT INTO outbound_attempts (
                attempt_id, job_id, attempt_number, worker_id, status,
                smtp_host, smtp_port, smtp_code, smtp_message,
                duration_ms, started_at_utc, completed_at_utc
            ) VALUES (?, ?, 1, 'harness', 'dry_run_simulated', 'authsmtp.register.it', 587, 250, 'DRY RUN', 10, 'now', 'now');
        """, (str(uuid.uuid4()), job.job_id))

    # Pre-repair state check
    corrupted = store.get_job(job.job_id)
    assert corrupted.attempt_count == 1
    assert corrupted.smtp_response_code == 250

    # 1. Execute repair
    res = store.repair_simulated_dry_run_job_metadata(
        job_id=job.job_id,
        expected_account_id="acc_info_it",
        expected_sender_email="info@pybim.it",
        expected_recipient_substr="h.lotfalian98@gmail.com",
        expected_rfc_message_id="<179541133922.2276.15229845794957036866@pybim.it>",
        operator_username="test_operator",
    )

    assert res["action"] == "REPAIRED"
    assert res["attempt_count"] == 0
    assert res["smtp_response_code"] is None
    assert res["smtp_response_message"] is None

    # Check repaired job in SQLite
    repaired = store.get_job(job.job_id)
    assert repaired.status == "scheduled"
    assert repaired.attempt_count == 0
    assert repaired.smtp_response_code is None
    assert repaired.smtp_response_message is None
    assert repaired.last_attempt_at_utc is None
    assert repaired.submitted_mime_bytes == b"MIME-TEST-PAYLOAD-BYTES"
    assert repaired.submitted_mime_sha256 == corrupted.submitted_mime_sha256

    # 2. Idempotency test: Re-run repair
    res_repeat = store.repair_simulated_dry_run_job_metadata(
        job_id=job.job_id,
        expected_account_id="acc_info_it",
        expected_sender_email="info@pybim.it",
        expected_recipient_substr="h.lotfalian98@gmail.com",
        expected_rfc_message_id="<179541133922.2276.15229845794957036866@pybim.it>",
    )
    assert res_repeat["action"] == "ALREADY_REPAIRED"
    assert res_repeat["attempt_count"] == 0

    # 3. Safety test: If job has a LIVE attempt, repair must abort
    with store._get_connection() as conn:
        conn.execute("""
            INSERT INTO outbound_attempts (
                attempt_id, job_id, attempt_number, worker_id, status,
                smtp_host, smtp_port, smtp_code, smtp_message,
                duration_ms, started_at_utc, completed_at_utc
            ) VALUES (?, ?, 2, 'worker', 'success', 'mail.register.it', 587, 250, 'OK', 10, 'now', 'now');
        """, (str(uuid.uuid4()), job.job_id))
        # Corrupt attempt_count again
        conn.execute("UPDATE outbound_jobs SET attempt_count = 2 WHERE job_id = ?;", (job.job_id,))

    with pytest.raises(RuntimeError, match="CRITICAL SAFETY VIOLATION"):
        store.repair_simulated_dry_run_job_metadata(
            job_id=job.job_id,
            expected_account_id="acc_info_it",
            expected_sender_email="info@pybim.it",
            expected_recipient_substr="h.lotfalian98@gmail.com",
        )
