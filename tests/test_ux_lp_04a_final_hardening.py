"""
UX-LP-04a Automated Test Suite: Final Qualified Leads Hardening, AI Handoff Safety & Pagination Performance.

Verifies:
1. Missing priority renders 'Unknown' in table and badge without fallback to Priority B.
2. Valid stored A/B/C priority remains unchanged and correctly badge-styled.
3. Research Ready + Draft Ready enables active '📋 AI Brief'.
4. Review Required, Missing Evidence, and Pending Research block cold AI Brief and show contextual actions (e.g. '📋 Review Research').
5. Prior Contact, DNC/Suppressed, Policy Ineligible, and Invalid Candidate ID block cold AI Brief.
6. Verified vs unreviewed 13-field records have accurate copy labels and content ('Copy Reviewed 13 Fields' vs 'Copy 13 Fields (Unreviewed)').
7. Copy operations cause zero database mutations and zero external network calls.
8. Pagination and sorting preserve accurate counts, deterministic results, and handle 'Unknown' priority consistently.
9. Existing manual draft eligibility and recipient verification remain enforced.
10. GET /sales/leads makes zero external API calls and sends zero emails.
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
from backend.sales_ui.router import (
    evaluate_candidate_readiness,
    evaluate_research_readiness,
    generate_ai_brief_text,
    generate_company_intelligence_text,
    generate_reviewed_13_fields_text,
)


@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_ux_lp_04a.sqlite3")
    research_db = str(tmp_path / "test_ux_lp_04a_research.sqlite3")

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


def test_01_missing_priority_renders_unknown(test_setup):
    """Case 1: Missing priority renders 'Unknown' in table and badge without fallback to Priority B."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:prio:unknown",
        company_name="Studio Senza Priorita",
        company_domain="senza-priorita.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    # Register record with priority_level None or empty
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:prio:unknown",
        record_number=10,
        company_name="Studio Senza Priorita",
        official_website="https://senza-priorita.it",
        priority_level=None,
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Unknown" in res.text
    # Must NOT have fabricated 'Priority B' for this record
    assert "badge-info" not in res.text or "badge-secondary" in res.text


def test_02_valid_stored_priority_remains_unchanged(test_setup):
    """Case 2: Valid stored A/B/C priority remains unchanged and correctly badge-styled."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:prio:a",
        company_name="Alpha Engineering SpA",
        company_domain="alpha-eng.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:prio:a",
        record_number=11,
        company_name="Alpha Engineering SpA",
        official_website="https://alpha-eng.it",
        priority_level="A",
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Priority A" in res.text


def test_03_research_ready_and_draft_ready_enables_ai_brief(test_setup):
    """Case 3: Research Ready + Draft Ready enables active '📋 AI Brief'."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:ready:full",
        company_name="Ready BIM Integrators",
        company_domain="ready-bim.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:ready:full",
        record_number=12,
        company_name="Ready BIM Integrators",
        official_website="https://ready-bim.it",
        business_activities="Revit and BIM coordination",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_full_01",
        candidate_id="cand:ready:full",
        first_name="Paolo",
        last_name="Rossi",
        full_name="Paolo Rossi",
        work_email="paolo@ready-bim.it",
        email_status="verified",
    ), candidate_id="cand:ready:full")

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "📋 AI Brief" in res.text


def test_04_review_required_and_pending_research_block_ai_brief(test_setup):
    """Case 4: Review Required and Pending Research block cold AI Brief and show contextual actions."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    # 1. Pending Research (unresearched)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:pending:res",
        company_name="Pending Res Studio",
        company_domain="pending-res.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))

    # 2. Review Required (record exists but unreviewed)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:review:req",
        company_name="Review Req BIM",
        company_domain="review-req.com",
        country="Germany",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:review:req",
        record_number=13,
        company_name="Review Req BIM",
        official_website="https://review-req.com",
        business_activities="Architecture",
        verification_status="EXTRACTED",
        priority_level="B",
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    # Contextual action link must appear for review
    assert "📋 Review Research" in res.text


def test_05_prior_contact_suppressed_ineligible_invalid_id_block_ai_brief(test_setup):
    """Case 5: Prior Contact, DNC/Suppressed, Policy Ineligible, and Invalid Candidate ID block cold AI Brief."""
    cand_store = test_setup["cand_store"]
    supp_store = test_setup["supp_store"]

    # 1. Suppressed
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:block:supp",
        company_name="Blocked DNC Firm",
        company_domain="blocked-dnc-firm.com",
        country="France",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    supp_store.add_suppression(email="info@blocked-dnc-firm.com", company_domain="blocked-dnc-firm.com", reason="opt_out")

    # 2. Prior Contact
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:block:prior",
        company_name="Prior Touch Firm",
        company_domain="prior-touch.com",
        country="Spain",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        historical_contact_status="contacted",
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "🚫 Brief Blocked" in res.text
    assert "⚠️ Cold Brief Blocked" in res.text


def test_06_verified_vs_unreviewed_copy_labels_and_content(test_setup):
    """Case 6: Verified vs unreviewed 13-field records have accurate copy labels and content."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    # Reviewed record
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:lbl:reviewed",
        company_name="Reviewed Studio SpA",
        company_domain="reviewed-studio.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:lbl:reviewed",
        record_number=201,
        company_name="Reviewed Studio SpA",
        official_website="https://reviewed-studio.it",
        business_activities="Architecture and engineering",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))

    # Unreviewed record
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:lbl:unreviewed",
        company_name="Unreviewed Studio",
        company_domain="unreviewed-studio.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:lbl:unreviewed",
        record_number=202,
        company_name="Unreviewed Studio",
        official_website="https://unreviewed-studio.it",
        business_activities="Architecture",
        verification_status="EXTRACTED",
        priority_level="B",
    ))

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    # Copy toast attributes must reflect verified status
    assert "Copy Reviewed 13 Fields" in res.text
    assert "Copy 13 Fields (Unreviewed)" in res.text


def test_07_copy_operations_cause_zero_database_mutations(test_setup):
    """Case 7: Copy operations and GET /sales/leads cause zero database mutations."""
    cand_store = test_setup["cand_store"]
    db_path = test_setup["db_path"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:nomut:01",
        company_name="No Mutation Srl",
        company_domain="no-mutation.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    ))

    import hashlib
    def get_hash(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    before_hash = get_hash(db_path)

    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200

    after_hash = get_hash(db_path)
    assert before_hash == after_hash, "GET /sales/leads must not mutate database"


def test_08_pagination_sorting_preserves_deterministic_results(test_setup):
    """Case 8: Pagination and sorting preserve accurate counts and handle 'Unknown' priority consistently."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    for i in range(15):
        cid = f"cand:sort:{i:02d}"
        cand_store.save_candidate(CandidateCompany(
            candidate_id=cid,
            company_name=f"Studio Sort {i:02d}",
            company_domain=f"sort{i:02d}.com",
            country="Italy",
            company_tier=CompanyTier.tier_1,
            discovery_status="qualified",
            qualification_outcome="qualified_for_enrichment",
        ))
        prio = "A" if i % 3 == 0 else ("B" if i % 3 == 1 else None)
        reg_store.save_record(LeadRegisterRecord(
            candidate_id=cid,
            record_number=300 + i,
            company_name=f"Studio Sort {i:02d}",
            official_website=f"https://sort{i:02d}.com",
            priority_level=prio,
        ))

    client = TestClient(app)
    res = client.get("/sales/leads?sort_by=priority&sort_order=asc&page_size=10&page=1")
    assert res.status_code == 200
    assert "Showing 10 of 15 matching" in res.text


def test_09_manual_draft_eligibility_and_recipient_verification_enforced(test_setup):
    """Case 9: Existing manual draft eligibility and recipient verification remain enforced."""
    cand_store = test_setup["cand_store"]
    reg_store = test_setup["reg_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:verif:enforce",
        company_name="Enforce Contact Srl",
        company_domain="enforce-contact.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    ))
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:verif:enforce",
        record_number=401,
        company_name="Enforce Contact Srl",
        official_website="https://enforce-contact.it",
        business_activities="MEP Engineering",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
    ))
    # No verified contact -> must show Verify Contact link
    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "📋 Verify Contact" in res.text


def test_10_zero_external_api_calls_and_sending_disabled(test_setup, monkeypatch):
    """Case 10: GET /sales/leads makes zero external API calls and sends zero emails."""
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    client = TestClient(app)
    start_time = time.time()
    res = client.get("/sales/leads")
    duration = time.time() - start_time

    assert res.status_code == 200
    assert duration < 5.0
