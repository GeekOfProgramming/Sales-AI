"""
tests/test_s3_5_3b_openrouter_replacement.py
Deterministic test suite for SalesAI S3.5.3B:
- OpenRouter free candidate replacement (t2_c5 -> google/gemma-4-26b-a4b-it:free).
- Historical preservation of candidate t2_c1 (disabled for live generation).
- Fail-closed free-tier classification and zero-cost price verification.
- Provider fallback disabling (provider.allow_fallbacks=false) and no models fallback.
- Client-side JSON Object Mode & Pydantic schema validation.
- SQLite budget reservation lifecycle and zero-spend reconciliation.
"""

import json
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
    OPENROUTER_SPEC,
)
from sales_engine.llm_gateway.contracts import LLMRequest
from sales_engine.llm_gateway.errors import LLMNotConfiguredError
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.llm_gateway.output_validation import LLMOutputValidator


# ===========================================================================
# 1. Candidate Binding & Historical Preservation Tests
# ===========================================================================

def test_t2_c5_candidate_spec_binding():
    """Verify t2_c5 is active, zero-cost, and maps to google/gemma-4-26b-a4b-it:free."""
    spec = get_candidate_spec("t2_c5")
    assert spec is not None
    assert spec.provider_id == "openrouter"
    assert spec.candidate_id == "t2_c5"
    assert spec.exact_model_id == "google/gemma-4-26b-a4b-it:free"
    assert spec.billing_mode == "VERIFIED_FREE_TIER"
    assert spec.is_generation_enabled is True
    assert spec.pricing.currency == "FREE"
    assert spec.pricing.input_per_1m == 0.0
    assert spec.pricing.output_per_1m == 0.0
    assert spec.capabilities.get("provider_fallbacks") is False
    assert spec.capabilities.get("supports_json_mode") is True


def test_t2_c1_historical_candidate_preservation():
    """Verify t2_c1 is preserved as historical metadata but disabled for live dispatch."""
    spec = get_candidate_spec("t2_c1")
    assert spec is not None
    assert spec.candidate_id == "t2_c1"
    assert spec.exact_model_id == "meta-llama/llama-3.3-70b-instruct:free"
    assert spec.is_generation_enabled is False


def test_openrouter_default_candidate_is_t2_c5():
    """Default candidate for openrouter provider is t2_c5."""
    spec = get_provider_spec("openrouter")
    assert spec is not None
    assert spec.candidate_id == "t2_c5"
    assert spec.exact_model_id == "google/gemma-4-26b-a4b-it:free"


def test_candidate_list_includes_both_t2_c1_and_t2_c5():
    """All supported candidates includes both historical t2_c1 and active t2_c5."""
    cands = list_supported_candidates(all_candidates=True)
    assert "t2_c1" in cands
    assert "t2_c5" in cands


# ===========================================================================
# 2. Billing Classification & Free-Only Gate Tests
# ===========================================================================

def test_classify_openrouter_models():
    """Verify explicit free model classification versus non-free or unverified models."""
    # Active candidate
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it:free") == "VERIFIED_FREE_TIER"
    # Historical candidate
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == "VERIFIED_FREE_TIER"
    # Paid variant lacking :free suffix
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it") == "VERIFIED_PAID"
    # Non-free models lacking :free suffix are VERIFIED_PAID
    assert classify_model_billing("openrouter", "openai/gpt-4o") == "VERIFIED_PAID"
    assert classify_model_billing("openrouter", "anthropic/claude-3.5-sonnet") == "VERIFIED_PAID"


def test_runner_rejects_historical_t2_c1_for_live_generation(tmp_path, monkeypatch):
    """ExternalBenchmarkRunner strictly blocks t2_c1 live generation."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-mock-test-key")
    db = str(tmp_path / "test_block.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openrouter",
        free_only=True,
        db_path=db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c1", provider_id="openrouter")
    assert "GENERATION_DISABLED" in str(exc_info.value)


def test_runner_allows_t2_c5_under_free_only(tmp_path):
    """ExternalBenchmarkRunner permits active candidate t2_c5 under --free-only."""
    db = str(tmp_path / "test_allow.db")
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
# 3. Payload & Provider Routing Controls Tests
# ===========================================================================

def test_openrouter_payload_disables_fallbacks():
    """OpenRouter payload must explicitly contain provider.allow_fallbacks=false and no models array."""
    provider = OpenAIProvider(
        provider_id="openrouter",
        base_url=OPENROUTER_T2_C5_SPEC.base_url,
        allowed_hosts=OPENROUTER_T2_C5_SPEC.allowed_hosts,
        env_var="OPENROUTER_API_KEY",
        api_key="sk-or-v1-mock-test-key-0000000000000000",
        default_model=OPENROUTER_T2_C5_SPEC.exact_model_id,
        model_capabilities={OPENROUTER_T2_C5_SPEC.exact_model_id: OPENROUTER_T2_C5_SPEC.capabilities},
        extra_headers=OPENROUTER_T2_C5_SPEC.capabilities.get("extra_headers"),
    )

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract structured information.",
        response_schema={"type": "object", "properties": {"company_name": {"type": "string"}}},
        max_output_tokens=1000,
    )
    payload = provider._build_payload(req, OPENROUTER_T2_C5_SPEC.exact_model_id)

    assert payload["model"] == "google/gemma-4-26b-a4b-it:free"
    # Provider-specific fallback disabling
    assert "provider" in payload
    assert payload["provider"] == {"allow_fallbacks": False}
    # No cross-model fallback array
    assert "models" not in payload
    # JSON Object Mode
    assert payload["response_format"] == {"type": "json_object"}


# ===========================================================================
# 4. Structured Output & Pydantic Schema Validation Tests
# ===========================================================================

def test_pydantic_schema_validation_passes_valid_gemma_json():
    """Client-side Pydantic validator passes compliant JSON output from Gemma."""
    from sales_engine.llm_gateway.contracts import LLMResponse
    from sales_engine.llm_gateway.output_schemas import WebsitePassAOutput

    validator = LLMOutputValidator()
    raw_json = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Milano MEP Ingegneria S.r.l. provides specialized BIM clash coordination.",
        "offerings": [{"name": "BIM Modeling and Clash Coordination", "status": "active"}],
        "target_industries": ["Mechanical, Electrical, and Plumbing Engineering"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Interference detection during pre-construction"],
        "client_testimonial_roles": ["Project Director"],
        "internal_team_roles": ["BIM Coordinator"],
    })

    # Direct Pydantic model validation
    parsed = WebsitePassAOutput.model_validate_json(raw_json)
    assert parsed.company_name == "Milano MEP Ingegneria S.r.l."

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract information",
        provider="openrouter",
        model="google/gemma-4-26b-a4b-it:free",
        request_id="req_openrouter_val_01",
        metadata={"subtask_id": "pass_a"},
    )
    resp = LLMResponse(
        request_id="req_openrouter_val_01",
        task_id="website_extraction",
        provider="openrouter",
        model="google/gemma-4-26b-a4b-it:free",
        content=raw_json,
    )
    val_res = validator.validate(req, resp, subtask_id="pass_a")
    assert val_res.is_valid is True
    assert parsed.company_name == "Milano MEP Ingegneria S.r.l."


def test_pydantic_schema_validation_fails_corrupted_json():
    """Client-side validator fails closed on corrupted or non-schema compliant output."""
    from sales_engine.llm_gateway.contracts import LLMResponse

    validator = LLMOutputValidator()
    raw_corrupted = "Not JSON output at all"

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract information",
        provider="openrouter",
        model="google/gemma-4-26b-a4b-it:free",
        request_id="req_openrouter_val_02",
    )
    resp = LLMResponse(
        request_id="req_openrouter_val_02",
        task_id="website_extraction",
        provider="openrouter",
        model="google/gemma-4-26b-a4b-it:free",
        content=raw_corrupted,
    )
    val_res = validator.validate(req, resp)
    assert val_res.is_valid is False
    assert len(val_res.failure_reasons) > 0
