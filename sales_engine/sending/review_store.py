import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from sales_engine.sending.schemas import (
    StoredDraft,
    ReviewEvent,
    SendAttempt,
    SuppressionEntry,
)

def get_default_db_path() -> Path:
    env_path = os.getenv("SALES_OUTREACH_DB")
    if env_path:
        return Path(env_path)
    return Path("data/sales_outreach.db")

DEFAULT_DB_PATH = Path("data/sales_outreach.db")


class ReviewStore:
    """
    SQLite-backed store for draft review queue, revision history,
    review events audit trail, and sending attempts.
    """

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else get_default_db_path()
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
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    lead_id TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    recipient_title TEXT,
                    recipient_email TEXT NOT NULL,
                    sender_email TEXT,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    service_used TEXT,
                    personalization_notes TEXT,
                    evidence_refs TEXT,
                    source_job_urls TEXT,
                    language TEXT DEFAULT 'en',
                    tone TEXT DEFAULT 'professional_concise',
                    prompt_version TEXT DEFAULT 'outreach_v1',
                    generation_model TEXT DEFAULT 'qwen2.5:1.5b',
                    approval_status TEXT NOT NULL,
                    send_status TEXT NOT NULL,
                    outreach_status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    approved_content_hash TEXT,
                    reviewer TEXT,
                    review_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    sent_at TEXT,
                    PRIMARY KEY (draft_id, revision)
                );

                CREATE TABLE IF NOT EXISTS review_events (
                    event_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    review_note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS send_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    send_key TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    provider_message_id TEXT,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_send_attempts_key ON send_attempts(send_key);
                CREATE INDEX IF NOT EXISTS idx_send_attempts_status ON send_attempts(status);

                CREATE TABLE IF NOT EXISTS suppression_list (
                    suppression_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    company_domain TEXT,
                    reason TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_suppression_email ON suppression_list(email);

                CREATE TABLE IF NOT EXISTS send_idempotency_locks (
                    send_key TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    locked_at TEXT NOT NULL
                );
            """)

    def save_draft(self, draft: StoredDraft) -> StoredDraft:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drafts (
                    draft_id, revision, lead_id, contact_id,
                    recipient_name, recipient_title, recipient_email, sender_email,
                    subject, body, service_used, personalization_notes,
                    evidence_refs, source_job_urls, language, tone,
                    prompt_version, generation_model,
                    approval_status, send_status, outreach_status,
                    content_hash, approved_content_hash, reviewer, review_note,
                    created_at, updated_at, approved_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.revision,
                    draft.lead_id,
                    draft.contact_id,
                    draft.recipient_name,
                    draft.recipient_title,
                    draft.recipient_email,
                    draft.sender_email,
                    draft.subject,
                    draft.body,
                    draft.service_used,
                    draft.personalization_notes,
                    json.dumps(draft.evidence_refs or []),
                    json.dumps(draft.source_job_urls or []),
                    draft.language,
                    draft.tone,
                    draft.prompt_version,
                    draft.generation_model,
                    draft.approval_status,
                    draft.send_status,
                    draft.outreach_status,
                    draft.content_hash,
                    draft.approved_content_hash,
                    draft.reviewer,
                    draft.review_note,
                    draft.created_at,
                    draft.updated_at,
                    draft.approved_at,
                    draft.sent_at,
                ),
            )
        return draft

    def get_draft(self, draft_id: str, revision: Optional[int] = None) -> Optional[StoredDraft]:
        with self._get_connection() as conn:
            if revision is not None:
                row = conn.execute(
                    "SELECT * FROM drafts WHERE draft_id = ? AND revision = ?",
                    (draft_id, revision),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM drafts WHERE draft_id = ? ORDER BY revision DESC LIMIT 1",
                    (draft_id,),
                ).fetchone()

        if not row:
            return None
        return self._row_to_draft(row)

    def list_drafts(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[StoredDraft], int]:
        filters = filters or {}
        clauses = []
        params: List[Any] = []

        if "approval_status" in filters and filters["approval_status"]:
            clauses.append("approval_status = ?")
            params.append(filters["approval_status"])
        if "send_status" in filters and filters["send_status"]:
            clauses.append("send_status = ?")
            params.append(filters["send_status"])
        if "lead_id" in filters and filters["lead_id"]:
            clauses.append("lead_id = ?")
            params.append(filters["lead_id"])
        if "contact_id" in filters and filters["contact_id"]:
            clauses.append("contact_id = ?")
            params.append(filters["contact_id"])
        if "recipient_email" in filters and filters["recipient_email"]:
            clauses.append("recipient_email = ?")
            params.append(filters["recipient_email"].strip().lower())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._get_connection() as conn:
            # Subquery to pick latest revision per draft_id
            count_sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT draft_id, MAX(revision) as max_rev FROM drafts
                    {where_sql}
                    GROUP BY draft_id
                )
            """
            total = conn.execute(count_sql, params).fetchone()[0]

            query_sql = f"""
                SELECT d.* FROM drafts d
                INNER JOIN (
                    SELECT draft_id, MAX(revision) as max_rev FROM drafts
                    {where_sql}
                    GROUP BY draft_id
                ) latest ON d.draft_id = latest.draft_id AND d.revision = latest.max_rev
                ORDER BY d.created_at ASC, d.draft_id ASC
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(query_sql, params + [limit, offset]).fetchall()

        return [self._row_to_draft(r) for r in rows], total

    def update_draft_status(
        self,
        draft_id: str,
        revision: int,
        approval_status: Optional[str] = None,
        send_status: Optional[str] = None,
        outreach_status: Optional[str] = None,
        approved_content_hash: Optional[str] = None,
        approved_at: Optional[str] = None,
        sent_at: Optional[str] = None,
        reviewer: Optional[str] = None,
        review_note: Optional[str] = None,
    ) -> Optional[StoredDraft]:
        now_iso = datetime.now(timezone.utc).isoformat()
        fields = ["updated_at = ?"]
        params: List[Any] = [now_iso]

        if approval_status is not None:
            fields.append("approval_status = ?")
            params.append(approval_status)
        if send_status is not None:
            fields.append("send_status = ?")
            params.append(send_status)
        if outreach_status is not None:
            fields.append("outreach_status = ?")
            params.append(outreach_status)
        if approved_content_hash is not None:
            fields.append("approved_content_hash = ?")
            params.append(approved_content_hash)
        if approved_at is not None:
            fields.append("approved_at = ?")
            params.append(approved_at)
        if sent_at is not None:
            fields.append("sent_at = ?")
            params.append(sent_at)
        if reviewer is not None:
            fields.append("reviewer = ?")
            params.append(reviewer)
        if review_note is not None:
            fields.append("review_note = ?")
            params.append(review_note)

        params.extend([draft_id, revision])
        with self._lock, self._get_connection() as conn:
            conn.execute(
                f"UPDATE drafts SET {', '.join(fields)} WHERE draft_id = ? AND revision = ?",
                params,
            )
        return self.get_draft(draft_id, revision)

    def record_event(self, event: ReviewEvent):
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO review_events (
                    event_id, draft_id, revision, action,
                    previous_status, new_status, reviewer, review_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.draft_id,
                    event.revision,
                    event.action,
                    event.previous_status,
                    event.new_status,
                    event.reviewer,
                    event.review_note,
                    event.created_at,
                ),
            )

    def list_events(self, draft_id: Optional[str] = None, limit: int = 100) -> List[ReviewEvent]:
        with self._get_connection() as conn:
            if draft_id:
                rows = conn.execute(
                    "SELECT * FROM review_events WHERE draft_id = ? ORDER BY created_at ASC LIMIT ?",
                    (draft_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review_events ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()

        events = []
        for r in rows:
            events.append(
                ReviewEvent(
                    event_id=r["event_id"],
                    draft_id=r["draft_id"],
                    revision=r["revision"],
                    action=r["action"],
                    previous_status=r["previous_status"],
                    new_status=r["new_status"],
                    reviewer=r["reviewer"],
                    review_note=r["review_note"],
                    created_at=r["created_at"],
                )
            )
        return events

    def get_review_events(self, draft_id: str) -> List[ReviewEvent]:
        return self.list_events(draft_id=draft_id)

    def record_send_attempt(self, attempt: SendAttempt):
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO send_attempts (
                    attempt_id, send_key, draft_id, revision,
                    provider, recipient_email, sender_email, status,
                    error_type, error_message, provider_message_id,
                    attempted_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.send_key,
                    attempt.draft_id,
                    attempt.revision,
                    attempt.provider,
                    attempt.recipient_email,
                    attempt.sender_email,
                    attempt.status,
                    attempt.error_type,
                    attempt.error_message,
                    attempt.provider_message_id,
                    attempt.attempted_at,
                    attempt.completed_at,
                ),
            )

    def get_send_attempts(self, draft_id: Optional[str] = None, limit: int = 100) -> List[SendAttempt]:
        with self._get_connection() as conn:
            if draft_id:
                rows = conn.execute(
                    "SELECT * FROM send_attempts WHERE draft_id = ? ORDER BY attempted_at DESC LIMIT ?",
                    (draft_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM send_attempts ORDER BY attempted_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        attempts = []
        for r in rows:
            attempts.append(
                SendAttempt(
                    attempt_id=r["attempt_id"],
                    send_key=r["send_key"],
                    draft_id=r["draft_id"],
                    revision=r["revision"],
                    provider=r["provider"],
                    recipient_email=r["recipient_email"],
                    sender_email=r["sender_email"],
                    status=r["status"],
                    error_type=r["error_type"],
                    error_message=r["error_message"],
                    provider_message_id=r["provider_message_id"],
                    attempted_at=r["attempted_at"],
                    completed_at=r["completed_at"],
                )
            )
        return attempts

    def has_successful_send(self, send_key: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM send_attempts WHERE send_key = ? AND status = 'sent' LIMIT 1",
                (send_key,),
            ).fetchone()
            if row:
                return True
            # Also check if idempotency lock is permanently 'sent'
            lock_row = conn.execute(
                "SELECT 1 FROM send_idempotency_locks WHERE send_key = ? AND status = 'sent' LIMIT 1",
                (send_key,),
            ).fetchone()
            return bool(lock_row)

    def acquire_send_lock(self, send_key: str, draft_id: str, revision: int) -> bool:
        """
        Transactional idempotency & concurrency lock:
        Attempts to insert into send_idempotency_locks with status='sending'.
        Fails if another send is active or has already completed for this send_key.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO send_idempotency_locks (send_key, draft_id, revision, status, locked_at)
                        VALUES (?, ?, ?, 'sending', ?)
                        """,
                        (send_key, draft_id, revision, now_iso),
                    )
                    return True
                except sqlite3.IntegrityError:
                    return False

    def mark_send_lock_sent(self, send_key: str):
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "UPDATE send_idempotency_locks SET status = 'sent' WHERE send_key = ?",
                (send_key,),
            )

    def release_send_lock(self, send_key: str):
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "DELETE FROM send_idempotency_locks WHERE send_key = ? AND status != 'sent'",
                (send_key,),
            )

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> StoredDraft:
        return StoredDraft(
            draft_id=row["draft_id"],
            revision=row["revision"],
            lead_id=row["lead_id"],
            contact_id=row["contact_id"],
            recipient_name=row["recipient_name"],
            recipient_title=row["recipient_title"],
            recipient_email=row["recipient_email"],
            sender_email=row["sender_email"],
            subject=row["subject"],
            body=row["body"],
            service_used=row["service_used"] or "",
            personalization_notes=row["personalization_notes"],
            evidence_refs=json.loads(row["evidence_refs"]) if row["evidence_refs"] else [],
            source_job_urls=json.loads(row["source_job_urls"]) if row["source_job_urls"] else [],
            language=row["language"] or "en",
            tone=row["tone"] or "professional_concise",
            prompt_version=row["prompt_version"] or "outreach_v1",
            generation_model=row["generation_model"] or "qwen2.5:1.5b",
            approval_status=row["approval_status"],
            send_status=row["send_status"],
            outreach_status=row["outreach_status"],
            content_hash=row["content_hash"],
            approved_content_hash=row["approved_content_hash"],
            reviewer=row["reviewer"],
            review_note=row["review_note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            sent_at=row["sent_at"],
        )
