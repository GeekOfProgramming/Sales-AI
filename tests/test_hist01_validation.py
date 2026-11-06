"""
Validation tests for HIST-01: End-to-End Historical Email Routing Validation.
Validates:
1. Preview baseline agreement and safe import confirmation against a disposable SQLite database.
2. Email-to-company routing: all matched messages are attached to companies, previous outreach is recorded,
   cold outreach is locked (cold_outreach_allowed=False), and contacted companies are removed from the Active Candidates queue.
3. Strict idempotency: Re-running the import causes 0 duplicate companies, 0 duplicate messages,
   and no unintended lifecycle transitions.
"""
import pytest
import shutil
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_hist01_env(tmp_path, monkeypatch):
    test_db = tmp_path / "hist01_disposable.db"
    # Copy from authoritative DB to ensure real-world test topology
    shutil.copy2("data/sales_outreach.db", str(test_db))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_hist01!")

    cand_store = CandidateStore(str(test_db))
    lifecycle_svc = CandidateLifecycleService(cand_store)
    importer = HistoricalImporter(store=cand_store)
    client = TestClient(app)

    return {
        "client": client,
        "cand_store": cand_store,
        "lifecycle_svc": lifecycle_svc,
        "importer": importer,
        "db_path": str(test_db),
        "excel_file": "C:/Sales-AI/SalesAI/data/staging/upload_20261105_103023_Emails.xlsx",
    }


def test_01_preview_baseline_and_reconciliation(disposable_hist01_env):
    """
    Test 1: Preview baseline agreement and safe import execution.
    - 308 company rows parsed.
    - 319 historical sent messages parsed.
    - 303 matched message proposals and 16 unmatched messages.
    - 21 new candidate proposals.
    - 0 ambiguous identities.
    """
    importer = disposable_hist01_env["importer"]
    excel_file = disposable_hist01_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    assert len(records) == 308

    sent_msgs = importer.parse_sent_emails(excel_file)
    assert len(sent_msgs) == 319

    reconciled = importer.reconcile_sent_emails(sent_msgs, staged_records=records)
    matched = [m for m in reconciled if m.reconciliation_status == "matched"]
    unmatched = [m for m in reconciled if m.reconciliation_status == "unmatched"]
    ambiguous = [m for m in reconciled if m.reconciliation_status == "ambiguous"]

    assert len(matched) == 303
    assert len(unmatched) == 16
    assert len(ambiguous) == 0

    analysis = importer.preview_lead_research_import(records)
    assert analysis["new_candidate_proposals"] == 21
    assert analysis["existing_candidate_matches"] == 287


def test_02_email_routing_and_lifecycle_invariants(disposable_hist01_env):
    """
    Test 2: Email-to-Company routing and lifecycle queue transitions.
    - Confirms all 308 records and 319 sent messages import successfully.
    - Confirms that previously contacted companies are NEVER in the Active Candidates queue.
    - Confirms all matched companies have cold_outreach_allowed = False and status = 'contacted'.
    - Confirms unmatched messages (16) remain stored with reconciliation_status = 'unmatched'.
    - Confirms historical pending queue properly tracks contacted companies awaiting review.
    """
    importer = disposable_hist01_env["importer"]
    cand_store = disposable_hist01_env["cand_store"]
    lifecycle_svc = disposable_hist01_env["lifecycle_svc"]
    excel_file = disposable_hist01_env["excel_file"]

    before_counts = lifecycle_svc.compute_queue_counts()

    records = importer.parse_xlsx(excel_file)
    res_import = importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    assert res_import.companies_created == 21

    sent_msgs = importer.parse_sent_emails(excel_file)
    sent_stats = importer.import_sent_emails(sent_msgs)
    assert sent_stats["imported"] == 319
    assert sent_stats["matched"] == 303
    assert sent_stats["unmatched"] == 16

    after_counts = lifecycle_svc.compute_queue_counts()

    # Active Candidates queue should decrease as newly contacted companies move to historical_pending
    assert after_counts["historical_pending"] == 301
    assert after_counts["all"] == 343

    # Invariant: No contacted company can be in the Active queue
    active_cands = [
        c for c in cand_store.list_candidates()
        if lifecycle_svc.get_candidate_lifecycle_state(c.candidate_id).primary_queue.value == "active"
    ]
    for c in active_cands:
        assert c.historical_contact_status not in ("contacted", "replied", "converted"), (
            f"Candidate {c.candidate_id} ({c.company_name}) has status '{c.historical_contact_status}' but is in Active queue!"
        )

    # Invariant: All matched sent emails are attached to existing or newly created candidate companies
    with cand_store._get_connection() as conn:
        matched_rows = conn.execute("""
            SELECT m.message_id, m.thread_id, m.from_address, m.to_address, m.subject,
                   m.email_body, m.sent_date, m.gmail_link, c.candidate_id, c.cold_outreach_allowed,
                   c.historical_contact_status
            FROM historical_sent_emails m
            JOIN candidate_companies c ON m.matched_candidate_id = c.candidate_id
            WHERE m.reconciliation_status = 'matched'
        """).fetchall()
        assert len(matched_rows) == 303
        for mr in matched_rows:
            assert mr["cold_outreach_allowed"] == 0
            assert mr["historical_contact_status"] in ("contacted", "replied", "converted")
            assert mr["subject"] is not None
            assert mr["email_body"] is not None
            assert mr["gmail_link"] is not None


def test_03_strict_idempotency_second_run(disposable_hist01_env):
    """
    Test 3: Strict Idempotency.
    Running the import a second time against the same database must:
    - Create 0 duplicate companies (companies_created == 0).
    - Create 0 duplicate sent messages (duplicates == 319, imported == 0).
    - Result in identical lifecycle queue counts.
    """
    importer = disposable_hist01_env["importer"]
    lifecycle_svc = disposable_hist01_env["lifecycle_svc"]
    excel_file = disposable_hist01_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    # First Run
    importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    importer.import_sent_emails(sent_msgs)
    counts_run1 = lifecycle_svc.compute_queue_counts()

    # Second Run
    res_run2 = importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    assert res_run2.companies_created == 0
    assert res_run2.companies_updated == 308

    stats_run2 = importer.import_sent_emails(sent_msgs)
    assert stats_run2["imported"] == 0
    assert stats_run2["duplicates"] == 319

    counts_run2 = lifecycle_svc.compute_queue_counts()
    assert counts_run1 == counts_run2
