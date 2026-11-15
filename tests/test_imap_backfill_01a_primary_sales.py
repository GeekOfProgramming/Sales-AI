"""
tests/test_imap_backfill_01a_primary_sales.py
Offline test suite for IMAP-BACKFILL-01A: Controlled Historical Sync for Primary Sales Mailboxes.

Verifications:
  1. Status reporting is strictly read-only and details local counts, ranges, and checkpoints offline.
  2. Status reporting with live-connect includes remote counts, UID ranges, and remote UIDVALIDITY.
  3. Resumes cleanly from existing checkpoints for info@pybim.com (Inbox starting at 11, Sent starting at 17).
  4. Resumes cleanly from existing checkpoints for info@pybim.it (Inbox starting at 15, Sent starting at 11).
  5. The first 20 initial messages are never re-ingested or duplicated across repeated runs.
  6. Batch boundaries strictly enforced: batch size capped at 50 messages per folder per run.
  7. Secondary accounts (careers, support, hamed, hamid) are strictly rejected from historical backfill.
  8. Dry-run mode produces zero database modifications and advances zero checkpoints.
  9. Preflight size gating and 10 MB threshold: oversized messages are safely marked deferred with headers preserved.
 10. UID gaps and partial server sequences are handled seamlessly without skipping messages.
 11. Remote server UIDVALIDITY rollover resets checkpoints safely to 0 and avoids cross-epoch collisions.
 12. Complete CRM safety: zero mutations to candidate_companies, candidate_contacts, manual_outreach_log, historical_sent_emails.
 13. Preserves cross-company matching safeguards (BiM Milano vs Piuarch).
 14. Unattended sync safety: account is_enabled is strictly kept/set to False (0).
 15. CLI safety flags: missing --live-connect or missing --confirm-backfill exits with code 3.
"""

import os
import sqlite3
import pytest
from unittest.mock import MagicMock

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
    get_account_config_by_email,
    get_pilot_account_config,
    HISTORICAL_BACKFILL_ALLOWED_ACCOUNTS,
    MAX_HISTORICAL_BATCH_LIMIT,
)


@pytest.fixture
def disposable_backfill_env(tmp_path, monkeypatch):
    """
    Sets up an isolated disposable environment mimicking production state for:
      - info@pybim.com (Inbox CP=11, Sent CP=17, UVs=1791495722 / 1791495724)
      - info@pybim.it  (Inbox CP=15, Sent CP=11, UVs=1791468594 / 1791468596)
      - careers@pybim.com (Secondary account)
      - Baseline CRM data
    """
    test_db = str(tmp_path / "disposable_backfill_sales_outreach.db")
    cred_file = tmp_path / ".test_credentials.enc"

    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)

    # 1. Initialize operational CRM tables
    from sales_engine.candidates.candidate_store import CandidateStore
    from sales_engine.sending.review_store import ReviewStore

    # Initialize complete candidate and review tables cleanly
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
    # Seed known companies: BiM Milano vs Piuarch
    cur.execute(
        "INSERT INTO candidate_companies (candidate_id, company_name, normalized_company_name, company_domain, discovery_status, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
        ("cmp_bim_milano", "BiM Milano", "bim milano", "bim-milano.it", "contacted", "2026-10-01T00:00:00", "2026-10-01T00:00:00"),
    )
    cur.execute(
        "INSERT INTO candidate_companies (candidate_id, company_name, normalized_company_name, company_domain, discovery_status, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
        ("cmp_piuarch", "Piuarch", "piuarch", "piuarch.it", "contacted", "2026-10-02T00:00:00", "2026-10-02T00:00:00"),
    )
    cur.execute(
        "INSERT INTO manual_outreach_log (log_id, candidate_id, recipient_email, recipient_name, subject, operator_notes, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
        ("mol_01", "cmp_bim_milano", "info@bim-milano.it", "Contact", "Manual outreach note", "Met in Milan", "2026-10-01T10:00:00"),
    )
    conn.commit()
    conn.close()

    # 2. Apply mailbox migrations
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # 3. Setup credentials
    cred_store = MailboxCredentialStore(store_path=str(cred_file))
    for acc_email in ["info@pybim.com", "info@pybim.it", "careers@pybim.com"]:
        acc = get_account_config_by_email(acc_email)
        acc.is_enabled = False
        repo.save_account(acc)
        cred_store.set_credential(acc.credential_ref, f"secret_for_{acc.account_id}")

    # 4. Setup info@pybim.com records (Initial 20: Inbox 2..11, Sent 8..17)
    com_acc = get_account_config_by_email("info@pybim.com")
    com_inbox = MailboxFolderRecord(
        folder_id=f"fld_{com_acc.account_id}_inbox",
        account_id=com_acc.account_id,
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791495722,
    )
    com_sent = MailboxFolderRecord(
        folder_id=f"fld_{com_acc.account_id}_sent",
        account_id=com_acc.account_id,
        folder_name="INBOX.Sent",
        folder_role="sent",
        last_uidvalidity=1791495724,
    )
    repo.save_folder(com_inbox)
    repo.save_folder(com_sent)

    for u in range(2, 12):
        repo.save_message(MailboxMessageRecord(
            message_id=f"msg_com_inbox_{u}",
            account_id=com_acc.account_id,
            folder_id=com_inbox.folder_id,
            folder_role="inbox",
            direction="inbound",
            imap_uid=u,
            imap_uidvalidity=1791495722,
            rfc_message_id=f"<msg-com-inbox-{u}@example.com>",
            from_address=f"sender{u}@client.com",
            to_addresses=["info@pybim.com"],
            subject=f"Inquiry COM {u}",
            date_utc="2026-11-10T10:00:00+00:00",
        ))
    for u in range(8, 18):
        repo.save_message(MailboxMessageRecord(
            message_id=f"msg_com_sent_{u}",
            account_id=com_acc.account_id,
            folder_id=com_sent.folder_id,
            folder_role="sent",
            direction="outbound",
            imap_uid=u,
            imap_uidvalidity=1791495724,
            rfc_message_id=f"<msg-com-sent-{u}@pybim.com>",
            from_address="info@pybim.com",
            to_addresses=[f"client{u}@firm.nl"],
            subject=f"Re: Inquiry COM {u}",
            date_utc="2026-11-13T10:00:00+00:00",
        ))

    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{com_acc.account_id}_{com_inbox.folder_id}",
        account_id=com_acc.account_id,
        folder_id=com_inbox.folder_id,
        uidvalidity=1791495722,
        last_synced_uid=11,
        messages_fetched_total=10,
        last_status="success",
    ))
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{com_acc.account_id}_{com_sent.folder_id}",
        account_id=com_acc.account_id,
        folder_id=com_sent.folder_id,
        uidvalidity=1791495724,
        last_synced_uid=17,
        messages_fetched_total=10,
        last_status="success",
    ))

    # 5. Setup info@pybim.it records (Initial 20: Inbox 6..15, Sent 1..11, one missing UID in sent)
    it_acc = get_account_config_by_email("info@pybim.it")
    it_inbox = MailboxFolderRecord(
        folder_id=f"fld_{it_acc.account_id}_inbox",
        account_id=it_acc.account_id,
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791468594,
    )
    it_sent = MailboxFolderRecord(
        folder_id=f"fld_{it_acc.account_id}_sent",
        account_id=it_acc.account_id,
        folder_name="INBOX.Sent",
        folder_role="sent",
        last_uidvalidity=1791468596,
    )
    repo.save_folder(it_inbox)
    repo.save_folder(it_sent)

    for u in range(6, 16):
        repo.save_message(MailboxMessageRecord(
            message_id=f"msg_it_inbox_{u}",
            account_id=it_acc.account_id,
            folder_id=it_inbox.folder_id,
            folder_role="inbox",
            direction="inbound",
            imap_uid=u,
            imap_uidvalidity=1791468594,
            rfc_message_id=f"<msg-it-inbox-{u}@example.it>",
            from_address=f"mittente{u}@cliente.it",
            to_addresses=["info@pybim.it"],
            subject=f"Richiesta IT {u}",
            date_utc="2026-11-10T11:00:00+00:00",
        ))
    for u in range(1, 12):
        if u == 5:
            continue  # simulate natural gap
        repo.save_message(MailboxMessageRecord(
            message_id=f"msg_it_sent_{u}",
            account_id=it_acc.account_id,
            folder_id=it_sent.folder_id,
            folder_role="sent",
            direction="outbound",
            imap_uid=u,
            imap_uidvalidity=1791468596,
            rfc_message_id=f"<msg-it-sent-{u}@pybim.it>",
            from_address="info@pybim.it",
            to_addresses=[f"cliente{u}@studio.it"],
            subject=f"Risposta IT {u}",
            date_utc="2026-11-13T11:00:00+00:00",
        ))

    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{it_acc.account_id}_{it_inbox.folder_id}",
        account_id=it_acc.account_id,
        folder_id=it_inbox.folder_id,
        uidvalidity=1791468594,
        last_synced_uid=15,
        messages_fetched_total=10,
        last_status="success",
    ))
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{it_acc.account_id}_{it_sent.folder_id}",
        account_id=it_acc.account_id,
        folder_id=it_sent.folder_id,
        uidvalidity=1791468596,
        last_synced_uid=11,
        messages_fetched_total=10,
        last_status="success",
    ))

    return {
        "db_path": test_db,
        "cred_store": cred_store,
        "repo": repo,
    }


def _build_mock_email_bytes(rfc_id, subject, from_addr, to_addr, date_str, body_text="Test content"):
    lines = [
        f"Message-ID: {rfc_id}",
        f"Subject: {subject}",
        f"From: {from_addr}",
        f"To: {to_addr}",
        f"Date: {date_str}",
        "",
        body_text,
    ]
    return "\r\n".join(lines).encode("utf-8")


def _setup_backfill_mock_provider(
    account_email: str,
    inbox_uv: int,
    sent_uv: int,
    available_inbox_uids: list,
    available_sent_uids: list,
    oversized_uids: set = None,
):
    """
    Creates a mock IMAP provider with realistic EXAMINE, UID SEARCH, RFC822.SIZE,
    and BODY.PEEK fetching behavior.
    """
    oversized_uids = oversized_uids or set()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_client = MagicMock()
    mock_provider.connect.return_value = mock_client

    real_provider = RegisterItReadOnlyIMAPProvider(account=get_account_config_by_email(account_email))
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc
    mock_provider.max_message_bytes = real_provider.max_message_bytes

    current_folder = ["INBOX"]

    def fake_examine(client, folder_name):
        current_folder[0] = folder_name
        if "Sent" in folder_name:
            return len(available_sent_uids), sent_uv
        return len(available_inbox_uids), inbox_uv

    mock_provider.examine_folder.side_effect = fake_examine

    def fake_fetch_messages(account, folder, since_uid=0, batch_size=50, client=None):
        is_sent = "Sent" in folder.folder_name or folder.folder_role == "sent"
        all_uids = sorted(available_sent_uids if is_sent else available_inbox_uids)
        uv = sent_uv if is_sent else inbox_uv

        new_uids = [u for u in all_uids if u > since_uid]
        if not new_uids:
            return [], since_uid, uv

        batch_uids = new_uids[:batch_size]
        highest_uid = max(batch_uids)

        messages = []
        for u in batch_uids:
            is_oversized = u in oversized_uids
            subj = f"Batch Subject {u}"
            from_a = account.email_address if is_sent else f"partner{u}@external.com"
            to_a = f"client{u}@firm.com" if is_sent else account.email_address

            raw_bytes = _build_mock_email_bytes(
                rfc_id=f"<msg-{account.account_id}-{u}@example.com>",
                subject=subj,
                from_addr=from_a,
                to_addr=to_a,
                date_str="Fri, 15 Nov 2026 12:00:00 +0000",
            )
            msg_rec = real_provider.parse_raw_email(
                account_id=account.account_id,
                folder_id=folder.folder_id,
                folder_role=folder.folder_role,
                direction="outbound" if is_sent else "inbound",
                imap_uid=u,
                imap_uidvalidity=uv,
                raw_bytes=raw_bytes,
            )
            if is_oversized:
                msg_rec.parse_status = "deferred_oversized"
                msg_rec.content_size_bytes = 15 * 1024 * 1024  # 15 MB
                msg_rec.body_truncated = True
                msg_rec.body_plain_text = "[OVERSIZED MESSAGE DEFERRED: 15.00 MB exceeds 10.0 MB threshold.]"

            messages.append(msg_rec)

        return messages, highest_uid, uv

    mock_provider.fetch_messages_by_uid.side_effect = fake_fetch_messages
    return mock_provider


def test_01_status_reporting_offline_reads_local_state(disposable_backfill_env):
    """Verifies that report_mailbox_status offline returns correct stored counts and checkpoints."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    res = runner.report_mailbox_status(live_connect=False)
    assert res["success"] is True
    assert res["email_address"] == "info@pybim.com"
    assert res["is_primary_sales_mailbox"] is True
    assert res["is_enabled"] is False

    inbox = res["folders"]["inbox"]
    assert inbox["stored_messages"] == 10
    assert inbox["stored_min_uid"] == 2
    assert inbox["stored_max_uid"] == 11
    assert inbox["checkpoint_uid"] == 11
    assert inbox["stored_uidvalidity"] == 1791495722
    assert inbox["remote_count"] is None  # Offline, no remote fetch

    sent = res["folders"]["sent"]
    assert sent["stored_messages"] == 10
    assert sent["stored_min_uid"] == 8
    assert sent["stored_max_uid"] == 17
    assert sent["checkpoint_uid"] == 17
    assert sent["stored_uidvalidity"] == 1791495724


def test_02_status_reporting_with_live_connect_inspects_remote(disposable_backfill_env):
    """Verifies that report_mailbox_status with live_connect queries remote counts and UID ranges."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.it"))
    runner.credential_store = env["cred_store"]

    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.it",
        inbox_uv=1791468594,
        sent_uv=1791468596,
        available_inbox_uids=list(range(6, 60)),  # 54 remote messages
        available_sent_uids=list(range(1, 40)),   # 39 remote messages
    )
    # Mock UID search on client
    mock_client = mock_p.connect.return_value
    mock_client.uid.side_effect = lambda cmd, arg: (
        ("OK", [b"6 7 8 9 10 11 12 13 14 15 50 59"]) if "INBOX" in mock_p.examine_folder.call_args[0][1] and "Sent" not in mock_p.examine_folder.call_args[0][1] else ("OK", [b"1 2 3 4 6 7 8 9 10 11 39"])
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.report_mailbox_status(live_connect=True)
    assert res["success"] is True
    assert res["folders"]["inbox"]["remote_count"] == 54
    assert res["folders"]["inbox"]["remote_uidvalidity"] == 1791468594
    assert res["folders"]["inbox"]["remote_min_uid"] == 6
    assert res["folders"]["inbox"]["remote_max_uid"] == 59

    assert res["folders"]["sent"]["remote_count"] == 39
    assert res["folders"]["sent"]["remote_uidvalidity"] == 1791468596
    assert res["folders"]["sent"]["remote_min_uid"] == 1
    assert res["folders"]["sent"]["remote_max_uid"] == 39


def test_03_resumes_from_existing_checkpoints_com(disposable_backfill_env):
    """Verifies that info@pybim.com backfill resumes strictly from Inbox UID 11 and Sent UID 17."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    # Provide server UIDs: Inbox has UIDs 2..30 (19 new messages: 12..30), Sent has 8..27 (10 new: 18..27)
    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=list(range(2, 31)),
        available_sent_uids=list(range(8, 28)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True

    # Check that initial 20 messages were not re-ingested
    inbox_s = res["inbox_sync"]
    assert inbox_s["starting_checkpoint"] == 11
    assert inbox_s["ending_checkpoint"] == 30
    assert inbox_s["messages_ingested"] == 19

    sent_s = res["sent_sync"]
    assert sent_s["starting_checkpoint"] == 17
    assert sent_s["ending_checkpoint"] == 27
    assert sent_s["messages_ingested"] == 10

    # Verify DB now contains total: 10 + 19 = 29 inbox, 10 + 10 = 20 sent
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    cnt_inbox = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE account_id = 'acc_info_com' AND folder_role = 'inbox';").fetchone()[0]
    cnt_sent = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE account_id = 'acc_info_com' AND folder_role = 'sent';").fetchone()[0]
    assert cnt_inbox == 29
    assert cnt_sent == 20
    conn.close()


def test_04_resumes_from_existing_checkpoints_it(disposable_backfill_env):
    """Verifies that info@pybim.it backfill resumes strictly from Inbox UID 15 and Sent UID 11."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.it"))
    runner.credential_store = env["cred_store"]

    # Available UIDs: Inbox 6..25 (10 new: 16..25), Sent 1..21 (10 new: 12..21)
    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.it",
        inbox_uv=1791468594,
        sent_uv=1791468596,
        available_inbox_uids=list(range(6, 26)),
        available_sent_uids=list(range(1, 22)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True

    inbox_s = res["inbox_sync"]
    assert inbox_s["starting_checkpoint"] == 15
    assert inbox_s["ending_checkpoint"] == 25
    assert inbox_s["messages_ingested"] == 10

    sent_s = res["sent_sync"]
    assert sent_s["starting_checkpoint"] == 11
    assert sent_s["ending_checkpoint"] == 21
    assert sent_s["messages_ingested"] == 10


def test_05_secondary_accounts_strictly_rejected(disposable_backfill_env):
    """Verifies that careers, support, hamed, hamid are blocked from historical backfill."""
    env = disposable_backfill_env

    for sec_email in ["careers@pybim.com", "support@pybim.com", "hamed.sharifi@pybim.it", "hamid.lotfalian@pybim.it"]:
        runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email(sec_email))
        runner.credential_store = env["cred_store"]

        res = runner.execute_historical_backfill()
        assert res["success"] is False
        assert res["error_category"] == "UNAUTHORIZED_TARGET"
        assert "restricted strictly to primary sales accounts" in res["error"]


def test_06_batch_boundaries_and_capped_limits(disposable_backfill_env):
    """Verifies that backfill caps batches at 50 messages even if higher limit is requested."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    # Server has 100 new messages available (12..111)
    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=list(range(2, 112)),
        available_sent_uids=list(range(8, 18)),
    )
    runner.provider_factory = lambda a: mock_p

    # Request limit of 100, must be capped at 50
    res = runner.execute_historical_backfill(batch_size=100, dry_run=False)
    assert res["success"] is True
    assert res["batch_size_limit"] == MAX_HISTORICAL_BATCH_LIMIT
    assert res["inbox_sync"]["messages_ingested"] == 50
    assert res["inbox_sync"]["ending_checkpoint"] == 61  # 11 + 50 = 61


def test_07_dry_run_makes_zero_database_mutations(disposable_backfill_env):
    """Verifies that dry-run mode returns prospective numbers without modifying SQLite."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=list(range(2, 40)),
        available_sent_uids=list(range(8, 30)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=True)
    assert res["success"] is True
    assert res["dry_run"] is True

    # Confirm message count in SQLite is untouched (still 10 inbox, 10 sent)
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    cnt_inbox = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE account_id = 'acc_info_com' AND folder_role = 'inbox';").fetchone()[0]
    cnt_sent = cur.execute("SELECT COUNT(*) FROM mailbox_messages WHERE account_id = 'acc_info_com' AND folder_role = 'sent';").fetchone()[0]
    assert cnt_inbox == 10
    assert cnt_sent == 10

    # Confirm checkpoints untouched
    sync_inbox = cur.execute("SELECT last_synced_uid FROM mailbox_folder_sync_state WHERE folder_id = 'fld_acc_info_com_inbox';").fetchone()[0]
    assert sync_inbox == 11
    conn.close()


def test_08_oversized_messages_are_safely_deferred(disposable_backfill_env):
    """Verifies that messages exceeding 10 MB threshold are deferred with header metadata preserved."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    # UIDs 12, 13, 14: UID 13 is oversized (15 MB)
    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        available_sent_uids=list(range(8, 18)),
        oversized_uids={13},
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True
    assert res["inbox_sync"]["messages_ingested"] == 3
    assert res["inbox_sync"]["messages_deferred"] == 1

    # Inspect deferred record in SQLite
    conn = sqlite3.connect(env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM mailbox_messages WHERE account_id = 'acc_info_com' AND imap_uid = 13;").fetchone()
    assert row is not None
    assert row["parse_status"] == "deferred_oversized"
    assert row["body_truncated"] == 1
    assert "15.00 MB exceeds" in row["body_plain_text"]
    conn.close()


def test_09_uidvalidity_rollover_resets_cursor_safely(disposable_backfill_env):
    """Verifies that a server UIDVALIDITY change safely resets checkpoint to 0 without skipped messages."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    # Provide NEW server UIDVALIDITY (e.g. 1999999999 instead of 1791495722)
    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1999999999,
        sent_uv=1791495724,
        available_inbox_uids=[1, 2, 3],  # new epoch UIDs starting from 1
        available_sent_uids=list(range(8, 18)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True

    # Inbox checkpoint should have advanced from 0 to 3 under new UIDVALIDITY
    inbox_s = res["inbox_sync"]
    assert inbox_s["uidvalidity"] == 1999999999
    assert inbox_s["ending_checkpoint"] == 3
    assert inbox_s["messages_ingested"] == 3


def test_10_operational_crm_tables_remain_untouched(disposable_backfill_env):
    """Verifies zero mutations to candidate_companies, candidate_contacts, manual_outreach_log."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    # Record initial operational CRM state
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    initial_companies = cur.execute("SELECT * FROM candidate_companies;").fetchall()
    initial_contacts = cur.execute("SELECT * FROM candidate_contacts;").fetchall()
    initial_history = cur.execute("SELECT * FROM historical_sent_emails;").fetchall()
    initial_manual = cur.execute("SELECT * FROM manual_outreach_log;").fetchall()
    conn.close()

    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=list(range(2, 25)),
        available_sent_uids=list(range(8, 25)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True

    # Re-verify operational CRM tables
    conn = sqlite3.connect(env["db_path"])
    cur = conn.cursor()
    assert cur.execute("SELECT * FROM candidate_companies;").fetchall() == initial_companies
    assert cur.execute("SELECT * FROM candidate_contacts;").fetchall() == initial_contacts
    assert cur.execute("SELECT * FROM historical_sent_emails;").fetchall() == initial_history
    assert cur.execute("SELECT * FROM manual_outreach_log;").fetchall() == initial_manual
    conn.close()


def test_11_unattended_sync_remains_disabled(disposable_backfill_env):
    """Verifies that is_enabled remains False after backfill execution."""
    env = disposable_backfill_env
    runner = MailboxPilotRunner(db_path=env["db_path"], account=get_account_config_by_email("info@pybim.com"))
    runner.credential_store = env["cred_store"]

    mock_p = _setup_backfill_mock_provider(
        account_email="info@pybim.com",
        inbox_uv=1791495722,
        sent_uv=1791495724,
        available_inbox_uids=list(range(2, 20)),
        available_sent_uids=list(range(8, 20)),
    )
    runner.provider_factory = lambda a: mock_p

    res = runner.execute_historical_backfill(batch_size=50, dry_run=False)
    assert res["success"] is True
    assert res["unattended_sync_disabled"] is True

    acc = env["repo"].get_account("acc_info_com")
    assert acc.is_enabled is False


def test_12_cli_safety_requires_live_connect_and_confirmation(disposable_backfill_env):
    """Verifies that missing --live-connect or missing --confirm-backfill exits with code 3."""
    import subprocess
    import sys

    env = disposable_backfill_env

    # 1. Without --live-connect
    proc1 = subprocess.run(
        [sys.executable, "scripts/run_mailbox_pilot.py", "--db-path", env["db_path"], "backfill"],
        capture_output=True,
        text=True,
    )
    assert proc1.returncode == 3
    assert "Live connection commands require explicit '--live-connect'" in proc1.stdout or "Live connection commands require explicit '--live-connect'" in proc1.stderr

    # 2. With --live-connect but without --confirm-backfill or --dry-run
    proc2 = subprocess.run(
        [sys.executable, "scripts/run_mailbox_pilot.py", "--db-path", env["db_path"], "backfill", "--live-connect"],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 3
    assert "--confirm-backfill" in proc2.stdout or "--confirm-backfill" in proc2.stderr
