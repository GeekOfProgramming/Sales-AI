"""Unit and integration tests for BACKUP-01 SQLite Automated Backup & Restore Verification."""

import os
import sys
import sqlite3
import tempfile
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.candidates.backup_engine import (
    resolve_configured_db_path,
    compute_streaming_sha256,
    generate_collision_resistant_backup_filename,
    create_sqlite_online_backup,
    verify_sqlite_backup_integrity,
    get_table_counts,
    verify_restore_in_disposable_clone,
)
from sales_engine.candidates.candidate_store import CandidateStore


def test_powershell_backup_launcher_syntax():
    """Verify backup_sales_ai.ps1 has zero syntax errors according to PowerShell AST."""
    ps1_path = REPO_ROOT / "backup_sales_ai.ps1"
    assert ps1_path.exists(), "backup_sales_ai.ps1 must exist"

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        f"& {{ $errors = $null; [System.Management.Automation.Language.Parser]::ParseInput((Get-Content '{str(ps1_path)}' -Raw), [ref]$null, [ref]$errors); if ($errors) {{ exit 1 }} else {{ exit 0 }} }}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"PowerShell parser found errors: {res.stdout} {res.stderr}"


def test_streaming_sha256_matches_standard_hash(tmp_path):
    """Verify compute_streaming_sha256 calculates accurate SHA-256 without memory ballooning."""
    import hashlib
    test_file = tmp_path / "test_data.bin"
    payload = b"SalesAI backup payload line 1234567890" * 1000
    test_file.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()
    actual = compute_streaming_sha256(test_file, chunk_size=256)
    assert actual == expected


def test_collision_resistant_filename_uniqueness():
    """Verify generate_collision_resistant_backup_filename produces unique names across rapid calls."""
    src = Path("data/sales_outreach.db")
    fn1 = generate_collision_resistant_backup_filename(src, prefix="auto")
    fn2 = generate_collision_resistant_backup_filename(src, prefix="auto")
    assert fn1 != fn2
    assert "auto_" in fn1
    assert fn1.endswith(".db")


def test_online_backup_of_sample_database(tmp_path):
    """Verify create_sqlite_online_backup performs atomic online backup from read-only connection."""
    # 1. Create a dummy sqlite database
    src_db = tmp_path / "test_source.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob');")
    conn.commit()
    conn.close()

    dest_dir = tmp_path / "backups"
    result = create_sqlite_online_backup(source_db_path=src_db, dest_dir=dest_dir, mode="manual")

    assert result.success is True
    assert Path(result.backup_path).exists()
    assert result.backup_size_bytes > 0
    assert result.integrity_status == "ok"
    assert result.fk_violations_count == 0
    assert len(result.sha256_checksum) == 64

    # Verify data in backup
    b_conn = sqlite3.connect(result.backup_path)
    count = b_conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
    b_conn.close()
    assert count == 2


def test_backup_handles_missing_source_cleanly(tmp_path):
    """Verify create_sqlite_online_backup fails cleanly when source does not exist."""
    missing_src = tmp_path / "nonexistent.db"
    dest_dir = tmp_path / "backups"
    result = create_sqlite_online_backup(source_db_path=missing_src, dest_dir=dest_dir)

    assert result.success is False
    assert result.integrity_status == "SOURCE_MISSING"
    assert "does not exist" in result.error_message


def test_restore_verification_detects_corrupt_backup(tmp_path):
    """Verify verify_sqlite_backup_integrity detects corrupt SQLite files."""
    corrupt_file = tmp_path / "corrupt.db"
    corrupt_file.write_bytes(b"SQLite format 3\x00this is corrupt garbage data 1234567890")

    status, fks = verify_sqlite_backup_integrity(corrupt_file)
    assert status != "ok"


def test_verify_restore_in_disposable_clone_matches_counts(tmp_path):
    """Verify verify_restore_in_disposable_clone clones to temp location and compares baseline counts."""
    # Setup test DB with matching key tables
    src_db = tmp_path / "test_src.db"
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE candidate_companies (id TEXT PRIMARY KEY, name TEXT);")
    conn.execute("CREATE TABLE candidate_contacts (id TEXT PRIMARY KEY, email TEXT);")
    conn.execute("INSERT INTO candidate_companies VALUES ('c1', 'Acme');")
    conn.execute("INSERT INTO candidate_contacts VALUES ('ct1', 'info@acme.com');")
    conn.commit()
    conn.close()

    backup_res = create_sqlite_online_backup(source_db_path=src_db, dest_dir=tmp_path / "b", mode="manual")
    assert backup_res.success is True

    restore_res = verify_restore_in_disposable_clone(
        backup_path=backup_res.backup_path,
        expected_tables=["candidate_companies", "candidate_contacts"],
    )
    assert restore_res["success"] is True
    assert restore_res["integrity_status"] == "ok"
    assert restore_res["restored_counts"]["candidate_companies"] == 1
    assert restore_res["restored_counts"]["candidate_contacts"] == 1
    assert len(restore_res["count_mismatches"]) == 0


def test_candidate_store_delegation_to_backup_engine(tmp_path):
    """Verify CandidateStore.create_backup and verify_backup delegate cleanly to backup_engine."""
    db_file = tmp_path / "store_test.db"
    store = CandidateStore(db_path=str(db_file))

    b_path, b_sha = store.create_backup(backup_dir=tmp_path / "store_backups")
    assert Path(b_path).exists()
    assert len(b_sha) == 64

    is_valid, err = store.verify_backup(b_path)
    assert is_valid is True
    assert err is None
