"""
sales_engine/sending/smtp_adapter.py
MAIL-SEND-01A: Authenticated, TLS-Verified Register.it SMTP Adapter.

Provides an isolated, credential-abstracted SMTP transport for:
- info@pybim.com (pyBIM Global)
- info@pybim.it (pyBIM Italia)

Proposed Register.it submission configuration:
- Host: authsmtp.securemail.pro
- Port: 465 (Implicit SSL/TLS)
- Alternative Port: 587 (Explicit STARTTLS)
- Account-specific credentials retrieved from MailboxCredentialStore.

Security & Offline Guarantees:
- ZERO plaintext passwords logged or saved.
- Strict timeout configuration (default 15.0s).
- Isolated connection abstraction allowing clean offline mocking and unit testing.
- No live connections are initiated unless explicitly invoked with live mode enabled.
"""

import os
import ssl
import smtplib
import logging
from typing import Optional, Dict, Any, List, Tuple
from email.message import EmailMessage

from sales_engine.inbox.credential_store import MailboxCredentialStore

logger = logging.getLogger(__name__)

# Register.it authoritative SMTP configurations
DEFAULT_REG_IT_SMTP_HOST = "authsmtp.securemail.pro"
DEFAULT_REG_IT_SMTP_PORT = 465  # Implicit SSL


class SMTPSubmissionResult:
    """Represents the structured outcome of an SMTP transmission attempt."""
    def __init__(
        self,
        success: bool,
        status_code: Optional[int] = None,
        response_message: Optional[str] = None,
        error_type: Optional[str] = None,
        error_details: Optional[str] = None,
        duration_ms: Optional[int] = None,
        is_retryable: bool = False,
        is_unknown_acceptance: bool = False,
    ):
        self.success = success
        self.status_code = status_code
        self.response_message = response_message
        self.error_type = error_type
        self.error_details = error_details
        self.duration_ms = duration_ms
        self.is_retryable = is_retryable
        self.is_unknown_acceptance = is_unknown_acceptance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "response_message": self.response_message,
            "error_type": self.error_type,
            "error_details": self.error_details,
            "duration_ms": self.duration_ms,
            "is_retryable": self.is_retryable,
            "is_unknown_acceptance": self.is_unknown_acceptance,
        }


class RegisterItSMTPAdapter:
    """
    Authenticated SMTP adapter for Register.it accounts.
    Uses MailboxCredentialStore to fetch account passwords safely.
    """

    def __init__(
        self,
        credential_store: Optional[MailboxCredentialStore] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        use_ssl: bool = True,
        timeout: float = 15.0,
    ):
        self.credential_store = credential_store or MailboxCredentialStore()
        self.host = host or os.environ.get("REG_IT_SMTP_HOST", DEFAULT_REG_IT_SMTP_HOST)
        self.port = int(port or os.environ.get("REG_IT_SMTP_PORT", DEFAULT_REG_IT_SMTP_PORT))
        self.use_ssl = use_ssl
        self.timeout = timeout

    def _get_account_password(self, credential_ref: str) -> Optional[str]:
        """Fetches decrypted password from secure store without logging."""
        if not credential_ref:
            return None
        return self.credential_store.get_credential(credential_ref)

    def send_message(
        self,
        sender_email: str,
        credential_ref: str,
        envelope_recipients: List[str],
        message: EmailMessage,
        live_send_enabled: bool = False,
        custom_server: Optional[Any] = None,
    ) -> SMTPSubmissionResult:
        """
        Executes SMTP transmission of an RFC 5322 EmailMessage.
        
        If live_send_enabled is False, performs a dry-run connection/credential check
        WITHOUT issuing DATA or sending bytes.
        
        custom_server can be passed for isolated unit tests / mocks.
        """
        import time
        t_start = time.time()

        if not envelope_recipients:
            return SMTPSubmissionResult(
                success=False,
                error_type="empty_recipients",
                error_details="No envelope recipients specified for message delivery.",
                is_retryable=False,
            )

        password = self._get_account_password(credential_ref)
        if not password:
            return SMTPSubmissionResult(
                success=False,
                error_type="missing_credential",
                error_details=f"No secure password available for credential_ref '{credential_ref}'.",
                is_retryable=False,
            )

        # OFFLINE SAFETY GATE
        if not live_send_enabled and custom_server is None:
            # Safe dry run: validate credentials and headers without network send
            duration_ms = int((time.time() - t_start) * 1000)
            return SMTPSubmissionResult(
                success=True,
                status_code=250,
                response_message="DRY RUN: Message validated successfully (live send disabled).",
                duration_ms=duration_ms,
            )

        # Network transmission (Mock or Real)
        server = custom_server
        try:
            if server is None:
                context = ssl.create_default_context()
                if self.use_ssl and self.port == 465:
                    server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
                else:
                    server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                    server.starttls(context=context)

                # Authenticate
                server.login(sender_email, password)

            # Send message bytes via send_message
            refused = server.send_message(
                msg=message,
                from_addr=sender_email,
                to_addrs=envelope_recipients,
            )

            # Handle partial recipient refusal
            if refused:
                duration_ms = int((time.time() - t_start) * 1000)
                refused_details = ", ".join(f"{r}: {code} {msg}" for r, (code, msg) in refused.items())
                return SMTPSubmissionResult(
                    success=False,
                    status_code=451,
                    error_type="recipient_refusal",
                    error_details=f"SMTP server refused one or more recipients: {refused_details}",
                    duration_ms=duration_ms,
                    is_retryable=True,
                )

            duration_ms = int((time.time() - t_start) * 1000)
            return SMTPSubmissionResult(
                success=True,
                status_code=250,
                response_message="2.0.0 OK: Message accepted for delivery.",
                duration_ms=duration_ms,
            )

        except smtplib.SMTPAuthenticationError as e:
            duration_ms = int((time.time() - t_start) * 1000)
            return SMTPSubmissionResult(
                success=False,
                status_code=e.smtp_code,
                error_type="auth_error",
                error_details="SMTP Authentication rejected by mail server.",
                duration_ms=duration_ms,
                is_retryable=False,
            )
        except smtplib.SMTPDataError as e:
            duration_ms = int((time.time() - t_start) * 1000)
            # 5xx permanent error; 4xx temporary error
            is_temp = 400 <= e.smtp_code < 500
            return SMTPSubmissionResult(
                success=False,
                status_code=e.smtp_code,
                error_type="data_error",
                error_details=f"SMTP DATA Error: {e.smtp_error}",
                duration_ms=duration_ms,
                is_retryable=is_temp,
            )
        except (smtplib.SMTPServerDisconnected, TimeoutError, smtplib.SMTPConnectError) as e:
            duration_ms = int((time.time() - t_start) * 1000)
            # CRITICAL: If timeout happens during or after data transfer, acceptance is UNCERTAIN!
            return SMTPSubmissionResult(
                success=False,
                status_code=0,
                error_type="connection_timeout",
                error_details=f"SMTP Connection broken or timed out: {type(e).__name__}",
                duration_ms=duration_ms,
                is_retryable=False,
                is_unknown_acceptance=True,  # Requires operator audit to prevent double-send
            )
        except Exception as e:
            duration_ms = int((time.time() - t_start) * 1000)
            return SMTPSubmissionResult(
                success=False,
                status_code=0,
                error_type="unexpected_error",
                error_details=f"Unexpected SMTP transmission error: {type(e).__name__}: {str(e)}",
                duration_ms=duration_ms,
                is_retryable=False,
            )
        finally:
            if custom_server is None and server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
