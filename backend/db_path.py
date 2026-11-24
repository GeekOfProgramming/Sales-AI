"""
backend/db_path.py
Shared, side-effect-free database path resolution boundary for Backend services.

Preserves caller-specific historical compatibility contracts (P-1, P-2, P-3, P-4)
without silently switching physical databases, while exposing centralized P-1
legacy and strict explicit target modes via `sales_engine.db_resolver`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import os
from typing import Optional, Union

from sales_engine.db_resolver import (
    DatabaseResolutionError,
    ResolutionPolicy,
    resolve_database_path,
)

DEFAULT_BACKEND_DB_STR = "data/sales_outreach.db"


class BackendDbMode(str, Enum):
    """Explicit database path-selection contract for Backend service factories."""

    LEGACY_P1 = "legacy_p1"
    LEGACY_P2 = "legacy_p2"
    LEGACY_P3 = "legacy_p3"
    LEGACY_P4 = "legacy_p4"
    CENTRALIZED_LEGACY = "centralized_legacy"
    CENTRALIZED_STRICT = "centralized_strict"


_LEGACY_MODE_SPECS: dict[BackendDbMode, tuple[tuple[str, ...], bool]] = {
    # mode -> (env_var_precedence, reject_selected_empty_string)
    # P-1 & P-3 historically used os.getenv(k, fallback) -> empty string was NOT unset; fail closed
    BackendDbMode.LEGACY_P1: (("SALES_OUTREACH_DB", "SALESAI_DB_PATH", "SALESAI_CANDIDATES_DB"), True),
    BackendDbMode.LEGACY_P3: (("SALESAI_DB_PATH", "SALESAI_CANDIDATES_DB"), True),
    # P-2 & P-4 historically used `os.getenv(k) or ...` / `if env_path:` -> "" fell through
    BackendDbMode.LEGACY_P2: (("SALES_OUTREACH_DB", "SALESAI_DB_PATH"), False),
    BackendDbMode.LEGACY_P4: (("SALES_OUTREACH_DB",), False),
}


def _lookup_env_case_aware(env_map: Mapping[str, str], key: str) -> Optional[str]:
    """Read an environment key without mutating env_map (case-insensitive on Windows)."""
    if key in env_map:
        return env_map[key]
    if os.name == "nt":
        target = key.upper()
        for existing_key, val in env_map.items():
            if isinstance(existing_key, str) and existing_key.upper() == target:
                return val
    return None


def resolve_backend_db_path(
    db_path: Optional[Union[str, os.PathLike[str]]] = None,
    *,
    mode: BackendDbMode = BackendDbMode.LEGACY_P1,
    env: Optional[Mapping[str, str]] = None,
    allow_memory: bool = True,
    repo_root: Optional[Union[str, os.PathLike[str]]] = None,
    relative_to_repo_root: bool = False,
) -> str:
    """
    Resolve the SQLite database path string for Backend services without opening SQLite.

    Modes:
      - `LEGACY_P1`: `SALES_OUTREACH_DB > SALESAI_DB_PATH > SALESAI_CANDIDATES_DB > "data/sales_outreach.db"`
        (rejects selected empty `""` or whitespace-only env values).
      - `LEGACY_P2`: `SALES_OUTREACH_DB > SALESAI_DB_PATH > "data/sales_outreach.db"`
        (preserves `""` fall-through from historical `or`-chain; rejects whitespace-only).
      - `LEGACY_P3`: `SALESAI_DB_PATH > SALESAI_CANDIDATES_DB > "data/sales_outreach.db"`
        (ignores `SALES_OUTREACH_DB`; rejects selected empty `""` or whitespace-only env values).
      - `LEGACY_P4`: `SALES_OUTREACH_DB > "data/sales_outreach.db"`
        (ignores `SALESAI_DB_PATH`/`SALESAI_CANDIDATES_DB`; preserves `""` fall-through; rejects whitespace-only).
      - `CENTRALIZED_LEGACY`: Delegates to `resolve_database_path(..., policy=LEGACY_COMPATIBLE)`.
      - `CENTRALIZED_STRICT`: Delegates to `resolve_database_path(..., policy=STRICT_EXPLICIT)`.
    """
    if not isinstance(mode, BackendDbMode):
        raise TypeError(f"mode must be a BackendDbMode, not {type(mode).__name__}")

    if mode is BackendDbMode.CENTRALIZED_LEGACY:
        resolved = resolve_database_path(
            db_path,
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env=env,
            allow_memory=allow_memory,
            repo_root=repo_root,
            relative_to_repo_root=relative_to_repo_root,
        )
        return str(resolved)

    if mode is BackendDbMode.CENTRALIZED_STRICT:
        resolved = resolve_database_path(
            db_path,
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env=env,
            allow_memory=allow_memory,
            repo_root=repo_root,
            relative_to_repo_root=relative_to_repo_root,
        )
        return str(resolved)

    # Historical compatibility modes (LEGACY_P1..P4)
    if db_path is not None:
        if isinstance(db_path, (bytes, bytearray)):
            raise TypeError(f"db_path must be str or PathLike[str], not {type(db_path).__name__}")
        raw_explicit = os.fspath(db_path)
        if not isinstance(raw_explicit, str):
            raise TypeError(f"db_path os.fspath() must return str, not {type(raw_explicit).__name__}")
        if raw_explicit != "":
            if not raw_explicit.strip():
                raise DatabaseResolutionError("Explicit db_path cannot be whitespace-only.")
            return raw_explicit

    env_map = os.environ if env is None else env
    if not isinstance(env_map, Mapping):
        raise TypeError(f"env must be a Mapping[str, str], not {type(env_map).__name__}")

    keys, reject_empty = _LEGACY_MODE_SPECS[mode]
    for key in keys:
        val = _lookup_env_case_aware(env_map, key)
        if val is None:
            continue
        if not isinstance(val, str):
            raise TypeError(f"Environment variable '{key}' must be str, not {type(val).__name__}")
        if val == "":
            if reject_empty:
                raise DatabaseResolutionError(
                    f"Configured Backend database environment variable '{key}' cannot be empty."
                )
            continue
        if not val.strip():
            raise DatabaseResolutionError(
                f"Configured Backend database environment variable '{key}' cannot be whitespace-only."
            )
        return val

    return DEFAULT_BACKEND_DB_STR
