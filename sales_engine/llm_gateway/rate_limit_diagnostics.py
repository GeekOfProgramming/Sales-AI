"""
sales_engine/llm_gateway/rate_limit_diagnostics.py
Deterministic HTTP 429 classification and sanitized diagnostic extraction for LLM Gateway.

Distinguishes between:
- ACCOUNT_QUOTA: Account-level quota, credit, or billing exhaustion.
- REQUEST_RATE_LIMIT: Per-minute or per-day RPM/TPM throttling.
- UPSTREAM_CAPACITY: Upstream shared cluster saturation or queue congestion.
- UNKNOWN_RATE_LIMIT: Unspecified or unconfirmed 429 rate limit.

Guarantees:
- Strict zero-secret leakage: Never includes API keys, tokens, or raw prompts.
- Does not assert upstream shared capacity as definitive unless explicitly evidenced.
- Extracts sanitized Retry-After headers in seconds.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class RateLimitDiagnostic:
    """Sanitized diagnostic details for an HTTP 429 response."""

    status_code: int
    provider_id: str
    cause_category: str  # "ACCOUNT_QUOTA", "REQUEST_RATE_LIMIT", "UPSTREAM_CAPACITY", "UNKNOWN_RATE_LIMIT"
    retry_after_seconds: Optional[float] = None
    provider_error_code: Optional[str] = None
    provider_error_type: Optional[str] = None
    safe_diagnostic_message: str = ""
    definitive_cause_proven: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_retry_after(headers: Optional[Dict[str, str]]) -> Optional[float]:
    """Extract and parse Retry-After header in seconds."""
    if not headers:
        return None
    # Case-insensitive lookup
    val = None
    for k, v in headers.items():
        if k.lower() == "retry-after":
            val = v
            break
        if k.lower() == "x-ratelimit-reset":
            val = v

    if not val:
        return None

    try:
        # Numeric seconds
        sec = float(str(val).strip())
        return max(0.0, sec)
    except (ValueError, TypeError):
        return None


def classify_http_429(
    status_code: int = 429,
    error_code: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    provider_id: str = "unknown",
) -> RateLimitDiagnostic:
    """
    Rigorously classify an HTTP 429 response based on error payload and response headers.
    Does NOT assert upstream capacity saturation unless definitive proof is present.
    """
    retry_after = parse_retry_after(headers)
    clean_code = str(error_code or "").strip().lower()
    clean_type = str(error_type or "").strip().lower()
    clean_msg = str(error_message or "").strip().lower()

    # 1. Account Quota Exhaustion
    quota_code_match = clean_code in (
        "insufficient_quota",
        "billing_not_active",
        "credit_exhausted",
        "quota_exceeded",
        "insufficient_credits",
    )
    quota_type_match = clean_type in ("insufficient_quota", "quota_error", "billing_error")
    quota_msg_match = any(
        term in clean_msg
        for term in (
            "quota",
            "credit",
            "billing",
            "balance",
            "insufficient quota",
            "quota exceeded",
            "billing credits exhausted",
            "check your plan and billing",
            "insufficient credits",
            "credit limit reached",
            "balance exhausted",
        )
    )

    if quota_code_match or quota_type_match or quota_msg_match:
        return RateLimitDiagnostic(
            status_code=status_code,
            provider_id=provider_id,
            cause_category="ACCOUNT_QUOTA",
            retry_after_seconds=retry_after,
            provider_error_code=error_code,
            provider_error_type=error_type,
            safe_diagnostic_message=f"{provider_id.capitalize()} account quota or billing credits exhausted (HTTP 429)",
            definitive_cause_proven=True,
        )

    # 2. Upstream Capacity Saturation (Requires explicit evidence)
    upstream_code_match = clean_code in (
        "upstream_error",
        "capacity_exceeded",
        "provider_overloaded",
        "upstream_capacity",
        "queue_saturated",
    )
    upstream_msg_match = any(
        term in clean_msg
        for term in (
            "upstream provider",
            "temporarily out of capacity",
            "shared-capacity cluster",
            "queue saturated",
            "upstream cluster",
            "no available providers for this model",
            "server overloaded",
        )
    )

    if upstream_code_match or upstream_msg_match:
        return RateLimitDiagnostic(
            status_code=status_code,
            provider_id=provider_id,
            cause_category="UPSTREAM_CAPACITY",
            retry_after_seconds=retry_after,
            provider_error_code=error_code,
            provider_error_type=error_type,
            safe_diagnostic_message=f"{provider_id.capitalize()} upstream capacity saturation or queue congestion (HTTP 429)",
            definitive_cause_proven=True,
        )

    # 3. Request Rate Limit (RPM / TPM / burst throttling)
    rpm_code_match = clean_code in (
        "rate_limit_exceeded",
        "requests_exceeded",
        "tokens_exceeded",
        "rate_limited",
    )
    rpm_msg_match = any(
        term in clean_msg
        for term in (
            "requests per minute",
            "tokens per minute",
            "requests per day",
            "rate limit reached",
            "too many requests",
            "per-minute limit",
            "rpm limit",
            "tpm limit",
        )
    )

    if rpm_code_match or rpm_msg_match:
        # If message specifies requests per minute/day or standard rate limit
        proven = bool(rpm_code_match or "per minute" in clean_msg or "rpm" in clean_msg)
        return RateLimitDiagnostic(
            status_code=status_code,
            provider_id=provider_id,
            cause_category="REQUEST_RATE_LIMIT",
            retry_after_seconds=retry_after,
            provider_error_code=error_code,
            provider_error_type=error_type,
            safe_diagnostic_message=f"{provider_id.capitalize()} request rate limit exceeded (HTTP 429)",
            definitive_cause_proven=proven,
        )

    # 4. Unknown / Unconfirmed Rate Limit
    # Fails closed against assuming upstream capacity without proof
    return RateLimitDiagnostic(
        status_code=status_code,
        provider_id=provider_id,
        cause_category="UNKNOWN_RATE_LIMIT",
        retry_after_seconds=retry_after,
        provider_error_code=error_code,
        provider_error_type=error_type,
        safe_diagnostic_message=f"{provider_id.capitalize()} rate limit exceeded with unconfirmed root cause (HTTP 429)",
        definitive_cause_proven=False,
    )
