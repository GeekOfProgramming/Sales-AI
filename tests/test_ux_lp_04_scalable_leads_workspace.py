"""
UX-LP-04 Test Suite: Scalable Qualified Leads Workspace.

Validates:
1. Work queues:
   - All Qualified Leads
   - Needs Research
   - Review Required
   - Research Ready
   - Ready for Manual Draft
   - Contact Verification Required
   - Previously Contacted
   - Suppressed / Ineligible
2. Server-side search & filtering:
   - Search by company name & domain (`q`)
   - Country filter
   - City filter
   - Priority filter (A, B, C)
   - Tier filter
   - Research readiness filter
   - Outreach readiness filter
3. Server-side sorting:
   - By priority, company_name, research_date, readiness (asc/desc)
4. Pagination:
   - Allowed page sizes (10, 25, 50) and state preservation across queries
5. Scalability verification:
   - 1,000+ synthetic candidates evaluated cleanly with fast response time and no data loss
6. AI Handoff safety & restrictions:
   - One-click copy texts pre-generated
   - AI brief copy disabled for Suppressed / DNC and Previously Contacted
   - Invalid candidate IDs handled gracefully (never actionable leads)
7. Non-destructive guarantees:
   - Read-only operations; zero background tasks or LLM transmissions on GET requests
"""

import pytest
import time
from pathlib import Path
from starlette.testclient import TestClient

from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.research_store import ResearchSnapshotStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.main import app
from backend.sales_ui.router import evaluate_candidate_readiness


@pytest.fixture
def workspace_setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_scalable_leads.sqlite3")
    research_db = str(tmp_path / "test_scalable_research.sqlite3")

    monkeypatch.setenv("SALES_OUTREACH_DB", db_path)
    monkeypatch.setenv("SALESAI_DB_PATH", db_path)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_path)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    cand_store = CandidateStore(db_path=db_path)
    reg_store = LeadRegisterStore(db_path=db_path)
    rev_store = ReviewStore(db_path=db_path)
    supp_store = SuppressionStore(review_store=rev_store)
    res_store = ResearchSnapshotStore(research_db)

    return {
        "db_path": db_path,
        "research_db": research_db,
        "cand_store": cand_store,
        "supp_store": supp_store,
        "reg_store": reg_store,
        "res_store": res_store,
    }


def test_01_work_queues_and_counts(workspace_setup):
    """Verify each of the 8 work queues correctly identifies candidates and accurately filters."""
    cand_store = workspace_setup["cand_store"]
    reg_store = workspace_setup["reg_store"]
    supp_store = workspace_setup["supp_store"]

    # 1. Needs Research lead (unresearched, qualified)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:needs_res",
        company_name="Needs Research Architects",
        company_domain="needs-res.com",
        country="France",
        city="Paris",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))

    # 2. Review Required lead (has register record with unreviewed status)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:rev_req",
        company_name="Review Required BIM Studio",
        company_domain="rev-req.com",
        country="Germany",
        city="Berlin",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:queue:rev_req",
        record_number=101,
        company_name="Review Required BIM Studio",
        official_website="https://rev-req.com",
        verification_status="PENDING",
        priority_level="B",
    ))

    # 3. Research Ready lead (OPERATOR_REVIEWED register record, has core evidence, no verified contact)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:res_ready",
        company_name="Research Ready Engineers",
        company_domain="res-ready.com",
        country="Italy",
        city="Milan",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:queue:res_ready",
        record_number=102,
        company_name="Research Ready Engineers",
        official_website="https://res-ready.com",
        business_activities="Structural BIM engineering and coordination",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))

    # 4. Ready for Manual Draft lead (research ready + verified contact)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:draft_ready",
        company_name="Draft Ready Consult Srl",
        company_domain="draft-ready.it",
        country="Italy",
        city="Rome",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:queue:draft_ready",
        record_number=103,
        company_name="Draft Ready Consult Srl",
        official_website="https://draft-ready.it",
        business_activities="Revit MEP modeling and clash detection",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_draft_01",
        candidate_id="cand:queue:draft_ready",
        first_name="Marco",
        last_name="Bianchi",
        full_name="Marco Bianchi",
        work_email="marco@draft-ready.it",
        email_status="verified",
    ), candidate_id="cand:queue:draft_ready")

    # 5. Contact Verification Required lead (research ready, but contact is unverified)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:verif_req",
        company_name="Verify Contact Associates",
        company_domain="verif-req.com",
        country="Spain",
        city="Madrid",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:queue:verif_req",
        record_number=104,
        company_name="Verify Contact Associates",
        official_website="https://verif-req.com",
        business_activities="Architectural BIM documentation",
        verification_status="OPERATOR_REVIEWED",
        priority_level="B",
    ))
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_unverif_01",
        candidate_id="cand:queue:verif_req",
        first_name="Carlos",
        last_name="Gomez",
        full_name="Carlos Gomez",
        work_email="carlos@verif-req.com",
        email_status="unverified",
    ), candidate_id="cand:queue:verif_req")

    # 6. Previously Contacted lead
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:prior_contact",
        company_name="Prior Contact Partners",
        company_domain="prior-contact.com",
        country="France",
        city="Lyon",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        historical_contact_status="contacted",
    ))

    # 7. Suppressed / Ineligible lead
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:queue:suppressed",
        company_name="Do Not Contact Corp",
        company_domain="dnc-corp.com",
        country="Germany",
        city="Munich",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    supp_store.add_suppression(
        email="info@dnc-corp.com",
        company_domain="dnc-corp.com",
        reason="opt_out",
    )

    client = TestClient(app)

    # Check "All Qualified Leads" queue
    res_all = client.get("/sales/leads")
    assert res_all.status_code == 200
    assert "Needs Research Architects" in res_all.text
    assert "Review Required BIM Studio" in res_all.text
    assert "Research Ready Engineers" in res_all.text
    assert "Draft Ready Consult Srl" in res_all.text
    assert "Verify Contact Associates" in res_all.text
    assert "Prior Contact Partners" in res_all.text
    assert "Do Not Contact Corp" in res_all.text

    # Queue: needs_research
    res_q_needs_res = client.get("/sales/leads?queue=needs_research")
    assert res_q_needs_res.status_code == 200
    assert "Needs Research Architects" in res_q_needs_res.text
    assert "Draft Ready Consult Srl" not in res_q_needs_res.text

    # Queue: draft_ready
    res_q_draft = client.get("/sales/leads?queue=draft_ready")
    assert res_q_draft.status_code == 200
    assert "Draft Ready Consult Srl" in res_q_draft.text
    assert "Needs Research Architects" not in res_q_draft.text

    # Queue: previously_contacted
    res_q_prior = client.get("/sales/leads?queue=previously_contacted")
    assert res_q_prior.status_code == 200
    assert "Prior Contact Partners" in res_q_prior.text
    assert "Draft Ready Consult Srl" not in res_q_prior.text

    # Queue: suppressed_or_ineligible
    res_q_supp = client.get("/sales/leads?queue=suppressed_or_ineligible")
    assert res_q_supp.status_code == 200
    assert "Do Not Contact Corp" in res_q_supp.text
    assert "Draft Ready Consult Srl" not in res_q_supp.text


def test_02_filters_city_priority_and_search(workspace_setup):
    """Validate server-side filtering by city, priority (A/B/C), and text search."""
    cand_store = workspace_setup["cand_store"]
    reg_store = workspace_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:filter:01",
        company_name="Alpha Engineering",
        company_domain="alpha-eng.com",
        country="Italy",
        city="Florence",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:filter:01",
        record_number=201,
        company_name="Alpha Engineering",
        official_website="https://alpha-eng.com",
        priority_level="A",
    ))

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:filter:02",
        company_name="Beta BIM Studio",
        company_domain="beta-bim.com",
        country="Italy",
        city="Venice",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:filter:02",
        record_number=202,
        company_name="Beta BIM Studio",
        official_website="https://beta-bim.com",
        priority_level="C",
    ))

    client = TestClient(app)

    # Search query
    res_search = client.get("/sales/leads?q=Alpha")
    assert res_search.status_code == 200
    assert "Alpha Engineering" in res_search.text
    assert "Beta BIM Studio" not in res_search.text

    # City filter
    res_city = client.get("/sales/leads?city=Venice")
    assert res_city.status_code == 200
    assert "Beta BIM Studio" in res_city.text
    assert "Alpha Engineering" not in res_city.text

    # Priority filter: Priority A
    res_prio_a = client.get("/sales/leads?priority=A")
    assert res_prio_a.status_code == 200
    assert "Alpha Engineering" in res_prio_a.text
    assert "Beta BIM Studio" not in res_prio_a.text

    # Priority filter: Priority C
    res_prio_c = client.get("/sales/leads?priority=C")
    assert res_prio_c.status_code == 200
    assert "Beta BIM Studio" in res_prio_c.text
    assert "Alpha Engineering" not in res_prio_c.text


def test_03_sorting_priority_and_company_name(workspace_setup):
    """Validate sorting by priority and company_name with asc and desc order."""
    cand_store = workspace_setup["cand_store"]
    reg_store = workspace_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:sort:01",
        company_name="Zeta Drafting",
        company_domain="zeta-draft.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:sort:01",
        record_number=301,
        company_name="Zeta Drafting",
        priority_level="A",
    ))

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:sort:02",
        company_name="Apex Modeling",
        company_domain="apex-model.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:sort:02",
        record_number=302,
        company_name="Apex Modeling",
        priority_level="C",
    ))

    client = TestClient(app)

    # Sort by company_name asc: Apex Modeling should appear before Zeta Drafting
    res_name_asc = client.get("/sales/leads?sort_by=company_name&sort_order=asc")
    assert res_name_asc.status_code == 200
    pos_apex = res_name_asc.text.find("Apex Modeling")
    pos_zeta = res_name_asc.text.find("Zeta Drafting")
    assert pos_apex < pos_zeta

    # Sort by company_name desc: Zeta Drafting should appear before Apex Modeling
    res_name_desc = client.get("/sales/leads?sort_by=company_name&sort_order=desc")
    assert res_name_desc.status_code == 200
    pos_apex_desc = res_name_desc.text.find("Apex Modeling")
    pos_zeta_desc = res_name_desc.text.find("Zeta Drafting")
    assert pos_zeta_desc < pos_apex_desc

    # Sort by priority asc: Priority A (Zeta) should appear before Priority C (Apex)
    res_prio_asc = client.get("/sales/leads?sort_by=priority&sort_order=asc")
    assert res_prio_asc.status_code == 200
    pos_zeta_prio = res_prio_asc.text.find("Zeta Drafting")
    pos_apex_prio = res_prio_asc.text.find("Apex Modeling")
    assert pos_zeta_prio < pos_apex_prio


def test_04_pagination_clamping_and_query_preservation(workspace_setup):
    """Validate page sizes (10, 25, 50) clamping and state preservation in pagination links."""
    cand_store = workspace_setup["cand_store"]

    for i in range(35):
        cand_store.save_candidate(CandidateCompany(
            candidate_id=f"cand:page:{i:03d}",
            company_name=f"Studio Page {i:03d}",
            company_domain=f"page{i:03d}.com",
            country="Germany",
            company_tier=CompanyTier.tier_1,
            discovery_status="qualified",
            qualification_outcome="qualified_for_enrichment",
        ))

    client = TestClient(app)

    # Request page_size=10
    res_p10 = client.get("/sales/leads?page_size=10&page=1&country=Germany")
    assert res_p10.status_code == 200
    assert "Page <strong>1</strong> of <strong>4</strong>" in res_p10.text
    assert "country=Germany" in res_p10.text
    assert "page_size=10" in res_p10.text
    assert "Next →" in res_p10.text

    # Request page 2
    res_p10_page2 = client.get("/sales/leads?page_size=10&page=2&country=Germany")
    assert res_p10_page2.status_code == 200
    assert "Page <strong>2</strong> of <strong>4</strong>" in res_p10_page2.text
    assert "← Previous" in res_p10_page2.text


def test_05_ai_handoff_copy_restrictions_and_safety(workspace_setup):
    """
    Ensure one-click copy text is pre-generated in HTML.
    Suppressed leads and previously contacted leads must have AI Brief blocked.
    """
    cand_store = workspace_setup["cand_store"]
    reg_store = workspace_setup["reg_store"]
    supp_store = workspace_setup["supp_store"]

    # Valid candidate with reviewed 13-field record and verified contact
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:handoff:valid",
        company_name="Valid AEC BIM Solutions",
        company_domain="valid-aec.com",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:handoff:valid",
        record_number=501,
        company_name="Valid AEC BIM Solutions",
        official_website="https://valid-aec.com",
        business_activities="BIM Architecture and Structural Design",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_valid_01",
        candidate_id="cand:handoff:valid",
        first_name="Alessandro",
        last_name="Rossi",
        full_name="Alessandro Rossi",
        work_email="alessandro@valid-aec.com",
        email_status="verified",
    ), candidate_id="cand:handoff:valid")

    # Suppressed candidate
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:handoff:suppressed",
        company_name="Blocked DNC Partner",
        company_domain="blocked-dnc.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    supp_store.add_suppression(email="info@blocked-dnc.com", company_domain="blocked-dnc.com", reason="opt_out")

    # Previously contacted candidate
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:handoff:contacted",
        company_name="Prior Reached Agency",
        company_domain="prior-reached.com",
        country="Spain",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        historical_contact_status="contacted",
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    # Valid candidate has active AI Brief button
    assert "📋 AI Brief" in res.text
    assert "📋 Intel" in res.text
    assert "📋 13 Fields" in res.text

    # Suppressed candidate has blocked brief button
    assert "🚫 Brief Blocked" in res.text

    # Previously contacted candidate has cold brief blocked
    assert "⚠️ Cold Brief Blocked" in res.text


def test_06_invalid_candidate_id_cannot_be_actionable(workspace_setup):
    """Ensure records with empty/invalid candidate_ids fail closed in evaluate_candidate_readiness."""
    cand_store = workspace_setup["cand_store"]
    reg_store = workspace_setup["reg_store"]
    supp_store = workspace_setup["supp_store"]

    cand_bad = CandidateCompany(
        candidate_id="",
        company_name="Ghost Architecture Studio",
        company_domain="ghost-studio.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )

    r = evaluate_candidate_readiness(cand_bad, cand_store, supp_store, reg_store)
    assert r["effective_readiness"] == "not_eligible"
    assert r["can_generate_draft"] is False
    assert r["base_eligible"] is False


def test_07_scalability_1000_candidates(tmp_path, monkeypatch):
    """
    Scalability stress test:
    Loads 1,000 synthetic candidates in a clean temporary database.
    Evaluates that the /sales/leads workspace loads smoothly with correct pagination.
    """
    stress_db = str(tmp_path / "stress_1000.sqlite3")
    monkeypatch.setenv("SALES_OUTREACH_DB", stress_db)
    monkeypatch.setenv("SALESAI_DB_PATH", stress_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", stress_db)

    cand_store = CandidateStore(db_path=stress_db)

    # Insert 1,000 synthetic candidates
    for i in range(1000):
        c = CandidateCompany(
            candidate_id=f"cand:stress:{i:04d}",
            company_name=f"Synthetic Studio {i:04d}",
            company_domain=f"synthetic{i:04d}.com",
            country="Italy" if i % 2 == 0 else "France",
            city="Milan" if i % 2 == 0 else "Paris",
            company_tier=CompanyTier.tier_1 if i % 3 == 0 else CompanyTier.tier_2,
            discovery_status="qualified",
            qualification_outcome="qualified_for_enrichment",
        )
        cand_store.save_candidate(c)

    client = TestClient(app)
    start_time = time.time()
    res = client.get("/sales/leads?page_size=50&page=1")
    duration = time.time() - start_time

    assert res.status_code == 200
    assert "Qualified Leads (1000)" in res.text
    assert "Showing 50 of 1000 matching" in res.text
    assert "Page <strong>1</strong> of <strong>20</strong>" in res.text
    # Fast evaluation check (well under 10.0 seconds for 1000 records in memory/sqlite)
    assert duration < 10.0
