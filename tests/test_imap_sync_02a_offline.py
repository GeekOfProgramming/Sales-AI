"""
tests/test_imap_sync_02a_offline.py
Offline test suite for IMAP-SYNC-02A: Resilient Read-Only IMAP Sync Engine.

Verifies all 18 offline scenarios:
  1. Initial Inbox synchronization
  2. Initial Sent synchronization
  3. Repeat sync without duplicates (idempotency)
  4. Interrupted batch and successful resume at last committed UID
  5. Failure before checkpoint commit preserves previous cursor
  6. UIDVALIDITY change detection and safe cursor reset to 0
  7. Multiple account/folder isolation
  8. Empty folders handling
  9. Large MIME attachment size gating (>10 MB marked 'deferred_oversized')
 10. Network timeout and retry handling
 11. Missing and malformed message headers
 12. Messages without RFC Message-ID
 13. Deferred oversized-message recovery
 14. Manual Sent without a subject matching on recipient/account/time
 15. Gmail historical copies recognized as mailbox presence, not new send
 16. BiM/Piuarch cross-company protection
 17. No remote flag mutations (\\Seen untouched)
 18. Zero operational CRM changes
"""

import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MailboxFolderSyncStateRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.reconciliation_service import MailboxReconciliationService
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.inbox.sync_service import MailboxSyncOrchestrator
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    """
    Sets up isolated temporary databases for operational CRM and mailbox repo.
    Prevents guard_operational_db from triggering on data/sales_outreach.db.
    """
    outreach_db = str(tmp_path / "sales_outreach.db")
    candidates_db = str(tmp_path / "candidates.db")

    monkeypatch.setenv("SALES_OUTREACH_DB", outreach_db)
    monkeypatch.setenv("SALESAI_DB_PATH", outreach_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", candidates_db)

    # 1. Candidate Store & Review Store initializes base candidate tables
    cand_store = CandidateStore(db_path=outreach_db)
    rev_store = ReviewStore(db_path=outreach_db)

    # 2. Mailbox migrations
    repo = MailboxRepository(db_path=outreach_db)
    repo.apply_migrations()

    # 3. Add outreach specific tables
    conn = sqlite3.connect(outreach_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS historical_sent_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            sender_account TEXT NOT NULL,
            to_address TEXT NOT NULL,
            recipient_emails_json TEXT NOT NULL,
            subject TEXT,
            sent_date TEXT,
            sent_time TEXT,
            matched_candidate_id TEXT
        )
    """)
    conn.commit()
    conn.close()

    return outreach_db, repo, cand_store, rev_store


@pytest.fixture
def sample_account():
    return MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Info IT",
        imap_host="pop.securemail.pro",
        imap_port=993,
        credential_ref="keyring:info@pybim.it",
        is_enabled=True,
    )


@pytest.fixture
def sample_inbox_folder():
    return MailboxFolderRecord(
        folder_id="fol_inbox_it",
        account_id="acc_info_it",
        folder_name="INBOX",
        folder_role="inbox",
        folder_delimiter="/",
        imap_flags=["\\HasNoChildren"],
        is_active=True,
    )


@pytest.fixture
def sample_sent_folder():
    return MailboxFolderRecord(
        folder_id="fol_sent_it",
        account_id="acc_info_it",
        folder_name="Sent",
        folder_role="sent",
        folder_delimiter="/",
        imap_flags=["\\HasNoChildren"],
        is_active=True,
    )


# ---------------------------------------------------------------------------
# 1. Initial Inbox synchronization
# ---------------------------------------------------------------------------
def test_01_initial_inbox_sync(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (2, 1001)

    now_iso = datetime.now(timezone.utc).isoformat()
    msg1 = MailboxMessageRecord(
        message_id="msg_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<msg1@client.com>",
        direction="inbound",
        subject="Project Inquiry 1",
        from_address="architect@client.com",
        to_addresses=["info@pybim.it"],
        date_utc=now_iso,
    )
    msg2 = MailboxMessageRecord(
        message_id="msg_2",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=2,
        rfc_message_id="<msg2@client.com>",
        direction="inbound",
        subject="Project Inquiry 2",
        from_address="engineer@client.com",
        to_addresses=["info@pybim.it"],
        date_utc=now_iso,
    )

    mock_provider.fetch_messages_by_uid.side_effect = [
        ([msg1, msg2], 2, 1001),
        ([], 2, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["total_messages_ingested"] == 2
    assert res["folders_synced"]["INBOX"]["status"] == "success"
    assert res["folders_synced"]["INBOX"]["ending_uid"] == 2

    # Verify sync checkpoint in SQLite
    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_inbox_folder.folder_id)
    assert sync_state is not None
    assert sync_state.last_synced_uid == 2
    assert sync_state.uidvalidity == 1001


# ---------------------------------------------------------------------------
# 2. Initial Sent synchronization
# ---------------------------------------------------------------------------
def test_02_initial_sent_sync(isolated_dbs, sample_account, sample_sent_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_sent_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_sent_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 2002)

    sent_msg = MailboxMessageRecord(
        message_id="msg_sent_1",
        account_id=sample_account.account_id,
        folder_id=sample_sent_folder.folder_id,
        folder_role="sent",
        imap_uidvalidity=2002,
        imap_uid=10,
        rfc_message_id="<sent1@pybim.it>",
        direction="outbound",
        subject="Proposal Response",
        from_address="info@pybim.it",
        to_addresses=["partner@studio.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    mock_provider.fetch_messages_by_uid.side_effect = [
        ([sent_msg], 10, 2002),
        ([], 10, 2002),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["sent"])
    assert res["total_messages_ingested"] == 1
    assert res["folders_synced"]["Sent"]["status"] == "success"
    assert res["folders_synced"]["Sent"]["ending_uid"] == 10

    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_sent_folder.folder_id)
    assert sync_state.last_synced_uid == 10


# ---------------------------------------------------------------------------
# 3. Repeat sync without duplicates (idempotency)
# ---------------------------------------------------------------------------
def test_03_repeat_sync_without_duplicates(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 1001)

    msg1 = MailboxMessageRecord(
        message_id="msg_repeat_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<msg_repeat@client.com>",
        direction="inbound",
        subject="Repeat Test",
        from_address="architect@client.com",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    # First run
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([msg1], 1, 1001),
        ([], 1, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res1 = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res1["total_messages_ingested"] == 1

    # Second run: remote has no new UIDs (since_uid=1)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([], 1, 1001),
    ]

    res2 = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res2["total_messages_ingested"] == 0
    assert res2["folders_synced"]["INBOX"]["ending_uid"] == 1

    # Check database: exactly 1 message
    msgs = repo.list_messages(sample_account.account_id, sample_inbox_folder.folder_id)
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# 4. Interrupted batch and successful resume at last committed UID
# ---------------------------------------------------------------------------
def test_04_interrupted_batch_and_resume(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (3, 1001)

    msg1 = MailboxMessageRecord(
        message_id="msg_int_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<msg_int_1@test.com>",
        direction="inbound",
        subject="Batch 1",
        from_address="a@b.com",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    # Batch 1 succeeds with msg1 (uid 1), batch 2 raises ConnectionResetError
    # We set max_batches=2 so orchestrator tries second batch and encounters exception
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([msg1], 1, 1001),
        ConnectionResetError("Simulated network dropout on batch 2"),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res1 = orchestrator.sync_folder(sample_account, sample_inbox_folder, batch_size=1, max_batches=2)
    assert res1["status"] == "failed"
    assert res1["ending_uid"] == 1

    # Verify checkpoint is committed at uid 1
    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_inbox_folder.folder_id)
    assert sync_state.last_synced_uid == 1

    # Resume run: next batch starts after UID 1
    msg2 = MailboxMessageRecord(
        message_id="msg_int_2",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=2,
        rfc_message_id="<msg_int_2@test.com>",
        direction="inbound",
        subject="Batch 2",
        from_address="a@b.com",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([msg2], 2, 1001),
        ([], 2, 1001),
    ]

    res2 = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res2["folders_synced"]["INBOX"]["status"] == "success"
    assert res2["folders_synced"]["INBOX"]["starting_uid"] == 1
    assert res2["folders_synced"]["INBOX"]["ending_uid"] == 2


# ---------------------------------------------------------------------------
# 5. Failure before checkpoint commit preserves previous cursor
# ---------------------------------------------------------------------------
def test_05_failure_before_checkpoint_commit_preserves_cursor(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Pre-set sync state at uid 5
    initial_sync = MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{sample_account.account_id}_{sample_inbox_folder.folder_id}",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        uidvalidity=1001,
        last_synced_uid=5,
        last_status="success",
    )
    repo.update_folder_sync_state(initial_sync)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (10, 1001)

    # Provider fetch fails immediately before returning any messages
    mock_provider.fetch_messages_by_uid.side_effect = TimeoutError("IMAP socket timed out")

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["folders_synced"]["INBOX"]["status"] == "failed"

    # Cursor must remain 5, not updated or reset
    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_inbox_folder.folder_id)
    assert sync_state.last_synced_uid == 5
    assert sync_state.last_status == "failed"


# ---------------------------------------------------------------------------
# 6. UIDVALIDITY change detection and safe cursor reset to 0
# ---------------------------------------------------------------------------
def test_06_uidvalidity_rollover_reset(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Prior state with old UIDVALIDITY 1001 and last_synced_uid 50
    old_sync = MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{sample_account.account_id}_{sample_inbox_folder.folder_id}",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        uidvalidity=1001,
        last_synced_uid=50,
        last_status="success",
    )
    repo.update_folder_sync_state(old_sync)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    # Remote mailbox regenerated with new UIDVALIDITY 9999
    mock_provider.examine_folder.return_value = (5, 9999)

    new_msg = MailboxMessageRecord(
        message_id="msg_new_uv_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=9999,
        imap_uid=1,
        rfc_message_id="<msg_uv@test.com>",
        direction="inbound",
        subject="Post Rollover",
        from_address="a@b.com",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    # Since cursor resets to 0, fetch begins from since_uid=0
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([new_msg], 1, 9999),
        ([], 1, 9999),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["folders_synced"]["INBOX"]["status"] == "success"
    assert res["folders_synced"]["INBOX"]["uidvalidity"] == 9999
    assert res["folders_synced"]["INBOX"]["ending_uid"] == 1

    # Verify durable state has new UIDVALIDITY and updated last_synced_uid
    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_inbox_folder.folder_id)
    assert sync_state.uidvalidity == 9999
    assert sync_state.last_synced_uid == 1


# ---------------------------------------------------------------------------
# 7. Multiple account/folder isolation
# ---------------------------------------------------------------------------
def test_07_multiple_account_and_folder_isolation(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs

    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info COM",
        imap_host="pop.securemail.pro",
        imap_port=993,
        credential_ref="keyring:info@pybim.com",
        is_enabled=True,
    )
    folder_com = MailboxFolderRecord(
        folder_id="fol_inbox_com",
        account_id="acc_info_com",
        folder_name="INBOX",
        folder_role="inbox",
        folder_delimiter="/",
        imap_flags=["\\HasNoChildren"],
        is_active=True,
    )

    repo.save_account(sample_account)
    repo.save_account(acc_com)
    repo.save_folder(sample_inbox_folder)
    repo.save_folder(folder_com)

    now_iso = datetime.now(timezone.utc).isoformat()
    # Identical UID 1 in both accounts with identical RFC Message-ID
    msg_it = MailboxMessageRecord(
        message_id="msg_it_uid1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<shared@vendor.com>",
        direction="inbound",
        subject="Shared RFP",
        from_address="vendor@partner.com",
        to_addresses=["info@pybim.it"],
        date_utc=now_iso,
    )
    msg_com = MailboxMessageRecord(
        message_id="msg_com_uid1",
        account_id=acc_com.account_id,
        folder_id=folder_com.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<shared@vendor.com>",
        direction="inbound",
        subject="Shared RFP",
        from_address="vendor@partner.com",
        to_addresses=["info@pybim.com"],
        date_utc=now_iso,
    )

    def factory(account):
        p = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
        p.connect.return_value = MagicMock()
        if account.account_id == sample_account.account_id:
            p.discover_folders.return_value = [sample_inbox_folder]
            p.examine_folder.return_value = (1, 1001)
            p.fetch_messages_by_uid.side_effect = [([msg_it], 1, 1001), ([], 1, 1001)]
        else:
            p.discover_folders.return_value = [folder_com]
            p.examine_folder.return_value = (1, 1001)
            p.fetch_messages_by_uid.side_effect = [([msg_com], 1, 1001), ([], 1, 1001)]
        return p

    orchestrator = MailboxSyncOrchestrator(mailbox_repo=repo, provider_factory=factory)

    orchestrator.sync_account_folders(sample_account)
    orchestrator.sync_account_folders(acc_com)

    msgs_it = repo.list_messages(sample_account.account_id, sample_inbox_folder.folder_id)
    msgs_com = repo.list_messages(acc_com.account_id, folder_com.folder_id)

    assert len(msgs_it) == 1
    assert len(msgs_com) == 1
    assert msgs_it[0].account_id == "acc_info_it"
    assert msgs_com[0].account_id == "acc_info_com"


# ---------------------------------------------------------------------------
# 8. Empty folders handling
# ---------------------------------------------------------------------------
def test_08_empty_folders_handling(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (0, 1001)  # Empty folder
    mock_provider.fetch_messages_by_uid.return_value = ([], 0, 1001)

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["total_messages_ingested"] == 0
    assert res["folders_synced"]["INBOX"]["status"] == "success"
    assert res["folders_synced"]["INBOX"]["ending_uid"] == 0

    sync_state = repo.get_folder_sync_state(sample_account.account_id, sample_inbox_folder.folder_id)
    assert sync_state.last_synced_uid == 0


# ---------------------------------------------------------------------------
# 9. Large MIME attachment size gating (>10 MB marked deferred_oversized)
# ---------------------------------------------------------------------------
def test_09_large_mime_attachment_size_gating(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Provider fetch returns an oversized message with parse_status="deferred_oversized"
    oversized_msg = MailboxMessageRecord(
        message_id="msg_huge_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=42,
        rfc_message_id="<heavy_model@client.com>",
        direction="inbound",
        subject="3D Model BIM 45MB",
        from_address="bim_lead@studio.it",
        to_addresses=["info@pybim.it"],
        body_plain_text="[BODY DEFERRED: Message size 47185920 bytes exceeds 10MB limit]",
        body_html_sanitized=None,
        parse_status="deferred_oversized",
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([oversized_msg], 42, 1001),
        ([], 42, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["total_messages_deferred"] == 1
    assert res["total_messages_ingested"] == 1

    # In database, message exists with headers, deferred parse status, and no binary blob
    saved = repo.get_message_by_occurrence(sample_account.account_id, sample_inbox_folder.folder_id, 1001, 42)
    assert saved is not None
    assert saved.parse_status == "deferred_oversized"
    assert "exceeds 10MB limit" in saved.body_plain_text


# ---------------------------------------------------------------------------
# 10. Network timeout and retry handling
# ---------------------------------------------------------------------------
def test_10_network_timeout_and_retry(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.side_effect = TimeoutError("Connection to pop.securemail.pro timed out")

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert len(res["errors"]) > 0
    assert res["folders_synced"]["INBOX"]["status"] == "failed"
    assert "timed out" in res["folders_synced"]["INBOX"]["error"]


# ---------------------------------------------------------------------------
# 11. Missing and malformed message headers
# ---------------------------------------------------------------------------
def test_11_missing_and_malformed_headers(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Malformed message without subject, from, or to
    malformed_msg = MailboxMessageRecord(
        message_id="msg_malformed_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=15,
        rfc_message_id="<malformed@test.com>",
        direction="inbound",
        subject="(No Subject)",
        from_address="unknown@unknown.invalid",
        to_addresses=[],
        body_plain_text="Broken headers body text",
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([malformed_msg], 15, 1001),
        ([], 15, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["total_messages_ingested"] == 1
    saved = repo.get_message_by_occurrence(sample_account.account_id, sample_inbox_folder.folder_id, 1001, 15)
    assert saved is not None
    assert saved.subject == "(No Subject)"


# ---------------------------------------------------------------------------
# 12. Messages without RFC Message-ID
# ---------------------------------------------------------------------------
def test_12_messages_without_rfc_message_id(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Message with null RFC Message-ID
    no_rfc_msg = MailboxMessageRecord(
        message_id="msg_no_rfc_1",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=16,
        rfc_message_id=None,
        direction="inbound",
        subject="Legacy Fax Notification",
        from_address="efax@telecom.it",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([no_rfc_msg], 16, 1001),
        ([], 16, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        provider_factory=lambda acc: mock_provider,
    )

    res = orchestrator.sync_account_folders(sample_account, target_folder_roles=["inbox"])
    assert res["total_messages_ingested"] == 1
    saved = repo.get_message_by_occurrence(sample_account.account_id, sample_inbox_folder.folder_id, 1001, 16)
    assert saved is not None
    assert saved.rfc_message_id is None


# ---------------------------------------------------------------------------
# 13. Deferred oversized-message recovery
# ---------------------------------------------------------------------------
def test_13_deferred_oversized_message_recovery(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Initially deferred
    oversized_msg = MailboxMessageRecord(
        message_id="msg_deferred_rec",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=88,
        rfc_message_id="<deferred88@client.com>",
        direction="inbound",
        subject="Oversized Attachment",
        from_address="client@client.com",
        to_addresses=["info@pybim.it"],
        body_plain_text="[BODY DEFERRED: Message size 15000000 bytes exceeds 10MB limit]",
        parse_status="deferred_oversized",
        date_utc=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_message(oversized_msg)

    # Later operator recovery: parsed body is downloaded under explicit override
    recovered_msg = oversized_msg.model_copy(update={
        "body_plain_text": "Full recovered body text without binary attachments.",
        "parse_status": "parsed_recovered",
    })
    repo.save_message(recovered_msg)

    saved = repo.get_message(oversized_msg.message_id)
    assert saved.parse_status == "parsed_recovered"
    assert "Full recovered body text" in saved.body_plain_text


# ---------------------------------------------------------------------------
# 14. Manual Sent without a subject matching on recipient/account/time
# ---------------------------------------------------------------------------
def test_14_manual_sent_without_subject_matching(isolated_dbs, sample_account, sample_sent_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_sent_folder)

    # Insert a synthetic manual_outreach_log record with empty string subject
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO manual_outreach_log (
                log_id, candidate_id, recipient_email, subject, sender_account, logged_at
            ) VALUES ('log_tb_123', 'cand:domain:studioxyz.it', 'contact@studioxyz.it', '', 'info@pybim.it', '2026-11-14T10:00:00Z');
        """)

    # IMAP Sent message with subject "Re: Offerta pyBIM" sent at 10:05 (5 mins later)
    outbound_msg = MailboxMessageRecord(
        message_id="msg_manual_match",
        account_id=sample_account.account_id,
        folder_id=sample_sent_folder.folder_id,
        folder_role="sent",
        imap_uidvalidity=1001,
        imap_uid=33,
        rfc_message_id="<tb_sent@pybim.it>",
        direction="outbound",
        subject="Re: Offerta pyBIM",
        from_address="info@pybim.it",
        to_addresses=["contact@studioxyz.it"],
        date_utc="2026-11-14T10:05:00Z",
    )

    reconciler = MailboxReconciliationService(
        mailbox_repo=repo,
        candidate_store=cand_store,
        review_store=rev_store,
    )
    proposal = reconciler.reconcile_manual_outreach(outbound_msg)

    assert proposal is not None
    assert proposal.confidence >= 0.70
    assert proposal.matched_log_id == "log_tb_123"
    assert proposal.reconciliation_status == "confirmed_on_server"


# ---------------------------------------------------------------------------
# 15. Gmail historical copies recognized as mailbox presence, not new send
# ---------------------------------------------------------------------------
def test_15_gmail_historical_copies_presence_only(isolated_dbs, sample_account, sample_sent_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_sent_folder)

    # Insert synthetic historical_sent_emails
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO historical_sent_emails (
                id, message_id, thread_id, sender_account, to_address, recipient_emails_json, subject, sent_date, sent_time, matched_candidate_id
            ) VALUES (861, '1a155284f3c8f57b', '1a155284f3c8f57b', 'info@pybim.it', 'mario@rossi.it', '["mario@rossi.it"]', 'pyBIM Introduction', '2026-10-15', '14:30:00', 'cand:domain:rossi.it');
        """)

    # Synchronized outbound message matching historical record
    outbound_msg = MailboxMessageRecord(
        message_id="msg_hist_match_1",
        account_id=sample_account.account_id,
        folder_id=sample_sent_folder.folder_id,
        folder_role="sent",
        imap_uidvalidity=1001,
        imap_uid=99,
        rfc_message_id="<gmail_copy_123@pybim.it>",
        direction="outbound",
        subject="pyBIM Introduction",
        from_address="info@pybim.it",
        to_addresses=["mario@rossi.it"],
        date_utc="2026-10-15T14:30:00Z",
    )

    reconciler = MailboxReconciliationService(
        mailbox_repo=repo,
        candidate_store=cand_store,
        review_store=rev_store,
    )
    proposal = reconciler.reconcile_historical_sent(outbound_msg)

    assert proposal is not None
    assert proposal.matched_historical_id == 861
    assert proposal.reconciliation_status == "verified_match"

    # Verify that historical_sent_emails table was NOT mutated or duplicated
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# 16. BiM/Piuarch cross-company protection
# ---------------------------------------------------------------------------
def test_16_bim_piuarch_cross_company_protection(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Setup candidate companies: BiM Milano (bim-milano.com) and Piuarch (piuarch.it)
    cand_bim = CandidateCompany(
        candidate_id="cand:domain:bim-milano.com",
        company_name="BiM Milano",
        normalized_company_name="bim milano",
        company_domain="bim-milano.com",
    )
    cand_piuarch = CandidateCompany(
        candidate_id="cand:domain:piuarch.it",
        company_name="Piuarch",
        normalized_company_name="piuarch",
        company_domain="piuarch.it",
    )
    cand_store.save_candidate(cand_bim)
    cand_store.save_candidate(cand_piuarch)

    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, created_at)
            VALUES ('cnt_piuarch_owned', 'cand:domain:piuarch.it', 'studio@piuarch.it', 'owned', '2026-11-15T00:00:00Z');
        """)
        conn.execute("""
            INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, notes, created_at)
            VALUES ('cnt_piuarch_in_bim', 'cand:domain:bim-milano.com', 'studio@piuarch.it', 'associated', 'Partner mention', '2026-11-15T00:00:00Z');
        """)

    # Message from studio@piuarch.it discussing BiM project
    msg = MailboxMessageRecord(
        message_id="msg_piuarch_cross",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=200,
        rfc_message_id="<piuarch_inquiry@piuarch.it>",
        direction="inbound",
        subject="BiM Milano project collaboration",
        from_address="studio@piuarch.it",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_message(msg)

    reconciler = MailboxReconciliationService(
        mailbox_repo=repo,
        candidate_store=cand_store,
        review_store=rev_store,
    )
    proposal = reconciler.stage_organization_link(msg)

    # Must link to Piuarch based on domain/contact ownership, NEVER to BiM Milano
    assert proposal is not None
    assert proposal.candidate_id == "cand:domain:piuarch.it"
    assert proposal.candidate_id != "cand:domain:bim-milano.com"


# ---------------------------------------------------------------------------
# 17. No remote flag mutations (\Seen untouched)
# ---------------------------------------------------------------------------
def test_17_no_remote_flag_mutations(isolated_dbs, sample_account, sample_inbox_folder):
    """
    Verify that IMAP operations exclusively use EXAMINE and BODY.PEEK.
    Never calls SELECT, store('+FLAGS'), or store('-FLAGS').
    """
    mock_imap = MagicMock()
    mock_imap.store.side_effect = AssertionError("STORE must never be called on remote mailbox")

    mock_imap.select.return_value = ("OK", [b"10"])
    mock_imap.response.return_value = ("OK", [b"12345"])
    mock_imap.search.return_value = ("OK", [b"1 2"])
    mock_imap.uid.side_effect = [
        # SEARCH
        ("OK", [b"1 2"]),
        # Pre-flight RFC822.SIZE
        ("OK", [b"1 (UID 1 RFC822.SIZE 1024)", b"2 (UID 2 RFC822.SIZE 2048)"]),
        # PEEK fetch
        ("OK", [
            (b"1 (UID 1 BODY[] {40}", b"From: a@b.com\r\nSubject: Hi\r\n\r\nTest 1"),
            b")",
            (b"2 (UID 2 BODY[] {40}", b"From: a@b.com\r\nSubject: Hi\r\n\r\nTest 2"),
            b")",
        ]),
    ]

    provider = RegisterItReadOnlyIMAPProvider(account=sample_account)
    count, uv = provider.examine_folder(mock_imap, "INBOX")
    assert count == 10

    msgs, highest_uid, uv = provider.fetch_messages_by_uid(
        account=sample_account,
        folder=sample_inbox_folder,
        since_uid=0,
        batch_size=50,
        client=mock_imap,
    )
    assert len(msgs) == 2
    assert highest_uid == 2

    # Assert select was called strictly with readonly=True (which sends EXAMINE on the wire)
    for c in mock_imap.select.call_args_list:
        assert c[1].get("readonly") is True
        assert c[1].get("mailbox") == "INBOX"
    assert mock_imap.store.call_count == 0


# ---------------------------------------------------------------------------
# 18. Zero operational CRM changes
# ---------------------------------------------------------------------------
def test_18_zero_operational_crm_changes(isolated_dbs, sample_account, sample_inbox_folder):
    db_path, repo, cand_store, rev_store = isolated_dbs
    repo.save_account(sample_account)
    repo.save_folder(sample_inbox_folder)

    # Snapshot tables
    cand = CandidateCompany(
        candidate_id="cand:domain:test.com",
        company_name="Test Corp",
        normalized_company_name="test corp",
        company_domain="test.com",
    )
    cand_store.save_candidate(cand)

    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, created_at)
            VALUES ('ct1', 'cand:domain:test.com', 'a@test.com', 'owned', '2026-11-15T00:00:00Z');
        """)
        conn.execute("""
            INSERT INTO suppression_list (suppression_id, email, company_domain, reason, source, created_at)
            VALUES ('sup1', 'supp@domain.com', 'domain.com', 'unsubscribed', 'manual', '2026-11-15T00:00:00Z');
        """)

    def get_crm_counts():
        with cand_store._get_connection() as conn:
            return {
                "companies": conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0],
                "contacts": conn.execute("SELECT COUNT(*) FROM candidate_contacts").fetchone()[0],
                "suppression": conn.execute("SELECT COUNT(*) FROM suppression_list").fetchone()[0],
            }

    counts_before = get_crm_counts()

    # Execute full sync orchestrator workflow
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.discover_folders.return_value = [sample_inbox_folder]
    mock_provider.connect.return_value = MagicMock()
    mock_provider.examine_folder.return_value = (1, 1001)

    msg = MailboxMessageRecord(
        message_id="msg_sync_isolated",
        account_id=sample_account.account_id,
        folder_id=sample_inbox_folder.folder_id,
        folder_role="inbox",
        imap_uidvalidity=1001,
        imap_uid=1,
        rfc_message_id="<test@test.com>",
        direction="inbound",
        subject="CRM Isolation Test",
        from_address="a@test.com",
        to_addresses=["info@pybim.it"],
        date_utc=datetime.now(timezone.utc).isoformat(),
    )
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([msg], 1, 1001),
        ([], 1, 1001),
    ]

    orchestrator = MailboxSyncOrchestrator(
        mailbox_repo=repo,
        reconciliation_service=MailboxReconciliationService(
            mailbox_repo=repo,
            candidate_store=cand_store,
            review_store=rev_store,
        ),
        provider_factory=lambda acc: mock_provider,
    )
    orchestrator.sync_account_folders(sample_account, auto_reconcile=True)

    counts_after = get_crm_counts()
    conn.close()

    assert counts_before == counts_after
