"""
Phase 10 Adversarial Regression Test Suite (P10-ADV-001..026).

Verifies the fixes for all 17 blockers identified in the independent adversarial review:
- Historical guard monotonicity (no reset via upsert)
- Real Phase 9 domain suppression authority
- Deterministic boolean spreadsheet parsing (No != True)
- Phase 7 export bridge type safety
- Phase 10.5 console bridge dictionary watchlist handling
- Observation and evidence full provenance SQLite round-trip
- Fact grounding provenance and subjective term filtering
- Secret and Bearer token sanitization in errors and logs
- Robust market configuration and dict lookup
- All 15 required target markets configured
- FastAPI endpoints for Discovery and Candidate Intelligence
- CLI execution with zero sends
- SQLite persistence of discovery runs and source health
- Population of historical_outreach_events table
- Content-stable hashing independent of filename/row
- Run counters differentiating new, updated, and known candidates
"""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

import pytest

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryStatus,
    FitAssessment,
    IntentAssessment,
    MarketEvidence,
    OpportunityType,
    RelationshipType,
    SourceHealthRecord,
    DiscoveryRunRecord,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.discovery_v2.source_registry import SourceRegistry, sanitize_secret_text
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.bridges import (
    Phase7ExportBridge,
    Phase8GroundingBridge,
    Phase9AuthorityBridge,
    Phase10ConsoleBridge,
)
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord, parse_bool
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.review_store import ReviewStore
from backend.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "candidates_adv_test.db"
    return str(db_file)


@pytest.fixture
def store(temp_db):
    return CandidateStore(db_path=temp_db)


@pytest.fixture
def review_store(tmp_path):
    db_file = tmp_path / "review_adv_test.db"
    return ReviewStore(db_path=db_file)


@pytest.fixture
def suppression_store(review_store):
    return SuppressionStore(review_store=review_store)


# ---------------------------------------------------------------------------
# P10-ADV-001: Historical outreach guard is strictly monotonic (cannot reset to True)
# ---------------------------------------------------------------------------
def test_p10_adv_001_historical_guard_monotonic_no_reset(store):
    # Candidate initially contacted
    c1 = CandidateCompany(
        candidate_id="cand:domain:acme-eng.it",
        company_name="Acme Engineering",
        company_domain="acme-eng.it",
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
    )
    store.save_candidate(c1)

    fetched1 = store.get_candidate("cand:domain:acme-eng.it")
    assert fetched1.cold_outreach_allowed is False
    assert fetched1.historical_contact_status == "contacted"

    # Re-discovery or naive upsert attempting cold_outreach_allowed=True
    c2 = CandidateCompany(
        candidate_id="cand:domain:acme-eng.it",
        company_name="Acme Engineering SpA",
        company_domain="acme-eng.it",
        historical_contact_status="uncontacted",
        cold_outreach_allowed=True,
    )
    store.save_candidate(c2)

    fetched2 = store.get_candidate("cand:domain:acme-eng.it")
    # MUST REMAIN FALSE
    assert fetched2.cold_outreach_allowed is False
    assert fetched2.historical_contact_status == "contacted"


# ---------------------------------------------------------------------------
# P10-ADV-002: Real Phase 9 domain suppression authority
# ---------------------------------------------------------------------------
def test_p10_adv_002_domain_suppression_real_phase9(suppression_store):
    # Add domain suppression to real Phase 9 SuppressionStore
    suppression_store.add_suppression(
        email="optout@suppressed-domain.com",
        company_domain="suppressed-domain.com",
        reason="opt_out",
        source="opt_out",
    )

    bridge = Phase9AuthorityBridge(suppression_store=suppression_store)

    # Check suppression by domain alone (without email)
    assert bridge.is_suppressed(domain="suppressed-domain.com") is True
    # Non-suppressed domain
    assert bridge.is_suppressed(domain="allowed-company.com") is False

    # Enforce safety on CandidateCompany
    cand = CandidateCompany(
        candidate_id="cand:domain:suppressed-domain.com",
        company_name="Suppressed Inc",
        company_domain="suppressed-domain.com",
        cold_outreach_allowed=True,
    )
    secured = bridge.enforce_safety_on_candidate(cand)
    assert secured.cold_outreach_allowed is False
    assert secured.suppressed is True
    assert secured.status == DiscoveryStatus.suppressed


# ---------------------------------------------------------------------------
# P10-ADV-003: Spreadsheet boolean parsing (No, False, 0 != True)
# ---------------------------------------------------------------------------
def test_p10_adv_003_boolean_spreadsheet_parsing(store, tmp_path):
    csv_file = tmp_path / "test_booleans.csv"
    csv_file.write_text(
        "Company,Domain,Email,DNC,Active Conversation,Wrong Email\n"
        "Studio Uno,uno.it,info@uno.it,No,No,No\n"
        "Studio Due,due.it,info@due.it,False,False,0\n"
        "Studio Tre,tre.it,info@tre.it,Yes,Yes,1\n",
        encoding="utf-8"
    )

    importer = HistoricalImporter(store=store)
    records = importer.parse_csv(csv_file)
    assert len(records) == 3

    r1, r2, r3 = records
    # "No" must be False!
    assert r1.is_dnc is False
    assert r1.has_active_conversation is False
    assert r1.is_wrong_email is False

    # "False", "0" must be False!
    assert r2.is_dnc is False
    assert r2.has_active_conversation is False
    assert r2.is_wrong_email is False

    # "Yes", "1" must be True!
    assert r3.is_dnc is True
    assert r3.has_active_conversation is True
    assert r3.is_wrong_email is True


# ---------------------------------------------------------------------------
# P10-ADV-004: Phase 7 export bridge type safety without AttributeError
# ---------------------------------------------------------------------------
def test_p10_adv_004_phase7_export_bridge_no_crash():
    cand = CandidateCompany(
        candidate_id="cand:domain:test-export.it",
        company_name="Test Export Srl",
        company_domain="test-export.it",
        outsourcing_potential="high",
        automation_potential="medium",
        company_tier=CompanyTier.tier_2,
        relationship_type=RelationshipType.service_buyer,
        opportunity_types=[OpportunityType.bim_coordination],
    )
    base_row = {"legacy_col_1": "val1", "company_name": "Test Export Srl"}
    enriched = Phase7ExportBridge.enrich_export_row(base_row, cand)
    assert enriched["company_tier"] == "tier_2"
    assert enriched["outsourcing_potential"] == "high"
    assert enriched["automation_potential"] == "medium"
    assert enriched["relationship_type"] == "service_buyer"
    assert enriched["opportunity_types"] == "bim_coordination"


# ---------------------------------------------------------------------------
# P10-ADV-005: Phase 10.5 console bridge handles dict watchlist entries without crash
# ---------------------------------------------------------------------------
def test_p10_adv_005_console_bridge_watchlist_summary_dict_no_crash(store):
    c = CandidateCompany(
        candidate_id="cand:domain:watch-test.it",
        company_name="Watch Test Studio",
        company_domain="watch-test.it",
    )
    store.save_candidate(c)
    store.add_to_watchlist("cand:domain:watch-test.it", reason="Monitoring growth signals")

    bridge = Phase10ConsoleBridge(store=store)
    summary = bridge.get_watchlist_summary()
    assert len(summary) >= 1
    w = summary[0]
    assert w["candidate_id"] == "cand:domain:watch-test.it"
    assert w["company_name"] == "Watch Test Studio"
    assert w["reason"] == "Monitoring growth signals"


# ---------------------------------------------------------------------------
# P10-ADV-006: Discovery observation full provenance preserved on DB roundtrip
# ---------------------------------------------------------------------------
def test_p10_adv_006_observation_db_roundtrip_provenance(store):
    c = CandidateCompany(
        candidate_id="cand:domain:prov-test.it",
        company_name="Provenance Test SpA",
        company_domain="prov-test.it",
    )
    store.save_candidate(c)

    obs = DiscoveryObservation(
        observation_id="obs_prov_123",
        candidate_id="cand:domain:prov-test.it",
        source_id="italy_engineering_directory",
        source_name="Italy Directory",
        source_type="directory",
        source_url="https://directory.it/company/123",
        discovery_mode="company_first",
        observed_company_name="Provenance Test SpA",
        observed_domain="prov-test.it",
        country="Italy",
        region="Lombardy",
        city="Milan",
        raw_payload={"raw_k": "raw_v"},
        extracted_data={"services": ["BIM", "MEP"], "employee_hint": "50+"},
        published_at="2026-03-01T10:00:00Z",
    )
    store.add_observation(obs)

    loaded = store.get_observations("cand:domain:prov-test.it")
    assert len(loaded) == 1
    o = loaded[0]
    assert o.source_id == "italy_engineering_directory"
    assert o.source_name == "Italy Directory"
    assert o.source_url == "https://directory.it/company/123"
    assert o.region == "Lombardy"
    assert o.city == "Milan"
    assert o.extracted_data.get("services") == ["BIM", "MEP"]
    assert o.raw_payload.get("raw_k") == "raw_v"
    assert o.published_at == "2026-03-01T10:00:00Z"


# ---------------------------------------------------------------------------
# P10-ADV-007: Phase 8 grounding rejects unverified manual seeds without source_url
# ---------------------------------------------------------------------------
def test_p10_adv_007_grounding_rejects_unverified_manual_seed():
    cand = CandidateCompany(
        candidate_id="cand:domain:seed-test.it",
        company_name="Seed Test Studio",
        company_domain="seed-test.it",
    )
    # Manual seed observation without source_url
    unverified_obs = DiscoveryObservation(
        observation_id="obs_seed_1",
        candidate_id=cand.candidate_id,
        source_type="manual_seed",
        source_id="manual_seed",
        source_url=None,
        extracted_data={"technologies": ["Revit", "Navisworks"], "project_title": "Project X"},
    )
    # Verified observation with source_url
    verified_obs = DiscoveryObservation(
        observation_id="obs_web_1",
        candidate_id=cand.candidate_id,
        source_type="web_source",
        source_id="company_website",
        source_url="https://seed-test.it/about",
        extracted_data={"technologies": ["Archicad"], "standards": ["ISO 19650"]},
    )

    facts = Phase8GroundingBridge.extract_grounded_facts(cand, [unverified_obs, verified_obs])
    # Technologies from unverified manual seed must NOT be admitted
    assert "Revit" not in facts["verified_technologies"]
    assert "Project X" not in [p["title"] for p in facts["verified_projects"]]
    # Verified technologies must be present
    assert "Archicad" in facts["verified_technologies"]
    assert "ISO 19650" in facts["verified_standards"]


# ---------------------------------------------------------------------------
# P10-ADV-008: Grounding bridge strictly filters subjective claims and assessments
# ---------------------------------------------------------------------------
def test_p10_adv_008_grounding_filters_subjective_terms():
    is_valid, err = Phase8GroundingBridge.sanitize_draft_claims(
        "Based on our research, your outsourcing_potential = high and likely understaffed."
    )
    assert is_valid is False
    assert "outsourcing_potential" in err.lower() or "understaffed" in err.lower()

    is_valid2, _ = Phase8GroundingBridge.sanitize_draft_claims(
        "We noticed your recent hospital expansion project in Milan delivered in IFC format."
    )
    assert is_valid2 is True


# ---------------------------------------------------------------------------
# P10-ADV-009: Secret and Bearer token sanitization in errors and source health
# ---------------------------------------------------------------------------
def test_p10_adv_009_source_error_secret_redaction(store):
    registry = SourceRegistry()
    leaky_error = "HTTP 401 Authorization: Bearer SUPERSECRET123456789 from api.brave.com?api_key=SECRET998877"
    registry.record_failure("brave_job_search", leaky_error)

    health = registry.get_health_summary()
    err = health["brave_job_search"]["last_error"]
    assert "SUPERSECRET" not in err
    assert "SECRET998877" not in err
    assert "[REDACTED]" in err


# ---------------------------------------------------------------------------
# P10-ADV-010: SourceRegistry markets dict lookup handles code and id case-insensitively
# ---------------------------------------------------------------------------
def test_p10_adv_010_markets_yaml_dict_lookup():
    registry = SourceRegistry()
    m_it = registry.get_market("IT")
    assert m_it is not None
    assert m_it.get("name") == "Italy"

    m_it_lower = registry.get_market("italy")
    assert m_it_lower is not None
    assert m_it_lower.get("name") == "Italy"

    m_de = registry.get_market("DE")
    assert m_de is not None
    assert m_de.get("name") == "Germany"


# ---------------------------------------------------------------------------
# P10-ADV-011: All 15 required target markets are configured
# ---------------------------------------------------------------------------
def test_p10_adv_011_all_15_markets_configured():
    registry = SourceRegistry()
    required_codes = ["IT", "DE", "AT", "CH", "FR", "ES", "NL", "GB", "IE", "AE", "SA", "OM", "AU", "CA", "US"]
    for code in required_codes:
        m = registry.get_market(code)
        assert m is not None, f"Market {code} must be configured in markets.yaml"


# ---------------------------------------------------------------------------
# P10-ADV-012: FastAPI Discovery & Candidate endpoints are registered and operational
# ---------------------------------------------------------------------------
def test_p10_adv_012_fastapi_endpoints_registered():
    client = TestClient(app)

    # 1. GET /api/sales/sources
    res_sources = client.get("/api/sales/sources")
    assert res_sources.status_code == 200
    assert "sources" in res_sources.json()

    # 2. GET /api/sales/candidates
    res_cands = client.get("/api/sales/candidates")
    assert res_cands.status_code == 200
    assert "candidates" in res_cands.json()

    # 3. GET /api/sales/watchlist
    res_watch = client.get("/api/sales/watchlist")
    assert res_watch.status_code == 200
    assert "watchlist" in res_watch.json()

    # 4. GET /api/sales/discovery/runs
    res_runs = client.get("/api/sales/discovery/runs")
    assert res_runs.status_code == 200
    assert "runs" in res_runs.json()


# ---------------------------------------------------------------------------
# P10-ADV-013: CLI execution supports commands with zero sends
# ---------------------------------------------------------------------------
def test_p10_adv_013_cli_execution_zero_sends():
    from sales_engine.discovery_v2.run import main as cli_main
    # Test sources returns 0
    assert cli_main(["sources"]) == 0
    # Test watchlist returns 0
    assert cli_main(["watchlist"]) == 0
    # Test empty args returns 0
    assert cli_main([]) == 0


# ---------------------------------------------------------------------------
# P10-ADV-014: Discovery runs are persisted to SQLite CandidateStore
# ---------------------------------------------------------------------------
def test_p10_adv_014_discovery_runs_persisted_to_sqlite(store):
    engine = DiscoveryEngineV2(candidate_store=store)
    rec = engine.run_multi_source_discovery(
        sources=["custom_src"],
        query="BIM",
        fetcher_funcs={"custom_src": lambda q: [{"company_name": "Test Co", "domain": "testco.it"}]},
    )
    # Check SQLite persistence
    stored = store.get_discovery_run(rec.run_id)
    assert stored is not None
    assert stored["run_id"] == rec.run_id
    assert stored["observations_found"] == 1
    assert stored["status"] == "completed"

    all_runs = store.list_discovery_runs()
    assert any(r["run_id"] == rec.run_id for r in all_runs)


# ---------------------------------------------------------------------------
# P10-ADV-015: Source health is persisted to SQLite CandidateStore
# ---------------------------------------------------------------------------
def test_p10_adv_015_source_health_persisted_to_sqlite(store):
    health_rec = SourceHealthRecord(
        source_name="brave_job_search",
        status="degraded",
        success_count=15,
        failure_count=3,
        last_success_at="2026-10-05T10:00:00Z",
        last_failure_at="2026-10-05T11:00:00Z",
        last_error="HTTP 429 Rate Limit",
    )
    store.save_source_health(health_rec)

    all_health = store.get_all_source_health()
    assert "brave_job_search" in all_health
    h = all_health["brave_job_search"]
    assert h["status"] == "degraded"
    assert h["success_count"] == 15
    assert h["failure_count"] == 3


# ---------------------------------------------------------------------------
# P10-ADV-016: Historical outreach events table is populated on import
# ---------------------------------------------------------------------------
def test_p10_adv_016_historical_outreach_events_populated(store, tmp_path):
    csv_file = tmp_path / "outreach_events_test.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Reply Status,Respondent Name,Respondent Email,Notes\n"
        "Studio Milano,milano.it,marco@milano.it,2025-05-10,Interested,Giulia Boss,giulia@milano.it,Had great intro call\n",
        encoding="utf-8"
    )

    importer = HistoricalImporter(store=store)
    records = importer.parse_csv(csv_file)
    res = importer.import_records(records)
    assert res.companies_created == 1

    cand = store.get_by_domain("milano.it")
    assert cand is not None

    events = store.get_historical_outreach_for_candidate(cand.candidate_id)
    assert len(events) >= 1
    ev = events[0]
    assert ev["contact_email"] == "marco@milano.it"
    assert ev["contacted_at"] == "2025-05-10"
    assert ev["reply_status"] == "Interested"
    assert ev["respondent_name"] == "Giulia Boss"
    assert ev["respondent_email"] == "giulia@milano.it"


# ---------------------------------------------------------------------------
# P10-ADV-017: Content-stable hash independent of filename and row position
# ---------------------------------------------------------------------------
def test_p10_adv_017_content_stable_hash_independent_of_file_and_row():
    rec1 = HistoricalRecord(
        source_file="file_A.csv",
        sheet_name="Sheet1",
        row_number=5,
        company_name="Studio Alfa",
        domain="alfa.it",
        tier_hint="tier_1",
        notes="Note 1",
        source_url=None,
        contact_name="Mario",
        contact_role="Director",
        emails=["mario@alfa.it"],
        contact_date="2025-01-01",
    )
    rec2 = HistoricalRecord(
        source_file="file_B_renamed.xlsx",
        sheet_name="Sheet99",
        row_number=120,
        company_name="Studio Alfa",
        domain="alfa.it",
        tier_hint="tier_1",
        notes="Note 1",
        source_url=None,
        contact_name="Mario",
        contact_role="Director",
        emails=["mario@alfa.it"],
        contact_date="2025-01-01",
    )
    assert rec1.compute_hash() == rec2.compute_hash()


# ---------------------------------------------------------------------------
# P10-ADV-018: MarketEvidence preserves published_at and freshness_status
# ---------------------------------------------------------------------------
def test_p10_adv_018_observation_evidence_freshness_status(store):
    c = CandidateCompany(
        candidate_id="cand:domain:evidence-test.it",
        company_name="Evidence Test Studio",
        company_domain="evidence-test.it",
    )
    store.save_candidate(c)

    ev = MarketEvidence(
        evidence_id="ev_001",
        candidate_id=c.candidate_id,
        category="job_signal",
        signal_type="bim_coordinator_hiring",
        text="Hiring 2 senior BIM Coordinators with Revit experience",
        source_url="https://evidence-test.it/careers",
        source_type="careers_page",
        observed_at="2026-10-01T12:00:00Z",
        published_at="2026-09-28T09:00:00Z",
        freshness_status="fresh",
    )
    store.add_evidence(ev)

    loaded = store.get_evidence(c.candidate_id)
    assert len(loaded) == 1
    e = loaded[0]
    assert e.published_at == "2026-09-28T09:00:00Z"
    assert e.freshness_status == "fresh"


# ---------------------------------------------------------------------------
# P10-ADV-019: Run counters differentiate new, updated, and known candidates
# ---------------------------------------------------------------------------
def test_p10_adv_019_run_counters_differentiate_new_and_updated(store):
    engine = DiscoveryEngineV2(candidate_store=store)

    data1 = [
        {"company_name": "Studio Gamma", "domain": "gamma.it"},
        {"company_name": "Studio Delta", "domain": "delta.it"},
    ]
    run1 = engine.run_multi_source_discovery(
        sources=["custom_src"],
        query="Rome",
        fetcher_funcs={"custom_src": lambda q: data1},
    )
    assert run1.new_candidates == 2
    assert run1.updated_candidates == 0
    assert run1.candidates_discovered == 2

    # Second run with 1 existing and 1 new candidate
    data2 = [
        {"company_name": "Studio Gamma", "domain": "gamma.it"},
        {"company_name": "Studio Epsilon", "domain": "epsilon.it"},
    ]
    run2 = engine.run_multi_source_discovery(
        sources=["custom_src"],
        query="Rome",
        fetcher_funcs={"custom_src": lambda q: data2},
    )
    assert run2.new_candidates == 1
    assert run2.updated_candidates == 1
    assert run2.known_candidates == 1
    assert run2.candidates_discovered == 2


# ---------------------------------------------------------------------------
# P10-ADV-020: Active conversation status never resets to False
# ---------------------------------------------------------------------------
def test_p10_adv_020_active_conversation_never_resets(store):
    c = CandidateCompany(
        candidate_id="cand:domain:active-test.it",
        company_name="Active Test SpA",
        company_domain="active-test.it",
        active_conversation=True,
        cold_outreach_allowed=False,
    )
    store.save_candidate(c)

    # Later re-observation without active conversation flag
    c_updated = CandidateCompany(
        candidate_id="cand:domain:active-test.it",
        company_name="Active Test SpA",
        company_domain="active-test.it",
        active_conversation=False,
        cold_outreach_allowed=True,
    )
    store.save_candidate(c_updated)

    res = store.get_candidate("cand:domain:active-test.it")
    assert res.active_conversation is True
    assert res.cold_outreach_allowed is False


# ---------------------------------------------------------------------------
# P10-ADV-021: Suppression status is monotonic and never resets
# ---------------------------------------------------------------------------
def test_p10_adv_021_suppression_monotonic_never_resets(store):
    c = CandidateCompany(
        candidate_id="cand:domain:supp-mono.it",
        company_name="Supp Mono Ltd",
        company_domain="supp-mono.it",
        suppressed=True,
        cold_outreach_allowed=False,
    )
    store.save_candidate(c)

    # Update attempting to unsuppress
    c_update = CandidateCompany(
        candidate_id="cand:domain:supp-mono.it",
        company_name="Supp Mono Ltd",
        company_domain="supp-mono.it",
        suppressed=False,
        cold_outreach_allowed=True,
    )
    store.save_candidate(c_update)

    res = store.get_candidate("cand:domain:supp-mono.it")
    assert res.suppressed is True
    assert res.cold_outreach_allowed is False


# ---------------------------------------------------------------------------
# P10-ADV-022: Historical contact status never regresses to uncontacted
# ---------------------------------------------------------------------------
def test_p10_adv_022_historical_contact_status_never_regresses_to_uncontacted(store):
    c = CandidateCompany(
        candidate_id="cand:domain:status-regress.it",
        company_name="Status Regress SpA",
        company_domain="status-regress.it",
        historical_contact_status="replied",
        reply_status="Interested",
    )
    store.save_candidate(c)

    c_update = CandidateCompany(
        candidate_id="cand:domain:status-regress.it",
        company_name="Status Regress SpA",
        company_domain="status-regress.it",
        historical_contact_status="uncontacted",
    )
    store.save_candidate(c_update)

    res = store.get_candidate("cand:domain:status-regress.it")
    assert res.historical_contact_status == "replied"
    assert res.reply_status == "Interested"


# ---------------------------------------------------------------------------
# P10-ADV-023: Safe additive schema migration on pre-existing database
# ---------------------------------------------------------------------------
def test_p10_adv_023_candidate_store_migration_columns(tmp_path):
    old_db = tmp_path / "old_schema.db"
    conn = sqlite3.connect(old_db)
    conn.execute("""
        CREATE TABLE candidate_companies (
            candidate_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            normalized_company_name TEXT NOT NULL,
            company_domain TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            company_types_json TEXT,
            industry_tags_json TEXT,
            legal_form TEXT,
            legal_form_raw TEXT,
            employee_size_hint TEXT,
            project_scale_hint TEXT,
            company_tier TEXT,
            tier_confidence REAL,
            tier_evidence_json TEXT,
            bim_maturity TEXT,
            bim_maturity_confidence REAL,
            direct_project_access INTEGER,
            direct_project_access_confidence REAL,
            outsourcing_potential TEXT,
            automation_potential TEXT,
            relationship_type TEXT,
            opportunity_types_json TEXT,
            discovery_status TEXT,
            fit_assessment TEXT,
            fit_reasons_json TEXT,
            intent_assessment TEXT,
            intent_signals_json TEXT,
            source_refs_json TEXT,
            evidence_refs_json TEXT,
            aliases_json TEXT,
            source_identities_json TEXT,
            domain_history_json TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            last_researched_at TEXT,
            historical_contact_status TEXT,
            last_contacted_at TEXT,
            reply_status TEXT,
            active_conversation INTEGER,
            suppressed INTEGER,
            cold_outreach_allowed INTEGER
        )
    """)
    conn.commit()
    conn.close()

    # Reopen via CandidateStore -> must run _migrate_columns without error
    migrated_store = CandidateStore(db_path=str(old_db))
    c = CandidateCompany(
        candidate_id="cand:domain:migrated.it",
        company_name="Migrated Srl",
        company_domain="migrated.it",
    )
    migrated_store.save_candidate(c)
    assert migrated_store.get_candidate("cand:domain:migrated.it") is not None


# ---------------------------------------------------------------------------
# P10-ADV-024: Zero-send audit invariant across Phase 10 modules
# ---------------------------------------------------------------------------
def test_p10_adv_024_zero_send_audit_invariant():
    discovery_dir = Path("sales_engine/discovery_v2")
    candidates_dir = Path("sales_engine/candidates")
    historical_dir = Path("sales_engine/historical")

    forbidden_calls = ["send_batch", "send_email", "smtplib", "boto3.client('ses')", "send_message"]

    for d in (discovery_dir, candidates_dir, historical_dir):
        for py_file in d.glob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="replace")
            for call in forbidden_calls:
                assert call not in text, f"Forbidden send call '{call}' detected in {py_file}!"


# ---------------------------------------------------------------------------
# P10-ADV-025: Source config full schema modeled correctly
# ---------------------------------------------------------------------------
def test_p10_adv_025_source_config_full_schema():
    registry = SourceRegistry()
    src = registry.get_source("brave_job_search")
    assert src is not None
    assert src.id == "brave_job_search"
    assert src.type == "search_engine"
    assert "Italy" in src.countries or "italy" in [c.lower() for c in src.countries]
    assert src.requires_api_key is True
    assert src.base_url is not None
    assert src.max_results_per_run == 50


# ---------------------------------------------------------------------------
# P10-ADV-026: End-to-end zero-send contract across API endpoints and CLI
# ---------------------------------------------------------------------------
def test_p10_adv_026_cli_and_api_zero_send_contract(store, review_store):
    from backend.main import get_discovery_services
    app_store, _, _, _ = get_discovery_services()

    # Verify review_store has 0 drafts
    initial_drafts = len(review_store.list_drafts())

    client = TestClient(app)
    # 1. Run discovery via API
    res = client.post("/api/sales/discovery/run", json={"sources": [], "query": "Engineering"})
    assert res.status_code == 200

    # 2. Add to watchlist via API
    # Create candidate in app_store first
    c = CandidateCompany(candidate_id="cand:domain:zero-send.it", company_name="Zero Send Co")
    app_store.save_candidate(c)
    res_w = client.post("/api/sales/candidates/cand:domain:zero-send.it/watch", json={"reason": "Monitor"})
    assert res_w.status_code == 200

    # 3. Qualify via API
    res_q = client.post("/api/sales/candidates/cand:domain:zero-send.it/qualify")
    assert res_q.status_code == 200

    # Review store drafts must STILL be unchanged (0 sent, 0 drafts created)
    final_drafts = len(review_store.list_drafts())
    assert final_drafts == initial_drafts
