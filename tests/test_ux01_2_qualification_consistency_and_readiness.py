"""
tests/test_ux01_2_qualification_consistency_and_readiness.py
Acceptance tests for SalesAI UX-01.2:
1. Contradictory outreach readiness fix:
   - Qualified candidate with domain-level suppression in SuppressionStore cannot show Draft Ready.
   - Authoritative suppression overrides all positive states (renders Suppressed, never contradictory).
   - Candidate suppressed only in candidate flags is also correctly blocked.
   - Candidate with prior contact/active conversation cannot show Draft Ready (renders Prior Contact).
   - Qualified candidate without an eligible verified recipient displays Contact Verification Required.
   - Qualified candidate with verified recipient and no restrictions displays Eligible for Manual Draft.
2. Unify Qualified Leads definitions & Dashboard parity:
   - Candidate with discovery_status='qualified' and qualification_outcome=None is recognized in BOTH Dashboard and /sales/leads.
   - Candidate with discovery_status='new' and qualification_outcome='qualified_for_enrichment' is recognized in BOTH.
   - Candidate with discovery_status='new' and qualification_outcome=None is excluded from BOTH.
   - Candidate with qualification_outcome='reject' is excluded from BOTH.
   - Candidate with qualification_outcome='watchlist' is excluded from BOTH.
3. Normalize Company Tier Labels:
   - CompanyTier.unknown renders as 'Unknown', never 'CompanyTier.unknown'.
   - CompanyTier.tier_1 renders as 'Tier 1'.
   - CompanyTier.tier_2 renders as 'Tier 2'.
   - CompanyTier.tier_3 renders as 'Tier 3'.
   - None / null renders as 'Unassigned'.
"""

import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import format_company_tier_label, is_candidate_qualified_lead
from backend.sales_ui.dashboard_service import DashboardService
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.schemas import ContactCandidate


@pytest.fixture
def test_setup_ux01_2(tmp_path):
    test_db = tmp_path / "test_ux01_2.db"
    cand_store = CandidateStore(db_path=test_db)
    reg_store = LeadRegisterStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)
    supp_store = SuppressionStore(rev_store)
    dash_svc = DashboardService(candidate_store=cand_store, review_store=rev_store)

    return {
        "db_path": test_db,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "dash_svc": dash_svc,
    }


def test_01_authoritative_suppression_overrides_draft_ready(test_setup_ux01_2, monkeypatch):
    """
    P0 Test: A qualified candidate with candidate.suppressed=False but an authoritative
    domain entry in SuppressionStore MUST display 'Suppressed' and NEVER 'Draft Ready'.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]
    supp_store = test_setup_ux01_2["supp_store"]

    c = CandidateCompany(
        candidate_id="cand:test:supp_auth",
        name="Suppressed Domain S.r.l.",
        domain="suppressed-auth.it",
        country="IT",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        suppressed=False,  # Note: candidate flag is False
    )
    cand_store.save_candidate(c)

    # Add verified contact to test that suppression strictly overrides verified contact availability
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_supp_01",
        candidate_id="cand:test:supp_auth",
        first_name="Marco",
        last_name="Rossi",
        full_name="Marco Rossi",
        work_email="marco@suppressed-auth.it",
        email_status="verified",
    ), candidate_id="cand:test:supp_auth")

    # Add authoritative suppression at domain level in SuppressionStore
    supp_store.add_suppression(email="any@suppressed-auth.it", company_domain="suppressed-auth.it", reason="opt_out")

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Suppressed Domain S.r.l." in res.text
    assert "Draft Ready" not in res.text
    assert "Suppressed" in res.text


def test_02_candidate_flag_suppression_blocks_draft_ready(test_setup_ux01_2, monkeypatch):
    """Verify candidate suppressed only in candidate flags is also correctly blocked."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]

    c = CandidateCompany(
        candidate_id="cand:test:supp_flag",
        name="Flagged Suppressed Co",
        domain="flag-suppressed.it",
        country="IT",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        suppressed=True,
    )
    cand_store.save_candidate(c)

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Flagged Suppressed Co" in res.text
    assert "Draft Ready" not in res.text
    assert "Suppressed" in res.text


def test_03_prior_contact_displays_prior_contact_not_draft_ready(test_setup_ux01_2, monkeypatch):
    """Verify previously contacted candidate shows Prior Contact, not Draft Ready."""
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]

    c = CandidateCompany(
        candidate_id="cand:test:contacted",
        name="Contacted Partner S.p.A.",
        domain="contacted-partner.it",
        country="IT",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        historical_contact_status="contacted",
        suppressed=False,
    )
    cand_store.save_candidate(c)

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Contacted Partner S.p.A." in res.text
    assert "Draft Ready" not in res.text
    assert "Prior Contact" in res.text


def test_04_missing_verified_recipient_displays_verification_required(test_setup_ux01_2, monkeypatch):
    """
    P0 Test: A qualified candidate without any verified recipient must display
    'Contact Verification Required', never 'Draft Ready'.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]

    c = CandidateCompany(
        candidate_id="cand:test:no_verified_contact",
        name="Studio Senza Contatti Verificati",
        domain="senza-verificati.it",
        country="IT",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        suppressed=False,
    )
    cand_store.save_candidate(c)

    # Save only an unverified contact
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_unverified",
        candidate_id="cand:test:no_verified_contact",
        first_name="Info",
        full_name="Info General",
        work_email="info@senza-verificati.it",
        email_status="public_unverified",
    ), candidate_id="cand:test:no_verified_contact")

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Studio Senza Contatti Verificati" in res.text
    assert "Draft Ready" not in res.text
    assert "Contact Verification Required" in res.text


def test_05_verified_recipient_displays_eligible_for_manual_draft(test_setup_ux01_2, monkeypatch):
    """
    Qualified candidate with verified recipient and zero restrictions receives
    'Eligible for Manual Draft'.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]

    c = CandidateCompany(
        candidate_id="cand:test:fully_ready",
        name="Studio Completamente Pronto",
        domain="pronto-draft.it",
        country="IT",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        suppressed=False,
    )
    cand_store.save_candidate(c)

    # Save a verified contact
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_verified_01",
        candidate_id="cand:test:fully_ready",
        first_name="Lucia",
        last_name="Bianchi",
        full_name="Lucia Bianchi",
        work_email="lucia@pronto-draft.it",
        email_status="verified",
    ), candidate_id="cand:test:fully_ready")

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    assert "Studio Completamente Pronto" in res.text
    assert "Eligible for Manual Draft" in res.text


def test_06_dashboard_and_leads_view_qualification_parity(test_setup_ux01_2, monkeypatch):
    """
    P0 Parity Test: Dashboard KPI 'qualified_leads' and '/sales/leads' returned count
    MUST match exactly across legacy qualified, current qualified, and rejected candidates.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_ux01_2["db_path"]))
    cand_store = test_setup_ux01_2["cand_store"]
    dash_svc = test_setup_ux01_2["dash_svc"]

    # 1. Legacy qualified (discovery_status='qualified', qualification_outcome=None)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:p1",
        name="Legacy Qualified Studio",
        domain="legacy-qual.it",
        discovery_status="qualified",
        qualification_outcome=None,
    ))

    # 2. Modern qualified (discovery_status='new', qualification_outcome='qualified_for_enrichment')
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:p2",
        name="Modern Qualified Studio",
        domain="modern-qual.it",
        discovery_status="new",
        qualification_outcome="qualified_for_enrichment",
    ))

    # 3. Unqualified discovery (discovery_status='new', qualification_outcome=None) -> EXCLUDED
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:p3",
        name="Raw Discovery New",
        domain="raw-discovery.it",
        discovery_status="new",
        qualification_outcome=None,
    ))

    # 4. Rejected candidate (discovery_status='qualified', qualification_outcome='reject') -> EXCLUDED
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:p4",
        name="Rejected Studio",
        domain="rejected-studio.it",
        discovery_status="qualified",
        qualification_outcome="reject",
    ))

    # 5. Watchlist candidate -> EXCLUDED from qualified leads
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:p5",
        name="Watchlisted Studio",
        domain="watch-studio.it",
        discovery_status="watchlist",
        qualification_outcome="watchlist",
    ))

    # Evaluate Dashboard summary count
    summary = dash_svc.get_summary()
    dashboard_kpi_count = summary.qualified_leads

    # Evaluate /sales/leads view response count
    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    # Both cand:p1 and cand:p2 must appear; cand:p3, cand:p4, cand:p5 must not appear
    assert "Legacy Qualified Studio" in res.text
    assert "Modern Qualified Studio" in res.text
    assert "Raw Discovery New" not in res.text
    assert "Rejected Studio" not in res.text
    assert "Watchlisted Studio" not in res.text

    # Verify Dashboard count matches exactly 2
    assert dashboard_kpi_count == 2, f"Expected 2 qualified leads in Dashboard, got {dashboard_kpi_count}"
    assert "Qualified Leads (2)" in res.text


def test_07_company_tier_labels_normalization():
    """Verify CompanyTier enums and strings are normalized to human-readable labels."""
    assert format_company_tier_label(CompanyTier.unknown) == "Unknown"
    assert format_company_tier_label(CompanyTier.tier_1) == "Tier 1"
    assert format_company_tier_label(CompanyTier.tier_2) == "Tier 2"
    assert format_company_tier_label(CompanyTier.tier_3) == "Tier 3"
    assert format_company_tier_label("CompanyTier.unknown") == "Unknown"
    assert format_company_tier_label("tier_1") == "Tier 1"
    assert format_company_tier_label("unknown") == "Unknown"
    assert format_company_tier_label(None) == "Unassigned"
    assert format_company_tier_label("") == "Unassigned"
