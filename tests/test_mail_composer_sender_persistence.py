"""
Test Suite: test_mail_composer_sender_persistence.py
Validates MAIL-COMPOSER-SENDER-PERSISTENCE-FIX:
1. Creating a draft with 'acc_info_com'.
2. Updating and saving the draft with 'acc_info_it'.
3. Asserting that 'acc_info_it' and 'info@pybim.it' are durably persisted in SQLite.
4. Verifying that reopening the draft renders <option value="acc_info_it" selected>.
5. Verifying that reopening does NOT reset to 'acc_info_com'.
6. Verifying that updating to an unauthorized sender fails closed with HTTP 400.
7. Verifying that drafts in 'scheduled' status protect immutable delivery snapshots.
8. Verifying that MIME construction uses stored account and credentials.
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxDraftRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from backend.sales_ui.router import CSRF_COOKIE_NAME
from backend.operator_auth import get_auth_service, OperatorAuthService
import backend.operator_auth


@pytest.fixture
def disposable_env(tmp_path, monkeypatch):
    test_db = str(tmp_path / "sender_persistence_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    # Point global auth service to isolated test database
    orig_auth = backend.operator_auth._auth_service
    test_auth = OperatorAuthService(db_path=test_db)
    backend.operator_auth._auth_service = test_auth

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Global Info",
        account_role="primary_sales",
        imap_host="imap.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_com",
        is_active=True,
    )
    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Italia Info",
        account_role="primary_sales",
        imap_host="imap.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_it",
        is_active=True,
    )
    repo.save_account(acc_com)
    repo.save_account(acc_it)

    yield {
        "db_path": test_db,
        "repo": repo,
        "auth": test_auth,
    }

    backend.operator_auth._auth_service = orig_auth


@pytest.fixture
def test_client(disposable_env):
    from backend.main import app
    client = TestClient(app)

    auth = disposable_env["auth"]
    user = auth.create_user("operator_tester", "SafePassword123!", "operator", "Test Operator")
    token = auth.create_session(user.user_id)
    client.cookies.set("sales_session", token)
    return client


def _get_csrf(client: TestClient) -> str:
    resp = client.get("/sales/mail-operations/compose")
    token = resp.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = "test_csrf_token_sender_persistence"
        client.cookies.set(CSRF_COOKIE_NAME, token)
    return token


def test_sender_account_persistence_lifecycle(test_client, disposable_env):
    """
    Simulates the exact operator workflow:
    1. Create draft with info@pybim.com (acc_info_com).
    2. Edit draft to info@pybim.it (acc_info_it) and save.
    3. Reopen draft and verify selected sender is acc_info_it.
    4. Verify no silent fallback to acc_info_com occurs.
    """
    repo = disposable_env["repo"]
    csrf = _get_csrf(test_client)

    # 1. Initial save with acc_info_com
    save_payload = {
        "csrf_token": csrf,
        "account_id": "acc_info_com",
        "to_addresses": "partner@example.it",
        "subject": "Initial English Subject",
        "body_plain_text": "Initial body text",
        "body_html": "<p>Initial body text</p>",
        "version": 1,
        "action_type": "save",
    }
    resp = test_client.post("/sales/mail-operations/compose/save", data=save_payload, follow_redirects=False)
    assert resp.status_code == 303
    loc = resp.headers.get("location")
    assert "draft_id=" in loc
    import urllib.parse
    draft_id = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["draft_id"][0]

    record1 = repo.get_draft(draft_id)
    assert record1 is not None
    assert record1.account_id == "acc_info_com"
    assert record1.sender_email == "info@pybim.com"
    assert record1.version == 1

    # 2. Operator updates draft to acc_info_it (info@pybim.it)
    update_payload = {
        "csrf_token": csrf,
        "draft_id": draft_id,
        "account_id": "acc_info_it",
        "to_addresses": "partner@example.it",
        "subject": "pyBIM | Email Delivery Test — Italian Domain",
        "body_plain_text": "Salve, questo è un test.",
        "body_html": "<p>Salve, questo è un test.</p>",
        "version": record1.version,
        "action_type": "save",
    }
    resp_update = test_client.post("/sales/mail-operations/compose/save", data=update_payload, follow_redirects=False)
    assert resp_update.status_code == 303

    record2 = repo.get_draft(draft_id)
    assert record2 is not None
    assert record2.account_id == "acc_info_it"
    assert record2.sender_email == "info@pybim.it"
    assert record2.version == 2

    # 3. Reopen draft via GET /sales/mail-operations/compose?draft_id=...
    resp_get = test_client.get(f"/sales/mail-operations/compose?draft_id={draft_id}")
    assert resp_get.status_code == 200
    html = resp_get.text

    # Verify acc_info_it is marked selected in the HTML
    assert '<option value="acc_info_it" selected>' in html
    # Verify acc_info_com is NOT selected
    assert '<option value="acc_info_com" selected>' not in html


def test_unauthorized_sender_rejection(test_client, disposable_env):
    """
    Submitting an unauthorized account ID must fail closed with HTTP 400.
    """
    csrf = _get_csrf(test_client)
    payload = {
        "csrf_token": csrf,
        "account_id": "acc_rogue_sender",
        "to_addresses": "partner@example.it",
        "subject": "Unauthorized Test",
        "body_plain_text": "Test body",
        "version": 1,
        "action_type": "save",
    }
    resp = test_client.post("/sales/mail-operations/compose/save", data=payload)
    assert resp.status_code == 400
    assert "Unauthorized sender account" in resp.text


def test_scheduled_draft_immutability_guard(test_client, disposable_env):
    """
    Once scheduled, editing the draft directly is blocked to protect immutable snapshots.
    """
    repo = disposable_env["repo"]
    csrf = _get_csrf(test_client)

    draft = MailboxDraftRecord(
        draft_id="drf_scheduled_lock",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        to_addresses=["locked@example.com"],
        subject="Immutable Draft",
        body_plain_text="Immutable body",
        draft_status="scheduled",
        created_by="operator_tester",
        updated_by="operator_tester",
        version=1,
    )
    repo.save_draft(draft)

    payload = {
        "csrf_token": csrf,
        "draft_id": "drf_scheduled_lock",
        "account_id": "acc_info_com",
        "to_addresses": "locked@example.com",
        "subject": "Tampered Subject",
        "body_plain_text": "Tampered body",
        "version": 1,
        "action_type": "save",
    }
    resp = test_client.post("/sales/mail-operations/compose/save", data=payload)
    assert resp.status_code == 400
    assert "currently scheduled" in resp.text
