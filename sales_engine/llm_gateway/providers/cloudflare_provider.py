"""
sales_engine/llm_gateway/providers/cloudflare_provider.py
Cloudflare Workers AI REST adapter for SalesAI LLM Gateway.
Enforces Cloudflare Account ID validation (32 hex characters), isolated token auth,
and strict HTTPS endpoint allowlisting (api.cloudflare.com).
Supports sync and async Chat Completions via Workers AI OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
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
)

DEFAULT_CLOUDFLARE_ALLOWED_HOSTS: Set[str] = {
    "api.cloudflare.com",
}

HEX_32_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")

import math

# Official Cloudflare Workers AI Published Neuron Rates for @cf/meta/llama-3.1-8b-instruct-fp8
# Source: https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/
# Source: https://developers.cloudflare.com/workers-ai/platform/pricing/
CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT = 13778.0
CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT = 26128.0
CLOUDFLARE_NEURON_UNIT_COST_USD = 0.000011  # $0.011 per 1,000 Neurons ($0.000011 / Neuron)
CLOUDFLARE_DAILY_FREE_NEURONS = 10000.0

# Published commercial-equivalent rate-card reference metadata (rounded per 1M tokens)
CLOUDFLARE_FP8_PUBLISHED_INPUT_USD_PER_1M = 0.152   # 13,778 * 0.000011 = $0.151558 -> $0.152
CLOUDFLARE_FP8_PUBLISHED_OUTPUT_USD_PER_1M = 0.287  # 26,128 * 0.000011 = $0.287408 -> $0.287


def _validate_token_count(tokens: Any, name: str) -> int:
    """
    Validate token count strictly:
    - Must be integer (bool is explicitly rejected as bool is a subclass of int in Python).
    - Cannot be NaN or Infinite.
    - Cannot be negative.
    """
    if isinstance(tokens, bool):
        raise TypeError(f"{name} cannot be a boolean, got {tokens!r}")
    if isinstance(tokens, float):
        if math.isnan(tokens) or math.isinf(tokens):
            raise ValueError(f"{name} cannot be NaN or Infinite, got {tokens!r}")
        raise TypeError(f"{name} must be an integer, got float {tokens!r}")
    if not isinstance(tokens, int):
        raise TypeError(f"{name} must be an integer, got {type(tokens).__name__}")
    if tokens < 0:
        raise ValueError(f"{name} cannot be negative, got {tokens}")
    return tokens


def estimate_cloudflare_neurons_exact(
    input_tokens: int,
    output_tokens: int,
    neurons_per_1m_input: float = CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT,
    neurons_per_1m_output: float = CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT,
) -> float:
    """
    Compute exact fractional Cloudflare Workers AI Neurons.
    Based on official Cloudflare published rates for @cf/meta/llama-3.1-8b-instruct-fp8:
    - 13,778 Neurons per 1M input tokens (0.013778 Neurons / token)
    - 26,128 Neurons per 1M output tokens (0.026128 Neurons / token)
    Used for telemetry, historical reconciliation, and exact mathematical conversion.
    """
    in_tok = _validate_token_count(input_tokens, "input_tokens")
    out_tok = _validate_token_count(output_tokens, "output_tokens")
    in_neurons = (in_tok / 1_000_000.0) * neurons_per_1m_input
    out_neurons = (out_tok / 1_000_000.0) * neurons_per_1m_output
    return in_neurons + out_neurons


def estimate_cloudflare_neurons(
    input_tokens: int,
    output_tokens: int,
    neurons_per_1m_input: float = CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT,
    neurons_per_1m_output: float = CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT,
    conservative_ceiling: bool = False,
    per_component_ceiling: bool = False,
) -> float:
    """
    Compute estimated Cloudflare Workers AI Neurons.

    Parameters:
    - input_tokens: count of prompt tokens.
    - output_tokens: count of generated/completion tokens.
    - conservative_ceiling: if True, applies ceiling operation for preflight reservation.
    - per_component_ceiling: if True (with conservative_ceiling=True), applies ceiling
      independently to input and output neuron components: ceil(in_neurons) + ceil(out_neurons).
      Guarantees no under-reservation on output-heavy or input-heavy cases.
      If False, applies ceiling to the combined sum: ceil(in_neurons + out_neurons).

    Default behavior returns exact fractional Neurons (float).
    """
    in_tok = _validate_token_count(input_tokens, "input_tokens")
    out_tok = _validate_token_count(output_tokens, "output_tokens")
    in_neurons = (in_tok / 1_000_000.0) * neurons_per_1m_input
    out_neurons = (out_tok / 1_000_000.0) * neurons_per_1m_output

    if conservative_ceiling:
        if per_component_ceiling:
            return float(math.ceil(in_neurons) + math.ceil(out_neurons))
        return float(math.ceil(in_neurons + out_neurons))

    return in_neurons + out_neurons


def estimate_cloudflare_neurons_preflight(
    input_tokens: int,
    output_tokens: int,
    per_component_ceiling: bool = True,
) -> int:
    """
    Conservative preflight integer Neuron budget reservation.
    By default uses per-component ceiling: ceil(in_neurons) + ceil(out_neurons).
    Guarantees no under-reservation for output-heavy requests (e.g. website extraction pass_a).
    """
    in_tok = _validate_token_count(input_tokens, "input_tokens")
    out_tok = _validate_token_count(output_tokens, "output_tokens")
    in_neurons = (in_tok / 1_000_000.0) * CLOUDFLARE_FP8_NEURONS_PER_1M_INPUT
    out_neurons = (out_tok / 1_000_000.0) * CLOUDFLARE_FP8_NEURONS_PER_1M_OUTPUT
    if per_component_ceiling:
        return math.ceil(in_neurons) + math.ceil(out_neurons)
    return math.ceil(in_neurons + out_neurons)


def estimate_cloudflare_usd_cost(
    input_tokens: int,
    output_tokens: int,
    use_exact_neurons: bool = True,
    neuron_unit_cost_usd: float = CLOUDFLARE_NEURON_UNIT_COST_USD,
) -> float:
    """
    Compute commercial-equivalent USD cost from estimated Neurons.
    Neuron rate card: $0.011 / 1,000 Neurons ($0.000011 / Neuron).
    Exact conversion:
    - Input: 13,778 Neurons / 1M tokens ($0.151558 / 1M)
    - Output: 26,128 Neurons / 1M tokens ($0.287408 / 1M)
    Published rounded rates: $0.152 input / $0.287 output per 1M tokens.
    """
    _validate_token_count(input_tokens, "input_tokens")
    _validate_token_count(output_tokens, "output_tokens")
    if use_exact_neurons:
        neurons = estimate_cloudflare_neurons_exact(input_tokens, output_tokens)
    else:
        neurons = float(estimate_cloudflare_neurons_preflight(input_tokens, output_tokens))
    return round(neurons * neuron_unit_cost_usd, 6)


def resolve_cloudflare_account_id(explicit_id: Optional[str] = None) -> str:
    """
    Resolve Cloudflare Account ID with strict fail-closed validation.
    Checks:
    1. Explicit argument.
    2. CLOUDFLARE_ACCOUNT_ID environment variable.
    3. %APPDATA%/pyBIM/SalesAI/api-secrets/cloudflare-account-id.txt
    Validates strictly against 32 hexadecimal character format.
    """
    candidate = explicit_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")

    if not candidate:
        appdata = os.environ.get("APPDATA")
        if appdata:
            id_file = Path(appdata) / "pyBIM" / "SalesAI" / "api-secrets" / "cloudflare-account-id.txt"
            if id_file.is_file():
                try:
                    candidate = id_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

    if not candidate or not candidate.strip():
        raise LLMNotConfiguredError(
            message="Missing Cloudflare Account ID: set CLOUDFLARE_ACCOUNT_ID or configure via scripts/SALESAI-SEVEN-PROVIDERS.ps1.",
            provider="cloudflare",
        )

    cleaned = candidate.strip()
    if not HEX_32_PATTERN.match(cleaned):
        raise LLMNotConfiguredError(
            message=f"Invalid Cloudflare Account ID format (expected 32 hexadecimal characters).",
            provider="cloudflare",
        )

    return cleaned


class CloudflareProvider(OpenAIProvider):
    """
    Cloudflare Workers AI provider adapter.
    Inherits from OpenAIProvider for chat completions while requiring verified Account ID
    and using Cloudflare's catalog search endpoint for model discovery.
    """

    def __init__(
        self,
        provider_id: str = "cloudflare",
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        base_url: Optional[str] = None,
        allowed_hosts: Optional[Set[str]] = None,
        env_var: str = "CLOUDFLARE_API_TOKEN",
        client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
        default_model: Optional[str] = "@cf/meta/llama-3.1-8b-instruct-fp8",
        model_capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        max_response_bytes: int = 1_000_000,
    ):
        self.account_id = resolve_cloudflare_account_id(account_id)

        target_base_url = (
            base_url
            or f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/v1"
        )
        if "{account_id}" in target_base_url:
            target_base_url = target_base_url.replace("{account_id}", self.account_id)

        super().__init__(
            provider_id=provider_id,
            api_key=api_key,
            base_url=target_base_url,
            allowed_hosts=allowed_hosts or DEFAULT_CLOUDFLARE_ALLOWED_HOSTS,
            env_var=env_var,
            client=client,
            async_client=async_client,
            transport=transport,
            async_transport=async_transport,
            default_model=default_model,
            model_capabilities=model_capabilities,
            max_response_bytes=max_response_bytes,
        )

    def probe_models(self, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """
        Probe Cloudflare Workers AI model catalog using the account-scoped search endpoint.
        Matches the behavior in scripts/SALESAI-SEVEN-PROVIDERS.ps1.
        """
        api_key = self._get_api_key()
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/models/search?per_page=20"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
            "Accept": "application/json",
        }

        timeout = httpx.Timeout(timeout=timeout_seconds, connect=10.0, read=timeout_seconds, write=10.0)
        client_kwargs = {"timeout": timeout, "follow_redirects": False}
        if self._transport:
            client_kwargs["transport"] = self._transport

        t0 = httpx._types.perf_counter() if hasattr(httpx, "_types") and hasattr(httpx._types, "perf_counter") else None
        import time
        start_t = time.perf_counter()

        try:
            if self._injected_client:
                resp = self._injected_client.get(endpoint, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(endpoint, headers=headers)
            end_t = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to Cloudflare Workers AI API at {endpoint}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids: List[str] = []
        try:
            data = resp.json()
            if isinstance(data, dict):
                # Cloudflare returns { success: true, result: [ { name: "@cf/...", ... } ] }
                if data.get("success") is False:
                    raise LLMProviderUnavailableError(
                        message="Cloudflare reported success: false in model catalog response.",
                        provider=self._provider_id,
                        task_id="probe_models",
                    )
                raw_list = data.get("result") or data.get("models") or data.get("data") or []
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, dict):
                            m_id = item.get("name") or item.get("id")
                            if m_id:
                                model_ids.append(str(m_id))
                        elif isinstance(item, str):
                            model_ids.append(item)
        except Exception as e:
            if isinstance(e, (LLMProviderUnavailableError, LLMAuthenticationError)):
                raise
            pass

        return {
            "provider_id": self._provider_id,
            "base_url": self.base_url,
            "status": "AUTHENTICATED",
            "status_code": resp.status_code,
            "latency_ms": round((end_t - start_t) * 1000.0, 2),
            "model_count": len(model_ids),
            "model_ids": sorted(model_ids),
        }

    async def probe_models_async(self, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """Asynchronous Cloudflare Workers AI model catalog probe."""
        api_key = self._get_api_key()
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/models/search?per_page=20"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
            "Accept": "application/json",
        }

        timeout = httpx.Timeout(timeout=timeout_seconds, connect=10.0, read=timeout_seconds, write=10.0)
        client_kwargs = {"timeout": timeout, "follow_redirects": False}
        if self._async_transport:
            client_kwargs["transport"] = self._async_transport

        import time
        start_t = time.perf_counter()

        try:
            if self._injected_async_client:
                resp = await self._injected_async_client.get(endpoint, headers=headers)
            else:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(endpoint, headers=headers)
            end_t = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to Cloudflare Workers AI API at {endpoint}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids: List[str] = []
        try:
            data = resp.json()
            if isinstance(data, dict):
                if data.get("success") is False:
                    raise LLMProviderUnavailableError(
                        message="Cloudflare reported success: false in model catalog response.",
                        provider=self._provider_id,
                        task_id="probe_models",
                    )
                raw_list = data.get("result") or data.get("models") or data.get("data") or []
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, dict):
                            m_id = item.get("name") or item.get("id")
                            if m_id:
                                model_ids.append(str(m_id))
                        elif isinstance(item, str):
                            model_ids.append(item)
        except Exception as e:
            if isinstance(e, (LLMProviderUnavailableError, LLMAuthenticationError)):
                raise
            pass

        return {
            "provider_id": self._provider_id,
            "base_url": self.base_url,
            "status": "AUTHENTICATED",
            "status_code": resp.status_code,
            "latency_ms": round((end_t - start_t) * 1000.0, 2),
            "model_count": len(model_ids),
            "model_ids": sorted(model_ids),
        }
