"""
tests/test_s2_1_llm_gateway_foundation.py
Comprehensive deterministic offline test suite for SalesAI S2.1 LLM Gateway Foundation.
Zero external network calls, zero model downloads, zero GPU requirements.
"""

import math
import sys
import pytest
from pydantic import ValidationError

from sales_engine.llm_gateway.contracts import ModelTier, LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import (
    LLMError,
    LLMNotConfiguredError,
    LLMAuthenticationError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMQuotaExceededError,
    LLMBudgetExceededError,
    LLMInvalidSchemaError,
    LLMProviderUnavailableError,
)
from sales_engine.llm_gateway.interface import BaseLLMProvider, LLMProviderGateway
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig


class OfflineFakeProvider(BaseLLMProvider):
    """Deterministic offline test-only provider. Makes zero network calls."""

    def __init__(
        self,
        provider_id: str = "fake_test_provider",
        canned_content: str = '{"status": "ok", "qualification": "accepted"}',
        input_tokens: int = 150,
        output_tokens: int = 45,
        cost_usd: float = 0.0002,
        latency_ms: float = 120.0,
        fail_with_error: Exception = None,
    ):
        self._provider_id = provider_id
        self.canned_content = canned_content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.fail_with_error = fail_with_error
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.fail_with_error:
            raise self.fail_with_error

        return LLMResponse(
            content=self.canned_content,
            provider=self._provider_id,
            model=request.model or "fake-model-v1",
            task_id=request.task_id,
            request_id=request.request_id,
            parsed_json={"status": "ok", "qualification": "accepted"},
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
            finish_reason="stop",
            provider_request_id=f"fake_tx_{self.call_count}",
        )

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        return self.generate(request)


# ---------------------------------------------------------------------------
# 1. Request Contract Tests (Validation & Boundaries)
# ---------------------------------------------------------------------------


def test_request_valid_construction_and_defaults():
    req = LLMRequest(
        task_id="Q01_website_pass_a",
        prompt="Extract company name from HTML",
    )
    assert req.task_id == "Q01_website_pass_a"
    assert req.prompt == "Extract company name from HTML"
    assert req.temperature == 0.2
    assert req.max_output_tokens == 2048
    assert req.timeout_seconds == 30.0
    assert req.request_id.startswith("req_")
    assert req.provider is None
    assert req.model is None
    assert req.tier is None
    assert req.response_schema is None


def test_request_schema_alias_compatibility():
    schema_dict = {"type": "object", "properties": {"name": {"type": "string"}}}
    # Should accept both alias 'schema' and field 'response_schema'
    req1 = LLMRequest(task_id="Q06_draft", prompt="Draft email", schema=schema_dict)
    assert req1.response_schema == schema_dict

    req2 = LLMRequest(task_id="Q06_draft", prompt="Draft email", response_schema=schema_dict)
    assert req2.response_schema == schema_dict


def test_request_empty_fields_fail():
    with pytest.raises(ValidationError):
        LLMRequest(task_id="", prompt="Valid prompt")

    with pytest.raises(ValidationError):
        LLMRequest(task_id="valid_task", prompt="")


def test_request_temperature_boundaries():
    # Valid bounds
    r0 = LLMRequest(task_id="t1", prompt="p", temperature=0.0)
    assert r0.temperature == 0.0
    r1 = LLMRequest(task_id="t1", prompt="p", temperature=1.0)
    assert r1.temperature == 1.0

    # Negative or above 1.0
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", temperature=-0.01)

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", temperature=1.01)

    # NaN and Inf must fail
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", temperature=float("nan"))

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", temperature=float("inf"))


def test_request_timeout_boundaries():
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", timeout_seconds=0.0)

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", timeout_seconds=-10.0)

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", timeout_seconds=301.0)

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", timeout_seconds=float("inf"))


def test_request_max_tokens_boundaries():
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", max_output_tokens=0)

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", max_output_tokens=32769)


def test_request_oversized_prompt():
    huge_prompt = "A" * 100_001
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt=huge_prompt)


def test_request_metadata_sanitization():
    # Safe metadata
    req = LLMRequest(task_id="t1", prompt="p", metadata={"lead_id": "lead_123", "step": 4})
    assert req.metadata["lead_id"] == "lead_123"

    # Prohibited keys
    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", metadata={"api_key": "sk-12345"})

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", metadata={"user_password": "secret"})

    with pytest.raises(ValidationError):
        LLMRequest(task_id="t1", prompt="p", metadata={"contact_email": "ceo@corp.com"})


def test_request_safe_repr_no_prompt_pii_leak():
    secret_prompt = "Confidential customer details: Mario Rossi, VAT IT123456789"
    req = LLMRequest(task_id="Q04_classify", prompt=secret_prompt)
    rep = repr(req)
    assert secret_prompt not in rep
    assert f"prompt_length={len(secret_prompt)}" in rep
    assert "task_id='Q04_classify'" in rep


def test_request_model_tier_enum():
    req = LLMRequest(
        task_id="Q02_extract",
        prompt="Extract signals",
        tier=ModelTier.TIER_1_BUDGET,
    )
    assert req.tier == ModelTier.TIER_1_BUDGET
    assert req.tier.value == "tier_1_budget"


# ---------------------------------------------------------------------------
# 2. Response Contract Tests
# ---------------------------------------------------------------------------


def test_response_valid_construction():
    resp = LLMResponse(
        content='{"verified": true}',
        provider="fake_provider",
        model="fake-v1",
        task_id="Q01_facts",
        request_id="req_abc123",
        parsed_json={"verified": True},
        input_tokens=250,
        output_tokens=30,
        cost_usd=0.00045,
        latency_ms=340.5,
        finish_reason="stop",
    )
    assert resp.content == '{"verified": true}'
    assert resp.input_tokens == 250
    assert resp.output_tokens == 30
    assert resp.cost_usd == 0.00045
    assert resp.latency_ms == 340.5


def test_response_unmeasured_metrics_are_none_not_zero():
    # If unmeasured, metrics must be None, never fabricated as 0
    resp = LLMResponse(
        content="Raw output text",
        provider="fake_provider",
        model="fake-v1",
        task_id="Q01_facts",
        request_id="req_abc123",
    )
    assert resp.input_tokens is None
    assert resp.output_tokens is None
    assert resp.cost_usd is None
    assert resp.latency_ms is None


def test_response_negative_metrics_prohibited():
    with pytest.raises(ValidationError):
        LLMResponse(
            content="txt", provider="p", model="m", task_id="t", request_id="r",
            input_tokens=-1
        )

    with pytest.raises(ValidationError):
        LLMResponse(
            content="txt", provider="p", model="m", task_id="t", request_id="r",
            cost_usd=-0.01
        )

    with pytest.raises(ValidationError):
        LLMResponse(
            content="txt", provider="p", model="m", task_id="t", request_id="r",
            latency_ms=-50.0
        )


def test_response_nan_or_inf_prohibited():
    with pytest.raises(ValidationError):
        LLMResponse(
            content="txt", provider="p", model="m", task_id="t", request_id="r",
            cost_usd=float("nan")
        )

    with pytest.raises(ValidationError):
        LLMResponse(
            content="txt", provider="p", model="m", task_id="t", request_id="r",
            latency_ms=float("inf")
        )


# ---------------------------------------------------------------------------
# 3. Provider Registry & Fail-Closed Gateway Tests
# ---------------------------------------------------------------------------


def test_registry_registration_and_lookup():
    reg = ProviderRegistry()
    fake = OfflineFakeProvider(provider_id="fake_one")

    assert not reg.has("fake_one")
    reg.register(fake)
    assert reg.has("fake_one")
    assert reg.get("fake_one") is fake
    assert reg.list_providers() == ["fake_one"]


def test_registry_duplicate_rejection():
    reg = ProviderRegistry()
    fake1 = OfflineFakeProvider(provider_id="dup_provider")
    fake2 = OfflineFakeProvider(provider_id="dup_provider")

    reg.register(fake1)
    # Reject duplicate by default
    with pytest.raises(ValueError, match="already registered"):
        reg.register(fake2)

    # Allow override when explicitly requested
    reg.register(fake2, allow_override=True)
    assert reg.get("dup_provider") is fake2


def test_gateway_disabled_by_default_fails_closed():
    # Gateway instantiated without config must be disabled
    gw = LLMProviderGateway()
    assert not gw.config.enabled

    req = LLMRequest(task_id="Q01", prompt="Hello", provider="fake_one")
    with pytest.raises(LLMNotConfiguredError, match="currently disabled"):
        gw.generate(req)


@pytest.mark.asyncio
async def test_gateway_async_disabled_by_default_fails_closed():
    gw = LLMProviderGateway()
    req = LLMRequest(task_id="Q01", prompt="Hello", provider="fake_one")
    with pytest.raises(LLMNotConfiguredError, match="currently disabled"):
        await gw.generate_async(req)


def test_gateway_missing_provider_fails_closed():
    config = GatewayConfig(enabled=True)
    gw = LLMProviderGateway(config=config)

    # No provider specified in request and no default in config
    req = LLMRequest(task_id="Q01", prompt="Hello")
    with pytest.raises(LLMNotConfiguredError, match="No LLM provider specified"):
        gw.generate(req)


def test_gateway_unregistered_provider_fails_closed():
    config = GatewayConfig(enabled=True)
    gw = LLMProviderGateway(config=config)

    req = LLMRequest(task_id="Q01", prompt="Hello", provider="nonexistent_provider")
    with pytest.raises(LLMNotConfiguredError, match="is not registered"):
        gw.generate(req)


def test_gateway_successful_sync_invocation():
    reg = ProviderRegistry()
    provider = OfflineFakeProvider(provider_id="test_prov")
    reg.register(provider)

    config = GatewayConfig(enabled=True, default_provider="test_prov")
    gw = LLMProviderGateway(registry=reg, config=config)

    req = LLMRequest(task_id="Q06_outreach", prompt="Write sales email")
    resp = gw.generate(req)

    assert resp.content == '{"status": "ok", "qualification": "accepted"}'
    assert resp.provider == "test_prov"
    assert resp.task_id == "Q06_outreach"
    assert resp.input_tokens == 150
    assert resp.cost_usd == 0.0002
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_gateway_successful_async_invocation():
    reg = ProviderRegistry()
    provider = OfflineFakeProvider(provider_id="async_prov")
    reg.register(provider)

    config = GatewayConfig(enabled=True, default_provider="async_prov")
    gw = LLMProviderGateway(registry=reg, config=config)

    req = LLMRequest(task_id="Q01_web", prompt="Analyze website")
    resp = await gw.generate_async(req)

    assert resp.content == '{"status": "ok", "qualification": "accepted"}'
    assert resp.provider == "async_prov"
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# 4. Standardized Error Hierarchy Tests
# ---------------------------------------------------------------------------


def test_error_classes_and_attributes():
    errors = [
        (LLMNotConfiguredError, "NOT_CONFIGURED", False),
        (LLMAuthenticationError, "AUTH_ERROR", False),
        (LLMTimeoutError, "TIMEOUT", True),
        (LLMRateLimitError, "RATE_LIMIT", True),
        (LLMQuotaExceededError, "QUOTA_EXCEEDED", False),
        (LLMBudgetExceededError, "BUDGET_EXCEEDED", False),
        (LLMInvalidSchemaError, "INVALID_SCHEMA", False),
        (LLMProviderUnavailableError, "PROVIDER_UNAVAILABLE", True),
    ]

    for err_cls, expected_code, expected_retryable in errors:
        err = err_cls(provider="test_p", task_id="t_1")
        assert issubclass(err_cls, LLMError)
        assert err.error_code == expected_code
        assert err.retryable == expected_retryable
        assert err.provider == "test_p"
        assert err.task_id == "t_1"

        # Safe formatting does not leak sensitive information
        err_str = str(err)
        assert f"[{expected_code}]" in err_str
        assert "provider=test_p" in err_str


# ---------------------------------------------------------------------------
# 5. Dependency Isolation & Architectural Guardrails
# ---------------------------------------------------------------------------


def test_no_heavy_ml_packages_imported_in_gateway():
    """Verify gateway package does not import heavy native ML wheels or Ollama in a clean Python process."""
    import subprocess
    cmd = (
        "import sys, sales_engine.llm_gateway; "
        "prohibited = ['ollama', 'chromadb', 'onnxruntime', 'torch', 'transformers', 'openai', 'anthropic', 'google.generativeai']; "
        "loaded = [p for p in prohibited if p in sys.modules]; "
        "assert not loaded, f'Prohibited modules loaded: {loaded}'; "
        "print('CLEAN')"
    )
    result = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
    assert result.returncode == 0, f"Import test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout
