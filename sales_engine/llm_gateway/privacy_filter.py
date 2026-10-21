"""
sales_engine/llm_gateway/privacy_filter.py
Deterministic, regex- and rule-based privacy filter and secret redaction for SalesAI.
Strictly local, fail-closed, zero ML/network dependencies.
Scans and redacts emails, phone numbers, IBANs, payment cards (Luhn checked),
bearer/API tokens, secrets, local filesystem paths, and session cookies.
Preserves innocent BIM standards (ISO 19650, UNI 11337, IFC, LOD, etc.).
"""

import re
from typing import Dict, Any, Optional, Tuple, List
from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Innocent BIM & Architectural Standards Protection
# ---------------------------------------------------------------------------
_PROTECTED_BIM_PATTERNS = [
    r"\bISO\s*19650(?:-[1-5])?(?::\d{4})?\b",
    r"\bUNI\s*11337(?:-[1-7])?(?::\d{4})?\b",
    r"\bDIN\s*276\b",
    r"\bBS\s*1192(?:-[1-4])?\b",
    r"\bEN\s*ISO\s*16739(?:-1)?\b",
    r"\bIFC\s*(?:2x3|4(?:\.\d+)?)\b",
    r"\bLOD\s*(?:100|200|300|350|400|500)\b",
    r"\bCOBie\s*(?:2(?:\.\d+)?)?\b",
    r"\bRevit\s*20\d{2}\b",
    r"\bArchicad\s*\d{2}\b",
    r"\bBIM\s*(?:Level\s*[0-3]|Manager|Coordinator|Specialist|Author)\b",
]
_PROTECTED_BIM_RE = re.compile(
    "|".join(f"(?:{p})" for p in _PROTECTED_BIM_PATTERNS),
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Sensitive Detection Patterns
# ---------------------------------------------------------------------------

# Explicit restricted blocks
_RESTRICTED_TAGS_RE = re.compile(
    r"<\s*(?:private|restricted|confidential)[^>]*>[\s\S]*?<\s*/\s*(?:private|restricted|confidential)\s*>",
    re.IGNORECASE,
)
_RESTRICTED_INLINE_RE = re.compile(
    r"\[(?:RESTRICTED|CONFIDENTIAL|PRIVATE):[^\]]*\]|CONFIDENTIAL:\s*[^\n]+",
    re.IGNORECASE,
)

# Email address
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# IBAN (Italian IT + 2 check + 1 letter + 22 alphanum; International: 2 letters + 2 check + 11-30 alphanum)
_ITALIAN_IBAN_RE = re.compile(
    r"\bIT\d{2}[A-Za-z]\d{10}[0-9A-Za-z]{12}\b",
    re.IGNORECASE,
)
_INTERNATIONAL_IBAN_RE = re.compile(
    r"\b(?!ISO|UNI|DIN|IFC|LOD|REV|VER)[A-Z]{2}\d{2}[A-Za-z0-9]{11,30}\b"
)

# Secrets, bearer tokens, API keys
_BEARER_TOKEN_RE = re.compile(
    r"\b(bearer\s+)([A-Za-z0-9_\-\.]{15,})\b",
    re.IGNORECASE,
)
_OPENAI_KEY_RE = re.compile(
    r"\b(sk-(?:live|proj|test)?-[A-Za-z0-9_\-]{20,})\b"
)
_SECRET_ASSIGN_RE = re.compile(
    r"\b(api[-_]?key|client_secret|refresh_token|access_token|smtp_pass(?:word)?|"
    r"password|passwd|pwd|private[-_]?key)\s*([:=])\s*['\"]?(?!\[[A-Z_]+_\d+\])([^\s,;&'\"]{4,})['\"]?",
    re.IGNORECASE,
)
_ENV_SECRET_RE = re.compile(
    r"\b([A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PASSWORD|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*['\"]?(?!\[[A-Z_]+_\d+\])([^\s'\"]{4,})['\"]?"
)
_COOKIE_RE = re.compile(
    r"\b(sessionid|connect\.sid|phpsessid|csrf_token|xsrf_token|cookie)\s*([:=])\s*['\"]?(?!\[[A-Z_]+_\d+\])([^\s,;&'\"]{6,})['\"]?",
    re.IGNORECASE,
)


# Local filesystem paths (Windows and Unix absolute paths that expose local user identity)
_LOCAL_PATH_WIN_RE = re.compile(
    r"\b[A-Za-z]:\\(?:Users|Sales-AI|projects|Desktop|Documents|AppData|Program Files|Windows)\\[^\s\"'>]+",
    re.IGNORECASE,
)
_LOCAL_PATH_UNIX_RE = re.compile(
    r"(?:^|[\s\"'])(/(?:home|Users|etc|var|tmp)/[^\s\"'>]+)"
)

# Payment card numbers: 13-19 digits, possibly separated by spaces or hyphens
_CARD_CANDIDATE_RE = re.compile(
    r"\b(?:\d[ -]*?){13,19}\b"
)

# Phone numbers (Italian and international)
# Guarded against BIM standards and dates
_INTL_PHONE_RE = re.compile(
    r"\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){1,4}(?:[\s.-]?\d{2,4}){1,4}"
)
_ITALIAN_LOCAL_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:0\d{1,4}[/\s.-]\d{5,8}|3\d{2}[\s.-]\d{6,7})(?![A-Za-z0-9_])"
)


def is_luhn_valid(number_str: str) -> bool:
    """Validate payment card candidate using standard Luhn algorithm."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


class RedactionResult(BaseModel):
    """
    Contract for privacy filtering results.
    Never exposes raw secrets or unredacted text in logs or safe representations.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    sanitized_text: str = Field(..., description="Redacted text safe for external transmission")
    redaction_counts: Dict[str, int] = Field(
        default_factory=lambda: {
            "emails": 0,
            "phones": 0,
            "ibans": 0,
            "cards": 0,
            "secrets": 0,
            "paths": 0,
            "restricted": 0,
        }
    )
    manual_review_required: bool = Field(default=False)
    rejection_reason: Optional[str] = Field(default=None)
    placeholder_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Local-only mapping from placeholder to original value. Never transmitted or logged.",
    )

    def safe_log_dict(self) -> Dict[str, Any]:
        """Return safe, non-sensitive telemetry dict without text or secrets."""
        return {
            "redaction_counts": dict(self.redaction_counts),
            "total_redactions": sum(self.redaction_counts.values()),
            "manual_review_required": self.manual_review_required,
            "rejection_reason": self.rejection_reason,
            "sanitized_length": len(self.sanitized_text),
        }

    def __repr__(self) -> str:
        return (
            f"RedactionResult(total_redactions={sum(self.redaction_counts.values())}, "
            f"manual_review_required={self.manual_review_required}, "
            f"sanitized_length={len(self.sanitized_text)})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class PrivacyFilter:
    """
    Deterministic privacy filter and secret redaction engine for SalesAI outbound payloads.
    Maintains per-request placeholder numbering (e.g. [PERSONAL_EMAIL_1], [SECRET_1]).
    Fails closed when suspicious unmaskable data or forbidden control characters are encountered.
    """

    def __init__(self, fail_closed_on_uncertainty: bool = True):
        self.fail_closed_on_uncertainty = fail_closed_on_uncertainty
        self._counts: Dict[str, int] = {
            "emails": 0,
            "phones": 0,
            "ibans": 0,
            "cards": 0,
            "secrets": 0,
            "paths": 0,
            "restricted": 0,
        }
        self._counters: Dict[str, int] = {
            "PERSONAL_EMAIL": 0,
            "PHONE": 0,
            "IBAN": 0,
            "CARD": 0,
            "SECRET": 0,
            "LOCAL_PATH": 0,
        }
        # Local-only map: placeholder -> original value
        self._placeholder_map: Dict[str, str] = {}
        # Reverse map: original value -> placeholder (ensures same secret gets same placeholder)
        self._val_to_placeholder: Dict[str, str] = {}

    def _get_placeholder(self, category: str, original_val: str) -> str:
        key = original_val.strip()
        if key in self._val_to_placeholder:
            return self._val_to_placeholder[key]

        self._counters[category] = self._counters.get(category, 0) + 1
        placeholder = f"[{category}_{self._counters[category]}]"
        self._val_to_placeholder[key] = placeholder
        self._placeholder_map[placeholder] = original_val
        return placeholder

    def filter_text(self, text: Optional[str]) -> RedactionResult:
        """
        Deterministically sanitize text by detecting and replacing sensitive entities.
        Preserves innocent BIM standards and non-sensitive business facts.
        """
        if not text:
            return RedactionResult(
                sanitized_text="",
                redaction_counts=dict(self._counts),
                placeholder_map=dict(self._placeholder_map),
            )

        # 1. Check for fatal control characters
        for char in text:
            code = ord(char)
            # Disallow null bytes and dangerous control characters (permit \t, \n, \r)
            if code == 0 or (code < 32 and char not in ("\t", "\n", "\r")) or code == 127:
                return RedactionResult(
                    sanitized_text="",
                    redaction_counts=dict(self._counts),
                    manual_review_required=True,
                    rejection_reason="Forbidden control character or null byte detected in text.",
                    placeholder_map=dict(self._placeholder_map),
                )

        sanitized = text
        manual_review = False
        rejection_reasons: List[str] = []

        # 2. Redact explicit restricted / private tags
        def _replace_restricted_tag(match: re.Match) -> str:
            self._counts["restricted"] += 1
            return "[RESTRICTED_REDACTED]"

        sanitized = _RESTRICTED_TAGS_RE.sub(_replace_restricted_tag, sanitized)
        sanitized = _RESTRICTED_INLINE_RE.sub(_replace_restricted_tag, sanitized)

        # 3. Redact local filesystem paths
        def _replace_win_path(match: re.Match) -> str:
            val = match.group(0)
            self._counts["paths"] += 1
            return self._get_placeholder("LOCAL_PATH", val)

        sanitized = _LOCAL_PATH_WIN_RE.sub(_replace_win_path, sanitized)

        def _replace_unix_path(match: re.Match) -> str:
            full = match.group(0)
            path_val = match.group(1)
            self._counts["paths"] += 1
            placeholder = self._get_placeholder("LOCAL_PATH", path_val)
            return full.replace(path_val, placeholder)

        sanitized = _LOCAL_PATH_UNIX_RE.sub(_replace_unix_path, sanitized)

        # 4. Redact secrets, tokens, cookies, assignments
        # 4a. OpenAI-style sk- keys
        def _replace_sk_key(match: re.Match) -> str:
            val = match.group(1)
            self._counts["secrets"] += 1
            return self._get_placeholder("SECRET", val)

        sanitized = _OPENAI_KEY_RE.sub(_replace_sk_key, sanitized)

        # 4b. Bearer tokens
        def _replace_bearer(match: re.Match) -> str:
            prefix = match.group(1)
            val = match.group(2)
            self._counts["secrets"] += 1
            placeholder = self._get_placeholder("SECRET", val)
            return f"{prefix}{placeholder}"

        sanitized = _BEARER_TOKEN_RE.sub(_replace_bearer, sanitized)

        # 4c. Secret assignments (api_key=..., password=...)
        def _replace_secret_assign(match: re.Match) -> str:
            k = match.group(1)
            sep = match.group(2)
            v = match.group(3)
            self._counts["secrets"] += 1
            placeholder = self._get_placeholder("SECRET", v)
            return f"{k}{sep}{placeholder}"

        sanitized = _SECRET_ASSIGN_RE.sub(_replace_secret_assign, sanitized)

        # 4d. Env secret assignments
        def _replace_env_secret(match: re.Match) -> str:
            k = match.group(1)
            v = match.group(2)
            self._counts["secrets"] += 1
            placeholder = self._get_placeholder("SECRET", v)
            return f"{k}={placeholder}"

        sanitized = _ENV_SECRET_RE.sub(_replace_env_secret, sanitized)

        # 4e. Session cookies
        def _replace_cookie(match: re.Match) -> str:
            k = match.group(1)
            sep = match.group(2)
            v = match.group(3)
            self._counts["secrets"] += 1
            placeholder = self._get_placeholder("SECRET", v)
            return f"{k}{sep}{placeholder}"

        sanitized = _COOKIE_RE.sub(_replace_cookie, sanitized)

        # 5. Redact Payment Cards (Luhn verified)
        def _replace_card(match: re.Match) -> str:
            raw_str = match.group(0)
            clean_digits = re.sub(r"[^\d]", "", raw_str)
            if len(clean_digits) >= 13 and is_luhn_valid(clean_digits):
                self._counts["cards"] += 1
                return self._get_placeholder("CARD", raw_str)
            return raw_str

        sanitized = _CARD_CANDIDATE_RE.sub(_replace_card, sanitized)

        # 6. Redact IBAN
        def _replace_italian_iban(match: re.Match) -> str:
            val = match.group(0)
            self._counts["ibans"] += 1
            return self._get_placeholder("IBAN", val)

        sanitized = _ITALIAN_IBAN_RE.sub(_replace_italian_iban, sanitized)

        def _replace_intl_iban(match: re.Match) -> str:
            val = match.group(0)
            # Ensure it's not a protected BIM standard
            if _PROTECTED_BIM_RE.search(val):
                return val
            self._counts["ibans"] += 1
            return self._get_placeholder("IBAN", val)

        sanitized = _INTERNATIONAL_IBAN_RE.sub(_replace_intl_iban, sanitized)

        # 7. Redact Emails
        def _replace_email(match: re.Match) -> str:
            val = match.group(0)
            self._counts["emails"] += 1
            return self._get_placeholder("PERSONAL_EMAIL", val)

        sanitized = _EMAIL_RE.sub(_replace_email, sanitized)

        # 8. Redact Phone Numbers (preserving BIM standards and dates)
        # 8a. International phones with '+'
        def _replace_intl_phone(match: re.Match) -> str:
            val = match.group(0)
            clean = re.sub(r"[^\d]", "", val)
            if 7 <= len(clean) <= 15:
                self._counts["phones"] += 1
                return self._get_placeholder("PHONE", val)
            return val

        sanitized = _INTL_PHONE_RE.sub(_replace_intl_phone, sanitized)

        # 8b. Italian local phones
        def _replace_it_phone(match: re.Match) -> str:
            val = match.group(0)
            start_pos = match.start()
            # Check context before match for BIM standards (ISO, UNI, DIN, etc.) or date
            pre_context = sanitized[max(0, start_pos - 15) : start_pos]
            if re.search(r"\b(?:ISO|UNI|DIN|LOD|BS|EN|IFC|REV|VER|REVIT)\b", pre_context, re.IGNORECASE):
                return val
            if re.search(r"\d{4}-", pre_context):  # Part of a date YYYY-MM-DD
                return val
            clean = re.sub(r"[^\d]", "", val)
            if 8 <= len(clean) <= 12:
                self._counts["phones"] += 1
                return self._get_placeholder("PHONE", val)
            return val

        sanitized = _ITALIAN_LOCAL_PHONE_RE.sub(_replace_it_phone, sanitized)

        # 9. Uncertainty & Fail-Closed Validation
        # If text still contains lingering unredacted sensitive keywords with high-entropy values
        if self.fail_closed_on_uncertainty:
            # Check for suspicious residual secret markers
            suspicious_residuals = re.search(
                r"\b(?:api_key|password|client_secret)\s*[:=]\s*(?!\[SECRET_\d+\])[^\s,;&'\"]+",
                sanitized,
                re.IGNORECASE,
            )
            if suspicious_residuals:
                manual_review = True
                rejection_reasons.append(f"Residual unredacted secret assignment detected: {suspicious_residuals.group(0)[:15]}...")

        return RedactionResult(
            sanitized_text=sanitized,
            redaction_counts=dict(self._counts),
            manual_review_required=manual_review,
            rejection_reason="; ".join(rejection_reasons) if rejection_reasons else None,
            placeholder_map=dict(self._placeholder_map),
        )
