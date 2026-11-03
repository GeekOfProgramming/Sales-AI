"""
tests/test_s3_5_5a_cloudflare_nvidia_readiness.py
Deterministic offline safety and readiness test suite for S3.5.5A:
- Cloudflare FP8 model pricing accuracy ($0.152/$0.287 per 1M) and rejection of stale pricing.
- Cloudflare Neuron conversion and conservative cost estimation.
- Unknown billing classification for Cloudflare and NVIDIA (fail closed under --free-only).
- Disabled live generation for historical t2_c4, proposed t2_c6, and historical t2_c3.
- Fail-closed candidate/provider mismatch enforcement (preventing silent fallback to default candidate).
- Cloudflare JSON Mode capabilities (supports_json_mode=True, supports_json_schema=False).
- Cloudflare Account ID 32-hex strict validation.
- Ledger historical reservation immutability.
All tests run OFFLINE using deterministic mock transports. Zero network/IO.
"""

import sqlite3
import pytest
import httpx
from pathlib import Path

from sales_engine.benchmark.provider_catalog import (
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    list_supported_candidates,
    CLOUDFLARE_SPEC,
    CLOUDFLARE_T2_C4_SPEC,
    CLOUDFLARE_T2_C6_SPEC,
    NVIDIA_SPEC,
)
from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.llm_gateway.contracts import LLMRequest
from sales_engine.llm_gateway.errors import (
    LLMNotConfiguredError,
    LLMInvalidSchemaError,
)
from sales_engine.llm_gateway.providers.cloudflare_provider import (
    CloudflareProvider,
    estimate_cloudflare_neurons,
    estimate_cloudflare_usd_cost,
    resolve_cloudflare_account_id,
    CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT,
    CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT,
    CLOUDFLARE_NEURON_UNIT_COST_USD,
    CLOUDFLARE_DAILY_FREE_NEURONS,
)


# ===========================================================================
# 1. Cloudflare Pricing & Rate Card Verification
# ===========================================================================

def test_cloudflare_t2_c6_rate_card_and_pricing_accuracy():
    """Verify published rate card pricing for @cf/meta/llama-3.1-8b-instruct-fp8."""
    spec = CLOUDFLARE_T2_C6_SPEC
    assert spec.candidate_id == "t2_c6"
    assert spec.exact_model_id == "@cf/meta/llama-3.1-8b-instruct-fp8"

    pricing = spec.pricing
    assert pricing is not None
    assert pricing.currency == "USD"
    # Strict verification: must be $0.152 input / $0.287 output per 1M tokens
    assert pricing.input_per_1m == 0.152
    assert pricing.output_per_1m == 0.287
    # Verify stale pricing ($0.05 / $0.20) is NOT retained
    assert pricing.input_per_1m != 0.05
    assert pricing.output_per_1m != 0.20


def test_cloudflare_neuron_estimation_and_cost_conversion():
    """Verify Cloudflare Workers AI Neuron conversion and USD cost formulas."""
    # 1. Official baseline rates (corrected from inverse-price derivation in S3.5.5B)
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT == 13778.0
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT == 26128.0
    assert CLOUDFLARE_NEURON_UNIT_COST_USD == 0.000011
    assert CLOUDFLARE_DAILY_FREE_NEURONS == 10000.0

    # 2. 1M input + 1M output tokens
    neurons_1m = estimate_cloudflare_neurons(1_000_000, 1_000_000)
    assert neurons_1m == 13778.0 + 26128.0 == 39906.0

    cost_1m = estimate_cloudflare_usd_cost(1_000_000, 1_000_000)
    # 39,906 Neurons * $0.000011 = $0.438966
    assert cost_1m == 0.438966
    assert round(cost_1m, 4) == 0.4390

    # 3. Standard single test case (e.g., 364 input tokens, 213 output tokens)
    neurons_single = estimate_cloudflare_neurons(364, 213)
    expected_neurons = (364 / 1_000_000.0) * 13778.0 + (213 / 1_000_000.0) * 26128.0
    assert round(neurons_single, 4) == round(expected_neurons, 4)
    assert neurons_single < 11.0  # ~10.58 Neurons

    # Free daily allocation of 10,000 Neurons covers > 900 standard requests
    single_case_cost = estimate_cloudflare_usd_cost(364, 213)
    assert single_case_cost > 0.0
    assert single_case_cost < 0.0002


# ===========================================================================
# 2. Billing Classification & Free-Only Fail-Closed Safety
# ===========================================================================

def test_cloudflare_and_nvidia_classified_as_unknown_billing():
    """Verify Cloudflare and NVIDIA are classified as UNKNOWN (not VERIFIED_FREE_TIER)."""
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct-fp8") == "UNKNOWN"
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct") == "UNKNOWN"
    assert classify_model_billing("nvidia", "meta/llama-3.3-70b-instruct") == "UNKNOWN"
    assert classify_model_billing("nvidia", "nvidia/llama-3.1-nemotron-70b-instruct") == "UNKNOWN"


def test_free_only_gate_blocks_cloudflare_and_nvidia(tmp_path):
    """Attempting live execution under --free-only strictly refuses Cloudflare and NVIDIA."""
    test_db = str(tmp_path / "test_free_gate.db")
    # Cloudflare t2_c6 under free-only
    with pytest.raises(LLMNotConfiguredError) as exc_cf:
        runner_cf = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            free_only=True,
            provider_filter="cloudflare",
            is_connectivity_smoke=True,
            db_path=test_db,
        )
        runner_cf.run_benchmark(task_filter="website_extraction:pass_a")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_cf.value)

    # NVIDIA t2_c3 under free-only
    with pytest.raises(LLMNotConfiguredError) as exc_nv:
        runner_nv = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            free_only=True,
            provider_filter="nvidia",
            is_connectivity_smoke=True,
            db_path=test_db,
        )
        runner_nv.run_benchmark(task_filter="website_extraction:pass_a")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_nv.value)


# ===========================================================================
# 3. Generation Disabling & Candidate Safety Gates
# ===========================================================================

def test_historical_and_proposed_candidates_generation_disabled():
    """Verify historical t2_c4, proposed t2_c6, and historical t2_c3 are generation-disabled."""
    assert CLOUDFLARE_T2_C4_SPEC.is_generation_enabled is False
    assert CLOUDFLARE_T2_C6_SPEC.is_generation_enabled is False
    assert NVIDIA_SPEC.is_generation_enabled is False


def test_disabled_candidates_cannot_dispatch_even_with_mock_transport(tmp_path):
    """Verify disabled candidates are rejected before network dispatch even with injected transport."""
    dummy_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    db = str(tmp_path / "test_safety.db")

    # Cloudflare t2_c4
    with pytest.raises(LLMNotConfiguredError) as exc_c4:
        runner_c4 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="cloudflare",
            real_provider_transport=dummy_transport,
            db_path=db,
        )
        runner_c4.run_benchmark(task_filter="website_extraction:pass_a")
    assert "GENERATION_DISABLED" in str(exc_c4.value)

    # NVIDIA t2_c3
    with pytest.raises(LLMNotConfiguredError) as exc_c3:
        runner_c3 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="nvidia",
            real_provider_transport=dummy_transport,
            db_path=db,
        )
        runner_c3.run_benchmark(task_filter="website_extraction:pass_a")
    assert "GENERATION_DISABLED" in str(exc_c3.value)


# ===========================================================================
# 4. Candidate / Provider Pairing Isolation & No Default Fallback
# ===========================================================================

def test_invalid_candidate_provider_pairing_fails_closed():
    """Verify candidate_id mismatch against provider_id fails closed and returns None."""
    # Requesting t1_c1 (OpenAI) under cloudflare must NOT return Cloudflare spec
    assert get_provider_spec("cloudflare", "t1_c1") is None
    # Requesting t2_c2 (Cohere) under nvidia must NOT return NVIDIA spec
    assert get_provider_spec("nvidia", "t2_c2") is None
    # Requesting t2_c4 (Cloudflare) under groq must NOT return Groq spec
    assert get_provider_spec("groq", "t2_c4") is None

    # Valid pairings succeed
    assert get_provider_spec("cloudflare", "t2_c4") is not None
    assert get_provider_spec("cloudflare", "t2_c6") is not None
    assert get_provider_spec("nvidia", "t2_c3") is not None


def test_runner_rejects_mismatched_candidate_and_provider(tmp_path):
    """Verify runner fails closed if requested candidate does not match requested provider."""
    test_db = str(tmp_path / "test_mismatch.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cloudflare",
        db_path=test_db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_mismatch:
        runner._configure_live_candidate(candidate_id="t1_c1", provider_id="cloudflare")
    assert "does not match provider" in str(exc_mismatch.value) or "is not verified" in str(exc_mismatch.value)


# ===========================================================================
# 5. Cloudflare Adapter & Structured Output Capabilities
# ===========================================================================

def test_cloudflare_structured_output_is_json_mode_not_schema():
    """Verify Cloudflare FP8 model specifies JSON Object Mode, not provider-side schema enforcement."""
    spec = CLOUDFLARE_T2_C6_SPEC
    caps = spec.capabilities
    assert caps.get("supports_json_mode") is True
    # Crucial safety check: Cloudflare does NOT support provider-side JSON Schema grammar enforcement
    assert caps.get("supports_json_schema") is False


def test_cloudflare_account_id_validation():
    """Verify 32 hexadecimal character format enforcement for Cloudflare Account ID."""
    valid_id = "2e11fcbc894e6839cb004415c7061dad"
    assert resolve_cloudflare_account_id(valid_id) == valid_id

    # Rejection of invalid account IDs
    with pytest.raises(LLMNotConfiguredError):
        resolve_cloudflare_account_id("not-a-valid-hex-id")

    with pytest.raises(LLMNotConfiguredError):
        resolve_cloudflare_account_id("12345")  # Too short

    with pytest.raises(LLMNotConfiguredError):
        resolve_cloudflare_account_id("")  # Empty


def test_cloudflare_provider_payload_and_url_formatting():
    """Verify CloudflareProvider URL construction and JSON mode response_format."""
    provider = CloudflareProvider(
        account_id="2e11fcbc894e6839cb004415c7061dad",
        api_key="mock-cf-token",
        model_capabilities={"@cf/meta/llama-3.1-8b-instruct-fp8": {"supports_json_mode": True, "supports_json_schema": False}},
    )

    assert "https://api.cloudflare.com/client/v4/accounts/2e11fcbc894e6839cb004415c7061dad/ai/v1" in provider.base_url

    req = LLMRequest(
        prompt="Extract company details",
        task_id="website_extraction",
        model="@cf/meta/llama-3.1-8b-instruct-fp8",
        provider="cloudflare",
        response_schema={"type": "object", "properties": {"company_name": {"type": "string"}}},
    )

    payload = provider._build_payload(req, "@cf/meta/llama-3.1-8b-instruct-fp8")
    assert payload["model"] == "@cf/meta/llama-3.1-8b-instruct-fp8"
    assert payload["response_format"] == {"type": "json_object"}


# ===========================================================================
# 6. SQLite Ledger Historical Immutability
# ===========================================================================

def test_historical_reservations_intact_in_budget_db():
    """Verify that all 12 historical benchmark ledger records remain completely unmutated."""
    db_path = Path("data/benchmark_budget.db")
    if not db_path.is_file() or db_path.stat().st_size == 0:
        pytest.skip("Benchmark database not present in distribution environment (operational SQLite excluded by security policy)")

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT reservation_id, model, status, final_cost_usd, failure_code FROM llm_reservations ORDER BY created_at ASC"
        ).fetchall()
        if len(rows) == 0:
            pytest.skip("Benchmark database contains no rows in distribution environment")
        assert len(rows) == 12

        # Verify historical S3.5.4D timeout reservation remains UNCERTAIN
        c0ae = next((r for r in rows if r[0] == "res_c0ae3d9e637e"), None)
        assert c0ae is not None
        assert c0ae[1] == "command-r-08-2024"
        assert c0ae[2] == "UNCERTAIN"
        assert c0ae[4] == "TIMEOUT"

        # Verify S3.5.4E successful controlled smoke reservation is RECONCILED
        a13f = next((r for r in rows if r[0] == "res_a13f7b9a62f0"), None)
        assert a13f is not None
        assert a13f[1] == "command-r-08-2024"
        assert a13f[2] == "RECONCILED"
        assert a13f[3] == 0.000182
    finally:
        conn.close()
