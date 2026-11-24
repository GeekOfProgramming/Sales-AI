"""
sales_engine/discovery_v2/db_path.py
Shared database path resolution boundary for Discovery v2.

Separates the historical P-3 Discovery compatibility contract from the
centralized P-1 target contract so existing callers do not silently switch
physical databases while new isolated tooling can opt into centralized or
strict explicit resolution.
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

DEFAULT_DISCOVERY_DB_STR = "data/sales_outreach.db"
LEGACY_P3_ENV_VARS: tuple[str, ...] = ("SALESAI_DB_PATH", "SALESAI_CANDIDATES_DB")


class DiscoveryDbMode(str, Enum):
    """Explicit database path-selection contract for Discovery v2 callers."""

    LEGACY_P3 = "legacy_p3"
    CENTRALIZED_LEGACY = "centralized_legacy"
    CENTRALIZED_STRICT = "centralized_strict"


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


def resolve_discovery_db_path(
    db_path: Optional[Union[str, os.PathLike[str]]] = None,
    *,
    mode: DiscoveryDbMode = DiscoveryDbMode.LEGACY_P3,
    env: Optional[Mapping[str, str]] = None,
    include_candidates_env: bool = True,
    allow_memory: bool = True,
    repo_root: Optional[Union[str, os.PathLike[str]]] = None,
    relative_to_repo_root: bool = False,
) -> str:
    """
    Resolve the SQLite database path string for Discovery v2 without opening SQLite.

    Modes:
      - `LEGACY_P3` (default): Preserves historical Discovery P-3 contract
        (`db_path > SALESAI_DB_PATH > [SALESAI_CANDIDATES_DB] > "data/sales_outreach.db"`),
        ignoring `SALES_OUTREACH_DB` and preserving relative path strings and `:memory:`.
        Missing (`None`) env vars fall through the precedence chain; selected empty (`""`) or
        whitespace-only env values fail closed with `DatabaseResolutionError` instead of
        silently falling back to another DB or production.
      - `CENTRALIZED_LEGACY`: Delegates to `resolve_database_path(..., policy=LEGACY_COMPATIBLE)`
        using P-1 precedence (`db_path > SALES_OUTREACH_DB > SALESAI_DB_PATH > SALESAI_CANDIDATES_DB`).
      - `CENTRALIZED_STRICT`: Delegates to `resolve_database_path(..., policy=STRICT_EXPLICIT)`
        for isolated tooling (rejects unconfigured, conflicting, relative, or operational paths).
    """
    if not isinstance(mode, DiscoveryDbMode):
        raise TypeError(f"mode must be a DiscoveryDbMode, not {type(mode).__name__}")

    if mode is DiscoveryDbMode.CENTRALIZED_LEGACY:
        resolved = resolve_database_path(
            db_path,
            policy=ResolutionPolicy.LEGACY_COMPATIBLE,
            env=env,
            allow_memory=allow_memory,
            repo_root=repo_root,
            relative_to_repo_root=relative_to_repo_root,
        )
        return str(resolved)

    if mode is DiscoveryDbMode.CENTRALIZED_STRICT:
        resolved = resolve_database_path(
            db_path,
            policy=ResolutionPolicy.STRICT_EXPLICIT,
            env=env,
            allow_memory=allow_memory,
            repo_root=repo_root,
            relative_to_repo_root=relative_to_repo_root,
        )
        return str(resolved)

    # DiscoveryDbMode.LEGACY_P3 — historical Discovery compatibility contract
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

    keys = LEGACY_P3_ENV_VARS if include_candidates_env else ("SALESAI_DB_PATH",)
    for key in keys:
        val = _lookup_env_case_aware(env_map, key)
        if val is None:
            continue
        if not isinstance(val, str):
            raise TypeError(f"Environment variable '{key}' must be str, not {type(val).__name__}")
        if not val or not val.strip():
            raise DatabaseResolutionError(
                f"Configured Discovery database environment variable '{key}' cannot be empty or whitespace-only."
            )
        return val

    return DEFAULT_DISCOVERY_DB_STR
