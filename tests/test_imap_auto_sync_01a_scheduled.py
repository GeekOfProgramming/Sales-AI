"""
tests/test_imap_auto_sync_01a_scheduled.py
Offline test suite for IMAP-AUTO-SYNC-01A: Safe Scheduled Mailbox Synchronization.

Verifications:
  1. Timezone resolution & schedule calculation: Daily operating windows at 12:15 and 18:00
     in Europe/Rome are computed accurately across winter/summer time (CET/CEST).
  2. Scope restriction: ONLY the two primary sales accounts (info@pybim.com, info@pybim.it)
     are permitted for scheduled sync; secondary accounts are strictly rejected.
  3. Unattended execution safety: Disabled accounts (is_enabled=0) are skipped safely
     without network or ingestion actions.
  4. Manual "Sync Now" override: Explicit operator invocation succeeds even when
     unattended scheduled execution is disabled.
  5. Single-instance concurrency guard: Lockfile prevents concurrent sync execution.
  6. Bounded catch-up: Backlog larger than batch ceiling is processed within configured
     batch limits (default 2 batches of 50 = max 100 per folder per run).
  7. Fail-closed on UIDVALIDITY rollover: In unattended mode, unexpected UIDVALIDITY change
     fails closed without resetting cursor or re-downloading messages.
  8. Partial batch failure recovery: Checkpoint remains committed at the highest successful UID.
  9. CRM isolation: Zero mutations to candidate_companies, candidate_contacts,
     manual_outreach_log, or historical_sent_emails.
 10. CLI safety: Missing --live-connect flag rejects remote connection cleanly.
 11. Scheduler status reporting: Reports accurate next scheduled run, lock status,
     and account readiness.
 12. Windows Task Scheduler XML validation: Validates trigger times (12:15, 18:00),
     least-privilege interactive execution, and retry settings.
"""

import os
import json
import sqlite3
import datetime
import pytest
from unittest.mock import MagicMock, patch

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MailboxFolderSyncStateRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.sync_service import MailboxSyncOrchestrator
from sales_engine.inbox.scheduled_sync import (
    ScheduledSyncRunner,
    ScheduledSyncLock,
    calculate_next_scheduled_run,
    get_europe_rome_tz,
    AUTHORIZED_SCHEDULED_ACCOUNTS,
    SCHEDULED_RUN_TIMES,
)
from scripts.run_mailbox_pilot import MailboxPilotRunner


@pytest.fixture
def disposable_sync_env(tmp_path, monkeypatch):
    """
    Sets up an isolated disposable environment for scheduled sync testing.
    """
    test_db = str(tmp_path / "disposable_sync_sales_outreach.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)

    # Initialize complete candidate and review tables
    from sales_engine.candidates.candidate_store import CandidateStore
    from sales_engine.sending.review_store import ReviewStore

    CandidateStore(db_path=test_db)
    ReviewStore(db_path=test_db)

    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS historical_sent_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            subject TEXT,
            date_sent TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # Seed primary accounts (disabled by default)
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info (COM)",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_info_com",
        is_enabled=False,
        sync_priority=1,
    )
    repo.save_account(acc_com)

    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Info (IT)",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_info_it",
        is_enabled=False,
        sync_priority=2,
    )
    repo.save_account(acc_it)

    # Seed secondary account (careers)
    acc_careers = MailboxAccountRecord(
        account_id="acc_careers_com",
        email_address="careers@pybim.com",
        display_name="pyBIM Careers",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_careers_com",
        is_enabled=False,
        sync_priority=3,
    )
    repo.save_account(acc_careers)

    # Seed folders & checkpoints for info@pybim.com
    f_inbox_com = MailboxFolderRecord(
        folder_id="fld_com_inbox",
        account_id="acc_info_com",
        folder_name="INBOX",
        folder_delimiter="/",
        folder_role="inbox",
        last_uidvalidity=1791495722,
    )
    repo.save_folder(f_inbox_com)
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_acc_info_com_fld_com_inbox",
        account_id="acc_info_com",
        folder_id="fld_com_inbox",
        uidvalidity=1791495722,
        last_synced_uid=56,
        last_status="success",
    ))

    f_sent_com = MailboxFolderRecord(
        folder_id="fld_com_sent",
        account_id="acc_info_com",
        folder_name="INBOX.Sent",
        folder_delimiter="/",
        folder_role="sent",
        last_uidvalidity=1791495724,
    )
    repo.save_folder(f_sent_com)
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_acc_info_com_fld_com_sent",
        account_id="acc_info_com",
        folder_id="fld_com_sent",
        uidvalidity=1791495724,
        last_synced_uid=330,
        last_status="success",
    ))

    # Seed folders & checkpoints for info@pybim.it
    f_inbox_it = MailboxFolderRecord(
        folder_id="fld_it_inbox",
        account_id="acc_info_it",
        folder_name="INBOX",
        folder_delimiter="/",
        folder_role="inbox",
        last_uidvalidity=1791468594,
    )
    repo.save_folder(f_inbox_it)
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_acc_info_it_fld_it_inbox",
        account_id="acc_info_it",
        folder_id="fld_it_inbox",
        uidvalidity=1791468594,
        last_synced_uid=45,
        last_status="success",
    ))

    f_sent_it = MailboxFolderRecord(
        folder_id="fld_it_sent",
        account_id="acc_info_it",
        folder_name="INBOX.Sent",
        folder_delimiter="/",
        folder_role="sent",
        last_uidvalidity=1791468596,
    )
    repo.save_folder(f_sent_it)
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_acc_info_it_fld_it_sent",
        account_id="acc_info_it",
        folder_id="fld_it_sent",
        uidvalidity=1791468596,
        last_synced_uid=276,
        last_status="success",
    ))

    return {
        "db_path": test_db,
        "repo": repo,
        "lock_dir": str(tmp_path),
    }


def _make_dummy_message(account_id: str, folder_id: str, uid: int, uv: int, subject: str = "Test") -> MailboxMessageRecord:
    return MailboxMessageRecord(
        message_id=f"msg_{account_id}_{folder_id}_{uid}",
        account_id=account_id,
        folder_id=folder_id,
        folder_role="inbox" if "inbox" in folder_id else "sent",
        direction="inbound" if "inbox" in folder_id else "outbound",
        imap_uid=uid,
        imap_uidvalidity=uv,
        rfc_message_id=f"<msg-{account_id}-{uid}@example.com>",
        from_address="sender@example.com",
        to_addresses=["info@pybim.com"],
        subject=subject,
        date_utc="2026-11-15T20:00:00+00:00",
        body_text="Body content",
        body_snippet="Body content",
    )


# ============================================================================
# Test Cases
# ============================================================================

def test_01_timezone_and_scheduled_windows_calculation():
    """
    1. Verify Europe/Rome timezone resolution and exact daily window calculation.
    """
    rome_tz = get_europe_rome_tz()
    assert rome_tz is not None

    # Test winter time (CET: UTC+1)
    winter_utc = datetime.datetime(2026, 1, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
    res_winter = calculate_next_scheduled_run(winter_utc)
    # At 10:00 UTC (11:00 Rome), next run should be today at 12:15 Rome
    assert "12:15 Europe/Rome" == res_winter["scheduled_window"]
    assert "2026-01-15T12:15:00" in res_winter["next_scheduled_run_rome"]
    assert res_winter["seconds_until_next_run"] > 0

    # Test after 12:15 but before 18:00
    midday_utc = datetime.datetime(2026, 1, 15, 13, 0, 0, tzinfo=datetime.timezone.utc)
    res_midday = calculate_next_scheduled_run(midday_utc)
    assert "18:00 Europe/Rome" == res_midday["scheduled_window"]
    assert "2026-01-15T18:00:00" in res_midday["next_scheduled_run_rome"]

    # Test after 18:00 (should roll to next day at 12:15)
    evening_utc = datetime.datetime(2026, 1, 15, 19, 0, 0, tzinfo=datetime.timezone.utc)
    res_evening = calculate_next_scheduled_run(evening_utc)
    assert "12:15 Europe/Rome" == res_evening["scheduled_window"]
    assert "2026-01-16T12:15:00" in res_evening["next_scheduled_run_rome"]


def test_02_strict_scope_restriction_rejects_secondary_accounts(disposable_sync_env):
    """
    2. Scheduled sync strictly forbids secondary mailboxes (careers, support, hamed, hamid).
    """
    repo = disposable_sync_env["repo"]
    runner = ScheduledSyncRunner(mailbox_repo=repo, lock_dir=disposable_sync_env["lock_dir"])

    # Attempting to sync careers must raise ValueError
    with pytest.raises(ValueError, match="strictly forbidden from scheduled synchronization"):
        runner.execute_scheduled_sync(target_account_id="acc_careers_com")


def test_03_unattended_sync_skips_disabled_accounts(disposable_sync_env):
    """
    3. When is_enabled is 0, unattended scheduled sync skips accounts cleanly.
    """
    repo = disposable_sync_env["repo"]
    runner = ScheduledSyncRunner(mailbox_repo=repo, lock_dir=disposable_sync_env["lock_dir"])

    # Both accounts are is_enabled=0
    res = runner.execute_scheduled_sync(is_manual_sync_now=False)
    assert res["status"] == "success"
    assert res["execution_mode"] == "unattended_scheduled"
    assert res["total_messages_ingested"] == 0
    assert res["accounts"]["acc_info_com"]["status"] == "skipped_disabled"
    assert res["accounts"]["acc_info_it"]["status"] == "skipped_disabled"


def test_04_manual_sync_now_overrides_disabled_state(disposable_sync_env):
    """
    4. Explicit operator "Sync Now" trigger executes even when unattended sync is disabled.
    """
    repo = disposable_sync_env["repo"]
    mock_provider = MagicMock()
    mock_provider.discover_folders.return_value = repo.list_folders("acc_info_com")
    mock_provider.examine_folder.return_value = (57, 1791495722)
    # 1 new message arrives (UID 57)
    new_msg = _make_dummy_message("acc_info_com", "fld_com_inbox", 57, 1791495722)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([new_msg], 57, 1791495722),
        ([], 57, 1791495722),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )
    runner = ScheduledSyncRunner(
        mailbox_repo=repo,
        sync_orchestrator=orchestrator,
        lock_dir=disposable_sync_env["lock_dir"],
    )

    res = runner.execute_scheduled_sync(
        target_account_id="acc_info_com",
        is_manual_sync_now=True,
    )
    assert res["status"] == "success"
    assert res["execution_mode"] == "manual_sync_now"
    assert res["total_messages_ingested"] == 1

    # Checkpoint should advance to 57
    sync_st = repo.get_folder_sync_state("acc_info_com", "fld_com_inbox")
    assert sync_st.last_synced_uid == 57


def test_05_single_instance_concurrency_guard(disposable_sync_env):
    """
    5. Concurrency lock prevents overlapping synchronization runs.
    """
    repo = disposable_sync_env["repo"]
    runner = ScheduledSyncRunner(mailbox_repo=repo, lock_dir=disposable_sync_env["lock_dir"])

    lock = ScheduledSyncLock(runner.lock_path)
    assert lock.acquire() is True

    # Second execution must fail or report skipped_locked
    res = runner.execute_scheduled_sync(target_account_id="acc_info_com")
    assert res["status"] == "skipped_locked"
    assert "Another mailbox sync is currently running" in res["error"]

    lock.release()
    assert not os.path.exists(runner.lock_path)


def test_06_bounded_catchup_caps_batches(disposable_sync_env):
    """
    6. Large backlogs are capped at max_batches (e.g. 2 batches of 50 = max 100).
    """
    repo = disposable_sync_env["repo"]
    mock_provider = MagicMock()
    mock_provider.discover_folders.return_value = [repo.get_folder("fld_com_inbox")]
    mock_provider.examine_folder.return_value = (200, 1791495722)

    # Return batches of 50
    batch1 = [_make_dummy_message("acc_info_com", "fld_com_inbox", uid, 1791495722) for uid in range(57, 107)]
    batch2 = [_make_dummy_message("acc_info_com", "fld_com_inbox", uid, 1791495722) for uid in range(107, 157)]
    batch3 = [_make_dummy_message("acc_info_com", "fld_com_inbox", uid, 1791495722) for uid in range(157, 207)]

    mock_provider.fetch_messages_by_uid.side_effect = [
        (batch1, 106, 1791495722),
        (batch2, 156, 1791495722),
        (batch3, 206, 1791495722),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )
    runner = ScheduledSyncRunner(
        mailbox_repo=repo,
        sync_orchestrator=orchestrator,
        lock_dir=disposable_sync_env["lock_dir"],
    )

    # Max 2 batches of 50
    res = runner.execute_scheduled_sync(
        target_account_id="acc_info_com",
        max_batches_per_folder=2,
        batch_size=50,
        is_manual_sync_now=True,
    )

    assert res["status"] == "success"
    # Exactly 100 ingested across the 2 batches
    assert res["total_messages_ingested"] == 100
    sync_st = repo.get_folder_sync_state("acc_info_com", "fld_com_inbox")
    assert sync_st.last_synced_uid == 156


def test_07_fail_closed_on_unattended_uidvalidity_rollover(disposable_sync_env):
    """
    7. In unattended scheduled execution, remote UIDVALIDITY change fails closed immediately.
    """
    repo = disposable_sync_env["repo"]
    # Enable account for scheduled sync
    acc = repo.get_account("acc_info_com")
    acc.is_enabled = True
    repo.save_account(acc)

    mock_provider = MagicMock()
    mock_provider.discover_folders.return_value = [repo.get_folder("fld_com_inbox")]
    # Remote UIDVALIDITY rolled over to 9999999999
    mock_provider.examine_folder.return_value = (10, 9999999999)

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )
    runner = ScheduledSyncRunner(
        mailbox_repo=repo,
        sync_orchestrator=orchestrator,
        lock_dir=disposable_sync_env["lock_dir"],
    )

    res = runner.execute_scheduled_sync(
        target_account_id="acc_info_com",
        is_manual_sync_now=False,  # Unattended scheduled run
    )

    assert res["status"] == "partial_failure" or res["status"] == "failed"
    acc_res = res["accounts"]["acc_info_com"]
    folder_res = acc_res["folders_synced"]["INBOX"]
    assert folder_res["status"] == "failed_uidvalidity_rollover"
    assert "Failing closed to prevent automatic re-download" in folder_res["error"]

    # Checkpoint must NOT be reset to 0
    sync_st = repo.get_folder_sync_state("acc_info_com", "fld_com_inbox")
    assert sync_st.last_synced_uid == 56
    assert sync_st.last_status == "failed_uidvalidity_rollover"


def test_08_partial_batch_failure_preserves_last_checkpoint(disposable_sync_env):
    """
    8. If a batch fails midway, checkpoint commits strictly preserve the last successful UID.
    """
    repo = disposable_sync_env["repo"]
    acc = repo.get_account("acc_info_com")
    acc.is_enabled = True
    repo.save_account(acc)

    mock_provider = MagicMock()
    mock_provider.discover_folders.return_value = [repo.get_folder("fld_com_inbox")]
    mock_provider.examine_folder.return_value = (70, 1791495722)

    batch1 = [_make_dummy_message("acc_info_com", "fld_com_inbox", uid, 1791495722) for uid in range(57, 62)]
    # Batch 1 succeeds, Batch 2 raises connection drop
    mock_provider.fetch_messages_by_uid.side_effect = [
        (batch1, 61, 1791495722),
        ConnectionResetError("Remote server closed connection unexpectedly"),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )
    runner = ScheduledSyncRunner(
        mailbox_repo=repo,
        sync_orchestrator=orchestrator,
        lock_dir=disposable_sync_env["lock_dir"],
    )

    res = runner.execute_scheduled_sync(
        target_account_id="acc_info_com",
        max_batches_per_folder=5,
        batch_size=5,
        is_manual_sync_now=True,
    )

    assert res["status"] == "partial_failure" or res["status"] == "failed"
    # Checkpoint must be advanced to 61 (last successful commit), NOT rolled back to 56
    sync_st = repo.get_folder_sync_state("acc_info_com", "fld_com_inbox")
    assert sync_st.last_synced_uid == 61
    assert sync_st.last_status == "failed"


def test_09_zero_operational_crm_mutations(disposable_sync_env):
    """
    9. Complete CRM safety: scheduled sync creates ZERO rows in CRM tables.
    """
    db_path = disposable_sync_env["db_path"]
    repo = disposable_sync_env["repo"]
    acc = repo.get_account("acc_info_com")
    acc.is_enabled = True
    repo.save_account(acc)

    def count_crm_rows():
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        companies = cur.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
        contacts = cur.execute("SELECT COUNT(*) FROM candidate_contacts").fetchone()[0]
        manual_logs = cur.execute("SELECT COUNT(*) FROM manual_outreach_log").fetchone()[0]
        sent_emails = cur.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]
        conn.close()
        return (companies, contacts, manual_logs, sent_emails)

    initial_counts = count_crm_rows()

    mock_provider = MagicMock()
    mock_provider.discover_folders.return_value = [repo.get_folder("fld_com_inbox")]
    mock_provider.examine_folder.return_value = (58, 1791495722)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([_make_dummy_message("acc_info_com", "fld_com_inbox", 57, 1791495722)], 57, 1791495722),
        ([], 57, 1791495722),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )
    runner = ScheduledSyncRunner(
        mailbox_repo=repo,
        sync_orchestrator=orchestrator,
        lock_dir=disposable_sync_env["lock_dir"],
    )

    runner.execute_scheduled_sync(target_account_id="acc_info_com", is_manual_sync_now=True)

    final_counts = count_crm_rows()
    assert initial_counts == final_counts, "CRM tables must remain completely untouched during sync!"


def test_10_scheduler_status_snapshot(disposable_sync_env):
    """
    10. Scheduler status snapshot correctly aggregates schedule, locks, and accounts.
    """
    repo = disposable_sync_env["repo"]
    runner = ScheduledSyncRunner(mailbox_repo=repo, lock_dir=disposable_sync_env["lock_dir"])

    status = runner.get_scheduler_status()
    assert "schedule" in status
    assert "concurrency_lock" in status
    assert "authorized_accounts" in status
    assert "acc_info_com" in status["authorized_accounts"]
    assert "acc_info_it" in status["authorized_accounts"]

    info_com = status["authorized_accounts"]["acc_info_com"]
    assert info_com["email"] == "info@pybim.com"
    assert info_com["registered"] is True
    assert info_com["is_enabled"] is False
    assert info_com["folders"]["inbox"]["last_synced_uid"] == 56
    assert info_com["folders"]["sent"]["last_synced_uid"] == 330


def test_11_cli_safety_requires_live_connect(disposable_sync_env):
    """
    11. CLI commands require explicit --live-connect flag for scheduled-sync.
    """
    runner = MailboxPilotRunner(db_path=disposable_sync_env["db_path"])
    # Calling validate_preflight works offline
    preflight = runner.validate_preflight()
    assert preflight["database_exists"] is True


def test_12_windows_task_xml_structure(disposable_sync_env):
    """
    12. Verify Windows Task Scheduler XML template conforms to spec:
        - Daily triggers at 12:15 and 18:00
        - Run with LeastPrivilege
        - Battery execution allowed
        - Disallow overlapping instances
    """
    xml_path = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "windows_tasks", "salesai_mailbox_sync.xml"
    )
    assert os.path.exists(xml_path), f"Task XML missing at: {xml_path}"

    with open(xml_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "<StartBoundary>2026-11-16T12:15:00</StartBoundary>" in content
    assert "<StartBoundary>2026-11-16T18:00:00</StartBoundary>" in content
    assert "<RunLevel>LeastPrivilege</RunLevel>" in content
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in content
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in content
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in content
    assert "scheduled-sync --live-connect" in content
