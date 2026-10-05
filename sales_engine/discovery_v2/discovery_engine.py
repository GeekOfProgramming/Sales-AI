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
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryStatus,
    MarketEvidence,
    OpportunityType,
    RelationshipType,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.discovery_v2.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryRunRecord:
    run_id: str
    mode: str
    source_name: str
    started_at: str
    completed_at: Optional[str] = None
    status: str = "running"  # running, completed, partial_failure, failed
    candidates_discovered: int = 0
    candidates_updated: int = 0
    observations_created: int = 0
    errors: List[str] = field(default_factory=list)


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
        extracted_data = extracted_data or {}
        canon_domain = self.store.canonicalize_domain(domain) if domain else None

        # Lookup or generate
        company = None
        if canon_domain:
            company = self.store.get_by_domain(canon_domain)

        company_id = company.company_id if company else self.store.generate_company_id(company_name, canon_domain)

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
            source_name=source_name,
            discovery_mode=discovery_mode,
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
        """
        run_id = f"run_{discovery_mode}_{int(datetime.now(timezone.utc).timestamp())}"
        run_rec = DiscoveryRunRecord(
            run_id=run_id,
            mode=discovery_mode,
            source_name=",".join(sources),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self.runs.append(run_rec)

        fetcher_funcs = fetcher_funcs or {}

        for src in sources:
            cfg = self.registry.get_source(src)
            if cfg and not cfg.enabled:
                logger.info(f"Skipping disabled source: {src}")
                continue

            fetcher = fetcher_funcs.get(src)
            if not fetcher:
                # Default mock/noop fetcher if none passed in
                continue

            try:
                items = fetcher(query)
                for item in items:
                    c_name = item.get("company_name") or item.get("name")
                    if not c_name:
                        continue
                    c_domain = item.get("domain") or item.get("website")
                    country = item.get("country")
                    city = item.get("city")
                    raw_data = item.get("raw", {})
                    ext_data = {k: v for k, v in item.items() if k not in ("raw", "company_name", "name", "domain", "website")}

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
                    run_rec.observations_created += 1
                    run_rec.candidates_discovered += 1

                self.registry.record_success(src)
            except Exception as e:
                err_msg = f"Source {src} failed during discovery: {str(e)}"
                logger.error(err_msg)
                run_rec.errors.append(err_msg)
                self.registry.record_failure(src, str(e))
                # Partial failure: Do NOT re-raise; allow remaining sources to proceed!

        run_rec.completed_at = datetime.now(timezone.utc).isoformat()
        if run_rec.errors and run_rec.candidates_discovered > 0:
            run_rec.status = "partial_failure"
        elif run_rec.errors:
            run_rec.status = "failed"
        else:
            run_rec.status = "completed"

        return run_rec
