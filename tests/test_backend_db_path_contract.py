"""
tests/test_backend_db_path_contract.py
Isolated contract and mocked factory tests for Backend database path resolution.
Zero operational DB access, zero FastAPI startup, zero external providers.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import threading
from typing import Any, Optional, Tuple
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


def test_allow_memory_enforcement_and_explicit_empty_rejection_across_all_modes(
    tmp_path: Path,
) -> None:
    """Reject allow_memory=False (explicit or selected env) and explicit ''/'   ' across all modes."""
    all_modes = (
        BackendDbMode.LEGACY_P1,
        BackendDbMode.LEGACY_P2,
        BackendDbMode.LEGACY_P3,
        BackendDbMode.LEGACY_P4,
        BackendDbMode.CENTRALIZED_LEGACY,
        BackendDbMode.CENTRALIZED_STRICT,
    )
    for mode in all_modes:
        for explicit_bad in ("", "   "):
            with pytest.raises(DatabaseResolutionError):
                resolve_backend_db_path(
                    explicit_bad,
                    mode=mode,
                    env={"SALES_OUTREACH_DB": str(tmp_path / "env.db")},
                )
        for mem_val in (":memory:", ":MEMORY:"):
            assert resolve_backend_db_path(mem_val, mode=mode, allow_memory=True) == ":memory:"
            with pytest.raises(DatabaseResolutionError):
                resolve_backend_db_path(mem_val, mode=mode, allow_memory=False)
            env_key = "SALESAI_DB_PATH" if mode is BackendDbMode.LEGACY_P3 else "SALES_OUTREACH_DB"
            assert resolve_backend_db_path(mode=mode, env={env_key: mem_val}, allow_memory=True) == ":memory:"
            with pytest.raises(DatabaseResolutionError):
                resolve_backend_db_path(mode=mode, env={env_key: mem_val}, allow_memory=False)

    # Legacy modes ignore lower-priority :memory: when higher-priority valid path is selected
    valid_out = str(tmp_path / "valid_primary.db")
    assert (
        resolve_backend_db_path(
            mode=BackendDbMode.LEGACY_P1,
            env={"SALES_OUTREACH_DB": valid_out, "SALESAI_DB_PATH": ":memory:"},
            allow_memory=False,
        )
        == valid_out
    )
    assert resolve_backend_db_path(None, mode=BackendDbMode.LEGACY_P2, env={"SALES_OUTREACH_DB": ""}) == DEFAULT_BACKEND_DB_STR


def _load_real_discovery_factory_namespace(
    *,
    cand_store_factory: Any = None,
    registry_factory: Any = None,
    classifier_factory: Any = None,
    engine_factory: Any = None,
) -> dict[str, Any]:
    """
    Compile the real Discovery cache state & `get_discovery_services()` directly from
    `backend/main.py` without importing FastAPI startup, stdout wrappers, or routers.
    """
    main_path = _DEFAULT_REPO_ROOT / "backend" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    target_vars = {
        "_discovery_store_inst",
        "_discovery_registry_inst",
        "_discovery_classifier_inst",
        "_discovery_engine_inst",
        "_discovery_cache_key",
        "_discovery_cache_lock",
    }
    target_funcs = {
        "_prepare_discovery_cache_target",
        "_normalize_discovery_cache_target",
        "get_discovery_services",
    }
    selected_nodes = []
    found_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in target_vars:
            selected_nodes.append(node)
            found_names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & target_vars:
                selected_nodes.append(node)
                found_names.update(names & target_vars)
        elif isinstance(node, ast.FunctionDef) and node.name in target_funcs:
            selected_nodes.append(node)
            found_names.add(node.name)

    assert found_names == target_vars | target_funcs

    namespace: dict[str, Any] = {
        "os": os,
        "Path": Path,
        "threading": threading,
        "Optional": Optional,
        "Tuple": Tuple,
        "_CandidateStore": cand_store_factory or MagicMock(),
        "_SourceRegistry": registry_factory or MagicMock(),
        "_CompanyClassifier": classifier_factory or MagicMock(),
        "_DiscoveryEngineV2": engine_factory or MagicMock(),
    }
    module_ast = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module_ast)
    exec(compile(module_ast, filename=str(main_path), mode="exec"), namespace)
    return namespace


def test_discovery_singleton_explicit_injection_and_independent_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_discovery_services()` injects explicit P-3 store DB & independent P-3 registry DB."""
    from sales_engine.discovery_v2.db_path import DEFAULT_DISCOVERY_DB_STR

    cand_db = str(tmp_path / "candidates_only.db")
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)

    mock_store = MagicMock()
    mock_store.db_path = cand_db
    mock_store_cls = MagicMock(return_value=mock_store)
    mock_reg = MagicMock()
    mock_reg_cls = MagicMock(return_value=mock_reg)
    mock_clf = MagicMock()
    mock_clf_cls = MagicMock(return_value=mock_clf)
    mock_eng = MagicMock()
    mock_eng_cls = MagicMock(return_value=mock_eng)

    ns = _load_real_discovery_factory_namespace(
        cand_store_factory=mock_store_cls,
        registry_factory=mock_reg_cls,
        classifier_factory=mock_clf_cls,
        engine_factory=mock_eng_cls,
    )

    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge") as mock_bridge:
        res1 = ns["get_discovery_services"]()
        res2 = ns["get_discovery_services"]()

    assert res1 == (mock_store, mock_reg, mock_clf, mock_eng)
    assert res2 == res1
    mock_store_cls.assert_called_once_with(db_path=str(Path(cand_db).resolve()))
    mock_reg_cls.assert_called_once_with(db_path=str(Path(DEFAULT_DISCOVERY_DB_STR).resolve()))
    mock_bridge.assert_called_once_with(db_path=cand_db)
    mock_clf_cls.assert_called_once_with(suppression_store=mock_bridge.return_value)
    mock_eng_cls.assert_called_once_with(
        candidate_store=mock_store, classifier=mock_clf, source_registry=mock_reg
    )


def test_discovery_singleton_equivalent_normalization_and_conflict_preserves_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equivalent spellings reuse cache (including ':memory:' case); changed targets fail closed."""
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)

    # 1. Equivalent filesystem spellings (relative vs resolved alt-case/slash) reuse same bundle
    rel_name = "rel_cache_test.db"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SALESAI_DB_PATH", rel_name)

    fake_store = MagicMock()
    fake_store.db_path = rel_name
    store_cls = MagicMock(return_value=fake_store)
    reg_cls = MagicMock(return_value=MagicMock())
    ns = _load_real_discovery_factory_namespace(
        cand_store_factory=store_cls,
        registry_factory=reg_cls,
    )

    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        first = ns["get_discovery_services"]()
        alt_abs = str((tmp_path / rel_name).resolve()).replace("\\", "/")
        if os.name == "nt":
            alt_abs = alt_abs.lower()
        monkeypatch.setenv("SALESAI_DB_PATH", alt_abs)
        second = ns["get_discovery_services"]()
        assert second == first
        assert store_cls.call_count == 1
        assert reg_cls.call_count == 1

        # Changing registry-independent SALESAI_CANDIDATES_DB raises conflict & keeps existing cache
        monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(tmp_path / "other_cand.db"))
        # SALESAI_DB_PATH still dominates LEGACY_P3; switch primary by setting another SALESAI_DB_PATH
        monkeypatch.setenv("SALESAI_DB_PATH", str(tmp_path / "different.db"))
        with pytest.raises(DatabaseConfigurationConflictError):
            ns["get_discovery_services"]()

        # Existing cached bundle stays intact and is returned once matching config returns
        monkeypatch.setenv("SALESAI_DB_PATH", rel_name)
        assert ns["get_discovery_services"]() == first

    # 2. Separate cache tracks primary & registry independently (changing SALESAI_CANDIDATES_DB only)
    monkeypatch.delenv("SALESAI_DB_PATH", raising=False)
    cand_a = str(tmp_path / "cand_a.db")
    cand_b = str(tmp_path / "cand_b.db")
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_a)
    fake_store2 = MagicMock()
    fake_store2.db_path = cand_a
    ns2 = _load_real_discovery_factory_namespace(cand_store_factory=MagicMock(return_value=fake_store2))
    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        orig2 = ns2["get_discovery_services"]()
        monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_b)
        with pytest.raises(DatabaseConfigurationConflictError):
            ns2["get_discovery_services"]()
        monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_a)
        assert ns2["get_discovery_services"]() == orig2

    # 3. :memory: and :MEMORY: normalize identically; switching to disk raises conflict
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)
    monkeypatch.setenv("SALESAI_DB_PATH", ":memory:")
    fake_mem = MagicMock()
    fake_mem.db_path = ":memory:"
    ns_mem = _load_real_discovery_factory_namespace(cand_store_factory=MagicMock(return_value=fake_mem))
    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        mem_bundle = ns_mem["get_discovery_services"]()
        monkeypatch.setenv("SALESAI_DB_PATH", ":MEMORY:")
        assert ns_mem["get_discovery_services"]() == mem_bundle
        monkeypatch.setenv("SALESAI_DB_PATH", str(tmp_path / "disk.db"))
        with pytest.raises(DatabaseConfigurationConflictError):
            ns_mem["get_discovery_services"]()


def test_discovery_singleton_concurrent_initialization_serialized_with_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent callers racing on first initialization synchronize cleanly and build once."""
    db_file = str(tmp_path / "concurrent.db")
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)

    entered_init = threading.Event()
    allow_finish = threading.Event()
    call_counts = {"store": 0, "registry": 0, "classifier": 0, "engine": 0}
    count_lock = threading.Lock()

    def make_store(*, db_path: str):
        with count_lock:
            call_counts["store"] += 1
        entered_init.set()
        assert allow_finish.wait(timeout=5.0)
        obj = MagicMock()
        obj.db_path = db_path
        return obj

    def make_counter(name: str):
        def _factory(*args, **kwargs):
            with count_lock:
                call_counts[name] += 1
            return MagicMock()
        return _factory

    ns = _load_real_discovery_factory_namespace(
        cand_store_factory=make_store,
        registry_factory=make_counter("registry"),
        classifier_factory=make_counter("classifier"),
        engine_factory=make_counter("engine"),
    )

    barrier = threading.Barrier(4)
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            results.append(ns["get_discovery_services"]())
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        assert entered_init.wait(timeout=5.0)
        # While holder is paused mid-init, no second constructor may start & cache key stays unset
        assert ns["_discovery_cache_key"] is None
        with count_lock:
            assert call_counts["store"] == 1
        allow_finish.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

    assert not errors
    assert len(results) == 4
    assert all(r == results[0] for r in results)
    assert call_counts == {"store": 1, "registry": 1, "classifier": 1, "engine": 1}


def test_discovery_singleton_failure_atomicity_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-validation and mid-init failures leave cache completely empty, release lock, and recover."""
    db_file = str(tmp_path / "atomic.db")
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)

    fake_store = MagicMock()
    fake_store.db_path = db_file
    store_cls = MagicMock(return_value=fake_store)
    reg_cls = MagicMock(side_effect=[RuntimeError("registry init boom"), MagicMock()])
    ns = _load_real_discovery_factory_namespace(
        cand_store_factory=store_cls,
        registry_factory=reg_cls,
    )

    # 1. Invalid registry target fails before CandidateStore instantiation
    with patch("sales_engine.discovery_v2.db_path.resolve_discovery_db_path", side_effect=DatabaseResolutionError("bad registry")):
        with pytest.raises(DatabaseResolutionError):
            ns["get_discovery_services"]()
    store_cls.assert_not_called()
    assert ns["_discovery_cache_lock"].acquire(blocking=False)
    ns["_discovery_cache_lock"].release()

    # 2. Failure after CandidateStore succeeds leaves ALL globals None and releases lock
    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        with pytest.raises(RuntimeError, match="registry init boom"):
            ns["get_discovery_services"]()

    assert ns["_discovery_store_inst"] is None
    assert ns["_discovery_registry_inst"] is None
    assert ns["_discovery_classifier_inst"] is None
    assert ns["_discovery_engine_inst"] is None
    assert ns["_discovery_cache_key"] is None
    assert ns["_discovery_cache_lock"].acquire(blocking=False)
    ns["_discovery_cache_lock"].release()

    # 3. Subsequent valid call succeeds cleanly
    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge"):
        bundle = ns["get_discovery_services"]()
    assert bundle[0] is fake_store
    assert ns["_discovery_cache_key"] is not None


def test_discovery_singleton_relative_path_stability_after_chdir_and_uri_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached ordinary relative paths anchor to initial cwd across chdir; URI-like targets stay verbatim."""
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    expected_abs = str((dir_a / "rel_stable.db").resolve())

    monkeypatch.chdir(dir_a)
    monkeypatch.delenv("SALES_OUTREACH_DB", raising=False)
    monkeypatch.delenv("SALESAI_CANDIDATES_DB", raising=False)
    monkeypatch.setenv("SALESAI_DB_PATH", "rel_stable.db")

    def make_store(*, db_path: str):
        obj = MagicMock()
        obj.db_path = db_path
        return obj

    def make_reg(*, db_path: str):
        obj = MagicMock()
        obj.db_path = db_path
        return obj

    ns = _load_real_discovery_factory_namespace(
        cand_store_factory=make_store,
        registry_factory=make_reg,
    )

    with patch("sales_engine.discovery_v2.bridges.Phase9AuthorityBridge") as mock_bridge:
        store1, reg1, _, _ = ns["get_discovery_services"]()
        assert store1.db_path == expected_abs
        assert reg1.db_path == expected_abs
        mock_bridge.assert_called_once_with(db_path=expected_abs)

        # Changing cwd and supplying equivalent absolute path reuses cached stores still anchored to dir_a
        monkeypatch.chdir(dir_b)
        monkeypatch.setenv("SALESAI_DB_PATH", expected_abs)
        store2, reg2, _, _ = ns["get_discovery_services"]()
        assert store2 is store1
        assert reg2 is reg1
        assert store2.db_path == expected_abs

        # Relative path after chdir now targets dir_b and must fail closed as a conflict
        monkeypatch.setenv("SALESAI_DB_PATH", "rel_stable.db")
        with pytest.raises(DatabaseConfigurationConflictError):
            ns["get_discovery_services"]()

    uri_target = "file:shared_mem?mode=memory&cache=shared"
    assert ns["_prepare_discovery_cache_target"](uri_target) == (uri_target, uri_target)
    assert ns["_normalize_discovery_cache_target"](uri_target) == uri_target
