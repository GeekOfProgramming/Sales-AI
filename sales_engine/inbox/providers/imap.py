import os
from typing import List, Dict, Any, Optional

from sales_engine.inbox.models import MailboxConnectionInfo
from sales_engine.inbox.providers.base import (
    BaseMailboxProvider,
    ProviderSyncBatch,
    ProviderSendResult,
)


class IMAPMailboxProvider(BaseMailboxProvider):
    """
    Standard IMAP/SMTP provider fallback.
    Safely degrades to not_configured when host/credentials are not present.
    """

    def __init__(self):
        self.host = os.getenv("IMAP_HOST")
        self.user = os.getenv("IMAP_USER")
        self.password = os.getenv("IMAP_PASSWORD")

    @property
    def provider_name(self) -> str:
        return "imap"

    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def get_connection_status(self) -> MailboxConnectionInfo:
        if not self.is_configured():
            return MailboxConnectionInfo(
                provider="imap",
                status="not_configured",
                account_email=self.user,
                error_message=None,
            )
        return MailboxConnectionInfo(
            provider="imap",
            status="configured",
            account_email=self.user,
            error_message=None,
        )

    def fetch_messages(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        lookback_days: int = 30,
    ) -> ProviderSyncBatch:
        if not self.is_configured():
            return ProviderSyncBatch(
                messages=[],
                new_cursor=cursor,
                errors=["IMAP provider not configured"],
            )
        return ProviderSyncBatch(messages=[], new_cursor=cursor, errors=[])

    def send_reply(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
        provider_thread_id: Optional[str] = None,
    ) -> ProviderSendResult:
        if not self.is_configured():
            return ProviderSendResult(
                success=False,
                error_message="IMAP provider not configured",
            )
        return ProviderSendResult(success=False, error_message="IMAP send requires active SMTP transport")
