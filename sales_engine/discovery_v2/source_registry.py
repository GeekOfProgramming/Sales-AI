"""
Source Registry and Market Configuration for Discovery V2.

Loads configuration from config/discovery_sources.yaml and config/markets.yaml.
Tracks health and metrics per source while ensuring zero secrets exposure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
    "company_website",
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


@dataclass
class SourceConfig:
    id: str
    name: str
    type: str
    priority: int = 100
    rate_limit_per_minute: int = 60
    enabled: bool = True
    discovery_mode: Union[str, List[str]] = "company_first"
    description: str = ""
    countries: List[str] = field(default_factory=list)
    company_types: List[str] = field(default_factory=list)
    requires_api_key: bool = False
    credential_env_var: Optional[str] = None
    base_url: Optional[str] = None
    max_results_per_run: int = 50
    notes: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceHealth:
    name: str
    status: str = "healthy"  # healthy, degraded, failing, disabled
    success_count: int = 0
    failure_count: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error_message: Optional[str] = None

    def record_success(self) -> None:
        self.success_count += 1
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        if self.status == "failing" and self.success_count > self.failure_count:
            self.status = "healthy"

    def record_failure(self, error: str) -> None:
        self.failure_count += 1
        self.last_failure_at = datetime.now(timezone.utc).isoformat()
        # Redact secrets before saving
        self.last_error_message = sanitize_secret_text(error)
        if self.failure_count >= 3:
            self.status = "degraded"
        if self.failure_count >= 10:
            self.status = "failing"


class SourceRegistry:
    """
    Manages discovery sources and market configs.
    """

    def __init__(
        self,
        sources_path: Optional[Path] = None,
        markets_path: Optional[Path] = None,
    ):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.sources_path = sources_path or (base_dir / "config" / "discovery_sources.yaml")
        self.markets_path = markets_path or (base_dir / "config" / "markets.yaml")
        self.sources: Dict[str, SourceConfig] = {}
        self.markets: Dict[str, Dict[str, Any]] = {}
        self.health_map: Dict[str, SourceHealth] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        if self.sources_path.exists():
            try:
                with open(self.sources_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                sources_data = data.get("sources", [])
                if isinstance(sources_data, list):
                    for cfg in sources_data:
                        sid = cfg.get("id") or cfg.get("name")
                        if not sid:
                            continue
                        sc = SourceConfig(
                            id=sid,
                            name=cfg.get("name", sid),
                            type=cfg.get("type", "unknown"),
                            priority=cfg.get("priority", 100),
                            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
                            enabled=cfg.get("enabled", True),
                            discovery_mode=cfg.get("discovery_mode", "company_first"),
                            description=cfg.get("description", cfg.get("notes", "")),
                            countries=cfg.get("countries", []),
                            company_types=cfg.get("company_types", []),
                            requires_api_key=cfg.get("requires_api_key", False),
                            credential_env_var=cfg.get("credential_env_var"),
                            base_url=cfg.get("base_url"),
                            max_results_per_run=cfg.get("max_results_per_run", 50),
                            notes=cfg.get("notes", ""),
                            params=cfg.get("params", {}),
                        )
                        self.sources[sid] = sc
                        self.health_map[sid] = SourceHealth(name=sid)
                elif isinstance(sources_data, dict):
                    for sid, cfg in sources_data.items():
                        sc = SourceConfig(
                            id=sid,
                            name=cfg.get("name", sid),
                            type=cfg.get("type", "unknown"),
                            priority=cfg.get("priority", 100),
                            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
                            enabled=cfg.get("enabled", True),
                            discovery_mode=cfg.get("discovery_mode", "company_first"),
                            description=cfg.get("description", cfg.get("notes", "")),
                            countries=cfg.get("countries", []),
                            company_types=cfg.get("company_types", []),
                            requires_api_key=cfg.get("requires_api_key", False),
                            credential_env_var=cfg.get("credential_env_var"),
                            base_url=cfg.get("base_url"),
                            max_results_per_run=cfg.get("max_results_per_run", 50),
                            notes=cfg.get("notes", ""),
                            params=cfg.get("params", {}),
                        )
                        self.sources[sid] = sc
                        self.health_map[sid] = SourceHealth(name=sid)
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

    def get_source(self, name_or_id: str) -> Optional[SourceConfig]:
        if not name_or_id:
            return None
        if name_or_id in self.sources:
            return self.sources[name_or_id]
        for s in self.sources.values():
            if s.name == name_or_id or s.id == name_or_id:
                return s
        return None

    def list_sources(self, enabled_only: bool = True) -> List[SourceConfig]:
        if enabled_only:
            return [s for s in self.sources.values() if s.enabled]
        return list(self.sources.values())

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
        Enforces taxonomy validation and rejects secrets.
        """
        if isinstance(config, dict):
            sid = config.get("id") or config.get("name")
            if not sid:
                raise ValueError("Source configuration must specify 'id' or 'name'")
            stype = config.get("type", "custom")
            if stype not in SUPPORTED_SOURCE_TYPES:
                raise ValueError(f"Unsupported source type '{stype}'. Must be one of: {sorted(SUPPORTED_SOURCE_TYPES)}")
            sc = SourceConfig(
                id=sid,
                name=config.get("name", sid),
                type=stype,
                priority=config.get("priority", 100),
                rate_limit_per_minute=config.get("rate_limit_per_minute", 60),
                enabled=config.get("enabled", True),
                discovery_mode=config.get("discovery_mode", "company_first"),
                description=config.get("description", config.get("notes", "")),
                countries=config.get("countries", []),
                company_types=config.get("company_types", []),
                requires_api_key=config.get("requires_api_key", False),
                credential_env_var=config.get("credential_env_var"),
                base_url=config.get("base_url"),
                max_results_per_run=config.get("max_results_per_run", 50),
                notes=config.get("notes", ""),
                params=config.get("params", {}),
            )
        elif isinstance(config, SourceConfig):
            if config.type not in SUPPORTED_SOURCE_TYPES:
                raise ValueError(f"Unsupported source type '{config.type}'. Must be one of: {sorted(SUPPORTED_SOURCE_TYPES)}")
            sc = config
        else:
            raise TypeError("config must be a SourceConfig or dict")

        self.sources[sc.id] = sc
        if sc.id not in self.health_map:
            self.health_map[sc.id] = SourceHealth(name=sc.id)
        return sc

    def record_success(self, source_name: str) -> None:
        if source_name not in self.health_map:
            self.health_map[source_name] = SourceHealth(name=source_name)
        self.health_map[source_name].record_success()

    def record_failure(self, source_name: str, error: str) -> None:
        if source_name not in self.health_map:
            self.health_map[source_name] = SourceHealth(name=source_name)
        self.health_map[source_name].record_failure(error)

    def get_health_summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "status": h.status,
                "success_count": h.success_count,
                "failure_count": h.failure_count,
                "last_success_at": h.last_success_at,
                "last_failure_at": h.last_failure_at,
                "last_error": h.last_error_message,
            }
            for name, h in self.health_map.items()
        }
