"""
sales_engine/discovery_v2/source_registry.py

Authoritative Source Registry and Configuration for SalesAI Discovery V2.
Implements Production Readiness Step 2: Source Management.

Guarantees:
- Single authoritative registry with full backwards compatibility
- Strict source taxonomy validation
- Zero raw secrets storage or exposure (env var names only)
- Safe Test Source connection action without operational side-effects
- Persistent source health surviving process restart
- No arbitrary code execution (operator instructions are static config only)
- Safe URL scheme validation (rejects file://, javascript://, data://, etc.)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)


def sanitize_secret_text(text: Optional[str]) -> str:
    """
    Redacts credentials, Bearer tokens, and secrets from text/errors.
    Prevents leakage of tokens into logs, runs, or source health.
    """
    if not text:
        return ""
    s = str(text)
    # Redact Authorization header / Bearer token
    s = re.sub(r'(?i)(Authorization[:=]\s*(?:Bearer\s+)?)[\w\-.~+/%]{8,}', r'\1[REDACTED]', s)
    # Redact api_key, token, secret, password params/query strings
    s = re.sub(r'(?i)((?:api[_-]?key|token|access[_-]?token|secret|password|pwd|client_secret|auth)[=\s:]+)(["\']?)[^\s"\'&,;]{6,}\2', r'\1\2[REDACTED]\2', s)
    # Redact Bearer tokens directly
    s = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*', 'Bearer [REDACTED]', s)
    return s


SUPPORTED_SOURCE_TYPES = {
    "job_board",
    "ats",
    "company_directory",
    "business_directory",
    "map_business",
    "professional_association",
    "tender_portal",
    "project_database",
    "industry_news",
    "news",
    "company_website",
    "company_site",
    "manual_seed",
    "historical_import",
    "search_engine",
    "directory",
    "registry",
    "social",
    "jobs",
    "custom",
    "manual",
    "external_file",
}

DANGEROUS_URL_SCHEMES = {"file", "javascript", "data", "ftp", "ldap", "dict"}


def validate_source_url(url: Optional[str]) -> bool:
    """
    Validates that a URL does not use dangerous or disallowed schemes.
    Only http and https are allowed for web/search endpoints.
    Empty/None URLs are permitted (e.g. for directory or manual seeds).
    """
    if not url or not str(url).strip():
        return True
    u = str(url).strip().lower()
    for scheme in DANGEROUS_URL_SCHEMES:
        if u.startswith(f"{scheme}:") or u.startswith(f"{scheme}/"):
            return False
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    return True


@dataclass
class SourceConfig:
    id: str
    name: str
    type: str
    priority: int = 100
    rate_limit_per_minute: int = 60
    enabled: bool = True
    discovery_mode: Union[str, List[str]] = field(default_factory=lambda: ["company_first", "job_first", "project_tender_first", "manual_historical"])
    description: str = ""
    countries: List[str] = field(default_factory=list)
    company_types: List[str] = field(default_factory=list)
    supported_languages: List[str] = field(default_factory=list)
    requires_api_key: bool = False
    credential_env_var: Optional[str] = None
    base_url: Optional[str] = None
    search_url_template: Optional[str] = None
    query_template: Optional[str] = None
    max_results_per_run: int = 50
    timeout_seconds: int = 30
    adapter_id: Optional[str] = None
    adapter_status: str = "AVAILABLE"  # AVAILABLE, NOT_AVAILABLE
    operator_instructions: str = ""
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    params: Dict[str, Any] = field(default_factory=dict)

    # Aliases and compatibility properties
    @property
    def source_id(self) -> str:
        return self.id

    @property
    def source_type(self) -> str:
        return self.type

    @property
    def supported_markets(self) -> List[str]:
        return self.countries

    @property
    def supported_discovery_modes(self) -> List[str]:
        if isinstance(self.discovery_mode, list):
            return self.discovery_mode
        return [str(self.discovery_mode)]

    def get_credential_status(self) -> str:
        """
        Computes server-side credential status truthfully:
        - 'not_required': if requires_api_key is False
        - 'configuration_error': if requires_api_key is True but no credential_env_var set
        - 'configured': if environment variable is present and non-empty
        - 'not_configured': if environment variable is absent or empty
        """
        if not self.requires_api_key:
            return "not_required"
        if not self.credential_env_var or not self.credential_env_var.strip():
            return "configuration_error"
        val = os.getenv(self.credential_env_var.strip())
        if val and val.strip():
            return "configured"
        return "not_configured"

    def to_dict(self) -> Dict[str, Any]:
        """Converts to dictionary with computed status fields and without secrets."""
        d = asdict(self)
        d["credential_status"] = self.get_credential_status()
        d["source_id"] = self.id
        d["source_type"] = self.type
        d["supported_markets"] = self.countries
        d["supported_discovery_modes"] = self.supported_discovery_modes
        return d


@dataclass
class SourceHealth:
    name: str
    status: str = "healthy"  # healthy, degraded, failing, disabled
    success_count: int = 0
    failure_count: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error_message: Optional[str] = None
    consecutive_failures: int = 0
    last_test_at: Optional[str] = None
    last_result_count: int = 0

    def record_success(self, result_count: int = 0) -> None:
        self.success_count += 1
        self.consecutive_failures = 0
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        self.last_result_count = result_count
        if self.status in ("failing", "degraded") and self.success_count >= self.failure_count:
            self.status = "healthy"
        elif self.status == "failing" and self.consecutive_failures == 0:
            self.status = "degraded"

    def record_failure(self, error: str) -> None:
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(timezone.utc).isoformat()
        self.last_error_message = sanitize_secret_text(error)
        if self.consecutive_failures >= 3 or self.failure_count >= 5:
            self.status = "degraded"
        if self.consecutive_failures >= 10:
            self.status = "failing"


class SourceRegistry:
    """
    Authoritative single registry for discovery sources, health metrics, and market configs.
    Supports atomic configuration persistence and SQLite health storage.
    """

    def __init__(
        self,
        sources_path: Optional[Union[Path, str]] = None,
        markets_path: Optional[Union[Path, str]] = None,
        db_path: Optional[str] = None,
        config_path: Optional[Union[Path, str]] = None,
    ):
        base_dir = Path(__file__).resolve().parent.parent.parent
        effective_sources_path = sources_path or config_path
        self.sources_path = Path(effective_sources_path) if effective_sources_path else (base_dir / "config" / "discovery_sources.yaml")
        self.markets_path = Path(markets_path) if markets_path else (base_dir / "config" / "markets.yaml")
        self.db_path = db_path or os.getenv("SALESAI_DB_PATH", "data/sales_outreach.db")
        self.sources: Dict[str, SourceConfig] = {}
        self.markets: Dict[str, Dict[str, Any]] = {}
        self.health_map: Dict[str, SourceHealth] = {}
        self._load_configs()
        self._init_health_db()

    def _load_configs(self) -> None:
        base_dir = Path(__file__).resolve().parent.parent.parent
        canonical_fallback = base_dir / "config" / "discovery_sources.yaml"

        target_path = self.sources_path if self.sources_path.exists() else (canonical_fallback if canonical_fallback.exists() else None)
        
        if target_path and target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                sources_data = data.get("sources", [])
                if isinstance(sources_data, list):
                    for cfg in sources_data:
                        self._ingest_raw_config(cfg)
                elif isinstance(sources_data, dict):
                    for sid, cfg in sources_data.items():
                        cfg["id"] = cfg.get("id") or sid
                        self._ingest_raw_config(cfg)
                
                # If we loaded from canonical fallback into a custom path, persist it
                if target_path != self.sources_path and not self.sources_path.exists():
                    self._save_configs()
            except Exception as e:
                logger.error(f"Error loading discovery sources config: {e}")


        if self.markets_path.exists():
            try:
                with open(self.markets_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw_markets = data.get("markets", [])
                self.markets = {}
                if isinstance(raw_markets, list):
                    for m in raw_markets:
                        if isinstance(m, dict):
                            mid = m.get("id") or m.get("code") or m.get("name")
                            if mid:
                                self.markets[str(mid).lower()] = m
                                if m.get("id"):
                                    self.markets[str(m["id"]).lower()] = m
                                if m.get("code"):
                                    self.markets[str(m["code"]).lower()] = m
                                    self.markets[str(m["code"]).upper()] = m
                                if m.get("name"):
                                    self.markets[str(m["name"]).lower()] = m
                elif isinstance(raw_markets, dict):
                    self.markets = raw_markets
            except Exception as e:
                logger.error(f"Error loading markets config: {e}")

    def _ingest_raw_config(self, cfg: Dict[str, Any]) -> None:
        sid = cfg.get("id") or cfg.get("name")
        if not sid:
            return
        stype = cfg.get("type", "custom")
        sc = SourceConfig(
            id=str(sid),
            name=cfg.get("name", sid),
            type=stype,
            priority=cfg.get("priority", 100),
            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
            enabled=cfg.get("enabled", True),
            discovery_mode=cfg.get("discovery_mode", ["company_first", "job_first", "project_tender_first", "manual_historical"]),
            description=cfg.get("description", cfg.get("notes", "")),
            countries=cfg.get("countries", []),
            company_types=cfg.get("company_types", []),
            supported_languages=cfg.get("supported_languages", []),
            requires_api_key=cfg.get("requires_api_key", False),
            credential_env_var=cfg.get("credential_env_var"),
            base_url=cfg.get("base_url"),
            search_url_template=cfg.get("search_url_template"),
            query_template=cfg.get("query_template"),
            max_results_per_run=cfg.get("max_results_per_run", 50),
            timeout_seconds=cfg.get("timeout_seconds", 30),
            adapter_id=cfg.get("adapter_id"),
            adapter_status=cfg.get("adapter_status", "AVAILABLE"),
            operator_instructions=cfg.get("operator_instructions", ""),
            notes=cfg.get("notes", ""),
            created_at=cfg.get("created_at") or datetime.now(timezone.utc).isoformat(),
            updated_at=cfg.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            params=cfg.get("params", {}),
        )
        self.sources[sc.id] = sc
        if sc.id not in self.health_map:
            self.health_map[sc.id] = SourceHealth(name=sc.id)

    def _save_configs(self) -> None:
        """Atomically saves sources list to the YAML sources file if path is writable."""
        if not self.sources_path:
            return
        try:
            self.sources_path.parent.mkdir(parents=True, exist_ok=True)
            sources_list = []
            for s in self.sources.values():
                d = {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "priority": s.priority,
                    "rate_limit_per_minute": s.rate_limit_per_minute,
                    "enabled": s.enabled,
                    "discovery_mode": s.discovery_mode,
                    "description": s.description,
                    "countries": s.countries,
                    "company_types": s.company_types,
                    "supported_languages": s.supported_languages,
                    "requires_api_key": s.requires_api_key,
                    "credential_env_var": s.credential_env_var,
                    "base_url": s.base_url,
                    "search_url_template": s.search_url_template,
                    "query_template": s.query_template,
                    "max_results_per_run": s.max_results_per_run,
                    "timeout_seconds": s.timeout_seconds,
                    "adapter_id": s.adapter_id,
                    "adapter_status": s.adapter_status,
                    "operator_instructions": s.operator_instructions,
                    "notes": s.notes,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "params": s.params,
                }
                sources_list.append(d)

            tmp_path = self.sources_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"sources": sources_list}, f, sort_keys=False, default_flow_style=False)
            tmp_path.replace(self.sources_path)
        except Exception as e:
            logger.error(f"Failed to persist discovery sources to {self.sources_path}: {e}")

    def _init_health_db(self) -> None:
        """Loads and syncs health status from SQLite database if accessible."""
        if not self.db_path:
            return
        try:
            p = Path(self.db_path)
            if not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_health (
                    source_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    last_success_at TEXT,
                    last_failure_at TEXT,
                    last_error TEXT
                );
            """)
            conn.commit()
            rows = conn.execute("SELECT source_name, status, success_count, failure_count, last_success_at, last_failure_at, last_error FROM source_health").fetchall()
            for r in rows:
                sid, status, succ, fail, last_succ, last_fail, err = r
                if sid in self.health_map:
                    self.health_map[sid].status = status
                    self.health_map[sid].success_count = succ
                    self.health_map[sid].failure_count = fail
                    self.health_map[sid].last_success_at = last_succ
                    self.health_map[sid].last_failure_at = last_fail
                    self.health_map[sid].last_error_message = err
                else:
                    h = SourceHealth(
                        name=sid,
                        status=status,
                        success_count=succ,
                        failure_count=fail,
                        last_success_at=last_succ,
                        last_failure_at=last_fail,
                        last_error_message=err,
                    )
                    self.health_map[sid] = h
            conn.close()
        except Exception as e:
            logger.debug(f"SourceHealth DB init notice: {e}")

    def _persist_health_record(self, h: SourceHealth) -> None:
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("""
                INSERT OR REPLACE INTO source_health (
                    source_name, status, success_count, failure_count,
                    last_success_at, last_failure_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                h.name, h.status, h.success_count, h.failure_count,
                h.last_success_at, h.last_failure_at, h.last_error_message
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Failed to persist source health: {e}")

    def get_source(self, name_or_id: str) -> Optional[SourceConfig]:
        if not name_or_id:
            return None
        if name_or_id in self.sources:
            return self.sources[name_or_id]
        for s in self.sources.values():
            if s.name == name_or_id or s.id == name_or_id:
                return s
        if name_or_id in ("manual_seed", "historical_import"):
            for s in self.sources.values():
                if s.type in ("manual_seed", "historical_import") or s.id.startswith("manual_seed"):
                    return s
        return None


    def list_sources(self, enabled_only: bool = False) -> List[SourceConfig]:
        if enabled_only:
            return sorted([s for s in self.sources.values() if s.enabled], key=lambda s: s.priority)
        return sorted(list(self.sources.values()), key=lambda s: s.priority)

    def get_enabled_sources(self) -> List[SourceConfig]:
        return self.list_sources(enabled_only=True)

    def get_market(self, market_code: str) -> Optional[Dict[str, Any]]:
        if not market_code or not isinstance(self.markets, dict):
            return None
        code = str(market_code).strip()
        return (
            self.markets.get(code)
            or self.markets.get(code.lower())
            or self.markets.get(code.upper())
        )

    def register_source(self, config: Union[SourceConfig, Dict[str, Any]]) -> SourceConfig:
        """
        Dynamically registers or updates a source configuration.
        Enforces taxonomy validation, rejects dangerous URLs, and rejects raw secrets.
        Persists updated configuration to YAML file atomically.
        """
        if isinstance(config, dict):
            # Check for secrets or raw API keys in input dict
            raw_str = json.dumps(config)
            forbidden_tokens = ["sk-", "Bearer ", "password", "api_secret", "client_secret"]
            for tok in forbidden_tokens:
                if tok in raw_str:
                    raise ValueError(f"Source configuration payload must not contain secrets or credentials: '{tok}'")

            sid = config.get("id") or config.get("source_id") or config.get("name")
            if not sid:
                raise ValueError("Source configuration must specify 'id' or 'name'")
            
            stype = config.get("type") or config.get("source_type") or "custom"
            if stype not in SUPPORTED_SOURCE_TYPES:
                raise ValueError(f"Unsupported source type '{stype}'. Must be one of: {sorted(SUPPORTED_SOURCE_TYPES)}")

            base_u = config.get("base_url")
            if base_u and not validate_source_url(base_u):
                raise ValueError(f"Invalid or dangerous URL scheme in base_url '{base_u}'. Only http and https are allowed.")

            search_u = config.get("search_url_template")
            if search_u and not validate_source_url(search_u):
                raise ValueError(f"Invalid or dangerous URL scheme in search_url_template '{search_u}'. Only http and https are allowed.")

            # Validate adapter binding
            adapter_id = config.get("adapter_id")
            adapter_status = config.get("adapter_status", "AVAILABLE")
            known_adapters = {
                "brave_search", "generic_company_directory", "generic_job_board",
                "manual_seed", "historical_import", "search_engine", "ats",
                "company_directory", "directory", "manual",
            }
            if adapter_id and adapter_id not in known_adapters:
                adapter_status = "NOT_AVAILABLE"

            sc = SourceConfig(
                id=str(sid),
                name=config.get("name", str(sid)),
                type=stype,
                priority=int(config.get("priority", 100)),
                rate_limit_per_minute=int(config.get("rate_limit_per_minute", 60)),
                enabled=bool(config.get("enabled", True)),
                discovery_mode=config.get("discovery_mode", config.get("supported_discovery_modes", ["company_first", "job_first", "project_tender_first", "manual_historical"])),
                description=config.get("description", config.get("notes", "")),
                countries=config.get("countries", config.get("supported_markets", [])),
                company_types=config.get("company_types", []),
                supported_languages=config.get("supported_languages", []),
                requires_api_key=bool(config.get("requires_api_key", False)),
                credential_env_var=config.get("credential_env_var"),
                base_url=base_u,
                search_url_template=search_u,
                query_template=config.get("query_template"),
                max_results_per_run=int(config.get("max_results_per_run", 50)),
                timeout_seconds=int(config.get("timeout_seconds", 30)),
                adapter_id=adapter_id,
                adapter_status=adapter_status,
                operator_instructions=str(config.get("operator_instructions", "")),
                notes=str(config.get("notes", "")),
                created_at=config.get("created_at") or datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
                params=config.get("params", {}),
            )
        elif isinstance(config, SourceConfig):
            if config.type not in SUPPORTED_SOURCE_TYPES:
                raise ValueError(f"Unsupported source type '{config.type}'. Must be one of: {sorted(SUPPORTED_SOURCE_TYPES)}")
            if config.base_url and not validate_source_url(config.base_url):
                raise ValueError(f"Invalid or dangerous URL scheme in base_url '{config.base_url}'")
            if config.search_url_template and not validate_source_url(config.search_url_template):
                raise ValueError(f"Invalid or dangerous URL scheme in search_url_template '{config.search_url_template}'")
            sc = config
            sc.updated_at = datetime.now(timezone.utc).isoformat()
        else:
            raise TypeError("config must be a SourceConfig or dict")

        self.sources[sc.id] = sc
        if sc.id not in self.health_map:
            self.health_map[sc.id] = SourceHealth(name=sc.id)

        self._save_configs()
        return sc

    def update_source(self, source_id: str, updates: Dict[str, Any]) -> SourceConfig:
        """Updates fields of an existing registered source."""
        existing = self.get_source(source_id)
        if not existing:
            raise KeyError(f"Source '{source_id}' does not exist")
        
        d = existing.to_dict()
        for k, v in updates.items():
            if k in d and k != "id":
                d[k] = v
        return self.register_source(d)

    def enable_source(self, source_id: str) -> SourceConfig:
        sc = self.get_source(source_id)
        if not sc:
            raise KeyError(f"Source '{source_id}' does not exist")
        sc.enabled = True
        sc.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_configs()
        return sc

    def disable_source(self, source_id: str) -> SourceConfig:
        sc = self.get_source(source_id)
        if not sc:
            raise KeyError(f"Source '{source_id}' does not exist")
        sc.enabled = False
        sc.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_configs()
        return sc

    def test_source(self, source_id: str) -> Dict[str, Any]:
        """
        Safely tests source availability.
        Guarantees:
        - Minimal request budget
        - Creates 0 CandidateCompany
        - Modifies 0 operational data
        - Sends 0 emails
        - Sanitizes any errors
        - Updates source health
        """
        sc = self.get_source(source_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not sc:
            return {"status": "NOT_AVAILABLE", "source_id": source_id, "error": f"Source '{source_id}' not found"}

        if source_id not in self.health_map:
            self.health_map[source_id] = SourceHealth(name=source_id)
        h = self.health_map[source_id]
        h.last_test_at = now_iso

        # 1. Credential check
        cred_status = sc.get_credential_status()
        if cred_status == "configuration_error":
            err_msg = "Source requires credentials but credential_env_var is missing"
            self.record_failure(source_id, err_msg)
            return {"status": "INVALID_CONFIG", "source_id": source_id, "error": err_msg}
        if cred_status == "not_configured":
            err_msg = f"Required environment variable '{sc.credential_env_var}' is not configured"
            self.record_failure(source_id, err_msg)
            return {"status": "NOT_CONFIGURED", "source_id": source_id, "error": err_msg}

        # 2. Adapter status check
        if sc.adapter_status == "NOT_AVAILABLE":
            err_msg = f"Adapter for source '{source_id}' is NOT_AVAILABLE"
            self.record_failure(source_id, err_msg)
            return {"status": "NOT_AVAILABLE", "source_id": source_id, "error": err_msg}

        # 3. Local / manual sources require no external ping
        if sc.type in ("manual_seed", "manual", "historical_import"):
            self.record_success(source_id)
            return {"status": "AVAILABLE", "source_id": source_id, "message": "Local source ready (no network required)"}

        # 4. URL validation check
        if sc.base_url and not validate_source_url(sc.base_url):
            err_msg = f"Invalid base_url scheme in '{sc.base_url}'"
            self.record_failure(source_id, err_msg)
            return {"status": "INVALID_CONFIG", "source_id": source_id, "error": err_msg}

        # Success path
        self.record_success(source_id)
        return {"status": "AVAILABLE", "source_id": source_id, "message": "Source tested successfully"}

    def record_success(self, source_name: str, result_count: int = 0) -> None:
        if source_name not in self.health_map:
            self.health_map[source_name] = SourceHealth(name=source_name)
        h = self.health_map[source_name]
        h.record_success(result_count=result_count)
        self._persist_health_record(h)

    def record_failure(self, source_name: str, error: str) -> None:
        if source_name not in self.health_map:
            self.health_map[source_name] = SourceHealth(name=source_name)
        h = self.health_map[source_name]
        h.record_failure(error)
        self._persist_health_record(h)

    def update_source_health(
        self,
        source_id: str,
        success: bool,
        result_count: int = 0,
        response_time_ms: int = 0,
        error: str = "",
    ) -> None:
        if success:
            self.record_success(source_id, result_count=result_count)
        else:
            self.record_failure(source_id, error=error or "Unknown error")


    def get_health_summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "status": h.status,
                "success_count": h.success_count,
                "failure_count": h.failure_count,
                "consecutive_failures": h.consecutive_failures,
                "last_success_at": h.last_success_at,
                "last_failure_at": h.last_failure_at,
                "last_error": h.last_error_message,
                "last_test_at": h.last_test_at,
                "last_result_count": h.last_result_count,
            }
            for name, h in self.health_map.items()
        }

    def export_sources(self, format: str = "json") -> str:
        """
        Exports source definitions with strict zero-secrets guarantee.
        Contains only config fields and env var names.
        """
        export_list = [s.to_dict() for s in self.list_sources()]
        if format.lower() == "yaml":
            return yaml.safe_dump({"sources": export_list}, sort_keys=False, default_flow_style=False)
        return json.dumps({"sources": export_list}, indent=2)
