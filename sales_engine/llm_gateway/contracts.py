"""
sales_engine/llm_gateway/contracts.py
Strongly typed data contracts for SalesAI LLM Gateway requests and responses.
Uses Pydantic v2 with strict validation and safe PII-scrubbed representations.
"""

import math
import re
import uuid
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator


# Sensitive patterns for safe logging redaction (S2.5.2)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SECRET_PATTERN = re.compile(
    r"(?:sk-[a-zA-Z0-9_-]{8,}|bearer\s+[a-zA-Z0-9_.-]{10,}|ghp_[a-zA-Z0-9]{10,}|(?:token|secret|password|api[_-]?key)[=:\s]+[a-zA-Z0-9_-]{8,})",
    re.IGNORECASE,
)
_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\[^\s\n\r\t]+|/(?:etc|usr|home|var|tmp|Users|AppData)/[^\s\n\r\t]+)")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_log_str(val: Optional[Any], max_len: int = 128) -> Optional[str]:
    """
    Sanitize strings destined for safe logging, __str__, or __repr__.
    Redacts email addresses, API tokens/secrets, filesystem paths, and control characters.
    Preserves normal valid identifiers (alphanumeric, dashes, dots, underscores, colons).
    """
    if val is None:
        return None
    s = str(val)
    if not s:
        return ""

    # Replace control characters with a space
    s = _CONTROL_CHAR_PATTERN.sub(" ", s).strip()

    # Redact sensitive data patterns
    s = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", s)
    s = _SECRET_PATTERN.sub("[REDACTED_SECRET]", s)
    s = _PATH_PATTERN.sub("[REDACTED_PATH]", s)

    if len(s) > max_len:
        s = s[:max_len]
    return s


class ModelTier(str, Enum):
    """
    Four-tier quality and cost classification.
    Used strictly as metadata in S2.1 (no automatic model routing or escalation).
    """

    TIER_1_BUDGET = "tier_1_budget"
    TIER_2_BALANCED = "tier_2_balanced"
    TIER_3_HIGH_FIDELITY = "tier_3_high_fidelity"
    TIER_4_FLAGSHIP = "tier_4_flagship"


class LLMRequest(BaseModel):
    """
    Canonical request contract for all LLM Gateway operations.
    Enforces strict size, numerical bounds, and opaque correlation tracking.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    task_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Canonical task identifier (e.g., 'Q01_website_pass_a', 'Q06_outreach_draft')",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="User or task prompt text, strictly bounded in length",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        max_length=50_000,
        description="Optional system prompt / instructions",
    )
    provider: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Explicit logical provider ID; None while disabled",
    )
    model: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Explicit model ID; None while disabled",
    )
    tier: Optional[ModelTier] = Field(
        default=None,
        description="Quality/cost classification tier (metadata only in S2.1)",
    )
    response_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="schema",
        description="Optional JSON schema dictionary for structured output enforcement",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Sampling temperature between 0.0 and 1.0 inclusive",
    )
    max_output_tokens: int = Field(
        default=2048,
        gt=0,
        le=32768,
        description="Maximum generation token ceiling",
    )
    request_id: str = Field(
        default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}",
        min_length=5,
        max_length=64,
        description="Opaque correlation ID (zero PII, emails, or personal data)",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Bounded request timeout in seconds",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Allowlisted operational identifiers only; zero secrets or personal records",
    )

    @field_validator("temperature")
    @classmethod
    def validate_temperature_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("temperature must be a finite real number (not NaN or Inf)")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("timeout_seconds must be a finite real number (not NaN or Inf)")
        return v

    @field_validator("metadata")
    @classmethod
    def validate_metadata_keys(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        # Disallow prohibited sensitive keys
        prohibited = {"password", "secret", "token", "api_key", "email", "authorization"}
        for k in v.keys():
            if any(p in k.lower() for p in prohibited):
                raise ValueError(f"Prohibited sensitive key in metadata: {k!r}")
        return v

    def __repr__(self) -> str:
        """Safe representation scrubbing raw prompt text to prevent PII leakage in logs."""
        prompt_len = len(self.prompt)
        sys_len = len(self.system_prompt) if self.system_prompt else 0
        return (
            f"LLMRequest(request_id={sanitize_log_str(self.request_id)!r}, "
            f"task_id={sanitize_log_str(self.task_id)!r}, "
            f"provider={sanitize_log_str(self.provider)!r}, "
            f"model={sanitize_log_str(self.model)!r}, tier={self.tier!r}, "
            f"prompt_length={prompt_len}, system_prompt_length={sys_len}, "
            f"temperature={self.temperature}, max_output_tokens={self.max_output_tokens}, "
            f"timeout_seconds={self.timeout_seconds})"
        )

    def __str__(self) -> str:
        """Ensure str(request) scrubs raw prompt text identically to repr(request)."""
        return self.__repr__()

    def safe_log_dict(self) -> Dict[str, Any]:
        """Return safe, non-sensitive dictionary suitable for application logging."""
        return {
            "request_id": sanitize_log_str(self.request_id),
            "task_id": sanitize_log_str(self.task_id),
            "provider": sanitize_log_str(self.provider),
            "model": sanitize_log_str(self.model),
            "tier": self.tier.value if self.tier else None,
            "prompt_length": len(self.prompt),
            "system_prompt_length": len(self.system_prompt) if self.system_prompt else 0,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
        }


class LLMResponse(BaseModel):
    """
    Canonical response contract for all LLM Gateway operations.
    Preserves raw content, parsed JSON, and uninvented metric tracking.
    """

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    content: str = Field(
        ...,
        description="Raw output text returned by the model",
    )
    provider: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Executing logical provider ID",
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Executing model name / identifier",
    )
    task_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Task identifier matching request",
    )
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Correlation ID matching request",
    )
    parsed_json: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parsed JSON dictionary only if actually parsed and validated",
    )
    input_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Measured prompt token count (None if unmeasured; never synthetic)",
    )
    output_tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Measured completion token count (None if unmeasured; never synthetic)",
    )
    cost_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Calculated cost in USD (None if unknown; never silently zero)",
    )
    latency_ms: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Measured wall-clock latency in milliseconds (None if unmeasured)",
    )
    finish_reason: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Upstream finish reason (e.g. 'stop', 'length', 'tool_calls')",
    )
    provider_request_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Opaque upstream provider transaction / trace identifier",
    )
    raw_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sanitized provider response headers/metadata (zero credentials)",
    )

    @field_validator("cost_usd", "latency_ms")
    @classmethod
    def validate_float_finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not math.isfinite(v):
            raise ValueError("Metric value must be a finite real number (not NaN or Inf)")
        return v

    def __repr__(self) -> str:
        """Safe representation scrubbing raw response text to prevent customer data leakage in logs."""
        content_len = len(self.content)
        return (
            f"LLMResponse(request_id={sanitize_log_str(self.request_id)!r}, "
            f"task_id={sanitize_log_str(self.task_id)!r}, "
            f"provider={sanitize_log_str(self.provider)!r}, "
            f"model={sanitize_log_str(self.model)!r}, content_length={content_len}, "
            f"input_tokens={self.input_tokens}, output_tokens={self.output_tokens}, "
            f"latency_ms={self.latency_ms}, finish_reason={sanitize_log_str(self.finish_reason)!r})"
        )

    def __str__(self) -> str:
        """Ensure str(response) scrubs raw content text identically to repr(response)."""
        return self.__repr__()

    def safe_log_dict(self) -> Dict[str, Any]:
        """Return safe, non-sensitive dictionary suitable for application logging."""
        has_prov_id = bool(self.provider_request_id and str(self.provider_request_id).strip())
        return {
            "request_id": sanitize_log_str(self.request_id),
            "task_id": sanitize_log_str(self.task_id),
            "provider": sanitize_log_str(self.provider),
            "model": sanitize_log_str(self.model),
            "content_length": len(self.content),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "finish_reason": sanitize_log_str(self.finish_reason),
            "has_provider_request_id": has_prov_id,
            "provider_request_id": None,
        }
