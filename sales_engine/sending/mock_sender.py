import uuid
from typing import Dict, Any, Optional, List

from sales_engine.sending.base_sender import BaseEmailSender
from sales_engine.sending.schemas import SendResult


class MockEmailSender(BaseEmailSender):
    """
    Test-only in-memory mock email sender.
    MUST NEVER automatically activate in production.
    """

    def __init__(
        self,
        simulate_auth_error: bool = False,
        simulate_timeout: bool = False,
        simulate_connection_error: bool = False,
        simulate_provider_error: bool = False,
    ):
        self.sent_messages: List[Dict[str, Any]] = []
        self.simulate_auth_error = simulate_auth_error
        self.simulate_timeout = simulate_timeout
        self.simulate_connection_error = simulate_connection_error
        self.simulate_provider_error = simulate_provider_error

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
        if self.simulate_auth_error:
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="mock",
                error_type="smtp_auth_error",
                error_message="Simulated SMTP authentication error",
            )

        if self.simulate_timeout:
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="mock",
                error_type="smtp_timeout",
                error_message="Simulated SMTP timeout",
            )

        if self.simulate_connection_error:
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="mock",
                error_type="smtp_connection_error",
                error_message="Simulated SMTP connection error",
            )

        if self.simulate_provider_error:
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="mock",
                error_type="provider_error",
                error_message="Simulated SMTP remote reject",
            )

        msg_id = f"mock-msg-{uuid.uuid4().hex[:12]}"
        record = {
            "draft_id": draft_id,
            "revision": revision,
            "send_key": send_key,
            "to_email": to_email,
            "from_email": from_email,
            "from_name": from_name,
            "subject": subject,
            "body": body,
            "headers": headers or {},
            "provider_message_id": msg_id,
        }
        self.sent_messages.append(record)

        return SendResult(
            draft_id=draft_id,
            revision=revision,
            send_key=send_key,
            status="sent",
            provider="mock",
            provider_message_id=msg_id,
        )
