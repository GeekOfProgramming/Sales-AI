"""
Source Registry and Market Configuration for Discovery V2.

Loads configuration from config/discovery_sources.yaml and config/markets.yaml.
Tracks health and metrics per source while ensuring zero secrets exposure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    name: str
    type: str
    priority: int = 100
    rate_limit_per_minute: int = 60
    enabled: bool = True
    discovery_mode: str = "company_first"
    description: str = ""
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
        self.last_error_message = error
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
                        name = cfg.get("id") or cfg.get("name")
                        if not name:
                            continue
                        sc = SourceConfig(
                            name=name,
                            type=cfg.get("type", "unknown"),
                            priority=cfg.get("priority", 100),
                            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
                            enabled=cfg.get("enabled", True),
                            discovery_mode=cfg.get("discovery_mode", "company_first"),
                            description=cfg.get("description", cfg.get("notes", "")),
                            params=cfg.get("params", {}),
                        )
                        self.sources[name] = sc
                        self.health_map[name] = SourceHealth(name=name)
                elif isinstance(sources_data, dict):
                    for name, cfg in sources_data.items():
                        sc = SourceConfig(
                            name=name,
                            type=cfg.get("type", "unknown"),
                            priority=cfg.get("priority", 100),
                            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 60),
                            enabled=cfg.get("enabled", True),
                            discovery_mode=cfg.get("discovery_mode", "company_first"),
                            description=cfg.get("description", cfg.get("notes", "")),
                            params=cfg.get("params", {}),
                        )
                        self.sources[name] = sc
                        self.health_map[name] = SourceHealth(name=name)
            except Exception as e:
                logger.error(f"Error loading discovery sources config: {e}")

        if self.markets_path.exists():
            try:
                with open(self.markets_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self.markets = data.get("markets", {})
            except Exception as e:
                logger.error(f"Error loading markets config: {e}")

    def get_source(self, name: str) -> Optional[SourceConfig]:
        return self.sources.get(name)

    def list_sources(self, enabled_only: bool = True) -> List[SourceConfig]:
        if enabled_only:
            return [s for s in self.sources.values() if s.enabled]
        return list(self.sources.values())

    def get_market(self, market_code: str) -> Optional[Dict[str, Any]]:
        return self.markets.get(market_code)

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
