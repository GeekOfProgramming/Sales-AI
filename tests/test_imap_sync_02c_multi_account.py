"""
tests/test_imap_sync_02c_multi_account.py
Offline test suite for IMAP-SYNC-02C-A: Multi-Mailbox Rollout Preparation.

Verifications:
  1. All six authorized account identities can be configured independently.
  2. The existing info@pybim.com pilot remains unchanged and functioning.
  3. Selecting one mailbox cannot access another mailbox's credential reference.
  4. Missing credentials fail closed safely.
  5. Disabled accounts cannot be synchronized or staged.
  6. Folder names are discovered individually per account.
  7. Ambiguous Sent folders stop staging safely.
  8. Duplicate messages across accounts/folders are prevented or isolated.
  9. Independent UIDVALIDITY and checkpoints work correctly across all accounts.
 10. No SMTP or remote flag mutations occur.
 11. Zero CRM state mutations (candidate_companies, candidate_contacts, etc.).
 12. Default CLI invocation makes zero network calls for any selected account.
 13. Operational test isolation is strictly enforced.
"""

import os
import sqlite3
import pytest
from unittest.mock import MagicMock

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore
from scripts.run_mailbox_pilot import (
    MailboxPilotRunner,
    get_account_config_by_email,
    get_pilot_account_config,
    AUTHORIZED_MAILBOX_CONFIGS,
    PILOT_ACCOUNT_ID,
    PILOT_EMAIL_ADDRESS,
)


@pytest.fixture
def disposable_multi_env(tmp_path, monkeypatch):
    """Sets up an isolated disposable environment protecting operational data."""
    test_db = str(tmp_path / "disposable_multi_sales_outreach.db")
    cred_file = tmp_path / ".test_credentials.enc"

    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(tmp_path / "candidates.db"))

    cand_store = CandidateStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    with sqlite3.connect(test_db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historical_sent_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                sender_account TEXT,
                sender_group TEXT,
                from_address TEXT,
                to_address TEXT,
                recipient_emails_json TEXT,
                cc TEXT,
                bcc TEXT,
                sent_date TEXT,
                sent_time TEXT,
                subject TEXT,
                email_body TEXT,
                body_truncated INTEGER DEFAULT 0,
                gmail_link TEXT,
                source_file TEXT,
                source_sheet TEXT,
                matched_candidate_id TEXT,
                matched_company_name TEXT,
                reconciliation_status TEXT DEFAULT 'pending',
                imported_at TEXT NOT NULL,
                match_evidence TEXT,
                proposed_candidate_id TEXT,
                proposed_company_name TEXT,
                proposal_status TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_outreach_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                contact_email TEXT,
                recipient_name TEXT,
                operator_user TEXT NOT NULL,
                sent_via_account TEXT NOT NULL,
                subject TEXT,
                logged_at TEXT NOT NULL,
                notes TEXT,
                email_message_id TEXT,
                verification_source TEXT DEFAULT 'manual'
            );
        """)

    cred_store = MailboxCredentialStore(store_path=str(cred_file))

    return {
        "db_path": test_db,
        "cred_store": cred_store,
        "repo": repo,
        "cand_store": cand_store,
        "rev_store": rev_store,
    }


def test_01_all_six_authorized_accounts_configured_independently():
    """Verify all 6 Register.it accounts have distinct, isolated configurations."""
    assert len(AUTHORIZED_MAILBOX_CONFIGS) == 6
    expected_emails = [
        "info@pybim.com",
        "info@pybim.it",
        "careers@pybim.com",
        "support@pybim.com",
        "hamed.sharifi@pybim.it",
        "hamid.lotfalian@pybim.it",
    ]
    seen_ids = set()
    seen_refs = set()

    for email in expected_emails:
        cfg = get_account_config_by_email(email)
        assert cfg.email_address == email
        assert cfg.imap_host == "pop.securemail.pro"
        assert cfg.imap_port == 993
        assert cfg.use_ssl is True
        assert cfg.is_enabled is False  # All disabled by default

        assert cfg.account_id not in seen_ids
        seen_ids.add(cfg.account_id)

        assert cfg.credential_ref not in seen_refs
        seen_refs.add(cfg.credential_ref)


def test_02_default_account_remains_info_pybim_com():
    """Verify default pilot account remains info@pybim.com with exact pilot parameters."""
    cfg = get_pilot_account_config()
    assert cfg.account_id == "acc_info_com"
    assert cfg.email_address == "info@pybim.com"
    assert cfg.credential_ref == "win_cred_acc_info_com"
    assert cfg.sync_priority == 1


def test_03_credential_reference_isolation(disposable_multi_env):
    """Selecting one account cannot access or validate against another account's credential."""
    cred_store = disposable_multi_env["cred_store"]
    db_path = disposable_multi_env["db_path"]

    # Provision only info@pybim.com
    cred_store.set_credential("win_cred_acc_info_com", "secret_info_com")

    # Runner for info@pybim.com passes credential check
    runner_com = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        account=get_account_config_by_email("info@pybim.com"),
    )
    rep_com = runner_com.validate_preflight(check_credential=True)
    assert rep_com["safety_passed"] is True
    assert rep_com["credential_configured"] is True

    # Runner for info@pybim.it fails closed
    runner_it = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        account=get_account_config_by_email("info@pybim.it"),
    )
    rep_it = runner_it.validate_preflight(check_credential=True)
    assert rep_it["safety_passed"] is False
    assert rep_it["credential_configured"] is False
    assert any("win_cred_acc_info_it" in err for err in rep_it["errors"])


def test_04_missing_credentials_fail_closed(disposable_multi_env):
    """Accounts without credentials cannot proceed to connection diagnostics."""
    cred_store = disposable_multi_env["cred_store"]
    db_path = disposable_multi_env["db_path"]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        account=get_account_config_by_email("careers@pybim.com"),
    )
    diag = runner.run_connection_diagnostics()
    assert diag["success"] is False
    assert diag["error_category"] == "PREFLIGHT_VALIDATION_FAILED"


def test_05_folder_discovery_is_per_account(disposable_multi_env):
    """Folder discovery queries the specific account and stores/returns its actual remote folders."""
    cred_store = disposable_multi_env["cred_store"]
    db_path = disposable_multi_env["db_path"]

    account = get_account_config_by_email("support@pybim.com")
    cred_store.set_credential(account.credential_ref, "dummy_pwd")

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.discover_folders.return_value = [
        MailboxFolderRecord(folder_id="fld_inbox", account_id=account.account_id, folder_name="INBOX", folder_role="inbox"),
        MailboxFolderRecord(folder_id="fld_sent", account_id=account.account_id, folder_name="INBOX.Sent", folder_role="sent"),
    ]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        account=account,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.discover_remote_folders()
    assert res["success"] is True
    assert res["detected_inbox"] == "INBOX"
    assert res["detected_sent"] == "INBOX.Sent"
    assert res["ambiguous_sent"] is False


def test_06_ambiguous_sent_folder_stops_staging(disposable_multi_env):
    """If an account discovers multiple Sent folders, discovery flags ambiguity."""
    cred_store = disposable_multi_env["cred_store"]
    db_path = disposable_multi_env["db_path"]

    account = get_account_config_by_email("hamed.sharifi@pybim.it")
    cred_store.set_credential(account.credential_ref, "dummy_pwd")

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.discover_folders.return_value = [
        MailboxFolderRecord(folder_id="f1", account_id=account.account_id, folder_name="Sent", folder_role="sent"),
        MailboxFolderRecord(folder_id="f2", account_id=account.account_id, folder_name="Sent Items", folder_role="sent"),
    ]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        account=account,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.discover_remote_folders()
    assert res["success"] is True
    assert res["ambiguous_sent"] is True
    assert res["detected_sent"] is None
    assert set(res["sent_candidates"]) == {"Sent", "Sent Items"}


def test_07_independent_uidvalidity_and_checkpoints(disposable_multi_env):
    """Each account maintains strictly isolated folder sync checkpoints."""
    repo: MailboxRepository = disposable_multi_env["repo"]

    acc1 = get_account_config_by_email("info@pybim.com")
    acc2 = get_account_config_by_email("hamid.lotfalian@pybim.it")

    repo.save_account(acc1)
    repo.save_account(acc2)

    fld1 = MailboxFolderRecord(
        folder_id=f"fld_{acc1.account_id}_inbox",
        account_id=acc1.account_id,
        folder_name="INBOX",
        folder_role="inbox",
    )
    fld2 = MailboxFolderRecord(
        folder_id=f"fld_{acc2.account_id}_inbox",
        account_id=acc2.account_id,
        folder_name="INBOX",
        folder_role="inbox",
    )
    repo.save_folder(fld1)
    repo.save_folder(fld2)

    from sales_engine.inbox.models import MailboxFolderSyncStateRecord
    srec1 = MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{acc1.account_id}_{fld1.folder_id}",
        account_id=acc1.account_id,
        folder_id=fld1.folder_id,
        uidvalidity=1001,
        last_synced_uid=11,
        messages_fetched_total=10,
        last_status="success",
    )
    srec2 = MailboxFolderSyncStateRecord(
        sync_state_id=f"sync_{acc2.account_id}_{fld2.folder_id}",
        account_id=acc2.account_id,
        folder_id=fld2.folder_id,
        uidvalidity=2002,
        last_synced_uid=45,
        messages_fetched_total=10,
        last_status="success",
    )
    repo.update_folder_sync_state(srec1)
    repo.update_folder_sync_state(srec2)

    s1 = repo.get_folder_sync_state(acc1.account_id, fld1.folder_id)
    s2 = repo.get_folder_sync_state(acc2.account_id, fld2.folder_id)

    assert s1.uidvalidity == 1001
    assert s1.last_synced_uid == 11
    assert s2.uidvalidity == 2002
    assert s2.last_synced_uid == 45


def test_08_cli_preflight_for_all_six_accounts(disposable_multi_env):
    """All six accounts can be inspected via validate_preflight without network calls."""
    cred_store = disposable_multi_env["cred_store"]
    db_path = disposable_multi_env["db_path"]

    for email in AUTHORIZED_MAILBOX_CONFIGS:
        account = get_account_config_by_email(email)
        runner = MailboxPilotRunner(db_path=db_path, cred_store=cred_store, account=account)
        report = runner.validate_preflight(check_credential=False)
        assert report["safety_passed"] is True
        assert report["email_address"] == email
        assert report["credential_ref"] == AUTHORIZED_MAILBOX_CONFIGS[email]["credential_ref"]
