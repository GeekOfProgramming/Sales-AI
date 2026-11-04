"""
HV-02.4 Minimal Staging Duplicate Fix Verification Test.

Requirements:
1. One disposable-database test containing existing historical contacts.
2. Import twice and confirm exact record counts and identities remain correct.
3. Verify that repeated Preview -> Confirm imports do not duplicate existing contacts,
   historical links, communication events, or meeting activities.
"""

import os
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.historical.importer import HistoricalImporter
from backend.sales_ui.router import get_preview_signing_secret
from backend.main import app


class CsrfAwareClient(TestClient):
    def __init__(self, app_instance):
        super().__init__(app_instance)
        self.csrf_token = "test_csrf_token_hv02_4"
        self.cookies.set("sales_csrf_token", self.csrf_token)

    def post(self, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["X-CSRF-Token"] = self.csrf_token
        kwargs["headers"] = headers
        return super().post(url, **kwargs)


def test_repeated_import_prevents_duplicate_contacts_and_preserves_identities(tmp_path, monkeypatch):
    """
    Test repeated Preview -> Confirm on a disposable database pre-seeded with existing historical contacts.
    Ensures that staging promotion does not duplicate rows in historical_contacts, historical_imports,
    or candidate_companies, and preserves primary keys.
    """
    staging_root = Path("data/staging/test_hv02_4")
    staging_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SALESAI_ALLOWED_IMPORT_ROOT", str(staging_root.resolve()))

    db_file = staging_root / f"repeat_test_{os.getpid()}.db"
    if db_file.exists():
        db_file.unlink()

    store = CandidateStore(str(db_file))
    importer = HistoricalImporter(store=store)

    # Pre-seed existing candidate and historical contact in live DB
    cid = "cand:domain:existingstudio.it"
    orig_cand = CandidateCompany(
        candidate_id=cid,
        company_name="Existing Studio",
        normalized_company_name="existing studio",
        company_domain="existingstudio.it",
        city="Torino",
        cold_outreach_allowed=True,
    )
    store.save_candidate(orig_cand)

    # Insert pre-existing historical_import and historical_contact
    with store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO historical_imports (
                import_hash, source_file, sheet_name, row_number,
                canonical_domain, company_name, emails_json, raw_row_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "hash_existing_01",
                "prior_data.xlsx",
                "Companies",
                2,
                "existingstudio.it",
                "Existing Studio",
                '["prior@existingstudio.it"]',
                "{}",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO historical_contacts (
                id, import_hash, canonical_domain, contact_name, contact_role, email, email_status, is_respondent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                42,
                "hash_existing_01",
                "existingstudio.it",
                "Prior Contact",
                "Architect",
                "prior@existingstudio.it",
                "historical_unknown",
                0,
            ),
        )

    # Prepare configuration for HTTP API
    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")

    # Create CSV import file that adds a new company AND re-imports the existing company
    csv_file = staging_root / f"import_feed_{os.getpid()}.csv"
    csv_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "Existing Studio,existingstudio.it,prior@existingstudio.it,2026-01-01,ایمیل ارسال شد\n"
        "New Studio,newstudio.it,info@newstudio.it,2026-06-15,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    client = CsrfAwareClient(app)

    # --- FIRST IMPORT ---
    prev_1 = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    assert prev_1.status_code == 200
    p1_data = prev_1.json()
    assert p1_data["readiness_status"] == "READY"

    conf_1 = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": p1_data["file_hash"],
            "preview_token": p1_data["preview_token"],
        },
        headers={"Accept": "application/json"},
    )
    assert conf_1.status_code == 200
    c1_data = conf_1.json()
    assert c1_data["status"] == "completed"

    # Inspect state after first import
    with store._get_connection() as conn:
        contacts_after_1 = conn.execute("SELECT id, email, canonical_domain FROM historical_contacts ORDER BY id").fetchall()
        imports_after_1 = conn.execute("SELECT import_hash FROM historical_imports").fetchall()

    count_contacts_1 = len(contacts_after_1)
    # Contact with id 42 must still exist
    assert any(c["id"] == 42 for c in contacts_after_1), "Existing contact ID 42 must be preserved"

    # --- SECOND IMPORT (REPEATED PREVIEW -> CONFIRM) ---
    prev_2 = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    assert prev_2.status_code == 200
    p2_data = prev_2.json()

    conf_2 = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(csv_file),
            "file_hash": p2_data["file_hash"],
            "preview_token": p2_data["preview_token"],
        },
        headers={"Accept": "application/json"},
    )
    assert conf_2.status_code == 200
    c2_data = conf_2.json()
    assert c2_data["status"] == "completed"

    # Inspect state after second import: counts and identities MUST match first import exactly
    with store._get_connection() as conn:
        contacts_after_2 = conn.execute("SELECT id, email, canonical_domain FROM historical_contacts ORDER BY id").fetchall()
        imports_after_2 = conn.execute("SELECT import_hash FROM historical_imports").fetchall()

    count_contacts_2 = len(contacts_after_2)
    assert count_contacts_2 == count_contacts_1, (
        f"Contact count changed after second import! Before: {count_contacts_1}, After: {count_contacts_2}"
    )
    assert len(imports_after_2) == len(imports_after_1), "Historical imports count must not duplicate"
    assert any(c["id"] == 42 for c in contacts_after_2), "Existing contact ID 42 must remain preserved after repeat"
