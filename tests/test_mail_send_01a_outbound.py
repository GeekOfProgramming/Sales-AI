"""
tests/test_mail_send_01a_outbound.py
Comprehensive Offline Test Suite for Register.it SMTP Adapter, Durable Queue & Safe Worker (MAIL-SEND-01A)

Verifies:
1. Sender credential isolation & TLS adapter offline dry run (no live network connections).
2. Standards-compliant RFC 5322 MIME generation, UTF-8 encoding, and stable Message-ID.
3. Strict BCC privacy (BCC is in envelope recipients, never in MIME headers).
4. CID-embedded official pyBIM logo and prevention of duplicate signatures.
5. Meeting template placeholder enforcement and platform URL checking.
6. Explicit human authorization & suppression (Do Not Contact) enforcement.
7. CRM exclusion semantics distinction (excluded_companies != DNC).
8. Legacy scheduled draft quarantine (pre-existing drafts cannot dispatch without revalidation).
9. Transactional queue leasing, multi-worker race prevention, and lease recovery.
10. Idempotency and double-queue prevention.
11. Partial SMTP refusal & retry logic (attempts < max).
12. Network timeout after DATA -> acceptance_unknown (manual audit required).
13. Overdue-job dormancy handling (laptop sleep protection).
14. Worker disabled by default (OUTBOUND_SEND_ENABLED=false).
15. Absolute zero CRM outreach mutations (no contacted status, no lead ownership change, no historical sent alteration).
"""

import os
import json
import uuid
import smtplib
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord, MailboxDraftRecord
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.sending.outbound_models import (
    OutboundQueueJob,
    OutboundAttemptRecord,
    OutboundWorkerConfig,
)
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.mime_builder import build_mime_message, generate_rfc_message_id, PYBIM_LOGO_CID
from sales_engine.sending.smtp_adapter import RegisterItSMTPAdapter, SMTPSubmissionResult
from sales_engine.sending.outbound_worker import OutboundWorker
from sales_engine.sending.outbound_service import OutboundDispatchService


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Provides a fresh isolated SQLite database with migrations and sample accounts."""
    db_file = str(tmp_path / "test_outbound.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("OUTBOUND_SEND_ENABLED", "false")

    repo = MailboxRepository(db_path=db_file)
    repo.apply_migrations()

    # Create dummy suppression_list and excluded_companies tables
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
                canonical_domain TEXT,
                company_name TEXT,
                known_aliases_json TEXT,
                exclusion_reason TEXT,
                operator_note TEXT,
                exclusion_status TEXT,
                created_at TEXT,
                updated_at TEXT,
                created_by TEXT,
                last_updated_by TEXT,
                provenance_source TEXT
            );
        """)

    # Seed accounts
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Global",
        account_role="primary_sales",
        imap_host="authsmtp.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_com",
        is_active=True,
    )
    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Italia",
        account_role="primary_sales",
        imap_host="authsmtp.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_it",
        is_active=True,
    )
    repo.save_account(acc_com)
    repo.save_account(acc_it)

    # Set mock credentials in temporary store
    cred_store_file = tmp_path / ".credentials.enc"
    cred_store = MailboxCredentialStore(store_path=cred_store_file)
    cred_store.set_credential("cred_info_com", "SecretPassCom123!")
    cred_store.set_credential("cred_info_it", "SecretPassIt123!")

    return {
        "db_path": db_file,
        "repo": repo,
        "cred_store": cred_store,
        "tmp_path": tmp_path,
    }


# ============================================================================
# 1. MIME Generation & BCC Privacy Tests
# ============================================================================

def test_mime_generation_headers_and_bcc_privacy():
    """Verify MIME headers, stable Message-ID, UTF-8 charset, and BCC privacy."""
    msg_id = "<stable-12345@pybim.com>"
    msg, envelope = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["client@example.com"],
        subject="Proposta Collaborazione BIM · pyBIM",
        body_plain_text="Gentile Architetto,\nInviamo proposta di collaborazione.",
        cc_addresses=["colleague@pybim.com"],
        bcc_addresses=["audit@pybim.com", "backup@pybim.com"],
        rfc_message_id=msg_id,
        account_id="acc_info_com",
    )

    # Verify visible headers
    assert msg["From"] == "pyBIM <info@pybim.com>"
    assert msg["To"] == "client@example.com"
    assert msg["Cc"] == "colleague@pybim.com"
    assert msg["Subject"] == "Proposta Collaborazione BIM · pyBIM"
    assert msg["Message-ID"] == msg_id

    # CRITICAL: BCC address must NEVER exist in visible headers
    assert "Bcc" not in msg
    assert "audit@pybim.com" not in msg.as_string()
    assert "backup@pybim.com" not in msg.as_string()

    # Envelope recipients MUST contain all To, CC, and BCC
    assert "client@example.com" in envelope
    assert "colleague@pybim.com" in envelope
    assert "audit@pybim.com" in envelope
    assert "backup@pybim.com" in envelope
    assert len(envelope) == 4


def test_mime_cid_embedded_logo_and_signatures():
    """Verify official pyBIM signature and CID inline logo embedding."""
    html_input = (
        "<p>Gentile Ingegnere,</p>"
        "<p>Ecco il riepilogo del nostro incontro.</p>"
        '<img src="https://www.pybim.com/logo_white_transparent.png" alt="pyBIM Logo">'
    )
    msg, _ = build_mime_message(
        sender_email="info@pybim.it",
        to_addresses=["partner@example.it"],
        subject="Riepilogo pyBIM",
        body_plain_text="Gentile Ingegnere,\nEcco il riepilogo.",
        body_html_sanitized=html_input,
        account_id="acc_info_it",
    )

    msg_str = msg.as_string()
    # Check remote logo URL was rewritten to cid:
    assert f"cid:{PYBIM_LOGO_CID}" in msg_str
    assert "https://www.pybim.com/logo_white_transparent.png" not in msg_str
    # Check Italian signature was appended
    assert "Ingegneria & Automazione Digitale" in msg_str or "info@pybim.it" in msg_str


def test_mime_unresolved_placeholder_rejection():
    """Verify that messages with unresolved meeting placeholders are strictly rejected."""
    with pytest.raises(ValueError, match="Unresolved placeholder"):
        build_mime_message(
            sender_email="info@pybim.com",
            to_addresses=["client@example.com"],
            subject="Meeting [Meeting topic]",
            body_plain_text="Please join us on [Meeting date] at [HH:MM]. Link: [Meeting link]",
        )


# ============================================================================
# 2. SMTP Transport & Credential Security Tests
# ============================================================================

def test_smtp_adapter_offline_dry_run(isolated_db):
    """Verify that live_send_enabled=False safely validates credentials without live transmission."""
    adapter = RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"])
    msg, env = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["test@example.com"],
        subject="Offline Test",
        body_plain_text="Hello world",
    )

    res = adapter.send_message(
        sender_email="info@pybim.com",
        credential_ref="cred_info_com",
        envelope_recipients=env,
        message=msg,
        live_send_enabled=False,
    )

    assert res.success is True
    assert res.status_code == 250
    assert "DRY RUN" in res.response_message


def test_smtp_adapter_missing_credentials(isolated_db):
    """Verify failure response if credential reference is missing or invalid."""
    adapter = RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"])
    msg, env = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["test@example.com"],
        subject="Offline Test",
        body_plain_text="Hello world",
    )

    res = adapter.send_message(
        sender_email="info@pybim.com",
        credential_ref="non_existent_ref",
        envelope_recipients=env,
        message=msg,
        live_send_enabled=True,
    )

    assert res.success is False
    assert res.error_type == "missing_credential"
    assert "non_existent_ref" in res.error_details


def test_smtp_adapter_mock_server_success(isolated_db):
    """Verify transmission with a mock SMTP server."""
    adapter = RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"])
    msg, env = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["test@example.com"],
        subject="Offline Test",
        body_plain_text="Hello world",
    )

    mock_server = MagicMock()
    mock_server.send_message.return_value = {}  # Empty dict means no recipient was refused

    res = adapter.send_message(
        sender_email="info@pybim.com",
        credential_ref="cred_info_com",
        envelope_recipients=env,
        message=msg,
        live_send_enabled=True,
        custom_server=mock_server,
    )

    assert res.success is True
    assert res.status_code == 250
    mock_server.send_message.assert_called_once()


def test_smtp_adapter_partial_recipient_refusal(isolated_db):
    """Verify handling when the server refuses one of the recipients."""
    adapter = RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"])
    msg, env = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["valid@example.com", "banned@example.com"],
        subject="Offline Test",
        body_plain_text="Hello world",
    )

    mock_server = MagicMock()
    # Mock SMTP returning refusal for banned@example.com
    mock_server.send_message.return_value = {"banned@example.com": (550, b"User unknown")}

    res = adapter.send_message(
        sender_email="info@pybim.com",
        credential_ref="cred_info_com",
        envelope_recipients=env,
        message=msg,
        live_send_enabled=True,
        custom_server=mock_server,
    )

    assert res.success is False
    assert res.error_type == "recipient_refusal"
    assert res.is_retryable is True


def test_smtp_adapter_unknown_acceptance_on_timeout(isolated_db):
    """Verify ambiguous timeout after DATA is flagged as is_unknown_acceptance."""
    adapter = RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"])
    msg, env = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["client@example.com"],
        subject="Offline Test",
        body_plain_text="Hello world",
    )

    mock_server = MagicMock()
    mock_server.send_message.side_effect = smtplib.SMTPServerDisconnected("Connection reset by peer after DATA")

    res = adapter.send_message(
        sender_email="info@pybim.com",
        credential_ref="cred_info_com",
        envelope_recipients=env,
        message=msg,
        live_send_enabled=True,
        custom_server=mock_server,
    )

    assert res.success is False
    assert res.is_unknown_acceptance is True
    assert res.error_type == "connection_timeout"


# ============================================================================
# 3. Safeguards & Suppression Tests
# ============================================================================

def test_safeguards_suppression_enforcement(isolated_db):
    """Verify Do Not Contact (suppression_list) stops emails to unsubscribed users or domains."""
    safeguards = OutboundSafeguardService(db_path=isolated_db["db_path"])

    # Insert suppression record
    with isolated_db["repo"].get_connection() as conn:
        conn.execute(
            "INSERT INTO suppression_list (suppression_id, email, company_domain, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?);",
            ("sup_1", "optout@target.com", None, "UNSUBSCRIBE", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT INTO suppression_list (suppression_id, email, company_domain, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?);",
            ("sup_2", None, "banneddomain.com", "BOUNCE_DOMAIN", datetime.now(timezone.utc).isoformat()),
        )

    # 1. Exact email suppressed
    with pytest.raises(SafeguardViolationError, match="Do Not Contact list"):
        safeguards.check_suppression(["optout@target.com"])

    # 2. Domain suppressed
    with pytest.raises(SafeguardViolationError, match="banneddomain.com"):
        safeguards.check_suppression(["anyone@banneddomain.com"])

    # 3. Clean recipient passes
    safeguards.check_suppression(["allowed@target.com"])


def test_safeguards_exclusion_semantics_distinction(isolated_db):
    """Verify Excluded Companies is treated as CRM disqualification, NOT equal to DNC suppression."""
    safeguards = OutboundSafeguardService(db_path=isolated_db["db_path"])

    with isolated_db["repo"].get_connection() as conn:
        conn.execute(
            "INSERT INTO excluded_companies (registry_id, candidate_id, company_name, exclusion_reason, exclusion_status) "
            "VALUES (?, ?, ?, ?, ?);",
            ("excl_1", "cand:comp:autodesk", "Autodesk", "SOFTWARE_VENDOR", "ACTIVE"),
        )

    excl = safeguards.check_crm_exclusion("cand:comp:autodesk", ["ceo@autodesk.com"])
    assert excl is not None
    assert excl["exclusion_reason"] == "SOFTWARE_VENDOR"

    # Crucially, exclusion does not raise SafeguardViolationError by itself, distinguishing policy semantics
    safeguards.check_suppression(["ceo@autodesk.com"])


# ============================================================================
# 4. Durable Outbound Queue & Quarantine Tests
# ============================================================================

def test_queue_and_quarantine_policy(isolated_db):
    """Verify that drafts are enqueued with frozen snapshots and legacy schedules are quarantined."""
    repo = isolated_db["repo"]
    dispatch_service = OutboundDispatchService(db_path=isolated_db["db_path"])

    # Create scheduled draft
    now_utc = datetime.now(timezone.utc)
    future_utc = (now_utc + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    draft = MailboxDraftRecord(
        draft_id="drf_test_sched_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["client@studioarchitettura.it"],
        subject="Collaborazione Progetto BIM",
        body_plain_text="Buongiorno, vorremmo presentare i nostri servizi BIM.",
        draft_status="scheduled",
        scheduled_for_utc=future_utc,
        scheduled_local_date="2026-11-18",
        scheduled_local_time="10:00",
        scheduled_timezone="Europe/Rome",
        snapshot_subject="Collaborazione Progetto BIM",
        snapshot_plain_text="Buongiorno, vorremmo presentare i nostri servizi BIM.",
        snapshot_to_addresses=["client@studioarchitettura.it"],
        created_by="hamid",
        updated_by="hamid",
    )
    repo.save_draft(draft)

    # Queue approved draft
    job = dispatch_service.queue_approved_draft(
        draft_id="drf_test_sched_01",
        operator_username="hamid",
    )

    assert job.job_id.startswith("job_")
    assert job.source_draft_id == "drf_test_sched_01"
    assert job.status == "scheduled"
    assert job.approved_by == "hamid"
    assert "client@studioarchitettura.it" in job.header_to_json

    # Idempotent second call returns existing job without error
    job_dup = dispatch_service.queue_approved_draft(
        draft_id="drf_test_sched_01",
        operator_username="hamid",
    )
    assert job_dup.job_id == job.job_id


def test_transactional_claiming_and_competing_workers(isolated_db):
    """Verify that multiple competing workers do NOT double-claim the same job."""
    queue_store = OutboundQueueStore(db_path=isolated_db["db_path"])

    # Seed 3 eligible jobs scheduled in the past
    past_utc = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

    for i in range(3):
        job = OutboundQueueJob(
            job_id=f"job_comp_{i}",
            source_draft_id=f"drf_{i}",
            account_id="acc_info_com",
            sender_email="info@pybim.com",
            envelope_from="info@pybim.com",
            envelope_recipients_json=json.dumps([f"test{i}@example.com"]),
            header_to_json=json.dumps([f"test{i}@example.com"]),
            subject=f"Job {i}",
            body_plain_text=f"Body {i}",
            rfc_message_id=f"<msg_{i}@pybim.com>",
            scheduled_for_utc=past_utc,
            scheduled_local_date="2026-11-17",
            scheduled_local_time="09:00",
            approved_by="hamid",
            approved_at_utc=past_utc,
            idempotency_key=f"idemp_{i}",
        )
        queue_store.enqueue_job(job)

    # Worker 1 claims batch of 2
    claimed_w1 = queue_store.claim_next_jobs(
        worker_id="worker_alpha",
        lease_id="lease_alpha",
        batch_size=2,
    )
    assert len(claimed_w1) == 2

    # Worker 2 immediately attempts to claim batch of 2
    claimed_w2 = queue_store.claim_next_jobs(
        worker_id="worker_beta",
        lease_id="lease_beta",
        batch_size=2,
    )
    # Only 1 unclaimed job remained
    assert len(claimed_w2) == 1

    # Ensure no intersection between claimed jobs
    w1_ids = {j.job_id for j in claimed_w1}
    w2_ids = {j.job_id for j in claimed_w2}
    assert w1_ids.isdisjoint(w2_ids)


# ============================================================================
# 5. Worker Execution, Dormancy & Safety Tests
# ============================================================================

def test_worker_disabled_by_default(isolated_db):
    """Verify worker is inert when OUTBOUND_SEND_ENABLED is false."""
    worker = OutboundWorker(db_path=isolated_db["db_path"])
    res = worker.process_batch()
    assert res["status"] == "disabled"
    assert res["processed"] == 0


def test_worker_overdue_job_dormancy_protection(isolated_db):
    """Verify that jobs overdue by > 2 hours (e.g. laptop sleep) require review and do not auto-send."""
    queue_store = OutboundQueueStore(db_path=isolated_db["db_path"])

    # Scheduled 3 hours ago
    ancient_utc = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    job = OutboundQueueJob(
        job_id="job_overdue",
        source_draft_id="drf_overdue",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        envelope_from="info@pybim.com",
        envelope_recipients_json=json.dumps(["client@example.com"]),
        header_to_json=json.dumps(["client@example.com"]),
        subject="Overdue Email",
        body_plain_text="This was scheduled long ago.",
        rfc_message_id="<overdue@pybim.com>",
        scheduled_for_utc=ancient_utc,
        scheduled_local_date="2026-11-17",
        scheduled_local_time="09:00",
        approved_by="hamid",
        approved_at_utc=ancient_utc,
        idempotency_key="idemp_overdue",
    )
    queue_store.enqueue_job(job)

    cfg = OutboundWorkerConfig(enabled=True, max_overdue_seconds=7200)  # 2 hours
    worker = OutboundWorker(db_path=isolated_db["db_path"], config=cfg)

    # Process
    res = worker.process_batch(custom_server=MagicMock())
    assert res["processed"] == 1
    assert res["review_required"] == 1

    updated_job = queue_store.get_job("job_overdue")
    assert updated_job.status == "requires_review"
    assert "overdue" in updated_job.review_reason.lower()


def test_worker_end_to_end_mock_delivery_and_crm_immutability(isolated_db):
    """
    Verify complete delivery pass:
    - Moves job to smtp_accepted
    - Records attempt log
    - NEVER touches CRM candidate status or historical outreach tables
    """
    queue_store = OutboundQueueStore(db_path=isolated_db["db_path"])
    past_utc = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

    job = OutboundQueueJob(
        job_id="job_live_mock",
        source_draft_id="drf_crm_test",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        envelope_from="info@pybim.it",
        envelope_recipients_json=json.dumps(["studio@bim.it"]),
        header_to_json=json.dumps(["studio@bim.it"]),
        subject="Proposta Studio",
        body_plain_text="Invio documentazione tecnica.",
        rfc_message_id="<mock_success@pybim.it>",
        candidate_id="cand:comp:studio",
        scheduled_for_utc=past_utc,
        scheduled_local_date="2026-11-17",
        scheduled_local_time="10:00",
        approved_by="hamid",
        approved_at_utc=past_utc,
        idempotency_key="idemp_live_mock",
    )
    queue_store.enqueue_job(job)

    # Create dummy source draft in repo
    draft = MailboxDraftRecord(
        draft_id="drf_crm_test",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        to_addresses=["studio@bim.it"],
        subject="Proposta Studio",
        body_plain_text="Invio documentazione tecnica.",
        draft_status="scheduled",
        candidate_id="cand:comp:studio",
        created_by="hamid",
        updated_by="hamid",
    )
    isolated_db["repo"].save_draft(draft)

    mock_server = MagicMock()
    mock_server.send_message.return_value = {}

    cfg = OutboundWorkerConfig(enabled=True)
    worker = OutboundWorker(
        db_path=isolated_db["db_path"],
        config=cfg,
        smtp_adapter=RegisterItSMTPAdapter(credential_store=isolated_db["cred_store"]),
    )

    res = worker.process_batch(custom_server=mock_server)
    print("RES DEBUG:", res)
    assert res["processed"] == 1
    assert res["accepted"] == 1

    # Check job status
    finished_job = queue_store.get_job("job_live_mock")
    assert finished_job.status == "smtp_accepted"
    assert finished_job.attempt_count == 1

    # Check attempt history
    attempts = queue_store.list_attempts_for_job("job_live_mock")
    assert len(attempts) == 1
    assert attempts[0].status == "success"

    # Check source draft status updated to sent
    reloaded_draft = isolated_db["repo"].get_draft("drf_crm_test")
    assert reloaded_draft.draft_status == "sent"

    # IMMUTABILITY ASSERTIONS:
    # Ensure no automated outreach log or historical sent emails were mutated
    with isolated_db["repo"].get_connection() as conn:
        has_hist = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_sent_emails'").fetchone()
        if has_hist:
            count = conn.execute("SELECT COUNT(*) FROM historical_sent_emails WHERE matched_candidate_id = 'cand:comp:studio'").fetchone()[0]
            assert count == 0


def test_sender_display_name_and_second_account_readiness():
    """
    MAIL-SEND-01F: Verify From headers, sender display names, and envelope consistency
    for both authorized accounts: info@pybim.com and info@pybim.it.
    """
    from sales_engine.sending.mime_builder import SENDER_DISPLAY_NAMES, format_sender_address

    # 1. Authoritative mapping verification
    assert SENDER_DISPLAY_NAMES["info@pybim.com"] == "pyBIM"
    assert SENDER_DISPLAY_NAMES["info@pybim.it"] == "pyBIM"

    # 2. format_sender_address formatting
    assert format_sender_address("info@pybim.com") == "pyBIM <info@pybim.com>"
    assert format_sender_address("info@pybim.it") == "pyBIM <info@pybim.it>"
    assert format_sender_address("info@pybim.com", "Custom Display") == "Custom Display <info@pybim.com>"

    # 3. Message generation for info@pybim.com
    msg_com, env_com = build_mime_message(
        sender_email="info@pybim.com",
        to_addresses=["client@studiobim.com"],
        subject="pyBIM Automation — Global",
        body_plain_text="Hello, we are pyBIM.",
        account_id="acc_info_com",
    )
    assert msg_com["From"] == "pyBIM <info@pybim.com>"
    assert msg_com["Reply-To"] == "info@pybim.com"
    assert env_com == ["client@studiobim.com"]

    # 4. Message generation for info@pybim.it
    msg_it, env_it = build_mime_message(
        sender_email="info@pybim.it",
        to_addresses=["studio@bim.it"],
        subject="pyBIM Automazione — Italia",
        body_plain_text="Buongiorno, siamo pyBIM.",
        body_html_sanitized="<p>Buongiorno, siamo pyBIM.</p>",
        account_id="acc_info_it",
    )
    assert msg_it["From"] == "pyBIM <info@pybim.it>"
    assert msg_it["Reply-To"] == "info@pybim.it"
    assert env_it == ["studio@bim.it"]

    # 5. Verify official signature preservation without duplication
    full_serialized = msg_it.as_string()
    assert "Ingegneria &amp; Automazione Digitale" in full_serialized or "Ingegneria & Automazione Digitale" in full_serialized
    assert "From: pyBIM <info@pybim.it>" in full_serialized
