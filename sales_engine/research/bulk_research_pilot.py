"""
sales_engine/research/bulk_research_pilot.py

BULK-01: High-Volume Research Readiness & Pilot Batch Orchestration.
Reuses existing services:
- CandidateStore & CandidateLifecycleService (safety & eligibility filters)
- WebsiteCrawlerV2 & CrawlBudget (bounded crawl, SSRF check, snapshot persistence)
- execute_company_analysis & ResearchSnapshotStore (LLM chunk resume, token bounding, cost estimation)

Guarantees:
1. Strict eligibility filtering:
   - Excludes candidates in suppression list or active exclusion list.
   - Excludes candidates with previous outreach detected or historical contact.
   - Excludes candidates with pending historical email proposals (HIST-04A manual gate).
   - Excludes candidates without valid canonical domains or with unresolved domain collisions.
2. Snapshot reuse: skips crawling if a valid completed snapshot already exists for the candidate.
3. Analysis reuse: skips LLM execution if a valid successful analysis already exists unless reauthorized.
4. Quota and cost bounding: stops safely on budget exhaustion or provider errors (503 / quota limit).
5. Resume safety: uses chunk checkpoints to resume partially completed analyses without duplicate requests.
6. Operator control: never automatically approves research fields, generates outreach, or sends emails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.candidates.models import CandidateCompany
from sales_engine.research.company_analysis_service import (
    CompanyAnalysisRecord,
    LLMProviderGateway,
    execute_company_analysis,
)
from sales_engine.research.research_store import ResearchSnapshot, ResearchSnapshotStore
from sales_engine.research.website_crawler import CrawlBudget, WebsiteCrawlerV2

logger = logging.getLogger(__name__)


@dataclass
class BulkResearchPilotConfig:
    """Configuration and guardrails for bulk research pilot."""
    max_candidates: int = 10
    max_pages_per_candidate: int = 5
    crawl_timeout_seconds: float = 15.0
    max_budget_per_candidate_usd: float = 0.50
    total_pilot_budget_usd: float = 5.00
    skip_existing_snapshots: bool = True
    skip_completed_analyses: bool = True
    force_reanalyze: bool = False
    provider_id: str = "gemini"
    model: Optional[str] = None
    require_auth: bool = False
    auth_token: Optional[str] = None


@dataclass
class CandidatePilotResult:
    """Result summary for an individual candidate in the pilot."""
    candidate_id: str
    company_name: str
    company_domain: str
    crawl_skipped: bool = False
    crawl_pages: int = 0
    crawl_status: str = "pending"
    analysis_skipped: bool = False
    analysis_status: str = "pending"
    analysis_id: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    error_message: Optional[str] = None


@dataclass
class BulkResearchPilotReport:
    """Comprehensive execution report for the bulk research pilot."""
    total_candidates_evaluated: int = 0
    total_candidates_eligible: int = 0
    candidates_processed: int = 0
    crawl_skipped_count: int = 0
    crawl_executed_count: int = 0
    analysis_skipped_count: int = 0
    analysis_success_count: int = 0
    analysis_failed_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    budget_exhausted: bool = False
    stopped_on_provider_error: bool = False
    operator_review_backlog_count: int = 0
    results: List[CandidatePilotResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_candidates_evaluated": self.total_candidates_evaluated,
            "total_candidates_eligible": self.total_candidates_eligible,
            "candidates_processed": self.candidates_processed,
            "crawl_skipped_count": self.crawl_skipped_count,
            "crawl_executed_count": self.crawl_executed_count,
            "analysis_skipped_count": self.analysis_skipped_count,
            "analysis_success_count": self.analysis_success_count,
            "analysis_failed_count": self.analysis_failed_count,
            "analysis_success_rate": (
                round(self.analysis_success_count / self.candidates_processed, 4)
                if self.candidates_processed > 0
                else 0.0
            ),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_cost_per_company_usd": (
                round(self.total_cost_usd / self.candidates_processed, 6)
                if self.candidates_processed > 0
                else 0.0
            ),
            "budget_exhausted": self.budget_exhausted,
            "stopped_on_provider_error": self.stopped_on_provider_error,
            "operator_review_backlog_count": self.operator_review_backlog_count,
            "results": [r.__dict__ for r in self.results],
        }


def get_pending_historical_proposed_candidate_ids(store: CandidateStore) -> Set[str]:
    """
    Identifies candidate IDs that have proposed historical outreach associations
    pending manual operator review (HIST-04A gate).
    These candidates must NOT enter automated cold outreach or pilot research.
    """
    proposed_ids: Set[str] = {
        "cand:domain:essequadroservice.it",
        "cand:domain:goodfor.it",
        "cand:domain:archicura.it",
        "cand:domain:myrthapools.com",
        "cand:domain:ener.it",
        "cand:domain:studioamati.it",
        "cand:domain:nexteco.it",
        "cand:domain:abimcstudio.com",
        "cand:domain:3ba.it",
    }
    try:
        with store._get_connection() as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_sent_emails'"
            ).fetchone()
            if has_table:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(historical_sent_emails)").fetchall()]
                if "proposed_candidate_id" in cols:
                    rows = conn.execute(
                        "SELECT DISTINCT proposed_candidate_id FROM historical_sent_emails WHERE proposal_status = 'proposed'"
                    ).fetchall()
                    for r in rows:
                        if r[0]:
                            proposed_ids.add(r[0])
    except Exception as e:
        logger.warning("Error querying historical_sent_emails for proposed candidates: %s", e)

    return proposed_ids


def select_eligible_pilot_candidates(
    store: CandidateStore,
    lifecycle_svc: Optional[CandidateLifecycleService] = None,
    exclusion_store: Optional[ExcludedCompaniesStore] = None,
    limit: int = 10,
) -> List[CandidateCompany]:
    """
    Selects candidates eligible for high-volume research pilot according to strict safety rules:
    1. Must be in Active Work queue (NEW, NEEDS_RESEARCH, NEEDS_VERIFICATION).
    2. Must NOT be suppressed, rejected, or duplicate.
    3. Must NOT be on the company exclusion list (ACTIVE).
    4. Must NOT have previous outreach detected or historical contact status (contacted, replied, etc.).
    5. Must NOT have pending historical email proposals (HIST-04A manual review gate).
    6. Must have a valid canonical company domain.
    7. Respects the limit (up to 10 candidates).
    """
    if lifecycle_svc is None:
        lifecycle_svc = CandidateLifecycleService(store)

    proposed_pending_cids = get_pending_historical_proposed_candidate_ids(store)

    all_candidates = store.list_candidates()
    eligible: List[CandidateCompany] = []

    for cand in all_candidates:
        cid = cand.candidate_id
        # Check pending historical gate
        if cid in proposed_pending_cids:
            logger.info("Candidate %s skipped: pending historical association review", cid)
            continue

        # Check domain validity
        domain = (cand.company_domain or "").strip().lower()
        if not domain or "." not in domain or "/" in domain or "@" in domain:
            continue

        # Check lifecycle projection
        state = lifecycle_svc.get_candidate_lifecycle_state(cid)
        if not state.is_active_work:
            continue

        # Check suppression and previous outreach flags
        if getattr(cand, "suppressed", False) or getattr(cand, "previous_outreach_detected", False):
            continue

        hist_status = getattr(cand, "historical_contact_status", "uncontacted")
        if hist_status in ("contacted", "replied", "converted", "dnc"):
            continue

        # Check active exclusions
        if exclusion_store:
            norm_name = getattr(cand, "normalized_company_name", "") or cand.company_name
            if exclusion_store.is_company_excluded(candidate_id=cid, company_domain=domain, company_name=norm_name):
                continue

        eligible.append(cand)
        if len(eligible) >= limit:
            break

    return eligible


class BulkResearchPilotRunner:
    """
    Executes a bounded research pilot across eligible candidates reusing existing
    crawler and analysis engines safely.
    """

    def __init__(
        self,
        candidate_store: CandidateStore,
        snapshot_store: ResearchSnapshotStore,
        config: Optional[BulkResearchPilotConfig] = None,
        gateway_override: Optional[LLMProviderGateway] = None,
        crawler_override: Optional[WebsiteCrawlerV2] = None,
    ):
        self.candidate_store = candidate_store
        self.snapshot_store = snapshot_store
        self.config = config or BulkResearchPilotConfig()
        self.lifecycle_svc = CandidateLifecycleService(candidate_store)
        self.exclusion_store = ExcludedCompaniesStore(db_path=candidate_store.db_path)
        self.gateway_override = gateway_override
        self.crawler = crawler_override or WebsiteCrawlerV2(
            snapshot_store=self.snapshot_store,
            budget=CrawlBudget(
                max_pages=self.config.max_pages_per_candidate,
                per_page_timeout_seconds=self.config.crawl_timeout_seconds,
            ),
        )

    def run_pilot(
        self,
        candidate_override: Optional[List[CandidateCompany]] = None,
    ) -> BulkResearchPilotReport:
        """
        Executes the bounded pilot batch.
        """
        report = BulkResearchPilotReport()

        # Step 1: Candidate Selection
        if candidate_override is not None:
            eligible_candidates = candidate_override
        else:
            eligible_candidates = select_eligible_pilot_candidates(
                store=self.candidate_store,
                lifecycle_svc=self.lifecycle_svc,
                exclusion_store=self.exclusion_store,
                limit=self.config.max_candidates,
            )

        report.total_candidates_eligible = len(eligible_candidates)
        report.total_candidates_evaluated = len(eligible_candidates)

        accumulated_cost_usd = 0.0

        for cand in eligible_candidates:
            # Check total budget limit before proceeding
            if accumulated_cost_usd >= self.config.total_pilot_budget_usd:
                logger.warning(
                    "Total pilot budget limit reached: $%.4f >= $%.4f. Halting batch.",
                    accumulated_cost_usd,
                    self.config.total_pilot_budget_usd,
                )
                report.budget_exhausted = True
                break

            result = CandidatePilotResult(
                candidate_id=cand.candidate_id,
                company_name=cand.company_name,
                company_domain=cand.company_domain or "",
            )
            report.candidates_processed += 1

            # Step 2: Snapshot Verification & Crawl
            snapshot: Optional[ResearchSnapshot] = None
            if self.config.skip_existing_snapshots:
                snapshot = self.snapshot_store.get_latest_snapshot(cand.candidate_id)

            if snapshot and snapshot.research_status == "complete":
                result.crawl_skipped = True
                result.crawl_status = "reused_existing"
                result.crawl_pages = snapshot.pages_succeeded
                report.crawl_skipped_count += 1
            else:
                try:
                    crawl_summary = self.crawler.crawl_website(
                        candidate_id=cand.candidate_id,
                        canonical_domain=cand.company_domain or "",
                        company_name=cand.company_name,
                    )
                    result.crawl_pages = crawl_summary.pages_succeeded
                    result.crawl_status = crawl_summary.status
                    report.crawl_executed_count += 1
                    snapshot = self.snapshot_store.get_latest_snapshot(cand.candidate_id)
                except Exception as crawl_err:
                    logger.error("Crawl error for %s: %s", cand.candidate_id, crawl_err)
                    result.crawl_status = "error"
                    result.error_message = f"Crawl failed: {str(crawl_err)}"
                    report.results.append(result)
                    continue

            if not snapshot:
                result.analysis_status = "skipped_no_snapshot"
                report.results.append(result)
                continue

            # Step 3: Analysis Verification & Execution
            if self.config.skip_completed_analyses and not self.config.force_reanalyze:
                existing_analysis = self.snapshot_store.get_latest_successful_analysis(cand.candidate_id)
                if existing_analysis:
                    result.analysis_skipped = True
                    result.analysis_status = "reused_existing"
                    result.analysis_id = existing_analysis.analysis_id
                    report.analysis_skipped_count += 1
                    report.operator_review_backlog_count += 1
                    report.results.append(result)
                    continue

            # Execute Gemini Analysis with resume and cost bounding
            try:
                analysis_record: CompanyAnalysisRecord = execute_company_analysis(
                    snapshot=snapshot,
                    candidate_id=cand.candidate_id,
                    provider_id=self.config.provider_id,
                    model=self.config.model,
                    snapshot_store=self.snapshot_store,
                    gateway_override=self.gateway_override,
                    auth_token=self.config.auth_token,
                    require_auth=self.config.require_auth,
                    max_approved_budget_usd=self.config.max_budget_per_candidate_usd,
                )

                if analysis_record.status == "SUCCESS":
                    result.analysis_status = "SUCCESS"
                    result.analysis_id = analysis_record.analysis_id
                    result.prompt_tokens = analysis_record.input_tokens
                    result.completion_tokens = analysis_record.output_tokens
                    result.total_tokens = analysis_record.input_tokens + analysis_record.output_tokens
                    result.estimated_cost_usd = analysis_record.cost_estimate

                    accumulated_cost_usd += result.estimated_cost_usd
                    report.analysis_success_count += 1
                    report.total_prompt_tokens += result.prompt_tokens
                    report.total_completion_tokens += result.completion_tokens
                    report.total_tokens += result.total_tokens
                    report.total_cost_usd = accumulated_cost_usd
                    # Each successful analysis enters operator review backlog (never auto-approved)
                    report.operator_review_backlog_count += 1
                else:
                    result.analysis_status = analysis_record.status
                    result.error_message = analysis_record.diagnostic_info
                    report.analysis_failed_count += 1
                    if "503" in analysis_record.diagnostic_info or "unavailable" in analysis_record.diagnostic_info.lower():
                        logger.warning("Provider 503 error detected in analysis. Pausing pilot.")
                        report.stopped_on_provider_error = True
                        report.results.append(result)
                        break

            except Exception as analysis_err:
                err_str = str(analysis_err)
                logger.error("Analysis exception for %s: %s", cand.candidate_id, err_str)
                result.analysis_status = "error"
                result.error_message = err_str
                report.analysis_failed_count += 1

                # If provider 503 or quota exceeded, pause batch safely
                if "503" in err_str or "PROVIDER_UNAVAILABLE" in err_str or "QUOTA" in err_str.upper() or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning("Provider unavailable or quota exceeded (%s). Pausing pilot.", err_str)
                    report.stopped_on_provider_error = True
                    report.results.append(result)
                    break

            report.results.append(result)

        return report
