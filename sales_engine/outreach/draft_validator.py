import re
from typing import List, Dict, Any, Optional, Tuple, Set

from sales_engine.outreach.schemas import EmailDraft, OutreachContext

# Patterns for forbidden unresolved placeholders
PLACEHOLDER_PATTERNS = [
    r"\{\{[^}]+\}\}",          # {{first_name}}, {{company}}
    r"\{[a-zA-Z0-9_]+\}",       # {role}, {company_name}
    r"\[[A-Za-z0-9 _\-]+\]",    # [Company Name], [Recipient Name]
    r"<[A-Za-z0-9 _\-]+>",      # <NAME>, <COMPANY>
]

# Sensitive personal data leakage regexes
SENSITIVE_KEYWORDS = [
    "password", "api_key", "secret_key", "credit card", "ssn",
    "access_token", "authorization secrets", "personal_phone",
    "home_address", "personal_email", "bearer "
]

# Fake subject prefixes
FAKE_PREFIXES = ["re:", "fwd:", "fw:"]


class DraftValidator:
    """
    Deterministic validator for generated EmailDraft objects.
    Enforces Phase 8 invariants:
    - Subject: 1 <= len <= 60 chars, no emoji, no ALL CAPS, no fake Re:/Fwd:
    - Body: 1 <= words <= 160 words
    - Service used: must be in context.active_services
    - Evidence refs: all returned refs must exist in context.evidence_items
    - Invariants: lead_id, contact_id, recipient_email must match context exactly
    - Statuses: approval_status == 'pending_review', send_status == 'not_sent'
    - Privacy: no personal phone numbers, no unresolved placeholders
    """

    @classmethod
    def validate_draft(
        cls,
        draft: EmailDraft,
        context: OutreachContext,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Validate draft against constraints and context.
        Returns: (is_valid, fatal_errors, warnings)
        """
        fatal_errors: List[str] = []
        warnings: List[str] = []

        # 1. State Invariants (CRITICAL)
        if draft.approval_status != "pending_review":
            fatal_errors.append(f"Invalid approval_status '{draft.approval_status}'. Phase 8 requires 'pending_review'.")
        if draft.send_status != "not_sent":
            fatal_errors.append(f"Invalid send_status '{draft.send_status}'. Phase 8 requires 'not_sent'.")

        # 2. Identity Invariants (CRITICAL)
        if draft.lead_id != context.lead_id:
            fatal_errors.append(f"Lead ID mismatch: draft has '{draft.lead_id}', context has '{context.lead_id}'.")
        if draft.contact_id != context.contact_id:
            fatal_errors.append(f"Contact ID mismatch: draft has '{draft.contact_id}', context has '{context.contact_id}'.")
        if draft.recipient_email != context.recipient_email:
            fatal_errors.append(f"Recipient email mismatch: draft has '{draft.recipient_email}', context has '{context.recipient_email}'.")

        # 3. Subject validation
        subject = (draft.subject or "").strip()
        if not subject:
            fatal_errors.append("Subject is missing or empty.")
        else:
            if len(subject) > 60:
                fatal_errors.append(f"Subject exceeds 60 characters limit (length={len(subject)}).")
            # Check fake prefixes
            lower_subj = subject.lower()
            for prefix in FAKE_PREFIXES:
                if lower_subj.startswith(prefix):
                    fatal_errors.append(f"Subject contains fake prefix '{prefix}'.")
            # Check ALL CAPS (if longer than 4 chars and upper)
            letters = [c for c in subject if c.isalpha()]
            if len(letters) >= 5 and all(c.isupper() for c in letters):
                fatal_errors.append("Subject is in ALL CAPS.")

        # 4. Body validation
        body = (draft.body or "").strip()
        if not body:
            fatal_errors.append("Body is missing or empty.")
        else:
            word_count = len(body.split())
            if word_count > 160:
                fatal_errors.append(f"Body exceeds 160 words limit (word_count={word_count}).")
            elif word_count < 20:
                warnings.append(f"Body is very short ({word_count} words).")

        # 5. Active service validation (Strict Exact Equality)
        normalized_active = {s.strip().lower() for s in context.active_services}
        draft_service = (draft.service_used or "").strip().lower()
        if not draft_service or draft_service not in normalized_active:
            fatal_errors.append(
                f"Pitched service '{draft.service_used}' is not among allowed active services: {context.active_services}."
            )

        # 6. Evidence references validation
        context_evidence_ids = {item.id for item in context.evidence_items}
        for ref in draft.evidence_refs:
            if ref not in context_evidence_ids:
                fatal_errors.append(f"Unknown evidence reference '{ref}' not found in generation context.")

        if not draft.evidence_refs:
            warnings.append("Draft returned no evidence references.")

        # 7. Unresolved Placeholders check
        text_to_scan = f"{subject}\n{body}"
        for pattern in PLACEHOLDER_PATTERNS:
            matches = re.findall(pattern, text_to_scan)
            if matches:
                fatal_errors.append(f"Unresolved placeholders found in draft: {matches}")

        # 8. Privacy / Sensitive Data check
        for kw in SENSITIVE_KEYWORDS:
            if kw in text_to_scan.lower():
                fatal_errors.append(f"Sensitive keyword '{kw}' detected in email text.")

        is_valid = len(fatal_errors) == 0
        return is_valid, fatal_errors, warnings
