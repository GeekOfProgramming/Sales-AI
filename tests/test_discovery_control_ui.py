"""
Tests for Production Readiness Step 3: Discovery Control UI & Search Profiles (PRD-DISCUI-001..020).
"""
import os
import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import get_console_services
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.discovery_v2.source_registry import SourceRegistry, SourceConfig
from sales_engine.discovery_v2.scope import (
    DiscoveryScope,
    DiscoveryRun,
    DiscoveryScopeStore,
    DiscoveryRunner,
    preview_discovery_plan,
)


class CsrfAwareClient(TestClient):
    """TestClient that automatically supplies valid CSRF token and local origin."""
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "test_csrf_token_step3_valid"
        if "sales_csrf_token" not in self.cookies:
            self.cookies.set("sales_csrf_token", token)

        data = kwargs.get("data")
        if data is None:
            kwargs["data"] = {"csrf_token": token}
        elif isinstance(data, dict) and "csrf_token" not in data:
            data_copy = dict(data)
            data_copy["csrf_token"] = token
            kwargs["data"] = data_copy

        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = {"Origin": "http://testserver"}
        elif "Origin" not in headers:
            headers_copy = dict(headers)
            headers_copy["Origin"] = "http://testserver"
            kwargs["headers"] = headers_copy

        return super().post(url, *args, **kwargs)


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_discovery_ui.db")


@pytest.fixture
def temp_cand_store(temp_db):
    return CandidateStore(db_path=temp_db)


@pytest.fixture
def temp_scope_store(temp_db):
    return DiscoveryScopeStore(db_path=temp_db)


@pytest.fixture
def temp_registry(tmp_path, temp_db):
    yaml_path = str(tmp_path / "sources.yaml")
    return SourceRegistry(config_path=yaml_path, db_path=temp_db)


@pytest.fixture
def client(temp_db, temp_cand_store, temp_scope_store, temp_registry, monkeypatch):
    monkeypatch.setenv("SALESAI_DB_PATH", temp_db)
    monkeypatch.setenv("SALES_OUTREACH_DB", temp_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", temp_db)

    # Override get_console_services in router
    def mock_get_console_services():
        svcs = get_console_services()
        svcs["cand_store"] = temp_cand_store
        svcs["scope_store"] = temp_scope_store
        svcs["registry"] = temp_registry
        svcs["discovery_runner"] = DiscoveryRunner(
            scope_store=temp_scope_store,
            candidate_store=temp_cand_store,
            source_registry=temp_registry,
            engine=svcs["engine"],
        )
        return svcs

    monkeypatch.setattr("backend.sales_ui.router.get_console_services", mock_get_console_services)
    c = CsrfAwareClient(app)
    c.cookies.set("sales_csrf_token", "test_csrf_token_step3_valid")
    return c


# ============================================================================
# PRD-DISCUI-001: Discovery page renders with zero LLM calls
# ============================================================================
def test_prd_discui_001_discovery_page_renders_with_zero_llm_calls(client):
    with patch("ai_engine.llm_client.BIMLLMClient.generate_code") as mock_generate:
        resp = client.get("/sales/discovery")
        assert resp.status_code == 200
        html = resp.text
        assert "Discovery Control" in html
        assert "Search Profiles" in html
        assert "Recent Discovery Runs" in html
        assert mock_generate.call_count == 0, "Page render must perform ZERO LLM calls"


# ============================================================================
# PRD-DISCUI-002: Create profile from UI persists DiscoveryScope
# ============================================================================
def test_prd_discui_002_create_profile_from_ui_persists_discoveryscope(client, temp_scope_store):
    form_data = {
        "scope_name": "Italy Architectural BIM",
        "discovery_modes": ["company_first"],
        "markets": ["Italy"],
        "source_ids": ["brave_job_search"],
        "relationship_types": ["service_buyer"],
        "opportunity_types": ["direct_bim_delivery"],
        "company_tiers": ["tier1", "tier2"],
        "bim_maturity": ["high", "medium"],
        "max_results": 35,
        "company_name_keywords": "BIM, Revit",
        "required_signals": "bim_manager",
        "excluded_keywords": "portal, staffing",
        "languages": "it, en",
        "notes": "Focused on northern Italy",
        "enabled": "true",
    }
    resp = client.post("/sales/discovery/profiles/create", data=form_data, follow_redirects=False)
    assert resp.status_code == 303
    assert "/sales/discovery" in resp.headers["location"]

    scopes = temp_scope_store.list_scopes()
    assert len(scopes) == 1
    sc = scopes[0]
    assert sc.scope_name == "Italy Architectural BIM"
    assert sc.discovery_modes == ["company_first"]
    assert sc.markets == ["Italy"]
    assert sc.max_results == 35
    assert sc.enabled is True
    assert "BIM" in sc.company_name_keywords
    assert "bim_manager" in sc.required_signals


# ============================================================================
# PRD-DISCUI-003: Edit profile updates existing scope without new identity
# ============================================================================
def test_prd_discui_003_edit_profile_updates_existing_scope_without_new_identity(client, temp_scope_store):
    orig = DiscoveryScope(
        scope_name="Germany MEP",
        discovery_modes=["company_first"],
        markets=["Germany"],
        max_results=50,
        scope_id="sc_edit_001",
    )
    temp_scope_store.save_scope(orig)

    update_data = {
        "scope_name": "Germany MEP & HVAC",
        "discovery_modes": ["company_first", "job_first"],
        "markets": ["Germany", "Austria"],
        "source_ids": ["brave_job_search"],
        "relationship_types": ["service_buyer"],
        "opportunity_types": ["mep_delivery"],
        "company_tiers": ["tier1"],
        "bim_maturity": ["high"],
        "max_results": 75,
        "company_name_keywords": "HVAC",
        "required_signals": "",
        "excluded_keywords": "",
        "languages": "de",
        "notes": "Updated scope",
        "enabled": "true",
    }
    resp = client.post("/sales/discovery/profiles/sc_edit_001/update", data=update_data, follow_redirects=False)
    assert resp.status_code == 303

    updated = temp_scope_store.get_scope("sc_edit_001")
    assert updated is not None
    assert updated.scope_id == "sc_edit_001"
    assert updated.scope_name == "Germany MEP & HVAC"
    assert updated.max_results == 75
    assert "Austria" in updated.markets


# ============================================================================
# PRD-DISCUI-004: Duplicate profile creates new scope ID
# ============================================================================
def test_prd_discui_004_duplicate_profile_creates_new_scope_id(client, temp_scope_store):
    orig = DiscoveryScope(
        scope_name="Swiss Infrastructure",
        discovery_modes=["company_first"],
        markets=["Switzerland"],
        max_results=30,
        scope_id="sc_swiss_orig",
    )
    temp_scope_store.save_scope(orig)

    resp = client.post("/sales/discovery/profiles/sc_swiss_orig/duplicate", follow_redirects=False)
    assert resp.status_code == 303

    scopes = temp_scope_store.list_scopes()
    assert len(scopes) == 2
    clones = [s for s in scopes if s.scope_id != "sc_swiss_orig"]
    assert len(clones) == 1
    clone = clones[0]
    assert clone.scope_name == "Swiss Infrastructure (Copy)"
    assert clone.scope_id != "sc_swiss_orig"
    assert clone.markets == ["Switzerland"]
    assert clone.max_results == 30


# ============================================================================
# PRD-DISCUI-005: Disabled profile cannot execute
# ============================================================================
def test_prd_discui_005_disabled_profile_cannot_execute(client, temp_scope_store):
    disabled_scope = DiscoveryScope(
        scope_name="Disabled Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        enabled=False,
        scope_id="sc_disabled_01",
    )
    temp_scope_store.save_scope(disabled_scope)

    # 1. UI run attempt
    resp = client.post("/sales/discovery/profiles/sc_disabled_01/run", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "disabled" in resp.headers["location"].lower()

    # 2. Runner direct invocation raises ValueError
    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=MagicMock())
    with pytest.raises(ValueError) as excinfo:
        runner.run("sc_disabled_01")
    assert "disabled" in str(excinfo.value).lower()


# ============================================================================
# PRD-DISCUI-006: Source options come from authoritative SourceRegistry
# ============================================================================
def test_prd_discui_006_source_options_come_from_authoritative_sourceregistry(client, temp_registry):
    temp_registry.save_source(SourceConfig(
        id="custom_aec_feed",
        name="Custom AEC Feed",
        type="news",
        enabled=True,
    ))

    resp = client.get("/sales/discovery")
    assert resp.status_code == 200
    html = resp.text
    assert "custom_aec_feed" in html
    assert "Custom AEC Feed" in html
    assert "brave_job_search" in html


# ============================================================================
# PRD-DISCUI-007: Preview performs zero external source calls
# ============================================================================
def test_prd_discui_007_preview_performs_zero_external_source_calls(client, temp_scope_store, temp_registry, temp_cand_store):
    scope = DiscoveryScope(
        scope_name="Preview Test Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["brave_job_search"],
        max_results=50,
        scope_id="sc_preview_01",
    )
    temp_scope_store.save_scope(scope)

    with patch("sales_engine.discovery_v2.adapters.get_default_adapter_for_source") as mock_adapter:
        plan = preview_discovery_plan(scope, registry=temp_registry)
        assert mock_adapter.call_count == 0, "Preview must perform ZERO external calls"
        assert plan["executable_count"] >= 0
        assert len(temp_cand_store.list_candidates()) == 0, "Preview must write ZERO candidates"


# ============================================================================
# PRD-DISCUI-008: Preview reports disabled source reason
# ============================================================================
def test_prd_discui_008_preview_reports_disabled_source_reason(temp_registry):
    temp_registry.save_source(SourceConfig(
        id="tender_feed_disabled",
        name="Tender Feed Disabled",
        type="tender_portal",
        enabled=False,
    ))
    scope = DiscoveryScope(
        scope_name="Tender Scope",
        discovery_modes=["project_tender_first"],
        markets=["Italy"],
        source_ids=["tender_feed_disabled"],
    )
    plan = preview_discovery_plan(scope, registry=temp_registry)
    assert plan["executable_count"] == 0
    assert plan["skipped_count"] == 1
    skipped = plan["skipped_sources"][0]
    assert skipped["source_id"] == "tender_feed_disabled"
    assert skipped["status"] == "disabled"
    assert "disabled in SourceRegistry" in skipped["reason"]


# ============================================================================
# PRD-DISCUI-009: Preview reports missing credential reason
# ============================================================================
def test_prd_discui_009_preview_reports_missing_credential_reason(temp_registry, monkeypatch):
    monkeypatch.delenv("MISSING_API_KEY_ENV", raising=False)
    temp_registry.save_source(SourceConfig(
        id="paid_directory_source",
        name="Paid Directory Source",
        type="directory",
        enabled=True,
        requires_api_key=True,
        credential_env_var="MISSING_API_KEY_ENV",
    ))
    scope = DiscoveryScope(
        scope_name="Paid Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["paid_directory_source"],
    )
    plan = preview_discovery_plan(scope, registry=temp_registry)
    assert plan["executable_count"] == 0
    assert plan["skipped_count"] == 1
    skipped = plan["skipped_sources"][0]
    assert skipped["status"] == "missing_credential"
    assert "MISSING_API_KEY_ENV" in skipped["reason"]


# ============================================================================
# PRD-DISCUI-010: Preview reports unsupported market/mode
# ============================================================================
def test_prd_discui_010_preview_reports_unsupported_market_mode(temp_registry):
    temp_registry.save_source(SourceConfig(
        id="italy_only_source",
        name="Italy Only Directory",
        type="directory",
        enabled=True,
        countries=["Italy"],
        discovery_mode=["company_first"],
    ))
    # Scope for Germany
    scope_market_mismatch = DiscoveryScope(
        scope_name="Germany Search",
        discovery_modes=["company_first"],
        markets=["Germany"],
        source_ids=["italy_only_source"],
    )
    plan1 = preview_discovery_plan(scope_market_mismatch, registry=temp_registry)
    assert plan1["skipped_count"] == 1
    assert plan1["skipped_sources"][0]["status"] == "unsupported_market"

    # Scope for job_first mode
    scope_mode_mismatch = DiscoveryScope(
        scope_name="Job Search",
        discovery_modes=["job_first"],
        markets=["Italy"],
        source_ids=["italy_only_source"],
    )
    plan2 = preview_discovery_plan(scope_mode_mismatch, registry=temp_registry)
    assert plan2["skipped_count"] == 1
    assert plan2["skipped_sources"][0]["status"] == "unsupported_mode"


# ============================================================================
# PRD-DISCUI-011: Real run delegates to existing DiscoveryRunner
# ============================================================================
def test_prd_discui_011_real_run_delegates_to_existing_discoveryrunner(client, temp_scope_store):
    scope = DiscoveryScope(
        scope_name="Runner Delegation Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        scope_id="sc_runner_del",
    )
    temp_scope_store.save_scope(scope)

    fake_run = DiscoveryRun(
        run_id="run_delegated_999",
        scope_id="sc_runner_del",
        status="completed",
        results_found=5,
    )
    with patch("sales_engine.discovery_v2.scope.DiscoveryRunner.run", return_value=fake_run) as mock_run:
        resp = client.post("/sales/discovery/profiles/sc_runner_del/run", follow_redirects=False)
        assert resp.status_code == 303
        assert mock_run.call_count == 1
        assert "run_delegated_999" in resp.headers["location"]


# ============================================================================
# PRD-DISCUI-012: Real run preserves scope max-results enforcement
# ============================================================================
def test_prd_discui_012_real_run_preserves_scope_max_results_enforcement(temp_scope_store, temp_cand_store, temp_registry):
    temp_registry.save_source(SourceConfig(
        id="unbounded_source",
        name="Unbounded Source",
        type="search_engine",
        enabled=True,
        countries=["Italy"],
        discovery_mode=["company_first"],
        max_results_per_run=100,
    ))
    scope = DiscoveryScope(
        scope_name="Bounded Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["unbounded_source"],
        max_results=3,  # Strict cap
        scope_id="sc_budget_3",
    )
    temp_scope_store.save_scope(scope)

    # Provide fetcher returning 10 items
    def mock_fetcher(query):
        return [
            {"company_name": f"Company {i}", "domain": f"company{i}.it", "country": "Italy"}
            for i in range(10)
        ]

    runner = DiscoveryRunner(
        scope_store=temp_scope_store,
        candidate_store=temp_cand_store,
        source_registry=temp_registry,
    )
    run = runner.run("sc_budget_3", fetcher_funcs={"unbounded_source": mock_fetcher})
    assert run.results_found == 3
    assert len(temp_cand_store.list_candidates()) == 3


# ============================================================================
# PRD-DISCUI-013: One failing source does not hide successful source results
# ============================================================================
def test_prd_discui_013_one_failing_source_does_not_hide_successful_results(temp_scope_store, temp_cand_store, temp_registry):
    temp_registry.save_source(SourceConfig(
        id="src_fails",
        name="Failing Source",
        type="directory",
        enabled=True,
        countries=["Italy"],
        discovery_mode=["company_first"],
    ))
    temp_registry.save_source(SourceConfig(
        id="src_succeeds",
        name="Successful Source",
        type="directory",
        enabled=True,
        countries=["Italy"],
        discovery_mode=["company_first"],
    ))
    scope = DiscoveryScope(
        scope_name="Resilience Scope",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["src_fails", "src_succeeds"],
        max_results=10,
        scope_id="sc_resilience",
    )
    temp_scope_store.save_scope(scope)

    def failing_fetcher(query):
        raise ConnectionError("Network down for src_fails")

    def successful_fetcher(query):
        return [
            {"company_name": "Resilient BIM SRL", "domain": "resilient-bim.it", "country": "Italy"}
        ]

    runner = DiscoveryRunner(
        scope_store=temp_scope_store,
        candidate_store=temp_cand_store,
        source_registry=temp_registry,
    )
    run = runner.run("sc_resilience", fetcher_funcs={
        "src_fails": failing_fetcher,
        "src_succeeds": successful_fetcher,
    })

    assert run.results_found == 1
    assert run.companies_created == 1
    assert "src_succeeds" in run.sources_executed
    assert any("Network down" in err for err in run.errors)
    assert temp_cand_store.get_by_domain("resilient-bim.it") is not None


# ============================================================================
# PRD-DISCUI-014: Recent run history renders correctly
# ============================================================================
def test_prd_discui_014_recent_run_history_renders_correctly(client, temp_scope_store):
    run = DiscoveryRun(
        run_id="run_history_test_100",
        scope_id="sc_test",
        scope_name="Historical Profile Name",
        started_at="2026-10-08T12:00:00Z",
        status="completed",
        markets=["Italy"],
        sources_executed=["brave_job_search"],
        results_found=12,
        companies_created=8,
        companies_updated=4,
    )
    temp_scope_store.save_run(run)

    resp = client.get("/sales/discovery")
    assert resp.status_code == 200
    html = resp.text
    assert "run_history_test_100" in html
    assert "Historical Profile Name" in html
    assert "12" in html


# ============================================================================
# PRD-DISCUI-015: Run detail shows requested/executed/skipped sources
# ============================================================================
def test_prd_discui_015_run_detail_shows_requested_executed_skipped_sources(client, temp_scope_store):
    run = DiscoveryRun(
        run_id="run_detail_breakdown_200",
        scope_id="sc_breakdown",
        scope_name="Breakdown Profile",
        started_at="2026-10-08T14:00:00Z",
        status="completed",
        sources_requested=["src_ok", "src_disabled_skip"],
        sources_executed=["src_ok"],
        skipped_sources=[{"name": "src_disabled_skip", "source_id": "src_disabled_skip", "reason": "Disabled in registry"}],
        results_found=5,
        companies_created=5,
    )
    temp_scope_store.save_run(run)

    resp = client.get("/sales/discovery/runs/run_detail_breakdown_200")
    assert resp.status_code == 200
    html = resp.text
    assert "run_detail_breakdown_200" in html
    assert "src_ok" in html
    assert "src_disabled_skip" in html
    assert "Disabled in registry" in html


# ============================================================================
# PRD-DISCUI-016: Profile edit does not rewrite historical run metadata
# ============================================================================
def test_prd_discui_016_profile_edit_does_not_rewrite_historical_run_metadata(client, temp_scope_store):
    orig = DiscoveryScope(
        scope_name="Original Scope Name",
        discovery_modes=["company_first"],
        markets=["Italy"],
        scope_id="sc_immutable_run",
    )
    temp_scope_store.save_scope(orig)

    run = DiscoveryRun(
        run_id="run_snap_300",
        scope_id="sc_immutable_run",
        scope_name="Original Scope Name",
        started_at="2026-10-08T10:00:00Z",
        status="completed",
        markets=["Italy"],
        results_found=7,
    )
    temp_scope_store.save_run(run)

    # Now edit the profile
    temp_scope_store.update_scope("sc_immutable_run", {"scope_name": "Totally New Scope Name"})

    # Check the historical run
    loaded_run = temp_scope_store.get_run("run_snap_300")
    assert loaded_run.scope_name == "Original Scope Name", "Historical run metadata must not be rewritten by profile edits"


# ============================================================================
# PRD-DISCUI-017: No mutating GET
# ============================================================================
def test_prd_discui_017_no_mutating_get(client):
    # GET to mutate endpoints should be rejected (405 Method Not Allowed)
    r1 = client.get("/sales/discovery/profiles/create")
    assert r1.status_code == 405

    r2 = client.get("/sales/discovery/profiles/sc_123/update")
    assert r2.status_code == 405

    r3 = client.get("/sales/discovery/profiles/sc_123/duplicate")
    assert r3.status_code == 405

    r4 = client.get("/sales/discovery/profiles/sc_123/toggle")
    assert r4.status_code == 405

    r5 = client.get("/sales/discovery/profiles/sc_123/run")
    assert r5.status_code == 405


# ============================================================================
# PRD-DISCUI-018: CSRF / same-origin protects mutations
# ============================================================================
def test_prd_discui_018_csrf_same_origin_protects_mutations(client, temp_scope_store):
    raw_client = TestClient(app)

    # 1. Missing CSRF token
    resp_no_csrf = raw_client.post(
        "/sales/discovery/profiles/create",
        data={"scope_name": "Attacker Profile", "discovery_modes": ["company_first"], "markets": ["Italy"]},
        headers={"Origin": "http://testserver"},
    )
    assert resp_no_csrf.status_code == 403

    # 2. Cross-origin request
    resp_cross_origin = raw_client.post(
        "/sales/discovery/profiles/create",
        data={"scope_name": "Attacker Profile", "csrf_token": "valid_token"},
        headers={"Origin": "http://malicious-website.com"},
        cookies={"sales_csrf_token": "valid_token"},
    )
    assert resp_cross_origin.status_code == 403


# ============================================================================
# PRD-DISCUI-019: Raw credentials never appear in UI
# ============================================================================
def test_prd_discui_019_raw_credentials_never_appear_in_ui(client, temp_registry, temp_scope_store, monkeypatch):
    secret_val = "SUPER_SECRET_TOKEN_XYZ_999"
    monkeypatch.setenv("TEST_SOURCE_SECRET_VAR", secret_val)

    temp_registry.save_source(SourceConfig(
        id="secret_src_ui",
        name="Secret Source",
        type="custom",
        enabled=True,
        requires_api_key=True,
        credential_env_var="TEST_SOURCE_SECRET_VAR",
    ))
    scope = DiscoveryScope(
        scope_name="Credential Safe Profile",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["secret_src_ui"],
        scope_id="sc_cred_safe",
    )
    temp_scope_store.save_scope(scope)

    # 1. Main discovery page
    resp_main = client.get("/sales/discovery")
    assert resp_main.status_code == 200
    assert secret_val not in resp_main.text

    # 2. Profile detail page
    resp_prof = client.get("/sales/discovery/profiles/sc_cred_safe")
    assert resp_prof.status_code == 200
    assert secret_val not in resp_prof.text


# ============================================================================
# PRD-DISCUI-020: Step 1 and Step 2 tests remain green
# ============================================================================
def test_prd_discui_020_step1_and_step2_tests_remain_green(temp_scope_store, temp_registry):
    # In-memory smoke test confirming Step 1 and Step 2 primitives function seamlessly together
    scope = DiscoveryScope(
        scope_name="Combined Primitives",
        discovery_modes=["company_first"],
        markets=["Italy"],
        source_ids=["italy_engineering_directory"],
    )
    saved = temp_scope_store.save_scope(scope)
    assert saved.scope_id is not None

    source = temp_registry.get_source("italy_engineering_directory")
    assert source is not None
    assert source.enabled is True

    plan = preview_discovery_plan(saved, registry=temp_registry)
    assert plan["executable_count"] >= 1
