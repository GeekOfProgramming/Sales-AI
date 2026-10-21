"""
sales_engine/llm_gateway/errors.py
Standardized exception hierarchy for SalesAI LLM Gateway.
Ensures zero leakage of sensitive data, prompts, or credentials in strings/repr.
"""

from typing import Optional


class LLMError(Exception):
    """
    Base exception for all LLM Gateway errors.
    Contains stable error codes and retryability flags without leaking sensitive data.
    """

    def __init__(
        self,
        message: str,
        error_code: str = "LLM_ERROR",
        retryable: bool = False,
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.retryable = retryable
        self.provider = provider
        self.task_id = task_id

    def __str__(self) -> str:
        ctx = []
        if self.provider:
            ctx.append(f"provider={self.provider}")
        if self.task_id:
            ctx.append(f"task={self.task_id}")
        ctx_str = f" [{', '.join(ctx)}]" if ctx else ""
        return f"[{self.error_code}] {self.message}{ctx_str}"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(error_code={self.error_code!r}, "
            f"message={self.message!r}, retryable={self.retryable}, "
            f"provider={self.provider!r}, task_id={self.task_id!r})"
        )


class LLMNotConfiguredError(LLMError):
    """Raised when no provider/model is configured, active, or registered (fail-closed)."""

    def __init__(
        self,
        message: str = "LLM Gateway is not configured or no provider is active.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="NOT_CONFIGURED",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMAuthenticationError(LLMError):
    """Raised when provider authentication fails (e.g., invalid/missing API key)."""

    def __init__(
        self,
        message: str = "Authentication failed with LLM provider.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="AUTH_ERROR",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMTimeoutError(LLMError):
    """Raised when an LLM call exceeds the configured timeout."""

    def __init__(
        self,
        message: str = "LLM request timed out.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="TIMEOUT",
            retryable=True,
            provider=provider,
            task_id=task_id,
        )


class LLMRateLimitError(LLMError):
    """Raised when the upstream provider returns HTTP 429 Rate Limit."""

    def __init__(
        self,
        message: str = "LLM provider rate limit exceeded.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="RATE_LIMIT",
            retryable=True,
            provider=provider,
            task_id=task_id,
        )


class LLMQuotaExceededError(LLMError):
    """Raised when the provider account runs out of credits or billing quota."""

    def __init__(
        self,
        message: str = "LLM provider account quota/credits exhausted.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="QUOTA_EXCEEDED",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMBudgetExceededError(LLMError):
    """Raised when the local daily or monthly spending budget cap is exceeded."""

    def __init__(
        self,
        message: str = "Local SalesAI LLM spending budget ceiling reached.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="BUDGET_EXCEEDED",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMInvalidSchemaError(LLMError):
    """Raised when provider output fails strict schema parsing/validation."""

    def __init__(
        self,
        message: str = "LLM response failed schema validation.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="INVALID_SCHEMA",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMProviderUnavailableError(LLMError):
    """Raised when provider service is unreachable, returning 5xx or connection error."""

    def __init__(
        self,
        message: str = "LLM provider service is temporarily unavailable.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="PROVIDER_UNAVAILABLE",
            retryable=True,
            provider=provider,
            task_id=task_id,
        )


class LLMRoutingIntegrityError(LLMError, ValueError):
    """Raised when an approved route fails integrity validation or tampering is detected."""

    def __init__(
        self,
        message: str = "Routing integrity validation failed.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="ROUTING_INTEGRITY_VIOLATION",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMEvaluationIntegrityError(LLMError, ValueError):
    """Raised when an evaluation scorecard fails integrity verification (mismatch, replay, stale)."""

    def __init__(
        self,
        message: str = "Evaluation scorecard integrity validation failed.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="EVALUATION_INTEGRITY_VIOLATION",
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMPrivacyError(LLMError, ValueError):
    """Base exception for all LLM Gateway privacy, redaction, and payload policy violations."""

    def __init__(
        self,
        message: str = "Privacy or payload policy validation failed.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
        error_code: str = "PRIVACY_VIOLATION",
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            retryable=False,
            provider=provider,
            task_id=task_id,
        )


class LLMUntrustedContentError(LLMPrivacyError):
    """Raised when untrusted content contains fatal security violations (control chars, malformed envelope, severe injection)."""

    def __init__(
        self,
        message: str = "Untrusted content security validation failed.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="UNTRUSTED_CONTENT_VIOLATION",
            provider=provider,
            task_id=task_id,
        )


class LLMAllowlistViolationError(LLMPrivacyError):
    """Raised when non-allowlisted, restricted, or forbidden fields are passed to the payload builder."""

    def __init__(
        self,
        message: str = "Payload contains forbidden or non-allowlisted fields.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="ALLOWLIST_VIOLATION",
            provider=provider,
            task_id=task_id,
        )


class LLMPrivacyRejectionError(LLMPrivacyError):
    """Raised when payload fails privacy scrubbing or ambiguity requires conservative manual review."""

    def __init__(
        self,
        message: str = "Payload rejected by privacy filter; manual review required.",
        provider: Optional[str] = None,
        task_id: Optional[str] = None,
    ):
        super().__init__(
            message=message,
            error_code="PRIVACY_REJECTION",
            provider=provider,
            task_id=task_id,
        )
