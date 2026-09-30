import re
from typing import Tuple, Optional, List

from sales_engine.sending.schemas import StoredDraft
from sales_engine.sending.approval_service import compute_content_fingerprint
from sales_engine.sending.suppression_store import SuppressionStore

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SendValidator:
    """
    Pre-send gate enforcing approval status, content fingerprints,
    suppression, sender identity, and safety invariants.
    Zero LLM involvement.
    """

    @classmethod
    def validate_pre_send(
        cls,
        draft: Optional[StoredDraft],
        suppression_store: SuppressionStore,
        trusted_sender_email: Optional[str] = None,
        email_send_enabled: bool = False,
        dry_run: bool = False,
        latest_revision: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validates draft readiness before provider network call.
        Returns: (is_valid, error_type, error_message).
        """
        # 1. Draft existence
        if draft is None:
            return False, "missing_recipient", "Draft does not exist"

        # 1.1 Obsolete revision check
        if latest_revision is not None and draft.revision < latest_revision:
            return (
                False,
                "stale_revision",
                f"Draft revision {draft.revision} is obsolete; current latest is {latest_revision}",
            )

        # 2. Approval Status
        if draft.approval_status != "approved":
            return (
                False,
                "not_approved",
                f"Draft approval_status is '{draft.approval_status}', but must be 'approved'",
            )

        # 3. Already sent check
        if draft.send_status == "sent":
            return (
                False,
                "already_sent",
                f"Draft {draft.draft_id} revision {draft.revision} has already been sent",
            )

        # 4. Recipient validity
        recip_email = (draft.recipient_email or "").strip().lower()
        if not recip_email:
            return False, "missing_recipient", "Draft recipient_email is missing or empty"

        if not EMAIL_REGEX.match(recip_email):
            return False, "invalid_recipient", f"Invalid recipient email format: '{recip_email}'"

        # 5. Sender Identity Validation
        draft_sender = (draft.sender_email or "").strip().lower()
        if not draft_sender:
            return (
                False,
                "sender_config_missing",
                "Draft sender_email is missing or empty; send payload sender must be explicit",
            )
        if not EMAIL_REGEX.match(draft_sender):
            return (
                False,
                "sender_config_missing",
                f"Invalid sender email format: '{draft_sender}'",
            )
        if not trusted_sender_email or not trusted_sender_email.strip():
            return (
                False,
                "sender_config_missing",
                "Trusted sender email is not configured (SMTP_FROM_EMAIL is unset)",
            )
        trusted_lower = trusted_sender_email.strip().lower()
        if draft_sender != trusted_lower:
            return (
                False,
                "sender_mismatch",
                f"Draft sender '{draft_sender}' does not match trusted sender '{trusted_lower}'",
            )

        # 6. Suppression check (Do-Not-Contact)
        if suppression_store.is_suppressed(recip_email):
            return (
                False,
                "suppressed_recipient",
                f"Recipient {recip_email} is on the do-not-contact suppression list",
            )

        # 7. Approval Fingerprint (Stale Approval Prevention)
        if not draft.approved_content_hash:
            return (
                False,
                "approval_stale",
                "Draft has no approved_content_hash recorded at approval time",
            )

        current_hash = compute_content_fingerprint(draft)
        if current_hash != draft.approved_content_hash:
            return (
                False,
                "approval_stale",
                "Draft content has been modified since approval (fingerprint mismatch)",
            )

        # 8. Subject / Body integrity & Placeholder checks
        if not draft.subject or not draft.subject.strip():
            return False, "validation_failed", "Draft subject is empty"

        if not draft.body or not draft.body.strip():
            return False, "validation_failed", "Draft body is empty"

        for bad in ["{{", "}}", "<NAME>", "[COMPANY]", "<COMPANY>"]:
            if bad in draft.subject or bad in draft.body:
                return False, "validation_failed", f"Draft contains unresolved template placeholder: '{bad}'"

        # 9. Phase 8 hard constraints reuse
        if len(draft.subject) > 60:
            return False, "validation_failed", f"Subject length ({len(draft.subject)}) exceeds 60 characters"

        words = draft.body.split()
        if len(words) > 160:
            return False, "validation_failed", f"Body length ({len(words)} words) exceeds 160 words limit"

        # 10. Sending enabled gate (unless dry_run)
        if not dry_run and not email_send_enabled:
            return (
                False,
                "sending_disabled",
                "Email sending is disabled (EMAIL_SEND_ENABLED=false)",
            )

        return True, None, None
