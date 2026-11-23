"""
Tests for MAIL-SEND-PILOT-SCHEDULE-GUARD:
Prevent Live Pilot Transmission Before Scheduled Time.
Strict containment rules:
1. Early live transmission rejected with clear error.
2. Due live transmission allowed when scheduled_for_utc <= now_utc.
3. Missing, malformed, or naive scheduled timestamps fail closed.
4. Dry-run validation of a future scheduled job remains safe and allowed.
5. No SMTP socket call, no attempt consumption, and no metadata mutation on early rejection.
6. Existing historical accepted jobs remain unaffected.
"""

import json
import os
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sales_engine.sending.outbound_models import (
    OutboundQueueJob,
    OutboundAttemptRecord,
)
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.smtp_adapter import SMTPSubmissionResult
from scripts.run_outbound_pilot import execute_single_message_pilot


@pytest.fixture
def disposable_db(tmp_path: Path):
    """Creates a clean disposable SQLite database with migrations applied."""
    db_file = tmp_path / "test_outreach.db"
    store = OutboundQueueStore(db_path=str(db_file), auto_apply_schema=True)
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


def create_job(
    store: OutboundQueueStore,
    job_id: str = "job_pilot_sched_001",
    account_id: str = "acc_info_it",
    sender_email: str = "info@pybim.it",
    recipient: str = "h.lotfalian98@gmail.com",
    rfc_message_id: str = "<sched.test.001@pybim.it>",
    status: str = "scheduled",
    scheduled_for_utc: str = "2026-11-26T08:00:00Z",
    attempt_count: int = 0,
    submitted_mime_bytes: bytes = b"MIME-TEST-PAYLOAD-FOR-SCHEDULE-GUARD",
) -> OutboundQueueJob:
    """Helper to insert an outbound job with specific schedule timestamp."""
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
        subject="Pilot Schedule Guard Subject",
        body_plain_text="Schedule Guard test body",
        body_html_sanitized="<p>Schedule Guard test body</p>",
        rfc_message_id=rfc_message_id,
        candidate_id=None,
        contact_id=None,
        scheduled_for_utc=scheduled_for_utc,
        scheduled_local_date="2026-11-26",
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
        max_attempts=3,
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


def test_early_live_submission_rejected(disposable_db, monkeypatch):
    """
    Ensure live pilot transmission for a future scheduled job is rejected
    BEFORE opening an SMTP connection or recording any attempt.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    # Schedule 3 days in the future
    future_time = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    job = create_job(store, job_id="job_future_live", scheduled_for_utc=future_time)

    # Set live sending environment variables
    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "true")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "true")

    with patch("scripts.run_outbound_pilot.RegisterItSMTPAdapter") as MockAdapter, \
         patch("scripts.run_outbound_pilot.MailboxCredentialStore") as MockCreds:

        mock_adapter_inst = MagicMock()
        MockAdapter.return_value = mock_adapter_inst

        mock_creds_inst = MagicMock()
        mock_creds_inst.get_account.return_value = {
            "account_id": "acc_info_it",
            "email_address": "info@pybim.it",
            "credential_ref": "reg_info_it",
        }
        mock_creds_inst.get_credential.return_value = "fake_dpapi_secret"
        MockCreds.return_value = mock_creds_inst

        # Attempt live send on future job
        with pytest.raises(ValueError, match="Live transmission blocked: Pilot job .* is scheduled for future delivery"):
            execute_single_message_pilot(
                db_path=disposable_db,
                job_id=job.job_id,
                allowed_recipient="h.lotfalian98@gmail.com",
                dry_run=False,
                credential_store=mock_creds_inst,
            )

        # Invariant checks: SMTP adapter was NEVER called
        assert mock_adapter_inst.send_message.call_count == 0

    # Invariant checks: Job metadata UNTOUCHED
    job_after = store.get_job(job.job_id)
    assert job_after.status == "scheduled"
    assert job_after.attempt_count == 0
    assert job_after.smtp_response_code is None
    assert job_after.last_attempt_at_utc is None

    # Invariant checks: Zero attempt records created
    with store._get_connection() as conn:
        attempts = conn.execute("SELECT * FROM outbound_attempts WHERE job_id = ?;", (job.job_id,)).fetchall()
        assert len(attempts) == 0


def test_dry_run_of_future_job_remains_safe_and_allowed(disposable_db):
    """
    Dry-run of a future scheduled job must be allowed so operators can inspect
    and validate the payload and configuration without transmitting.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    job = create_job(store, job_id="job_future_dryrun", scheduled_for_utc=future_time)

    with patch("scripts.run_outbound_pilot.RegisterItSMTPAdapter") as MockAdapter, \
         patch("scripts.run_outbound_pilot.MailboxCredentialStore") as MockCreds:

        mock_adapter_inst = MagicMock()
        mock_adapter_inst.host = "authsmtp.register.it"
        mock_adapter_inst.port = 587
        mock_adapter_inst.send_message.return_value = SMTPSubmissionResult(
            success=True,
            status_code=250,
            response_message="DRY RUN: Message validated successfully (live send disabled).",
            duration_ms=10,
            is_simulated=True,
        )
        MockAdapter.return_value = mock_adapter_inst

        mock_creds_inst = MagicMock()
        mock_creds_inst.get_account.return_value = {
            "account_id": "acc_info_it",
            "email_address": "info@pybim.it",
            "credential_ref": "reg_info_it",
        }
        mock_creds_inst.get_credential.return_value = "fake_dpapi_secret"
        MockCreds.return_value = mock_creds_inst

        result = execute_single_message_pilot(
            db_path=disposable_db,
            job_id=job.job_id,
            allowed_recipient="h.lotfalian98@gmail.com",
            dry_run=True,
            credential_store=mock_creds_inst,
        )

        assert result["success"] is True
        assert result["mode"] == "DRY_RUN"
        assert result["status"] == "dry_run_simulated"

    # Job remains scheduled with 0 attempts consumed
    job_after = store.get_job(job.job_id)
    assert job_after.status == "scheduled"
    assert job_after.attempt_count == 0
    assert job_after.smtp_response_code is None


def test_due_live_submission_allowed(disposable_db, monkeypatch):
    """
    When scheduled_for_utc is in the past (job is due), live transmission
    passes the schedule guard.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    # Scheduled 10 minutes in the past
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    job = create_job(store, job_id="job_due_live", scheduled_for_utc=past_time)

    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "true")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "true")

    with patch("scripts.run_outbound_pilot.RegisterItSMTPAdapter") as MockAdapter, \
         patch("scripts.run_outbound_pilot.MailboxCredentialStore") as MockCreds:

        mock_adapter_inst = MagicMock()
        mock_adapter_inst.host = "authsmtp.register.it"
        mock_adapter_inst.port = 587
        mock_adapter_inst.send_message.return_value = SMTPSubmissionResult(
            success=True,
            status_code=250,
            response_message="2.0.0 OK Live Pilot Delivery",
            duration_ms=250,
            is_simulated=False,
        )
        MockAdapter.return_value = mock_adapter_inst

        mock_creds_inst = MagicMock()
        mock_creds_inst.get_account.return_value = {
            "account_id": "acc_info_it",
            "email_address": "info@pybim.it",
            "credential_ref": "reg_info_it",
        }
        mock_creds_inst.get_credential.return_value = "fake_dpapi_secret"
        MockCreds.return_value = mock_creds_inst

        res = execute_single_message_pilot(
            db_path=disposable_db,
            job_id=job.job_id,
            allowed_recipient="h.lotfalian98@gmail.com",
            dry_run=False,
            credential_store=mock_creds_inst,
        )

        assert res["success"] is True
        assert res["mode"] == "LIVE_SEND"
        assert res["status"] == "smtp_accepted"
        assert mock_adapter_inst.send_message.call_count == 1

    job_after = store.get_job(job.job_id)
    assert job_after.status == "smtp_accepted"
    assert job_after.attempt_count == 1
    assert job_after.smtp_response_code == 250


@pytest.mark.parametrize("bad_timestamp, expected_err", [
    ("", "missing or invalid scheduled timestamp"),
    ("   ", "malformed scheduled_for_utc timestamp"),
    ("not-a-timestamp", "malformed scheduled_for_utc timestamp"),
    ("2026-11-26 08:00:00", "timezone-naive"),  # Naive datetime
])
def test_missing_or_invalid_timestamp_rejected(disposable_db, bad_timestamp, expected_err):
    """
    Missing, malformed, or naive scheduled timestamps fail closed immediately.
    """
    store = OutboundQueueStore(db_path=disposable_db, auto_apply_schema=False)
    # Insert job with valid initially then corrupt via SQL to simulate edge cases
    job = create_job(store, job_id=f"job_bad_ts_{uuid.uuid4().hex[:6]}")

    with store._get_connection() as conn:
        conn.execute("UPDATE outbound_jobs SET scheduled_for_utc = ? WHERE job_id = ?;", (bad_timestamp, job.job_id))

    mock_creds = MagicMock()
    mock_creds.get_credential.return_value = "secret"
    mock_creds.get_account.return_value = {
        "account_id": "acc_info_it",
        "email_address": "info@pybim.it",
        "credential_ref": "reg_info_it",
    }

    with pytest.raises(ValueError, match=expected_err):
        execute_single_message_pilot(
            db_path=disposable_db,
            job_id=job.job_id,
            allowed_recipient="h.lotfalian98@gmail.com",
            dry_run=True,
            credential_store=mock_creds,
        )
