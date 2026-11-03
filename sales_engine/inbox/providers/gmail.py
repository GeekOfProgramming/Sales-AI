import os
from typing import List, Dict, Any, Optional

from sales_engine.inbox.models import MailboxConnectionInfo
from sales_engine.inbox.providers.base import (
    BaseMailboxProvider,
    ProviderSyncBatch,
    ProviderSendResult,
)


class GmailMailboxProvider(BaseMailboxProvider):
    """
    Gmail API provider using OAuth.
    Safely degrades to not_configured when credentials are not present.
    """

    def __init__(self):
        self.client_id = os.getenv("GMAIL_CLIENT_ID")
        self.credentials_json = os.getenv("GMAIL_CREDENTIALS_JSON")
        self.token_path = os.getenv("GMAIL_TOKEN_PATH")

    @property
    def provider_name(self) -> str:
        return "gmail"

    def is_configured(self) -> bool:
        return bool(self.client_id or self.credentials_json or (self.token_path and os.path.exists(self.token_path)))

    def get_connection_status(self) -> MailboxConnectionInfo:
        if not self.is_configured():
            return MailboxConnectionInfo(
                provider="gmail",
                status="not_configured",
                account_email=os.getenv("GMAIL_ACCOUNT_EMAIL"),
                error_message=None,
            )
        return MailboxConnectionInfo(
            provider="gmail",
            status="configured",
            account_email=os.getenv("GMAIL_ACCOUNT_EMAIL"),
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
                errors=["Gmail provider not configured (missing credentials)"],
            )
        # Production OAuth fetch stub
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
                error_message="Gmail provider not configured",
            )
        return ProviderSendResult(success=False, error_message="Live Gmail sending requires interactive OAuth flow")
