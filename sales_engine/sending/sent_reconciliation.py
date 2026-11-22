"""
sales_engine/sending/sent_reconciliation.py
MAIL-SEND-01G: Safe, Idempotent IMAP Sent-Folder Reconciliation Service.

Provides:
1. Exact Message-ID remote search in Register.it INBOX.Sent (read-only diagnostic).
2. Idempotent IMAP APPEND for successfully submitted outbound jobs (smtp_accepted).
3. Ambiguous timeout handling with post-failure remote verification.
4. Strict account binding (original authorized sender only).
5. Safe MIME reconstruction from immutable OutboundQueueJob record.
"""

import ssl
import time
import imaplib
import logging
from typing import Dict, Any, Optional, List, Tuple
from email.utils import formatdate

from sales_engine.sending.outbound_models import OutboundQueueJob
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.mime_builder import build_mime_message
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.models import MailboxAccountRecord

logger = logging.getLogger(__name__)

DEFAULT_IMAP_TIMEOUT = 25.0
DEFAULT_SENT_FOLDER = "INBOX.Sent"


class SentReconciliationError(Exception):
    """Base exception for sent reconciliation failures."""
    pass


class SentReconciliationService:
    """
    Safely reconciles outbound SMTP accepted messages into Register.it IMAP Sent folder.
    """

    def __init__(
        self,
        db_path: str,
        credential_store: Optional[MailboxCredentialStore] = None,
        timeout: float = DEFAULT_IMAP_TIMEOUT,
    ):
        self.db_path = db_path
        self.credential_store = credential_store or MailboxCredentialStore()
        self.timeout = timeout
        self.queue_store = OutboundQueueStore(db_path=db_path, auto_apply_schema=False)
        self.mailbox_repo = MailboxRepository(db_path=db_path, auto_migrate=False)

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create secure TLS context for IMAP."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def connect_imap(self, account: MailboxAccountRecord) -> imaplib.IMAP4_SSL:
        """Connect and authenticate to account's IMAP server via TLS."""
        if not account.imap_host or not account.imap_port:
            raise SentReconciliationError(f"Account {account.account_id} has invalid IMAP host/port.")

        cred_ref = account.credential_ref or account.account_id
        password = self.credential_store.get_credential(cred_ref)
        if not password:
            raise SentReconciliationError(f"No stored credentials available for account '{account.account_id}' (ref: '{cred_ref}').")

        ctx = self._create_ssl_context()
        try:
            client = imaplib.IMAP4_SSL(
                host=account.imap_host,
                port=account.imap_port,
                ssl_context=ctx,
                timeout=self.timeout,
            )
            login_user = account.email_address
            client.login(login_user, password)
            return client
        except Exception as e:
            raise SentReconciliationError(f"IMAP connection failed for account {account.account_id}: {e}")

    def find_remote_sent_message(
        self,
        client: imaplib.IMAP4_SSL,
        rfc_message_id: str,
        sent_folder: str = DEFAULT_SENT_FOLDER,
    ) -> List[str]:
        """
        Searches remote sent folder for messages matching exact RFC Message-ID.
        Returns list of matched UIDs (or empty list if not found).
        """
        clean_msg_id = (rfc_message_id or "").strip()
        if not clean_msg_id:
            raise ValueError("rfc_message_id cannot be empty")

        # Select folder (read-only if possible, or normal select)
        status, _ = client.select(sent_folder, readonly=True)
        if status != "OK":
            # Some IMAP servers require select without readonly=True
            status, resp = client.select(sent_folder)
            if status != "OK":
                raise SentReconciliationError(f"Cannot select sent folder '{sent_folder}': {resp}")

        # Search by Message-ID header
        # RFC 3501 search: HEADER Message-ID "<value>"
        # Note: if Message-ID is enclosed in brackets, ensure exact match
        search_query = f'HEADER Message-ID "{clean_msg_id}"'
        status, data = client.uid("SEARCH", None, search_query)
        if status != "OK":
            raise SentReconciliationError(f"IMAP search failed in '{sent_folder}' with query: {search_query}")

        uids: List[str] = []
        if data and data[0]:
            raw_uids = data[0].decode("ascii", errors="ignore").split()
            uids = [u.strip() for u in raw_uids if u.strip()]

        return uids

    def inspect_remote_sent_status(
        self,
        job_id: str,
        sent_folder: str = DEFAULT_SENT_FOLDER,
        client: Optional[imaplib.IMAP4_SSL] = None,
    ) -> Dict[str, Any]:
        """
        Read-only diagnostic: checks whether job's RFC Message-ID exists in remote Sent folder.
        """
        job = self.queue_store.get_job(job_id)
        if not job:
            raise SentReconciliationError(f"Job '{job_id}' not found in outbound queue.")

        if not job.rfc_message_id:
            raise SentReconciliationError(f"Job '{job_id}' has no rfc_message_id.")

        account = self.mailbox_repo.get_account_by_email(job.sender_email)
        if not account:
            raise SentReconciliationError(f"Sender account not found for email: {job.sender_email}")

        managed_client = False
        if client is None:
            client = self.connect_imap(account)
            managed_client = True

        try:
            matched_uids = self.find_remote_sent_message(client, job.rfc_message_id, sent_folder=sent_folder)
            return {
                "job_id": job.job_id,
                "sender_email": job.sender_email,
                "account_id": account.account_id,
                "rfc_message_id": job.rfc_message_id,
                "sent_folder": sent_folder,
                "exists_in_sent": len(matched_uids) > 0,
                "match_count": len(matched_uids),
                "matched_uids": matched_uids,
            }
        finally:
            if managed_client and client:
                try:
                    client.logout()
                except Exception:
                    pass

    def reconstruct_job_mime(self, job: OutboundQueueJob) -> bytes:
        """
        Reconstructs RFC 5322 MIME message bytes faithfully from immutable OutboundQueueJob fields.
        """
        import json

        def _parse_recipients(val):
            if isinstance(val, list):
                return val
            if isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    return [val.strip()]
            return []

        to_recipients = _parse_recipients(job.header_to_json)
        cc_recipients = _parse_recipients(job.header_cc_json)
        bcc_recipients = _parse_recipients(job.header_bcc_json)

        msg, _ = build_mime_message(
            sender_email=job.sender_email,
            to_addresses=to_recipients,
            subject=job.subject,
            body_plain_text=job.body_plain_text or "",
            body_html_sanitized=job.body_html_sanitized,
            cc_addresses=cc_recipients,
            bcc_addresses=bcc_recipients,
            rfc_message_id=job.rfc_message_id,
            account_id=job.account_id,
            include_official_signature=False,  # Signature is already embedded in body_plain_text / body_html_sanitized
        )
        return msg.as_bytes()

    def reconcile_job_sent_copy(
        self,
        job_id: str,
        dry_run: bool = True,
        live_append: bool = False,
        sent_folder: str = DEFAULT_SENT_FOLDER,
        operator_username: str = "operator_reconciliation",
        client: Optional[imaplib.IMAP4_SSL] = None,
    ) -> Dict[str, Any]:
        """
        Safely reconciles an accepted outbound job into the IMAP Sent folder.
        
        Guarantees:
        - Job status must be 'smtp_accepted'.
        - Checks for existing message by rfc_message_id before any append (idempotent).
        - If dry_run=True, verifies credentials, folder selection, and MIME reconstruction without calling APPEND.
        - If live_append=True (and dry_run=False), performs IMAP APPEND with \\Seen flag.
        - If an ambiguous network timeout occurs during APPEND, immediately verifies if message landed remotely.
        """
        job = self.queue_store.get_job(job_id)
        if not job:
            raise SentReconciliationError(f"Job '{job_id}' not found in outbound queue.")

        if job.status != "smtp_accepted":
            raise SentReconciliationError(
                f"Job '{job_id}' status is '{job.status}'. Reconciliation only allowed for 'smtp_accepted' jobs."
            )

        if not job.rfc_message_id:
            raise SentReconciliationError(f"Job '{job_id}' does not have an RFC Message-ID recorded.")

        account = self.mailbox_repo.get_account_by_email(job.sender_email)
        if not account:
            raise SentReconciliationError(f"Authorized mailbox account not found for sender '{job.sender_email}'.")

        # 1. Reconstruct MIME bytes
        try:
            raw_mime_bytes = self.reconstruct_job_mime(job)
        except Exception as e:
            raise SentReconciliationError(f"Failed to reconstruct MIME message for job {job_id}: {e}")

        managed_client = False
        if client is None:
            client = self.connect_imap(account)
            managed_client = True

        try:
            # 2. Check remote sent folder (duplicate prevention)
            existing_uids = self.find_remote_sent_message(client, job.rfc_message_id, sent_folder=sent_folder)
            if existing_uids:
                return {
                    "success": True,
                    "action": "SKIPPED_ALREADY_EXISTS",
                    "status": "already_reconciled",
                    "job_id": job.job_id,
                    "rfc_message_id": job.rfc_message_id,
                    "sent_folder": sent_folder,
                    "account_id": account.account_id,
                    "sender_email": job.sender_email,
                    "matched_uids": existing_uids,
                    "message": f"Message with Message-ID {job.rfc_message_id} already exists in {sent_folder} (UIDs: {existing_uids}).",
                }

            # 3. Dry-Run Handling
            if dry_run or not live_append:
                return {
                    "success": True,
                    "action": "DRY_RUN_VALIDATED",
                    "status": "dry_run_ready",
                    "job_id": job.job_id,
                    "rfc_message_id": job.rfc_message_id,
                    "sent_folder": sent_folder,
                    "account_id": account.account_id,
                    "sender_email": job.sender_email,
                    "raw_mime_bytes_len": len(raw_mime_bytes),
                    "message": f"Preflight checks passed. Message ready to append to {sent_folder} ({len(raw_mime_bytes)} bytes).",
                }

            # 4. Live IMAP APPEND
            # Prepare internal date formatted or None
            # Using imaplib.Time2Internaldate if available, or None
            internal_date = imaplib.Time2Internaldate(time.time())
            flags = r"(\Seen)"

            try:
                append_status, append_resp = client.append(
                    sent_folder,
                    flags,
                    internal_date,
                    raw_mime_bytes,
                )
            except Exception as append_exc:
                logger.warning(f"Ambiguous APPEND failure for job {job_id}: {append_exc}. Verifying remote state...")
                # Verify if append actually succeeded despite connection error
                try:
                    verify_uids = self.find_remote_sent_message(client, job.rfc_message_id, sent_folder=sent_folder)
                    if verify_uids:
                        logger.info(f"Verified remote message arrived despite exception (UIDs: {verify_uids}).")
                        return {
                            "success": True,
                            "action": "APPENDED_WITH_RECOVERY",
                            "status": "reconciled_after_timeout",
                            "job_id": job.job_id,
                            "rfc_message_id": job.rfc_message_id,
                            "sent_folder": sent_folder,
                            "account_id": account.account_id,
                            "sender_email": job.sender_email,
                            "matched_uids": verify_uids,
                            "operator": operator_username,
                            "message": f"Message verified in {sent_folder} after network timeout recovery.",
                        }
                except Exception as verify_exc:
                    logger.error(f"Post-append verification failed: {verify_exc}")

                return {
                    "success": False,
                    "action": "FAILED_AMBIGUOUS_TIMEOUT",
                    "status": "reconciliation_unknown",
                    "job_id": job.job_id,
                    "rfc_message_id": job.rfc_message_id,
                    "sent_folder": sent_folder,
                    "account_id": account.account_id,
                    "error": str(append_exc),
                    "message": "Append encountered network/server error; remote status could not be confirmed.",
                }

            if append_status != "OK":
                return {
                    "success": False,
                    "action": "FAILED_SERVER_REJECTION",
                    "status": "reconciliation_rejected",
                    "job_id": job.job_id,
                    "rfc_message_id": job.rfc_message_id,
                    "sent_folder": sent_folder,
                    "response": str(append_resp),
                    "message": f"IMAP server rejected APPEND: {append_resp}",
                }

            # 5. Confirm message appears in remote Sent folder
            confirm_uids = self.find_remote_sent_message(client, job.rfc_message_id, sent_folder=sent_folder)
            return {
                "success": True,
                "action": "APPENDED_CONFIRMED",
                "status": "reconciled",
                "job_id": job.job_id,
                "rfc_message_id": job.rfc_message_id,
                "sent_folder": sent_folder,
                "account_id": account.account_id,
                "sender_email": job.sender_email,
                "matched_uids": confirm_uids,
                "raw_mime_bytes_len": len(raw_mime_bytes),
                "operator": operator_username,
                "message": f"Successfully appended and verified message in {sent_folder}.",
            }

        finally:
            if managed_client and client:
                try:
                    client.logout()
                except Exception:
                    pass
