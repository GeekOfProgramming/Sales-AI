"""
tests/test_s3_5_1_gemini_diagnostics.py
SalesAI S3.5.1 — Gemini 3.5 Integration & HTTP Error Diagnostics.

Deterministic offline test suite verifying:
1. Gemini 3.5 provider specification and catalog registration (t1_c5, gemini-3.5-flash-lite).
2. Backward compatibility: t1_c2 maps to gemini-2.5-flash-lite; historical mappings preserved.
3. HTTP 404 error mapping: OpenAIProvider and CohereProvider map HTTP 404 to LLMNotFoundError.
4. HTTP 400 error mapping: maps to LLMInvalidSchemaError.
5. HTTP 401/403 error mapping: maps to LLMAuthenticationError.
6. HTTP 429 error mapping: maps to LLMQuotaExceededError or LLMRateLimitError.
7. HTTP 5xx error mapping: maps to LLMProviderUnavailableError.
8. Request serialization and endpoint generation for Gemini v1beta/openai.
9. Free Tier eligibility and billing classification for gemini-3.5-flash-lite.
10. Provider and candidate selection consistency in live and dry-run modes.
11. Structured output and response parsing for gemini-3.5-flash-lite.
12. Budget reservation integrity and non-zero conservative liability accounting.
13. Immutable preservation of historical UNCERTAIN reservation in SQLite ledger.
"""

import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sales_engine.benchmark.external_runner import (
    BenchmarkRunReport,
    ExternalBenchmarkRunner,
)
from sales_engine.benchmark.provider_catalog import (
    GEMINI_2_5_SPEC,
    GEMINI_3_5_SPEC,
    GEMINI_SPEC,
    classify_model_billing,
    get_candidate_spec,
    get_provider_spec,
    list_supported_candidates,
)
from sales_engine.llm_gateway.contracts import LLMRequest, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMInvalidRequestError,
    LLMInvalidSchemaError,
    LLMNotConfiguredError,
    LLMNotFoundError,
    LLMPermissionError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
)
from sales_engine.llm_gateway.budget_ledger import BudgetLedger, BudgetPolicyConfig
from sales_engine.llm_gateway.model_catalog import ModelCatalog, ModelCandidate, PricingMetadata
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider


# ==============================================================================
# 1. Catalog & Connection Spec Integrity
# ==============================================================================

def test_gemini_3_5_spec_integrity():
    """Verify GEMINI_3_5_SPEC has correct model ID, candidate slot, endpoint, and pricing metadata."""
    assert GEMINI_3_5_SPEC.provider_id == "gemini"
    assert GEMINI_3_5_SPEC.candidate_id == "t1_c5"
    assert GEMINI_3_5_SPEC.exact_model_id == "gemini-3.5-flash-lite"
    assert GEMINI_3_5_SPEC.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert "generativelanguage.googleapis.com" in GEMINI_3_5_SPEC.allowed_hosts
    assert GEMINI_3_5_SPEC.env_var == "GEMINI_API_KEY"
    assert GEMINI_3_5_SPEC.billing_mode == "VERIFIED_FREE_TIER"
    assert GEMINI_3_5_SPEC.is_generation_enabled is True

    # Pricing & conservative accounting
    pricing = GEMINI_3_5_SPEC.pricing
    assert pricing is not None
    assert pricing.currency == "FREE"
    assert pricing.input_per_1m == 0.30
    assert pricing.output_per_1m == 2.50

    # Capabilities
    caps = GEMINI_3_5_SPEC.capabilities
    assert caps["supports_json_schema"] is True
    assert caps["supports_json_mode"] is True
    assert caps["max_tokens_key"] == "max_tokens"


def test_gemini_2_5_historical_preservation():
    """Verify historical gemini-2.5-flash-lite remains preserved on t1_c2 without data loss."""
    assert GEMINI_2_5_SPEC.candidate_id == "t1_c2"
    assert GEMINI_2_5_SPEC.exact_model_id == "gemini-2.5-flash-lite"
    assert get_candidate_spec("t1_c2").exact_model_id == "gemini-2.5-flash-lite"
    assert get_candidate_spec("t1_c5").exact_model_id == "gemini-3.5-flash-lite"


def test_provider_and_candidate_lookups():
    """Verify get_provider_spec and get_candidate_spec resolve correctly."""
    # Default gemini provider spec resolves to the verified active gemini-3.5-flash-lite
    gemini_default = get_provider_spec("gemini")
    assert gemini_default.exact_model_id == "gemini-3.5-flash-lite"
    assert gemini_default.candidate_id == "t1_c5"

    # Specific candidate lookups
    assert get_provider_spec("gemini", candidate_id="t1_c2").exact_model_id == "gemini-2.5-flash-lite"
    assert get_provider_spec("gemini", candidate_id="t1_c5").exact_model_id == "gemini-3.5-flash-lite"

    # All candidates list includes t1_c5
    all_cands = list_supported_candidates(all_candidates=True)
    assert "t1_c5" in all_cands
    assert "t1_c2" in all_cands


def test_catalog_can_register_t1_c5():
    """Verify ModelCatalog safely registers and resolves extension slot t1_c5."""
    catalog = ModelCatalog.create_default_16_slot_catalog()
    assert len(catalog.candidates) == 16  # Canonical 16 slots preserved

    # Register extension slot t1_c5
    t1_c5_candidate = ModelCandidate(
        candidate_id="t1_c5",
        tier=ModelTier.TIER_1_BUDGET,
        priority=5,
        provider_id="gemini",
        exact_model_id="gemini-3.5-flash-lite",
        enabled=False,
    )
    catalog.register_candidate(t1_c5_candidate)
    assert len(catalog.candidates) == 17
    resolved = catalog.get_candidate("t1_c5")
    assert resolved is not None
    assert resolved.candidate_id == "t1_c5"
    assert resolved.priority == 5


def test_classify_model_billing_gemini():
    """Verify billing classification for Gemini models."""
    assert classify_model_billing("gemini", "gemini-3.5-flash-lite") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("gemini", "gemini-2.5-flash-lite") == "VERIFIED_FREE_TIER"
    assert classify_model_billing("gemini", "unknown-model") == "UNKNOWN"


# ==============================================================================
# 2. HTTP Error Diagnostics (OpenAI-compatible & Cohere)
# ==============================================================================

def test_http_404_maps_to_llm_not_found_error():
    """Verify HTTP 404 raises LLMNotFoundError distinguishing model-not-found from provider downtime."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            404,
            json={
                "error": {
                    "message": "models/gemini-2.5-flash-lite is not found for API version v1beta",
                    "type": "not_found",
                    "code": 404,
                }
            },
        )
    )
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyTestKeyOffline",
        transport=transport,
        default_model="gemini-2.5-flash-lite",
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test prompt",
        model="gemini-2.5-flash-lite",
    )

    with pytest.raises(LLMNotFoundError) as exc_info:
        provider.generate(req)

    err = exc_info.value
    assert err.error_code == "NOT_FOUND"
    assert err.retryable is False
    assert "404" in str(err)
    assert "not found" in str(err).lower()


def test_http_400_maps_to_invalid_request_error():
    """Verify HTTP 400 raises LLMInvalidRequestError (invalid arguments / bad request, NOT invalid schema)."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            400,
            json={"error": {"message": "Invalid request parameter: temperature out of range", "code": 400}},
        )
    )
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyTestKeyOffline",
        transport=transport,
        default_model="gemini-3.5-flash-lite",
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test prompt",
        model="gemini-3.5-flash-lite",
    )

    with pytest.raises(LLMInvalidRequestError) as exc_info:
        provider.generate(req)

    err = exc_info.value
    assert err.error_code == "INVALID_REQUEST"
    assert err.retryable is False
    assert "400" in str(err)


def test_http_401_maps_to_authentication_error():
    """Verify HTTP 401 raises LLMAuthenticationError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            401,
            json={"error": {"message": "API key not valid", "code": 401}},
        )
    )
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyBadKey",
        transport=transport,
        default_model="gemini-3.5-flash-lite",
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test",
        model="gemini-3.5-flash-lite",
    )
    with pytest.raises(LLMAuthenticationError) as exc_info:
        provider.generate(req)
    assert exc_info.value.error_code == "AUTH_ERROR"
    assert exc_info.value.retryable is False


def test_http_403_maps_to_permission_error():
    """Verify HTTP 403 raises LLMPermissionError (distinguishing permission denied from bad key)."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            403,
            json={"error": {"message": "Permission denied for project", "code": 403}},
        )
    )
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyRestrictedKey",
        transport=transport,
        default_model="gemini-3.5-flash-lite",
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test",
        model="gemini-3.5-flash-lite",
    )
    with pytest.raises(LLMPermissionError) as exc_info:
        provider.generate(req)
    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert exc_info.value.retryable is False


def test_http_429_quota_vs_rate_limit():
    """Verify HTTP 429 distinguishes quota exhaustion from transient rate limiting."""
    # Quota exceeded
    t_quota = httpx.MockTransport(
        lambda req: httpx.Response(
            429,
            json={"error": {"message": "Resource has been exhausted (e.g. check quota)", "code": 429}},
        )
    )
    p_quota = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyKey",
        transport=t_quota,
        default_model="gemini-3.5-flash-lite",
    )
    req = LLMRequest(task_id="website_extraction", prompt="Test", model="gemini-3.5-flash-lite")
    with pytest.raises(LLMQuotaExceededError):
        p_quota.generate(req)

    # Transient rate limit
    t_rate = httpx.MockTransport(
        lambda req: httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached for requests per minute", "code": 429}},
        )
    )
    p_rate = OpenAIProvider(
        provider_id="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        allowed_hosts={"generativelanguage.googleapis.com"},
        env_var="GEMINI_API_KEY",
        api_key="AIzaSyKey",
        transport=t_rate,
        default_model="gemini-3.5-flash-lite",
    )
    with pytest.raises(LLMRateLimitError):
        p_rate.generate(req)


def test_http_5xx_maps_to_provider_unavailable():
    """Verify HTTP 500, 502, 503 raise LLMProviderUnavailableError with retryable=True."""
    for sc in (500, 502, 503):
        t_5xx = httpx.MockTransport(
            lambda req, code=sc: httpx.Response(code, text="Internal Server Error")
        )
        p_5xx = OpenAIProvider(
            provider_id="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            allowed_hosts={"generativelanguage.googleapis.com"},
            env_var="GEMINI_API_KEY",
            api_key="AIzaSyKey",
            transport=t_5xx,
            default_model="gemini-3.5-flash-lite",
        )
        req = LLMRequest(task_id="website_extraction", prompt="Test", model="gemini-3.5-flash-lite")
        with pytest.raises(LLMProviderUnavailableError) as exc_info:
            p_5xx.generate(req)
        assert exc_info.value.retryable is True


def test_cohere_http_error_mapping():
    """Verify Cohere provider maps 400 (LLMInvalidRequestError) and 404 (LLMNotFoundError) cleanly."""
    t_400 = httpx.MockTransport(
        lambda req: httpx.Response(400, json={"message": "invalid request format"})
    )
    p_cohere_400 = CohereProvider(
        provider_id="cohere",
        base_url="https://api.cohere.com/v2",
        allowed_hosts={"api.cohere.com"},
        env_var="COHERE_API_KEY",
        api_key="cohere-key",
        transport=t_400,
        default_model="command-r-08-2024",
    )
    req = LLMRequest(task_id="website_extraction", prompt="Test", model="command-r-08-2024")
    with pytest.raises(LLMInvalidRequestError):
        p_cohere_400.generate(req)

    t_404 = httpx.MockTransport(
        lambda req: httpx.Response(404, json={"message": "model not found"})
    )
    p_cohere = CohereProvider(
        provider_id="cohere",
        base_url="https://api.cohere.com/v2",
        allowed_hosts={"api.cohere.com"},
        env_var="COHERE_API_KEY",
        api_key="cohere-key",
        transport=t_404,
        default_model="command-r-08-2024",
    )
    with pytest.raises(LLMNotFoundError):
        p_cohere.generate(req)


# ==============================================================================
# 3. Request Serialization & Response Parsing
# ==============================================================================

def test_gemini_request_serialization_and_response_parsing():
    """Verify request serialization matches OpenAI-compatible endpoint for Gemini and parses 200 OK."""
    recorded_requests = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert request.headers["Authorization"] == "Bearer AIzaSyLiveMockKey"

        body = json.loads(request.read().decode("utf-8"))
        assert body["model"] == "gemini-3.5-flash-lite"
        assert "messages" in body
        assert "response_format" in body

        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-gemini-35-test",
                "object": "chat.completion",
                "created": 1729780000,
                "model": "gemini-3.5-flash-lite",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "company_name": "pyBIM Solutions",
                                "products_services": ["pyBIM Clash Automation"],
                                "target_audience": "BIM and Revit engineers",
                                "evidence_refs": ["EV01"],
                            }),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 25,
                    "total_tokens": 37,
                },
            },
        )

    transport = httpx.MockTransport(handle_request)
    provider = OpenAIProvider(
        provider_id="gemini",
        base_url=GEMINI_3_5_SPEC.base_url,
        allowed_hosts=GEMINI_3_5_SPEC.allowed_hosts,
        env_var=GEMINI_3_5_SPEC.env_var,
        api_key="AIzaSyLiveMockKey",
        transport=transport,
        default_model=GEMINI_3_5_SPEC.exact_model_id,
        model_capabilities={GEMINI_3_5_SPEC.exact_model_id: GEMINI_3_5_SPEC.capabilities},
    )

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Analyze company website content",
        model="gemini-3.5-flash-lite",
        max_output_tokens=256,
        response_schema={
            "type": "object",
            "properties": {
                "company_name": {"type": "string"},
                "products_services": {"type": "array", "items": {"type": "string"}},
                "target_audience": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["company_name", "products_services", "target_audience", "evidence_refs"],
        },
    )

    response = provider.generate(req)
    assert response.content is not None
    assert response.model == "gemini-3.5-flash-lite"
    assert response.input_tokens == 12
    assert response.output_tokens == 25
    parsed = json.loads(response.content)
    assert parsed["company_name"] == "pyBIM Solutions"
    assert len(recorded_requests) == 1


# ==============================================================================
# 4. Runner Candidate Selection & Dry-Run Consistency
# ==============================================================================

def test_dry_run_provider_gemini_recommends_gemini_candidate():
    """Verify that dry-run with --provider gemini consistently selects and recommends candidate t1_c5."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        provider_filter="gemini",
    )
    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        max_cases_per_task=1,
    )

    # In dry-run mode with provider_filter="gemini", recommendations must be for t1_c5
    rec = report.recommended_candidates.get("website_extraction:pass_a")
    assert rec is not None
    assert rec["candidate_id"] == "t1_c5"
    assert rec["exact_model_id"] == "gemini-3.5-flash-lite"


def test_live_runner_configures_gemini_3_5_with_free_only(tmp_path):
    """Verify live runner configures gemini-3.5-flash-lite when --provider gemini --free-only is passed."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "company_name": "Test Co",
                                "products_services": ["Service A"],
                                "target_audience": "Engineers",
                                "evidence_refs": ["EV_WEB_A_01"],
                            }),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
            },
        )
    )

    test_db = str(tmp_path / "test_ledger.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        max_requests=1,
        max_cost_usd=0.10,
        provider_filter="gemini",
        free_only=True,
        is_connectivity_smoke=True,
        real_provider_transport=transport,
        db_path=test_db,
    )

    # _configure_live_candidate should enable t1_c5
    runner._configure_live_candidate(candidate_id="t1_c5", provider_id="gemini")
    active_cand = runner.catalog.get_candidate("t1_c5")
    assert active_cand is not None
    assert active_cand.enabled is True
    assert active_cand.exact_model_id == "gemini-3.5-flash-lite"
    assert active_cand.pricing.currency == "FREE"


def test_live_runner_rejects_candidate_mismatch(tmp_path):
    """Verify live runner fails closed when candidate and provider do not match."""
    test_db = str(tmp_path / "test_ledger.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="gemini",
        real_provider_transport=httpx.MockTransport(lambda req: httpx.Response(200)),
        db_path=test_db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t1_c1", provider_id="gemini")
    assert "does not match provider" in str(exc_info.value)


# ==============================================================================
# 5. Ledger & Historical UNCERTAIN Reservation Preservation
# ==============================================================================

def test_uncertain_reservation_budget_lifecycle_isolated(tmp_path):
    """
    Verify deterministically using an isolated SQLite ledger that UNCERTAIN reservations
    retain preflight liability without being zeroed out.
    """
    db_file = str(tmp_path / "portable_budget.db")
    policy = BudgetPolicyConfig(
        enabled=True,
        max_request_spend_usd=0.05,
        daily_limit_usd=1.00,
        monthly_limit_usd=15.00,
        max_concurrent_requests=1,
    )
    ledger = BudgetLedger(db_path=db_file, config=policy)

    # Create a reservation
    catalog = ModelCatalog.create_default_16_slot_catalog()
    candidate = catalog.get_candidate("t1_c2")
    pricing = PricingMetadata(
        currency="FREE",
        input_per_1m=0.075,
        output_per_1m=0.30,
        effective_date="2026-10-23",
    )
    candidate = candidate.model_copy(update={"pricing": pricing, "provider_id": "gemini", "exact_model_id": "gemini-2.5-flash-lite"})
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Sample test prompt",
        max_output_tokens=2048,
    )
    res_obj = ledger.reserve(req, candidate)
    res_id = res_obj.reservation_id
    assert res_id.startswith("res_")

    # Mark failed in-flight (dispatched=True -> UNCERTAIN)
    ledger.mark_reservation_failed(res_id, error_code="PROVIDER_UNAVAILABLE", dispatched=True)

    # Check status and retained spend
    with ledger._lock:
        conn = ledger._get_connection()
        try:
            row = conn.execute(
                "SELECT status, failure_code, reserved_usd, final_cost_usd FROM llm_reservations WHERE reservation_id = ?;",
                (res_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == "UNCERTAIN"
            assert row[1] == "PROVIDER_UNAVAILABLE"
            assert row[2] > 0.0
            assert row[3] == row[2]  # Liability retained
        finally:
            conn.close()


def test_historical_uncertain_reservation_preserved_in_db():
    """
    Verify the historical UNCERTAIN reservation 'res_dce87e9ce2a9'
    from the S3.4 gemini-2.5-flash-lite live smoke failure is intact in the workspace database.
    If running in a clean release export where runtime databases are intentionally excluded,
    this test skips gracefully.
    """
    db_path = "data/benchmark_budget.db"
    if not os.path.exists(db_path):
        pytest.skip("Runtime database data/benchmark_budget.db is not present (e.g. clean source export).")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT reservation_id, model, status, failure_code, reserved_usd, final_cost_usd
            FROM llm_reservations
            WHERE reservation_id = 'res_dce87e9ce2a9';
            """
        )
        row = cursor.fetchone()
        assert row is not None, "Reservation res_dce87e9ce2a9 must be present in workspace ledger"
        res_id, model, status, failure_code, reserved_usd, final_cost_usd = row
        assert res_id == "res_dce87e9ce2a9"
        assert model == "gemini-2.5-flash-lite"
        assert status == "UNCERTAIN"
        assert failure_code == "PROVIDER_UNAVAILABLE"
        assert reserved_usd > 0.0
        # The preflight estimate was held as the liability for the uncertain call
        assert final_cost_usd == reserved_usd
    finally:
        conn.close()
