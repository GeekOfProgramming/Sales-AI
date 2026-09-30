"""
sales_engine/sending/sanitizer.py
Centralized secret and credential sanitizer for Phase 9.
Redacts passwords, tokens, API keys, and authorization headers from error messages,
audit events, logs, and API responses.
"""

import os
import re
from typing import Optional, List, Any

# Pattern matching for common credential and secret leaks
SECRET_PATTERNS = [
    # Key=value or key: value for passwords
    re.compile(r"(?i)\b(smtp_password|password|passwd|pwd)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?", re.IGNORECASE),
    # Key=value or key: value for tokens and keys
    re.compile(r"(?i)\b(bearer|token|access_token|refresh_token|api_key|secret|client_secret)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?", re.IGNORECASE),
    # Authorization header format
    re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)([^\s,;]+)", re.IGNORECASE),
]


def sanitize_error_message(msg: Any, extra_secrets: Optional[List[str]] = None) -> str:
    """
    Sanitizes an error or exception string, redacting any secrets, passwords, or tokens.
    """
    if msg is None:
        return ""
    text = str(msg)

    # 1. Exact string substitution for known runtime secrets
    env_secrets = [
        os.environ.get("SMTP_PASSWORD"),
        os.environ.get("SMTP_USERNAME"),
        os.environ.get("OUTREACH_API_KEY"),
    ]
    all_secrets = (extra_secrets or []) + env_secrets
    for s in all_secrets:
        if s and len(str(s).strip()) >= 3:
            clean_s = str(s).strip()
            text = text.replace(clean_s, "********")

    # 2. Regex-based pattern redaction for structured secrets
    # Password patterns: replace matched secret value with ********
    text = re.sub(
        r"(?i)\b(smtp_password|password|passwd|pwd)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?",
        r"\1=********",
        text,
    )
    # Token / secret patterns
    text = re.sub(
        r"(?i)\b(access_token|refresh_token|api_key|client_secret|secret)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?",
        r"\1=********",
        text,
    )
    # Authorization: Bearer <token>
    text = re.sub(
        r"(?i)\b(authorization\s*:\s*bearer\s+)([^\s,;]+)",
        r"\1********",
        text,
    )
    text = re.sub(
        r"(?i)\b(bearer\s+)([a-zA-Z0-9_\-\.]{8,})",
        r"\1********",
        text,
    )

    return text[:500].strip()
