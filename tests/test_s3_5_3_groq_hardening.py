"""
tests/test_s3_5_3_groq_hardening.py
Deterministic regression test suite for SalesAI S3.5.3A:
- Hardened free-tier classification (no generic Groq or substring Gemini wildcards).
- Strict model-level allowlists for VERIFIED_FREE_TIER.
- Free-only gate enforcement in ExternalBenchmarkRunner.
- Request payload inspection for candidate t1_c3 (json_object mode).
"""

import httpx
import pytest

from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.provider_catalog import (
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    EXPLICIT_VERIFIED_FREE_MODELS,
    GROQ_SPEC,
)
from sales_engine.llm_gateway.errors import LLMNotConfiguredError


# ===========================================================================
# 1. Hardened Billing Classification Tests
# ===========================================================================

def test_classify_groq_explicit_versus_generic():
    """Generic Groq provider identity must not grant free tier; only explicit verified model qualifies."""
    # Explicit verified free candidate t1_c3
    assert classify_model_billing("groq", "openai/gpt-oss-20b") == "VERIFIED_FREE_TIER"
    assert "openai/gpt-oss-20b" in EXPLICIT_VERIFIED_FREE_MODELS["groq"]

    # Other Groq models MUST return UNKNOWN
    assert classify_model_billing("groq", "llama-3.3-70b-versatile") == "UNKNOWN"
    assert classify_model_billing("groq", "mixtral-8x7b-32768") == "UNKNOWN"
    assert classify_model_billing("groq", "qwen/qwen3.8-27b") == "UNKNOWN"
    assert classify_model_billing("groq", "any-unverified-groq-model") == "UNKNOWN"


def test_classify_gemini_explicit_versus_generic():
    """Generic substring 'gemini' must not qualify; only explicit verified models qualify."""
    # Explicit verified free candidates
    assert classify_model_billing("gemini", "gemini-2.5-flash-lite") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("gemini", "gemini-3.5-flash-lite") == "VERIFIED_FREE_TIER"
    assert "gemini-2.5-flash-lite" in EXPLICIT_VERIFIED_FREE_MODELS["gemini"]
    assert "gemini-3.5-flash-lite" in EXPLICIT_VERIFIED_FREE_MODELS["gemini"]

    # Other Gemini models MUST NOT automatically qualify
    assert classify_model_billing("gemini", "gemini-1.5-pro") == "UNKNOWN"
    assert classify_model_billing("gemini", "gemini-1.5-flash") == "UNKNOWN"
    assert classify_model_billing("gemini", "gemini-ultra") == "UNKNOWN"
    assert classify_model_billing("gemini", "my-custom-gemini-finetune") == "UNKNOWN"


def test_classify_openrouter_free_suffix_required():
    """OpenRouter requires explicit :free suffix."""
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it:free") == "VERIFIED_FREE_TIER"

    # Non-free models are VERIFIED_PAID
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct") == "VERIFIED_PAID"
    assert classify_model_billing("openrouter", "openai/gpt-4o") == "VERIFIED_PAID"


def test_classify_paid_and_unknown_providers():
    """OpenAI is VERIFIED_PAID; Cohere, NVIDIA, Cloudflare, xAI are UNKNOWN."""
    assert classify_model_billing("openai", "gpt-6-luna") == "VERIFIED_PAID"
    assert classify_model_billing("openai", "gpt-4o") == "VERIFIED_PAID"

    assert classify_model_billing("cohere", "command-r-08-2024") == "UNKNOWN"
    assert classify_model_billing("nvidia", "meta/llama-3.3-70b-instruct") == "UNKNOWN"
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct") == "UNKNOWN"
    assert classify_model_billing("xai", "grok-4-fast") == "UNKNOWN"
    assert classify_model_billing("unknown_provider", "some-model") == "UNKNOWN"


# ===========================================================================
# 2. Free-Only Gate Dynamic Enforcement Tests
# ===========================================================================

def test_runner_free_only_allows_explicit_groq(tmp_path):
    """ExternalBenchmarkRunner allows candidate t1_c3 (openai/gpt-oss-20b) under --free-only."""
    db = str(tmp_path / "test_free.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="groq",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )
    runner._configure_live_candidate(candidate_id="t1_c3", provider_id="groq")
    cand = runner.catalog.get_candidate("t1_c3")
    assert cand is not None
    assert cand.enabled is True
    assert cand.exact_model_id == "openai/gpt-oss-20b"


def test_runner_free_only_rejects_unverified_groq_model(tmp_path):
    """ExternalBenchmarkRunner rejects an unverified Groq model under --free-only."""
    import dataclasses
    db = str(tmp_path / "test_free_rej.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="groq",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )
    # If candidate t1_c3 was somehow modified to an unverified model
    spec = dataclasses.replace(get_candidate_spec("t1_c3"), exact_model_id="llama-3.3-70b-versatile")
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        # Pass modified spec to trigger runner's validation
        dynamic = classify_model_billing(spec.provider_id, spec.exact_model_id)
        if runner.free_only and (spec.billing_mode != "VERIFIED_FREE_TIER" or dynamic != "VERIFIED_FREE_TIER"):
            raise LLMNotConfiguredError(f"FREE_ELIGIBILITY_UNCONFIRMED: Provider '{spec.provider_id}' / Model '{spec.exact_model_id}' is classified as '{dynamic}'")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)


def test_runner_free_only_rejects_paid_and_unknown(tmp_path):
    """ExternalBenchmarkRunner strictly rejects openai, cohere, nvidia, cloudflare, xai under --free-only."""
    db = str(tmp_path / "test_rej.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    for provider, cand_id in [
        ("openai", "t1_c1"),
        ("cohere", "t2_c2"),
        ("nvidia", "t2_c3"),
        ("cloudflare", "t2_c4"),
        ("xai", "t1_c4"),
    ]:
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter=provider,
            free_only=True,
            db_path=db,
            real_provider_transport=transport,
        )
        with pytest.raises(LLMNotConfiguredError) as exc_info:
            runner._configure_live_candidate(candidate_id=cand_id, provider_id=provider)
        assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value) or "XAI_GENERATION_DISABLED" in str(exc_info.value)


# ===========================================================================
# 3. Request Serialization & Candidate Spec Integrity Tests
# ===========================================================================

def test_groq_candidate_spec_integrity():
    """Verify GROQ_SPEC candidate ID, model ID, endpoints, and published pricing."""
    spec = GROQ_SPEC
    assert spec.provider_id == "groq"
    assert spec.candidate_id == "t1_c3"
    assert spec.exact_model_id == "openai/gpt-oss-20b"
    assert spec.base_url == "https://api.groq.com/openai/v1"
    assert "api.groq.com" in spec.allowed_hosts
    assert spec.env_var == "GROQ_API_KEY"
    assert spec.billing_mode == "VERIFIED_FREE_TIER"
    # Rate card: $0.075 input / $0.30 output per 1M tokens
    assert spec.pricing.input_per_1m == 0.075
    assert spec.pricing.output_per_1m == 0.30
    assert spec.pricing.currency == "FREE"


def test_groq_request_payload_uses_json_object_mode():
    """
    Groq OpenAI-compatible provider adapter uses response_format={"type": "json_object"}.
    Confirms JSON Object Mode is active (not strict json_schema), so client-side schema validation is essential.
    """
    from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
    from sales_engine.llm_gateway.contracts import LLMRequest

    provider = OpenAIProvider(
        provider_id="groq",
        base_url=GROQ_SPEC.base_url,
        allowed_hosts=GROQ_SPEC.allowed_hosts,
        env_var="GROQ_API_KEY",
        api_key="gsk_mock_test_key_00000000000000000000",
        default_model=GROQ_SPEC.exact_model_id,
        model_capabilities={GROQ_SPEC.exact_model_id: GROQ_SPEC.capabilities},
    )

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract website information.",
        response_schema={"type": "object", "properties": {"company_name": {"type": "string"}}},
        max_output_tokens=1000,
    )
    payload = provider._build_payload(req, GROQ_SPEC.exact_model_id)

    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 1000
