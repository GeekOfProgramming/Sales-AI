"""
sales_engine/llm_gateway
Lightweight, provider-independent LLM Gateway foundation for SalesAI.
Delegates all LLM operations to external HTTPS APIs with fail-closed governance.
"""

from sales_engine.llm_gateway.contracts import (
    ModelTier,
    LLMRequest,
    LLMResponse,
)
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
from sales_engine.llm_gateway.interface import (
    BaseLLMProvider,
    LLMProviderGateway,
)
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider

__all__ = [
    # Contracts
    "ModelTier",
    "LLMRequest",
    "LLMResponse",
    # Interfaces & Providers
    "BaseLLMProvider",
    "LLMProviderGateway",
    "OpenAIProvider",
    # Registry & Config
    "ProviderRegistry",
    "GatewayConfig",
    # Exceptions
    "LLMError",
    "LLMNotConfiguredError",
    "LLMAuthenticationError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMQuotaExceededError",
    "LLMBudgetExceededError",
    "LLMInvalidSchemaError",
    "LLMProviderUnavailableError",
]
