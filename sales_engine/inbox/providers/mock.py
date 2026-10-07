import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sales_engine.inbox.models import MailboxConnectionInfo
from sales_engine.inbox.providers.base import (
    BaseMailboxProvider,
    ProviderSyncBatch,
    ProviderSendResult,
)


class MockMailboxProvider(BaseMailboxProvider):
    """
    Deterministic in-memory mailbox provider for testing and QA.
    Supports enqueuing test messages, simulating cursors, and recording sent replies.
    """

    def __init__(self, account_email: str = "outreach@pybim.it"):
        self._account_email = account_email
        self._message_queue: List[Dict[str, Any]] = []
        self._sent_replies: List[Dict[str, Any]] = []
        self._status: str = "connected"
        self._simulated_error: Optional[str] = None

    @property
    def provider_name(self) -> str:
        return "mock"

    def set_status(self, status: str, error_message: Optional[str] = None):
        self._status = status
        self._simulated_error = error_message

    def enqueue_message(self, message: Dict[str, Any]):
        """Enqueue an inbound message for subsequent sync."""
        if "provider_message_id" not in message:
            message["provider_message_id"] = f"mock_msg_{uuid.uuid4().hex[:10]}"
        if "received_at" not in message:
            message["received_at"] = datetime.now(timezone.utc).isoformat()
        self._message_queue.append(message)

    def enqueue_messages(self, messages: List[Dict[str, Any]]):
        for msg in messages:
            self.enqueue_message(msg)

    def clear_queue(self):
        self._message_queue.clear()

    @property
    def sent_replies(self) -> List[Dict[str, Any]]:
        return list(self._sent_replies)

    def clear_sent_replies(self):
        self._sent_replies.clear()

    def get_connection_status(self) -> MailboxConnectionInfo:
        return MailboxConnectionInfo(
            provider="mock",
            status=self._status, # type: ignore
            account_email=self._account_email,
            last_synced_at=datetime.now(timezone.utc).isoformat(),
            error_message=self._simulated_error,
        )

    def fetch_messages(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        lookback_days: int = 30,
    ) -> ProviderSyncBatch:
        if self._simulated_error:
            return ProviderSyncBatch(
                messages=[],
                new_cursor=cursor,
                errors=[self._simulated_error],
            )

        start_idx = 0
        if cursor and cursor.startswith("mock_cursor_"):
            try:
                start_idx = int(cursor.replace("mock_cursor_", ""))
            except ValueError:
                start_idx = 0

        total = len(self._message_queue)
        end_idx = min(start_idx + limit, total)
        slice_messages = self._message_queue[start_idx:end_idx]

        new_cursor = f"mock_cursor_{end_idx}" if end_idx > start_idx else cursor

        return ProviderSyncBatch(
            messages=slice_messages,
            new_cursor=new_cursor,
            errors=[],
        )

    def send_reply(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
        provider_thread_id: Optional[str] = None,
    ) -> ProviderSendResult:
        if self._simulated_error:
            return ProviderSendResult(
                success=False,
                error_message=self._simulated_error,
            )

        reply_id = f"mock_reply_{uuid.uuid4().hex[:10]}"
        sent_record = {
            "reply_id": reply_id,
            "to_email": to_email,
            "subject": subject,
            "body_text": body_text,
            "in_reply_to": in_reply_to,
            "references": references or [],
            "provider_thread_id": provider_thread_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self._sent_replies.append(sent_record)
        return ProviderSendResult(success=True, provider_message_id=reply_id)
