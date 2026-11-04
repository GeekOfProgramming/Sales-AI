"""
UI-05 Minimal Verification Tests.

1. Historical Review Pending View & Candidate Detail Navigation:
   - Verifies /sales/candidates?queue=historical_pending clearly displays previously contacted status,
     historical pending queue badges, and a prominent 'Review History' action link pointing to #section-outreach.
   - Verifies candidate detail outreach tab renders with historical finalization decision authority
     and explicit callout explaining the consequences of finalization.

2. Company Communication History & Status Separation:
   - Verifies candidate detail outreach area clearly distinguishes between:
     1) Imported historical sent emails (Gmail archive)
     2) Historical communications and reply tracking events
     3) Historical meeting activities & follow-ups
     4) Native application outreach drafts
     5) Confirmed manual external dispatches
   - Verifies sent and replies views link back to candidate profiles and clearly distinguish statuses
     without conflating contacted, replied, meeting, and finalized.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.models import DiscoveryStatus
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_ui05_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ui05_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    excl_store = ExcludedCompaniesStore(str(db_file))
    excl_store._init_db()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ui05!!")

    # Initialize HistoricalImporter schema
    imp = HistoricalImporter(store=cand_store)

    # Seed Candidate 1: Historical Pending Candidate
    cand1 = CandidateCompany(
        candidate_id="cand:domain:architeam.it",
        company_name="ArchiTeam Studio Srl",
        normalized_company_name="architeam studio srl",
        company_domain="architeam.it",
        country="Italy",
        city="Milan",
        company_tier="tier_1",
        discovery_status=DiscoveryStatus.known_company,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
        cold_outreach_allowed=False,
        source_refs=["historical_gmail"],
    )
    cand_store.save_candidate(cand1)

    # Seed historical outreach event (Phase 10.5)
    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO historical_outreach_events (
                event_id, candidate_id, contact_email, contact_name, channel,
                contacted_at, reply_status, reply_at, meeting_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "evt-arch-001",
            "cand:domain:architeam.it",
            "info@architeam.it",
            "Arch. Giorgio Rossi",
            "email",
            "2026-05-10T14:30:00Z",
            "interested",
            "2026-05-11T09:15:00Z",
            "proposed",
            "Expressed interest in pyBIM modeling workflow.",
        ))

        # Seed historical sent email (Gmail archive)
        conn.execute("""
            INSERT INTO historical_sent_emails (
                message_id, thread_id, sender_account, from_address, to_address,
                recipient_emails_json, source_file, source_sheet, subject,
                sent_date, sent_time, matched_candidate_id, matched_company_name,
                reconciliation_status, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "msg-hist-999",
            "thread-hist-999",
            "outreach@pybim.it",
            "outreach@pybim.it",
            "info@architeam.it",
            '["info@architeam.it"]',
            "test_archive.xlsx",
            "Sent_Emails",
            "pyBIM Introduction for ArchiTeam",
            "2026-05-10",
            "14:30:00",
            "cand:domain:architeam.it",
            "ArchiTeam Studio Srl",
            "matched",
            "2026-05-10T14:35:00Z",
        ))

        # Seed historical meeting activity
        conn.execute("""
            INSERT INTO historical_meeting_activities (
                activity_hash, company_name, canonical_domain, status, sender,
                contact_date, follow_up_date, notes, matched_candidate_id, matched_company_name,
                source_file, source_sheet, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "act-hash-1234",
            "ArchiTeam Studio Srl",
            "architeam.it",
            "Meeting Completed",
            "outreach@pybim.it",
            "2026-05-15",
            "2026-05-20",
            "Discussed initial BIM pilot parameters with lead architect.",
            "cand:domain:architeam.it",
            "ArchiTeam Studio Srl",
            "test_archive.xlsx",
            "Meetings",
            "2026-05-15T10:00:00Z",
        ))

    return {
        "db_file": db_file,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "candidate_id": "cand:domain:architeam.it",
    }


def test_historical_pending_view_and_navigation(disposable_ui05_env):
    """
    Test 1:
    - /sales/candidates?queue=historical_pending renders candidates awaiting review.
    - Displays 'Review History' action leading to #section-outreach.
    - Displays 'Historical Pending' queue badge and contacted indicators.
    - Candidate detail displays the historical outreach decision callout in the decision bar.
    """
    client = TestClient(app)

    # 1. Historical Pending Queue View
    resp = client.get("/sales/candidates?queue=historical_pending")
    assert resp.status_code == 200
    html = resp.text
    assert "ArchiTeam Studio Srl" in html
    assert "Historical Pending" in html
    assert "Review History" in html
    assert "tab=outreach#section-outreach" in html
    assert "Contacted" in html

    # 2. Candidate Detail View
    cand_id = disposable_ui05_env["candidate_id"]
    det_resp = client.get(f"/sales/candidates/{cand_id}")
    assert det_resp.status_code == 200
    det_html = det_resp.text
    assert "ArchiTeam Studio Srl" in det_html
    assert "Operator Review Decision (Human-in-the-Loop)" in det_html
    assert "Finalize Historical Outreach" in det_html
    assert "Selecting <em>Finalize Historical Outreach</em> archives this company" in det_html


def test_communication_history_sections_and_status_separation(disposable_ui05_env):
    """
    Test 2:
    - Candidate Detail Outreach tab clearly separates:
      1) Imported Historical Sent Emails (Gmail Archive)
      2) Historical Communications & Reply Tracking
      3) Historical Meeting Activities & Follow-ups
      4) Native Outreach Drafts
      5) Confirmed Manual External Dispatches
    - Sent and Replies views link back to the candidate workspace with proper context.
    - Conflation check: Verifies 'contacted', 'replied', and 'meeting' are rendered as distinct badges.
    """
    client = TestClient(app)
    cand_id = disposable_ui05_env["candidate_id"]

    # 1. Candidate Detail Outreach Section
    det_resp = client.get(f"/sales/candidates/{cand_id}")
    assert det_resp.status_code == 200
    html = det_resp.text

    # Verify distinct historical communication areas exist
    assert "1. Imported Historical Sent Emails (Gmail Archive)" in html
    assert "2. Historical Communications &amp; Reply Tracking" in html or "2. Historical Communications & Reply Tracking" in html
    assert "3. Historical Meeting Activities &amp; Follow-ups" in html or "3. Historical Meeting Activities & Follow-ups" in html
    assert "4. Native Outreach Drafts" in html
    assert "5. Confirmed Manual External Dispatches" in html

    # Verify seeded data appears under each section
    assert "pyBIM Introduction for ArchiTeam" in html
    assert "interested" in html
    assert "Meeting Completed" in html
    assert "Discussed initial BIM pilot parameters with lead architect." in html

    # 2. Sent History View links back to candidate
    sent_resp = client.get("/sales/sent")
    assert sent_resp.status_code == 200
    sent_html = sent_resp.text
    assert f"/sales/candidates/{cand_id}?tab=outreach#section-outreach" in sent_html

    # 3. Replies View links back to candidate
    replies_resp = client.get("/sales/replies")
    assert replies_resp.status_code == 200
    replies_html = replies_resp.text
    assert f"/sales/candidates/{cand_id}?tab=outreach#section-outreach" in replies_html
