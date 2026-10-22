"""
sales_engine/llm_gateway/providers/openai_provider.py
Lightweight OpenAI-compatible HTTPS REST adapter for SalesAI LLM Gateway.
Uses httpx without commercial vendor SDKs. Supports sync and async invocations.
"""

import ipaddress
import json
import os
import re
import time
from typing import Optional, Set, Dict, Any
from urllib.parse import urlparse

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
from sales_engine.llm_gateway.interface import BaseLLMProvider


DEFAULT_ALLOWED_HOSTS: Set[str] = {
    "api.openai.com",
}


def is_unsafe_host(hostname: str) -> bool:
    """Check if hostname is a private/loopback/link-local address or localhost."""
    if not hostname:
        return True
    lower = hostname.lower()
    if lower in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(lower)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        # Not an IP literal, standard domain name
        return False


def validate_endpoint_url(url: str, allowed_hosts: Set[str]) -> str:
    """
    Validate that the endpoint URL is a safe, allowlisted HTTPS endpoint.
    Rejects non-HTTPS, loopback, private networks, and userinfo URLs.
    """
    if not url:
        raise ValueError("Endpoint URL cannot be empty.")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Endpoint URL must use secure HTTPS scheme, got {parsed.scheme!r}")

    if parsed.username or parsed.password:
        raise ValueError("Endpoint URL cannot contain embedded credentials / userinfo.")

    host = parsed.hostname
    if not host or is_unsafe_host(host):
        raise ValueError(f"Endpoint host {host!r} is prohibited (private/loopback/unsafe destination).")

    if host.lower() not in {h.lower() for h in allowed_hosts}:
        raise ValueError(
            f"Endpoint host {host!r} is not in the approved allowlist: {sorted(list(allowed_hosts))}"
        )

    return url.rstrip("/")


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI-compatible Chat Completions HTTPS REST adapter.
    Implements BaseLLMProvider with fail-closed configuration and strict boundary guards.
    """

    def __init__(
        self,
        provider_id: str = "openai",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        allowed_hosts: Optional[Set[str]] = None,
        client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
        default_model: Optional[str] = None,
        model_capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        max_response_bytes: int = 1_000_000,
    ):
        self._provider_id = provider_id
        self._api_key = api_key
        self.allowed_hosts = allowed_hosts or DEFAULT_ALLOWED_HOSTS
        self.base_url = validate_endpoint_url(base_url, self.allowed_hosts)
        self.default_model = default_model
        self.model_capabilities = model_capabilities or {}
        self.max_response_bytes = max_response_bytes

        # Transports and clients (for dependency injection / testing)
        self._injected_client = client
        self._injected_async_client = async_client
        self._transport = transport
        self._async_transport = async_transport

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _get_api_key(self) -> str:
        """Retrieve API key from constructor or environment. Raises if missing."""
        key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not key or not key.strip():
            raise LLMNotConfiguredError(
                message=f"Missing API key for provider '{self._provider_id}'. Set OPENAI_API_KEY.",
                provider=self._provider_id,
            )
        return key.strip()

    def _resolve_model(self, request: LLMRequest) -> str:
        """Resolve model name from request or default. Rejects if unspecified."""
        model = request.model or self.default_model
        if not model or not model.strip():
            raise LLMNotConfiguredError(
                message=f"No model specified for provider '{self._provider_id}'.",
                provider=self._provider_id,
                task_id=request.task_id,
            )
        return model.strip()

    def _build_payload(self, request: LLMRequest, model: str) -> Dict[str, Any]:
        """Build canonical Chat Completions JSON payload with capability checks."""
        messages = []
        if request.system_prompt and request.system_prompt.strip():
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        cap = self.model_capabilities.get(model, {})

        is_reasoning = (
            cap.get("is_reasoning_model", False)
            or cap.get("use_max_completion_tokens", False)
            or "o1" in model.lower()
            or "o3" in model.lower()
            or "luna" in model.lower()
        )

        reasoning_effort = (request.metadata or {}).get("reasoning_effort") or cap.get("reasoning_effort")

        # Temperature: reasoning models with reasoning effort other than 'none' do not support temperature
        supports_temp = cap.get("supports_temperature", True)
        omit_temperature = (
            not supports_temp
            or (is_reasoning and reasoning_effort != "none")
            or (reasoning_effort is not None and reasoning_effort != "none")
        )

        if not omit_temperature:
            payload["temperature"] = request.temperature

        # Token ceiling: reasoning models use max_completion_tokens, standard models use max_tokens
        use_max_completion_tokens = cap.get("use_max_completion_tokens", False) or is_reasoning
        if use_max_completion_tokens:
            payload["max_completion_tokens"] = request.max_output_tokens
        else:
            payload["max_tokens"] = request.max_output_tokens

        if reasoning_effort and reasoning_effort != "none":
            payload["reasoning_effort"] = reasoning_effort

        # JSON Mode / Structured Output
        if request.response_schema is not None or cap.get("force_json_mode", False):
            if not cap.get("supports_json_mode", True):
                raise LLMInvalidSchemaError(
                    message=f"Model '{model}' does not support JSON mode / structured outputs.",
                    provider=self._provider_id,
                    task_id=request.task_id,
                )
            payload["response_format"] = {"type": "json_object"}

        return payload

    def _parse_http_error(self, resp: httpx.Response, task_id: str) -> Exception:
        """Map HTTP error status codes to standardized LLM exceptions without leaking secrets."""
        status = resp.status_code
        err_msg = f"OpenAI HTTP error {status}"
        error_code = ""
        error_type = ""

        try:
            err_data = resp.json()
            if isinstance(err_data, dict) and "error" in err_data:
                err_obj = err_data["error"]
                if isinstance(err_obj, dict):
                    error_code = str(err_obj.get("code") or "")
                    error_type = str(err_obj.get("type") or "")
                    # Extract high-level safe message if available
                    safe_desc = err_obj.get("message")
                    if safe_desc and isinstance(safe_desc, str):
                        # Strip any sensitive tokens / prompts
                        err_msg = f"OpenAI error ({error_type or error_code or status}): {safe_desc[:120]}"
        except Exception:
            pass

        if status in (401, 403):
            return LLMAuthenticationError(
                message=f"OpenAI authentication failed (HTTP {status})",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status == 429:
            is_quota = (
                error_code in ("insufficient_quota", "billing_not_active")
                or error_type == "insufficient_quota"
                or "quota" in err_msg.lower()
            )
            if is_quota:
                return LLMQuotaExceededError(
                    message="OpenAI account quota or billing credits exhausted (HTTP 429)",
                    provider=self._provider_id,
                    task_id=task_id,
                )
            return LLMRateLimitError(
                message="OpenAI rate limit exceeded (HTTP 429)",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status >= 500:
            return LLMProviderUnavailableError(
                message=f"OpenAI service temporarily unavailable (HTTP {status})",
                provider=self._provider_id,
                task_id=task_id,
            )

        return LLMProviderUnavailableError(
            message=f"OpenAI unexpected HTTP error {status}",
            provider=self._provider_id,
            task_id=task_id,
        )

    def _parse_response_body(
        self,
        resp: httpx.Response,
        request: LLMRequest,
        model: str,
        latency_ms: float,
    ) -> LLMResponse:
        """Parse HTTP response into typed LLMResponse with safety checks."""
        # Check payload size ceiling
        if len(resp.content) > self.max_response_bytes:
            raise LLMInvalidSchemaError(
                message=f"Response body exceeded safety limit ({len(resp.content)} > {self.max_response_bytes} bytes).",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        try:
            data = resp.json()
        except Exception:
            raise LLMInvalidSchemaError(
                message="Provider response is not valid JSON.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        if not isinstance(data, dict):
            raise LLMInvalidSchemaError(
                message="Provider response root is not a JSON object.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise LLMInvalidSchemaError(
                message="Provider response contains no choices.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMInvalidSchemaError(
                message="Provider choice element is not an object.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            raise LLMInvalidSchemaError(
                message="Choice message is not an object.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        refusal = message.get("refusal")
        if refusal:
            raise LLMInvalidSchemaError(
                message=f"Model refused generation: {str(refusal)[:100]}",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        raw_content = message.get("content")
        if raw_content is None:
            raise LLMInvalidSchemaError(
                message="Choice message contains no content text.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        finish_reason = first_choice.get("finish_reason")

        # Parse usage (only if actually reported)
        usage = data.get("usage")
        input_tokens = None
        output_tokens = None
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            if isinstance(pt, int) and pt >= 0:
                input_tokens = pt
            if isinstance(ct, int) and ct >= 0:
                output_tokens = ct

        # Provider request ID tracking
        prov_req_id = resp.headers.get("x-request-id") or data.get("id")

        # Attempt JSON parsing if schema was requested
        parsed_json = None
        if request.response_schema is not None:
            try:
                # Strip potential markdown fences if returned
                cleaned = raw_content.strip()
                fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
                if fence_match:
                    cleaned = fence_match.group(1).strip()
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    parsed_json = parsed
            except Exception:
                # Keep parsed_json as None; S2.5 handles schema repair retries
                pass

        # Sanitized metadata
        raw_meta = {
            "status_code": resp.status_code,
            "system_fingerprint": data.get("system_fingerprint"),
        }
        if "x-request-id" in resp.headers:
            raw_meta["x-request-id"] = resp.headers["x-request-id"]

        return LLMResponse(
            content=raw_content,
            provider=self._provider_id,
            model=model,
            task_id=request.task_id,
            request_id=request.request_id,
            parsed_json=parsed_json,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,  # Strictly unmeasured in S2.2
            latency_ms=latency_ms,
            finish_reason=str(finish_reason) if finish_reason else None,
            provider_request_id=str(prov_req_id) if prov_req_id else None,
            raw_metadata=raw_meta,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Synchronous generation using httpx.Client."""
        api_key = self._get_api_key()
        model = self._resolve_model(request)
        payload = self._build_payload(request, model)

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=10.0,
            read=request.timeout_seconds,
            write=10.0,
        )

        client_kwargs = {
            "timeout": timeout,
            "follow_redirects": False,  # Security: never follow redirects
        }
        if self._transport:
            client_kwargs["transport"] = self._transport

        t0 = time.perf_counter()
        try:
            if self._injected_client:
                resp = self._injected_client.post(endpoint, json=payload, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {request.timeout_seconds}s.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message="Network connectivity error connecting to OpenAI API.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e

        latency_ms = (t1 - t0) * 1000.0

        if resp.status_code != 200:
            raise self._parse_http_error(resp, request.task_id)

        return self._parse_response_body(resp, request, model, latency_ms)

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        """Asynchronous generation using httpx.AsyncClient."""
        api_key = self._get_api_key()
        model = self._resolve_model(request)
        payload = self._build_payload(request, model)

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=10.0,
            read=request.timeout_seconds,
            write=10.0,
        )

        client_kwargs = {
            "timeout": timeout,
            "follow_redirects": False,  # Security: never follow redirects
        }
        if self._async_transport:
            client_kwargs["transport"] = self._async_transport

        t0 = time.perf_counter()
        try:
            if self._injected_async_client:
                resp = await self._injected_async_client.post(endpoint, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(endpoint, json=payload, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {request.timeout_seconds}s.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message="Network connectivity error connecting to OpenAI API.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e

        latency_ms = (t1 - t0) * 1000.0

        if resp.status_code != 200:
            raise self._parse_http_error(resp, request.task_id)

        return self._parse_response_body(resp, request, model, latency_ms)
