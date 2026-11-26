"""
tests/test_db_resolver.py
Isolated unit tests for sales_engine.db_resolver (no SQLite connections or app imports).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sales_engine.db_resolver import (
    _DEFAULT_REPO_ROOT,
    DatabaseConfigurationConflictError,
    DatabaseResolutionError,
    ProtectedDatabasePathError,
    ResolutionPolicy,
    resolve_database_path,
)


def test_explicit_db_path_overrides_env_values_including_conflicts(tmp_path: Path) -> None:
    """Explicit db_path takes highest priority even when ambient env vars conflict."""
    explicit_db = tmp_path / "explicit.db"
    env = {
        "SALES_OUTREACH_DB": str(tmp_path / "from_env_a.db"),
        "SALESAI_DB_PATH": str(tmp_path / "from_env_b.db"),
        "SALESAI_CANDIDATES_DB": str(tmp_path / "from_env_c.db"),
    }

    for policy in (ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.LEGACY_COMPATIBLE):
        resolved = resolve_database_path(explicit_db, policy=policy, env=env)
        assert resolved == explicit_db.resolve()


def test_sales_outreach_db_takes_precedence_over_salesai_db_path(tmp_path: Path) -> None:
    """SALES_OUTREACH_DB wins over SALESAI_DB_PATH and SALESAI_CANDIDATES_DB in legacy mode."""
    primary = tmp_path / "primary.db"
    secondary = tmp_path / "secondary.db"
    tertiary = tmp_path / "tertiary.db"
    env = {
        "SALES_OUTREACH_DB": str(primary),
        "SALESAI_DB_PATH": str(secondary),
        "SALESAI_CANDIDATES_DB": str(tertiary),
    }

    resolved = resolve_database_path(
        policy=ResolutionPolicy.LEGACY_COMPATIBLE,
        env=env,
    )
    assert resolved == primary.resolve()


def test_salesai_db_path_takes_precedence_over_salesai_candidates_db(tmp_path: Path) -> None:
    """SALESAI_DB_PATH wins over SALESAI_CANDIDATES_DB when SALES_OUTREACH_DB is unset."""
    secondary = tmp_path / "secondary.db"
    tertiary = tmp_path / "tertiary.db"
    env = {
        "SALESAI_DB_PATH": str(secondary),
        "SALESAI_CANDIDATES_DB": str(tertiary),
    }

    resolved = resolve_database_path(
        policy=ResolutionPolicy.LEGACY_COMPATIBLE,
        env=env,
    )
    assert resolved == secondary.resolve()


def test_legacy_fallback_resolves_to_repo_operational_db_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfigured LEGACY_COMPATIBLE resolves to <repo_root>/data/sales_outreach.db regardless of cwd."""
    fake_repo = tmp_path / "repo_root"
    other_cwd = tmp_path / "somewhere_else"
    other_cwd.mkdir(parents=True)
    monkeypatch.chdir(other_cwd)

    resolved = resolve_database_path(
        policy=ResolutionPolicy.LEGACY_COMPATIBLE,
        env={},
        repo_root=fake_repo,
    )
    assert resolved == (fake_repo / "data" / "sales_outreach.db").resolve()


def test_strict_mode_rejects_unconfigured_resolution(tmp_path: Path) -> None:
    """STRICT_EXPLICIT fails closed when neither db_path nor env vars are configured."""
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env={},
            repo_root=tmp_path / "repo",
        )


def test_strict_mode_rejects_conflicting_env_vars(tmp_path: Path) -> None:
    """STRICT_EXPLICIT raises DatabaseConfigurationConflictError when non-empty DB env vars diverge."""
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    env = {
        "SALES_OUTREACH_DB": str(db_a),
        "SALESAI_DB_PATH": str(db_b),
    }

    with pytest.raises(DatabaseConfigurationConflictError):
        resolve_database_path(policy=ResolutionPolicy.STRICT_EXPLICIT, env=env)


def test_strict_mode_rejects_operational_database_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STRICT_EXPLICIT rejects operational data/ paths, backups, '..' traversals, and symlink escapes."""
    fake_repo = tmp_path / "repo"
    op_targets = [
        fake_repo / "data" / "sales_outreach.db",
        fake_repo / "data" / "backups" / ".." / "sales_outreach.db",
        fake_repo / "data" / "sales_outreach.db.bak",
    ]

    for target in op_targets:
        with pytest.raises(ProtectedDatabasePathError):
            resolve_database_path(
                target,
                policy=ResolutionPolicy.STRICT_EXPLICIT,
                repo_root=fake_repo,
            )
        with pytest.raises(ProtectedDatabasePathError):
            resolve_database_path(
                policy=ResolutionPolicy.STRICT_EXPLICIT,
                env={"SALES_OUTREACH_DB": str(target)},
                repo_root=fake_repo,
            )

    # Deterministic symlink/junction escape simulation via mocked Path.resolve
    link_in_tmp = tmp_path / "disposable_link.db"
    prod_resolved = (fake_repo / "data" / "sales_outreach.db").resolve()
    orig_resolve = Path.resolve

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if os.path.normcase(str(self)) == os.path.normcase(str(link_in_tmp)):
            return prod_resolved
        return orig_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(ProtectedDatabasePathError):
        resolve_database_path(
            link_in_tmp,
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            repo_root=fake_repo,
        )


def test_relative_path_policy_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths fail in STRICT_EXPLICIT and resolve cwd-relatively in LEGACY_COMPATIBLE."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(DatabaseResolutionError):
        resolve_database_path("rel/test.db", policy=ResolutionPolicy.STRICT_EXPLICIT)

    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env={"SALES_OUTREACH_DB": "rel/env.db"},
        )

    assert (
        resolve_database_path("rel/test.db", policy=ResolutionPolicy.LEGACY_COMPATIBLE)
        == (tmp_path / "rel" / "test.db").resolve()
    )
    assert (
        resolve_database_path(
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env={"SALES_OUTREACH_DB": "rel/env.db"},
        )
        == (tmp_path / "rel" / "env.db").resolve()
    )


def test_equivalent_normalized_paths_do_not_conflict(tmp_path: Path) -> None:
    """Equivalent paths using mixed casing, slashes, or '..' pass STRICT_EXPLICIT without conflict."""
    canonical = (tmp_path / "sub" / "shared.db").resolve()
    sub_dir = tmp_path / "sub"
    alt_traversal = str(sub_dir / "nested" / ".." / "shared.db")
    alt_slashes = str(canonical).replace("\\", "/")
    if os.name == "nt":
        alt_slashes = alt_slashes.lower()

    env = {
        "SALES_OUTREACH_DB": str(canonical),
        "SALESAI_DB_PATH": alt_traversal,
        "SALESAI_CANDIDATES_DB": alt_slashes,
    }

    resolved = resolve_database_path(policy=ResolutionPolicy.STRICT_EXPLICIT, env=env)
    assert resolved == canonical


def test_empty_env_values_treated_as_unset(tmp_path: Path) -> None:
    """Empty string ('') env vars are ignored in favor of next precedence or fallback."""
    fallback_target = tmp_path / "fallback.db"
    env = {
        "SALES_OUTREACH_DB": "",
        "SALESAI_DB_PATH": "",
        "SALESAI_CANDIDATES_DB": str(fallback_target),
    }

    assert (
        resolve_database_path(policy=ResolutionPolicy.STRICT_EXPLICIT, env=env)
        == fallback_target.resolve()
    )

    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env={"SALES_OUTREACH_DB": "", "SALESAI_DB_PATH": ""},
        )


@pytest.mark.parametrize("policy", [ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.LEGACY_COMPATIBLE])
def test_whitespace_only_selected_env_rejected_without_fallback(
    tmp_path: Path, policy: ResolutionPolicy
) -> None:
    """Whitespace-only selected env value raises DatabaseResolutionError instead of falling back."""
    # 1. No lower-priority configuration
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=policy,
            env={"SALES_OUTREACH_DB": "   "},
            repo_root=tmp_path / "repo",
        )

    # 2. Valid lower-priority configuration must not be silently selected
    valid_lower = tmp_path / "lower.db"
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=policy,
            env={"SALES_OUTREACH_DB": "\t ", "SALESAI_DB_PATH": str(valid_lower)},
            repo_root=tmp_path / "repo",
        )


def test_lower_priority_whitespace_ignored_in_legacy_but_rejected_in_strict(
    tmp_path: Path,
) -> None:
    """Valid selected higher-priority var ignores lower-priority whitespace in legacy, fails in strict."""
    valid_primary = tmp_path / "primary.db"
    env = {
        "SALES_OUTREACH_DB": str(valid_primary),
        "SALESAI_DB_PATH": "   ",
    }

    assert (
        resolve_database_path(policy=ResolutionPolicy.LEGACY_COMPATIBLE, env=env)
        == valid_primary.resolve()
    )
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(policy=ResolutionPolicy.STRICT_EXPLICIT, env=env)


def test_explicit_db_path_overrides_whitespace_ambient_env(tmp_path: Path) -> None:
    """Explicit db_path retains precedence over whitespace-containing ambient env vars."""
    explicit_db = tmp_path / "explicit.db"
    env = {
        "SALES_OUTREACH_DB": "   ",
        "SALESAI_DB_PATH": "\t",
        "SALESAI_CANDIDATES_DB": "  ",
    }
    for policy in (ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.LEGACY_COMPATIBLE):
        assert resolve_database_path(explicit_db, policy=policy, env=env) == explicit_db.resolve()


def test_memory_handling_is_explicit_and_controlled() -> None:
    """':memory:' requires allow_memory=True and returns ':memory:' cleanly without Path resolution."""
    for policy in (ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.LEGACY_COMPATIBLE):
        with pytest.raises(DatabaseResolutionError):
            resolve_database_path(":memory:", policy=policy, allow_memory=False)

        assert (
            resolve_database_path(":memory:", policy=policy, allow_memory=True)
            == ":memory:"
        )
        assert (
            resolve_database_path(":MEMORY:", policy=policy, allow_memory=True)
            == ":memory:"
        )


def test_invalid_uri_unc_nul_and_padded_paths_rejected(tmp_path: Path) -> None:
    """SQLite URIs, UNC paths, NUL bytes, and whitespace-padded paths fail closed in both policies."""
    bad_inputs = [
        "",
        "   ",
        f" {tmp_path / 'padded.db'}",
        "file:test.db?mode=ro",
        f"{tmp_path / 'query.db'}?cache=shared",
        r"\\server\share\db.sqlite",
        f"{tmp_path / 'bad.db'}\x00",
    ]
    for policy in (ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.LEGACY_COMPATIBLE):
        for bad in bad_inputs:
            with pytest.raises(DatabaseResolutionError):
                resolve_database_path(bad, policy=policy)


def test_resolver_does_not_create_files_dirs_or_mutate_env(tmp_path: Path) -> None:
    """Resolving a nonexistent nested path creates neither parent directories nor SQLite files."""
    nonexistent_parent = tmp_path / "never_created_dir"
    target = nonexistent_parent / "never_created.db"
    env = {"SALES_OUTREACH_DB": str(target)}
    env_snapshot = dict(env)

    resolved = resolve_database_path(policy=ResolutionPolicy.STRICT_EXPLICIT, env=env)
    assert resolved == target.resolve()
    assert not nonexistent_parent.exists()
    assert not target.exists()
    assert env == env_snapshot


def test_custom_repo_root_cannot_bypass_actual_operational_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing a custom repo_root and chdir-ing away must not allow actual operational DB/backups."""
    monkeypatch.chdir(tmp_path)
    fake_repo = tmp_path / "fake_repo"

    actual_protected = [
        _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db",
        _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db.backup",
        _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db-wal",
        _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db-shm",
        _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db-journal",
        _DEFAULT_REPO_ROOT / "data" / "backups" / "auto.db",
    ]
    for target in actual_protected:
        with pytest.raises(ProtectedDatabasePathError):
            resolve_database_path(
                target,
                policy=ResolutionPolicy.STRICT_EXPLICIT,
                repo_root=fake_repo,
            )


def test_windows_case_insensitive_env_keys(tmp_path: Path) -> None:
    """Case-variant env keys resolve on Windows and reject duplicate case variants in strict mode."""
    target = tmp_path / "cased.db"
    other = tmp_path / "other.db"

    resolved = resolve_database_path(
        policy=ResolutionPolicy.STRICT_EXPLICIT,
        env={"sales_outreach_db": str(target)},
    )
    if os.name == "nt":
        assert resolved == target.resolve()
        with pytest.raises(DatabaseConfigurationConflictError):
            resolve_database_path(
                policy=ResolutionPolicy.STRICT_EXPLICIT,
                env={"SALES_OUTREACH_DB": str(target), "sales_outreach_db": str(other)},
            )
    else:
        # On POSIX, lowercase key is distinct and ignored
        assert resolved != target.resolve()  # pragma: no cover


@pytest.mark.parametrize(
    "env_factory",
    [
        lambda valid: {
            "SALES_OUTREACH_DB": str(valid),
            "SALESAI_DB_PATH": "file:ignored.db?mode=ro",
            "SALESAI_CANDIDATES_DB": r"\\server\share\ignored.db",
        },
        lambda valid: {
            "SALESAI_DB_PATH": str(valid),
            "SALESAI_CANDIDATES_DB": "file:ignored.db?mode=ro",
        },
        lambda valid: {
            "SALES_OUTREACH_DB": str(valid),
            "SALESAI_DB_PATH": ":memory:",
        },
    ],
)
def test_legacy_ignores_malformed_or_memory_lower_priority_env(
    tmp_path: Path, env_factory
) -> None:
    """LEGACY_COMPATIBLE short-circuits on highest-priority var without validating ignored vars."""
    valid_db = tmp_path / "selected.db"
    env = env_factory(valid_db)

    resolved = resolve_database_path(
        policy=ResolutionPolicy.LEGACY_COMPATIBLE,
        env=env,
        allow_memory=False,
    )
    assert resolved == valid_db.resolve()

    # STRICT_EXPLICIT must still reject malformed/conflicting lower-priority env vars
    with pytest.raises((DatabaseResolutionError, DatabaseConfigurationConflictError)):
        resolve_database_path(
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env=env,
            allow_memory=False,
        )


def test_legacy_malformed_selected_high_priority_raises_without_fallback(
    tmp_path: Path,
) -> None:
    """Malformed highest-priority env var raises instead of falling back to lower-priority or default."""
    valid_lower = tmp_path / "lower.db"
    env = {
        "SALES_OUTREACH_DB": "file:invalid.db?mode=ro",
        "SALESAI_DB_PATH": str(valid_lower),
    }
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env=env,
            repo_root=tmp_path / "repo",
        )


def test_relative_to_repo_root_anchoring_and_strict_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify relative_to_repo_root anchors relative paths to repo_root only in LEGACY_COMPATIBLE."""
    fake_repo = tmp_path / "repo"
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True)
    monkeypatch.chdir(other_cwd)

    # Explicit relative db_path and relative env path with relative_to_repo_root=False -> cwd-relative
    assert (
        resolve_database_path(
            "rel/explicit.db",
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            repo_root=fake_repo,
            relative_to_repo_root=False,
        )
        == (other_cwd / "rel" / "explicit.db").resolve()
    )
    assert (
        resolve_database_path(
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env={"SALES_OUTREACH_DB": "rel/env.db"},
            repo_root=fake_repo,
            relative_to_repo_root=False,
        )
        == (other_cwd / "rel" / "env.db").resolve()
    )

    # With relative_to_repo_root=True -> repo_root-relative (BackupEngine-compatible)
    assert (
        resolve_database_path(
            "rel/explicit.db",
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            repo_root=fake_repo,
            relative_to_repo_root=True,
        )
        == (fake_repo / "rel" / "explicit.db").resolve()
    )
    assert (
        resolve_database_path(
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env={"SALES_OUTREACH_DB": "rel/env.db"},
            repo_root=fake_repo,
            relative_to_repo_root=True,
        )
        == (fake_repo / "rel" / "env.db").resolve()
    )

    # Absolute paths and default fallback behave consistently under both anchoring options
    abs_db = tmp_path / "abs.db"
    for anchor in (False, True):
        assert (
            resolve_database_path(
                abs_db,
                policy=ResolutionPolicy.LEGACY_COMPATIBLE,
                repo_root=fake_repo,
                relative_to_repo_root=anchor,
            )
            == abs_db.resolve()
        )
        assert (
            resolve_database_path(
                policy=ResolutionPolicy.LEGACY_COMPATIBLE,
                env={},
                repo_root=fake_repo,
                relative_to_repo_root=anchor,
            )
            == (fake_repo / "data" / "sales_outreach.db").resolve()
        )

    # STRICT_EXPLICIT must still reject relative paths even when relative_to_repo_root=True
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            "rel/strict.db",
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            repo_root=fake_repo,
            relative_to_repo_root=True,
        )
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env={"SALES_OUTREACH_DB": "rel/strict.db"},
            repo_root=fake_repo,
            relative_to_repo_root=True,
        )


def test_centralized_operational_explicit_env_conflict_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CENTRALIZED_OPERATIONAL enforces conflict checks, repo-root anchoring, and safe fallback."""
    fake_repo = tmp_path / "op_repo"
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir(parents=True)
    monkeypatch.chdir(other_cwd)

    # 1. Unconfigured fallback -> <repo_root>/data/sales_outreach.db
    assert (
        resolve_database_path(
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            env={},
            repo_root=fake_repo,
        )
        == (fake_repo / "data" / "sales_outreach.db").resolve()
    )

    # 2. Relative explicit and env paths anchor to repo_root regardless of cwd
    assert (
        resolve_database_path(
            "data/custom.db",
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            env={},
            repo_root=fake_repo,
        )
        == (fake_repo / "data" / "custom.db").resolve()
    )
    assert (
        resolve_database_path(
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            env={"SALESAI_CANDIDATES_DB": "data/env_rel.db"},
            repo_root=fake_repo,
        )
        == (fake_repo / "data" / "env_rel.db").resolve()
    )

    # 3. Equivalent explicit + env targets succeed; disagreeing explicit vs env fail closed
    matching_abs = (fake_repo / "data" / "sales_outreach.db").resolve()
    assert (
        resolve_database_path(
            matching_abs,
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            env={
                "SALES_OUTREACH_DB": "data/sales_outreach.db",
                "SALESAI_DB_PATH": str(matching_abs),
            },
            repo_root=fake_repo,
        )
        == matching_abs
    )
    with pytest.raises(DatabaseConfigurationConflictError):
        resolve_database_path(
            tmp_path / "explicit_other.db",
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            env={"SALES_OUTREACH_DB": str(matching_abs)},
            repo_root=fake_repo,
        )

    # 4. In-memory rejected by default; explicit allow_memory=True permitted when consistent
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(":memory:", policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL)
    assert (
        resolve_database_path(
            ":memory:",
            policy=ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            allow_memory=True,
            env={},
        )
        == ":memory:"
    )


def test_additional_protected_roots_do_not_weaken_strict_explicit(tmp_path: Path) -> None:
    """Additional protected_roots protect sibling trees while preserving all mandatory STRICT protections."""
    fake_dev_repo = tmp_path / "dev_repo"
    fake_op_data = tmp_path / "sibling_op" / "data"

    # Mandatory _DEFAULT_REPO_ROOT/data protection still holds in STRICT_EXPLICIT even with custom protected_roots
    with pytest.raises(ProtectedDatabasePathError):
        resolve_database_path(
            _DEFAULT_REPO_ROOT / "data" / "sales_outreach.db",
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            repo_root=fake_dev_repo,
            protected_roots=[fake_op_data],
        )

    # Sibling operational root blocked in both STRICT_EXPLICIT and CENTRALIZED_OPERATIONAL
    for policy in (
        ResolutionPolicy.STRICT_EXPLICIT,
        ResolutionPolicy.CENTRALIZED_OPERATIONAL,
    ):
        with pytest.raises(ProtectedDatabasePathError):
            resolve_database_path(
                fake_op_data / "backups" / ".." / "sales_outreach.db",
                policy=policy,
                repo_root=fake_dev_repo,
                protected_roots=[fake_op_data],
            )
        with pytest.raises(ProtectedDatabasePathError):
            resolve_database_path(
                policy=policy,
                env={"SALESAI_DB_PATH": str(fake_op_data / "sales_outreach.db-wal")},
                repo_root=fake_dev_repo,
                protected_roots=[fake_op_data],
            )

    # Invalid protected_roots input fails closed
    with pytest.raises(TypeError):
        resolve_database_path(
            tmp_path / "ok.db",
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            protected_roots=str(fake_op_data),  # type: ignore[arg-type]
        )
    with pytest.raises(DatabaseResolutionError):
        resolve_database_path(
            tmp_path / "ok.db",
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            protected_roots=["   "],
        )
