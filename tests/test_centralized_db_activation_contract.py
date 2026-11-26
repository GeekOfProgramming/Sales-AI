"""
tests/test_centralized_db_activation_contract.py
DB-FINAL-B1 focused contract tests for CENTRALIZED_OPERATIONAL policy,
protected-root safety boundaries, adapter parity, and legacy preservation.

Runs completely offline with synthetic directory trees only.
Never accesses operational databases, credential stores, or live providers.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.db_path import BackendDbMode, resolve_backend_db_path
from sales_engine.db_resolver import (
    _DEFAULT_REPO_ROOT,
    DatabaseConfigurationConflictError,
    DatabaseResolutionError,
    ProtectedDatabasePathError,
    ResolutionPolicy,
    resolve_database_path,
)
from sales_engine.discovery_v2.db_path import DiscoveryDbMode, resolve_discovery_db_path


def _resolve_both_centralized(
    db_path=None,
    *,
    env=None,
    repo_root=None,
    allow_memory=None,
    protected_roots=None,
):
    backend_res = resolve_backend_db_path(
        db_path,
        mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
        env=env,
        allow_memory=allow_memory,
        repo_root=repo_root,
        protected_roots=protected_roots,
    )
    discovery_res = resolve_discovery_db_path(
        db_path,
        mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL,
        env=env,
        allow_memory=allow_memory,
        repo_root=repo_root,
        protected_roots=protected_roots,
    )
    assert backend_res == discovery_res
    return backend_res


def test_centralized_operational_explicit_absolute_and_repo_root_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adapters resolve explicit absolute and repo-root relative paths identically regardless of cwd."""
    fake_repo = tmp_path / "sales_repo"
    other_cwd = tmp_path / "unrelated_cwd"
    other_cwd.mkdir(parents=True)
    monkeypatch.chdir(other_cwd)

    explicit_abs = (tmp_path / "isolated" / "explicit.db").resolve()
    assert (
        _resolve_both_centralized(explicit_abs, env={}, repo_root=fake_repo)
        == str(explicit_abs)
    )
    assert (
        _resolve_both_centralized("data/custom_ops.db", env={}, repo_root=fake_repo)
        == str((fake_repo / "data" / "custom_ops.db").resolve())
    )
    assert (
        _resolve_both_centralized(
            env={"SALES_OUTREACH_DB": "nested/env_ops.db"},
            repo_root=fake_repo,
        )
        == str((fake_repo / "nested" / "env_ops.db").resolve())
    )


def test_centralized_operational_env_precedence_and_documented_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consistent single env var or documented <repo_root>/data/sales_outreach.db fallback across adapters."""
    fake_repo = tmp_path / "op_repo"
    monkeypatch.chdir(tmp_path)

    p_out = (tmp_path / "env_out.db").resolve()
    p_db = (tmp_path / "env_db.db").resolve()
    p_cand = (tmp_path / "env_cand.db").resolve()

    assert (
        _resolve_both_centralized(env={"SALES_OUTREACH_DB": str(p_out)}, repo_root=fake_repo)
        == str(p_out)
    )
    assert (
        _resolve_both_centralized(env={"SALESAI_DB_PATH": str(p_db)}, repo_root=fake_repo)
        == str(p_db)
    )
    assert (
        _resolve_both_centralized(env={"SALESAI_CANDIDATES_DB": str(p_cand)}, repo_root=fake_repo)
        == str(p_cand)
    )
    assert (
        _resolve_both_centralized(env={}, repo_root=fake_repo)
        == str((fake_repo / "data" / "sales_outreach.db").resolve())
    )


def test_centralized_operational_rejects_env_and_explicit_conflicts(tmp_path: Path) -> None:
    """Conflicting env vars and explicit vs env disagreements fail closed across both adapters."""
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    db_c = tmp_path / "c.db"

    conflict_envs = [
        {"SALES_OUTREACH_DB": str(db_a), "SALESAI_DB_PATH": str(db_b)},
        {"SALESAI_DB_PATH": str(db_b), "SALESAI_CANDIDATES_DB": str(db_c)},
        {"SALES_OUTREACH_DB": str(db_a), "SALESAI_CANDIDATES_DB": str(db_c)},
    ]
    for env in conflict_envs:
        for mode_fn in (
            lambda e: resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env=e),
            lambda e: resolve_discovery_db_path(mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, env=e),
        ):
            with pytest.raises(DatabaseConfigurationConflictError):
                mode_fn(env)

    # Explicit path disagreeing with configured env fails closed
    for env in (
        {"SALES_OUTREACH_DB": str(db_b)},
        {"SALESAI_DB_PATH": str(db_b)},
        {"SALESAI_CANDIDATES_DB": str(db_b)},
    ):
        with pytest.raises(DatabaseConfigurationConflictError):
            resolve_backend_db_path(
                db_a,
                mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
                env=env,
            )
        with pytest.raises(DatabaseConfigurationConflictError):
            resolve_discovery_db_path(
                db_a,
                mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL,
                env=env,
            )


def test_centralized_operational_equivalent_normalized_and_windows_case_equivalence(
    tmp_path: Path,
) -> None:
    """Mixed slashes, '..' traversal, repo-relative equivalence, and Windows casing do not conflict."""
    fake_repo = tmp_path / "repo"
    canonical = (fake_repo / "data" / "sales_outreach.db").resolve()
    traversal = str(fake_repo / "data" / "backups" / ".." / "sales_outreach.db")
    forward_slashes = str(canonical).replace("\\", "/")
    if os.name == "nt":
        forward_slashes = forward_slashes.upper() if forward_slashes.islower() else forward_slashes.lower()

    env = {
        "SALES_OUTREACH_DB": "data/sales_outreach.db",
        "SALESAI_DB_PATH": traversal,
        "SALESAI_CANDIDATES_DB": forward_slashes,
    }
    assert (
        _resolve_both_centralized(canonical, env=env, repo_root=fake_repo)
        == str(canonical)
    )

    if os.name == "nt":
        # Case-variant env key alone works; duplicate case-variant keys fail closed
        assert (
            _resolve_both_centralized(env={"sales_outreach_db": str(canonical)}, repo_root=fake_repo)
            == str(canonical)
        )
        with pytest.raises(DatabaseConfigurationConflictError):
            resolve_backend_db_path(
                mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
                env={"SALES_OUTREACH_DB": str(canonical), "sales_outreach_db": str(canonical)},
            )


def test_centralized_operational_empty_and_whitespace_handling(tmp_path: Path) -> None:
    """Empty '' env values are unset; whitespace-only env/explicit values fail without fallback."""
    fake_repo = tmp_path / "repo"
    valid = (tmp_path / "valid.db").resolve()

    # Empty string env is unset
    assert (
        _resolve_both_centralized(
            env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": "", "SALESAI_CANDIDATES_DB": str(valid)},
            repo_root=fake_repo,
        )
        == str(valid)
    )
    assert (
        _resolve_both_centralized(
            valid,
            env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": ""},
            repo_root=fake_repo,
        )
        == str(valid)
    )

    # Whitespace-only in any configured env var or explicit path fails closed (even with valid explicit/other env)
    for bad_env in (
        {"SALES_OUTREACH_DB": "   "},
        {"SALES_OUTREACH_DB": str(valid), "SALESAI_DB_PATH": "\t "},
        {"SALESAI_CANDIDATES_DB": " \n"},
    ):
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env=bad_env, repo_root=fake_repo)
        with pytest.raises(DatabaseResolutionError):
            resolve_discovery_db_path(mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, env=bad_env, repo_root=fake_repo)
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(valid, mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env=bad_env, repo_root=fake_repo)

    for bad_explicit in ("", "   ", "\t"):
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(bad_explicit, mode=BackendDbMode.CENTRALIZED_OPERATIONAL, repo_root=fake_repo)
        with pytest.raises(DatabaseResolutionError):
            resolve_discovery_db_path(bad_explicit, mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, repo_root=fake_repo)


def test_centralized_operational_rejects_unsafe_paths_and_default_memory(tmp_path: Path) -> None:
    """SQLite URIs, UNC, Windows device paths, ADS, NUL, drive-relative, and default :memory: fail closed."""
    unsafe_inputs = [
        "file:test.db?mode=ro",
        f"{tmp_path / 'query.db'}?cache=shared",
        r"\\server\share\sales.db",
        "//server/share/sales.db",
        r"\\.\C:\temp\sales.db",
        r"\\?\C:\temp\sales.db",
        f"{tmp_path / 'nul_byte.db'}\x00",
    ]
    if os.name == "nt":
        unsafe_inputs.extend([
            r"C:\temp\sales.db:stream",
            "C:relative_drive.db",
            r"\Sales-AI\SalesAI\data\sales_outreach.db",
            "/Sales-AI/SalesAI/data/sales_outreach.db",
            str(tmp_path / "sales_outreach.db."),
            str(tmp_path / "dir ." / "sales_outreach.db"),
            "NUL",
            "nul.db",
            str(tmp_path / "bad|name.db"),
        ])

    for bad in unsafe_inputs:
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(bad, mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env={})
        with pytest.raises(DatabaseResolutionError):
            resolve_discovery_db_path(bad, mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, env={})
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env={"SALES_OUTREACH_DB": bad})
        with pytest.raises(DatabaseResolutionError):
            resolve_discovery_db_path(mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, env={"SALES_OUTREACH_DB": bad})

    # In-memory rejected by default; explicit allow_memory=True works consistently
    for mem in (":memory:", ":MEMORY:"):
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(mem, mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env={})
        with pytest.raises(DatabaseResolutionError):
            resolve_discovery_db_path(mem, mode=DiscoveryDbMode.CENTRALIZED_OPERATIONAL, env={})
        with pytest.raises(DatabaseResolutionError):
            resolve_backend_db_path(mode=BackendDbMode.CENTRALIZED_OPERATIONAL, env={"SALES_OUTREACH_DB": mem})
        assert _resolve_both_centralized(mem, env={}, allow_memory=True) == ":memory:"
        assert _resolve_both_centralized(env={"SALES_OUTREACH_DB": mem}, allow_memory=True) == ":memory:"


def test_sibling_operational_root_backups_sidecars_and_traversal_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic sibling operational data/ root blocks direct, traversal, backup, sidecar, and fallback access."""
    fake_dev_repo = tmp_path / "Sales-AI-Dev" / "SalesAI"
    fake_op_data = tmp_path / "Sales-AI" / "SalesAI" / "data"
    fake_op_root = fake_op_data.parent
    monkeypatch.chdir(tmp_path)

    blocked_targets = [
        fake_op_data / "sales_outreach.db",
        fake_op_data / "sales_outreach.db-wal",
        fake_op_data / "sales_outreach.db-shm",
        fake_op_data / "sales_outreach.db-journal",
        fake_op_data / "sales_outreach.db.backup",
        fake_op_data / "sales_outreach.db.bak",
        fake_op_data / "backups" / "auto_backup.db",
        fake_op_data / "backups" / ".." / "sales_outreach.db",
        fake_dev_repo / ".." / ".." / "Sales-AI" / "SalesAI" / "data" / "sales_outreach.db",
    ]

    for target in blocked_targets:
        for mode in (BackendDbMode.CENTRALIZED_STRICT, BackendDbMode.CENTRALIZED_OPERATIONAL):
            with pytest.raises(ProtectedDatabasePathError):
                resolve_backend_db_path(
                    target,
                    mode=mode,
                    repo_root=fake_dev_repo,
                    protected_roots=[fake_op_data],
                )
        for dmode in (DiscoveryDbMode.CENTRALIZED_STRICT, DiscoveryDbMode.CENTRALIZED_OPERATIONAL):
            with pytest.raises(ProtectedDatabasePathError):
                resolve_discovery_db_path(
                    target,
                    mode=dmode,
                    repo_root=fake_dev_repo,
                    protected_roots=[fake_op_data],
                )

    # Unconfigured fallback into protected operational root is also blocked
    with pytest.raises(ProtectedDatabasePathError):
        resolve_backend_db_path(
            mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
            env={},
            repo_root=fake_op_root,
            protected_roots=[fake_op_data],
        )

    # Protected file-level entry also blocks its sidecars
    specific_db = tmp_path / "protected_single" / "vault.db"
    with pytest.raises(ProtectedDatabasePathError):
        resolve_database_path(
            str(specific_db) + "-wal",
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            protected_roots=[specific_db],
        )

    # STRICT_EXPLICIT mandatory protections cannot be overridden by custom protected_roots
    with pytest.raises(ProtectedDatabasePathError):
        resolve_backend_db_path(
            _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db",
            mode=BackendDbMode.CENTRALIZED_STRICT,
            repo_root=fake_dev_repo,
            protected_roots=[tmp_path / "other_protected"],
        )

    # Legacy adapter modes also enforce protected_roots when explicitly supplied
    with pytest.raises(ProtectedDatabasePathError):
        resolve_backend_db_path(
            fake_op_data / "sales_outreach.db",
            mode=BackendDbMode.LEGACY_P1,
            protected_roots=[fake_op_data],
        )
    with pytest.raises(ProtectedDatabasePathError):
        resolve_discovery_db_path(
            fake_op_data / "sales_outreach.db",
            mode=DiscoveryDbMode.LEGACY_P3,
            protected_roots=[fake_op_data],
        )


def test_symlink_and_windows_junction_alias_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symlink and real Windows junction aliases targeting protected operational roots fail closed."""
    fake_dev_repo = tmp_path / "dev_repo"
    fake_op_data = tmp_path / "op_repo" / "data"
    fake_op_data.mkdir(parents=True)
    fake_dev_repo.mkdir(parents=True)

    # 1. Deterministic Path.resolve simulation for symlink/alias escape
    alias_path = tmp_path / "dev_repo" / "alias.db"
    op_resolved = (fake_op_data / "sales_outreach.db").resolve()
    orig_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if os.path.normcase(str(self)) == os.path.normcase(str(alias_path)):
            return op_resolved
        return orig_resolve(self, strict=strict)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "resolve", fake_resolve)
        with pytest.raises(ProtectedDatabasePathError):
            resolve_backend_db_path(
                alias_path,
                mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
                repo_root=fake_dev_repo,
                protected_roots=[fake_op_data],
            )
        with pytest.raises(ProtectedDatabasePathError):
            resolve_discovery_db_path(
                alias_path,
                mode=DiscoveryDbMode.CENTRALIZED_STRICT,
                repo_root=fake_dev_repo,
                protected_roots=[fake_op_data],
            )

    # 2. Real Windows directory junction test in synthetic tmp_path
    if os.name == "nt":
        junction_dir = fake_dev_repo / "data_junction"
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction_dir), str(fake_op_data)],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            pytest.skip(f"Windows junction creation unavailable: {proc.stderr or proc.stdout}")
        try:
            with pytest.raises(ProtectedDatabasePathError):
                resolve_backend_db_path(
                    junction_dir / "sales_outreach.db",
                    mode=BackendDbMode.CENTRALIZED_OPERATIONAL,
                    repo_root=fake_dev_repo,
                    protected_roots=[fake_op_data],
                )
            with pytest.raises(ProtectedDatabasePathError):
                resolve_discovery_db_path(
                    junction_dir / "sales_outreach.db",
                    mode=DiscoveryDbMode.CENTRALIZED_STRICT,
                    repo_root=fake_dev_repo,
                    protected_roots=[fake_op_data],
                )
        finally:
            if junction_dir.exists():
                junction_dir.rmdir()


def test_resolution_performs_no_sqlite_or_filesystem_writes_and_preserves_legacy_defaults(
    tmp_path: Path,
) -> None:
    """Resolver creates no dirs/files/connections and no application consumer uses CENTRALIZED_OPERATIONAL yet."""
    nonexistent_dir = tmp_path / "never_created"
    target = nonexistent_dir / "test.db"
    env = {"SALES_OUTREACH_DB": str(target)}
    env_copy = dict(env)

    with patch.object(sqlite3, "connect", side_effect=AssertionError("sqlite3.connect must not be called")):
        res = _resolve_both_centralized(env=env, repo_root=tmp_path / "repo")

    assert res == str(target.resolve())
    assert not nonexistent_dir.exists()
    assert not target.exists()
    assert env == env_copy

    # Verify default modes of existing adapters and resolve_database_path remain unchanged
    assert inspect.signature(resolve_database_path).parameters["policy"].default is ResolutionPolicy.STRICT_EXPLICIT
    assert inspect.signature(resolve_backend_db_path).parameters["mode"].default is BackendDbMode.LEGACY_P1
    assert inspect.signature(resolve_discovery_db_path).parameters["mode"].default is DiscoveryDbMode.LEGACY_P3

    # Verify no production/application consumer file references CENTRALIZED_OPERATIONAL yet
    consumer_files = [
        _DEFAULT_REPO_ROOT / "backend" / "main.py",
        _DEFAULT_REPO_ROOT / "backend" / "operator_auth.py",
        _DEFAULT_REPO_ROOT / "backend" / "sales_ui" / "router.py",
        _DEFAULT_REPO_ROOT / "backend" / "sales_inbox_api.py",
        _DEFAULT_REPO_ROOT / "backend" / "sales_analytics_api.py",
        _DEFAULT_REPO_ROOT / "sales_engine" / "discovery_v2" / "run.py",
        _DEFAULT_REPO_ROOT / "sales_engine" / "discovery_v2" / "scope.py",
        _DEFAULT_REPO_ROOT / "sales_engine" / "discovery_v2" / "source_registry.py",
        _DEFAULT_REPO_ROOT / "sales_engine" / "discovery_v2" / "bridges.py",
        _DEFAULT_REPO_ROOT / "sales_engine" / "candidates" / "backup_engine.py",
        _DEFAULT_REPO_ROOT / "scripts" / "run_mailbox_pilot.py",
        _DEFAULT_REPO_ROOT / "scripts" / "run_outbound_pilot.py",
    ]
    for path in consumer_files:
        text = path.read_text(encoding="utf-8")
        assert "CENTRALIZED_OPERATIONAL" not in text, f"Unexpected consumer switch in {path}"
