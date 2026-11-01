"""
UX-LP-03a Acceptance Test Suite: AI Handoff Final Integrity Closure.

Focuses on the 8 required integrity closure scenarios:
1. Approved but incomplete research record (missing activities or website) -> missing_evidence state.
2. Approved record with sufficient documented evidence and minimum required info -> research_ready state.
3. Unreviewed 13-field record (EXTRACTED) -> review_required state and labeled as unapproved in copied text.
4. Suppressed/DNC company with AI Brief blocked, while Company Intelligence is permitted with prominent restriction warning.
5. Previously contacted company with cold-outreach brief blocked and instructions to inspect history.
6. Unverified business activities correctly labeled as unverified/extracted in copied text.
7. Existing verified-contact and draft-readiness rules unchanged (Documented != Verified).
8. No mutations or external API requests from page views or copy actions (hash integrity).
"""

import hashlib
import pytest
from starlette.testclient import TestClient

from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.research_store import ResearchSnapshotStore, PageEvidence
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.sales_ui.router import (
    evaluate_candidate_readiness,
    evaluate_research_readiness,
    generate_company_intelligence_text,
    generate_reviewed_13_fields_text,
    generate_ai_brief_text,
)


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    """Creates isolated temporary databases and TestClient using backend.main.app."""
    outreach_db = str(tmp_path / "test_lp03a_outreach.db")
    research_db = str(tmp_path / "test_lp03a_research.sqlite3")

    monkeypatch.setenv("SALES_OUTREACH_DB", outreach_db)
    monkeypatch.setenv("SALESAI_DB_PATH", outreach_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", outreach_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    cand_store = CandidateStore(db_path=outreach_db)
    reg_store = LeadRegisterStore(db_path=outreach_db)
    rev_store = ReviewStore(db_path=outreach_db)
    supp_store = SuppressionStore(rev_store)
    snap_store = ResearchSnapshotStore(db_path=research_db)

    from backend.main import app
    client = TestClient(app)
    dash_resp = client.get("/sales/dashboard")
    csrf_token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    return {
        "client": client,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "snap_store": snap_store,
        "csrf_token": csrf_token,
        "headers": headers,
        "outreach_db": outreach_db,
        "research_db": research_db,
    }


def test_01_approved_but_incomplete_research_record(env_setup):
    """Test 1: Approved but incomplete research record (missing activities or evidence) -> missing_evidence."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]

    cid = "cand:lp03a:incomplete"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Incomplete Data Studio",
        company_domain="incompletestudio.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)

    # Record is OPERATOR_REVIEWED but missing business activities and evidence
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Incomplete Data Studio",
        official_website="https://incompletestudio.it",
        business_activities="",  # Missing required info!
        verification_status="OPERATOR_REVIEWED",
        provenance_source="pilot_research",  # Not operator_edited and no evidence_urls
    )
    reg_store.save_record(rec)

    r_res = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_res["research_status"] == "missing_evidence"
    assert r_res["research_label"] == "Missing Evidence"
    assert r_res["is_reviewed"] is False


def test_02_approved_record_with_sufficient_documented_evidence(env_setup):
    """Test 2: Approved record with sufficient documented evidence -> research_ready."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]

    cid = "cand:lp03a:sufficient"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Soluzioni BIM Complete Srl",
        company_domain="soluzionibim.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Soluzioni BIM Complete Srl",
        official_website="https://soluzionibim.it",
        business_activities="Architectural and Structural BIM Modeling",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
        evidence_urls=["https://soluzionibim.it/services"],
    )
    reg_store.save_record(rec)

    r_res = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_res["research_status"] == "research_ready"
    assert r_res["research_label"] == "Research Ready"
    assert r_res["is_reviewed"] is True


def test_03_unreviewed_13_field_record(env_setup):
    """Test 3: Unreviewed 13-field record (EXTRACTED) -> review_required and labeled as unapproved in copied text."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]

    cid = "cand:lp03a:unreviewed"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Studio Bozza Srl",
        company_domain="studiobozza.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Studio Bozza Srl",
        official_website="https://studiobozza.it",
        business_activities="General architecture",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(rec)

    r_res = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_res["research_status"] == "review_required"

    # In copied 13 fields text:
    text_13 = generate_reviewed_13_fields_text(rec, cand)
    assert "Verification Status: EXTRACTED" in text_13
    assert "Editorial State: Unapproved / Raw Data" in text_13


def test_04_suppressed_company_ai_brief_blocked(env_setup):
    """Test 4: Suppressed company with AI Brief blocked, while Company Intelligence is permitted with prominent restriction warning."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    client = env_setup["client"]

    cid = "cand:lp03a:suppressed"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Suppressed Engineering Srl",
        company_domain="suppressed-eng.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)
    supp_store.add_suppression(email="info@suppressed-eng.it", company_domain="suppressed-eng.it", reason="opt_out")

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r_outreach["is_suppressed"] is True

    # 1. AI Brief text is blocked
    brief_text = generate_ai_brief_text(cand, None, None, r_outreach, {"verification_status": "Unlinked", "record_number": None})
    assert "OUTREACH BRIEF BLOCKED — SUPPRESSED / DO NOT CONTACT" in brief_text
    assert "Generation of sales outreach writing prompts is strictly prohibited" in brief_text

    # 2. Company Intelligence has restriction warning
    intel_text = generate_company_intelligence_text(cand, None, None, r_outreach, {"verification_status": "Unlinked", "research_label": "Pending", "research_status": "unresearched"})
    assert "RESTRICTION NOTICE: INTERNAL FACTUAL REFERENCE ONLY" in intel_text
    assert "Do NOT use this intelligence to initiate or prepare cold sales outreach" in intel_text

    # 3. HTTP View disables AI Brief copy button
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "AI Brief Blocked (Suppressed)" in resp.text
    assert "disabled" in resp.text


def test_05_previously_contacted_company_cold_brief_blocked(env_setup):
    """Test 5: Previously contacted company with cold-outreach brief blocked and instructions to inspect history."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    client = env_setup["client"]

    cid = "cand:lp03a:contacted"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Already Contacted Studio",
        company_domain="alreadycontacted.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        historical_contact_status="contacted",
    )
    c_store.save_candidate(cand)

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r_outreach["is_previously_contacted"] is True

    # AI Brief text is blocked for cold outreach
    brief_text = generate_ai_brief_text(cand, None, None, r_outreach, {"verification_status": "Unlinked", "record_number": None})
    assert "COLD OUTREACH BRIEF BLOCKED — PRIOR CONTACT RECORDED" in brief_text
    assert "inspect Area 5 (Outreach & History) for previous communication logs" in brief_text

    # HTTP View renders disabled button
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "Cold AI Brief Blocked (Prior Contact)" in resp.text


def test_06_unverified_business_activities_correctly_labeled(env_setup):
    """Test 6: Unverified business activities correctly labeled as unverified in copied text."""
    cid = "cand:lp03a:unverified_acts"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Unverified Activities Srl",
        company_domain="unverifiedacts.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Unverified Activities Srl",
        business_activities="Crawled generic engineering services",
        verification_status="EXTRACTED",
    )

    outreach_dummy = {
        "is_suppressed": False,
        "is_previously_contacted": False,
        "readiness_label": "Verification Required",
        "base_eligible": True,
        "has_verified_recipient": False,
        "all_workspace_recipients": [],
    }
    research_dummy = {
        "research_label": "Review Required",
        "verification_status": "EXTRACTED",
        "record_number": 12,
    }

    # Company Intel
    intel_text = generate_company_intelligence_text(cand, rec, None, outreach_dummy, research_dummy)
    assert "## 2. Business Activities & Specializations ([EXTRACTED])" in intel_text

    # AI Brief
    brief_text = generate_ai_brief_text(cand, rec, None, outreach_dummy, research_dummy)
    assert "## Business Activities & Specializations (Source Grounded: [EXTRACTED])" in brief_text


def test_07_existing_verified_contact_and_draft_readiness_unchanged(env_setup):
    """Test 7: Existing verified-contact and draft-readiness rules unchanged (Documented != Verified)."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:lp03a:doc_not_ver"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Studio Valutazione Srl",
        company_domain="studioval.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:val:1",
            candidate_id=cid,
            full_name="Gianni Morandi",
            work_email="gianni@studioval.it",
            email_status="documented",  # Documented in register, not verified
        ),
        candidate_id=cid,
    )

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Studio Valutazione Srl",
        official_website="https://studioval.it",
        business_activities="Consulting",
        contact_email_addresses="gianni@studioval.it",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
    )
    reg_store.save_record(rec)

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    # Effective readiness remains verification_required, can_generate_draft is False
    assert r_outreach["effective_readiness"] == "verification_required"
    assert r_outreach["can_generate_draft"] is False
    assert r_outreach["has_verified_recipient"] is False


def test_08_no_mutations_or_external_api_requests(env_setup):
    """Test 8: No mutations or external API requests from page views or copy actions."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    client = env_setup["client"]
    outreach_db = env_setup["outreach_db"]
    research_db = env_setup["research_db"]

    cid = "cand:lp03a:zero_mut"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Zero Mutation SpA",
        company_domain="zeromutation.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Zero Mutation SpA",
        official_website="https://zeromutation.it",
        business_activities="Zero side-effect modeling",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
    )
    reg_store.save_record(rec)

    h_outreach_pre = hashlib.sha256(open(outreach_db, "rb").read()).hexdigest()
    h_research_pre = hashlib.sha256(open(research_db, "rb").read()).hexdigest()

    resp_leads = client.get("/sales/leads")
    assert resp_leads.status_code == 200

    resp_cand = client.get(f"/sales/candidates/{cid}")
    assert resp_cand.status_code == 200

    h_outreach_post = hashlib.sha256(open(outreach_db, "rb").read()).hexdigest()
    h_research_post = hashlib.sha256(open(research_db, "rb").read()).hexdigest()

    assert h_outreach_pre == h_outreach_post
    assert h_research_pre == h_research_post
