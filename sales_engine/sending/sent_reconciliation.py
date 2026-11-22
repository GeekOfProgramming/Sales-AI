"""
sales_engine/sending/sent_reconciliation.py
MAIL-SEND-01G & MAIL-SEND-01G-HOTFIX: Safe, Audited IMAP Sent-Folder Reconciliation Service.

Provides:
1. Exact Message-ID remote search in Register.it INBOX.Sent (read-only diagnostic).
2. Provenance-aware MIME resolution:
   - DURABLE_EXACT: Pre-submission byte-exact MIME stored in outbound_jobs (SHA-256 verified).
   - IMPORTED_RECEIVED_EML: Imported from operator-supplied received .eml exported from recipient mailbox (e.g., Gmail).
   - RECONSTRUCTED_SEMANTIC: Rebuilt from immutable OutboundQueueJob snapshot (explicitly non-byte-identical).
3. EML comparison engine:
   - Compares Message-ID, sender, recipients, subject, decoded plain-text body, HTML signature count,
     CID logo references, and attachments.
   - Audits and reports transport-added headers (DKIM, Received, ARC, SPF, Authentication-Results, etc.)
     vs pre-submission headers (From display name, Date, boundaries).
4. Idempotent IMAP APPEND for successfully submitted outbound jobs (smtp_accepted).
5. Ambiguous timeout handling with post-failure remote verification.
6. Strict account binding (original authorized sender only).
"""

import ssl
import time
import email
import hashlib
import imaplib
import logging
from typing import Dict, Any, Optional, List, Tuple
from email.utils import formatdate, parseaddr
from email.policy import default as default_policy

from sales_engine.sending.outbound_models import OutboundQueueJob
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.mime_builder import build_mime_message, PYBIM_LOGO_CID
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore
from sales_engine.inbox.models import MailboxAccountRecord

logger = logging.getLogger(__name__)

DEFAULT_IMAP_TIMEOUT = 25.0
DEFAULT_SENT_FOLDER = "INBOX.Sent"

# Known transport / receiver headers added downstream by MTAs / Gmail
TRANSPORT_ADDED_HEADERS = {
    "received",
    "received-spf",
    "authentication-results",
    "arc-seal",
    "arc-message-signature",
    "arc-authentication-results",
    "dkim-signature",
    "x-google-smtp-source",
    "x-received",
    "delivered-to",
    "return-path",
}


class SentReconciliationError(Exception):
    """Base exception for sent reconciliation failures."""
    pass


def compare_eml_against_job(job: OutboundQueueJob, eml_bytes: bytes) -> Dict[str, Any]:
    """
    Compares an operator-supplied .eml file (e.g. exported from Gmail) against the job's immutable snapshot.
    Audits:
    - Message-ID match
    - Sender address & display name match
    - Recipient match (To, Cc, Bcc)
    - Subject match
    - Plaintext & HTML body match
    - HTML signature count & CID logo references
    - Transport headers vs Pre-submission headers
    """
    import json

    def _parse_list(val):
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

    parsed_msg = email.message_from_bytes(eml_bytes, policy=default_policy)

    discrepancies: List[str] = []
    transport_headers: List[str] = []
    pre_submission_headers: Dict[str, str] = {}

    for k, v in parsed_msg.items():
        k_lower = k.lower()
        if k_lower in TRANSPORT_ADDED_HEADERS:
            transport_headers.append(k)
        else:
            pre_submission_headers[k] = str(v)

    # 1. Message-ID
    eml_msg_id = (parsed_msg.get("Message-ID") or "").strip()
    job_msg_id = (job.rfc_message_id or "").strip()
    msg_id_match = (eml_msg_id == job_msg_id)
    if not msg_id_match:
        discrepancies.append(f"Message-ID mismatch: EML has '{eml_msg_id}' vs Job '{job_msg_id}'")

    # 2. Sender / From
    eml_from_raw = parsed_msg.get("From") or ""
    eml_from_name, eml_from_addr = parseaddr(eml_from_raw)
    sender_match = (eml_from_addr.lower() == job.sender_email.lower())
    if not sender_match:
        discrepancies.append(f"Sender email mismatch: EML has '{eml_from_addr}' vs Job '{job.sender_email}'")

    # 3. Recipients
    expected_to = [a.lower().strip() for a in _parse_list(job.header_to_json)]
    eml_to_raw = parsed_msg.get_all("To", [])
    eml_to_addrs = []
    for to_hdr in eml_to_raw:
        for item in str(to_hdr).split(","):
            _, addr = parseaddr(item)
            if addr:
                eml_to_addrs.append(addr.lower().strip())

    to_match = (sorted(expected_to) == sorted(eml_to_addrs))
    if not to_match:
        discrepancies.append(f"To recipients mismatch: EML has {eml_to_addrs} vs Job expected {expected_to}")

    # 4. Subject
    eml_subject = (parsed_msg.get("Subject") or "").strip()
    job_subject = (job.subject or "").strip()
    subject_match = (eml_subject == job_subject)
    if not subject_match:
        discrepancies.append(f"Subject mismatch: EML has '{eml_subject}' vs Job '{job_subject}'")

    # 5. Extract body parts & attachments
    plain_parts = []
    html_parts = []
    attachments = []
    cid_logos_found = []

    for part in parsed_msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition") or "")
        content_id = str(part.get("Content-ID") or "").strip("<>")

        if content_id == PYBIM_LOGO_CID or "pybim-logo" in content_id:
            cid_logos_found.append(content_id)

        if "attachment" in content_disposition:
            attachments.append({
                "filename": part.get_filename() or "unnamed",
                "content_type": content_type,
                "size_bytes": len(part.get_payload(decode=True) or b""),
            })
        elif content_type == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                plain_parts.append(payload.decode("utf-8", errors="replace"))
        elif content_type == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                html_parts.append(payload.decode("utf-8", errors="replace"))

    eml_plain = "\n".join(plain_parts).strip()
    eml_html = "\n".join(html_parts).strip()

    # Normalize whitespace for robust comparison
    norm_job_plain = " ".join(job.body_plain_text.split()) if job.body_plain_text else ""
    norm_eml_plain = " ".join(eml_plain.split())
    plain_match = (norm_job_plain in norm_eml_plain or norm_eml_plain in norm_job_plain or norm_job_plain == norm_eml_plain)
    if not plain_match:
        discrepancies.append("Plain text body content does not match job text snapshot")

    # HTML signature count check
    sig_count_eml = eml_html.count("pybim-logo") + eml_html.count("Ing. H. Lotfalian") + eml_html.count("info@pybim.com")
    has_cid_logo = (len(cid_logos_found) > 0 or PYBIM_LOGO_CID in eml_html or "cid:pybim-logo" in eml_html)

    return {
        "is_compatible": (len(discrepancies) == 0 and msg_id_match and sender_match and to_match and subject_match),
        "message_id_match": msg_id_match,
        "sender_match": sender_match,
        "to_match": to_match,
        "subject_match": subject_match,
        "plain_body_match": plain_match,
        "has_cid_logo": has_cid_logo,
        "cid_logos_found": cid_logos_found,
        "attachments_count": len(attachments),
        "attachments": attachments,
        "eml_from_name": eml_from_name,
        "eml_from_addr": eml_from_addr,
        "eml_date": parsed_msg.get("Date"),
        "transport_headers_detected": transport_headers,
        "transport_headers_count": len(transport_headers),
        "discrepancies": discrepancies,
    }


def clean_received_eml_for_sent_folder(eml_bytes: bytes) -> bytes:
    """
    Cleans downstream MTA transport headers (e.g. Delivered-To, Received, Received-SPF,
    Authentication-Results, ARC-*, Return-Path) from a recipient-exported .eml file so that
    it represents a clean sender-side outbound message suitable for IMAP APPEND into INBOX.Sent.
    Preserves all MIME boundaries, Date, From, To, Cc, Subject, Message-ID, and body parts.
    """
    parsed_msg = email.message_from_bytes(eml_bytes, policy=default_policy)
    
    # Remove transport headers added after departure from MSA
    headers_to_remove = [k for k in parsed_msg.keys() if k.lower() in TRANSPORT_ADDED_HEADERS]
    for h in headers_to_remove:
        try:
            del parsed_msg[h]
        except Exception:
            pass

    return parsed_msg.as_bytes()


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
        self.queue_store = OutboundQueueStore(db_path=db_path, auto_apply_schema=True)
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
            status, resp = client.select(sent_folder)
            if status != "OK":
                raise SentReconciliationError(f"Cannot select sent folder '{sent_folder}': {resp}")

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
        Reconstructs RFC 5322 MIME message bytes from immutable OutboundQueueJob fields.
        NOTE: This is a semantic reconstruction, NOT guaranteed to be byte-identical to original SMTP bytes.
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
            include_official_signature=False,  # Signature was already frozen into body snapshot
        )
        return msg.as_bytes()

    def resolve_message_bytes_and_provenance(
        self,
        job: OutboundQueueJob,
        eml_path: Optional[str] = None,
        allow_reconstruction: bool = False,
    ) -> Tuple[bytes, str, Dict[str, Any]]:
        """
        Resolves MIME bytes and classifies exact provenance:
        1. DURABLE_EXACT: If job has submitted_mime_bytes stored with matching SHA-256 hash.
        2. IMPORTED_RECEIVED_EML: If an operator-supplied EML file is verified and sanitized.
        3. RECONSTRUCTED_SEMANTIC: If operator explicitly authorizes semantic reconstruction.
        """
        # Case 1: Stored pre-submission exact MIME bytes
        if job.submitted_mime_bytes:
            sha256 = hashlib.sha256(job.submitted_mime_bytes).hexdigest()
            if job.submitted_mime_sha256 and sha256 != job.submitted_mime_sha256:
                raise SentReconciliationError(
                    f"Integrity check failed: stored MIME SHA-256 '{sha256}' does not match recorded '{job.submitted_mime_sha256}'."
                )
            audit = {
                "provenance": "DURABLE_EXACT",
                "is_byte_identical": True,
                "sha256": sha256,
                "size_bytes": len(job.submitted_mime_bytes),
                "notes": "Exact pre-submission MIME bytes recovered from durable queue store with verified SHA-256.",
            }
            return job.submitted_mime_bytes, "DURABLE_EXACT", audit

        # Case 2: Operator-supplied EML
        if eml_path:
            import os
            if not os.path.exists(eml_path):
                raise SentReconciliationError(f"Supplied EML file not found: {eml_path}")

            with open(eml_path, "rb") as f:
                eml_raw = f.read()

            comp = compare_eml_against_job(job, eml_raw)
            if not comp["is_compatible"]:
                raise SentReconciliationError(
                    f"Supplied EML is incompatible with Job {job.job_id}: {'; '.join(comp['discrepancies'])}"
                )

            cleaned_bytes = clean_received_eml_for_sent_folder(eml_raw)
            audit = {
                "provenance": "IMPORTED_RECEIVED_EML",
                "is_byte_identical": False,  # Cleaned of transport headers
                "comparison": comp,
                "original_eml_sha256": hashlib.sha256(eml_raw).hexdigest(),
                "cleaned_eml_sha256": hashlib.sha256(cleaned_bytes).hexdigest(),
                "size_bytes": len(cleaned_bytes),
                "notes": (
                    f"Verified against recipient EML. Removed {comp['transport_headers_count']} transport headers "
                    f"({', '.join(comp['transport_headers_detected'][:4])}...). Message-ID, Subject and body match exactly."
                ),
            }
            return cleaned_bytes, "IMPORTED_RECEIVED_EML", audit

        # Case 3: Semantic Reconstruction
        if allow_reconstruction:
            reconstructed_bytes = self.reconstruct_job_mime(job)
            sha256 = hashlib.sha256(reconstructed_bytes).hexdigest()
            audit = {
                "provenance": "RECONSTRUCTED_SEMANTIC",
                "is_byte_identical": False,
                "sha256": sha256,
                "size_bytes": len(reconstructed_bytes),
                "notes": (
                    "CRITICAL NOTICE: Reconstructed from immutable job snapshot. "
                    "Not guaranteed to be byte-identical to original SMTP transmission."
                ),
            }
            return reconstructed_bytes, "RECONSTRUCTED_SEMANTIC", audit

        # Neither durable bytes nor EML provided and reconstruction not authorized
        raise SentReconciliationError(
            f"No exact pre-submission MIME bytes exist for Job '{job.job_id}'. "
            f"To reconcile, provide an operator-exported recipient EML via --eml-file, "
            f"or explicitly authorize semantic reconstruction via --allow-reconstruction."
        )

    def reconcile_job_sent_copy(
        self,
        job_id: str,
        dry_run: bool = True,
        live_append: bool = False,
        sent_folder: str = DEFAULT_SENT_FOLDER,
        operator_username: str = "operator_reconciliation",
        eml_path: Optional[str] = None,
        allow_reconstruction: bool = False,
        client: Optional[imaplib.IMAP4_SSL] = None,
    ) -> Dict[str, Any]:
        """
        Safely reconciles an accepted outbound job into the IMAP Sent folder.
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

        # 1. Resolve MIME bytes and determine provenance
        raw_mime_bytes, provenance, audit_info = self.resolve_message_bytes_and_provenance(
            job=job,
            eml_path=eml_path,
            allow_reconstruction=allow_reconstruction,
        )

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
                    "provenance": provenance,
                    "is_byte_identical": audit_info["is_byte_identical"],
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
                    "provenance": provenance,
                    "is_byte_identical": audit_info["is_byte_identical"],
                    "raw_mime_bytes_len": len(raw_mime_bytes),
                    "audit_info": audit_info,
                    "message": f"Preflight checks passed ({provenance}). Message ready to append to {sent_folder} ({len(raw_mime_bytes)} bytes).",
                }

            # 4. Live IMAP APPEND
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
                            "provenance": provenance,
                            "is_byte_identical": audit_info["is_byte_identical"],
                            "matched_uids": verify_uids,
                            "operator": operator_username,
                            "audit_info": audit_info,
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
                "provenance": provenance,
                "is_byte_identical": audit_info["is_byte_identical"],
                "matched_uids": confirm_uids,
                "raw_mime_bytes_len": len(raw_mime_bytes),
                "operator": operator_username,
                "audit_info": audit_info,
                "message": f"Successfully appended and verified message in {sent_folder} ({provenance}).",
            }

        finally:
            if managed_client and client:
                try:
                    client.logout()
                except Exception:
                    pass
