import os
import re
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

from sales_engine.inbox.store import InboxStore
from sales_engine.inbox.models import (
    Conversation,
    ReplySuggestion,
    ConversationEvent,
)
from sales_engine.inbox.providers.base import BaseMailboxProvider
from sales_engine.inbox.providers.mock import MockMailboxProvider
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.approval_service import get_trusted_senders


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ReplySendResult(BaseModel):
    success: bool
    attempt_id: str
    send_key: str
    status: str
    provider_message_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class ReplySendOrchestrator:
    """
    Coordinates explicit sending of approved reply suggestions.
    Enforces Phase 9 safety parity: trusted sender verification, suppression checks,
    EMAIL_SEND_ENABLED guard, operator confirmation, and strict idempotency.
    """

    def __init__(
        self,
        inbox_store: InboxStore,
        suppression_store: Optional[SuppressionStore] = None,
        provider: Optional[BaseMailboxProvider] = None,
        email_send_enabled: Optional[bool] = None,
    ):
        self.inbox_store = inbox_store
        self.suppression_store = suppression_store or SuppressionStore()
        self.provider = provider or MockMailboxProvider()

        if email_send_enabled is not None:
            self.email_send_enabled = email_send_enabled
        else:
            self.email_send_enabled = os.environ.get("EMAIL_SEND_ENABLED", "false").lower() in ("true", "1", "yes")

    def send_reply(
        self,
        conversation_id: str,
        suggestion_id: str,
        sender_email: str,
        operator_confirmed: bool = False,
        actor: str = "operator",
    ) -> ReplySendResult:
        attempt_id = f"rsa_{uuid.uuid4().hex[:12]}"

        # 1. Mandatory Operator Confirmation
        if not operator_confirmed:
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="CONFIRMATION_REQUIRED",
                error_message="Explicit operator confirmation is required to send a reply.",
            )

        # 2. Sender Trust Verification
        clean_sender = sender_email.strip().lower() if sender_email else ""
        trusted_senders = get_trusted_senders()
        if clean_sender not in trusted_senders:
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="UNTRUSTED_SENDER",
                error_message=f"Sender email '{clean_sender}' is not configured as a trusted sender.",
            )

        # 3. Retrieve Conversation and Suggestion
        conv = self.inbox_store.get_conversation(conversation_id)
        if not conv:
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="CONVERSATION_NOT_FOUND",
                error_message=f"Conversation '{conversation_id}' not found.",
            )

        sug = self.inbox_store.get_suggestion(suggestion_id)
        if not sug:
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="SUGGESTION_NOT_FOUND",
                error_message=f"Suggestion '{suggestion_id}' not found.",
            )

        # 4. State Verification: must be approved, cannot be sent already
        if sug.status == "sent":
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="ALREADY_SENT",
                error_message="This suggestion revision has already been sent and is immutable.",
            )

        if sug.status != "approved":
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="APPROVAL_REQUIRED",
                error_message=f"Suggestion status is '{sug.status}'. Human approval is required before sending.",
            )

        # 5. Retrieve Inbound Message & Recipient
        inb = self.inbox_store.get_inbound_message(sug.inbound_message_id)
        if not inb:
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="INBOUND_MESSAGE_NOT_FOUND",
                error_message="Referenced inbound message record is missing.",
            )

        recipient_email = inb.from_email.strip().lower()
        if not recipient_email or not EMAIL_REGEX.match(recipient_email):
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="INVALID_RECIPIENT",
                error_message=f"Invalid recipient email address: {recipient_email}",
            )

        # 6. Suppression Check
        if self.suppression_store.is_suppressed(email=recipient_email):
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="RECIPIENT_SUPPRESSED",
                error_message=f"Recipient '{recipient_email}' is on the Phase 9 suppression list.",
            )

        # 7. Unsafe conversation state check
        if conv.status in ("do_not_contact", "bounce", "auto_reply"):
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key="",
                status="blocked",
                error_type="UNSAFE_CONVERSATION_STATE",
                error_message=f"Cannot send reply to conversation in '{conv.status}' state.",
            )

        # 8. Deterministic Idempotency Key
        send_key = hashlib.sha256(
            f"{conv.conversation_id}:{sug.suggestion_id}:{recipient_email}".encode()
        ).hexdigest()

        if self.inbox_store.is_reply_send_key_used(send_key):
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key=send_key,
                status="blocked",
                error_type="DUPLICATE_SEND_BLOCKED",
                error_message="Duplicate reply send prevented by idempotency lock.",
            )

        # 9. Send enabled check (unless MockMailboxProvider in test)
        if not self.email_send_enabled and not isinstance(self.provider, MockMailboxProvider):
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key=send_key,
                status="blocked",
                error_type="SEND_DISABLED",
                error_message="Outbound email sending is disabled (EMAIL_SEND_ENABLED=false).",
            )

        # 10. Execute send via Provider
        prov_res = self.provider.send_reply(
            to_email=recipient_email,
            subject=sug.subject,
            body_text=sug.body,
            in_reply_to=inb.internet_message_id,
            references=inb.references + ([inb.internet_message_id] if inb.internet_message_id else []),
            provider_thread_id=conv.provider_thread_id,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        if not prov_res.success:
            self.inbox_store.record_reply_send_attempt(
                attempt_id=attempt_id,
                send_key=send_key,
                conversation_id=conv.conversation_id,
                suggestion_id=sug.suggestion_id,
                provider=self.provider.provider_name,
                recipient_email=recipient_email,
                sender_email=clean_sender,
                status="failed",
                error_type="PROVIDER_SEND_FAILED",
                error_message=prov_res.error_message,
            )
            return ReplySendResult(
                success=False,
                attempt_id=attempt_id,
                send_key=send_key,
                status="failed",
                error_type="PROVIDER_SEND_FAILED",
                error_message=prov_res.error_message,
            )

        # 11. Record Successful Send & Immutability
        self.inbox_store.record_reply_send_attempt(
            attempt_id=attempt_id,
            send_key=send_key,
            conversation_id=conv.conversation_id,
            suggestion_id=sug.suggestion_id,
            provider=self.provider.provider_name,
            recipient_email=recipient_email,
            sender_email=clean_sender,
            status="sent",
        )

        sug.status = "sent"
        sug.sent_at = now_iso
        self.inbox_store.save_suggestion(sug)

        conv.status = "waiting_on_them"
        conv.last_message_at = now_iso
        conv.updated_at = now_iso
        self.inbox_store.save_conversation(conv)

        # Audit Event
        self.inbox_store.add_event(
            ConversationEvent(
                conversation_id=conv.conversation_id,
                event_type="reply_sent",
                actor=actor,
                details={
                    "suggestion_id": sug.suggestion_id,
                    "attempt_id": attempt_id,
                    "provider": self.provider.provider_name,
                    "provider_message_id": prov_res.provider_message_id,
                    "recipient_email": recipient_email,
                    "sender_email": clean_sender,
                },
            )
        )

        return ReplySendResult(
            success=True,
            attempt_id=attempt_id,
            send_key=send_key,
            status="sent",
            provider_message_id=prov_res.provider_message_id,
        )
