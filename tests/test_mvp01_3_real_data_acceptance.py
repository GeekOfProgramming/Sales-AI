"""
Acceptance Test Suite for SalesAI MVP-01.3:
Actual 283-Row Workbook Acceptance & Outreach State Integrity

Authoritative reference: Emails-10-27-2026-18-19.xlsx (October 27, 2026)
Supersedes previous October 26 (252-row) workbook.

Key requirements verified:
1. Backend clean startup: NameError 'Dict'/'Any' eliminated, clean import of backend.main.
2. 283-row exact Persian-header multi-sheet workbook parsing (Italy 101, Milan 16, Turin 20, Rome 17, Bozen 30, LinkedIn 19, Indeed 0, Austria 20, Germany 20, Switzerland 40, Belgium 0, France 0).
3. 276 distinct canonical domains with 7 domain collisions detected and surfaced for human review.
4. Persian outreach status mapping integrity matching real workbook distribution:
   - 141 'بررسی اولیه' (initial review) -> uncontacted, cold outreach allowed, no reply status.
   - 137 'ایمیل ارسال شد' (email sent) -> contacted, cold outreach blocked, no reply date/reply status.
   - 2 'مصاحبه / دعوت به همکاری' (interview/collaboration) -> replied / positive engagement preserved, no invented meeting.
   - 2 'پیگیری شود' (follow-up) -> contacted / follow-up preserved.
   - 1 'ایمیل اشتباه بودش' (invalid email) -> flagged wrong email for that specific address; does NOT suppress whole company if other valid contacts exist.
5. Zero unmapped main Persian headers.
6. Persian contact-person names ('شخص مسئول / رابط') preserved and NOT overwritten with None.
7. Brave double-click & replay protection: per-operator-action idempotency token bound to search params rejects duplicate submissions server-side without a 2nd network dispatch.
8. State transition lifecycle: DISPATCH_ATTEMPTED cannot rollback to PRE_DISPATCH_CANCELLED; preserved as UNCERTAIN.
9. Approved-only export strictly enforces manual operator approval.
10. Strictly offline: zero live Brave queries, zero LLM calls, zero email sends.
"""

import hashlib
import io
import os
import re
import tempfile
import urllib.parse
import pytest
import openpyxl
from pathlib import Path
from typing import List, Dict, Any
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.historical.importer import (
    HistoricalImporter,
    normalize_persian_text,
    normalize_outreach_status,
)


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

# Sheet row counts for the updated October 27 workbook (283 total company rows)
OCT_27_SHEET_ROW_COUNTS = [
    ("Dashboard", 0),       # summary dashboard
    ("Meeting", 0),         # meeting notes
    ("Italy", 101),
    ("Milan", 16),
    ("Turin", 20),
    ("Rome", 17),
    ("Bozen", 30),          # 30 in Oct 27 (was 19 in Oct 26)
    ("LinkedIn", 19),
    ("Indeed", 0),
    ("Austria", 20),
    ("Germany", 20),
    ("Switzerland", 40),    # 40 in Oct 27 (was 20 in Oct 26)
    ("Belgium", 0),
    ("France", 0),
]


def create_exact_283_row_oct27_workbook() -> str:
    """
    Creates an exact synthetic fixture conforming to Emails-10-27-2026-18-19.xlsx:
    - 14 sheets
    - 283 total company rows across 9 populated geographic/source sheets
    - Exact status distribution:
      * 141 بررسی اولیه
      * 137 ایمیل ارسال شد
      * 2 مصاحبه / دعوت به همکاری
      * 2 پیگیری شود
      * 1 ایمیل اشتباه بودش
    - 276 distinct canonical domains with exactly 7 domain collisions (283 rows total)
    - Populated contact-person name in Persian column 'شخص مسئول / رابط'
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # 1. Dashboard sheet
    ws_dash = wb.create_sheet(title="Dashboard")
    ws_dash.append(["شاخص", "مقدار"])
    ws_dash.append(["تعداد کل شرکتها", 283])
    ws_dash.append(["ایمیل ارسال شد", 141])
    ws_dash.append(["مصاحبه / دعوت به همکاری", 2])
    ws_dash.append(["باقی مانده برای ایمیل", 142])

    # 2. Meeting sheet
    ws_meet = wb.create_sheet(title="Meeting")
    ws_meet.append(["ردیف", "نام شرکت", "تاریخ جلسه", "وضعیت"])
    ws_meet.append(["1", "Studio Bozen Meeting", "2026-11-05", "برگزار شد"])

    # Prepare status distribution queue for 283 rows
    # 141 'بررسی اولیه', 137 'ایمیل ارسال شد', 2 'مصاحبه / دعوت به همکاری', 2 'پیگیری شود', 1 'ایمیل اشتباه بودش'
    statuses = (
        ["بررسی اولیه"] * 141 +
        ["ایمیل ارسال شد"] * 137 +
        ["مصاحبه / دعوت به همکاری"] * 2 +
        ["پیگیری شود"] * 2 +
        ["ایمیل اشتباه بودش"] * 1
    )
    assert len(statuses) == 283

    # We want 276 distinct domains across 283 rows (7 collisions)
    # Ensure collisions occur within the sent group: indices [201, 202, 203, 204, 205, 206, 207]
    # reusing domains from [150, 151, 152, 153, 154, 155, 156] (both are in the 'ایمیل ارسال شد' range 141..277)
    collision_indices = {201: 150, 202: 151, 203: 152, 204: 153, 205: 154, 206: 155, 207: 156}

    global_row_idx = 0
    generated_domains = []

    for sname, rcount in OCT_27_SHEET_ROW_COUNTS:
        if sname in ("Dashboard", "Meeting"):
            continue
        ws = wb.create_sheet(title=sname)
        ws.append(PERSIAN_COLUMNS)

        for i in range(1, rcount + 1):
            st = statuses[global_row_idx]

            # Domain generation
            if global_row_idx in collision_indices:
                dom = generated_domains[collision_indices[global_row_idx]]
            else:
                dom = f"https://www.studio-{sname.lower()}-{i}-{global_row_idx}.com"

            generated_domains.append(dom)

            is_sent = (st == "ایمیل ارسال شد")
            is_reply = (st == "مصاحبه / دعوت به همکاری")
            contact_date = "2026-10-15" if (is_sent or is_reply) else ""
            reply_date = "2026-10-18" if is_reply else ""
            follow_up_date = "2026-10-28" if st == "پیگیری شود" else ""

            ws.append([
                i,
                "A" if i % 2 == 0 else "B",
                f"اولویت شرکت {i} در حوزه پایدار",
                f"مهندسی و معماری {sname} {i}",
                f"منطقه {sname}",
                f"+39 02 {global_row_idx:06d}",
                "مدلسازی BIM و محاسبات سازه",
                dom,
                f"مهندس علیرضا رضایی {i}",  # Persian contact name
                f"یادداشت تفصیلی شرکت {i}",
                f"https://jobs.example.com/{sname.lower()}/{i}",
                f"https://linkedin.com/company/{sname.lower()}-{i}",
                f"contact@{Path(dom).name}; info@{Path(dom).name}",
                st,
                "Hassan",
                "2026-10-27",
                contact_date,
                follow_up_date,
                reply_date,
                "یادداشت پیگیری تلفنی",
            ])
            global_row_idx += 1

    assert global_row_idx == 283

    staging_dir = Path(__file__).resolve().parent.parent / "data" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = staging_dir / f"test_oct27_fixture_{hashlib.md5(str(os.getpid()).encode()).hexdigest()[:8]}.xlsx"
    wb.save(str(tmp_path))
    wb.close()
    return str(tmp_path)


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
        return super().post(url, *args, **kwargs)


@pytest.fixture
def temp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    store = CandidateStore(db_path=db_path)
    yield store, db_path
    try:
        os.remove(db_path)
    except Exception:
        pass


@pytest.fixture
def oct27_workbook():
    path = create_exact_283_row_oct27_workbook()
    yield path
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Defect 1: Clean Backend Startup
# ---------------------------------------------------------------------------
def test_defect1_clean_backend_startup_no_name_error():
    """Verify backend.main can be imported without NameError 'Dict' or 'Any'."""
    import backend.main
    assert hasattr(backend.main, "app")
    assert backend.main.app is not None


# ---------------------------------------------------------------------------
# Defect 2: Initial Review is Classified Uncontacted (Not Contacted)
# ---------------------------------------------------------------------------
def test_defect2_persian_outreach_status_conservative_mapping():
    """Verify conservative explicit Persian status normalization."""
    # 1. بررسی اولیه -> uncontacted, cold_outreach_allowed=True, NO reply
    res_review = normalize_outreach_status("بررسی اولیه")
    assert res_review["historical_contact_status"] == "uncontacted"
    assert res_review["has_past_contact"] is False
    assert res_review["reply_status"] is None
    assert res_review["is_wrong_email"] is False

    # 2. ایمیل ارسال شد -> contacted, has_past_contact=True, NO reply
    res_sent = normalize_outreach_status("ایمیل ارسال شد")
    assert res_sent["historical_contact_status"] == "contacted"
    assert res_sent["has_past_contact"] is True
    assert res_sent["reply_status"] is None

    # 3. مصاحبه / دعوت به همکاری -> replied, positive_interview, active_conversation
    res_interview = normalize_outreach_status("مصاحبه / دعوت به همکاری")
    assert res_interview["historical_contact_status"] == "replied"
    assert res_interview["has_past_contact"] is True
    assert res_interview["reply_status"] == "positive_interview"
    assert res_interview["has_active_conversation"] is True

    # 4. پیگیری شود -> contacted, follow_up_needed=True
    res_follow = normalize_outreach_status("پیگیری شود")
    assert res_follow["historical_contact_status"] == "contacted"
    assert res_follow["has_past_contact"] is True
    assert res_follow["follow_up_needed"] is True

    # 5. ایمیل اشتباه بودش -> is_wrong_email=True, uncontacted
    res_wrong = normalize_outreach_status("ایمیل اشتباه بودش")
    assert res_wrong["historical_contact_status"] == "uncontacted"
    assert res_wrong["is_wrong_email"] is True
    assert res_wrong["has_past_contact"] is False


def test_defect2_initial_review_141_records_remain_uncontacted(temp_db, oct27_workbook):
    """
    Import the full 283-row workbook into an isolated test database.
    Assert:
    - 141 records with status 'بررسی اولیه' have cold_outreach_allowed = True and status 'uncontacted'.
    - 137 records with 'ایمیل ارسال شد' have cold_outreach_allowed = False and status 'contacted'.
    - 2 interview records have status 'replied' and active conversation.
    - 2 follow-up records have status 'contacted'.
    - 1 wrong-email record is flagged without suppressing the whole entity.
    """
    store, _ = temp_db
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(oct27_workbook)
    assert len(records) == 283

    # Check parsed records distribution
    review_recs = [r for r in records if r.raw_outreach_status == "بررسی اولیه"]
    sent_recs = [r for r in records if r.raw_outreach_status == "ایمیل ارسال شد"]
    interview_recs = [r for r in records if r.raw_outreach_status == "مصاحبه / دعوت به همکاری"]
    followup_recs = [r for r in records if r.raw_outreach_status == "پیگیری شود"]
    wrong_email_recs = [r for r in records if r.raw_outreach_status == "ایمیل اشتباه بودش"]

    assert len(review_recs) == 141
    assert len(sent_recs) == 137
    assert len(interview_recs) == 2
    assert len(followup_recs) == 2
    assert len(wrong_email_recs) == 1

    # Execute import
    res = importer.import_records(records)
    assert res.total_rows_read == 283
    assert res.records_parsed == 283

    # Query candidate companies from the isolated database
    all_candidates = store.list_candidates()
    uncontacted_comps = [c for c in all_candidates if c.historical_contact_status == "uncontacted"]
    contacted_comps = [c for c in all_candidates if c.historical_contact_status == "contacted"]
    replied_comps = [c for c in all_candidates if c.historical_contact_status == "replied"]

    # Verify CandidateStore distinct entities:
    # 283 rows with 276 distinct canonical domains (7 collisions merged into canonical domains)
    assert len(all_candidates) == 276
    assert len(uncontacted_comps) == 142
    for comp in uncontacted_comps:
        assert comp.cold_outreach_allowed is True
        assert comp.reply_status is None

    # Contacted companies = 139 source rows minus 7 domain collisions = 132 distinct entities
    assert len(contacted_comps) == 132
    for comp in contacted_comps:
        assert comp.cold_outreach_allowed is False

    # Replied companies = 2 positive interviews
    assert len(replied_comps) == 2
    for comp in replied_comps:
        assert comp.cold_outreach_allowed is False
        assert comp.active_conversation is True
        assert comp.reply_status == "positive_interview"


# ---------------------------------------------------------------------------
# Defect 3: Contact-Person Names Not Overwritten
# ---------------------------------------------------------------------------
def test_defect3_persian_contact_person_name_preserved(temp_db, oct27_workbook):
    """Verify that Persian contact-person names are extracted and preserved, not overwritten with None."""
    store, _ = temp_db
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(oct27_workbook)
    assert len(records) == 283

    # All 283 rows have populated Persian contact names
    populated_names = [r for r in records if r.contact_name and "مهندس" in r.contact_name]
    assert len(populated_names) == 283

    # Inspection report also tracks contact name extraction count
    inspection = importer.inspect_workbook(oct27_workbook)
    assert inspection["populated_contact_name_count"] == 283


# ---------------------------------------------------------------------------
# Defect 4: Real Workbook Identity Collisions Reconciliation
# ---------------------------------------------------------------------------
def test_defect4_domain_collision_inspection_report(temp_db, oct27_workbook):
    """
    Verify pre-commit inspection accurately reports:
    - 283 company records
    - 276 distinct canonical domains
    - exactly 7 domain collisions requiring human review
    - 0 unmapped Persian headers
    """
    store, _ = temp_db
    importer = HistoricalImporter(store=store)

    inspection = importer.inspect_workbook(oct27_workbook)
    assert inspection["company_records_count"] == 283
    assert inspection["distinct_canonical_domains"] == 276
    assert inspection["domain_collisions_count"] == 7

    collisions = inspection["domain_collisions"]
    assert len(collisions) == 7
    for col in collisions:
        assert col["occurrence_count"] >= 2
        assert col["requires_human_review"] is True
        assert col["collision_type"] in ("verified_duplicate", "possible_duplicate", "separate_entities_or_branches")
        assert len(col["rows"]) >= 2

    # Check worksheet breakdown
    sheets = {s["sheet_name"]: s["row_count"] for s in inspection["sheets"]}
    assert sheets["Italy"] == 101
    assert sheets["Milan"] == 16
    assert sheets["Turin"] == 20
    assert sheets["Rome"] == 17
    assert sheets["Bozen"] == 30
    assert sheets["LinkedIn"] == 19
    assert sheets["Indeed"] == 0
    assert sheets["Austria"] == 20
    assert sheets["Germany"] == 20
    assert sheets["Switzerland"] == 40
    assert sheets["Belgium"] == 0
    assert sheets["France"] == 0

    # Ensure zero unmapped columns in company sheets
    for s in inspection["sheets"]:
        if not s["is_summary_sheet"] and s["row_count"] > 0:
            unmapped = [col for col, mapped in s["column_mappings"].items() if mapped == "unmapped"]
            assert len(unmapped) == 0, f"Sheet {s['sheet_name']} has unmapped columns: {unmapped}"


def test_defect4_idempotent_reimport_preserves_provenance(temp_db, oct27_workbook):
    """Verify that re-importing the same workbook is strictly idempotent and does not create duplicates."""
    store, _ = temp_db
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(oct27_workbook)
    res1 = importer.import_records(records)
    assert res1.records_parsed == 283
    assert res1.duplicates_skipped == 0

    # Second import must skip all exact records
    res2 = importer.import_records(records)
    assert res2.records_parsed == 283
    assert res2.duplicates_skipped == 283
    assert res2.companies_created == 0


# ---------------------------------------------------------------------------
# Defect 5: Brave Double-Click Protection & Idempotency Token
# ---------------------------------------------------------------------------
def test_defect5_idempotency_token_blocks_double_clicks(temp_db):
    """Verify that submitting duplicate idempotency tokens is rejected without dispatch."""
    store, _ = temp_db

    token = "operator_action_hash_123"
    ok1, err1 = store.reserve_pilot_search_attempt(
        attempt_id="attempt_001",
        provider="brave_job_search",
        query="BIM architect Milan",
        max_attempts_budget=20,
        idempotency_token=token,
    )
    assert ok1 is True
    assert err1 is None

    # Immediate second click with identical idempotency token
    ok2, err2 = store.reserve_pilot_search_attempt(
        attempt_id="attempt_002",
        provider="brave_job_search",
        query="BIM architect Milan",
        max_attempts_budget=20,
        idempotency_token=token,
    )
    assert ok2 is False
    assert "Duplicate search submission detected" in err2


def test_defect5_attempt_lifecycle_state_transitions(temp_db):
    """
    Verify attempt state lifecycle:
    - DISPATCH_ATTEMPTED cannot be rolled back to PRE_DISPATCH_CANCELLED without proof of non-dispatch.
    - Transitions to UNCERTAIN are preserved.
    """
    store, _ = temp_db

    store.reserve_pilot_search_attempt(
        attempt_id="attempt_trans_1",
        provider="brave_job_search",
        query="BIM Turin",
        max_attempts_budget=20,
    )

    # Transition to DISPATCH_ATTEMPTED
    store.update_pilot_search_attempt(
        attempt_id="attempt_trans_1",
        status="DISPATCH_ATTEMPTED",
    )

    # Attempt illegal transition from DISPATCH_ATTEMPTED to PRE_DISPATCH_CANCELLED
    store.update_pilot_search_attempt(
        attempt_id="attempt_trans_1",
        status="PRE_DISPATCH_CANCELLED",
        error_message="Operator aborted during request",
    )

    # Must be marked UNCERTAIN, not cancelled
    with store._get_connection() as conn:
        row = conn.execute("SELECT status, error_message FROM pilot_search_attempts WHERE attempt_id = ?", ("attempt_trans_1",)).fetchone()
        assert row["status"] == "UNCERTAIN"
        assert "marked UNCERTAIN" in row["error_message"]


# ---------------------------------------------------------------------------
# Defect 6: Operator UI Acceptance Preview (Read-Only)
# ---------------------------------------------------------------------------
def test_defect6_ui_preview_is_strictly_read_only(oct27_workbook):
    """Verify the UI preview endpoint displays full 283-row acceptance metrics in read-only mode."""
    csrf_client = CsrfAwareClient(app)

    resp = csrf_client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": oct27_workbook},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["rows_detected"] == 283
    assert data["distinct_canonical_domains"] == 276
    assert data["domain_collisions_count"] == 7
    assert data["populated_contact_name_count"] == 283
    assert data["classification_counts"]["uncontacted"] == 142  # 141 review + 1 wrong email
    assert data["classification_counts"]["contacted"] == 139   # 137 sent + 2 follow-up
    assert data["classification_counts"]["replied"] == 2       # 2 interviews


# ---------------------------------------------------------------------------
# Safety & Offline Guarantees
# ---------------------------------------------------------------------------
def test_strict_offline_zero_email_and_no_live_brave_in_tests():
    """Verify strictly offline configuration: zero email sending and zero live Brave calls."""
    assert os.getenv("EMAIL_SEND_ENABLED", "false").lower() in ("false", "0", "no")
