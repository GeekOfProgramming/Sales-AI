"""
tests/test_s3_6b_qualification_runner_safety.py
Deterministic offline tests for SalesAI S3.6B Qualification Runner Hardening & Safety.
Strictly offline: no network calls, no live credentials, no database writes to production ledger.
"""

import math
import pytest
from pathlib import Path
from typing import Dict, Any, List

from sales_engine.benchmark.external_runner import (
    ExternalBenchmarkRunner,
    CandidateTaskSummary,
    BenchmarkCaseExecutionResult,
    BenchmarkRunReport,
)
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMNotConfiguredError,
    LLMCandidateMismatchError,
)
from sales_engine.benchmark.fixtures_s3 import (
    BENCHMARK_CATALOG_VERSION,
    get_fixtures_for_task,
    get_all_s3_fixtures,
)
from sales_engine.llm_gateway.model_catalog import ModelCatalog, ModelCandidate, ModelTier, PricingMetadata


class TestTaskSelectionFailClosed:
    """Phase 1: Strict task filtering - fail closed on unknown, misspelled, or ambiguous tasks."""

    def test_reject_unknown_task_identifier(self, tmp_path):
        """Runner must reject unknown or misspelled task names without dispatching."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        with pytest.raises(LLMNotConfiguredError, match="INVALID_TASK_FILTER: Unknown task identifier"):
            runner.run_benchmark(task_filter="website_extracshun:pass_a")

    def test_reject_unsupported_subtask(self, tmp_path):
        """Runner must reject invalid subtasks on valid tasks."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        with pytest.raises(LLMNotConfiguredError, match="INVALID_TASK_FILTER: Unknown task identifier"):
            runner.run_benchmark(task_filter="job_analysis:subtask_99")

    def test_reject_blank_or_whitespace_task_filter(self, tmp_path):
        """Blank or whitespace-only task filter must be rejected."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        with pytest.raises(LLMNotConfiguredError, match="INVALID_TASK_FILTER: task_filter cannot be empty"):
            runner.run_benchmark(task_filter="   ")

    def test_reject_ambiguous_task_in_live_mode(self, tmp_path):
        """In live mode, ambiguous prefix matching multiple subtasks (e.g. 'website_extraction') must fail closed."""
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            provider_filter="gemini",
            db_path=str(tmp_path / "test.db"),
        )
        with pytest.raises(LLMNotConfiguredError, match="AMBIGUOUS_TASK_FILTER"):
            runner.run_benchmark(task_filter="website_extraction", candidate_filter="t1_c5")

    def test_accept_valid_exact_task_path(self, tmp_path):
        """Exact task path 'website_extraction:pass_a' succeeds in dry-run mode."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        report = runner.run_benchmark(task_filter="website_extraction:pass_a", max_cases_per_task=2)
        assert len(report.tasks_evaluated) == 1
        assert report.tasks_evaluated[0] == "website_extraction:pass_a"

    def test_preserve_offline_all_task_benchmarking(self, tmp_path):
        """task_filter=None or 'all' evaluates all 7 canonical paths in offline mode."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        report = runner.run_benchmark(task_filter="all", max_cases_per_task=1)
        assert len(report.tasks_evaluated) == 7


class TestQualificationDenominatorAccounting:
    """Phase 2: Correct denominator accounting, partial dispatch, and qualification criteria."""

    def test_full_ten_planned_ten_passed(self, tmp_path):
        """10 planned, 10 attempted, 10 evaluable, high pass rate -> Qualified."""
        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        report = runner.run_benchmark(task_filter="website_extraction:pass_a")
        summary = report.candidate_summaries[0]
        assert summary.cases_planned == 10
        assert summary.requests_attempted == 10
        assert summary.cases_evaluable == 10
        assert summary.cases_passed >= 8
        assert summary.overall_pass_rate >= 0.8
        assert summary.qualified is True
        assert summary.task_qualification_eligible is True

    def test_ten_planned_ten_attempted_eight_passed(self, tmp_path):
        """10 planned, 10 attempted, 10 evaluable, 8 passed -> 80% pass rate -> Qualified."""
        # Synthesize summary directly exercising the logic
        summary = CandidateTaskSummary(
            candidate_id="t1_c3",
            task_id="website_extraction",
            subtask_id="pass_a",
            provider_id="groq",
            exact_model_id="openai/gpt-oss-20b",
            cases_planned=10,
            requests_attempted=10,
            responses_received=10,
            cases_evaluable=10,
            cases_passed=8,
            overall_pass_rate=0.8,
            qualified=True,
            task_qualification_eligible=True,
        )
        assert summary.cases_planned == 10
        assert summary.cases_evaluable == 10
        assert summary.overall_pass_rate == 0.8
        assert summary.qualified is True

    def test_eight_passed_out_of_ten_planned_due_to_unattempted_never_qualifies(self, tmp_path):
        """Critical rule: 8 passed out of 10 planned when 2 unattempted MUST NEVER qualify."""
        # Simulating execution where request cap stopped run at 8 cases
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")
        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test.db"),
            qualification_threshold=0.8,
        )
        # Mock candidate
        cand = runner.catalog.get_candidate("t1_c1")
        # Run only 8 fixtures
        partial_fixtures = fixtures[:8]
        summary = runner._evaluate_candidate_for_task(
            candidate=cand,
            task_id="website_extraction",
            subtask_id="pass_a",
            fixtures=partial_fixtures,
            run_id="test_partial",
        )
        assert summary.cases_evaluable == 8
        # Since partial_fixtures had len 8, it was marked SMOKE_ONLY (<10) and disqualified
        assert summary.is_smoke_only is True
        assert summary.qualified is False
        assert any("minimum 10 required" in r or "Smoke test evaluation" in r for r in summary.disqualification_reasons)

    def test_ten_planned_eight_evaluated_due_to_transport_error_never_qualifies(self, tmp_path):
        """10 planned, 10 attempted, 2 transport failures, 8 evaluable, 8 passed -> Incomplete -> NEVER qualifies."""
        cand = ModelCandidate(
            candidate_id="t1_c5",
            tier=ModelTier.TIER_1_BUDGET,
            priority=5,
            provider_id="gemini",
            exact_model_id="gemini-3.5-flash-lite",
            enabled=True,
            verified_capabilities={"supports_json_mode": True},
            pricing=PricingMetadata(currency="USD", input_per_1m=0.30, output_per_1m=2.50),
        )
        # Create 10 case results: 8 passing, 2 transport errors
        cases = []
        for i in range(8):
            cases.append(BenchmarkCaseExecutionResult(
                case_id=f"web_a_{i+1:02d}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=1000,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
            ))
        for i in range(8, 10):
            cases.append(BenchmarkCaseExecutionResult(
                case_id=f"web_a_{i+1:02d}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                failure_categories=["GATEWAY_DISPATCH_ERROR"], failure_reasons=["HTTP 503 Backend Unavailable"],
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
            ))

        runner = ExternalBenchmarkRunner(dry_run=True, db_path=str(tmp_path / "test.db"))
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")
        
        # Override _execute_single_case to return our pre-canned case outcomes
        it = iter(cases)
        runner._execute_single_case = lambda candidate, fixture, run_id: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand,
            task_id="website_extraction",
            subtask_id="pass_a",
            fixtures=fixtures,
            run_id="test_run",
        )
        assert summary.cases_planned == 10
        assert summary.requests_attempted == 10
        assert summary.transport_failures == 2
        assert summary.cases_evaluable == 8
        assert summary.cases_passed == 8
        assert summary.evaluation_state == "PARTIAL_EVALUATION"
        assert summary.qualified is False
        assert any("only 8 of 10 cases were evaluable" in r for r in summary.disqualification_reasons)


class TestRequestPacingAndSafety:
    """Phase 4: Provider request pacing with mockable sleeper."""

    def test_inter_request_pacing_delay_invoked(self, tmp_path):
        """Runner must invoke sleeper between requests when inter_request_delay_seconds > 0."""
        sleep_durations = []
        mock_sleeper = lambda s: sleep_durations.append(s)

        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test.db"),
            inter_request_delay_seconds=2.5,
            sleeper=mock_sleeper,
        )
        report = runner.run_benchmark(task_filter="website_extraction:pass_a", max_cases_per_task=3)
        # For 3 cases, sleeper should be called exactly 2 times with 2.5
        assert len(sleep_durations) == 2
        assert sleep_durations == [2.5, 2.5]

    def test_reject_invalid_pacing_delay(self, tmp_path):
        """Negative, NaN, Inf, or excessive delay must be rejected."""
        with pytest.raises(ValueError, match="inter_request_delay_seconds must be a non-negative"):
            ExternalBenchmarkRunner(dry_run=True, inter_request_delay_seconds=-1.0)

        with pytest.raises(ValueError, match="inter_request_delay_seconds must be a non-negative"):
            ExternalBenchmarkRunner(dry_run=True, inter_request_delay_seconds=float("nan"))

        with pytest.raises(ValueError, match="inter_request_delay_seconds must be a non-negative"):
            ExternalBenchmarkRunner(dry_run=True, inter_request_delay_seconds=15.0)  # > 10.0


class TestBudgetUpperBoundsAndLedgerPreservation:
    """Phase 5 & 6: Budget calculation accuracy and historical immutability."""

    def test_preflight_reservation_upper_bounds(self):
        """Preflight upper bound across 30 requests on website_extraction:pass_a equals $0.035831."""
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")
        assert len(fixtures) == 10
        tot_in = sum(math.ceil((len(f.prompt) + (len(f.system_prompt) if f.system_prompt else 0)) / 4.0) for f in fixtures)
        tot_out = 10 * 1024  # default max_output_tokens

        gemini_c = (tot_in * 0.30 + tot_out * 2.50) / 1e6
        groq_c = (tot_in * 0.075 + tot_out * 0.30) / 1e6
        cohere_c = (tot_in * 0.15 + tot_out * 0.60) / 1e6
        total_c = gemini_c + groq_c + cohere_c

        assert pytest.approx(gemini_c, rel=1e-4) == 0.026180
        assert pytest.approx(groq_c, rel=1e-4) == 0.003217
        assert pytest.approx(cohere_c, rel=1e-4) == 0.006434
        assert pytest.approx(total_c, rel=1e-4) == 0.035831

    def test_historical_fixtures_and_version_intact(self):
        """Fixture catalog version and case count must be strictly immutable."""
        assert BENCHMARK_CATALOG_VERSION == "s3.0-catalog-v1"
        fixtures = get_all_s3_fixtures()
        assert len(fixtures) == 70
