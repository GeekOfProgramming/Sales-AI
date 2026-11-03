"""
tests/test_s3_5_4a_readiness_safety.py
Deterministic safety and readiness test suite for SalesAI S3.5.4A:
- OpenRouter strict billing classification (exact allowlist, rejecting invented :free models as UNKNOWN).
- Historical preservation of t2_c1 (generation disabled, cannot be bypassed even with mock transport).
- Preservation of active candidate t2_c5 (enabled generation).
- Live preflight catalog zero-price verification (failing closed on stale/missing/paid catalog responses).
- Deterministic HTTP 429 rate limit classification (distinguishing quota, RPM, upstream capacity, and unknown).
- Evaluation state tagging (INFERENCE_UNVERIFIED / RATE_LIMITED without false safety violation marks).
- Proposed Cloudflare replacement candidate t2_c6 preservation without mutating t2_c4.
"""

import json
import os
from unittest.mock import patch

import httpx
import pytest

from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.provider_catalog import (
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    list_supported_candidates,
    OPENROUTER_T2_C1_SPEC,
    OPENROUTER_T2_C5_SPEC,
    CLOUDFLARE_T2_C4_SPEC,
    CLOUDFLARE_T2_C6_SPEC,
)
from sales_engine.llm_gateway.contracts import LLMRequest
from sales_engine.llm_gateway.errors import (
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMRateLimitError,
)
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.llm_gateway.rate_limit_diagnostics import (
    RateLimitDiagnostic,
    classify_http_429,
    parse_retry_after,
)


# ===========================================================================
# 1. Billing Classification & Exact Allowlist Enforcement
# ===========================================================================

def test_openrouter_billing_classification_allowlist_enforcement():
    """Verify exact membership in explicit allowlist for OpenRouter free models."""
    # Explicitly approved active model
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it:free") == "VERIFIED_FREE_TIER"
    # Explicitly approved historical model
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == "VERIFIED_FREE_TIER"

    # Invented/unknown models carrying :free suffix must be rejected as UNKNOWN, NOT VERIFIED_FREE_TIER
    assert classify_model_billing("openrouter", "invented/fake-model:free") == "UNKNOWN"
    assert classify_model_billing("openrouter", "meta-llama/llama-3-8b-instruct:free") == "UNKNOWN"
    assert classify_model_billing("openrouter", "mistralai/mistral-7b:free") == "UNKNOWN"
    assert classify_model_billing("openrouter", "qwen/qwen-2.5-7b:free") == "UNKNOWN"

    # Paid models lacking :free suffix are classified as VERIFIED_PAID
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it") == "VERIFIED_PAID"
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct") == "VERIFIED_PAID"
    assert classify_model_billing("openrouter", "openai/gpt-4o") == "VERIFIED_PAID"


def test_cohere_nvidia_cloudflare_default_unknown_billing():
    """Non-free or unverified providers remain UNKNOWN."""
    assert classify_model_billing("cohere", "command-r-08-2024") == "UNKNOWN"
    assert classify_model_billing("nvidia", "meta/llama-3.3-70b-instruct") == "UNKNOWN"
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct") == "UNKNOWN"
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct-fp8") == "UNKNOWN"


# ===========================================================================
# 2. Historical Candidate Preservation & Transport Invariant Gate
# ===========================================================================

def test_historical_t2_c1_preserved_with_generation_disabled():
    """Candidate t2_c1 is cataloged with is_generation_enabled=False."""
    spec = get_candidate_spec("t2_c1")
    assert spec is not None
    assert spec.candidate_id == "t2_c1"
    assert spec.exact_model_id == "meta-llama/llama-3.3-70b-instruct:free"
    assert spec.is_generation_enabled is False


def test_generation_disabled_cannot_be_bypassed_with_mock_transport(tmp_path):
    """ExternalBenchmarkRunner strictly blocks t2_c1 even when mock HTTP transport is injected."""
    db = str(tmp_path / "test_block_mock.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c1", provider_id="openrouter")

    assert "GENERATION_DISABLED" in str(exc_info.value)
    assert "t2_c1" in str(exc_info.value)


def test_active_t2_c5_permitted_with_mock_transport_and_valid_catalog(tmp_path):
    """Active candidate t2_c5 is permitted when mock transport provides valid catalog."""
    db = str(tmp_path / "test_allow_t2_c5.db")

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/gemma-4-26b-a4b-it:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": []})

    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )
    runner._configure_live_candidate(candidate_id="t2_c5", provider_id="openrouter")
    cand = runner.catalog.get_candidate("t2_c5")
    assert cand is not None
    assert cand.enabled is True
    assert cand.exact_model_id == "google/gemma-4-26b-a4b-it:free"


# ===========================================================================
# 3. Live Preflight Zero-Cost Catalog Verification Tests
# ===========================================================================

def test_preflight_fails_closed_when_catalog_endpoint_returns_error(tmp_path):
    """Preflight fails closed if OpenRouter /models returns HTTP 500/503."""
    db = str(tmp_path / "test_preflight_500.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="Internal Server Error"))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c5", provider_id="openrouter")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "Live OpenRouter catalog query failed (HTTP 500)" in str(exc_info.value)


def test_preflight_fails_closed_when_model_missing_from_catalog(tmp_path):
    """Preflight fails closed if the model is absent from the live catalog."""
    db = str(tmp_path / "test_preflight_missing.db")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "meta-llama/llama-3.1-8b-instruct",
                        "pricing": {"prompt": "0.0001", "completion": "0.0002"},
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c5", provider_id="openrouter")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "was not found in the live OpenRouter catalog" in str(exc_info.value)


def test_preflight_fails_closed_when_catalog_reports_non_zero_pricing(tmp_path):
    """Preflight fails closed if the live catalog reports paid pricing for the model."""
    db = str(tmp_path / "test_preflight_paid.db")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "google/gemma-4-26b-a4b-it:free",
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c5", provider_id="openrouter")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "reports non-zero pricing" in str(exc_info.value)


def test_preflight_fails_closed_on_stale_or_empty_catalog_response(tmp_path):
    """Preflight fails closed on empty or invalid data array in catalog."""
    db = str(tmp_path / "test_preflight_stale.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c5", provider_id="openrouter")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "returned empty or stale model list" in str(exc_info.value)


# ===========================================================================
# 4. Deterministic HTTP 429 Classification Tests
# ===========================================================================

def test_classify_http_429_quota_exhausted():
    """429 with insufficient quota/credits is classified as ACCOUNT_QUOTA."""
    diag = classify_http_429(
        status_code=429,
        error_code="insufficient_quota",
        error_type="insufficient_quota",
        error_message="You exceeded your current quota, please check your plan and billing details.",
        provider_id="openai",
    )
    assert diag.cause_category == "ACCOUNT_QUOTA"
    assert diag.definitive_cause_proven is True
    assert "account quota or billing credits exhausted" in diag.safe_diagnostic_message


def test_classify_http_429_rpm_limit():
    """429 with per-minute rate limit is classified as REQUEST_RATE_LIMIT."""
    diag = classify_http_429(
        status_code=429,
        error_code="rate_limit_exceeded",
        error_type="requests",
        error_message="Rate limit reached for requests per minute (RPM). Please wait.",
        headers={"retry-after": "15"},
        provider_id="groq",
    )
    assert diag.cause_category == "REQUEST_RATE_LIMIT"
    assert diag.definitive_cause_proven is True
    assert diag.retry_after_seconds == 15.0


def test_classify_http_429_upstream_capacity_proven():
    """429 with explicit upstream provider queue saturation is classified as UPSTREAM_CAPACITY."""
    diag = classify_http_429(
        status_code=429,
        error_code="capacity_exceeded",
        error_type="upstream_error",
        error_message="Provider returned 429: upstream provider temporarily out of capacity for this model.",
        headers={"retry-after": "5"},
        provider_id="openrouter",
    )
    assert diag.cause_category == "UPSTREAM_CAPACITY"
    assert diag.definitive_cause_proven is True
    assert diag.retry_after_seconds == 5.0


def test_classify_http_429_unknown_when_unproven():
    """429 without specific diagnostic indicator is UNKNOWN_RATE_LIMIT with definitive_cause_proven=False."""
    diag = classify_http_429(
        status_code=429,
        error_code=None,
        error_type=None,
        error_message="Openrouter rate limit exceeded (HTTP 429)",
        headers={},
        provider_id="openrouter",
    )
    assert diag.cause_category == "UNKNOWN_RATE_LIMIT"
    assert diag.definitive_cause_proven is False
    # Upstream capacity must NOT be asserted as definitive without proof
    assert "unconfirmed root cause" in diag.safe_diagnostic_message


def test_parse_retry_after_headers():
    """Retry-After header safely extracted as numeric seconds."""
    assert parse_retry_after({"retry-after": "30"}) == 30.0
    assert parse_retry_after({"Retry-After": "45.5"}) == 45.5
    assert parse_retry_after({"x-ratelimit-reset": "10"}) == 10.0
    assert parse_retry_after({}) is None
    assert parse_retry_after({"retry-after": "invalid-str"}) is None


def test_provider_adapter_raises_structured_rate_limit_error():
    """OpenAIProvider maps 429 to LLMRateLimitError with attached diagnostic."""
    provider = OpenAIProvider(
        provider_id="openrouter",
        base_url="https://openrouter.ai/api/v1",
        allowed_hosts={"openrouter.ai"},
        api_key="sk-or-v1-mock-test-key",
        default_model="google/gemma-4-26b-a4b-it:free",
    )

    fake_resp = httpx.Response(
        429,
        headers={"retry-after": "25"},
        json={"error": {"message": "Rate limit exceeded (HTTP 429)"}},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )

    err = provider._parse_http_error(fake_resp, task_id="website_extraction")
    assert isinstance(err, LLMRateLimitError)
    assert err.cause_category in ("REQUEST_RATE_LIMIT", "UNKNOWN_RATE_LIMIT")
    assert err.retry_after_seconds == 25.0
    assert err.diagnostic is not None


# ===========================================================================
# 5. Evaluation State & Disqualification Taxonomy Tests
# ===========================================================================

def test_runner_evaluates_rate_limited_run_without_safety_violations(tmp_path):
    """When a live run is rate limited (HTTP 429), evaluation_state is INFERENCE_UNVERIFIED and safety violations are 0."""
    db = str(tmp_path / "test_rate_limit_run.db")

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/gemma-4-26b-a4b-it:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                        }
                    ]
                },
            )
        # Chat completion fails with HTTP 429
        return httpx.Response(
            429,
            headers={"retry-after": "10"},
            json={"error": {"message": "Openrouter rate limit exceeded (HTTP 429)"}},
        )

    transport = httpx.MockTransport(handler)

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        max_requests=1,
        max_cost_usd=0.01,
        db_path=db,
        real_provider_transport=transport,
        is_connectivity_smoke=True,
    )

    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t2_c5",
        max_cases_per_task=1,
    )

    summary = report.candidate_summaries[0]
    assert summary.evaluation_state == "INFERENCE_UNVERIFIED / RATE_LIMITED"
    # Crucial safety correction: Transport HTTP 429 must NOT be counted as a model safety violation
    assert summary.safety_violations_count == 0
    assert summary.qualified is False
    assert any("Inference unverified: Candidate experienced rate limit (HTTP 429)" in d for d in summary.disqualification_reasons)
    assert not any("Hard safety violations detected" in d for d in summary.disqualification_reasons)


# ===========================================================================
# 6. Proposed Candidate t2_c6 Preservation Tests
# ===========================================================================

def test_cloudflare_t2_c4_preserved_and_t2_c6_registered():
    """Verify historical t2_c4 is preserved and proposed t2_c6 is cataloged."""
    cands = list_supported_candidates(all_candidates=True)
    assert "t2_c4" in cands
    assert "t2_c6" in cands

    spec_c4 = get_candidate_spec("t2_c4")
    assert spec_c4 is not None
    assert spec_c4.exact_model_id == "@cf/meta/llama-3.1-8b-instruct"

    spec_c6 = get_candidate_spec("t2_c6")
    assert spec_c6 is not None
    assert spec_c6.exact_model_id == "@cf/meta/llama-3.1-8b-instruct-fp8"
    assert spec_c6.is_generation_enabled is False  # Proposed only
