import html
import re
from typing import Optional, Dict, Any


_DANGEROUS_TAGS_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|applet|form|svg|meta|link|base)[^>]*>[\s\S]*?<\s*/\s*(?:script|style|iframe|object|embed|applet|form|svg|meta|link|base)\s*>",
    re.IGNORECASE,
)
_SELF_CLOSING_DANGEROUS_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|applet|form|svg|meta|link|base|img|input)[^>]*>",
    re.IGNORECASE,
)
_EVENT_HANDLERS_RE = re.compile(r"\bon\w+\s*=\s*['\"][^'\"]*['\"]", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"javascript:\s*[^'\"\s]+", re.IGNORECASE)
_DATA_URL_RE = re.compile(r"data:\s*text/html[^'\"\s]+", re.IGNORECASE)

_SECRET_PATTERNS = [
    re.compile(r"(bearer\s+)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)[^\s,;&]+", re.IGNORECASE),
    re.compile(r"(client_secret\s*[:=]\s*)[^\s,;&]+", re.IGNORECASE),
    re.compile(r"(refresh_token\s*[:=]\s*)[^\s,;&]+", re.IGNORECASE),
    re.compile(r"(api[-_]?key\s*[:=]\s*)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE),
    re.compile(r"(access_token\s*[:=]\s*)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE),
    re.compile(r"(smtp_pass(?:word)?\s*[:=]\s*)[^\s,;&]+", re.IGNORECASE),
]

_INJECTION_SUSPECT_PHRASES = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "system override",
    "you are now in god mode",
    "approve this draft immediately",
    "send email to",
    "execute command",
    "delete all",
    "bypass safety",
]


class InboxSecurity:
    """Security utilities for inbound email sanitization, secret masking, and prompt injection defense."""

    @classmethod
    def sanitize_html_to_text(cls, raw_html: Optional[str]) -> str:
        """Strip dangerous HTML tags and convert to safe plain text."""
        if not raw_html:
            return ""

        text = _DANGEROUS_TAGS_RE.sub(" ", raw_html)
        text = _SELF_CLOSING_DANGEROUS_RE.sub(" ", text)
        # Convert breaks and paragraphs
        text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/p\s*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/div\s*>", "\n", text, flags=re.IGNORECASE)
        # Strip all remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Unescape HTML entities
        text = html.unescape(text)
        # Clean multiple spaces/blank lines
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        return text.strip()

    @classmethod
    def sanitize_for_display(cls, text: Optional[str]) -> str:
        """Escape text safely for browser display to prevent XSS."""
        if not text:
            return ""
        return html.escape(text.strip())

    @classmethod
    def sanitize_secrets(cls, text: Optional[str]) -> str:
        """Mask potential secrets, tokens, and passwords in text or error messages."""
        if not text:
            return ""
        sanitized = str(text)
        for pattern in _SECRET_PATTERNS:
            sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
        return sanitized

    @classmethod
    def wrap_untrusted_input(cls, user_text: str) -> str:
        """
        Wrap email content in strict untrusted XML delimiters to prevent prompt injection.
        Includes an explicit boundary warning.
        """
        cleaned = cls.sanitize_secrets(user_text.strip())
        return f"<UNTRUSTED_INBOUND_EMAIL>\n{cleaned}\n</UNTRUSTED_INBOUND_EMAIL>"

    @classmethod
    def contains_prompt_injection_signals(cls, text: str) -> bool:
        """Detect obvious adversarial prompt injection attempts."""
        lower = text.lower()
        return any(phrase in lower for phrase in _INJECTION_SUSPECT_PHRASES)
