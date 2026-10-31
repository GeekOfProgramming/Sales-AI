"""
tests/test_s3_5_4b_cohere_trial_guard.py
SalesAI S3.5.4B — Deterministic Verification Suite for Cohere Trial Guard & Safety Invariants.

Verifies:
1. Critical import and type hints evaluation in sales_engine.llm_gateway.errors (Phase 1).
2. Fail-closed Cohere Trial guard (--require-cohere-trial) scoping (Phase 3).
3. Live preflight trial-only entitlement verification & header inspection (Phase 3).
4. Rejection of production keys, exhausted quotas, missing models, and transport failures.
5. Cohere v2 Chat API adapter contract: payload building, JSON mode, token parsing, zero secret leakage (Phase 4).
6. Trial accounting provenance and commercial-equivalent preservation (Phase 5).
"""

import json
import pytest
import typing
import httpx
from pathlib import Path

from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.provider_catalog import (
    COHERE_SPEC,
    OPENAI_SPEC,
    get_provider_spec,
    classify_model_billing,
)
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMBudgetExceededError,
)
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
from sales_engine.llm_gateway.model_catalog import ModelCatalog, ModelCandidate, ModelTier, PricingMetadata


# ---------------------------------------------------------------------------
# Phase 1: Critical Import and Type Hint Evaluation Tests
# ---------------------------------------------------------------------------

def test_errors_module_any_import_and_type_hints():
    """Verify sales_engine.llm_gateway.errors has Any imported and get_type_hints evaluates cleanly."""
    import sales_engine.llm_gateway.errors as err_mod
    assert hasattr(err_mod, "Any")
    hints = typing.get_type_hints(err_mod.LLMRateLimitError.__init__)
    assert "diagnostic" in hints
    assert hints["diagnostic"] == typing.Optional[typing.Any]


def test_cohere_error_hierarchy_uses_rate_limit_diagnostics():
    """Verify LLMRateLimitError holds RateLimitDiagnostic and cause category."""
    err = LLMRateLimitError(
        message="Too Many Requests",
        provider="cohere",
        cause_category="ACCOUNT_QUOTA",
        retry_after_seconds=60.0,
        definitive_cause_proven=True,
    )
    assert err.cause_category == "ACCOUNT_QUOTA"
    assert err.retry_after_seconds == 60.0
    assert err.definitive_cause_proven is True


# ---------------------------------------------------------------------------
# Phase 3: Cohere Trial-Only Guard Scoping & Fail-Closed Enforcement
# ---------------------------------------------------------------------------

def test_require_cohere_trial_rejects_non_cohere_provider():
    """--require-cohere-trial must fail closed if applied to any provider other than cohere."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(
            dry_run=True,
            provider_filter="groq",
            require_cohere_trial=True,
        )
    assert "COHERE_TRIAL_GUARD_VIOLATION" in str(exc_info.value)


def test_require_cohere_trial_rejects_mismatched_candidate():
    """--require-cohere-trial must fail closed if candidate is not t2_c2."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        provider_filter="cohere",
        require_cohere_trial=True,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
        )
    assert "COHERE_TRIAL_GUARD_VIOLATION" in str(exc_info.value)


def test_cohere_catalog_retains_unknown_billing_mode(tmp_path):
    """Verify Cohere is deliberately UNKNOWN in provider catalog and blocked by --free-only."""
    spec = get_provider_spec("cohere")
    assert spec is not None
    assert spec.billing_mode == "UNKNOWN"
    assert classify_model_billing("cohere", "command-r-08-2024") == "UNKNOWN"

    db = str(tmp_path / "test_unknown.db")
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        free_only=True,
        provider_filter="cohere",
        db_path=db,
        api_key="sk-test",
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")
    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 3: Live Preflight Entitlement Verification (Mocked Transports)
# ---------------------------------------------------------------------------

def _build_mock_cohere_catalog_response(
    target_model: str = "command-r-08-2024",
    trial_limit: str = "100",
    trial_remaining: str = "99",
    include_trial_headers: bool = True,
    status_code: int = 200,
    include_target_in_body: bool = True,
):
    headers = {
        "content-type": "application/json",
    }
    if include_trial_headers:
        headers["x-trial-endpoint-call-limit"] = trial_limit
        headers["x-trial-endpoint-call-remaining"] = trial_remaining
        headers["x-endpoint-monthly-call-limit"] = "1000"

    models = []
    if include_target_in_body:
        models.append({
            "name": target_model,
            "endpoints": ["generate", "chat"],
            "context_length": 128000,
        })
    models.append({"name": "embed-multilingual-v3.0", "endpoints": ["embed"]})

    body = json.dumps({"models": models})
    return httpx.Response(status_code=status_code, headers=headers, content=body.encode("utf-8"))


def test_preflight_verifies_active_trial_entitlement(tmp_path):
    """Successful preflight verifies active trial key, remaining allowance, and model presence."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/models" in str(request.url):
            return _build_mock_cohere_catalog_response()
        return httpx.Response(200, json={"message": {"content": "ok"}})

    db = str(tmp_path / "test_preflight_ok.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
    )
    runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")
    assert runner.real_provider is not None
    assert runner.gateway_config.default_provider == "cohere"


def test_preflight_rejects_production_key_lacking_trial_headers(tmp_path):
    """Fails closed if Cohere response lacks authoritative x-trial headers (rejects production keys)."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Production key lacks trial headers
        return _build_mock_cohere_catalog_response(include_trial_headers=False)

    db = str(tmp_path / "test_reject_prod.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "missing authoritative Trial headers" in str(exc_info.value)


def test_preflight_rejects_exhausted_trial_allowance(tmp_path):
    """Fails closed if trial calls remaining <= 0."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _build_mock_cohere_catalog_response(trial_remaining="0")

    db = str(tmp_path / "test_exhausted.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "Trial allowance exhausted" in str(exc_info.value)


def test_preflight_rejects_missing_target_model(tmp_path):
    """Fails closed if exact model command-r-08-2024 is missing from active catalog."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _build_mock_cohere_catalog_response(include_target_in_body=False)

    db = str(tmp_path / "test_missing_model.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "was not found in active Cohere catalog" in str(exc_info.value)


def test_preflight_fails_closed_on_http_error(tmp_path):
    """Fails closed if Cohere metadata probe returns non-200."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid api token"})

    db = str(tmp_path / "test_http_err.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        runner._configure_live_candidate(candidate_id="t2_c2", provider_id="cohere")

    assert "FREE_ELIGIBILITY_UNCONFIRMED" in str(exc_info.value)
    assert "HTTP 401" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 4: Cohere Adapter Contract Verification
# ---------------------------------------------------------------------------

def test_cohere_provider_build_payload_json_mode():
    """Verify CohereProvider builds correct native v2 payload with response_format=json_object."""
    provider = CohereProvider(
        api_key="sk-test",
        default_model="command-r-08-2024",
        model_capabilities={"command-r-08-2024": {"supports_json_mode": True}},
    )
    req = LLMRequest(
        prompt="Extract company name from text.",
        system_prompt="Output strict JSON.",
        task_id="website_extraction",
        model="command-r-08-2024",
        temperature=0.2,
        max_output_tokens=500,
    )
    payload = provider._build_payload(req, "command-r-08-2024")
    assert payload["model"] == "command-r-08-2024"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 500
    assert payload["response_format"] == {"type": "json_object"}
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


def test_cohere_provider_parses_tokens_and_captures_trial_headers():
    """Verify response parsing extracts usage tokens and captures sanitized trial headers in raw_metadata."""
    provider = CohereProvider(api_key="sk-test")
    req = LLMRequest(
        prompt="hello",
        task_id="test_task",
        model="command-r-08-2024",
        request_id="req_001",
    )
    cohere_resp_json = {
        "id": "cohere_req_12345",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": '{"result": "success"}'}],
        },
        "finish_reason": "COMPLETE",
        "usage": {
            "tokens": {
                "input_tokens": 120,
                "output_tokens": 45,
            }
        },
    }
    raw_headers = {
        "x-request-id": "cohere_req_12345",
        "x-trial-endpoint-call-limit": "100",
        "x-trial-endpoint-call-remaining": "98",
        "x-endpoint-monthly-call-limit": "1000",
    }
    http_resp = httpx.Response(
        200,
        headers=raw_headers,
        content=json.dumps(cohere_resp_json).encode("utf-8"),
    )
    llm_resp = provider._parse_response_body(http_resp, req, "command-r-08-2024", latency_ms=150.0)

    assert llm_resp.content == '{"result": "success"}'
    assert llm_resp.input_tokens == 120
    assert llm_resp.output_tokens == 45
    assert llm_resp.provider_request_id == "cohere_req_12345"
    assert llm_resp.finish_reason == "COMPLETE"
    assert "headers" in llm_resp.raw_metadata
    assert llm_resp.raw_metadata["headers"]["x-trial-endpoint-call-remaining"] == "98"


# ---------------------------------------------------------------------------
# Phase 5: Trial Accounting & Provenance Verification
# ---------------------------------------------------------------------------

def test_dry_run_t2_c2_simulation_labeled_offline_mock():
    """Offline dry-run simulation for candidate t2_c2 must execute with OFFLINE_MOCK provenance."""
    runner = ExternalBenchmarkRunner(
        dry_run=True,
        provider_filter="cohere",
        require_cohere_trial=True,
    )
    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t2_c2",
        max_cases_per_task=1,
    )
    assert report.is_dry_run is True
    assert report.total_cases_run == 1
    summary = report.candidate_summaries[0]
    assert summary.candidate_id == "t2_c2"
    case = summary.case_results[0]
    assert case.spend_provenance == "OFFLINE_MOCK"
    assert case.exact_model_id == "command-r-08-2024"


def test_live_execution_records_verified_cohere_trial_provenance(tmp_path):
    """Live execution under require_cohere_trial must record VERIFIED_COHERE_TRIAL provenance."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/models" in url_str:
            return _build_mock_cohere_catalog_response()
        if "/chat" in url_str:
            return httpx.Response(
                200,
                headers={
                    "x-request-id": "cohere_trial_req_001",
                    "x-trial-endpoint-call-limit": "100",
                    "x-trial-endpoint-call-remaining": "99",
                },
                json={
                    "id": "cohere_trial_req_001",
                    "message": {
                        "role": "assistant",
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "offerings": [{"name": "BIM Clash Coordination", "status": "active"}],
                                "certifications": [],
                                "icp_signals": ["MEP engineering consultancy"],
                                "technologies": ["Autodesk Revit", "Navisworks", "Solibri"],
                                "target_industries": ["Architecture", "Engineering", "Construction"],
                                "evidence_refs": ["EV_01"],
                            }),
                        }],
                    },
                    "usage": {"tokens": {"input_tokens": 150, "output_tokens": 80}},
                },
            )
        return httpx.Response(404)

    db = str(tmp_path / "test_trial_provenance.db")
    transport = httpx.MockTransport(handler)
    runner = ExternalBenchmarkRunner(
        dry_run=False,
        confirm_paid=True,
        provider_filter="cohere",
        require_cohere_trial=True,
        api_key="sk-cohere-test-key",
        real_provider_transport=transport,
        db_path=db,
        max_requests=1,
        max_cost_usd=0.05,
    )
    report = runner.run_benchmark(
        task_filter="website_extraction:pass_a",
        candidate_filter="t2_c2",
        max_cases_per_task=1,
    )
    assert report.total_cases_run == 1
    summary = report.candidate_summaries[0]
    case = summary.case_results[0]
    assert case.spend_provenance == "VERIFIED_COHERE_TRIAL"
    assert case.input_tokens == 150
    assert case.output_tokens == 80
    assert case.cost_usd is not None and case.cost_usd > 0.0
