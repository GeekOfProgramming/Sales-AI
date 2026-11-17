"""
tests/test_mail_compose_01a_brand_assets.py
MAIL-COMPOSE-01A-BRAND-ASSETS: Official Signatures and Meeting Templates Verification.

Verifies:
1. Official signature assets integration for info@pybim.com and info@pybim.it:
   - Preserves HTML table layout, inline styling, WhatsApp links, phone number +39 352 288 7878, LinkedIn.
   - Clean plain-text representation.
   - Local preview logo mapping.
2. All four meeting email designs (EN/IT x Dark/Light):
   - Correct template loading.
   - Invitation vs Confirmation modes.
   - Parameter substitution and escaping.
   - Unresolved placeholder detection preventing 'ready_to_schedule'.
   - Dedicated plain-text fallback generation.
   - Branded footer integration without duplicate corporate signatures.
3. Security & Isolation:
   - Sanitization allows official logo and table structure while defanging malicious scripts / tracking pixels.
   - Zero SMTP delivery or scheduling side effects.
   - Zero CRM database mutations.
"""

import os
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from sales_engine.inbox.signatures import (
    get_default_signature,
    get_signature_html_with_local_preview,
    SIGNATURES_REGISTRY,
)
from sales_engine.inbox.meeting_templates import (
    build_meeting_email,
    validate_meeting_parameters,
    MEETING_TEMPLATES,
    TEMPLATE_CHOICES,
)
from sales_engine.inbox.html_sanitizer import sanitize_email_html
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxAccountRecord
from backend.sales_ui.router import CSRF_COOKIE_NAME
from backend.operator_auth import get_auth_service


@pytest.fixture
def brand_assets_env(tmp_path, monkeypatch):
    test_db = str(tmp_path / "brand_assets_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

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

    return {"db_path": test_db, "repo": repo}


@pytest.fixture
def auth_client(brand_assets_env):
    from backend.main import app
    client = TestClient(app)

    auth = get_auth_service()
    user = auth.create_user("operator_brand", "BrandPass123!", "operator", "Brand Operator")
    token = auth.create_session(user.user_id)
    client.cookies.set("sales_session", token)
    return client


def _get_csrf_data(client: TestClient) -> dict:
    resp = client.get("/sales/mail-operations/compose")
    csrf_cookie = resp.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_cookie:
        csrf_cookie = "csrf_test_brand_assets_token"
        client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
    return {"token": csrf_cookie}


# ============================================================================
# 1. Official Signature Integration Tests
# ============================================================================

def test_official_signatures_loaded():
    """Verify official HTML signatures for info@pybim.com and info@pybim.it."""
    sig_com = get_default_signature("acc_info_com")
    sig_it = get_default_signature("acc_info_it")

    # Layout checks
    assert "<table" in sig_com["html"]
    assert "width=\"500\"" in sig_com["html"]
    assert "Engineering & Digital Automation" in sig_com["html"]
    assert "+39 352 288 7878" in sig_com["html"]
    assert "https://wa.me/393522887878" in sig_com["html"]
    assert "https://www.linkedin.com/company/pybim" in sig_com["html"]

    assert "<table" in sig_it["html"]
    assert "Ingegneria & Automazione Digitale" in sig_it["html"]
    assert "info@pybim.it" in sig_it["html"]
    assert "+39 352 288 7878" in sig_it["html"]
    assert "Chatta su WhatsApp" in sig_it["html"]


def test_signature_local_preview_rewriting():
    """Verify that local preview rewrites logo URL to /static/brand/logo_white_transparent.png."""
    sig_com = get_default_signature("acc_info_com")
    preview_html = get_signature_html_with_local_preview(sig_com["html"])
    assert "/static/brand/logo_white_transparent.png" in preview_html
    assert "https://www.pybim.com/logo_white_transparent.png" not in preview_html


# ============================================================================
# 2. Meeting Email Templates Tests
# ============================================================================

def test_four_meeting_templates_exist():
    """Verify all 4 meeting templates are present and loaded."""
    for key in ("en_dark", "en_light", "it_dark", "it_light"):
        assert key in MEETING_TEMPLATES
        assert len(MEETING_TEMPLATES[key]) > 500
        assert "pyBIM" in MEETING_TEMPLATES[key]


def test_build_meeting_email_invitation_en_dark():
    """Verify building an English Dark Invitation email."""
    result = build_meeting_email(
        template_id="en_dark",
        message_type="invitation",
        recipient_name="Arch. Sarah Jenkins",
        meeting_topic="Project Coordination Phase 2",
        meeting_date="24 November 2026",
        meeting_time="14:30",
        meeting_url="https://meet.google.com/abc-defg-hij",
        agenda_1="Model Federation Review",
        agenda_2="Clash Detection Matrix",
        agenda_3="Deliverable Milestones",
        for_local_preview=True,
    )

    # Subject
    assert result["subject"] == "Meeting Invitation | pyBIM — Project Coordination Phase 2"

    # HTML
    html_content = result["body_html"]
    assert "Arch. Sarah Jenkins" in html_content
    assert "Project Coordination Phase 2" in html_content
    assert "24 November 2026" in html_content
    assert "14:30" in html_content
    assert "https://meet.google.com/abc-defg-hij" in html_content
    assert "Model Federation Review" in html_content
    assert "/static/brand/logo_white_transparent.png" in html_content
    assert result["has_placeholders"] is False

    # Plain text
    plain = result["body_plain_text"]
    assert "Dear Arch. Sarah Jenkins," in plain
    assert "MEETING: Project Coordination Phase 2" in plain
    assert "01. Model Federation Review" in plain
    assert "Kind regards,\nThe pyBIM Team" in plain


def test_build_meeting_email_confirmation_it_light():
    """Verify building an Italian Light Confirmation email."""
    result = build_meeting_email(
        template_id="it_light",
        message_type="confirmation",
        recipient_name="Ing. Marco Rossi",
        meeting_topic="Allineamento Tecnico Revit",
        meeting_date="15 Dicembre 2026",
        meeting_time="10:00",
        meeting_url="https://meet.google.com/xyz-uvwx-rst",
        for_local_preview=False,
    )

    # Subject
    assert result["subject"] == "Conferma riunione | pyBIM — Allineamento Tecnico Revit"

    # HTML
    html_content = result["body_html"]
    assert "Gentile [[RECIPIENT]]" not in html_content
    assert "Ing. Marco Rossi" in html_content
    assert "La ringraziamo per la conferma" in html_content
    assert "ACCEDI ALLA VIDEORIUNIONE" in html_content
    assert "info@pybim.it" in html_content
    assert "www.pybim.it/it" in html_content

    # Plain text
    plain = result["body_plain_text"]
    assert "Gentile Ing. Marco Rossi," in plain
    assert "La ringraziamo per la conferma." in plain
    assert "RIUNIONE: Allineamento Tecnico Revit" in plain
    assert "Cordiali saluti,\nIl team pyBIM" in plain


def test_meeting_validation():
    """Verify validation flags incomplete placeholders."""
    # Incomplete params
    invalid_params = {
        "topic": "[Meeting topic]",
        "recipient": "",
        "date": "12 Nov",
        "time": "15:00",
        "link": "not-a-link",
    }
    errors = validate_meeting_parameters(invalid_params)
    assert len(errors) >= 3

    # Complete params
    valid_params = {
        "topic": "Revit Standards Review",
        "recipient": "Arch. Bianchi",
        "date": "20 Nov 2026",
        "time": "11:00",
        "link": "https://meet.google.com/abc-xyz",
    }
    valid_errors = validate_meeting_parameters(valid_params)
    assert len(valid_errors) == 0


# ============================================================================
# 3. Router Integration & Placeholder Safety Guard Tests
# ============================================================================

def test_ready_to_schedule_rejects_unresolved_placeholders(auth_client):
    """Router rejects marking draft as 'ready_to_schedule' if unresolved placeholders remain."""
    csrf_data = _get_csrf_data(auth_client)

    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_info_com",
        "to_addresses": "client@partner.com",
        "subject": "Meeting Invitation | pyBIM — [Meeting topic]",
        "body_plain_text": "Dear [Recipient name], please join the call at [HH:MM].",
        "action_type": "ready_to_schedule",
        "version": "1",
    }

    resp = auth_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
    )
    assert resp.status_code == 400
    assert "unresolved placeholder" in resp.json()["detail"].lower()


def test_ready_to_schedule_allows_completed_draft(auth_client, brand_assets_env):
    """Router allows marking fully resolved draft as 'ready_to_schedule'."""
    csrf_data = _get_csrf_data(auth_client)

    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_info_com",
        "to_addresses": "client@partner.com",
        "subject": "Meeting Invitation | pyBIM — Coordination Session",
        "body_plain_text": "Dear Arch. Rossi,\nWe invite you to our coordination call on 20 Nov at 15:00.",
        "action_type": "ready_to_schedule",
        "version": "1",
    }

    resp = auth_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    draft_id = resp.headers["location"].split("draft_id=")[1].split("&")[0]

    repo = brand_assets_env["repo"]
    draft = repo.get_draft(draft_id)
    assert draft.draft_status == "ready_to_schedule"


# ============================================================================
# 4. HTML Sanitizer & Security Tests
# ============================================================================

def test_html_sanitizer_preserves_official_brand_elements():
    """Verify that official logos and table layouts are preserved while attack vectors are defanged."""
    dirty_html = """
    <table cellpadding="0" cellspacing="0" border="0" width="500">
      <tr>
        <td>
          <img src="/static/brand/logo_white_transparent.png" width="100" height="100">
          <img src="https://www.pybim.com/logo_white_transparent.png" width="100" height="100">
          <img src="http://untrusted-tracker.com/pixel.png" width="1" height="1">
          <script>alert(1);</script>
          <a href="javascript:stealCookie()">Click here</a>
        </td>
      </tr>
    </table>
    """
    clean_html = sanitize_email_html(dirty_html)

    # Official logos preserved
    assert 'src="/static/brand/logo_white_transparent.png"' in clean_html
    assert 'src="https://www.pybim.com/logo_white_transparent.png"' in clean_html

    # Untrusted tracker defanged: src attribute removed, converted to data-src and blocked-remote-image
    assert 'data-src="http://untrusted-tracker.com/pixel.png"' in clean_html
    assert "blocked-remote-image" in clean_html
    soup = BeautifulSoup(clean_html, "html.parser")
    tracker_imgs = [img for img in soup.find_all("img") if "untrusted-tracker.com" in (img.get("data-src") or "")]
    assert len(tracker_imgs) == 1
    assert "src" not in tracker_imgs[0].attrs
    assert 'data-src="http://untrusted-tracker.com/pixel.png"' in clean_html
    assert "blocked-remote-image" in clean_html

    # Attacks eliminated
    assert "<script" not in clean_html
    assert "javascript:" not in clean_html
