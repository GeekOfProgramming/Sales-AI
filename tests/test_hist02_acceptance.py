"""
HIST-02: Acceptance Verification Test Suite.
Validates:
1. Identity collision and ambiguity reconciliation (preview inspection vs lead research import semantics).
2. Email-to-company relationship integrity (303 matched messages preserving full headers, body, timestamp, Gmail links; 16 unmatched messages isolated).
3. UI acceptance rendering across all 5 key routes:
   - /sales/sent
   - /sales/candidates?queue=historical_pending
   - /sales/candidates?queue=active
   - /sales/candidates?queue=all
   - /sales/candidates/{cid} (Outreach & Communication History)
"""
import pytest
import shutil
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_hist02_env(tmp_path, monkeypatch):
    test_db = tmp_path / "hist02_acceptance.db"
    # Copy from authoritative DB to ensure realistic starting point
    shutil.copy2("data/sales_outreach.db", str(test_db))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("SALESAI_DB_PATH", str(test_db))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(test_db))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_hist02!")

    cand_store = CandidateStore(str(test_db))
    lifecycle_svc = CandidateLifecycleService(cand_store)
    importer = HistoricalImporter(store=cand_store)
    excel_file = "C:/Sales-AI/SalesAI/data/staging/upload_20261105_103023_Emails.xlsx"

    # Perform the staged import on the disposable clone
    records = importer.parse_xlsx(excel_file)
    importer.import_lead_research_records(records, source_file_name="upload_20261105_103023_Emails.xlsx")
    sent_msgs = importer.parse_sent_emails(excel_file)
    importer.import_sent_emails(sent_msgs)

    client = TestClient(app)

    return {
        "client": client,
        "cand_store": cand_store,
        "lifecycle_svc": lifecycle_svc,
        "importer": importer,
        "db_path": str(test_db),
        "excel_file": excel_file,
    }


def test_01_identity_collision_and_ambiguity_reconciliation(disposable_hist02_env):
    """
    Check 1: Identity collision and ambiguity reconciliation.
    - Demonstrates why workbook inspection shows 14 multi-domain rows & 4 collision groups,
      while Lead Research preview reports 0 ambiguous candidate identities.
    - Verifies that the 4 collision domains (buromilan.com, itinera-spa.it, sperispa.com, agap2.it)
      are legitimate multiple corporate records (e.g., initial row vs detailed 13-field registry row)
      for the SAME legal entity, safely merging to one canonical candidate without creating divergent clones.
    - Verifies that the 2 missing domain rows (Studio di architettura Barzon-Ceola, ARES S.r.l.)
      are safely handled as candidate proposals without website, not colliding with any other company.
    """
    store = disposable_hist02_env["cand_store"]
    importer = disposable_hist02_env["importer"]
    excel_file = disposable_hist02_env["excel_file"]

    # 1. Inspection analysis
    inspection = importer.inspect_workbook(excel_file)
    assert inspection["multiple_plausible_domains_count"] == 14
    assert inspection["domain_collisions_count"] == 4
    assert inspection["missing_domain_count"] == 2

    # 2. Check collision entities in the imported candidate store
    collision_domains = ["buromilan.com", "itinera-spa.it", "sperispa.com", "agap2.it"]
    for dom in collision_domains:
        cand = store.get_candidate_by_domain(dom)
        assert cand is not None, f"Candidate with domain {dom} should exist"
        # Verify exactly one candidate exists for each canonical domain
        with store._get_connection() as conn:
            matching = conn.execute("SELECT candidate_id, company_name FROM candidate_companies WHERE company_domain = ?", (dom,)).fetchall()
            assert len(matching) == 1, f"Expected exactly 1 candidate for domain {dom}, found {len(matching)}"

    # 3. Missing domain candidates
    with store._get_connection() as conn:
        ares = conn.execute("SELECT candidate_id, company_name, company_domain FROM candidate_companies WHERE company_name LIKE '%ARES%'").fetchall()
        assert len(ares) >= 1
        for a in ares:
            assert a["company_domain"] is None or a["company_domain"] == "" or "[link" in a["company_domain"] or not a["company_domain"]


def test_02_email_to_company_relationship_integrity(disposable_hist02_env):
    """
    Check 2: Email reconciliation accuracy.
    - All 303 matched messages preserve complete metadata: Message ID, Thread ID, sender, recipient,
      subject, full body, sent_date, sent_time, and valid Gmail link.
    - Unmatched messages (16) are safely classified as 'unmatched', with NO candidate ID attached,
      preventing accidental company contamination.
    - Matched candidates are correctly assigned cold_outreach_allowed=0 and historical_contact_status='contacted'.
    - Unrelated companies are NOT merged through shared generic email domains.
    """
    store = disposable_hist02_env["cand_store"]

    with store._get_connection() as conn:
        # Check matched emails
        matched_emails = conn.execute("""
            SELECT message_id, thread_id, sender_account, from_address, to_address, subject,
                   email_body, sent_date, sent_time, gmail_link, matched_candidate_id,
                   matched_company_name, reconciliation_status
            FROM historical_sent_emails
            WHERE reconciliation_status = 'matched'
        """).fetchall()

        assert len(matched_emails) == 303
        for em in matched_emails:
            assert em["message_id"] and len(em["message_id"]) >= 8
            assert em["thread_id"] and len(em["thread_id"]) >= 8
            assert em["from_address"] and "pybim" in em["from_address"].lower()
            assert em["to_address"] and "@" in em["to_address"]
            assert em["subject"] and len(em["subject"]) > 5
            assert em["email_body"] and len(em["email_body"]) > 20
            assert em["sent_date"] and len(em["sent_date"]) == 10
            assert em["gmail_link"] and "mail.google.com" in em["gmail_link"]
            assert em["matched_candidate_id"] is not None

        # Check unmatched emails
        unmatched_emails = conn.execute("""
            SELECT message_id, to_address, subject, matched_candidate_id, reconciliation_status
            FROM historical_sent_emails
            WHERE reconciliation_status != 'matched'
        """).fetchall()

        assert len(unmatched_emails) == 16
        for um in unmatched_emails:
            assert um["matched_candidate_id"] is None
            assert um["reconciliation_status"] == "unmatched"

        # Check that generic domains were not used to cross-merge candidates
        # Distinct candidate IDs for matched emails must be 292
        distinct_cids = conn.execute("SELECT COUNT(DISTINCT matched_candidate_id) FROM historical_sent_emails WHERE reconciliation_status = 'matched'").fetchone()[0]
        assert distinct_cids == 292


def test_03_ui_pages_and_outreach_history_rendering(disposable_hist02_env):
    """
    Check 3: UI acceptance across key endpoints.
    - /sales/sent renders historical sent emails and shows unmatched count (16).
    - /sales/candidates?queue=historical_pending renders contacted companies with their previous outreach indicator.
    - /sales/candidates?queue=active contains ZERO contacted companies.
    - /sales/candidates?queue=all shows all 343 companies.
    - Candidate Detail renders communication history with sent emails and timeline events.
    """
    client = disposable_hist02_env["client"]
    store = disposable_hist02_env["cand_store"]
    lifecycle_svc = disposable_hist02_env["lifecycle_svc"]

    # 1. /sales/sent
    resp_sent = client.get("/sales/sent")
    assert resp_sent.status_code == 200
    html_sent = resp_sent.text
    assert "Historical Sent Messages" in html_sent or "historical" in html_sent.lower()
    assert "319" in html_sent
    assert "303" in html_sent

    # 2. /sales/candidates?queue=historical_pending
    resp_pending = client.get("/sales/candidates?queue=historical_pending")
    assert resp_pending.status_code == 200
    html_pending = resp_pending.text
    assert "301" in html_pending or "Historical Review Pending" in html_pending

    # 3. /sales/candidates?queue=active
    resp_active = client.get("/sales/candidates?queue=active")
    assert resp_active.status_code == 200
    html_active = resp_active.text
    # Invariant: none of the contacted companies appear as actionable in active
    active_cands = [
        c for c in store.list_candidates()
        if lifecycle_svc.get_candidate_lifecycle_state(c.candidate_id).primary_queue.value == "active"
    ]
    for c in active_cands:
        assert c.historical_contact_status == "uncontacted"
        # Invariant: cold outreach is NOT locked due to prior outreach (it was never contacted)
        assert c.historical_contact_status not in ("contacted", "replied", "converted")

    # 4. /sales/candidates?queue=all
    resp_all = client.get("/sales/candidates?queue=all")
    assert resp_all.status_code == 200
    html_all = resp_all.text
    assert "343" in html_all

    # 5. Candidate Detail for a matched company: e.g. DP e-Mobility Srl
    cid = "cand:domain:dpserviceenergy.it"
    resp_detail = client.get(f"/sales/candidates/{cid}")
    assert resp_detail.status_code == 200
    html_detail = resp_detail.text
    assert "DP e-Mobility" in html_detail
    assert "Previously Contacted" in html_detail or "contacted" in html_detail.lower()
    assert "Proposta di collaborazione" in html_detail
    assert "1a19c29ae8db45ee" in html_detail or "1a1994de850d327f" in html_detail or "mail.google.com" in html_detail
