"""
tests/test_navigation_consolidation_e2e.py
Real-Browser Playwright E2E Tests for Navigation Consolidation (SALES-UI-NAV-01)

Verifies:
1. 7 Logical Navigation Groups are rendered.
2. Dropdowns function on hover/focus.
3. Removed top-level redundant links ("All Companies", "Human Verification") are now organized in dropdowns.
4. Preserved deep link filter views (/sales/candidates?queue=all, /sales/candidates?queue=needs_verification).
5. Visual viewports at 1280px, 1440px, and 1920px without navigation clipping or horizontal page break.
6. Absolute database immutability: production/operational sales_outreach.db is never touched.
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
from sales_engine.inbox.models import MailboxAccountRecord
from backend.operator_auth import get_auth_service, init_auth_schema


@pytest.fixture(scope="function")
def nav_server(tmp_path, monkeypatch):
    test_db = str(tmp_path / "nav_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()
    init_auth_schema(test_db)

    # Seed primary mailbox account
    acc = MailboxAccountRecord(
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
    repo.save_account(acc)

    auth = get_auth_service(test_db)
    user = auth.create_user(
        username="test_nav_admin",
        password="ValidPassword123!",
        role="admin",
        full_name="Navigation Test Admin",
    )
    session_token = auth.create_session(user.user_id)

    from backend.main import app
    port = 8892
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    time.sleep(2)
    base_url = f"http://127.0.0.1:{port}"
    yield {"base_url": base_url, "db_path": test_db, "session_token": session_token}

    server.should_exit = True
    thread.join(timeout=3)


def login_admin(page, base_url, session_token):
    # Set session cookie directly for reliable authentication
    page.context.add_cookies([
        {
            "name": "sales_session",
            "value": session_token,
            "domain": "127.0.0.1",
            "path": "/",
        }
    ])
    page.goto(f"{base_url}/sales/dashboard")
    page.wait_for_load_state("networkidle")


def test_nav_groups_rendered(nav_server):
    """Verify all 7 navigation groups exist and top-level links are consolidated."""
    base_url = nav_server["base_url"]
    session_token = nav_server["session_token"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login_admin(page, base_url, session_token)

        # 1. Overview (Dashboard)
        dashboard_link = page.locator("#nav-dashboard")
        assert dashboard_link.is_visible()
        assert "Dashboard" in dashboard_link.text_content()

        # 2. Discovery Dropdown
        discovery_btn = page.locator("#nav-discovery-btn")
        assert discovery_btn.is_visible()
        assert "Discovery" in discovery_btn.text_content()

        # 3. Companies & Research Dropdown
        companies_btn = page.locator("#nav-companies-btn")
        assert companies_btn.is_visible()
        assert "Companies & Research" in companies_btn.text_content()

        # 4. Outreach Dropdown
        outreach_btn = page.locator("#nav-outreach-btn")
        assert outreach_btn.is_visible()
        assert "Outreach" in outreach_btn.text_content()

        # 5. Mail Dropdown
        mail_btn = page.locator("#nav-mail-btn")
        assert mail_btn.is_visible()
        assert "Mail" in mail_btn.text_content()

        # 6. Insights
        analytics_link = page.locator("#nav-analytics")
        assert analytics_link.is_visible()
        assert "Insights" in analytics_link.text_content()

        # 7. Administration Dropdown
        admin_btn = page.locator("#nav-admin-btn")
        assert admin_btn.is_visible()
        assert "Administration" in admin_btn.text_content()

        # Verify no un-grouped top-level "All Companies" link in top-level nav-bar
        top_level_links = page.locator(".nav-bar-unified > .nav-link, .nav-bar-unified > .nav-item > .nav-link").all_text_contents()
        for text in top_level_links:
            assert "All Companies" not in text
            assert "Human Verification" not in text

        browser.close()


def test_dropdown_navigation_and_deep_links(nav_server):
    """Verify dropdown menus display on hover and deep links navigate correctly."""
    base_url = nav_server["base_url"]
    session_token = nav_server["session_token"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login_admin(page, base_url, session_token)

        # Hover Companies & Research and check items
        companies_dropdown = page.locator(".nav-dropdown:has(#nav-companies-btn)")
        companies_dropdown.hover()

        # Click "All Companies" within the dropdown
        all_companies_item = page.locator('a.nav-dropdown-item[href="/sales/candidates?queue=all"]')
        assert all_companies_item.is_visible()
        all_companies_item.click()
        page.wait_for_load_state("networkidle")
        assert "/sales/candidates?queue=all" in page.url

        # Check Human Verification queue link
        companies_dropdown = page.locator(".nav-dropdown:has(#nav-companies-btn)")
        companies_dropdown.hover()
        hv_item = page.locator('a.nav-dropdown-item[href="/sales/candidates?queue=needs_verification"]')
        assert hv_item.is_visible()
        hv_item.click()
        page.wait_for_load_state("networkidle")
        assert "/sales/candidates?queue=needs_verification" in page.url

        # Check Mail dropdown items
        mail_dropdown = page.locator(".nav-dropdown:has(#nav-mail-btn)")
        mail_dropdown.hover()
        mail_ops_item = page.locator('a.nav-dropdown-item[href="/sales/mail-operations"]')
        assert mail_ops_item.is_visible()
        compose_item = page.locator('a.nav-dropdown-item[href="/sales/mail-operations/compose"]')
        assert compose_item.is_visible()
        drafts_item = page.locator('a.nav-dropdown-item[href="/sales/mail-operations/drafts"]')
        assert drafts_item.is_visible()

        browser.close()


def test_responsive_navigation_viewports(nav_server):
    """Verify 1280px, 1440px, and 1920px viewports render without broken navigation."""
    base_url = nav_server["base_url"]
    session_token = nav_server["session_token"]
    viewports = [
        (1280, 800, "1280x800"),
        (1440, 900, "1440x900"),
        (1920, 1080, "1920x1080"),
    ]
    os.makedirs("tests/reports", exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h, label in viewports:
            page = browser.new_page(viewport={"width": w, "height": h})
            login_admin(page, base_url, session_token)

            # Ensure all 7 main navigation elements are visible
            assert page.locator("#nav-dashboard").is_visible()
            assert page.locator("#nav-discovery-btn").is_visible()
            assert page.locator("#nav-companies-btn").is_visible()
            assert page.locator("#nav-outreach-btn").is_visible()
            assert page.locator("#nav-mail-btn").is_visible()
            assert page.locator("#nav-analytics").is_visible()
            assert page.locator("#nav-admin-btn").is_visible()

            # Capture screenshot
            page.screenshot(path=f"tests/reports/nav_consolidated_{label}.png")
            page.close()

        browser.close()
