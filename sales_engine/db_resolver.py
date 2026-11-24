"""
sales_engine/db_resolver.py
Standalone, side-effect-free SQLite database path resolver for Sales-AI.

This module performs pure path resolution without opening SQLite connections,
creating directories or files, reading .env files, or mutating process state.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import os
from pathlib import Path
from typing import Optional, Union


# Repository root derived from sales_engine/db_resolver.py -> sales_engine -> repo root
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OPERATIONAL_REL_PATH = Path("data") / "sales_outreach.db"

ENV_VAR_PRECEDENCE: tuple[str, ...] = (
    "SALES_OUTREACH_DB",
    "SALESAI_DB_PATH",
    "SALESAI_CANDIDATES_DB",
)
_ENV_VARS_UPPER = frozenset(k.upper() for k in ENV_VAR_PRECEDENCE)
_WINDOWS_INVALID_PATH_CHARS = '<>|"*'


class ResolutionPolicy(str, Enum):
    """Explicit path-resolution strictness policy."""

    LEGACY_COMPATIBLE = "legacy_compatible"
    STRICT_EXPLICIT = "strict_explicit"


class DatabaseResolutionError(ValueError):
    """Raised when a database path cannot be resolved safely under the active policy."""


class DatabaseConfigurationConflictError(DatabaseResolutionError):
    """Raised in strict mode when multiple configured database env vars disagree."""


class ProtectedDatabasePathError(PermissionError):
    """Raised in strict mode when resolution targets a protected operational database path."""


def _to_normcase_path(resolved: Path) -> Path:
    return Path(os.path.normcase(str(resolved)))


def _is_same_or_within(candidate: Path, base_dir: Path) -> bool:
    return candidate == base_dir or candidate.is_relative_to(base_dir)


def _resolve_repo_root(repo_root: Optional[Union[str, os.PathLike[str]]]) -> Path:
    base = _DEFAULT_REPO_ROOT if repo_root is None else Path(os.fspath(repo_root))
    try:
        return base.resolve()
    except (OSError, RuntimeError) as exc:
        raise DatabaseResolutionError(f"Unable to resolve repository root '{base}'") from exc


def _validate_raw_path_string(
    raw_value: Union[str, os.PathLike[str]],
    *,
    label: str,
) -> str:
    """Validate type, encoding hygiene, and unsupported URI/UNC/Windows syntax."""
    if isinstance(raw_value, (bytes, bytearray)):
        raise TypeError(f"{label} must be str or PathLike[str], not {type(raw_value).__name__}")
    try:
        raw_str = os.fspath(raw_value)
    except TypeError as exc:
        raise TypeError(f"{label} must be str or PathLike[str]") from exc
    if not isinstance(raw_str, str):
        raise TypeError(f"{label} os.fspath() must return str, not {type(raw_str).__name__}")

    if not raw_str or raw_str != raw_str.strip():
        raise DatabaseResolutionError(
            f"{label} must be non-empty without leading or trailing whitespace"
        )
    if "\x00" in raw_str:
        raise DatabaseResolutionError(f"{label} contains NUL byte")

    lower_str = raw_str.lower()
    if lower_str.startswith("file:") or "?" in raw_str:
        raise DatabaseResolutionError(
            f"{label} must be a filesystem path, not a SQLite URI: '{raw_str}'"
        )
    if raw_str.startswith(("\\\\", "//")):
        raise DatabaseResolutionError(
            f"{label} must not be a UNC or extended device path: '{raw_str}'"
        )

    if os.name == "nt" and lower_str != ":memory:":
        colon_indices = [idx for idx, ch in enumerate(raw_str) if ch == ":"]
        if any(idx != 1 for idx in colon_indices) or (
            colon_indices == [1] and not raw_str[0].isalpha()
        ):
            raise DatabaseResolutionError(
                f"{label} contains invalid colon or alternate data stream syntax: '{raw_str}'"
            )
        if any(ch in raw_str for ch in _WINDOWS_INVALID_PATH_CHARS):
            raise DatabaseResolutionError(
                f"{label} contains invalid Windows path characters: '{raw_str}'"
            )

    return raw_str


def _resolve_single_candidate(
    raw_value: Union[str, os.PathLike[str]],
    *,
    label: str,
    policy: ResolutionPolicy,
    allow_memory: bool,
    repo_root_resolved: Path,
    relative_to_repo_root: bool = False,
) -> Union[Path, str]:
    """Validate and resolve a single explicit or env-configured database target."""
    raw_str = _validate_raw_path_string(raw_value, label=label)

    if raw_str.lower() == ":memory:":
        if not allow_memory:
            raise DatabaseResolutionError(
                f"{label} specified ':memory:', which is disabled (allow_memory=False)"
            )
        return ":memory:"

    path_obj = Path(raw_str)
    if not path_obj.is_absolute():
        if policy is ResolutionPolicy.STRICT_EXPLICIT:
            raise DatabaseResolutionError(
                f"{label} must be an absolute path under STRICT_EXPLICIT policy; got '{raw_str}'"
            )
        if relative_to_repo_root:
            path_obj = repo_root_resolved / path_obj

    try:
        resolved = path_obj.resolve()
    except (OSError, RuntimeError) as exc:
        raise DatabaseResolutionError(f"{label} could not be resolved safely: '{raw_str}'") from exc

    if str(resolved).startswith(("\\\\", "//")):
        raise DatabaseResolutionError(
            f"{label} resolved to a UNC or extended device path: '{resolved}'"
        )

    if policy is ResolutionPolicy.STRICT_EXPLICIT:
        _assert_not_protected_operational(resolved, repo_root_resolved=repo_root_resolved, label=label)

    return resolved


def _assert_not_protected_operational(
    resolved_target: Path,
    *,
    repo_root_resolved: Path,
    label: str,
) -> None:
    """Fail closed in strict mode when target resolves into operational data/ or backups."""
    norm_target = _to_normcase_path(resolved_target)
    try:
        norm_actual_repo_data = _to_normcase_path((_DEFAULT_REPO_ROOT / "data").resolve())
        norm_repo_data = _to_normcase_path((repo_root_resolved / "data").resolve())
        norm_cwd_data = _to_normcase_path((Path.cwd().resolve() / "data").resolve())
    except (OSError, RuntimeError) as exc:
        raise DatabaseResolutionError("Unable to resolve operational database boundaries") from exc

    if _is_same_or_within(norm_target, norm_actual_repo_data) or _is_same_or_within(
        norm_target, norm_repo_data
    ):
        raise ProtectedDatabasePathError(
            f"{label} resolves to protected operational data directory: '{resolved_target}'"
        )

    cwd_protected_files = {
        norm_cwd_data / name
        for name in (
            "sales_outreach.db",
            "sales_outreach.db.backup",
            "sales_outreach.db.bak",
            "sales_outreach.db-wal",
            "sales_outreach.db-shm",
            "sales_outreach.db-journal",
        )
    }
    if _is_same_or_within(norm_target, norm_cwd_data / "backups") or norm_target in cwd_protected_files:
        raise ProtectedDatabasePathError(
            f"{label} resolves to protected operational database path: '{resolved_target}'"
        )


def _extract_env_candidates(
    env_map: Mapping[str, str],
    *,
    policy: ResolutionPolicy,
) -> dict[str, str]:
    """
    Extract configured values for ENV_VAR_PRECEDENCE without mutating env_map.
    Empty string ('') values are treated as unset, while whitespace-only values
    are preserved so selected or strict-mode validation rejects them explicitly.
    """
    if not isinstance(env_map, Mapping):
        raise TypeError(f"env must be a Mapping[str, str], not {type(env_map).__name__}")

    found: dict[str, str] = {}
    seen_upper: dict[str, str] = {}

    for key, val in env_map.items():
        if not isinstance(key, str):
            continue
        key_upper = key.upper() if os.name == "nt" else key
        if key_upper not in _ENV_VARS_UPPER:
            continue

        if os.name == "nt":
            if key_upper in seen_upper and policy is ResolutionPolicy.STRICT_EXPLICIT:
                raise DatabaseConfigurationConflictError(
                    f"Duplicate case-insensitive database env key: '{key}' conflicts with '{seen_upper[key_upper]}'"
                )
            seen_upper[key_upper] = key

        if not isinstance(val, str):
            raise TypeError(f"Environment variable '{key}' must be str, not {type(val).__name__}")

        if val == "":
            # Empty string env values are treated as unset; whitespace-only values fail validation
            continue

        canonical_key = next(k for k in ENV_VAR_PRECEDENCE if k.upper() == key_upper)
        # In legacy mode, exact canonical casing takes precedence if duplicate case variants exist
        if canonical_key not in found or key == canonical_key:
            found[canonical_key] = val

    return found


def resolve_database_path(
    db_path: Optional[Union[str, os.PathLike[str]]] = None,
    *,
    policy: ResolutionPolicy = ResolutionPolicy.STRICT_EXPLICIT,
    env: Optional[Mapping[str, str]] = None,
    allow_memory: bool = False,
    repo_root: Optional[Union[str, os.PathLike[str]]] = None,
    relative_to_repo_root: bool = False,
) -> Union[Path, str]:
    """
    Resolve the SQLite database path cleanly without opening or creating any file.

    Precedence:
      1. Explicit `db_path` argument (overrides environment variables)
      2. `SALES_OUTREACH_DB`
      3. `SALESAI_DB_PATH`
      4. `SALESAI_CANDIDATES_DB`
      5. Repository-root operational fallback (`<repo_root>/data/sales_outreach.db`),
         permitted ONLY under `ResolutionPolicy.LEGACY_COMPATIBLE`.

    Policies:
      - `STRICT_EXPLICIT` (default): Requires explicit `db_path` or unambiguous absolute env config;
        rejects relative paths, conflicting non-empty env values, and protected operational paths.
      - `LEGACY_COMPATIBLE`: Preserves legacy precedence, short-circuits on the highest-priority
        configured env var without validating ignored lower-priority vars, and allows zero-config
        fallback to `<repo_root>/data/sales_outreach.db`. When `relative_to_repo_root=True`,
        relative explicit/env paths anchor to `repo_root` instead of `cwd`.
    """
    if not isinstance(policy, ResolutionPolicy):
        raise TypeError(f"policy must be a ResolutionPolicy, not {type(policy).__name__}")
    if not isinstance(relative_to_repo_root, bool):
        raise TypeError(
            f"relative_to_repo_root must be bool, not {type(relative_to_repo_root).__name__}"
        )

    repo_root_resolved = _resolve_repo_root(repo_root)

    # 1. Explicit db_path overrides all environment variables
    if db_path is not None:
        return _resolve_single_candidate(
            db_path,
            label="db_path",
            policy=policy,
            allow_memory=allow_memory,
            repo_root_resolved=repo_root_resolved,
            relative_to_repo_root=relative_to_repo_root,
        )

    # 2. Environment variable resolution
    env_map = os.environ if env is None else env
    configured = _extract_env_candidates(env_map, policy=policy)

    if configured:
        if policy is ResolutionPolicy.LEGACY_COMPATIBLE:
            # Short-circuit on highest-priority configured env var; do not validate ignored vars
            for key in ENV_VAR_PRECEDENCE:
                if key in configured:
                    return _resolve_single_candidate(
                        configured[key],
                        label=f"env[{key}]",
                        policy=policy,
                        allow_memory=allow_memory,
                        repo_root_resolved=repo_root_resolved,
                        relative_to_repo_root=relative_to_repo_root,
                    )

        resolved_entries: dict[str, Union[Path, str]] = {}
        for key in ENV_VAR_PRECEDENCE:
            if key in configured:
                resolved_entries[key] = _resolve_single_candidate(
                    configured[key],
                    label=f"env[{key}]",
                    policy=policy,
                    allow_memory=allow_memory,
                    repo_root_resolved=repo_root_resolved,
                    relative_to_repo_root=relative_to_repo_root,
                )

        if len(resolved_entries) > 1:
            normalized_set = {
                v if isinstance(v, str) else _to_normcase_path(v)
                for v in resolved_entries.values()
            }
            if len(normalized_set) > 1:
                details = ", ".join(f"{k}={configured[k]!r}" for k in resolved_entries)
                raise DatabaseConfigurationConflictError(
                    f"Conflicting database environment variables under STRICT_EXPLICIT policy: {details}"
                )

        for key in ENV_VAR_PRECEDENCE:
            if key in resolved_entries:
                return resolved_entries[key]

    # 3. Unconfigured fallback
    if policy is ResolutionPolicy.LEGACY_COMPATIBLE:
        return (repo_root_resolved / _DEFAULT_OPERATIONAL_REL_PATH).resolve()

    raise DatabaseResolutionError(
        "No explicit db_path or configured database environment variable "
        "(SALES_OUTREACH_DB, SALESAI_DB_PATH, SALESAI_CANDIDATES_DB) "
        "under STRICT_EXPLICIT policy."
    )
