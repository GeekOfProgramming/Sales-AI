"""
sales_engine/inbox/mailbox_repository.py
MAIL-DATA-01: SQLite storage foundation for read-only multi-mailbox integration.

This module provides the low-level data layer and safe migrations for the five
new staging structures defined in MAIL-ARCH-01:
  1. mailbox_accounts
  2. mailbox_folders
  3. mailbox_folder_sync_state
  4. mailbox_messages
  5. message_crm_links

CRITICAL ARCHITECTURAL CONSTRAINTS:
  - Additive migrations only (CREATE TABLE/INDEX IF NOT EXISTS). Zero DROP TABLE.
  - Zero modifications to candidate_companies, candidate_contacts, historical_sent_emails,
    or manual_outreach_log.
  - Ingestion staging ONLY: inserting or retrieving mailbox messages causes ZERO CRM status mutations.
  - Strict bounded body sizes to avoid unbounded database growth (max 1MB plaintext, max 2MB html).
  - Passwords and secrets are NEVER stored or logged.
"""

import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxFolderSyncStateRecord,
    MailboxMessageRecord,
    MessageCrmLinkRecord,
)

logger = logging.getLogger(__name__)

# Bounded storage limits for email body evidence
MAX_BODY_PLAIN_CHARS = 1_000_000  # ~1 MB text
MAX_BODY_HTML_CHARS = 2_000_000   # ~2 MB sanitized HTML


def get_default_inbox_db_path() -> Path:
    env_path = os.getenv("SALES_OUTREACH_DB") or os.getenv("SALESAI_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path("data/sales_outreach.db")


class MailboxRepository:
    """
    Authoritative SQLite repository for multi-mailbox staging evidence.
    Operates in WAL mode with strict foreign key constraints and transactional integrity.
    """

    SCHEMA_VERSION = "mail_data_01_v1"

    def __init__(self, db_path: Optional[Union[Path, str]] = None, auto_migrate: bool = False):
        self.db_path = Path(db_path) if db_path else get_default_inbox_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if auto_migrate:
            self.apply_migrations()

    def get_connection(self) -> sqlite3.Connection:
        """Provide a configured SQLite connection with foreign keys enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # -----------------------------------------------------------------------
    # Migrations
    # -----------------------------------------------------------------------

    def apply_migrations(self) -> Dict[str, Any]:
        """
        Execute safe, idempotent additive migrations creating the five foundation tables.
        Returns migration status and table verification details.
        """
        with self._lock, self.get_connection() as conn:
            # 1. Accounts
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mailbox_accounts (
                    account_id TEXT PRIMARY KEY,
                    email_address TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'register_it',
                    imap_host TEXT NOT NULL DEFAULT 'pop.securemail.pro',
                    imap_port INTEGER NOT NULL DEFAULT 993,
                    use_ssl INTEGER NOT NULL DEFAULT 1,
                    credential_ref TEXT NOT NULL,
                    is_enabled INTEGER NOT NULL DEFAULT 0,
                    sync_priority INTEGER NOT NULL DEFAULT 10,
                    connection_status TEXT NOT NULL DEFAULT 'configured',
                    last_successful_sync_at TEXT,
                    last_error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_acc_email ON mailbox_accounts(email_address);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_acc_enabled ON mailbox_accounts(is_enabled);")

            # 2. Folders
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mailbox_folders (
                    folder_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    folder_name TEXT NOT NULL,
                    folder_delimiter TEXT NOT NULL DEFAULT '/',
                    folder_role TEXT NOT NULL DEFAULT 'other',
                    imap_flags_json TEXT DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_uidvalidity INTEGER,
                    last_seen_uid INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES mailbox_accounts(account_id) ON DELETE CASCADE,
                    UNIQUE(account_id, folder_name)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_fld_acc ON mailbox_folders(account_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_fld_role ON mailbox_folders(folder_role);")

            # 3. Folder Sync State (Checkpoints)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mailbox_folder_sync_state (
                    sync_state_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    folder_id TEXT NOT NULL,
                    uidvalidity INTEGER NOT NULL,
                    highest_modseq INTEGER,
                    last_synced_uid INTEGER NOT NULL DEFAULT 0,
                    last_sync_started_at TEXT,
                    last_sync_completed_at TEXT,
                    last_status TEXT NOT NULL DEFAULT 'idle',
                    messages_fetched_total INTEGER DEFAULT 0,
                    last_error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES mailbox_accounts(account_id) ON DELETE CASCADE,
                    FOREIGN KEY (folder_id) REFERENCES mailbox_folders(folder_id) ON DELETE CASCADE,
                    UNIQUE(account_id, folder_id)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_sync_acc_fld ON mailbox_folder_sync_state(account_id, folder_id);")

            # 4. Mailbox Messages (Immutable Raw Evidence)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mailbox_messages (
                    message_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    folder_id TEXT NOT NULL,
                    folder_role TEXT NOT NULL DEFAULT 'other',
                    direction TEXT NOT NULL,
                    imap_uid INTEGER NOT NULL,
                    imap_uidvalidity INTEGER NOT NULL,
                    rfc_message_id TEXT,
                    in_reply_to TEXT,
                    references_json TEXT DEFAULT '[]',
                    from_address TEXT NOT NULL,
                    from_name TEXT,
                    to_addresses_json TEXT NOT NULL DEFAULT '[]',
                    cc_addresses_json TEXT DEFAULT '[]',
                    bcc_addresses_json TEXT DEFAULT '[]',
                    reply_to_addresses_json TEXT DEFAULT '[]',
                    subject TEXT,
                    date_utc TEXT NOT NULL,
                    body_plain_text TEXT,
                    body_html_sanitized TEXT,
                    has_html INTEGER DEFAULT 0,
                    has_attachments INTEGER DEFAULT 0,
                    attachments_meta_json TEXT DEFAULT '[]',
                    parse_status TEXT NOT NULL DEFAULT 'parsed',
                    raw_headers_json TEXT DEFAULT '{}',
                    body_truncated INTEGER DEFAULT 0,
                    content_size_bytes INTEGER DEFAULT 0,
                    ingested_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES mailbox_accounts(account_id) ON DELETE CASCADE,
                    FOREIGN KEY (folder_id) REFERENCES mailbox_folders(folder_id) ON DELETE CASCADE,
                    UNIQUE(account_id, folder_id, imap_uidvalidity, imap_uid)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_msg_acc_fld ON mailbox_messages(account_id, folder_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_msg_rfc ON mailbox_messages(rfc_message_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_msg_irt ON mailbox_messages(in_reply_to);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_msg_from ON mailbox_messages(from_address);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mb_msg_date ON mailbox_messages(date_utc DESC);")

            # 5. Message CRM Links (Decoupled Policy / Linking Layer)
            # Candidate foreign key is enforced if candidate_companies exists
            has_cand_table = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_companies'"
            ).fetchone())

            if has_cand_table:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS message_crm_links (
                        link_id TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL,
                        candidate_id TEXT,
                        contact_id TEXT,
                        link_status TEXT NOT NULL DEFAULT 'suggested_pending_review',
                        link_method TEXT NOT NULL DEFAULT 'manual_operator',
                        link_confidence REAL NOT NULL DEFAULT 0.0,
                        evidence_summary TEXT,
                        verified_by TEXT,
                        verified_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (message_id) REFERENCES mailbox_messages(message_id) ON DELETE CASCADE,
                        FOREIGN KEY (candidate_id) REFERENCES candidate_companies(candidate_id) ON DELETE SET NULL,
                        UNIQUE(message_id, candidate_id)
                    );
                """)
            else:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS message_crm_links (
                        link_id TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL,
                        candidate_id TEXT,
                        contact_id TEXT,
                        link_status TEXT NOT NULL DEFAULT 'suggested_pending_review',
                        link_method TEXT NOT NULL DEFAULT 'manual_operator',
                        link_confidence REAL NOT NULL DEFAULT 0.0,
                        evidence_summary TEXT,
                        verified_by TEXT,
                        verified_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (message_id) REFERENCES mailbox_messages(message_id) ON DELETE CASCADE,
                        UNIQUE(message_id, candidate_id)
                    );
                """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mcl_cand ON message_crm_links(candidate_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mcl_status ON message_crm_links(link_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mcl_msg ON message_crm_links(message_id);")

            # Verify integrity
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()

            return {
                "success": True,
                "version": self.SCHEMA_VERSION,
                "integrity": integrity,
                "fk_violations_count": len(fk_violations),
            }

    # -----------------------------------------------------------------------
    # Account Operations
    # -----------------------------------------------------------------------

    def save_account(self, account: MailboxAccountRecord) -> None:
        """Create or update a MailboxAccountRecord."""
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO mailbox_accounts (
                    account_id, email_address, display_name, provider, imap_host,
                    imap_port, use_ssl, credential_ref, is_enabled, sync_priority,
                    connection_status, last_successful_sync_at, last_error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    email_address = excluded.email_address,
                    display_name = excluded.display_name,
                    provider = excluded.provider,
                    imap_host = excluded.imap_host,
                    imap_port = excluded.imap_port,
                    use_ssl = excluded.use_ssl,
                    credential_ref = excluded.credential_ref,
                    is_enabled = excluded.is_enabled,
                    sync_priority = excluded.sync_priority,
                    connection_status = excluded.connection_status,
                    last_successful_sync_at = excluded.last_successful_sync_at,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at;
                """,
                (
                    account.account_id,
                    account.email_address.strip().lower(),
                    account.display_name,
                    account.provider,
                    account.imap_host,
                    account.imap_port,
                    1 if account.use_ssl else 0,
                    account.credential_ref,
                    1 if account.is_enabled else 0,
                    account.sync_priority,
                    account.connection_status,
                    account.last_successful_sync_at,
                    account.last_error_message,
                    account.created_at,
                    account.updated_at,
                ),
            )

    def get_account(self, account_id: str) -> Optional[MailboxAccountRecord]:
        """Fetch account record by account_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_accounts WHERE account_id = ? LIMIT 1;",
                (account_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_account(row)

    def get_account_by_email(self, email_address: str) -> Optional[MailboxAccountRecord]:
        """Fetch account record by email address."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_accounts WHERE LOWER(email_address) = ? LIMIT 1;",
                (email_address.strip().lower(),),
            ).fetchone()
            if not row:
                return None
            return self._row_to_account(row)

    def list_accounts(self, enabled_only: bool = False) -> List[MailboxAccountRecord]:
        """List configured mailbox accounts ordered by priority."""
        with self.get_connection() as conn:
            query = "SELECT * FROM mailbox_accounts"
            params: List[Any] = []
            if enabled_only:
                query += " WHERE is_enabled = 1"
            query += " ORDER BY sync_priority ASC, account_id ASC;"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_account(r) for r in rows]

    def set_account_enabled(self, account_id: str, is_enabled: bool) -> bool:
        """
        Safely update account enablement status in-place without replacing or deleting rows.
        Guarantees foreign key integrity for related folders, messages, and sync checkpoints.
        Returns True if an existing account was updated, False if account not found.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE mailbox_accounts
                SET is_enabled = ?, updated_at = ?
                WHERE account_id = ?;
                """,
                (1 if is_enabled else 0, now_iso, account_id),
            )
            return cursor.rowcount > 0

    def _row_to_account(self, row: sqlite3.Row) -> MailboxAccountRecord:
        return MailboxAccountRecord(
            account_id=row["account_id"],
            email_address=row["email_address"],
            display_name=row["display_name"],
            provider=row["provider"],
            imap_host=row["imap_host"],
            imap_port=row["imap_port"],
            use_ssl=bool(row["use_ssl"]),
            credential_ref=row["credential_ref"],
            is_enabled=bool(row["is_enabled"]),
            sync_priority=row["sync_priority"],
            connection_status=row["connection_status"],
            last_successful_sync_at=row["last_successful_sync_at"],
            last_error_message=row["last_error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -----------------------------------------------------------------------
    # Folder Operations
    # -----------------------------------------------------------------------

    def save_folder(self, folder: MailboxFolderRecord) -> MailboxFolderRecord:
        """
        Create or update a MailboxFolderRecord.
        If a folder with the same (account_id, folder_name) already exists,
        its existing folder_id is preserved and returned to protect foreign key integrity.
        """
        with self._lock, self.get_connection() as conn:
            existing = conn.execute(
                "SELECT folder_id, created_at FROM mailbox_folders WHERE account_id = ? AND folder_name = ? LIMIT 1;",
                (folder.account_id, folder.folder_name),
            ).fetchone()

            if existing:
                # Retain authoritative primary key folder_id already referenced by child tables
                persisted_folder_id = existing["folder_id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE mailbox_folders SET
                        folder_delimiter = ?,
                        folder_role = ?,
                        imap_flags_json = ?,
                        is_active = ?,
                        last_uidvalidity = ?,
                        last_seen_uid = ?,
                        updated_at = ?
                    WHERE folder_id = ?;
                    """,
                    (
                        folder.folder_delimiter,
                        folder.folder_role,
                        json.dumps(folder.imap_flags, ensure_ascii=False),
                        1 if folder.is_active else 0,
                        folder.last_uidvalidity,
                        folder.last_seen_uid,
                        folder.updated_at,
                        persisted_folder_id,
                    ),
                )
                folder.folder_id = persisted_folder_id
                folder.created_at = created_at
            else:
                conn.execute(
                    """
                    INSERT INTO mailbox_folders (
                        folder_id, account_id, folder_name, folder_delimiter, folder_role,
                        imap_flags_json, is_active, last_uidvalidity, last_seen_uid,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        folder.folder_id,
                        folder.account_id,
                        folder.folder_name,
                        folder.folder_delimiter,
                        folder.folder_role,
                        json.dumps(folder.imap_flags, ensure_ascii=False),
                        1 if folder.is_active else 0,
                        folder.last_uidvalidity,
                        folder.last_seen_uid,
                        folder.created_at,
                        folder.updated_at,
                    ),
                )
            return folder

    def get_folder(self, folder_id: str) -> Optional[MailboxFolderRecord]:
        """Fetch folder by folder_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_folders WHERE folder_id = ? LIMIT 1;",
                (folder_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_folder(row)

    def get_folder_by_name(self, account_id: str, folder_name: str) -> Optional[MailboxFolderRecord]:
        """Fetch folder by account and exact remote folder name."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_folders WHERE account_id = ? AND folder_name = ? LIMIT 1;",
                (account_id, folder_name),
            ).fetchone()
            if not row:
                return None
            return self._row_to_folder(row)

    def list_folders(self, account_id: str, active_only: bool = True) -> List[MailboxFolderRecord]:
        """List discovered folders for an account."""
        with self.get_connection() as conn:
            query = "SELECT * FROM mailbox_folders WHERE account_id = ?"
            params: List[Any] = [account_id]
            if active_only:
                query += " AND is_active = 1"
            query += " ORDER BY folder_name ASC;"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_folder(r) for r in rows]

    def _row_to_folder(self, row: sqlite3.Row) -> MailboxFolderRecord:
        flags = json.loads(row["imap_flags_json"] or "[]")
        return MailboxFolderRecord(
            folder_id=row["folder_id"],
            account_id=row["account_id"],
            folder_name=row["folder_name"],
            folder_delimiter=row["folder_delimiter"],
            folder_role=row["folder_role"],
            imap_flags=flags,
            is_active=bool(row["is_active"]),
            last_uidvalidity=row["last_uidvalidity"],
            last_seen_uid=row["last_seen_uid"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -----------------------------------------------------------------------
    # Sync Checkpoint Operations
    # -----------------------------------------------------------------------

    def get_folder_sync_state(self, account_id: str, folder_id: str) -> Optional[MailboxFolderSyncStateRecord]:
        """Retrieve durable checkpoint for an account and folder."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
                (account_id, folder_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_sync_state(row)

    def update_folder_sync_state(self, record: MailboxFolderSyncStateRecord) -> None:
        """
        Durable, monotonic checkpoint update.
        Safely detects UIDVALIDITY changes and prevents invalid backwards cursor moves.
        """
        with self._lock, self.get_connection() as conn:
            existing = conn.execute(
                "SELECT uidvalidity, last_synced_uid FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
                (record.account_id, record.folder_id),
            ).fetchone()

            if existing:
                prev_uv = existing["uidvalidity"]
                prev_uid = existing["last_synced_uid"]

                # If UIDVALIDITY changed, require explicit reset or log rollover warning
                if record.uidvalidity != prev_uv:
                    logger.warning(
                        f"UIDVALIDITY rollover detected for {record.account_id}/{record.folder_id}: "
                        f"was {prev_uv}, now {record.uidvalidity}. Checkpoint will be reset."
                    )

            conn.execute(
                """
                INSERT INTO mailbox_folder_sync_state (
                    sync_state_id, account_id, folder_id, uidvalidity, highest_modseq,
                    last_synced_uid, last_sync_started_at, last_sync_completed_at,
                    last_status, messages_fetched_total, last_error_message, retry_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, folder_id) DO UPDATE SET
                    uidvalidity = excluded.uidvalidity,
                    highest_modseq = excluded.highest_modseq,
                    last_synced_uid = excluded.last_synced_uid,
                    last_sync_started_at = excluded.last_sync_started_at,
                    last_sync_completed_at = excluded.last_sync_completed_at,
                    last_status = excluded.last_status,
                    messages_fetched_total = excluded.messages_fetched_total,
                    last_error_message = excluded.last_error_message,
                    retry_count = excluded.retry_count,
                    updated_at = excluded.updated_at;
                """,
                (
                    record.sync_state_id,
                    record.account_id,
                    record.folder_id,
                    record.uidvalidity,
                    record.highest_modseq,
                    record.last_synced_uid,
                    record.last_sync_started_at,
                    record.last_sync_completed_at,
                    record.last_status,
                    record.messages_fetched_total,
                    record.last_error_message,
                    record.retry_count,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def _row_to_sync_state(self, row: sqlite3.Row) -> MailboxFolderSyncStateRecord:
        return MailboxFolderSyncStateRecord(
            sync_state_id=row["sync_state_id"],
            account_id=row["account_id"],
            folder_id=row["folder_id"],
            uidvalidity=row["uidvalidity"],
            highest_modseq=row["highest_modseq"],
            last_synced_uid=row["last_synced_uid"],
            last_sync_started_at=row["last_sync_started_at"],
            last_sync_completed_at=row["last_sync_completed_at"],
            last_status=row["last_status"],
            messages_fetched_total=row["messages_fetched_total"],
            last_error_message=row["last_error_message"],
            retry_count=row["retry_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -----------------------------------------------------------------------
    # Mailbox Message Operations
    # -----------------------------------------------------------------------

    def save_message(self, message: MailboxMessageRecord) -> Tuple[bool, str]:
        """
        Idempotently insert a MailboxMessageRecord.
        Preserves original immutable occurrence identity (account_id, folder_id, imap_uidvalidity, imap_uid).
        Enforces maximum body text and HTML lengths.
        Returns (is_new_record, message_id).
        """
        # Bounded body sanitization / truncation
        body_plain = message.body_plain_text or ""
        body_html = message.body_html_sanitized or ""
        truncated = message.body_truncated

        if len(body_plain) > MAX_BODY_PLAIN_CHARS:
            body_plain = body_plain[:MAX_BODY_PLAIN_CHARS]
            truncated = True
        if len(body_html) > MAX_BODY_HTML_CHARS:
            body_html = body_html[:MAX_BODY_HTML_CHARS]
            truncated = True

        size_bytes = message.content_size_bytes or (len(body_plain.encode("utf-8")) + len(body_html.encode("utf-8")))

        with self._lock, self.get_connection() as conn:
            existing = conn.execute(
                """
                SELECT message_id FROM mailbox_messages 
                WHERE account_id = ? AND folder_id = ? AND imap_uidvalidity = ? AND imap_uid = ?
                LIMIT 1;
                """,
                (message.account_id, message.folder_id, message.imap_uidvalidity, message.imap_uid),
            ).fetchone()

            if existing:
                # Existing exact occurrence: allow updating parse_status / body if recovering deferred message
                if existing["message_id"] == message.message_id or message.parse_status != "deferred_oversized":
                    conn.execute(
                        """
                        UPDATE mailbox_messages
                        SET body_plain_text = ?,
                            body_html_sanitized = ?,
                            has_html = ?,
                            has_attachments = ?,
                            attachments_meta_json = ?,
                            parse_status = ?,
                            raw_headers_json = ?,
                            body_truncated = ?,
                            content_size_bytes = ?
                        WHERE message_id = ?;
                        """,
                        (
                            body_plain,
                            body_html,
                            1 if message.has_html else 0,
                            1 if message.has_attachments else 0,
                            json.dumps(message.attachments_metadata, ensure_ascii=False),
                            message.parse_status,
                            json.dumps(message.raw_headers, ensure_ascii=False),
                            1 if truncated else 0,
                            size_bytes,
                            existing["message_id"],
                        ),
                    )
                return False, existing["message_id"]

            conn.execute(
                """
                INSERT INTO mailbox_messages (
                    message_id, account_id, folder_id, folder_role, direction,
                    imap_uid, imap_uidvalidity, rfc_message_id, in_reply_to,
                    references_json, from_address, from_name, to_addresses_json,
                    cc_addresses_json, bcc_addresses_json, reply_to_addresses_json,
                    subject, date_utc, body_plain_text, body_html_sanitized,
                    has_html, has_attachments, attachments_meta_json, parse_status,
                    raw_headers_json, body_truncated, content_size_bytes, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    message.message_id,
                    message.account_id,
                    message.folder_id,
                    message.folder_role,
                    message.direction,
                    message.imap_uid,
                    message.imap_uidvalidity,
                    message.rfc_message_id.strip() if message.rfc_message_id else None,
                    message.in_reply_to.strip() if message.in_reply_to else None,
                    json.dumps(message.references, ensure_ascii=False),
                    message.from_address.strip(),
                    message.from_name,
                    json.dumps(message.to_addresses, ensure_ascii=False),
                    json.dumps(message.cc_addresses, ensure_ascii=False),
                    json.dumps(message.bcc_addresses, ensure_ascii=False),
                    json.dumps(message.reply_to_addresses, ensure_ascii=False),
                    message.subject,
                    message.date_utc,
                    body_plain,
                    body_html,
                    1 if message.has_html else 0,
                    1 if message.has_attachments else 0,
                    json.dumps(message.attachments_metadata, ensure_ascii=False),
                    message.parse_status,
                    json.dumps(message.raw_headers, ensure_ascii=False),
                    1 if truncated else 0,
                    size_bytes,
                    message.ingested_at,
                ),
            )
            return True, message.message_id

    def get_message_by_occurrence(
        self, account_id: str, folder_id: str, uidvalidity: int, uid: int
    ) -> Optional[MailboxMessageRecord]:
        """Fetch message by exact IMAP physical occurrence."""
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM mailbox_messages 
                WHERE account_id = ? AND folder_id = ? AND imap_uidvalidity = ? AND imap_uid = ?
                LIMIT 1;
                """,
                (account_id, folder_id, uidvalidity, uid),
            ).fetchone()
            if not row:
                return None
            return self._row_to_message(row)

    def get_message(self, message_id: str) -> Optional[MailboxMessageRecord]:
        """Fetch message by internal message_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_messages WHERE message_id = ? LIMIT 1;",
                (message_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_message(row)

    def find_messages_by_rfc_id(self, rfc_message_id: str) -> List[MailboxMessageRecord]:
        """
        Find all physical occurrences sharing an RFC Message-ID.
        (e.g. Copied across accounts or folders).
        """
        clean_rfc = rfc_message_id.strip().strip("<>").strip()
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mailbox_messages 
                WHERE rfc_message_id = ? OR rfc_message_id = ?
                ORDER BY date_utc ASC;
                """,
                (clean_rfc, f"<{clean_rfc}>"),
            ).fetchall()
            return [self._row_to_message(r) for r in rows]

    def list_messages(
        self,
        account_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        direction: Optional[str] = None,
        since_date_utc: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MailboxMessageRecord]:
        """Query staged mailbox messages with optional filtering."""
        with self.get_connection() as conn:
            clauses = []
            params: List[Any] = []

            if account_id:
                clauses.append("account_id = ?")
                params.append(account_id)
            if folder_id:
                clauses.append("folder_id = ?")
                params.append(folder_id)
            if direction:
                clauses.append("direction = ?")
                params.append(direction)
            if since_date_utc:
                clauses.append("date_utc >= ?")
                params.append(since_date_utc)

            query = "SELECT * FROM mailbox_messages"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY date_utc DESC LIMIT ? OFFSET ?;"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_message(r) for r in rows]

    def _row_to_message(self, row: sqlite3.Row) -> MailboxMessageRecord:
        return MailboxMessageRecord(
            message_id=row["message_id"],
            account_id=row["account_id"],
            folder_id=row["folder_id"],
            folder_role=row["folder_role"],
            direction=row["direction"],
            imap_uid=row["imap_uid"],
            imap_uidvalidity=row["imap_uidvalidity"],
            rfc_message_id=row["rfc_message_id"],
            in_reply_to=row["in_reply_to"],
            references=json.loads(row["references_json"] or "[]"),
            from_address=row["from_address"],
            from_name=row["from_name"],
            to_addresses=json.loads(row["to_addresses_json"] or "[]"),
            cc_addresses=json.loads(row["cc_addresses_json"] or "[]"),
            bcc_addresses=json.loads(row["bcc_addresses_json"] or "[]"),
            reply_to_addresses=json.loads(row["reply_to_addresses_json"] or "[]"),
            subject=row["subject"],
            date_utc=row["date_utc"],
            body_plain_text=row["body_plain_text"],
            body_html_sanitized=row["body_html_sanitized"],
            has_html=bool(row["has_html"]),
            has_attachments=bool(row["has_attachments"]),
            attachments_metadata=json.loads(row["attachments_meta_json"] or "[]"),
            parse_status=row["parse_status"],
            raw_headers=json.loads(row["raw_headers_json"] or "{}"),
            body_truncated=bool(row["body_truncated"]),
            content_size_bytes=row["content_size_bytes"],
            ingested_at=row["ingested_at"],
        )

    # -----------------------------------------------------------------------
    # Message CRM Link Operations
    # -----------------------------------------------------------------------

    def save_crm_link(self, link: MessageCrmLinkRecord) -> None:
        """
        Store or update a staging CRM link between a message and a company/contact.
        Never executes automatic mutations on candidate_companies.
        """
        with self._lock, self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO message_crm_links (
                    link_id, message_id, candidate_id, contact_id, link_status,
                    link_method, link_confidence, evidence_summary, verified_by,
                    verified_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id, candidate_id) DO UPDATE SET
                    contact_id = excluded.contact_id,
                    link_status = excluded.link_status,
                    link_method = excluded.link_method,
                    link_confidence = excluded.link_confidence,
                    evidence_summary = excluded.evidence_summary,
                    verified_by = excluded.verified_by,
                    verified_at = excluded.verified_at,
                    updated_at = excluded.updated_at;
                """,
                (
                    link.link_id,
                    link.message_id,
                    link.candidate_id,
                    link.contact_id,
                    link.link_status,
                    link.link_method,
                    link.link_confidence,
                    link.evidence_summary,
                    link.verified_by,
                    link.verified_at,
                    link.created_at,
                    link.updated_at,
                ),
            )

    def get_crm_link(self, link_id: str) -> Optional[MessageCrmLinkRecord]:
        """Fetch CRM link by link_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM message_crm_links WHERE link_id = ? LIMIT 1;",
                (link_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_crm_link(row)

    def list_links_for_candidate(self, candidate_id: str) -> List[MessageCrmLinkRecord]:
        """List all proposed or verified message links for a candidate company."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM message_crm_links WHERE candidate_id = ? ORDER BY created_at DESC;",
                (candidate_id,),
            ).fetchall()
            return [self._row_to_crm_link(r) for r in rows]

    def list_pending_review_links(self, limit: int = 50) -> List[MessageCrmLinkRecord]:
        """List unconfirmed links requiring operator review."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM message_crm_links WHERE link_status = 'suggested_pending_review' ORDER BY created_at DESC LIMIT ?;",
                (limit,),
            ).fetchall()
            return [self._row_to_crm_link(r) for r in rows]

    def _row_to_crm_link(self, row: sqlite3.Row) -> MessageCrmLinkRecord:
        return MessageCrmLinkRecord(
            link_id=row["link_id"],
            message_id=row["message_id"],
            candidate_id=row["candidate_id"],
            contact_id=row["contact_id"],
            link_status=row["link_status"],
            link_method=row["link_method"],
            link_confidence=row["link_confidence"],
            evidence_summary=row["evidence_summary"],
            verified_by=row["verified_by"],
            verified_at=row["verified_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
