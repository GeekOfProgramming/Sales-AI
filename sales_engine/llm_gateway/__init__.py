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
    LLMInvalidRequestError,
    LLMAuthenticationError,
    LLMPermissionError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMQuotaExceededError,
    LLMBudgetExceededError,
    LLMInvalidSchemaError,
    LLMNotFoundError,
    LLMProviderUnavailableError,
    LLMPrivacyError,
    LLMUntrustedContentError,
    LLMAllowlistViolationError,
    LLMPrivacyRejectionError,
)
from sales_engine.llm_gateway.rate_limit_diagnostics import (
    RateLimitDiagnostic,
    classify_http_429,
)
from sales_engine.llm_gateway.privacy_filter import (
    PrivacyFilter,
    RedactionResult,
    is_luhn_valid,
)
from sales_engine.llm_gateway.source_boundaries import (
    InputTrustClass,
    normalize_company_domain,
    wrap_untrusted_source,
    contains_prompt_injection,
)
from sales_engine.llm_gateway.payload_builder import (
    PAYLOAD_POLICY_VERSION,
    TASK_EXCERPT_LIMITS,
    TASK_ALLOWLISTS,
    FORBIDDEN_FIELDS,
    PayloadBuildResult,
    PayloadBuilder,
    check_full_text_opt_out_signals,
    preview_sanitized_payload,
)
from sales_engine.llm_gateway.output_schemas import (
    SCHEMA_REGISTRY_VERSION,
    SchemaRegistry,
    WebsitePassAOutput,
    WebsitePassBOutput,
    JobAnalysisOutput,
    DiscoveryQueryGenerationOutput,
    InboxClassificationOutput,
    ReplySuggestionOutput,
    OutreachDraftOutput,
)
from sales_engine.llm_gateway.repair_policy import (
    REPAIR_POLICY_VERSION,
    RepairPolicy,
    RepairProposal,
)
from sales_engine.llm_gateway.output_validation import (
    OUTPUT_VALIDATOR_VERSION,
    OutputValidationResult,
    LLMOutputValidator,
)
from sales_engine.llm_gateway.budget_ledger import (
    BudgetPolicyConfig,
    ReservationStatus,
    BudgetReservation,
    BudgetSummary,
    BudgetLedger,
)
from sales_engine.llm_gateway.runtime_guards import (
    RuntimeGuard,
)
from sales_engine.llm_gateway.errors import (
    LLMConcurrencyLimitError,
    LLMUncertainUsageError,
    LLMDuplicateRequestError,
    LLMCandidateMismatchError,
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
    "LLMInvalidRequestError",
    "LLMAuthenticationError",
    "LLMPermissionError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMQuotaExceededError",
    "LLMBudgetExceededError",
    "LLMInvalidSchemaError",
    "LLMNotFoundError",
    "LLMProviderUnavailableError",
    "LLMPrivacyError",
    "LLMUntrustedContentError",
    "LLMAllowlistViolationError",
    "LLMPrivacyRejectionError",
    "LLMConcurrencyLimitError",
    "LLMUncertainUsageError",
    "LLMDuplicateRequestError",
    "LLMCandidateMismatchError",
    # Privacy & Boundaries (S2.4)
    "PrivacyFilter",
    "RedactionResult",
    "is_luhn_valid",
    "InputTrustClass",
    "normalize_company_domain",
    "wrap_untrusted_source",
    "contains_prompt_injection",
    "PAYLOAD_POLICY_VERSION",
    "TASK_EXCERPT_LIMITS",
    "TASK_ALLOWLISTS",
    "FORBIDDEN_FIELDS",
    "PayloadBuildResult",
    "PayloadBuilder",
    "check_full_text_opt_out_signals",
    "preview_sanitized_payload",
    # Output Validation & Repair (S2.5)
    "OutputValidationResult",
    "LLMOutputValidator",
    "OUTPUT_VALIDATOR_VERSION",
    "SchemaRegistry",
    "SCHEMA_REGISTRY_VERSION",
    "WebsitePassAOutput",
    "WebsitePassBOutput",
    "JobAnalysisOutput",
    "DiscoveryQueryGenerationOutput",
    "InboxClassificationOutput",
    "ReplySuggestionOutput",
    "OutreachDraftOutput",
    "RepairPolicy",
    "RepairProposal",
    "REPAIR_POLICY_VERSION",
    # Budget Ledger & Runtime Guards (S2.6)
    "BudgetPolicyConfig",
    "ReservationStatus",
    "BudgetReservation",
    "BudgetSummary",
    "BudgetLedger",
    "RuntimeGuard",
]
