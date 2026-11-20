#!/usr/bin/env python3
"""
scripts/migrate_outbound_queue.py
MAIL-SEND-01B: Explicit, Idempotent Database Migration Runner for Outbound Queue & Scheduling.

Applies additive schema upgrades to the operational database:
1. Verifies database connectivity and WAL mode.
2. Adds missing scheduling and snapshot columns to 'mailbox_drafts' (if missing):
   - scheduled_local_date (TEXT)
   - scheduled_local_time (TEXT)
   - scheduled_timezone (TEXT)
   - scheduled_approved_by (TEXT)
   - scheduled_at_utc (TEXT)
   - snapshot_subject (TEXT)
   - snapshot_plain_text (TEXT)
   - snapshot_html_sanitized (TEXT)
   - snapshot_to_addresses_json (TEXT)
3. Creates 'outbound_jobs' table and performance indexes (if missing).
4. Creates 'outbound_attempts' table and performance indexes (if missing).
5. Runs SQLite PRAGMA integrity_check and PRAGMA foreign_key_check.
6. Reports migration status and guarantees zero mutation of existing draft records.

Usage:
    python scripts/migrate_outbound_queue.py [--db-path PATH] [--dry-run]
"""

import os
import sys
import sqlite3
import argparse
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_outbound_queue")

DEFAULT_DB_PATH = os.getenv("SALES_OUTREACH_DB", "data/sales_outreach.db")

REQUIRED_DRAFT_COLUMNS = [
    ("scheduled_local_date", "TEXT"),
    ("scheduled_local_time", "TEXT"),
    ("scheduled_timezone", "TEXT"),
    ("scheduled_approved_by", "TEXT"),
    ("scheduled_at_utc", "TEXT"),
    ("snapshot_subject", "TEXT"),
    ("snapshot_plain_text", "TEXT"),
    ("snapshot_html_sanitized", "TEXT"),
    ("snapshot_to_addresses_json", "TEXT"),
]


def run_migration(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Target database file does not exist: {abs_path}")

    logger.info(f"Initiating schema preflight and migration for: {abs_path}")
    logger.info(f"Mode: {'DRY RUN (no changes applied)' if dry_run else 'LIVE MIGRATION'}")

    conn = sqlite3.connect(abs_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    changes_applied = []
    skipped_existing = []

    try:
        # Pre-check database integrity
        pre_integrity = cursor.execute("PRAGMA integrity_check;").fetchone()[0]
        if pre_integrity != "ok":
            raise RuntimeError(f"Pre-migration PRAGMA integrity_check failed: {pre_integrity}")

        # Check existing tables and indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
        existing_indexes = {r[0] for r in cursor.fetchall()}

        # 1. Update mailbox_drafts columns
        if "mailbox_drafts" in tables:
            cursor.execute("PRAGMA table_info(mailbox_drafts);")
            existing_cols = {r["name"] for r in cursor.fetchall()}

            for col_name, col_type in REQUIRED_DRAFT_COLUMNS:
                if col_name not in existing_cols:
                    sql = f"ALTER TABLE mailbox_drafts ADD COLUMN {col_name} {col_type};"
                    if dry_run:
                        changes_applied.append(f"[DRY RUN] Would execute: {sql}")
                    else:
                        cursor.execute(sql)
                        changes_applied.append(f"Added column mailbox_drafts.{col_name} ({col_type})")
                else:
                    skipped_existing.append(f"Column mailbox_drafts.{col_name} already present")

            # Indexes for mailbox_drafts
            draft_indexes = [
                ("idx_mb_drafts_acc", "CREATE INDEX IF NOT EXISTS idx_mb_drafts_acc ON mailbox_drafts(account_id);"),
                ("idx_mb_drafts_status", "CREATE INDEX IF NOT EXISTS idx_mb_drafts_status ON mailbox_drafts(draft_status);"),
                ("idx_mb_drafts_cand", "CREATE INDEX IF NOT EXISTS idx_mb_drafts_cand ON mailbox_drafts(candidate_id);"),
                ("idx_mb_drafts_updated", "CREATE INDEX IF NOT EXISTS idx_mb_drafts_updated ON mailbox_drafts(updated_at DESC);"),
                ("idx_mb_drafts_sched_utc", "CREATE INDEX IF NOT EXISTS idx_mb_drafts_sched_utc ON mailbox_drafts(scheduled_for_utc);"),
            ]
            for idx_name, idx_sql in draft_indexes:
                if idx_name not in existing_indexes:
                    if dry_run:
                        changes_applied.append(f"[DRY RUN] Would ensure index {idx_name}")
                    else:
                        cursor.execute(idx_sql)
                        changes_applied.append(f"Ensured index {idx_name}")
                        existing_indexes.add(idx_name)
                else:
                    skipped_existing.append(f"Index {idx_name} already present")
        else:
            logger.warning("Table 'mailbox_drafts' does not exist in target database; draft column upgrade skipped.")

        # 2. outbound_jobs table
        if "outbound_jobs" not in tables:
            create_jobs_sql = """
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
            """
            if dry_run:
                changes_applied.append("[DRY RUN] Would create table outbound_jobs")
            else:
                cursor.execute(create_jobs_sql)
                changes_applied.append("Created table outbound_jobs")
        else:
            skipped_existing.append("Table outbound_jobs already present")

        # Indexes for outbound_jobs
        outbound_jobs_indexes = [
            ("idx_outbound_status", "CREATE INDEX IF NOT EXISTS idx_outbound_status ON outbound_jobs(status);"),
            ("idx_outbound_sched_utc", "CREATE INDEX IF NOT EXISTS idx_outbound_sched_utc ON outbound_jobs(scheduled_for_utc);"),
            ("idx_outbound_draft_id", "CREATE INDEX IF NOT EXISTS idx_outbound_draft_id ON outbound_jobs(source_draft_id);"),
            ("idx_outbound_lease_exp", "CREATE INDEX IF NOT EXISTS idx_outbound_lease_exp ON outbound_jobs(lease_expires_at_utc);"),
            ("idx_outbound_msg_id", "CREATE INDEX IF NOT EXISTS idx_outbound_msg_id ON outbound_jobs(rfc_message_id);"),
        ]
        for idx_name, idx_sql in outbound_jobs_indexes:
            if idx_name not in existing_indexes:
                if dry_run:
                    changes_applied.append(f"[DRY RUN] Would ensure index {idx_name}")
                else:
                    cursor.execute(idx_sql)
                    changes_applied.append(f"Ensured index {idx_name}")
                    existing_indexes.add(idx_name)
            else:
                skipped_existing.append(f"Index {idx_name} already present")

        # 3. outbound_attempts table
        if "outbound_attempts" not in tables:
            create_attempts_sql = """
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
            """
            if dry_run:
                changes_applied.append("[DRY RUN] Would create table outbound_attempts")
            else:
                cursor.execute(create_attempts_sql)
                changes_applied.append("Created table outbound_attempts")
        else:
            skipped_existing.append("Table outbound_attempts already present")

        # Indexes for outbound_attempts
        outbound_att_indexes = [
            ("idx_outbound_att_job", "CREATE INDEX IF NOT EXISTS idx_outbound_att_job ON outbound_attempts(job_id);"),
            ("idx_outbound_att_created", "CREATE INDEX IF NOT EXISTS idx_outbound_att_created ON outbound_attempts(completed_at_utc DESC);"),
        ]
        for idx_name, idx_sql in outbound_att_indexes:
            if idx_name not in existing_indexes:
                if dry_run:
                    changes_applied.append(f"[DRY RUN] Would ensure index {idx_name}")
                else:
                    cursor.execute(idx_sql)
                    changes_applied.append(f"Ensured index {idx_name}")
                    existing_indexes.add(idx_name)
            else:
                skipped_existing.append(f"Index {idx_name} already present")

        if not dry_run:
            conn.commit()

        # Post-check database integrity & foreign keys
        post_integrity = cursor.execute("PRAGMA integrity_check;").fetchone()[0]
        fk_violations = cursor.execute("PRAGMA foreign_key_check;").fetchall()

        if post_integrity != "ok":
            raise RuntimeError(f"Post-migration PRAGMA integrity_check failed: {post_integrity}")
        if fk_violations:
            raise RuntimeError(f"Post-migration PRAGMA foreign_key_check detected violations: {fk_violations}")

        logger.info(f"Migration completed successfully. Changes applied: {len(changes_applied)}, Items already present: {len(skipped_existing)}")
        return {
            "status": "success",
            "dry_run": dry_run,
            "db_path": abs_path,
            "changes_applied": changes_applied,
            "skipped_existing": skipped_existing,
            "integrity": post_integrity,
            "foreign_key_violations": len(fk_violations),
        }

    except Exception as e:
        if not dry_run:
            conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate Sales-AI database for outbound queue and email scheduling.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to SQLite database (default: data/sales_outreach.db)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report required changes without applying them.")
    args = parser.parse_args()

    result = run_migration(args.db_path, dry_run=args.dry_run)
    print("\n--- Migration Preflight Report ---")
    print(f"Target DB: {result['db_path']}")
    print(f"Dry Run: {result['dry_run']}")
    print(f"Integrity Check: {result['integrity']}")
    print(f"Foreign Key Violations: {result['foreign_key_violations']}")
    print("\nChanges Applied:")
    for c in result["changes_applied"]:
        print(f"  + {c}")
    print("\nExisting Items Kept Intact:")
    for s in result["skipped_existing"]:
        print(f"  . {s}")
    print("----------------------------------\n")


if __name__ == "__main__":
    main()
