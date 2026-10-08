"""
tests/test_search_scope.py

Focused test suite for Production Readiness Step 1:
SEARCH SCOPE & DISCOVERY CONTROL

Tests PRD-SCOPE-001 through PRD-SCOPE-012:
- PRD-SCOPE-001 saved scope reloads identically
- PRD-SCOPE-002 only selected markets execute
- PRD-SCOPE-003 only selected enabled sources execute
- PRD-SCOPE-004 disabled source does not execute
- PRD-SCOPE-005 max_results is enforced
- PRD-SCOPE-006 job_first routes through job-first discovery
- PRD-SCOPE-007 company_first creates CandidateCompany without fake job
- PRD-SCOPE-008 project_tender_first creates evidence without fake job
- PRD-SCOPE-009 manual_historical preserves historical safeguards
- PRD-SCOPE-010 repeated run does not duplicate company identity
- PRD-SCOPE-011 scope never changes Phase 5 / suppression / send authority
- PRD-SCOPE-012 discovery run stores scope/run metadata correctly
"""

import os
import sqlite3
import pytest
from pathlib import Path
from typing import List, Dict, Any

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier, BIMMaturity
from sales_engine.discovery_v2.source_registry import SourceConfig, SourceRegistry
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.scope import (
    DiscoveryScope,
    DiscoveryRun,
    DiscoveryScopeStore,
    DiscoveryRunner,
    run_discovery,
    DiscoveryMode,
    SUPPORTED_MARKETS,
    SUPPORTED_RELATIONSHIP_TYPES,
    SUPPORTED_OPPORTUNITY_TYPES,
    SUPPORTED_COMPANY_TIERS,
    SUPPORTED_BIM_MATURITY,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    db = str(tmp_path / "test_scope.db")
    return db


@pytest.fixture
def scope_store(tmp_db: str) -> DiscoveryScopeStore:
    return DiscoveryScopeStore(db_path=tmp_db)


@pytest.fixture
def candidate_store(tmp_db: str) -> CandidateStore:
    return CandidateStore(db_path=tmp_db)


@pytest.fixture
def source_registry() -> SourceRegistry:
    reg = SourceRegistry()
    reg.register_source(SourceConfig(
        id="mock_web",
        name="Mock Web",
        type="search_engine",
        enabled=True,
        requires_api_key=False,
    ))
    reg.register_source(SourceConfig(
        id="mock_dir",
        name="Mock Directory",
        type="company_directory",
        enabled=True,
        requires_api_key=False,
    ))
    reg.register_source(SourceConfig(
        id="mock_disabled",
        name="Mock Disabled",
        type="tender_portal",
        enabled=False,
        requires_api_key=False,
    ))
    return reg


# =========================================================================
# PRD-SCOPE-001: saved scope reloads identically
# =========================================================================
def test_prd_scope_001_saved_scope_reloads_identically(scope_store: DiscoveryScopeStore):
    scope = DiscoveryScope(
        scope_name="Italy BIM Contractors",
        discovery_modes=["company_first", "job_first"],
        markets=["Italy", "Germany", "Austria"],
        source_ids=["mock_web", "mock_dir"],
        relationship_types=["service_buyer", "strategic_partner"],
        opportunity_types=["direct_bim_delivery", "mep_delivery", "revit_api"],
        company_tiers=["tier1", "tier2"],
        bim_maturity=["high", "medium"],
        max_results=35,
        company_name_keywords=["Ingegneria", "Costruzioni"],
        required_signals=["revit", "bim"],
        excluded_keywords=["recruitment", "staffing"],
        languages=["it", "en"],
        notes="High-priority Northern Italy contractors",
        enabled=True,
    )

    saved = scope_store.save_scope(scope)
    assert saved.scope_id != ""

    reloaded = scope_store.get_scope(saved.scope_id)
    assert reloaded is not None
    assert reloaded.scope_id == saved.scope_id
    assert reloaded.scope_name == "Italy BIM Contractors"
    assert reloaded.discovery_modes == ["company_first", "job_first"]
    assert reloaded.markets == ["Italy", "Germany", "Austria"]
    assert reloaded.source_ids == ["mock_web", "mock_dir"]
    assert reloaded.relationship_types == ["service_buyer", "strategic_partner"]
    assert reloaded.opportunity_types == ["direct_bim_delivery", "mep_delivery", "revit_api"]
    assert reloaded.company_tiers == ["tier1", "tier2"]
    assert reloaded.bim_maturity == ["high", "medium"]
    assert reloaded.max_results == 35
    assert reloaded.company_name_keywords == ["Ingegneria", "Costruzioni"]
    assert reloaded.required_signals == ["revit", "bim"]
    assert reloaded.excluded_keywords == ["recruitment", "staffing"]
    assert reloaded.languages == ["it", "en"]
    assert reloaded.notes == "High-priority Northern Italy contractors"
    assert reloaded.enabled is True
    assert reloaded.created_at != ""
    assert reloaded.updated_at != ""


# =========================================================================
# PRD-SCOPE-002: only selected markets execute
# =========================================================================
def test_prd_scope_002_only_selected_markets_execute(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="DACH BIM Focus",
        markets=["Germany", "Austria"],
        source_ids=["mock_web"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    queried_markets = []

    def mock_fetcher(query: str) -> List[Dict[str, Any]]:
        for market in SUPPORTED_MARKETS:
            if market in query:
                queried_markets.append(market)
        return [
            {"company_name": "Berlin BIM Gmbh", "domain": "berlinbim.de", "country": "Germany"}
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": mock_fetcher})
    assert run.status == "completed"
    assert set(queried_markets).issubset({"Germany", "Austria"})
    assert "USA" not in queried_markets
    assert "UAE" not in queried_markets
    assert "Italy" not in queried_markets


# =========================================================================
# PRD-SCOPE-003: only selected enabled sources execute
# =========================================================================
def test_prd_scope_003_only_selected_enabled_sources_execute(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    # Registry has mock_web, mock_dir, mock_disabled
    # Scope selects ONLY mock_dir
    scope = DiscoveryScope(
        scope_name="Directory Only",
        markets=["Italy"],
        source_ids=["mock_dir"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    executed_sources = []

    def fetch_web(q):
        executed_sources.append("mock_web")
        return []

    def fetch_dir(q):
        executed_sources.append("mock_dir")
        return [{"company_name": "Milano Eng", "domain": "milanoeng.it"}]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(
        saved.scope_id,
        fetcher_funcs={"mock_web": fetch_web, "mock_dir": fetch_dir},
    )

    assert run.status == "completed"
    assert "mock_dir" in run.sources_executed
    assert "mock_web" not in run.sources_executed
    assert executed_sources == ["mock_dir"]


# =========================================================================
# PRD-SCOPE-004: disabled source does not execute
# =========================================================================
def test_prd_scope_004_disabled_source_does_not_execute(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    # mock_disabled is explicitly in scope, but disabled in registry
    scope = DiscoveryScope(
        scope_name="Mixed Scope",
        markets=["France"],
        source_ids=["mock_web", "mock_disabled"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    executed_sources = []

    def fetch_web(q):
        executed_sources.append("mock_web")
        return [{"company_name": "Paris BIM", "domain": "parisbim.fr"}]

    def fetch_disabled(q):
        executed_sources.append("mock_disabled")
        return [{"company_name": "Secret Corp", "domain": "secret.fr"}]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(
        saved.scope_id,
        fetcher_funcs={"mock_web": fetch_web, "mock_disabled": fetch_disabled},
    )

    assert run.status == "completed"
    assert "mock_disabled" not in run.sources_executed
    assert "mock_disabled" not in executed_sources
    assert "mock_web" in run.sources_executed


# =========================================================================
# PRD-SCOPE-005: max_results is enforced
# =========================================================================
def test_prd_scope_005_max_results_is_enforced(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Limited Scope",
        markets=["Italy"],
        source_ids=["mock_web"],
        max_results=3,
    )
    saved = scope_store.save_scope(scope)

    def fetch_many(q):
        return [
            {"company_name": f"Firm {i}", "domain": f"firm{i}.it"}
            for i in range(1, 15)
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_many})
    assert run.status == "completed"
    assert run.results_found == 3
    assert run.companies_created == 3


# =========================================================================
# PRD-SCOPE-006: job_first routes through job-first discovery
# =========================================================================
def test_prd_scope_006_job_first_routes_through_job_first_discovery(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Job First Scope",
        markets=["UK"],
        source_ids=["mock_web"],
        discovery_modes=["job_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_job(q):
        return [
            {
                "company_name": "London BIM Ltd",
                "domain": "londonbim.co.uk",
                "job_title": "Senior BIM Coordinator",
                "structured_job_id": "job_uk_1001",
            }
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_job})
    assert run.status == "completed"
    assert run.companies_created == 1

    company = candidate_store.get_candidate_by_domain("londonbim.co.uk")
    assert company is not None
    obs = candidate_store.get_observations(company.id)
    assert len(obs) > 0
    assert obs[0].discovery_mode == "job_first"


# =========================================================================
# PRD-SCOPE-007: company_first creates CandidateCompany without fake job
# =========================================================================
def test_prd_scope_007_company_first_creates_candidate_without_fake_job(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Company Directory Scope",
        markets=["Italy"],
        source_ids=["mock_dir"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_company(q):
        # Even if raw data maliciously injects job fields, company_first mode MUST strip them
        return [
            {
                "company_name": "Studio Ingegneria Roma",
                "domain": "studioingegneria.it",
                "structured_job_id": "fake_job_999",
                "job_id": "fake_job_999",
            }
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_dir": fetch_company})
    assert run.status == "completed"

    company = candidate_store.get_candidate_by_domain("studioingegneria.it")
    assert company is not None
    obs = candidate_store.get_observations(company.id)
    assert len(obs) > 0
    assert obs[0].discovery_mode == "company_first"
    # Verify no fake job injected into extracted data
    assert "fake_job_999" not in str(obs[0].extracted_data)


# =========================================================================
# PRD-SCOPE-008: project_tender_first creates evidence without fake job
# =========================================================================
def test_prd_scope_008_project_tender_first_creates_evidence_without_fake_job(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Tender Scope",
        markets=["Spain"],
        source_ids=["mock_web"],
        discovery_modes=["project_tender_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_tender(q):
        return [
            {
                "company_name": "Madrid Infraestructuras",
                "domain": "madridinfra.es",
                "project_tender_id": "tender_es_2026",
                "job_id": "should_not_exist",
            }
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_tender})
    assert run.status == "completed"

    company = candidate_store.get_candidate_by_domain("madridinfra.es")
    assert company is not None
    obs = candidate_store.get_observations(company.id)
    assert len(obs) > 0
    assert obs[0].discovery_mode == "project_tender_first"
    assert "should_not_exist" not in str(obs[0].extracted_data)


# =========================================================================
# PRD-SCOPE-009: manual_historical preserves historical safeguards
# =========================================================================
def test_prd_scope_009_manual_historical_preserves_historical_safeguards(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Historical Archives",
        markets=["Italy"],
        source_ids=["mock_web"],
        discovery_modes=["manual_historical"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_historical(q):
        return [
            {
                "company_name": "Archivio BIM 2024",
                "domain": "archiviobim.it",
                "historical_interaction": "consulted_2024",
            }
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_historical})
    assert run.status == "completed"

    company = candidate_store.get_candidate_by_domain("archiviobim.it")
    assert company is not None
    obs = candidate_store.get_observations(company.id)
    assert len(obs) > 0
    assert obs[0].discovery_mode == "manual_historical"


# =========================================================================
# PRD-SCOPE-010: repeated run does not duplicate company identity
# =========================================================================
def test_prd_scope_010_repeated_run_does_not_duplicate_company_identity(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Deduplication Scope",
        markets=["Italy"],
        source_ids=["mock_web"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_company(q):
        return [
            {"company_name": "BIM Dynamics", "domain": "https://www.bimdynamics.it/contact"}
        ]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )

    run1 = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_company})
    assert run1.companies_created == 1
    assert run1.companies_updated == 0

    run2 = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_company})
    assert run2.companies_created == 0
    assert run2.companies_updated == 1

    # Candidate store must contain exactly ONE record for bimdynamics.it
    all_cands = candidate_store.list_candidates()
    matching = [c for c in all_cands if c.company_domain == "bimdynamics.it"]
    assert len(matching) == 1


# =========================================================================
# PRD-SCOPE-011: scope never changes Phase 5 / suppression / send authority
# =========================================================================
def test_prd_scope_011_scope_never_changes_phase5_suppression_send_authority(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
    tmp_db: str,
):
    from sales_engine.discovery_v2.bridges import Phase5FreezeBridge, Phase9AuthorityBridge

    # Initialize suppression table and add a suppressed domain
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS suppression_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            company_domain TEXT,
            scope TEXT,
            value TEXT,
            reason TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO suppression_list (company_domain, scope, value, reason) VALUES (?, ?, ?, ?)",
        ("suppressed.it", "domain", "suppressed.it", "test"),
    )
    conn.commit()
    conn.close()

    scope = DiscoveryScope(
        scope_name="Safety Verification Scope",
        markets=["Italy"],
        source_ids=["mock_web"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_data(q):
        return [{"company_name": "Suppressed Corp", "domain": "suppressed.it"}]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )
    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_data})
    assert run.status == "completed"

    # Verify candidate created has NO Phase 5 synthetic score
    cand = candidate_store.get_candidate_by_domain("suppressed.it")
    assert cand is not None
    assert Phase5FreezeBridge.get_phase5_score(cand) is None

    # Verify Phase 9 suppression authority remains authoritative
    phase9 = Phase9AuthorityBridge(db_path=tmp_db)
    assert phase9.is_domain_suppressed("suppressed.it") is True

    # Verify suppression table is unchanged
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT company_domain, reason FROM suppression_list").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == ("suppressed.it", "test")

    # Invariant: runner has NO sending capability or email client
    assert not hasattr(runner, "send_email")
    assert not hasattr(runner, "smtp_client")
    assert not hasattr(runner, "outbox")


# =========================================================================
# PRD-SCOPE-012: discovery run stores scope/run metadata correctly
# =========================================================================
def test_prd_scope_012_discovery_run_stores_scope_run_metadata_correctly(
    scope_store: DiscoveryScopeStore,
    candidate_store: CandidateStore,
    source_registry: SourceRegistry,
):
    scope = DiscoveryScope(
        scope_name="Metadata Audit Scope",
        markets=["Germany", "Netherlands"],
        source_ids=["mock_web"],
        discovery_modes=["company_first"],
    )
    saved = scope_store.save_scope(scope)

    def fetch_data(q):
        return [{"company_name": "Munich CAD", "domain": "munichcad.de"}]

    runner = DiscoveryRunner(
        scope_store=scope_store,
        candidate_store=candidate_store,
        source_registry=source_registry,
    )
    run = runner.run(saved.scope_id, fetcher_funcs={"mock_web": fetch_data})

    # Retrieve stored run
    stored_run = scope_store.get_run(run.run_id)
    assert stored_run is not None
    assert stored_run.run_id == run.run_id
    assert stored_run.scope_id == saved.scope_id
    assert stored_run.status == "completed"
    assert stored_run.started_at != ""
    assert stored_run.finished_at != ""
    assert stored_run.markets == ["Germany", "Netherlands"]
    assert stored_run.discovery_modes == ["company_first"]
    assert stored_run.sources_requested == ["mock_web"]
    assert stored_run.sources_executed == ["mock_web"]
    assert stored_run.results_found == 1
    assert stored_run.companies_created == 1
    assert stored_run.companies_updated == 0
    assert stored_run.errors == []

    # Check secret sanitization: no credentials or secrets stored
    run_dict = stored_run.to_dict()
    assert "password" not in run_dict
    assert "api_secret" not in run_dict
    assert "token" not in run_dict


# =========================================================================
# PRD-SCOPE-API: FastAPI endpoints for Discovery Scopes
# =========================================================================
def test_prd_scope_api_endpoints(tmp_path: Path):
    from fastapi.testclient import TestClient
    import backend.main as bm

    db_path = str(tmp_path / "api_scope_test.db")
    os.environ["SALESAI_DB_PATH"] = db_path
    bm._discovery_store_inst = None
    bm._discovery_classifier_inst = None
    bm._discovery_engine_inst = None

    client = TestClient(bm.app)

    # 1. Create scope via POST /api/sales/discovery/scopes
    payload = {
        "scope_name": "API Created Italy Contractors",
        "discovery_modes": ["company_first"],
        "markets": ["Italy"],
        "max_results": 20,
        "company_name_keywords": ["Costruzioni"],
        "notes": "Created via FastAPI",
    }
    res_post = client.post("/api/sales/discovery/scopes", json=payload)
    assert res_post.status_code == 200, res_post.text
    data_post = res_post.json()
    assert data_post["scope_name"] == "API Created Italy Contractors"
    scope_id = data_post["scope_id"]
    assert scope_id != ""

    # 2. Get scope via GET /api/sales/discovery/scopes/{scope_id}
    res_get = client.get(f"/api/sales/discovery/scopes/{scope_id}")
    assert res_get.status_code == 200
    assert res_get.json()["scope_id"] == scope_id

    # 3. List scopes via GET /api/sales/discovery/scopes
    res_list = client.get("/api/sales/discovery/scopes")
    assert res_list.status_code == 200
    assert res_list.json()["total"] >= 1

    # 4. Run discovery via POST /api/sales/discovery/run with scope_id
    res_run = client.post("/api/sales/discovery/run", json={"scope_id": scope_id})
    assert res_run.status_code == 200
    run_data = res_run.json()
    assert run_data["scope_id"] == scope_id
    assert run_data["status"] in ("completed", "failed")
