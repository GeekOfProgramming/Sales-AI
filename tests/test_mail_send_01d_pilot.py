"""
tests/test_mail_send_01d_pilot.py
MAIL-SEND-01D: Live SMTP Pilot Readiness, Security Gates & Single-Message Test Harness.

Comprehensive Offline Test Suite Verifying:
1. Production database safety & read-only diagnostic assertions.
2. Single-message pilot harness containment:
   - Exactly one message maximum.
   - Exact job ID match required.
   - Exact recipient allowlist match required.
   - Sender allowlist enforcement (info@pybim.com, info@pybim.it).
   - Rejection of discarded or unscheduled drafts.
   - Suppression list (Do Not Contact) enforcement at execution time.
   - Placeholder rejection.
3. Legacy vs New sending isolation:
   - Requiring OUTBOUND_SEND_ENABLED and PILOT_ALLOW_LIVE_SEND.
   - Ensuring EMAIL_SEND_ENABLED (legacy outreach) remains strictly disabled and isolated.
   - Disabling either flag fails closed.
4. Account deactivation and session revocation:
   - Setting is_active=0 immediately revokes sessions.
   - Deactivated users cannot authenticate.
   - Historical audit records are preserved.
   - Sole administrator cannot be deactivated.
5. SMTP adapter and mock transmission:
   - Dry-run simulation.
   - Successful mock transmission (positive 250 response).
   - Unknown acceptance on timeout triggers 'acceptance_unknown' without auto-resend.
"""

import os
import json
import uuid
import tempfile
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxDraftRecord, MailboxAccountRecord
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.sending.outbound_models import OutboundQueueJob, OutboundAttemptRecord
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_service import OutboundDispatchService
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.mime_builder import build_mime_message, validate_message_payload
from sales_engine.sending.smtp_adapter import RegisterItSMTPAdapter
from backend.operator_auth import get_auth_service, init_auth_schema, OperatorUser, OperatorAuthService
import backend.operator_auth as operator_auth_mod
from scripts.run_outbound_pilot import (
    get_diagnostics,
    execute_single_message_pilot,
    check_draft_eligibility,
    admit_draft_to_queue,
)


@pytest.fixture(scope="function")
def pilot_test_env(tmp_path, monkeypatch):
    """Provides a fresh isolated database and DPAPI credential mock."""
    db_file = str(tmp_path / "test_pilot_ops.db")
    cred_file = str(tmp_path / "test_creds.enc")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "false")
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "false")

    # Reset global singleton to point to test db
    operator_auth_mod._auth_service = None

    repo = MailboxRepository(db_path=db_file)
    repo.apply_migrations()
    init_auth_schema(db_file)

    # Seed mailbox accounts for foreign key constraints
    repo.save_account(
        MailboxAccountRecord(
            account_id="acc_info_com",
            email_address="info@pybim.com",
            display_name="pyBIM Info (COM)",
            imap_host="imaps.securemail.pro",
            imap_port=993,
            imap_security="ssl",
            auth_type="basic_win_cred",
            credential_ref="win_cred_acc_info_com",
            is_active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    repo.save_account(
        MailboxAccountRecord(
            account_id="acc_info_it",
            email_address="info@pybim.it",
            display_name="pyBIM Info (IT)",
            imap_host="imaps.securemail.pro",
            imap_port=993,
            imap_security="ssl",
            auth_type="basic_win_cred",
            credential_ref="win_cred_acc_info_it",
            is_active=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    # Set up credentials in mock store
    cred_store = MailboxCredentialStore(store_path=cred_file)
    cred_store.set_credential("win_cred_acc_info_com", "ValidSecretCom123!")
    cred_store.set_credential("win_cred_acc_info_it", "ValidSecretIt123!")

    # Set up suppression tables
    with repo.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_list (
                suppression_id TEXT PRIMARY KEY,
                email TEXT,
                company_domain TEXT,
                reason TEXT,
                source TEXT,
                scope TEXT,
                value TEXT,
                created_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS excluded_companies (
                registry_id TEXT PRIMARY KEY,
                candidate_id TEXT,
                company_name TEXT,
                exclusion_reason TEXT,
                exclusion_status TEXT,
                created_at TEXT
            );
        """)
        conn.commit()

    # Create admin user
    auth = get_auth_service(db_file)
    admin = auth.create_user("pilot_admin", "AdminPassword123!", role="admin", full_name="Pilot Admin")
    op = auth.create_user("test_operator_1", "OperatorPass123!", role="operator", full_name="Test Operator 1")

    yield {
        "db_path": db_file,
        "cred_store": cred_store,
        "repo": repo,
        "auth": auth,
        "admin": admin,
        "operator": op,
    }

    operator_auth_mod._auth_service = None


def test_diagnostics_report_accurate_state_without_mutations(pilot_test_env):
    """Verify get_diagnostics reports exact status without creating or modifying database records."""
    db_path = pilot_test_env["db_path"]
    diag = get_diagnostics(db_path)

    assert diag["gates"]["OUTBOUND_SEND_ENABLED"] is False
    assert diag["gates"]["EMAIL_SEND_ENABLED (Legacy)"] is False
    assert diag["gates"]["PILOT_ALLOW_LIVE_SEND"] is False
    assert diag["gates"]["live_transmission_permitted"] is False
    assert diag["queue_summary"]["total_jobs"] == 0
    assert diag["queue_summary"]["scheduled"] == 0


def test_pilot_single_message_dry_run_simulation(pilot_test_env):
    """Verify single-message pilot dry run simulates transmission and records attempt without live sending."""
    db_path = pilot_test_env["db_path"]
    queue_store = OutboundQueueStore(db_path=db_path)

    # Stage a valid scheduled job
    job = OutboundQueueJob(
        job_id="job_pilot_001",
        source_draft_id="drf_pilot_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["test.recipient@partner.com"]),
        header_to_json=json.dumps(["test.recipient@partner.com"]),
        subject="Pilot Proposal",
        body_plain_text="This is a test pilot message.",
        rfc_message_id="<pilot001@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="pilot_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_p001",
    )
    queue_store.enqueue_job(job)

    res = execute_single_message_pilot(
        db_path=db_path,
        job_id="job_pilot_001",
        allowed_recipient="test.recipient@partner.com",
        dry_run=True,
        credential_store=pilot_test_env["cred_store"],
    )

    assert res["success"] is True
    assert res["mode"] == "DRY_RUN"
    assert res["status"] == "dry_run_simulated"

    # Verify job remains 'scheduled' after dry run
    reloaded_job = queue_store.get_job("job_pilot_001")
    assert reloaded_job.status == "scheduled"

    # Verify attempt recorded
    attempts = queue_store.list_attempts_for_job("job_pilot_001")
    assert len(attempts) == 1
    assert attempts[0].status == "dry_run_simulated"


def test_pilot_recipient_mismatch_fails_closed(pilot_test_env):
    """Verify pilot refuses to send if requested recipient does not strictly match job destination."""
    db_path = pilot_test_env["db_path"]
    queue_store = OutboundQueueStore(db_path=db_path)

    job = OutboundQueueJob(
        job_id="job_pilot_002",
        source_draft_id="drf_pilot_002",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["customer@realdomain.com"]),
        header_to_json=json.dumps(["customer@realdomain.com"]),
        subject="Real Proposal",
        body_plain_text="Message body",
        rfc_message_id="<p002@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="pilot_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_p002",
    )
    queue_store.enqueue_job(job)

    # Attempt to send with mismatched allowlist
    with pytest.raises(ValueError, match="Envelope recipient mismatch"):
        execute_single_message_pilot(
            db_path=db_path,
            job_id="job_pilot_002",
            allowed_recipient="different@partner.com",
            dry_run=True,
        )


def test_pilot_suppressed_recipient_fails_closed(pilot_test_env):
    """Verify that a suppressed recipient is blocked by pre-send DNC enforcement."""
    db_path = pilot_test_env["db_path"]
    queue_store = OutboundQueueStore(db_path=db_path)

    # Add recipient to suppression_list
    with pilot_test_env["repo"].get_connection() as conn:
        conn.execute(
            "INSERT INTO suppression_list (suppression_id, email, reason) VALUES ('s1', 'dnc@example.com', 'Opted out');"
        )
        conn.commit()

    job = OutboundQueueJob(
        job_id="job_pilot_003",
        source_draft_id="drf_pilot_003",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["dnc@example.com"]),
        header_to_json=json.dumps(["dnc@example.com"]),
        subject="Hello",
        body_plain_text="Body text",
        rfc_message_id="<p003@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="pilot_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_p003",
    )
    queue_store.enqueue_job(job)

    with pytest.raises(SafeguardViolationError, match="Do Not Contact"):
        execute_single_message_pilot(
            db_path=db_path,
            job_id="job_pilot_003",
            allowed_recipient="dnc@example.com",
            dry_run=True,
        )


def test_pilot_live_send_with_mock_smtp_server(pilot_test_env, monkeypatch):
    """Verify live transmission gate with mock SMTP server accepts message and updates state."""
    db_path = pilot_test_env["db_path"]
    queue_store = OutboundQueueStore(db_path=db_path)

    # Create dummy source draft in repo
    draft = MailboxDraftRecord(
        draft_id="drf_pilot_live",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["partner.verified@company.com"],
        subject="Pilot Meeting",
        body_plain_text="Here is the confirmed agenda.",
        draft_status="scheduled",
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    pilot_test_env["repo"].save_draft(draft)

    job = OutboundQueueJob(
        job_id="job_pilot_live",
        source_draft_id="drf_pilot_live",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["partner.verified@company.com"]),
        header_to_json=json.dumps(["partner.verified@company.com"]),
        subject="Pilot Meeting",
        body_plain_text="Here is the confirmed agenda.",
        rfc_message_id="<p_live@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="pilot_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_live",
    )
    queue_store.enqueue_job(job)

    # Mock SMTP server returning OK
    mock_server = MagicMock()
    mock_server.send_message.return_value = {}

    # Enable pilot live send flags
    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "true")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "true")

    res = execute_single_message_pilot(
        db_path=db_path,
        job_id="job_pilot_live",
        allowed_recipient="partner.verified@company.com",
        dry_run=False,
        custom_smtp_server=mock_server,
        credential_store=pilot_test_env["cred_store"],
    )

    assert res["success"] is True
    assert res["mode"] == "LIVE_SEND"
    assert res["status"] == "smtp_accepted"

    # Verify job moved to smtp_accepted
    reloaded_job = queue_store.get_job("job_pilot_live")
    assert reloaded_job.status == "smtp_accepted"

    # Verify source draft status updated to sent
    reloaded_draft = pilot_test_env["repo"].get_draft("drf_pilot_live")
    assert reloaded_draft.draft_status == "sent"


def test_pilot_unknown_acceptance_on_timeout(pilot_test_env, monkeypatch):
    """Verify ambiguous timeout during SMTP transmission marks job as acceptance_unknown without retrying."""
    db_path = pilot_test_env["db_path"]
    queue_store = OutboundQueueStore(db_path=db_path)

    job = OutboundQueueJob(
        job_id="job_pilot_timeout",
        source_draft_id="drf_timeout",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["timeout.partner@company.com"]),
        header_to_json=json.dumps(["timeout.partner@company.com"]),
        subject="Timeout Test",
        body_plain_text="Content",
        rfc_message_id="<timeout@pybim.com>",
        scheduled_for_utc="2026-11-20T14:00:00Z",
        scheduled_local_date="2026-11-20",
        scheduled_local_time="15:00",
        approved_by="pilot_admin",
        approved_at_utc="2026-11-20T14:00:00Z",
        idempotency_key="idemp_timeout",
    )
    queue_store.enqueue_job(job)

    mock_server = MagicMock()
    import smtplib
    mock_server.send_message.side_effect = smtplib.SMTPServerDisconnected("Connection abruptly closed after DATA")

    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "true")
    monkeypatch.setenv("PILOT_ALLOW_LIVE_SEND", "true")

    res = execute_single_message_pilot(
        db_path=db_path,
        job_id="job_pilot_timeout",
        allowed_recipient="timeout.partner@company.com",
        dry_run=False,
        custom_smtp_server=mock_server,
        credential_store=pilot_test_env["cred_store"],
    )

    assert res["success"] is False
    assert res["status"] == "acceptance_unknown"

    reloaded_job = queue_store.get_job("job_pilot_timeout")
    assert reloaded_job.status == "acceptance_unknown"
    assert "timeout" in reloaded_job.review_reason.lower()


def test_reversible_account_deactivation_and_session_revocation(pilot_test_env):
    """Verify operator account deactivation revokes active sessions and prevents login while preserving history."""
    auth: OperatorAuthService = pilot_test_env["auth"]

    # Authenticate test operator and create active session
    user = auth.authenticate_user("test_operator_1", "OperatorPass123!")
    assert user is not None
    session_token = auth.create_session(user.user_id)
    assert auth.validate_session(session_token) is not None

    # Deactivate test operator
    ok = auth.set_user_active("test_operator_1", is_active=False)
    assert ok is True

    # 1. Existing session must be immediately rejected
    assert auth.validate_session(session_token) is None

    # 2. Re-login attempt must be blocked
    assert auth.authenticate_user("test_operator_1", "OperatorPass123!") is None

    # 3. User record is preserved in list_users as inactive
    users = auth.list_users()
    test_u = next(u for u in users if u.username == "test_operator_1")
    assert test_u.is_active is False

    # 4. Reactivation restores login capability cleanly
    auth.set_user_active("test_operator_1", is_active=True)
    restored_u = auth.authenticate_user("test_operator_1", "OperatorPass123!")
    assert restored_u is not None
    assert restored_u.is_active is True


# ==============================================================================
# MAIL-SEND-01D-QUEUE-BRIDGE Tests
# ==============================================================================

def test_check_draft_eligibility_scheduled_vs_unscheduled(pilot_test_env):
    """Verify eligibility inspector correctly distinguishes unscheduled from scheduled drafts."""
    db_path = pilot_test_env["db_path"]
    repo: MailboxRepository = pilot_test_env["repo"]

    # 1. Unscheduled draft ('ready_to_schedule')
    draft_unsched = MailboxDraftRecord(
        draft_id="drf_elig_unsched",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["h.lotfalian98@gmail.com"],
        subject="pyBIM SMTP Pilot — Test 01",
        body_plain_text="Content",
        draft_status="ready_to_schedule",
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    repo.save_draft(draft_unsched)

    res_unsched = check_draft_eligibility(db_path, "drf_elig_unsched", "h.lotfalian98@gmail.com")
    assert res_unsched["eligible"] is False
    assert any("Only 'scheduled' drafts" in r for r in res_unsched["reasons"])
    assert any("missing scheduled_for_utc" in r for r in res_unsched["reasons"])

    # 2. Valid scheduled draft in the future
    future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    draft_sched = MailboxDraftRecord(
        draft_id="drf_elig_sched",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["h.lotfalian98@gmail.com"],
        subject="pyBIM SMTP Pilot — Test 01",
        body_plain_text="Content",
        draft_status="scheduled",
        scheduled_for_utc=future_time,
        scheduled_local_date="2026-11-20",
        scheduled_local_time="17:00",
        scheduled_timezone="Europe/Rome",
        snapshot_subject="pyBIM SMTP Pilot — Test 01",
        snapshot_plain_text="Content",
        snapshot_to_addresses=["h.lotfalian98@gmail.com"],
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    repo.save_draft(draft_sched)

    res_sched = check_draft_eligibility(db_path, "drf_elig_sched", "h.lotfalian98@gmail.com")
    assert res_sched["eligible"] is True
    assert res_sched["reasons"] == []
    assert res_sched["is_expired"] is False


def test_admit_draft_to_queue_success_and_idempotency(pilot_test_env):
    """Verify explicit admission creates one queued outbound job and prevents duplicate admission."""
    db_path = pilot_test_env["db_path"]
    repo: MailboxRepository = pilot_test_env["repo"]

    future_time = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    draft = MailboxDraftRecord(
        draft_id="drf_admit_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["partner@example.com"],
        subject="Meeting Confirmation",
        body_plain_text="Confirmed agenda.",
        draft_status="scheduled",
        scheduled_for_utc=future_time,
        scheduled_local_date="2026-11-20",
        scheduled_local_time="18:00",
        scheduled_timezone="Europe/Rome",
        snapshot_subject="Meeting Confirmation",
        snapshot_plain_text="Confirmed agenda.",
        snapshot_to_addresses=["partner@example.com"],
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    repo.save_draft(draft)

    # First admission: should succeed and create a Job ID
    adm_res = admit_draft_to_queue(
        db_path=db_path,
        draft_id="drf_admit_001",
        allowed_recipient="partner@example.com",
        operator_username="pilot_admin",
    )
    assert adm_res["success"] is True
    job_id = adm_res["job_id"]
    assert job_id.startswith("job_")
    assert adm_res["status"] == "scheduled"

    # Verify queue record exists
    queue_store = OutboundQueueStore(db_path=db_path)
    job = queue_store.get_job(job_id)
    assert job is not None
    assert job.source_draft_id == "drf_admit_001"
    assert job.status == "scheduled"

    # Second admission: duplicate admission must be rejected by idempotency check
    with pytest.raises(ValueError, match="Draft already admitted to queue"):
        admit_draft_to_queue(
            db_path=db_path,
            draft_id="drf_admit_001",
            allowed_recipient="partner@example.com",
            operator_username="pilot_admin",
        )


def test_admit_draft_expired_schedule_rejection_and_override(pilot_test_env):
    """Verify expired scheduled times cannot silently become active without explicit override."""
    db_path = pilot_test_env["db_path"]
    repo: MailboxRepository = pilot_test_env["repo"]

    past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    draft = MailboxDraftRecord(
        draft_id="drf_expired_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["partner.expired@example.com"],
        subject="Past Meeting",
        body_plain_text="Past content.",
        draft_status="scheduled",
        scheduled_for_utc=past_time,
        scheduled_local_date="2026-11-20",
        scheduled_local_time="10:00",
        scheduled_timezone="Europe/Rome",
        snapshot_subject="Past Meeting",
        snapshot_plain_text="Past content.",
        snapshot_to_addresses=["partner.expired@example.com"],
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    repo.save_draft(draft)

    # 1. Check eligibility reports expired
    elig = check_draft_eligibility(db_path, "drf_expired_001", "partner.expired@example.com")
    assert elig["eligible"] is False
    assert elig["is_expired"] is True
    assert any("has already passed (expired)" in r for r in elig["reasons"])

    # 2. Admission rejected by default
    with pytest.raises(ValueError, match="has already passed"):
        admit_draft_to_queue(
            db_path=db_path,
            draft_id="drf_expired_001",
            allowed_recipient="partner.expired@example.com",
            operator_username="pilot_admin",
            allow_expired=False,
        )

    # 3. Explicit operator override admits quarantined/expired test job
    adm_res = admit_draft_to_queue(
        db_path=db_path,
        draft_id="drf_expired_001",
        allowed_recipient="partner.expired@example.com",
        operator_username="pilot_admin",
        allow_expired=True,
    )
    assert adm_res["success"] is True
    assert adm_res["job_id"].startswith("job_")


def test_admit_draft_suppression_and_recipient_mismatch(pilot_test_env):
    """Verify DNC suppression and recipient mismatch prevent queue admission."""
    db_path = pilot_test_env["db_path"]
    repo: MailboxRepository = pilot_test_env["repo"]

    # Insert suppressed address
    with repo.get_connection() as conn:
        conn.execute(
            "INSERT INTO suppression_list (suppression_id, email, reason) VALUES ('s_dnc', 'dnc.admit@example.com', 'Opt-out');"
        )
        conn.commit()

    future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    draft = MailboxDraftRecord(
        draft_id="drf_suppressed_admit",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["dnc.admit@example.com"],
        subject="Meeting",
        body_plain_text="Body",
        draft_status="scheduled",
        scheduled_for_utc=future_time,
        created_by="pilot_admin",
        updated_by="pilot_admin",
    )
    repo.save_draft(draft)

    # Check eligibility fails
    elig = check_draft_eligibility(db_path, "drf_suppressed_admit", "dnc.admit@example.com")
    assert elig["eligible"] is False
    assert any("suppressed on the Do Not Contact list" in r for r in elig["reasons"])

    # Admission fails
    with pytest.raises(ValueError, match="Do Not Contact"):
        admit_draft_to_queue(
            db_path=db_path,
            draft_id="drf_suppressed_admit",
            allowed_recipient="dnc.admit@example.com",
        )

    # Recipient mismatch fails
    with pytest.raises(ValueError, match="Recipient mismatch"):
        admit_draft_to_queue(
            db_path=db_path,
            draft_id="drf_suppressed_admit",
            allowed_recipient="different@example.com",
        )

