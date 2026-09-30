import os
import smtplib
import socket
import uuid
import logging
from email.mime.text import MIMEText
from typing import Dict, Any, Optional

from sales_engine.sending.base_sender import BaseEmailSender
from sales_engine.sending.schemas import SendResult

logger = logging.getLogger(__name__)


def sanitize_error(msg: str, secret_patterns: Optional[list] = None) -> str:
    """Removes sensitive credentials from error strings."""
    sanitized = str(msg)
    if secret_patterns:
        for secret in secret_patterns:
            if secret and str(secret).strip():
                sanitized = sanitized.replace(str(secret).strip(), "********")
    return sanitized[:300]


class SMTPEmailSender(BaseEmailSender):
    """
    Standard SMTP email sender adapter for Phase 9.
    Sends plain text email via configured SMTP host/port.
    Never logs or leaks credentials.
    """

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: Optional[bool] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
    ):
        self.smtp_host = smtp_host or os.environ.get("SMTP_HOST", "localhost")
        self.smtp_port = int(smtp_port or os.environ.get("SMTP_PORT", 587))
        self.smtp_username = smtp_username or os.environ.get("SMTP_USERNAME")
        self.smtp_password = smtp_password or os.environ.get("SMTP_PASSWORD")
        self.use_tls = (
            use_tls
            if use_tls is not None
            else os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
        )
        self.from_email = from_email or os.environ.get("SMTP_FROM_EMAIL")
        self.from_name = from_name or os.environ.get("SMTP_FROM_NAME")

    def sanitize_error(self, msg: str) -> str:
        return sanitize_error(msg, [self.smtp_password, self.smtp_username])

    def _sanitize_error(self, msg: str) -> str:
        return self.sanitize_error(msg)

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
        secrets_to_sanitize = [self.smtp_password, self.smtp_username]

        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            sender_display = f"{from_name} <{from_email}>" if from_name else from_email
            msg["From"] = sender_display
            msg["To"] = to_email
            generated_msg_id = f"<{uuid.uuid4()}@{socket.getfqdn()}>"
            msg["Message-ID"] = generated_msg_id

            if headers:
                for k, v in headers.items():
                    if k.lower() not in ("from", "to", "subject", "message-id"):
                        msg[k] = v

            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15.0)
            try:
                server.ehlo()
                if self.use_tls:
                    server.starttls()
                    server.ehlo()

                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)

                server.sendmail(from_email, [to_email], msg.as_string())
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="sent",
                provider="smtp",
                provider_message_id=generated_msg_id,
            )

        except smtplib.SMTPAuthenticationError as e:
            raw_msg = sanitize_error(str(e), secrets_to_sanitize)
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="smtp",
                error_type="smtp_auth_error",
                error_message=f"SMTP authentication failed: {raw_msg}",
            )
        except (TimeoutError, socket.timeout) as e:
            raw_msg = sanitize_error(str(e), secrets_to_sanitize)
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="smtp",
                error_type="smtp_timeout",
                error_message=f"SMTP connection timeout: {raw_msg}",
            )
        except (smtplib.SMTPConnectError, ConnectionError, OSError) as e:
            raw_msg = sanitize_error(str(e), secrets_to_sanitize)
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="smtp",
                error_type="smtp_connection_error",
                error_message=f"SMTP connection error: {raw_msg}",
            )
        except Exception as e:
            raw_msg = sanitize_error(str(e), secrets_to_sanitize)
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="failed",
                provider="smtp",
                error_type="provider_error",
                error_message=f"SMTP delivery error: {raw_msg}",
            )
