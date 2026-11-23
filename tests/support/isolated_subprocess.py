"""
tests/support/isolated_subprocess.py
Test-only fail-closed subprocess runner ensuring strictly isolated SQLite environments.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Optional, Sequence, Union


# Resolve repository root dynamically relative to this helper file:
# tests/support/isolated_subprocess.py -> tests/support -> tests -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_CANONICAL_DB_KEYS = ("SALES_OUTREACH_DB", "SALESAI_DB_PATH", "SALESAI_CANDIDATES_DB")
_CANONICAL_DB_KEYS_UPPER = frozenset(k.upper() for k in _CANONICAL_DB_KEYS)

_OPERATIONAL_FILENAMES = (
    "sales_outreach.db",
    "sales_outreach.db.backup",
    "sales_outreach.db.bak",
    "sales_outreach.db-wal",
    "sales_outreach.db-shm",
    "sales_outreach.db-journal",
)

_WINDOWS_INVALID_PATH_CHARS = '<>|"*'


def _to_normcase_path(resolved: Path) -> Path:
    """Return a normalized-case Path for case-insensitive containment comparisons."""
    return Path(os.path.normcase(str(resolved)))


def _is_same_or_within(candidate: Path, base_dir: Path) -> bool:
    """Return True if candidate equals base_dir or resides strictly beneath it."""
    return candidate == base_dir or candidate.is_relative_to(base_dir)


def _is_strictly_within(candidate: Path, base_dir: Path) -> bool:
    """Return True if candidate resides strictly beneath base_dir (and is not base_dir itself)."""
    return candidate != base_dir and candidate.is_relative_to(base_dir)


def _validate_and_normalize_fs_path(
    raw_path: Union[str, os.PathLike[str]],
    *,
    label: str,
) -> tuple[Path, Path]:
    """
    Validate a filesystem path without opening or creating any file, returning
    (resolved_path, normcase_resolved_path).
    """
    if isinstance(raw_path, (bytes, bytearray)):
        raise TypeError(f"{label} must be str or PathLike[str], not {type(raw_path).__name__}")

    try:
        raw_str = os.fspath(raw_path)
    except TypeError as exc:
        raise TypeError(f"{label} must be str or PathLike[str]") from exc

    if not isinstance(raw_str, str):
        raise TypeError(f"{label} os.fspath() must return str, not {type(raw_str).__name__}")

    if not raw_str or raw_str != raw_str.strip():
        raise ValueError(f"{label} must be non-empty without leading or trailing whitespace")

    if "\x00" in raw_str:
        raise ValueError(f"{label} contains NUL byte")

    lower_str = raw_str.lower()
    if lower_str == ":memory:" or lower_str.startswith("file:") or "?" in raw_str:
        raise ValueError(
            f"{label} must be an absolute filesystem path, not ':memory:' or SQLite URI: '{raw_str}'"
        )

    if raw_str.startswith(("\\\\", "//")):
        raise ValueError(f"{label} must not be a UNC or extended device path: '{raw_str}'")

    if os.name == "nt":
        colon_indices = [idx for idx, ch in enumerate(raw_str) if ch == ":"]
        if any(idx != 1 for idx in colon_indices) or (
            colon_indices == [1] and not raw_str[0].isalpha()
        ):
            raise ValueError(
                f"{label} contains invalid colon or alternate data stream syntax: '{raw_str}'"
            )
        if any(ch in raw_str for ch in _WINDOWS_INVALID_PATH_CHARS):
            raise ValueError(f"{label} contains invalid Windows path characters: '{raw_str}'")

    path_obj = Path(raw_str)
    if not path_obj.is_absolute():
        raise ValueError(f"{label} must be an absolute path; got relative path '{raw_str}'")

    try:
        resolved = path_obj.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} could not be resolved safely: '{raw_str}'") from exc

    if str(resolved).startswith(("\\\\", "//")):
        raise ValueError(f"{label} resolved to a UNC or extended device path: '{resolved}'")

    return resolved, _to_normcase_path(resolved)


def _get_repo_norm_paths() -> tuple[Path, Path]:
    """Return normcase Paths for repository root and repository data directory."""
    try:
        repo_root_resolved = _REPO_ROOT.resolve()
        repo_data_resolved = (_REPO_ROOT / "data").resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Unable to resolve repository operational boundaries safely") from exc
    return _to_normcase_path(repo_root_resolved), _to_normcase_path(repo_data_resolved)


def _check_not_operational(norm_target: Path, *, label: str) -> None:
    """Reject paths targeting repository data/ or cwd-relative operational files/backups."""
    _, norm_repo_data = _get_repo_norm_paths()
    if _is_same_or_within(norm_target, norm_repo_data):
        raise PermissionError(
            f"CRITICAL TEST ISOLATION FAILURE: {label} resolves to protected operational "
            f"data directory '{norm_target}'"
        )

    try:
        cwd_resolved = Path.cwd().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Unable to resolve current working directory safely") from exc

    cwd_data = cwd_resolved / "data"
    try:
        cwd_backups_norm = _to_normcase_path((cwd_data / "backups").resolve())
        cwd_op_files = {
            _to_normcase_path((cwd_data / name).resolve()) for name in _OPERATIONAL_FILENAMES
        }
    except (OSError, RuntimeError) as exc:
        raise ValueError("Unable to resolve cwd-relative operational paths safely") from exc

    if _is_same_or_within(norm_target, cwd_backups_norm) or norm_target in cwd_op_files:
        raise PermissionError(
            f"CRITICAL TEST ISOLATION FAILURE: {label} resolves to protected operational "
            f"database or backup path '{norm_target}'"
        )


def _validate_trusted_dir(
    raw_dir: Union[str, os.PathLike[str]],
    *,
    label: str,
) -> tuple[Path, Path]:
    """Validate that a directory is a safe disposable root and cannot encompass repo/data."""
    resolved_dir, norm_dir = _validate_and_normalize_fs_path(raw_dir, label=label)

    if resolved_dir.exists() and not resolved_dir.is_dir():
        raise ValueError(f"{label} exists but is not a directory: '{resolved_dir}'")

    if resolved_dir.parent == resolved_dir:
        raise ValueError(f"{label} cannot be a filesystem root: '{resolved_dir}'")

    norm_repo_root, norm_repo_data = _get_repo_norm_paths()

    if (
        _is_same_or_within(norm_dir, norm_repo_data)
        or _is_strictly_within(norm_repo_data, norm_dir)
        or _is_same_or_within(norm_dir, norm_repo_root)
        or _is_strictly_within(norm_repo_root, norm_dir)
    ):
        raise PermissionError(
            f"CRITICAL TEST ISOLATION FAILURE: {label} '{resolved_dir}' overlaps or "
            f"encompasses the repository or operational data directory"
        )

    _check_not_operational(norm_dir, label=label)
    return resolved_dir, norm_dir


def _validate_command(cmd: Sequence[str]) -> None:
    """Ensure cmd is a non-empty list/tuple of NUL-free strings with non-empty executable."""
    if isinstance(cmd, (str, bytes, bytearray, memoryview)) or not isinstance(cmd, (list, tuple)):
        raise TypeError(
            f"cmd must be a list or tuple of strings, not {type(cmd).__name__}"
        )

    if len(cmd) == 0:
        raise ValueError("cmd must not be empty")

    for idx, arg in enumerate(cmd):
        if not isinstance(arg, str):
            raise TypeError(f"cmd[{idx}] must be str, not {type(arg).__name__}")
        if "\x00" in arg:
            raise ValueError(f"cmd[{idx}] contains NUL byte")

    if not cmd[0].strip():
        raise ValueError("cmd executable name (cmd[0]) must not be empty or whitespace-only")


def _parse_cli_db_paths(cmd: Sequence[str]) -> list[str]:
    """Extract all --db-path arguments from cmd sequence, failing closed on malformed flags."""
    extracted: list[str] = []
    i = 0
    n = len(cmd)
    while i < n:
        arg = cmd[i]
        arg_lower = arg.lower()
        if arg_lower == "--db-path":
            if i + 1 >= n:
                raise ValueError(f"CLI argument '{arg}' is missing its path value")
            val = cmd[i + 1]
            if not val or not val.strip():
                raise ValueError(f"CLI argument '{arg}' has an empty or whitespace path value")
            if val != val.strip():
                raise ValueError(
                    f"CLI argument '{arg}' path value has leading or trailing whitespace: {val!r}"
                )
            if val.startswith("-"):
                raise ValueError(
                    f"CLI argument '{arg}' received option-like value '{val}' instead of a path"
                )
            extracted.append(val)
            i += 2
            continue
        if arg_lower.startswith("--db-path="):
            val = arg.split("=", 1)[1]
            if not val or not val.strip():
                raise ValueError(f"CLI argument '{arg}' has an empty or whitespace path value")
            if val != val.strip():
                raise ValueError(
                    f"CLI argument '{arg}' path value has leading or trailing whitespace: {val!r}"
                )
            if val.startswith("-"):
                raise ValueError(
                    f"CLI argument '{arg}' received option-like value '{val}' instead of a path"
                )
            extracted.append(val)
        i += 1
    return extracted


def _validate_extra_env(
    extra_env: Optional[Mapping[str, str]],
    *,
    norm_target: Path,
    isolated_db_path: Union[str, os.PathLike[str]],
) -> dict[str, str]:
    """Validate caller extra_env entries without mutating caller mappings or os.environ."""
    if extra_env is None:
        return {}

    if not isinstance(extra_env, Mapping):
        raise TypeError(f"extra_env must be a Mapping[str, str], not {type(extra_env).__name__}")

    clean_extra: dict[str, str] = {}
    seen_upper: dict[str, str] = {}

    for k, v in extra_env.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError("All keys and values in extra_env must be str")
        if not k or "\x00" in k or "\x00" in v:
            raise ValueError("extra_env contains empty key or NUL byte")

        k_upper = k.upper()
        if os.name == "nt":
            if k_upper in seen_upper:
                raise ValueError(
                    f"Duplicate case-insensitive key in extra_env: '{k}' conflicts with '{seen_upper[k_upper]}'"
                )
            seen_upper[k_upper] = k

        if k_upper in _CANONICAL_DB_KEYS_UPPER:
            _, norm_val = _validate_and_normalize_fs_path(v, label=f"extra_env['{k}']")
            _check_not_operational(norm_val, label=f"extra_env['{k}']")
            if norm_val != norm_target:
                raise ValueError(
                    f"Conflict in extra_env['{k}']: '{v}' does not match '{isolated_db_path}'"
                )
        else:
            clean_extra[k] = v

    return clean_extra


def run_isolated_subprocess(
    cmd: Sequence[str],
    *,
    isolated_db_path: Union[str, os.PathLike[str]],
    trusted_dir: Optional[Union[str, os.PathLike[str]]] = None,
    extra_env: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """
    Execute a subprocess with strictly enforced, fail-closed database isolation.

    Guarantees:
    1. Rejects non-False 'shell' and direct 'env' kwargs unconditionally.
    2. Requires cmd to be a non-empty list/tuple of strings (rejects str/bytes commands).
    3. Validates isolated_db_path as a clean, absolute filesystem path outside operational paths
       and strictly contained within trusted_dir (defaulting to tempfile.gettempdir()).
    4. Validates all CLI --db-path arguments against isolated_db_path and trusted_dir.
    5. Validates extra_env conflicts case-insensitively on Windows and populates identical
       SALES_OUTREACH_DB, SALESAI_DB_PATH, and SALESAI_CANDIDATES_DB values.
    6. Does not mutate parent os.environ and never creates or opens any SQLite file.
    """
    if "shell" in kwargs and kwargs["shell"] is not False:
        raise ValueError(
            "run_isolated_subprocess unconditionally rejects non-False 'shell' to prevent shell-parsing bypass."
        )

    if "env" in kwargs:
        raise ValueError(
            "Passing 'env' directly to run_isolated_subprocess is forbidden to prevent isolation bypass. "
            "Use 'extra_env' instead."
        )

    _validate_command(cmd)

    resolved_target, norm_target = _validate_and_normalize_fs_path(
        isolated_db_path, label="isolated_db_path"
    )
    if resolved_target.exists() and resolved_target.is_dir():
        raise ValueError(
            f"isolated_db_path must point to a file path, not an existing directory: '{resolved_target}'"
        )

    _check_not_operational(norm_target, label="isolated_db_path")

    boundary_dir = trusted_dir if trusted_dir is not None else tempfile.gettempdir()
    boundary_label = "trusted_dir" if trusted_dir is not None else "default temp directory"
    _, norm_trusted_dir = _validate_trusted_dir(boundary_dir, label=boundary_label)

    if not _is_strictly_within(norm_target, norm_trusted_dir):
        raise PermissionError(
            f"CRITICAL TEST ISOLATION FAILURE: isolated_db_path '{resolved_target}' escapes "
            f"trusted disposable directory '{norm_trusted_dir}'"
        )

    # Validate all CLI --db-path arguments in cmd
    for cp in _parse_cli_db_paths(cmd):
        resolved_cli, norm_cli = _validate_and_normalize_fs_path(cp, label="CLI --db-path")
        _check_not_operational(norm_cli, label="CLI --db-path")
        if not _is_strictly_within(norm_cli, norm_trusted_dir):
            raise PermissionError(
                f"CRITICAL TEST ISOLATION FAILURE: CLI --db-path '{resolved_cli}' escapes "
                f"trusted disposable directory '{norm_trusted_dir}'"
            )
        if norm_cli != norm_target:
            raise ValueError(
                f"CLI --db-path mismatch: command specifies '{cp}' but runner requires '{isolated_db_path}'"
            )

    clean_extra_env = _validate_extra_env(
        extra_env,
        norm_target=norm_target,
        isolated_db_path=isolated_db_path,
    )

    # Construct child environment without mutating parent os.environ
    child_env = os.environ.copy()

    # Strip existing DB keys case-insensitively from child_env
    for existing_key in [k for k in child_env if k.upper() in _CANONICAL_DB_KEYS_UPPER]:
        child_env.pop(existing_key, None)

    if os.name == "nt" and clean_extra_env:
        extra_upper = {k.upper() for k in clean_extra_env}
        for existing_key in [k for k in child_env if k.upper() in extra_upper]:
            child_env.pop(existing_key, None)

    child_env.update(clean_extra_env)

    target_str = str(resolved_target)
    for k in _CANONICAL_DB_KEYS:
        child_env[k] = target_str

    return subprocess.run(cmd, env=child_env, **kwargs)
