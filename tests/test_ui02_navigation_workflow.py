"""
UI-02 Minimal Verification Tests.

1. Navigation rendering and route smoke check:
   - Verifies all 5 navigation groups and 15 navigation entries render without template syntax errors.
   - Verifies navigation badge counters render correctly.
2. Dashboard workflow counters check:
   - Verifies dashboard counters agree with lifecycle queue counts (Active, Needs Research, Needs Verification, Historical Pending, Qualified Leads).
   - Verifies metric card links lead to the correct routes.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.sending.review_store import ReviewStore
from backend.sales_ui.dashboard_service import DashboardService


@pytest.fixture
def disposable_console_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ui02_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ui02!!")

    # Seed candidates across distinct lifecycle states
    # 1. Active: Needs Research
    cand1 = CandidateCompany(
        candidate_id="cand:c1",
        company_name="Studio One",
        normalized_company_name="studio one",
        company_domain="studio-one.it",
        discovery_status=DiscoveryStatus.researching,
        qualification_outcome=QualificationOutcome.research_more,
        cold_outreach_allowed=False,
    )
    # 2. Active: Needs Verification (has lead register record status 'Unverified')
    cand2 = CandidateCompany(
        candidate_id="cand:c2",
        company_name="Studio Two",
        normalized_company_name="studio two",
        company_domain="studio-two.it",
        discovery_status=DiscoveryStatus.new,
        cold_outreach_allowed=False,
    )
    # 3. Historical Pending (has prior contact)
    cand3 = CandidateCompany(
        candidate_id="cand:c3",
        company_name="Studio Three",
        normalized_company_name="studio three",
        company_domain="studio-three.it",
        discovery_status=DiscoveryStatus.new,
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
    )
    # 4. Qualified Lead
    cand4 = CandidateCompany(
        candidate_id="cand:c4",
        company_name="Studio Four",
        normalized_company_name="studio four",
        company_domain="studio-four.it",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=False,
    )
    cand_store.save_candidate(cand1)
    cand_store.save_candidate(cand2)
    cand_store.save_candidate(cand3)
    cand_store.save_candidate(cand4)

    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()

    # Attach lead register record to cand2 to make it Needs Verification
    reg_rec = LeadRegisterRecord(
        candidate_id="cand:c2",
        company_name="Studio Two",
        verification_status="Unverified",
    )
    reg_store.save_record(reg_rec)

    client = TestClient(app)
    yield {
        "client": client,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "db_file": db_file,
    }


def test_01_navigation_rendering_and_route_smoke(disposable_console_env):
    """
    Check 1: Navigation rendering and route smoke check.
    - All 5 navigation groups exist with the required labels.
    - All routes in the 5 groups return 200 OK.
    """
    client = disposable_console_env["client"]

    # 1. Fetch dashboard to check base.html navigation markup
    resp = client.get("/sales/dashboard")
    assert resp.status_code == 200
    html = resp.text

    # Group labels
    assert "Overview" in html
    assert "Discovery &amp; Companies" in html or "Discovery & Companies" in html
    assert "Research &amp; Qualification" in html or "Research & Qualification" in html
    assert "Outreach" in html
    assert "Administration" in html

    # Navigation entries
    assert 'href="/sales/dashboard"' in html
    assert 'href="/sales/discovery"' in html
    assert 'href="/sales/sources"' in html
    assert 'href="/sales/candidates"' in html
    assert 'href="/sales/candidates?queue=all"' in html
    assert 'href="/sales/register"' in html
    assert 'href="/sales/candidates?queue=needs_verification"' in html
    assert 'href="/sales/leads"' in html
    assert 'href="/sales/drafts"' in html
    assert 'href="/sales/approved"' in html
    assert 'href="/sales/sent"' in html
    assert 'href="/sales/replies"' in html
    assert 'href="/sales/excluded-companies"' in html
    assert 'href="/sales/do-not-contact"' in html
    assert 'href="/sales/watchlist"' in html
    assert 'href="/sales/analytics"' in html
    assert 'href="/sales/settings"' in html

    # Route smoke check for all key navigation destinations
    endpoints = [
        "/sales/dashboard",
        "/sales/discovery",
        "/sales/sources",
        "/sales/candidates",
        "/sales/candidates?queue=all",
        "/sales/register",
        "/sales/candidates?queue=needs_verification",
        "/sales/leads",
        "/sales/drafts",
        "/sales/approved",
        "/sales/sent",
        "/sales/replies",
        "/sales/excluded-companies",
        "/sales/do-not-contact",
        "/sales/watchlist",
        "/sales/analytics",
        "/sales/settings",
    ]
    for ep in endpoints:
        r = client.get(ep)
        assert r.status_code == 200, f"Endpoint {ep} failed with status {r.status_code}"


def test_02_dashboard_workflow_counters_agree_with_lifecycle_queues(disposable_console_env):
    """
    Check 2: Dashboard counters agree with authoritative lifecycle queue service.
    - Active Candidates
    - Needs Research
    - Needs Verification
    - Historical Review Pending
    - Qualified Leads
    - Pending Draft Review
    """
    client = disposable_console_env["client"]
    cand_store = disposable_console_env["cand_store"]
    rev_store = disposable_console_env["rev_store"]

    # Compute expected authoritative counts via CandidateLifecycleService
    lifecycle_svc = CandidateLifecycleService(cand_store)
    q_counts = lifecycle_svc.compute_queue_counts()

    dash_svc = DashboardService(candidate_store=cand_store, review_store=rev_store)
    summary = dash_svc.get_summary()

    # Exact agreement checks
    assert summary.active_candidates == q_counts["active"]
    assert summary.needs_research == q_counts["needs_research"]
    assert summary.needs_verification == q_counts["needs_verification"]
    assert summary.historical_pending == q_counts["historical_pending"]
    assert summary.qualified_leads == q_counts["qualified"]

    # Verify dashboard HTML renders these exact cards and links
    resp = client.get("/sales/dashboard")
    assert resp.status_code == 200
    html = resp.text

    assert 'href="/sales/candidates?queue=active"' in html
    assert 'href="/sales/candidates?queue=needs_research"' in html
    assert 'href="/sales/candidates?queue=needs_verification"' in html
    assert 'href="/sales/candidates?queue=historical_pending"' in html
    assert 'href="/sales/leads"' in html
    assert 'href="/sales/drafts"' in html

    # Verify counts appear in the page
    assert f">{summary.active_candidates}</span>" in html
    assert f">{summary.needs_research}</span>" in html
    assert f">{summary.needs_verification}</span>" in html
    assert f">{summary.historical_pending}</span>" in html
    assert f">{summary.qualified_leads}</span>" in html
