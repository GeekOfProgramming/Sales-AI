"""
tests/test_discovery_db_path_contract.py
Isolated contract and propagation tests for Discovery v2 database path resolution.
Zero operational DB access, zero live Discovery providers, zero network requests.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sales_engine.db_resolver import (
    _DEFAULT_REPO_ROOT,
    DatabaseConfigurationConflictError,
    DatabaseResolutionError,
    ProtectedDatabasePathError,
)
from sales_engine.discovery_v2.db_path import (
    DEFAULT_DISCOVERY_DB_STR,
    DiscoveryDbMode,
    resolve_discovery_db_path,
)


def test_legacy_p3_precedence_and_ignores_sales_outreach_db(tmp_path: Path) -> None:
    """LEGACY_P3 preserves SALESAI_DB_PATH > SALESAI_CANDIDATES_DB > default and ignores SALES_OUTREACH_DB."""
    outreach = str(tmp_path / "outreach.db")
    db_path = str(tmp_path / "salesai_db.db")
    cand_db = str(tmp_path / "candidates.db")

    # Only SALES_OUTREACH_DB set -> ignored in LEGACY_P3 (no silent switch from historical P-3)
    assert (
        resolve_discovery_db_path(env={"SALES_OUTREACH_DB": outreach})
        == DEFAULT_DISCOVERY_DB_STR
    )

    # SALESAI_DB_PATH wins over SALESAI_CANDIDATES_DB and SALES_OUTREACH_DB
    assert (
        resolve_discovery_db_path(
            env={
                "SALES_OUTREACH_DB": outreach,
                "SALESAI_DB_PATH": db_path,
                "SALESAI_CANDIDATES_DB": cand_db,
            }
        )
        == db_path
    )

    # Only SALESAI_CANDIDATES_DB set -> selected when include_candidates_env=True, ignored when False
    assert (
        resolve_discovery_db_path(
            env={"SALESAI_CANDIDATES_DB": cand_db},
            include_candidates_env=True,
        )
        == cand_db
    )
    assert (
        resolve_discovery_db_path(
            env={"SALESAI_CANDIDATES_DB": cand_db},
            include_candidates_env=False,
        )
        == DEFAULT_DISCOVERY_DB_STR
    )


def test_centralized_legacy_uses_p1_precedence(tmp_path: Path) -> None:
    """CENTRALIZED_LEGACY adopts centralized P-1 precedence via db_resolver."""
    outreach = tmp_path / "outreach.db"
    db_path = tmp_path / "salesai_db.db"

    resolved = resolve_discovery_db_path(
        mode=DiscoveryDbMode.CENTRALIZED_LEGACY,
        env={"SALES_OUTREACH_DB": str(outreach), "SALESAI_DB_PATH": str(db_path)},
    )
    assert resolved == str(outreach.resolve())


def test_explicit_db_path_overrides_env_across_all_modes(tmp_path: Path) -> None:
    """Explicit db_path overrides ambient environment variables (including invalid ones) in every mode."""
    explicit = tmp_path / "explicit.db"
    env_conflict = {
        "SALES_OUTREACH_DB": str(tmp_path / "env_a.db"),
        "SALESAI_DB_PATH": str(tmp_path / "env_b.db"),
        "SALESAI_CANDIDATES_DB": str(tmp_path / "env_c.db"),
    }
    env_invalid = {
        "SALES_OUTREACH_DB": "   ",
        "SALESAI_DB_PATH": "",
        "SALESAI_CANDIDATES_DB": "   ",
    }

    for env in (env_conflict, env_invalid):
        assert (
            resolve_discovery_db_path(str(explicit), mode=DiscoveryDbMode.LEGACY_P3, env=env)
            == str(explicit)
        )
        assert (
            resolve_discovery_db_path(explicit, mode=DiscoveryDbMode.CENTRALIZED_LEGACY, env=env)
            == str(explicit.resolve())
        )
        assert (
            resolve_discovery_db_path(explicit, mode=DiscoveryDbMode.CENTRALIZED_STRICT, env=env)
            == str(explicit.resolve())
        )


def test_centralized_strict_equivalent_vs_conflicting_and_operational_rejection(
    tmp_path: Path,
) -> None:
    """CENTRALIZED_STRICT accepts equivalent normalized paths and rejects conflicts/operational DBs."""
    canonical = (tmp_path / "shared.db").resolve()
    alt = str(canonical).replace("\\", "/")
    if os.name == "nt":
        alt = alt.lower()

    assert (
        resolve_discovery_db_path(
            mode=DiscoveryDbMode.CENTRALIZED_STRICT,
            env={"SALES_OUTREACH_DB": str(canonical), "SALESAI_DB_PATH": alt},
        )
        == str(canonical)
    )

    with pytest.raises(DatabaseConfigurationConflictError):
        resolve_discovery_db_path(
            mode=DiscoveryDbMode.CENTRALIZED_STRICT,
            env={
                "SALES_OUTREACH_DB": str(tmp_path / "a.db"),
                "SALESAI_DB_PATH": str(tmp_path / "b.db"),
            },
        )

    with pytest.raises(ProtectedDatabasePathError):
        resolve_discovery_db_path(
            _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db",
            mode=DiscoveryDbMode.CENTRALIZED_STRICT,
        )


def test_relative_and_memory_semantics(tmp_path: Path) -> None:
    """Relative paths and :memory: work in LEGACY_P3/CENTRALIZED_LEGACY and follow STRICT rules."""
    assert (
        resolve_discovery_db_path("rel/custom.db", mode=DiscoveryDbMode.LEGACY_P3)
        == "rel/custom.db"
    )
    assert (
        resolve_discovery_db_path(":memory:", mode=DiscoveryDbMode.LEGACY_P3, allow_memory=True)
        == ":memory:"
    )
    assert (
        resolve_discovery_db_path(":memory:", mode=DiscoveryDbMode.CENTRALIZED_STRICT, allow_memory=True)
        == ":memory:"
    )
    with pytest.raises(DatabaseResolutionError):
        resolve_discovery_db_path("rel/custom.db", mode=DiscoveryDbMode.CENTRALIZED_STRICT)
    with pytest.raises(DatabaseResolutionError):
        resolve_discovery_db_path(":memory:", mode=DiscoveryDbMode.CENTRALIZED_STRICT, allow_memory=False)


@pytest.mark.parametrize(
    "bad_env",
    [
        {"SALESAI_DB_PATH": ""},
        {"SALESAI_DB_PATH": "", "SALESAI_CANDIDATES_DB": "C:/temp/valid_candidates.db"},
        {"SALESAI_CANDIDATES_DB": ""},
        {"SALESAI_DB_PATH": "   "},
        {"SALESAI_CANDIDATES_DB": "\t\n"},
    ],
)
def test_legacy_p3_rejects_selected_empty_or_whitespace_env_without_fallback(
    bad_env: dict[str, str],
) -> None:
    """Selected empty or whitespace env values fail closed without falling back to lower env or production."""
    with pytest.raises(DatabaseResolutionError):
        resolve_discovery_db_path(
            mode=DiscoveryDbMode.LEGACY_P3,
            env=bad_env,
            include_candidates_env=True,
        )


@pytest.mark.parametrize("ignored_val", ["", "   "])
def test_legacy_p3_ignores_invalid_lower_priority_when_higher_valid(
    tmp_path: Path, ignored_val: str
) -> None:
    """Valid SALESAI_DB_PATH is selected cleanly even when ignored SALESAI_CANDIDATES_DB is empty/whitespace."""
    valid_db = str(tmp_path / "primary_salesai.db")
    assert (
        resolve_discovery_db_path(
            mode=DiscoveryDbMode.LEGACY_P3,
            env={"SALESAI_DB_PATH": valid_db, "SALESAI_CANDIDATES_DB": ignored_val},
            include_candidates_env=True,
        )
        == valid_db
    )


def test_historical_downstream_selection_preserved_when_only_candidates_db_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When only SALESAI_CANDIDATES_DB is set, CandidateStore uses it while SourceRegistry keeps its independent selection."""
    from sales_engine.discovery_v2.bridges import Phase9AuthorityBridge
    from sales_engine.discovery_v2.run import get_cli_services
    from sales_engine.discovery_v2.scope import DiscoveryRunner, run_discovery
    from sales_engine.discovery_v2.source_registry import SourceRegistry

    cand_db = str(tmp_path / "candidates_only.db")
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)

    # SourceRegistry and Phase9AuthorityBridge ignore SALESAI_CANDIDATES_DB historically
    with (
        patch.object(SourceRegistry, "_load_configs"),
        patch.object(SourceRegistry, "_init_health_db"),
    ):
        reg = SourceRegistry()
        assert reg.db_path == DEFAULT_DISCOVERY_DB_STR

    bridge = Phase9AuthorityBridge()
    assert bridge.db_path == DEFAULT_DISCOVERY_DB_STR

    # get_cli_services selects cand_db for CandidateStore & Phase9AuthorityBridge, calls SourceRegistry() independently
    fake_store = MagicMock()
    fake_store.db_path = cand_db
    with (
        patch("sales_engine.discovery_v2.run.CandidateStore", return_value=fake_store) as mock_cand,
        patch("sales_engine.discovery_v2.run.SourceRegistry") as mock_reg_cls,
        patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge") as mock_bridge_cls,
        patch("sales_engine.discovery_v2.run.CompanyClassifier"),
        patch("sales_engine.discovery_v2.run.DiscoveryEngineV2"),
        patch("sales_engine.discovery_v2.run.Phase10ConsoleBridge"),
    ):
        get_cli_services()
        mock_cand.assert_called_once_with(db_path=cand_db)
        mock_reg_cls.assert_called_once_with()
        mock_bridge_cls.assert_called_once_with(db_path=cand_db)

    # run_discovery propagates cand_db to DiscoveryScopeStore & CandidateStore; DiscoveryRunner keeps SourceRegistry()
    with (
        patch("sales_engine.discovery_v2.scope.DiscoveryScopeStore") as mock_scope_store,
        patch("sales_engine.discovery_v2.scope.CandidateStore", return_value=fake_store) as mock_cand,
        patch("sales_engine.discovery_v2.scope.SourceRegistry") as mock_reg_cls,
        patch("sales_engine.discovery_v2.scope.DiscoveryEngineV2"),
        patch.object(DiscoveryRunner, "run", return_value=MagicMock()),
    ):
        run_discovery(scope_id="scp_test")
        mock_scope_store.assert_called_once_with(db_path=cand_db)
        mock_cand.assert_called_once_with(db_path=cand_db)
        mock_reg_cls.assert_called_once_with()


def test_explicit_orchestration_db_path_preserves_independent_source_registry_and_injected_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit orchestration db_path does not redirect default SourceRegistry; explicit SourceRegistry injection preserved."""
    from sales_engine.discovery_v2.run import get_cli_services
    from sales_engine.discovery_v2.scope import DiscoveryRunner

    explicit_db = str(tmp_path / "explicit_orch.db")
    env_reg_db = str(tmp_path / "registry_env.db")
    monkeypatch.setenv("SALESAI_DB_PATH", env_reg_db)

    fake_store = MagicMock()
    fake_store.db_path = explicit_db
    with (
        patch("sales_engine.discovery_v2.run.CandidateStore", return_value=fake_store) as mock_cand,
        patch("sales_engine.discovery_v2.run.SourceRegistry") as mock_reg_cls,
        patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge") as mock_bridge_cls,
        patch("sales_engine.discovery_v2.run.CompanyClassifier"),
        patch("sales_engine.discovery_v2.run.DiscoveryEngineV2"),
        patch("sales_engine.discovery_v2.run.Phase10ConsoleBridge"),
    ):
        get_cli_services(db_path=explicit_db)
        mock_cand.assert_called_once_with(db_path=explicit_db)
        mock_reg_cls.assert_called_once_with()
        mock_bridge_cls.assert_called_once_with(db_path=explicit_db)

    # Explicitly injected SourceRegistry instance is preserved verbatim without instantiating a new one
    custom_reg = MagicMock()
    with (
        patch("sales_engine.discovery_v2.scope.SourceRegistry") as mock_scope_reg_cls,
        patch("sales_engine.discovery_v2.scope.DiscoveryEngineV2"),
    ):
        runner = DiscoveryRunner(
            scope_store=MagicMock(),
            candidate_store=fake_store,
            source_registry=custom_reg,
        )
        assert runner.registry is custom_reg
        mock_scope_reg_cls.assert_not_called()


def test_invalid_env_aborts_before_sqlite_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selected empty or whitespace env fails closed BEFORE SourceRegistry, Bridge, or run_discovery opens SQLite."""
    from sales_engine.discovery_v2.bridges import Phase9AuthorityBridge
    from sales_engine.discovery_v2.run import get_cli_services
    from sales_engine.discovery_v2.scope import run_discovery
    from sales_engine.discovery_v2.source_registry import SourceRegistry

    for bad in ("", "   "):
        monkeypatch.setenv("SALESAI_DB_PATH", bad)
        with (
            patch("sales_engine.discovery_v2.source_registry.sqlite3.connect") as mock_reg_sql,
            patch.object(SourceRegistry, "_init_health_db") as mock_init_health,
            patch("sales_engine.discovery_v2.bridges.sqlite3.connect") as mock_br_sql,
            patch("sales_engine.discovery_v2.scope.sqlite3.connect") as mock_scope_sql,
            patch("sales_engine.candidates.candidate_store.sqlite3.connect") as mock_cand_sql,
        ):
            with pytest.raises(DatabaseResolutionError):
                SourceRegistry()
            with pytest.raises(DatabaseResolutionError):
                Phase9AuthorityBridge()
            with pytest.raises(DatabaseResolutionError):
                get_cli_services()
            with pytest.raises(DatabaseResolutionError):
                run_discovery(scope_id="scp_unused")

            mock_reg_sql.assert_not_called()
            mock_init_health.assert_not_called()
            mock_br_sql.assert_not_called()
            mock_scope_sql.assert_not_called()
            mock_cand_sql.assert_not_called()


def test_discovery_allow_memory_and_explicit_empty_rejection(tmp_path: Path) -> None:
    """Enforce allow_memory=False and reject explicit ''/'   ' while keeping None env resolution."""
    for mode in (
        DiscoveryDbMode.LEGACY_P3,
        DiscoveryDbMode.CENTRALIZED_LEGACY,
        DiscoveryDbMode.CENTRALIZED_STRICT,
    ):
        for bad_explicit in ("", "   "):
            with pytest.raises(DatabaseResolutionError):
                resolve_discovery_db_path(
                    bad_explicit,
                    mode=mode,
                    env={"SALESAI_DB_PATH": str(tmp_path / "fallback.db")},
                )
        for mem_val in (":memory:", ":MEMORY:"):
            assert resolve_discovery_db_path(mem_val, mode=mode, allow_memory=True) == ":memory:"
            with pytest.raises(DatabaseResolutionError):
                resolve_discovery_db_path(mem_val, mode=mode, allow_memory=False)
            env_key = "SALESAI_DB_PATH" if mode is DiscoveryDbMode.LEGACY_P3 else "SALES_OUTREACH_DB"
            assert resolve_discovery_db_path(mode=mode, env={env_key: mem_val}, allow_memory=True) == ":memory:"
            with pytest.raises(DatabaseResolutionError):
                resolve_discovery_db_path(mode=mode, env={env_key: mem_val}, allow_memory=False)

    # Lower-priority :memory: stays ignored in LEGACY_P3 when SALESAI_DB_PATH is valid
    valid = str(tmp_path / "valid_p3.db")
    assert (
        resolve_discovery_db_path(
            None,
            mode=DiscoveryDbMode.LEGACY_P3,
            env={"SALESAI_DB_PATH": valid, "SALESAI_CANDIDATES_DB": ":memory:"},
            allow_memory=False,
        )
        == valid
    )
