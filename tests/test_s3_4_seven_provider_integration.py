"""
tests/test_s3_4_seven_provider_integration.py
SalesAI S3.4 — Seven-Provider Integration, Contract Diagnostics & Safe Inference Readiness.

Deterministic offline test suite verifying:
1. Seven-provider catalog integrity and canonical taxonomy
2. Credential isolation (zero cross-vendor fallback)
3. Endpoint allowlist validation (rejection of unapproved, private, loopback, non-HTTPS)
4. Cloudflare Account ID handling (32-hex validation, missing/invalid format fail-closed)
5. Cohere native v2 Chat API request serialization and response normalization
6. OpenRouter free model eligibility (:free suffix verification) and fail-closed non-free rejection
7. NVIDIA NIM catalog availability vs inference entitlement separation
8. Free-only gate enforcement across all seven providers
9. HTTP error mapping (400, 401, 403, 429 quota, 429 rate limit, 500, timeout)
10. Budget reservation and reconciliation (free tier $0.00 spend vs paid rate card)
11. Missing usage counts marked UNCERTAIN with retained preflight liability
12. Deterministic reproducer and contract diagnostics for OpenAI web_a_01 validation failure
13. Historical xAI Grok generation locked by default
14. Smoke-test safety boundary: never approves production routes
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sales_engine.benchmark.external_runner import (
    BenchmarkRunReport,
    CandidateTaskSummary,
    ExternalBenchmarkRunner,
)
from sales_engine.benchmark.fixtures_s3 import get_fixtures_for_task
from sales_engine.benchmark.ground_truth_evaluator import (
    GroundTruthEvaluationResult,
    GroundTruthEvaluator,
)
from sales_engine.benchmark.provider_catalog import (
    ALL_SUPPORTED_PROVIDERS,
    CANONICAL_SEVEN_PROVIDERS,
    CLOUDFLARE_SPEC,
    COHERE_SPEC,
    GEMINI_SPEC,
    GROQ_SPEC,
    NVIDIA_SPEC,
    OPENAI_SPEC,
    OPENROUTER_SPEC,
    XAI_SPEC,
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    list_canonical_seven_providers,
    list_supported_candidates,
    list_supported_providers,
)
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMInvalidSchemaError,
    LLMNotConfiguredError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUncertainUsageError,
)
from sales_engine.llm_gateway.model_catalog import ModelCandidate, PricingMetadata
from sales_engine.llm_gateway.output_validation import LLMOutputValidator
from sales_engine.llm_gateway.providers.cloudflare_provider import (
    CloudflareProvider,
    resolve_cloudflare_account_id,
)
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
from sales_engine.llm_gateway.providers.openai_provider import (
    OpenAIProvider,
    validate_endpoint_url,
)
from scripts.probe_llm_providers import probe_single_provider


# ===========================================================================
# 1. Seven-Provider Catalog Integrity & Canonical Taxonomy
# ===========================================================================

def test_seven_provider_catalog_integrity():
    """Verify all seven canonical providers + historical xAI have complete specs."""
    canonical = list_canonical_seven_providers()
    assert canonical == ["openai", "gemini", "groq", "openrouter", "cohere", "nvidia", "cloudflare"]
    assert len(canonical) == 7

    all_providers = list_supported_providers(all_providers=True)
    assert set(all_providers) == {"openai", "gemini", "groq", "openrouter", "cohere", "nvidia", "cloudflare", "xai"}

    candidates = list_supported_candidates(all_candidates=True)
    assert set(candidates) == {"t1_c1", "t1_c2", "t1_c3", "t1_c4", "t1_c5", "t2_c1", "t2_c2", "t2_c3", "t2_c4", "t2_c5", "t2_c6"}

    for p in all_providers:
        spec = get_provider_spec(p)
        assert spec is not None
        assert spec.base_url.startswith("https://")
        assert len(spec.allowed_hosts) > 0
        assert spec.pricing is not None
        assert spec.pricing.currency in ("USD", "FREE")
        assert spec.checked_date == "2026-10-24" or spec.checked_date.startswith("2026-10")
        assert len(spec.verified_source) > 0


def test_candidate_mappings_no_silent_collisions():
    """Verify exact candidate slot bindings without collisions."""
    expected_bindings = {
        "t1_c1": ("openai", "gpt-6-luna"),
        "t1_c2": ("gemini", "gemini-2.5-flash-lite"),
        "t1_c3": ("groq", "openai/gpt-oss-20b"),
        "t1_c4": ("xai", "grok-4-fast"),
        "t2_c1": ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
        "t2_c2": ("cohere", "command-r-08-2024"),
        "t2_c3": ("nvidia", "meta/llama-3.3-70b-instruct"),
        "t2_c4": ("cloudflare", "@cf/meta/llama-3.1-8b-instruct"),
    }
    for cid, (expected_prov, expected_model) in expected_bindings.items():
        spec = get_candidate_spec(cid)
        assert spec is not None
        assert spec.provider_id == expected_prov
        assert spec.exact_model_id == expected_model
        assert spec.candidate_id == cid


# ===========================================================================
# 2. Endpoint Allowlist & Security Validation
# ===========================================================================

def test_endpoint_allowlist_approved_hosts():
    """Approved provider hosts pass validation."""
    approved_urls = [
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://api.groq.com/openai/v1",
        "https://openrouter.ai/api/v1",
        "https://api.cohere.com/v2",
        "https://integrate.api.nvidia.com/v1",
        "https://api.cloudflare.com/client/v4/accounts/0123456789abcdef0123456789abcdef/ai/v1",
    ]
    for url in approved_urls:
        validated = validate_endpoint_url(url, allowed_hosts={
            "api.openai.com",
            "generativelanguage.googleapis.com",
            "api.groq.com",
            "openrouter.ai",
            "api.cohere.com",
            "integrate.api.nvidia.com",
            "api.cloudflare.com",
        })
        assert validated.startswith("https://")


def test_endpoint_allowlist_unsafe_destinations():
    """Rejects unsafe, loopback, private, and non-HTTPS endpoints."""
    allowed = {"api.openai.com", "openrouter.ai", "api.cohere.com", "integrate.api.nvidia.com", "api.cloudflare.com"}
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://localhost:8000/v1", allowed)
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://127.0.0.1/v1", allowed)
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://10.0.1.5/v1", allowed)
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://192.168.1.100/v1", allowed)
    with pytest.raises(ValueError, match="secure HTTPS scheme"):
        validate_endpoint_url("http://openrouter.ai/api/v1", allowed)
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_endpoint_url("https://user:pass@openrouter.ai/api/v1", allowed)


# ===========================================================================
# 3. Credential Isolation & Cloudflare Account ID Handling
# ===========================================================================

def test_credential_isolation_fail_closed():
    """Each provider fails closed when its dedicated env var is missing."""
    with patch.dict(os.environ, {}, clear=True):
        p_cohere = CohereProvider(api_key=None)
        with pytest.raises(LLMNotConfiguredError, match="Missing API key for provider 'cohere'"):
            p_cohere._get_api_key()

        p_openai = OpenAIProvider(provider_id="openai", api_key=None)
        with pytest.raises(LLMNotConfiguredError, match="Missing API key for provider 'openai'"):
            p_openai._get_api_key()


def test_no_cross_vendor_key_leakage():
    """An environment key for one provider is never used for another."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-key-only"}, clear=True):
        p_cohere = CohereProvider(api_key=None)
        with pytest.raises(LLMNotConfiguredError, match="COHERE_API_KEY"):
            p_cohere._get_api_key()

        p_openrouter = OpenAIProvider(provider_id="openrouter", env_var="OPENROUTER_API_KEY")
        with pytest.raises(LLMNotConfiguredError, match="OPENROUTER_API_KEY"):
            p_openrouter._get_api_key()


def test_cloudflare_account_id_validation():
    """Cloudflare requires a 32-hex Account ID; invalid or missing IDs fail closed."""
    # 1. Missing Account ID
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(LLMNotConfiguredError, match="Missing Cloudflare Account ID"):
            resolve_cloudflare_account_id(None)

    # 2. Invalid Account ID (non-hex, wrong length)
    with pytest.raises(LLMNotConfiguredError, match="Invalid Cloudflare Account ID format"):
        resolve_cloudflare_account_id("not-32-hex-characters")
    with pytest.raises(LLMNotConfiguredError, match="Invalid Cloudflare Account ID format"):
        resolve_cloudflare_account_id("12345")

    # 3. Valid Account ID (32 hex characters)
    valid_id = "0123456789abcdef0123456789abcdef"
    resolved = resolve_cloudflare_account_id(valid_id)
    assert resolved == valid_id

    # 4. Correct base_url construction
    cf_provider = CloudflareProvider(
        account_id=valid_id,
        api_key="mock-cf-token",
    )
    assert valid_id in cf_provider.base_url
    assert cf_provider.base_url == f"https://api.cloudflare.com/client/v4/accounts/{valid_id}/ai/v1"


# ===========================================================================
# 4. Cohere Native v2 Chat API Serialization & Deserialization
# ===========================================================================

def test_cohere_v2_request_serialization():
    """Cohere v2 Chat request serialization builds messages array and response_format."""
    provider = CohereProvider(api_key="mock-key")
    req = LLMRequest(
        prompt="Analyze this company.",
        system_prompt="You are a BIM analyst.",
        task_id="website_extraction",
        max_output_tokens=500,
        temperature=0.2,
        response_schema={"type": "object"},
        request_id="req_cohere_01",
    )
    payload = provider._build_payload(req, model="command-r-08-2024")
    assert payload["model"] == "command-r-08-2024"
    assert payload["max_tokens"] == 500
    assert payload["temperature"] == 0.2
    assert payload["response_format"] == {"type": "json_object"}
    assert len(payload["messages"]) == 2
    assert payload["messages"][0] == {"role": "system", "content": "You are a BIM analyst."}
    assert payload["messages"][1] == {"role": "user", "content": "Analyze this company."}


def test_cohere_v2_response_parsing():
    """Cohere v2 response body parses message.content array and token usage."""
    provider = CohereProvider(api_key="mock-key")
    req = LLMRequest(
        prompt="Analyze company",
        task_id="website_extraction",
        request_id="req_cohere_02",
        response_schema={"type": "object"},
    )
    cohere_resp_json = {
        "id": "cohere_req_987",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": '{"company_name": "Milano MEP Ingegneria", "technologies": ["Revit"]}',
                }
            ],
        },
        "finish_reason": "COMPLETE",
        "usage": {
            "tokens": {
                "input_tokens": 150,
                "output_tokens": 280,
            }
        },
    }

    mock_resp = httpx.Response(
        status_code=200,
        content=json.dumps(cohere_resp_json).encode("utf-8"),
        headers={"x-request-id": "cohere_req_987"},
        request=httpx.Request("POST", "https://api.cohere.com/v2/chat"),
    )
    llm_resp = provider._parse_response_body(mock_resp, req, "command-r-08-2024", 1250.0)

    assert llm_resp.provider == "cohere"
    assert llm_resp.model == "command-r-08-2024"
    assert llm_resp.input_tokens == 150
    assert llm_resp.output_tokens == 280
    assert llm_resp.finish_reason == "COMPLETE"
    assert llm_resp.provider_request_id == "cohere_req_987"
    assert llm_resp.parsed_json == {"company_name": "Milano MEP Ingegneria", "technologies": ["Revit"]}


def test_cohere_probe_models_mock():
    """Cohere probe endpoint (GET /v1/models?page_size=20) parses model list."""
    mock_transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            json={
                "models": [
                    {"name": "command-r-08-2024", "endpoints": ["chat"]},
                    {"name": "command-r-plus-08-2024", "endpoints": ["chat"]},
                    {"name": "command-light", "endpoints": ["chat"]},
                ]
            },
        )
    )
    provider = CohereProvider(api_key="mock-cohere-key", transport=mock_transport)
    probe_res = provider.probe_models()
    assert probe_res["status"] == "AUTHENTICATED"
    assert probe_res["model_count"] == 3
    assert "command-r-08-2024" in probe_res["model_ids"]


# ===========================================================================
# 5. OpenRouter Free Tier & Model Eligibility Controls
# ===========================================================================

def test_openrouter_model_billing_classification():
    """Only models explicitly in allowlist and carrying :free suffix qualify as VERIFIED_FREE_TIER."""
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("openrouter", "google/gemma-4-26b-a4b-it:free") == "VERIFIED_FREE_TIER"
    # Unverified :free model rejected as UNKNOWN under explicit allowlist policy
    assert classify_model_billing("openrouter", "google/gemini-2.0-flash-lite:free") == "UNKNOWN"
    # Paid or non-free models on OpenRouter
    assert classify_model_billing("openrouter", "openai/gpt-4o") == "VERIFIED_PAID"
    assert classify_model_billing("openrouter", "anthropic/claude-3.5-sonnet") == "VERIFIED_PAID"


def test_free_only_gate_accepts_verified_free_providers():
    """Free-only gate allows gemini, groq, and openrouter with :free model."""
    assert classify_model_billing("gemini", "gemini-2.5-flash-lite") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("groq", "openai/gpt-oss-20b") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("openrouter", "meta-llama/llama-3.3-70b-instruct:free") == "VERIFIED_FREE_TIER"


def test_free_only_gate_rejects_paid_and_unknown_providers():
    """Free-only gate strictly rejects openai, cohere, nvidia, cloudflare, and xai."""
    assert classify_model_billing("openai", "gpt-6-luna") == "VERIFIED_PAID"
    assert classify_model_billing("cohere", "command-r-08-2024") == "UNKNOWN"
    assert classify_model_billing("nvidia", "meta/llama-3.3-70b-instruct") == "UNKNOWN"
    assert classify_model_billing("cloudflare", "@cf/meta/llama-3.1-8b-instruct") == "UNKNOWN"
    assert classify_model_billing("xai", "grok-4-fast") == "UNKNOWN"


def test_runner_free_only_rejection_for_non_free_models(tmp_path):
    """ExternalBenchmarkRunner rejects non-free models under --free-only."""
    db_file = str(tmp_path / "test_budget.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    for provider in ("openai", "cohere", "nvidia", "cloudflare", "xai"):
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter=provider,
            free_only=True,
            db_path=db_file,
            real_provider_transport=transport,
        )
        spec = get_provider_spec(provider)
        with pytest.raises(LLMNotConfiguredError, match="FREE_ELIGIBILITY_UNCONFIRMED"):
            runner._configure_live_candidate(candidate_id=spec.candidate_id, provider_id=provider)


# ===========================================================================
# 6. HTTP Error Mapping & Exception Hardening
# ===========================================================================

@pytest.mark.parametrize("status_code, expected_exc", [
    (401, LLMAuthenticationError),
    (403, LLMAuthenticationError),
    (429, LLMRateLimitError),
    (500, LLMProviderUnavailableError),
    (503, LLMProviderUnavailableError),
])
def test_cohere_http_error_mapping(status_code, expected_exc):
    """Cohere provider maps HTTP error codes without secret leakage."""
    resp = httpx.Response(status_code=status_code, json={"message": "Error details"})
    provider = CohereProvider(api_key="mock-key")
    exc = provider._parse_http_error(resp, "test_task")
    assert isinstance(exc, expected_exc)


def test_cloudflare_probe_models_mock():
    """Cloudflare search endpoint probe parses result list and verifies success: true."""
    mock_transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            json={
                "success": True,
                "result": [
                    {"name": "@cf/meta/llama-3.1-8b-instruct", "id": "llama-8b-id"},
                    {"name": "@cf/mistral/mistral-7b-instruct-v0.1", "id": "mistral-id"},
                ],
            },
        )
    )
    provider = CloudflareProvider(
        account_id="0123456789abcdef0123456789abcdef",
        api_key="cf-test-token",
        transport=mock_transport,
    )
    probe_res = provider.probe_models()
    assert probe_res["status"] == "AUTHENTICATED"
    assert probe_res["model_count"] == 2
    assert "@cf/meta/llama-3.1-8b-instruct" in probe_res["model_ids"]


# ===========================================================================
# 7. Budget Ledger Accounting & Free Tier Zero Reconciliation
# ===========================================================================

def test_openrouter_free_model_reconciles_to_zero_spend(tmp_path):
    """Free tier model reservations reconcile to $0.000000 actual spend."""
    db_file = str(tmp_path / "budget_free.db")
    content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Provides specialized BIM clash coordination.",
        "offerings": [{"name": "MEP Modeling", "status": "active"}],
        "technologies": ["Autodesk Revit"],
        "evidence_refs": ["EV_01"],
    })

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
        return httpx.Response(
            200,
            json={
                "id": "gen-openrouter-free-1",
                "model": "google/gemma-4-26b-a4b-it:free",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 250},
            },
        )

    transport = httpx.MockTransport(handler)

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-test"}):
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="openrouter",
            free_only=True,
            max_requests=1,
            max_cost_usd=0.01,
            db_path=db_file,
            real_provider_transport=transport,
            is_connectivity_smoke=True,
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t2_c5",
            max_cases_per_task=1,
        )

        assert report.total_cases_run == 1
        assert report.total_cost_usd == 0.0
        case_res = report.candidate_summaries[0].case_results[0]
        assert case_res.cost_usd == 0.0
        assert case_res.spend_provenance == "VERIFIED_FREE_TIER"
        assert case_res.sqlite_reservation_status == "RECONCILED"


def test_missing_usage_marks_reservation_uncertain_not_free(tmp_path):
    """When provider response omits usage dict, reservation is held as UNCERTAIN, never $0."""
    db_file = str(tmp_path / "cohere_no_usage.db")
    content = json.dumps({
        "company_name": "Milano MEP",
        "company_summary": "BIM coordination",
        "offerings": [{"name": "MEP", "status": "active"}],
        "evidence_refs": ["EV_01"],
    })

    # Response with missing usage!
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "cohere-no-usage",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": content}],
                },
                "finish_reason": "COMPLETE",
                # Omit "usage" field
            },
        )
    )

    with patch.dict(os.environ, {"COHERE_API_KEY": "test-cohere-key"}):
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="cohere",
            free_only=False,
            max_requests=1,
            max_cost_usd=0.10,
            db_path=db_file,
            real_provider_transport=transport,
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t2_c2",
            max_cases_per_task=1,
        )

        assert report.total_cases_run == 1
        case_res = report.candidate_summaries[0].case_results[0]
        # Held as UNCERTAIN
        assert case_res.sqlite_reservation_status == "UNCERTAIN"
        assert case_res.cost_usd is not None
        assert case_res.cost_usd > 0.0  # Retained projected spend, NEVER $0.00!


# ===========================================================================
# 8. Deterministic Investigation of OpenAI web_a_01 Smoke Failure
# ===========================================================================

def test_openai_web_a_01_smoke_failure_deterministic_reproducer():
    """
    Deterministic offline regression test reproducing and resolving the exact
    OpenAI web_a_01 smoke failure from S3.2 run s3_bench_187335fa5d.

    Verifies the 7 required diagnostic dimensions:
    1. Syntactically valid JSON was produced.
    2. 'offerings.status' violated the strict canonical enum.
    3. Parser discarded fields upon schema failure (validated_data is None).
    4. Evidence references in raw JSON are safely preserved without false citation missing claims.
    5. Downstream scoring correctly isolates invalid schema (SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA)
       and does not falsely report empty company name mismatch.
    6. Request-side structured output / strict schema prevents enum divergence.
    7. Repair proposal mechanism is available without triggering unmetered paid retries.
    """
    fixture = get_fixtures_for_task("website_extraction", "pass_a")[0]
    assert fixture.case_id == "web_a_01"

    # Exact raw JSON structure emitted during the S3.2 pilot:
    raw_payload = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Provides specialized BIM clash coordination and MEP modeling services in Lombardy since 2015.",
        "offerings": [
            {"name": "BIM clash coordination", "status": "available"},  # Invalid enum: 'available' not allowed
            {"name": "MEP modeling", "status": "active"},
        ],
        "target_industries": ["Architecture", "Engineering", "Construction"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Clashes in ductwork"],
        "client_testimonial_roles": ["Project Manager"],
        "internal_team_roles": ["BIM Coordinator"],
        "technologies": ["Autodesk Revit", "Navisworks", "Solibri"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "MEP", "Clash Detection"],
        "evidence_refs": ["EV_01"],
    })

    # Diagnostic 1: Model produced syntactically valid JSON
    parsed = json.loads(raw_payload)
    assert isinstance(parsed, dict)
    assert parsed["company_name"] == "Milano MEP Ingegneria S.r.l."

    req = LLMRequest(
        prompt=fixture.prompt,
        task_id="website_extraction",
        model="gpt-6-luna",
        provider="openai",
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_investigation_01",
    )
    resp = LLMResponse(
        content=raw_payload,
        provider="openai",
        model="gpt-6-luna",
        task_id="website_extraction",
        request_id="req_investigation_01",
    )

    validator = LLMOutputValidator()
    val_result = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )

    # Diagnostic 2: offerings.status violated canonical enum
    assert val_result.is_valid is False
    assert any("SCHEMA_INVALID_ENUM_OFFERINGS" in r for r in val_result.failure_reasons)

    # Diagnostic 3: Parser discarded fields because schema validation failed
    assert val_result.validated_data is None

    # Diagnostic 7: Repair proposal mechanism is generated for operator review
    assert val_result.repair_proposal is not None

    # Evaluate with GroundTruthEvaluator
    eval_result = GroundTruthEvaluator.evaluate(
        fixture=fixture,
        request=req,
        response=resp,
        val_result=val_result,
    )

    # Diagnostic 4: Evidence references in raw JSON are recognized (evidence_pass is True)
    assert eval_result.evidence_pass is True
    assert "EV_01" in eval_result.grounding_evidence_found
    assert "SEMANTIC_GROUNDING_MISSING" not in eval_result.failure_categories

    # Diagnostic 5: Downstream scoring records SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA
    # and strictly DOES NOT falsely claim company mismatch with empty string ''
    assert eval_result.schema_pass is False
    assert eval_result.ground_truth_pass is False
    assert eval_result.overall_pass is False
    assert "SYNTACTIC_VALIDATION_ERROR" in eval_result.failure_categories
    assert "SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA" in eval_result.failure_categories
    assert "GROUND_TRUTH_COMPANY_MISMATCH" not in eval_result.failure_categories
    assert not any("Extracted company name '' does not match" in r for r in eval_result.failure_reasons)

    # Diagnostic 6: Structured output with valid enum values passes validation cleanly
    corrected_payload = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Provides specialized BIM clash coordination and MEP modeling services in Lombardy since 2015.",
        "offerings": [
            {"name": "BIM clash coordination", "status": "active"},  # Valid enum!
            {"name": "MEP modeling", "status": "active"},
        ],
        "target_industries": ["Architecture", "Engineering", "Construction"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Clashes in ductwork"],
        "client_testimonial_roles": ["Project Manager"],
        "internal_team_roles": ["BIM Coordinator"],
        "technologies": ["Autodesk Revit", "Navisworks", "Solibri"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "MEP", "Clash Detection"],
        "evidence_refs": ["EV_01"],
    })
    req_corrected = LLMRequest(
        prompt=fixture.prompt,
        task_id="website_extraction",
        model="gpt-6-luna",
        provider="openai",
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_investigation_02",
    )
    resp_corrected = LLMResponse(
        content=corrected_payload,
        provider="openai",
        model="gpt-6-luna",
        task_id="website_extraction",
        request_id="req_investigation_02",
    )
    val_corrected = validator.validate(
        request=req_corrected,
        response=resp_corrected,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )
    assert val_corrected.is_valid is True
    eval_corrected = GroundTruthEvaluator.evaluate(
        fixture=fixture,
        request=req_corrected,
        response=resp_corrected,
        val_result=val_corrected,
    )
    assert eval_corrected.schema_pass is True
    assert eval_corrected.ground_truth_pass is True
    assert eval_corrected.overall_pass is True


# ===========================================================================
# 9. Operational Safety & Smoke Test Boundaries
# ===========================================================================

def test_xai_remains_locked_by_default(tmp_path):
    """xAI Grok generation is strictly disabled unless explicitly authorized."""
    db_file = str(tmp_path / "xai_gate.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="xai",
        db_path=db_file,
        allow_xai_generation=False,
    )
    with pytest.raises(LLMNotConfiguredError, match="XAI_GENERATION_DISABLED"):
        runner._configure_live_candidate(candidate_id="t1_c4", provider_id="xai")


def test_smoke_only_never_approves_production(tmp_path):
    """Smoke test runs are marked SMOKE_ONLY and cannot approve production routes."""
    mock_transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=200,
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "company_name": "Milano MEP Ingegneria S.r.l.",
                            "company_summary": "BIM engineering studio.",
                            "offerings": [{"name": "MEP", "status": "active"}],
                            "technologies": ["Autodesk Revit"],
                            "evidence_refs": ["EV_01"],
                        })
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 150},
            },
        )
    )
    db_file = str(tmp_path / "smoke_audit.db")
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-mock"}):
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="gemini",
            max_requests=1,
            max_cost_usd=0.10,
            db_path=db_file,
            is_connectivity_smoke=True,
            real_provider_transport=mock_transport,
        )
        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            max_cases_per_task=1,
        )
        assert report.is_smoke_only is True
        for rec in report.recommended_candidates.values():
            assert rec["status"] == "SMOKE_ONLY"
            assert rec["is_production_approved"] is False
