"""
HV-02.3 Minimal Safety Fix Focused Tests.

1. Test cross-source communication deduplication with combined evidence:
   - Distinct follow-up emails to the same recipient on different dates are NOT merged.
   - Different recipients on the same date are NOT merged.
   - Combined evidence (same candidate + same date + same recipient) is safely merged.
2. Test SQLite promotion preserving existing relational data and child foreign keys without ID collisions.
"""

import sqlite3
import pytest
from pathlib import Path
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord, SentEmailRecord
from fastapi.testclient import TestClient
from backend.main import app


def test_cross_source_combined_evidence_deduplication(tmp_path):
    """
    Test 1: Combined evidence rule for cross-source Excel/Gmail deduplication:
    - Same recipient on distinct follow-up dates retains distinct outreach events with unique Message IDs.
    - Same candidate + date + recipient enriches existing outreach event.
    """
    db_file = tmp_path / "dedup_test.db"
    store = CandidateStore(str(db_file))
    importer = HistoricalImporter(store=store)

    cid = "cand:domain:followupstudio.it"
    c = CandidateCompany(
        candidate_id=cid,
        company_name="Followup Studio",
        normalized_company_name="followup studio",
        company_domain="followupstudio.it",
    )
    store.save_candidate(c)

    # Initial Excel event on 2026-10-10
    store.record_historical_outreach(
        candidate_id=cid,
        source_ref="historical:initial.xlsx",
        contact_name="Mario Rossi",
        contact_email="mario@followupstudio.it",
        contacted_at="2026-10-10 14:00:00",
        channel="email",
        reply_status="contacted",
        notes="Initial email from spreadsheet",
        raw_row_hash="excel:hash:1",
    )

    # Gmail message 1: Corresponds to initial email (same date 2026-10-10, same recipient)
    msg1 = SentEmailRecord(
        message_id="msg_initial_123",
        thread_id="th_1",
        sender_account="info@pybim.it",
        sender_group=None,
        from_address="info@pybim.it",
        to_address="mario@followupstudio.it",
        recipient_emails=["mario@followupstudio.it"],
        sent_date="2026-10-10",
        subject="Initial outreach",
        matched_candidate_id=cid,
        matched_company_name="Followup Studio",
        reconciliation_status="matched",
    )

    # Gmail message 2: Distinct follow-up email to SAME recipient on a DIFFERENT date (2026-10-25)
    msg2 = SentEmailRecord(
        message_id="msg_followup_456",
        thread_id="th_1",
        sender_account="info@pybim.it",
        sender_group=None,
        from_address="info@pybim.it",
        to_address="mario@followupstudio.it",
        recipient_emails=["mario@followupstudio.it"],
        sent_date="2026-10-25",
        subject="Follow-up on pyBIM collaboration",
        matched_candidate_id=cid,
        matched_company_name="Followup Studio",
        reconciliation_status="matched",
    )

    # Gmail message 3: Same date (2026-10-10) but DIFFERENT recipient (director@followupstudio.it)
    msg3 = SentEmailRecord(
        message_id="msg_director_789",
        thread_id="th_2",
        sender_account="info@pybim.it",
        sender_group=None,
        from_address="info@pybim.it",
        to_address="director@followupstudio.it",
        recipient_emails=["director@followupstudio.it"],
        sent_date="2026-10-10",
        subject="Direct to director",
        matched_candidate_id=cid,
        matched_company_name="Followup Studio",
        reconciliation_status="matched",
    )

    importer.import_sent_emails([msg1, msg2, msg3])

    events = store.get_historical_outreach_for_candidate(cid)
    # Must have exactly 3 events:
    # 1. Initial event enriched with msg_initial_123
    # 2. Distinct follow-up msg_followup_456
    # 3. Distinct recipient msg_director_789
    assert len(events) == 3, f"Expected 3 distinct outreach events, got {len(events)}"

    # Verify msg_initial_123 was enriched into initial event notes
    initial_ev = next(e for e in events if e["contacted_at"].startswith("2026-10-10") and e["contact_email"] == "mario@followupstudio.it")
    assert "msg_initial_123" in initial_ev["notes"]

    # Verify msg_followup_456 is an independent event with its own Message ID preserved
    followup_ev = next(e for e in events if e["contacted_at"] == "2026-10-25")
    assert "msg_followup_456" in followup_ev["notes"]

    # Verify msg_director_789 is also independent
    director_ev = next(e for e in events if e["contact_email"] == "director@followupstudio.it")
    assert "msg_director_789" in director_ev["notes"]


def test_sqlite_promotion_preserves_relational_data_and_auto_increment_ids(tmp_path, monkeypatch):
    """
    Test 2: SQLite promotion preserves existing relational data and child foreign keys.
    """
    db_file = tmp_path / "live_relational.db"
    store = CandidateStore(str(db_file))
    importer = HistoricalImporter(store=store)

    # 1. Create existing historical contact and existing candidate in live DB
    cid = "cand:domain:existingstudio.it"
    c = CandidateCompany(
        candidate_id=cid,
        company_name="Existing Studio",
        normalized_company_name="existing studio",
        company_domain="existingstudio.it",
    )
    store.save_candidate(c)

    # Pre-populate historical_imports and child historical_contacts with specific autoincrement ID=1
    with store._get_connection() as conn:
        conn.execute(
            """INSERT INTO historical_imports (
                import_hash, source_file, sheet_name, row_number, canonical_domain,
                company_name, emails_json, has_outreach, has_reply, is_active_conversation,
                is_dnc, raw_row_json, imported_at
            ) VALUES ('prev_hash_1', 'old.xlsx', 'Sheet1', 1, 'existingstudio.it', 'Existing Studio', '[\"old@existingstudio.it\"]', 0, 0, 0, 0, '{}', '2026-01-01');"""
        )
        conn.execute(
            """INSERT INTO historical_contacts (
                id, import_hash, canonical_domain, contact_name, contact_role, email, email_status, is_respondent
            ) VALUES (1, 'prev_hash_1', 'existingstudio.it', 'Pre-existing Contact', 'Director', 'old@existingstudio.it', 'verified', 0);"""
        )

    # Prepare staged import with new import and contacts
    staging_file = tmp_path / "new_staging_import.csv"
    staging_file.write_text(
        "Company,Domain,Email,Contact Date,Status\n"
        "New Studio,newstudio.it,info@newstudio.it,2026-05-20,ایمیل ارسال شد\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("SALESAI_ALLOWED_IMPORT_ROOT", str(tmp_path.resolve()))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_signing_key_32bytes_long_secret!")

    client = TestClient(app)
    client.cookies.set("sales_csrf_token", "test_csrf")
    headers = {"X-CSRF-Token": "test_csrf", "Referer": "http://testserver/sales/sources", "Accept": "application/json"}

    # Preview
    p_resp = client.post("/sales/sources/import-history/preview", data={"file_path": str(staging_file)}, headers=headers)
    assert p_resp.status_code == 200
    pdata = p_resp.json()

    # Confirm
    c_resp = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(staging_file), "file_hash": pdata["file_hash"], "preview_token": pdata["preview_token"]},
        headers=headers,
    )
    assert c_resp.status_code == 200
    assert c_resp.json()["status"] == "completed"

    # Verify live DB relational integrity:
    with store._get_connection() as conn:
        # Check pre-existing contact is untouched and retained
        old_contact = conn.execute("SELECT * FROM historical_contacts WHERE email = 'old@existingstudio.it'").fetchone()
        assert old_contact is not None
        assert old_contact["contact_name"] == "Pre-existing Contact"

        # Check new contact is inserted
        new_contact = conn.execute("SELECT * FROM historical_contacts WHERE email = 'info@newstudio.it'").fetchone()
        assert new_contact is not None

        # Verify foreign keys are 100% valid
        fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fk_violations) == 0, f"Foreign key violations found: {fk_violations}"
