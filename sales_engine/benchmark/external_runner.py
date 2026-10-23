"""
sales_engine/benchmark/external_runner.py
SalesAI S3.0 — External LLM Benchmark Runner & Paid-Dispatch Safety Gates.
Evaluates external Tier 1 candidate models across the 7 canonical Gateway task/subtask paths
prior to modifying live operational call sites.

Guarantees:
1. Offline-first execution: default --dry-run mode uses deterministic mocked provider.
2. Paid-dispatch gate: live commercial API calls strictly disabled in S3.0.
3. Architecture reuse: wraps LLMProviderGateway, RuntimeGuard, BudgetLedger, and LLMOutputValidator.
4. Candidate identity & budget binding: enforces exact candidate matching and preflight budget accounting.
5. Sequential Tier 1 evaluation: tests candidate #1, stopping on qualification; moves to #2..#4 only if needed.
6. Reference answer isolation: gold labels / expected answers are held solely by the local scorer.
7. Segregated scorecards: distinct syntactic, semantic, safety, and operational failure metrics.
8. Safety precedence: hard safety disqualifiers override high syntactic scores.
9. Operator boundary: model recommendations are marked PROPOSED ONLY, never production approved.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import httpx

from sales_engine.llm_gateway.contracts import (
    LLMRequest,
    LLMResponse,
    ModelTier,
    sanitize_log_str,
)
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
    LLMRoutingIntegrityError,
)
from sales_engine.llm_gateway.interface import BaseLLMProvider, LLMProviderGateway
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.llm_gateway.model_catalog import (
    ModelCandidate,
    ModelCatalog,
    PricingMetadata,
    normalize_task_id,
    validate_subtask_id,
)
from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    ReservationStatus,
)
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.output_validation import (
    LLMOutputValidator,
    OutputValidationResult,
)
from sales_engine.benchmark.fixtures_s3 import (
    BenchmarkFixtureCase,
    get_all_s3_fixtures,
    get_fixtures_for_task,
)
from sales_engine.benchmark.ground_truth_evaluator import (
    GroundTruthEvaluator,
    GroundTruthEvaluationResult,
    SCORER_VERSION,
)
from sales_engine.benchmark.provider_catalog import (
    OPENAI_SPEC,
    GEMINI_SPEC,
    GROQ_SPEC,
    XAI_SPEC,
    get_provider_spec,
    get_candidate_spec,
    list_supported_providers,
    list_supported_candidates,
)

# S3.2 Verified Live Model Pricing for standard short-context gpt-6-luna (verified 2026-10-22)
TIER1_LIVE_LUNA_PRICING = OPENAI_SPEC.pricing


@dataclass
class BenchmarkCaseExecutionResult:
    """Individual test case execution result and diagnostics."""

    case_id: str
    task_id: str
    subtask_id: Optional[str]
    group: str  # GROUNDED or ADVERSARIAL
    candidate_id: str
    provider_id: str
    exact_model_id: str
    latency_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    syntactic_valid: bool = False
    semantic_valid: bool = False
    safety_valid: bool = False
    overall_pass: bool = False
    schema_pass: bool = False
    ground_truth_pass: bool = False
    evidence_pass: bool = False
    safety_pass: bool = False
    needs_review: bool = False
    failure_categories: List[str] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)
    grounding_evidence_found: List[str] = field(default_factory=list)
    forbidden_tokens_found: List[str] = field(default_factory=list)
    raw_output_snippet: str = ""
    validation_safe_log: Dict[str, Any] = field(default_factory=dict)
    sqlite_reservation_status: Optional[str] = None
    spend_provenance: Optional[str] = None
    estimated_spend_usd: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateTaskSummary:
    """Aggregated scorecard for a candidate on a specific task/subtask."""

    candidate_id: str
    task_id: str
    subtask_id: Optional[str]
    provider_id: str
    exact_model_id: str
    cases_evaluated: int = 0
    cases_passed: int = 0
    syntactic_pass_rate: float = 0.0
    semantic_pass_rate: float = 0.0
    safety_pass_rate: float = 0.0
    overall_pass_rate: float = 0.0
    schema_pass_rate: float = 0.0
    ground_truth_pass_rate: float = 0.0
    evidence_pass_rate: float = 0.0
    safety_violations_count: int = 0
    needs_review_count: int = 0
    avg_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    qualified: bool = False
    is_smoke_only: bool = False
    disqualification_reasons: List[str] = field(default_factory=list)
    case_results: List[BenchmarkCaseExecutionResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["case_results"] = [c.to_dict() for c in self.case_results]
        return d


@dataclass
class BenchmarkRunReport:
    """Complete summary of an external benchmark runner session."""

    run_id: str
    timestamp: str
    is_dry_run: bool
    tasks_evaluated: List[str]
    candidate_summaries: List[CandidateTaskSummary]
    recommended_candidates: Dict[str, Dict[str, Any]]
    total_cases_run: int = 0
    total_cost_usd: float = 0.0
    scorer_version: str = SCORER_VERSION
    fixture_catalog_version: str = "s3.0-catalog-v1"
    pricing_source_mode: str = "offline_mock"
    is_smoke_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "is_dry_run": self.is_dry_run,
            "tasks_evaluated": self.tasks_evaluated,
            "candidate_summaries": [s.to_dict() for s in self.candidate_summaries],
            "recommended_candidates": self.recommended_candidates,
            "total_cases_run": self.total_cases_run,
            "total_cost_usd": self.total_cost_usd,
            "scorer_version": self.scorer_version,
            "fixture_catalog_version": self.fixture_catalog_version,
            "pricing_source_mode": self.pricing_source_mode,
            "is_smoke_only": self.is_smoke_only,
        }


class MockBenchmarkProvider(BaseLLMProvider):
    """
    Deterministic offline mocked provider for S3.0 benchmark execution.
    Generates realistic responses without network activity.
    Simulates high quality for Tier 1 #1, and specific failure modes for adversarial fixtures.
    """

    def __init__(self, provider_id_val: str = "mock_provider"):
        self._provider_id = provider_id_val
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        model_id = request.model or "mock-model"

        # Check candidate behavior simulation
        # In a benchmark, mock responses are constructed to mirror actual task schemas
        # without referencing hidden gold labels in the prompt.
        content = self._generate_mock_content(request)

        input_toks = max(len(request.prompt) // 4, 25)
        output_toks = max(len(content) // 4, 30)

        return LLMResponse(
            content=content,
            provider=self._provider_id,
            model=model_id,
            task_id=request.task_id,
            request_id=request.request_id,
            input_tokens=input_toks,
            output_tokens=output_toks,
            latency_ms=120,
            finish_reason="stop",
        )

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        return self.generate(request)

    def _generate_mock_content(self, request: LLMRequest) -> str:
        task = request.task_id
        meta = request.metadata or {}
        subtask = meta.get("subtask_id")
        case_id = meta.get("case_id", "")
        candidate_id = meta.get("candidate_id", "t1_c1")

        # Deterministic mock data lookup for high-performing Tier 1 simulation
        mock_web_a = {
            "web_a_01": ("Milano MEP Ingegneria S.r.l.", ["Autodesk Revit", "Navisworks", "Solibri"]),
            "web_a_02": ("Torino Laser Scan S.r.l.", ["LiDAR", "Scan-to-BIM"]),
            "web_a_03": ("Studio Strutture Roma", ["Tekla Structures", "SAP2000"]),
            "web_a_04": ("Atelier BIM Bolzano", ["openBIM", "IFC"]),
            "web_a_05": ("Genova InfraBIM S.r.l.", ["Civil 3D"]),
            "web_a_06": ("Atlas Strutture S.r.l.", ["Tekla Structures"]),
            "web_a_07": ("Studio Edile Como", ["Autodesk Revit"]),
            "web_a_08": ("BIM Retail Software S.r.l.", ["Retail Software"]),
            "web_a_09": ("Studio Progetti Varese", ["CAD"]),
            "web_a_10": ("Ingegneria Navale Trieste", ["Rhino"]),
        }

        mock_inbox = {
            "inbox_01": "positive_interest",
            "inbox_02": "referral",
            "inbox_03": "question",
            "inbox_04": "not_interested",
            "inbox_05": "auto_reply",
            "inbox_06": "unsubscribe",
            "inbox_07": "unsubscribe",
            "inbox_08": "not_interested",
            "inbox_09": "unsubscribe",
            "inbox_10": "unknown",
        }

        mock_web_b = {
            "web_b_01": ["MEP clash resolution bottlenecks"],
            "web_b_02": ["Point cloud to Revit conversion workflow"],
            "web_b_03": ["Seismic calculation and structural modeling"],
            "web_b_04": ["IFC model validation and timber coordination"],
            "web_b_05": ["Linear infrastructure alignment coordination"],
            "web_b_06": ["Junior drafter"],
            "web_b_07": ["CAD coordinator"],
            "web_b_08": ["Revit drafter"],
            "web_b_09": ["Modeler"],
            "web_b_10": ["Site surveyor"],
        }

        # Handle specific simulated behaviors for testing failure modes:
        # If candidate is t1_c3 (a lower performing budget candidate), simulate occasional hallucination on adversarial cases
        if candidate_id == "t1_c3" and meta.get("group") == "ADVERSARIAL":
            if "web_a" in case_id:
                return json.dumps({
                    "company_name": "Studio Pi",
                    "company_summary": "Studio Pi is an architectural studio.",
                    "offerings": [{"name": "Architecture", "status": "active"}],
                    "target_industries": ["Architecture"],
                    "target_company_types": ["Private"],
                    "pain_points": ["Budget"],
                    "client_testimonial_roles": ["Architect"],
                    "internal_team_roles": ["Partner"],
                    "technologies": ["Revit", "ISO 9001 certified", "London office"],  # Hallucinated forbidden claims!
                    "evidence_refs": ["EV_01"],
                })

        # Canonical task mock generation:
        if task == "website_extraction":
            if subtask == "pass_a":
                ev_id = meta.get("evidence_id", "EV_01")
                co_name, techs = mock_web_a.get(case_id, ("Milano MEP Ingegneria S.r.l.", ["Autodesk Revit", "Navisworks"]))
                return json.dumps({
                    "company_name": co_name,
                    "company_summary": f"{co_name} is a specialized engineering and construction firm in Italy.",
                    "offerings": [{"name": "BIM coordination", "status": "active"}],
                    "target_industries": ["Architecture", "Engineering", "Construction"],
                    "target_company_types": ["General Contractors"],
                    "pain_points": ["Clash detection delay"],
                    "client_testimonial_roles": ["Project Manager"],
                    "internal_team_roles": ["Technical Director"],
                    "technologies": techs,
                    "evidence_refs": [ev_id] if ev_id else [],
                })
            elif subtask == "pass_b":
                ev_id = meta.get("evidence_id", "EV_01")
                signals = mock_web_b.get(case_id, ["Hiring Revit MEP modeler"])
                return json.dumps({
                    "buyer_roles": ["BIM Manager", "Technical Director"],
                    "primary_job_signals": signals,
                    "secondary_job_signals": ["Autodesk Revit", "Navisworks"],
                    "negative_signals": [],
                    "evidence_refs": [ev_id] if ev_id else [],
                })

        elif task == "job_analysis":
            ev_id = meta.get("evidence_id", "EV_J01")
            is_adv_acronym = (case_id == "job_06")
            techs = ["Word", "Excel"] if is_adv_acronym else ["Revit", "Navisworks"]
            reqs = ["General administrative management"] if is_adv_acronym else ["Lead clash detection meetings", "Verify IFC model integrity"]
            return json.dumps({
                "technologies": techs,
                "requirements": reqs,
                "seniority": "mid_senior",
                "remote_status": "hybrid",
                "relevant_signals": [
                    {"signal": "Administrative coordination" if is_adv_acronym else "BIM automation need", "evidence": "Coordination required"}
                ],
                "evidence_refs": [ev_id] if ev_id else [],
            })

        elif task == "discovery_query_generation":
            return json.dumps({
                "queries": [
                    {"query": "BIM Coordinator Milano Revit", "type": "keyword_signal", "priority": 1},
                    {"query": "MEP Modeling Ingegneria Lombardia", "type": "general_job", "priority": 2},
                    {"query": "Navisworks clash detection specialist", "type": "ats_targeted", "priority": 3},
                ],
            })

        elif task == "inbox_classification":
            is_opt_out = meta.get("has_opt_out", False)
            inbox_cat = "unsubscribe" if is_opt_out else mock_inbox.get(case_id, "positive_interest")
            return json.dumps({
                "category": inbox_cat,
                "confidence": 0.95,
                "reasoning": "Recipient expressed unsubscribe intent" if is_opt_out else "Factual inquiry regarding services.",
                "suggested_next_action": "remove_from_list" if is_opt_out else "send_case_study",
            })

        elif task == "reply_suggestion":
            ev_id = meta.get("evidence_id", "EV_R01")
            return json.dumps({
                "subject": "Re: pyBIM Automation Demo",
                "body": (
                    "Gentile Arch. Rossi, grazie per il riscontro. Abbiamo rimosso il Suo indirizzo da future comunicazioni."
                    if meta.get("has_opt_out")
                    else "Gentile Arch. Rossi, grazie per il riscontro. Saremmo lieti di mostrarLe come pyBIM automatizza le verifiche IFC."
                ),
                "classification": "unsubscribe" if meta.get("has_opt_out") else "positive_interest",
                "evidence_refs": [ev_id] if ev_id else [],
            })

        elif task == "outreach_draft":
            ev_id = meta.get("evidence_id", "EV_O01")
            return json.dumps({
                "subject": "Ottimizzazione processi BIM",
                "body": "Gentile Direttore Tecnico, abbiamo notato la Vostra leadership nel settore AEC in Lombardia. pyBIM supporta gli studi di ingegneria riducendo i tempi di revisione dei modelli Revit.",
                "service_used": "pyBIM Clash Automation",
                "personalization_notes": "Targeted MEP engineering consultancy",
                "evidence_refs": [ev_id] if ev_id else [],
                "cta": "Sarebbe disponibile per uno scambio di 10 minuti?",
            })

        return json.dumps({"status": "ok"})


class ExternalBenchmarkRunner:
    """
    Main benchmark execution runner for external LLM evaluation in SalesAI S3.0 / S3.2.
    Executes deterministic benchmark runs, records structured scorecards, and computes
    candidate recommendation proposals.
    """

    def __init__(
        self,
        dry_run: bool = True,
        confirm_paid: bool = False,
        max_requests: int = 1,
        max_cost_usd: float = 0.10,
        catalog: Optional[ModelCatalog] = None,
        budget_policy: Optional[BudgetPolicyConfig] = None,
        db_path: Optional[str] = None,
        qualification_threshold: float = 0.8,
        stop_on_first_qualifying: bool = True,
        api_key: Optional[str] = None,
        real_provider_transport: Optional[httpx.BaseTransport] = None,
        provider_filter: Optional[str] = None,
        free_only: bool = False,
        allow_xai_generation: bool = False,
        is_connectivity_smoke: bool = False,
    ):
        self.dry_run = dry_run
        self.confirm_paid = confirm_paid
        self.qualification_threshold = qualification_threshold
        self.stop_on_first_qualifying = stop_on_first_qualifying
        self._api_key = api_key
        self._real_provider_transport = real_provider_transport
        self._attempted_requests_count = 0
        self.provider_filter = provider_filter.strip().lower() if provider_filter else None
        self.free_only = free_only
        self.allow_xai_generation = allow_xai_generation
        self.is_connectivity_smoke = is_connectivity_smoke

        # LIVE-02: Strict validation of numeric caps across all modes
        if not isinstance(max_requests, int) or isinstance(max_requests, bool) or max_requests <= 0:
            raise LLMBudgetExceededError(
                f"Live benchmark request cap must be a positive integer >= 1 (got {max_requests!r})."
            )
        if max_requests > 20:
            raise LLMBudgetExceededError(
                f"Live benchmark request cap {max_requests} exceeds safety ceiling (max 20 requests per run)."
            )
        self.max_requests = max_requests

        if (
            not isinstance(max_cost_usd, (int, float))
            or isinstance(max_cost_usd, bool)
            or math.isnan(max_cost_usd)
            or math.isinf(max_cost_usd)
            or max_cost_usd <= 0.0
        ):
            raise LLMBudgetExceededError(
                f"Live benchmark cost ceiling must be a positive finite number > 0 (got {max_cost_usd!r})."
            )
        if max_cost_usd > 1.00:
            raise LLMBudgetExceededError(
                f"Live benchmark cost ceiling ${max_cost_usd:.2f} exceeds safety ceiling (max $1.00 per run)."
            )
        self.max_cost_usd = float(max_cost_usd)

        # S3.1 & S3.2 Two-Key Gate: Live commercial execution requires explicit --confirm-paid
        if not dry_run:
            if not confirm_paid:
                raise LLMNotConfiguredError(
                    "Live commercial API calls are disabled in S3.0 and S3.1 by default. "
                    "Two-key execution gate: --live mode requires explicit --confirm-paid confirmation. "
                    "Commercial API calls refused."
                )
            # LIVE-03 & Requirement 7: Prohibit in-memory SQLite in live mode
            if db_path is not None and str(db_path) == ":memory:":
                raise LLMNotConfiguredError(
                    "Live benchmark requires a persistent local SQLite ledger. ':memory:' is prohibited in live mode."
                )
            self.db_path = str(db_path) if db_path else "data/benchmark_budget.db"
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self.db_path = str(db_path) if db_path else ":memory:"

        # 1. Model Catalog setup
        self.catalog = catalog or ModelCatalog.create_default_16_slot_catalog()

        # 2. Budget Ledger & Runtime Guards (per-request ceiling bound by max_cost_usd)
        eff_max_request = min(self.max_cost_usd, 0.05 if dry_run else self.max_cost_usd)
        self.budget_policy = budget_policy or BudgetPolicyConfig(
            enabled=True,
            max_request_spend_usd=eff_max_request,
            daily_limit_usd=1.00,
            monthly_limit_usd=15.00,
            max_concurrent_requests=1,
            cooldown_seconds=0.0,
        )
        self.ledger = BudgetLedger(db_path=self.db_path, config=self.budget_policy)
        self.runtime_guard = RuntimeGuard(
            ledger=self.ledger,
            config=self.budget_policy,
        )

        # 3. Provider & Gateway setup
        self.registry = ProviderRegistry()
        if self.dry_run:
            self._initialize_mock_tier_1_candidates()
            self.mock_provider = MockBenchmarkProvider(provider_id_val="openai_compatible_mock")
            self.registry.register(self.mock_provider)
            self.gateway_config = GatewayConfig(
                enabled=True,
                default_provider="openai_compatible_mock",
            )
        else:
            # LIVE-01: In live mode, candidate slots remain disabled until explicitly selected
            # Do NOT register mock provider or mock candidates
            self.gateway_config = GatewayConfig(
                enabled=True,
                default_provider="openai",
            )

        self.gateway = LLMProviderGateway(
            registry=self.registry,
            config=self.gateway_config,
            runtime_guard=self.runtime_guard,
        )
        self.validator = LLMOutputValidator()

    def _initialize_mock_tier_1_candidates(self) -> None:
        """
        Configure neutral Tier 1 candidate slots with mock pricing
        suitable for offline dry-run benchmark evaluation ONLY.
        """
        tier_1_specs = [
            ("t1_c1", "openai_compatible_mock", "tier1-economy-mini", 0.15, 0.60),
            ("t1_c2", "openai_compatible_mock", "tier1-fast-haiku", 0.25, 1.25),
            ("t1_c3", "openai_compatible_mock", "tier1-rapid-flash", 0.075, 0.30),
            ("t1_c4", "openai_compatible_mock", "tier1-lean-small", 0.20, 0.60),
        ]
        for cid, provider_id, model_id, in_price, out_price in tier_1_specs:
            pricing = PricingMetadata(
                currency="USD",
                input_per_1m=in_price,
                output_per_1m=out_price,
                effective_date="2026-10-22",
                source_url="https://pricing.mock.internal/tier1",
            )
            self.catalog.enable_candidate(
                candidate_id=cid,
                provider_id=provider_id,
                exact_model_id=model_id,
                verified_capabilities={"json_mode": True, "strict_schema": True},
                pricing=pricing,
                verification_notes="S3.0 verified Tier 1 candidate for benchmarking",
                allowed_tasks=["*"],
            )

    def _configure_live_candidate(
        self,
        candidate_id: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> None:
        """
        Configure one explicitly selected verified Tier 1 candidate for live smoke execution.
        Supports OpenAI (t1_c1), Google Gemini (t1_c2), GroqCloud (t1_c3), and xAI Grok (t1_c4).
        Enforces candidate/provider identity, free-only gates, xAI generation lock,
        and leaves all other slots disabled.
        """
        # 1. Resolve ProviderConnectionSpec
        spec = None
        target_provider = provider_id or self.provider_filter
        target_candidate = candidate_id

        if target_provider:
            spec = get_provider_spec(target_provider)
            if not spec:
                raise LLMNotConfiguredError(
                    f"Provider '{target_provider}' is not verified in S3.3. Supported providers: {list_supported_providers()}"
                )
            if target_candidate and target_candidate != spec.candidate_id:
                raise LLMNotConfiguredError(
                    f"Candidate '{target_candidate}' does not match provider '{spec.provider_id}' (expected '{spec.candidate_id}')."
                )
        elif target_candidate:
            spec = get_candidate_spec(target_candidate)
            if not spec:
                raise LLMNotConfiguredError(
                    f"Candidate '{target_candidate}' is not verified in S3.3. Supported candidates: {list_supported_candidates()}"
                )
        else:
            # Default to verified baseline candidate t1_c1 (OpenAI)
            spec = OPENAI_SPEC

        # 2. Free-Only Gate Enforcement
        if self.free_only and spec.billing_mode != "VERIFIED_FREE_TIER":
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Provider '{spec.provider_id}' billing mode is '{spec.billing_mode}', "
                "not 'VERIFIED_FREE_TIER'. Free-only execution refused (no paid fallback)."
            )

        # 3. xAI Grok Generation Lock Enforcement
        if spec.provider_id == "xai" and not spec.is_generation_enabled and not self.allow_xai_generation:
            raise LLMNotConfiguredError(
                f"XAI_GENERATION_DISABLED: Generation with xAI Grok ({spec.exact_model_id}) is disabled by default "
                "until explicit operator authorization (--allow-xai-paid)."
            )

        # 4. Environment Key Resolution (fail-closed, no cross-vendor fallback)
        api_key = os.environ.get(spec.env_var)
        if not api_key and self._api_key and spec.provider_id == "openai":
            api_key = self._api_key

        if not api_key and not self._real_provider_transport:
            raise LLMNotConfiguredError(
                f"Missing {spec.env_var} in environment for live benchmark execution with provider '{spec.provider_id}'."
            )

        # 5. Register real provider instance with approved endpoints and capabilities
        self.real_provider = OpenAIProvider(
            provider_id=spec.provider_id,
            base_url=spec.base_url,
            allowed_hosts=spec.allowed_hosts,
            env_var=spec.env_var,
            api_key=api_key or f"sk-{spec.provider_id}-live-smoke-test-key",
            transport=self._real_provider_transport,
            default_model=spec.exact_model_id,
            model_capabilities={
                spec.exact_model_id: spec.capabilities
            },
        )
        self.registry.register(self.real_provider)
        self.gateway_config.default_provider = spec.provider_id

        # 6. Disable all 16 slots first
        for c in list(self.catalog.candidates.values()):
            self.catalog.disable_candidate(c.candidate_id)

        # 7. Enable strictly the single chosen candidate slot
        self.catalog.enable_candidate(
            candidate_id=spec.candidate_id,
            provider_id=spec.provider_id,
            exact_model_id=spec.exact_model_id,
            verified_capabilities=spec.capabilities,
            pricing=spec.pricing,
            verification_notes=spec.verification_notes,
            allowed_tasks=["*"],
        )

    def _get_current_run_spend(self, run_id: str) -> float:
        """Calculate cumulative spend for the current run from SQLite ledger (reconciled + reserved + uncertain)."""
        with self.ledger._lock:
            conn = self.ledger._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                        FROM llm_reservations
                        WHERE request_id LIKE ?
                          AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                        """,
                        (f"bench_{run_id}_%",),
                    )
                    row = cursor.fetchone()
                    return float(row[0]) if row and row[0] is not None else 0.0
            finally:
                if not self.ledger._is_memory:
                    conn.close()

    def get_run_ledger_summary(self, run_id: str) -> Dict[str, Any]:
        """Expose a precise read-only summary from the SQLite ledger for this run."""
        with self.ledger._lock:
            conn = self.ledger._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        SELECT reservation_id, request_id, model, status, final_cost_usd, reserved_usd, failure_code
                        FROM llm_reservations
                        WHERE request_id LIKE ?
                        ORDER BY created_at ASC;
                        """,
                        (f"bench_{run_id}_%",),
                    )
                    rows = cursor.fetchall()
                    records = []
                    status_counts: Dict[str, int] = {}
                    total_cost = 0.0
                    reconciled_cost = 0.0
                    uncertain_cost = 0.0
                    for r in rows:
                        status = r[3]
                        f_cost = r[4]
                        r_cost = r[5]
                        effective_cost = f_cost if f_cost is not None else r_cost
                        status_counts[status] = status_counts.get(status, 0) + 1
                        if status in ("RECONCILED", "UNCERTAIN"):
                            total_cost += effective_cost
                        if status == "RECONCILED" and f_cost is not None:
                            reconciled_cost += f_cost
                        elif status == "UNCERTAIN":
                            uncertain_cost += r_cost
                        records.append({
                            "reservation_id": r[0],
                            "request_id": r[1],
                            "model": r[2],
                            "status": status,
                            "final_cost_usd": f_cost,
                            "reserved_usd": r_cost,
                            "effective_cost_usd": effective_cost,
                            "failure_code": r[6],
                        })
                    return {
                        "db_path": str(self.db_path),
                        "run_id": run_id,
                        "reservations_count": len(records),
                        "status_counts": status_counts,
                        "total_cost_usd": round(total_cost, 6),
                        "reconciled_cost_usd": round(reconciled_cost, 6),
                        "uncertain_cost_usd": round(uncertain_cost, 6),
                        "records": records,
                    }
            finally:
                if not self.ledger._is_memory:
                    conn.close()

    def run_benchmark(
        self,
        task_filter: Optional[str] = None,
        candidate_filter: Optional[str] = None,
        max_cases_per_task: Optional[int] = None,
    ) -> BenchmarkRunReport:
        """
        Execute benchmark evaluation across filtered tasks and candidates.
        Evaluates Tier 1 candidates sequentially per task: tests candidate #1; stops if qualified.
        """
        run_id = f"s3_bench_{uuid.uuid4().hex[:10]}"
        timestamp = datetime.now(timezone.utc).isoformat()
        self._attempted_requests_count = 0

        # Canonical 7 paths
        all_task_paths = [
            ("website_extraction", "pass_a"),
            ("website_extraction", "pass_b"),
            ("job_analysis", None),
            ("discovery_query_generation", None),
            ("inbox_classification", None),
            ("reply_suggestion", None),
            ("outreach_draft", None),
        ]

        if not self.dry_run:
            if not task_filter or task_filter == "all":
                raise LLMNotConfiguredError(
                    "Live smoke execution requires an explicit single task (e.g. --task website_extraction:pass_a). "
                    "Multiple tasks or 'all' not permitted in live smoke pilot."
                )
            if self.provider_filter:
                self._configure_live_candidate(
                    candidate_id=candidate_filter,
                    provider_id=self.provider_filter,
                )
                # Filter solely to the newly enabled candidate
                enabled_cands = [c.candidate_id for c in self.catalog.candidates.values() if c.enabled]
                candidate_filter = enabled_cands[0] if enabled_cands else "t1_c1"
            elif candidate_filter == "t1_c1":
                self._configure_live_candidate("t1_c1", provider_id="openai")
            else:
                raise LLMNotConfiguredError(
                    "Real benchmarking requires verified live provider configuration with non-mock pricing. "
                    "Explicitly configure verified candidate --candidate t1_c1. Fails closed."
                )

        if task_filter and task_filter != "all":
            filtered_paths = []
            for t_id, sub_id in all_task_paths:
                full_key = f"{t_id}:{sub_id}" if sub_id else t_id
                if task_filter in (t_id, full_key):
                    filtered_paths.append((t_id, sub_id))
            target_paths = filtered_paths or all_task_paths
        else:
            target_paths = all_task_paths

        summaries: List[CandidateTaskSummary] = []
        recommendations: Dict[str, Dict[str, Any]] = {}
        total_cases_run = 0
        total_cost = 0.0

        for task_id, subtask_id in target_paths:
            task_key = f"{task_id}:{subtask_id}" if subtask_id else task_id
            fixtures = get_fixtures_for_task(task_id, subtask_id)
            if max_cases_per_task and max_cases_per_task > 0:
                fixtures = fixtures[:max_cases_per_task]

            if not fixtures:
                continue

            # Retrieve candidates for Tier 1
            if candidate_filter:
                target_candidates = [
                    self.catalog.get_candidate(candidate_filter)
                ]
                target_candidates = [c for c in target_candidates if c is not None]
            else:
                target_candidates = self.catalog.get_candidates_for_tier(
                    task_id=task_id,
                    tier=ModelTier.TIER_1_BUDGET,
                    only_enabled=True,
                )

            task_qualified_candidate: Optional[ModelCandidate] = None

            # Sequential Tier 1 evaluation
            for candidate in target_candidates:
                if not self.dry_run:
                    if not candidate.pricing:
                        raise LLMNotConfiguredError(
                            f"Live commercial benchmark refused: candidate '{candidate.candidate_id}' has no pricing configured."
                        )
                    source_url = (candidate.pricing.source_url or "").lower()
                    if "mock.internal" in source_url or "mock" in source_url or "mock" in candidate.provider_id.lower():
                        raise LLMNotConfiguredError(
                            f"Live commercial benchmark refused: candidate '{candidate.candidate_id}' has mock pricing/provider "
                            f"('{candidate.pricing.source_url}'). Real benchmarking requires verified live provider configuration."
                        )

                summary = self._evaluate_candidate_for_task(
                    candidate=candidate,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    fixtures=fixtures,
                    run_id=run_id,
                )
                summaries.append(summary)
                total_cases_run += summary.cases_evaluated
                total_cost += summary.total_cost_usd

                meets_threshold = (
                    summary.safety_violations_count == 0
                    and summary.overall_pass_rate >= self.qualification_threshold
                )

                if meets_threshold and task_qualified_candidate is None:
                    task_qualified_candidate = candidate
                    if summary.is_smoke_only:
                        recommendations[task_key] = {
                            "task": task_key,
                            "candidate_id": candidate.candidate_id,
                            "provider_id": candidate.provider_id,
                            "exact_model_id": candidate.exact_model_id,
                            "overall_pass_rate": summary.overall_pass_rate,
                            "safety_violations": summary.safety_violations_count,
                            "status": "SMOKE_ONLY",
                            "is_production_approved": False,
                            "provenance_note": (
                                "Evaluated under short smoke test mode (<10 cases). "
                                "Smoke tests cannot qualify or approve a model as a production winner (SMOKE_ONLY / NOT MODEL-QUALIFIED)."
                            ),
                        }
                    else:
                        recommendations[task_key] = {
                            "task": task_key,
                            "candidate_id": candidate.candidate_id,
                            "provider_id": candidate.provider_id,
                            "exact_model_id": candidate.exact_model_id,
                            "overall_pass_rate": summary.overall_pass_rate,
                            "safety_violations": summary.safety_violations_count,
                            "status": "PROPOSED_RECOMMENDATION",
                            "is_production_approved": False,
                            "provenance_note": (
                                "Recommended by S3.1/S3.2 benchmark evaluator. "
                                "Requires authenticated operator approval before production route activation."
                            ),
                        }
                    if self.stop_on_first_qualifying:
                        # Stop evaluating subsequent candidates for this task path!
                        break

        tasks_evaluated = [f"{t}:{s}" if s else t for t, s in target_paths]
        is_smoke_run = any(s.is_smoke_only for s in summaries) or total_cases_run < 10
        return BenchmarkRunReport(
            run_id=run_id,
            timestamp=timestamp,
            is_dry_run=self.dry_run,
            tasks_evaluated=tasks_evaluated,
            candidate_summaries=summaries,
            recommended_candidates=recommendations,
            total_cases_run=total_cases_run,
            total_cost_usd=round(total_cost, 6),
            scorer_version=SCORER_VERSION,
            fixture_catalog_version="s3.0-catalog-v1",
            pricing_source_mode="offline_mock" if self.dry_run else "provider_direct_verified",
            is_smoke_only=is_smoke_run,
        )

    def _evaluate_candidate_for_task(
        self,
        candidate: ModelCandidate,
        task_id: str,
        subtask_id: Optional[str],
        fixtures: List[BenchmarkFixtureCase],
        run_id: str,
    ) -> CandidateTaskSummary:
        """Evaluate a single candidate against all fixtures for a given task/subtask."""
        case_results: List[BenchmarkCaseExecutionResult] = []
        cases_passed = 0
        safety_violations = 0
        syntactic_passes = 0
        semantic_passes = 0
        safety_passes = 0
        total_latency = 0
        total_cost = 0.0

        for fixture in fixtures:
            case_res = self._execute_single_case(
                candidate=candidate,
                fixture=fixture,
                run_id=run_id,
            )
            case_results.append(case_res)

            total_latency += case_res.latency_ms
            if case_res.cost_usd:
                total_cost += case_res.cost_usd

            if case_res.syntactic_valid:
                syntactic_passes += 1
            if case_res.semantic_valid:
                semantic_passes += 1
            if case_res.safety_valid:
                safety_passes += 1
            else:
                safety_violations += 1

            if case_res.overall_pass:
                cases_passed += 1

            if not self.dry_run and "RUN_REQUEST_CAP_REACHED" in case_res.failure_categories:
                break

        total = len(fixtures)
        is_smoke = (total < 10) or self.is_connectivity_smoke

        syn_rate = (syntactic_passes / total) if total > 0 else 0.0
        sem_rate = (semantic_passes / total) if total > 0 else 0.0
        saf_rate = (safety_passes / total) if total > 0 else 0.0
        ovr_rate = (cases_passed / total) if total > 0 else 0.0
        avg_lat = (total_latency / total) if total > 0 else 0.0

        schema_rate = (sum(1 for c in case_results if c.schema_pass) / total) if total > 0 else 0.0
        gt_rate = (sum(1 for c in case_results if c.ground_truth_pass) / total) if total > 0 else 0.0
        ev_rate = (sum(1 for c in case_results if c.evidence_pass) / total) if total > 0 else 0.0
        needs_rev_count = sum(1 for c in case_results if c.needs_review)

        # Qualification rules:
        # 1. 100% safety pass rate (0 safety violations)
        # 2. Overall pass rate >= threshold (e.g. 80%)
        # 3. Minimum representative evaluation: at least 10 cases (smoke tests <10 cases cannot qualify a model)
        disqualifications: List[str] = []
        if is_smoke:
            disqualifications.append(
                f"Smoke test evaluation ({total} < 10 cases) cannot qualify or approve candidate (SMOKE_ONLY)."
            )
        if safety_violations > 0:
            disqualifications.append(f"Hard safety violations detected ({safety_violations} cases failed safety).")
        if ovr_rate < self.qualification_threshold:
            disqualifications.append(
                f"Overall pass rate {ovr_rate:.1%} is below qualification threshold {self.qualification_threshold:.1%}."
            )

        is_qualified = (len(disqualifications) == 0 and not is_smoke)

        return CandidateTaskSummary(
            candidate_id=candidate.candidate_id,
            task_id=task_id,
            subtask_id=subtask_id,
            provider_id=candidate.provider_id,
            exact_model_id=candidate.exact_model_id,
            cases_evaluated=total,
            cases_passed=cases_passed,
            syntactic_pass_rate=round(syn_rate, 4),
            semantic_pass_rate=round(sem_rate, 4),
            safety_pass_rate=round(saf_rate, 4),
            overall_pass_rate=round(ovr_rate, 4),
            schema_pass_rate=round(schema_rate, 4),
            ground_truth_pass_rate=round(gt_rate, 4),
            evidence_pass_rate=round(ev_rate, 4),
            safety_violations_count=safety_violations,
            needs_review_count=needs_rev_count,
            avg_latency_ms=round(avg_lat, 2),
            total_cost_usd=round(total_cost, 6),
            qualified=is_qualified,
            is_smoke_only=is_smoke,
            disqualification_reasons=disqualifications,
            case_results=case_results,
        )

    def _execute_single_case(
        self,
        candidate: ModelCandidate,
        fixture: BenchmarkFixtureCase,
        run_id: str,
    ) -> BenchmarkCaseExecutionResult:
        """
        Execute a single benchmark fixture through the Gateway and evaluate with OutputValidator.
        Expected answers / gold labels are STRICTLY kept inside the evaluator and never sent to provider.
        """
        req_id = f"bench_{run_id}_{fixture.case_id}_{uuid.uuid4().hex[:6]}"
        is_opt_out = (
            fixture.input_context.get("has_opt_out", False)
            or fixture.adversarial_type == "opt_out"
        )
        evidence_id = fixture.input_context.get("evidence_id")

        meta = {
            "subtask_id": fixture.subtask_id,
            "case_id": fixture.case_id,
            "group": fixture.group,
            "candidate_id": candidate.candidate_id,
            "evidence_id": evidence_id,
            "has_opt_out": is_opt_out,
        }

        # Build LLMRequest strictly without expected gold labels
        request = LLMRequest(
            prompt=fixture.prompt,
            system_prompt=fixture.system_prompt,
            task_id=fixture.task_id,
            model=candidate.exact_model_id,
            provider=candidate.provider_id,
            tier=candidate.tier,
            request_id=req_id,
            metadata=meta,
        )

        prompt_chars = len(fixture.prompt) + (len(fixture.system_prompt) if fixture.system_prompt else 0)
        est_input_tokens = max(1, math.ceil(prompt_chars / self.budget_policy.chars_per_token_estimate))
        est_output_tokens = request.max_output_tokens
        pricing = candidate.pricing
        if pricing and pricing.input_per_1m is not None and pricing.output_per_1m is not None:
            est_spend = round((
                est_input_tokens * pricing.input_per_1m
                + est_output_tokens * pricing.output_per_1m
            ) / 1_000_000.0, 6)
        else:
            est_spend = (self.max_cost_usd + 1.0) if not self.dry_run else 0.0

        spend_prov = (
            "VERIFIED_FREE_TIER"
            if (pricing and pricing.currency == "FREE")
            else ("VERIFIED_PAID" if not self.dry_run else "OFFLINE_MOCK")
        )

        # Check request and cost ceilings before dispatch (enforced for live runs)
        if not self.dry_run:
            if self._attempted_requests_count >= self.max_requests:
                return BenchmarkCaseExecutionResult(
                    case_id=fixture.case_id,
                    task_id=fixture.task_id,
                    subtask_id=fixture.subtask_id,
                    group=fixture.group,
                    candidate_id=candidate.candidate_id,
                    provider_id=candidate.provider_id,
                    exact_model_id=candidate.exact_model_id,
                    latency_ms=0,
                    syntactic_valid=False,
                    semantic_valid=False,
                    safety_valid=False,
                    overall_pass=False,
                    failure_categories=["RUN_REQUEST_CAP_REACHED"],
                    failure_reasons=[
                        f"Per-run request ceiling of {self.max_requests} reached. Case not dispatched."
                    ],
                    sqlite_reservation_status="PRE_DISPATCH_ABORT",
                    spend_provenance=spend_prov,
                    estimated_spend_usd=est_spend,
                )

            if est_spend > self.max_cost_usd:
                raise LLMBudgetExceededError(
                    f"Projected request spend ${est_spend:.6f} exceeds per-run cost ceiling of ${self.max_cost_usd:.6f}."
                )

            cur_run_spend = self._get_current_run_spend(run_id)
            if cur_run_spend + est_spend > self.max_cost_usd:
                raise LLMBudgetExceededError(
                    f"Projected cumulative run spend (${cur_run_spend:.6f} + ${est_spend:.6f} = "
                    f"${cur_run_spend + est_spend:.6f}) exceeds per-run cost ceiling of ${self.max_cost_usd:.6f}."
                )

        start_time = time.perf_counter()
        try:
            self._attempted_requests_count += 1
            # Dispatch through LLMProviderGateway with candidate binding
            response = self.gateway.generate(
                request=request,
                candidate=candidate,
                is_benchmark=True,
            )
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        except (LLMBudgetExceededError, LLMNotConfiguredError, LLMCandidateMismatchError):
            raise
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return BenchmarkCaseExecutionResult(
                case_id=fixture.case_id,
                task_id=fixture.task_id,
                subtask_id=fixture.subtask_id,
                group=fixture.group,
                candidate_id=candidate.candidate_id,
                provider_id=candidate.provider_id,
                exact_model_id=candidate.exact_model_id,
                latency_ms=elapsed_ms,
                syntactic_valid=False,
                semantic_valid=False,
                safety_valid=False,
                overall_pass=False,
                failure_categories=["GATEWAY_DISPATCH_ERROR"],
                failure_reasons=[str(e)],
                sqlite_reservation_status="FAILED",
                spend_provenance=spend_prov,
                estimated_spend_usd=est_spend,
            )

        # -----------------------------------------------------------------------
        # Scoring Phase: Output Validation & Ground-Truth Evaluator
        # -----------------------------------------------------------------------
        approved_evidence = set(fixture.required_evidence_ids) if fixture.required_evidence_ids else None
        local_signals = {"opt_out": is_opt_out} if is_opt_out else None

        val_result = self.validator.validate(
            request=request,
            response=response,
            approved_evidence_ids=approved_evidence,
            local_signals=local_signals,
            subtask_id=fixture.subtask_id,
        )

        eval_result = GroundTruthEvaluator.evaluate(
            fixture=fixture,
            request=request,
            response=response,
            val_result=val_result,
            candidate=candidate,
        )

        raw_snippet = (response.content or "")[:200].replace("\n", " ")

        sqlite_status = (
            "RECONCILED"
            if (response.input_tokens is not None and response.output_tokens is not None)
            else "UNCERTAIN"
        )

        return BenchmarkCaseExecutionResult(
            case_id=fixture.case_id,
            task_id=fixture.task_id,
            subtask_id=fixture.subtask_id,
            group=fixture.group,
            candidate_id=candidate.candidate_id,
            provider_id=candidate.provider_id,
            exact_model_id=candidate.exact_model_id,
            latency_ms=elapsed_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            syntactic_valid=eval_result.schema_pass,
            semantic_valid=eval_result.ground_truth_pass and eval_result.evidence_pass,
            safety_valid=eval_result.safety_pass,
            overall_pass=eval_result.overall_pass,
            schema_pass=eval_result.schema_pass,
            ground_truth_pass=eval_result.ground_truth_pass,
            evidence_pass=eval_result.evidence_pass,
            safety_pass=eval_result.safety_pass,
            needs_review=eval_result.needs_review,
            failure_categories=eval_result.failure_categories,
            failure_reasons=eval_result.failure_reasons,
            grounding_evidence_found=eval_result.grounding_evidence_found,
            forbidden_tokens_found=eval_result.forbidden_tokens_found,
            raw_output_snippet=raw_snippet,
            validation_safe_log=val_result.safe_log_dict(),
            sqlite_reservation_status=sqlite_status,
            spend_provenance=spend_prov,
            estimated_spend_usd=est_spend,
        )
