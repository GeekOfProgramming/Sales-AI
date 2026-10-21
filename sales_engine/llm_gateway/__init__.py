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
from sales_engine.llm_gateway.model_catalog import (
    ModelCandidate,
    ModelCatalog,
    PricingMetadata,
    CANONICAL_TASKS,
    normalize_task_id,
)
from sales_engine.llm_gateway.router import (
    TaskRouteDecision,
    TaskRouter,
)
from sales_engine.llm_gateway.evaluation_policy import (
    EvaluationDecision,
    CandidateScorecard,
    TaskEvaluationState,
    EvaluationPlanner,
)

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
    # Catalog & Taxonomy
    "ModelCandidate",
    "ModelCatalog",
    "PricingMetadata",
    "CANONICAL_TASKS",
    "normalize_task_id",
    # Router
    "TaskRouteDecision",
    "TaskRouter",
    # Evaluation State Machine
    "EvaluationDecision",
    "CandidateScorecard",
    "TaskEvaluationState",
    "EvaluationPlanner",
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

