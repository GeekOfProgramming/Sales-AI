"""
tests/test_s3_3_four_provider_onboarding.py
Comprehensive offline test suite for SalesAI S3.3 Four-Provider API Onboarding.

Verifies:
- Provider connection specs and endpoint safety validation
- Missing key and no cross-vendor fallback
- Free-only fail-closed gate (FREE_ELIGIBILITY_UNCONFIRMED)
- xAI Grok generation lock (XAI_GENERATION_DISABLED)
- Model parameter compatibility (max_tokens vs max_completion_tokens, json_mode)
- Read-only probe_models metadata queries (HTTP 200/401/403/429/500)
- Free-tier accounting ($0.000000 actual spend, risk reservation ceiling)
- Missing usage -> UNCERTAIN (never $0)
- Request caps and budget ceilings
- Output directory isolation
- Existing OpenAI baseline preservation
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Any

import httpx
import pytest

from sales_engine.benchmark.external_runner import (
    BenchmarkCaseExecutionResult,
    ExternalBenchmarkRunner,
)
from sales_engine.benchmark.provider_catalog import (
    GEMINI_SPEC,
    GROQ_SPEC,
    OPENAI_SPEC,
    XAI_SPEC,
    get_candidate_spec,
    get_provider_spec,
    list_supported_candidates,
    list_supported_providers,
)
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.contracts import LLMRequest, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMNotConfiguredError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUncertainUsageError,
)
from sales_engine.llm_gateway.providers.openai_provider import (
    OpenAIProvider,
    validate_endpoint_url,
)


# ===========================================================================
# 1. Catalog & Endpoint Validation Tests
# ===========================================================================

def test_provider_catalog_specs_integrity():
    """Verify that all four providers have complete, immutable connection specs."""
    providers = list_supported_providers()
    assert set(providers) == {"openai", "gemini", "groq", "xai"}

    candidates = list_supported_candidates()
    assert set(candidates) == {"t1_c1", "t1_c2", "t1_c3", "t1_c4"}

    for p in providers:
        spec = get_provider_spec(p)
        assert spec is not None
        assert spec.base_url.startswith("https://")
        assert len(spec.allowed_hosts) > 0
        assert spec.env_var.endswith("_API_KEY")
        assert spec.pricing is not None
        assert spec.pricing.input_per_1m > 0
        assert spec.pricing.output_per_1m > 0
        assert spec.capabilities.get("supports_json_mode") is True


def test_endpoint_validation_unsafe_host_rejection():
    """Rejects unsafe, private, loopback, or non-HTTPS endpoint URLs."""
    allowed = {"api.openai.com", "generativelanguage.googleapis.com", "api.groq.com", "api.x.ai"}

    # Prohibited hosts
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://localhost/v1", allowed)
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://127.0.0.1/v1", allowed)
    with pytest.raises(ValueError, match="is prohibited"):
        validate_endpoint_url("https://192.168.1.1/v1", allowed)

    # Non-HTTPS
    with pytest.raises(ValueError, match="secure HTTPS scheme"):
        validate_endpoint_url("http://api.openai.com/v1", allowed)

    # Embedded credentials
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_endpoint_url("https://user:pass@api.openai.com/v1", allowed)

    # Unapproved external host
    with pytest.raises(ValueError, match="not in the approved allowlist"):
        validate_endpoint_url("https://api.unapproved-vendor.com/v1", allowed)


def test_endpoint_validation_approved_hosts():
    """Approves verified static endpoints for all four providers."""
    allowed = {"api.openai.com", "generativelanguage.googleapis.com", "api.groq.com", "api.x.ai"}
    assert validate_endpoint_url(OPENAI_SPEC.base_url, allowed) == "https://api.openai.com/v1"
    assert validate_endpoint_url(GEMINI_SPEC.base_url, allowed) == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert validate_endpoint_url(GROQ_SPEC.base_url, allowed) == "https://api.groq.com/openai/v1"
    assert validate_endpoint_url(XAI_SPEC.base_url, allowed) == "https://api.x.ai/v1"


# ===========================================================================
# 2. Key Isolation & No Cross-Vendor Fallback Tests
# ===========================================================================

def test_missing_key_fails_closed_per_provider(monkeypatch):
    """Missing provider environment key fails closed naming the exact key."""
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    for prov, spec in [("openai", OPENAI_SPEC), ("gemini", GEMINI_SPEC), ("groq", GROQ_SPEC), ("xai", XAI_SPEC)]:
        provider = OpenAIProvider(
            provider_id=prov,
            base_url=spec.base_url,
            allowed_hosts=spec.allowed_hosts,
            env_var=spec.env_var,
        )
        with pytest.raises(LLMNotConfiguredError) as exc_info:
            provider._get_api_key()
        assert spec.env_var in str(exc_info.value)


def test_no_cross_vendor_key_fallback(monkeypatch):
    """Having OPENAI_API_KEY set must NEVER satisfy Gemini, Groq, or xAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-openai-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    gemini_prov = OpenAIProvider(
        provider_id="gemini",
        base_url=GEMINI_SPEC.base_url,
        allowed_hosts=GEMINI_SPEC.allowed_hosts,
        env_var="GEMINI_API_KEY",
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        gemini_prov._get_api_key()
    assert "GEMINI_API_KEY" in str(exc_info.value)
    assert "sk-proj-openai-secret" not in str(exc_info.value)


def test_candidate_provider_mismatch_fails_closed():
    """Attempting to pair candidate t1_c1 (OpenAI) with provider 'gemini' fails closed."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
    )
    # Testing _configure_live_candidate validation
    with pytest.raises(LLMNotConfiguredError, match="does not match provider"):
        runner._configure_live_candidate(candidate_id="t1_c1", provider_id="gemini")


# ===========================================================================
# 3. Free-Only Gate & xAI Generation Lock Tests
# ===========================================================================

def test_free_only_gate_accepts_gemini_and_groq(tmp_path):
    """--free-only permits Gemini and Groq (VERIFIED_FREE_TIER)."""
    db = str(tmp_path / "test_free.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))

    # Gemini
    runner_gemini = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="gemini",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )
    runner_gemini._configure_live_candidate(candidate_id="t1_c2", provider_id="gemini")
    assert runner_gemini.catalog.get_candidate("t1_c2").enabled is True

    # Groq
    runner_groq = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="groq",
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )
    runner_groq._configure_live_candidate(candidate_id="t1_c3", provider_id="groq")
    assert runner_groq.catalog.get_candidate("t1_c3").enabled is True


def test_free_only_gate_rejects_openai_and_xai(tmp_path):
    """--free-only rejects OpenAI and xAI with FREE_ELIGIBILITY_UNCONFIRMED."""
    db = str(tmp_path / "test_free_rej.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        free_only=True,
        db_path=db,
        real_provider_transport=transport,
    )

    # OpenAI is VERIFIED_PAID -> must refuse
    with pytest.raises(LLMNotConfiguredError) as exc_openai:
        runner._configure_live_candidate(candidate_id="t1_c1", provider_id="openai")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_openai.value)

    # xAI is UNKNOWN -> must refuse
    with pytest.raises(LLMNotConfiguredError) as exc_xai:
        runner._configure_live_candidate(candidate_id="t1_c4", provider_id="xai")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_xai.value)


def test_xai_generation_strictly_disabled_by_default(tmp_path):
    """Attempting live generation with xAI without --allow-xai-paid raises XAI_GENERATION_DISABLED."""
    db = str(tmp_path / "test_xai_lock.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="xai",
        allow_xai_generation=False,  # default
        db_path=db,
        real_provider_transport=transport,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t1_c4", provider_id="xai")
    assert "XAI_GENERATION_DISABLED" in str(exc_info.value)


def test_xai_generation_authorized_with_explicit_flag(tmp_path):
    """When allow_xai_generation=True, xAI candidate t1_c4 is configured."""
    db = str(tmp_path / "test_xai_auth.db")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="xai",
        allow_xai_generation=True,
        db_path=db,
        real_provider_transport=transport,
    )
    runner._configure_live_candidate(candidate_id="t1_c4", provider_id="xai")
    cand = runner.catalog.get_candidate("t1_c4")
    assert cand is not None
    assert cand.enabled is True
    assert cand.exact_model_id == "grok-4-fast"


# ===========================================================================
# 4. Request Payload & Parameter Compatibility Tests
# ===========================================================================

def test_token_parameter_selection_gemini_and_groq():
    """Gemini and Groq use max_tokens; OpenAI luna uses max_completion_tokens."""
    # Gemini
    gemini_prov = OpenAIProvider(
        provider_id="gemini",
        base_url=GEMINI_SPEC.base_url,
        allowed_hosts=GEMINI_SPEC.allowed_hosts,
        api_key="mock-gemini-key",
        default_model=GEMINI_SPEC.exact_model_id,
        model_capabilities={GEMINI_SPEC.exact_model_id: GEMINI_SPEC.capabilities},
    )
    req = LLMRequest(task_id="website_extraction", prompt="Test prompt", max_output_tokens=350)
    p_gemini = gemini_prov._build_payload(req, GEMINI_SPEC.exact_model_id)
    assert "max_tokens" in p_gemini
    assert p_gemini["max_tokens"] == 350
    assert "max_completion_tokens" not in p_gemini

    # Groq
    groq_prov = OpenAIProvider(
        provider_id="groq",
        base_url=GROQ_SPEC.base_url,
        allowed_hosts=GROQ_SPEC.allowed_hosts,
        api_key="mock-groq-key",
        default_model=GROQ_SPEC.exact_model_id,
        model_capabilities={GROQ_SPEC.exact_model_id: GROQ_SPEC.capabilities},
    )
    p_groq = groq_prov._build_payload(req, GROQ_SPEC.exact_model_id)
    assert "max_tokens" in p_groq
    assert p_groq["max_tokens"] == 350
    assert "max_completion_tokens" not in p_groq

    # OpenAI luna
    openai_prov = OpenAIProvider(
        provider_id="openai",
        base_url=OPENAI_SPEC.base_url,
        allowed_hosts=OPENAI_SPEC.allowed_hosts,
        api_key="mock-openai-key",
        default_model=OPENAI_SPEC.exact_model_id,
        model_capabilities={OPENAI_SPEC.exact_model_id: OPENAI_SPEC.capabilities},
    )
    p_openai = openai_prov._build_payload(req, OPENAI_SPEC.exact_model_id)
    assert "max_completion_tokens" in p_openai
    assert p_openai["max_completion_tokens"] == 350
    assert "max_tokens" not in p_openai


def test_json_mode_parameter_handling():
    """All 4 providers support json_mode response_format."""
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test",
        response_schema={"type": "object"},
    )
    for spec in (OPENAI_SPEC, GEMINI_SPEC, GROQ_SPEC, XAI_SPEC):
        prov = OpenAIProvider(
            provider_id=spec.provider_id,
            base_url=spec.base_url,
            allowed_hosts=spec.allowed_hosts,
            api_key="mock-key",
            default_model=spec.exact_model_id,
            model_capabilities={spec.exact_model_id: spec.capabilities},
        )
        payload = prov._build_payload(req, spec.exact_model_id)
        assert payload.get("response_format") == {"type": "json_object"}


# ===========================================================================
# 5. Metadata Probe (GET /models) Offline Mock Tests
# ===========================================================================

def test_probe_models_success_200():
    """Mock GET /models returns 200 with list of models."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gemini-2.5-flash-lite", "object": "model"},
                    {"id": "gemini-1.5-pro", "object": "model"},
                ]
            },
        )
    )
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url=GEMINI_SPEC.base_url,
        allowed_hosts=GEMINI_SPEC.allowed_hosts,
        api_key="mock-key",
        transport=transport,
    )
    res = provider.probe_models()
    assert res["status"] == "AUTHENTICATED"
    assert res["status_code"] == 200
    assert res["model_count"] == 2
    assert "gemini-2.5-flash-lite" in res["model_ids"]


def test_probe_models_auth_error_401():
    """Mock GET /models returns 401, raises LLMAuthenticationError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})
    )
    provider = OpenAIProvider(
        provider_id="groq",
        base_url=GROQ_SPEC.base_url,
        allowed_hosts=GROQ_SPEC.allowed_hosts,
        api_key="bad-groq-key",
        transport=transport,
    )
    with pytest.raises(LLMAuthenticationError) as exc_info:
        provider.probe_models()
    assert exc_info.value.error_code == "AUTH_ERROR"


def test_probe_models_quota_error_429():
    """Mock GET /models returns 429 with insufficient_quota, raises LLMQuotaExceededError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "You exceeded your current quota."}})
    )
    provider = OpenAIProvider(
        provider_id="openai",
        base_url=OPENAI_SPEC.base_url,
        allowed_hosts=OPENAI_SPEC.allowed_hosts,
        api_key="mock-key",
        transport=transport,
    )
    with pytest.raises(LLMQuotaExceededError) as exc_info:
        provider.probe_models()
    assert exc_info.value.error_code == "QUOTA_EXCEEDED"


def test_probe_models_rate_limit_429():
    """Mock GET /models returns 429 with rate_limit_exceeded, raises LLMRateLimitError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached."}})
    )
    provider = OpenAIProvider(
        provider_id="groq",
        base_url=GROQ_SPEC.base_url,
        allowed_hosts=GROQ_SPEC.allowed_hosts,
        api_key="mock-key",
        transport=transport,
    )
    with pytest.raises(LLMRateLimitError) as exc_info:
        provider.probe_models()
    assert exc_info.value.error_code == "RATE_LIMIT"


def test_probe_models_timeout():
    """Mock GET /models timeout raises LLMTimeoutError."""
    def timeout_handler(req):
        raise httpx.ReadTimeout("Timeout connecting to server")

    transport = httpx.MockTransport(timeout_handler)
    provider = OpenAIProvider(
        provider_id="openai",
        base_url=OPENAI_SPEC.base_url,
        allowed_hosts=OPENAI_SPEC.allowed_hosts,
        api_key="mock-key",
        transport=transport,
    )
    with pytest.raises(LLMTimeoutError) as exc_info:
        provider.probe_models(timeout_seconds=0.1)
    assert exc_info.value.error_code == "TIMEOUT"


@pytest.mark.asyncio
async def test_probe_models_async_200():
    """Mock async GET /models returns 200 with list of models."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "openai/gpt-oss-20b", "object": "model"},
                ]
            },
        )
    )
    provider = OpenAIProvider(
        provider_id="groq",
        base_url=GROQ_SPEC.base_url,
        allowed_hosts=GROQ_SPEC.allowed_hosts,
        api_key="mock-key",
        async_transport=transport,
    )
    res = await provider.probe_models_async()
    assert res["status"] == "AUTHENTICATED"
    assert res["status_code"] == 200
    assert "openai/gpt-oss-20b" in res["model_ids"]


def test_probe_models_server_error_500():
    """Mock GET /models returns 500, raises LLMProviderUnavailableError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(500, json={"error": {"message": "Internal Server Error"}})
    )
    provider = OpenAIProvider(
        provider_id="xai",
        base_url=XAI_SPEC.base_url,
        allowed_hosts=XAI_SPEC.allowed_hosts,
        api_key="mock-key",
        transport=transport,
    )
    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        provider.probe_models()
    assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"


# ===========================================================================
# 6. Live Benchmark Dispatch & Accounting Offline Mock Tests
# ===========================================================================

def test_free_tier_reconciles_zero_cost(tmp_path):
    """
    Simulate live smoke dispatch with Google Gemini (gemini-2.5-flash-lite).
    Actual spend must reconcile to $0.000000 while liability reservation is held safely.
    """
    db_file = str(tmp_path / "gemini_free.db")
    valid_website_content = json.dumps({
        "company_name": "Studio Ingegneria BIM S.r.l.",
        "company_summary": "Studio Ingegneria BIM S.r.l. is an architectural consultancy.",
        "offerings": [{"name": "BIM Modeling", "status": "active"}],
        "target_industries": ["Architecture"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Clash detection"],
        "client_testimonial_roles": ["Project Manager"],
        "internal_team_roles": ["Technical Director"],
        "technologies": ["Autodesk Revit", "Navisworks"],
        "evidence_refs": ["EV_01"],
    })

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "chatcmpl-gemini-free-test",
                "object": "chat.completion",
                "model": "gemini-2.5-flash-lite",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": valid_website_content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
            },
        )
    )

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="gemini",
        free_only=True,
        max_requests=1,
        max_cost_usd=0.01,
        db_path=db_file,
        real_provider_transport=transport,
        is_connectivity_smoke=True,
    )

    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t1_c2",
        max_cases_per_task=1,
    )

    assert report.total_cases_run == 1
    # Cost reconciled to $0.000000 for FREE tier
    assert report.total_cost_usd == 0.0
    case_res = report.candidate_summaries[0].case_results[0]
    assert case_res.cost_usd == 0.0
    assert case_res.spend_provenance == "VERIFIED_FREE_TIER"
    assert case_res.sqlite_reservation_status == "RECONCILED"
    assert case_res.estimated_spend_usd is not None and case_res.estimated_spend_usd > 0.0

    # Ledger audit
    audit = runner.get_run_ledger_summary(report.run_id)
    assert audit["total_cost_usd"] == 0.0
    assert audit["status_counts"].get("RECONCILED") == 1


def test_missing_usage_marks_reservation_uncertain_not_free(tmp_path):
    """When provider response omits usage dict, reservation is held as UNCERTAIN, never $0."""
    db_file = str(tmp_path / "gemini_no_usage.db")
    content = json.dumps({"company_name": "Test", "offerings": [{"name": "A", "status": "active"}]})

    # Response with missing usage!
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "chatcmpl-no-usage",
                "model": "gemini-2.5-flash-lite",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                # No "usage" field
            },
        )
    )

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="gemini",
        free_only=True,
        max_requests=1,
        max_cost_usd=0.01,
        db_path=db_file,
        real_provider_transport=transport,
    )

    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t1_c2",
        max_cases_per_task=1,
    )

    audit = runner.get_run_ledger_summary(report.run_id)
    assert audit["status_counts"].get("UNCERTAIN") == 1
    # Uncertain spend retains the non-zero reservation amount!
    assert audit["uncertain_cost_usd"] > 0.0


def test_one_request_cap_stops_second_dispatch(tmp_path):
    """--max-requests 1 enforces hard ceiling: second case is aborted pre-dispatch."""
    db_file = str(tmp_path / "req_cap.db")
    content = json.dumps({"company_name": "Test", "offerings": [{"name": "A", "status": "active"}]})
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "model": "openai/gpt-oss-20b",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 50},
            },
        )
    )

    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="groq",
        free_only=True,
        max_requests=1,
        max_cost_usd=0.05,
        db_path=db_file,
        real_provider_transport=transport,
    )

    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t1_c3",
        max_cases_per_task=2,  # asks for 2, but max_requests is 1!
    )

    summary = report.candidate_summaries[0]
    assert len(summary.case_results) == 2
    assert "RUN_REQUEST_CAP_REACHED" not in summary.case_results[0].failure_categories
    # Second case aborted before HTTP dispatch
    assert "RUN_REQUEST_CAP_REACHED" in summary.case_results[1].failure_categories


def test_two_key_gate_enforced_for_free_providers(tmp_path):
    """Attempting live execution with Gemini or Groq without --confirm-paid fails closed."""
    db_file = str(tmp_path / "two_key.db")
    with pytest.raises(LLMNotConfiguredError, match="Two-key execution gate"):
        ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=False,  # NOT confirmed!
            provider_filter="gemini",
            free_only=True,
            db_path=db_file,
        )


def test_smoke_only_cannot_qualify_model(tmp_path):
    """A single-case smoke run marks recommendation as SMOKE_ONLY, not production qualified."""
    db_file = str(tmp_path / "smoke_qual.db")
    valid_content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Milano MEP Ingegneria S.r.l. is a specialized engineering firm.",
        "offerings": [{"name": "BIM coordination", "status": "active"}],
        "target_industries": ["Engineering"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Clash detection delay"],
        "client_testimonial_roles": ["Project Manager"],
        "internal_team_roles": ["Technical Director"],
        "technologies": ["Autodesk Revit", "Navisworks"],
        "evidence_refs": ["EV_01"],
    })
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "cmpl-luna",
                "model": "gpt-6-luna",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": valid_content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )
    )
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="openai",
        max_requests=1,
        max_cost_usd=0.05,
        db_path=db_file,
        real_provider_transport=transport,
        is_connectivity_smoke=True,
    )
    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t1_c1",
        max_cases_per_task=1,
    )
    assert report.is_smoke_only is True
    rec = report.recommended_candidates.get("website_extraction:pass_a")
    assert rec is not None
    assert rec["status"] == "SMOKE_ONLY"
    assert rec["is_production_approved"] is False


def test_separate_output_folders_per_provider(tmp_path):
    """CSV and JSON exports can be routed to provider-specific subdirectories."""
    out_gemini = tmp_path / "runtime" / "benchmarks" / "gemini"
    out_groq = tmp_path / "runtime" / "benchmarks" / "groq"

    rep_gen_gemini = S3BenchmarkReportGenerator(output_dir=out_gemini)
    rep_gen_groq = S3BenchmarkReportGenerator(output_dir=out_groq)

    assert rep_gen_gemini.output_dir != rep_gen_groq.output_dir
    assert rep_gen_gemini.output_dir.name == "gemini"
    assert rep_gen_groq.output_dir.name == "groq"


def test_cli_probe_models_exits_cleanly(monkeypatch):
    """scripts/probe_llm_providers.py reports NOT_CONFIGURED when key is missing and exits 0."""
    import sys
    from scripts.probe_llm_providers import main as probe_main
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["probe_llm_providers.py", "--provider", "openai"])
    code = probe_main()
    assert code == 0


def test_cli_benchmark_free_only_guard(monkeypatch, tmp_path):
    """CLI fails closed when --free-only is combined with a paid provider."""
    import sys
    from scripts.benchmark_external_llm import main as bench_main
    db_file = str(tmp_path / "cli_free_guard.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-val")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_external_llm.py",
            "--live",
            "--confirm-paid",
            "--provider", "openai",
            "--free-only",
            "--task", "website_extraction:pass_a",
            "--db-path", db_file,
        ],
    )
    code = bench_main()
    assert code == 1
