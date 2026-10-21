"""
tests/test_s2_2_openai_https_adapter.py
Comprehensive deterministic offline test suite for SalesAI S2.2 OpenAI HTTPS Adapter.
Uses httpx.MockTransport with zero live network calls and zero external API dependencies.
"""

import json
import logging
import sys
from typing import Dict, Any, Optional, Set
import pytest
import httpx

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMInvalidSchemaError,
    LLMNotConfiguredError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sales_engine.llm_gateway.providers.openai_provider import (
    OpenAIProvider,
    validate_endpoint_url,
    is_unsafe_host,
)
from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig


# Helper to build canned OpenAI Chat Completion responses
def build_mock_completion(
    content: str = "This is a valid response.",
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 42,
    completion_tokens: int = 15,
    finish_reason: str = "stop",
    tx_id: str = "chatcmpl_mock_12345",
) -> Dict:
    return {
        "id": tx_id,
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": "fp_mock_abc",
    }


# ---------------------------------------------------------------------------
# 1. Endpoint Validation & Network Security Tests
# ---------------------------------------------------------------------------


def test_endpoint_valid_https():
    url = "https://api.openai.com/v1"
    validated = validate_endpoint_url(url, {"api.openai.com"})
    assert validated == "https://api.openai.com/v1"


def test_endpoint_rejects_http():
    with pytest.raises(ValueError, match="secure HTTPS scheme"):
        validate_endpoint_url("http://api.openai.com/v1", {"api.openai.com"})


def test_endpoint_rejects_userinfo():
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_endpoint_url("https://user:pass@api.openai.com/v1", {"api.openai.com"})


def test_endpoint_rejects_loopback_and_private_ips():
    unsafe_urls = [
        "https://localhost/v1",
        "https://127.0.0.1:8000/v1",
        "https://0.0.0.0/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.1/v1",
        "https://172.16.0.1/v1",
        "https://169.254.169.254/v1",  # AWS/cloud metadata address
    ]
    for u in unsafe_urls:
        with pytest.raises(ValueError, match="prohibited"):
            validate_endpoint_url(u, {"localhost", "127.0.0.1", "169.254.169.254", "api.openai.com"})


def test_endpoint_rejects_unapproved_hosts():
    with pytest.raises(ValueError, match="not in the approved allowlist"):
        validate_endpoint_url("https://malicious-api.com/v1", {"api.openai.com"})


# ---------------------------------------------------------------------------
# 2. Construction, Configuration & Fail-Closed Guardrails
# ---------------------------------------------------------------------------


def test_provider_construction_no_network():
    # Constructing provider must not initiate any network calls
    provider = OpenAIProvider(
        api_key="test-key-mock",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    )
    assert provider.provider_id == "openai"
    assert provider.base_url == "https://api.openai.com/v1"


def test_provider_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIProvider(api_key=None, default_model="gpt-4o-mini")

    req = LLMRequest(task_id="Q01", prompt="Extract info")
    with pytest.raises(LLMNotConfiguredError, match="Missing API key"):
        provider.generate(req)


def test_provider_missing_model_fails_closed():
    provider = OpenAIProvider(api_key="test-key", default_model=None)
    req = LLMRequest(task_id="Q01", prompt="Extract info", model=None)

    with pytest.raises(LLMNotConfiguredError, match="No model specified"):
        provider.generate(req)


# ---------------------------------------------------------------------------
# 3. Synchronous and Asynchronous Execution Parity (MockTransport)
# ---------------------------------------------------------------------------


def test_sync_generate_success():
    expected_response = build_mock_completion(
        content='{"company": "Studio BIM Milano", "verified": true}',
        model="gpt-4o-mini",
        prompt_tokens=120,
        completion_tokens=30,
    )

    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("Authorization") == "Bearer sk-test-key-12345"

        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "gpt-4o-mini"
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "Extract BIM info"

        return httpx.Response(200, json=expected_response, headers={"x-request-id": "req-mock-xyz"})

    transport = httpx.MockTransport(handler)
    provider = OpenAIProvider(
        api_key="sk-test-key-12345",
        default_model="gpt-4o-mini",
        transport=transport,
    )

    req = LLMRequest(task_id="Q01_extract", prompt="Extract BIM info")
    resp = provider.generate(req)

    assert resp.content == '{"company": "Studio BIM Milano", "verified": true}'
    assert resp.model == "gpt-4o-mini"
    assert resp.input_tokens == 120
    assert resp.output_tokens == 30
    assert resp.cost_usd is None  # Strictly unmeasured in S2.2
    assert resp.latency_ms is not None and resp.latency_ms >= 0.0
    assert resp.provider_request_id == "req-mock-xyz"


@pytest.mark.asyncio
async def test_async_generate_success():
    expected_response = build_mock_completion(
        content="Async generation successful.",
        model="gpt-4o",
        prompt_tokens=85,
        completion_tokens=20,
    )

    async def async_handler(request: httpx.Request):
        return httpx.Response(200, json=expected_response, headers={"x-request-id": "async-req-789"})

    async_transport = httpx.MockTransport(async_handler)
    provider = OpenAIProvider(
        api_key="sk-async-key",
        default_model="gpt-4o",
        async_transport=async_transport,
    )

    req = LLMRequest(task_id="Q02_async", prompt="Async test prompt")
    resp = await provider.generate_async(req)

    assert resp.content == "Async generation successful."
    assert resp.model == "gpt-4o"
    assert resp.input_tokens == 85
    assert resp.output_tokens == 20
    assert resp.provider_request_id == "async-req-789"


# ---------------------------------------------------------------------------
# 4. Message Roles & Structured Output JSON Mode
# ---------------------------------------------------------------------------


def test_system_prompt_and_user_prompt_formatting():
    captured_body = {}

    def handler(request: httpx.Request):
        nonlocal captured_body
        captured_body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=build_mock_completion())

    transport = httpx.MockTransport(handler)
    provider = OpenAIProvider(api_key="sk-mock", transport=transport)

    req = LLMRequest(
        task_id="Q06_draft",
        model="gpt-4o",
        system_prompt="You are a polite Italian B2B sales assistant.",
        prompt="Draft outreach to Archicad design studio.",
        temperature=0.3,
        max_output_tokens=1500,
    )
    provider.generate(req)

    assert len(captured_body["messages"]) == 2
    assert captured_body["messages"][0]["role"] == "system"
    assert captured_body["messages"][0]["content"] == "You are a polite Italian B2B sales assistant."
    assert captured_body["messages"][1]["role"] == "user"
    assert captured_body["messages"][1]["content"] == "Draft outreach to Archicad design studio."
    assert captured_body["temperature"] == 0.3
    assert captured_body["max_tokens"] == 1500


def test_json_mode_response_format_requested():
    captured_body = {}

    def handler(request: httpx.Request):
        nonlocal captured_body
        captured_body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json=build_mock_completion(content='{"is_qualified": true, "score": 85}'),
        )

    transport = httpx.MockTransport(handler)
    provider = OpenAIProvider(
        api_key="sk-mock",
        default_model="gpt-4o-mini",
        transport=transport,
    )

    req = LLMRequest(
        task_id="Q04_qualify",
        prompt="Rate company",
        schema={"type": "object", "properties": {"is_qualified": {"type": "boolean"}}},
    )
    resp = provider.generate(req)

    assert captured_body.get("response_format") == {"type": "json_object"}
    assert resp.parsed_json == {"is_qualified": True, "score": 85}


def test_unsupported_json_mode_fails_closed():
    # If model capability explicitly marks json mode unsupported
    caps = {"legacy-completion-model": {"supports_json_mode": False}}
    provider = OpenAIProvider(
        api_key="sk-mock",
        default_model="legacy-completion-model",
        model_capabilities=caps,
    )

    req = LLMRequest(
        task_id="Q04",
        prompt="Evaluate",
        schema={"type": "object"},
    )
    with pytest.raises(LLMInvalidSchemaError, match="does not support JSON mode"):
        provider.generate(req)


# ---------------------------------------------------------------------------
# 5. Usage Accounting & Clock-Derived Latency
# ---------------------------------------------------------------------------


def test_missing_usage_preserves_none():
    resp_without_usage = build_mock_completion()
    del resp_without_usage["usage"]

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=resp_without_usage))
    provider = OpenAIProvider(api_key="sk-mock", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    resp = provider.generate(req)

    assert resp.input_tokens is None
    assert resp.output_tokens is None


def test_latency_is_measured_wall_clock():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=build_mock_completion()))
    provider = OpenAIProvider(api_key="sk-mock", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    resp = provider.generate(req)

    # Must be non-negative real float measured from clock
    assert isinstance(resp.latency_ms, float)
    assert resp.latency_ms >= 0.0


# ---------------------------------------------------------------------------
# 6. HTTP Error Mapping Tests
# ---------------------------------------------------------------------------


def test_error_401_authentication():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            401,
            json={"error": {"message": "Invalid API Key provided", "type": "invalid_request_error"}},
        )
    )
    provider = OpenAIProvider(api_key="bad-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMAuthenticationError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "AUTH_ERROR"
    assert not exc_info.value.retryable


def test_error_429_rate_limit():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached for requests", "type": "requests", "code": "rate_limit_exceeded"}},
        )
    )
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMRateLimitError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "RATE_LIMIT"
    assert exc_info.value.retryable is True


def test_error_429_quota_exhausted():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            429,
            json={"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}},
        )
    )
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMQuotaExceededError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "QUOTA_EXCEEDED"
    assert not exc_info.value.retryable


def test_error_500_provider_unavailable():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "Internal server error"}))
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.retryable is True


def test_error_timeout_mapping():
    def timeout_handler(req: httpx.Request):
        raise httpx.ReadTimeout("Read timed out")

    transport = httpx.MockTransport(timeout_handler)
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test", timeout_seconds=5.0)
    with pytest.raises(LLMTimeoutError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "TIMEOUT"
    assert exc_info.value.retryable is True


def test_error_network_connection_mapping():
    def conn_handler(req: httpx.Request):
        raise httpx.ConnectError("Failed to establish connection")

    transport = httpx.MockTransport(conn_handler)
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        provider.generate(req)

    assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 7. Malformed, Incomplete & Adversarial Payload Tests
# ---------------------------------------------------------------------------


def test_empty_choices_raises_invalid_schema():
    empty_choices = {"id": "1", "choices": [], "usage": {}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=empty_choices))
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMInvalidSchemaError, match="no choices"):
        provider.generate(req)


def test_model_refusal_raises_invalid_schema():
    refusal_payload = {
        "id": "1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "refusal": "I cannot fulfill this request."},
                "finish_reason": "stop",
            }
        ],
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=refusal_payload))
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Malicious prompt")
    with pytest.raises(LLMInvalidSchemaError, match="refused generation"):
        provider.generate(req)


def test_invalid_json_body_raises_invalid_schema():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"Not a json string"))
    provider = OpenAIProvider(api_key="sk-key", default_model="gpt-4o", transport=transport)

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMInvalidSchemaError, match="not valid JSON"):
        provider.generate(req)


def test_oversized_response_body_raises_invalid_schema():
    provider = OpenAIProvider(
        api_key="sk-key",
        default_model="gpt-4o",
        max_response_bytes=100,  # small threshold for test
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"X" * 150)),
    )

    req = LLMRequest(task_id="Q01", prompt="Test")
    with pytest.raises(LLMInvalidSchemaError, match="exceeded safety limit"):
        provider.generate(req)


# ---------------------------------------------------------------------------
# 8. Security & Secret Exposure Tests across str, repr, and logs
# ---------------------------------------------------------------------------


def test_str_and_repr_do_not_expose_prompt_or_credentials():
    secret_key = "sk-super-secret-production-key-999"
    secret_prompt = "Confidential business intelligence: project tender for Turin rail station"

    req = LLMRequest(task_id="Q06_draft", prompt=secret_prompt)

    # Check str(request) and repr(request)
    assert secret_prompt not in str(req)
    assert secret_prompt not in repr(req)
    assert f"prompt_length={len(secret_prompt)}" in str(req)

    # Check safe_log_dict
    log_dict = req.safe_log_dict()
    assert "prompt" not in log_dict
    assert log_dict["prompt_length"] == len(secret_prompt)

    # Check response safe str / repr
    secret_response = "Sensitive generated reply with IBAN IT999999"
    resp = LLMResponse(
        content=secret_response,
        provider="openai",
        model="gpt-4o",
        task_id="Q06_draft",
        request_id=req.request_id,
    )
    assert secret_response not in str(resp)
    assert secret_response not in repr(resp)
    assert f"content_length={len(secret_response)}" in str(resp)

    # Check that exceptions do not expose keys
    err = LLMAuthenticationError(message="Auth failed with OpenAI", provider="openai")
    assert secret_key not in str(err)
    assert secret_key not in repr(err)


# ---------------------------------------------------------------------------
# 9. Gateway Integration & Dependency Injection
# ---------------------------------------------------------------------------


def test_gateway_integration_with_openai_provider():
    reg = ProviderRegistry()
    mock_resp = build_mock_completion(content="Integrated gateway output")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=mock_resp))

    openai_prov = OpenAIProvider(
        api_key="sk-gw-test",
        default_model="gpt-4o-mini",
        transport=transport,
    )
    reg.register(openai_prov)

    config = GatewayConfig(enabled=True, default_provider="openai")
    gateway = LLMProviderGateway(registry=reg, config=config)

    req = LLMRequest(task_id="Q01_extract", prompt="Analyze website")
    resp = gateway.generate(req)

    assert resp.content == "Integrated gateway output"
    assert resp.provider == "openai"
    assert resp.model == "gpt-4o-mini"
