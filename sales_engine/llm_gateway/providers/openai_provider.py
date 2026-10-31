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
    LLMInvalidRequestError,
    LLMInvalidSchemaError,
    LLMNotConfiguredError,
    LLMNotFoundError,
    LLMPermissionError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sales_engine.llm_gateway.rate_limit_diagnostics import classify_http_429
from sales_engine.llm_gateway.interface import BaseLLMProvider


DEFAULT_ALLOWED_HOSTS: Set[str] = {
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "api.x.ai",
    "openrouter.ai",
    "api.cohere.com",
    "integrate.api.nvidia.com",
    "api.cloudflare.com",
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
    Supports OpenAI, Google Gemini, GroqCloud, OpenRouter, NVIDIA NIM, and Cloudflare Workers AI.
    """

    PROVIDER_ENV_VARS: Dict[str, str] = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "cohere": "COHERE_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "cloudflare": "CLOUDFLARE_API_TOKEN",
    }

    def __init__(
        self,
        provider_id: str = "openai",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        allowed_hosts: Optional[Set[str]] = None,
        env_var: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
        default_model: Optional[str] = None,
        model_capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        max_response_bytes: int = 1_000_000,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self._provider_id = provider_id
        self._api_key = api_key
        self.env_var = env_var or self.PROVIDER_ENV_VARS.get(provider_id.lower(), "OPENAI_API_KEY")
        self.allowed_hosts = allowed_hosts or DEFAULT_ALLOWED_HOSTS
        self.base_url = validate_endpoint_url(base_url, self.allowed_hosts)
        self.default_model = default_model
        self.model_capabilities = model_capabilities or {}
        self.max_response_bytes = max_response_bytes
        self.extra_headers = extra_headers or {}

        # Transports and clients (for dependency injection / testing)
        self._injected_client = client
        self._injected_async_client = async_client
        self._transport = transport
        self._async_transport = async_transport

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _get_api_key(self) -> str:
        """Retrieve API key from constructor or specific provider environment variable. Raises if missing."""
        target_env = self.env_var or self.PROVIDER_ENV_VARS.get(self._provider_id.lower(), "OPENAI_API_KEY")
        key = self._api_key or os.environ.get(target_env)
        if not key or not key.strip():
            raise LLMNotConfiguredError(
                message=f"Missing API key for provider '{self._provider_id}'. Set {target_env}.",
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

        # Token ceiling: respect explicit max_tokens_key, or default based on reasoning capabilities
        explicit_token_key = cap.get("max_tokens_key")
        if explicit_token_key:
            payload[explicit_token_key] = request.max_output_tokens
        else:
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

        # OpenRouter-specific routing controls (Hardened S3.5.3B)
        # Explicitly disable OpenRouter provider fallback with provider.allow_fallbacks=false
        # Ensures no unexpected paid-provider or cross-model routing can occur
        if self._provider_id == "openrouter" or cap.get("provider_fallbacks") is False:
            payload["provider"] = {"allow_fallbacks": False}

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

        if status == 400:
            return LLMInvalidRequestError(
                message=f"{self._provider_id.capitalize()} invalid request or invalid arguments (HTTP 400): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status == 401:
            return LLMAuthenticationError(
                message=f"{self._provider_id.capitalize()} authentication failed (HTTP 401): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status == 403:
            return LLMPermissionError(
                message=f"{self._provider_id.capitalize()} permission denied (HTTP 403): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status == 404:
            return LLMNotFoundError(
                message=f"{self._provider_id.capitalize()} resource or model not found (HTTP 404): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )

        if status == 429:
            diag = classify_http_429(
                status_code=status,
                error_code=error_code,
                error_type=error_type,
                error_message=err_msg,
                headers=dict(resp.headers) if hasattr(resp, "headers") else {},
                provider_id=self._provider_id,
            )
            if diag.cause_category == "ACCOUNT_QUOTA":
                return LLMQuotaExceededError(
                    message=f"{self._provider_id.capitalize()} account quota or billing credits exhausted (HTTP 429)",
                    provider=self._provider_id,
                    task_id=task_id,
                )
            return LLMRateLimitError(
                message=f"{self._provider_id.capitalize()} rate limit exceeded (HTTP 429)",
                provider=self._provider_id,
                task_id=task_id,
                cause_category=diag.cause_category,
                retry_after_seconds=diag.retry_after_seconds,
                definitive_cause_proven=diag.definitive_cause_proven,
                diagnostic=diag,
            )

        if status >= 500:
            return LLMProviderUnavailableError(
                message=f"{self._provider_id.capitalize()} service temporarily unavailable (HTTP {status})",
                provider=self._provider_id,
                task_id=task_id,
            )

        return LLMProviderUnavailableError(
            message=f"{self._provider_id.capitalize()} unexpected HTTP error {status}",
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
        if self.extra_headers:
            headers.update(self.extra_headers)
        if self._provider_id == "openrouter" and "HTTP-Referer" not in headers:
            headers["HTTP-Referer"] = "https://pybim.it/salesai"
            headers["X-Title"] = "SalesAI"

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
        if self.extra_headers:
            headers.update(self.extra_headers)
        if self._provider_id == "openrouter" and "HTTP-Referer" not in headers:
            headers["HTTP-Referer"] = "https://pybim.it/salesai"
            headers["X-Title"] = "SalesAI"

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

    def probe_models(self, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """
        Perform a read-only metadata probe (GET /models) to verify authentication and endpoint connectivity.
        Does NOT initiate any text generation or token consumption.
        Returns safe summary dictionary with model IDs and status.
        Raises LLMAuthenticationError, LLMProviderUnavailableError, LLMTimeoutError, etc. on failure.
        Never exposes API keys or sensitive error bodies.
        """
        api_key = self._get_api_key()
        endpoint = f"{self.base_url}/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
        }
        if self.extra_headers:
            headers.update(self.extra_headers)
        if self._provider_id == "openrouter" and "HTTP-Referer" not in headers:
            headers["HTTP-Referer"] = "https://pybim.it/salesai"
            headers["X-Title"] = "SalesAI"
        timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=10.0,
            read=timeout_seconds,
            write=10.0,
        )
        client_kwargs = {
            "timeout": timeout,
            "follow_redirects": False,
        }
        if self._transport:
            client_kwargs["transport"] = self._transport

        t0 = time.perf_counter()
        try:
            if self._injected_client:
                resp = self._injected_client.get(endpoint, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(endpoint, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to {self._provider_id} API at {self.base_url}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids = []
        try:
            data = resp.json()
            raw_list = []
            if isinstance(data, dict):
                raw_list = data.get("data") or data.get("models") or data.get("result") or []
            elif isinstance(data, list):
                raw_list = data
            for item in raw_list:
                if isinstance(item, dict):
                    m_id = item.get("id") or item.get("name")
                    if m_id:
                        model_ids.append(str(m_id))
                elif isinstance(item, str):
                    model_ids.append(item)
        except Exception:
            pass

        return {
            "provider_id": self._provider_id,
            "base_url": self.base_url,
            "status": "AUTHENTICATED",
            "status_code": resp.status_code,
            "latency_ms": round((t1 - t0) * 1000.0, 2),
            "model_count": len(model_ids),
            "model_ids": sorted(model_ids),
        }

    async def probe_models_async(self, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """Asynchronous metadata probe (GET /models)."""
        api_key = self._get_api_key()
        endpoint = f"{self.base_url}/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
        }
        if self.extra_headers:
            headers.update(self.extra_headers)
        if self._provider_id == "openrouter" and "HTTP-Referer" not in headers:
            headers["HTTP-Referer"] = "https://pybim.it/salesai"
            headers["X-Title"] = "SalesAI"
        timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=10.0,
            read=timeout_seconds,
            write=10.0,
        )
        client_kwargs = {
            "timeout": timeout,
            "follow_redirects": False,
        }
        if self._async_transport:
            client_kwargs["transport"] = self._async_transport

        t0 = time.perf_counter()
        try:
            if self._injected_async_client:
                resp = await self._injected_async_client.get(endpoint, headers=headers)
            else:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(endpoint, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to {self._provider_id} API at {self.base_url}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids = []
        try:
            data = resp.json()
            raw_list = []
            if isinstance(data, dict):
                raw_list = data.get("data") or data.get("models") or data.get("result") or []
            elif isinstance(data, list):
                raw_list = data
            for item in raw_list:
                if isinstance(item, dict):
                    m_id = item.get("id") or item.get("name")
                    if m_id:
                        model_ids.append(str(m_id))
                elif isinstance(item, str):
                    model_ids.append(item)
        except Exception:
            pass

        return {
            "provider_id": self._provider_id,
            "base_url": self.base_url,
            "status": "AUTHENTICATED",
            "status_code": resp.status_code,
            "latency_ms": round((t1 - t0) * 1000.0, 2),
            "model_count": len(model_ids),
            "model_ids": sorted(model_ids),
        }

