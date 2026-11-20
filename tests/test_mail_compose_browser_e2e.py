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

        # 1. Test New Draft Composer — Blank slate defaults (MAIL-COMPOSE-01C)
        resp = page.goto(f"{base_url}/sales/mail-operations/compose")
        page.wait_for_timeout(1000)
        print("\nPAGE LOADED URL:", page.url, "HTTP STATUS:", resp.status if resp else "no resp")
        if "#visual-preview-render" not in page.content():
            print("PAGE CONTENT PREVIEW:\n", page.content()[:1000])
        assert len(errors) == 0, f"Unexpected page errors: {errors}"

        # Blank default assertions: to, subject, editable body should all be empty
        assert page.input_value("#to_addresses") == "", "New draft should have empty recipient"
        assert page.input_value("#subject") == "", "New draft should have empty subject"
        assert page.input_value("#standard_body_content") == "", "New draft body textarea should be blank (no 'Dear Architect')"
        rich_initial_text = page.eval_on_selector("#rich_body_editor", "el => el.innerText.trim()")
        assert rich_initial_text == "", "Rich text surface should be blank initially"

        # Sender should default to authorized primary account and signature should be in visual preview
        assert page.input_value("#account_id") == "acc_info_com"
        prev_html = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert len(prev_html) > 500, "Visual preview was blank"
        assert "info@pybim.com" in prev_html
        assert "Empty message body" in prev_html

        # Typing in rich text editor updates hidden textarea and preview in real time
        page.fill("#rich_body_editor", "Real-time browser typed content")
        page.dispatch_event("#rich_body_editor", "input")
        page.wait_for_timeout(300)
        assert page.input_value("#standard_body_content") == "Real-time browser typed content"
        updated_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "Real-time browser typed content" in updated_prev

        # Rich text toolbar buttons test: bold, italic, list
        page.click("button[title='Bold (Ctrl+B)']")
        page.click("button[title='Italic (Ctrl+I)']")
        page.wait_for_timeout(100)

        # Test B2B Template Switching (MAIL-COMPOSE-01C Section B)
        # Select Initial B2B Introduction (English)
        # User already typed text, so changing template triggers confirm prompt
        page.once("dialog", lambda dialog: dialog.accept())
        page.select_option("#template_select", "b2b_intro_en")
        page.dispatch_event("#template_select", "change")
        page.wait_for_timeout(400)

        # Subject and body populated with clean B2B template
        intro_subject = page.input_value("#subject")
        intro_body = page.input_value("#standard_body_content")
        assert "Introduction | pyBIM" in intro_subject
        assert "streamlining modeling workflows" in intro_body
        assert "Dear Architect" not in intro_body
        intro_prev = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "streamlining modeling workflows" in intro_prev
        assert "info@pybim.com" in intro_prev

        # Select Italian B2B Introduction
        page.once("dialog", lambda dialog: dialog.accept())
        page.select_option("#template_select", "b2b_intro_it")
        page.dispatch_event("#template_select", "change")
        page.wait_for_timeout(400)
        intro_it_body = page.input_value("#standard_body_content")
        assert "Gentile Studio" in intro_it_body
        assert "commesse digitali" in intro_it_body

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
        assert page.is_visible("#rich_body_editor")

        # Form submission: Save Draft
        page.fill("#to_addresses", "lead@company.com")
        page.fill("#subject", "E2E Verified Proposal")
        page.fill("#rich_body_editor", "Draft content verified with Playwright.")
        page.dispatch_event("#rich_body_editor", "input")
        page.wait_for_timeout(300)

        page.click("button[value='save']")
        page.wait_for_url("**/sales/mail-operations/compose?draft_id=*", timeout=8000)
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

        # 3. MAIL-COMPOSE-01A-READY-UX-FIX: Test Inline Validation & Mark Ready UX
        page.goto(f"{base_url}/sales/mail-operations/compose")
        page.wait_for_timeout(500)

        # Switch to meeting template
        page.select_option("#template_select", "meeting_en_dark")
        page.dispatch_event("#template_select", "change")
        page.wait_for_timeout(300)

        # Fill recipient and subject, but leave meeting URL empty/placeholder
        page.fill("#to_addresses", "partner@bimstudio.it")
        page.fill("#m_recipient", "Arch. Rossi")
        page.fill("#m_topic", "BIM Technical Coordination")
        page.fill("#m_date", "25 November 2026")
        page.fill("#m_time", "15:00")
        page.fill("#m_link", "")  # empty URL placeholder
        page.dispatch_event("#m_link", "input")
        page.wait_for_timeout(300)

        # Click Mark Ready to Schedule -> MUST NOT navigate away to raw JSON 400
        page.click("#btn-ready-to-schedule")
        page.wait_for_timeout(800)

        # User remains on compose page
        assert "/sales/mail-operations/compose" in page.url
        # Inline error banner is visible with specific placeholder error
        assert page.is_visible("#composer-inline-error"), "Inline error banner should be visible on 400 validation error"
        err_text = page.inner_text("#composer-error-detail")
        assert "unresolved placeholder" in err_text.lower() or "meeting url" in err_text.lower()

        # Input values are preserved
        assert page.input_value("#to_addresses") == "partner@bimstudio.it"
        assert page.input_value("#m_recipient") == "Arch. Rossi"

        # Now complete the meeting URL with a valid link matching platform
        page.fill("#m_link", "https://meet.google.com/abc-defg-hij")
        page.dispatch_event("#m_link", "input")
        page.wait_for_timeout(300)

        # Mark Ready to Schedule -> Now succeeds and transitions status
        page.click("#btn-ready-to-schedule")
        page.wait_for_url("**/sales/mail-operations/compose?draft_id=*", timeout=8000)
        assert "draft_id=" in page.url
        ready_draft_id = page.url.split("draft_id=")[1].split("&")[0]

        ready_rec = repo.get_draft(ready_draft_id)
        assert ready_rec is not None
        assert ready_rec.draft_status == "ready_to_schedule"
        assert "MEETING 01" in (ready_rec.body_html_sanitized or "")

        # 4. Test Meeting Draft Duplication & Reopen Persistence
        page.click("button:has-text('Duplicate')")
        page.wait_for_load_state("networkidle")
        assert "draft_id=" in page.url
        dup_meeting_id = page.url.split("draft_id=")[1].split("&")[0]
        assert dup_meeting_id != ready_draft_id

        # Reload duplicated meeting draft in browser
        page.goto(f"{base_url}/sales/mail-operations/compose?draft_id={dup_meeting_id}")
        page.wait_for_timeout(800)

        # Verify template selector automatically detected and restored meeting template
        selected_tmpl = page.input_value("#template_select")
        assert selected_tmpl.startswith("meeting_"), f"Expected meeting template, got: {selected_tmpl}"
        assert page.is_visible("#meeting-studio-panel")
        assert not page.is_visible("#signature-control-row")

        # Verify meeting fields were parsed and restored
        assert page.input_value("#m_topic") == "BIM Technical Coordination"
        assert page.input_value("#m_recipient") == "Arch. Rossi"
        assert page.input_value("#m_link") == "https://meet.google.com/abc-defg-hij"

        # Verify preview renders meeting design without duplicated signature
        meeting_reloaded_preview = page.eval_on_selector("#visual-preview-render", "el => el.innerHTML")
        assert "MEETING 01" in meeting_reloaded_preview
        # Ensure standard signature table does not appear inside meeting preview
        assert "Engineering &amp; Digital Automation" not in meeting_reloaded_preview

        # 5. MAIL-COMPOSE-01B: Test Schedule Dialog, Reschedule, Cancellation, and Queue UI
        # Open schedule modal on the ready draft
        page.goto(f"{base_url}/sales/mail-operations/compose?draft_id={ready_draft_id}")
        page.wait_for_timeout(500)

        # "Schedule Send" button is visible for ready_to_schedule status
        assert page.is_visible("#btn-open-schedule-modal")
        page.click("#btn-open-schedule-modal")
        page.wait_for_timeout(300)

        assert page.is_visible("#schedule-modal-overlay")
        assert page.inner_text("#modal-summary-sender") == "info@pybim.com"
        assert "partner@bimstudio.it" in page.inner_text("#modal-summary-recipient")

        # Quick preset Tomorrow 09:30
        page.click("button:has-text('Tomorrow 09:30')")
        assert page.input_value("#modal-send-time") == "09:30"

        # Explicit confirmation
        page.check("#modal-confirm-checkbox")

        # Submit schedule
        page.click("#modal-btn-confirm-schedule")
        page.wait_for_url("**/sales/mail-operations/compose?draft_id=*", timeout=8000)
        page.wait_for_timeout(500)

        # Verify status transitioned to SCHEDULED in UI and database
        assert "SCHEDULED" in page.inner_text("#composer-status-badge")
        assert page.is_visible("#btn-cancel-schedule-banner")
        sched_rec = repo.get_draft(ready_draft_id)
        assert sched_rec.draft_status == "scheduled"
        assert sched_rec.scheduled_local_time == "09:30"
        assert sched_rec.scheduled_timezone == "Europe/Rome"
        assert sched_rec.snapshot_subject is not None

        # Verify Drafts & Queue dashboard displays scheduled delivery and actions
        page.goto(f"{base_url}/sales/mail-operations/drafts")
        page.wait_for_timeout(500)
        assert page.is_visible(".badge-status-scheduled")
        assert "09:30" in page.content()
        assert "Reschedule" in page.content()

        # Cancel schedule from composer
        page.goto(f"{base_url}/sales/mail-operations/compose?draft_id={ready_draft_id}")
        page.wait_for_timeout(500)
        page.click("#btn-cancel-schedule-banner")
        page.wait_for_url("**/sales/mail-operations/compose?draft_id=*", timeout=8000)
        page.wait_for_timeout(500)

        assert "READY_TO_SCHEDULE" in page.inner_text("#composer-status-badge")
        canc_rec = repo.get_draft(ready_draft_id)
        assert canc_rec.draft_status == "ready_to_schedule"
        assert canc_rec.scheduled_for_utc is None

        # 6. MAIL-COMPOSE-01B-SCHEDULE-RESPONSE-HOTFIX: Error Handling & Double-Click Prevention
        # Open schedule dialog again
        page.click("#btn-open-schedule-modal")
        page.wait_for_timeout(300)
        assert page.is_visible("#schedule-modal-overlay")

        # Test past date validation feedback (Must display actual server error, NOT 'body stream already read' or generic connection error)
        page.fill("#modal-send-date", "2020-01-01")
        page.fill("#modal-send-time", "10:00")
        page.check("#modal-confirm-checkbox")

        page.click("#modal-btn-confirm-schedule")
        page.wait_for_timeout(600)

        assert page.is_visible("#modal-schedule-error")
        err_msg = page.inner_text("#modal-schedule-error-detail")
        assert "cannot be in the past" in err_msg.lower()
        assert "stream already read" not in err_msg.lower()
        assert "connection error" not in err_msg.lower()

        # Input values are preserved after validation error
        assert page.input_value("#modal-send-date") == "2020-01-01"
        assert page.input_value("#modal-send-time") == "10:00"

        # Verify button re-enabled after error
        btn_el = page.locator("#modal-btn-confirm-schedule")
        assert btn_el.is_enabled()

        # Fix to valid future date/time
        page.click("button:has-text('Tomorrow 10:00')")
        assert page.input_value("#modal-send-time") == "10:00"

        # Test duplicate click prevention (button disables on click)
        page.click("#modal-btn-confirm-schedule")
        # Ensure it transitions smoothly to scheduled without double-stream read error
        page.wait_for_url("**/sales/mail-operations/compose?draft_id=*", timeout=8000)
        page.wait_for_timeout(500)
        assert "SCHEDULED" in page.inner_text("#composer-status-badge")

        browser.close()
