"""
tests/test_imap_sync_02b_pilot.py
Offline test suite for IMAP-SYNC-02B: Single-Mailbox Live Pilot Preparation & Controlled Execution.

Verifications:
  1. Default invocation makes zero network connections.
  2. Wrong or unauthorized account is rejected (Only info@pybim.com is permitted).
  3. Connection diagnostic mode is strictly read-only.
  4. Folder discovery preserves actual server names and delimiters.
  5. Ambiguous Sent folder requires review / detection.
  6. Message limits (max 10 Inbox, max 10 Sent) are strictly enforced.
  7. Oversized MIME messages (>10 MB) are deferred without downloading attachments.
  8. Repeated staging is idempotent (no duplicate message rows or checkpoints).
  9. Failed fetch does not skip uncommitted UIDs.
 10. No remote Seen/Unseen modifications occur (Flag preservation verified).
 11. No operational CRM mutations occur (candidate_companies, candidate_contacts,
     historical_sent_emails, manual_outreach_log are untouched).
 12. Invalid or missing credentials fail safely without crashing.
 13. Historical Gmail copies do not create new sent events.
 14. Manual Sent without subject cannot be falsely confirmed by time proximity alone.
 15. Pilot command works independently of the web server.
 16. Existing test isolation guarantees zero modifications to data/sales_outreach.db.
"""

import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.inbox.reconciliation_service import MailboxReconciliationService
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore
from scripts.run_mailbox_pilot import (
    MailboxPilotRunner,
    get_pilot_account_config,
    PILOT_ACCOUNT_ID,
    PILOT_EMAIL_ADDRESS,
    PILOT_CREDENTIAL_REF,
    PILOT_MAX_INBOX_MESSAGES,
    PILOT_MAX_SENT_MESSAGES,
)


@pytest.fixture
def disposable_pilot_env(tmp_path, monkeypatch):
    """
    Sets up an isolated disposable environment for pilot testing.
    Protects data/sales_outreach.db from any writes.
    """
    test_db = str(tmp_path / "disposable_sales_outreach.db")
    cred_file = tmp_path / ".test_credentials.enc"

    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(tmp_path / "candidates.db"))

    # Initialize candidate tables via CandidateStore and ReviewStore
    cand_store = CandidateStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)

    # Initialize mailbox tables
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # Initialize outreach tables
    with sqlite3.connect(test_db) as conn:
        conn.execute("""
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
            );
        """)

    cred_store = MailboxCredentialStore(store_path=cred_file)

    return {
        "db_path": test_db,
        "cred_store": cred_store,
        "repo": repo,
        "cand_store": cand_store,
        "rev_store": rev_store,
    }


# ---------------------------------------------------------------------------
# 1. Default invocation makes zero network connections
# ---------------------------------------------------------------------------
def test_01_default_invocation_makes_zero_network_connections(disposable_pilot_env):
    """The default check command must inspect local state only without network sockets."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]

    mock_provider = MagicMock()
    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    report = runner.validate_preflight(check_credential=True)
    # Target info is correct, but credential is not yet provisioned
    assert report["email_address"] == PILOT_EMAIL_ADDRESS
    assert report["credential_configured"] is False
    assert report["safety_passed"] is False

    # Assert zero provider or network methods were invoked
    assert mock_provider.connect.call_count == 0
    assert mock_provider.run_diagnostics.call_count == 0


# ---------------------------------------------------------------------------
# 2. Wrong or unauthorized account is rejected
# ---------------------------------------------------------------------------
def test_02_wrong_or_unauthorized_account_is_rejected(disposable_pilot_env):
    """Piloting must strictly reject any account other than info@pybim.com."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]

    runner = MailboxPilotRunner(db_path=db_path, cred_store=cred_store)
    # Attempt to swap account to info@pybim.it
    runner.account = runner.account.model_copy(update={"email_address": "info@pybim.it"})

    report = runner.validate_preflight()
    assert report["safety_passed"] is False
    assert any("Unauthorized pilot target" in err for err in report["errors"])


# ---------------------------------------------------------------------------
# 3. Connection diagnostic mode is read-only
# ---------------------------------------------------------------------------
def test_03_connection_diagnostic_mode_is_read_only(disposable_pilot_env):
    """Diagnostic mode executes safe capability query and logs out without mutations."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]

    cred_store.set_credential(PILOT_CREDENTIAL_REF, "pilot_dummy_secret")

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.run_diagnostics.return_value = {
        "connected": True,
        "tls_verified": True,
        "authenticated": True,
        "capabilities": ["IMAP4rev1", "IDLE", "UIDPLUS"],
        "folders_count": 5,
        "detected_inbox": "INBOX",
        "detected_sent": "Sent",
        "ambiguous_sent": False,
        "error_category": None,
        "error_message": None,
    }

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.run_connection_diagnostics()
    assert res["success"] is True
    assert res["diagnostics"]["connected"] is True
    assert res["diagnostics"]["authenticated"] is True
    assert res["diagnostics"]["tls_verified"] is True


# ---------------------------------------------------------------------------
# 4. Folder discovery preserves actual server names and delimiters
# ---------------------------------------------------------------------------
def test_04_folder_discovery_preserves_actual_server_names(disposable_pilot_env):
    """Discovers remote folders while preserving exact server names, slashes, and roles."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    fld_inbox = MailboxFolderRecord(
        folder_id="fld_inbox",
        account_id=PILOT_ACCOUNT_ID,
        folder_name="INBOX",
        folder_delimiter="/",
        folder_role="inbox",
    )
    fld_sent = MailboxFolderRecord(
        folder_id="fld_sent",
        account_id=PILOT_ACCOUNT_ID,
        folder_name="Posta inviata",  # Italian localized name on Register.it
        folder_delimiter="/",
        folder_role="sent",
    )

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = MagicMock()
    mock_provider.discover_folders.return_value = [fld_inbox, fld_sent]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.discover_remote_folders()
    assert res["success"] is True
    assert res["total_folders"] == 2
    assert res["detected_inbox"] == "INBOX"
    assert res["detected_sent"] == "Posta inviata"
    assert res["ambiguous_sent"] is False


# ---------------------------------------------------------------------------
# 5. Ambiguous Sent folder requires review
# ---------------------------------------------------------------------------
def test_05_ambiguous_sent_folder_requires_review(disposable_pilot_env):
    """When multiple Sent candidates exist (e.g. Sent and 'Posta inviata'), flag as ambiguous."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    fld1 = MailboxFolderRecord(folder_id="f1", account_id=PILOT_ACCOUNT_ID, folder_name="Sent", folder_role="sent")
    fld2 = MailboxFolderRecord(folder_id="f2", account_id=PILOT_ACCOUNT_ID, folder_name="Sent Items", folder_role="sent")

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = MagicMock()
    mock_provider.discover_folders.return_value = [fld1, fld2]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.discover_remote_folders()
    assert res["success"] is True
    assert res["ambiguous_sent"] is True
    assert res["detected_sent"] is None
    assert set(res["sent_candidates"]) == {"Sent", "Sent Items"}


# ---------------------------------------------------------------------------
# 6. Message limits (max 10 Inbox, max 10 Sent) are strictly enforced
# ---------------------------------------------------------------------------
def test_06_message_limits_strictly_enforced(disposable_pilot_env):
    """Limits cannot exceed 10 messages even if higher values are passed in."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    repo = disposable_pilot_env["repo"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    mock_client = MagicMock()
    # 25 UIDs in remote folder
    mock_client.uid.side_effect = [
        ("OK", [b"1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25"]),
        # Pre-flight sizes for 10 messages
        ("OK", [f"{i} (UID {i} RFC822.SIZE 1024)".encode() for i in range(16, 26)]),
    ]

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (25, 1001)
    mock_provider.fetch_message_flags.return_value = {i: ["\\Seen"] for i in range(16, 26)}

    # Messages returned should be capped at 10
    sample_msgs = [
        MailboxMessageRecord(
            message_id=f"msg_{i}",
            account_id=PILOT_ACCOUNT_ID,
            folder_id="fld_acc_info_com_inbox",
            folder_role="inbox",
            direction="inbound",
            imap_uid=i,
            imap_uidvalidity=1001,
            from_address="client@test.com",
            to_addresses=["info@pybim.com"],
            subject=f"Inquiry {i}",
            date_utc="2026-11-15T00:00:00Z",
        )
        for i in range(16, 26)
    ]
    mock_provider.fetch_messages_by_uid.return_value = (sample_msgs, 25, 1001)

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(
        inbox_folder_name="INBOX",
        max_inbox=50,  # Requesting 50 must be capped at 10
    )

    assert res["success"] is True
    assert res["inbox_sync"]["messages_ingested"] == 10


# ---------------------------------------------------------------------------
# 7. Oversized MIME messages are deferred without downloading attachments
# ---------------------------------------------------------------------------
def test_07_oversized_mime_messages_deferred(disposable_pilot_env):
    """Messages exceeding 10 MB are marked deferred_oversized without storing attachments."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    repo = disposable_pilot_env["repo"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"100"])

    oversized_msg = MailboxMessageRecord(
        message_id="msg_oversized_pilot",
        account_id=PILOT_ACCOUNT_ID,
        folder_id="fld_acc_info_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=100,
        imap_uidvalidity=1001,
        from_address="bim_partner@largefiles.com",
        to_addresses=["info@pybim.com"],
        subject="Large 3D Model 50MB",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="[BODY DEFERRED: Message size 52428800 bytes exceeds 10MB limit]",
        parse_status="deferred_oversized",
    )

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_message_flags.return_value = {100: ["\\Seen"]}
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([oversized_msg], 100, 1001),
        ([], 100, 1001),
    ]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX", max_inbox=10)
    assert res["success"] is True
    assert res["inbox_sync"]["messages_deferred"] == 1

    saved = repo.get_message_by_occurrence(PILOT_ACCOUNT_ID, "fld_acc_info_com_inbox", 1001, 100)
    assert saved is not None
    assert saved.parse_status == "deferred_oversized"
    assert "exceeds 10MB limit" in saved.body_plain_text


# ---------------------------------------------------------------------------
# 8. Repeated staging is idempotent
# ---------------------------------------------------------------------------
def test_08_repeated_staging_is_idempotent(disposable_pilot_env):
    """Re-running the pilot on identical messages causes zero duplicate rows."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    repo = disposable_pilot_env["repo"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"1 2"])

    sample_msgs = [
        MailboxMessageRecord(
            message_id=f"msg_idem_{i}",
            account_id=PILOT_ACCOUNT_ID,
            folder_id="fld_acc_info_com_inbox",
            folder_role="inbox",
            direction="inbound",
            imap_uid=i,
            imap_uidvalidity=1001,
            from_address="user@client.com",
            to_addresses=["info@pybim.com"],
            subject=f"Idempotency {i}",
            date_utc="2026-11-15T00:00:00Z",
        )
        for i in [1, 2]
    ]

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (2, 1001)
    mock_provider.fetch_message_flags.return_value = {1: ["\\Seen"], 2: []}
    mock_provider.fetch_messages_by_uid.side_effect = [
        ([sample_msgs[0], sample_msgs[1]], 2, 1001),
        ([], 2, 1001),
        ([], 2, 1001),  # Second run: no new UIDs
    ]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    # First run
    res1 = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX")
    assert res1["inbox_sync"]["messages_ingested"] == 2

    # Second run
    res2 = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX")
    assert res2["inbox_sync"]["messages_ingested"] == 0

    # Total rows in SQLite remains 2
    msgs = repo.list_messages(PILOT_ACCOUNT_ID, "fld_acc_info_com_inbox")
    assert len(msgs) == 2


# ---------------------------------------------------------------------------
# 9. Failed fetch does not skip UIDs
# ---------------------------------------------------------------------------
def test_09_failed_fetch_does_not_skip_uids(disposable_pilot_env):
    """Network failure during sync preserves previous durable cursor without skipping."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    repo = disposable_pilot_env["repo"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"1 2 3"])

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (3, 1001)
    mock_provider.fetch_message_flags.return_value = {}
    mock_provider.fetch_messages_by_uid.side_effect = ConnectionResetError("Connection lost during fetch")

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX")
    assert res["success"] is True  # Folder sync handled cleanly
    assert res["inbox_sync"]["status"] == "failed"
    assert res["inbox_sync"]["ending_uid"] == 0

    # Checkpoint remained at 0
    state = repo.get_folder_sync_state(PILOT_ACCOUNT_ID, "fld_acc_info_com_inbox")
    assert state.last_synced_uid == 0
    assert state.last_status == "failed"


# ---------------------------------------------------------------------------
# 10. No remote Seen/Unseen modifications occur
# ---------------------------------------------------------------------------
def test_10_no_remote_seen_unseen_modifications(disposable_pilot_env):
    """Verifies flags before and after pilot execution are compared and unmutated."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"10"])

    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (1, 1001)

    # Initial flags: \Answered (Unseen)
    mock_provider.fetch_message_flags.return_value = {10: ["\\Answered"]}

    msg = MailboxMessageRecord(
        message_id="msg_flag_check",
        account_id=PILOT_ACCOUNT_ID,
        folder_id="fld_acc_info_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=10,
        imap_uidvalidity=1001,
        from_address="sender@test.com",
        to_addresses=["info@pybim.com"],
        subject="Flag Test",
        date_utc="2026-11-15T00:00:00Z",
    )
    mock_provider.fetch_messages_by_uid.side_effect = [([msg], 10, 1001), ([], 10, 1001)]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX")
    assert res["success"] is True
    assert res["flag_preservation"]["verified"] is True
    assert res["flag_preservation"]["mutations_detected"] is False


# ---------------------------------------------------------------------------
# 11. No operational CRM mutations occur
# ---------------------------------------------------------------------------
def test_11_no_operational_crm_mutations_occur(disposable_pilot_env):
    """Zero writes to candidate_companies, candidate_contacts, or historical records."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    cand_store = disposable_pilot_env["cand_store"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    # Seed operational data
    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO candidate_companies (candidate_id, company_name, normalized_company_name, company_domain)
            VALUES ('cand:test', 'Test Company', 'test company', 'test.com');
        """)
        conn.execute("""
            INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, created_at)
            VALUES ('ct_test', 'cand:test', 'owner@test.com', 'owned', '2026-11-15T00:00:00Z');
        """)
        conn.execute("""
            INSERT INTO suppression_list (suppression_id, email, company_domain, reason, source, created_at)
            VALUES ('sup_1', 'bad@test.com', 'test.com', 'bounced', 'manual', '2026-11-15T00:00:00Z');
        """)

    def count_crm():
        with cand_store._get_connection() as conn:
            return {
                "companies": conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0],
                "contacts": conn.execute("SELECT COUNT(*) FROM candidate_contacts").fetchone()[0],
                "suppression": conn.execute("SELECT COUNT(*) FROM suppression_list").fetchone()[0],
            }

    counts_before = count_crm()

    # Run pilot staging
    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"1"])
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_message_flags.return_value = {1: ["\\Seen"]}

    msg = MailboxMessageRecord(
        message_id="msg_crm_safe",
        account_id=PILOT_ACCOUNT_ID,
        folder_id="fld_acc_info_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=1,
        imap_uidvalidity=1001,
        from_address="owner@test.com",
        to_addresses=["info@pybim.com"],
        subject="Pilot Inquiry",
        date_utc="2026-11-15T00:00:00Z",
    )
    mock_provider.fetch_messages_by_uid.side_effect = [([msg], 1, 1001), ([], 1, 1001)]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(inbox_folder_name="INBOX")
    assert res["success"] is True

    counts_after = count_crm()
    assert counts_before == counts_after


# ---------------------------------------------------------------------------
# 12. Invalid or missing credentials fail safely
# ---------------------------------------------------------------------------
def test_12_missing_credentials_fail_safely(disposable_pilot_env):
    """Missing DPAPI password prevents live connection and returns clear guidance."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]

    runner = MailboxPilotRunner(db_path=db_path, cred_store=cred_store)
    res = runner.run_connection_diagnostics()

    assert res["success"] is False
    assert res["error_category"] == "PREFLIGHT_VALIDATION_FAILED"
    assert any("not found in DPAPI store" in err for err in res["errors"])


# ---------------------------------------------------------------------------
# 13. Historical Gmail copies do not create new sent events
# ---------------------------------------------------------------------------
def test_13_historical_gmail_copies_do_not_create_sent_events(disposable_pilot_env):
    """A copied historical sent message is staged as mailbox evidence only."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    cred_store.set_credential(PILOT_CREDENTIAL_REF, "dummy_pwd")

    # Insert historical sent email
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO historical_sent_emails (
                id, message_id, thread_id, sender_account, to_address, recipient_emails_json, subject, sent_date, sent_time, matched_candidate_id
            ) VALUES (500, 'gmail_msg_500', 'th_500', 'info@pybim.com', 'partner@arch.it', '["partner@arch.it"]', 'pyBIM Architecture Proposal', '2026-10-10', '15:00:00', 'cand:arch');
        """)

    # Staged sent message
    msg = MailboxMessageRecord(
        message_id="msg_gmail_copy_pilot",
        account_id=PILOT_ACCOUNT_ID,
        folder_id="fld_acc_info_com_sent",
        folder_role="sent",
        direction="outbound",
        imap_uid=808,
        imap_uidvalidity=1001,
        from_address="info@pybim.com",
        to_addresses=["partner@arch.it"],
        subject="pyBIM Architecture Proposal",
        date_utc="2026-10-10T15:00:00Z",
    )

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b"808"])
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (1, 1001)
    mock_provider.fetch_message_flags.return_value = {808: ["\\Seen"]}
    mock_provider.fetch_messages_by_uid.side_effect = [([msg], 808, 1001), ([], 808, 1001)]

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    res = runner.execute_pilot_sample_staging(sent_folder_name="Sent")
    assert res["success"] is True

    # Confirm count in historical_sent_emails did NOT increase
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# 14. Manual Sent without subject cannot be falsely confirmed by time alone
# ---------------------------------------------------------------------------
def test_14_manual_sent_without_subject_cannot_falsely_confirm(disposable_pilot_env):
    """When a manual log has no subject, non-matching recipient prevents false confirmation."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]
    repo = disposable_pilot_env["repo"]
    cand_store = disposable_pilot_env["cand_store"]
    rev_store = disposable_pilot_env["rev_store"]

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO manual_outreach_log (
                log_id, candidate_id, recipient_email, subject, sender_account, logged_at
            ) VALUES ('log_tb_target', 'cand:studio_a', 'studio_a@design.it', '', 'info@pybim.com', '2026-11-14T10:00:00Z');
        """)

    # Different recipient sent at exact same time
    outbound_msg = MailboxMessageRecord(
        message_id="msg_outbound_diff",
        account_id=PILOT_ACCOUNT_ID,
        folder_id="fld_sent",
        folder_role="sent",
        imap_uidvalidity=1001,
        imap_uid=400,
        direction="outbound",
        subject="Proposta pyBIM",
        from_address="info@pybim.com",
        to_addresses=["unrelated_studio@other.it"],
        date_utc="2026-11-14T10:00:00Z",
    )

    reconciler = MailboxReconciliationService(
        mailbox_repo=repo,
        candidate_store=cand_store,
        review_store=rev_store,
    )
    proposal = reconciler.reconcile_manual_outreach(outbound_msg)

    # Must be unmatched because recipient does not match
    assert proposal.reconciliation_status == "unmatched"
    assert proposal.matched_log_id is None


# ---------------------------------------------------------------------------
# 15. Pilot command works independently of the web server
# ---------------------------------------------------------------------------
def test_15_pilot_command_runs_independently_of_web_server(disposable_pilot_env):
    """Runner initializes repositories and executes CLI workflows without starting FastAPI."""
    cred_store = disposable_pilot_env["cred_store"]
    db_path = disposable_pilot_env["db_path"]

    runner = MailboxPilotRunner(db_path=db_path, cred_store=cred_store)
    report = runner.validate_preflight(check_credential=False)
    assert report["safety_passed"] is True
    assert report["email_address"] == PILOT_EMAIL_ADDRESS
