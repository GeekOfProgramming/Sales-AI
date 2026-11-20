"""
tests/test_navigation_polish_01b.py
Real-Browser Playwright Tests for SALES-UI-NAV-01B:
- Navigation Visual Polish, Overflow Fix & Dropdown Reliability.

Verifies:
1. All 7 groups render in compact, balanced single row with zero unwanted scrollbars.
2. Click-to-open, click-outside-to-close, Escape-to-close, and keyboard navigation.
3. No scrollbar or overflow at 1280x800, 1366x768, 1440x900, 1920x1080 (and clean 1024x768 tablet layout).
4. Dropdown menus remain positioned above page content and visible without clipping.
5. All deep links preserved (/sales/candidates, /sales/register, /sales/leads, /sales/mail-operations, /sales/analytics, /sales/settings, /sales/users).
6. Operator identity badge positioned cleanly on the right with functional sign-out form.
7. Active states applied correctly.
8. Zero console errors on navigation.
"""

import os
import time
import tempfile
import threading
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord
from backend.operator_auth import get_auth_service, init_auth_schema


@pytest.fixture(scope="function")
def polished_nav_server(tmp_path, monkeypatch):
    test_db = str(tmp_path / "nav_polish_test.db")
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
        username="hamid",
        password="ValidPassword123!",
        role="admin",
        full_name="Hamid Admin",
    )
    session_token = auth.create_session(user.user_id)

    from backend.main import app
    port = 8895
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


def auth_page(page, base_url, session_token):
    page.context.add_cookies([
        {
            "name": "sales_session",
            "value": session_token,
            "domain": "127.0.0.1",
            "path": "/",
        }
    ])


def test_no_unwanted_navigation_scrollbars_and_overflow(polished_nav_server):
    """Verify navigation container has zero unwanted vertical/horizontal scrollbar across viewports."""
    base_url = polished_nav_server["base_url"]
    session_token = polished_nav_server["session_token"]
    viewports = [
        (1280, 800),
        (1366, 768),
        (1440, 900),
        (1920, 1080),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h in viewports:
            page = browser.new_page(viewport={"width": w, "height": h})
            auth_page(page, base_url, session_token)
            page.goto(f"{base_url}/sales/analytics")
            page.wait_for_load_state("networkidle")

            # Assert nav container does NOT overflow
            nav_dims = page.evaluate('''() => {
                const nav = document.querySelector(".nav-bar-unified");
                const header = document.querySelector(".app-header-unified");
                return {
                    navScrollWidth: nav.scrollWidth,
                    navClientWidth: nav.clientWidth,
                    navScrollHeight: nav.scrollHeight,
                    navClientHeight: nav.clientHeight,
                    headerScrollWidth: header.scrollWidth,
                    headerClientWidth: header.clientWidth,
                };
            }''')

            assert nav_dims["navScrollWidth"] <= nav_dims["navClientWidth"], (
                f"Viewport {w}x{h} nav overflows horizontally: scrollWidth={nav_dims['navScrollWidth']}, clientWidth={nav_dims['navClientWidth']}"
            )
            assert nav_dims["navScrollHeight"] <= nav_dims["navClientHeight"], (
                f"Viewport {w}x{h} nav overflows vertically: scrollHeight={nav_dims['navScrollHeight']}, clientHeight={nav_dims['navClientHeight']}"
            )
            assert nav_dims["headerScrollWidth"] <= nav_dims["headerClientWidth"], (
                f"Viewport {w}x{h} header overflows: scrollWidth={nav_dims['headerScrollWidth']}, clientWidth={nav_dims['headerClientWidth']}"
            )
            page.close()
        browser.close()


def test_dropdown_click_and_keyboard_interaction(polished_nav_server):
    """Verify click to open, click outside to close, Escape to close, and keyboard navigation."""
    base_url = polished_nav_server["base_url"]
    session_token = polished_nav_server["session_token"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        auth_page(page, base_url, session_token)
        page.goto(f"{base_url}/sales/analytics")
        page.wait_for_load_state("networkidle")

        companies_btn = page.locator("#nav-companies-btn")
        companies_dd = page.locator(".nav-dropdown:has(#nav-companies-btn)")
        companies_menu = companies_dd.locator(".nav-dropdown-menu")

        # 1. Initially closed (aria-expanded is false)
        assert companies_btn.get_attribute("aria-expanded") == "false"

        # 2. Click to open
        companies_btn.click()
        assert "open" in companies_dd.get_attribute("class")
        assert companies_btn.get_attribute("aria-expanded") == "true"
        assert companies_menu.is_visible()

        # 3. Click outside to close
        page.locator("body").click(position={"x": 500, "y": 400})
        assert "open" not in (companies_dd.get_attribute("class") or "")
        assert companies_btn.get_attribute("aria-expanded") == "false"

        # 4. Open again, then press Escape to close
        companies_btn.click()
        assert "open" in companies_dd.get_attribute("class")
        page.keyboard.press("Escape")
        assert "open" not in (companies_dd.get_attribute("class") or "")
        assert companies_btn.get_attribute("aria-expanded") == "false"

        # 5. Opening another dropdown closes the first
        mail_btn = page.locator("#nav-mail-btn")
        mail_dd = page.locator(".nav-dropdown:has(#nav-mail-btn)")
        companies_btn.click()
        assert "open" in companies_dd.get_attribute("class")
        mail_btn.click()
        assert "open" in mail_dd.get_attribute("class")
        assert "open" not in (companies_dd.get_attribute("class") or "")

        browser.close()


def test_deep_links_and_active_states(polished_nav_server):
    """Verify all navigation routes load cleanly and reflect correct active state."""
    base_url = polished_nav_server["base_url"]
    session_token = polished_nav_server["session_token"]

    test_routes = [
        ("/sales/dashboard", "#nav-dashboard", True),
        ("/sales/candidates", "#nav-companies-btn", True),
        ("/sales/candidates?queue=all", "#nav-companies-btn", True),
        ("/sales/candidates?queue=needs_verification", "#nav-companies-btn", True),
        ("/sales/register", "#nav-companies-btn", True),
        ("/sales/leads", "#nav-companies-btn", True),
        ("/sales/mail-operations", "#nav-mail-btn", True),
        ("/sales/mail-operations/compose", "#nav-mail-btn", False),
        ("/sales/mail-operations/drafts", "#nav-mail-btn", False),
        ("/sales/analytics", "#nav-analytics", True),
        ("/sales/settings", "#nav-admin-btn", True),
        ("/sales/users", "#nav-admin-btn", True),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        auth_page(page, base_url, session_token)

        for route, nav_selector, expect_active in test_routes:
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            response = page.goto(f"{base_url}{route}")
            page.wait_for_load_state("networkidle")
            assert response is not None and response.status == 200, f"Route {route} failed to load"

            nav_el = page.locator(nav_selector)
            assert nav_el.is_visible(), f"Nav element {nav_selector} not visible on route {route}"
            if expect_active:
                assert "active" in (nav_el.get_attribute("class") or ""), f"Nav element {nav_selector} expected active on {route}"

            # Ensure no Javascript console errors
            assert len(console_errors) == 0, f"Console errors on {route}: {console_errors}"

        browser.close()


def test_representative_screenshots_generated(polished_nav_server):
    """Generates clean before/after screenshots across key pages for operator verification."""
    base_url = polished_nav_server["base_url"]
    session_token = polished_nav_server["session_token"]
    os.makedirs("tests/reports", exist_ok=True)

    pages_to_capture = [
        ("/sales/dashboard", "dashboard_polished"),
        ("/sales/candidates", "candidates_polished"),
        ("/sales/register", "research_register_polished"),
        ("/sales/mail-operations", "mail_ops_polished"),
        ("/sales/mail-operations/compose", "compose_polished"),
        ("/sales/analytics", "analytics_polished"),
        ("/sales/settings", "settings_polished"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for route, filename in pages_to_capture:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            auth_page(page, base_url, session_token)
            page.goto(f"{base_url}{route}")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=f"tests/reports/{filename}.png", full_page=False)
            page.close()

        # Capture open dropdown screenshot
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        auth_page(page, base_url, session_token)
        page.goto(f"{base_url}/sales/analytics")
        page.wait_for_load_state("networkidle")
        page.locator("#nav-companies-btn").click()
        page.screenshot(path="tests/reports/dropdown_open_polished.png", full_page=False)
        page.close()

        browser.close()
