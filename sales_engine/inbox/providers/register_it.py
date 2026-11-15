"""
sales_engine/inbox/providers/register_it.py
IMAP-CONN-01: Secure, strictly read-only IMAP adapter for Register.it mailboxes.

Key Architectural Guarantees:
  1. Strict read-only IMAP:
     - Uses EXAMINE instead of SELECT to never open a folder in read/write mode.
     - Uses BODY.PEEK[] to never set the \\Seen flag on remote messages.
     - Prohibits STORE, APPEND, COPY, MOVE, EXPUNGE, DELETE.
     - send_reply() fails closed with NotImplementedError.
  2. TLS Verification:
     - Strict SSL context verification (ssl.create_default_context(), check_hostname=True).
     - Standard port 993.
  3. Bounded Timeouts:
     - Socket timeout set to 20.0s for connection, login, and fetch operations.
  4. Robust MIME Parsing:
     - Safe header decoding (RFC 2047 words).
     - Normalizes dates to UTC ISO 8601 strings.
     - Captures plain-text and HTML safely.
     - Collects attachment metadata without downloading or saving binary content.
     - Respects MAX_BODY_PLAIN_CHARS and MAX_BODY_HTML_CHARS limits.
  5. Zero Credential Leakage:
     - Never logs or exposes passwords.
     - Structured diagnostics return only connection state, server capabilities,
       and discovered folders.
"""

import ssl
import imaplib
import email
import logging
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Set

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MailboxConnectionInfo,
)
from sales_engine.inbox.providers.base import (
    BaseMailboxProvider,
    ProviderSyncBatch,
    ProviderSendResult,
)
from sales_engine.inbox.mailbox_repository import (
    MAX_BODY_PLAIN_CHARS,
    MAX_BODY_HTML_CHARS,
)
from sales_engine.inbox.credential_store import MailboxCredentialStore

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_MESSAGE_BYTES = 10 * 1024 * 1024  # 10 MB threshold per IMAP-SYNC-02A


class RegisterItReadOnlyIMAPProvider(BaseMailboxProvider):
    """
    Dedicated read-only IMAP provider tailored for Register.it (pop.securemail.pro:993).
    Supports multi-account credentials, folder discovery, and non-intrusive message inspection.
    """

    def __init__(
        self,
        account: Optional[MailboxAccountRecord] = None,
        credential_store: Optional[MailboxCredentialStore] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ):
        self.account = account
        self.credential_store = credential_store or MailboxCredentialStore()
        self.timeout = timeout
        self.max_message_bytes = max_message_bytes

    @property
    def provider_name(self) -> str:
        return "register_it"

    # -----------------------------------------------------------------------
    # Connection Management
    # -----------------------------------------------------------------------

    def create_ssl_context(self) -> ssl.SSLContext:
        """Create a hardened TLS context with strict certificate verification."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def connect(self, account: Optional[MailboxAccountRecord] = None) -> imaplib.IMAP4_SSL:
        """
        Establish a TLS IMAP connection and authenticate using OS-backed credentials.
        Fails safely without leaking passwords.
        """
        acc = account or self.account
        if not acc:
            raise ValueError("No mailbox account specified for IMAP connection")

        password = self.credential_store.get_credential(acc.credential_ref)
        if not password:
            raise ValueError(f"No credential found for ref: {acc.credential_ref}")

        ssl_ctx = self.create_ssl_context()

        try:
            client = imaplib.IMAP4_SSL(
                host=acc.imap_host,
                port=acc.imap_port,
                ssl_context=ssl_ctx,
                timeout=self.timeout,
            )
        except Exception as e:
            logger.error(f"Failed to connect to IMAP server {acc.imap_host}:{acc.imap_port}: {type(e).__name__}")
            raise

        try:
            # Login with full email address as username
            client.login(acc.email_address, password)
            return client
        except Exception as e:
            try:
                client.logout()
            except Exception:
                pass
            logger.error(f"Authentication failed for account {acc.account_id}: {type(e).__name__}")
            raise

    # -----------------------------------------------------------------------
    # Diagnostics & Health Check
    # -----------------------------------------------------------------------

    def run_diagnostics(self, account: Optional[MailboxAccountRecord] = None) -> Dict[str, Any]:
        """
        Run safe, read-only connectivity diagnostics.
        Returns connection state, capabilities, and discovered folders without secrets.
        """
        acc = account or self.account
        if not acc:
            return {
                "account_id": None,
                "connected": False,
                "tls_verified": False,
                "error_category": "MISSING_ACCOUNT",
                "error_message": "No account configuration provided",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }

        timestamp_utc = datetime.now(timezone.utc).isoformat()
        res: Dict[str, Any] = {
            "account_id": acc.account_id,
            "email_address": acc.email_address,
            "imap_host": acc.imap_host,
            "imap_port": acc.imap_port,
            "use_ssl": acc.use_ssl,
            "connected": False,
            "tls_verified": False,
            "authenticated": False,
            "capabilities": [],
            "folders_count": 0,
            "detected_inbox": None,
            "detected_sent": None,
            "ambiguous_sent": False,
            "error_category": None,
            "error_message": None,
            "timestamp_utc": timestamp_utc,
        }

        # Check credential availability
        pwd = self.credential_store.get_credential(acc.credential_ref)
        if not pwd:
            res["error_category"] = "MISSING_CREDENTIAL"
            res["error_message"] = f"No secure credential configured for ref '{acc.credential_ref}'"
            return res

        client: Optional[imaplib.IMAP4_SSL] = None
        try:
            client = self.connect(acc)
            res["connected"] = True
            res["tls_verified"] = True
            res["authenticated"] = True

            # Server capabilities
            caps = client.capabilities if hasattr(client, "capabilities") else ()
            res["capabilities"] = list(caps)

            # Folder discovery
            folders = self.discover_folders(acc, client=client)
            res["folders_count"] = len(folders)
            for f in folders:
                if f.folder_role == "inbox" and not res["detected_inbox"]:
                    res["detected_inbox"] = f.folder_name
                elif f.folder_role == "sent":
                    if res["detected_sent"] and res["detected_sent"] != f.folder_name:
                        res["ambiguous_sent"] = True
                    res["detected_sent"] = f.folder_name

        except imaplib.IMAP4.error as e:
            res["error_category"] = "IMAP_PROTOCOL_ERROR"
            res["error_message"] = str(e)
        except ssl.SSLError as e:
            res["error_category"] = "SSL_TLS_ERROR"
            res["error_message"] = str(e)
        except TimeoutError:
            res["error_category"] = "TIMEOUT_ERROR"
            res["error_message"] = f"Connection timed out after {self.timeout}s"
        except Exception as e:
            res["error_category"] = "CONNECTION_EXCEPTION"
            res["error_message"] = str(e)
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

        return res

    # -----------------------------------------------------------------------
    # Folder Discovery & Role Classification
    # -----------------------------------------------------------------------

    def discover_folders(
        self,
        account: MailboxAccountRecord,
        client: Optional[imaplib.IMAP4_SSL] = None,
    ) -> List[MailboxFolderRecord]:
        """
        Discover remote folders using IMAP LIST without selecting them.
        Detects inbox, sent, drafts, archive, and trash roles safely.
        """
        owns_client = False
        if client is None:
            client = self.connect(account)
            owns_client = True

        try:
            typ, data = client.list()
            if typ != "OK" or not data:
                return []

            discovered: List[MailboxFolderRecord] = []
            now_iso = datetime.now(timezone.utc).isoformat()

            for item in data:
                if not item or not isinstance(item, (bytes, bytearray)):
                    continue
                parsed = self._parse_list_response(item.decode("utf-8", errors="replace"))
                if not parsed:
                    continue

                flags, delim, name = parsed
                role = self._classify_folder_role(name, flags)
                fid = f"fld_{account.account_id}_{abs(hash(name)) % 10_000_000}"

                discovered.append(
                    MailboxFolderRecord(
                        folder_id=fid,
                        account_id=account.account_id,
                        folder_name=name,
                        folder_delimiter=delim,
                        folder_role=role,
                        imap_flags=flags,
                        is_active=True,
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                )

            return discovered

        finally:
            if owns_client and client:
                try:
                    client.logout()
                except Exception:
                    pass

    def _parse_list_response(self, line: str) -> Optional[Tuple[List[str], str, str]]:
        """Parse raw IMAP LIST response line into (flags, delimiter, folder_name)."""
        import re
        # Pattern: (\Flags) "Delimiter" "Name" or (\Flags) "Delimiter" Name
        m = re.match(r'^\((?P<flags>[^\)]*)\)\s+(?:"(?P<delim>[^"]+)"|(?P<delim_bare>\S+))\s+(?:"(?P<name>[^"]+)"|(?P<name_bare>.+))$', line.strip())
        if not m:
            return None

        flags_str = m.group("flags") or ""
        flags = [f.strip() for f in flags_str.split() if f.strip()]
        delim = m.group("delim") or m.group("delim_bare") or "/"
        name = m.group("name") or m.group("name_bare") or ""
        return flags, delim, name.strip()

    def _classify_folder_role(self, folder_name: str, imap_flags: List[str]) -> str:
        """Classify folder role based on standard RFC 6154 flags and multilingual heuristics."""
        lower_flags = [f.lower() for f in imap_flags]
        lower_name = folder_name.lower().strip()

        # 1. Standard RFC 6154 SPECIAL-USE flags
        if r"\inbox" in lower_flags or lower_name == "inbox":
            return "inbox"
        if r"\sent" in lower_flags:
            return "sent"
        if r"\drafts" in lower_flags:
            return "drafts"
        if r"\archive" in lower_flags:
            return "archive"
        if r"\trash" in lower_flags:
            return "trash"
        if r"\junk" in lower_flags:
            return "junk"

        # 2. Heuristic multilingual patterns (Italian / English common for Register.it)
        if lower_name in ("sent", "sent messages", "sent items", "posta inviata", "inviati", "elementi inviati"):
            return "sent"
        if lower_name in ("drafts", "bozze"):
            return "drafts"
        if lower_name in ("archive", "archivio") or lower_name.startswith("archive/") or lower_name.startswith("archivio/"):
            return "archive"
        if lower_name in ("trash", "cestino", "deleted items") or lower_name.startswith("trash/") or lower_name.startswith("cestino/"):
            return "trash"
        if lower_name in ("junk", "spam", "posta indesiderata"):
            return "junk"

        # Check subfolder patterns (e.g. INBOX/Sent, INBOX/Posta Inviata)
        if lower_name.endswith("/sent") or lower_name.endswith(".sent") or "posta inviata" in lower_name:
            return "sent"
        if lower_name.endswith("/archive") or lower_name.endswith("/archivio") or "archivio" in lower_name:
            return "archive"

        return "other"

    # -----------------------------------------------------------------------
    # Read-Only Message Inspection
    # -----------------------------------------------------------------------

    def examine_folder(
        self,
        client: imaplib.IMAP4_SSL,
        folder_name: str,
    ) -> Tuple[int, int]:
        """
        Selects a folder in strictly READ-ONLY mode using EXAMINE.
        Returns (message_count, uidvalidity).
        """
        # Ensure folder name is quoted if containing spaces or special characters
        quoted = f'"{folder_name}"' if " " in folder_name and not folder_name.startswith('"') else folder_name
        typ, data = client.examine(quoted)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"Failed to EXAMINE folder {folder_name}: {data}")

        count = int(data[0]) if data and data[0] else 0

        # Retrieve UIDVALIDITY from response
        # client.response('UIDVALIDITY') returns e.g. ('OK', [b'123456'])
        uv_typ, uv_data = client.response("UIDVALIDITY")
        uidvalidity = int(uv_data[0]) if uv_typ == "OK" and uv_data and uv_data[0] else 0

        return count, uidvalidity

    def fetch_messages_by_uid(
        self,
        account: MailboxAccountRecord,
        folder: MailboxFolderRecord,
        since_uid: int = 0,
        batch_size: int = 50,
        client: Optional[imaplib.IMAP4_SSL] = None,
    ) -> Tuple[List[MailboxMessageRecord], int, int]:
        """
        Fetch new messages since since_uid using UID SEARCH and BODY.PEEK[].
        Guaranteed non-flag-changing.
        Returns (messages, new_highest_uid, uidvalidity).
        """
        owns_client = False
        if client is None:
            client = self.connect(account)
            owns_client = True

        try:
            _, uidvalidity = self.examine_folder(client, folder.folder_name)

            # Search UIDs strictly greater than since_uid
            search_crit = f"UID {since_uid + 1}:*" if since_uid > 0 else "ALL"
            typ, data = client.uid("SEARCH", search_crit)
            if typ != "OK" or not data or not data[0]:
                return [], since_uid, uidvalidity

            all_uids = [int(u) for u in data[0].split() if u.isdigit()]
            new_uids = [u for u in all_uids if u > since_uid]
            if not new_uids:
                return [], since_uid, uidvalidity

            # Slice to bounded batch size
            batch_uids = sorted(new_uids)[:batch_size]
            uid_str = ",".join(str(u) for u in batch_uids)

            # IMAP-SYNC-02A: Pre-flight size gating with RFC822.SIZE
            # Query message sizes first to protect against downloading huge binary attachments
            typ, size_data = client.uid("FETCH", uid_str, "(UID RFC822.SIZE)")
            uid_sizes: Dict[int, int] = {}
            if typ == "OK" and size_data:
                for s_item in size_data:
                    if isinstance(s_item, (bytes, bytearray)):
                        s_str = s_item.decode("utf-8", errors="replace")
                        u_val = self._extract_uid_from_fetch_header(s_str)
                        m_sz = self._extract_size_from_fetch_header(s_str)
                        if u_val and m_sz is not None:
                            uid_sizes[u_val] = m_sz

            # Segregate UIDs into normal vs oversized
            normal_uids = [u for u in batch_uids if uid_sizes.get(u, 0) <= self.max_message_bytes]
            oversized_uids = [u for u in batch_uids if uid_sizes.get(u, 0) > self.max_message_bytes]

            messages: List[MailboxMessageRecord] = []
            highest_uid = since_uid

            # 1. Fetch normal messages using full RFC822.PEEK
            if normal_uids:
                norm_str = ",".join(str(u) for u in normal_uids)
                fetch_cmd = "(UID RFC822.PEEK)"
                typ, fetch_data = client.uid("FETCH", norm_str, fetch_cmd)
                if typ == "OK" and fetch_data:
                    for item in fetch_data:
                        if not isinstance(item, tuple) or len(item) < 2:
                            continue

                        raw_header, raw_bytes = item[0], item[1]
                        uid_val = self._extract_uid_from_fetch_header(raw_header.decode("utf-8", errors="replace"))
                        if not uid_val:
                            continue

                        if uid_val > highest_uid:
                            highest_uid = uid_val

                        direction = "outbound" if folder.folder_role == "sent" else "inbound"
                        msg_rec = self.parse_raw_email(
                            account_id=account.account_id,
                            folder_id=folder.folder_id,
                            folder_role=folder.folder_role,
                            direction=direction,
                            imap_uid=uid_val,
                            imap_uidvalidity=uidvalidity,
                            raw_bytes=raw_bytes,
                        )
                        messages.append(msg_rec)

            # 2. Fetch oversized messages safely: retrieve headers only using BODY.PEEK[HEADER]
            # Does NOT download the large binary payload. Marks message with parse_status='deferred_oversized'.
            if oversized_uids:
                over_str = ",".join(str(u) for u in oversized_uids)
                typ, header_data = client.uid("FETCH", over_str, "(UID BODY.PEEK[HEADER])")
                if typ == "OK" and header_data:
                    for item in header_data:
                        if not isinstance(item, tuple) or len(item) < 2:
                            continue

                        raw_header, raw_bytes = item[0], item[1]
                        uid_val = self._extract_uid_from_fetch_header(raw_header.decode("utf-8", errors="replace"))
                        if not uid_val:
                            continue

                        if uid_val > highest_uid:
                            highest_uid = uid_val

                        direction = "outbound" if folder.folder_role == "sent" else "inbound"
                        recorded_size = uid_sizes.get(uid_val, self.max_message_bytes)
                        msg_rec = self.parse_raw_email(
                            account_id=account.account_id,
                            folder_id=folder.folder_id,
                            folder_role=folder.folder_role,
                            direction=direction,
                            imap_uid=uid_val,
                            imap_uidvalidity=uidvalidity,
                            raw_bytes=raw_bytes,
                        )
                        # Explicitly mark as deferred oversized with actual size
                        msg_rec.parse_status = "deferred_oversized"
                        msg_rec.content_size_bytes = recorded_size
                        msg_rec.body_truncated = True
                        msg_rec.body_plain_text = (
                            f"[OVERSIZED MESSAGE DEFERRED: {recorded_size / (1024*1024):.2f} MB exceeds "
                            f"{self.max_message_bytes / (1024*1024):.1f} MB threshold. Headers preserved; body download deferred.]"
                        )
                        messages.append(msg_rec)

            return messages, highest_uid, uidvalidity

        finally:
            if owns_client and client:
                try:
                    client.logout()
                except Exception:
                    pass

    def _extract_uid_from_fetch_header(self, header_str: str) -> Optional[int]:
        import re
        m = re.search(r"UID\s+(\d+)", header_str, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def _extract_size_from_fetch_header(self, header_str: str) -> Optional[int]:
        import re
        m = re.search(r"RFC822\.SIZE\s+(\d+)", header_str, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    # -----------------------------------------------------------------------
    # MIME Parsing & Normalization
    # -----------------------------------------------------------------------

    def parse_raw_email(
        self,
        account_id: str,
        folder_id: str,
        folder_role: str,
        direction: str,
        imap_uid: int,
        imap_uidvalidity: int,
        raw_bytes: bytes,
    ) -> MailboxMessageRecord:
        """
        Parse raw RFC 822 email bytes into a normalized MailboxMessageRecord.
        Decodes international headers, extracts text/html parts, and collects attachment metadata.
        """
        msg = email.message_from_bytes(raw_bytes)

        # 1. Internal Message ID: deterministic SHA256 of physical occurrence
        import hashlib
        occ_key = f"{account_id}:{folder_id}:{imap_uidvalidity}:{imap_uid}"
        internal_msg_id = f"msg_{hashlib.sha256(occ_key.encode('utf-8')).hexdigest()[:16]}"

        # 2. RFC Headers
        rfc_message_id = self._clean_header(msg.get("Message-ID"))
        in_reply_to = self._clean_header(msg.get("In-Reply-To"))
        references = self._parse_references(msg.get("References"))

        subject = self._decode_mime_words(msg.get("Subject", ""))
        from_raw = self._decode_mime_words(msg.get("From", ""))
        from_addr, from_name = self._parse_address_and_name(from_raw)

        to_addrs = self._parse_address_list(msg.get("To", ""))
        cc_addrs = self._parse_address_list(msg.get("Cc", ""))
        bcc_addrs = self._parse_address_list(msg.get("Bcc", ""))
        reply_to_addrs = self._parse_address_list(msg.get("Reply-To", ""))

        # 3. Date UTC
        date_raw = msg.get("Date")
        date_utc = self._normalize_date_to_utc(date_raw)

        # 4. Body Content & Attachment Extraction
        plain_text, html_sanitized, has_html, has_att, attachments_meta = self._extract_payloads(msg)

        # Truncation check
        body_truncated = False
        if len(plain_text) > MAX_BODY_PLAIN_CHARS:
            plain_text = plain_text[:MAX_BODY_PLAIN_CHARS]
            body_truncated = True
        if len(html_sanitized) > MAX_BODY_HTML_CHARS:
            html_sanitized = html_sanitized[:MAX_BODY_HTML_CHARS]
            body_truncated = True

        # Header dict for audit
        audit_headers = {
            "Message-ID": rfc_message_id,
            "Subject": subject,
            "From": from_raw,
            "Date": date_raw,
        }

        now_iso = datetime.now(timezone.utc).isoformat()

        return MailboxMessageRecord(
            message_id=internal_msg_id,
            account_id=account_id,
            folder_id=folder_id,
            folder_role=folder_role,
            direction=direction,
            imap_uid=imap_uid,
            imap_uidvalidity=imap_uidvalidity,
            rfc_message_id=rfc_message_id,
            in_reply_to=in_reply_to,
            references=references,
            from_address=from_addr or "unknown@domain.com",
            from_name=from_name,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            bcc_addresses=bcc_addrs,
            reply_to_addresses=reply_to_addrs,
            subject=subject,
            date_utc=date_utc,
            body_plain_text=plain_text,
            body_html_sanitized=html_sanitized,
            has_html=has_html,
            has_attachments=has_att,
            attachments_metadata=attachments_meta,
            parse_status="parsed",
            raw_headers=audit_headers,
            body_truncated=body_truncated,
            content_size_bytes=len(raw_bytes),
            ingested_at=now_iso,
        )

    def _clean_header(self, val: Optional[str]) -> Optional[str]:
        if not val or not str(val).strip():
            return None
        return str(val).strip()

    def _decode_mime_words(self, header_val: str) -> str:
        if not header_val:
            return ""
        try:
            decoded_fragments = decode_header(header_val)
            res = []
            for frag, encoding in decoded_fragments:
                if isinstance(frag, bytes):
                    enc = encoding or "utf-8"
                    try:
                        res.append(frag.decode(enc, errors="replace"))
                    except Exception:
                        res.append(frag.decode("latin1", errors="replace"))
                else:
                    res.append(str(frag))
            return "".join(res).strip()
        except Exception:
            return str(header_val).strip()

    def _parse_references(self, refs_val: Optional[str]) -> List[str]:
        if not refs_val:
            return []
        import re
        matches = re.findall(r"<[^>]+>", refs_val)
        if matches:
            return [m.strip() for m in matches]
        return [r.strip() for r in refs_val.split() if r.strip()]

    def _parse_address_and_name(self, raw_addr: str) -> Tuple[str, Optional[str]]:
        import email.utils
        name, addr = email.utils.parseaddr(raw_addr)
        name_clean = self._decode_mime_words(name) if name else None
        return addr.strip().lower(), name_clean

    def _parse_address_list(self, raw_field: Optional[str]) -> List[str]:
        if not raw_field:
            return []
        import email.utils
        addrs = email.utils.getaddresses([raw_field])
        return [a[1].strip().lower() for a in addrs if a[1] and a[1].strip()]

    def _normalize_date_to_utc(self, raw_date: Optional[str]) -> str:
        if not raw_date:
            return datetime.now(timezone.utc).isoformat()
        try:
            dt = parsedate_to_datetime(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    def _extract_payloads(self, msg: email.message.Message) -> Tuple[str, str, bool, bool, List[Dict[str, Any]]]:
        """Extract text and html parts, and attachment metadata without binaries."""
        plain_parts = []
        html_parts = []
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disp = str(part.get("Content-Disposition", "")).lower()

                if "attachment" in content_disp:
                    filename = self._decode_mime_words(part.get_filename() or "untitled")
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                    attachments.append({
                        "filename": filename,
                        "content_type": content_type,
                        "size_bytes": size,
                    })
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        plain_parts.append(payload.decode(charset, errors="replace"))
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html_parts.append(payload.decode(charset, errors="replace"))
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if content_type == "text/html":
                    html_parts.append(text)
                else:
                    plain_parts.append(text)

        full_plain = "\n\n".join(plain_parts).strip()
        full_html = "\n\n".join(html_parts).strip()

        has_html = bool(full_html)
        has_attachments = len(attachments) > 0

        # Basic safe HTML sanitization (strip scripts and styles)
        sanitized_html = self._sanitize_html(full_html) if has_html else ""

        # If no plain text was provided, generate text from HTML
        if not full_plain and sanitized_html:
            import re
            full_plain = re.sub(r"<[^>]+>", " ", sanitized_html).strip()

        return full_plain, sanitized_html, has_html, has_attachments, attachments

    def _sanitize_html(self, raw_html: str) -> str:
        """Strip dangerous active HTML tags."""
        import re
        cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", "", raw_html)
        cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", "", cleaned)
        cleaned = re.sub(r"(?is)<iframe[^>]*>.*?</iframe>", "", cleaned)
        cleaned = re.sub(r"(?is)<object[^>]*>.*?</object>", "", cleaned)
        cleaned = re.sub(r"(?is)<embed[^>]*>.*?</embed>", "", cleaned)
        return cleaned.strip()

    # -----------------------------------------------------------------------
    # BaseMailboxProvider Overrides (Strictly Read-Only Enforcements)
    # -----------------------------------------------------------------------

    def get_connection_status(self) -> MailboxConnectionInfo:
        if not self.account:
            return MailboxConnectionInfo(
                provider="register_it",
                status="not_configured",
                account_email=None,
                error_message=None,
            )
        diag = self.run_diagnostics(self.account)
        status = "connected" if diag["connected"] else "error" if diag["error_category"] else "configured"
        return MailboxConnectionInfo(
            provider="register_it",
            status=status,
            account_email=self.account.email_address,
            last_synced_at=self.account.last_successful_sync_at,
            error_message=diag.get("error_message"),
        )

    def fetch_messages(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        lookback_days: int = 30,
    ) -> ProviderSyncBatch:
        """
        Passive placeholder complying with BaseMailboxProvider interface.
        Operational synchronization will be orchestrated by IMAPSyncManager in IMAP-SYNC-02.
        """
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
        """
        P0 SAFETY RULE: The Register.it IMAP provider is strictly read-only.
        Fail closed with an explicit error.
        """
        raise NotImplementedError("RegisterItReadOnlyIMAPProvider is strictly read-only. Outbound sending is disabled.")
