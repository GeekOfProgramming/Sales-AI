"""
tests/test_repository_cleanup.py
Authoritative verification test suite for Production Readiness Step 4:
Repository Cleanup, Release Structure, Hygiene, and Deterministic Packaging.

Covers tests PRD-CLEAN-001 through PRD-CLEAN-020.
"""

import os
import json
import yaml
import zipfile
from pathlib import Path
import pytest

from scripts.rebuild_zip import should_exclude, build_release_zip, build_release_zip_stats
from scripts.audit_repository import audit_repository, ALLOWED_ROOT_FILES


REPO_ROOT = Path(__file__).parent.parent.resolve()


def test_prd_clean_001_release_manifest_contains_required_runtime_modules():
    """PRD-CLEAN-001: Release manifest must exist and define required runtime include roots."""
    manifest_path = REPO_ROOT / "release_manifest.json"
    assert manifest_path.exists(), "release_manifest.json must exist in repository root"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "profiles" in data
    assert "developer_source" in data["profiles"]
    roots = data["profiles"]["developer_source"]["include_roots"]

    required_roots = [
        "sales_engine",
        "backend",
        "ai_engine",
        "config",
        "data/rules",
        "static",
        "requirements.txt",
        "start_server.py",
        "run_sales_ai.bat",
    ]
    for req in required_roots:
        assert req in roots, f"Required root {req} must be in release manifest include roots"


def test_prd_clean_002_release_excludes_runtime_db_files(tmp_path):
    """PRD-CLEAN-002: Release packaging must exclude runtime DB and SQLite files."""
    assert should_exclude(Path("sales_outreach.db")) is True
    assert should_exclude(Path("data/sales_outreach.db")) is True
    assert should_exclude(Path("candidates.db")) is True
    assert should_exclude(Path("local_store.sqlite")) is True
    assert should_exclude(Path("data/sales_outreach.db-wal")) is True
    assert should_exclude(Path("data/sales_outreach.db-shm")) is True

    # Test in synthetic build
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sales_engine").mkdir()
    (proj / "sales_engine" / "mod.py").write_text("x = 1")
    (proj / "candidates.db").write_bytes(b"SQLITE")
    (proj / "runtime.sqlite3").write_bytes(b"SQLITE")

    out_zip = tmp_path / "out.zip"
    count = build_release_zip(proj, out_zip)
    assert count == 1

    with zipfile.ZipFile(out_zip, "r") as zf:
        namelist = zf.namelist()
    assert "sales_engine/mod.py" in namelist
    assert "candidates.db" not in namelist
    assert "runtime.sqlite3" not in namelist


def test_prd_clean_003_release_excludes_env_and_secret_files(tmp_path):
    """PRD-CLEAN-003: Release packaging must exclude .env and sensitive credential files."""
    assert should_exclude(Path(".env")) is True
    assert should_exclude(Path(".env.local")) is True
    assert should_exclude(Path(".env.production")) is True
    assert should_exclude(Path("credentials.json")) is True
    assert should_exclude(Path("client_secret.json")) is True
    assert should_exclude(Path("token.json")) is True
    assert should_exclude(Path("oauth_token.txt")) is True

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("x = 1")
    (proj / ".env").write_text("SECRET=123")
    (proj / "client_secret.json").write_text("{\"secret\": \"abc\"}")

    out_zip = tmp_path / "out.zip"
    build_release_zip(proj, out_zip)
    with zipfile.ZipFile(out_zip, "r") as zf:
        namelist = zf.namelist()
    assert "app.py" in namelist
    assert ".env" not in namelist
    assert "client_secret.json" not in namelist


def test_prd_clean_004_release_excludes_cache_and_compiled_artifacts(tmp_path):
    """PRD-CLEAN-004: Release packaging must exclude cache directories and compiled bytecode."""
    assert should_exclude(Path("__pycache__/mod.cpython-314.pyc")) is True
    assert should_exclude(Path("sub/__pycache__/mod.cpython-314.pyc")) is True
    assert should_exclude(Path(".pytest_cache/v/cache/step")) is True
    assert should_exclude(Path("file.pyc")) is True
    assert should_exclude(Path("file.pyo")) is True
    assert should_exclude(Path("file.pyd")) is True


def test_prd_clean_005_release_excludes_nested_zip_artifacts(tmp_path):
    """PRD-CLEAN-005: Release packaging must exclude nested ZIP files."""
    assert should_exclude(Path("test.zip")) is True
    assert should_exclude(Path("SalesAI_export.zip")) is True
    assert should_exclude(Path("backup/old.zip")) is True


def test_prd_clean_006_archived_phase_docs_are_not_in_repository_root():
    """PRD-CLEAN-006: Historical phase specs, QA golden specs, and patch docs must not be in repo root."""
    root_files = [f.name for f in REPO_ROOT.iterdir() if f.is_file()]
    for f in root_files:
        assert not f.startswith("PHASE"), f"Historical phase doc {f} must not be in repo root"
        assert not f.startswith("PRODUCTION-STEP"), f"Production step doc {f} must not be in repo root"

    # Verify they exist in docs/archive/
    archive_dir = REPO_ROOT / "docs" / "archive"
    assert archive_dir.exists()
    assert (archive_dir / "phase_specs").exists()
    assert (archive_dir / "qa_specs").exists()
    assert (archive_dir / "adversarial_patches").exists()
    assert (archive_dir / "production_steps").exists()

    archived_files = list(archive_dir.rglob("*.md"))
    assert len(archived_files) >= 15, "Archive must contain all preserved historical specs"


def test_prd_clean_007_historical_archive_has_explicit_archived_warning():
    """PRD-CLEAN-007: docs/archive/README.md must explicitly warn that specs are non-authoritative."""
    readme_path = REPO_ROOT / "docs" / "archive" / "README.md"
    assert readme_path.exists(), "docs/archive/README.md must exist"
    content = readme_path.read_text(encoding="utf-8")
    assert "ARCHIVED" in content
    assert "NOT CURRENT IMPLEMENTATION SPEC" in content


def test_prd_clean_008_active_readme_paths_remain_valid():
    """PRD-CLEAN-008: Active README and README-TEST files must exist in repository root."""
    assert (REPO_ROOT / "README.md").exists()
    assert (REPO_ROOT / "README-TEST.md").exists()
    assert (REPO_ROOT / "requirements.txt").exists()


def test_prd_clean_009_salesai_startup_entrypoint_remains_present():
    """PRD-CLEAN-009: SalesAI production startup files must remain present and valid."""
    start_server = REPO_ROOT / "start_server.py"
    run_bat = REPO_ROOT / "run_sales_ai.bat"
    assert start_server.exists(), "start_server.py must exist in root"
    assert run_bat.exists(), "run_sales_ai.bat must exist in root"

    content = start_server.read_text(encoding="utf-8")
    assert "ENABLE_PUBLIC_TUNNEL" in content, "start_server.py must gate public tunnel behind explicit flag"


def test_prd_clean_010_frozen_test_suites_remain_present():
    """PRD-CLEAN-010: Frozen regression test suites for Phases 1-12 must remain intact."""
    required_test_files = [
        "test_discovery.py",
        "test_extraction.py",
        "test_enrichment.py",
        "test_outreach.py",
        "test_sending.py",
        "test_admin_gate.py",
        "test_phase10_regressions.py",
        "test_phase11_regressions.py",
        "test_phase12_regressions.py",
    ]
    for tf in required_test_files:
        p = REPO_ROOT / "tests" / tf
        assert p.exists(), f"Frozen test suite {tf} must remain present"


def test_prd_clean_011_step1_2_3_tests_remain_present():
    """PRD-CLEAN-011: Step 1, Step 2, and Step 3 tests must remain present under tests/."""
    assert (REPO_ROOT / "tests" / "test_search_scope.py").exists()
    assert (REPO_ROOT / "tests" / "test_source_management.py").exists()
    assert (REPO_ROOT / "tests" / "test_discovery_control_ui.py").exists()


def test_prd_clean_012_source_config_remains_present():
    """PRD-CLEAN-012: config/discovery_sources.yaml must exist and load valid YAML."""
    cfg_path = REPO_ROOT / "config" / "discovery_sources.yaml"
    assert cfg_path.exists()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "sources" in data
    assert len(data["sources"]) > 0


def test_prd_clean_013_ui_templates_and_static_assets_remain_present():
    """PRD-CLEAN-013: All required web console templates and assets must remain present."""
    tpl_dir = REPO_ROOT / "backend" / "sales_ui" / "templates"
    assert (tpl_dir / "discovery.html").exists()
    assert (tpl_dir / "discovery_profile.html").exists()
    assert (tpl_dir / "discovery_run_detail.html").exists()
    assert (tpl_dir / "sources.html").exists()
    assert (tpl_dir / "base.html").exists()
    assert (REPO_ROOT / "static").exists()


def test_prd_clean_014_no_unresolved_import_caused_by_relocation():
    """PRD-CLEAN-014: Relocation of files must cause zero import failures in runtime code."""
    from sales_engine.discovery_v2.scope import DiscoveryScope, DiscoveryScopeStore, preview_discovery_plan
    from sales_engine.discovery_v2.source_registry import SourceRegistry, SourceConfig
    from sales_engine.discovery_v2.adapters import get_default_adapter_for_source
    from backend.main import app
    from backend.sales_ui.router import router
    from scripts.audit_repository import audit_repository
    from scripts.rebuild_zip import build_release_zip_stats

    assert app is not None
    assert router is not None
    assert DiscoveryScope is not None


def test_prd_clean_015_no_unexpected_production_binary_in_release():
    """PRD-CLEAN-015: No third-party binaries or executables may ship in release."""
    assert should_exclude(Path("cloudflared.exe")) is True
    assert should_exclude(Path("some_tool.exe")) is True
    assert should_exclude(Path("sub/binary.exe")) is True


def test_prd_clean_016_sample_exports_contain_no_obvious_secret_patterns():
    """PRD-CLEAN-016: Sample exports in exports/sample_export/ must be sanitized of real secrets."""
    sample_dir = REPO_ROOT / "exports" / "sample_export"
    assert sample_dir.exists()

    for item in sample_dir.rglob("*"):
        if item.is_file() and item.suffix.lower() in (".json", ".csv"):
            text = item.read_text(encoding="utf-8", errors="replace")
            assert "BEGIN PRIVATE KEY" not in text
            assert "AIzaSy" not in text  # Google API key pattern
            assert "sk-proj-" not in text  # OpenAI API key pattern


def test_prd_clean_017_rebuild_zip_output_is_deterministic(tmp_path):
    """PRD-CLEAN-017: rebuild_zip output must be deterministic in file list and count."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sales_engine").mkdir()
    (proj / "sales_engine" / "b.py").write_text("b = 2")
    (proj / "sales_engine" / "a.py").write_text("a = 1")
    (proj / "README.md").write_text("# Hello")

    zip1 = tmp_path / "z1.zip"
    zip2 = tmp_path / "z2.zip"

    stats1 = build_release_zip_stats(proj, zip1, profile="developer_source")
    stats2 = build_release_zip_stats(proj, zip2, profile="developer_source")

    assert stats1["included_count"] == stats2["included_count"]
    assert stats1["denylist_violations_count"] == 0

    with zipfile.ZipFile(zip1, "r") as z1, zipfile.ZipFile(zip2, "r") as z2:
        names1 = z1.namelist()
        names2 = z2.namelist()

    assert names1 == names2
    # Check alphabetical ordering
    assert names1 == sorted(names1)


def test_prd_clean_018_release_does_not_recursively_include_itself(tmp_path):
    """PRD-CLEAN-018: rebuild_zip must never package SalesAI_export.zip inside itself."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "mod.py").write_text("x = 1")
    target_zip = proj / "SalesAI_export.zip"

    build_release_zip(proj, target_zip)
    with zipfile.ZipFile(target_zip, "r") as zf:
        namelist = zf.namelist()

    assert "SalesAI_export.zip" not in namelist
    for name in namelist:
        assert not name.endswith(".zip")


def test_prd_clean_019_repository_audit_script_reports_zero_critical_hygiene_violations():
    """PRD-CLEAN-019: Repository audit script must report zero hygiene violations."""
    report = audit_repository(REPO_ROOT)
    assert report["clean"] is True, f"Audit script flagged violations: {report['violations']}"
    assert report["total_violations"] == 0


def test_prd_clean_020_cleanup_does_not_change_business_behavior():
    """PRD-CLEAN-020: Repository cleanup must preserve core business functionality."""
    from sales_engine.discovery_v2.scope import DiscoveryScope, preview_discovery_plan
    from sales_engine.discovery_v2.source_registry import SourceRegistry

    registry = SourceRegistry()
    scope = DiscoveryScope(
        scope_name="Cleanup Verification Scope",
        markets=["Italy"],
        discovery_modes=["company_first"],
        source_ids=["italy_engineering_directory"],
        max_results=20,
    )

    plan = preview_discovery_plan(scope, registry)
    assert plan["scope_name"] == "Cleanup Verification Scope"
    assert any(s["source_id"] == "italy_engineering_directory" for s in plan["executable_sources"])
    assert plan["effective_total_budget"] == 20
