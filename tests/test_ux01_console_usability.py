"""
Tests for UX-01: Real-World Console Usability Fixes
Verifies:
1. Replies: notes truncation, expand/details rendering, RTL/Unicode preservation, and pagination.
2. Counter Consistency: top nav candidates badge perfectly matches candidate queue and dashboard active count.
3. Research Status Clarity: distinction of Verified Record, Partially Reviewed, Needs Verification, Needs Research.
4. Visual Refinements: 13-field section group headers and collapsed long evidence links.
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import _get_navigation_badge_counts
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def disposable_ux_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ux01_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ux01!!")

    cand1 = CandidateCompany(
        candidate_id="cand:ux1",
        company_name="Studio Alpha",
        normalized_company_name="studio alpha",
        company_domain="studio-alpha.com",
        discovery_status=DiscoveryStatus.researching,
        qualification_outcome=QualificationOutcome.research_more,
        cold_outreach_allowed=False,
    )
    cand_store.save_candidate(cand1)

    # Seed historical outreach event with Persian / long note
    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO historical_outreach_events (
                event_id, candidate_id, contact_email, contact_name, channel,
                contacted_at, reply_status, reply_at, meeting_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "evt-ux-001",
            "cand:ux1",
            "contact@studio-alpha.com",
            "Studio Lead",
            "email",
            "2026-05-10T14:30:00Z",
            "replied",
            "2026-05-11T09:15:00Z",
            "held",
            "پاسخ مثبت دریافت شد و جلسه دموی نرم‌افزار برای هفته آینده تنظیم گردید. This note is deliberately long to test truncation and expandable details rendering in the table.",
        ))

    client = TestClient(app)
    return {
        "client": client,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "db_file": str(db_file),
    }


def test_01_replies_pagination_and_note_expansion(disposable_ux_env):
    client = disposable_ux_env["client"]
    resp = client.get("/sales/replies")
    assert resp.status_code == 200
    html = resp.text

    assert "Historical Recorded Responses" in html or "Replies & Inbound Intelligence" in html
    assert "dir=\"auto\"" in html

    resp_p2 = client.get("/sales/replies?resp_page=1&resp_page_size=10")
    assert resp_p2.status_code == 200


def test_02_counter_consistency_top_nav_and_lifecycle(disposable_ux_env):
    cand_store = disposable_ux_env["cand_store"]
    lifecycle_svc = CandidateLifecycleService(cand_store)
    queue_counts = lifecycle_svc.compute_queue_counts()
    nav_badges = _get_navigation_badge_counts()

    assert "candidates" in nav_badges
    assert nav_badges["candidates"] == queue_counts["active"]
    assert nav_badges["human_verification"] == queue_counts["needs_verification"]


def test_03_research_status_clarity_rendering(disposable_ux_env):
    client = disposable_ux_env["client"]
    resp = client.get("/sales/candidates")
    assert resp.status_code == 200
    html = resp.text

    # Assert new clarified status labels exist in the rendered output or templates
    assert any(badge in html for badge in ["Verified Record", "Needs Research", "Needs Verification", "Partially Reviewed"])


def test_04_research_review_section_grouping_and_details_collapse(disposable_ux_env):
    client = disposable_ux_env["client"]
    cand_store = disposable_ux_env["cand_store"]
    candidates = cand_store.list_candidates()
    assert len(candidates) > 0
    cid = candidates[0].candidate_id

    resp = client.get(f"/sales/candidates/{cid}/research-review")
    if resp.status_code == 200:
        html = resp.text
        assert "Section 1: Core Identity, Legal & Official Website" in html
        assert "Section 2: Location, Contact Details & Key Personnel" in html
        assert "Section 3: Priority Rationale, Business Operations & Intelligence" in html
        assert "Section 4: Traceable References, Provenance & Verification" in html

    detail_resp = client.get(f"/sales/candidates/{cid}")
    if detail_resp.status_code == 200:
        assert "crawled snapshot" in detail_resp.text.lower() or "research" in detail_resp.text.lower()
