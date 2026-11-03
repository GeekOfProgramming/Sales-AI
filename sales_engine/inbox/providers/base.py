from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from sales_engine.inbox.models import MailboxConnectionInfo


class ProviderSyncBatch(BaseModel):
    """Batch of messages returned by an inbox provider sync step."""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    new_cursor: Optional[str] = None
    errors: List[str] = Field(default_factory=list)


class ProviderSendResult(BaseModel):
    """Result of attempting to send a reply via a provider."""
    success: bool
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None


class BaseMailboxProvider(ABC):
    """Abstract base class for all inbox email providers (Mock, Gmail, Graph, IMAP)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier name of the provider adapter."""
        pass

    @abstractmethod
    def get_connection_status(self) -> MailboxConnectionInfo:
        """Return safe connection status without exposing credentials."""
        pass

    @abstractmethod
    def fetch_messages(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        lookback_days: int = 30,
    ) -> ProviderSyncBatch:
        """Fetch newly arrived messages since the given cursor."""
        pass

    @abstractmethod
    def send_reply(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
        provider_thread_id: Optional[str] = None,
    ) -> ProviderSendResult:
        """Send an explicitly approved reply through this provider."""
        pass
