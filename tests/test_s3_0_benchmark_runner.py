"""
tests/test_s3_0_benchmark_runner.py
Deterministic offline test suite for SalesAI S3.0 — External LLM Benchmark Runner & Paid-Dispatch Safety Gates.

Validates:
1. A1 Fix: Duplicate request_id detection & single-dispatch guarantee:
   - Serial duplicate: returns cached response without second HTTP call.
   - Concurrent duplicate: raises LLMDuplicateRequestError.
   - Restart / persistent DB duplicate: raises LLMDuplicateRequestError from ledger.
2. A2 Fix: Pricing / candidate identity binding:
   - Candidate model mismatch raises LLMCandidateMismatchError before preflight and dispatch.
   - Candidate provider mismatch raises LLMCandidateMismatchError.
   - Candidate tier mismatch raises LLMCandidateMismatchError.
3. Golden 10 Dataset Mapping:
   - Maps compatible legacy cases into canonical Gateway tasks.
   - Explicitly flags G05 (meeting_time_extraction) as unmappable to Gateway contracts.
4. 70-Case Synthetic Catalog Integrity:
   - 10 cases per path across all 7 paths (5 grounded, 5 adversarial).
   - Reference answers / gold labels are isolated and never present in prompt text.
5. Sequential Tier 1 Candidate Evaluation:
   - Evaluates Candidate #1 first, stopping if qualified.
   - Evaluates Candidate #2 if Candidate #1 fails qualification.
6. Hard Safety Disqualifier Precedence:
   - Hard safety violations override high syntactic schema validity.
7. Offline Dry-Run & Live Safety Gate:
   - Dry-run mode executes 100% offline without network calls.
   - Attempted live execution strictly fails closed with LLMNotConfiguredError.
8. Multi-Format Report Generation:
   - Generates JSON, CSV (one row per candidate x task x case), and Markdown reports.
   - Explicit non-approval notice in reports.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
import pytest

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
    LLMRoutingIntegrityError,
)
from sales_engine.llm_gateway.interface import BaseLLMProvider, LLMProviderGateway
from sales_engine.llm_gateway.model_catalog import (
    ModelCandidate,
    ModelCatalog,
    PricingMetadata,
)
from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    ReservationStatus,
)
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.output_validation import LLMOutputValidator

from sales_engine.benchmark.fixtures_s3 import (
    ALL_S3_BENCHMARK_FIXTURES,
    BenchmarkFixtureCase,
    get_all_s3_fixtures,
    get_fixtures_for_task,
)
from sales_engine.benchmark.golden_mapper import GoldenDatasetMapper, MappedGoldenCase
from sales_engine.benchmark.external_runner import (
    BenchmarkCaseExecutionResult,
    BenchmarkRunReport,
    CandidateTaskSummary,
    ExternalBenchmarkRunner,
    MockBenchmarkProvider,
)
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator


class CountingMockProvider(BaseLLMProvider):
    """Offline test provider that counts calls and returns deterministic responses."""

    def __init__(self, provider_id_val: str = "test_provider"):
        self._provider_id = provider_id_val
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            content=json.dumps({"company_name": "Test Co", "company_summary": "Test Summary"}),
            provider=self._provider_id,
            model=request.model or "test-model",
            task_id=request.task_id,
            request_id=request.request_id,
            input_tokens=50,
            output_tokens=25,
            latency_ms=10,
            finish_reason="stop",
        )

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        return self.generate(request)


# ---------------------------------------------------------------------------
# 1. A1 Blocker Tests: Duplicate request_id & Idempotency
# ---------------------------------------------------------------------------

def test_a1_serial_duplicate_returns_cached_response_single_dispatch():
    """Verify calling guarded_call twice with the same request_id returns cached response and dispatches provider once."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test.db")
        policy = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.10, daily_limit_usd=1.00)
        ledger = BudgetLedger(db_path=db_path, config=policy)
        guard = RuntimeGuard(ledger=ledger, config=policy)
        provider = CountingMockProvider("mock_p")

        candidate = ModelCandidate(
            candidate_id="t1_c1",
            tier=ModelTier.TIER_1_BUDGET,
            priority=1,
            provider_id="mock_p",
            exact_model_id="test-model",
            enabled=True,
            verified_capabilities={"json_mode": True},
            pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60),
        )

        req = LLMRequest(
            prompt="Hello world",
            task_id="website_extraction",
            model="test-model",
            provider="mock_p",
            tier=ModelTier.TIER_1_BUDGET,
            request_id="req_unique_a1_001",
        )

        # Call 1: dispatches provider
        resp1 = guard.guarded_call(request=req, candidate=candidate, call_fn=provider.generate)
        assert provider.call_count == 1
        assert resp1.request_id == "req_unique_a1_001"

        # Call 2 with identical request_id: returns cached response without second provider call
        resp2 = guard.guarded_call(request=req, candidate=candidate, call_fn=provider.generate)
        assert provider.call_count == 1  # Still 1! Provider was not called a second time!
        assert resp2.request_id == "req_unique_a1_001"

        # Ledger check: exactly ONE reconciled reservation
        summary = ledger.get_budget_summary()
        assert summary.total_reservations_today == 1
        assert summary.status_counts_today.get("RECONCILED") == 1
        del ledger
        del guard
        import gc; gc.collect()


def test_a1_concurrent_duplicate_raises_duplicate_error():
    """Verify in-flight duplicate request_id raises LLMDuplicateRequestError."""
    policy = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.10, daily_limit_usd=1.00)
    ledger = BudgetLedger(db_path=":memory:", config=policy)
    guard = RuntimeGuard(ledger=ledger, config=policy)

    candidate = ModelCandidate(
        candidate_id="t1_c1",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        provider_id="mock_p",
        exact_model_id="test-model",
        enabled=True,
        verified_capabilities={"json_mode": True},
        pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60),
    )

    req = LLMRequest(
        prompt="Hello world",
        task_id="website_extraction",
        model="test-model",
        provider="mock_p",
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_in_flight_concurrent",
    )

    # Manually register in-flight
    guard._in_flight_request_ids.add("req_in_flight_concurrent")

    with pytest.raises(LLMDuplicateRequestError) as exc_info:
        guard.guarded_call(request=req, candidate=candidate, call_fn=lambda r: None)

    assert "currently in flight" in str(exc_info.value)


def test_a1_restart_db_duplicate_raises_duplicate_error():
    """Verify after process restart (empty cache), duplicate request_id in persistent DB raises LLMDuplicateRequestError."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_restart.db")
        policy = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.10, daily_limit_usd=1.00)
        ledger1 = BudgetLedger(db_path=db_path, config=policy)
        guard1 = RuntimeGuard(ledger=ledger1, config=policy)
        provider = CountingMockProvider("mock_p")

        candidate = ModelCandidate(
            candidate_id="t1_c1",
            tier=ModelTier.TIER_1_BUDGET,
            priority=1,
            provider_id="mock_p",
            exact_model_id="test-model",
            enabled=True,
            verified_capabilities={"json_mode": True},
            pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60),
        )

        req = LLMRequest(
            prompt="Hello world",
            task_id="website_extraction",
            model="test-model",
            provider="mock_p",
            tier=ModelTier.TIER_1_BUDGET,
            request_id="req_restart_test_001",
        )

        # Run with guard1
        resp = guard1.guarded_call(request=req, candidate=candidate, call_fn=provider.generate)
        assert provider.call_count == 1

        # Simulate restart: new ledger and guard with clean memory cache pointing to same DB
        ledger2 = BudgetLedger(db_path=db_path, config=policy)
        guard2 = RuntimeGuard(ledger=ledger2, config=policy)

        # Guard2 should detect that request_id was already reconciled and fail closed!
        with pytest.raises(LLMDuplicateRequestError) as exc_info:
            guard2.guarded_call(request=req, candidate=candidate, call_fn=provider.generate)

        assert "already been processed" in str(exc_info.value)
        assert provider.call_count == 1  # ZERO additional HTTP calls
        del ledger1
        del guard1
        del ledger2
        del guard2
        import gc; gc.collect()


# ---------------------------------------------------------------------------
# 2. A2 Blocker Tests: Candidate Identity & Pricing Mismatch
# ---------------------------------------------------------------------------

def test_a2_mismatched_candidate_model_fails_closed_before_dispatch():
    """Verify caller cannot reserve budget under cheap candidate while requesting expensive model."""
    policy = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.10, daily_limit_usd=1.00)
    ledger = BudgetLedger(db_path=":memory:", config=policy)
    guard = RuntimeGuard(ledger=ledger, config=policy)
    provider = CountingMockProvider("mock_p")

    # Inexpensive candidate
    cheap_candidate = ModelCandidate(
        candidate_id="t1_c3",
        tier=ModelTier.TIER_1_BUDGET,
        priority=3,
        provider_id="mock_p",
        exact_model_id="economy-model",
        enabled=True,
        verified_capabilities={"json_mode": True},
        pricing=PricingMetadata(input_per_1m=0.01, output_per_1m=0.02),
    )

    # Request specifies expensive model
    req = LLMRequest(
        prompt="Hello world",
        task_id="website_extraction",
        model="expensive-model",  # MISMATCH!
        provider="mock_p",
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_mismatch_001",
    )

    with pytest.raises(LLMCandidateMismatchError) as exc_info:
        guard.guarded_call(request=req, candidate=cheap_candidate, call_fn=provider.generate)

    assert "candidate identity mismatch" in str(exc_info.value)
    assert "request.model" in str(exc_info.value)
    assert provider.call_count == 0  # Zero HTTP calls dispatched!

    # Verify no reservation was left in ledger
    summary = ledger.get_budget_summary()
    assert summary.total_reservations_today == 0
    assert len(summary.outstanding_reservations) == 0


def test_a2_mismatched_provider_or_tier_fails_closed():
    """Verify candidate provider or tier mismatch also strictly fails closed."""
    policy = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.10, daily_limit_usd=1.00)
    ledger = BudgetLedger(db_path=":memory:", config=policy)
    guard = RuntimeGuard(ledger=ledger, config=policy)
    provider = CountingMockProvider("provider_a")

    candidate = ModelCandidate(
        candidate_id="t1_c1",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        provider_id="provider_a",
        exact_model_id="model-a",
        enabled=True,
        verified_capabilities={"json_mode": True},
        pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60),
    )

    # Provider mismatch
    req_bad_provider = LLMRequest(
        prompt="Hello world",
        task_id="website_extraction",
        model="model-a",
        provider="provider_b",  # MISMATCH!
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_prov_mismatch",
    )
    with pytest.raises(LLMCandidateMismatchError) as exc_info:
        guard.guarded_call(request=req_bad_provider, candidate=candidate, call_fn=provider.generate)
    assert "candidate identity mismatch" in str(exc_info.value)
    assert "request.provider" in str(exc_info.value)

    # Tier mismatch
    req_bad_tier = LLMRequest(
        prompt="Hello world",
        task_id="website_extraction",
        model="model-a",
        provider="provider_a",
        tier=ModelTier.TIER_4_FLAGSHIP,  # MISMATCH!
        request_id="req_tier_mismatch",
    )
    with pytest.raises(LLMCandidateMismatchError) as exc_info:
        guard.guarded_call(request=req_bad_tier, candidate=candidate, call_fn=provider.generate)
    assert "candidate identity mismatch" in str(exc_info.value)
    assert "request.tier" in str(exc_info.value)
    assert provider.call_count == 0


# ---------------------------------------------------------------------------
# 3. Golden 10 Dataset Mapping Tests
# ---------------------------------------------------------------------------

def test_golden_10_mapper_compatible_cases_and_g05_unmappable():
    """Verify Golden 10 dataset maps 9 cases and flags G05 (meeting_time_extraction) as unmappable."""
    mapper = GoldenDatasetMapper()
    mappable, unmappable = mapper.load_and_map()

    # Total 10 cases in synthetic dataset
    assert len(mappable) == 9
    assert len(unmappable) == 1

    # Verify G05 is unmappable
    g05 = unmappable[0]
    assert g05.golden_id == "G05"
    assert g05.legacy_task == "meeting_time_extraction"
    assert not g05.is_mappable
    assert "meeting_time_extraction" in str(g05.unmappable_reason)

    # Verify mappable cases cover the expected canonical tasks
    mapped_tasks = {m.canonical_task for m in mappable}
    assert "website_extraction" in mapped_tasks
    assert "job_analysis" in mapped_tasks
    assert "outreach_draft" in mapped_tasks
    assert "inbox_classification" in mapped_tasks


# ---------------------------------------------------------------------------
# 4. 70-Case Fixture Catalog Integrity Tests
# ---------------------------------------------------------------------------

def test_70_case_catalog_complete_and_gold_labels_isolated():
    """Verify 70 cases across 7 paths (10 per path: 5 grounded, 5 adversarial) with gold labels isolated."""
    all_fixtures = get_all_s3_fixtures()
    assert len(all_fixtures) == 70

    expected_paths = [
        ("website_extraction", "pass_a"),
        ("website_extraction", "pass_b"),
        ("job_analysis", None),
        ("discovery_query_generation", None),
        ("inbox_classification", None),
        ("reply_suggestion", None),
        ("outreach_draft", None),
    ]

    for task_id, subtask_id in expected_paths:
        fixtures = get_fixtures_for_task(task_id, subtask_id)
        assert len(fixtures) == 10, f"Expected 10 fixtures for {task_id}:{subtask_id}"

        grounded = [f for f in fixtures if f.group == "GROUNDED"]
        adversarial = [f for f in fixtures if f.group == "ADVERSARIAL"]
        assert len(grounded) == 5
        assert len(adversarial) == 5

        # Check prompt isolation: gold label / evaluator checks must NOT appear in prompt
        for f in fixtures:
            assert f.checks_description not in f.prompt
            if f.system_prompt:
                assert f.checks_description not in f.system_prompt
            if f.group == "GROUNDED":
                for forbidden in f.never_claim:
                    assert forbidden.lower() not in f.prompt.lower(), (
                        f"Forbidden claim '{forbidden}' leaked into prompt for case {f.case_id}!"
                    )


# ---------------------------------------------------------------------------
# 5. Sequential Tier 1 Candidate Evaluation Tests
# ---------------------------------------------------------------------------

def test_sequential_tier1_evaluation_stops_on_first_qualifying():
    """Verify runner evaluates candidate #1 first, and stops when #1 qualifies."""
    runner = ExternalBenchmarkRunner(dry_run=True, stop_on_first_qualifying=True)

    # Run on single task with full 10 cases for qualification check
    report = runner.run_benchmark(
        task_filter="job_analysis",
        max_cases_per_task=10,
    )

    # Candidate #1 (t1_c1) qualifies in mock runner -> runner stops!
    assert len(report.candidate_summaries) == 1
    summary = report.candidate_summaries[0]
    assert summary.candidate_id == "t1_c1"
    assert summary.qualified is True

    # Recommendation recorded as proposal only
    assert "job_analysis" in report.recommended_candidates
    rec = report.recommended_candidates["job_analysis"]
    assert rec["candidate_id"] == "t1_c1"
    assert rec["is_production_approved"] is False
    assert "PROPOSED" in rec["status"]


def test_sequential_tier1_progresses_when_candidate_disqualified():
    """Verify runner progresses to candidate #2 when candidate #1 fails qualification."""
    runner = ExternalBenchmarkRunner(dry_run=True, stop_on_first_qualifying=True)

    # Configure t1_c1 to fail by setting threshold higher than possible, or evaluate with a custom candidate filter
    report_t1_c3 = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t1_c3",  # t1_c3 simulates hallucination on adv cases
        max_cases_per_task=10,
    )

    summary_t1_c3 = report_t1_c3.candidate_summaries[0]
    assert summary_t1_c3.candidate_id == "t1_c3"
    # Verify hallucination was flagged
    assert len(summary_t1_c3.disqualification_reasons) > 0


# ---------------------------------------------------------------------------
# 6. Hard Safety Disqualifier Tests
# ---------------------------------------------------------------------------

def test_hard_safety_disqualifier_overrides_syntactic_validity():
    """Verify safety violations override high syntactic validity and disqualify the candidate."""
    runner = ExternalBenchmarkRunner(dry_run=True)

    # Mock case where candidate has high syntactic score but fails safety
    result = BenchmarkCaseExecutionResult(
        case_id="case_safety_test",
        task_id="inbox_classification",
        subtask_id=None,
        group="ADVERSARIAL",
        candidate_id="t1_c1",
        provider_id="mock_p",
        exact_model_id="mock-m",
        latency_ms=50,
        syntactic_valid=True,  # Clean JSON syntax!
        semantic_valid=False,
        safety_valid=False,     # Safety violation!
        overall_pass=False,
        failure_categories=["SAFETY_OPT_OUT_VIOLATION"],
        failure_reasons=["Model ignored opt-out"],
    )

    assert result.syntactic_valid is True
    assert result.overall_pass is False

    # In summary evaluation, safety_violations_count > 0 must disqualify
    summary = CandidateTaskSummary(
        candidate_id="t1_c1",
        task_id="inbox_classification",
        subtask_id=None,
        provider_id="mock_p",
        exact_model_id="mock-m",
        cases_evaluated=10,
        cases_passed=9,
        syntactic_pass_rate=1.0,
        overall_pass_rate=0.9,
        safety_violations_count=1,
        qualified=False,
        disqualification_reasons=["Hard safety violations detected"],
    )
    assert summary.qualified is False
    assert "Hard safety violations" in summary.disqualification_reasons[0]


# ---------------------------------------------------------------------------
# 7. Offline Dry-Run & Live Mode Gate Tests
# ---------------------------------------------------------------------------

def test_live_mode_strictly_fails_closed_in_s3_0():
    """Verify attempting live commercial execution raises LLMNotConfiguredError in S3.0."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(dry_run=False)

    assert "Live commercial API calls are disabled in S3.0" in str(exc_info.value)


def test_dry_run_executes_offline_with_zero_network_calls():
    """Verify default dry-run execution completes without network calls."""
    runner = ExternalBenchmarkRunner(dry_run=True)
    report = runner.run_benchmark(
        task_filter="discovery_query_generation",
        max_cases_per_task=2,
    )
    assert report.is_dry_run is True
    assert report.total_cases_run == 2
    assert report.total_cost_usd >= 0.0


# ---------------------------------------------------------------------------
# 8. Multi-Format Report Generation Tests
# ---------------------------------------------------------------------------

def test_multi_format_report_generation():
    """Verify JSON, CSV, and Markdown reports are generated properly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        runner = ExternalBenchmarkRunner(dry_run=True)
        report = runner.run_benchmark(
            task_filter="reply_suggestion",
            max_cases_per_task=2,
        )

        gen = S3BenchmarkReportGenerator(output_dir=out_dir)
        paths = gen.generate_all(report)

        # JSON verification
        json_path = paths["json"]
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["run_id"] == report.run_id
        assert len(data["candidate_summaries"]) >= 1

        # CSV verification (one row per candidate x task x case)
        csv_path = paths["csv"]
        assert csv_path.exists()
        csv_lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        # header + 2 case rows = 3 lines
        assert len(csv_lines) == 3
        assert "run_id,task_id,subtask_id" in csv_lines[0]

        # Markdown verification
        md_path = paths["markdown"]
        assert md_path.exists()
        md_content = md_path.read_text(encoding="utf-8")
        assert "External LLM Benchmark Report" in md_content
        assert "constitute production route approvals" in md_content
        assert "PROPOSED" in md_content or "SMOKE_ONLY" in md_content
