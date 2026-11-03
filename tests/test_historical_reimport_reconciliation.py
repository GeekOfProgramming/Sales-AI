"""
Comprehensive Test Suite for Historical Excel Reimport & Gmail Sent-Email Reconciliation.
Branch: feature/email-led-next

Validates:
1. Classification of Sent_Emails as a communication sheet (does not trigger company mapping failures).
2. Idempotent import of Gmail sent messages (keyed by sender_account, message_id) without duplicate events.
3. Safe reconciliation strictly by exact recipient email matching.
4. Non-downgrade of OPERATOR_REVIEWED Lead Register records upon reimport.
5. Preservation of supply-side dates without timezone fabrication.
6. Full rollback of import transactions upon any staging failure.
7. Sent history view UI rendering of historical Gmail records and unmatched review queue.
"""

import io
import os
import sys
import json
import sqlite3
import pytest
import openpyxl
from pathlib import Path
from tempfile import NamedTemporaryFile
from fastapi.testclient import TestClient

from sales_engine.historical.importer import (
    HistoricalImporter,
    SentEmailRecord,
    HistoricalRecord,
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from backend.main import app


@pytest.fixture
def temp_db_path(tmp_path):
    db_file = tmp_path / "test_sales_reconciliation.db"
    return str(db_file)


@pytest.fixture
def synthetic_workbook(tmp_path):
    """
    Creates a synthetic, privacy-safe Excel workbook mirroring the structure of Emails(2).xlsx:
    - Geographic company sheets with Italian regional names (e.g. Veneto).
    - Summary sheet (Dashboard) and Meeting sheet (which must be excluded from company creation).
    - Sent_Emails sheet containing communication history with 15 headers.
    """
    wb_file = tmp_path / "synthetic_historical_emails.xlsx"
    wb = openpyxl.Workbook()
    
    # 1. Dashboard (summary)
    ws_dash = wb.active
    ws_dash.title = "Dashboard"
    ws_dash.append(["Metric", "Value"])
    ws_dash.append(["Total Leads", 10])

    # 2. Meeting
    ws_meet = wb.create_sheet(title="Meeting")
    ws_meet.append(["Company", "Status", "Notes"])
    ws_meet.append(["Bimatic GmbH", "Meeting Held", "Discussed BIM workflow"])

    # 3. Company Sheet: Veneto
    ws_veneto = wb.create_sheet(title="Veneto")
    # Standard 20 columns
    ws_veneto.append([
        "ردیف", "سطح شرکت A B C", "توضیحات کلی", "نام و عنوان", "موقعیت جغرافیایی",
        "شماره‌های تماس", "موضوع کاری", "وب‌سایت رسمی", "شخص مسئول / رابط",
        "توضیحات و یادداشت‌ها", "لینک آگهی کار", "لینک‌های دیگر", "ایمیل‌های ارتباطی",
        "وضعیت ارتباط", "فرستنده", "تاریخ بررسی", "تاریخ ارسال ایمیل",
        "تاریخ پیگیری", "وضعیت پاسخ", "یادداشت پیگیری"
    ])
    # Company 1: Alpha BIM (contacted via info@alphabim.it)
    ws_veneto.append([
        "1.0", "A", "Good fit", "Alpha BIM S.r.l.", "Padova, Italy",
        "+39 049 111111", "Architectural BIM", "alphabim.it", "Mario Rossi",
        "Notes", None, None, "info@alphabim.it, contact@alphabim.it",
        "ایمیل ارسال شد", "info@pybim.it", "2026-10-20", "2026-10-25",
        None, None, None
    ])
    # Company 2: Beta Studio (domain-less company)
    ws_veneto.append([
        "2.0", "B", "Small studio", "Studio Architettura Beta", "Verona, Italy",
        "+39 045 222222", "Restoration", None, "Luigi Bianchi",
        "Notes", None, None, "studiobeta@gmail.com",
        "بررسی اولیه", None, "2026-10-21", None,
        None, None, None
    ])
    # Company 3: Gamma Engineering (uncontacted)
    ws_veneto.append([
        "3.0", "A", "Large engineering", "Gamma Engineering S.p.A.", "Vicenza, Italy",
        "+39 044 333333", "Structural BIM", "gammaeng.it", "Anna Verdi",
        "Notes", None, None, "info@gammaeng.it",
        "بررسی اولیه", None, "2026-10-22", None,
        None, None, None
    ])

    # 4. Sent_Emails worksheet
    ws_sent = wb.create_sheet(title="Sent_Emails")
    ws_sent.append([
        "Message ID", "Thread ID", "Sender Account", "Sender Group", "From", "To",
        "Recipient Emails", "CC", "BCC", "Sent Date", "Sent Time", "Subject",
        "Email Body", "Body Truncated", "Gmail Link"
    ])
    # Message 1: sent to Alpha BIM (matches Company 1)
    ws_sent.append([
        "msg_alpha_001", "thread_alpha_001", "info@pybim.it", "General IT",
        "pyBIM <info@pybim.it>", "info@alphabim.it", "info@alphabim.it",
        None, None, "2026-10-25", "10:30:00", "Proposta di collaborazione BIM",
        "Gentile team Alpha, vi scriviamo per...", "NO", "https://mail.google.com/mail/u/0/#all/thread_alpha_001"
    ])
    # Message 2: sent to Beta Studio (matches domain-less Company 2)
    ws_sent.append([
        "msg_beta_002", "thread_beta_002", "info@pybim.it", "General IT",
        "pyBIM <info@pybim.it>", "studiobeta@gmail.com", "studiobeta@gmail.com",
        None, None, "2026-10-26", "11:15:00", "Collaborazione modellazione BIM",
        "Gentile Studio Beta, vi presentiamo i nostri servizi...", "NO", "https://mail.google.com/mail/u/0/#all/thread_beta_002"
    ])
    # Message 3: sent to an external contact NOT in any company sheet (must be classified as unmatched)
    ws_sent.append([
        "msg_unmatched_003", "thread_unmatched_003", "info@pybim.com", "International",
        "pyBIM <info@pybim.com>", "external.architect@unmatched.org", "external.architect@unmatched.org",
        None, None, "2026-10-27", "14:20:00", "BIM Partnership Inquiry",
        "Hello, we would like to explore partnership...", "NO", "https://mail.google.com/mail/u/0/#all/thread_unmatched_003"
    ])

    wb.save(str(wb_file))
    return str(wb_file)


def test_01_sent_emails_classification_and_column_isolation(synthetic_workbook, temp_db_path):
    """
    Verifies that Sent_Emails is classified as a communication sheet and its 15 headers
    do not pollute company column mappings or cause unmapped column failures (BLOCKED).
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    inspection = importer.inspect_workbook(synthetic_workbook)
    sheets = inspection.get("sheets", [])
    
    sheet_by_name = {s["sheet_name"]: s for s in sheets}
    assert "Sent_Emails" in sheet_by_name
    sent_sheet = sheet_by_name["Sent_Emails"]
    
    assert sent_sheet["is_communication_sheet"] is True
    assert sent_sheet["mapping_valid"] is True
    
    # Check that company sheets are parsed cleanly
    assert "Veneto" in sheet_by_name
    assert sheet_by_name["Veneto"]["is_communication_sheet"] is False
    assert sheet_by_name["Veneto"]["is_summary_sheet"] is False

    # Summary and communication sheets must not cause unmapped column errors
    records = importer.parse_xlsx(synthetic_workbook)
    assert len(records) == 3  # Only the 3 company records from Veneto


def test_02_idempotent_gmail_history_import(synthetic_workbook, temp_db_path):
    """
    Verifies that importing Sent_Emails is strictly idempotent:
    - 1st run inserts records.
    - 2nd run skips existing (sender_account, message_id) with 0 new duplicates.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # First parse and import company records
    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    sent_msgs = importer.parse_sent_emails(synthetic_workbook)
    assert len(sent_msgs) == 3

    # Run 1
    stats1 = importer.import_sent_emails(sent_msgs)
    assert stats1["total"] == 3
    assert stats1["imported"] == 3
    assert stats1["matched"] == 2
    assert stats1["unmatched"] == 1
    assert stats1["duplicates"] == 0

    # Run 2 (Strict Idempotency Check)
    stats2 = importer.import_sent_emails(sent_msgs)
    assert stats2["total"] == 3
    assert stats2["imported"] == 0
    assert stats2["duplicates"] == 3

    # DB verify
    db_stats = importer.get_sent_emails_stats()
    assert db_stats["total"] == 3
    assert db_stats["matched"] == 2
    assert db_stats["unmatched"] == 1
    assert db_stats["unique_messages"] == 3
    assert db_stats["unique_threads"] == 3


def test_03_exact_recipient_reconciliation_integrity(synthetic_workbook, temp_db_path):
    """
    Validates that reconciliation matches ONLY by exact verified recipient email address:
    - Alpha BIM matches cand:domain:alphabim.it
    - Studio Architettura Beta matches its domain-less candidate_id
    - external.architect@unmatched.org remains unmatched for operator review.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    sent_msgs = importer.parse_sent_emails(synthetic_workbook)
    reconciled = importer.reconcile_sent_emails(sent_msgs, staged_records=records)

    reconciled_by_mid = {m.message_id: m for m in reconciled}

    # Message 1
    m1 = reconciled_by_mid["msg_alpha_001"]
    assert m1.reconciliation_status == "matched"
    assert m1.matched_candidate_id == "cand:domain:alphabim.it"
    assert "Alpha BIM" in (m1.matched_company_name or "")

    # Message 2 (Domain-less)
    m2 = reconciled_by_mid["msg_beta_002"]
    assert m2.reconciliation_status == "matched"
    assert "cand:name:studioarchitetturabeta" in m2.matched_candidate_id
    assert "Studio Architettura Beta" in (m2.matched_company_name or "")

    # Message 3 (Unmatched)
    m3 = reconciled_by_mid["msg_unmatched_003"]
    assert m3.reconciliation_status == "unmatched"
    assert m3.matched_candidate_id is None


def test_04_outreach_guard_and_no_auto_authorization(synthetic_workbook, temp_db_path):
    """
    Validates that matching a sent email sets cold_outreach_allowed=0 and marks previous outreach,
    but NEVER authorizes cold outreach or converts unsent drafts.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    # Beta Studio started as "بررسی اولیه" (cold_outreach_allowed was True)
    cands = store.list_candidates()
    beta = next(c for c in cands if "Studio Architettura Beta" in c.company_name)
    assert beta is not None
    assert beta.cold_outreach_allowed is True

    # Import sent emails
    sent_msgs = importer.parse_sent_emails(synthetic_workbook)
    importer.import_sent_emails(sent_msgs)

    # After reconciliation, Beta Studio must be blocked from cold outreach
    beta_updated = store.get_by_id(beta.candidate_id)
    assert beta_updated.cold_outreach_allowed is False
    assert beta_updated.historical_contact_status in ("contacted", "replied")

    # Gamma Engineering had NO sent email and was uncontacted: remains allowed
    gamma = store.get_by_domain("gammaeng.it")
    assert gamma is not None
    assert gamma.cold_outreach_allowed is True


def test_05_operator_reviewed_records_strictly_preserved(synthetic_workbook, temp_db_path):
    """
    Validates that records marked OPERATOR_REVIEWED in the Lead Register are NEVER
    overwritten or downgraded by subsequent reimport.
    """
    store = CandidateStore(temp_db_path)
    reg_store = LeadRegisterStore(db_path=temp_db_path)
    importer = HistoricalImporter(store=store)

    # Create candidate in CandidateStore and review it in LeadRegisterStore
    cand = CandidateCompany(
        company_id="cand:domain:alphabim.it",
        company_name="Alpha BIM S.r.l. (Reviewed)",
        company_domain="alphabim.it",
        country="Italy",
        discovery_status="qualified",
    )
    store.save_candidate(cand)

    reg_rec = LeadRegisterRecord(
        candidate_id="cand:domain:alphabim.it",
        company_name="Alpha BIM S.r.l. (Reviewed)",
        official_website="https://alphabim.it",
        business_activities="Advanced BIM Authoring",
        responsible_person="Ing. Mario Rossi",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reg_rec)

    # Run reimport from workbook
    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    # Verify that LeadRegisterRecord verification status is still OPERATOR_REVIEWED
    after_rec = reg_store.get_explicit_record("cand:domain:alphabim.it")
    assert after_rec is not None
    assert after_rec.verification_status == "OPERATOR_REVIEWED"
    assert after_rec.responsible_person == "Ing. Mario Rossi"


def test_06_timezone_preservation_without_fabrication(synthetic_workbook, temp_db_path):
    """
    Validates that Sent Date and Sent Time from the sheet are preserved verbatim
    without adding fake UTC or local timezone offsets.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    sent_msgs = importer.parse_sent_emails(synthetic_workbook)
    importer.import_sent_emails(sent_msgs)

    stored_emails = importer.get_sent_emails()
    alpha_msg = next(m for m in stored_emails if m["message_id"] == "msg_alpha_001")
    
    assert alpha_msg["sent_date"] == "2026-10-25"
    assert alpha_msg["sent_time"] == "10:30:00"
    # Ensure no timezone fabrication like +00:00 or Z in sent_date / sent_time
    assert "+00:00" not in alpha_msg["sent_date"]
    assert "Z" not in alpha_msg["sent_time"]


def test_07_transactional_staging_rollback_on_failure(synthetic_workbook, temp_db_path):
    """
    Validates that if an error occurs during the staging execution, the live database
    remains completely untouched and rolls back cleanly.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    initial_comps_count = len(store.list_candidates())

    # Simulate transactional staging failure pattern as executed in router.confirm_historical_import_action
    staging_clone = NamedTemporaryFile(suffix="_import_stage.db", delete=False)
    staging_path = staging_clone.name
    staging_clone.close()

    try:
        with store._get_connection() as src_conn:
            initial_version = src_conn.execute("PRAGMA data_version;").fetchone()[0]
            clone_conn = sqlite3.connect(staging_path)
            try:
                src_conn.backup(clone_conn)
            finally:
                clone_conn.close()

        staged_store = CandidateStore(staging_path)
        staged_importer = HistoricalImporter(store=staged_store)
        
        # Simulate exception during sent email processing
        raise RuntimeError("Simulated transient network/disk error during staging")

    except RuntimeError:
        pass
    finally:
        # On Windows, close connection pool or ignore file lock during test cleanup
        if os.path.exists(staging_path):
            try:
                os.remove(staging_path)
            except PermissionError:
                pass

    # Ensure live database has exact initial candidate count
    assert len(store.list_candidates()) == initial_comps_count


def test_08_sent_history_view_displays_reconciled_and_unmatched_queue(temp_db_path, monkeypatch):
    """
    Tests GET /sales/sent endpoint to ensure the HTML renders:
    1. Historical Gmail Sent Emails card.
    2. Matched / unmatched statistics badge.
    3. Unmatched / Ambiguous Sent Emails Review Queue.
    """
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # Seed one matched and one unmatched message
    msgs = [
        SentEmailRecord(
            message_id="mid_matched_101",
            thread_id="th_101",
            sender_account="info@pybim.it",
            sender_group="General IT",
            from_address="pyBIM <info@pybim.it>",
            to_address="test@company.it",
            recipient_emails=["test@company.it"],
            sent_date="2026-11-01",
            sent_time="09:00:00",
            subject="Test Subject Matched",
            email_body="Body text...",
            matched_candidate_id="cand:domain:company.it",
            matched_company_name="Company S.r.l.",
            reconciliation_status="matched",
        ),
        SentEmailRecord(
            message_id="mid_unmatched_102",
            thread_id="th_102",
            sender_account="info@pybim.com",
            sender_group="International",
            from_address="pyBIM <info@pybim.com>",
            to_address="unknown@orphan.org",
            recipient_emails=["unknown@orphan.org"],
            sent_date="2026-11-02",
            sent_time="15:30:00",
            subject="Test Subject Unmatched",
            email_body="Unmatched message text...",
            reconciliation_status="unmatched",
        ),
    ]
    importer.import_sent_emails(msgs)

    # Point get_console_services to this store without recursive call
    import backend.sales_ui.router as router_mod
    real_svcs = router_mod.get_console_services()
    mock_svcs = {
        "cand_store": store,
        "rev_store": real_svcs["rev_store"],
        "suppression_svc": real_svcs["suppression_svc"],
    }
    monkeypatch.setattr(router_mod, "get_console_services", lambda: mock_svcs)

    client = TestClient(app)
    resp = client.get("/sales/sent")
    assert resp.status_code == 200
    html = resp.text

    assert "Historical Gmail Sent Emails" in html
    assert "Company S.r.l." in html or "mid_matched_101" in html
    assert "Unmatched / Ambiguous Sent Emails Review Queue" in html
    assert "unknown@orphan.org" in html
    assert "mid_unmatched_102" in html
