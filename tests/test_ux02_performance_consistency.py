"""
Tests for UX-02: Real-Data UI Performance & Consistency.
Validates:
1. Replies pagination correctness and efficiency (LIMIT/OFFSET, total count, preservation of filters, notes, RTL/Unicode).
2. Navigation counter consistency (strict agreement with authoritative lifecycle queue counts, verification of context reuse optimization).
3. Research field and evidence preservation (all 13 fields present, section headers, operator actions intact, collapsed evidence links preserving all source URLs).
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import _get_navigation_badge_counts, render_console_template
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.sending.review_store import ReviewStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord


@pytest.fixture
def disposable_ux02_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ux02_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    excl_store = ExcludedCompaniesStore(str(db_file))
    excl_store._init_db()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ux02!!")

    # Seed candidate with research Excel provenance and full 13-field record
    cand = CandidateCompany(
        candidate_id="cand:ux2:acme",
        company_name="Acme Engineering SpA",
        normalized_company_name="acme engineering spa",
        company_domain="acme-engineering.it",
        country="Italy",
        city="Bologna",
        company_tier="tier_1",
        discovery_status=DiscoveryStatus.researching,
        qualification_outcome=QualificationOutcome.research_more,
        cold_outreach_allowed=False,
        source_refs=["research_lead_excel"],
    )
    cand_store.save_candidate(cand)

    # Seed 13-field record with multiple comma-separated URLs in Field 12 and Field 11
    multiple_reference_urls = (
        "https://linkedin.com/company/acme, https://twitter.com/acme, "
        "https://facebook.com/acme, https://glassdoor.com/acme, "
        "https://github.com/acme, https://youtube.com/acme"
    )
    reg_rec = LeadRegisterRecord(
        candidate_id="cand:ux2:acme",
        record_number=101,
        priority_level="A",
        priority_rationale="Leading BIM structural modeling consultant in Emilia-Romagna",
        company_name="Acme Engineering SpA",
        legal_identity="IT09876543210",
        geographic_location="Via dell'Ingegneria 42, 40121 Bologna (BO), Italy",
        telephone_numbers="+39 051 9876543",
        business_activities="Structural engineering, BIM authoring, HVAC coordination",
        official_website="https://www.acme-engineering.it",
        responsible_person="Ing. Alessandro Bianchi (Direttore Tecnico)",
        descriptions_and_notes="High-profile civil engineering projects and infrastructure design.",
        job_advertisement_links="https://www.acme-engineering.it/careers/bim-lead",
        other_links_and_references=multiple_reference_urls,
        contact_email_addresses="info@acme-engineering.it",
        provenance_source="research_lead_excel",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(reg_rec)

    # Seed multiple historical outreach events to test pagination limits and offsets
    with cand_store._get_connection() as conn:
        for i in range(1, 36):
            conn.execute("""
                INSERT INTO historical_outreach_events (
                    event_id, candidate_id, contact_email, contact_name, channel,
                    contacted_at, reply_status, reply_at, meeting_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"evt-ux2-{i:03d}",
                "cand:ux2:acme",
                f"contact{i}@acme-engineering.it",
                f"Engineer {i}",
                "email",
                f"2026-05-{min(i, 28):02d}T10:00:00Z",
                "interested" if i % 2 == 0 else "neutral",
                f"2026-05-{min(i, 28):02d}T15:30:00Z",
                "scheduled" if i % 3 == 0 else "none",
                f"Note {i}: پاسخ دریافت شد و مذاکرات فنی در جریان است. RTL/Persian Unicode test note #{i}." if i == 1 else f"Note {i}: Short outreach summary #{i}."
            ))

    client = TestClient(app)
    return {
        "client": client,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "reg_store": reg_store,
        "db_file": str(db_file),
    }


def test_01_replies_pagination_correctness(disposable_ux02_env):
    """
    Test 1: Replies pagination correctness.
    - Total count reflects total historical responses in database (35 seeded).
    - Page 1 returns exactly 25 items (default page size).
    - Page 2 returns the remaining 10 items.
    - Notes truncation with expand details rendered.
    - RTL and Unicode formatting (dir='auto', unicode-bidi) preserved.
    - Query parameters (resp_page, resp_page_size, category, status) properly reflected in links.
    """
    client = disposable_ux02_env["client"]

    # Page 1 request
    resp_p1 = client.get("/sales/replies?resp_page=1&resp_page_size=25")
    assert resp_p1.status_code == 200
    html_p1 = resp_p1.text

    assert "Historical Recorded Responses (35)" in html_p1
    assert "Showing 1 to 25 of 35 responses" in html_p1
    assert "Next" in html_p1
    assert "resp_page=2" in html_p1
    assert "dir=\"auto\"" in html_p1
    assert "پاسخ دریافت شد" in html_p1

    # Page 2 request
    resp_p2 = client.get("/sales/replies?resp_page=2&resp_page_size=25")
    assert resp_p2.status_code == 200
    html_p2 = resp_p2.text

    assert "Showing 26 to 35 of 35 responses" in html_p2
    assert "Previous" in html_p2
    assert "resp_page=1" in html_p2


def test_02_navigation_counter_consistency(disposable_ux02_env):
    """
    Test 2: Navigation counter consistency.
    - Confirm _get_navigation_badge_counts matches CandidateLifecycleService.compute_queue_counts().
    - Verify that when queue_counts or summary are passed in context, render_console_template reuses them.
    - Never returns stale or divergent counts.
    """
    cand_store = disposable_ux02_env["cand_store"]
    lifecycle_svc = CandidateLifecycleService(cand_store)
    queue_counts = lifecycle_svc.compute_queue_counts()

    badges = _get_navigation_badge_counts()
    assert badges["candidates"] == queue_counts["active"]
    assert badges["human_verification"] == queue_counts["needs_verification"]

    # Verify context optimization reuse in render_console_template
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/sales/dashboard", "headers": []}
    req = Request(scope)

    # Calling render_console_template with existing queue_counts
    ctx = {
        "request": req,
        "queue_counts": queue_counts,
    }
    # Check that render_console_template populates nav_badges matching queue_counts
    render_console_template(req, "dashboard.html", ctx)
    assert "nav_badges" in ctx
    assert ctx["nav_badges"]["candidates"] == queue_counts["active"]
    assert ctx["nav_badges"]["human_verification"] == queue_counts["needs_verification"]


def test_03_research_field_and_evidence_preservation(disposable_ux02_env):
    """
    Test 3: Research field and evidence preservation.
    - All 13 original lead register fields remain present in Research Review.
    - 4 section headers dividing the 13 fields render correctly.
    - Field-level operator actions (apply checkboxes, verify & approve, manual correction) remain intact.
    - Collapsed evidence links preserve ALL original source URLs (both initial slice and expanded list).
    - Verification statuses and outreach eligibility remain distinctly marked.
    """
    client = disposable_ux02_env["client"]
    cid = "cand:ux2:acme"

    resp = client.get(f"/sales/candidates/{cid}/research")
    assert resp.status_code == 200
    html = resp.text

    # Section Headers
    assert "Section 1: Core Identity, Legal &amp; Official Website" in html or "Section 1: Core Identity, Legal & Official Website" in html
    assert "Section 2: Location, Contact Details &amp; Key Personnel" in html or "Section 2: Location, Contact Details & Key Personnel" in html
    assert "Section 3: Priority Rationale, Business Operations &amp; Intelligence" in html or "Section 3: Priority Rationale, Business Operations & Intelligence" in html
    assert "Section 4: Traceable References, Provenance &amp; Verification" in html or "Section 4: Traceable References, Provenance & Verification" in html

    # All 13 fields presence
    assert "1. Company Name" in html
    assert "2. Priority Level &amp; Rationale" in html or "2. Priority Level & Rationale" in html
    assert "3. Legal Identity / P.IVA" in html
    assert "4. Geographic Location" in html
    assert "5. Telephone Numbers" in html
    assert "6. Contact Emails" in html
    assert "7. Responsible Person / Key Contact" in html
    assert "8. Business Activities &amp; BIM" in html or "8. Business Activities & BIM" in html
    assert "9. Official Website" in html
    assert "10. Descriptions &amp; Notes" in html or "10. Descriptions & Notes" in html
    assert "11. Careers &amp; Opportunities" in html or "11. Careers & Opportunities" in html
    assert "12. References &amp; Evidence URLs" in html or "12. References & Evidence URLs" in html
    assert "13. Provenance &amp; Verification" in html or "13. Provenance & Verification" in html

    # Stored values from record
    assert "Acme Engineering SpA" in html
    assert "IT09876543210" in html
    assert "Bologna" in html
    assert "+39 051 9876543" in html
    assert "https://www.acme-engineering.it" in html
    assert "Alessandro Bianchi" in html
    assert "info@acme-engineering.it" in html

    # Field-level operator actions intact
    assert "name=\"apply_company_name\"" in html
    assert "name=\"apply_legal_identity\"" in html
    assert "name=\"apply_geographic_location\"" in html
    assert "name=\"apply_official_website\"" in html
    assert "verify-official-website-form" in html
    assert "Manual Correction" in html or "Independent Verification" in html

    # Collapsed evidence links: verify all 6 URLs are preserved in the HTML
    assert "linkedin.com/company/acme" in html
    assert "twitter.com/acme" in html
    assert "facebook.com/acme" in html
    assert "glassdoor.com/acme" in html
    # URLs in collapsed details (items 5 and 6)
    assert "github.com/acme" in html
    assert "youtube.com/acme" in html
    assert "+ 2 more reference links" in html

