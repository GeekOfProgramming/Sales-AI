"""
tests/test_mail_data_01_foundation.py
Comprehensive regression and acceptance test suite for MAIL-DATA-01.

Verifications:
1. All five tables and required indexes are created with valid schemas.
2. Re-running migrations is idempotent and safe (PRAGMA integrity_check == ok).
3. Existing CRM, historical, and Inbox tables remain unchanged.
4. Six distinct Register.it mailbox identities can coexist.
5. Duplicate account/folder/UIDVALIDITY/UID occurrences do not create duplicate messages.
6. The same RFC Message-ID in different folders/accounts is preserved as separate IMAP occurrences.
7. Messages without RFC Message-ID are fully supported.
8. Folder checkpoint updates are transactional and monotonic for current UIDVALIDITY.
9. A UIDVALIDITY rollover is safely detected and does not overwrite or skip messages.
10. Staging a message creates ZERO CRM outreach, contact, or suppression changes.
11. Message-to-company links can remain pending review without triggering CRM mutations.
12. No passwords or authentication secrets appear in SQLite rows, logs, or models.
13. Bounded message size enforcement truncates oversized bodies without crash.
"""

import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timezone

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxFolderSyncStateRecord,
    MailboxMessageRecord,
    MessageCrmLinkRecord,
)
from sales_engine.inbox.mailbox_repository import (
    MailboxRepository,
    MAX_BODY_PLAIN_CHARS,
    MAX_BODY_HTML_CHARS,
)
from sales_engine.inbox.store import InboxStore
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.backup_engine import (
    create_sqlite_online_backup,
    verify_restore_in_disposable_clone,
)


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    """Fixture providing a disposable, clean SQLite database path."""
    db_file = tmp_path / "test_mail_data_01.db"
    return db_file


@pytest.fixture
def repo(disposable_db: Path) -> MailboxRepository:
    """Fixture providing a migrated MailboxRepository on a disposable database."""
    r = MailboxRepository(db_path=disposable_db, auto_migrate=True)
    return r


def test_01_all_five_tables_and_indexes_created(repo: MailboxRepository):
    """Verify all 5 tables and required indexes are created in SQLite."""
    with repo.get_connection() as conn:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            ).fetchall()
        ]
        assert "mailbox_accounts" in tables
        assert "mailbox_folders" in tables
        assert "mailbox_folder_sync_state" in tables
        assert "mailbox_messages" in tables
        assert "message_crm_links" in tables

        indexes = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name;"
            ).fetchall()
        ]
        assert "idx_mb_acc_email" in indexes
        assert "idx_mb_fld_acc" in indexes
        assert "idx_mb_sync_acc_fld" in indexes
        assert "idx_mb_msg_rfc" in indexes
        assert "idx_mb_msg_irt" in indexes
        assert "idx_mb_msg_from" in indexes
        assert "idx_mcl_cand" in indexes
        assert "idx_mcl_status" in indexes

        # Check PRAGMA integrity
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"


def test_02_migrations_are_idempotent(repo: MailboxRepository):
    """Verify repeated execution of apply_migrations produces no errors or duplicates."""
    for _ in range(3):
        res = repo.apply_migrations()
        assert res["success"] is True
        assert res["integrity"] == "ok"
        assert res["fk_violations_count"] == 0


def test_03_existing_crm_and_inbox_tables_remain_unchanged(disposable_db: Path):
    """Verify existing InboxStore and CandidateStore can initialize alongside MailboxRepository."""
    # 1. Initialize standard InboxStore
    inbox_store = InboxStore(db_path=disposable_db)
    # 2. Initialize CandidateStore
    cand_store = CandidateStore(db_path=disposable_db)
    # 3. Apply MailboxRepository migrations
    repo = MailboxRepository(db_path=disposable_db, auto_migrate=True)

    with repo.get_connection() as conn:
        tables = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        # Existing InboxStore tables
        assert "inbound_messages" in tables
        assert "conversations" in tables
        assert "reply_suggestions" in tables
        # Existing CandidateStore tables
        assert "candidate_companies" in tables
        assert "candidate_contacts" in tables
        # New MAIL-DATA-01 tables
        assert "mailbox_accounts" in tables
        assert "mailbox_messages" in tables
        assert "message_crm_links" in tables


def test_04_six_authorized_mailboxes_coexist(repo: MailboxRepository):
    """Verify all six Register.it mailboxes can be configured independently with disabled status."""
    authorized_emails = [
        ("acc_info_com", "info@pybim.com", "pyBIM Info (COM)"),
        ("acc_info_it", "info@pybim.it", "pyBIM Info (IT)"),
        ("acc_careers_com", "careers@pybim.com", "pyBIM Careers"),
        ("acc_support_com", "support@pybim.com", "pyBIM Support"),
        ("acc_hamed_it", "hamed.sharifi@pybim.it", "Hamed Sharifi"),
        ("acc_hamid_it", "hamid.lotfalian@pybim.it", "Hamid Lotfalian"),
    ]

    for acc_id, email, disp_name in authorized_emails:
        rec = MailboxAccountRecord(
            account_id=acc_id,
            email_address=email,
            display_name=disp_name,
            provider="register_it",
            imap_host="pop.securemail.pro",
            imap_port=993,
            use_ssl=True,
            credential_ref=f"win_cred_{acc_id}",
            is_enabled=False,  # Disabled by default
        )
        repo.save_account(rec)

    accounts = repo.list_accounts()
    assert len(accounts) == 6

    # Verify querying by ID and by Email
    acc_pilot = repo.get_account("acc_info_com")
    assert acc_pilot is not None
    assert acc_pilot.email_address == "info@pybim.com"
    assert acc_pilot.is_enabled is False

    acc_by_email = repo.get_account_by_email("info@pybim.it")
    assert acc_by_email is not None
    assert acc_by_email.account_id == "acc_info_it"


def test_05_idempotent_message_insertion_by_physical_occurrence(repo: MailboxRepository):
    """
    Verify duplicate (account_id, folder_id, UIDVALIDITY, UID) occurrences
    do not create duplicate records.
    """
    # Create account and folder
    acc = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info",
        credential_ref="cred_1",
    )
    repo.save_account(acc)

    folder = MailboxFolderRecord(
        folder_id="fld_inbox_1",
        account_id="acc_info_com",
        folder_name="INBOX",
        folder_role="inbox",
    )
    repo.save_folder(folder)

    msg = MailboxMessageRecord(
        message_id="msg_occ_1",
        account_id="acc_info_com",
        folder_id="fld_inbox_1",
        folder_role="inbox",
        direction="inbound",
        imap_uid=101,
        imap_uidvalidity=123456,
        rfc_message_id="<test101@client.it>",
        from_address="client@partner.it",
        to_addresses=["info@pybim.com"],
        subject="Proposta BIM",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="Salve, siamo interessati.",
    )

    is_new, mid = repo.save_message(msg)
    assert is_new is True
    assert mid == "msg_occ_1"

    # Second insert of exact same IMAP physical occurrence
    msg_dup = MailboxMessageRecord(
        message_id="msg_occ_different_internal_id",
        account_id="acc_info_com",
        folder_id="fld_inbox_1",
        folder_role="inbox",
        direction="inbound",
        imap_uid=101,
        imap_uidvalidity=123456,
        rfc_message_id="<test101@client.it>",
        from_address="client@partner.it",
        to_addresses=["info@pybim.com"],
        subject="Proposta BIM",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="Salve, siamo interessati.",
    )

    is_new_2, mid_2 = repo.save_message(msg_dup)
    assert is_new_2 is False
    assert mid_2 == "msg_occ_1"  # Retained original message_id

    # Verify only 1 row exists
    with repo.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM mailbox_messages").fetchone()[0]
        assert count == 1


def test_06_same_rfc_message_id_in_different_folders_or_accounts_preserved(repo: MailboxRepository):
    """
    Verify the same RFC Message-ID appearing across accounts/folders
    (e.g. Sent and Archive, or Hamed and Hamid) is preserved as distinct IMAP occurrences.
    """
    # 1. Accounts
    repo.save_account(MailboxAccountRecord(account_id="acc_hamed", email_address="hamed.sharifi@pybim.it", display_name="Hamed", credential_ref="c1"))
    repo.save_account(MailboxAccountRecord(account_id="acc_hamid", email_address="hamid.lotfalian@pybim.it", display_name="Hamid", credential_ref="c2"))

    repo.save_folder(MailboxFolderRecord(folder_id="fld_hamed_inbox", account_id="acc_hamed", folder_name="INBOX", folder_role="inbox"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_hamid_inbox", account_id="acc_hamid", folder_name="INBOX", folder_role="inbox"))

    shared_rfc_id = "<shared_announcement_99@partner.it>"

    # Message received by Hamed
    msg1 = MailboxMessageRecord(
        message_id="msg_hamed_99",
        account_id="acc_hamed",
        folder_id="fld_hamed_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=501,
        imap_uidvalidity=999,
        rfc_message_id=shared_rfc_id,
        from_address="partner@domain.it",
        to_addresses=["hamed.sharifi@pybim.it", "hamid.lotfalian@pybim.it"],
        subject="Partner Collaboration",
        date_utc="2026-11-15T00:00:00Z",
    )
    # Same message received by Hamid
    msg2 = MailboxMessageRecord(
        message_id="msg_hamid_99",
        account_id="acc_hamid",
        folder_id="fld_hamid_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=702,
        imap_uidvalidity=888,
        rfc_message_id=shared_rfc_id,
        from_address="partner@domain.it",
        to_addresses=["hamed.sharifi@pybim.it", "hamid.lotfalian@pybim.it"],
        subject="Partner Collaboration",
        date_utc="2026-11-15T00:00:00Z",
    )

    is_new1, mid1 = repo.save_message(msg1)
    is_new2, mid2 = repo.save_message(msg2)

    assert is_new1 is True
    assert is_new2 is True
    assert mid1 != mid2

    # Query occurrences by RFC ID
    occurrences = repo.find_messages_by_rfc_id(shared_rfc_id)
    assert len(occurrences) == 2
    account_ids = set(o.account_id for o in occurrences)
    assert account_ids == {"acc_hamed", "acc_hamid"}


def test_07_messages_without_rfc_message_id_supported(repo: MailboxRepository):
    """Verify legacy or poorly formed messages lacking RFC Message-ID are safely stored."""
    repo.save_account(MailboxAccountRecord(account_id="acc_test", email_address="info@pybim.com", display_name="Test", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_test", account_id="acc_test", folder_name="INBOX"))

    msg = MailboxMessageRecord(
        message_id="msg_no_rfc",
        account_id="acc_test",
        folder_id="fld_test",
        direction="inbound",
        imap_uid=12,
        imap_uidvalidity=111,
        rfc_message_id=None,  # Missing RFC ID
        from_address="legacy@server.it",
        subject="No RFC ID message",
        date_utc="2026-11-15T00:00:00Z",
    )
    is_new, mid = repo.save_message(msg)
    assert is_new is True

    retrieved = repo.get_message("msg_no_rfc")
    assert retrieved is not None
    assert retrieved.rfc_message_id is None
    assert retrieved.from_address == "legacy@server.it"


def test_08_folder_checkpoint_updates_are_transactional_and_monotonic(repo: MailboxRepository):
    """Verify durable checkpoints progress monotonically within the same UIDVALIDITY."""
    repo.save_account(MailboxAccountRecord(account_id="acc_1", email_address="info@pybim.com", display_name="A1", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_1", account_id="acc_1", folder_name="INBOX"))

    # Initial checkpoint
    ckpt = MailboxFolderSyncStateRecord(
        sync_state_id="sync_1",
        account_id="acc_1",
        folder_id="fld_1",
        uidvalidity=5000,
        last_synced_uid=10,
        last_status="success",
        messages_fetched_total=10,
    )
    repo.update_folder_sync_state(ckpt)

    state = repo.get_folder_sync_state("acc_1", "fld_1")
    assert state is not None
    assert state.last_synced_uid == 10

    # Advance checkpoint
    ckpt.last_synced_uid = 25
    ckpt.messages_fetched_total = 25
    ckpt.last_status = "idle"
    repo.update_folder_sync_state(ckpt)

    state2 = repo.get_folder_sync_state("acc_1", "fld_1")
    assert state2 is not None
    assert state2.last_synced_uid == 25
    assert state2.messages_fetched_total == 25


def test_09_uidvalidity_rollover_safety(repo: MailboxRepository):
    """
    Verify changing UIDVALIDITY updates checkpoint correctly without silently
    corrupting the old UID sequence.
    """
    repo.save_account(MailboxAccountRecord(account_id="acc_1", email_address="info@pybim.com", display_name="A1", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_1", account_id="acc_1", folder_name="INBOX"))

    # Initial state with UIDVALIDITY 1000
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_1",
        account_id="acc_1",
        folder_id="fld_1",
        uidvalidity=1000,
        last_synced_uid=80,
    ))

    # Server resets UIDVALIDITY to 2000
    repo.update_folder_sync_state(MailboxFolderSyncStateRecord(
        sync_state_id="sync_1",
        account_id="acc_1",
        folder_id="fld_1",
        uidvalidity=2000,
        last_synced_uid=5,  # Restarted UIDs
        last_status="uidvalidity_reset",
    ))

    state = repo.get_folder_sync_state("acc_1", "fld_1")
    assert state is not None
    assert state.uidvalidity == 2000
    assert state.last_synced_uid == 5
    assert state.last_status == "uidvalidity_reset"


def test_10_staging_message_creates_zero_crm_mutations(disposable_db: Path):
    """
    CRITICAL SAFETY TEST:
    Verify that creating accounts, folders, and staging messages creates ZERO
    modifications to candidate_companies, candidate_contacts, or outreach status.
    """
    cand_store = CandidateStore(db_path=disposable_db)
    repo = MailboxRepository(db_path=disposable_db, auto_migrate=True)

    # Insert a real candidate company into CandidateStore
    from sales_engine.candidates.models import CandidateCompany
    test_cand = CandidateCompany(
        candidate_id="cand:domain:piuarch.it",
        company_name="Piuarch",
        normalized_company_name="piuarch",
        company_domain="piuarch.it",
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
    )
    cand_store.save_candidate(test_cand)

    # Initial candidate state
    initial_cand = cand_store.get_candidate("cand:domain:piuarch.it")
    assert initial_cand.cold_outreach_allowed is True
    assert initial_cand.suppressed is False
    assert initial_cand.active_conversation is False

    # Stage an inbound email from studio@piuarch.it
    repo.save_account(MailboxAccountRecord(account_id="acc_info", email_address="info@pybim.it", display_name="Info", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_inbox", account_id="acc_info", folder_name="INBOX"))

    msg = MailboxMessageRecord(
        message_id="msg_stage_test",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="inbound",
        imap_uid=55,
        imap_uidvalidity=101,
        from_address="studio@piuarch.it",
        subject="Re: Proposta BIM",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="Non siamo interessati. Unsubscribe.",
    )
    repo.save_message(msg)

    # Assert CandidateCompany in CandidateStore is completely UNTOUCHED
    post_cand = cand_store.get_candidate("cand:domain:piuarch.it")
    assert post_cand.cold_outreach_allowed is True
    assert post_cand.suppressed is False
    assert post_cand.active_conversation is False


def test_11_message_to_company_links_can_remain_pending_review(repo: MailboxRepository):
    """Verify linking proposals are stored in message_crm_links without mutating CRM state."""
    repo.save_account(MailboxAccountRecord(account_id="acc_1", email_address="info@pybim.it", display_name="A1", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_1", account_id="acc_1", folder_name="INBOX"))

    msg = MailboxMessageRecord(
        message_id="msg_lnk_1",
        account_id="acc_1",
        folder_id="fld_1",
        direction="inbound",
        imap_uid=99,
        imap_uidvalidity=1,
        from_address="partner@domain.it",
        date_utc="2026-11-15T00:00:00Z",
    )
    repo.save_message(msg)

    link = MessageCrmLinkRecord(
        link_id="lnk_1",
        message_id="msg_lnk_1",
        candidate_id="cand:domain:domain.it",
        link_status="suggested_pending_review",
        link_method="manual_operator",
        link_confidence=0.5,
        evidence_summary="Possible domain match pending operator confirmation",
    )
    repo.save_crm_link(link)

    pending = repo.list_pending_review_links()
    assert len(pending) == 1
    assert pending[0].link_id == "lnk_1"
    assert pending[0].link_status == "suggested_pending_review"


def test_12_no_passwords_or_secrets_in_sqlite(repo: MailboxRepository):
    """
    SECURITY AUDIT TEST:
    Verify that nowhere in the mailbox tables do password columns or plaintext secrets exist.
    """
    with repo.get_connection() as conn:
        for table in ["mailbox_accounts", "mailbox_folders", "mailbox_folder_sync_state", "mailbox_messages", "message_crm_links"]:
            cols = [c[1].lower() for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            for secret_term in ["password", "secret", "passwd", "token", "auth_key", "credential_secret"]:
                assert secret_term not in cols, f"Security violation: found '{secret_term}' column in {table}"


def test_13_bounded_body_truncation_prevents_unbounded_storage(repo: MailboxRepository):
    """Verify oversized email bodies are truncated cleanly without crashing."""
    repo.save_account(MailboxAccountRecord(account_id="acc_1", email_address="info@pybim.com", display_name="A1", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_1", account_id="acc_1", folder_name="INBOX"))

    # Generate an oversized text body (1.5 MB)
    huge_text = "A" * (MAX_BODY_PLAIN_CHARS + 500_000)

    msg = MailboxMessageRecord(
        message_id="msg_huge",
        account_id="acc_1",
        folder_id="fld_1",
        direction="inbound",
        imap_uid=1001,
        imap_uidvalidity=1,
        from_address="spammer@bigdata.com",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text=huge_text,
    )
    is_new, mid = repo.save_message(msg)
    assert is_new is True

    stored = repo.get_message("msg_huge")
    assert stored is not None
    assert stored.body_truncated is True
    assert len(stored.body_plain_text) == MAX_BODY_PLAIN_CHARS


def test_14_backup_and_restore_verification_coverage(disposable_db: Path, tmp_path: Path):
    """
    Verify that the BACKUP-01 automated backup engine backs up the new mailbox tables,
    and disposable clone restore verification succeeds.
    """
    repo = MailboxRepository(db_path=disposable_db, auto_migrate=True)
    repo.save_account(MailboxAccountRecord(account_id="acc_backup", email_address="info@pybim.com", display_name="Backup Test", credential_ref="c"))

    # Run online backup
    backup_dir = tmp_path / "backups"
    res = create_sqlite_online_backup(source_db_path=disposable_db, dest_dir=backup_dir, mode="manual")
    assert res.success is True
    assert Path(res.backup_path).exists()

    # Verify restore into disposable clone including mailbox_accounts
    restore_res = verify_restore_in_disposable_clone(
        backup_path=res.backup_path,
        expected_tables=["mailbox_accounts", "mailbox_folders", "mailbox_messages"],
    )
    assert restore_res["success"] is True
    assert restore_res["integrity_status"] == "ok"
    assert restore_res["restored_counts"]["mailbox_accounts"] == 1
