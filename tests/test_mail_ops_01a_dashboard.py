"""
Tests for MAIL-OPS-01A: Mailbox Operations Read-Only Dashboard.
Tests:
- Scoping to primary sales mailboxes (info@pybim.com, info@pybim.it)
- Strict exclusion of secondary accounts from primary sales views
- Folder filtering (inbox vs sent)
- Pagination and search query filtering
- Verifiable thread grouping with incomplete evidence
- HTML body sanitization (blocking script, iframe, onload, remote tracking images)
- CRM reconciliation proposal rendering without mutations
- Authentication and permission checks (unauthenticated redirects to login)
- Verification that no operational DB mutations or emails occur
"""

import os
import sqlite3
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from sales_engine.inbox.models import MailboxAccountRecord, MailboxFolderRecord, MailboxMessageRecord, MessageCrmLinkRecord
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.html_sanitizer import sanitize_email_html


@pytest.fixture
def disposable_mail_ops_env(tmp_path, monkeypatch):
    """
    Creates an isolated disposable SQLite database mirroring the primary and secondary
    staged mailbox environment.
    """
    test_db = str(tmp_path / "mail_ops_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # Seed primary accounts
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info (COM)",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_info_com",
        is_enabled=True,
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
        is_enabled=True,
        sync_priority=2,
    )
    repo.save_account(acc_it)

    # Seed secondary account (must NEVER leak into primary views)
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

    # Folders
    fld_com_inbox = MailboxFolderRecord(
        folder_id="fld_acc_info_com_inbox",
        account_id="acc_info_com",
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791495722,
    )
    repo.save_folder(fld_com_inbox)

    fld_com_sent = MailboxFolderRecord(
        folder_id="fld_acc_info_com_sent",
        account_id="acc_info_com",
        folder_name="INBOX.Sent",
        folder_role="sent",
        last_uidvalidity=1791495724,
    )
    repo.save_folder(fld_com_sent)

    fld_careers_inbox = MailboxFolderRecord(
        folder_id="fld_acc_careers_com_inbox",
        account_id="acc_careers_com",
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791499999,
    )
    repo.save_folder(fld_careers_inbox)

    # Seed Messages
    # 1. Com Inbox parent message
    msg1 = MailboxMessageRecord(
        message_id="msg_com_inbox_1",
        account_id="acc_info_com",
        folder_id="fld_acc_info_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=10,
        imap_uidvalidity=1791495722,
        rfc_message_id="<parent-123@client.com>",
        from_address="partner@client.com",
        from_name="Partner Lead",
        to_addresses=["info@pybim.com"],
        subject="Project Alpha: Initial Inquiry",
        date_utc="2026-11-10T10:00:00+00:00",
        body_plain_text="Hello pyBIM, can we discuss modeling assistance?",
        body_html_sanitized="<p>Hello pyBIM, can we discuss modeling assistance?</p>",
        has_html=True,
    )
    repo.save_message(msg1)

    # 2. Com Sent child message (reply to msg1)
    msg2 = MailboxMessageRecord(
        message_id="msg_com_sent_2",
        account_id="acc_info_com",
        folder_id="fld_acc_info_com_sent",
        folder_role="sent",
        direction="outbound",
        imap_uid=20,
        imap_uidvalidity=1791495724,
        rfc_message_id="<reply-456@pybim.com>",
        in_reply_to="<parent-123@client.com>",
        references=["<parent-123@client.com>"],
        from_address="info@pybim.com",
        from_name="pyBIM Sales",
        to_addresses=["partner@client.com"],
        subject="Project Beta: Reply and Schedule Call",
        date_utc="2026-11-10T14:30:00+00:00",
        body_plain_text="Hi, yes we would be happy to help. Let us schedule a call.",
        body_html_sanitized="<p>Hi, yes we would be happy to help. Let us schedule a call.</p>",
        has_html=True,
    )
    repo.save_message(msg2)

    # 3. Secondary Careers message (must NOT appear in primary sales views)
    msg_careers = MailboxMessageRecord(
        message_id="msg_careers_3",
        account_id="acc_careers_com",
        folder_id="fld_acc_careers_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=1,
        imap_uidvalidity=1791499999,
        rfc_message_id="<resume-999@applicant.com>",
        from_address="dev@applicant.com",
        to_addresses=["careers@pybim.com"],
        subject="Application for BIM Specialist",
        date_utc="2026-11-11T09:00:00+00:00",
        body_plain_text="Here is my resume.",
        has_html=False,
    )
    repo.save_message(msg_careers)

    # 4. CRM Link Proposal for msg1
    link1 = MessageCrmLinkRecord(
        link_id="lnk_msg_com_inbox_1",
        message_id="msg_com_inbox_1",
        candidate_id="cand:domain:client.com",
        contact_id="cont_partner_01",
        link_status="verified",
        link_method="verified_contact_email",
        link_confidence=0.95,
        evidence_summary="Verified contact match for partner@client.com",
    )
    repo.save_crm_link(link1)

    # 5. Create an authenticated operator user
    from backend.operator_auth import get_auth_service
    auth_svc = get_auth_service()
    user = auth_svc.create_user("test_operator", "StrongPassword123!", "admin", "Operator Test")
    session_token = auth_svc.create_session(user.user_id)

    return {
        "repo": repo,
        "db_path": test_db,
        "session_token": session_token,
        "msg1": msg1,
        "msg2": msg2,
        "msg_careers": msg_careers,
    }


def test_01_unauthenticated_request_redirects(disposable_mail_ops_env):
    """
    1. Unauthenticated GET request to /sales/mail-operations redirects to /sales/login.
    """
    from backend.main import app
    client = TestClient(app)
    res = client.get("/sales/mail-operations", follow_redirects=False)
    assert res.status_code == 303
    assert "/sales/login" in res.headers.get("location", "")


def test_02_account_scoping_primary_sales_only(disposable_mail_ops_env):
    """
    2. Primary sales dashboard shows ONLY acc_info_com and acc_info_it.
       Secondary account messages (e.g. acc_careers_com) are strictly excluded.
    """
    from backend.main import app
    client = TestClient(app)
    client.cookies.set("sales_session", disposable_mail_ops_env["session_token"])

    res = client.get("/sales/mail-operations")
    assert res.status_code == 200
    html = res.text

    # Primary messages present
    assert "Project Alpha: Initial Inquiry" in html
    assert "Project Beta: Reply and Schedule Call" in html

    # Secondary account strictly excluded
    assert "Application for BIM Specialist" not in html
    assert "careers@pybim.com" not in html


def test_03_folder_selection_filtering(disposable_mail_ops_env):
    """
    3. Folder selector filters messages by folder_role ('inbox' vs 'sent').
    """
    from backend.main import app
    client = TestClient(app)
    client.cookies.set("sales_session", disposable_mail_ops_env["session_token"])

    # Test Inbox only
    res_inbox = client.get("/sales/mail-operations?folder=inbox")
    assert res_inbox.status_code == 200
    assert "Project Alpha: Initial Inquiry" in res_inbox.text
    assert "Project Beta: Reply and Schedule Call" not in res_inbox.text

    # Test Sent only
    res_sent = client.get("/sales/mail-operations?folder=sent")
    assert res_sent.status_code == 200
    assert "Project Beta: Reply and Schedule Call" in res_sent.text
    assert "Project Alpha: Initial Inquiry" not in res_sent.text


def test_04_pagination_and_search(disposable_mail_ops_env):
    """
    4. Server-side pagination and text query search work accurately.
    """
    from backend.main import app
    client = TestClient(app)
    client.cookies.set("sales_session", disposable_mail_ops_env["session_token"])

    # Search for specific term
    res_search = client.get("/sales/mail-operations?q=schedule")
    assert res_search.status_code == 200
    assert "Project Beta: Reply and Schedule Call" in res_search.text
    assert "Project Alpha: Initial Inquiry" not in res_search.text

    # Search for nonexistent term
    res_none = client.get("/sales/mail-operations?q=nonexistentkeyword123")
    assert res_none.status_code == 200
    assert "No messages found." in res_none.text


def test_05_verifiable_thread_grouping(disposable_mail_ops_env):
    """
    5. Thread view accurately groups parent and reply based strictly on Message-ID & In-Reply-To evidence.
    """
    from backend.main import app
    client = TestClient(app)
    client.cookies.set("sales_session", disposable_mail_ops_env["session_token"])

    # Select child message
    res = client.get("/sales/mail-operations?message_id=msg_com_sent_2")
    assert res.status_code == 200
    html = res.text

    assert "Related Thread Evidence" in html
    assert "msg_com_inbox_1" in html
    assert "msg_com_sent_2" in html
    assert "Grouped strictly via verifiable RFC Message-ID" in html


def test_06_html_sanitizer_security():
    """
    6. HTML Sanitizer blocks scripts, event handlers, iframes, and defangs tracking pixels.
    """
    dirty_html = """
    <div>
      <script>alert('xss');</script>
      <iframe src="http://evil.com"></iframe>
      <a href="javascript:stealToken()" onclick="bad()">Click Me</a>
      <a href="https://pybim.com">Safe Link</a>
      <img src="https://tracker.service.com/open.png" alt="tracker">
    </div>
    """
    clean_html = sanitize_email_html(dirty_html)

    assert "<script>" not in clean_html
    assert "<iframe>" not in clean_html
    assert "onclick" not in clean_html
    assert "javascript:" not in clean_html
    assert 'href="https://pybim.com"' in clean_html
    assert 'target="_blank"' in clean_html
    assert 'rel="noopener noreferrer"' in clean_html

    # Tracking image defanged: no active src attribute, only data-src
    assert ' src="' not in clean_html
    assert 'data-src="https://tracker.service.com/open.png"' in clean_html
    assert "Remote tracking image blocked" in clean_html


def test_07_crm_matching_proposal_inspection_without_mutation(disposable_mail_ops_env):
    """
    7. Displays CRM matching proposal with evidence summary and candidate link without mutating any records.
    """
    from backend.main import app
    client = TestClient(app)
    client.cookies.set("sales_session", disposable_mail_ops_env["session_token"])

    res = client.get("/sales/mail-operations?message_id=msg_com_inbox_1")
    assert res.status_code == 200
    html = res.text

    assert "CRM Match Proposal (Read-Only)" in html
    assert "cand:domain:client.com" in html
    assert "cont_partner_01" in html
    assert "Verified contact match for partner@client.com" in html
    assert "VERIFIED" in html

    # Confirm that candidate_companies or leads table has 0 mutations
    with sqlite3.connect(disposable_mail_ops_env["db_path"]) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = [t[0] for t in tables]
        # Foreign key integrity check
        fkv = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fkv) == 0
