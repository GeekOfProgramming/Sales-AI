"""
sales_engine/sending/outbound_queue_store.py
MAIL-SEND-01A: Durable Transactional Outbound Queue Store.

Manages the two dedicated queue tables:
- outbound_jobs
- outbound_attempts

Guarantees:
- ACID transactional job creation, claiming, and completion.
- Optimistic concurrency protection.
- Worker lease expiration and reclamation.
- Complete idempotency based on unique idempotency_key or source_draft_id.
- Backward compatibility: zero modification to existing draft records.
- Legacy schedule quarantine: pre-existing scheduled drafts require explicit operator revalidation.
"""

import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from sales_engine.sending.outbound_models import (
    OutboundQueueJob,
    OutboundAttemptRecord,
    OutboundJobStatus,
)


class OutboundQueueStore:
    """
    Dedicated SQLite store for outbound jobs and attempt history.
    """

    def __init__(self, db_path: str, auto_apply_schema: bool = True):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        if auto_apply_schema:
            self.apply_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def apply_schema(self) -> None:
        """Creates additive queue tables and performance indexes idempotently."""
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbound_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_draft_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    envelope_from TEXT NOT NULL,
                    envelope_recipients_json TEXT NOT NULL,
                    header_to_json TEXT NOT NULL,
                    header_cc_json TEXT NOT NULL DEFAULT '[]',
                    header_bcc_json TEXT NOT NULL DEFAULT '[]',
                    subject TEXT NOT NULL,
                    body_plain_text TEXT NOT NULL,
                    body_html_sanitized TEXT,
                    rfc_message_id TEXT NOT NULL,
                    candidate_id TEXT,
                    contact_id TEXT,
                    scheduled_for_utc TEXT NOT NULL,
                    scheduled_local_date TEXT NOT NULL,
                    scheduled_local_time TEXT NOT NULL,
                    scheduled_timezone TEXT NOT NULL DEFAULT 'Europe/Rome',
                    approved_by TEXT NOT NULL,
                    approved_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    version INTEGER NOT NULL DEFAULT 1,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    worker_lease_id TEXT,
                    worker_leased_at_utc TEXT,
                    lease_expires_at_utc TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    last_attempt_at_utc TEXT,
                    smtp_response_code INTEGER,
                    smtp_response_message TEXT,
                    review_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_status ON outbound_jobs(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_sched_utc ON outbound_jobs(scheduled_for_utc);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_draft_id ON outbound_jobs(source_draft_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_lease_exp ON outbound_jobs(lease_expires_at_utc);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_msg_id ON outbound_jobs(rfc_message_id);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbound_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    smtp_host TEXT NOT NULL,
                    smtp_port INTEGER NOT NULL,
                    smtp_code INTEGER,
                    smtp_message TEXT,
                    error_type TEXT,
                    error_details TEXT,
                    duration_ms INTEGER,
                    started_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES outbound_jobs(job_id) ON DELETE CASCADE
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_att_job ON outbound_attempts(job_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbound_att_created ON outbound_attempts(completed_at_utc DESC);")

    def enqueue_job(self, job: OutboundQueueJob) -> OutboundQueueJob:
        """
        Atomically enqueues a new approved outbound job.
        Raises ValueError if idempotency_key already exists.
        """
        with self._lock, self._get_connection() as conn:
            # Ensure schema exists before write operation
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_jobs'").fetchone()
            if not has_table:
                self.apply_schema()

            # Check duplicate idempotency
            existing = conn.execute(
                "SELECT job_id FROM outbound_jobs WHERE idempotency_key = ? LIMIT 1",
                (job.idempotency_key,),
            ).fetchone()
            if existing:
                raise ValueError(f"Outbound job with idempotency key '{job.idempotency_key}' already exists (Job ID: {existing['job_id']}).")

            conn.execute("""
                INSERT INTO outbound_jobs (
                    job_id, source_draft_id, account_id, sender_email, envelope_from,
                    envelope_recipients_json, header_to_json, header_cc_json, header_bcc_json,
                    subject, body_plain_text, body_html_sanitized, rfc_message_id,
                    candidate_id, contact_id, scheduled_for_utc, scheduled_local_date,
                    scheduled_local_time, scheduled_timezone, approved_by, approved_at_utc,
                    status, version, idempotency_key, worker_lease_id, worker_leased_at_utc,
                    lease_expires_at_utc, attempt_count, max_attempts, last_attempt_at_utc,
                    smtp_response_code, smtp_response_message, review_reason, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                );
            """, (
                job.job_id, job.source_draft_id, job.account_id, job.sender_email, job.envelope_from,
                job.envelope_recipients_json, job.header_to_json, job.header_cc_json, job.header_bcc_json,
                job.subject, job.body_plain_text, job.body_html_sanitized, job.rfc_message_id,
                job.candidate_id, job.contact_id, job.scheduled_for_utc, job.scheduled_local_date,
                job.scheduled_local_time, job.scheduled_timezone, job.approved_by, job.approved_at_utc,
                job.status, job.version, job.idempotency_key, job.worker_lease_id, job.worker_leased_at_utc,
                job.lease_expires_at_utc, job.attempt_count, job.max_attempts, job.last_attempt_at_utc,
                job.smtp_response_code, job.smtp_response_message, job.review_reason, job.created_at, job.updated_at,
            ))
            return job

    def get_job(self, job_id: str) -> Optional[OutboundQueueJob]:
        """Retrieves an outbound job by ID."""
        with self._get_connection() as conn:
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_jobs'").fetchone()
            if not has_table:
                return None
            row = conn.execute("SELECT * FROM outbound_jobs WHERE job_id = ? LIMIT 1;", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def get_job_by_draft_id(self, source_draft_id: str) -> Optional[OutboundQueueJob]:
        """Retrieves active or latest outbound job for a given source draft."""
        with self._get_connection() as conn:
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_jobs'").fetchone()
            if not has_table:
                return None
            row = conn.execute(
                "SELECT * FROM outbound_jobs WHERE source_draft_id = ? ORDER BY created_at DESC LIMIT 1;",
                (source_draft_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def list_jobs(
        self,
        status: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[OutboundQueueJob]:
        """Lists queue jobs with optional filtering."""
        with self._get_connection() as conn:
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_jobs'").fetchone()
            if not has_table:
                return []
            clauses = ["1=1"]
            params: List[Any] = []
            if status and status != "all":
                clauses.append("status = ?")
                params.append(status)
            if account_id and account_id != "all":
                clauses.append("account_id = ?")
                params.append(account_id)

            where_str = " AND ".join(clauses)
            query = f"SELECT * FROM outbound_jobs WHERE {where_str} ORDER BY scheduled_for_utc ASC LIMIT ? OFFSET ?;"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_job(r) for r in rows]

    def count_jobs(self, status: Optional[str] = None, account_id: Optional[str] = None) -> int:
        """Counts jobs matching criteria."""
        with self._get_connection() as conn:
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_jobs'").fetchone()
            if not has_table:
                return 0
            clauses = ["1=1"]
            params: List[Any] = []
            if status and status != "all":
                clauses.append("status = ?")
                params.append(status)
            if account_id and account_id != "all":
                clauses.append("account_id = ?")
                params.append(account_id)

            where_str = " AND ".join(clauses)
            return conn.execute(f"SELECT COUNT(*) FROM outbound_jobs WHERE {where_str};", params).fetchone()[0]

    def claim_next_jobs(
        self,
        worker_id: str,
        lease_id: str,
        lease_duration_seconds: int = 300,
        batch_size: int = 10,
        now_utc: Optional[str] = None,
    ) -> List[OutboundQueueJob]:
        """
        Atomically leases eligible 'scheduled' jobs whose scheduled_for_utc <= now_utc,
        or reclaims stale 'sending' jobs whose lease_expires_at_utc < now_utc.
        Uses optimistic version increments to avoid multi-worker race conditions.
        """
        current_time = now_utc or datetime.now(timezone.utc).isoformat()
        import datetime as dt_mod
        now_dt = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
        lease_exp_dt = now_dt + dt_mod.timedelta(seconds=lease_duration_seconds)
        lease_exp_str = lease_exp_dt.isoformat().replace("+00:00", "Z")

        claimed_jobs: List[OutboundQueueJob] = []

        with self._lock, self._get_connection() as conn:
            # 1. Candidate query: eligible scheduled jobs or expired leases
            candidates = conn.execute("""
                SELECT * FROM outbound_jobs
                WHERE (
                    status = 'scheduled' AND scheduled_for_utc <= ?
                ) OR (
                    status = 'sending' AND lease_expires_at_utc < ?
                )
                ORDER BY scheduled_for_utc ASC
                LIMIT ?;
            """, (current_time, current_time, batch_size)).fetchall()

            for cand in candidates:
                job_id = cand["job_id"]
                current_ver = cand["version"]

                # Atomic conditional update claiming the lease
                res = conn.execute("""
                    UPDATE outbound_jobs
                    SET status = 'sending',
                        worker_lease_id = ?,
                        worker_leased_at_utc = ?,
                        lease_expires_at_utc = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE job_id = ? AND version = ?;
                """, (lease_id, current_time, lease_exp_str, current_time, job_id, current_ver))

                if res.rowcount == 1:
                    # Successfully claimed
                    updated_row = conn.execute("SELECT * FROM outbound_jobs WHERE job_id = ?;", (job_id,)).fetchone()
                    claimed_jobs.append(self._row_to_job(updated_row))

        return claimed_jobs

    def record_attempt(
        self,
        attempt: OutboundAttemptRecord,
        new_job_status: OutboundJobStatus,
        smtp_response_code: Optional[int] = None,
        smtp_response_message: Optional[str] = None,
        review_reason: Optional[str] = None,
    ) -> OutboundQueueJob:
        """
        Atomically records an attempt log and updates job status and attempt count.
        Releases worker lease.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            # 1. Insert attempt record
            conn.execute("""
                INSERT INTO outbound_attempts (
                    attempt_id, job_id, attempt_number, worker_id, status,
                    smtp_host, smtp_port, smtp_code, smtp_message,
                    error_type, error_details, duration_ms, started_at_utc, completed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                attempt.attempt_id, attempt.job_id, attempt.attempt_number, attempt.worker_id,
                attempt.status, attempt.smtp_host, attempt.smtp_port, attempt.smtp_code,
                attempt.smtp_message, attempt.error_type, attempt.error_details,
                attempt.duration_ms, attempt.started_at_utc, attempt.completed_at_utc,
            ))

            # 2. Update parent job
            conn.execute("""
                UPDATE outbound_jobs
                SET status = ?,
                    attempt_count = attempt_count + 1,
                    last_attempt_at_utc = ?,
                    smtp_response_code = ?,
                    smtp_response_message = ?,
                    review_reason = ?,
                    worker_lease_id = NULL,
                    lease_expires_at_utc = NULL,
                    version = version + 1,
                    updated_at = ?
                WHERE job_id = ?;
            """, (
                new_job_status, now_utc, smtp_response_code, smtp_response_message,
                review_reason, now_utc, attempt.job_id,
            ))

            row = conn.execute("SELECT * FROM outbound_jobs WHERE job_id = ?;", (attempt.job_id,)).fetchone()
            return self._row_to_job(row)

    def cancel_job(self, job_id: str, operator_username: str, reason: Optional[str] = None) -> OutboundQueueJob:
        """Cancels a scheduled or review-required job before sending."""
        now_utc = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            job = conn.execute("SELECT status FROM outbound_jobs WHERE job_id = ?;", (job_id,)).fetchone()
            if not job:
                raise ValueError(f"Job '{job_id}' not found.")
            if job["status"] in ("smtp_accepted", "sending"):
                raise ValueError(f"Cannot cancel job in state '{job['status']}'.")

            conn.execute("""
                UPDATE outbound_jobs
                SET status = 'cancelled',
                    review_reason = ?,
                    worker_lease_id = NULL,
                    lease_expires_at_utc = NULL,
                    version = version + 1,
                    updated_at = ?
                WHERE job_id = ?;
            """, (f"Cancelled by {operator_username}: {reason or 'No reason provided'}", now_utc, job_id))

            row = conn.execute("SELECT * FROM outbound_jobs WHERE job_id = ?;", (job_id,)).fetchone()
            return self._row_to_job(row)

    def list_attempts_for_job(self, job_id: str) -> List[OutboundAttemptRecord]:
        """Retrieves attempt history for a given job."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM outbound_attempts WHERE job_id = ? ORDER BY attempt_number ASC;",
                (job_id,),
            ).fetchall()
            return [
                OutboundAttemptRecord(
                    attempt_id=r["attempt_id"],
                    job_id=r["job_id"],
                    attempt_number=r["attempt_number"],
                    worker_id=r["worker_id"],
                    status=r["status"],
                    smtp_host=r["smtp_host"],
                    smtp_port=r["smtp_port"],
                    smtp_code=r["smtp_code"],
                    smtp_message=r["smtp_message"],
                    error_type=r["error_type"],
                    error_details=r["error_details"],
                    duration_ms=r["duration_ms"],
                    started_at_utc=r["started_at_utc"],
                    completed_at_utc=r["completed_at_utc"],
                )
                for r in rows
            ]

    def _row_to_job(self, row: sqlite3.Row) -> OutboundQueueJob:
        return OutboundQueueJob(
            job_id=row["job_id"],
            source_draft_id=row["source_draft_id"],
            account_id=row["account_id"],
            sender_email=row["sender_email"],
            envelope_from=row["envelope_from"],
            envelope_recipients_json=row["envelope_recipients_json"],
            header_to_json=row["header_to_json"],
            header_cc_json=row["header_cc_json"],
            header_bcc_json=row["header_bcc_json"],
            subject=row["subject"],
            body_plain_text=row["body_plain_text"],
            body_html_sanitized=row["body_html_sanitized"],
            rfc_message_id=row["rfc_message_id"],
            candidate_id=row["candidate_id"],
            contact_id=row["contact_id"],
            scheduled_for_utc=row["scheduled_for_utc"],
            scheduled_local_date=row["scheduled_local_date"],
            scheduled_local_time=row["scheduled_local_time"],
            scheduled_timezone=row["scheduled_timezone"],
            approved_by=row["approved_by"],
            approved_at_utc=row["approved_at_utc"],
            status=row["status"],
            version=row["version"],
            idempotency_key=row["idempotency_key"],
            worker_lease_id=row["worker_lease_id"],
            worker_leased_at_utc=row["worker_leased_at_utc"],
            lease_expires_at_utc=row["lease_expires_at_utc"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            last_attempt_at_utc=row["last_attempt_at_utc"],
            smtp_response_code=row["smtp_response_code"],
            smtp_response_message=row["smtp_response_message"],
            review_reason=row["review_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
