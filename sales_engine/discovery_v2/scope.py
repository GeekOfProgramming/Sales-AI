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
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    status: str = "running"  # running, completed, failed
    markets: List[str] = field(default_factory=list)
    discovery_modes: List[str] = field(default_factory=list)
    sources_requested: List[str] = field(default_factory=list)
    sources_executed: List[str] = field(default_factory=list)
    results_found: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiscoveryRun:
        known_keys = {
            "run_id", "scope_id", "started_at", "finished_at", "status",
            "markets", "discovery_modes", "sources_requested", "sources_executed",
            "results_found", "companies_created", "companies_updated", "errors",
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
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    markets_json TEXT NOT NULL,
                    discovery_modes_json TEXT NOT NULL,
                    sources_requested_json TEXT NOT NULL,
                    sources_executed_json TEXT NOT NULL,
                    results_found INTEGER DEFAULT 0,
                    companies_created INTEGER DEFAULT 0,
                    companies_updated INTEGER DEFAULT 0,
                    errors_json TEXT NOT NULL DEFAULT '[]'
                );
            """)

    def save_scope(self, scope: DiscoveryScope) -> bool:
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

    def save_run(self, run: DiscoveryRun) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO scope_discovery_runs (
                    run_id, scope_id, started_at, finished_at, status,
                    markets_json, discovery_modes_json, sources_requested_json,
                    sources_executed_json, results_found, companies_created,
                    companies_updated, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.scope_id,
                    run.started_at,
                    run.finished_at,
                    run.status,
                    json.dumps(run.markets),
                    json.dumps(run.discovery_modes),
                    json.dumps(run.sources_requested),
                    json.dumps(run.sources_executed),
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
        return DiscoveryRun(
            run_id=r["run_id"],
            scope_id=r["scope_id"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            status=r["status"],
            markets=json.loads(r["markets_json"]),
            discovery_modes=json.loads(r["discovery_modes_json"]),
            sources_requested=json.loads(r["sources_requested_json"]),
            sources_executed=json.loads(r["sources_executed_json"]),
            results_found=int(r["results_found"]),
            companies_created=int(r["companies_created"]),
            companies_updated=int(r["companies_updated"]),
            errors=json.loads(r["errors_json"]),
        )


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
            raise ValueError(f"DiscoveryScope '{scope_id}' is disabled")

        run_id = f"run_{scope.scope_id}_{uuid.uuid4().hex[:8]}"
        run = DiscoveryRun(
            run_id=run_id,
            scope_id=scope.scope_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            markets=list(scope.markets),
            discovery_modes=list(scope.discovery_modes),
            sources_requested=list(scope.source_ids),
            sources_executed=[],
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
        sources_to_run = []
        for src_id in scope.source_ids:
            cfg = self.registry.get_source(src_id)
            if not cfg:
                # If unregistered source, skip with notice
                msg = f"Source '{src_id}' requested by scope is not registered in SourceRegistry"
                logger.warning(msg)
                run.errors.append(msg)
                continue
            if not cfg.enabled:
                logger.info(f"Source '{src_id}' is disabled in SourceRegistry; skipping execution")
                continue
            sources_to_run.append(src_id)

        # Normalize markets (case-insensitive mapping to allowed country names/codes)
        target_markets = {m.strip().lower() for m in scope.markets}

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

                for item in raw_items:
                    if collected_count >= max_allowed:
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
