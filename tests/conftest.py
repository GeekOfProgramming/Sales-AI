"""
Pytest configuration for SalesAI test suite.
Strict Isolation Policy:
- Test executions NEVER use the operational database (data/sales_outreach.db or configured operational DB).
- Every test runs with an isolated temporary SQLite database automatically.
- Production database modification during pytest will fail with an assertion error.
"""

from datetime import datetime
import os
import sys
import types
import tempfile
import sqlite3
import hashlib
import pytest

try:
    import ollama
except ImportError:
    mock_ollama = types.ModuleType("ollama")
    mock_ollama.Client = None
    sys.modules["ollama"] = mock_ollama


def _compute_db_dump_hash(db_path: str) -> str:
    """Safely computes a content hash of an operational SQLite database even if locked in WAL mode."""
    if not os.path.exists(db_path):
        return ""
    uri = f"file:///{os.path.abspath(db_path).replace(os.sep, '/')}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        mem = sqlite3.connect(":memory:")
        conn.backup(mem)
        conn.close()
        h = hashlib.sha256()
        for line in mem.iterdump():
            h.update(line.encode("utf-8"))
        mem.close()
        return h.hexdigest()
    except Exception:
        # Fallback to direct file read if not locked
        try:
            with open(db_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""


# Determine operational database paths to protect
_POSSIBLE_OP_PATHS = [
    os.path.abspath("C:/Sales-AI/SalesAI/data/sales_outreach.db"),
    os.path.abspath("data/sales_outreach.db"),
]
if os.getenv("SALES_OUTREACH_DB"):
    _POSSIBLE_OP_PATHS.append(os.path.abspath(os.getenv("SALES_OUTREACH_DB")))


@pytest.fixture(scope="session", autouse=True)
def guard_operational_db():
    """
    Session-level safeguard:
    Records operational DB hash at test start and asserts it has not changed after all tests finish.
    """
    initial_hashes = {}
    for p in set(_POSSIBLE_OP_PATHS):
        if os.path.exists(p):
            initial_hashes[p] = _compute_db_dump_hash(p)

    yield

    for p, init_hash in initial_hashes.items():
        if os.path.exists(p):
            final_hash = _compute_db_dump_hash(p)
            assert final_hash == init_hash, (
                f"CRITICAL VIOLATION: Operational database {p} was modified during test run! "
                f"Initial SHA256: {init_hash}, Final SHA256: {final_hash}"
            )


@pytest.fixture(autouse=True)
def default_test_env(request, monkeypatch, tmp_path):
    """
    Ensure standard test environment is strictly isolated:
    1. Isolated SQLite database via SALES_OUTREACH_DB, SALESAI_DB_PATH, SALESAI_CANDIDATES_DB
    2. Zero external LLM calls or email sending
    """
    # Create isolated database for every test unless test handles its own DB
    test_db = str(tmp_path / "isolated_test_sales.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    fspath_str = str(request.node.fspath)
    if "test_phase10_5" in fspath_str:
        monkeypatch.setenv("SMTP_FROM_EMAIL", "sales@pybim.it")
        monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "sales@pybim.it,outreach@pybim.com")
        monkeypatch.setenv("OUTREACH_SENDER_EMAIL", "sales@pybim.it")
    else:
        # Default sender for Phase 9 and earlier test suites
        if "SMTP_FROM_EMAIL" not in os.environ or os.environ.get("SMTP_FROM_EMAIL") == "sales@pybim.it":
            monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@pybim.com")
        if "SMTP_ALLOWED_SENDERS" not in os.environ or os.environ.get("SMTP_ALLOWED_SENDERS") == "sales@pybim.it":
            monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "outreach@pybim.com,sales@pybim.it")

    # Anchor Phase 5 lead aggregation time to test snapshot baseline (2026-09-30)
    # only for Phase 5 acceptance tests where fixed snapshot dates (2026-09-28) are used
    if "test_phase5_" in request.node.name or "test_phase5_acceptance" in fspath_str:
        try:
            import sales_engine.leads.lead_aggregator as la
            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 9, 30, 12, 0, 0)
            monkeypatch.setattr(la, "datetime", FixedDateTime)
        except Exception:
            pass

    yield
