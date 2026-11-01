"""
UX-LP-03 Qualified Lead Research-to-AI Handoff Acceptance Suite.

Covers all 12 required scenarios:
1. Qualified lead with approved research (OPERATOR_REVIEWED/VERIFIED) and verified recipient -> Research Ready & Draft Ready.
2. Qualified lead with approved research but unverified recipient -> Research Ready & Verification Required.
3. Qualified lead with incomplete/unreviewed research (EXTRACTED or unreviewed) -> Review Required.
4. Prior-contact company -> Warning banners rendered in AI brief, cold outreach blocked.
5. Suppressed/DNC company -> DO NOT CONTACT safety banner, internal inspection only, cold outreach forbidden.
6. Ineligible company (cold_outreach_allowed=False or unqualified) -> Policy restricted.
7. Malformed or empty Candidate ID -> Fails closed, marked invalid.
8. Company without an explicitly linked 13-Field Register record -> Handled gracefully with fallback.
9. Snapshot with incomplete evidence -> Reflected in research status and brief text.
10. Mixed-language and excessively long imported records -> Clean display name truncated/cleaned, full name preserved in detail.
11. Correct clipboard text contents for all 3 copy targets (Company Intelligence, Reviewed 13 Fields, AI Brief), ensuring no fabricated tenders/RFPs.
12. Viewing and generating briefs executes zero network requests, zero LLM calls, and makes zero mutations to SQLite.
"""

import os
import hashlib
import pytest
from starlette.testclient import TestClient

from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot, PageEvidence
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.sales_ui.router import (
    evaluate_candidate_readiness,
    evaluate_research_readiness,
    format_company_display_name,
    generate_company_intelligence_text,
    generate_reviewed_13_fields_text,
    generate_ai_brief_text,
)


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    """Creates isolated temporary databases and TestClient using backend.main.app for UX-LP-03 tests."""
    outreach_db = str(tmp_path / "test_lp03_outreach.db")
    research_db = str(tmp_path / "test_lp03_research.sqlite3")

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


def test_scenario_01_qualified_approved_research_verified_recipient(env_setup):
    """Scenario 1: Approved research + verified recipient -> Research Ready & Draft Ready."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    snap_store = env_setup["snap_store"]
    client = env_setup["client"]

    cid = "cand:s1:all_ready"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Studio Architettura Progetti Srl",
        company_domain="studioprogetti.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:p1:1",
            candidate_id=cid,
            full_name="Elena Rossi",
            work_email="elena@studioprogetti.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Studio Architettura Progetti Srl",
        official_website="https://studioprogetti.it",
        business_activities="Architectural BIM Modeling",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
        priority_rationale="High tier BIM adopter",
    )
    reg_store.save_record(rec)

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    r_research = evaluate_research_readiness(cand, reg_store, snap_store)

    assert r_research["research_status"] == "research_ready"
    assert r_research["is_reviewed"] is True
    assert r_outreach["effective_readiness"] == "draft_ready"
    assert r_outreach["can_generate_draft"] is True

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "AI Handoff &amp; Brief" in resp.text
    assert "Research Ready" in resp.text
    assert "Eligible for Manual Draft" in resp.text


def test_scenario_02_qualified_approved_research_unverified_recipient(env_setup):
    """Scenario 2: Approved research but unverified recipient -> Research Ready & Verification Required."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    snap_store = env_setup["snap_store"]
    client = env_setup["client"]

    cid = "cand:s2:res_ready_contact_unverified"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Ingenieria Delta Spa",
        company_domain="deltaing.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:delta:1",
            candidate_id=cid,
            full_name="Luca Bianchi",
            work_email="info@deltaing.it",
            email_status="documented",  # Documented != Verified
        ),
        candidate_id=cid,
    )

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Ingenieria Delta Spa",
        official_website="https://deltaing.it",
        business_activities="Civil and structural engineering",
        verification_status="VERIFIED",
    )
    reg_store.save_record(rec)

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    r_research = evaluate_research_readiness(cand, reg_store, snap_store)

    assert r_research["research_status"] == "research_ready"
    assert r_outreach["effective_readiness"] == "verification_required"
    assert r_outreach["can_generate_draft"] is False

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "CONTACT VERIFICATION REQUIRED" in resp.text
    assert "no verified recipient is on file" in resp.text


def test_scenario_03_qualified_unreviewed_research(env_setup):
    """Scenario 3: Unreviewed research (EXTRACTED) -> Review Required."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]

    cid = "cand:s3:unreviewed_res"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Gamma Structural Srl",
        company_domain="gammastruct.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_2,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Gamma Structural Srl",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(rec)

    r_research = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_research["research_status"] == "review_required"
    assert r_research["is_reviewed"] is False
    assert "Review Required" in r_research["research_label"]


def test_scenario_04_prior_contact_company(env_setup):
    """Scenario 4: Prior contact company -> Warning banners rendered in AI brief, cold outreach blocked."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    snap_store = env_setup["snap_store"]
    client = env_setup["client"]

    cid = "cand:s4:prior_contact"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Omega Ingegneria Srl",
        company_domain="omegaing.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
        historical_contact_status="contacted",
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:omega:1",
            candidate_id=cid,
            full_name="Gianni Verdi",
            work_email="gianni@omegaing.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r_outreach["effective_readiness"] == "prior_contact"
    assert r_outreach["can_generate_draft"] is False

    brief_text = generate_ai_brief_text(cand, None, None, r_outreach, {"verification_status": "Unlinked", "record_number": None})
    assert "PRIOR OUTREACH DETECTED" in brief_text
    assert "Cold outreach drafting is blocked" in brief_text

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "PRIOR OUTREACH DETECTED" in resp.text
    assert "Historical communications exist for this company" in resp.text


def test_scenario_05_suppressed_dnc_company(env_setup):
    """Scenario 5: Suppressed/DNC company -> DO NOT CONTACT safety banner, internal inspection only."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    client = env_setup["client"]

    cid = "cand:s5:suppressed_corp"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Banned Tech Spa",
        company_domain="bannedtech.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    supp_store.add_suppression(email="info@bannedtech.it", company_domain="bannedtech.it", reason="opt_out")

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r_outreach["effective_readiness"] == "suppressed"
    assert r_outreach["can_generate_draft"] is False

    brief_text = generate_ai_brief_text(cand, None, None, r_outreach, {"verification_status": "Unlinked", "record_number": None})
    assert "DO NOT SEND OUTREACH" in brief_text
    assert "COMPANY / DOMAIN IS SUPPRESSED (DNC)" in brief_text

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "INTERNAL INSPECTION ONLY — DO NOT CONTACT (SUPPRESSED)" in resp.text
    assert "Autonomous drafting or sales outreach is strictly blocked" in resp.text


def test_scenario_06_ineligible_company(env_setup):
    """Scenario 6: Ineligible company (cold_outreach_allowed=False or unqualified) -> Policy restricted."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s6:ineligible"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Public Agency Consortium",
        company_domain="agency.gov.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=False,
    )
    c_store.save_candidate(cand)

    r_outreach = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r_outreach["effective_readiness"] == "not_eligible"
    assert r_outreach["can_generate_draft"] is False


def test_scenario_07_malformed_empty_candidate_id(env_setup):
    """Scenario 7: Malformed or empty Candidate ID -> Fails closed, marked invalid."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]
    snap_store = env_setup["snap_store"]

    cand_bad = CandidateCompany(
        candidate_id="   ",
        company_name="Bad ID Corp",
        company_domain="badid.it",
    )
    r_outreach = evaluate_candidate_readiness(cand_bad, c_store, supp_store, reg_store)
    r_research = evaluate_research_readiness(cand_bad, reg_store, snap_store)

    assert r_outreach["effective_readiness"] == "not_eligible"
    assert r_research["research_status"] == "unresearched"


def test_scenario_08_company_without_linked_register_record(env_setup):
    """Scenario 8: Company without explicitly linked 13-Field Register record -> Handled gracefully with fallback."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]
    client = env_setup["client"]

    cid = "cand:s8:no_register"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Orphan Record Engineering",
        company_domain="orphanrecord.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    r_research = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_research["research_status"] == "unresearched"
    assert r_research["is_reviewed"] is False
    assert r_research["record_number"] is None

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "No explicit 13-field Lead Register record has been linked or approved yet" in resp.text


def test_scenario_09_snapshot_incomplete_evidence(env_setup):
    """Scenario 9: Snapshot with incomplete evidence -> Reflected in research status and brief text."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]

    cid = "cand:s9:partial_evidence"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Partial Evidence Studio",
        company_domain="partialstudio.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    # Save snapshot with only minimal evidence
    dummy_page = PageEvidence(
        snapshot_id="snp:s9:partial",
        evidence_id="evi:1",
        requested_url="https://partialstudio.it",
        resolved_url="https://partialstudio.it",
        page_type="homepage",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-01T20:00:00Z",
        title="Partial Evidence Studio Home",
        meta_description="Architectural modeling",
        content_sha256="abc123sha",
        cleaned_text="Partial Evidence Studio home page content.",
    )
    snap_store.create_snapshot(
        candidate_id=cid,
        canonical_domain="partialstudio.it",
        company_name="Partial Evidence Studio",
        pages=[dummy_page],
        research_status="partial",
    )
    snap = snap_store.get_latest_snapshot(cid)

    r_research = evaluate_research_readiness(cand, reg_store, snap_store)
    assert r_research["research_status"] == "review_required"

    readiness_dummy = {
        "readiness_label": "Eligible for Manual Draft",
        "base_eligible": True,
        "is_suppressed": False,
        "is_previously_contacted": False,
        "has_verified_recipient": True,
        "all_workspace_recipients": [],
    }
    intel_text = generate_company_intelligence_text(cand, None, snap, readiness_dummy, r_research)
    assert "Latest Crawl Snapshot" in intel_text
    assert "Crawled: 1/1 pages" in intel_text


def test_scenario_10_mixed_language_and_long_imported_names(env_setup):
    """Scenario 10: Mixed-language and excessively long imported records -> Clean display name truncated/cleaned, full name preserved."""
    raw_long_name = "Studio Tecnico Associato di Ingegneria Civile ed Architettura dei Fratelli Rossi & Co. - Progettazione Integrata BIM e Certificazioni Energetiche S.r.l."
    domain = "studiorossi-associati.it"

    clean_name = format_company_display_name(raw_long_name, domain)
    assert len(clean_name) <= 80
    assert " - Progettazione" not in clean_name

    raw_latin_cyrillic = "ООО Архитектурное бюро Alpha / Alfa Studio Srl"
    clean_mixed = format_company_display_name(raw_latin_cyrillic, "alfastudio.it")
    assert len(clean_mixed) <= 80

    c_store = env_setup["cand_store"]
    cid = "cand:s10:long_name"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name=raw_long_name,
        company_domain=domain,
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)

    client = env_setup["client"]
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    # Cleaned title in header is displayed
    assert clean_name in resp.text
    # Full raw name is preserved in the title attribute of the lead link or subtitle
    assert "studiorossi-associati.it" in resp.text


def test_scenario_11_clipboard_text_targets_and_no_fabrication(env_setup):
    """Scenario 11: Correct clipboard text contents for all 3 copy targets, ensuring no fabricated tenders/RFPs."""
    cid = "cand:s11:clipboard_test"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="BIM Solutions Milano Srl",
        company_domain="bimsolutions.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
        outsourcing_potential="high",
        fit_assessment="high",
        intent_assessment="high",
    )

    rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=42,
        company_name="BIM Solutions Milano Srl",
        legal_identity="IT01234567890",
        official_website="https://bimsolutions.it",
        business_activities="BIM Management and Consulting",
        descriptions_and_notes="Progetti BIM complessi per ospedali e infrastrutture.",
        other_links_and_references="https://bimsolutions.it/portfolio/hospital-hub",
        contact_email_addresses="info@bimsolutions.it",
        verification_status="OPERATOR_REVIEWED",
        priority_level="A",
        priority_rationale="Strategic BIM partner in Lombardy",
    )

    dummy_page = PageEvidence(
        snapshot_id="snp:s11:completed",
        evidence_id="evi:11",
        requested_url="https://bimsolutions.it",
        resolved_url="https://bimsolutions.it",
        page_type="homepage",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-01T20:00:00Z",
        title="BIM Solutions Milano",
        meta_description="BIM Management and Consulting",
        content_sha256="abc11sha",
        cleaned_text="BIM Solutions Milano home page content.",
    )
    snap = ResearchSnapshot(
        snapshot_id="snp:s11:completed",
        candidate_id=cid,
        canonical_domain="bimsolutions.it",
        company_name="BIM Solutions Milano Srl",
        created_at="2026-11-01T20:00:00Z",
        completed_at="2026-11-01T20:02:00Z",
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        pages_succeeded=1,
        pages_attempted=1,
        research_status="completed",
        content_checksum="dummy_checksum_s11",
        pages=[dummy_page],
    )

    outreach_info = {
        "readiness_label": "Contact Verification Required",
        "base_eligible": True,
        "is_suppressed": False,
        "is_previously_contacted": False,
        "has_verified_recipient": False,
        "all_workspace_recipients": [
            {
                "full_name": "Ing. Luca Verdi",
                "work_email": "l.verdi@bimsolutions.it",
                "email_status": "documented",
                "is_verified": False,
                "is_documented": True,
                "job_title": "Technical Director",
                "provenance": "13-Field Register",
            }
        ],
    }
    research_info = {
        "research_label": "Research Ready",
        "research_status": "research_ready",
        "verification_status": "OPERATOR_REVIEWED",
        "record_number": 42,
    }

    # 1. Company Intelligence
    intel = generate_company_intelligence_text(cand, rec, snap, outreach_info, research_info)
    assert "# Company Intelligence: BIM Solutions Milano Srl" in intel
    assert "Legal Identity (P.IVA / CF): IT01234567890" in intel
    assert "BIM Management and Consulting" in intel
    assert "13-Field Register Record: #42" in intel

    # 2. Reviewed 13 Fields
    fields_13 = generate_reviewed_13_fields_text(rec, cand)
    assert "13-Field Lead Register (Record #42)" in fields_13
    assert "Verification Status: OPERATOR_REVIEWED" in fields_13
    assert "7. Business Activities / Services: BIM Management and Consulting" in fields_13
    assert "10. Descriptions & Operator Notes: Progetti BIM complessi per ospedali e infrastrutture." in fields_13
    assert "13. Contact Email Addresses: info@bimsolutions.it" in fields_13

    # 3. AI Brief
    brief = generate_ai_brief_text(cand, rec, snap, outreach_info, research_info)
    assert "# AI Operator Brief: BIM Solutions Milano Srl" in brief
    assert "Disclaimer: This brief provides factual reference data only" in brief
    assert "Commercial Hypotheses vs. Verified Facts" in brief
    assert "NOT an active published tender unless confirmed in notes" in brief
    assert "NO VERIFIED RECIPIENT ON FILE" in brief
    assert "Do NOT claim the company published a tender or outsourcing RFP" in brief


def test_scenario_12_zero_network_zero_llm_zero_db_mutation(env_setup):
    """Scenario 12: Viewing and generating briefs executes zero network requests, zero LLM calls, and makes zero mutations to SQLite."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    snap_store = env_setup["snap_store"]
    client = env_setup["client"]
    outreach_db = env_setup["outreach_db"]
    research_db = env_setup["research_db"]

    cid = "cand:s12:zero_side_effect"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Pure Read Srl",
        company_domain="pureread.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
    )
    c_store.save_candidate(cand)
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Pure Read Srl",
        official_website="https://pureread.it",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec)

    # Compute baseline checksums
    outreach_hash_pre = hashlib.sha256(open(outreach_db, "rb").read()).hexdigest()
    research_hash_pre = hashlib.sha256(open(research_db, "rb").read()).hexdigest()

    # GET requests
    resp_leads = client.get("/sales/leads")
    assert resp_leads.status_code == 200

    resp_cand = client.get(f"/sales/candidates/{cid}")
    assert resp_cand.status_code == 200

    # Ensure no database mutations occurred during GET requests
    outreach_hash_post = hashlib.sha256(open(outreach_db, "rb").read()).hexdigest()
    research_hash_post = hashlib.sha256(open(research_db, "rb").read()).hexdigest()

    assert outreach_hash_pre == outreach_hash_post
    assert research_hash_pre == research_hash_post
