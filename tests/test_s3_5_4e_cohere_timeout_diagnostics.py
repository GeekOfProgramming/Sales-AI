"""
tests/test_s3_5_4e_cohere_timeout_diagnostics.py
Offline deterministic test suite for S3.5.4E:
- Timeout exception categorization (connect, read, write, pool)
- CohereProvider timeout handling with granular diagnostics
- Runtime budget guard ledger status preservation on timeout
- Strict bounded validation of --cohere-trial-timeout-seconds
- Non-interference with standard workflows and other providers
- No credential or prompt disclosure in timeout diagnostics
"""

import math
import pytest
import httpx

from sales_engine.llm_gateway.contracts import LLMRequest, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMTimeoutError,
    LLMNotConfiguredError,
    LLMBudgetExceededError,
)
from sales_engine.llm_gateway.providers.cohere_provider import (
    CohereProvider,
    classify_httpx_timeout,
)
from sales_engine.llm_gateway.model_catalog import ModelCandidate, PricingMetadata
from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    ReservationStatus,
)
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard
from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.fixtures_s3 import PASS_A_CASES


# ===========================================================================
# 1. Timeout Classification Unit Tests
# ===========================================================================

def test_classify_httpx_timeout_categories():
    """Verify granular categorization of HTTPX timeout exception subclasses."""
    req = httpx.Request("POST", "https://api.cohere.com/v2/chat")

    connect_err = httpx.ConnectTimeout("Connection timed out", request=req)
    read_err = httpx.ReadTimeout("Read timed out after 30s", request=req)
    write_err = httpx.WriteTimeout("Write timed out", request=req)
    pool_err = httpx.PoolTimeout("Pool acquisition timed out", request=req)
    generic_err = httpx.TimeoutException("Generic timeout", request=req)

    assert classify_httpx_timeout(connect_err) == "CONNECT_TIMEOUT"
    assert classify_httpx_timeout(read_err) == "READ_TIMEOUT"
    assert classify_httpx_timeout(write_err) == "WRITE_TIMEOUT"
    assert classify_httpx_timeout(pool_err) == "POOL_TIMEOUT"
    assert classify_httpx_timeout(generic_err) == "REQUEST_TIMEOUT"


# ===========================================================================
# 2. CohereProvider Granular Timeout Handling (Sync & Async)
# ===========================================================================

def test_cohere_provider_sync_timeout_classification():
    """Verify CohereProvider.generate attaches correct timeout category and sanitized message."""
    def timeout_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Mock upstream read stalled", request=request)

    transport = httpx.MockTransport(timeout_transport)
    provider = CohereProvider(
        provider_id="cohere",
        api_key="mock-trial-key",
        transport=transport,
    )

    req = LLMRequest(
        prompt="Extract company details",
        task_id="website_extraction",
        model="command-r-08-2024",
        provider="cohere",
        timeout_seconds=30.0,
    )

    with pytest.raises(LLMTimeoutError) as exc_info:
        provider.generate(req)

    err = exc_info.value
    assert err.timeout_category == "READ_TIMEOUT"
    assert "READ_TIMEOUT" in str(err)
    assert "mock-trial-key" not in str(err)
    assert "Extract company details" not in str(err)


@pytest.mark.asyncio
async def test_cohere_provider_async_timeout_classification():
    """Verify CohereProvider.generate_async attaches correct timeout category."""
    async def async_timeout_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Mock TLS handshake timed out", request=request)

    transport = httpx.MockTransport(async_timeout_transport)
    provider = CohereProvider(
        provider_id="cohere",
        api_key="mock-trial-key",
        async_transport=transport,
    )

    req = LLMRequest(
        prompt="Extract company details",
        task_id="website_extraction",
        model="command-r-08-2024",
        provider="cohere",
        timeout_seconds=60.0,
    )

    with pytest.raises(LLMTimeoutError) as exc_info:
        await provider.generate_async(req)

    err = exc_info.value
    assert err.timeout_category == "CONNECT_TIMEOUT"
    assert "CONNECT_TIMEOUT" in str(err)
    assert "mock-trial-key" not in str(err)


# ===========================================================================
# 3. Runtime Guard & Ledger Reservation on Timeout
# ===========================================================================

def test_runtime_guard_ledger_uncertain_on_timeout():
    """Verify RuntimeGuard marks SQLite reservation UNCERTAIN with granular timeout code."""
    config = BudgetPolicyConfig(
        enabled=True,
        max_request_spend_usd=0.05,
        daily_limit_usd=1.00,
        monthly_limit_usd=15.00,
        max_concurrent_requests=1,
    )
    ledger = BudgetLedger(db_path=":memory:", config=config)
    guard = RuntimeGuard(ledger=ledger, config=config)

    candidate = ModelCandidate(
        candidate_id="t2_c2",
        provider_id="cohere",
        exact_model_id="command-r-08-2024",
        tier=ModelTier.TIER_2_BALANCED,
        priority=2,
        enabled=True,
        pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60, currency="USD"),
        allowed_tasks=["website_extraction"],
        verified_capabilities={"supports_json_schema": True},
    )

    req = LLMRequest(
        prompt="Test prompt",
        task_id="website_extraction",
        model="command-r-08-2024",
        provider="cohere",
        tier=ModelTier.TIER_2_BALANCED,
        request_id="bench_test_timeout_001",
        timeout_seconds=30.0,
    )

    def failing_call(request: LLMRequest):
        raise LLMTimeoutError(
            message="Request timed out after 30.0s (READ_TIMEOUT).",
            provider="cohere",
            task_id="website_extraction",
            timeout_category="READ_TIMEOUT",
        )

    with pytest.raises(LLMTimeoutError):
        guard.guarded_call(request=req, candidate=candidate, call_fn=failing_call)

    # Check ledger: reservation must be UNCERTAIN, failure_code must be TIMEOUT_READ_TIMEOUT
    with ledger._lock:
        conn = ledger._get_connection()
        cursor = conn.execute(
            "SELECT status, failure_code, reserved_usd, final_cost_usd FROM llm_reservations WHERE request_id = ?",
            ("bench_test_timeout_001",),
        )
        row = cursor.fetchone()
        assert row is not None
        status, fail_code, reserved_usd, final_cost_usd = row
        assert status == ReservationStatus.UNCERTAIN.value
        assert fail_code == "TIMEOUT_READ_TIMEOUT"
        assert reserved_usd > 0.0
        assert final_cost_usd == reserved_usd


# ===========================================================================
# 4. Cohere-Specific Bounded Timeout Setting Validation
# ===========================================================================

def test_cohere_trial_timeout_valid():
    """Verify valid values up to 60.0 are accepted when cohere and trial are configured."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        require_cohere_trial=True,
        provider_filter="cohere",
        cohere_trial_timeout_seconds=60.0,
    )
    assert runner.cohere_trial_timeout_seconds == 60.0

    runner45 = ExternalBenchmarkRunner(
        dry_run=True,
        require_cohere_trial=True,
        provider_filter="cohere",
        cohere_trial_timeout_seconds=45.5,
    )
    assert runner45.cohere_trial_timeout_seconds == 45.5


@pytest.mark.parametrize("invalid_timeout", [
    0,
    0.0,
    -1.0,
    -30.0,
    60.1,
    120.0,
    float("nan"),
    float("inf"),
    float("-inf"),
    "60",
    True,
    False,
])
def test_cohere_trial_timeout_rejects_invalid_values(invalid_timeout):
    """Verify non-positive, >60s, NaN, infinite, and boolean values are strictly rejected."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(
            dry_run=True,
            require_cohere_trial=True,
            provider_filter="cohere",
            cohere_trial_timeout_seconds=invalid_timeout,
        )
    assert "COHERE_TIMEOUT_GUARD_VIOLATION" in str(exc_info.value)


def test_cohere_trial_timeout_rejects_without_require_cohere_trial():
    """Verify timeout override cannot be set without --require-cohere-trial."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(
            dry_run=True,
            require_cohere_trial=False,
            provider_filter="cohere",
            cohere_trial_timeout_seconds=60.0,
        )
    assert "COHERE_TIMEOUT_GUARD_VIOLATION" in str(exc_info.value)
    assert "requires --require-cohere-trial" in str(exc_info.value)


def test_cohere_trial_timeout_rejects_non_cohere_provider():
    """Verify timeout override cannot be applied to other providers."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(
            dry_run=True,
            require_cohere_trial=True,
            provider_filter="openai",
            cohere_trial_timeout_seconds=60.0,
        )
    assert "COHERE_TIMEOUT_GUARD_VIOLATION" in str(exc_info.value)


# ===========================================================================
# 5. Propagation to LLMRequest and Isolation
# ===========================================================================

def test_cohere_trial_timeout_propagates_to_cohere_request():
    """Verify cohere_trial_timeout_seconds sets request.timeout_seconds for t2_c2."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        require_cohere_trial=True,
        provider_filter="cohere",
        cohere_trial_timeout_seconds=55.0,
    )

    candidate = ModelCandidate(
        candidate_id="t2_c2",
        provider_id="cohere",
        exact_model_id="command-r-08-2024",
        tier=ModelTier.TIER_2_BALANCED,
        priority=2,
        enabled=True,
        pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60, currency="USD"),
        allowed_tasks=["website_extraction"],
        verified_capabilities={"supports_json_schema": True},
    )

    fixture = PASS_A_CASES[0]

    captured_requests = []
    original_generate = runner.gateway.generate

    def intercepting_generate(request, candidate, is_benchmark=False):
        captured_requests.append(request)
        return LLMResponse(
            content='{"status": "ok"}',
            provider=candidate.provider_id,
            model=candidate.exact_model_id,
            task_id="website_extraction",
            input_tokens=10,
            output_tokens=10,
        )

    runner.gateway.generate = intercepting_generate

    runner._execute_single_case(candidate=candidate, fixture=fixture, run_id="test_prop")

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.timeout_seconds == 55.0


def test_timeout_remains_default_for_other_candidates():
    """Verify other candidates/providers remain pinned to default 30.0s timeout."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        provider_filter=None,
    )

    candidate = ModelCandidate(
        candidate_id="t1_c5",
        provider_id="gemini",
        exact_model_id="gemini-3.5-flash-lite",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        enabled=True,
        pricing=PricingMetadata(input_per_1m=0.075, output_per_1m=0.30, currency="USD"),
        allowed_tasks=["website_extraction"],
        verified_capabilities={"supports_json_schema": True},
    )

    fixture = PASS_A_CASES[0]

    captured_requests = []

    def intercepting_generate(request, candidate, is_benchmark=False):
        captured_requests.append(request)
        return LLMResponse(
            content='{"status": "ok"}',
            provider=candidate.provider_id,
            model=candidate.exact_model_id,
            task_id="website_extraction",
            input_tokens=10,
            output_tokens=10,
        )

    runner.gateway.generate = intercepting_generate

    runner._execute_single_case(candidate=candidate, fixture=fixture, run_id="test_default")

    assert len(captured_requests) == 1
    assert captured_requests[0].timeout_seconds == 30.0


# ===========================================================================
# 6. Runner Timeout Result Diagnostics & Accounting
# ===========================================================================

def test_runner_handles_timeout_as_uncertain_with_category():
    """Verify runner marks timed out case as UNCERTAIN with granular TIMEOUT category."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        require_cohere_trial=True,
        provider_filter="cohere",
        cohere_trial_timeout_seconds=60.0,
    )

    candidate = ModelCandidate(
        candidate_id="t2_c2",
        provider_id="cohere",
        exact_model_id="command-r-08-2024",
        tier=ModelTier.TIER_2_BALANCED,
        priority=2,
        enabled=True,
        pricing=PricingMetadata(input_per_1m=0.15, output_per_1m=0.60, currency="USD"),
        allowed_tasks=["website_extraction"],
        verified_capabilities={"supports_json_schema": True},
    )

    fixture = PASS_A_CASES[0]

    def timing_out_generate(request, candidate, is_benchmark=False):
        raise LLMTimeoutError(
            message="Request timed out after 60.0s (READ_TIMEOUT).",
            provider="cohere",
            task_id="website_extraction",
            timeout_category="READ_TIMEOUT",
        )

    runner.gateway.generate = timing_out_generate

    result = runner._execute_single_case(candidate=candidate, fixture=fixture, run_id="test_timeout_res")

    assert result.overall_pass is False
    assert result.sqlite_reservation_status == "UNCERTAIN"
    assert "TIMEOUT_READ_TIMEOUT" in result.failure_categories
    assert "GATEWAY_DISPATCH_ERROR" in result.failure_categories
    assert result.cost_usd is not None and result.cost_usd > 0.0
