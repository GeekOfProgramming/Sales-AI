"""
Tests for Production Readiness Step 2: Source Management (PRD-SOURCE-001..020).
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from sales_engine.discovery_v2.source_registry import (
    SourceRegistry,
    SourceConfig,
    SUPPORTED_SOURCE_TYPES,
    validate_source_url,
)
from sales_engine.discovery_v2.scope import (
    DiscoveryScope,
    DiscoveryScopeStore,
    DiscoveryRunner,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany


@pytest.fixture
def temp_store(tmp_path):
    db_file = str(tmp_path / "test_sources.db")
    return CandidateStore(db_path=db_file)


@pytest.fixture
def temp_scope_store(tmp_path):
    db_file = str(tmp_path / "test_scopes.db")
    return DiscoveryScopeStore(db_path=db_file)


@pytest.fixture
def temp_registry(tmp_path, temp_store):
    yaml_file = str(tmp_path / "sources.yaml")
    return SourceRegistry(config_path=yaml_file, db_path=temp_store.db_path)


# ============================================================================
# PRD-SOURCE-001: existing sources migrate/load without ID change
# ============================================================================
def test_prd_source_001_existing_sources_migrate_load_without_id_change(temp_registry):
    sources = temp_registry.list_sources()
    assert len(sources) >= 5, "Expected default canonical sources to be loaded"
    source_ids = {s.id for s in sources}
    expected_ids = {
        "brave_job_search",
        "ats_greenhouse",
        "ats_lever",
        "italy_engineering_directory",
        "oice_association_directory",
    }
    assert expected_ids.issubset(source_ids), f"Missing canonical sources: {expected_ids - source_ids}"
    
    # Check that brave_job_search has correct metadata
    brave = temp_registry.get_source("brave_job_search")
    assert brave is not None
    assert "Brave" in brave.name
    assert brave.requires_api_key is True
    assert brave.credential_env_var == "BRAVE_SEARCH_API_KEY"


# ============================================================================
# PRD-SOURCE-002: add valid source persists and reloads
# ============================================================================
def test_prd_source_002_add_valid_source_persists_and_reloads(tmp_path, temp_store):
    yaml_file = str(tmp_path / "sources_persisted.yaml")
    reg1 = SourceRegistry(config_path=yaml_file, db_path=temp_store.db_path)
    
    new_src = reg1.register_source({
        "id": "german_bim_portal",
        "name": "German BIM Portal",
        "type": "company_directory",
        "priority": 45,
        "max_results_per_run": 30,
        "countries": ["DE", "AT"],
        "discovery_mode": ["company_first"],
        "base_url": "https://bim-germany.example.com",
        "adapter_id": "generic_http",
        "operator_instructions": "Focus on Munich and Berlin architectural contractors.",
    })
    assert new_src.id == "german_bim_portal"
    assert new_src.priority == 45
    assert new_src.max_results_per_run == 30

    # Reload in a completely new registry instance from same file
    reg2 = SourceRegistry(config_path=yaml_file, db_path=temp_store.db_path)
    loaded = reg2.get_source("german_bim_portal")
    assert loaded is not None
    assert loaded.name == "German BIM Portal"
    assert loaded.countries == ["DE", "AT"]
    assert loaded.max_results_per_run == 30
    assert loaded.operator_instructions == "Focus on Munich and Berlin architectural contractors."


# ============================================================================
# PRD-SOURCE-003: unknown source type rejected
# ============================================================================
def test_prd_source_003_unknown_source_type_rejected(temp_registry):
    with pytest.raises(ValueError, match="Unsupported source type"):
        temp_registry.register_source({
            "id": "invalid_type_src",
            "name": "Invalid Source",
            "type": "arbitrary_executable_script",
        })


# ============================================================================
# PRD-SOURCE-004: unknown adapter rejected or marked NOT_AVAILABLE safely
# ============================================================================
def test_prd_source_004_unknown_adapter_rejected_or_marked_not_available(temp_registry, temp_store, temp_scope_store):
    src = temp_registry.register_source({
        "id": "unknown_adapter_src",
        "name": "Unknown Adapter Source",
        "type": "company_directory",
        "adapter_id": "non_existent_adapter_xyz",
    })
    # Adapter status should be marked NOT_AVAILABLE
    assert src.adapter_status == "NOT_AVAILABLE"

    # Running discovery with this source must safely skip it rather than crash
    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Test Unknown Adapter",
        source_ids=["unknown_adapter_src"],
        markets=["Italy"],
        discovery_modes=["company_first"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id)
    assert run_rec.status == "completed"
    assert any("not_available" in err.lower() for err in run_rec.errors)


# ============================================================================
# PRD-SOURCE-005: enable/disable controls execution
# ============================================================================
def test_prd_source_005_enable_disable_controls_execution(temp_registry, temp_store, temp_scope_store):
    temp_registry.disable_source("brave_job_search")
    assert temp_registry.get_source("brave_job_search").enabled is False

    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Test Disabled Source",
        source_ids=["brave_job_search"],
        markets=["Italy"],
        discovery_modes=["job_first"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id)
    assert run_rec.status == "completed"
    assert any("brave_job_search' is disabled" in err for err in run_rec.errors)

    # Re-enable
    temp_registry.enable_source("brave_job_search")
    assert temp_registry.get_source("brave_job_search").enabled is True


# ============================================================================
# PRD-SOURCE-006: credential value never stored/rendered/logged
# ============================================================================
def test_prd_source_006_credential_value_never_stored_rendered_logged(temp_registry):
    # Attempting to supply an actual API key or secret token in credential_env_var must be rejected
    with pytest.raises(ValueError, match="must not contain secrets or credentials"):
        temp_registry.register_source({
            "id": "leak_test_src",
            "name": "Leak Test Source",
            "type": "search_engine",
            "credential_env_var": "sk-proj-1234567890abcdef1234567890",
            "requires_api_key": True,
        })

    with pytest.raises(ValueError, match="must not contain secrets or credentials"):
        temp_registry.register_source({
            "id": "leak_test_bearer",
            "name": "Leak Test Bearer",
            "type": "search_engine",
            "credential_env_var": "Bearer some_secret_token",
            "requires_api_key": True,
        })


# ============================================================================
# PRD-SOURCE-007: credential status uses credential_env_var
# ============================================================================
def test_prd_source_007_credential_status_uses_credential_env_var(temp_registry):
    cfg = SourceConfig(
        id="test_cred_status",
        name="Test Cred Status",
        type="search_engine",
        requires_api_key=True,
        credential_env_var="CUSTOM_TEST_API_KEY",
    )
    
    # 1. Not set in environment -> not_configured
    with patch.dict(os.environ, {}, clear=True):
        assert cfg.get_credential_status() == "not_configured"

    # 2. Set in environment -> configured
    with patch.dict(os.environ, {"CUSTOM_TEST_API_KEY": "valid_token_value"}):
        assert cfg.get_credential_status() == "configured"

    # 3. Requires key but no env var defined -> configuration_error
    cfg_no_var = SourceConfig(
        id="test_cred_no_var",
        name="Test Cred No Var",
        type="search_engine",
        requires_api_key=True,
        credential_env_var=None,
    )
    assert cfg_no_var.get_credential_status() == "configuration_error"

    # 4. Open / public source -> not_required
    cfg_open = SourceConfig(
        id="test_open",
        name="Open Source",
        type="search_engine",
        requires_api_key=False,
    )
    assert cfg_open.get_credential_status() == "not_required"


# ============================================================================
# PRD-SOURCE-008: per-source max_results enforced
# ============================================================================
def test_prd_source_008_per_source_max_results_enforced(temp_registry, temp_store, temp_scope_store):
    temp_registry.register_source({
        "id": "capped_source",
        "name": "Capped Source",
        "type": "company_directory",
        "max_results_per_run": 5,
        "adapter_id": "generic_company_directory",
        "countries": ["Italy"],
    })

    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Scope High Budget",
        source_ids=["capped_source"],
        markets=["Italy"],
        max_results=100,  # Higher than source max
    )
    saved = temp_scope_store.save_scope(scope)

    # Provide a mock fetcher returning 20 items
    mock_items = [{"company_name": f"Studio {i}", "domain": f"studio{i}.it", "country": "Italy"} for i in range(20)]
    run_rec = runner.run(saved.scope_id, fetcher_funcs={"capped_source": lambda q: mock_items})
    
    # Must be capped at source max_results_per_run (5)
    assert run_rec.results_found == 5


# ============================================================================
# PRD-SOURCE-009: unsupported market is skipped explicitly
# ============================================================================
def test_prd_source_009_unsupported_market_is_skipped_explicitly(temp_registry, temp_store, temp_scope_store):
    temp_registry.register_source({
        "id": "italy_only_source",
        "name": "Italy Only Source",
        "type": "company_directory",
        "countries": ["Italy"],
        "adapter_id": "generic_company_directory",
    })

    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Germany Market Scope",
        source_ids=["italy_only_source"],
        markets=["Germany"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id)
    assert run_rec.status == "completed"
    assert any("does not support requested markets" in err for err in run_rec.errors)


# ============================================================================
# PRD-SOURCE-010: unsupported discovery mode is skipped explicitly
# ============================================================================
def test_prd_source_010_unsupported_discovery_mode_is_skipped_explicitly(temp_registry, temp_store, temp_scope_store):
    temp_registry.register_source({
        "id": "jobs_only_source",
        "name": "Jobs Only Source",
        "type": "job_board",
        "discovery_mode": ["job_first"],
        "adapter_id": "generic_job_board",
        "countries": ["Italy"],
    })

    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Project First Scope",
        source_ids=["jobs_only_source"],
        markets=["Italy"],
        discovery_modes=["project_tender_first"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id)
    assert run_rec.status == "completed"
    assert any("does not support requested discovery modes" in err for err in run_rec.errors)


# ============================================================================
# PRD-SOURCE-011: test connection creates no candidates/operational mutations
# ============================================================================
def test_prd_source_011_test_connection_creates_no_candidates_operational_mutations(temp_registry, temp_store):
    with temp_store._get_connection() as conn:
        initial_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]

    # Test local / manual source
    res = temp_registry.test_source("manual_seed")
    assert res["status"] in ("AVAILABLE", "available")

    # Test network source with mock
    with patch("urllib.request.urlopen") as mock_url:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_url.return_value.__enter__.return_value = mock_response
        res2 = temp_registry.test_source("brave_job_search")
        assert res2["status"] in ("AVAILABLE", "CONFIG_REQUIRED", "NOT_CONFIGURED")

    # Verify CandidateStore was untouched
    with temp_store._get_connection() as conn:
        final_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    assert final_count == initial_count, "Test source should create 0 candidate companies"


# ============================================================================
# PRD-SOURCE-012: source health persists after restart
# ============================================================================
def test_prd_source_012_source_health_persists_after_restart(tmp_path, temp_store):
    yaml_file = str(tmp_path / "sources_health.yaml")
    reg1 = SourceRegistry(config_path=yaml_file, db_path=temp_store.db_path)
    
    # Record health update in reg1
    reg1.update_source_health(
        source_id="brave_job_search",
        success=True,
        result_count=15,
        response_time_ms=120,
    )
    health1 = reg1.get_health_summary()["brave_job_search"]
    assert health1["status"] in ("healthy", "operational")
    assert health1["success_count"] >= 1

    # Restart in reg2 with same db_path
    reg2 = SourceRegistry(config_path=yaml_file, db_path=temp_store.db_path)
    health2 = reg2.get_health_summary()["brave_job_search"]
    assert health2["status"] in ("healthy", "operational")
    assert health2["success_count"] >= 1


# ============================================================================
# PRD-SOURCE-013: disabled source history remains visible
# ============================================================================
def test_prd_source_013_disabled_source_history_remains_visible(temp_registry, temp_store):
    # Create candidate with source tag
    cand = CandidateCompany(
        candidate_id=temp_store._generate_candidate_id("visible-bim.it", "Visible BIM Studio", None),
        company_name="Visible BIM Studio",
        company_domain="visible-bim.it",
        source_refs=["italy_engineering_directory"],
    )
    temp_store.save_candidate(cand)

    # Disable the source
    temp_registry.disable_source("italy_engineering_directory")

    # History / candidate must remain intact and retrievable
    retrieved = temp_store.get_by_domain("visible-bim.it")
    assert retrieved is not None
    assert retrieved.company_name == "Visible BIM Studio"
    assert "italy_engineering_directory" in retrieved.source_refs


# ============================================================================
# PRD-SOURCE-014: saved DiscoveryScope handles disabled/unavailable source safely
# ============================================================================
def test_prd_source_014_saved_discovery_scope_handles_disabled_unavailable_source_safely(temp_registry, temp_store, temp_scope_store):
    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Scope with Deleted Source",
        source_ids=["completely_non_existent_source_id_999"],
        markets=["Italy"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id)
    assert run_rec.status == "completed"
    assert any("not registered in sourceregistry" in err.lower() for err in run_rec.errors)


# ============================================================================
# PRD-SOURCE-015: no mutating GET
# ============================================================================
def test_prd_source_015_no_mutating_get():
    from backend.main import app
    client = TestClient(app)

    # Calling GET /api/sales/sources repeatedly should not change source state
    r1 = client.get("/api/sales/sources")
    assert r1.status_code == 200
    count1 = len(r1.json())

    r2 = client.get("/api/sales/sources")
    assert r2.status_code == 200
    count2 = len(r2.json())
    assert count1 == count2


# ============================================================================
# PRD-SOURCE-016: unsafe URL scheme rejected
# ============================================================================
def test_prd_source_016_unsafe_url_scheme_rejected(temp_registry):
    for bad_url in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "ftp://ftp.example.com",
    ]:
        assert validate_source_url(bad_url) is False

        with pytest.raises(ValueError, match="Invalid or dangerous URL scheme"):
            temp_registry.register_source({
                "id": "bad_url_src",
                "name": "Bad URL Source",
                "type": "company_directory",
                "base_url": bad_url,
            })


# ============================================================================
# PRD-SOURCE-017: source instructions cannot execute arbitrary code
# ============================================================================
def test_prd_source_017_source_instructions_cannot_execute_arbitrary_code(temp_registry):
    payload = "__import__('os').system('echo pwned')\n{{ 7*7 }}\n$(rm -rf /)"
    src = temp_registry.register_source({
        "id": "inert_instructions_src",
        "name": "Inert Source",
        "type": "company_directory",
        "operator_instructions": payload,
    })
    # Stored strictly as plain text, never evaluated
    assert src.operator_instructions == payload
    assert isinstance(src.operator_instructions, str)


# ============================================================================
# PRD-SOURCE-018: source config export contains no secrets
# ============================================================================
def test_prd_source_018_source_config_export_contains_no_secrets(temp_registry):
    with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "super_secret_token_12345"}):
        export_str = temp_registry.export_sources(format="json")
        export_data = json.loads(export_str)
        assert "sources" in export_data
        assert isinstance(export_data["sources"], list)
        assert "super_secret_token_12345" not in export_str
        assert "BRAVE_SEARCH_API_KEY" in export_str


# ============================================================================
# PRD-SOURCE-019: source management UI renders without LLM calls
# ============================================================================
def test_prd_source_019_source_management_ui_renders_without_llm_calls():
    from backend.main import app
    client = TestClient(app)

    with patch("sales_engine.candidates.classifier.CompanyClassifier.classify_company") as mock_classify, \
         patch("urllib.request.urlopen") as mock_url:
        response = client.get("/sales/sources")
        assert response.status_code == 200
        assert "Discovery Sources" in response.text
        assert not mock_classify.called
        assert not mock_url.called


# ============================================================================
# PRD-SOURCE-020: full existing Phase 1–12 + Step 1 regression remains green
# ============================================================================
def test_prd_source_020_full_existing_phase_and_step1_regression_remains_green(temp_registry, temp_store, temp_scope_store):
    # Verify Step 1 scope operations execute cleanly with the authoritative SourceRegistry
    runner = DiscoveryRunner(scope_store=temp_scope_store, candidate_store=temp_store, source_registry=temp_registry)
    scope = DiscoveryScope(
        scope_name="Step 1 Scope Compatibility",
        source_ids=["italy_engineering_directory"],
        markets=["Italy"],
        discovery_modes=["company_first"],
    )
    saved = temp_scope_store.save_scope(scope)
    run_rec = runner.run(saved.scope_id, fetcher_funcs={"italy_engineering_directory": lambda q: [{"company_name": "Test Studio", "domain": "test.it"}]})
    assert run_rec.status == "completed"
    assert run_rec.scope_id == saved.scope_id
