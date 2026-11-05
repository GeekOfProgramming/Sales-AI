"""
HIST-04: Unmatched Email Reconciliation Safety Fix Test Suite.
Validates:
1. Reconciling unmatched historical sent messages against verified candidate identities:
   - Matches recipient email and verified company-domain fallbacks (314 matched, 5 unmatched).
   - Preserves non-matched messages (internal forwards, personal recipients, unverified companies) as unmatched.
2. Historical outreach protection:
   - For matched candidates (e.g. EsseQuadro Service Srl), sets cold_outreach_allowed=False,
     records historical outreach events, sets historical_contact_status='contacted',
     moves out of Active Work to Historical Review Pending, and preserves research/review fields without finalizing.
3. Strict idempotency and queue invariant verification:
   - Re-running import creates 0 duplicates and retains identical lifecycle queue counts.
"""
import pytest
import shutil
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_hist04_env(tmp_path, monkeypatch):
    test_db = tmp_path / "hist04_safety.db"
    shutil.copy2("data/sales_outreach.db", str(test_db))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("SALESAI_DB_PATH", str(test_db))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(test_db))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_hist04!")

    cand_store = CandidateStore(str(test_db))
    lifecycle_svc = CandidateLifecycleService(cand_store)
    importer = HistoricalImporter(store=cand_store)
    excel_file = "C:/Sales-AI/SalesAI/data/staging/upload_20261105_103023_Emails.xlsx"

    client = TestClient(app)

    return {
        "client": client,
        "cand_store": cand_store,
        "lifecycle_svc": lifecycle_svc,
        "importer": importer,
        "db_path": str(test_db),
        "excel_file": excel_file,
    }


def test_01_candidate_reconciliation_multi_tier(disposable_hist04_env):
    """
    Test 1: Multi-tier candidate reconciliation.
    - Resolves 314 matched messages (304 exact contact/staged email matches + 10 verified candidate-domain matches).
    - Exactly 5 messages remain unresolved (3 internal pyBIM forwards, 1 personal Gmail invite, 1 unverified external company).
    - 0 ambiguous associations created.
    """
    importer = disposable_hist04_env["importer"]
    excel_file = disposable_hist04_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    reconciled = importer.reconcile_sent_emails(sent_msgs, staged_records=records)
    matched = [m for m in reconciled if m.reconciliation_status == "matched"]
    unmatched = [m for m in reconciled if m.reconciliation_status == "unmatched"]
    ambiguous = [m for m in reconciled if m.reconciliation_status == "ambiguous"]

    assert len(reconciled) == 319
    assert len(matched) == 314
    assert len(unmatched) == 5
    assert len(ambiguous) == 0

    unmatched_ids = {m.message_id for m in unmatched}
    expected_unmatched_ids = {
        "1a19c26057c19b21",  # amministrazione@energekpd.com (Unverified external company)
        "1a18e20fdc99b8ef",  # shahin.amayeh@amabim.com (Internal pyBIM forward)
        "1a18c71c4cc7c62a",  # shahin.amayeh@amabim.com (Internal pyBIM forward)
        "1a18c724c888b015",  # shahin.amayeh@amabim.com (Internal pyBIM forward)
        "1a158683f6dca07f",  # antoniogiuseppe.lato@gmail.com (Personal Gmail meet invite)
    }
    assert unmatched_ids == expected_unmatched_ids


def test_02_historical_outreach_protection_and_lifecycle(disposable_hist04_env):
    """
    Test 2: Historical Outreach Protection & Active Work Exclusion.
    - Verified candidates with newly matched outreach (e.g. EsseQuadro Service Srl)
      must have cold_outreach_allowed=False, historical_contact_status='contacted',
      outreach events recorded, and primary queue moved to HISTORICAL_REVIEW_PENDING.
    - Candidate is excluded from Active Work (is_active_work == False).
    - Operator review decision NOT finalized (remains NOT_REVIEWED).
    """
    importer = disposable_hist04_env["importer"]
    store = disposable_hist04_env["cand_store"]
    lifecycle_svc = disposable_hist04_env["lifecycle_svc"]
    excel_file = disposable_hist04_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    # Pre-import baseline for EsseQuadro
    eq_before = store.get_candidate("cand:domain:essequadroservice.it")
    assert eq_before.cold_outreach_allowed is True
    assert eq_before.historical_contact_status == "uncontacted"
    state_before = lifecycle_svc.get_candidate_lifecycle_state("cand:domain:essequadroservice.it")
    assert state_before.is_active_work is True

    # Execute import
    importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    importer.import_sent_emails(sent_msgs)

    # Post-import verification for EsseQuadro
    eq_after = store.get_candidate("cand:domain:essequadroservice.it")
    assert eq_after.cold_outreach_allowed is False
    assert eq_after.historical_contact_status == "contacted"
    assert eq_after.manual_review_decision == "NOT_REVIEWED"

    state_after = lifecycle_svc.get_candidate_lifecycle_state("cand:domain:essequadroservice.it")
    assert state_after.is_active_work is False
    assert state_after.primary_queue.value == "historical_pending"

    events = store.get_historical_outreach_for_candidate("cand:domain:essequadroservice.it")
    assert len(events) >= 1
    assert any("essequadroservice.it" in (e.get("contact_email") or "") for e in events)


def test_03_strict_idempotency_and_queue_counts(disposable_hist04_env):
    """
    Test 3: Strict Idempotency & Queue Count Parity.
    - Queue counts before import: active=23, historical_pending=279, all=322, contacted=279.
    - Queue counts after Run 1: active=21, historical_pending=302, all=343, contacted=302.
    - Second import run must produce 0 duplicate companies and 0 duplicate sent emails.
    - Lifecycle queue counts after Run 2 must strictly match Run 1.
    """
    importer = disposable_hist04_env["importer"]
    store = disposable_hist04_env["cand_store"]
    lifecycle_svc = disposable_hist04_env["lifecycle_svc"]
    excel_file = disposable_hist04_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    # Baseline before import
    counts_before = lifecycle_svc.compute_queue_counts()
    assert counts_before["active"] == 23
    assert counts_before["historical_pending"] == 279
    assert counts_before["all"] == 322

    # Run 1
    importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    sent_stats1 = importer.import_sent_emails(sent_msgs)
    assert sent_stats1["imported"] == 319
    assert sent_stats1["matched"] == 314
    assert sent_stats1["unmatched"] == 5

    counts_run1 = lifecycle_svc.compute_queue_counts()
    assert counts_run1["active"] == 21
    assert counts_run1["historical_pending"] == 302
    assert counts_run1["all"] == 343

    with store._get_connection() as conn:
        contacted_run1 = conn.execute(
            "SELECT count(*) FROM candidate_companies WHERE historical_contact_status IN ('contacted', 'replied', 'converted', 'dnc')"
        ).fetchone()[0]
        assert contacted_run1 == 302

    # Run 2 (Idempotency)
    lead_res2 = importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    assert lead_res2.companies_created == 0

    sent_stats2 = importer.import_sent_emails(sent_msgs)
    assert sent_stats2["imported"] == 0
    assert sent_stats2["duplicates"] == 319

    counts_run2 = lifecycle_svc.compute_queue_counts()
    assert counts_run2 == counts_run1
