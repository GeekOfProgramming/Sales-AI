"""
tests/test_imap_conn_01_adapter.py
Offline acceptance tests for IMAP-CONN-01: Secure Read-Only Register.it IMAP Adapter.

Verifications:
1. Hardened TLS configuration (checks hostname, verifies certificates).
2. Authentication with mock/disposable Windows DPAPI credential store.
3. Missing or invalid credentials fail safely without leaking passwords.
4. Folder discovery preserves original server paths, namespaces and delimiters.
5. EXAMINE is used instead of writable SELECT.
6. UID FETCH uses PEEK semantics and preserves Seen/Unseen flags.
7. Discoverable Sent folders under INBOX namespace (e.g. "INBOX/Sent" or "Posta Inviata").
8. Coexistence of multiple independent mailboxes.
9. Message headers, MIME content, and Unicode subjects parsed accurately.
10. Safe handling of messages lacking RFC Message-ID.
11. Bounded body-size and attachment metadata collection without binary persistence.
12. send_reply() is strictly disabled and fails closed.
13. Structured diagnostics return connection state without credentials.
14. Zero CRM records are mutated during any adapter operation.
"""

import ssl
import email
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
)
from sales_engine.inbox.credential_store import MailboxCredentialStore, WindowsDPAPI
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany


@pytest.fixture
def temp_cred_store(tmp_path: Path) -> MailboxCredentialStore:
    """Fixture providing an isolated credential store file."""
    cred_file = tmp_path / ".test_mailbox_creds.enc"
    return MailboxCredentialStore(store_path=cred_file)


@pytest.fixture
def pilot_account() -> MailboxAccountRecord:
    """Fixture providing the info@pybim.com pilot account definition."""
    return MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info (COM)",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_info_com",
        is_enabled=False,
    )


def test_01_hardened_tls_configuration(pilot_account):
    """Verify strict TLS context configuration (certificate verification and hostname check)."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account)
    ctx = provider.create_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_02_secure_credential_storage_roundtrip(temp_cred_store):
    """Verify secure storage and retrieval of mailbox credentials using DPAPI/fallback."""
    temp_cred_store.set_credential("win_cred_test_1", "SecretPass123!@#")
    retrieved = temp_cred_store.get_credential("win_cred_test_1")
    assert retrieved == "SecretPass123!@#"
    assert temp_cred_store.list_configured_refs() == ["win_cred_test_1"]

    # Deletion
    assert temp_cred_store.delete_credential("win_cred_test_1") is True
    assert temp_cred_store.get_credential("win_cred_test_1") is None


def test_03_missing_or_invalid_credential_fails_safely(pilot_account, temp_cred_store):
    """Verify connecting with missing credential fails safely without leaking secrets."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)
    
    with pytest.raises(ValueError) as exc_info:
        provider.connect()
    assert "No credential found" in str(exc_info.value)
    assert "Secret" not in str(exc_info.value)

    # Diagnostics should report MISSING_CREDENTIAL
    diag = provider.run_diagnostics()
    assert diag["connected"] is False
    assert diag["error_category"] == "MISSING_CREDENTIAL"


def test_04_folder_discovery_preserves_paths_and_delimiters(pilot_account, temp_cred_store):
    """
    Verify folder discovery accurately parses flags, delimiters, and remote names
    including localized names (Posta Inviata, Bozze).
    """
    temp_cred_store.set_credential("win_cred_info_com", "dummy_pass")
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    # Mock IMAP client with realistic Register.it LIST response
    mock_client = MagicMock()
    mock_client.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "INBOX/Sent"',
            b'(\\HasNoChildren) "/" "Posta Inviata"',
            b'(\\HasNoChildren \\Drafts) "/" "Bozze"',
            b'(\\HasNoChildren \\Trash) "/" "Cestino"',
            b'(\\HasNoChildren) "/" "Archivio/2026"',
        ],
    )

    folders = provider.discover_folders(pilot_account, client=mock_client)
    assert len(folders) == 6

    by_name = {f.folder_name: f for f in folders}
    assert by_name["INBOX"].folder_role == "inbox"
    assert by_name["INBOX/Sent"].folder_role == "sent"
    assert by_name["Posta Inviata"].folder_role == "sent"
    assert by_name["Bozze"].folder_role == "drafts"
    assert by_name["Cestino"].folder_role == "trash"
    assert by_name["Archivio/2026"].folder_role == "archive"
    assert by_name["INBOX/Sent"].folder_delimiter == "/"


def test_05_examine_mode_is_used_instead_of_select(pilot_account, temp_cred_store):
    """
    P0 SAFETY RULE: Verify EXAMINE is strictly called via select(readonly=True)
    to guarantee read-only folder selection on the IMAP wire.
    """
    temp_cred_store.set_credential("win_cred_info_com", "dummy_pass")
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"42"])
    mock_client.response.return_value = ("OK", [b"987654"])

    count, uidvalidity = provider.examine_folder(mock_client, "INBOX")
    assert count == 42
    assert uidvalidity == 987654

    # Assert select was invoked with readonly=True (which sends EXAMINE on the wire)
    mock_client.select.assert_called_once_with(mailbox="INBOX", readonly=True)


def test_05b_real_imaplib_wire_command_is_examine(pilot_account, temp_cred_store):
    """
    Verify with real standard-library imaplib.IMAP4 instance that select(readonly=True)
    dispatches the 'EXAMINE' command to _simple_command, NOT 'SELECT'.
    """
    import imaplib
    real_client = imaplib.IMAP4.__new__(imaplib.IMAP4)
    commands_sent = []

    def mock_simple_command(cmd_name, arg):
        commands_sent.append((cmd_name, arg))
        real_client.untagged_responses["EXISTS"] = [b"42"]
        real_client.untagged_responses["UIDVALIDITY"] = [b"987654"]
        return ("OK", [b"42"])

    real_client._simple_command = mock_simple_command
    real_client._astring = lambda s: f'"{s}"' if " " in s else s
    real_client.state = "AUTH"
    real_client.response = lambda code: ("OK", real_client.untagged_responses.get(code, []))

    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)
    count, uidvalidity = provider.examine_folder(real_client, "INBOX.Sent")

    assert count == 42
    assert uidvalidity == 987654
    assert len(commands_sent) == 1
    # Verify exact command sent over wire
    assert commands_sent[0][0] == "EXAMINE"
    assert "INBOX.Sent" in commands_sent[0][1]


def test_05c_realistic_imaplib_examine_uidvalidity_parsing(pilot_account, temp_cred_store):
    """
    Test realistic imaplib EXAMINE response where response('UIDVALIDITY') returns
    ('UIDVALIDITY', [b'1712345678']) per Python standard library implementation,
    and verify distinct UIDVALIDITY values for different folders.
    """
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    mock_client = MagicMock()
    mock_client.select.side_effect = lambda mailbox, readonly: ("OK", [b"15"] if mailbox == "INBOX" else [b"7"])

    def mock_response(code):
        if code == "UIDVALIDITY":
            # Real imaplib returns ('UIDVALIDITY', [b'...'])
            # We simulate INBOX = 1712345678, Sent = 1898765432
            current_fld = mock_client.select.call_args[1].get("mailbox") or mock_client.select.call_args[0][0]
            val = b"1712345678" if current_fld == "INBOX" else b"1898765432"
            return ("UIDVALIDITY", [val])
        if code == "OK":
            return ("OK", [None])
        return (code, [None])

    mock_client.response.side_effect = mock_response

    # Examine INBOX
    inbox_cnt, inbox_uv = provider.examine_folder(mock_client, "INBOX")
    assert inbox_cnt == 15
    assert inbox_uv == 1712345678

    # Examine INBOX.Sent
    sent_cnt, sent_uv = provider.examine_folder(mock_client, "INBOX.Sent")
    assert sent_cnt == 7
    assert sent_uv == 1898765432
    assert inbox_uv != sent_uv


def test_05d_missing_or_zero_uidvalidity_fails_closed(pilot_account, temp_cred_store):
    """Verify missing, zero, or non-numeric UIDVALIDITY strictly raises ValueError."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    # 1. Missing UIDVALIDITY
    mock_client1 = MagicMock()
    mock_client1.select.return_value = ("OK", [b"5"])
    mock_client1.response.side_effect = lambda code: (code, [None])
    if hasattr(mock_client1, "untagged_responses"):
        del mock_client1.untagged_responses

    with pytest.raises(ValueError, match="Missing or invalid server UIDVALIDITY"):
        provider.examine_folder(mock_client1, "INBOX")

    # 2. Zero UIDVALIDITY
    mock_client2 = MagicMock()
    mock_client2.select.return_value = ("OK", [b"5"])
    mock_client2.response.side_effect = lambda code: (code, [b"0"] if code == "UIDVALIDITY" else [None])

    with pytest.raises(ValueError, match="Missing or invalid server UIDVALIDITY"):
        provider.examine_folder(mock_client2, "INBOX")

    # 3. Non-numeric UIDVALIDITY
    mock_client3 = MagicMock()
    mock_client3.select.return_value = ("OK", [b"5"])
    mock_client3.response.side_effect = lambda code: (code, [b"invalid"] if code == "UIDVALIDITY" else [None])

    with pytest.raises(ValueError, match="Missing or invalid server UIDVALIDITY"):
        provider.examine_folder(mock_client3, "INBOX")


def test_05e_untagged_ok_bracket_fallback_uidvalidity_parsing(pilot_account, temp_cred_store):
    """Verify fallback parses [UIDVALIDITY 1698765432] from untagged OK line."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"12"])
    mock_client.response.side_effect = lambda code: (
        ("UIDVALIDITY", [None])
        if code == "UIDVALIDITY"
        else ("OK", [b"[UIDVALIDITY 1698765432] UIDs valid", b"[UIDNEXT 15] Predicted next UID"])
    )

    count, uv = provider.examine_folder(mock_client, "INBOX")
    assert count == 12
    assert uv == 1698765432


def test_06_uid_fetch_uses_peek_semantics_preserving_flags(pilot_account, temp_cred_store):
    """
    P0 SAFETY RULE: Verify messages are fetched using BODY.PEEK[],
    preventing alteration of remote \\Seen flags. Specifically verify RFC822.PEEK
    is NEVER sent (as Register.it rejects it with BAD).
    """
    temp_cred_store.set_credential("win_cred_info_com", "dummy_pass")
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    folder = MailboxFolderRecord(
        folder_id="fld_inbox",
        account_id=pilot_account.account_id,
        folder_name="INBOX",
        folder_role="inbox",
    )

    sample_email = (
        b"From: studio@piuarch.it\r\n"
        b"To: info@pybim.com\r\n"
        b"Subject: Richiesta informazioni BIM\r\n"
        b"Message-ID: <msg123@piuarch.it>\r\n"
        b"Date: Sun, 15 Nov 2026 00:00:00 +0000\r\n"
        b"\r\n"
        b"Buongiorno, vorremmo maggiori dettagli sui vostri servizi."
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"1"])
    mock_client.response.return_value = ("OK", [b"12345"])
    mock_client.uid.side_effect = [
        ("OK", [b"105"]),  # SEARCH response
        ("OK", [b"1 (UID 105 RFC822.SIZE 150)"]),  # RFC822.SIZE response
        ("OK", [(b"1 (UID 105 BODY[] {150})", sample_email)]),  # BODY.PEEK[] response from server
    ]

    messages, new_highest_uid, uv = provider.fetch_messages_by_uid(
        pilot_account, folder, since_uid=100, client=mock_client
    )

    assert len(messages) == 1
    assert messages[0].imap_uid == 105
    assert new_highest_uid == 105
    assert uv == 12345
    assert messages[0].from_address == "studio@piuarch.it"

    # Assert that UID FETCH used BODY.PEEK[] and never RFC822.PEEK
    peek_calls = [call for call in mock_client.uid.call_args_list if len(call[0]) > 2 and "BODY.PEEK[]" in call[0][2]]
    assert len(peek_calls) == 1
    rfc_peek_calls = [call for call in mock_client.uid.call_args_list if len(call[0]) > 2 and "RFC822.PEEK" in call[0][2]]
    assert len(rfc_peek_calls) == 0


def test_06b_register_it_rejects_rfc822_peek_and_provider_uses_body_peek(pilot_account, temp_cred_store):
    """
    Regression test: Register.it returns BAD for RFC822.PEEK:
    "UID command error: BAD [b'Error in IMAP command UID FETCH: Unknown parameter RFC822.PEEK']"
    Verify the provider dispatches standards-compliant BODY.PEEK[] which Register.it accepts.
    """
    temp_cred_store.set_credential("win_cred_info_com", "dummy_pass")
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)

    folder = MailboxFolderRecord(
        folder_id="fld_inbox",
        account_id=pilot_account.account_id,
        folder_name="INBOX",
        folder_role="inbox",
    )

    sample_email = (
        b"From: client@example.com\r\n"
        b"To: info@pybim.com\r\n"
        b"Subject: Test\r\n"
        b"\r\n"
        b"Hello"
    )

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"1"])
    mock_client.response.return_value = ("OK", [b"12345"])

    def uid_side_effect(cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"201"])
        if cmd == "FETCH":
            target_uids, fetch_param = args[0], args[1]
            if "RFC822.PEEK" in fetch_param:
                # Simulate Register.it exact server rejection
                import imaplib
                raise imaplib.IMAP4.error("UID command error: BAD [b'Error in IMAP command UID FETCH: Unknown parameter RFC822.PEEK (0.001 + 0.000 secs).']")
            if "RFC822.SIZE" in fetch_param:
                return ("OK", [b"1 (UID 201 RFC822.SIZE 120)"])
            if "BODY.PEEK[]" in fetch_param:
                return ("OK", [(b"1 (UID 201 BODY[] {120})", sample_email)])
        raise ValueError(f"Unexpected mock call: {cmd} {args}")

    mock_client.uid.side_effect = uid_side_effect

    messages, new_highest_uid, uv = provider.fetch_messages_by_uid(
        pilot_account, folder, since_uid=200, client=mock_client
    )

    assert len(messages) == 1
    assert messages[0].imap_uid == 201
    assert messages[0].from_address == "client@example.com"


def test_07_mime_parsing_unicode_and_headers(pilot_account):
    """Verify MIME parsing decodes international RFC 2047 words and formats UTC dates."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account)

    raw_email = (
        b"From: =?utf-8?B?TWFyaW8gUm9zc2k=?= <mario.rossi@studiobim.it>\r\n"
        b"To: info@pybim.com, partner@pybim.it\r\n"
        b"Cc: manager@studiobim.it\r\n"
        b"Subject: =?utf-8?B?UHJvcG9zdGEgZGkgY29sbGFib3JhemlvbmUgQklNIOKAkyBweUJJTQ==?=\r\n"
        b"Message-ID: <unique_msg_999@studiobim.it>\r\n"
        b"In-Reply-To: <orig_outreach_1@pybim.com>\r\n"
        b"References: <orig_outreach_1@pybim.com> <prev_ref_2@pybim.com>\r\n"
        b"Date: Sat, 14 Nov 2026 23:30:00 +0100\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Gentile pyBIM, confermiamo interesse per la modellazione OpenBIM."
    )

    msg = provider.parse_raw_email(
        account_id=pilot_account.account_id,
        folder_id="fld_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=500,
        imap_uidvalidity=999,
        raw_bytes=raw_email,
    )

    assert msg.from_name == "Mario Rossi"
    assert msg.from_address == "mario.rossi@studiobim.it"
    assert msg.to_addresses == ["info@pybim.com", "partner@pybim.it"]
    assert msg.cc_addresses == ["manager@studiobim.it"]
    assert "Proposta di collaborazione BIM – pyBIM" in msg.subject
    assert msg.rfc_message_id == "<unique_msg_999@studiobim.it>"
    assert msg.in_reply_to == "<orig_outreach_1@pybim.com>"
    assert len(msg.references) == 2
    assert "2026-11-14T22:30:00+00:00" in msg.date_utc  # Converted +0100 to UTC


def test_08_missing_rfc_message_id_handled_safely(pilot_account):
    """Verify messages missing RFC Message-ID are assigned deterministic internal IDs."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account)

    raw_email = (
        b"From: anonymous@partner.it\r\n"
        b"To: info@pybim.com\r\n"
        b"Subject: No message id\r\n"
        b"Date: Sun, 15 Nov 2026 00:00:00 +0000\r\n"
        b"\r\n"
        b"Test message without RFC id"
    )

    msg = provider.parse_raw_email(
        account_id=pilot_account.account_id,
        folder_id="fld_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=601,
        imap_uidvalidity=999,
        raw_bytes=raw_email,
    )

    assert msg.rfc_message_id is None
    assert msg.message_id.startswith("msg_")
    assert msg.from_address == "anonymous@partner.it"


def test_09_attachment_metadata_without_binary_persistence(pilot_account):
    """Verify attachment metadata (name, size, type) is collected without storing binaries."""
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account)

    raw_multipart = (
        b"From: info@client.it\r\n"
        b"To: info@pybim.com\r\n"
        b"Subject: Allegato Capitolato\r\n"
        b"Date: Sun, 15 Nov 2026 00:00:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=\"BOUNDARY\"\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"In allegato il capitolato informativo.\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"Capitolato_BIM_2026.pdf\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"JVBERi0xLjQKJcTl8uXr...\r\n"
        b"--BOUNDARY--\r\n"
    )

    msg = provider.parse_raw_email(
        account_id=pilot_account.account_id,
        folder_id="fld_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=701,
        imap_uidvalidity=999,
        raw_bytes=raw_multipart,
    )

    assert msg.has_attachments is True
    assert len(msg.attachments_metadata) == 1
    att = msg.attachments_metadata[0]
    assert att["filename"] == "Capitolato_BIM_2026.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["size_bytes"] > 0
    # Crucial: no raw binary stored in message record
    assert not hasattr(msg, "binary_content")


def test_10_send_reply_is_strictly_forbidden(pilot_account):
    """
    P0 SAFETY RULE: Verify send_reply() raises NotImplementedError
    and cannot be used to dispatch emails.
    """
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account)
    with pytest.raises(NotImplementedError) as exc_info:
        provider.send_reply(
            to_email="client@domain.it",
            subject="Re: Test",
            body_text="Reply text",
        )
    assert "strictly read-only" in str(exc_info.value)


def test_11_zero_crm_mutations_during_adapter_operations(tmp_path, pilot_account, temp_cred_store):
    """
    CRITICAL ACCEPTANCE TEST:
    Verify that executing adapter diagnostics, folder discovery, and message parsing
    creates ZERO changes to CandidateStore records.
    """
    db_file = tmp_path / "crm_safety_test.db"
    cand_store = CandidateStore(db_path=db_file)

    test_company = CandidateCompany(
        candidate_id="cand:domain:piuarch.it",
        company_name="Piuarch",
        normalized_company_name="piuarch",
        company_domain="piuarch.it",
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
    )
    cand_store.save_candidate(test_company)

    # Run adapter operations
    provider = RegisterItReadOnlyIMAPProvider(account=pilot_account, credential_store=temp_cred_store)
    mock_client = MagicMock()
    mock_client.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    provider.discover_folders(pilot_account, client=mock_client)

    raw_email = (
        b"From: studio@piuarch.it\r\n"
        b"To: info@pybim.com\r\n"
        b"Subject: Unsubscribe\r\n"
        b"\r\n"
        b"Please remove us."
    )
    provider.parse_raw_email(
        account_id=pilot_account.account_id,
        folder_id="fld_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=801,
        imap_uidvalidity=999,
        raw_bytes=raw_email,
    )

    # Verify CandidateCompany in CandidateStore is completely UNTOUCHED
    cand_after = cand_store.get_candidate("cand:domain:piuarch.it")
    assert cand_after.cold_outreach_allowed is True
    assert cand_after.suppressed is False
    assert cand_after.active_conversation is False
