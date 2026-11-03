import os
from typing import List, Dict, Any, Optional

from sales_engine.inbox.models import MailboxConnectionInfo
from sales_engine.inbox.providers.base import (
    BaseMailboxProvider,
    ProviderSyncBatch,
    ProviderSendResult,
)


class MicrosoftGraphMailboxProvider(BaseMailboxProvider):
    """
    Microsoft Graph API provider for Outlook / Office 365 mailboxes.
    Safely degrades to not_configured when credentials are not present.
    """

    def __init__(self):
        self.client_id = os.getenv("MS_GRAPH_CLIENT_ID")
        self.tenant_id = os.getenv("MS_GRAPH_TENANT_ID")
        self.client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET")

    @property
    def provider_name(self) -> str:
        return "graph"

    def is_configured(self) -> bool:
        return bool(self.client_id and self.tenant_id and self.client_secret)

    def get_connection_status(self) -> MailboxConnectionInfo:
        if not self.is_configured():
            return MailboxConnectionInfo(
                provider="graph",
                status="not_configured",
                account_email=os.getenv("MS_GRAPH_ACCOUNT_EMAIL"),
                error_message=None,
            )
        return MailboxConnectionInfo(
            provider="graph",
            status="configured",
            account_email=os.getenv("MS_GRAPH_ACCOUNT_EMAIL"),
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
                errors=["Microsoft Graph provider not configured (missing credentials)"],
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
                error_message="Microsoft Graph provider not configured",
            )
        return ProviderSendResult(success=False, error_message="Live Graph sending requires OAuth token")
