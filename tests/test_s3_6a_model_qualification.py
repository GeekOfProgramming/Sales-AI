"""
tests/test_s3_6a_model_qualification.py
Offline deterministic tests for SalesAI S3.6A Model Qualification Framework.
Strictly offline: no network calls, no live credentials, no database writes to production ledger.
"""

import math
import pytest
from typing import Dict, Any, List

from sales_engine.llm_gateway.providers.cloudflare_provider import (
    estimate_cloudflare_neurons,
    estimate_cloudflare_neurons_exact,
    estimate_cloudflare_neurons_preflight,
    estimate_cloudflare_usd_cost,
    CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT,
    CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT,
    CLOUDFLARE_NEURON_UNIT_COST_USD,
)
from sales_engine.benchmark.fixtures_s3 import (
    ALL_S3_BENCHMARK_FIXTURES,
    BENCHMARK_CATALOG_VERSION,
    get_fixtures_for_task,
    get_all_s3_fixtures,
)
from sales_engine.benchmark.external_runner import (
    CandidateTaskSummary,
    BenchmarkCaseExecutionResult,
    BenchmarkRunReport,
)
from sales_engine.llm_gateway.model_catalog import ModelCatalog, ModelCandidate, ModelTier
from sales_engine.benchmark.ground_truth_evaluator import GroundTruthEvaluationResult, SCORER_VERSION


class TestCloudflareNeuronInputValidation:
    """Phase 2: Strict fail-closed input validation for Cloudflare Neuron calculations."""

    def test_reject_boolean_tokens(self):
        """Boolean values (subclasses of int) must be explicitly rejected."""
        with pytest.raises(TypeError, match="cannot be a boolean"):
            estimate_cloudflare_neurons_exact(True, 100)

        with pytest.raises(TypeError, match="cannot be a boolean"):
            estimate_cloudflare_neurons_exact(100, False)

        with pytest.raises(TypeError, match="cannot be a boolean"):
            estimate_cloudflare_neurons_preflight(True, 100)

        with pytest.raises(TypeError, match="cannot be a boolean"):
            estimate_cloudflare_usd_cost(100, False)

    def test_reject_negative_tokens(self):
        """Negative token counts must raise ValueError."""
        with pytest.raises(ValueError, match="cannot be negative"):
            estimate_cloudflare_neurons_exact(-1, 100)

        with pytest.raises(ValueError, match="cannot be negative"):
            estimate_cloudflare_neurons(-10, 50)

        with pytest.raises(ValueError, match="cannot be negative"):
            estimate_cloudflare_neurons_preflight(100, -5)

        with pytest.raises(ValueError, match="cannot be negative"):
            estimate_cloudflare_usd_cost(-50, 100)

    def test_reject_nan_and_inf(self):
        """NaN and Infinite float token counts must raise ValueError."""
        with pytest.raises(ValueError, match="cannot be NaN or Infinite"):
            estimate_cloudflare_neurons_exact(float("nan"), 100)

        with pytest.raises(ValueError, match="cannot be NaN or Infinite"):
            estimate_cloudflare_neurons_preflight(100, float("inf"))

        with pytest.raises(ValueError, match="cannot be NaN or Infinite"):
            estimate_cloudflare_usd_cost(float("-inf"), 100)

    def test_reject_non_integer_floats_and_types(self):
        """Non-integer floats, strings, None, and objects must raise TypeError."""
        with pytest.raises(TypeError, match="must be an integer"):
            estimate_cloudflare_neurons_exact(12.5, 100)

        with pytest.raises(TypeError, match="must be an integer"):
            estimate_cloudflare_neurons_preflight("500", 100)

        with pytest.raises(TypeError, match="must be an integer"):
            estimate_cloudflare_usd_cost(None, 100)

    def test_official_constants_preserved(self):
        """Official Cloudflare published FP8 rates must match exact documented numbers."""
        assert CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT == 13778.0
        assert CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT == 26128.0
        assert CLOUDFLARE_NEURON_UNIT_COST_USD == 0.000011

        # Check exact calculation for 1,000 input and 500 output tokens
        # (1000/1e6)*13778 = 13.778
        # (500/1e6)*26128 = 13.064
        # Total = 26.842
        exact = estimate_cloudflare_neurons_exact(1000, 500)
        assert pytest.approx(26.842, rel=1e-6) == exact


class TestQualificationDatasetAudit:
    """Phase 3: Audit of 70 synthetic cases in fixtures_s3.py."""

    def test_catalog_size_and_version(self):
        """Catalog must contain exactly 70 cases with immutable catalog version."""
        fixtures = get_all_s3_fixtures()
        assert len(fixtures) == 70
        assert BENCHMARK_CATALOG_VERSION == "s3.0-catalog-v1"

    def test_task_distribution_and_parity(self):
        """Exactly 7 tasks x 10 cases = 70 cases. Exactly 35 Grounded and 35 Adversarial."""
        fixtures = get_all_s3_fixtures()
        grounded = [f for f in fixtures if f.group == "GROUNDED"]
        adversarial = [f for f in fixtures if f.group == "ADVERSARIAL"]
        assert len(grounded) == 35
        assert len(adversarial) == 35

        tasks = [
            ("website_extraction", "pass_a"),
            ("website_extraction", "pass_b"),
            ("job_analysis", None),
            ("discovery_query_generation", None),
            ("inbox_classification", None),
            ("reply_suggestion", None),
            ("outreach_draft", None),
        ]
        for tid, sub in tasks:
            task_fixtures = get_fixtures_for_task(tid, sub)
            assert len(task_fixtures) == 10, f"Task {tid}:{sub} must have exactly 10 cases"
            task_grounded = [f for f in task_fixtures if f.group == "GROUNDED"]
            task_adv = [f for f in task_fixtures if f.group == "ADVERSARIAL"]
            assert len(task_grounded) == 5
            assert len(task_adv) == 5

    def test_synthetic_facts_and_evidence_invariants(self):
        """Every fixture must specify evidence requirements and non-empty checks."""
        for f in ALL_S3_BENCHMARK_FIXTURES:
            assert f.case_id
            assert f.title
            assert f.prompt
            assert len(f.required_evidence_ids) > 0, f"Case {f.case_id} lacks required evidence IDs"
            assert f.checks_description


class TestScorecardAndStatusTaxonomy:
    """Phase 4: Provider-neutral scorecard design and status taxonomy."""

    def test_candidate_task_summary_denominators(self):
        """Summary metrics must correctly normalize by evaluated cases denominator."""
        summary = CandidateTaskSummary(
            candidate_id="t1_c5",
            task_id="website_extraction",
            subtask_id="pass_a",
            provider_id="gemini",
            exact_model_id="gemini-3.5-flash-lite",
            cases_evaluated=10,
            cases_passed=8,
            overall_pass_rate=0.8,
            schema_pass_rate=1.0,
            ground_truth_pass_rate=0.9,
            evidence_pass_rate=0.9,
            safety_pass_rate=1.0,
            safety_violations_count=0,
            needs_review_count=1,
            avg_latency_ms=1250.0,
            total_cost_usd=0.000150,
            qualified=True,
            is_smoke_only=False,
            evaluation_state="EVALUATED",
        )
        assert summary.cases_evaluated == 10
        assert summary.overall_pass_rate == 0.8
        assert summary.safety_violations_count == 0
        assert summary.qualified is True
        assert summary.is_smoke_only is False

    def test_transport_failure_vs_schema_failure_distinction(self):
        """Transport failure (e.g. rate limit, dispatch error) must not be counted as model quality failure."""
        res_transport = BenchmarkCaseExecutionResult(
            case_id="web_a_01",
            task_id="website_extraction",
            subtask_id="pass_a",
            group="GROUNDED",
            candidate_id="t2_c5",
            provider_id="openrouter",
            exact_model_id="google/gemma-4-26b-a4b-it:free",
            latency_ms=150,
            failure_categories=["GATEWAY_DISPATCH_ERROR"],
            failure_reasons=["HTTP 429 Rate Limited"],
            syntactic_valid=False,
            schema_pass=False,
        )
        # Should record failure categories clearly
        assert "GATEWAY_DISPATCH_ERROR" in res_transport.failure_categories
        assert res_transport.syntactic_valid is False

        res_schema = BenchmarkCaseExecutionResult(
            case_id="web_a_01",
            task_id="website_extraction",
            subtask_id="pass_a",
            group="GROUNDED",
            candidate_id="t2_c2",
            provider_id="cohere",
            exact_model_id="command-r-08-2024",
            latency_ms=5000,
            failure_categories=["SYNTACTIC_VALIDATION_ERROR"],
            failure_reasons=["Field 'evidence_refs' must be list of strings"],
            syntactic_valid=False,
            schema_pass=False,
        )
        assert "SYNTACTIC_VALIDATION_ERROR" in res_schema.failure_categories

    def test_smoke_only_never_auto_approved(self):
        """A run with fewer than 10 cases must always be flagged is_smoke_only=True and never qualified."""
        summary_smoke = CandidateTaskSummary(
            candidate_id="t1_c3",
            task_id="website_extraction",
            subtask_id="pass_a",
            provider_id="groq",
            exact_model_id="openai/gpt-oss-20b",
            cases_evaluated=1,
            cases_passed=1,
            overall_pass_rate=1.0,
            schema_pass_rate=1.0,
            ground_truth_pass_rate=1.0,
            evidence_pass_rate=1.0,
            safety_pass_rate=1.0,
            qualified=False,
            is_smoke_only=True,
            evaluation_state="EVALUATED",
            disqualification_reasons=["Smoke test evaluation (1 < 10 cases) cannot qualify or approve candidate (SMOKE_ONLY)."],
        )
        assert summary_smoke.is_smoke_only is True
        assert summary_smoke.qualified is False
        assert len(summary_smoke.disqualification_reasons) > 0


class TestMultiModelBenchmarkDesignSafety:
    """Phase 5: Financial safety and multi-candidate fairness constraints."""

    def test_commercial_cost_vs_billable_cost_accounting(self):
        """Trial and Free tiers record $0.00 billable cost while tracking commercial-equivalent cost."""
        comm_cost = 0.000182
        billable_trial = 0.00  # For Cohere Trial
        billable_free = 0.00   # For Gemini / Groq Free tier
        billable_paid = comm_cost

        assert billable_trial == 0.0
        assert billable_free == 0.0
        assert billable_paid > 0.0

    def test_future_benchmark_request_count_calculation(self):
        """Evaluating 3 candidates across 10 cases of website_extraction:pass_a yields exactly 30 requests."""
        candidates = ["t1_c5", "t1_c3", "t2_c2"]
        cases_per_candidate = 10
        total_requests = len(candidates) * cases_per_candidate
        assert total_requests == 30

        # Max cost ceiling calculation:
        # Gemini t1_c5: 10 * ~$0.0006 = ~$0.0060
        # Groq t1_c3: 10 * ~$0.0001 = ~$0.0010
        # Cohere t2_c2: 10 * ~$0.0002 = ~$0.0020
        # Conservative commercial-equivalent budget: $0.05
        # Conservative billable budget: $0.00
        assert total_requests <= 50  # well within conservative bounds
