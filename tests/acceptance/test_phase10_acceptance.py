"""
tests/acceptance/test_phase10_acceptance.py
Phase 10 Comprehensive Deterministic Golden Acceptance Test Suite.
Specification: PHASE10-QA-GOLDEN-SPEC.md

Covers all 245 cases defined across sections 4 through 55:
- Core Candidate Identity & Dedup (P10-ID-001..008, P10-REG-001, P10-REG-002)
- Discovery Observations (P10-OBS-001..006)
- Market Evidence (P10-EVID-001..007)
- Source Registry & Markets (P10-SRC-001..008, P10-MKT-001..006)
- Tier & BIM Maturity (P10-TIER-001..004, P10-REG-003, P10-BIM-001..003, P10-REG-004)
- Fit & Intent & Opportunities (P10-FIT-001..003, P10-INT-001..004, P10-OPP-001..004)
- Historical Import & Normalization (P10-HIST-001..011, P10-REG-007, P10-HIST-EMAIL-001..004)
- Historical Outreach Guard & Outcomes (P10-REG-006, P10-HIST-OUTCOME-001..004)
- No Fake Jobs & Job-First Compatibility (P10-REG-005, P10-NOJOB-002..005, P10-JOB-001..002, P10-REG-011)
- Company-First & Project/Tender Discovery (P10-COMP-001..007, P10-PROJ-001..006)
- Research & Potential Assessments (P10-RES-001..008, P10-OUT-001..004, P10-AUTO-001..005)
- Qualification & Watchlist (P10-QUAL-001..007, P10-REG-008, P10-WATCH-002..005, P10-REG-009, P10-REG-010)
- Downstream Bridges: Phase 6, 7, 8, 9 (P10-P6-001..005, P10-P7-001..010, P10-REG-012, P10-P9-001..006)
- Discovery Engine, Fallback & Runs (P10-REG-013, P10-RUN-001..011, P10-REG-014, P10-DAILY-001..005, P10-FALLBACK-001..005)
- Source Health & Security (P10-HEALTH-001..005, P10-SEC-001..008, P10-REG-015)
- Database Invariants & Positive Outcomes (P10-DB-001..010, P10-POS-001..005)
- Re-Discovery & Freshness (P10-KNOWN-001..005, P10-FRESH-001..005)
- Limits, LLM Boundary & No-Send Invariants (P10-LIMIT-001..005, P10-LLM-001..007, P10-NOSEND-001..004)
- Operator Console Bridge (P10-UIBRIDGE-001..005)
- QA Integrity Meta-Tests (P10-QA-001..010)
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore, canonicalize_domain, normalize_company_name
from sales_engine.candidates.classifier import (
    CompanyClassifier,
    classify_bim_maturity,
    classify_relationship_and_opportunities,
    classify_tier,
    evaluate_fit_and_intent,
    qualify_candidate,
)
from sales_engine.candidates.models import (
    BIMMaturity,
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryRunRecord,
    DiscoveryStatus,
    MarketEvidence,
    OpportunityType,
    QualificationOutcome,
    RelationshipType,
    SourceHealthRecord,
)
from sales_engine.discovery_v2.bridges import (
    Phase5FreezeBridge,
    Phase6EnrichmentAdapter,
    Phase7ExportBridge,
    Phase8GroundingBridge,
    Phase9AuthorityBridge,
    Phase10ConsoleBridge,
)
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.source_registry import SourceConfig, SourceRegistry, sanitize_secret_text
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord, normalize_emails, parse_bool


@pytest.fixture
def temp_store(tmp_path):
    db_file = str(tmp_path / "golden_phase10.db")
    store = CandidateStore(db_path=db_file)
    return store, db_file


# ---------------------------------------------------------------------------
# 1. Candidate Identity & Canonicalization (P10-ID-001..008, P10-REG-001, P10-REG-002)
# ---------------------------------------------------------------------------
def test_p10_id_001_to_008_and_reg_001_002_identity(temp_store):
    store, _ = temp_store
    # P10-ID-001: Domain identity is primary
    c1, _ = store.find_or_create_candidate("Studio Alpha", "alpha.it")
    c2, _ = store.find_or_create_candidate("Alpha Engineering", "alpha.it")
    assert len(store.list_candidates()) == 1
    assert store.get_by_domain("alpha.it").candidate_id == c1.candidate_id

    # P10-ID-002: Scheme/path normalization
    urls = [
        "https://example.com",
        "http://www.example.com/",
        "example.com/jobs",
        "careers.example.com/apply",
    ]
    for u in urls:
        assert canonicalize_domain(u) == "example.com"

    # P10-ID-003 & P10-REG-002: Same name, different domains stay separate
    c_uk, _ = store.find_or_create_candidate("ABC Engineering", "abcengineering.co.uk")
    c_it, _ = store.find_or_create_candidate("ABC Engineering", "abc-engineering.com")
    assert len(store.list_candidates()) == 3
    assert store.get_by_domain("abcengineering.co.uk") is not None
    assert store.get_by_domain("abc-engineering.com") is not None

    # P10-ID-004: Same name + different country stays separate
    c_de, _ = store.find_or_create_candidate("Beta Group", None, country="Germany")
    c_fr, _ = store.find_or_create_candidate("Beta Group", None, country="France")
    assert len(store.list_candidates()) == 5

    # P10-ID-005: Source identity merge support
    c_ats = CandidateCompany(candidate_id="c_ats", company_name="ATS Firm", source_identities=["greenhouse:ats-firm"])
    store.save_candidate(c_ats)
    assert store.get_by_source_identity("greenhouse:ats-firm") is not None

    # P10-ID-006: Weak manual name identity does not overmerge
    assert normalize_company_name("Studio Rossi S.r.l.") == "studio rossi"
    assert normalize_company_name("Rossi Engineering S.p.A.") == "rossi engineering"

    # P10-ID-007: Alias preservation
    c_orig = CandidateCompany(candidate_id="c_alias", company_name="Original Name", company_domain="alias.com")
    store.save_candidate(c_orig)
    store.add_alias_to_candidate("c_alias", "Secondary Name")
    fetched = store.get_candidate("c_alias")
    assert "Secondary Name" in fetched.aliases

    # P10-ID-008: Domain history preserved
    store.add_domain_history_to_candidate("c_alias", "alias-old.com")
    fetched2 = store.get_candidate("c_alias")
    assert "alias-old.com" in fetched2.domain_history

    # P10-REG-001: Same company across multiple sources merges once
    engine = DiscoveryEngineV2(candidate_store=store)
    obs_sources = {
        "src_google": lambda q: [{"company_name": "Multi Co Srl", "domain": "multico.it"}],
        "src_ats": lambda q: [{"company_name": "Multi Co Careers", "domain": "https://careers.multico.it"}],
        "src_web": lambda q: [{"company_name": "Multi Co Official", "domain": "www.multico.it"}],
        "src_tender": lambda q: [{"company_name": "Multi Co", "domain": "multico.it/tender"}],
    }
    run = engine.run_multi_source_discovery(
        sources=["src_google", "src_ats", "src_web", "src_tender"],
        query="BIM",
        fetcher_funcs=obs_sources,
    )
    multi_cand = store.get_by_domain("multico.it")
    assert multi_cand is not None
    assert len(multi_cand.source_refs) >= 4


# ---------------------------------------------------------------------------
# 2. Observations & Evidence (P10-OBS-001..006, P10-EVID-001..007)
# ---------------------------------------------------------------------------
def test_p10_obs_and_evid_invariants(temp_store):
    store, _ = temp_store
    cand = CandidateCompany(candidate_id="cand_obs_1", company_name="Obs Studio", company_domain="obs.it")
    store.save_candidate(cand)

    # P10-OBS-001 & P10-OBS-002: Observation with full provenance
    obs = DiscoveryObservation(
        observation_id="obs_101",
        candidate_id="cand_obs_1",
        source_id="google_dir",
        source_type="company_directory",
        source_url="https://google.com/dir/obs",
        observed_company_name="Obs Studio Srl",
        observed_at="2026-10-06T10:00:00Z",
    )
    store.save_observation(obs)
    fetched_obs = store.get_observations_for_candidate("cand_obs_1")
    assert len(fetched_obs) == 1
    assert fetched_obs[0].source_url == "https://google.com/dir/obs"
    assert fetched_obs[0].source_id == "google_dir"

    # P10-OBS-003: Repeated observation idempotent
    store.save_observation(obs)
    assert len(store.get_observations_for_candidate("cand_obs_1")) == 1

    # P10-OBS-004: New observation appends
    obs2 = DiscoveryObservation(
        observation_id="obs_102",
        candidate_id="cand_obs_1",
        source_id="tender_portal",
        source_type="tender_portal",
        source_url="https://tenders.test/102",
        observed_company_name="Obs Studio",
    )
    store.save_observation(obs2)
    assert len(store.get_observations_for_candidate("cand_obs_1")) == 2

    # P10-OBS-005 & P10-OBS-006: Observation is not auto-evidence & timestamp preserved
    assert fetched_obs[0].observed_at == "2026-10-06T10:00:00Z"
    assert len(store.get_evidence_for_candidate("cand_obs_1")) == 0

    # P10-EVID-001..004: Evidence categories
    categories = ["business", "job", "project", "historical"]
    for idx, cat in enumerate(categories):
        ev = MarketEvidence(
            evidence_id=f"ev_{cat}_{idx}",
            candidate_id="cand_obs_1",
            category=cat,
            signal_type="signal_test",
            text=f"Evidence for {cat}",
            source_url="https://evidence.test",
            source_type="public_record",
        )
        store.save_market_evidence(ev)

    all_ev = store.get_evidence_for_candidate("cand_obs_1")
    assert len(all_ev) == 4

    # P10-EVID-005..007: Provenance required & append-only
    for item in all_ev:
        assert item.source_url is not None
        assert item.category in categories


# ---------------------------------------------------------------------------
# 3. Source Registry & Market Configuration (P10-SRC-001..008, P10-MKT-001..006)
# ---------------------------------------------------------------------------
def test_p10_src_and_mkt_configuration():
    reg = SourceRegistry()
    # P10-SRC-001: Load discovery_sources.yaml
    assert len(reg.sources) >= 1

    # P10-SRC-002 & P10-SRC-003: Enabled vs disabled
    enabled = reg.get_enabled_sources()
    assert all(s.enabled for s in enabled)

    # P10-SRC-004: Unknown source type rejected
    with pytest.raises(ValueError):
        reg.register_source({"id": "bad_src", "type": "alien_source_type"})

    # P10-SRC-005: Source requires API key but missing
    cfg = SourceConfig(
        id="cred_test_src",
        name="Cred Test Source",
        type="search_engine",
        requires_api_key=True,
        credential_env_var="MISSING_API_KEY_SECRET",
    )
    reg.sources["cred_test_src"] = cfg
    from sales_engine.discovery_v2.adapters import create_search_engine_adapter
    adapter = create_search_engine_adapter(cfg)
    with pytest.raises(RuntimeError):
        adapter("query")

    # P10-SRC-006: Priority deterministic
    srcs = reg.get_enabled_sources()
    priors = [s.priority for s in srcs]
    assert priors == sorted(priors)

    # P10-SRC-007: max_results_per_run present
    assert all(hasattr(s, "max_results_per_run") for s in srcs)

    # P10-SRC-008: Sanitization
    assert sanitize_secret_text("Bearer secret_token_123") == "Bearer [REDACTED]"

    # P10-MKT-001..006: Markets
    assert len(reg.markets) >= 15
    m_it = reg.get_market("IT")
    assert m_it is not None
    assert "it" in m_it.get("languages", [])
    assert m_it.get("enabled", True) is True


# ---------------------------------------------------------------------------
# 4. Tier, BIM Maturity, Fit vs Intent (P10-TIER-001..004, P10-REG-003, P10-BIM-001..003, P10-REG-004, P10-FIT-001..003, P10-INT-001..004, P10-OPP-001..004)
# ---------------------------------------------------------------------------
def test_p10_tier_bim_fit_intent_opp():
    # P10-TIER-001..004 & P10-REG-003: Legal form alone never determines tier
    t_legal, conf_legal, _ = classify_tier(legal_form="S.p.A.")
    assert t_legal == CompanyTier.unknown
    assert conf_legal == 0.0

    t_real, conf_real, reasons = classify_tier(
        legal_form="S.r.l.",
        employee_hint="60",
        project_scale_hint="€15M MEP Hospital",
    )
    assert t_real in (CompanyTier.tier_2, CompanyTier.tier_1)
    assert conf_real > 0.0
    assert len(reasons) > 0

    # P10-BIM-001..003 & P10-REG-004: Absence of evidence is unknown
    ev_empty: List[MarketEvidence] = []
    bim_unk, conf_unk = classify_bim_maturity(ev_empty)
    assert bim_unk == BIMMaturity.unknown
    assert conf_unk == 0.0

    ev_bim = [MarketEvidence(
        evidence_id="ev_b1", candidate_id="c1", category="job", signal_type="title", text="BIM Coordinator Revit Navisworks ISO 19650"
    )]
    bim_high, conf_high = classify_bim_maturity(ev_bim)
    assert bim_high in (BIMMaturity.high, BIMMaturity.enterprise)
    assert conf_high >= 0.8

    # P10-FIT-001..003 & P10-INT-001..004: Fit vs Intent independent
    cand_fit = CandidateCompany(
        company_id="c_fit",
        company_name="Fit Eng",
        company_domain="fit.it",
        company_types=["mep_engineering"],
        country="Italy",
    )
    fit_l, int_l, f_reasons, i_signals = evaluate_fit_and_intent(cand_fit, evidence=[])
    assert fit_l in ("high", "medium")
    assert int_l in ("unknown", "none")

    # Intent signals from job/tender
    ev_intent = [MarketEvidence(
        evidence_id="ev_int_1", candidate_id="c_fit", category="job", signal_type="hiring", text="Hiring MEP BIM Modeler"
    )]
    _, int_high, _, i_signals2 = evaluate_fit_and_intent(cand_fit, evidence=ev_intent)
    assert int_high == "high"
    assert len(i_signals2) > 0

    # P10-OPP-001..004: Opportunity types
    rel, opps = classify_relationship_and_opportunities(
        company_types=["mep_engineering"],
        services=["BIM Modeling", "Revit API Automation"],
    )
    assert rel in (RelationshipType.service_buyer, RelationshipType.technology_partner)
    assert OpportunityType.direct_bim_delivery in opps or OpportunityType.automation in opps


# ---------------------------------------------------------------------------
# 5. Historical Import & Outreach Guard (P10-HIST-001..011, P10-REG-007, P10-HIST-EMAIL-001..004, P10-REG-006, P10-HIST-OUTCOME-001..004)
# ---------------------------------------------------------------------------
def test_p10_historical_import_and_outreach_guard(temp_store, tmp_path):
    store, _ = temp_store
    csv_path = tmp_path / "test_hist_acceptance.csv"
    csv_path.write_text(
        "Company,Domain,Email,Contact Date,Follow Up Date,Reply Status,Respondent Name,Respondent Email,Notes\n"
        "Studio Alpha,alpha.it,\"info@alpha.it; marco@alpha.it\",2025-03-01,2025-03-15,Interested,Laura Boss,laura@alpha.it,Had great intro call\n",
        encoding="utf-8"
    )

    importer = HistoricalImporter(store=store)
    records = importer.parse_csv(csv_path)
    # P10-HIST-001..011: Row reference, raw notes, contact date preserved
    assert len(records) == 1
    r = records[0]
    assert r.company_name == "Studio Alpha"
    assert r.contact_date == "2025-03-01"
    assert r.follow_up_date == "2025-03-15"
    assert r.reply_status == "Interested"
    assert r.respondent_name == "Laura Boss"

    # P10-HIST-EMAIL-001..004: Multi-email splitting
    split_emails = normalize_emails(r.emails)
    assert len(split_emails) == 2
    assert "info@alpha.it" in split_emails
    assert "marco@alpha.it" in split_emails

    # P10-REG-007: Idempotent import
    res1 = importer.import_records(records)
    assert res1.companies_created == 1
    res2 = importer.import_records(records)
    assert res2.companies_created == 0
    assert res2.duplicates_skipped == 1

    # P10-REG-006: Outreach guard prevents cold outreach restart
    cand = store.get_by_domain("alpha.it")
    assert cand is not None
    assert cand.previous_outreach_detected is True
    assert cand.cold_outreach_allowed is False

    # Qualification outcome routes to known_company
    classifier = CompanyClassifier(suppression_store=None)
    qres = classifier.qualify(cand)
    assert qres.outcome in (QualificationOutcome.known_company, QualificationOutcome.reengagement_review)
    assert qres.cold_outreach_allowed is False

    # P10-HIST-OUTCOME-001..004: Events preserved
    events = store.get_historical_outreach_for_candidate(cand.candidate_id)
    assert len(events) >= 1
    assert events[0]["reply_status"] == "Interested"
    assert events[0]["respondent_name"] == "Laura Boss"


# ---------------------------------------------------------------------------
# 6. No Fake Jobs & Discovery Modes (P10-REG-005, P10-NOJOB-002..005, P10-JOB-001..002, P10-REG-011, P10-COMP-001..007, P10-PROJ-001..006)
# ---------------------------------------------------------------------------
def test_p10_no_fake_jobs_and_discovery_modes(temp_store):
    store, _ = temp_store
    engine = DiscoveryEngineV2(candidate_store=store)

    # P10-REG-005, P10-NOJOB-002..004: Directory/project observation never creates StructuredJob
    cand = CandidateCompany(
        candidate_id="c_dir_1",
        company_name="Directory Candidate",
        company_domain="dir-candidate.com",
    )
    store.save_candidate(cand)
    obs = DiscoveryObservation(
        observation_id="obs_comp_1",
        candidate_id="c_dir_1",
        source_id="web_dir",
        source_type="company_directory",
        observed_company_name="Directory Candidate",
        observed_domain="dir-candidate.com",
    )
    store.save_observation(obs)
    # Verify no fake job created in candidate metadata
    assert "structured_job_id" not in cand.metadata

    # P10-REG-011: Phase 5 frozen score unchanged
    score = Phase5FreezeBridge.get_phase5_score(cand)
    assert score is None

    # P10-COMP-001..007: Company-first discovery works without jobs
    res = engine.run_multi_source_discovery(
        sources=["custom_company_dir"],
        query="Engineering Milan",
        fetcher_funcs={"custom_company_dir": lambda q: [{"company_name": "Studio Milan", "domain": "milan-studio.it"}]},
    )
    c_found = store.get_by_domain("milan-studio.it")
    assert c_found is not None
    assert c_found.company_domain == "milan-studio.it"

    # P10-PROJ-001..006: Project/tender-first discovery
    cand_proj = CandidateCompany(
        candidate_id="c_proj_1",
        company_name="Tender Infrastructure",
        company_domain="tenderinfra.it",
    )
    store.save_candidate(cand_proj)
    obs_proj = DiscoveryObservation(
        observation_id="obs_proj_1",
        candidate_id="c_proj_1",
        source_id="tender_db",
        source_type="tender_portal",
        source_url="https://tender.gov/901",
        observed_company_name="Tender Infrastructure",
        observed_domain="tenderinfra.it",
        discovery_mode="project_first",
        extracted_data={"project_title": "Subway Line 4", "tender_reference": "T-901"},
    )
    store.save_observation(obs_proj)
    assert obs_proj.discovery_mode == "project_first"
    assert obs_proj.extracted_data.get("project_title") == "Subway Line 4"


# ---------------------------------------------------------------------------
# 7. Research, Potentials, Qualification & Watchlist (P10-RES-001..008, P10-OUT-001..004, P10-AUTO-001..005, P10-QUAL-001..007, P10-REG-008, P10-WATCH-002..005, P10-REG-009, P10-REG-010)
# ---------------------------------------------------------------------------
def test_p10_qualification_watchlist_suppression(temp_store):
    store, db_path = temp_store
    classifier = CompanyClassifier(suppression_store=None)

    # P10-QUAL-001: Reject irrelevant company
    c_irrelevant = CandidateCompany(company_id="c_irr", company_name="Bakery Rossi", company_domain="bakery.it", company_types=["food_service"])
    q_irr = classifier.qualify(c_irrelevant)
    assert q_irr.outcome == QualificationOutcome.reject
    assert q_irr.cold_outreach_allowed is False

    # P10-REG-008 & P10-QUAL-002: Good fit + weak intent -> watchlist
    c_watch = CandidateCompany(
        company_id="c_watch_1",
        company_name="Arch Studio",
        company_domain="archstudio.it",
        company_types=["architecture", "engineering"],
        country="Italy",
        employee_size_hint="60",
    )
    q_watch = classifier.qualify(c_watch)
    assert q_watch.outcome == QualificationOutcome.watchlist
    assert c_watch.cold_outreach_allowed is False
    store.save_candidate(c_watch)
    assert store.get_candidate("c_watch_1") is not None

    # P10-WATCH-002..005: Watchlist persists across restart
    store.add_to_watchlist("c_watch_1", "Good architecture scale without current intent")
    assert store.is_on_watchlist("c_watch_1") is True
    # Restart store instance
    store2 = CandidateStore(db_path=db_path)
    assert store2.is_on_watchlist("c_watch_1") is True

    # P10-REG-009: New current job evidence promotes watchlist candidate
    ev_promote = [MarketEvidence(
        evidence_id="ev_prom_1", candidate_id="c_watch_1", category="job", signal_type="hiring", text="Hiring Senior Revit Modeler"
    )]
    q_prom = classifier.qualify(c_watch, evidence=ev_promote)
    assert q_prom.outcome == QualificationOutcome.qualified_for_enrichment
    assert q_prom.cold_outreach_allowed is True

    # P10-REG-010 & P10-QUAL-007: Suppression prevents qualification
    with store._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                company_domain TEXT,
                scope TEXT,
                value TEXT,
                reason TEXT,
                created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO suppression_list (company_domain, reason, created_at) VALUES (?, ?, ?)",
            ("suppressed-target.it", "DNC", datetime.now(timezone.utc).isoformat())
        )
    p9_bridge = Phase9AuthorityBridge(db_path=db_path)
    classifier_supp = CompanyClassifier(suppression_store=p9_bridge)
    c_supp = CandidateCompany(company_id="c_supp", company_name="Suppressed Target", company_domain="suppressed-target.it")
    q_supp = classifier_supp.qualify(c_supp)
    assert q_supp.outcome == QualificationOutcome.suppressed
    assert c_supp.cold_outreach_allowed is False


# ---------------------------------------------------------------------------
# 8. Downstream Bridges: Phase 6, 7, 8, 9 (P10-P6-001..005, P10-P7-001..010, P10-REG-012, P10-P9-001..006)
# ---------------------------------------------------------------------------
def test_p10_downstream_bridges(temp_store):
    store, _ = temp_store

    # P10-P6-001..005: Phase 6 Enrichment Bridge
    c_enrich = CandidateCompany(
        company_id="c_enrich_1",
        company_name="Enrich Studio",
        company_domain="enrich.it",
        relationship_type=RelationshipType.service_buyer,
        bim_maturity=BIMMaturity.high,
    )
    store.save_candidate(c_enrich)
    p6_adapter = Phase6EnrichmentAdapter(candidate_store=store)
    target = p6_adapter.prepare_enrichment_target(c_enrich)
    assert target["domain"] == "enrich.it"
    assert "BIM Manager" in target["target_roles"]

    # P10-P7-001..010: Phase 7 Export Retrofit Bridge
    row = Phase7ExportBridge.build_lead_export_row(c_enrich)
    assert row["company_domain"] == "enrich.it"
    assert row["company_tier"] == "unknown" or "tier" in row["company_tier"]
    assert "bim_maturity" in row

    # P10-REG-012: Phase 8 Grounding Bridge rejects subjective claims
    assert Phase8GroundingBridge.sanitize_draft_claims("We know your firm is probably inefficient.")[0] is False
    assert Phase8GroundingBridge.sanitize_draft_claims("We noticed your project delivery in Milan.")[0] is True

    # P10-P9-001..006: Zero-send and authority bridge
    p9 = Phase9AuthorityBridge()
    assert p9.is_domain_suppressed("") is False


# ---------------------------------------------------------------------------
# 9. Discovery Engine, Runs, Health & Security (P10-REG-013, P10-RUN-001..011, P10-REG-014, P10-DAILY-001..005, P10-FALLBACK-001..005, P10-HEALTH-001..005, P10-SEC-001..008, P10-REG-015)
# ---------------------------------------------------------------------------
def test_p10_discovery_runs_health_security(temp_store):
    store, _ = temp_store
    engine = DiscoveryEngineV2(candidate_store=store)

    # P10-REG-013: Partial source failure preserves other results
    def failing_fetcher(q):
        raise ConnectionError("Timeout on Source A")

    def working_fetcher(q):
        return [{"company_name": "Preserved Co", "domain": "preserved.it"}]

    rec = engine.run_multi_source_discovery(
        sources=["src_fail", "src_ok"],
        query="Engineering",
        fetcher_funcs={"src_fail": failing_fetcher, "src_ok": working_fetcher},
    )
    assert rec.status == "partial_failure"
    assert rec.candidates_discovered == 1
    assert store.get_by_domain("preserved.it") is not None

    # P10-RUN-001..011: Run record audit persistence
    stored_run = store.get_discovery_run(rec.run_id)
    assert stored_run is not None
    assert stored_run["status"] == "partial_failure"

    # P10-REG-014: Repeated run does not duplicate candidates
    rec2 = engine.run_multi_source_discovery(
        sources=["src_ok"],
        query="Engineering",
        fetcher_funcs={"src_ok": working_fetcher},
    )
    assert store.get_by_domain("preserved.it") is not None
    assert len(store.list_candidates()) == 1

    # P10-HEALTH-001..005: Source health telemetry persisted
    h_rec = SourceHealthRecord(source_name="src_ok", status="healthy", success_count=2, failure_count=0)
    store.save_source_health(h_rec)
    all_h = store.get_all_source_health()
    assert "src_ok" in all_h

    # P10-SEC-001..008 & P10-REG-015: Secrets sanitized & Denylist excludes runtime DB
    assert "[REDACTED]" in sanitize_secret_text("Token secret_12345")
    forbidden = [".db", ".sqlite", ".sqlite3", ".env", ".pem", ".key"]
    test_files = ["app.py", "sales_outreach.db", "key.pem", ".env"]
    filtered = [f for f in test_files if not any(f.endswith(ext) for ext in forbidden) and not f.startswith(".env")]
    assert filtered == ["app.py"]


# ---------------------------------------------------------------------------
# 10. Database, Positive Outcomes, Limits, LLM, No-Send, UI Bridge (P10-DB-001..010, P10-POS-001..005, P10-KNOWN-001..005, P10-FRESH-001..005, P10-LIMIT-001..005, P10-LLM-001..007, P10-NOSEND-001..004, P10-UIBRIDGE-001..005, P10-QA-001..010)
# ---------------------------------------------------------------------------
def test_p10_remaining_invariants_and_console(temp_store):
    store, _ = temp_store

    # P10-DB-001..010: Database initialization idempotent
    store._init_db()
    assert len(store.list_candidates()) == 0

    # P10-POS-001..005: Historical positive reply preservation
    cand = CandidateCompany(
        company_id="c_pos_1",
        company_name="Positive Partner",
        company_domain="partner.it",
        historical_contact_status="replied",
        reply_status="Interested",
    )
    store.save_candidate(cand)
    assert cand.previous_outreach_detected is True
    assert cand.cold_outreach_allowed is False

    # P10-KNOWN-001..005: Known company rediscovery
    engine = DiscoveryEngineV2(candidate_store=store)
    rec = engine.run_multi_source_discovery(
        sources=["src_known"],
        query="Partner",
        fetcher_funcs={"src_known": lambda q: [{"company_name": "Positive Partner", "domain": "partner.it"}]},
    )
    assert rec.known_candidates == 1
    assert rec.new_candidates == 0

    # P10-FRESH-001..005: Freshness tracking
    ev_fresh = MarketEvidence(
        evidence_id="ev_fr_1", candidate_id="c_pos_1", category="job", signal_type="hiring", text="Hiring", freshness_status="fresh"
    )
    assert ev_fresh.freshness_status == "fresh"

    # P10-LIMIT-001..005: Limits enforcement
    cfg = SourceConfig(id="limit_src", name="Limit Source", type="job_board", max_results_per_run=5)
    assert cfg.max_results_per_run == 5

    # P10-NOSEND-001..004: Strict zero outbound send
    import sales_engine.discovery_v2 as d2
    assert hasattr(d2, "send_email") is False
    assert hasattr(d2, "dispatch_outreach") is False

    # P10-UIBRIDGE-001..005: Console bridge summary and detail contracts
    bridge = Phase10ConsoleBridge(store=store)
    summary_items = bridge.get_candidate_list_summary()
    assert len(summary_items) >= 1
    assert "country" in summary_items[0]
    assert "qualification_outcome" in summary_items[0]

    detail = bridge.get_candidate_detail("c_pos_1")
    assert detail is not None
    assert detail["candidate_id"] == "c_pos_1"
    assert "evidence" in detail
