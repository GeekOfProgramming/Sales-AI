"""
HV-02 Comprehensive Regression Test Suite:
Complete Historical Excel Import & Local Database Integrity.

Branch: feature/email-led-human-verification

Tests:
1. Complete 15-field preservation in Sent_Emails and idempotency on (sender_account, message_id).
2. Unmatched email review queue preserves all unmatched messages with body, dates, and headers.
3. Meeting worksheet activity ingestion links to existing companies without creating duplicate companies.
4. Expanded HistoricalRecord.compute_hash() detects modifications to notes, phone numbers, topics, and links.
5. Reimport preserves operator-reviewed Lead Register fields and field reviews.
6. Exclusion Registry and historical DNC precedence is preserved during reimport (cold_outreach_allowed=False).
7. Staged transaction rollback cleanly restores initial database state if any error occurs.
8. Zero authoritative mutation during preview simulation.
9. Verification of actual updated workbook upload_20261103_215831_Emails.xlsx against a cloned test database.
"""

import io
import os
import sys
import json
import sqlite3
import hashlib
import pytest
import openpyxl
from pathlib import Path
from tempfile import NamedTemporaryFile
from fastapi.testclient import TestClient

from sales_engine.historical.importer import (
    HistoricalImporter,
    SentEmailRecord,
    MeetingRecord,
    HistoricalRecord,
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    normalize_outreach_status,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from backend.main import app


@pytest.fixture
def temp_db_path(tmp_path):
    db_file = tmp_path / "test_hv02_sales.db"
    return str(db_file)


@pytest.fixture
def synthetic_hv02_workbook(tmp_path):
    """
    Creates a synthetic workbook mirroring Emails(2).xlsx structure:
    - Veneto (company rows)
    - Meeting (5 activity rows matching existing companies)
    - Sent_Emails (15 columns, some matched, some unmatched)
    """
    wb_file = tmp_path / "synthetic_hv02_emails.xlsx"
    wb = openpyxl.Workbook()

    # 1. Dashboard
    ws_dash = wb.active
    ws_dash.title = "Dashboard"
    ws_dash.append(["Metric", "Value"])
    ws_dash.append(["Total Leads", 5])

    # 2. Meeting worksheet (5 activity rows)
    ws_meet = wb.create_sheet(title="Meeting")
    ws_meet.append([
        "ردیف", "سطح شرکت A B C", "دلیل الویت", "نام و عنوان", "موقعیت جغرافیایی",
        "شماره‌های تماس", "موضوع کاری", "وب‌سایت رسمی", "شخص مسئول / رابط",
        "توضیحات و یادداشت‌ها", "لینک آگهی کار", "سایر لینک‌ها و راه‌های ارتباطی",
        "ایمیل‌های ارتباطی", "وضعیت ارتباط", "ارسال کننده", "تاریخ آخرین تغییر",
        "تاریخ ارسال ایمیل", "تاریخ پیگیری", "تاریخ پاسخدهی", "یادداشت‌های پیگیری", "شیت مبدا"
    ])
    # Meeting row 1: Alpha BIM
    ws_meet.append([
        1, "A", "High priority BIM partner", "Alpha BIM S.r.l.", "Padova, Italy",
        "+39 049 111111", "Architectural BIM", "https://www.alphabim.it/", "Mario Rossi",
        "Meeting discussion notes", None, None, "info@alphabim.it", "پیگیری شود",
        "info@pybim.it", "2026-10-22", "2026-10-21 10:00:00", "2026-11-02", "2026-10-21 11:30:00",
        "Presentation sent, follow up next week", "Veneto"
    ])
    # Meeting row 2: Bimatic GmbH
    ws_meet.append([
        2, "A", "Infrastructure specialist", "Bimatic GmbH", "Zurich, Switzerland",
        "+41 79 203 93 85", "Infrastructure Scan-to-BIM", "https://www.bimatic.ch/en", "Jan Sigrist",
        "Infrastructure partner", None, None, "jan.sigrist@bimatic.ch", "مصاحبه / دعوت به همکاری",
        "info@pybim.com", "2026-10-29", "2026-10-28 12:58:00", None, "2026-10-29 07:36:00",
        "Follow up for 2-way partnership", "Switzerland"
    ])

    # 3. Company Sheet: Veneto
    ws_veneto = wb.create_sheet(title="Veneto")
    ws_veneto.append([
        "ردیف", "سطح شرکت A B C", "دلیل الویت", "نام و عنوان", "موقعیت جغرافیایی",
        "شماره‌های تماس", "موضوع کاری", "وب‌سایت رسمی", "شخص مسئول / رابط",
        "توضیحات و یادداشت‌ها", "لینک آگهی کار", "سایر لینک‌ها و راه‌های ارتباطی", "ایمیل‌های ارتباطی",
        "وضعیت ارتباط", "ارسال کننده", "تاریخ آخرین تغییر", "تاریخ ارسال ایمیل",
        "تاریخ پیگیری", "تاریخ پاسخدهی", "یادداشت‌های پیگیری"
    ])
    # Company 1: Alpha BIM S.r.l.
    ws_veneto.append([
        "1.0", "A", "High priority", "Alpha BIM S.r.l.", "Padova, Italy",
        "+39 049 111111", "Architectural BIM", "https://www.alphabim.it/", "Mario Rossi",
        "Detailed company notes here", None, None, "info@alphabim.it, contact@alphabim.it",
        "ایمیل ارسال شد", "info@pybim.it", "2026-10-20", "2026-10-21 10:00:00",
        "2026-11-02", None, None
    ])
    # Company 2: Bimatic GmbH
    ws_veneto.append([
        "2.0", "A", "Infrastructure", "Bimatic GmbH", "Zurich, Switzerland",
        "+41 79 203 93 85", "Scan to BIM", "https://www.bimatic.ch/en", "Jan Sigrist",
        "Swiss BIM firm", None, None, "jan.sigrist@bimatic.ch",
        "بررسی اولیه", "info@pybim.com", "2026-10-28", None,
        None, None, None
    ])
    # Company 3: Gamma Studio (uncontacted)
    ws_veneto.append([
        "3.0", "B", None, "Gamma Studio Architettura", "Treviso, Italy",
        "+39 0422 99999", "Restoration", "https://www.gammastudio.it/", "Luigi Gamma",
        "Initial candidate", None, None, "luigi@gammastudio.it",
        "بررسی اولیه", None, "2026-10-25", None,
        None, None, None
    ])

    # 4. Sent_Emails (15 columns)
    ws_sent = wb.create_sheet(title="Sent_Emails")
    ws_sent.append([
        "Message ID", "Thread ID", "Sender Account", "Sender Group", "From", "To",
        "Recipient Emails", "CC", "BCC", "Sent Date", "Sent Time", "Subject",
        "Email Body", "Body Truncated", "Gmail Link"
    ])
    # Message 1: Sent to Alpha BIM (matched)
    ws_sent.append([
        "msg_alpha_01", "thread_alpha_01", "info@pybim.it", "Italy", "pyBIM <info@pybim.it>",
        "info@alphabim.it", "info@alphabim.it", None, None, "2026-10-21", "10:00:00",
        "Proposta di collaborazione", "Gentile Alpha BIM, ecco la proposta.", "NO",
        "https://mail.google.com/mail/u/0/#all/thread_alpha_01"
    ])
    # Message 2: Sent to an external consultant (unmatched)
    ws_sent.append([
        "msg_unmatched_01", "thread_unmatched_01", "info@pybim.com", "General", "pyBIM <info@pybim.com>",
        "architect@unknownconsulting.com", "architect@unknownconsulting.com", "cc@other.com", None,
        "2026-10-27", "16:45:00", "International BIM Inquiry", "Hello, do you support IFC4?", "NO",
        "https://mail.google.com/mail/u/0/#all/thread_unmatched_01"
    ])

    wb.save(str(wb_file))
    return str(wb_file)


# ---------------------------------------------------------------------------
# Test 1: Complete 15-field preservation in Sent_Emails & Idempotency
# ---------------------------------------------------------------------------
def test_01_complete_15_field_preservation_and_idempotency(synthetic_hv02_workbook, temp_db_path):
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    sent_records = importer.parse_sent_emails(synthetic_hv02_workbook)
    assert len(sent_records) == 2

    # Check that all 15 fields are parsed faithfully without truncation or timezone shifts
    msg1 = next(m for m in sent_records if m.message_id == "msg_alpha_01")
    assert msg1.message_id == "msg_alpha_01"
    assert msg1.thread_id == "thread_alpha_01"
    assert msg1.sender_account == "info@pybim.it"
    assert msg1.sender_group == "Italy"
    assert msg1.from_address == "pyBIM <info@pybim.it>"
    assert msg1.to_address == "info@alphabim.it"
    assert msg1.recipient_emails == ["info@alphabim.it"]
    assert msg1.sent_date == "2026-10-21"
    assert msg1.sent_time == "10:00:00"
    assert msg1.subject == "Proposta di collaborazione"
    assert msg1.email_body == "Gentile Alpha BIM, ecco la proposta."
    assert msg1.body_truncated == "NO"
    assert "thread_alpha_01" in msg1.gmail_link

    # First import
    stats1 = importer.import_sent_emails(sent_records)
    assert stats1["imported"] == 2
    assert stats1["duplicates"] == 0

    # Second import (strict idempotency on (sender_account, message_id))
    stats2 = importer.import_sent_emails(sent_records)
    assert stats2["imported"] == 0
    assert stats2["duplicates"] == 2

    # Verify rows in DB
    all_rows = importer.get_sent_emails()
    assert len(all_rows) == 2
    for r in all_rows:
        assert r["body_truncated"] == "NO"
        assert r["source_sheet"] == "Sent_Emails"


# ---------------------------------------------------------------------------
# Test 2: Unmatched email queue preserves all details for operator review
# ---------------------------------------------------------------------------
def test_02_unmatched_email_queue_preservation(synthetic_hv02_workbook, temp_db_path):
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # First import company records so that Alpha BIM is known to the store
    comp_records = importer.parse_xlsx(synthetic_hv02_workbook, sheet_name="Veneto")
    importer.import_records(comp_records)

    sent_records = importer.parse_sent_emails(synthetic_hv02_workbook)
    importer.import_sent_emails(sent_records)

    unmatched = importer.get_sent_emails(status="unmatched")
    assert len(unmatched) == 1
    unmatched_msg = unmatched[0]
    assert unmatched_msg["message_id"] == "msg_unmatched_01"
    assert unmatched_msg["recipient_emails"] == ["architect@unknownconsulting.com"]
    assert unmatched_msg["email_body"] == "Hello, do you support IFC4?"
    assert unmatched_msg["cc"] == "cc@other.com"
    assert unmatched_msg["sent_date"] == "2026-10-27"
    assert unmatched_msg["sent_time"] == "16:45:00"


# ---------------------------------------------------------------------------
# Test 3: Meeting sheet activity ingestion links without creating duplicates
# ---------------------------------------------------------------------------
def test_03_meeting_activity_ingestion_links_without_duplicate_companies(synthetic_hv02_workbook, temp_db_path):
    store = CandidateStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # 1. Parse and import companies from Veneto sheet
    comp_records = importer.parse_xlsx(synthetic_hv02_workbook, sheet_name="Veneto")
    importer.import_records(comp_records)

    with store._get_connection() as conn:
        initial_comp_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    assert initial_comp_count == 3

    # 2. Parse and import meeting activities
    meetings = importer.parse_meeting_activities(synthetic_hv02_workbook)
    assert len(meetings) == 2

    meeting_stats = importer.import_meeting_activities(meetings, staged_records=comp_records)
    assert meeting_stats["imported"] == 2
    assert meeting_stats["matched"] == 2
    assert meeting_stats["unmatched"] == 0

    # Invariant: candidate_companies count MUST NOT increase
    with store._get_connection() as conn:
        after_comp_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    assert after_comp_count == initial_comp_count

    # Verify meeting activities table
    activities = importer.get_meeting_activities()
    assert len(activities) == 2
    alpha_act = next(a for a in activities if "Alpha" in a["company_name"])
    assert alpha_act["matched_candidate_id"] == "cand:domain:alphabim.it"
    assert "Presentation sent" in alpha_act["follow_up_notes"]
    assert alpha_act["origin_sheet"] == "Veneto"

    # Verify candidate company status update
    alpha_cand = store.get_by_domain("alphabim.it")
    assert alpha_cand.cold_outreach_allowed is False
    assert alpha_cand.active_conversation is True
    assert alpha_cand.historical_contact_status in ("contacted", "replied")


# ---------------------------------------------------------------------------
# Test 4: Expanded HistoricalRecord.compute_hash() detects modifications
# ---------------------------------------------------------------------------
def test_04_expanded_compute_hash_detects_mutable_descriptive_edits():
    base_rec = HistoricalRecord(
        source_file="Emails(2).xlsx",
        sheet_name="Veneto",
        row_number=10,
        company_name="Studio Alpha",
        domain="alpha.it",
        tier_hint="A",
        notes="Original notes",
        source_url=None,
        contact_name="Mario Rossi",
        contact_role="Director",
        country="Italy",
        city="Padova",
        emails=["info@alpha.it"],
        phone_numbers="+39 049 123456",
        business_topic="BIM Coordination",
        job_link="https://alpha.it/jobs",
        other_links="https://linkedin.com/in/mario",
        follow_up_notes="Follow up soon",
        raw_location="Padova (PD), Italy",
    )
    base_hash = base_rec.compute_hash()

    # Changing notes must produce a distinct hash
    rec_notes_changed = HistoricalRecord(**{**base_rec.__dict__, "notes": "Updated notes with new BIM details"})
    assert rec_notes_changed.compute_hash() != base_hash

    # Changing phone number must produce a distinct hash
    rec_phone_changed = HistoricalRecord(**{**base_rec.__dict__, "phone_numbers": "+39 049 999999"})
    assert rec_phone_changed.compute_hash() != base_hash

    # Changing business topic must produce a distinct hash
    rec_topic_changed = HistoricalRecord(**{**base_rec.__dict__, "business_topic": "Scan-to-BIM & Digital Twin"})
    assert rec_topic_changed.compute_hash() != base_hash

    # Changing job link must produce a distinct hash
    rec_job_changed = HistoricalRecord(**{**base_rec.__dict__, "job_link": "https://alpha.it/careers/bim-lead"})
    assert rec_job_changed.compute_hash() != base_hash

    # Changing other links must produce a distinct hash
    rec_other_changed = HistoricalRecord(**{**base_rec.__dict__, "other_links": "https://instagram.com/alpha"})
    assert rec_other_changed.compute_hash() != base_hash

    # Changing follow up notes must produce a distinct hash
    rec_fnotes_changed = HistoricalRecord(**{**base_rec.__dict__, "follow_up_notes": "Call scheduled on Friday"})
    assert rec_fnotes_changed.compute_hash() != base_hash

    # Changing raw location must produce a distinct hash
    rec_loc_changed = HistoricalRecord(**{**base_rec.__dict__, "raw_location": "Milano, Italy"})
    assert rec_loc_changed.compute_hash() != base_hash

    # Identical content in another file/row must produce the EXACT same hash
    identical_rec = HistoricalRecord(
        **{**base_rec.__dict__, "source_file": "Different_Workbook.xlsx", "row_number": 999}
    )
    assert identical_rec.compute_hash() == base_hash


# ---------------------------------------------------------------------------
# Test 5: Reimport preserves operator-reviewed Lead Register fields
# ---------------------------------------------------------------------------
def test_05_reimport_preserves_operator_reviewed_lead_register_fields(synthetic_hv02_workbook, temp_db_path):
    store = CandidateStore(temp_db_path)
    lr_store = LeadRegisterStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # 1. Initial import of companies
    records = importer.parse_xlsx(synthetic_hv02_workbook, sheet_name="Veneto")
    importer.import_records(records)

    # 2. Simulate operator review on Alpha BIM
    cid = "cand:domain:alphabim.it"
    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Alpha BIM S.r.l.",
        verification_status="OPERATOR_REVIEWED",
        responsible_person="Ing. Mario Rossi (Verified by Operator)",
        official_website="https://www.alphabim.it/",
    )
    lr_store.save_record(reg_rec)

    from sales_engine.leads.register_models import FieldReviewRecord
    review_rec = FieldReviewRecord(
        candidate_id=cid,
        field_name="responsible_person",
        approved_value="Ing. Mario Rossi (Verified by Operator)",
        value_checksum=hashlib.sha256(b"Ing. Mario Rossi (Verified by Operator)").hexdigest(),
        review_state="OPERATOR_APPROVED",
        source_type="operator_manual",
        operator_note="Confirmed via official company imprint",
    )
    lr_store.save_field_review(review_rec)

    # 3. Perform Re-import
    reimport_records = importer.parse_xlsx(synthetic_hv02_workbook, sheet_name="Veneto")
    importer.import_records(reimport_records)

    # Invariant: Lead Register record and Field Review MUST retain OPERATOR_REVIEWED state
    after_reg = lr_store.get_record(cid)
    assert after_reg is not None
    assert after_reg.verification_status == "OPERATOR_REVIEWED"
    assert after_reg.responsible_person == "Ing. Mario Rossi (Verified by Operator)"

    field_reviews = lr_store.get_field_reviews(cid)
    assert len(field_reviews) >= 1
    assert "responsible_person" in field_reviews
    rp_review = field_reviews["responsible_person"]
    assert rp_review.review_state == "OPERATOR_APPROVED"
    assert rp_review.approved_value == "Ing. Mario Rossi (Verified by Operator)"


# ---------------------------------------------------------------------------
# Test 6: Exclusion Registry & DNC Precedence is preserved during reimport
# ---------------------------------------------------------------------------
def test_06_exclusion_and_dnc_precedence_preserved_during_reimport(temp_db_path):
    store = CandidateStore(temp_db_path)
    excl_store = ExcludedCompaniesStore(temp_db_path)
    importer = HistoricalImporter(store=store)

    # 1. Company is marked as Excluded in the Exclusion Registry
    excl_store.exclude_company(
        company_name="Suppressed Architettura S.r.l.",
        canonical_domain="suppressed.it",
        exclusion_reason="NOT_A_FIT_SERVICES",
        operator="operator",
        operator_note="Operator confirmed unsuitable",
    )

    # 2. An incoming historical Excel record attempts to import it as uncontacted ('بررسی اولیه')
    rec = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Veneto",
        row_number=1,
        company_name="Suppressed Architettura S.r.l.",
        domain="suppressed.it",
        tier_hint="A",
        notes="Unsuitable vendor",
        source_url=None,
        contact_name=None,
        contact_role=None,
        country="Italy",
        city="Padova",
        emails=["info@suppressed.it"],
        raw_outreach_status="بررسی اولیه",
    )
    importer.import_records([rec])

    # Invariant: cold_outreach_allowed must remain False due to Exclusion Registry / DNC
    cand = store.get_by_domain("suppressed.it")
    assert cand is not None
    assert cand.cold_outreach_allowed is False


# ---------------------------------------------------------------------------
# Test 7: Staged transaction rollback cleanly restores initial state on error
# ---------------------------------------------------------------------------
def test_07_staged_transaction_rollback_preserves_database_integrity(tmp_path, monkeypatch):
    # Place workbook inside data/staging to satisfy resolve_safe_import_path
    staging_dir = Path("data/staging").resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    wb_file = staging_dir / f"test_rollback_{os.getpid()}.xlsx"

    wb = openpyxl.Workbook()
    ws_veneto = wb.active
    ws_veneto.title = "Veneto"
    ws_veneto.append([
        "ردیف", "سطح شرکت A B C", "دلیل الویت", "نام و عنوان", "موقعیت جغرافیایی",
        "شماره‌های تماس", "موضوع کاری", "وب‌سایت رسمی", "شخص مسئول / رابط",
        "توضیحات و یادداشت‌ها", "لینک آگهی کار", "سایر لینک‌ها و راه‌های ارتباطی", "ایمیل‌های ارتباطی",
        "وضعیت ارتباط", "ارسال کننده", "تاریخ آخرین تغییر", "تاریخ ارسال ایمیل",
        "تاریخ پیگیری", "تاریخ پاسخدهی", "یادداشت‌های پیگیری"
    ])
    ws_veneto.append([
        "1.0", "A", "High priority", "Alpha BIM S.r.l.", "Padova, Italy",
        "+39 049 111111", "Architectural BIM", "https://www.alphabim.it/", "Mario Rossi",
        "Detailed company notes here", None, None, "info@alphabim.it",
        "ایمیل ارسال شد", "info@pybim.it", "2026-10-20", "2026-10-21 10:00:00",
        "2026-11-02", None, None
    ])
    wb.save(str(wb_file))

    temp_db_path = str(tmp_path / "test_hv02_sales.db")
    store = CandidateStore(temp_db_path)
    # Initialize schema tables
    HistoricalImporter(store=store)

    client = TestClient(app)
    monkeypatch.setattr("backend.sales_ui.router.get_console_services", lambda: {"cand_store": store})

    csrf_token = "valid_csrf_token_test_07"
    client.cookies.set("sales_csrf_token", csrf_token)

    try:
        # Get preview token
        prev_resp = client.post(
            "/sales/sources/import-history/preview",
            data={"file_path": str(wb_file), "csrf_token": csrf_token},
            headers={"Accept": "application/json"},
        )
        assert prev_resp.status_code == 200
        token = prev_resp.json()["preview_token"]

        # Initial snapshot of candidate_companies count
        with store._get_connection() as conn:
            init_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]

        # Inject an intentional error during confirm (e.g. failure during import_records)
        def broken_import(*args, **kwargs):
            raise RuntimeError("Simulated unrecoverable failure during import")

        monkeypatch.setattr(HistoricalImporter, "import_records", broken_import)

        confirm_resp = client.post(
            "/sales/sources/import-history/confirm",
            data={
                "file_path": str(wb_file),
                "preview_token": token,
                "csrf_token": csrf_token,
            },
        )
        assert confirm_resp.status_code == 500
        assert "Import failed during execution and was completely rolled back" in confirm_resp.json()["detail"]

        # Invariant: Database state must be identical to initial state
        with store._get_connection() as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
        assert after_count == init_count
    finally:
        if wb_file.exists():
            wb_file.unlink()


# ---------------------------------------------------------------------------
# Test 8: Zero authoritative mutation during preview simulation
# ---------------------------------------------------------------------------
def test_08_zero_authoritative_mutation_during_preview(tmp_path, monkeypatch):
    staging_dir = Path("data/staging").resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    wb_file = staging_dir / f"test_preview_zero_mut_{os.getpid()}.xlsx"

    wb = openpyxl.Workbook()
    ws_veneto = wb.active
    ws_veneto.title = "Veneto"
    ws_veneto.append([
        "ردیف", "سطح شرکت A B C", "دلیل الویت", "نام و عنوان", "موقعیت جغرافیایی",
        "شماره‌های تماس", "موضوع کاری", "وب‌سایت رسمی", "شخص مسئول / رابط",
        "توضیحات و یادداشت‌ها", "لینک آگهی کار", "سایر لینک‌ها و راه‌های ارتباطی", "ایمیل‌های ارتباطی",
        "وضعیت ارتباط", "ارسال کننده", "تاریخ آخرین تغییر", "تاریخ ارسال ایمیل",
        "تاریخ پیگیری", "تاریخ پاسخدهی", "یادداشت‌های پیگیری"
    ])
    ws_veneto.append([
        "1.0", "A", "High priority", "Alpha BIM S.r.l.", "Padova, Italy",
        "+39 049 111111", "Architectural BIM", "https://www.alphabim.it/", "Mario Rossi",
        "Detailed company notes here", None, None, "info@alphabim.it",
        "ایمیل ارسال شد", "info@pybim.it", "2026-10-20", "2026-10-21 10:00:00",
        "2026-11-02", None, None
    ])
    wb.save(str(wb_file))

    temp_db_path = str(tmp_path / "test_hv02_sales.db")
    store = CandidateStore(temp_db_path)
    HistoricalImporter(store=store)

    client = TestClient(app)
    monkeypatch.setattr("backend.sales_ui.router.get_console_services", lambda: {"cand_store": store})

    csrf_token = "valid_csrf_token_test_08"
    client.cookies.set("sales_csrf_token", csrf_token)

    try:
        with store._get_connection() as conn:
            c_count_before = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
            h_count_before = conn.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0]
            s_count_before = conn.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]

        resp = client.post(
            "/sales/sources/import-history/preview",
            data={"file_path": str(wb_file), "csrf_token": csrf_token},
        )
        assert resp.status_code == 200

        with store._get_connection() as conn:
            c_count_after = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
            h_count_after = conn.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0]
            s_count_after = conn.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]

        assert c_count_before == c_count_after == 0
        assert h_count_before == h_count_after == 0
        assert s_count_before == s_count_after == 0
    finally:
        if wb_file.exists():
            wb_file.unlink()


# ---------------------------------------------------------------------------
# Test 9: Complete validation of actual uploaded workbook on cloned database
# ---------------------------------------------------------------------------
def test_09_actual_uploaded_workbook_simulation_on_cloned_db(tmp_path):
    actual_workbook = Path("data/staging/upload_20261103_215831_Emails.xlsx")
    if not actual_workbook.exists():
        pytest.skip(f"Actual workbook {actual_workbook} not present in staging")

    # Clone authoritative database into temporary directory
    prod_db = Path("data/sales_outreach.db")
    test_db = tmp_path / "cloned_sales_outreach.db"

    src_conn = sqlite3.connect(prod_db)
    dst_conn = sqlite3.connect(test_db)
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    store = CandidateStore(str(test_db))
    importer = HistoricalImporter(store=store)

    # 1. Inspect workbook
    inspection = importer.inspect_workbook(actual_workbook)
    assert inspection["company_records_count"] == 284

    # 2. Parse and import Sent_Emails
    sent_msgs = importer.parse_sent_emails(actual_workbook)
    assert len(sent_msgs) == 295

    # 3. Parse and import Meeting activities
    meetings = importer.parse_meeting_activities(actual_workbook)
    assert len(meetings) == 5

    # 4. Parse company records
    comp_records = importer.parse_xlsx(actual_workbook)
    assert len(comp_records) == 284

    # Reconcile Sent Emails against staged records and store
    reconciled_sent = importer.reconcile_sent_emails(sent_msgs, staged_records=comp_records)
    matched_sent = [m for m in reconciled_sent if m.reconciliation_status == "matched"]
    unmatched_sent = [m for m in reconciled_sent if m.reconciliation_status == "unmatched"]
    ambiguous_sent = [m for m in reconciled_sent if m.reconciliation_status == "ambiguous"]

    assert len(matched_sent) == 279
    assert len(unmatched_sent) == 16
    assert len(ambiguous_sent) == 0

    # Execute import on cloned database
    with store._get_connection() as conn:
        # Load existing operator decisions
        dec_rows = conn.execute("SELECT * FROM operator_import_decisions").fetchall()
        op_decisions = [dict(r) for r in dec_rows]

    imp_result = importer.import_records(comp_records, operator_decisions=op_decisions)
    assert imp_result.records_parsed == 284
    # 283 existing + 1 new (ETC STUDIO S.R.L.)
    assert imp_result.companies_created >= 1

    sent_stats = importer.import_sent_emails(sent_msgs)
    assert sent_stats["imported"] == 295
    assert sent_stats["matched"] == 279
    assert sent_stats["unmatched"] == 16

    meet_stats = importer.import_meeting_activities(meetings, staged_records=comp_records)
    assert meet_stats["imported"] == 5
    assert meet_stats["matched"] == 5
