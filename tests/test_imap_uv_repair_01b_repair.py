"""
tests/test_imap_uv_repair_01b_repair.py
Offline test suite for IMAP-UV-REPAIR-01B: Safe, Transactional Repair of Pilot UIDVALIDITY Records.

Verifications:
  1. Full successful transactional repair assigns positive UIDVALIDITY (Inbox: 1791495722, Sent: 1791495724).
  2. Distinct folder UIDVALIDITY values are correctly isolated and mapped.
  3. Pre-repair SQLite online backup and disposable clone restore verification pass.
  4. Dry-run mode performs zero database modifications and leaves imap_uidvalidity=0 intact.
  5. Checkpoint consistency validation: modified checkpoints abort repair safely before commit.
  6. Header mismatch or missing UID dynamically aborts repair and rolls back transaction.
  7. Idempotent rerun: executing repair when already repaired returns ALREADY_REPAIRED with zero writes.
  8. Zero operational CRM mutations: candidate_companies, candidate_contacts, manual_outreach_log untouched.
  9. Foreign keys & CRM link proposals intact: message IDs preserved, message_crm_links preserved.
 10. CLI safety: missing --confirm-repair or missing --live-connect fails closed with exit code 3.
"""

import os
import sqlite3
import pytest
from unittest.mock import MagicMock

from tests.support.isolated_subprocess import run_isolated_subprocess

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MailboxFolderSyncStateRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from scripts.run_mailbox_pilot import (
    MailboxPilotRunner,
    get_pilot_account_config,
    PILOT_ACCOUNT_ID,
    PILOT_EMAIL_ADDRESS,
    PILOT_CREDENTIAL_REF,
)


@pytest.fixture
def disposable_repair_env(tmp_path, monkeypatch):
    """Sets up an isolated disposable environment with 20 synthetic pilot records and CRM tables."""
    test_db = str(tmp_path / "disposable_repair_sales_outreach.db")
    cred_file = tmp_path / ".test_credentials.enc"
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)

    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)

    # 1. Initialize schema & base tables
    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE candidate_companies (
            candidate_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            normalized_company_name TEXT NOT NULL,
            company_domain TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE candidate_contacts (
            contact_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            work_email TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE TABLE historical_sent_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            subject TEXT,
            date_sent TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE manual_outreach_log (
            log_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            operator_notes TEXT,
            created_at TEXT NOT NULL
        );
    """)
    # Seed baseline operational CRM data
    cur.execute(
        "INSERT INTO candidate_companies (candidate_id, company_name, normalized_company_name, company_domain, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
        ("cmp_1001", "Acme Architecture", "acme architecture", "acme-arch.com", "qualified", "2026-11-01T00:00:00", "2026-11-01T00:00:00"),
    )
    cur.execute(
        "INSERT INTO candidate_contacts (contact_id, candidate_id, first_name, last_name, work_email, created_at) VALUES (?, ?, ?, ?, ?, ?);",
        ("cnt_2001", "cmp_1001", "Jan", "Jansen", "jan@acme-arch.com", "2026-11-01T00:00:00"),
    )
    cur.execute(
        "INSERT INTO historical_sent_emails (company_name, contact_email, subject, date_sent) VALUES (?, ?, ?, ?);",
        ("Acme Architecture", "jan@acme-arch.com", "Intro SalesAI", "2026-10-15T09:00:00"),
    )
    cur.execute(
        "INSERT INTO manual_outreach_log (log_id, company_name, contact_email, operator_notes, created_at) VALUES (?, ?, ?, ?, ?);",
        ("mol_3001", "Acme Architecture", "jan@acme-arch.com", "Spoke via LinkedIn", "2026-10-16T12:00:00"),
    )
    conn.commit()
    conn.close()

    # Apply mailbox migrations
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # Save pilot account
    pilot_acc = get_pilot_account_config()
    repo.save_account(pilot_acc)

    # Save folders
    inbox_fld = MailboxFolderRecord(
        folder_id=f"fld_{PILOT_ACCOUNT_ID}_inbox",
        account_id=PILOT_ACCOUNT_ID,
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=0,
    )
    sent_fld = MailboxFolderRecord(
        folder_id=f"fld_{PILOT_ACCOUNT_ID}_sent",
        account_id=PILOT_ACCOUNT_ID,
        folder_name="INBOX.Sent",
        folder_role="sent",
        last_uidvalidity=0,
    )
    repo.save_folder(inbox_fld)
    repo.save_folder(sent_fld)

    # Insert 10 inbox records (UIDs 2..11) with imap_uidvalidity = 0
    for u in range(2, 12):
        msg = MailboxMessageRecord(
            message_id=f"msg_inbox_{u}",
            account_id=PILOT_ACCOUNT_ID,
            folder_id=inbox_fld.folder_id,
            folder_role="inbox",
            direction="inbound",
            imap_uid=u,
            imap_uidvalidity=0,
            rfc_message_id=f"<msg-inbox-{u}@example.com>",
            from_address=f"sender{u}@client.com",
            from_name=f"Sender {u}",
            to_addresses=["info@pybim.com"],
            subject=f"Inquiry {u}",
            date_utc="2026-11-10T10:00:00+00:00",
        )
        repo.save_message(msg)

    # Insert 10 sent records (UIDs 8..17) with imap_uidvalidity = 0
    for u in range(8, 18):
        msg = MailboxMessageRecord(
            message_id=f"msg_sent_{u}",
            account_id=PILOT_ACCOUNT_ID,
            folder_id=sent_fld.folder_id,
            folder_role="sent",
            direction="outbound",
            imap_uid=u,
            imap_uidvalidity=0,
            rfc_message_id=f"<msg-sent-{u}@pybim.com>",
            from_address="info@pybim.com",
            to_addresses=[f"client{u}@firm.nl"],
            subject=f"Re: Inquiry {u}",
            date_utc="2026-11-13T10:00:00+00:00",
        )
        repo.save_message(msg)

    # Create 20 proposal links in message_crm_links
    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    for u in range(2, 12):
        cur.execute(
            """
            INSERT INTO message_crm_links (link_id, message_id, candidate_id, link_confidence, link_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (f"link_inbox_{u}", f"msg_inbox_{u}", "cmp_1001", 0.95, "suggested_pending_review", "2026-11-10T11:00:00", "2026-11-10T11:00:00"),
        )
    for u in range(8, 18):
        cur.execute(
            """
            INSERT INTO message_crm_links (link_id, message_id, candidate_id, link_confidence, link_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (f"link_sent_{u}", f"msg_sent_{u}", "cmp_1001", 0.90, "suggested_pending_review", "2026-11-13T11:00:00", "2026-11-13T11:00:00"),
        )
    conn.commit()
    conn.close()

    # Set initial checkpoints: INBOX=11, Sent=17 with uidvalidity=0
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{PILOT_ACCOUNT_ID}_{inbox_fld.folder_id}",
        account_id=PILOT_ACCOUNT_ID,
        folder_id=inbox_fld.folder_id,
        uidvalidity=0,
        last_synced_uid=11,
        messages_fetched_total=10,
        last_status="success",
    ))
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{PILOT_ACCOUNT_ID}_{sent_fld.folder_id}",
        account_id=PILOT_ACCOUNT_ID,
        folder_id=sent_fld.folder_id,
        uidvalidity=0,
        last_synced_uid=17,
        messages_fetched_total=10,
        last_status="success",
    ))

    cred_store = MailboxCredentialStore(store_path=str(cred_file))
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pilot_password")

    return {
        "db_path": test_db,
        "backup_dir": backup_dir,
        "cred_store": cred_store,
        "repo": repo,
    }


def _build_mock_email_bytes(rfc_id, subject, from_addr, to_addr, date_str):
    lines = []
    if rfc_id:
        lines.append(f"Message-ID: {rfc_id}")
    if subject:
        lines.append(f"Subject: {subject}")
    if from_addr:
        lines.append(f"From: {from_addr}")
    if to_addr:
        lines.append(f"To: {to_addr}")
    if date_str:
        lines.append(f"Date: {date_str}")
    lines.append("")
    lines.append("Body content")
    return "\r\n".join(lines).encode("utf-8")


def _setup_mock_provider(inbox_uv=1791495722, sent_uv=1791495724):
    """Creates a mock IMAP provider returning valid headers for all 20 pilot messages."""
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_client = MagicMock()
    mock_provider.connect.return_value = mock_client

    # Realistic provider header cleaning/parsing delegates
    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    current_folder = ["INBOX"]

    def fake_examine(client, folder_name):
        current_folder[0] = folder_name
        if "Sent" in folder_name:
            return 10, sent_uv
        return 10, inbox_uv

    mock_provider.examine_folder.side_effect = fake_examine

    def fake_uid(cmd, uids_str, fetch_args):
        if cmd.upper() == "FETCH":
            res_items = []
            uid_list = [int(x) for x in uids_str.split(",") if x.strip()]
            for u in uid_list:
                if "Sent" in current_folder[0]:
                    hdr_bytes = _build_mock_email_bytes(
                        rfc_id=f"<msg-sent-{u}@pybim.com>",
                        subject=f"Re: Inquiry {u}",
                        from_addr="info@pybim.com",
                        to_addr=f"client{u}@firm.nl",
                        date_str="Fri, 13 Nov 2026 10:00:00 +0000",
                    )
                else:
                    hdr_bytes = _build_mock_email_bytes(
                        rfc_id=f"<msg-inbox-{u}@example.com>",
                        subject=f"Inquiry {u}",
                        from_addr=f"sender{u}@client.com",
                        to_addr="info@pybim.com",
                        date_str="Tue, 10 Nov 2026 10:00:00 +0000",
                    )
                desc = f"{u} (UID {u} BODY[HEADER] {{{len(hdr_bytes)}}})".encode("ascii")
                res_items.append((desc, hdr_bytes))
            return "OK", res_items
        return "OK", []

    mock_client.uid.side_effect = fake_uid
    return mock_provider


def test_01_successful_transactional_repair(disposable_repair_env):
    """Verifies that full repair updates all 20 records and sync states to positive UIDVALIDITY."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    res = runner.repair_pilot_uidvalidity(
        dry_run=False,
        backup_dir=env["backup_dir"],
    )

    assert res["success"] is True
    assert res["status"] == "REPAIR_COMMITTED"
    assert res["server_uidvalidity"]["inbox"] == 1791495722
    assert res["server_uidvalidity"]["sent"] == 1791495724
    assert res["rows_updated"]["inbox_messages"] == 10
    assert res["rows_updated"]["sent_messages"] == 10
    assert res["rows_updated"]["mailbox_folders"] == 2
    assert res["rows_updated"]["mailbox_sync_state"] == 2
    assert res["post_verification"]["zero_uv_count"] == 0
    assert res["post_verification"]["crm_links_preserved"] == 20

    # Verify backup was created and intact
    assert os.path.exists(res["backup"]["backup_path"])
    assert res["backup"]["integrity_status"] == "ok"

    # Direct database queries to confirm state
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()

    inbox_uvs = cur.execute("SELECT DISTINCT imap_uidvalidity FROM mailbox_messages WHERE folder_role = 'inbox';").fetchall()
    assert inbox_uvs == [(1791495722,)]

    sent_uvs = cur.execute("SELECT DISTINCT imap_uidvalidity FROM mailbox_messages WHERE folder_role = 'sent';").fetchall()
    assert sent_uvs == [(1791495724,)]

    fld_uvs = cur.execute("SELECT folder_role, last_uidvalidity FROM mailbox_folders ORDER BY folder_role;").fetchall()
    assert fld_uvs == [("inbox", 1791495722), ("sent", 1791495724)]

    sync_uvs = cur.execute("SELECT folder_id, uidvalidity, last_synced_uid FROM mailbox_folder_sync_state ORDER BY folder_id;").fetchall()
    assert sync_uvs == [
        (f"fld_{PILOT_ACCOUNT_ID}_inbox", 1791495722, 11),
        (f"fld_{PILOT_ACCOUNT_ID}_sent", 1791495724, 17),
    ]

    conn.close()


def test_02_dry_run_makes_zero_modifications(disposable_repair_env):
    """Verifies that dry-run mode returns projected updates without modifying SQLite or creating backups."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    res = runner.repair_pilot_uidvalidity(
        dry_run=True,
        backup_dir=env["backup_dir"],
    )

    assert res["success"] is True
    assert res["status"] == "DRY_RUN_SUCCESS"
    assert res["dry_run"] is True
    assert res["projected_updates"]["inbox_messages"] == 10
    assert res["projected_updates"]["sent_messages"] == 10
    assert "backup" not in res  # No backup should be created during dry-run

    # Check zero database rows modified
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    zero_count = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE imap_uidvalidity = 0;").fetchone()[0]
    assert zero_count == 20
    conn.close()

    # Verify backup dir remains empty
    assert len(os.listdir(env["backup_dir"])) == 0


def test_03_idempotent_rerun_returns_already_repaired(disposable_repair_env):
    """Verifies that rerunning repair on an already repaired database is a safe no-op."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    # First run: commits repair
    res1 = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res1["status"] == "REPAIR_COMMITTED"

    # Second run: detects already repaired
    res2 = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res2["success"] is True
    assert res2["status"] == "ALREADY_REPAIRED"
    assert res2["rows_updated"]["inbox_messages"] == 0
    assert res2["rows_updated"]["sent_messages"] == 0


def test_04_mismatched_message_aborts_repair(disposable_repair_env):
    """Verifies that if any message fails remote header verification, repair aborts before DB write."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]

    # Mock provider where UID 5 has a mismatching subject
    mock_provider = _setup_mock_provider()
    orig_uid = mock_provider.connect.return_value.uid.side_effect

    def corrupted_uid(cmd, uids_str, fetch_args):
        typ, items = orig_uid(cmd, uids_str, fetch_args)
        new_items = []
        for desc, b in items:
            if b"UID 5" in desc:
                # Corrupt subject
                corrupt_b = _build_mock_email_bytes(
                    rfc_id="<msg-inbox-5@example.com>",
                    subject="TAMPERED SUBJECT",
                    from_addr="sender5@client.com",
                    to_addr="info@pybim.com",
                    date_str="Tue, 10 Nov 2026 10:00:00 +0000",
                )
                new_items.append((desc, corrupt_b))
            else:
                new_items.append((desc, b))
        return typ, new_items

    mock_provider.connect.return_value.uid.side_effect = corrupted_uid
    runner.provider_factory = lambda account: mock_provider

    res = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res["success"] is False
    assert res["error_category"] == "REPAIR_INELIGIBLE"

    # Verify zero database updates occurred
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    zero_count = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE imap_uidvalidity = 0;").fetchone()[0]
    assert zero_count == 20
    conn.close()


def test_05_inconsistent_checkpoints_abort_repair(disposable_repair_env):
    """Verifies that inconsistent sync checkpoints abort repair safely."""
    env = disposable_repair_env
    # Corrupt inbox checkpoint to 99 instead of 11
    conn = sqlite3.connect(env["db_path"])
    conn.execute(
        "UPDATE mailbox_folder_sync_state SET last_synced_uid = 99 WHERE folder_id = ?;",
        (f"fld_{PILOT_ACCOUNT_ID}_inbox",),
    )
    conn.commit()
    conn.close()

    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    res = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res["success"] is False
    assert res["error_category"] == "REPAIR_INELIGIBLE"

    # Confirm imap_uidvalidity unchanged
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    zero_count = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE imap_uidvalidity = 0;").fetchone()[0]
    assert zero_count == 20
    conn.close()


def test_06_zero_operational_crm_mutations(disposable_repair_env):
    """Verifies that operational CRM tables remain completely pristine throughout repair."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    # Capture initial CRM row counts and data
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    initial_companies = cur.execute("SELECT * FROM candidate_companies;").fetchall()
    initial_contacts = cur.execute("SELECT * FROM candidate_contacts;").fetchall()
    initial_history = cur.execute("SELECT * FROM historical_sent_emails;").fetchall()
    initial_outreach = cur.execute("SELECT * FROM manual_outreach_log;").fetchall()
    initial_crm_links = cur.execute("SELECT * FROM message_crm_links ORDER BY link_id;").fetchall()
    conn.close()

    res = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res["success"] is True

    # Re-verify CRM data
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    assert cur.execute("SELECT * FROM candidate_companies;").fetchall() == initial_companies
    assert cur.execute("SELECT * FROM candidate_contacts;").fetchall() == initial_contacts
    assert cur.execute("SELECT * FROM historical_sent_emails;").fetchall() == initial_history
    assert cur.execute("SELECT * FROM manual_outreach_log;").fetchall() == initial_outreach
    assert cur.execute("SELECT * FROM message_crm_links ORDER BY link_id;").fetchall() == initial_crm_links
    conn.close()


def test_07_backup_failure_aborts_before_write(disposable_repair_env, monkeypatch):
    """Verifies that if backup creation fails, repair aborts without any database writes."""
    env = disposable_repair_env
    runner = MailboxPilotRunner(db_path=env["db_path"])
    runner.credential_store = env["cred_store"]
    runner.provider_factory = lambda account: _setup_mock_provider()

    from sales_engine.candidates.backup_engine import BackupResult
    # Force backup failure
    def mock_failing_backup(**kwargs):
        return BackupResult(
            success=False,
            source_db_path=env["db_path"],
            backup_path="",
            backup_size_bytes=0,
            timestamp_utc="2026-11-15T00:00:00",
            sha256_checksum="",
            integrity_status="MOCK_DISK_FULL",
            fk_violations_count=0,
            fk_violations=[],
            error_message="Mock disk full simulation",
        )

    monkeypatch.setattr("sales_engine.candidates.backup_engine.create_sqlite_online_backup", mock_failing_backup)

    res = runner.repair_pilot_uidvalidity(dry_run=False, backup_dir=env["backup_dir"])
    assert res["success"] is False
    assert res["error_category"] == "BACKUP_FAILED"

    # Confirm imap_uidvalidity untouched
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    zero_count = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE imap_uidvalidity = 0;").fetchone()[0]
    assert zero_count == 20
    conn.close()


def test_08_cli_safety_requires_live_connect_and_confirmation(disposable_repair_env):
    """Verifies CLI flag enforcement: missing --live-connect or --confirm-repair exits with code 3."""
    import subprocess
    import sys

    env = disposable_repair_env

    # 1. Without --live-connect
    proc1 = run_isolated_subprocess(
        [sys.executable, "scripts/run_mailbox_pilot.py", "--db-path", env["db_path"], "repair-uidvalidity"],
        isolated_db_path=env["db_path"],
        capture_output=True,
        text=True,
    )
    assert proc1.returncode == 3
    assert "Live connection commands require explicit '--live-connect'" in proc1.stdout or "Live connection commands require explicit '--live-connect'" in proc1.stderr

    # 2. With --live-connect but without --confirm-repair or --dry-run
    proc2 = run_isolated_subprocess(
        [sys.executable, "scripts/run_mailbox_pilot.py", "--db-path", env["db_path"], "repair-uidvalidity", "--live-connect"],
        isolated_db_path=env["db_path"],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 3
    assert "--confirm-repair" in proc2.stdout or "--confirm-repair" in proc2.stderr
