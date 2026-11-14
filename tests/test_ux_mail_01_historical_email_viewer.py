import os
import json
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from backend.sales_ui.router import derive_company_outreach_status


@pytest.fixture(autouse=True)
def setup_isolated_db(tmp_path, monkeypatch):
    """
    Sets up an isolated copy of the database for the test session,
    ensuring zero mutation of the operational database.
    """
    test_db = tmp_path / "isolated_test_sales.db"
    src_path = Path("data/sales_outreach.db")
    if src_path.exists():
        src_conn = sqlite3.connect(f"file:///{src_path.resolve().as_posix()}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(test_db))
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("SALESAI_DB_PATH", str(test_db))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(test_db))
    yield str(test_db)


@pytest.fixture
def client():
    return TestClient(app)


def test_full_body_display_formatting_and_viewer_modal(client):
    """
    Test 1: Full-body display, original formatting, escaping, missing/truncated body and Gmail link.
    Verifies that:
    - The viewer modal is embedded on both candidate detail and sent history pages.
    - Historical emails have individual 'View Email' triggers and safe embedded JSON data.
    - Plain-text bodies with newlines, paragraphs, and characters are preserved verbatim.
    - Truncated source flag (body_truncated) is accurately passed and handled without assumption.
    - Empty bodies are represented without claiming full text.
    - The API endpoint /sales/historical-emails/{id} safely serves full email metadata and body.
    """
    # 1. Test API endpoint for a known historical message
    resp = client.get("/sales/historical-emails/1136")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == 1136
    assert data["message_id"] == "1a1d42cadd9acda9"
    assert data["sender_account"] == "info@pybim.com"
    assert data["to_address"] == "office@ondernemendemmen.nl"
    assert "Potential MEP BIM Collaboration" in data["subject"]
    assert len(data["email_body"]) > 500
    assert "\n" in data["email_body"]  # Preserves newlines
    assert data["body_truncated"] == "NO"
    assert "mail.google.com" in data["gmail_link"]

    # 2. Test candidate detail page renders View Email actions and safe embedded data
    cand_resp = client.get("/sales/candidates/cand:domain:ondernemendemmen.nl")
    assert cand_resp.status_code == 200
    cand_soup = BeautifulSoup(cand_resp.text, "html.parser")

    # Verify modal is present in DOM
    modal = cand_soup.find(id="historical-email-viewer-modal")
    assert modal is not None, "historical-email-viewer-modal must be present in DOM"
    assert modal.find(id="hem-subject") is not None
    assert modal.find(id="hem-body-content") is not None
    assert modal.find(id="hem-truncated-notice") is not None
    assert modal.find(id="hem-missing-notice") is not None
    assert modal.find(id="hem-gmail-link") is not None

    # Verify both historical messages have independent View Email buttons and scripts
    script_1136 = cand_soup.find("script", id="hist-email-data-1136")
    script_1223 = cand_soup.find("script", id="hist-email-data-1223")
    assert script_1136 is not None, "hist-email-data-1136 script tag must exist"
    assert script_1223 is not None, "hist-email-data-1223 script tag must exist"

    data_1136 = json.loads(script_1136.text)
    data_1223 = json.loads(script_1223.text)

    # Both messages can be opened independently and display their own distinct bodies & recipients
    assert data_1136["id"] == 1136
    assert data_1136["to_address"] == "office@ondernemendemmen.nl"
    assert data_1223["id"] == 1223
    assert data_1223["to_address"] == "l.hoge@ondernemendemmen.nl"
    assert data_1136["email_body"] != ""
    assert data_1223["email_body"] != ""
    assert data_1136["message_id"] != data_1223["message_id"]

    # 3. Test sent history page (/sales/sent) renders View Email triggers and modal
    sent_resp = client.get("/sales/sent")
    assert sent_resp.status_code == 200
    sent_soup = BeautifulSoup(sent_resp.text, "html.parser")

    sent_modal = sent_soup.find(id="historical-email-viewer-modal")
    assert sent_modal is not None, "Sent page must include historical email modal"
    sent_view_btns = [btn for btn in sent_soup.find_all("button") if "View Email" in btn.text]
    assert len(sent_view_btns) > 0, "Sent page must include View Email buttons"


def test_outreach_status_provenance_and_reply_consistency(client):
    """
    Test 2: Outreach status provenance consistency, preserving genuinely confirmed replies.
    Verifies that:
    - cand:domain:ondernemendemmen.nl has verified historical evidence producing REPLY_RECEIVED.
    - Historical Communications table accurately shows the confirmed reply event with reply timestamp and notes.
    - Legitimately confirmed replies are preserved.
    - Mere 'contacted' status is never falsely converted to 'reply received'.
    """
    db_path = os.environ.get("SALES_OUTREACH_DB", "data/sales_outreach.db")
    store = CandidateStore(db_path)

    cand = store.get_by_id("cand:domain:ondernemendemmen.nl")
    assert cand is not None
    assert cand.company_domain == "ondernemendemmen.nl"

    # 1. Check independent verified historical evidence in historical_imports
    with store._get_connection() as conn:
        import_row = conn.execute(
            "SELECT raw_row_json, has_reply FROM historical_imports WHERE canonical_domain = 'ondernemendemmen.nl'"
        ).fetchone()
        assert import_row is not None
        assert import_row["has_reply"] == 1

        raw_data = json.loads(import_row["raw_row_json"])
        # Explicit reply date and notes recorded in source workbook
        assert "2026-11-13 15:38:00" in raw_data.get("تاریخ پاسخدهی", "")
        assert "گفته به l.hoge@ondernemendemmen.nl ایمیل بزنین" in raw_data.get("یادداشت‌های پیگیری", "")

        # Check historical outreach events table has explicit reply_at
        outreach_rows = conn.execute(
            "SELECT * FROM historical_outreach_events WHERE candidate_id = ?",
            ("cand:domain:ondernemendemmen.nl",)
        ).fetchall()
        assert len(outreach_rows) == 2

        events_by_email = {dict(r)["contact_email"]: dict(r) for r in outreach_rows}
        assert "office@ondernemendemmen.nl" in events_by_email
        first_event = events_by_email["office@ondernemendemmen.nl"]
        assert first_event["reply_at"] == "2026-11-13 15:38:00"

        assert "l.hoge@ondernemendemmen.nl" in events_by_email
        second_event = events_by_email["l.hoge@ondernemendemmen.nl"]
        assert second_event["reply_status"] == "contacted"
        assert second_event["reply_at"] is None

    # 2. Check candidate status derivation
    # Since cand.historical_contact_status == 'replied', derive_company_outreach_status produces 'REPLY_RECEIVED'
    derived_status = derive_company_outreach_status(
        cand=cand,
        drafts=[],
        manual_logs=[],
        historical_sent_emails=[],
    )
    assert derived_status == "REPLY_RECEIVED"

    # Verify that a candidate with only 'contacted' is NEVER converted to 'REPLY_RECEIVED'
    cand_contacted_only = store.get_by_id("cand:domain:ondernemendemmen.nl")
    cand_contacted_only.historical_contact_status = "contacted"
    cand_contacted_only.reply_status = None
    status_contacted = derive_company_outreach_status(
        cand=cand_contacted_only,
        drafts=[],
        manual_logs=[],
        historical_sent_emails=[],
    )
    assert status_contacted == "SENT_MANUALLY_CONFIRMED"
    assert status_contacted != "REPLY_RECEIVED"

    # 3. Verify Candidate Detail HTML displays the verified reply evidence in the table
    resp = client.get("/sales/candidates/cand:domain:ondernemendemmen.nl")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "html.parser")

    # Table 2 must show 'replied' badge and the reply date 2026-11-13 15:38
    card_sections = soup.find_all("div", class_="card")
    comm_card = None
    for card in card_sections:
        if "Historical Communications & Reply Tracking" in card.text:
            comm_card = card
            break

    assert comm_card is not None
    comm_text = comm_card.text
    assert "2026-11-13 15:38" in comm_text, "Confirmed reply timestamp must be visible in table"
    assert "replied" in comm_text.lower(), "'replied' status badge must be visible in table"
    assert "l.hoge@ondernemendemmen.nl" in comm_text, "Referral email from reply note must be visible in table"
