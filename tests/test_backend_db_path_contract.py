"""
tests/test_backend_db_path_contract.py
Isolated contract and mocked factory tests for Backend database path resolution.
Zero operational DB access, zero FastAPI startup, zero external providers.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.db_path import (
    DEFAULT_BACKEND_DB_STR,
    BackendDbMode,
    resolve_backend_db_path,
)
from sales_engine.db_resolver import (
    _DEFAULT_REPO_ROOT,
    DatabaseConfigurationConflictError,
    DatabaseResolutionError,
    ProtectedDatabasePathError,
)


def test_historical_p1_p2_p3_p4_precedence_matrix(tmp_path: Path) -> None:
    """Verify exact historical precedence across LEGACY_P1, LEGACY_P2, LEGACY_P3, and LEGACY_P4."""
    p_out = str(tmp_path / "outreach.db")
    p_db = str(tmp_path / "salesai_db.db")
    p_cand = str(tmp_path / "candidates.db")

    # 1. All unset -> default string
    for mode in (
        BackendDbMode.LEGACY_P1,
        BackendDbMode.LEGACY_P2,
        BackendDbMode.LEGACY_P3,
        BackendDbMode.LEGACY_P4,
    ):
        assert resolve_backend_db_path(mode=mode, env={}) == DEFAULT_BACKEND_DB_STR

    # 2. Only SALES_OUTREACH_DB set
    env_out = {"SALES_OUTREACH_DB": p_out}
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P1, env=env_out) == p_out
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P2, env=env_out) == p_out
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P3, env=env_out) == DEFAULT_BACKEND_DB_STR
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P4, env=env_out) == p_out

    # 3. Only SALESAI_DB_PATH set
    env_db = {"SALESAI_DB_PATH": p_db}
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P1, env=env_db) == p_db
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P2, env=env_db) == p_db
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P3, env=env_db) == p_db
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P4, env=env_db) == DEFAULT_BACKEND_DB_STR

    # 4. Only SALESAI_CANDIDATES_DB set
    env_cand = {"SALESAI_CANDIDATES_DB": p_cand}
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P1, env=env_cand) == p_cand
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P2, env=env_cand) == DEFAULT_BACKEND_DB_STR
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P3, env=env_cand) == p_cand
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P4, env=env_cand) == DEFAULT_BACKEND_DB_STR

    # 5. All three conflicting -> each mode follows its own historical precedence
    env_all = {
        "SALES_OUTREACH_DB": p_out,
        "SALESAI_DB_PATH": p_db,
        "SALESAI_CANDIDATES_DB": p_cand,
    }
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P1, env=env_all) == p_out
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P2, env=env_all) == p_out
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P3, env=env_all) == p_db
    assert resolve_backend_db_path(mode=BackendDbMode.LEGACY_P4, env=env_all) == p_out


def test_empty_string_semantics_distinguish_getenv_default_vs_or_chain(tmp_path: Path) -> None:
    """P-1/P-3 reject selected empty string; P-2/P-4 preserve historical empty-string fall-through."""
    lower = str(tmp_path / "lower.db")

    # P-1 and P-3 historically used os.getenv(k, fallback): selected "" fails closed
    with pytest.raises(DatabaseResolutionError):
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P1,
            env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": lower},
        )
    with pytest.raises(DatabaseResolutionError):
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P3,
            env={"SALESAI_DB_PATH": "", "SALESAI_CANDIDATES_DB": lower},
        )

    # Valid higher-priority ignores invalid lower-priority in P-1/P-3
    assert (
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P1,
            env={"SALES_OUTREACH_DB": lower, "SALESAI_DB_PATH": ""},
        )
        == lower
    )

    # P-2 and P-4 historically used `or` / `if env_path:`: "" falls through
    assert (
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P2,
            env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": lower},
        )
        == lower
    )
    assert (
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P4,
            env={"SALES_OUTREACH_DB": ""},
        )
        == DEFAULT_BACKEND_DB_STR
    )


@pytest.mark.parametrize(
    "mode,env_key",
    [
        (BackendDbMode.LEGACY_P1, "SALES_OUTREACH_DB"),
        (BackendDbMode.LEGACY_P2, "SALES_OUTREACH_DB"),
        (BackendDbMode.LEGACY_P3, "SALESAI_DB_PATH"),
        (BackendDbMode.LEGACY_P4, "SALES_OUTREACH_DB"),
        (BackendDbMode.CENTRALIZED_LEGACY, "SALES_OUTREACH_DB"),
        (BackendDbMode.CENTRALIZED_STRICT, "SALES_OUTREACH_DB"),
    ],
)
def test_whitespace_only_selected_env_rejected_in_all_modes(
    mode: BackendDbMode, env_key: str
) -> None:
    """Whitespace-only selected env value fails closed across all modes without production fallback."""
    with pytest.raises(DatabaseResolutionError):
        resolve_backend_db_path(mode=mode, env={env_key: "   "})


def test_explicit_path_relative_memory_and_strict_mode_guarantees(tmp_path: Path) -> None:
    """Explicit path overrides ambient conflicts; relative/:memory: follow mode; strict blocks prod."""
    explicit = tmp_path / "explicit.db"
    env_conflict = {
        "SALES_OUTREACH_DB": str(tmp_path / "a.db"),
        "SALESAI_DB_PATH": str(tmp_path / "b.db"),
    }

    for mode in (
        BackendDbMode.LEGACY_P1,
        BackendDbMode.LEGACY_P2,
        BackendDbMode.LEGACY_P3,
        BackendDbMode.LEGACY_P4,
    ):
        res = resolve_backend_db_path(str(explicit), mode=mode, env=env_conflict)
        assert isinstance(res, str)
        assert res == str(explicit)
        assert resolve_backend_db_path("rel/local.db", mode=mode) == "rel/local.db"
        assert resolve_backend_db_path(":memory:", mode=mode, allow_memory=True) == ":memory:"

    # CENTRALIZED_LEGACY adopts P-1 and resolves via db_resolver
    assert (
        resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_LEGACY, env=env_conflict)
        == str((tmp_path / "a.db").resolve())
    )

    # CENTRALIZED_STRICT rejects conflicts, relative paths, and operational production DB
    with pytest.raises(DatabaseConfigurationConflictError):
        resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_STRICT, env=env_conflict)
    with pytest.raises(DatabaseResolutionError):
        resolve_backend_db_path("rel/local.db", mode=BackendDbMode.CENTRALIZED_STRICT)
    with pytest.raises(ProtectedDatabasePathError):
        resolve_backend_db_path(
            _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db",
            mode=BackendDbMode.CENTRALIZED_STRICT,
        )


def test_analytics_and_inbox_factories_propagate_resolved_paths_without_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify analytics (P-1) and inbox (P-2) factories propagate resolved paths cleanly with mocked stores."""
    from backend import sales_analytics_api, sales_inbox_api

    p_out = str(tmp_path / "outreach_api.db")
    p_cand = str(tmp_path / "cand_only_api.db")

    # 1. Analytics uses LEGACY_P1 (honors SALESAI_CANDIDATES_DB when higher vars unset)
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", p_cand)
    assert sales_analytics_api.get_analytics_db_path() == p_cand

    with (
        patch.object(sales_analytics_api, "AnalyticsStore") as mock_astore,
        patch.object(sales_analytics_api, "AnalyticsProjector") as mock_proj,
        patch.object(sales_analytics_api, "AnalyticsMetricsCalculator"),
        patch.object(sales_analytics_api, "RecommendationEngine"),
        patch.object(sales_analytics_api, "ModelEvaluator"),
        patch.object(sales_analytics_api, "AnalyticsExporter"),
    ):
        sales_analytics_api.get_analytics_services()
        mock_astore.assert_called_once_with(db_path=p_cand)
        mock_proj.assert_called_once()
        assert mock_proj.call_args.kwargs["db_path"] == p_cand

    # 2. Inbox uses LEGACY_P2 (ignores SALESAI_CANDIDATES_DB; honors SALES_OUTREACH_DB)
    monkeypatch.setenv("SALES_OUTREACH_DB", p_out)
    monkeypatch.setenv("MAILBOX_PROVIDER", "mock")
    with (
        patch.object(sales_inbox_api, "CandidateStore") as mock_cstore,
        patch.object(sales_inbox_api, "InboxStore") as mock_istore,
        patch.object(sales_inbox_api, "ReviewStore") as mock_rstore,
        patch.object(sales_inbox_api, "SuppressionStore"),
        patch.object(sales_inbox_api, "MockMailboxProvider"),
        patch.object(sales_inbox_api, "ReplyClassifier"),
        patch.object(sales_inbox_api, "ReplySuggestionEngine"),
        patch.object(sales_inbox_api, "InboxSyncManager"),
        patch.object(sales_inbox_api, "ReplySendOrchestrator"),
    ):
        sales_inbox_api.get_inbox_services()
        mock_cstore.assert_called_once_with(db_path=p_out)
        mock_istore.assert_called_once_with(db_path=p_out)
        mock_rstore.assert_called_once_with(db_path=p_out)

    # 3. Invalid whitespace config aborts before any store constructor runs
    monkeypatch.setenv("SALES_OUTREACH_DB", "   ")
    with (
        patch.object(sales_analytics_api, "AnalyticsStore") as mock_astore,
        patch.object(sales_inbox_api, "CandidateStore") as mock_cstore,
    ):
        with pytest.raises(DatabaseResolutionError):
            sales_analytics_api.get_analytics_services()
        with pytest.raises(DatabaseResolutionError):
            sales_inbox_api.get_inbox_services()
        mock_astore.assert_not_called()
        mock_cstore.assert_not_called()
