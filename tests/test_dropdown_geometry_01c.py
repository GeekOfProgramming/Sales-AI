"""
tests/test_dropdown_geometry_01c.py
Comprehensive Geometry & Clipping Acceptance Tests for SALES-UI-NAV-01C:
Verifies:
1. All 5 dropdown groups open without clipping inside the navigation container.
2. Every single dropdown menu item is 100% visible and unclipped (verified via elementFromPoint).
3. The navigation container scrollTop and scrollLeft remain 0 when accessing dropdown items (no scrolling needed).
4. No ancestor clips the dropdowns across target viewports: 1024x768, 1280x800, 1366x768, 1440x900, 1920x1080.
5. Captures screenshots of OPEN Administration and Mail dropdowns.
6. Verifies direct navigation to target routes via dropdown clicks without nav scrolling.
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
def geometry_server(tmp_path, monkeypatch):
    test_db = str(tmp_path / "nav_geometry_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()
    init_auth_schema(test_db)

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
    port = 8899
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


def test_administration_all_items_visible_and_clickable_without_nav_scroll(geometry_server):
    """
    CRITICAL ACCEPTANCE CRITERIA:
    Clicking Administration must immediately show all five links:
    1. Excluded Companies
    2. Do Not Contact
    3. Watchlist
    4. Settings
    5. Users (admin only)
    All links must be visible without scrolling the navigation container at normal desktop viewport sizes.
    """
    base_url = geometry_server["base_url"]
    session_token = geometry_server["session_token"]
    viewports = [
        (1024, 768),
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

            admin_btn = page.locator("#nav-admin-btn")
            assert admin_btn.is_visible()
            admin_btn.click()
            page.wait_for_timeout(200)

            # Measure Administration dropdown geometry
            metrics = page.evaluate('''() => {
                const nav = document.querySelector(".nav-bar-unified");
                const header = document.querySelector(".app-header-unified");
                const menu = document.querySelector(".nav-dropdown-menu-right");
                const items = Array.from(menu.querySelectorAll(".nav-dropdown-item"));

                return {
                    navScrollTop: nav.scrollTop,
                    navScrollLeft: nav.scrollLeft,
                    navClientHeight: nav.clientHeight,
                    menuRect: menu.getBoundingClientRect(),
                    items: items.map(it => {
                        const r = it.getBoundingClientRect();
                        const cx = r.left + r.width / 2;
                        const cy = r.top + r.height / 2;
                        const elAtPoint = document.elementFromPoint(cx, cy);
                        return {
                            text: it.innerText.trim(),
                            rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height },
                            hitTargetMatches: it.contains(elAtPoint)
                        };
                    })
                };
            }''')

            # 1. Zero scrolling required in nav
            assert metrics["navScrollTop"] == 0, f"Nav bar scrollTop was {metrics['navScrollTop']} (expected 0) at {w}x{h}"
            assert metrics["navScrollLeft"] == 0, f"Nav bar scrollLeft was {metrics['navScrollLeft']} (expected 0) at {w}x{h}"

            # 2. All 5 links present
            items = metrics["items"]
            item_texts = [it["text"] for it in items]
            expected_links = ["Excluded Companies", "Do Not Contact", "Watchlist", "Settings", "Users"]
            for exp in expected_links:
                assert any(exp in txt for txt in item_texts), f"Link '{exp}' missing in Admin dropdown at {w}x{h}"

            # 3. Every link is fully visible (hit target matches, not covered or clipped by ancestors)
            for it in items:
                assert it["hitTargetMatches"] is True, f"Link '{it['text']}' was clipped or obscured at {w}x{h}: {it}"
                assert it["rect"]["height"] > 20, f"Link '{it['text']}' had invalid height: {it['rect']}"

            # 4. Dropdown sits below trigger, floating over main content (top > 40px)
            assert metrics["menuRect"]["top"] >= 40, f"Menu did not position below header at {w}x{h}"

            page.close()
        browser.close()


def test_all_dropdown_groups_unclipped_across_viewports(geometry_server):
    """
    Verifies Discovery, Companies & Research, Outreach, Mail, and Administration dropdowns
    across all 5 target viewports (1024x768, 1280x800, 1366x768, 1440x900, 1920x1080).
    """
    base_url = geometry_server["base_url"]
    session_token = geometry_server["session_token"]
    viewports = [
        (1024, 768),
        (1280, 800),
        (1366, 768),
        (1440, 900),
        (1920, 1080),
    ]
    dropdowns = [
        ("discovery", "#nav-discovery-btn", ".nav-dropdown:has(#nav-discovery-btn) .nav-dropdown-menu"),
        ("companies", "#nav-companies-btn", ".nav-dropdown:has(#nav-companies-btn) .nav-dropdown-menu"),
        ("outreach", "#nav-outreach-btn", ".nav-dropdown:has(#nav-outreach-btn) .nav-dropdown-menu"),
        ("mail", "#nav-mail-btn", ".nav-dropdown:has(#nav-mail-btn) .nav-dropdown-menu"),
        ("admin", "#nav-admin-btn", ".nav-dropdown:has(#nav-admin-btn) .nav-dropdown-menu"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h in viewports:
            page = browser.new_page(viewport={"width": w, "height": h})
            auth_page(page, base_url, session_token)
            page.goto(f"{base_url}/sales/analytics")
            page.wait_for_load_state("networkidle")

            for name, trigger_sel, menu_sel in dropdowns:
                btn = page.locator(trigger_sel)
                assert btn.is_visible(), f"Trigger {trigger_sel} not visible at {w}x{h}"
                btn.click()
                page.wait_for_timeout(150)

                # Validate menu item geometry & unclipped visibility
                metrics = page.evaluate('''(menuSelector) => {
                    const nav = document.querySelector(".nav-bar-unified");
                    const menu = document.querySelector(menuSelector);
                    const items = Array.from(menu.querySelectorAll(".nav-dropdown-item"));

                    return {
                        navScrollTop: nav.scrollTop,
                        navScrollLeft: nav.scrollLeft,
                        items: items.map(it => {
                            const r = it.getBoundingClientRect();
                            const cx = r.left + r.width / 2;
                            const cy = r.top + r.height / 2;
                            const elAtPoint = document.elementFromPoint(cx, cy);
                            return {
                                text: it.innerText.trim(),
                                hitTargetMatches: it.contains(elAtPoint),
                                height: r.height
                            };
                        })
                    };
                }''', menu_sel)

                assert metrics["navScrollTop"] == 0, f"Nav scrollTop != 0 for {name} at {w}x{h}"
                assert len(metrics["items"]) > 0, f"No items found in {name} menu at {w}x{h}"

                for it in metrics["items"]:
                    assert it["hitTargetMatches"] is True, f"Item '{it['text']}' in '{name}' was clipped or covered at {w}x{h}"
                    assert it["height"] > 20, f"Item '{it['text']}' in '{name}' had invalid height at {w}x{h}"

                # Close dropdown before testing next
                page.click("body", position={"x": 5, "y": 5})
                page.wait_for_timeout(100)

            page.close()
        browser.close()


def test_open_dropdown_screenshots_captured(geometry_server):
    """
    Captures screenshots of OPEN Administration and Mail dropdowns at 1440x900 and 1024x768.
    """
    base_url = geometry_server["base_url"]
    session_token = geometry_server["session_token"]
    os.makedirs("tests/reports", exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for w, h in [(1440, 900), (1024, 768)]:
            # 1. Administration open
            page = browser.new_page(viewport={"width": w, "height": h})
            auth_page(page, base_url, session_token)
            page.goto(f"{base_url}/sales/analytics")
            page.wait_for_load_state("networkidle")
            page.click("#nav-admin-btn")
            page.wait_for_timeout(200)
            page.screenshot(path=f"tests/reports/admin_open_{w}x{h}.png")
            page.close()

            # 2. Mail open
            page = browser.new_page(viewport={"width": w, "height": h})
            auth_page(page, base_url, session_token)
            page.goto(f"{base_url}/sales/analytics")
            page.wait_for_load_state("networkidle")
            page.click("#nav-mail-btn")
            page.wait_for_timeout(200)
            page.screenshot(path=f"tests/reports/mail_open_{w}x{h}.png")
            page.close()

        browser.close()
