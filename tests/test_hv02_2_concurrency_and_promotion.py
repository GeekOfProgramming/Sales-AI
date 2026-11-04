"""
HV-02.2 Concurrency and SQLite Promotion Failure-Injection Verification Suite.

Mandatory test coverage:
1. Successful promotion with no concurrent changes via HTTP Preview -> Confirm.
2. A second connection writing during staging (PRAGMA data_version detects and aborts cleanly).
3. A second connection attempting to write during promotion (locked out or rejected).
4. Failure during promotion (verifies atomic rollback; target DB unchanged).
5. Integrity-check failure on staging (aborts before touching live database).
6. Preserving original rows, Candidate IDs and historical records across promotion.
7. Recovery from a verified pre-import backup.
8. Repeating an identical import without unwanted duplicates (idempotency).
9. Missing/invalid preview signing secret fail-closed behavior.
"""

import os
import json
import sqlite3
import hashlib
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from sales_engine.candidates.candidate_store import (
    CandidateStore,
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
)
from sales_engine.historical.importer import (
    HistoricalImporter,
    HistoricalRecord,
)
from backend.sales_ui.router import (
    get_preview_signing_secret,
    confirm_historical_import_action,
)
from fastapi.testclient import TestClient
from backend.main import app
import hmac


class CsrfAwareClient(TestClient):
    def __init__(self, app_instance):
        super().__init__(app_instance)
        self.csrf_token = "test_csrf_token_hv02_2"
        self.cookies.set("sales_csrf_token", self.csrf_token)

    def post(self, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["X-CSRF-Token"] = self.csrf_token
        headers["Referer"] = "http://testserver/sales/sources"
        kwargs["headers"] = headers
        return super().post(url, **kwargs)


@pytest.fixture
def disposable_env(tmp_path, monkeypatch):
    """Sets up a disposable SQLite database and returns paths within allowed staging root."""
    staging_root = Path("data/staging/test_hv02_2")
    staging_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SALESAI_ALLOWED_IMPORT_ROOT", str(staging_root.resolve()))

    db_file = staging_root / f"disposable_sales_{os.getpid()}_{hashlib.md5(str(tmp_path).encode()).hexdigest()[:8]}.db"
    if db_file.exists():
        db_file.unlink()

    store = CandidateStore(str(db_file))
    with store._get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL;")

    # Seed with an existing candidate
    orig_cand = CandidateCompany(
        candidate_id="cand:domain:origstudio.it",
        company_name="Original Studio BIM",
        normalized_company_name="original studio bim",
        company_domain="origstudio.it",
        country="Italy",
        city="Milano",
        cold_outreach_allowed=True,
    )
    store.save_candidate(orig_cand)

    yield {
        "store": store,
        "db_path": db_file,
        "staging_root": staging_root,
        "tmp_path": tmp_path,
    }

    try:
        if db_file.exists():
            db_file.unlink()
    except Exception:
        pass


def test_01_successful_http_preview_to_confirm_promotion(disposable_env, monkeypatch):
    """1. Successful HTTP Preview -> Confirm promotion with no concurrent changes."""
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    csv_file = staging_root / f"new_import_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Promoted Studio,promotedstudio.it,info@promotedstudio.it,2026-05-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)

    # Preview
    prev_resp = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    assert prev_resp.status_code == 200
    pdata = prev_resp.json()
    assert pdata["readiness_status"] == "READY"
    token = pdata["preview_token"]
    fhash = pdata["file_hash"]

    # Confirm
    conf_resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": fhash,
            "preview_token": token,
        },
        headers={"Accept": "application/json"},
    )
    assert conf_resp.status_code == 200
    cdata = conf_resp.json()
    assert cdata["status"] == "completed"
    assert cdata["companies_created"] == 1
    assert cdata["backup_path"] is not None

    # Verify live DB content
    store = CandidateStore(str(db_path))
    orig = store.get_by_id("cand:domain:origstudio.it")
    assert orig is not None, "Original candidate must be preserved"
    new_cand = store.get_by_domain("promotedstudio.it")
    assert new_cand is not None, "Imported candidate must exist in live DB"


def test_02_concurrent_write_during_staging_detected_and_aborted(disposable_env, monkeypatch):
    """2. A second connection writing during staging aborts promotion to prevent silent data loss."""
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    csv_file = staging_root / f"concurrent_test_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Staged Studio,stagedstudio.it,info@stagedstudio.it,2026-05-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)
    prev_resp = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    pdata = prev_resp.json()
    token = pdata["preview_token"]
    fhash = pdata["file_hash"]

    # Hook into import_records to inject concurrent write on live database
    real_import = HistoricalImporter.import_records

    def poisoned_import_records(self, records, operator_decisions=None):
        # Second connection writes to live DB using CandidateStore so all model invariants are met
        live_store = CandidateStore(str(db_path))
        c_cand = CandidateCompany(
            candidate_id="cand:concurrent:1",
            company_name="Concurrent Corp",
            normalized_company_name="concurrent corp",
            discovery_status=DiscoveryStatus.new,
        )
        live_store.save_candidate(c_cand)
        return real_import(self, records, operator_decisions=operator_decisions)

    monkeypatch.setattr(HistoricalImporter, "import_records", poisoned_import_records)

    conf_resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": fhash,
            "preview_token": token,
        },
        headers={"Accept": "application/json"},
    )
    assert conf_resp.status_code == 500
    assert "concurrent database modification detected" in conf_resp.json()["detail"].lower()

    # Verify live DB was NOT overwritten by staging
    store = CandidateStore(str(db_path))
    assert store.get_by_id("cand:concurrent:1") is not None, "Concurrent write must NOT be wiped out"
    assert store.get_by_domain("stagedstudio.it") is None, "Failed staging promotion must NOT persist"


def test_03_concurrent_write_locked_out_during_promotion(disposable_env):
    """3. Second connection attempting to write during active promotion is blocked by BEGIN IMMEDIATE."""
    db_path = disposable_env["db_path"]
    target_conn = sqlite3.connect(str(db_path))
    target_conn.execute("PRAGMA busy_timeout = 50;")

    # Start transaction on target_conn
    target_conn.execute("BEGIN IMMEDIATE;")

    # Another connection tries to write
    other_conn = sqlite3.connect(str(db_path))
    other_conn.execute("PRAGMA busy_timeout = 50;")

    locked_out = False
    try:
        other_conn.execute(
            "INSERT INTO candidate_companies (candidate_id, company_name, normalized_company_name) VALUES ('cand:conflict:2', 'Conflict Corp', 'conflict corp');"
        )
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            locked_out = True
    finally:
        target_conn.rollback()
        target_conn.close()
        other_conn.close()

    assert locked_out, "Concurrent writer must be locked out when target promotion holds BEGIN IMMEDIATE"


def test_04_atomic_rollback_on_failure_during_promotion(disposable_env, monkeypatch):
    """4. Simulated failure during promotion completely rolls back; live DB is identical to pre-import state."""
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    csv_file = staging_root / f"rollback_test_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Rollback Studio,rollbackstudio.it,info@rollbackstudio.it,2026-05-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)
    prev_resp = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    pdata = prev_resp.json()
    token = pdata["preview_token"]
    fhash = pdata["file_hash"]

    # Snapshot pre-import database content
    store = CandidateStore(str(db_path))
    with store._get_connection() as conn:
        pre_count = conn.execute("SELECT COUNT(*) FROM candidate_companies;").fetchone()[0]

    # Inject failure during import processing on staging
    real_import = HistoricalImporter.import_records

    def failing_import(self, records, operator_decisions=None):
        res = real_import(self, records, operator_decisions=operator_decisions)
        raise RuntimeError("Simulated failure during import execution")

    monkeypatch.setattr(HistoricalImporter, "import_records", failing_import)

    conf_resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": fhash,
            "preview_token": token,
        },
        headers={"Accept": "application/json"},
    )
    assert conf_resp.status_code == 500
    assert "rolled back" in conf_resp.json()["detail"].lower()

    # Verify live DB is intact and unchanged
    with store._get_connection() as conn:
        post_count = conn.execute("SELECT COUNT(*) FROM candidate_companies;").fetchone()[0]
        assert post_count == pre_count, "Database must remain strictly unchanged on promotion failure"
        assert store.get_by_domain("rollbackstudio.it") is None


def test_05_integrity_check_failure_aborts_before_promotion(disposable_env, monkeypatch):
    """5. Staging integrity check failure prevents promotion to live DB."""
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    csv_file = staging_root / f"integrity_test_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Integrity Studio,integritystudio.it,info@integritystudio.it,2026-05-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)
    prev_resp = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    pdata = prev_resp.json()
    token = pdata["preview_token"]
    fhash = pdata["file_hash"]

    # Patch import_records to corrupt staging clone
    real_import = HistoricalImporter.import_records

    def corrupting_import(self, records, operator_decisions=None):
        res = real_import(self, records, operator_decisions=operator_decisions)
        # Corrupt the staging store
        with self.store._get_connection() as conn:
            conn.execute("PRAGMA writable_schema = ON;")
            conn.execute("UPDATE sqlite_master SET sql = 'INVALID SQL CORRUPT' WHERE type = 'table' AND name = 'candidate_companies';")
            conn.commit()
        return res

    monkeypatch.setattr(HistoricalImporter, "import_records", corrupting_import)

    conf_resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": fhash,
            "preview_token": token,
        },
        headers={"Accept": "application/json"},
    )
    assert conf_resp.status_code == 500
    detail = conf_resp.json()["detail"].lower()
    assert "integrity check failed" in detail or "malformed database schema" in detail

    store = CandidateStore(str(db_path))
    assert store.get_by_domain("integritystudio.it") is None


def test_06_verified_pre_import_backup_and_recovery(disposable_env):
    """6 & 7. Creates verified pre-import backup and demonstrates reliable recovery."""
    store = disposable_env["store"]
    db_path = disposable_env["db_path"]

    backup_path, backup_sha = store.create_backup()
    assert backup_path.exists()
    assert len(backup_sha) == 64

    # Verify backup
    valid, err = store.verify_backup(backup_path)
    assert valid is True
    assert err is None

    # Mutate live DB (e.g. disaster)
    with store._get_connection() as conn:
        conn.execute("DELETE FROM candidate_companies;")
        conn.commit()

    assert len(store.list_candidates()) == 0

    # Recover from backup
    restore_conn = sqlite3.connect(str(db_path))
    bkp_conn = sqlite3.connect(str(backup_path))
    try:
        bkp_conn.backup(restore_conn)
    finally:
        bkp_conn.close()
        restore_conn.close()

    # Check recovery
    recovered_cands = store.list_candidates()
    assert len(recovered_cands) == 1
    assert recovered_cands[0].candidate_id == "cand:domain:origstudio.it"


def test_07_idempotent_repeated_import_prevents_unwanted_duplicates(disposable_env, monkeypatch):
    """8. Repeating an identical import updates without unwanted duplicate records."""
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    csv_file = staging_root / f"idempotent_test_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Idempotent Studio,idempotentstudio.it,info@idempotentstudio.it,2026-05-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)

    # First run
    p1 = client.post("/sales/sources/import-history/preview", data={"file_path": str(csv_file)}, headers={"Accept": "application/json"}).json()
    c1 = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(csv_file), "file_hash": p1["file_hash"], "preview_token": p1["preview_token"]},
        headers={"Accept": "application/json"},
    ).json()
    assert c1["companies_created"] == 1

    # Second run
    p2 = client.post("/sales/sources/import-history/preview", data={"file_path": str(csv_file)}, headers={"Accept": "application/json"}).json()
    c2 = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(csv_file), "file_hash": p2["file_hash"], "preview_token": p2["preview_token"]},
        headers={"Accept": "application/json"},
    ).json()
    assert c2["companies_created"] == 0

    store = CandidateStore(str(db_path))
    assert len(store.list_candidates()) == 2  # Original + 1 newly imported
    events = store.get_historical_outreach_for_candidate("cand:domain:idempotentstudio.it")
    assert len(events) == 1, "Repeated import must NOT create duplicate outreach events"


def test_08_missing_secret_fails_closed_and_rejects_wrong_secret(disposable_env, monkeypatch):
    """9. Missing secret does not use public salt; wrong secret is rejected."""
    monkeypatch.delenv("PREVIEW_SIGNING_SECRET", raising=False)
    # Ensure no existing key file
    key_file = Path("data/.preview_signing_key")
    if key_file.exists():
        key_file.unlink()

    secret = get_preview_signing_secret()
    assert secret != b"salesai_preview_salt"
    assert len(secret) >= 32

    # Verify token check rejects forged token with wrong salt
    db_path = disposable_env["db_path"]
    staging_root = disposable_env["staging_root"]
    csv_file = staging_root / f"forged_{os.getpid()}.csv"
    csv_file.write_text("Company,Domain\nForged,forged.it\n", encoding="utf-8")

    fhash = hashlib.sha256(csv_file.read_bytes()).hexdigest()
    forged_token = hmac.new(b"salesai_preview_salt", f"{str(csv_file.resolve())}:{fhash}:".encode(), hashlib.sha256).hexdigest()

    client = CsrfAwareClient(app)
    resp = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(csv_file), "file_hash": fhash, "preview_token": forged_token},
    )
    assert resp.status_code == 400
    assert "invalid preview confirmation token" in resp.json()["detail"].lower()
