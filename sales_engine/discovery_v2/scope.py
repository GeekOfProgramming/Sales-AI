# sales_engine/discovery_v2/scope.py
"""
Search Scope & Discovery Control (Production Readiness Step 1).

Decoupled control layer above Phase 10 discovery engine.
Defines what, where, and how discovery executes before running searches.

Guarantees:
- Zero mutations to Phase 5 scoring, Phase 9 suppression, send limits, or email sending.
- Strictly respects selected markets and enabled sources.
- No fake StructuredJob creation in non-job discovery modes.
- Deduplication by canonical domain identity.
- Full run audit metadata persistence.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.candidates.models import (
    CandidateCompany,
    DiscoveryObservation,
    DiscoveryStatus,
    OpportunityType,
    RelationshipType,
    CompanyTier,
    BIMMaturity,
)
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.source_registry import SourceConfig, SourceRegistry, sanitize_secret_text

logger = logging.getLogger(__name__)


# =========================================================================
# Canonical Constants & Supported Sets
# =========================================================================

class DiscoveryMode(str, Enum):
    JOB_FIRST = "job_first"
    COMPANY_FIRST = "company_first"
    PROJECT_TENDER_FIRST = "project_tender_first"
    MANUAL_HISTORICAL = "manual_historical"


SUPPORTED_MARKETS: List[str] = [
    "Italy",
    "Germany",
    "Austria",
    "Switzerland",
    "France",
    "Spain",
    "Netherlands",
    "UK",
    "Ireland",
    "UAE",
    "Saudi Arabia",
    "Oman",
    "Australia",
    "Canada",
    "USA",
]

SUPPORTED_RELATIONSHIP_TYPES: List[str] = [
    "service_buyer",
    "technology_partner",
    "channel_partner",
    "strategic_partner",
    "mixed",
    "unknown",
]

SUPPORTED_OPPORTUNITY_TYPES: List[str] = [
    "direct_bim_delivery",
    "mep_delivery",
    "bim_coordination",
    "overflow_capacity",
    "automation",
    "revit_api",
    "data_parameter_automation",
    "custom_software",
    "technology_partnership",
    "channel_partnership",
    "strategic_collaboration",
    "unknown",
]

SUPPORTED_COMPANY_TIERS: List[str] = [
    "tier1",
    "tier2",
    "tier3",
    "unknown",
]

SUPPORTED_BIM_MATURITY: List[str] = [
    "high",
    "medium",
    "low",
    "unknown",
]


def validate_scope_payload(data: Dict[str, Any], registry: Optional[SourceRegistry] = None) -> List[str]:
    """
    Validates Search Profile / DiscoveryScope attributes against authoritative taxonomies.
    Returns list of validation error strings, or empty list if valid.
    """
    errors: List[str] = []

    name = str(data.get("scope_name") or "").strip()
    if not name:
        errors.append("Scope name cannot be empty")

    modes = data.get("discovery_modes")
    if not modes or not isinstance(modes, list) or len(modes) == 0:
        errors.append("At least one discovery mode must be selected")
    else:
        allowed_modes = {m.value for m in DiscoveryMode}
        for m in modes:
            if m not in allowed_modes:
                errors.append(f"Invalid discovery mode: '{m}'")

    markets = data.get("markets")
    if not markets or not isinstance(markets, list) or len(markets) == 0:
        errors.append("At least one market must be selected")
    else:
        allowed_markets = {m.lower() for m in SUPPORTED_MARKETS}
        for m in markets:
            if str(m).strip().lower() not in allowed_markets:
                errors.append(f"Unsupported market: '{m}'")

    max_results = data.get("max_results")
    try:
        max_int = int(max_results)
        if max_int <= 0:
            errors.append("Max results must be greater than 0")
    except (TypeError, ValueError):
        errors.append("Max results must be a valid positive integer")

    source_ids = data.get("source_ids") or []
    if registry and source_ids:
        for sid in source_ids:
            if not registry.get_source(sid):
                errors.append(f"Unknown source ID: '{sid}'")

    rel_types = data.get("relationship_types") or []
    for rt in rel_types:
        if rt not in SUPPORTED_RELATIONSHIP_TYPES:
            errors.append(f"Unknown relationship type: '{rt}'")

    opp_types = data.get("opportunity_types") or []
    for ot in opp_types:
        if ot not in SUPPORTED_OPPORTUNITY_TYPES:
            errors.append(f"Unknown opportunity type: '{ot}'")

    tiers = data.get("company_tiers") or []
    for t in tiers:
        if t not in SUPPORTED_COMPANY_TIERS:
            errors.append(f"Unknown company tier: '{t}'")

    bim_mat = data.get("bim_maturity") or []
    for bm in bim_mat:
        if bm not in SUPPORTED_BIM_MATURITY:
            errors.append(f"Unknown BIM maturity: '{bm}'")

    return errors


# =========================================================================
# DiscoveryScope Model
# =========================================================================

@dataclass
class DiscoveryScope:
    scope_name: str
    scope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    discovery_modes: List[str] = field(default_factory=lambda: ["company_first"])
    markets: List[str] = field(default_factory=lambda: ["Italy"])
    source_ids: List[str] = field(default_factory=list)
    relationship_types: List[str] = field(default_factory=lambda: ["service_buyer"])
    opportunity_types: List[str] = field(default_factory=lambda: ["direct_bim_delivery"])
    company_tiers: List[str] = field(default_factory=lambda: ["tier1", "tier2", "tier3", "unknown"])
    bim_maturity: List[str] = field(default_factory=lambda: ["high", "medium", "low", "unknown"])
    max_results: int = 50
    company_name_keywords: List[str] = field(default_factory=list)
    required_signals: List[str] = field(default_factory=list)
    excluded_keywords: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.scope_id:
            self.scope_id = str(uuid.uuid4())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiscoveryScope:
        known_keys = {
            "scope_id", "scope_name", "discovery_modes", "markets", "source_ids",
            "relationship_types", "opportunity_types", "company_tiers", "bim_maturity",
            "max_results", "company_name_keywords", "required_signals", "excluded_keywords",
            "languages", "notes", "enabled", "created_at", "updated_at",
        }
        filtered = {k: v for k, v in data.items() if k in known_keys}
        return cls(**filtered)


# =========================================================================
# DiscoveryRun Record Model
# =========================================================================

@dataclass
class DiscoveryRun:
    run_id: str
    scope_id: Optional[str] = None
    scope_name: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    status: str = "running"  # running, completed, failed
    markets: List[str] = field(default_factory=list)
    discovery_modes: List[str] = field(default_factory=list)
    sources_requested: List[str] = field(default_factory=list)
    sources_executed: List[str] = field(default_factory=list)
    skipped_sources: List[Dict[str, Any]] = field(default_factory=list)
    results_found: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiscoveryRun:
        known_keys = {
            "run_id", "scope_id", "scope_name", "started_at", "finished_at", "status",
            "markets", "discovery_modes", "sources_requested", "sources_executed",
            "skipped_sources", "results_found", "companies_created", "companies_updated", "errors",
        }
        filtered = {k: v for k, v in data.items() if k in known_keys}
        return cls(**filtered)



# =========================================================================
# DiscoveryScopeStore Persistence
# =========================================================================

class DiscoveryScopeStore:
    """
    Persists DiscoveryScopes and Scope-based DiscoveryRuns in SQLite.
    Safe and idempotent table initialization.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_scopes (
                    scope_id TEXT PRIMARY KEY,
                    scope_name TEXT NOT NULL,
                    discovery_modes_json TEXT NOT NULL,
                    markets_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    relationship_types_json TEXT NOT NULL,
                    opportunity_types_json TEXT NOT NULL,
                    company_tiers_json TEXT NOT NULL,
                    bim_maturity_json TEXT NOT NULL,
                    max_results INTEGER NOT NULL DEFAULT 50,
                    keywords_json TEXT DEFAULT '[]',
                    required_signals_json TEXT DEFAULT '[]',
                    excluded_keywords_json TEXT DEFAULT '[]',
                    languages_json TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS scope_discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    scope_id TEXT,
                    scope_name TEXT DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    markets_json TEXT NOT NULL,
                    discovery_modes_json TEXT NOT NULL,
                    sources_requested_json TEXT NOT NULL,
                    sources_executed_json TEXT NOT NULL,
                    skipped_sources_json TEXT DEFAULT '[]',
                    results_found INTEGER DEFAULT 0,
                    companies_created INTEGER DEFAULT 0,
                    companies_updated INTEGER DEFAULT 0,
                    errors_json TEXT NOT NULL DEFAULT '[]'
                );
            """)
            try:
                conn.execute("ALTER TABLE scope_discovery_runs ADD COLUMN scope_name TEXT DEFAULT ''")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE scope_discovery_runs ADD COLUMN skipped_sources_json TEXT DEFAULT '[]'")
            except Exception:
                pass

    def save_scope(self, scope: DiscoveryScope) -> DiscoveryScope:
        scope.updated_at = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO discovery_scopes (
                    scope_id, scope_name, discovery_modes_json, markets_json,
                    source_ids_json, relationship_types_json, opportunity_types_json,
                    company_tiers_json, bim_maturity_json, max_results,
                    keywords_json, required_signals_json, excluded_keywords_json,
                    languages_json, notes, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.scope_id,
                    scope.scope_name,
                    json.dumps(scope.discovery_modes),
                    json.dumps(scope.markets),
                    json.dumps(scope.source_ids),
                    json.dumps(scope.relationship_types),
                    json.dumps(scope.opportunity_types),
                    json.dumps(scope.company_tiers),
                    json.dumps(scope.bim_maturity),
                    scope.max_results,
                    json.dumps(scope.company_name_keywords),
                    json.dumps(scope.required_signals),
                    json.dumps(scope.excluded_keywords),
                    json.dumps(scope.languages),
                    scope.notes,
                    1 if scope.enabled else 0,
                    scope.created_at,
                    scope.updated_at,
                ),
            )
            return scope

    def get_scope(self, scope_id: str) -> Optional[DiscoveryScope]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM discovery_scopes WHERE scope_id = ?", (scope_id,)).fetchone()
            if not row:
                return None
            return self._row_to_scope(row)

    def list_scopes(self, enabled_only: bool = False) -> List[DiscoveryScope]:
        query = "SELECT * FROM discovery_scopes"
        params: List[Any] = []
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_scope(r) for r in rows]

    def delete_scope(self, scope_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM discovery_scopes WHERE scope_id = ?", (scope_id,))
            return cur.rowcount > 0

    def update_scope(self, scope_id: str, updates: Dict[str, Any]) -> DiscoveryScope:
        existing = self.get_scope(scope_id)
        if not existing:
            raise KeyError(f"DiscoveryScope '{scope_id}' not found")
        
        d = existing.to_dict()
        for k, v in updates.items():
            if k in d and k not in ("scope_id", "created_at"):
                d[k] = v
        d["updated_at"] = datetime.now(timezone.utc).isoformat()
        updated_scope = DiscoveryScope.from_dict(d)
        return self.save_scope(updated_scope)

    def duplicate_scope(self, scope_id: str) -> DiscoveryScope:
        orig = self.get_scope(scope_id)
        if not orig:
            raise KeyError(f"DiscoveryScope '{scope_id}' not found")
        now_iso = datetime.now(timezone.utc).isoformat()
        new_id = f"scope_{uuid.uuid4().hex[:12]}"
        clone = DiscoveryScope(
            scope_id=new_id,
            scope_name=f"{orig.scope_name} (Copy)",
            discovery_modes=list(orig.discovery_modes),
            markets=list(orig.markets),
            source_ids=list(orig.source_ids),
            relationship_types=list(orig.relationship_types),
            opportunity_types=list(orig.opportunity_types),
            company_tiers=list(orig.company_tiers),
            bim_maturity=list(orig.bim_maturity),
            max_results=orig.max_results,
            company_name_keywords=list(orig.company_name_keywords),
            required_signals=list(orig.required_signals),
            excluded_keywords=list(orig.excluded_keywords),
            languages=list(orig.languages),
            notes=orig.notes,
            enabled=orig.enabled,
            created_at=now_iso,
            updated_at=now_iso,
        )
        return self.save_scope(clone)

    def enable_scope(self, scope_id: str) -> DiscoveryScope:
        return self.update_scope(scope_id, {"enabled": True})

    def disable_scope(self, scope_id: str) -> DiscoveryScope:
        return self.update_scope(scope_id, {"enabled": False})

    def save_run(self, run: DiscoveryRun) -> DiscoveryRun:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO scope_discovery_runs (
                    run_id, scope_id, scope_name, started_at, finished_at, status,
                    markets_json, discovery_modes_json, sources_requested_json,
                    sources_executed_json, skipped_sources_json, results_found, companies_created,
                    companies_updated, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.scope_id,
                    run.scope_name or "",
                    run.started_at,
                    run.finished_at,
                    run.status,
                    json.dumps(run.markets),
                    json.dumps(run.discovery_modes),
                    json.dumps(run.sources_requested),
                    json.dumps(run.sources_executed),
                    json.dumps(run.skipped_sources),
                    run.results_found,
                    run.companies_created,
                    run.companies_updated,
                    json.dumps(run.errors),
                ),
            )
            return run

    def get_run(self, run_id: str) -> Optional[DiscoveryRun]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM scope_discovery_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def list_runs(self, scope_id: Optional[str] = None, limit: int = 50) -> List[DiscoveryRun]:
        query = "SELECT * FROM scope_discovery_runs"
        params: List[Any] = []
        if scope_id:
            query += " WHERE scope_id = ?"
            params.append(scope_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_run(r) for r in rows]


    def _row_to_scope(self, r: sqlite3.Row) -> DiscoveryScope:
        return DiscoveryScope(
            scope_id=r["scope_id"],
            scope_name=r["scope_name"],
            discovery_modes=json.loads(r["discovery_modes_json"]),
            markets=json.loads(r["markets_json"]),
            source_ids=json.loads(r["source_ids_json"]),
            relationship_types=json.loads(r["relationship_types_json"]),
            opportunity_types=json.loads(r["opportunity_types_json"]),
            company_tiers=json.loads(r["company_tiers_json"]),
            bim_maturity=json.loads(r["bim_maturity_json"]),
            max_results=int(r["max_results"]),
            company_name_keywords=json.loads(r["keywords_json"] or "[]"),
            required_signals=json.loads(r["required_signals_json"] or "[]"),
            excluded_keywords=json.loads(r["excluded_keywords_json"] or "[]"),
            languages=json.loads(r["languages_json"] or "[]"),
            notes=r["notes"] or "",
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def _row_to_run(self, r: sqlite3.Row) -> DiscoveryRun:
        keys = r.keys()
        s_name = r["scope_name"] if "scope_name" in keys and r["scope_name"] else None
        skips = json.loads(r["skipped_sources_json"]) if "skipped_sources_json" in keys and r["skipped_sources_json"] else []
        return DiscoveryRun(
            run_id=r["run_id"],
            scope_id=r["scope_id"],
            scope_name=s_name,
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            status=r["status"],
            markets=json.loads(r["markets_json"]),
            discovery_modes=json.loads(r["discovery_modes_json"]),
            sources_requested=json.loads(r["sources_requested_json"]),
            sources_executed=json.loads(r["sources_executed_json"]),
            skipped_sources=skips,
            results_found=int(r["results_found"]),
            companies_created=int(r["companies_created"]),
            companies_updated=int(r["companies_updated"]),
            errors=json.loads(r["errors_json"]),
        )


def preview_discovery_plan(
    scope: DiscoveryScope,
    registry: Optional[SourceRegistry] = None,
) -> Dict[str, Any]:
    """
    Calculates an execution plan preview without making any network calls,
    candidate writes, or operational state mutations (PRD-DISCUI-007).
    """
    from sales_engine.discovery_v2.source_registry import SourceRegistry
    reg = registry or SourceRegistry()
    target_markets = {m.strip().lower() for m in scope.markets}
    scope_modes_set = {str(m).strip().lower() for m in scope.discovery_modes}

    executable_sources = []
    skipped_sources = []

    for src_id in scope.source_ids:
        cfg = reg.get_source(src_id)
        if not cfg:
            skipped_sources.append({
                "source_id": src_id,
                "name": src_id,
                "status": "not_registered",
                "reason": f"Source '{src_id}' is not registered in SourceRegistry",
            })
            continue

        s_name = cfg.name or src_id

        # 1. Enabled check
        if not cfg.enabled:
            skipped_sources.append({
                "source_id": src_id,
                "name": s_name,
                "status": "disabled",
                "reason": "Source is disabled in SourceRegistry",
            })
            continue

        # 2. Adapter status check
        if getattr(cfg, "adapter_status", "AVAILABLE") == "NOT_AVAILABLE":
            skipped_sources.append({
                "source_id": src_id,
                "name": s_name,
                "status": "unavailable",
                "reason": f"Adapter '{cfg.adapter_id or 'none'}' is NOT_AVAILABLE",
            })
            continue

        # 3. Credential status check
        cred_status = cfg.get_credential_status()
        if cred_status in ("not_configured", "configuration_error"):
            skipped_sources.append({
                "source_id": src_id,
                "name": s_name,
                "status": "missing_credential",
                "reason": f"Credential not configured (env var: {cfg.credential_env_var or 'none'})",
            })
            continue

        # 4. Market compatibility check
        source_markets = getattr(cfg, "countries", None) or getattr(cfg, "supported_markets", [])
        if source_markets:
            supp_markets = {str(c).strip().lower() for c in source_markets}
            if target_markets and not supp_markets.intersection(target_markets):
                skipped_sources.append({
                    "source_id": src_id,
                    "name": s_name,
                    "status": "unsupported_market",
                    "reason": f"Source does not support requested markets {scope.markets}",
                })
                continue

        # 5. Discovery mode compatibility check
        raw_modes = getattr(cfg, "discovery_mode", None) or getattr(cfg, "supported_discovery_modes", ["company_first"])
        modes_list = raw_modes if isinstance(raw_modes, list) else [str(raw_modes)]
        source_modes_set = {str(m).strip().lower() for m in modes_list}
        if scope_modes_set and not source_modes_set.intersection(scope_modes_set):
            skipped_sources.append({
                "source_id": src_id,
                "name": s_name,
                "status": "unsupported_mode",
                "reason": f"Source does not support requested discovery modes {scope.discovery_modes}",
            })
            continue

        # Executable!
        effective_budget = min(cfg.max_results_per_run, scope.max_results)
        executable_sources.append({
            "source_id": src_id,
            "name": s_name,
            "type": cfg.type,
            "priority": cfg.priority,
            "adapter_id": cfg.adapter_id,
            "credential_status": cred_status,
            "max_results_per_run": cfg.max_results_per_run,
            "effective_budget": effective_budget,
            "markets": cfg.countries,
            "modes": modes_list,
        })

    # Sort executable by priority
    executable_sources.sort(key=lambda x: x["priority"])
    effective_total = min(scope.max_results, sum(s["effective_budget"] for s in executable_sources)) if executable_sources else 0

    return {
        "scope_id": scope.scope_id,
        "scope_name": scope.scope_name,
        "enabled": scope.enabled,
        "markets": scope.markets,
        "discovery_modes": scope.discovery_modes,
        "max_results": scope.max_results,
        "sources_requested": scope.source_ids,
        "executable_sources": executable_sources,
        "skipped_sources": skipped_sources,
        "executable_count": len(executable_sources),
        "skipped_count": len(skipped_sources),
        "effective_total_budget": effective_total,
    }


# =========================================================================
# DiscoveryRunner Service
# =========================================================================

class DiscoveryRunner:
    """
    Executes candidate discovery driven strictly by a DiscoveryScope.
    Coordinates between DiscoveryScopeStore, SourceRegistry, DiscoveryEngineV2, and CandidateStore.
    """

    def __init__(
        self,
        scope_store: DiscoveryScopeStore,
        candidate_store: CandidateStore,
        source_registry: Optional[SourceRegistry] = None,
        discovery_engine: Optional[DiscoveryEngineV2] = None,
        engine: Optional[DiscoveryEngineV2] = None,
    ):
        self.scope_store = scope_store
        self.candidate_store = candidate_store
        self.registry = source_registry or SourceRegistry()
        self.engine = engine or discovery_engine or DiscoveryEngineV2(
            candidate_store=candidate_store,
            source_registry=self.registry,
        )

    def run(
        self,
        scope_id: str,
        fetcher_funcs: Optional[Dict[str, Callable[[str], List[Dict[str, Any]]]]] = None,
        query_override: Optional[str] = None,
    ) -> DiscoveryRun:
        """
        Executes discovery bounded by scope_id.
        """
        scope = self.scope_store.get_scope(scope_id)
        if not scope:
            raise ValueError(f"DiscoveryScope '{scope_id}' does not exist")
        if not scope.enabled:
            raise ValueError(f"DiscoveryScope '{scope.scope_name}' is disabled")

        run_id = f"run_{scope.scope_id}_{uuid.uuid4().hex[:8]}"
        run = DiscoveryRun(
            run_id=run_id,
            scope_id=scope.scope_id,
            scope_name=scope.scope_name,
            started_at=datetime.now(timezone.utc).isoformat(),
            markets=list(scope.markets),
            discovery_modes=list(scope.discovery_modes),
            sources_requested=list(scope.source_ids),
            sources_executed=[],
            skipped_sources=[],
            results_found=0,
            companies_created=0,
            companies_updated=0,
            errors=[],
            status="running",
        )
        self.scope_store.save_run(run)

        fetcher_funcs = fetcher_funcs or {}
        max_allowed = max(1, scope.max_results)
        collected_count = 0

        # Determine sources to execute:
        # Must be in scope.source_ids AND enabled in SourceRegistry
        target_markets = {m.strip().lower() for m in scope.markets}
        scope_modes_set = {str(m).strip().lower() for m in scope.discovery_modes}

        sources_to_run = []
        for src_id in scope.source_ids:
            cfg = self.registry.get_source(src_id)
            if not cfg:
                # If unregistered source, skip with notice (PRD-SOURCE-014)
                msg = f"Source '{src_id}' requested by scope is not registered in SourceRegistry"
                logger.warning(msg)
                run.errors.append(msg)
                run.skipped_sources.append({"source_id": src_id, "name": src_id, "reason": msg})
                continue
            if not cfg.enabled:
                msg = f"Source '{src_id}' is disabled in SourceRegistry; skipping execution"
                logger.info(msg)
                run.errors.append(msg)
                run.skipped_sources.append({"source_id": src_id, "name": cfg.name or src_id, "reason": msg})
                continue

            # Check adapter status (PRD-SOURCE-004)
            if getattr(cfg, "adapter_status", "AVAILABLE") == "NOT_AVAILABLE":
                msg = f"Source '{src_id}' adapter is NOT_AVAILABLE; skipping execution"
                logger.warning(msg)
                run.errors.append(msg)
                run.skipped_sources.append({"source_id": src_id, "name": cfg.name or src_id, "reason": msg})
                continue

            # Check credentials if source requires API key
            cred_status = cfg.get_credential_status()
            if cred_status in ("not_configured", "configuration_error"):
                msg = f"Source '{src_id}' requires credentials which are not configured"
                logger.warning(msg)
                run.errors.append(msg)
                run.skipped_sources.append({"source_id": src_id, "name": cfg.name or src_id, "reason": msg})
                continue

            # Check supported markets compatibility (PRD-SOURCE-009)
            source_markets = getattr(cfg, "countries", None) or getattr(cfg, "supported_markets", [])
            if source_markets:
                supp_set = {str(c).strip().lower() for c in source_markets}
                if target_markets and not supp_set.intersection(target_markets):
                    msg = f"Source '{src_id}' does not support requested markets {scope.markets}; skipping execution"
                    logger.info(msg)
                    run.errors.append(msg)
                    run.skipped_sources.append({"source_id": src_id, "name": cfg.name or src_id, "reason": msg})
                    continue

            # Check supported discovery modes compatibility (PRD-SOURCE-010)
            modes_list = getattr(cfg, "supported_discovery_modes", None) or getattr(cfg, "discovery_mode", ["company_first"])
            modes_list = modes_list if isinstance(modes_list, list) else [str(modes_list)]
            source_modes_set = {str(m).strip().lower() for m in modes_list}
            if scope_modes_set and not source_modes_set.intersection(scope_modes_set):
                msg = f"Source '{src_id}' does not support requested discovery modes {scope.discovery_modes}; skipping execution"
                logger.info(msg)
                run.errors.append(msg)
                run.skipped_sources.append({"source_id": src_id, "name": cfg.name or src_id, "reason": msg})
                continue

            sources_to_run.append(src_id)


        # Query to pass
        query = query_override or (" ".join(scope.company_name_keywords) if scope.company_name_keywords else "BIM")

        try:
            for src_id in sources_to_run:
                if collected_count >= max_allowed:
                    break

                cfg = self.registry.get_source(src_id)
                fetcher = fetcher_funcs.get(src_id)
                if not fetcher and cfg:
                    from sales_engine.discovery_v2.adapters import get_default_adapter_for_source
                    fetcher = get_default_adapter_for_source(cfg)

                if not fetcher:
                    msg = f"Source '{src_id}' has no callable adapter"
                    logger.warning(msg)
                    run.errors.append(msg)
                    continue

                run.sources_executed.append(src_id)

                try:
                    raw_items = fetcher(query)
                except Exception as e:
                    err_msg = f"Fetcher for source '{src_id}' raised error: {e}"
                    logger.error(err_msg)
                    run.errors.append(err_msg)
                    continue

                # Enforce per-source max_results limit (PRD-SOURCE-008)
                source_max = getattr(cfg, "max_results_per_run", 50) if cfg else 50
                source_budget = min(source_max, max_allowed - collected_count)
                source_collected = 0

                for item in raw_items:
                    if collected_count >= max_allowed or source_collected >= source_budget:
                        break

                    c_name = item.get("company_name") or item.get("name")
                    if not c_name:
                        continue

                    # Filter by market: if item specifies a country, it must match selected markets
                    item_country = (item.get("country") or "").strip()
                    if item_country:
                        if target_markets and item_country.lower() not in target_markets:
                            # Exclude company outside selected markets
                            continue
                    else:
                        item_country = scope.markets[0] if scope.markets else ""

                    # Exclude keywords if configured
                    if scope.excluded_keywords:
                        item_text = f"{c_name} {item.get('description', '')}".lower()
                        if any(kw.lower() in item_text for kw in scope.excluded_keywords):
                            continue

                    c_domain = item.get("domain") or item.get("website")
                    canon_domain = self.candidate_store.canonicalize_domain(c_domain) if c_domain else None

                    # Check if candidate already exists
                    existing = self.candidate_store.get_by_domain(canon_domain) if canon_domain else None
                    if not existing:
                        cid = self.candidate_store.generate_company_id(c_name, canon_domain, item_country)
                        existing = self.candidate_store.get_by_id(cid)

                    # Determine primary discovery mode
                    primary_mode = scope.discovery_modes[0] if scope.discovery_modes else "company_first"

                    # Prepare extracted data
                    raw_data = item.get("raw", {})
                    ext_data = {
                        k: v for k, v in item.items()
                        if k not in ("raw", "company_name", "name", "domain", "website")
                    }

                    # INVARIANT: Strict NO FAKE JOBS
                    # Only genuine job_first mode can have job identifiers
                    if primary_mode != "job_first":
                        ext_data.pop("structured_job_id", None)
                        ext_data.pop("job_id", None)
                        ext_data.pop("job_title", None)

                    comp, obs = self.engine.discover_company(
                        company_name=c_name,
                        domain=c_domain,
                        source_name=src_id,
                        discovery_mode=primary_mode,
                        raw_payload=raw_data,
                        extracted_data=ext_data,
                        country=item_country or (scope.markets[0] if scope.markets else None),
                        city=item.get("city"),
                    )

                    collected_count += 1
                    source_collected += 1
                    run.results_found += 1

                    if existing:
                        run.companies_updated += 1
                    else:
                        run.companies_created += 1

            run.status = "completed"
        except Exception as e:
            run.status = "failed"
            run.errors.append(str(e))
            logger.exception(f"Discovery run {run_id} failed: {e}")
        finally:
            run.finished_at = datetime.now(timezone.utc).isoformat()
            self.scope_store.save_run(run)

        return run


def run_discovery(
    scope_id: str,
    db_path: Optional[str] = None,
    fetcher_funcs: Optional[Dict[str, Callable[[str], List[Dict[str, Any]]]]] = None,
) -> DiscoveryRun:
    """
    Public top-level service entrypoint to execute discovery by scope_id.
    """
    import os
    resolved_db = db_path or os.getenv("SALESAI_DB_PATH", os.getenv("SALESAI_CANDIDATES_DB", "data/sales_outreach.db"))
    scope_store = DiscoveryScopeStore(db_path=resolved_db)
    cand_store = CandidateStore(db_path=resolved_db)
    runner = DiscoveryRunner(scope_store=scope_store, candidate_store=cand_store)
    return runner.run(scope_id=scope_id, fetcher_funcs=fetcher_funcs)
