"""
sales_engine/db_resolver.py
Standalone, side-effect-free SQLite database path resolver for Sales-AI.

This module performs pure path resolution without opening SQLite connections,
creating directories or files, reading .env files, or mutating process state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
import os
from pathlib import Path
from typing import Optional, Union


# Repository root derived from sales_engine/db_resolver.py -> sales_engine -> repo root
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OPERATIONAL_REL_PATH = Path("data") / "sales_outreach.db"
_SIDECAR_AND_BACKUP_SUFFIXES: tuple[str, ...] = (
    ".backup",
    ".bak",
    "-wal",
    "-shm",
    "-journal",
)

ENV_VAR_PRECEDENCE: tuple[str, ...] = (
    "SALES_OUTREACH_DB",
    "SALESAI_DB_PATH",
    "SALESAI_CANDIDATES_DB",
)
_ENV_VARS_UPPER = frozenset(k.upper() for k in ENV_VAR_PRECEDENCE)
_WINDOWS_INVALID_PATH_CHARS = '<>|"*'
_WINDOWS_RESERVED_DEVICE_NAMES = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


class ResolutionPolicy(str, Enum):
    """Explicit path-resolution strictness policy."""

    LEGACY_COMPATIBLE = "legacy_compatible"
    STRICT_EXPLICIT = "strict_explicit"
    CENTRALIZED_OPERATIONAL = "centralized_operational"


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
        if raw_str[0] in ("\\", "/"):
            raise DatabaseResolutionError(
                f"{label} uses unsupported Windows rooted path without drive specification: '{raw_str}'"
            )
        colon_indices = [idx for idx, ch in enumerate(raw_str) if ch == ":"]
        if any(idx != 1 for idx in colon_indices) or (
            colon_indices == [1] and not raw_str[0].isalpha()
        ):
            raise DatabaseResolutionError(
                f"{label} contains invalid colon or alternate data stream syntax: '{raw_str}'"
            )
        if colon_indices == [1] and (len(raw_str) == 2 or raw_str[2] not in ("\\", "/")):
            raise DatabaseResolutionError(
                f"{label} uses unsupported Windows drive-relative path syntax: '{raw_str}'"
            )
        if any(ch in raw_str for ch in _WINDOWS_INVALID_PATH_CHARS):
            raise DatabaseResolutionError(
                f"{label} contains invalid Windows path characters: '{raw_str}'"
            )
        for segment in raw_str.replace("/", "\\").split("\\"):
            if not segment or segment in (".", "..") or (len(segment) == 2 and segment[1] == ":"):
                continue
            if segment.endswith((".", " ")):
                raise DatabaseResolutionError(
                    f"{label} contains Windows path segment with trailing dot or space: '{raw_str}'"
                )
            stem = segment.split(".", 1)[0].rstrip(" ").upper()
            if stem in _WINDOWS_RESERVED_DEVICE_NAMES:
                raise DatabaseResolutionError(
                    f"{label} contains reserved Windows device name: '{raw_str}'"
                )

    return raw_str


def _normalize_protected_roots(
    protected_roots: Optional[Sequence[Union[str, os.PathLike[str]]]],
    *,
    repo_root_resolved: Path,
    anchor_relative_to_repo: bool,
) -> list[tuple[Path, Path]]:
    """Validate and resolve additional protected filesystem roots without touching disk contents."""
    if protected_roots is None:
        return []
    if isinstance(protected_roots, (str, bytes, bytearray, os.PathLike)):
        raise TypeError(
            "protected_roots must be a sequence of path strings or PathLike objects, "
            f"not {type(protected_roots).__name__}"
        )
    if not isinstance(protected_roots, Sequence):
        raise TypeError(
            f"protected_roots must be a sequence, not {type(protected_roots).__name__}"
        )

    normalized: list[tuple[Path, Path]] = []
    for idx, entry in enumerate(protected_roots):
        label = f"protected_roots[{idx}]"
        raw_str = _validate_raw_path_string(entry, label=label)
        if raw_str.lower() == ":memory:":
            raise DatabaseResolutionError(f"{label} cannot be ':memory:'")

        root_path = Path(raw_str)
        if not root_path.is_absolute() and anchor_relative_to_repo:
            root_path = repo_root_resolved / root_path

        try:
            resolved_root = root_path.resolve()
        except (OSError, RuntimeError) as exc:
            raise DatabaseResolutionError(
                f"{label} could not be resolved safely: '{raw_str}'"
            ) from exc

        if str(resolved_root).startswith(("\\\\", "//")):
            raise DatabaseResolutionError(
                f"{label} resolved to a UNC or extended device path: '{resolved_root}'"
            )
        normalized.append((resolved_root, _to_normcase_path(resolved_root)))

    return normalized


def _assert_not_in_protected_roots(
    resolved_target: Path,
    *,
    protected_roots_normalized: Sequence[tuple[Path, Path]],
    label: str,
) -> None:
    """Fail closed when resolved_target falls within any explicitly protected root or its sidecars."""
    if not protected_roots_normalized:
        return

    norm_target = _to_normcase_path(resolved_target)
    target_str = str(norm_target)
    for raw_root, norm_root in protected_roots_normalized:
        if _is_same_or_within(norm_target, norm_root):
            raise ProtectedDatabasePathError(
                f"{label} resolves to protected path '{raw_root}': '{resolved_target}'"
            )
        if raw_root.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            root_str = str(norm_root)
            if any(target_str == root_str + suffix for suffix in _SIDECAR_AND_BACKUP_SUFFIXES):
                raise ProtectedDatabasePathError(
                    f"{label} resolves to protected database sidecar/backup for '{raw_root}': '{resolved_target}'"
                )


def enforce_protected_roots(
    candidate: Union[str, os.PathLike[str]],
    *,
    protected_roots: Optional[Sequence[Union[str, os.PathLike[str]]]],
    repo_root: Optional[Union[str, os.PathLike[str]]] = None,
    relative_to_repo_root: bool = False,
    label: str = "db_path",
) -> None:
    """Validate protected_roots and fail closed if a non-memory candidate targets any protected root."""
    if protected_roots is None:
        return
    repo_root_resolved = _resolve_repo_root(repo_root)
    protected_roots_normalized = _normalize_protected_roots(
        protected_roots,
        repo_root_resolved=repo_root_resolved,
        anchor_relative_to_repo=relative_to_repo_root,
    )
    raw_str = _validate_raw_path_string(candidate, label=label)
    if raw_str.lower() == ":memory:":
        return
    path_obj = Path(raw_str)
    if not path_obj.is_absolute() and relative_to_repo_root:
        path_obj = repo_root_resolved / path_obj
    try:
        resolved = path_obj.resolve()
    except (OSError, RuntimeError) as exc:
        raise DatabaseResolutionError(f"{label} could not be resolved safely: '{raw_str}'") from exc
    if str(resolved).startswith(("\\\\", "//")):
        raise DatabaseResolutionError(
            f"{label} resolved to a UNC or extended device path: '{resolved}'"
        )
    _assert_not_in_protected_roots(
        resolved,
        protected_roots_normalized=protected_roots_normalized,
        label=label,
    )


def _resolve_single_candidate(
    raw_value: Union[str, os.PathLike[str]],
    *,
    label: str,
    policy: ResolutionPolicy,
    allow_memory: bool,
    repo_root_resolved: Path,
    relative_to_repo_root: bool = False,
    protected_roots_normalized: Sequence[tuple[Path, Path]] = (),
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
        if relative_to_repo_root or policy is ResolutionPolicy.CENTRALIZED_OPERATIONAL:
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

    if policy in (ResolutionPolicy.STRICT_EXPLICIT, ResolutionPolicy.CENTRALIZED_OPERATIONAL) or protected_roots_normalized:
        _assert_not_in_protected_roots(
            resolved,
            protected_roots_normalized=protected_roots_normalized,
            label=label,
        )

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
            if key_upper in seen_upper and policy in (
                ResolutionPolicy.STRICT_EXPLICIT,
                ResolutionPolicy.CENTRALIZED_OPERATIONAL,
            ):
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
    protected_roots: Optional[Sequence[Union[str, os.PathLike[str]]]] = None,
) -> Union[Path, str]:
    """
    Resolve the SQLite database path cleanly without opening or creating any file.

    Precedence:
      1. Explicit `db_path` argument (overrides env vars in `LEGACY_COMPATIBLE`/`STRICT_EXPLICIT`;
         must agree with any configured env vars in `CENTRALIZED_OPERATIONAL`)
      2. `SALES_OUTREACH_DB`
      3. `SALESAI_DB_PATH`
      4. `SALESAI_CANDIDATES_DB`
      5. Repository-root operational fallback (`<repo_root>/data/sales_outreach.db`),
         permitted under `LEGACY_COMPATIBLE` and `CENTRALIZED_OPERATIONAL`.

    Policies:
      - `STRICT_EXPLICIT` (default): Requires explicit `db_path` or unambiguous absolute env config;
        rejects relative paths, conflicting non-empty env values, and protected operational paths.
      - `LEGACY_COMPATIBLE`: Preserves legacy precedence, short-circuits on the highest-priority
        configured env var without validating ignored lower-priority vars, and allows zero-config
        fallback to `<repo_root>/data/sales_outreach.db`. When `relative_to_repo_root=True`,
        relative explicit/env paths anchor to `repo_root` instead of `cwd`.
      - `CENTRALIZED_OPERATIONAL`: Conflict-checked operational P-1 policy. Anchors all relative
        paths to `repo_root`, normalizes paths to canonical absolutes, validates all non-empty
        env vars (failing closed on whitespace/invalid syntax or disagreeing targets, including
        explicit `db_path` vs configured env), permits `<repo_root>/data/sales_outreach.db`
        unless blocked by `protected_roots`, and rejects `:memory:` unless `allow_memory=True`.
    """
    if not isinstance(policy, ResolutionPolicy):
        raise TypeError(f"policy must be a ResolutionPolicy, not {type(policy).__name__}")
    if not isinstance(allow_memory, bool):
        raise TypeError(f"allow_memory must be bool, not {type(allow_memory).__name__}")
    if not isinstance(relative_to_repo_root, bool):
        raise TypeError(
            f"relative_to_repo_root must be bool, not {type(relative_to_repo_root).__name__}"
        )

    repo_root_resolved = _resolve_repo_root(repo_root)
    anchor_relative = relative_to_repo_root or (policy is ResolutionPolicy.CENTRALIZED_OPERATIONAL)
    protected_roots_normalized = _normalize_protected_roots(
        protected_roots,
        repo_root_resolved=repo_root_resolved,
        anchor_relative_to_repo=anchor_relative,
    )

    # 1. Explicit db_path in LEGACY_COMPATIBLE and STRICT_EXPLICIT overrides ambient env
    if db_path is not None and policy is not ResolutionPolicy.CENTRALIZED_OPERATIONAL:
        return _resolve_single_candidate(
            db_path,
            label="db_path",
            policy=policy,
            allow_memory=allow_memory,
            repo_root_resolved=repo_root_resolved,
            relative_to_repo_root=relative_to_repo_root,
            protected_roots_normalized=protected_roots_normalized,
        )

    # 2. Environment variable resolution
    env_map = os.environ if env is None else env
    configured = _extract_env_candidates(env_map, policy=policy)

    if policy is ResolutionPolicy.CENTRALIZED_OPERATIONAL:
        explicit_resolved: Optional[Union[Path, str]] = None
        if db_path is not None:
            explicit_resolved = _resolve_single_candidate(
                db_path,
                label="db_path",
                policy=policy,
                allow_memory=allow_memory,
                repo_root_resolved=repo_root_resolved,
                relative_to_repo_root=True,
                protected_roots_normalized=protected_roots_normalized,
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
                    relative_to_repo_root=True,
                    protected_roots_normalized=protected_roots_normalized,
                )

        if len(resolved_entries) > 1:
            normalized_set = {
                v if isinstance(v, str) else _to_normcase_path(v)
                for v in resolved_entries.values()
            }
            if len(normalized_set) > 1:
                details = ", ".join(f"{k}={configured[k]!r}" for k in resolved_entries)
                raise DatabaseConfigurationConflictError(
                    "Conflicting database environment variables under CENTRALIZED_OPERATIONAL policy: "
                    f"{details}"
                )

        if explicit_resolved is not None:
            if resolved_entries:
                norm_explicit = (
                    explicit_resolved
                    if isinstance(explicit_resolved, str)
                    else _to_normcase_path(explicit_resolved)
                )
                first_env_key = next(iter(resolved_entries))
                first_env_val = resolved_entries[first_env_key]
                norm_env = (
                    first_env_val
                    if isinstance(first_env_val, str)
                    else _to_normcase_path(first_env_val)
                )
                if norm_explicit != norm_env:
                    raise DatabaseConfigurationConflictError(
                        "Explicit db_path conflicts with configured database environment variable "
                        f"{first_env_key}={configured[first_env_key]!r} under CENTRALIZED_OPERATIONAL policy"
                    )
            return explicit_resolved

        if resolved_entries:
            for key in ENV_VAR_PRECEDENCE:
                if key in resolved_entries:
                    return resolved_entries[key]

        fallback_path = (repo_root_resolved / _DEFAULT_OPERATIONAL_REL_PATH).resolve()
        _assert_not_in_protected_roots(
            fallback_path,
            protected_roots_normalized=protected_roots_normalized,
            label="default_fallback",
        )
        return fallback_path

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
                        protected_roots_normalized=protected_roots_normalized,
                    )

        resolved_entries = {}
        for key in ENV_VAR_PRECEDENCE:
            if key in configured:
                resolved_entries[key] = _resolve_single_candidate(
                    configured[key],
                    label=f"env[{key}]",
                    policy=policy,
                    allow_memory=allow_memory,
                    repo_root_resolved=repo_root_resolved,
                    relative_to_repo_root=relative_to_repo_root,
                    protected_roots_normalized=protected_roots_normalized,
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
        fallback_path = (repo_root_resolved / _DEFAULT_OPERATIONAL_REL_PATH).resolve()
        _assert_not_in_protected_roots(
            fallback_path,
            protected_roots_normalized=protected_roots_normalized,
            label="default_fallback",
        )
        return fallback_path

    raise DatabaseResolutionError(
        "No explicit db_path or configured database environment variable "
        "(SALES_OUTREACH_DB, SALESAI_DB_PATH, SALESAI_CANDIDATES_DB) "
        "under STRICT_EXPLICIT policy."
    )
