from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from sales_engine.sending.schemas import SendResult


class BaseEmailSender(ABC):
    """
    Abstract adapter for email delivery providers (SMTP, Mock).
    """

    @abstractmethod
    def send_email(
        self,
        draft_id: str,
        revision: int,
        send_key: str,
        to_email: str,
        from_email: str,
        from_name: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> SendResult:
        """
        Deliver a text/plain cold email.
        Returns SendResult indicating provider success or failure.
        """
        pass
