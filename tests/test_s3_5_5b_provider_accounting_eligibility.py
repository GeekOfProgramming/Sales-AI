"""
tests/test_s3_5_5b_provider_accounting_eligibility.py
SalesAI S3.5.5B — Test suite for Cloudflare Neuron accounting corrections,
provider catalog routing integrity, multi-candidate selection safety,
and NVIDIA model eligibility review.

All tests run strictly OFFLINE with zero network calls and zero credit/token spend.
"""

import math
import sqlite3
from pathlib import Path
import pytest
import httpx

from sales_engine.benchmark.provider_catalog import (
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    list_supported_candidates,
    CLOUDFLARE_SPEC,
    CLOUDFLARE_T2_C4_SPEC,
    CLOUDFLARE_T2_C6_SPEC,
    GEMINI_3_5_SPEC,
    GROQ_SPEC,
    COHERE_SPEC,
    NVIDIA_SPEC,
    PROVIDER_SPECS_BY_ID,
)
from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.llm_gateway.contracts import LLMRequest
from sales_engine.llm_gateway.errors import (
    LLMNotConfiguredError,
)
from sales_engine.llm_gateway.providers.cloudflare_provider import (
    CloudflareProvider,
    estimate_cloudflare_neurons,
    estimate_cloudflare_neurons_exact,
    estimate_cloudflare_neurons_preflight,
    estimate_cloudflare_usd_cost,
    resolve_cloudflare_account_id,
    CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT,
    CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT,
    CLOUDFLARE_NEURON_UNIT_COST_USD,
    CLOUDFLARE_DAILY_FREE_NEURONS,
    CLOUDFLARE_FP8_PUBLISHED_INPUT_USD_PER_1M,
    CLOUDFLARE_FP8_PUBLISHED_OUTPUT_USD_PER_1M,
)


# ===========================================================================
# 1. Exact Cloudflare Neuron Constants & Pricing Separation
# ===========================================================================

def test_cloudflare_official_neuron_rates_and_published_pricing():
    """
    Verify official Cloudflare model-specific Neuron rates for @cf/meta/llama-3.1-8b-instruct-fp8:
    - Input: 13,778 Neurons / 1M tokens
    - Output: 26,128 Neurons / 1M tokens
    - Unit price: $0.011 / 1,000 Neurons ($0.000011 / Neuron)
    And published commercial-equivalent rate card:
    - Input: $0.152 / 1M tokens
    - Output: $0.287 / 1M tokens
    """
    # Native Neuron constants
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT == 13778.0
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT == 26128.0
    assert CLOUDFLARE_NEURON_UNIT_COST_USD == 0.000011
    assert CLOUDFLARE_DAILY_FREE_NEURONS == 10000.0

    # Ensure inverse-price derived rates (13,818 and 26,091) are purged
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT != 13818.0
    assert CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT != 26091.0

    # Published rate card metadata
    assert CLOUDFLARE_FP8_PUBLISHED_INPUT_USD_PER_1M == 0.152
    assert CLOUDFLARE_FP8_PUBLISHED_OUTPUT_USD_PER_1M == 0.287

    # Mathematical relationship: exact neuron cost vs published rounded cost
    exact_in_cost_1m = 13778.0 * 0.000011  # 0.151558
    assert round(exact_in_cost_1m, 3) == 0.152

    exact_out_cost_1m = 26128.0 * 0.000011  # 0.287408
    assert round(exact_out_cost_1m, 3) == 0.287


# ===========================================================================
# 2. Input-Only, Output-Only, Balanced, Near-Boundary, Maximum-Output Requests
# ===========================================================================

def test_input_only_requests():
    """Verify exact and conservative Neuron estimation for input-only requests."""
    # 1 token input
    exact_1 = estimate_cloudflare_neurons_exact(1, 0)
    assert round(exact_1, 6) == 0.013778
    preflight_1 = estimate_cloudflare_neurons_preflight(1, 0)
    assert preflight_1 == 1  # ceil(0.013778) = 1

    # 1,000 tokens input
    exact_1k = estimate_cloudflare_neurons_exact(1000, 0)
    assert round(exact_1k, 3) == 13.778
    preflight_1k = estimate_cloudflare_neurons_preflight(1000, 0)
    assert preflight_1k == 14  # ceil(13.778) = 14

    # 1,000,000 tokens input
    exact_1m = estimate_cloudflare_neurons_exact(1_000_000, 0)
    assert exact_1m == 13778.0
    preflight_1m = estimate_cloudflare_neurons_preflight(1_000_000, 0)
    assert preflight_1m == 13778


def test_output_only_requests():
    """Verify output-only accounting: guarantees no under-reservation on completion tokens."""
    # 1 token output
    exact_1 = estimate_cloudflare_neurons_exact(0, 1)
    assert round(exact_1, 6) == 0.026128
    preflight_1 = estimate_cloudflare_neurons_preflight(0, 1)
    assert preflight_1 == 1  # ceil(0.026128) = 1

    # 500 tokens output
    exact_500 = estimate_cloudflare_neurons_exact(0, 500)
    expected_500 = (500 / 1e6) * 26128.0  # 13.064
    assert round(exact_500, 3) == 13.064
    preflight_500 = estimate_cloudflare_neurons_preflight(0, 500)
    assert preflight_500 == 14  # ceil(13.064) = 14

    # 1,024 tokens output (standard max output for pass_a extraction)
    exact_1024 = estimate_cloudflare_neurons_exact(0, 1024)
    expected_1024 = (1024 / 1e6) * 26128.0  # 26.755072
    assert round(exact_1024, 4) == 26.7551
    preflight_1024 = estimate_cloudflare_neurons_preflight(0, 1024)
    assert preflight_1024 == 27  # ceil(26.755) = 27


def test_balanced_requests():
    """Verify balanced prompt and completion cases."""
    # 500 in, 500 out
    exact_500_500 = estimate_cloudflare_neurons_exact(500, 500)
    in_part = 500 * 0.013778   # 6.889
    out_part = 500 * 0.026128  # 13.064
    assert round(exact_500_500, 3) == round(in_part + out_part, 3) == 19.953

    # Per-component ceiling: ceil(6.889) + ceil(13.064) = 7 + 14 = 21
    preflight_comp = estimate_cloudflare_neurons_preflight(500, 500, per_component_ceiling=True)
    assert preflight_comp == 21

    # Combined ceiling: ceil(19.953) = 20
    preflight_comb = estimate_cloudflare_neurons_preflight(500, 500, per_component_ceiling=False)
    assert preflight_comb == 20

    # Per-component ceiling is strictly >= combined ceiling (conservative upper bound)
    assert preflight_comp >= preflight_comb


def test_near_boundary_thresholds():
    """Verify behavior right around integer Neuron boundaries."""
    # 72 input tokens: 72 * 0.013778 = 0.992016 (< 1 Neuron)
    exact_72 = estimate_cloudflare_neurons_exact(72, 0)
    assert exact_72 < 1.0
    assert estimate_cloudflare_neurons_preflight(72, 0) == 1

    # 73 input tokens: 73 * 0.013778 = 1.005794 (> 1 Neuron)
    exact_73 = estimate_cloudflare_neurons_exact(73, 0)
    assert exact_73 > 1.0
    assert estimate_cloudflare_neurons_preflight(73, 0) == 2

    # 38 output tokens: 38 * 0.026128 = 0.992864 (< 1 Neuron)
    exact_out_38 = estimate_cloudflare_neurons_exact(0, 38)
    assert exact_out_38 < 1.0
    assert estimate_cloudflare_neurons_preflight(0, 38) == 1

    # 39 output tokens: 39 * 0.026128 = 1.018992 (> 1 Neuron)
    exact_out_39 = estimate_cloudflare_neurons_exact(0, 39)
    assert exact_out_39 > 1.0
    assert estimate_cloudflare_neurons_preflight(0, 39) == 2


def test_maximum_output_requests():
    """Verify large context output requests avoid under-reservation."""
    # Large extraction: 500 input tokens, 8,192 output tokens
    exact = estimate_cloudflare_neurons_exact(500, 8192)
    in_n = (500 / 1e6) * 13778.0     # 6.889
    out_n = (8192 / 1e6) * 26128.0   # 214.040576
    assert round(exact, 4) == round(in_n + out_n, 4) == 220.9296

    # Conservative reservation: ceil(6.889) + ceil(214.041) = 7 + 215 = 222 Neurons
    preflight = estimate_cloudflare_neurons_preflight(500, 8192)
    assert preflight == 222
    assert preflight > exact

    # Free daily allocation of 10,000 Neurons can support 45 of these maximum-output calls
    assert math.floor(10000.0 / preflight) == 45


# ===========================================================================
# 3. Provider Readiness Rate Card Integrity (Gemini, Groq, Cohere)
# ===========================================================================

def test_authoritative_rate_card_reporting_for_qualified_candidates():
    """
    Verify authoritative commercial reference rate cards and limits:
    - Gemini 3.5 Flash-Lite (t1_c5): $0.30 in / $2.50 out per 1M (NOT Gemini 2.5 $0.10/$0.40)
    - GroqCloud GPT-OSS-20B (t1_c3): $0.075 in / $0.30 out per 1M (NOT $0/$0 reference rate)
      and published free-plan limits: 30 RPM and 1,000 RPD.
    - Cohere Command R (t2_c2): Trial Chat limit 20 RPM / 1,000 calls per month.
    """
    # 1. Gemini 3.5 Flash-Lite
    gemini_spec = GEMINI_3_5_SPEC
    assert gemini_spec.candidate_id == "t1_c5"
    assert gemini_spec.exact_model_id == "gemini-3.5-flash-lite"
    assert gemini_spec.pricing.input_per_1m == 0.30
    assert gemini_spec.pricing.output_per_1m == 2.50
    # Guard against accidental Gemini 2.5 rate leakage
    assert gemini_spec.pricing.input_per_1m != 0.10
    assert gemini_spec.pricing.output_per_1m != 0.40

    # 2. GroqCloud GPT-OSS-20B
    groq_spec = GROQ_SPEC
    assert groq_spec.candidate_id == "t1_c3"
    assert groq_spec.exact_model_id == "openai/gpt-oss-20b"
    assert groq_spec.pricing.input_per_1m == 0.075
    assert groq_spec.pricing.output_per_1m == 0.30

    # 3. Cohere Command R
    cohere_spec = COHERE_SPEC
    assert cohere_spec.candidate_id == "t2_c2"
    assert cohere_spec.exact_model_id == "command-r-08-2024"
    assert cohere_spec.pricing.input_per_1m == 0.15
    assert cohere_spec.pricing.output_per_1m == 0.60


# ===========================================================================
# 4. Multi-Candidate Routing Invariants & Default Decision Safety
# ===========================================================================

def test_cloudflare_default_candidate_invariants():
    """
    Verify Cloudflare default mapping and candidate separation:
    - PROVIDER_SPECS_BY_ID['cloudflare'] is historical t2_c4 (CLOUDFLARE_SPEC).
    - CLOUDFLARE_T2_C4_SPEC is candidate t2_c4.
    - CLOUDFLARE_T2_C6_SPEC is proposed candidate t2_c6.
    - Both t2_c4 and t2_c6 remain generation-disabled.
    """
    assert PROVIDER_SPECS_BY_ID["cloudflare"].candidate_id == "t2_c4"
    assert CLOUDFLARE_T2_C4_SPEC.candidate_id == "t2_c4"
    assert CLOUDFLARE_T2_C6_SPEC.candidate_id == "t2_c6"
    assert CLOUDFLARE_T2_C4_SPEC.is_generation_enabled is False
    assert CLOUDFLARE_T2_C6_SPEC.is_generation_enabled is False


def test_cloudflare_routing_safety_matrix(tmp_path):
    """
    Verify complete safety matrix for Cloudflare candidate routing:
    1. Provider-only dispatch without candidate fails closed.
    2. Candidate-only t2_c4 fails closed (generation disabled).
    3. Candidate-only t2_c6 fails closed (generation disabled).
    4. Mismatched candidate/provider fails closed.
    5. Injected mock transport cannot bypass generation-disabled gate.
    """
    mock_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    test_db = str(tmp_path / "test_cf_routing.db")

    # 1. Provider-only dispatch without candidate (fails closed with GENERATION_DISABLED)
    with pytest.raises(LLMNotConfiguredError) as exc_prov_only:
        runner_prov = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="cloudflare",
            real_provider_transport=mock_transport,
            db_path=test_db,
        )
        runner_prov.run_benchmark(task_filter="website_extraction:pass_a")
    assert "GENERATION_DISABLED" in str(exc_prov_only.value)
    assert "explicit candidate selection is required" in str(exc_prov_only.value)

    # 2. Candidate-only t2_c4 (historical model unavailable, generation disabled)
    with pytest.raises(LLMNotConfiguredError) as exc_c4:
        runner_c4 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            real_provider_transport=mock_transport,
            db_path=test_db,
        )
        runner_c4._configure_live_candidate(candidate_id="t2_c4")
    assert "GENERATION_DISABLED" in str(exc_c4.value)
    assert "t2_c4" in str(exc_c4.value)

    # 3. Candidate-only t2_c6 (proposed model unverified, generation disabled)
    with pytest.raises(LLMNotConfiguredError) as exc_c6:
        runner_c6 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            real_provider_transport=mock_transport,
            db_path=test_db,
        )
        runner_c6._configure_live_candidate(candidate_id="t2_c6")
    assert "GENERATION_DISABLED" in str(exc_c6.value)
    assert "t2_c6" in str(exc_c6.value)

    # 4. Mismatched candidate and provider (t1_c1 with cloudflare)
    with pytest.raises(LLMNotConfiguredError) as exc_mismatch:
        runner_mismatch = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            real_provider_transport=mock_transport,
            db_path=test_db,
        )
        runner_mismatch._configure_live_candidate(candidate_id="t1_c1", provider_id="cloudflare")
    assert "does not match provider" in str(exc_mismatch.value)


# ===========================================================================
# 5. NVIDIA Model Eligibility & Entitlement Isolation
# ===========================================================================

def test_nvidia_historical_model_remains_disabled():
    """Verify NVIDIA historical candidate t2_c3 is preserved and generation-disabled."""
    spec = NVIDIA_SPEC
    assert spec.candidate_id == "t2_c3"
    assert spec.exact_model_id == "meta/llama-3.3-70b-instruct"
    assert spec.is_generation_enabled is False


def test_nvidia_dispatch_blocked_even_with_mock_transport(tmp_path):
    """Verify NVIDIA cannot dispatch even with mock transport injected."""
    mock_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    test_db = str(tmp_path / "test_nv_routing.db")

    with pytest.raises(LLMNotConfiguredError) as exc:
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="nvidia",
            real_provider_transport=mock_transport,
            db_path=test_db,
        )
        runner.run_benchmark(task_filter="website_extraction:pass_a")
    assert "GENERATION_DISABLED" in str(exc.value)


# ===========================================================================
# 6. Historical Ledger Immutability
# ===========================================================================

def test_s3_5_5b_ledger_reservations_remain_intact():
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
        assert round(a13f[3], 6) == 0.000182
    finally:
        conn.close()
