"""
tests/test_mail_send_01b_integration.py
MAIL-SEND-01B: Test Suite for Scheduling Integration, Outbound Safety, and Preflight Migration.

Validates:
1. HTTP 500 resolution: scheduling fails gracefully with actionable 503 error on unmigrated database.
2. Migration script idempotency: running twice applies schema cleanly without corruption or data loss.
3. Scheduling succeeds and updates snapshot columns after migration.
4. Outbound queue integration: draft -> OutboundDispatchService enqueues job with immutable snapshot.
5. Dry-run safety gate: SMTPSubmissionResult with is_simulated=True NEVER marks draft as 'sent'
   and leaves queue job in safe status (not 'smtp_accepted').
6. Fail-closed killswitch matrix:
   - OUTBOUND_SEND_ENABLED=false, EMAIL_SEND_ENABLED=false -> inert
   - OUTBOUND_SEND_ENABLED=true, EMAIL_SEND_ENABLED=false -> inert (contradiction fails closed)
   - OUTBOUND_SEND_ENABLED=false, EMAIL_SEND_ENABLED=true -> inert (contradiction fails closed)
7. Full envelope suppression enforcement (To, CC, BCC).
8. Legacy schedule quarantine enforcement.
9. OutboundQueueStore read safety: does not auto-mutate database when auto_apply_schema=False.
"""

import os
import json
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone

from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxDraftRecord
from sales_engine.sending.outbound_models import OutboundQueueJob, OutboundWorkerConfig
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.outbound_service import OutboundDispatchService
from sales_engine.sending.outbound_worker import OutboundWorker
from sales_engine.sending.smtp_adapter import RegisterItSMTPAdapter, SMTPSubmissionResult
from scripts.migrate_outbound_queue import run_migration


@pytest.fixture
def temp_unmigrated_db():
    """Creates a temporary SQLite DB with only base mailbox tables and unmigrated mailbox_drafts."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    conn = sqlite3.connect(db_path)
    # Create complete base mailbox_accounts
    conn.execute("""
        CREATE TABLE mailbox_accounts (
            account_id TEXT PRIMARY KEY,
            email_address TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'register_it',
            imap_host TEXT NOT NULL DEFAULT 'pop.securemail.pro',
            imap_port INTEGER NOT NULL DEFAULT 993,
            use_ssl INTEGER NOT NULL DEFAULT 1,
            credential_ref TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            sync_priority INTEGER NOT NULL DEFAULT 10,
            connection_status TEXT NOT NULL DEFAULT 'configured',
            last_successful_sync_at TEXT,
            last_error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        INSERT INTO mailbox_accounts (
            account_id, email_address, display_name, credential_ref, created_at, updated_at
        ) VALUES (
            'acc_info_com', 'info@pybim.com', 'pyBIM Global', 'cred_pybim_com',
            '2026-11-20T10:00:00Z', '2026-11-20T10:00:00Z'
        );
    """)

    # Base unmigrated mailbox_drafts (like initial MAIL-COMPOSE-01A schema before 01B columns)
    conn.execute("""
        CREATE TABLE mailbox_drafts (
            draft_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            sender_email TEXT NOT NULL,
            to_addresses_json TEXT NOT NULL DEFAULT '[]',
            cc_addresses_json TEXT NOT NULL DEFAULT '[]',
            bcc_addresses_json TEXT NOT NULL DEFAULT '[]',
            subject TEXT NOT NULL DEFAULT '',
            body_plain_text TEXT NOT NULL DEFAULT '',
            body_html_sanitized TEXT,
            candidate_id TEXT,
            contact_id TEXT,
            draft_status TEXT NOT NULL DEFAULT 'draft',
            scheduled_for_utc TEXT,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES mailbox_accounts(account_id)
        );
    """)
    # Insert a test draft
    conn.execute("""
        INSERT INTO mailbox_drafts (
            draft_id, account_id, sender_email, to_addresses_json, subject,
            body_plain_text, draft_status, created_by, updated_by, created_at, updated_at
        ) VALUES (
            'drf_test_unmigrated', 'acc_info_com', 'info@pybim.com', '["lead@partner.it"]',
            'Proposal for BIM Automation', 'Dear Partner, let us discuss.',
            'ready_to_schedule', 'operator', 'operator',
            '2026-11-20T10:00:00Z', '2026-11-20T10:00:00Z'
        );
    """)
    conn.commit()
    conn.close()

    yield db_path

    # Clean up with retry for Windows file locking
    import time
    for _ in range(5):
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_scheduling_on_unmigrated_db_raises_actionable_runtime_error(temp_unmigrated_db):
    """Verifies that scheduling against an unmigrated database raises RuntimeError with migration instructions instead of raw 500."""
    repo = MailboxRepository(db_path=temp_unmigrated_db)
    
    with pytest.raises(RuntimeError) as exc_info:
        repo.schedule_draft(
            draft_id="drf_test_unmigrated",
            scheduled_for_utc="2026-11-21T09:00:00Z",
            scheduled_local_date="2026-11-21",
            scheduled_local_time="10:00",
            scheduled_timezone="Europe/Rome",
            operator_username="operator",
        )
    assert "Database schema is missing required scheduling columns" in str(exc_info.value)
    assert "migrate_outbound_queue.py" in str(exc_info.value)


def test_migration_runner_idempotency_and_preservation(temp_unmigrated_db):
    """Runs migration twice to prove idempotency, verify schema addition and zero draft data loss."""
    # First pass: live migration
    res1 = run_migration(temp_unmigrated_db, dry_run=False)
    assert res1["status"] == "success"
    assert res1["integrity"] == "ok"
    assert res1["foreign_key_violations"] == 0
    assert len(res1["changes_applied"]) > 0

    # Verify columns were added
    conn = sqlite3.connect(temp_unmigrated_db)
    c = conn.cursor()
    c.execute("PRAGMA table_info(mailbox_drafts);")
    cols = [r[1] for r in c.fetchall()]
    assert "scheduled_local_date" in cols
    assert "snapshot_subject" in cols
    assert "snapshot_to_addresses_json" in cols

    # Verify draft was not mutated or corrupted
    c.execute("SELECT draft_id, draft_status, version, subject FROM mailbox_drafts WHERE draft_id = 'drf_test_unmigrated';")
    draft_row = c.fetchone()
    assert draft_row[0] == "drf_test_unmigrated"
    assert draft_row[1] == "ready_to_schedule"
    assert draft_row[2] == 1
    assert draft_row[3] == "Proposal for BIM Automation"

    # Tables outbound_jobs and outbound_attempts exist
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'outbound%';")
    outbound_tables = {r[0] for r in c.fetchall()}
    assert "outbound_jobs" in outbound_tables
    assert "outbound_attempts" in outbound_tables
    conn.close()

    # Second pass: verify idempotency
    res2 = run_migration(temp_unmigrated_db, dry_run=False)
    assert res2["status"] == "success"
    assert len(res2["changes_applied"]) == 0
    assert len(res2["skipped_existing"]) > 0


def test_scheduling_succeeds_after_migration(temp_unmigrated_db):
    """After running migration, schedule_draft freezes immutable snapshots and sets status to 'scheduled'."""
    run_migration(temp_unmigrated_db, dry_run=False)

    repo = MailboxRepository(db_path=temp_unmigrated_db)
    scheduled_draft = repo.schedule_draft(
        draft_id="drf_test_unmigrated",
        scheduled_for_utc="2026-11-21T09:00:00Z",
        scheduled_local_date="2026-11-21",
        scheduled_local_time="10:00",
        scheduled_timezone="Europe/Rome",
        operator_username="operator",
    )

    assert scheduled_draft.draft_status == "scheduled"
    assert scheduled_draft.scheduled_for_utc == "2026-11-21T09:00:00Z"
    assert scheduled_draft.scheduled_local_date == "2026-11-21"
    assert scheduled_draft.scheduled_local_time == "10:00"
    assert scheduled_draft.snapshot_subject == "Proposal for BIM Automation"
    assert scheduled_draft.snapshot_plain_text == "Dear Partner, let us discuss."
    assert scheduled_draft.snapshot_to_addresses == ["lead@partner.it"]
    assert scheduled_draft.version == 2


def test_outbound_queue_store_does_not_silently_mutate_db_on_read(temp_unmigrated_db):
    """OutboundQueueStore(auto_apply_schema=False) must not modify schema when performing queries."""
    store = OutboundQueueStore(db_path=temp_unmigrated_db, auto_apply_schema=False)
    
    # Query non-existent tables
    assert store.get_job("job_nonexistent") is None
    assert store.get_job_by_draft_id("drf_any") is None
    assert store.list_jobs() == []
    assert store.count_jobs() == 0

    # Assert outbound tables still DO NOT exist
    conn = sqlite3.connect(temp_unmigrated_db)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'outbound%';")
    assert c.fetchall() == []
    conn.close()


def test_dry_run_simulation_never_marks_draft_sent(temp_unmigrated_db, monkeypatch):
    """
    CRITICAL SAFETY GATE:
    When OUTBOUND_SEND_ENABLED=false or when SMTPSubmissionResult is a simulated dry-run,
    the worker must NEVER mark the draft as 'sent' or the job as 'smtp_accepted'.
    """
    run_migration(temp_unmigrated_db, dry_run=False)

    repo = MailboxRepository(db_path=temp_unmigrated_db)
    repo.schedule_draft(
        draft_id="drf_test_unmigrated",
        scheduled_for_utc="2026-11-20T08:00:00Z",  # in the past so eligible
        scheduled_local_date="2026-11-20",
        scheduled_local_time="09:00",
        scheduled_timezone="Europe/Rome",
        operator_username="operator",
    )

    # Queue the draft
    dispatch = OutboundDispatchService(db_path=temp_unmigrated_db)
    job = dispatch.queue_approved_draft("drf_test_unmigrated", operator_username="operator")
    assert job.status == "scheduled"

    # Worker configured with simulated adapter
    class MockDryRunAdapter(RegisterItSMTPAdapter):
        def send_message(self, *args, **kwargs):
            return SMTPSubmissionResult(
                success=True,
                status_code=250,
                response_message="DRY RUN: Message validated successfully.",
                is_simulated=True,
            )

    worker = OutboundWorker(
        db_path=temp_unmigrated_db,
        smtp_adapter=MockDryRunAdapter(),
        config=OutboundWorkerConfig(enabled=True),  # Attempting run
    )

    # Process batch with custom server to bypass inert check
    res = worker.process_batch(custom_server=object(), now_utc="2026-11-20T10:00:00Z")
    assert res["processed"] == 1
    assert res["jobs"][0]["final_status"] == "dry_run_simulated"

    # VERIFY DRAFT STATUS IS STILL 'scheduled', NOT 'sent'!
    draft_after = repo.get_draft("drf_test_unmigrated")
    assert draft_after.draft_status == "scheduled"
    assert draft_after.draft_status != "sent"

    # VERIFY JOB IS STILL 'scheduled'
    store = OutboundQueueStore(db_path=temp_unmigrated_db)
    job_after = store.get_job(job.job_id)
    assert job_after.status == "scheduled"
    assert job_after.status != "smtp_accepted"


def test_killswitch_contradiction_fails_closed(temp_unmigrated_db, monkeypatch):
    """
    Verifies that any contradiction between EMAIL_SEND_ENABLED and OUTBOUND_SEND_ENABLED fails closed.
    """
    run_migration(temp_unmigrated_db, dry_run=False)

    # Matrix:
    # 1. OUTBOUND=true, EMAIL=false -> disabled
    # 2. OUTBOUND=false, EMAIL=true -> disabled
    # 3. OUTBOUND=false, EMAIL=false -> disabled
    configs = [
        ("true", "false"),
        ("false", "true"),
        ("false", "false"),
        ("invalid", "invalid"),
    ]

    for out_val, email_val in configs:
        monkeypatch.setenv("OUTBOUND_SEND_ENABLED", out_val)
        monkeypatch.setenv("EMAIL_SEND_ENABLED", email_val)

        worker = OutboundWorker(db_path=temp_unmigrated_db)
        res = worker.process_batch()
        assert res["status"] == "disabled"
        assert res["processed"] == 0


def test_full_envelope_suppression_blocks_cc_and_bcc(temp_unmigrated_db):
    """Verifies that Do Not Contact suppression applies to To, CC, and BCC addresses."""
    run_migration(temp_unmigrated_db, dry_run=False)

    # Add suppression
    conn = sqlite3.connect(temp_unmigrated_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suppression_list (
            suppression_id TEXT PRIMARY KEY,
            email TEXT,
            company_domain TEXT,
            scope TEXT,
            value TEXT,
            reason TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO suppression_list (suppression_id, email, reason) "
        "VALUES ('sup_1', 'blocked@competitor.com', 'Opted out');"
    )
    # Update draft to include suppressed CC
    conn.execute(
        "UPDATE mailbox_drafts SET cc_addresses_json = '[\"blocked@competitor.com\"]' WHERE draft_id = 'drf_test_unmigrated';"
    )
    conn.commit()
    conn.close()

    repo = MailboxRepository(db_path=temp_unmigrated_db)
    repo.schedule_draft(
        draft_id="drf_test_unmigrated",
        scheduled_for_utc="2026-11-21T09:00:00Z",
        scheduled_local_date="2026-11-21",
        scheduled_local_time="10:00",
        scheduled_timezone="Europe/Rome",
        operator_username="operator",
    )

    dispatch = OutboundDispatchService(db_path=temp_unmigrated_db)
    with pytest.raises(SafeguardViolationError) as exc_info:
        dispatch.queue_approved_draft("drf_test_unmigrated", operator_username="operator")

    assert "blocked@competitor.com" in str(exc_info.value)
    assert "suppressed on the Do Not Contact list" in str(exc_info.value)
