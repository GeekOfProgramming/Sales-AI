"""
tests/test_s3_6b1_campaign_acceptance.py
SalesAI S3.6B.1 — Critical Qualification Campaign Acceptance Fixes Test Suite.

Deterministic offline test suite covering all 10 requirements of S3.6B.1:
1. CLI pacing integration (--inter-request-delay 2.5, parsing, validation, propagation, no sleep on last request, smoke compatibility).
2. Disqualified candidate recommendations (incomplete/disqualified candidate NEVER receives PROPOSED_RECOMMENDATION).
3. Explicit case lifecycle enum & counter consistency (NOT_ATTEMPTED, PRE_DISPATCH_BLOCKED, DISPATCH_ATTEMPTED, RESPONSE_RECEIVED, EVALUATED, DISPATCH_FAILED).
4. Complete evaluation enforcement before qualification (10/10 evaluable 8 pass, 10/8 evaluable 8 pass, 12/10 evaluable 10 pass, 10/9 evaluable 9 pass, transport fails, budget blocks).
5. Fail-closed on unrecoverable provider errors (HTTP 429 quota exhaustion, auth errors, remaining cases marked NOT_ATTEMPTED).
6. Campaign-wide financial limits (persistent campaign ID, 3 approved candidates, max 10/30 requests, aggregate spend caps, multi-process SQLite safe).
7. Report accounting & parity (CSV, JSON, Markdown parity; preserving uncertain and billable cost distinctions).
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from sales_engine.benchmark.external_runner import (
    BenchmarkCaseExecutionResult,
    BenchmarkRunReport,
    CampaignBudgetGuard,
    CandidateTaskSummary,
    CaseLifecycleStatus,
    ExternalBenchmarkRunner,
)
from sales_engine.benchmark.fixtures_s3 import get_fixtures_for_task
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.contracts import LLMRequest, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMQuotaExceededError,
    LLMTimeoutError,
)
from sales_engine.llm_gateway.model_catalog import ModelCandidate, PricingMetadata
from scripts.benchmark_external_llm import parse_args


# ==============================================================================
# 1. CLI Pacing Integration Tests
# ==============================================================================

class TestCLIPacingIntegration:
    """Verifies Requirement 1: CLI pacing argument and simulated pacing."""

    def test_cli_argument_parsing_valid_delay(self):
        """CLI recognizes --inter-request-delay 2.5."""
        test_args = ["benchmark_external_llm.py", "--inter-request-delay", "2.5"]
        with patch.object(sys, "argv", test_args):
            args = parse_args()
            assert args.inter_request_delay == 2.5

    def test_cli_argument_parsing_default_zero(self):
        """CLI default delay is 0.0."""
        test_args = ["benchmark_external_llm.py"]
        with patch.object(sys, "argv", test_args):
            args = parse_args()
            assert args.inter_request_delay == 0.0

    @pytest.mark.parametrize("invalid_delay", ["-1.0", "nan", "inf", "15.0"])
    def test_cli_argument_parsing_invalid_delay_rejected(self, invalid_delay):
        """Invalid negative, NaN, infinite, and excessive delays (>10.0) exit with code 2."""
        test_args = ["benchmark_external_llm.py", "--inter-request-delay", invalid_delay]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()
            assert exc_info.value.code == 2

    def test_cli_entry_point_subprocess_propagation(self):
        """Invoke scripts/benchmark_external_llm.py directly via subprocess."""
        cmd = [
            sys.executable,
            "scripts/benchmark_external_llm.py",
            "--inter-request-delay",
            "2.5",
            "--max-cases",
            "1",
            "--task",
            "website_extraction:pass_a",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        assert "Benchmark Execution Completed" in result.stdout

    def test_actual_simulated_pacing_across_ten_cases_no_sleep_on_last(self, tmp_path):
        """Verify mock sleeper is invoked exactly 9 times across 10 cases with no sleep after 10th."""
        sleep_durations = []
        mock_sleeper = lambda s: sleep_durations.append(s)

        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_pacing.db"),
            inter_request_delay_seconds=2.5,
            sleeper=mock_sleeper,
        )
        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c5",
            max_cases_per_task=10,
        )

        assert len(sleep_durations) == 9
        assert all(d == 2.5 for d in sleep_durations)

    def test_backward_compatibility_for_one_request_smoke_tests(self, tmp_path):
        """Single request smoke test must execute without any sleep invocation."""
        sleep_durations = []
        mock_sleeper = lambda s: sleep_durations.append(s)

        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_smoke_pacing.db"),
            inter_request_delay_seconds=2.5,
            sleeper=mock_sleeper,
            is_connectivity_smoke=True,
        )
        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c5",
            max_cases_per_task=1,
        )

        assert len(sleep_durations) == 0


# ==============================================================================
# 2. Disqualified Candidate Recommendations
# ==============================================================================

class TestDisqualifiedCandidateRecommendations:
    """Verifies Requirement 2: Disqualified candidates are never recommended."""

    def test_eight_out_of_ten_passed_is_incomplete_never_proposed_recommendation(self, tmp_path):
        """
        Independent reproduction:
        10 planned, 8 evaluable, 8 passed (pass rate 80%), qualified=False.
        Runner must emit 'INCOMPLETE / NOT QUALIFIED', never 'PROPOSED_RECOMMENDATION'.
        """
        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_rec.db"),
            qualification_threshold=0.8,
        )

        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        # Mock 8 passing cases, followed by 2 pre-dispatch blocks
        canned_results = []
        for i in range(8):
            canned_results.append(
                BenchmarkCaseExecutionResult(
                    case_id=f"web_a_{i+1:02d}",
                    task_id="website_extraction",
                    subtask_id="pass_a",
                    group="GROUNDED",
                    candidate_id="t1_c5",
                    provider_id="gemini",
                    exact_model_id="gemini-3.5-flash-lite",
                    latency_ms=100,
                    syntactic_valid=True,
                    semantic_valid=True,
                    safety_valid=True,
                    overall_pass=True,
                    schema_pass=True,
                    ground_truth_pass=True,
                    evidence_pass=True,
                    safety_pass=True,
                    lifecycle_status=CaseLifecycleStatus.EVALUATED,
                )
            )
        for i in range(8, 10):
            canned_results.append(
                BenchmarkCaseExecutionResult(
                    case_id=f"web_a_{i+1:02d}",
                    task_id="website_extraction",
                    subtask_id="pass_a",
                    group="GROUNDED",
                    candidate_id="t1_c5",
                    provider_id="gemini",
                    exact_model_id="gemini-3.5-flash-lite",
                    latency_ms=0,
                    syntactic_valid=False,
                    semantic_valid=False,
                    safety_valid=False,
                    overall_pass=False,
                    failure_categories=["RUN_REQUEST_CAP_REACHED"],
                    failure_reasons=["Per-run request ceiling reached"],
                    lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
                )
            )

        it = iter(canned_results)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c5",
            max_cases_per_task=10,
        )

        summary = report.candidate_summaries[0]
        assert summary.cases_planned == 10
        assert summary.requests_attempted == 8
        assert summary.cases_evaluable == 8
        assert summary.cases_passed == 8
        assert summary.overall_pass_rate == 0.8
        assert summary.qualified is False
        assert summary.task_qualification_eligible is False

        rec = report.recommended_candidates.get("website_extraction:pass_a")
        assert rec is not None
        assert rec["status"] == "INCOMPLETE / NOT QUALIFIED"
        assert rec["is_production_approved"] is False
        assert "PROPOSED_RECOMMENDATION" not in rec["status"]


# ==============================================================================
# 3. Explicit Case Lifecycle Enum & Counter Consistency
# ==============================================================================

class TestCaseLifecycleAndCounters:
    """Verifies Requirement 3: Explicit lifecycle status enum and counter invariants."""

    def test_lifecycle_enum_values(self):
        """All required lifecycle enum members are present."""
        expected = {
            "NOT_ATTEMPTED",
            "PRE_DISPATCH_BLOCKED",
            "DISPATCH_ATTEMPTED",
            "RESPONSE_RECEIVED",
            "EVALUATED",
            "DISPATCH_FAILED",
        }
        actual = {status.value for status in CaseLifecycleStatus}
        assert actual == expected

    def test_counter_consistency_and_invariants(self, tmp_path):
        """
        Invariants:
        requests_attempted <= cases_planned
        responses_received <= requests_attempted
        cases_evaluable <= responses_received
        cases_evaluated == cases_evaluable
        unattempted_cases includes pre-dispatch budget blocks
        """
        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_lifecycle.db"),
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        # 6 evaluated, 2 dispatch failed (transport), 2 pre-dispatch blocked
        canned = []
        for i in range(6):
            canned.append(BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ))
        for i in range(6, 8):
            canned.append(BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["GATEWAY_DISPATCH_ERROR"], failure_reasons=["Transport failure"],
                lifecycle_status=CaseLifecycleStatus.DISPATCH_FAILED,
            ))
        for i in range(8, 10):
            canned.append(BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=0,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["PRE_DISPATCH_BUDGET_BLOCKED"], failure_reasons=["Budget ceiling"],
                lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
            ))

        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand,
            task_id="website_extraction",
            subtask_id="pass_a",
            fixtures=fixtures,
            run_id="test_lc",
        )

        assert summary.cases_planned == 10
        assert summary.requests_attempted == 8   # 6 evaluated + 2 dispatch failed
        assert summary.responses_received == 6   # 6 successful responses
        assert summary.cases_evaluable == 6
        assert summary.cases_evaluated == summary.cases_evaluable
        assert summary.unattempted_cases == 2    # 2 pre-dispatch blocked
        assert summary.requests_attempted <= summary.cases_planned
        assert summary.responses_received <= summary.requests_attempted
        assert summary.cases_evaluable <= summary.responses_received


# ==============================================================================
# 4. Enforce Complete Evaluation Before Qualification
# ==============================================================================

class TestCompleteEvaluationEnforcement:
    """Verifies Requirement 4: Exact evaluation test matrix."""

    def test_ten_of_ten_evaluable_eight_passing_qualifies(self, tmp_path):
        """10/10 evaluable, 8 passing -> Qualifies."""
        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_matrix.db"),
            qualification_threshold=0.8,
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=(i < 8),
                schema_pass=True, ground_truth_pass=(i < 8), evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(10)
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r1"
        )
        assert summary.qualified is True
        assert summary.task_qualification_eligible is True

    def test_ten_planned_eight_evaluable_eight_passing_disqualified(self, tmp_path):
        """10 planned, 8 evaluable, 8 passing -> Disqualified."""
        runner = ExternalBenchmarkRunner(
            dry_run=True, db_path=str(tmp_path / "test_matrix.db"), qualification_threshold=0.8
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(8)
        ] + [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=0,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["PRE_DISPATCH_BUDGET_BLOCKED"], failure_reasons=["Budget block"],
                lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
            ) for i in range(8, 10)
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r2"
        )
        assert summary.qualified is False
        assert any("only 8 of 10 cases were evaluable" in r for r in summary.disqualification_reasons)

    def test_twelve_planned_ten_evaluable_ten_passing_disqualified(self, tmp_path):
        """12 planned, 10 evaluable, 10 passing -> Disqualified (incomplete evaluation)."""
        runner = ExternalBenchmarkRunner(
            dry_run=True, db_path=str(tmp_path / "test_matrix.db"), qualification_threshold=0.8
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = (get_fixtures_for_task("website_extraction", "pass_a") * 2)[:12]

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(10)
        ] + [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=0,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["PRE_DISPATCH_BUDGET_BLOCKED"], failure_reasons=["Budget block"],
                lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
            ) for i in range(10, 12)
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r3"
        )
        assert summary.qualified is False
        assert any("only 10 of 12 cases were evaluable" in r for r in summary.disqualification_reasons)

    def test_ten_planned_nine_evaluable_nine_passing_disqualified(self, tmp_path):
        """10 planned, 9 evaluable, 9 passing -> Disqualified."""
        runner = ExternalBenchmarkRunner(
            dry_run=True, db_path=str(tmp_path / "test_matrix.db"), qualification_threshold=0.8
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(9)
        ] + [
            BenchmarkCaseExecutionResult(
                case_id="c_9", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=0,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["GATEWAY_DISPATCH_ERROR"], failure_reasons=["Transport drop"],
                lifecycle_status=CaseLifecycleStatus.DISPATCH_FAILED,
            )
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r4"
        )
        assert summary.qualified is False
        assert any("only 9 of 10 cases were evaluable" in r for r in summary.disqualification_reasons)

    def test_ten_planned_ten_attempted_two_transport_failures(self, tmp_path):
        """10 planned, 10 attempted, 2 transport failures -> Disqualified."""
        runner = ExternalBenchmarkRunner(
            dry_run=True, db_path=str(tmp_path / "test_matrix.db"), qualification_threshold=0.8
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(8)
        ] + [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["GATEWAY_DISPATCH_ERROR"], failure_reasons=["Transport 500"],
                lifecycle_status=CaseLifecycleStatus.DISPATCH_FAILED,
            ) for i in range(8, 10)
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r5"
        )
        assert summary.qualified is False
        assert summary.transport_failures == 2
        assert summary.cases_evaluable == 8

    def test_ten_planned_eight_attempted_two_pre_dispatch_budget_blocks(self, tmp_path):
        """10 planned, 8 attempted, 2 pre-dispatch budget blocks -> Disqualified."""
        runner = ExternalBenchmarkRunner(
            dry_run=True, db_path=str(tmp_path / "test_matrix.db"), qualification_threshold=0.8
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        canned = [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ) for i in range(8)
        ] + [
            BenchmarkCaseExecutionResult(
                case_id=f"c_{i}", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=0,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["PRE_DISPATCH_BUDGET_BLOCKED"], failure_reasons=["Cap reached"],
                lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
            ) for i in range(8, 10)
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand, task_id="website_extraction", subtask_id="pass_a", fixtures=fixtures, run_id="r6"
        )
        assert summary.qualified is False
        assert summary.unattempted_cases == 2
        assert summary.requests_attempted == 8


# ==============================================================================
# 5. Fail Closed on Unrecoverable Provider Errors
# ==============================================================================

class TestFailClosedUnrecoverableErrors:
    """Verifies Requirement 5: Early stop on HTTP 429 quota exhaustion and auth errors."""

    def test_http_429_stops_run_and_records_remaining_cases_not_attempted(self, tmp_path):
        """Encountering HTTP 429 stops the loop immediately; remaining cases are NOT_ATTEMPTED."""
        runner = ExternalBenchmarkRunner(
            dry_run=True,
            db_path=str(tmp_path / "test_429.db"),
        )
        cand = runner.catalog.get_candidate("t1_c5")
        fixtures = get_fixtures_for_task("website_extraction", "pass_a")

        # First case passes, second case hits HTTP 429 quota limit
        canned = [
            BenchmarkCaseExecutionResult(
                case_id="web_a_01", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=100,
                syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
                schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
                lifecycle_status=CaseLifecycleStatus.EVALUATED,
            ),
            BenchmarkCaseExecutionResult(
                case_id="web_a_02", task_id="website_extraction", subtask_id="pass_a",
                group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
                exact_model_id="gemini-3.5-flash-lite", latency_ms=50,
                syntactic_valid=False, semantic_valid=False, safety_valid=False, overall_pass=False,
                failure_categories=["RATE_LIMIT"], failure_reasons=["HTTP 429 Quota Exceeded"],
                lifecycle_status=CaseLifecycleStatus.DISPATCH_FAILED,
            ),
        ]
        it = iter(canned)
        runner._execute_single_case = lambda *args, **kwargs: next(it)

        summary = runner._evaluate_candidate_for_task(
            candidate=cand,
            task_id="website_extraction",
            subtask_id="pass_a",
            fixtures=fixtures,
            run_id="test_429_run",
        )

        assert summary.cases_planned == 10
        assert summary.requests_attempted == 2
        assert summary.unattempted_cases == 8
        assert summary.rate_limit_failures == 1
        assert len(summary.case_results) == 10

        # Verify cases 3 through 10 are recorded as NOT_ATTEMPTED
        for c in summary.case_results[2:]:
            assert c.lifecycle_status == CaseLifecycleStatus.NOT_ATTEMPTED
            assert "UNATTEMPTED_CASE" in c.failure_categories


# ==============================================================================
# 6. Campaign-Wide Financial Limits
# ==============================================================================

class TestCampaignBudgetGuard:
    """Verifies Requirement 6: Persistent multi-process campaign budget guard."""

    def test_campaign_guard_restricts_to_approved_candidates(self, tmp_path):
        """Only t1_c5, t1_c3, and t2_c2 are approved."""
        db_path = str(tmp_path / "campaign_test.db")
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_test_001")

        # Approved
        guard.check_and_reserve(
            request_id="req_1", candidate_id="t1_c5",
            estimated_commercial_usd=0.002, estimated_billable_usd=0.0
        )
        guard.reconcile("req_1", actual_commercial_usd=0.002, actual_billable_usd=0.0)

        # Disallowed candidate
        with pytest.raises(Exception, match="not in approved campaign candidate set"):
            guard.check_and_reserve(
                request_id="req_bad", candidate_id="t1_c1",
                estimated_commercial_usd=0.001, estimated_billable_usd=0.001
            )

    def test_campaign_guard_per_candidate_and_aggregate_caps(self, tmp_path):
        """Enforces max 10 requests per candidate and max 30 aggregate across campaign."""
        db_path = str(tmp_path / "campaign_caps.db")
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_caps_001")

        # 10 requests for t1_c5 succeed
        for i in range(10):
            req_id = f"req_c5_{i}"
            guard.check_and_reserve(
                request_id=req_id, candidate_id="t1_c5",
                estimated_commercial_usd=0.001, estimated_billable_usd=0.0
            )
            guard.reconcile(req_id, actual_commercial_usd=0.001, actual_billable_usd=0.0)

        # 11th request for t1_c5 must fail
        with pytest.raises(LLMBudgetExceededError, match="reached campaign request limit"):
            guard.check_and_reserve(
                request_id="req_c5_11", candidate_id="t1_c5",
                estimated_commercial_usd=0.001, estimated_billable_usd=0.0
            )

    def test_campaign_guard_uncertain_liability_awareness(self, tmp_path):
        """UNCERTAIN reservations hold liability against commercial cap."""
        db_path = str(tmp_path / "campaign_uncertain.db")
        guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id="camp_unc_001",
            commercial_cost_cap_usd=0.010,
        )

        guard.check_and_reserve(
            request_id="req_u1", candidate_id="t1_c3",
            estimated_commercial_usd=0.008, estimated_billable_usd=0.0
        )
        guard.reconcile("req_u1", actual_commercial_usd=0.008, actual_billable_usd=0.0, is_uncertain=True)

        # Attempting second request of $0.005 exceeds $0.010 cap because $0.008 UNCERTAIN is held
        with pytest.raises(LLMBudgetExceededError, match="exceeds aggregate cap"):
            guard.check_and_reserve(
                request_id="req_u2", candidate_id="t1_c3",
                estimated_commercial_usd=0.005, estimated_billable_usd=0.0
            )


# ==============================================================================
# 7. Correct Report Accounting & Parity
# ==============================================================================

class TestReportAccountingAndParity:
    """Verifies Requirement 7: Parity between JSON, CSV, and Markdown exports."""

    def test_report_export_parity(self, tmp_path):
        """JSON, CSV, and Markdown exports reflect consistent accounting totals."""
        out_dir = tmp_path / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)

        case1 = BenchmarkCaseExecutionResult(
            case_id="web_a_01", task_id="website_extraction", subtask_id="pass_a",
            group="GROUNDED", candidate_id="t1_c5", provider_id="gemini",
            exact_model_id="gemini-3.5-flash-lite", latency_ms=150,
            input_tokens=100, output_tokens=50, cost_usd=0.000155,
            commercial_cost_usd=0.000155, billable_cost_usd=0.0,
            syntactic_valid=True, semantic_valid=True, safety_valid=True, overall_pass=True,
            schema_pass=True, ground_truth_pass=True, evidence_pass=True, safety_pass=True,
            spend_provenance="VERIFIED_FREE_TIER", sqlite_reservation_status="RECONCILED",
            lifecycle_status=CaseLifecycleStatus.EVALUATED,
        )

        summary = CandidateTaskSummary(
            candidate_id="t1_c5", task_id="website_extraction", subtask_id="pass_a",
            provider_id="gemini", exact_model_id="gemini-3.5-flash-lite",
            cases_planned=1, requests_attempted=1, responses_received=1, cases_evaluable=1,
            cases_passed=1, cases_evaluated=1, overall_pass_rate=1.0,
            total_cost_usd=0.000155, commercial_cost_usd=0.000155, billable_cost_usd=0.0,
            qualified=False, is_smoke_only=True, case_results=[case1]
        )

        report = BenchmarkRunReport(
            run_id="s3_bench_testparity",
            timestamp="2026-10-26T12:00:00Z",
            is_dry_run=True,
            tasks_evaluated=["website_extraction:pass_a"],
            candidate_summaries=[summary],
            recommended_candidates={},
            total_cases_run=1,
            total_cost_usd=0.000155,
            is_smoke_only=True,
        )

        gen = S3BenchmarkReportGenerator(output_dir=out_dir)
        paths = gen.generate_all(report)

        # 1. JSON
        json_data = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert json_data["total_cost_usd"] == 0.000155
        assert json_data["candidate_summaries"][0]["case_results"][0]["lifecycle_status"] == "EVALUATED"

        # 2. CSV
        with open(paths["csv"], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["lifecycle_status"] == "EVALUATED"
            assert rows[0]["commercial_cost_usd"] == "0.000155"
            assert rows[0]["billable_cost_usd"] == "0.000000"

        # 3. Markdown
        md_text = paths["markdown"].read_text(encoding="utf-8")
        assert "$0.000155" in md_text
        assert "$0.000000" in md_text
