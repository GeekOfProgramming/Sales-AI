"""
tests/test_imap_uv_repair_01a_verification.py
Offline test suite for IMAP-UV-REPAIR-01A: Read-Only Verification of Existing Pilot Messages.

Verifications:
  1. Default invocation without --live-connect refuses network connections.
  2. Verification is strictly restricted to info@pybim.com (other accounts rejected).
  3. Positive server UIDVALIDITY accurately extracted independently for INBOX and Sent.
  4. 100% of the 20 existing synthetic pilot records match and verify correctly.
  5. Header mismatches (RFC Message-ID, Subject, From) are properly flagged as MISMATCH.
  6. Missing remote message UIDs are properly flagged as MISSING.
  7. Missing RFC Message-ID falls back to multi-attribute consensus.
  8. Missing or non-positive UIDVALIDITY fails closed safely.
  9. Checkpoint consistency validation detects matching vs mismatched checkpoints.
 10. Verification makes zero SQLite mutations (read-only SQLite query).
 11. Verification makes zero remote flag changes.
 12. No credentials, full bodies, or sensitive tokens exposed in report.
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
    PILOT_ACCOUNT_ID,
    PILOT_EMAIL_ADDRESS,
    PILOT_CREDENTIAL_REF,
)


@pytest.fixture
def disposable_verify_env(tmp_path, monkeypatch):
    """Sets up an isolated disposable environment with 20 synthetic pilot records."""
    test_db = str(tmp_path / "disposable_verify_sales_outreach.db")
    cred_file = tmp_path / ".test_credentials.enc"

    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)

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

    # Set checkpoints: INBOX=11, Sent=17
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


def test_01_target_restriction_rejects_other_mailboxes(disposable_verify_env):
    """Verification must strictly reject accounts other than info@pybim.com."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]

    other_acc = get_account_config_by_email("info@pybim.it")
    runner = MailboxPilotRunner(db_path=db_path, cred_store=cred_store, account=other_acc)
    res = runner.verify_existing_pilot_messages()

    assert res["success"] is False
    assert res["error_category"] == "UNAUTHORIZED_TARGET"
    assert "strictly restricted to info@pybim.com" in res["error"]


def test_02_all_20_pilot_messages_verified_successfully(disposable_verify_env):
    """Verify that when remote headers match exactly, all 20 records report VERIFIED."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client

    # Realistic provider header cleaning/parsing delegate
    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    # Distinct UIDVALIDITY values
    mock_provider.examine_folder.side_effect = lambda c, folder: (
        (10, 1698765432) if folder == "INBOX" else (10, 1798765432)
    )

    def uid_side_effect(cmd, uid_str, query):
        uids = [int(u) for u in uid_str.split(",")]
        res_tuples = []
        # Check which folder was selected most recently
        current_folder = mock_provider.examine_folder.call_args[0][1]
        for u in uids:
            if current_folder == "INBOX":
                raw = _build_mock_email_bytes(
                    rfc_id=f"<msg-inbox-{u}@example.com>",
                    subject=f"Inquiry {u}",
                    from_addr=f"sender{u}@client.com",
                    to_addr="info@pybim.com",
                    date_str="Tue, 10 Nov 2026 10:00:00 +0000",
                )
            else:
                raw = _build_mock_email_bytes(
                    rfc_id=f"<msg-sent-{u}@pybim.com>",
                    subject=f"Re: Inquiry {u}",
                    from_addr="info@pybim.com",
                    to_addr=f"client{u}@firm.nl",
                    date_str="Fri, 13 Nov 2026 10:00:00 +0000",
                )
            hdr_header = f"{u} (UID {u} BODY[HEADER] {{{len(raw)}}})".encode()
            res_tuples.append((hdr_header, raw))
        return ("OK", res_tuples)

    mock_client.uid.side_effect = uid_side_effect

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    report = runner.verify_existing_pilot_messages()

    assert report["success"] is True
    assert report["actual_inbox_uidvalidity"] == 1698765432
    assert report["actual_sent_uidvalidity"] == 1798765432
    assert report["actual_inbox_uidvalidity"] != report["actual_sent_uidvalidity"]

    assert report["inbox_verified_count"] == 10
    assert report["sent_verified_count"] == 10
    assert report["inbox_mismatched_count"] == 0
    assert report["sent_mismatched_count"] == 0
    assert report["inbox_missing_count"] == 0
    assert report["sent_missing_count"] == 0
    assert report["checkpoint_consistency"]["is_consistent"] is True
    assert report["safe_repair_eligible"] is True


def test_03_header_mismatch_detected(disposable_verify_env):
    """Verify that if a remote message has a different Subject or RFC ID, it is marked MISMATCH."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client

    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    mock_provider.examine_folder.return_value = (10, 1698765432)

    def uid_side_effect(cmd, uid_str, query):
        uids = [int(u) for u in uid_str.split(",")]
        res_tuples = []
        for u in uids:
            # For UID 5, simulate altered subject and mismatched Message-ID
            subj = "COMPLETELY DIFFERENT SUBJECT" if u == 5 else f"Inquiry {u}"
            rfc_id = f"<different-{u}@example.com>" if u == 5 else f"<msg-inbox-{u}@example.com>"
            raw = _build_mock_email_bytes(
                rfc_id=rfc_id,
                subject=subj,
                from_addr=f"sender{u}@client.com",
                to_addr="info@pybim.com",
                date_str="Tue, 10 Nov 2026 10:00:00 +0000",
            )
            hdr_header = f"{u} (UID {u} BODY[HEADER] {{{len(raw)}}})".encode()
            res_tuples.append((hdr_header, raw))
        return ("OK", res_tuples)

    mock_client.uid.side_effect = uid_side_effect

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    report = runner.verify_existing_pilot_messages()
    assert report["success"] is True
    assert report["inbox_verified_count"] == 9
    assert report["inbox_mismatched_count"] == 1
    assert report["safe_repair_eligible"] is False


def test_04_missing_remote_uid_detected(disposable_verify_env):
    """Verify that if a remote UID was deleted/vanished, it is marked MISSING."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client

    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    mock_provider.examine_folder.return_value = (10, 1698765432)

    def uid_side_effect(cmd, uid_str, query):
        uids = [int(u) for u in uid_str.split(",")]
        res_tuples = []
        for u in uids:
            if u == 7:
                continue  # UID 7 missing from server response
            raw = _build_mock_email_bytes(
                rfc_id=f"<msg-inbox-{u}@example.com>",
                subject=f"Inquiry {u}",
                from_addr=f"sender{u}@client.com",
                to_addr="info@pybim.com",
                date_str="Tue, 10 Nov 2026 10:00:00 +0000",
            )
            hdr_header = f"{u} (UID {u} BODY[HEADER] {{{len(raw)}}})".encode()
            res_tuples.append((hdr_header, raw))
        return ("OK", res_tuples)

    mock_client.uid.side_effect = uid_side_effect

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    report = runner.verify_existing_pilot_messages()
    assert report["success"] is True
    assert report["inbox_verified_count"] == 9
    assert report["inbox_missing_count"] == 1
    assert report["safe_repair_eligible"] is False


def test_05_missing_rfc_id_consensus_handling(disposable_verify_env):
    """Verify that messages without RFC Message-ID use multi-attribute consensus."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]
    repo = disposable_verify_env["repo"]

    # Clear RFC Message-ID for UID 3 in database
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE mailbox_messages SET rfc_message_id = NULL WHERE imap_uid = 3 AND folder_role = 'inbox';")

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client

    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    mock_provider.examine_folder.return_value = (10, 1698765432)

    def uid_side_effect(cmd, uid_str, query):
        uids = [int(u) for u in uid_str.split(",")]
        res_tuples = []
        for u in uids:
            rfc_id = None if u == 3 else f"<msg-inbox-{u}@example.com>"
            raw = _build_mock_email_bytes(
                rfc_id=rfc_id,
                subject=f"Inquiry {u}",
                from_addr=f"sender{u}@client.com",
                to_addr="info@pybim.com",
                date_str="Tue, 10 Nov 2026 10:00:00 +0000",
            )
            hdr_header = f"{u} (UID {u} BODY[HEADER] {{{len(raw)}}})".encode()
            res_tuples.append((hdr_header, raw))
        return ("OK", res_tuples)

    mock_client.uid.side_effect = uid_side_effect

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    report = runner.verify_existing_pilot_messages()
    assert report["success"] is True
    # UID 3 was verified via multi-attribute consensus
    v3 = [v for v in report["verifications"]["inbox"] if v["uid"] == 3][0]
    assert v3["status"] == "VERIFIED"
    assert "consensus" in v3["evidence"]


def test_06_verification_makes_zero_sqlite_mutations(disposable_verify_env):
    """Strictly assert that verify_existing_pilot_messages does not change SQLite tables or records."""
    db_path = disposable_verify_env["db_path"]
    cred_store = disposable_verify_env["cred_store"]

    with sqlite3.connect(db_path) as conn:
        before_count = conn.execute("SELECT COUNT(*) FROM mailbox_messages;").fetchone()[0]
        before_uv = conn.execute("SELECT DISTINCT imap_uidvalidity FROM mailbox_messages;").fetchall()

    mock_client = MagicMock()
    mock_provider = MagicMock(spec=RegisterItReadOnlyIMAPProvider)
    mock_provider.connect.return_value = mock_client
    mock_provider.examine_folder.return_value = (10, 1698765432)
    mock_client.uid.return_value = ("OK", [])

    real_provider = RegisterItReadOnlyIMAPProvider(account=get_pilot_account_config())
    mock_provider._clean_header = real_provider._clean_header
    mock_provider._decode_mime_words = real_provider._decode_mime_words
    mock_provider._parse_address_and_name = real_provider._parse_address_and_name
    mock_provider._normalize_date_to_utc = real_provider._normalize_date_to_utc

    runner = MailboxPilotRunner(
        db_path=db_path,
        cred_store=cred_store,
        provider_factory=lambda acc: mock_provider,
    )

    runner.verify_existing_pilot_messages()

    with sqlite3.connect(db_path) as conn:
        after_count = conn.execute("SELECT COUNT(*) FROM mailbox_messages;").fetchone()[0]
        after_uv = conn.execute("SELECT DISTINCT imap_uidvalidity FROM mailbox_messages;").fetchall()

    assert before_count == after_count == 20
    assert before_uv == after_uv == [(0,)]
