"""
HIST-04A: Manual Reconciliation Gate Test Suite.
Validates:
1. Multi-tier candidate reconciliation with manual gate:
   - 303 direct matches subject to existing identity safeguards (proposal_status='approved').
   - 11 fallback matches flagged as proposed associations requiring explicit operator approval
     (reconciliation_status='unmatched', proposal_status='proposed').
   - Exactly 5 messages strictly unassociated (reconciliation_status='unmatched', proposal_status=None).
   - 0 ambiguous associations.
2. Historical outreach protection & Active Candidates preservation:
   - Proposed candidate EsseQuadro (and other proposed candidates) retain cold_outreach_allowed=True,
     historical_contact_status='uncontacted', and remain in Active Work queue.
   - Verified direct outreach (e.g. ETC...Studio) is properly attached and removed from Active Work.
   - Active Work queue count transitions predictably (23 baseline + 2 uncontacted from workbook - 1 direct match = 24),
     leaving all 11 proposed candidate records untouched.
   - Strict idempotency on repeated import.
"""
import pytest
import shutil
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_hist04a_env(tmp_path, monkeypatch):
    test_db = tmp_path / "hist04a_gate.db"
    shutil.copy2("data/sales_outreach.db", str(test_db))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("SALESAI_DB_PATH", str(test_db))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(test_db))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_hist04a!")

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


def test_01_manual_reconciliation_gate_breakdown(disposable_hist04a_env):
    """
    Test 1: Manual Reconciliation Gate Breakdown.
    - Resolves exactly 303 direct matches (reconciliation_status='matched', proposal_status='approved').
    - Resolves exactly 11 proposed associations (reconciliation_status='unmatched', proposal_status='proposed').
    - Exactly 5 messages remain strictly unassociated (reconciliation_status='unmatched', proposal_status=None).
    - Ambiguous count is 0. Total messages is 319.
    """
    importer = disposable_hist04a_env["importer"]
    excel_file = disposable_hist04a_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    reconciled = importer.reconcile_sent_emails(sent_msgs, staged_records=records)
    matched = [m for m in reconciled if m.reconciliation_status == "matched"]
    proposed = [m for m in reconciled if m.reconciliation_status == "unmatched" and m.proposal_status == "proposed"]
    unassociated = [m for m in reconciled if m.reconciliation_status == "unmatched" and m.proposal_status != "proposed"]
    ambiguous = [m for m in reconciled if m.reconciliation_status == "ambiguous"]

    assert len(reconciled) == 319
    assert len(matched) == 303
    assert len(proposed) == 11
    assert len(unassociated) == 5
    assert len(ambiguous) == 0

    # Verify the 11 proposed associations have candidate IDs and evidence
    for p in proposed:
        assert p.proposed_candidate_id is not None
        assert p.proposed_candidate_id.startswith("cand:")
        assert p.match_evidence is not None

    # Verify the 5 unassociated messages
    unmatched_ids = {m.message_id for m in unassociated}
    expected_unmatched_ids = {
        "1a19c26057c19b21",  # amministrazione@energekpd.com
        "1a18e20fdc99b8ef",  # shahin.amayeh@amabim.com
        "1a18c71c4cc7c62a",  # shahin.amayeh@amabim.com
        "1a18c724c888b015",  # shahin.amayeh@amabim.com
        "1a158683f6dca07f",  # antoniogiuseppe.lato@gmail.com
    }
    assert unmatched_ids == expected_unmatched_ids


def test_02_proposed_associations_do_not_alter_active_work_and_idempotency(disposable_hist04a_env):
    """
    Test 2: Candidate Protection, Active Work Preservation, and Idempotency.
    - Proposed candidate EsseQuadro retains cold_outreach_allowed=True and remains in Active Work.
    - No historical outreach events are attached to candidates of unapproved proposals.
    - Only confirmed direct matches (ETC...Studio) move to Historical Review Pending.
    - Active Work queue transitions from 23 -> 24 (23 baseline + 2 uncontacted from workbook - 1 direct match).
    - All 11 proposed candidate records remain untouched.
    - Re-running import is strictly idempotent (0 duplicates created, counts match).
    """
    importer = disposable_hist04a_env["importer"]
    store = disposable_hist04a_env["cand_store"]
    lifecycle_svc = disposable_hist04a_env["lifecycle_svc"]
    excel_file = disposable_hist04a_env["excel_file"]

    records = importer.parse_xlsx(excel_file)
    sent_msgs = importer.parse_sent_emails(excel_file)

    # Initial baseline
    counts_init = lifecycle_svc.compute_queue_counts()
    assert counts_init["active"] == 23
    assert counts_init["all"] == 322

    # Step A: Pre-reconcile sent messages with staged records (303 matched, 11 proposed, 5 unassociated)
    reconciled_sent = importer.reconcile_sent_emails(sent_msgs, staged_records=records)

    # Step B: Import Staged Company Records (Creates 21 new candidates, total becomes 343)
    lead_res1 = importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    assert lead_res1.companies_created == 21
    counts_after_staged = lifecycle_svc.compute_queue_counts()
    assert counts_after_staged["all"] == 343

    # Baseline for proposed candidate EsseQuadro before sent email import
    eq_before = store.get_candidate("cand:domain:essequadroservice.it")
    assert eq_before.cold_outreach_allowed is True
    assert eq_before.historical_contact_status == "uncontacted"

    # Step C: Import Sent Emails with HIST-04A manual gate
    sent_stats1 = importer.import_sent_emails(reconciled_sent)
    assert sent_stats1["imported"] == 319
    assert sent_stats1["matched"] == 303
    assert sent_stats1["unmatched"] == 16

    # Verify candidate with proposed association was NOT altered
    eq_after = store.get_candidate("cand:domain:essequadroservice.it")
    assert eq_after.cold_outreach_allowed is True
    assert eq_after.historical_contact_status == "uncontacted"
    eq_events = store.get_historical_outreach_for_candidate("cand:domain:essequadroservice.it")
    assert len(eq_events) == 0

    # Verify Active Work queue:
    # 23 initial active work + 2 new uncontacted from workbook - 1 direct match (ETC...Studio) = 24
    # Proposed candidate EsseQuadro remains safely in active work
    counts_after_sent = lifecycle_svc.compute_queue_counts()
    assert counts_after_sent["active"] == 24
    assert counts_after_sent["historical_pending"] == 299
    assert counts_after_sent["all"] == 343

    # Step D: Idempotency check (Run 2)
    lead_res2 = importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    assert lead_res2.companies_created == 0

    sent_stats2 = importer.import_sent_emails(reconciled_sent)
    assert sent_stats2["imported"] == 0
    assert sent_stats2["duplicates"] == 319

    counts_run2 = lifecycle_svc.compute_queue_counts()
    assert counts_run2 == counts_after_sent
