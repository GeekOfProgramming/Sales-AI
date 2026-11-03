import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from sales_engine.inbox.models import (
    InboundMessage,
    Conversation,
    ConversationEvent,
    ReplySuggestion,
    MeetingProposal,
    MailboxConnectionInfo,
    MailboxSyncSummary,
)
from sales_engine.inbox.security import InboxSecurity


def get_default_inbox_db_path() -> Path:
    env_path = os.getenv("SALES_OUTREACH_DB") or os.getenv("SALESAI_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path("data/sales_outreach.db")


class InboxStore:
    """
    SQLite-backed store for Phase 11 Inbox records, conversations,
    suggestions, audit timeline, meeting proposals, and sync cursors.
    """

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else get_default_inbox_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._lock, self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mailbox_connections (
                    provider TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    account_email TEXT,
                    last_synced_at TEXT,
                    error_message TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mailbox_sync_state (
                    provider TEXT PRIMARY KEY,
                    cursor TEXT,
                    last_attempted_sync TEXT,
                    last_successful_sync TEXT,
                    messages_fetched INTEGER DEFAULT 0,
                    new_replies INTEGER DEFAULT 0,
                    errors_json TEXT DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS inbound_messages (
                    inbound_message_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_message_id TEXT NOT NULL,
                    provider_thread_id TEXT,
                    internet_message_id TEXT,
                    in_reply_to TEXT,
                    references_json TEXT DEFAULT '[]',
                    from_name TEXT,
                    from_email TEXT NOT NULL,
                    to_emails_json TEXT DEFAULT '[]',
                    cc_emails_json TEXT DEFAULT '[]',
                    subject TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    body_html_present INTEGER DEFAULT 0,
                    received_at TEXT NOT NULL,
                    linked_outbound_draft_id TEXT,
                    linked_outbound_revision INTEGER,
                    linked_send_attempt_id TEXT,
                    company_id TEXT,
                    contact_id TEXT,
                    conversation_id TEXT,
                    classification TEXT NOT NULL DEFAULT 'unknown',
                    classification_confidence REAL DEFAULT 0.0,
                    is_auto_reply INTEGER DEFAULT 0,
                    is_bounce INTEGER DEFAULT 0,
                    is_unsubscribe INTEGER DEFAULT 0,
                    raw_headers_json TEXT,
                    raw_provider_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inbound_prov_msg ON inbound_messages(provider, provider_message_id);
                CREATE INDEX IF NOT EXISTS idx_inbound_net_id ON inbound_messages(internet_message_id);
                CREATE INDEX IF NOT EXISTS idx_inbound_in_reply ON inbound_messages(in_reply_to);
                CREATE INDEX IF NOT EXISTS idx_inbound_conv ON inbound_messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_inbound_company ON inbound_messages(company_id);

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    company_id TEXT,
                    primary_contact_id TEXT,
                    provider TEXT NOT NULL,
                    provider_thread_id TEXT,
                    status TEXT NOT NULL,
                    reply_category TEXT NOT NULL DEFAULT 'unknown',
                    sentiment_optional TEXT,
                    first_outbound_at TEXT,
                    first_reply_at TEXT,
                    last_message_at TEXT,
                    actual_respondent_contact_id TEXT,
                    meeting_status TEXT DEFAULT 'none',
                    opportunity_status TEXT DEFAULT 'unknown',
                    active_conversation INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conv_company ON conversations(company_id);
                CREATE INDEX IF NOT EXISTS idx_conv_thread ON conversations(provider, provider_thread_id);
                CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);

                CREATE TABLE IF NOT EXISTS conversation_events (
                    event_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cev_conv ON conversation_events(conversation_id);

                CREATE TABLE IF NOT EXISTS reply_classifications (
                    classification_id TEXT PRIMARY KEY,
                    inbound_message_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    method TEXT NOT NULL,
                    reasoning TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reply_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    inbound_message_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    evidence_refs_json TEXT DEFAULT '[]',
                    classification TEXT NOT NULL DEFAULT 'unknown',
                    generation_model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    language TEXT DEFAULT 'en',
                    tone TEXT DEFAULT 'professional_concise',
                    status TEXT NOT NULL DEFAULT 'pending_review',
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    approved_at TEXT,
                    sent_at TEXT,
                    approved_by TEXT,
                    rejection_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sug_conv ON reply_suggestions(conversation_id);

                CREATE TABLE IF NOT EXISTS reply_send_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    send_key TEXT UNIQUE NOT NULL,
                    conversation_id TEXT NOT NULL,
                    suggestion_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_rsa_key ON reply_send_attempts(send_key);

                CREATE TABLE IF NOT EXISTS meeting_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    inbound_message_id TEXT NOT NULL,
                    proposed_times_json TEXT DEFAULT '[]',
                    timezone TEXT,
                    raw_text_snippet TEXT,
                    status TEXT DEFAULT 'proposed',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mtg_conv ON meeting_proposals(conversation_id);
            """)

    # -----------------------------------------------------------------------
    # Inbound Messages
    # -----------------------------------------------------------------------

    def save_inbound_message(self, msg: InboundMessage) -> InboundMessage:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO inbound_messages (
                    inbound_message_id, provider, provider_message_id, provider_thread_id,
                    internet_message_id, in_reply_to, references_json,
                    from_name, from_email, to_emails_json, cc_emails_json,
                    subject, body_text, body_html_present, received_at,
                    linked_outbound_draft_id, linked_outbound_revision, linked_send_attempt_id,
                    company_id, contact_id, conversation_id,
                    classification, classification_confidence,
                    is_auto_reply, is_bounce, is_unsubscribe,
                    raw_headers_json, raw_provider_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.inbound_message_id,
                    msg.provider,
                    msg.provider_message_id,
                    msg.provider_thread_id,
                    msg.internet_message_id,
                    msg.in_reply_to,
                    json.dumps(msg.references),
                    msg.from_name,
                    msg.from_email.lower().strip(),
                    json.dumps(msg.to_emails),
                    json.dumps(msg.cc_emails),
                    msg.subject,
                    msg.body_text,
                    1 if msg.body_html_present else 0,
                    msg.received_at,
                    msg.linked_outbound_draft_id,
                    msg.linked_outbound_revision,
                    msg.linked_send_attempt_id,
                    msg.company_id,
                    msg.contact_id,
                    msg.conversation_id,
                    msg.classification,
                    msg.classification_confidence,
                    1 if msg.is_auto_reply else 0,
                    1 if msg.is_bounce else 0,
                    1 if msg.is_unsubscribe else 0,
                    json.dumps(msg.raw_headers_ref) if msg.raw_headers_ref else None,
                    json.dumps(msg.raw_provider_ref) if msg.raw_provider_ref else None,
                    msg.created_at,
                ),
            )
        return msg

    def get_inbound_message(self, message_id: str) -> Optional[InboundMessage]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM inbound_messages WHERE inbound_message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_inbound_message(row)

    def find_inbound_by_provider_id(self, provider: str, provider_message_id: str) -> Optional[InboundMessage]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM inbound_messages WHERE provider = ? AND provider_message_id = ? LIMIT 1",
                (provider, provider_message_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_inbound_message(row)

    def find_inbound_by_internet_id(self, internet_message_id: str) -> Optional[InboundMessage]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM inbound_messages WHERE internet_message_id = ? LIMIT 1",
                (internet_message_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_inbound_message(row)

    def list_inbound_messages(self, conversation_id: Optional[str] = None, limit: int = 100) -> List[InboundMessage]:
        with self._get_connection() as conn:
            if conversation_id:
                rows = conn.execute(
                    "SELECT * FROM inbound_messages WHERE conversation_id = ? ORDER BY received_at ASC LIMIT ?",
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM inbound_messages ORDER BY received_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_inbound_message(r) for r in rows]

    def _row_to_inbound_message(self, row: sqlite3.Row) -> InboundMessage:
        return InboundMessage(
            inbound_message_id=row["inbound_message_id"],
            provider=row["provider"],
            provider_message_id=row["provider_message_id"],
            provider_thread_id=row["provider_thread_id"],
            internet_message_id=row["internet_message_id"],
            in_reply_to=row["in_reply_to"],
            references=json.loads(row["references_json"] or "[]"),
            from_name=row["from_name"],
            from_email=row["from_email"],
            to_emails=json.loads(row["to_emails_json"] or "[]"),
            cc_emails=json.loads(row["cc_emails_json"] or "[]"),
            subject=row["subject"],
            body_text=row["body_text"],
            body_html_present=bool(row["body_html_present"]),
            received_at=row["received_at"],
            linked_outbound_draft_id=row["linked_outbound_draft_id"],
            linked_outbound_revision=row["linked_outbound_revision"],
            linked_send_attempt_id=row["linked_send_attempt_id"],
            company_id=row["company_id"],
            contact_id=row["contact_id"],
            conversation_id=row["conversation_id"],
            classification=row["classification"],
            classification_confidence=row["classification_confidence"],
            is_auto_reply=bool(row["is_auto_reply"]),
            is_bounce=bool(row["is_bounce"]),
            is_unsubscribe=bool(row["is_unsubscribe"]),
            raw_headers_ref=json.loads(row["raw_headers_json"]) if row["raw_headers_json"] else None,
            raw_provider_ref=json.loads(row["raw_provider_json"]) if row["raw_provider_json"] else None,
            created_at=row["created_at"],
        )

    # -----------------------------------------------------------------------
    # Conversations
    # -----------------------------------------------------------------------

    def save_conversation(self, conv: Conversation) -> Conversation:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations (
                    conversation_id, company_id, primary_contact_id, provider,
                    provider_thread_id, status, reply_category, sentiment_optional,
                    first_outbound_at, first_reply_at, last_message_at,
                    actual_respondent_contact_id, meeting_status, opportunity_status,
                    active_conversation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conv.conversation_id,
                    conv.company_id,
                    conv.primary_contact_id,
                    conv.provider,
                    conv.provider_thread_id,
                    conv.status,
                    conv.reply_category,
                    conv.sentiment_optional,
                    conv.first_outbound_at,
                    conv.first_reply_at,
                    conv.last_message_at,
                    conv.actual_respondent_contact_id,
                    conv.meeting_status,
                    conv.opportunity_status,
                    1 if conv.active_conversation else 0,
                    conv.created_at,
                    conv.updated_at,
                ),
            )
        return conv

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ? LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_conversation(row)

    def find_conversation_by_thread(self, provider: str, provider_thread_id: str) -> Optional[Conversation]:
        if not provider_thread_id:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE provider = ? AND provider_thread_id = ? LIMIT 1",
                (provider, provider_thread_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_conversation(row)

    def find_conversation_by_company(self, company_id: str) -> Optional[Conversation]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE company_id = ? ORDER BY last_message_at DESC LIMIT 1",
                (company_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_conversation(row)

    def list_conversations(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Conversation]:
        query = "SELECT * FROM conversations WHERE 1=1"
        params: List[Any] = []
        if category:
            query += " AND reply_category = ?"
            params.append(category)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY last_message_at DESC, created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_conversation(r) for r in rows]

    def _row_to_conversation(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            conversation_id=row["conversation_id"],
            company_id=row["company_id"],
            primary_contact_id=row["primary_contact_id"],
            provider=row["provider"],
            provider_thread_id=row["provider_thread_id"],
            status=row["status"],
            reply_category=row["reply_category"],
            sentiment_optional=row["sentiment_optional"],
            first_outbound_at=row["first_outbound_at"],
            first_reply_at=row["first_reply_at"],
            last_message_at=row["last_message_at"],
            actual_respondent_contact_id=row["actual_respondent_contact_id"],
            meeting_status=row["meeting_status"] or "none",
            opportunity_status=row["opportunity_status"] or "unknown",
            active_conversation=bool(row["active_conversation"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -----------------------------------------------------------------------
    # Conversation Events (Audit Trail)
    # -----------------------------------------------------------------------

    def add_event(self, event: ConversationEvent) -> ConversationEvent:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_events (
                    event_id, conversation_id, event_type, actor, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.conversation_id,
                    event.event_type,
                    event.actor,
                    json.dumps(event.details),
                    event.created_at,
                ),
            )
        return event

    def list_events(self, conversation_id: str, limit: int = 100) -> List[ConversationEvent]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM conversation_events WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
            return [
                ConversationEvent(
                    event_id=r["event_id"],
                    conversation_id=r["conversation_id"],
                    event_type=r["event_type"],
                    actor=r["actor"],
                    details=json.loads(r["details_json"] or "{}"),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # -----------------------------------------------------------------------
    # Reply Suggestions
    # -----------------------------------------------------------------------

    def save_suggestion(self, sug: ReplySuggestion) -> ReplySuggestion:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reply_suggestions (
                    suggestion_id, conversation_id, inbound_message_id, subject, body,
                    evidence_refs_json, classification, generation_model, prompt_version,
                    language, tone, status, created_at, updated_at, approved_at, sent_at,
                    approved_by, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sug.suggestion_id,
                    sug.conversation_id,
                    sug.inbound_message_id,
                    sug.subject,
                    sug.body,
                    json.dumps(sug.evidence_refs),
                    sug.classification,
                    sug.generation_model,
                    sug.prompt_version,
                    sug.language,
                    sug.tone,
                    sug.status,
                    sug.created_at,
                    sug.updated_at,
                    sug.approved_at,
                    sug.sent_at,
                    sug.approved_by,
                    sug.rejection_reason,
                ),
            )
        return sug

    def get_suggestion(self, suggestion_id: str) -> Optional[ReplySuggestion]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reply_suggestions WHERE suggestion_id = ? LIMIT 1",
                (suggestion_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_suggestion(row)

    def get_latest_suggestion_for_conversation(self, conversation_id: str) -> Optional[ReplySuggestion]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reply_suggestions WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_suggestion(row)

    def _row_to_suggestion(self, row: sqlite3.Row) -> ReplySuggestion:
        return ReplySuggestion(
            suggestion_id=row["suggestion_id"],
            conversation_id=row["conversation_id"],
            inbound_message_id=row["inbound_message_id"],
            subject=row["subject"],
            body=row["body"],
            evidence_refs=json.loads(row["evidence_refs_json"] or "[]"),
            classification=row["classification"],
            generation_model=row["generation_model"],
            prompt_version=row["prompt_version"],
            language=row["language"] or "en",
            tone=row["tone"] or "professional_concise",
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            sent_at=row["sent_at"],
            approved_by=row["approved_by"],
            rejection_reason=row["rejection_reason"],
        )

    # -----------------------------------------------------------------------
    # Meeting Proposals
    # -----------------------------------------------------------------------

    def save_meeting_proposal(self, prop: MeetingProposal) -> MeetingProposal:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO meeting_proposals (
                    proposal_id, conversation_id, inbound_message_id,
                    proposed_times_json, timezone, raw_text_snippet, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prop.proposal_id,
                    prop.conversation_id,
                    prop.inbound_message_id,
                    json.dumps(prop.proposed_times),
                    prop.timezone,
                    prop.raw_text_snippet,
                    prop.status,
                    prop.created_at,
                ),
            )
        return prop

    def get_meeting_proposal(self, conversation_id: str) -> Optional[MeetingProposal]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM meeting_proposals WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            return MeetingProposal(
                proposal_id=row["proposal_id"],
                conversation_id=row["conversation_id"],
                inbound_message_id=row["inbound_message_id"],
                proposed_times=json.loads(row["proposed_times_json"] or "[]"),
                timezone=row["timezone"],
                raw_text_snippet=row["raw_text_snippet"],
                status=row["status"],
                created_at=row["created_at"],
            )

    # -----------------------------------------------------------------------
    # Mailbox Connection & Sync State
    # -----------------------------------------------------------------------

    def update_connection_state(self, info: MailboxConnectionInfo):
        with self._lock, self._get_connection() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO mailbox_connections (
                    provider, status, account_email, last_synced_at, error_message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    info.provider,
                    info.status,
                    info.account_email,
                    info.last_synced_at,
                    InboxSecurity.sanitize_secrets(info.error_message),
                    now_iso,
                ),
            )

    def get_connection_state(self, provider: str = "mock") -> MailboxConnectionInfo:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_connections WHERE provider = ? LIMIT 1",
                (provider,),
            ).fetchone()
            if not row:
                return MailboxConnectionInfo(provider=provider, status="not_configured")
            return MailboxConnectionInfo(
                provider=row["provider"],
                status=row["status"],
                account_email=row["account_email"],
                last_synced_at=row["last_synced_at"],
                error_message=row["error_message"],
            )

    def update_sync_state(self, summary: MailboxSyncSummary):
        with self._lock, self._get_connection() as conn:
            sanitized_errors = [InboxSecurity.sanitize_secrets(e) for e in summary.errors]
            conn.execute(
                """
                INSERT OR REPLACE INTO mailbox_sync_state (
                    provider, cursor, last_attempted_sync, last_successful_sync,
                    messages_fetched, new_replies, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.provider,
                    summary.cursor,
                    summary.started_at,
                    summary.completed_at if not summary.errors else None,
                    summary.messages_fetched,
                    summary.new_replies,
                    json.dumps(sanitized_errors),
                ),
            )

    def get_sync_state(self, provider: str = "mock") -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mailbox_sync_state WHERE provider = ? LIMIT 1",
                (provider,),
            ).fetchone()
            if not row:
                return None
            return {
                "provider": row["provider"],
                "cursor": row["cursor"],
                "last_attempted_sync": row["last_attempted_sync"],
                "last_successful_sync": row["last_successful_sync"],
                "messages_fetched": row["messages_fetched"],
                "new_replies": row["new_replies"],
                "errors": json.loads(row["errors_json"] or "[]"),
            }

    # -----------------------------------------------------------------------
    # Send Attempts & Idempotency
    # -----------------------------------------------------------------------

    def record_reply_send_attempt(
        self,
        attempt_id: str,
        send_key: str,
        conversation_id: str,
        suggestion_id: str,
        provider: str,
        recipient_email: str,
        sender_email: str,
        status: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        with self._lock, self._get_connection() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO reply_send_attempts (
                    attempt_id, send_key, conversation_id, suggestion_id, provider,
                    recipient_email, sender_email, status, error_type, error_message,
                    attempted_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    send_key,
                    conversation_id,
                    suggestion_id,
                    provider,
                    recipient_email.lower().strip(),
                    sender_email.lower().strip(),
                    status,
                    error_type,
                    InboxSecurity.sanitize_secrets(error_message),
                    now_iso,
                    now_iso if status in ("sent", "failed", "blocked") else None,
                ),
            )

    def is_reply_send_key_used(self, send_key: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM reply_send_attempts WHERE send_key = ? AND status = 'sent' LIMIT 1",
                (send_key,),
            ).fetchone()
            return row is not None
