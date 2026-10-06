"""
Discovery Engine V2 for SalesAI Phase 10.

Coordinates multi-source candidate discovery across four modes:
1. job_first (real job signals)
2. company_first (registries, directories, direct web)
3. project_tender_first (projects, public tenders)
4. manual_seed (curated manual lists)

Guarantees:
- Strict NO FAKE JOBS rule: only real job evidence creates StructuredJob records.
  Company-first, tender-first, and manual seeds never create StructuredJob.
- Company identity deduplication: canonical domain-driven. Different domains = separate companies.
  No fuzzy merging by name alone.
- Partial failure resilience: errors in one source do not discard results from other sources.
- Repeated discovery idempotency: deterministic runs do not duplicate companies or observations.
- Absolute Zero-Send guarantee: Discovery never triggers or executes email sends.
- Persistent runs and source health: all runs and source health metrics stored in SQLite CandidateStore.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryRunRecord,
    DiscoveryStatus,
    MarketEvidence,
    OpportunityType,
    RelationshipType,
    SourceHealthRecord,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.discovery_v2.source_registry import SourceRegistry, sanitize_secret_text

logger = logging.getLogger(__name__)


class DiscoveryEngineV2:
    """
    Core engine for multi-source candidate discovery.
    """

    def __init__(
        self,
        candidate_store: CandidateStore,
        classifier: Optional[CompanyClassifier] = None,
        source_registry: Optional[SourceRegistry] = None,
    ):
        self.store = candidate_store
        self.classifier = classifier or CompanyClassifier()
        self.registry = source_registry or SourceRegistry()
        self.runs: List[DiscoveryRunRecord] = []

    def discover_company(
        self,
        company_name: str,
        domain: Optional[str] = None,
        source_name: str = "manual_seed",
        discovery_mode: str = "company_first",
        raw_payload: Optional[Dict[str, Any]] = None,
        extracted_data: Optional[Dict[str, Any]] = None,
        country: Optional[str] = None,
        city: Optional[str] = None,
    ) -> Tuple[CandidateCompany, DiscoveryObservation]:
        """
        Ingest a discovered company from any source.
        Enforces canonical domain deduplication and observation recording.
        """
        raw_payload = raw_payload or {}
        extracted_data = dict(extracted_data or {})
        # Strip synthetic job fields if not genuine job-first mode
        if discovery_mode != "job_first":
            extracted_data.pop("structured_job_id", None)
            extracted_data.pop("job_id", None)

        canon_domain = self.store.canonicalize_domain(domain) if domain else None

        # Lookup or generate
        company = None
        if canon_domain:
            company = self.store.get_by_domain(canon_domain)

        company_id = company.company_id if company else self.store.generate_company_id(company_name, canon_domain, country)

        if not company:
            company = self.store.get_by_id(company_id)

        if not company:
            company = CandidateCompany(
                company_id=company_id,
                name=company_name,
                canonical_domain=canon_domain,
                country=country or extracted_data.get("country"),
                city=city or extracted_data.get("city"),
                discovery_sources=[source_name],
                status=DiscoveryStatus.discovered,
            )

        # Update metadata/aliases
        if company_name not in company.aliases:
            company.aliases.append(company_name)
        if source_name not in company.discovery_sources:
            company.discovery_sources.append(source_name)

        # Observation idempotency hash
        payload_hash = hashlib.sha256(
            f"{company_id}:{source_name}:{discovery_mode}:{sorted(extracted_data.items())}".encode("utf-8")
        ).hexdigest()[:16]
        obs_id = f"obs_{source_name}_{payload_hash}"

        obs = DiscoveryObservation(
            observation_id=obs_id,
            company_id=company.company_id,
            source_id=source_name,
            source_name=source_name,
            source_type=self.registry.get_source(source_name).type if self.registry.get_source(source_name) else "web_source",
            discovery_mode=discovery_mode,
            observed_company_name=company_name,
            observed_domain=canon_domain,
            country=country or company.country,
            city=city or company.city,
            raw_payload=raw_payload,
            extracted_data=extracted_data,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

        saved_company = self.store.upsert_company(company, observation=obs)
        return saved_company, obs

    def run_multi_source_discovery(
        self,
        sources: List[str],
        query: str,
        fetcher_funcs: Optional[Dict[str, Callable[[str], List[Dict[str, Any]]]]] = None,
        discovery_mode: str = "company_first",
    ) -> DiscoveryRunRecord:
        """
        Execute discovery across multiple registered sources with partial-failure isolation.
        Persists runs and source health metrics to SQLite CandidateStore.
        """
        # Unique timestamp + hex to avoid collision even in rapid parallel tests
        unique_suffix = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
        run_id = f"run_{discovery_mode}_{unique_suffix}"
        run_rec = DiscoveryRunRecord(
            run_id=run_id,
            mode=discovery_mode,
            source_name=",".join(sources),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self.runs.append(run_rec)
        self.store.save_discovery_run(run_rec)

        fetcher_funcs = fetcher_funcs or {}

        for src in sources:
            cfg = self.registry.get_source(src)
            if cfg and not cfg.enabled:
                logger.info(f"Skipping disabled source: {src}")
                continue

            # Check credentials if source requires API key
            if cfg and cfg.requires_api_key:
                env_var = cfg.credential_env_var or f"{cfg.id.upper()}_API_KEY"
                import os
                if not os.environ.get(env_var):
                    err_msg = f"Source {src} requires credential '{env_var}' which is not set"
                    logger.warning(err_msg)
                    run_rec.errors.append(err_msg)
                    self.registry.record_failure(src, err_msg)
                    continue

            fetcher = fetcher_funcs.get(src)
            if not fetcher:
                # Try built-in default adapter
                if cfg:
                    from sales_engine.discovery_v2.adapters import get_default_adapter_for_source
                    fetcher = get_default_adapter_for_source(cfg)
                if not fetcher:
                    err_msg = f"Source {src} has no registered adapter"
                    logger.warning(err_msg)
                    run_rec.errors.append(err_msg)
                    self.registry.record_failure(src, err_msg)
                    continue

            try:
                raw_items = fetcher(query)
                # Enforce max_results_per_run contract
                max_results = cfg.max_results_per_run if cfg and cfg.max_results_per_run else 50
                items = raw_items[:max_results]

                for item in items:
                    c_name = item.get("company_name") or item.get("name")
                    if not c_name:
                        continue
                    c_domain = item.get("domain") or item.get("website")
                    country = item.get("country")
                    city = item.get("city")
                    raw_data = item.get("raw", {})
                    ext_data = {k: v for k, v in item.items() if k not in ("raw", "company_name", "name", "domain", "website")}

                    # Check if candidate already exists
                    canon = self.store.canonicalize_domain(c_domain) if c_domain else None
                    existing = self.store.get_by_domain(canon) if canon else None
                    if not existing:
                        cid = self.store.generate_company_id(c_name, canon, country)
                        existing = self.store.get_by_id(cid)

                    # NO FAKE JOBS check:
                    # If this is not a job-first mode, never attach job IDs or fake jobs!
                    if discovery_mode != "job_first":
                        ext_data.pop("structured_job_id", None)
                        ext_data.pop("job_id", None)

                    comp, obs = self.discover_company(
                        company_name=c_name,
                        domain=c_domain,
                        source_name=src,
                        discovery_mode=discovery_mode,
                        raw_payload=raw_data,
                        extracted_data=ext_data,
                        country=country,
                        city=city,
                    )
                    run_rec.observations_found += 1
                    # Check whether observation was genuinely newly added
                    is_new_obs = self.store.add_observation(obs)
                    if is_new_obs:
                        run_rec.observations_created += 1

                    run_rec.candidates_discovered += 1

                    if existing:
                        run_rec.known_candidates += 1
                        run_rec.updated_candidates += 1
                        run_rec.candidates_updated += 1
                    else:
                        run_rec.new_candidates += 1

                    # Count outcome categories
                    if comp.status == DiscoveryStatus.watchlist:
                        run_rec.watchlist_candidates += 1
                    elif comp.status == DiscoveryStatus.qualified:
                        run_rec.qualified_candidates += 1
                    elif comp.status == DiscoveryStatus.rejected:
                        run_rec.rejected_candidates += 1

                self.registry.record_success(src)
            except Exception as e:
                clean_err = sanitize_secret_text(str(e))
                err_msg = f"Source {src} failed during discovery: {clean_err}"
                logger.error(err_msg)
                run_rec.errors.append(err_msg)
                self.registry.record_failure(src, clean_err)
                # Partial failure: Do NOT re-raise; allow remaining sources to proceed!

        run_rec.completed_at = datetime.now(timezone.utc).isoformat()
        if run_rec.errors and run_rec.observations_found > 0:
            run_rec.status = "partial_failure"
        elif run_rec.errors:
            run_rec.status = "failed"
        else:
            run_rec.status = "completed"

        # Persist run and source health to SQLite CandidateStore
        self.store.save_discovery_run(run_rec)
        for s_name, s_health in self.registry.health_map.items():
            self.store.save_source_health(SourceHealthRecord(
                source_name=s_health.name,
                status=s_health.status,
                success_count=s_health.success_count,
                failure_count=s_health.failure_count,
                last_success_at=s_health.last_success_at,
                last_failure_at=s_health.last_failure_at,
                last_error=s_health.last_error_message,
            ))

        return run_rec
