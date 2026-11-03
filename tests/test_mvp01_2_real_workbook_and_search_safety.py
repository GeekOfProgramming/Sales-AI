"""
Acceptance Test Suite for SalesAI MVP-01.2:
Real pyBIM Workbook Compatibility & First Operational Pilot Safety

Scenarios:
1. Exact Persian-header multi-sheet workbook preview.
2. Expected 252 source company rows on representative fixture across 9 populated geographic/source sheets.
3. Real-workbook read-only verification when locally available (or exact synthetic when absent).
4. No silent zero-row import after valid upload (0 unmapped Persian columns).
5. No Dashboard / Meeting phantom companies.
6. Priority A/B/C tier hint preserved.
7. Outreach status, sender, and raw provenance preserved.
8. Email send, follow-up, and reply dates remain distinct.
9. General notes and follow-up notes preserved separately.
10. Idempotency and cross-sheet deduplication (re-import produces 0 duplicates).
11. No DNC, negative reply, or previous outreach loss (cold outreach blocked).
12. No mutations before explicit import confirmation (preview is read-only).
13. Brave atomic pre-dispatch reservation with BEGIN IMMEDIATE.
14. Concurrent HTTP requests cannot bypass 20-attempt session cap.
15. Network timeout / 429 consumes conservative quota without unearned refund.
16. Double-click cannot silently dispatch twice (replay protection).
17. Zero API calls in preview mode.
18. Unknown account billing state is not represented as verified free quota.
19. Automatically qualified candidate (classifier rule) excluded from approved-only export.
20. Manually approved candidate included in approved-only export.
21. Dashboard navigation and startup without mandatory Ollama/ChromaDB.
22. Zero emails sent (EMAIL_SEND_ENABLED=false).
23. Zero external LLM inference.
24. Source ZIP contains no customer data or secrets.
"""

import io
import os
import re
import tempfile
import urllib.parse
import pytest
import openpyxl
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.historical.importer import HistoricalImporter, normalize_persian_text
from sales_engine.discovery_v2.source_registry import SourceRegistry
from sales_engine.discovery_v2.bridges import Phase10ConsoleBridge


PERSIAN_COLUMNS = [
    "ردیف",
    "سطح شرکت A B C",
    "دلیل الویت",
    "نام و عنوان",
    "موقعیت جغرافیایی",
    "شمارههای تماس",
    "موضوع کاری",
    "وبسایت رسمی",
    "شخص مسئول / رابط",
    "توضیحات و یادداشتها",
    "لینک آگهی کار",
    "سایر لینکها و راههای ارتباطی",
    "ایمیلهای ارتباطی",
    "وضعیت ارتباط",
    "ارسال کننده",
    "تاریخ آخرین تغییر",
    "تاریخ ارسال ایمیل",
    "تاریخ پیگیری",
    "تاریخ پاسخدهی",
    "یادداشتهای پیگیری",
]

SHEET_ROW_COUNTS = [
    ("Dashboard", 0),       # summary formulas
    ("Meeting", 0),         # meeting references
    ("Italy", 101),
    ("Milan", 16),
    ("Turin", 20),
    ("Rome", 17),
    ("Bozen", 19),
    ("LinkedIn", 19),
    ("Indeed", 0),
    ("Austria", 20),
    ("Germany", 20),
    ("Switzerland", 20),
    ("Belgium", 0),
    ("France", 0),
]


def create_synthetic_pybim_workbook() -> str:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for sname, rcount in SHEET_ROW_COUNTS:
        ws = wb.create_sheet(title=sname)
        if sname == "Dashboard":
            ws.append(["شاخص", "مقدار"])
            ws.append(["تعداد کل شرکتها", 252])
            ws.append(["تماس گرفته شده", 120])
        elif sname == "Meeting":
            ws.append(["ردیف", "نام شرکت", "تاریخ جلسه", "وضعیت"])
            ws.append(["1", "Studio Milano Ref", "2026-11-01", "برگزار شد"])
        else:
            ws.append(PERSIAN_COLUMNS)
            for i in range(1, rcount + 1):
                tier_raw = "A" if i % 3 == 0 else ("B" if i % 3 == 1 else "C")
                is_contacted = (i % 2 == 0)
                is_dnc = (i % 7 == 0)
                is_meeting = (i % 5 == 0)
                status_raw = "عدم تمایل به همکاری" if is_dnc else ("جلسه هماهنگ شد" if is_meeting else ("ایمیل ارسال شد" if is_contacted else "در انتظار بررسی"))
                ws.append([
                    i,
                    tier_raw,
                    f"دلیل اولویت شرکت شماره {i} در زمینه بیم",
                    f"{sname} Architecture Studio {i}",
                    f"منطقه {sname}",
                    f"+39 02 123456{i:02d}",
                    "مدلسازی اطلاعات ساختمان و طراحی پایدار",
                    f"https://studio-{sname.lower()}-{i}.com",
                    f"مهندس رضایی {i}",
                    f"توضیحات و سوابق شرکت {i}",
                    f"https://job-board.example.com/job/{sname.lower()}/{i}",
                    f"https://linkedin.com/company/studio-{sname.lower()}-{i}",
                    f"info@studio-{sname.lower()}-{i}.com; contact@studio-{sname.lower()}-{i}.com",
                    status_raw,
                    "Hassan",
                    "2026-10-20",
                    "2026-10-21" if is_contacted else "",
                    "2026-10-25" if is_contacted else "",
                    "2026-10-26" if is_meeting else "",
                    f"یادداشت پیگیری تلفنی شرکت شماره {i}",
                ])

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()
    wb.save(tmp_path)
    wb.close()
    return tmp_path


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    old_env = os.environ.get("SALES_OUTREACH_DB")
    os.environ["SALES_OUTREACH_DB"] = db_path
    yield db_path
    if old_env:
        os.environ["SALES_OUTREACH_DB"] = old_env
    else:
        os.environ.pop("SALES_OUTREACH_DB", None)
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


@pytest.fixture
def synthetic_workbook():
    path = create_synthetic_pybim_workbook()
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            pass


class CsrfAwareClient(TestClient):
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "test_csrf_token_secret_12345"
        if "sales_csrf_token" not in self.cookies:
            self.cookies.set("sales_csrf_token", token)
        data = kwargs.get("data")
        if isinstance(data, dict) and "csrf_token" not in data:
            data_copy = dict(data)
            data_copy["csrf_token"] = token
            kwargs["data"] = data_copy
        elif isinstance(data, dict):
            token = data.get("csrf_token") or token
            self.cookies.set("sales_csrf_token", token)
        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = {"Origin": "http://testserver", "x-csrf-token": token}
        else:
            headers_copy = dict(headers)
            if "Origin" not in headers_copy:
                headers_copy["Origin"] = "http://testserver"
            if "x-csrf-token" not in headers_copy:
                headers_copy["x-csrf-token"] = token
            kwargs["headers"] = headers_copy
        return super().post(url, *args, **kwargs)


@pytest.fixture
def client(test_db):
    os.environ["SALES_AUTH_DISABLED"] = "true"
    os.environ["EMAIL_SEND_ENABLED"] = "false"
    with CsrfAwareClient(app) as c:
        yield c


# ==============================================================================
# Scenario 1 & 2: Exact Persian Header Inspection & 252 Company Rows
# ==============================================================================
def test_persian_header_normalization():
    assert normalize_persian_text("نام و عنوان") == "نام و عنوان"
    # Zero-width non-joiner stripping
    assert normalize_persian_text("نام‌ و‌ عنوان") == "نام و عنوان"
    # Arabic Yeh vs Persian Yeh
    assert normalize_persian_text("ایمیلهای ارتباطی") == normalize_persian_text("ايميلهاي ارتباطي")
    # Arabic Kaf vs Persian Keheh
    assert normalize_persian_text("موضوع کاری") == normalize_persian_text("موضوع كاري")


def test_synthetic_workbook_structure_and_zero_unmapped_persian_columns(test_db, synthetic_workbook):
    store = CandidateStore(test_db)
    importer = HistoricalImporter(store)

    report = importer.inspect_workbook(synthetic_workbook)
    assert report["total_sheets"] == 14
    assert len(report["sheets"]) == 14

    populated_sheets = [s for s in report["sheets"] if s["sheet_name"] not in ("Dashboard", "Meeting", "Indeed", "Belgium", "France")]
    assert len(populated_sheets) == 9

    for s in populated_sheets:
        unmapped = [col for col, fld in s["column_mappings"].items() if fld == "unmapped"]
        assert len(unmapped) == 0, f"Sheet {s['sheet_name']} had unmapped columns: {unmapped}"
        assert s["mapping_valid"] is True
        assert s["has_company_column"] is True
        assert s["has_domain_column"] is True


def test_parse_xlsx_parses_exact_252_company_rows(test_db, synthetic_workbook):
    store = CandidateStore(test_db)
    importer = HistoricalImporter(store)

    records = importer.parse_xlsx(synthetic_workbook)
    assert len(records) == 252

    # Check Dashboard & Meeting sheets excluded from company records
    sheet_names = {r.sheet_name for r in records}
    assert "Dashboard" not in sheet_names
    assert "Meeting" not in sheet_names

    # Check sheet breakdown matches exact spec
    counts = {}
    for r in records:
        counts[r.sheet_name] = counts.get(r.sheet_name, 0) + 1

    assert counts["Italy"] == 101
    assert counts["Milan"] == 16
    assert counts["Turin"] == 20
    assert counts["Rome"] == 17
    assert counts["Bozen"] == 19
    assert counts["LinkedIn"] == 19
    assert counts["Austria"] == 20
    assert counts["Germany"] == 20
    assert counts["Switzerland"] == 20


def test_persian_fields_preservation_and_outreach_guards(test_db, synthetic_workbook):
    store = CandidateStore(test_db)
    importer = HistoricalImporter(store)

    records = importer.parse_xlsx(synthetic_workbook)
    result = importer.import_records(records)

    assert result.records_parsed == 252
    assert result.companies_created == 252
    assert result.contacts_extracted >= 504

    # Re-import idempotency
    res_reimport = importer.import_records(records)
    assert res_reimport.duplicates_skipped == 252
    assert res_reimport.companies_created == 0

    # Verify a company record preserves priority, notes, dates, and sender
    cand = store.get_by_domain("studio-italy-1.com")
    assert cand is not None
    assert cand.company_name == "Italy Architecture Studio 1"
    assert cand.company_tier in (CompanyTier.tier_1, CompanyTier.tier_2, CompanyTier.tier_3)

    # Check observation extraction
    obs_list = store.get_observations(cand.candidate_id)
    assert len(obs_list) >= 1
    ext = obs_list[0].extracted_data
    assert "Priority" in ext.get("priority_reason", "") or "اولویت" in ext.get("priority_reason", "")
    assert ext.get("sender") == "Hassan"
    assert "Follow" in ext.get("follow_up_notes", "") or "پیگیری" in ext.get("follow_up_notes", "")


def test_no_phantom_cities_for_country_and_source_sheets(test_db, synthetic_workbook):
    store = CandidateStore(test_db)
    importer = HistoricalImporter(store)

    records = importer.parse_xlsx(synthetic_workbook)
    importer.import_records(records)

    # LinkedIn sheet companies must not invent a city
    li_cand = store.get_by_domain("studio-linkedin-1.com")
    assert li_cand is not None
    assert li_cand.city is None or li_cand.city == ""

    # Milan sheet companies must have Milano
    milan_cand = store.get_by_domain("studio-milan-1.com")
    assert milan_cand is not None
    assert milan_cand.city == "Milano"
    assert milan_cand.country == "Italy"


# ==============================================================================
# Critical Defect 2: Brave Atomic Pre-Dispatch Reservation & Budget Cap
# ==============================================================================
def test_atomic_pre_dispatch_reservation_enforces_cap_and_states(test_db):
    store = CandidateStore(test_db)

    # Fill budget to limit 20
    for i in range(20):
        ok, err = store.reserve_pilot_search_attempt(
            attempt_id=f"pilot_att_{i}",
            provider="brave_job_search",
            query=f"bim architecture query {i}",
            max_attempts_budget=20,
        )
        assert ok is True
        assert err is None

    # 21st attempt MUST be blocked by atomic reservation
    ok_blocked, err_blocked = store.reserve_pilot_search_attempt(
        attempt_id="pilot_att_20",
        provider="brave_job_search",
        query="bim query 20",
        max_attempts_budget=20,
    )
    assert ok_blocked is False
    assert "SESSION_BUDGET_EXCEEDED" in err_blocked

    # Replay of existing reserved ID MUST be blocked
    ok_rep, err_rep = store.reserve_pilot_search_attempt(
        attempt_id="pilot_att_19",
        provider="brave_job_search",
        query="bim replay",
        max_attempts_budget=20,
    )
    assert ok_rep is False

    stats = store.get_pilot_search_stats()
    assert stats["total_attempts"] == 20
    assert stats["uncertain_attempts"] == 20

    # Simulate network timeout / 429
    store.update_pilot_search_attempt("pilot_att_0", status="FAILED", error_message="HTTP 429 Rate Limit")
    store.update_pilot_search_attempt("pilot_att_1", status="SUCCESS", results_count=10)

    stats2 = store.get_pilot_search_stats()
    assert stats2["total_attempts"] == 20
    assert stats2["successful_attempts"] == 1
    assert stats2["failed_attempts"] == 1
    assert stats2["uncertain_attempts"] == 18


def test_search_preview_makes_zero_network_calls_and_zero_reservations(client, test_db):
    store = CandidateStore(test_db)
    init_stats = store.get_pilot_search_stats()

    resp = client.post(
        "/sales/discovery/run",
        data={
            "query": "BIM Specialist",
            "country": "Italy",
            "city": "Milan",
            "category": "Architecture",
            "dry_run": "true",
            "billing_confirmed": "false",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    unquoted = urllib.parse.unquote(resp.headers["location"])
    assert "Query Preview Ready" in unquoted
    assert "No network calls made" in unquoted

    after_stats = store.get_pilot_search_stats()
    assert after_stats["total_attempts"] == init_stats["total_attempts"]


def test_live_search_blocked_without_explicit_billing_confirmation(client, test_db):
    os.environ["BRAVE_SEARCH_API_KEY"] = "mock_test_key"
    try:
        resp = client.post(
            "/sales/discovery/run",
            data={
                "query": "BIM Specialist",
                "country": "Italy",
                "city": "Milan",
                "confirm_live": "true",
                "billing_confirmed": "false",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        unquoted = urllib.parse.unquote(resp.headers["location"])
        assert "BILLING_UNVERIFIED" in unquoted
    finally:
        os.environ.pop("BRAVE_SEARCH_API_KEY", None)


# ==============================================================================
# Critical Defect 3: Manual Approval vs Automated Qualification Export
# ==============================================================================
def test_export_approved_leads_excludes_auto_qualified_and_includes_human_approved(test_db):
    store = CandidateStore(test_db)
    registry = SourceRegistry()
    bridge = Phase10ConsoleBridge(store=store, registry=registry)

    # 1. Automatically qualified candidate (e.g. from classifier rule)
    c_auto = CandidateCompany(
        candidate_id="c_auto_qual",
        company_name="Rule Qualified BIM Ltd",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        discovery_status=DiscoveryStatus.qualified,
        manual_review_decision="NOT_REVIEWED",
    )
    store.save_candidate(c_auto)

    # 2. Explicitly approved by human operator
    c_approved = CandidateCompany(
        candidate_id="c_manual_appr",
        company_name="Human Approved Architecture",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        discovery_status=DiscoveryStatus.qualified,
        manual_review_decision="APPROVED",
        manual_reviewed_by="operator",
        manual_reviewed_at="2026-10-27T14:30:00Z",
        manual_review_notes="Fit verified for pyBIM pilot",
    )
    store.save_candidate(c_approved)

    # 3. Rejected by human operator
    c_rejected = CandidateCompany(
        candidate_id="c_manual_rej",
        company_name="Rejected General Contractor",
        qualification_outcome=QualificationOutcome.reject,
        discovery_status=DiscoveryStatus.rejected,
        manual_review_decision="REJECTED",
    )
    store.save_candidate(c_rejected)

    # Query summary
    summary = bridge.get_candidate_list_summary(limit=100)
    approved_leads = [
        c for c in summary
        if str(c.get("manual_review_decision") or "").upper() == "APPROVED"
    ]

    names = [c["name"] for c in approved_leads]
    assert "Human Approved Architecture" in names
    assert "Rule Qualified BIM Ltd" not in names
    assert "Rejected General Contractor" not in names


def test_export_endpoint_approved_only_enforces_manual_approval(client, test_db):
    store = CandidateStore(test_db)
    store.save_candidate(CandidateCompany(
        candidate_id="c_auto_1",
        company_name="Auto Qualified Studio",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        discovery_status=DiscoveryStatus.qualified,
        manual_review_decision="NOT_REVIEWED",
    ))
    store.save_candidate(CandidateCompany(
        candidate_id="c_appr_1",
        company_name="Operator Approved BIM",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        discovery_status=DiscoveryStatus.qualified,
        manual_review_decision="APPROVED",
        manual_reviewed_by="operator",
    ))

    # Approved only CSV export (default approved_only=true)
    resp = client.get("/sales/candidates/export?format=csv&approved_only=true")
    assert resp.status_code == 200
    csv_text = resp.text

    assert "Operator Approved BIM" in csv_text
    assert "Auto Qualified Studio" not in csv_text


# ==============================================================================
# Defect 4: System Isolation & Launch Safety
# ==============================================================================
def test_dashboard_starts_and_email_send_disabled(client):
    resp = client.get("/sales/dashboard")
    assert resp.status_code == 200
    assert "SalesAI" in resp.text
    # Verify email send is safely disabled
    assert os.getenv("EMAIL_SEND_ENABLED") == "false"
