"""
sales_engine/llm_gateway/source_boundaries.py
Robust encoding, wrapping, and trust boundary isolation for external untrusted text.
Ensures websites, job postings, inbound emails, and search snippets are isolated as inert
JSON-escaped data values, preventing delimiter breakouts or system instruction spoofing.
"""

import json
import re
from enum import Enum
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse

from sales_engine.llm_gateway.errors import LLMUntrustedContentError


class InputTrustClass(str, Enum):
    """
    Model trust hierarchy for SalesAI prompt assembly.
    Guarantees strict separation between instructions and untrusted data.
    """

    TRUSTED_POLICY = "trusted_policy"
    APPROVED_BUSINESS_FACTS = "approved_business_facts"
    UNTRUSTED_SOURCE = "untrusted_source"
    RESTRICTED_LOCAL_ONLY = "restricted_local_only"


# Common adversarial injection phrases to detect and log
_INJECTION_PHRASES = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "system override",
    "you are now in god mode",
    "switch provider",
    "disable budget",
    "send email to",
    "ignore suppression",
    "promote tier 4",
    "bypass safety",
    "act as system",
    "</untrusted_content>",
    "<system>",
    "[inst]",
    "### system:",
]


def contains_prompt_injection(text: str) -> bool:
    """Return True if text contains recognizable adversarial prompt injection phrases."""
    if not text:
        return False
    lower = text.lower()
    return any(p in lower for p in _INJECTION_PHRASES)


def normalize_company_domain(domain_or_url: str) -> str:
    """
    Normalize public company domain to clean lowercase hostname without scheme, port, path, or query.
    Fails closed on invalid hostnames, empty strings, data URLs, or suspicious inputs.
    """
    if not domain_or_url or not isinstance(domain_or_url, str):
        raise LLMUntrustedContentError("Company domain cannot be empty.")

    cleaned = domain_or_url.strip()

    # Reject data: or javascript: URIs
    if cleaned.lower().startswith(("data:", "javascript:", "file:")):
        raise LLMUntrustedContentError(f"Suspicious URI scheme in company domain: {cleaned[:30]!r}")

    # Add scheme if missing so urlparse handles it correctly
    if "://" not in cleaned:
        cleaned = "https://" + cleaned

    try:
        parsed = urlparse(cleaned)
    except Exception as e:
        raise LLMUntrustedContentError(f"Malformed company domain URL: {str(e)}")

    hostname = parsed.hostname
    if not hostname:
        raise LLMUntrustedContentError(f"Could not extract valid hostname from domain input: {domain_or_url!r}")

    hostname = hostname.lower().strip()

    # Strip leading www.
    if hostname.startswith("www."):
        hostname = hostname[4:]

    # Validate hostname characters (RFC 1123 format)
    if not re.match(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", hostname):
        raise LLMUntrustedContentError(f"Invalid company domain hostname format: {hostname!r}")

    return hostname


def wrap_untrusted_source(
    content: str,
    source_kind: str,
    source_id: str,
    max_chars: Optional[int] = None,
) -> Tuple[str, bool]:
    """
    Wrap untrusted source text inside a JSON-escaped typed envelope.
    Returns (wrapped_envelope_str, injection_detected_bool).

    Fails closed on forbidden control characters or malformed inputs.
    """
    if not isinstance(content, str):
        raise LLMUntrustedContentError(f"Untrusted content must be str, got {type(content).__name__}")

    if not source_id or not isinstance(source_id, str):
        raise LLMUntrustedContentError("source_id must be a non-empty string.")

    if not source_kind or not isinstance(source_kind, str):
        raise LLMUntrustedContentError("source_kind must be a non-empty string.")

    # 1. Check for fatal control characters (null bytes or invalid ascii)
    for c in content:
        code = ord(c)
        if code == 0 or (code < 32 and c not in ("\t", "\n", "\r")) or code == 127:
            raise LLMUntrustedContentError(f"Untrusted source '{source_id}' contains forbidden control characters.")

    # 2. Check for data URLs
    if re.search(r"data:\s*text/html", content, re.IGNORECASE):
        raise LLMUntrustedContentError(f"Untrusted source '{source_id}' contains forbidden data URL.")

    # 3. Detect injection attempts (for audit flagging)
    injection_detected = contains_prompt_injection(content)

    # 4. Truncate if max_chars is specified
    text = content
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]

    # 5. Serialize as JSON data value inside typed envelope
    # JSON encoding ensures all quotes, newlines, and potential delimiter tags are safely escaped
    envelope_payload = {
        "source_id": source_id,
        "source_kind": source_kind,
        "untrusted": True,
        "content": text,
    }
    json_str = json.dumps(envelope_payload, ensure_ascii=False)

    wrapped = (
        f'<UNTRUSTED_CONTENT source_kind="{source_kind}" source_id="{source_id}" untrusted="true">\n'
        f"{json_str}\n"
        f"</UNTRUSTED_CONTENT>"
    )

    return wrapped, injection_detected
