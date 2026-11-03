"""
sales_engine/llm_gateway/providers/cohere_provider.py
Native Cohere v2 Chat API REST adapter for SalesAI LLM Gateway.
Supports Cohere Command models via the native v2 Chat interface without heavy SDKs.
Supports sync and async invocations, strict endpoint validation, and fail-closed security.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set

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
from sales_engine.llm_gateway.output_schemas import to_cohere_json_schema
from sales_engine.llm_gateway.providers.openai_provider import validate_endpoint_url


DEFAULT_COHERE_ALLOWED_HOSTS: Set[str] = {
    "api.cohere.com",
}


def classify_httpx_timeout(e: Exception) -> str:
    """Classify underlying HTTPX timeout exception into granular sanitized categories."""
    if isinstance(e, httpx.ConnectTimeout):
        return "CONNECT_TIMEOUT"
    elif isinstance(e, httpx.ReadTimeout):
        return "READ_TIMEOUT"
    elif isinstance(e, httpx.WriteTimeout):
        return "WRITE_TIMEOUT"
    elif isinstance(e, httpx.PoolTimeout):
        return "POOL_TIMEOUT"
    return "REQUEST_TIMEOUT"


class CohereProvider(BaseLLMProvider):
    """
    Adapter for Cohere native v2 Chat API (POST /v2/chat) and models API.
    Does not assume OpenAI compatibility.
    """

    def __init__(
        self,
        provider_id: str = "cohere",
        api_key: Optional[str] = None,
        base_url: str = "https://api.cohere.com/v2",
        allowed_hosts: Optional[Set[str]] = None,
        env_var: str = "COHERE_API_KEY",
        client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
        default_model: Optional[str] = "command-r-08-2024",
        model_capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        max_response_bytes: int = 1_000_000,
    ):
        self._provider_id = provider_id
        self._api_key = api_key
        self.env_var = env_var
        self.allowed_hosts = allowed_hosts or DEFAULT_COHERE_ALLOWED_HOSTS
        self.base_url = validate_endpoint_url(base_url, self.allowed_hosts)
        self.default_model = default_model
        self.model_capabilities = model_capabilities or {}
        self.max_response_bytes = max_response_bytes

        self._injected_client = client
        self._injected_async_client = async_client
        self._transport = transport
        self._async_transport = async_transport

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _get_api_key(self) -> str:
        key = self._api_key or os.environ.get(self.env_var)
        if not key or not key.strip():
            raise LLMNotConfiguredError(
                message=f"Missing API key for provider '{self._provider_id}'. Set {self.env_var}.",
                provider=self._provider_id,
            )
        return key.strip()

    def _resolve_model(self, request: LLMRequest) -> str:
        model = request.model or self.default_model
        if not model or not model.strip():
            raise LLMNotConfiguredError(
                message=f"No model specified for provider '{self._provider_id}'.",
                provider=self._provider_id,
                task_id=request.task_id,
            )
        return model.strip()

    def _build_payload(self, request: LLMRequest, model: str) -> Dict[str, Any]:
        """Build native Cohere v2 Chat API request payload."""
        messages: List[Dict[str, str]] = []
        if request.system_prompt and request.system_prompt.strip():
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        if request.max_output_tokens:
            payload["max_tokens"] = request.max_output_tokens

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        # Structured output / JSON mode in Cohere v2
        cap = self.model_capabilities.get(model, {})
        is_command = "command" in model.lower()
        supports_schema = cap.get("supports_json_schema", is_command and "command-r" in model.lower())
        supports_json_mode = cap.get("supports_json_mode", is_command) or cap.get("force_json_mode", False)

        if request.response_schema is not None:
            # Distinguish detailed schema request (with properties) from basic JSON object mode
            has_properties = isinstance(request.response_schema, dict) and "properties" in request.response_schema
            if has_properties:
                if not supports_schema:
                    raise LLMInvalidSchemaError(
                        message=f"Model '{model}' does not support JSON schema enforcement; refusing silent downgrade to JSON Object Mode.",
                        provider=self._provider_id,
                        task_id=request.task_id,
                    )
                # Convert and sanitize schema for Cohere v2
                try:
                    cohere_schema = to_cohere_json_schema(request.response_schema)
                except Exception as e:
                    raise LLMInvalidSchemaError(
                        message=f"Cohere JSON schema conversion failed before dispatch: {e}",
                        provider=self._provider_id,
                        task_id=request.task_id,
                    ) from e
                payload["response_format"] = {
                    "type": "json_object",
                    "schema": cohere_schema,
                }
            elif supports_json_mode or supports_schema:
                # Basic JSON object mode (e.g. response_schema={"type": "object"})
                payload["response_format"] = {"type": "json_object"}
            else:
                raise LLMInvalidSchemaError(
                    message=f"Model '{model}' does not support JSON mode or structured outputs.",
                    provider=self._provider_id,
                    task_id=request.task_id,
                )
        elif supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        return payload

    def _parse_http_error(self, resp: httpx.Response, task_id: str) -> Exception:
        """Map HTTP error status codes to standardized LLM exceptions."""
        status = resp.status_code
        err_msg = f"Cohere HTTP error {status}"
        try:
            err_data = resp.json()
            if isinstance(err_data, dict):
                safe_msg = err_data.get("message") or err_data.get("error")
                if safe_msg and isinstance(safe_msg, str):
                    err_msg = f"Cohere error ({status}): {safe_msg[:120]}"
        except Exception:
            pass

        if status == 400:
            return LLMInvalidRequestError(
                message=f"Cohere invalid request or invalid arguments (HTTP 400): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )
        if status == 401:
            return LLMAuthenticationError(
                message=f"Cohere authentication failed (HTTP 401): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )
        if status == 403:
            return LLMPermissionError(
                message=f"Cohere permission denied (HTTP 403): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )
        if status == 404:
            return LLMNotFoundError(
                message=f"Cohere resource or model not found (HTTP 404): {err_msg}",
                provider=self._provider_id,
                task_id=task_id,
            )
        if status == 429:
            diag = classify_http_429(
                status_code=status,
                error_code=None,
                error_type=None,
                error_message=err_msg,
                headers=dict(resp.headers) if hasattr(resp, "headers") else {},
                provider_id=self._provider_id,
            )
            if diag.cause_category == "ACCOUNT_QUOTA":
                return LLMQuotaExceededError(
                    message="Cohere quota or rate limit exhausted (HTTP 429)",
                    provider=self._provider_id,
                    task_id=task_id,
                )
            return LLMRateLimitError(
                message="Cohere rate limit exceeded (HTTP 429)",
                provider=self._provider_id,
                task_id=task_id,
                cause_category=diag.cause_category,
                retry_after_seconds=diag.retry_after_seconds,
                definitive_cause_proven=diag.definitive_cause_proven,
                diagnostic=diag,
            )
        if status >= 500:
            return LLMProviderUnavailableError(
                message=f"Cohere service temporarily unavailable (HTTP {status})",
                provider=self._provider_id,
                task_id=task_id,
            )
        return LLMProviderUnavailableError(
            message=f"Cohere unexpected HTTP error {status}",
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
        """Parse Cohere v2 Chat API response body."""
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

        # Cohere v2 returns message: { role: "assistant", content: [ { type: "text", text: "..." } ] }
        message = data.get("message")
        if not message or not isinstance(message, dict):
            # Fallback check for choices (in case user configured an OpenAI compat proxy)
            choices = data.get("choices")
            if choices and isinstance(choices, list) and len(choices) > 0:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message", {})

        if not message or not isinstance(message, dict):
            raise LLMInvalidSchemaError(
                message="Cohere response missing valid message object.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        raw_content = ""
        content_field = message.get("content")
        if isinstance(content_field, list):
            for block in content_field:
                if isinstance(block, dict) and block.get("type") == "text":
                    raw_content += block.get("text", "")
                elif isinstance(block, str):
                    raw_content += block
        elif isinstance(content_field, str):
            raw_content = content_field
        else:
            raise LLMInvalidSchemaError(
                message="Cohere response message contains no content text.",
                provider=self._provider_id,
                task_id=request.task_id,
            )

        finish_reason = data.get("finish_reason")

        # Parse usage: Cohere v2 provides usage.tokens.input_tokens / output_tokens
        usage = data.get("usage")
        input_tokens = None
        output_tokens = None
        billed_input_tokens = None
        billed_output_tokens = None
        if isinstance(usage, dict):
            tokens = usage.get("tokens")
            if isinstance(tokens, dict):
                pt = tokens.get("input_tokens")
                ct = tokens.get("output_tokens")
                if isinstance(pt, int) and pt >= 0:
                    input_tokens = pt
                if isinstance(ct, int) and ct >= 0:
                    output_tokens = ct

            billed_units = usage.get("billed_units")
            if isinstance(billed_units, dict):
                b_in = billed_units.get("input_tokens")
                b_out = billed_units.get("output_tokens")
                if isinstance(b_in, int) and b_in >= 0:
                    billed_input_tokens = b_in
                if isinstance(b_out, int) and b_out >= 0:
                    billed_output_tokens = b_out
                # Fallback to billed_units if tokens dictionary was omitted
                if input_tokens is None and billed_input_tokens is not None:
                    input_tokens = billed_input_tokens
                if output_tokens is None and billed_output_tokens is not None:
                    output_tokens = billed_output_tokens
            elif "prompt_tokens" in usage and "completion_tokens" in usage:
                pt = usage.get("prompt_tokens")
                ct = usage.get("completion_tokens")
                if isinstance(pt, int) and pt >= 0:
                    input_tokens = pt
                if isinstance(ct, int) and ct >= 0:
                    output_tokens = ct

        prov_req_id = resp.headers.get("x-request-id") or data.get("id")

        safe_headers: Dict[str, str] = {}
        if hasattr(resp, "headers"):
            for k, v in resp.headers.items():
                if any(x in k.lower() for x in ["trial", "limit", "remaining", "quota", "ratelimit", "monthly"]):
                    safe_headers[k.lower()] = str(v)

        parsed_json = None
        if request.response_schema is not None:
            try:
                cleaned = raw_content.strip()
                fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
                if fence_match:
                    cleaned = fence_match.group(1).strip()
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    parsed_json = parsed
            except Exception:
                pass

        raw_meta: Dict[str, Any] = {
            "status_code": resp.status_code,
            "headers": safe_headers,
        }
        if billed_input_tokens is not None or billed_output_tokens is not None:
            raw_meta["billed_units"] = {
                "input_tokens": billed_input_tokens,
                "output_tokens": billed_output_tokens,
            }

        return LLMResponse(
            content=raw_content,
            provider=self._provider_id,
            model=model,
            task_id=request.task_id,
            request_id=request.request_id,
            parsed_json=parsed_json,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
            latency_ms=latency_ms,
            finish_reason=str(finish_reason) if finish_reason else None,
            provider_request_id=str(prov_req_id) if prov_req_id else None,
            raw_metadata=raw_meta,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Synchronous Cohere v2 Chat generation."""
        api_key = self._get_api_key()
        model = self._resolve_model(request)
        payload = self._build_payload(request, model)

        # Base URL might be https://api.cohere.com/v2 or https://api.cohere.com
        endpoint = f"{self.base_url}/chat" if not self.base_url.endswith("/chat") else self.base_url

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=min(10.0, request.timeout_seconds),
            read=request.timeout_seconds,
            write=min(10.0, request.timeout_seconds),
            pool=min(10.0, request.timeout_seconds),
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
                resp = self._injected_client.post(endpoint, json=payload, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            cat = classify_httpx_timeout(e)
            raise LLMTimeoutError(
                message=f"Request timed out after {request.timeout_seconds}s ({cat}).",
                provider=self._provider_id,
                task_id=request.task_id,
                timeout_category=cat,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message="Network connectivity error connecting to Cohere API.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, request.task_id)

        latency_ms = (t1 - t0) * 1000.0
        return self._parse_response_body(resp, request, model, latency_ms)

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        """Asynchronous Cohere v2 Chat generation."""
        api_key = self._get_api_key()
        model = self._resolve_model(request)
        payload = self._build_payload(request, model)

        endpoint = f"{self.base_url}/chat" if not self.base_url.endswith("/chat") else self.base_url
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=request.timeout_seconds,
            connect=min(10.0, request.timeout_seconds),
            read=request.timeout_seconds,
            write=min(10.0, request.timeout_seconds),
            pool=min(10.0, request.timeout_seconds),
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
                resp = await self._injected_async_client.post(endpoint, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(endpoint, json=payload, headers=headers)
            t1 = time.perf_counter()
        except httpx.TimeoutException as e:
            cat = classify_httpx_timeout(e)
            raise LLMTimeoutError(
                message=f"Request timed out after {request.timeout_seconds}s ({cat}).",
                provider=self._provider_id,
                task_id=request.task_id,
                timeout_category=cat,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message="Network connectivity error connecting to Cohere API.",
                provider=self._provider_id,
                task_id=request.task_id,
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, request.task_id)

        latency_ms = (t1 - t0) * 1000.0
        return self._parse_response_body(resp, request, model, latency_ms)

    def probe_models(self, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        """Perform metadata probe for Cohere models (GET /v1/models?page_size=20)."""
        api_key = self._get_api_key()
        endpoint = "https://api.cohere.com/v1/models?page_size=20"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=min(10.0, timeout_seconds),
            read=timeout_seconds,
            write=min(10.0, timeout_seconds),
            pool=min(10.0, timeout_seconds),
        )
        client_kwargs = {"timeout": timeout, "follow_redirects": False}
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
            cat = classify_httpx_timeout(e)
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s ({cat}).",
                provider=self._provider_id,
                task_id="probe_models",
                timeout_category=cat,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to {self._provider_id} API at {endpoint}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids: List[str] = []
        try:
            data = resp.json()
            models_list = data.get("models") or data.get("data") or []
            if isinstance(models_list, list):
                for item in models_list:
                    if isinstance(item, dict):
                        m_name = item.get("name") or item.get("id")
                        if m_name:
                            model_ids.append(str(m_name))
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
        """Asynchronous metadata probe for Cohere models."""
        api_key = self._get_api_key()
        endpoint = "https://api.cohere.com/v1/models?page_size=20"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=min(10.0, timeout_seconds),
            read=timeout_seconds,
            write=min(10.0, timeout_seconds),
            pool=min(10.0, timeout_seconds),
        )
        client_kwargs = {"timeout": timeout, "follow_redirects": False}
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
            cat = classify_httpx_timeout(e)
            raise LLMTimeoutError(
                message=f"Model probe timed out after {timeout_seconds}s ({cat}).",
                provider=self._provider_id,
                task_id="probe_models",
                timeout_category=cat,
            ) from e
        except httpx.NetworkError as e:
            raise LLMProviderUnavailableError(
                message=f"Network error connecting to {self._provider_id} API at {endpoint}.",
                provider=self._provider_id,
                task_id="probe_models",
            ) from e

        if resp.status_code != 200:
            raise self._parse_http_error(resp, "probe_models")

        model_ids: List[str] = []
        try:
            data = resp.json()
            models_list = data.get("models") or data.get("data") or []
            if isinstance(models_list, list):
                for item in models_list:
                    if isinstance(item, dict):
                        m_name = item.get("name") or item.get("id")
                        if m_name:
                            model_ids.append(str(m_name))
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
