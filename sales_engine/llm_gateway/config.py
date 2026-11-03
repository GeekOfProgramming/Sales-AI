"""
sales_engine/llm_gateway/config.py
Fail-closed configuration for SalesAI LLM Gateway.
Defaults to disabled with zero automatic network activation.
"""

import os
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class GatewayConfig(BaseModel):
    """
    Configuration settings governing LLM Gateway behavior.
    Default state is strictly disabled (fail-closed) to protect against unintended API usage.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for LLM Gateway. Default is False (fail-closed).",
    )
    default_provider: Optional[str] = Field(
        default=None,
        description="Default provider ID if not explicitly specified in the request.",
    )
    default_model: Optional[str] = Field(
        default=None,
        description="Default model name if not explicitly specified in the request.",
    )
    default_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Default timeout in seconds.",
    )
    max_request_chars: int = Field(
        default=100_000,
        gt=0,
        le=500_000,
        description="Upper safety ceiling on prompt text length.",
    )
    strict_schema_validation: bool = Field(
        default=True,
        description="Enforce strict schema validation on model outputs.",
    )

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """
        Build GatewayConfig from environment variables.
        Defaults to enabled=False unless explicitly set to '1', 'true', or 'yes'.
        """
        raw_enabled = os.environ.get("SALESAI_LLM_GATEWAY_ENABLED", "").lower()
        is_enabled = raw_enabled in ("1", "true", "yes")

        default_provider = os.environ.get("SALESAI_LLM_DEFAULT_PROVIDER") or None
        default_model = os.environ.get("SALESAI_LLM_DEFAULT_MODEL") or None

        raw_timeout = os.environ.get("SALESAI_LLM_TIMEOUT")
        timeout = float(raw_timeout) if raw_timeout else 30.0

        raw_max_chars = os.environ.get("SALESAI_LLM_MAX_CHARS")
        max_chars = int(raw_max_chars) if raw_max_chars else 100_000

        raw_strict = os.environ.get("SALESAI_LLM_STRICT_SCHEMA", "1").lower()
        strict_schema = raw_strict in ("1", "true", "yes")

        return cls(
            enabled=is_enabled,
            default_provider=default_provider,
            default_model=default_model,
            default_timeout_seconds=timeout,
            max_request_chars=max_chars,
            strict_schema_validation=strict_schema,
        )
