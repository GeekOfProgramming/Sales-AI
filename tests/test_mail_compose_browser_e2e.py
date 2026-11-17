"""
tests/test_mail_compose_browser_e2e.py
Real-Browser Playwright E2E Tests for Email Composer Interactivity (MAIL-COMPOSE-01A-JS-RUNTIME-HOTFIX)
"""

import os
import shutil
import tempfile
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord, MailboxDraftRecord
from backend.operator_auth import get_auth_service, init_auth_schema


@pytest.fixture(scope="function")
def browser_server(tmp_path, monkeypatch):
    test_db = str(tmp_path / "browser_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()
    init_auth_schema(test_db)

    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Global",
        account_role="primary_sales",
        imap_host="imap.test.com",
        imap_port=993,
        smtp_host="smtp.test.com",
        smtp_port=465,
        credential_ref="cred_info_com",
        is_active=True,
    )
    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Italia",
        account_role="primary_sales",
        imap_host="imap.test.com",
        imap_port=993,
        smtp_host="smtp.test.com",
        smtp_port=465,
        credential_ref="cred_info_it",
        is_active=True,
    )
    repo.save_account(acc_com)
    repo.save_account(acc_it)

    # Seed operator-reported draft scenario
    drf_op = MailboxDraftRecord(
        draft_id="drf_a05943967b84",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["client@studio.it"],
        subject="Operator Test Draft",
        body_plain_text="Hello operator test draft\r\n--\r\npyBIM Team\r\ninfo@pybim.com\r\nhttps://pybim.com",
        draft_status="draft",
        created_by="operator_test",
        updated_by="operator_test",
        version=1,
    )
    repo.save_draft(drf_op)

    auth = get_auth_service(test_db)
    user = auth.create_user("playwright_op", "PlayPass123!", "operator", "Playwright Operator")
    session_token = auth.create_session(user.user_id)

    from backend.main import app
    port = 8991
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(2)

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "session_token": session_token,
        "repo": repo,
        "db_path": test_db,
    }

    server.should_exit = True


def test_composer_browser_interactions_and_regression(browser_server):
    """
    End-to-End Real Browser Verification:
    1. Zero JavaScript/Console syntax errors on page load.
    2. Visual preview rendering works immediately.
    3. Template switching (Standard <-> Meeting templates).
    4. Platform selector changes CTA button and placeholder text.
    5. Draft Save, Reload, Duplicate, and Discard actions function cleanly.
    6. No duplicate signature accumulation on reload.
    """
    base_url = browser_server["base_url"]
    session_token = browser_server["session_token"]
    repo = browser_server["repo"]

    errors = []
    console_msgs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies([{
            "name": "sales_session",
            "value": session_token,
            "domain": "127.0.0.1",
            "path": "/",
        }])
        page = context.new_page()
        page.on("console", lambda msg: console_msgs.append(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: errors.append(str(err)))

        # 1. Test New Draft Composer
        resp = page.goto(f"{base_url}/sales/mail-operations/compose")
        page.wait_for_timeout(1000)
        print("\nPAGE LOADED URL:", page.url, "HTTP STATUS:", resp.status if resp else "no resp")
        if "#visual-preview-render" not in page.content():
            print("PAGE CONTENT PREVIEW:\n", page.content()[:1000])
        assert len(errors) == 0, f"Unexpected page errors: {errors}"

        # Visual preview should be populated
        prev_html = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert len(prev_html) > 500, "Visual preview was blank"
        assert "info@pybim.com" in prev_html

        # Typing in message content updates visual preview in real time
        page.fill("#standard_body_content", "Real-time browser typed content")
        page.wait_for_timeout(300)
        updated_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "Real-time browser typed content" in updated_prev

        # Tab switching
        page.click("#tab-btn-plain")
        assert page.is_visible("#tab-pane-plain")
        assert not page.is_visible("#tab-pane-visual")

        page.click("#tab-btn-html")
        assert page.is_visible("#tab-pane-html")
        assert not page.is_visible("#tab-pane-visual")

        page.click("#tab-btn-visual")
        assert page.is_visible("#tab-pane-visual")

        # Toggle CC / BCC
        assert not page.is_visible("#extra-recipients-box")
        page.click("button[onclick='toggleExtraRecipients()']")
        assert page.is_visible("#extra-recipients-box")

        # Meeting template selection
        page.select_option("#template_select", "meeting_en_dark")
        page.dispatch_event("#template_select", "change")
        page.wait_for_timeout(400)
        assert page.is_visible("#meeting-studio-panel")
        assert not page.is_visible("#signature-control-row")

        meeting_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "MEETING 01" in meeting_prev

        # Meeting Platform selector: MS Teams
        page.select_option("#m_platform", "ms_teams")
        page.dispatch_event("#m_platform", "change")
        page.wait_for_timeout(300)
        teams_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "JOIN MICROSOFT TEAMS MEETING" in teams_prev
        assert "Microsoft Teams" in teams_prev

        # Meeting Platform selector: Zoom
        page.select_option("#m_platform", "zoom")
        page.dispatch_event("#m_platform", "change")
        page.wait_for_timeout(300)
        zoom_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "JOIN ZOOM MEETING" in zoom_prev

        # Meeting Platform selector: Custom
        page.select_option("#m_platform", "custom")
        page.dispatch_event("#m_platform", "change")
        page.wait_for_timeout(300)
        assert page.is_visible("#m_custom_platform_row")
        page.fill("#m_custom_platform_name", "Webex")
        page.wait_for_timeout(300)
        custom_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "JOIN WEBEX MEETING" in custom_prev

        # Switch back to Standard Email
        page.select_option("#template_select", "standard")
        page.dispatch_event("#template_select", "change")
        page.wait_for_timeout(300)
        assert not page.is_visible("#meeting-studio-panel")
        assert page.is_visible("#standard_body_content")

        # Form submission: Save Draft
        page.fill("#to_addresses", "lead@company.com")
        page.fill("#subject", "E2E Verified Proposal")
        page.fill("#standard_body_content", "Draft content verified with Playwright.")
        page.wait_for_timeout(300)

        page.click("button[value='save']")
        page.wait_for_load_state("networkidle")
        assert "draft_id=" in page.url
        new_draft_id = page.url.split("draft_id=")[1].split("&")[0]

        # Verify draft in SQLite
        db_draft = repo.get_draft(new_draft_id)
        assert db_draft is not None
        assert "lead@company.com" in db_draft.to_addresses
        assert db_draft.subject == "E2E Verified Proposal"

        # Check that reloaded editor has clean user content without duplicate signature
        reloaded_body = page.input_value("#standard_body_content")
        assert reloaded_body == "Draft content verified with Playwright."

        # Duplicate draft
        page.click("button:has-text('Duplicate')")
        page.wait_for_load_state("networkidle")
        assert "draft_id=" in page.url
        dup_draft_id = page.url.split("draft_id=")[1].split("&")[0]
        assert dup_draft_id != new_draft_id

        # Discard draft
        page.on("dialog", lambda dialog: dialog.accept())
        page.click("button:has-text('Discard')")
        page.wait_for_load_state("networkidle")
        assert "/sales/mail-operations/drafts" in page.url

        discarded_record = repo.get_draft(dup_draft_id)
        assert discarded_record.draft_status == "discarded"

        # 2. Test the specific operator-reported draft: drf_a05943967b84
        page.goto(f"{base_url}/sales/mail-operations/compose?draft_id=drf_a05943967b84")
        page.wait_for_timeout(1000)
        assert len(errors) == 0, f"Page errors loading drf_a05943967b84: {errors}"

        op_prev_html = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert len(op_prev_html) > 500, "Operator draft preview is blank"
        assert "Hello operator test draft" in op_prev_html

        # Cancel navigation
        page.click("a:has-text('Cancel')")
        page.wait_for_load_state("networkidle")
        assert "/sales/mail-operations/drafts" in page.url

        browser.close()
