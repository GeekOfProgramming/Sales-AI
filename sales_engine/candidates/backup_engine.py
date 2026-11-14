"""
SalesAI Backup & Verification Engine (BACKUP-01)
Provides robust online SQLite backups, collision-resistant naming,
streaming SHA-256 calculation, PRAGMA integrity & FK verification,
and disposable restore verification.
"""

from __future__ import annotations

import os
import sys
import hashlib
import json
import logging
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_REL = "data/sales_outreach.db"
DEFAULT_AUTOMATIC_BACKUP_DIR = REPO_ROOT / "data" / "backups" / "automatic"
DEFAULT_MANUAL_BACKUP_DIR = REPO_ROOT / "data" / "backups"


@dataclass
class BackupResult:
    success: bool
    source_db_path: str
    backup_path: str
    backup_size_bytes: int
    timestamp_utc: str
    sha256_checksum: str
    integrity_status: str
    fk_violations_count: int
    fk_violations: List[str]
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_configured_db_path() -> Path:
    """
    Resolves the authoritative database path from environment variables or defaults,
    matching application precedence:
    1. SALES_OUTREACH_DB
    2. SALESAI_DB_PATH
    3. SALESAI_CANDIDATES_DB
    4. data/sales_outreach.db
    """
    env_path = (
        os.getenv("SALES_OUTREACH_DB")
        or os.getenv("SALESAI_DB_PATH")
        or os.getenv("SALESAI_CANDIDATES_DB")
    )
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p.resolve()
    return (REPO_ROOT / DEFAULT_DB_REL).resolve()


def compute_streaming_sha256(file_path: Union[str, Path], chunk_size: int = 1024 * 1024) -> str:
    """
    Computes SHA-256 hash by streaming chunks to avoid loading entire DB files into memory.
    """
    p = Path(file_path)
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_collision_resistant_backup_filename(source_db_path: Path, prefix: str = "") -> str:
    """
    Generates collision-resistant filename formatted as:
    [prefix_]{source_stem}_backup_YYYYMMDD_HHMMSS_UTC_<unique_suffix>.db
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    unique_suffix = uuid.uuid4().hex[:8]
    pfx = f"{prefix}_" if prefix else ""
    return f"{pfx}{source_db_path.stem}_backup_{ts}_UTC_{unique_suffix}{source_db_path.suffix}"


def create_sqlite_online_backup(
    source_db_path: Optional[Union[str, Path]] = None,
    dest_dir: Optional[Union[str, Path]] = None,
    mode: str = "manual",  # "manual" or "automatic"
) -> BackupResult:
    """
    Executes a safe online SQLite backup using sqlite3.backup() API from a read-only source.
    Validates integrity and foreign keys. Fails safely on errors. Never overwrites.
    """
    src_path = Path(source_db_path).resolve() if source_db_path else resolve_configured_db_path()
    
    if not src_path.exists():
        err = f"Source database file does not exist: {src_path}"
        logger.error(err)
        return BackupResult(
            success=False,
            source_db_path=str(src_path),
            backup_path="",
            backup_size_bytes=0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sha256_checksum="",
            integrity_status="SOURCE_MISSING",
            fk_violations_count=0,
            fk_violations=[],
            error_message=err,
        )

    # Determine destination directory
    if dest_dir:
        destination_dir = Path(dest_dir).resolve()
    else:
        if mode == "automatic":
            destination_dir = DEFAULT_AUTOMATIC_BACKUP_DIR.resolve()
        else:
            destination_dir = DEFAULT_MANUAL_BACKUP_DIR.resolve()

    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        err = f"Failed to create backup directory '{destination_dir}': {e}"
        logger.error(err)
        return BackupResult(
            success=False,
            source_db_path=str(src_path),
            backup_path="",
            backup_size_bytes=0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sha256_checksum="",
            integrity_status="DIR_CREATE_FAILED",
            fk_violations_count=0,
            fk_violations=[],
            error_message=err,
        )

    # Generate collision-resistant filename and verify non-existence
    backup_filename = generate_collision_resistant_backup_filename(src_path, prefix="auto" if mode == "automatic" else "manual")
    dest_path = destination_dir / backup_filename
    if dest_path.exists():
        err = f"Target backup file collision detected: {dest_path}"
        logger.error(err)
        return BackupResult(
            success=False,
            source_db_path=str(src_path),
            backup_path=str(dest_path),
            backup_size_bytes=0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sha256_checksum="",
            integrity_status="COLLISION_ERROR",
            fk_violations_count=0,
            fk_violations=[],
            error_message=err,
        )

    temp_dest_path = dest_path.with_suffix(".tmp_backup")
    if temp_dest_path.exists():
        temp_dest_path.unlink()

    timestamp_utc = datetime.now(timezone.utc).isoformat()

    try:
        # Use URI mode=ro to ensure source is never modified during backup
        src_uri = f"file:{src_path.as_posix()}?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True, timeout=30.0)
        dest_conn = sqlite3.connect(str(temp_dest_path), timeout=60.0)

        try:
            # Online backup API
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

        # Rename temporary backup file to final collision-resistant name
        temp_dest_path.replace(dest_path)

        # Verify PRAGMA integrity_check and foreign_key_check on completed backup
        integrity_status, fk_violations = verify_sqlite_backup_integrity(dest_path)
        if integrity_status != "ok":
            dest_path.unlink(missing_ok=True)
            err = f"Backup integrity check failed: {integrity_status}"
            logger.error(err)
            return BackupResult(
                success=False,
                source_db_path=str(src_path),
                backup_path=str(dest_path),
                backup_size_bytes=0,
                timestamp_utc=timestamp_utc,
                sha256_checksum="",
                integrity_status=integrity_status,
                fk_violations_count=len(fk_violations),
                fk_violations=fk_violations,
                error_message=err,
            )

        # Compute streaming checksum and size
        backup_size = dest_path.stat().st_size
        if backup_size == 0:
            dest_path.unlink(missing_ok=True)
            err = "Completed backup file is 0 bytes (partial/empty backup)"
            logger.error(err)
            return BackupResult(
                success=False,
                source_db_path=str(src_path),
                backup_path=str(dest_path),
                backup_size_bytes=0,
                timestamp_utc=timestamp_utc,
                sha256_checksum="",
                integrity_status="EMPTY_BACKUP",
                fk_violations_count=0,
                fk_violations=[],
                error_message=err,
            )

        sha256_sum = compute_streaming_sha256(dest_path)

        return BackupResult(
            success=True,
            source_db_path=str(src_path),
            backup_path=str(dest_path),
            backup_size_bytes=backup_size,
            timestamp_utc=timestamp_utc,
            sha256_checksum=sha256_sum,
            integrity_status=integrity_status,
            fk_violations_count=len(fk_violations),
            fk_violations=fk_violations,
            error_message=None,
        )

    except Exception as e:
        if temp_dest_path.exists():
            temp_dest_path.unlink(missing_ok=True)
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        err = f"Online SQLite backup failed: {e}"
        logger.exception(err)
        return BackupResult(
            success=False,
            source_db_path=str(src_path),
            backup_path=str(dest_path),
            backup_size_bytes=0,
            timestamp_utc=timestamp_utc,
            sha256_checksum="",
            integrity_status="BACKUP_EXCEPTION",
            fk_violations_count=0,
            fk_violations=[],
            error_message=err,
        )


def verify_sqlite_backup_integrity(backup_path: Union[str, Path]) -> Tuple[str, List[str]]:
    """
    Runs PRAGMA integrity_check and PRAGMA foreign_key_check on a backup file.
    Returns (integrity_status: str, fk_violations: List[str]).
    """
    p = Path(backup_path)
    if not p.exists():
        return "FILE_NOT_FOUND", []

    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30.0)
        try:
            cur = conn.cursor()
            # 1. Full integrity check
            rows = cur.execute("PRAGMA integrity_check;").fetchall()
            if not rows or rows[0][0] != "ok":
                res = "; ".join([r[0] for r in rows if r]) if rows else "no response"
                return f"FAILED: {res}", []

            # 2. Foreign key check
            fk_rows = cur.execute("PRAGMA foreign_key_check;").fetchall()
            violations = []
            if fk_rows:
                for row in fk_rows:
                    table_name = row[0] if len(row) > 0 else "unknown"
                    rowid = row[1] if len(row) > 1 else "unknown"
                    fktable = row[2] if len(row) > 2 else "unknown"
                    violations.append(f"Table '{table_name}' rowid {rowid} references '{fktable}'")
            return "ok", violations
        finally:
            conn.close()
    except Exception as e:
        return f"ERROR: {e}", []


def get_table_counts(db_path: Union[str, Path], tables: Optional[List[str]] = None) -> Dict[str, int]:
    """
    Queries row counts for specified or all sqlite_master tables in read-only mode.
    """
    p = Path(db_path)
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=30.0)
    counts: Dict[str, int] = {}
    try:
        cur = conn.cursor()
        if not tables:
            all_tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;").fetchall()]
        else:
            all_tables = tables

        for t in all_tables:
            try:
                c = cur.execute(f'SELECT COUNT(*) FROM "{t}";').fetchone()[0]
                counts[t] = c
            except Exception:
                counts[t] = -1
    finally:
        conn.close()
    return counts


def verify_restore_in_disposable_clone(
    backup_path: Union[str, Path],
    expected_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Clones the backup into an isolated temporary directory and verifies that:
    1. The clone can be opened and queried.
    2. PRAGMA integrity_check passes on the clone.
    3. PRAGMA foreign_key_check is evaluated.
    4. Table row counts match expected baseline counts.
    Never restores over live data.
    """
    b_path = Path(backup_path).resolve()
    if not b_path.exists():
        return {
            "success": False,
            "error": f"Backup file not found: {b_path}",
        }

    key_tables = expected_tables or [
        "candidate_companies",
        "candidate_contacts",
        "historical_sent_emails",
        "manual_outreach_log",
        "research_snapshots",
        "lead_register_records",
        "lead_register_field_reviews",
    ]

    # Baseline counts from backup
    baseline_counts = get_table_counts(b_path, key_tables)

    with tempfile.TemporaryDirectory(prefix="salesai_restore_verify_") as tmp_dir:
        disposable_db = Path(tmp_dir) / f"restored_{b_path.name}"

        # Copy/restore into disposable location
        src_conn = sqlite3.connect(f"file:{b_path.as_posix()}?mode=ro", uri=True)
        dest_conn = sqlite3.connect(str(disposable_db))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

        # Run integrity checks on restored copy
        restored_integrity, restored_fks = verify_sqlite_backup_integrity(disposable_db)
        if restored_integrity != "ok":
            return {
                "success": False,
                "disposable_db_path": str(disposable_db),
                "integrity_status": restored_integrity,
                "error": f"Restored clone failed integrity check: {restored_integrity}",
            }

        # Compare row counts
        restored_counts = get_table_counts(disposable_db, key_tables)
        count_mismatches = {}
        for t in key_tables:
            expected = baseline_counts.get(t, 0)
            actual = restored_counts.get(t, 0)
            if expected != actual:
                count_mismatches[t] = {"expected": expected, "actual": actual}

        success = len(count_mismatches) == 0 and restored_integrity == "ok"
        return {
            "success": success,
            "backup_source": str(b_path),
            "disposable_db_path": str(disposable_db),
            "integrity_status": restored_integrity,
            "fk_violations_count": len(restored_fks),
            "fk_violations": restored_fks,
            "baseline_counts": baseline_counts,
            "restored_counts": restored_counts,
            "count_mismatches": count_mismatches,
            "verified_tables_count": len(key_tables),
        }
