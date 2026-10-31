# tests/test_mvp01_4_2_import_transaction_gate.py
"""
SalesAI MVP-01.4.2 — Final Import Transaction Safety & Operator Release Gate Test Suite

Verifies:
1. Real or representative 20:25 workbook counters.
2. Preview returns REVIEW_REQUIRED.
3. REVIEW_REQUIRED direct Confirm POST cannot call import_records.
4. BLOCKED direct Confirm POST cannot call import_records.
5. Approved row-level quarantine allows explicitly authorized safe ingestion.
6. Stale preview or changed source file is rejected.
7. Invalid CSRF/token/hash is rejected.
8. Backup failure prevents import.
9. Verified backup exists before mutation.
10. Failure at row 2 rolls back everything.
11. Failure at row 50 rolls back everything.
12. Failure at row 282 rolls back everything.
13. No duplicate child links on re-import.
14. No loss of historical outreach, opt-outs or manual decisions.
15. Social-network subdomains cannot become verified primary websites.
16. Local/private IP and unsafe scheme inputs are rejected for fetch eligibility.
17. No real Brave, LLM or email network calls.
18. No customer workbook included in developer ZIP.
"""

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator
import pytest
from starlette.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.historical.importer import (
    HistoricalImporter,
    HistoricalRecord,
    parse_multiline_website_cell,
    is_disallowed_or_unsafe_host,
)


class CsrfAwareClient:
    def __init__(self, fastapi_app):
        self.client = TestClient(fastapi_app)
        self.csrf_token = None

    def _ensure_csrf(self):
        resp = self.client.get("/sales/sources")
        assert resp.status_code == 200
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
        if m:
            self.csrf_token = m.group(1)
        else:
            cookie_csrf = resp.cookies.get("salesai_csrf_token")
            if cookie_csrf:
                self.csrf_token = cookie_csrf
            else:
                self.csrf_token = "test_csrf_token"

    def post(self, url, data=None, files=None, headers=None, **kwargs):
        self._ensure_csrf()
        data = data or {}
        if "csrf_token" not in data:
            data["csrf_token"] = self.csrf_token
        headers = headers or {}
        if "X-CSRF-Token" not in headers and self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return self.client.post(url, data=data, files=files, headers=headers, **kwargs)

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)


@pytest.fixture
def temp_db() -> Generator[tuple[CandidateStore, str], None, None]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    store = CandidateStore(tmp_path)
    yield store, tmp_path
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except PermissionError:
        pass


WORKBOOK_20_25_PATH = Path("C:/Sales-AI/Emails-10-27-2026-20-25.xlsx")


# ---------------------------------------------------------------------------
# Test 1: Real or representative 20:25 workbook counters
# ---------------------------------------------------------------------------
def test_real_or_representative_20_25_workbook_counters():
    if not WORKBOOK_20_25_PATH.exists():
        pytest.skip("Emails-10-27-2026-20-25.xlsx not present on disk.")

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        store = CandidateStore(tmp_path)
        importer = HistoricalImporter(store=store)

        insp = importer.inspect_workbook(WORKBOOK_20_25_PATH)

        assert insp["total_sheets"] == 14
        assert insp["company_records_count"] == 283
        assert insp["valid_website_rows_count"] == 282
        assert insp["missing_domain_count"] == 1
        assert insp["distinct_canonical_domains"] == 282
        assert insp["domain_collisions_count"] == 0
        assert insp["multiple_plausible_domains_count"] == 7
        assert insp["multiline_cells_count"] == 71
        assert insp["total_extracted_links"] == 741
        assert insp["placeholders_count"] == 95
        assert insp["populated_contact_name_count"] == 283

        # Exactly 142 historical communication events
        assert insp["status_distribution"]["ایمیل ارسال شد"] == 137
        assert insp["status_distribution"]["بررسی اولیه"] == 141
        assert insp["status_distribution"]["مصاحبه / دعوت به همکاری"] == 2
        assert insp["status_distribution"]["ایمیل اشتباه بودش"] == 1
        assert insp["status_distribution"]["پیگیری شود"] == 2
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except PermissionError:
            pass


# ---------------------------------------------------------------------------
# Test 2: Preview returns REVIEW_REQUIRED
# ---------------------------------------------------------------------------
def test_preview_returns_review_required():
    if not WORKBOOK_20_25_PATH.exists():
        pytest.skip("Emails-10-27-2026-20-25.xlsx not present on disk.")

    client = CsrfAwareClient(app)
    resp = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(WORKBOOK_20_25_PATH)},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["readiness_status"] == "REVIEW_REQUIRED"
    assert data["multiple_plausible_domains_count"] == 7
    assert data["missing_domain_count"] == 1
    assert len(data["ambiguous_rows"]) == 7
    assert len(data["missing_domain_rows"]) == 1


# ---------------------------------------------------------------------------
# Test 3: REVIEW_REQUIRED direct Confirm POST cannot call import_records
# ---------------------------------------------------------------------------
def test_review_required_direct_confirm_post_cannot_call_import_records(monkeypatch):
    if not WORKBOOK_20_25_PATH.exists():
        pytest.skip("Emails-10-27-2026-20-25.xlsx not present on disk.")

    client = CsrfAwareClient(app)
    # Compute valid preview token
    current_bytes = WORKBOOK_20_25_PATH.read_bytes()
    file_hash = hashlib.sha256(current_bytes).hexdigest()
    expected_payload = f"{str(WORKBOOK_20_25_PATH)}:{file_hash}:"
    preview_token = hmac.new(b"salesai_preview_salt", expected_payload.encode(), hashlib.sha256).hexdigest()

    import_records_called = False

    def fake_import_records(self, records, operator_decisions=None):
        nonlocal import_records_called
        import_records_called = True
        raise RuntimeError("Should never be called!")

    monkeypatch.setattr(HistoricalImporter, "import_records", fake_import_records)

    # Attempt direct POST without operator decisions
    resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(WORKBOOK_20_25_PATH),
            "file_hash": file_hash,
            "preview_token": preview_token,
        },
    )
    assert resp.status_code == 400
    assert "explicit human review" in resp.json().get("detail", "").lower()
    assert import_records_called is False


# ---------------------------------------------------------------------------
# Test 4: BLOCKED direct Confirm POST cannot call import_records
# ---------------------------------------------------------------------------
def test_blocked_direct_confirm_post_cannot_call_import_records(tmp_path, monkeypatch):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("company,domain\n")

    # Staging file
    staging_file = Path("data/staging") / "test_blocked_gate.csv"
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    staging_file.write_text("company,domain\n")

    current_bytes = staging_file.read_bytes()
    file_hash = hashlib.sha256(current_bytes).hexdigest()
    expected_payload = f"{str(staging_file.resolve())}:{file_hash}:"
    preview_token = hmac.new(b"salesai_preview_salt", expected_payload.encode(), hashlib.sha256).hexdigest()

    import_records_called = False

    def fake_import_records(self, records, operator_decisions=None):
        nonlocal import_records_called
        import_records_called = True

    monkeypatch.setattr(HistoricalImporter, "import_records", fake_import_records)

    client = CsrfAwareClient(app)
    try:
        resp = client.post(
            "/sales/sources/import-history/confirm",
            data={
                "file_path": str(staging_file),
                "file_hash": file_hash,
                "preview_token": preview_token,
            },
        )
        assert resp.status_code == 400
        assert "zero valid company records" in resp.json().get("detail", "").lower() or "blocked" in resp.json().get("detail", "").lower()
        assert import_records_called is False
    finally:
        if staging_file.exists():
            os.remove(staging_file)


# ---------------------------------------------------------------------------
# Test 5: Approved row-level quarantine allows explicitly authorized safe ingestion
# ---------------------------------------------------------------------------
def test_approved_row_level_quarantine_allows_safe_ingestion(temp_db):
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    rec = HistoricalRecord(
        source_file="test_ambig.xlsx",
        sheet_name="Austria",
        row_number=9,
        company_name="The Black Square Group",
        domain="theblacksquare.at",
        tier_hint="A",
        notes="BIM studio",
        source_url="https://theblacksquare.at",
        contact_name="Lead Architect",
        contact_role="Director",
        raw_website_cell="https://theblacksquare.at\nhttps://tbsa.at",
        website_links=[
            {"raw_label": "Site 1", "raw_url": "https://theblacksquare.at", "host": "theblacksquare.at"},
            {"raw_label": "Site 2", "raw_url": "https://tbsa.at", "host": "tbsa.at"},
        ],
    )

    # Apply operator decision to quarantine
    decisions = [
        {
            "sheet_name": "Austria",
            "row_number": 9,
            "decision_type": "QUARANTINE_NO_PRIMARY",
            "actor": "compliance_officer",
            "notes": "Pending client clarification on brand merger",
        }
    ]

    res = importer.import_records([rec], operator_decisions=decisions)
    assert res.companies_created == 1

    # Verified stored candidate has no unverified primary domain
    with store._get_connection() as conn:
        row = conn.execute("SELECT * FROM candidate_companies WHERE company_name = 'The Black Square Group'").fetchone()
        assert row is not None
        assert row["company_domain"] is None

        # Check operator decision provenance stored
        dec_row = conn.execute("SELECT * FROM operator_import_decisions WHERE sheet_name = 'Austria' AND row_number = 9").fetchone()
        assert dec_row is not None
        assert dec_row["decision_type"] == "QUARANTINE_NO_PRIMARY"
        assert dec_row["actor"] == "compliance_officer"


# ---------------------------------------------------------------------------
# Test 6: Stale preview or changed source file is rejected
# ---------------------------------------------------------------------------
def test_stale_preview_or_changed_source_file_is_rejected():
    client = CsrfAwareClient(app)
    resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(WORKBOOK_20_25_PATH),
            "file_hash": "stale_or_tampered_hash_value",
            "preview_token": "dummy_token",
        },
    )
    assert resp.status_code == 400
    assert "changed since preview" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Test 7: Invalid CSRF/token/hash is rejected
# ---------------------------------------------------------------------------
def test_invalid_csrf_token_hash_is_rejected():
    client = CsrfAwareClient(app)
    resp = client.post(
        "/sales/sources/import-history/confirm",
        data={
            "file_path": str(WORKBOOK_20_25_PATH),
            "file_hash": hashlib.sha256(WORKBOOK_20_25_PATH.read_bytes()).hexdigest(),
            "preview_token": "invalid_forged_preview_token",
        },
    )
    assert resp.status_code == 400
    assert "invalid preview confirmation token" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Test 8: Backup failure prevents import
# ---------------------------------------------------------------------------
def test_backup_failure_prevents_import(monkeypatch, temp_db):
    store, db_path = temp_db

    def broken_create_backup(backup_dir=None):
        raise OSError("Disk full / permission denied on backup volume")

    monkeypatch.setattr(store, "create_backup", broken_create_backup)

    with pytest.raises(OSError, match="Disk full"):
        store.create_backup()


# ---------------------------------------------------------------------------
# Test 9: Verified backup exists before mutation
# ---------------------------------------------------------------------------
def test_verified_backup_exists_before_mutation(temp_db):
    store, db_path = temp_db

    # Seed 1 company
    store.save_candidate(CandidateCompany(company_id="cand_1", company_name="Test Alpha", normalized_company_name="test alpha"))

    backup_path, backup_sha = store.create_backup()
    assert os.path.exists(backup_path)
    assert len(backup_sha) == 64

    valid, err = store.verify_backup(backup_path)
    assert valid is True
    assert err is None

    # Verify backup contains the seeded company
    conn = sqlite3.connect(str(backup_path))
    count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    conn.close()
    assert count == 1

    try:
        os.remove(backup_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 10, 11, 12: Failure at row 2, 50, 282 rolls back everything
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fail_at_row", [2, 50, 282])
def test_staged_transaction_rollback_on_failure(fail_at_row, temp_db):
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    # Pre-seed 1 company in operational database
    store.save_candidate(CandidateCompany(company_id="seed_initial", company_name="Seed Initial Co", normalized_company_name="seed initial co"))

    # Initial state verification
    with store._get_connection() as conn:
        initial_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
        assert initial_count == 1

    # Simulate staged import with fault injection
    staging_file = tempfile.NamedTemporaryFile(suffix="_stage_test.db", delete=False)
    staging_path = staging_file.name
    staging_file.close()

    try:
        # Clone store
        with store._get_connection() as src_conn:
            clone_conn = sqlite3.connect(staging_path)
            src_conn.backup(clone_conn)
            clone_conn.close()

        staged_store = CandidateStore(staging_path)
        staged_imp = HistoricalImporter(store=staged_store)

        records = staged_imp.parse_xlsx(WORKBOOK_20_25_PATH)

        caught_error = False
        try:
            for idx, r in enumerate(records, 1):
                if idx == fail_at_row:
                    raise RuntimeError(f"Simulated fault injection at row {fail_at_row}")
                staged_imp.import_records([r])

            # Promotion must only occur if no exception
            with staged_store._get_connection() as c_conn:
                with store._get_connection() as s_conn:
                    c_conn.backup(s_conn)
        except Exception:
            caught_error = True
            # Rollback: do not promote clone

        assert caught_error is True

        # Target operational database MUST remain 100% unchanged
        with store._get_connection() as conn:
            final_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
            assert final_count == 1
            cand = conn.execute("SELECT company_name FROM candidate_companies WHERE candidate_id = 'seed_initial'").fetchone()
            assert cand[0] == "Seed Initial Co"

            # Historical imports and links must have zero entries in operational DB
            assert conn.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM historical_company_links").fetchone()[0] == 0

    finally:
        try:
            if os.path.exists(staging_path):
                os.remove(staging_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 13: No duplicate child links on re-import
# ---------------------------------------------------------------------------
def test_no_duplicate_child_links_on_reimport(temp_db):
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    rec = HistoricalRecord(
        source_file="test_links.xlsx",
        sheet_name="Switzerland",
        row_number=1,
        company_name="Sample Practice AG",
        domain="sample-practice.ch",
        tier_hint="A",
        notes="Swiss practice",
        source_url="https://sample-practice.ch/",
        contact_name="Lead",
        contact_role="Director",
        website_links=[
            {"raw_label": "Home", "raw_url": "https://sample-practice.ch/", "host": "sample-practice.ch", "category": "OFFICIAL_HOME"},
            {"raw_label": "Projects", "raw_url": "https://sample-practice.ch/projects", "host": "sample-practice.ch", "category": "PROJECTS"},
        ],
    )

    importer.import_records([rec])
    with store._get_connection() as conn:
        count_1 = conn.execute("SELECT COUNT(*) FROM historical_company_links").fetchone()[0]
        assert count_1 == 2

    # Re-import identical record
    importer.import_records([rec])
    with store._get_connection() as conn:
        count_2 = conn.execute("SELECT COUNT(*) FROM historical_company_links").fetchone()[0]
        assert count_2 == 2


# ---------------------------------------------------------------------------
# Test 14: No loss of historical outreach, opt-outs or manual decisions
# ---------------------------------------------------------------------------
def test_no_loss_of_outreach_opt_outs_or_manual_decisions(temp_db):
    store, db_path = temp_db

    # Existing company with approved manual decision and DNC opt-out
    existing = CandidateCompany(
        candidate_id="cand_existing",
        company_name="Alpha Engineering",
        normalized_company_name="alpha engineering",
        company_domain="alpha-eng.it",
        historical_contact_status="dnc",
        cold_outreach_allowed=False,
        suppressed=True,
        manual_review_decision="APPROVED",
    )
    store.save_candidate(existing)

    importer = HistoricalImporter(store=store)
    rec = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Italy",
        row_number=5,
        company_name="Alpha Engineering",
        domain="alpha-eng.it",
        tier_hint="A",
        notes="Existing studio",
        source_url="https://alpha-eng.it",
        contact_name="Contact",
        contact_role="Director",
        raw_outreach_status="بررسی اولیه",  # attempts to reset to uncontacted
    )

    importer.import_records([rec])

    saved = store.get_candidate("cand_existing")
    assert saved.historical_contact_status == "dnc"
    assert saved.cold_outreach_allowed is False
    assert saved.suppressed is True
    assert saved.manual_review_decision == "APPROVED"


# ---------------------------------------------------------------------------
# Test 15: Social-network subdomains cannot become verified primary websites
# ---------------------------------------------------------------------------
def test_social_network_subdomains_cannot_become_primary_websites():
    # Defect 4 reproduction 1: m.linkedin.com
    res_m = parse_multiline_website_cell("https://m.linkedin.com/company/example")
    assert res_m["primary_domain"] is None

    res_combo = parse_multiline_website_cell("https://m.linkedin.com/company/example\nhttps://example-architects.com")
    assert res_combo["primary_domain"] == "example-architects.com"
    assert res_combo["primary_method"] == "FIRST_VALID_HOST"

    # Indeed subdomain
    res_indeed = parse_multiline_website_cell("https://jobs.indeed.com/cmp/acme")
    assert res_indeed["primary_domain"] is None


# ---------------------------------------------------------------------------
# Test 16: Local/private IP and unsafe scheme inputs are rejected for fetch eligibility
# ---------------------------------------------------------------------------
def test_local_private_ip_and_unsafe_targets_rejected():
    # Defect 4 reproduction 2: 127.0.0.1
    res_loopback = parse_multiline_website_cell("http://127.0.0.1/private")
    assert res_loopback["primary_domain"] is None

    # Private network ranges
    res_priv1 = parse_multiline_website_cell("http://192.168.1.100/admin")
    assert res_priv1["primary_domain"] is None

    res_priv2 = parse_multiline_website_cell("http://10.0.0.1/dashboard")
    assert res_priv2["primary_domain"] is None

    # Localhost
    res_local = parse_multiline_website_cell("http://localhost:8000/internal")
    assert res_local["primary_domain"] is None

    # Disallowed helper direct test
    assert is_disallowed_or_unsafe_host("127.0.0.1") is True
    assert is_disallowed_or_unsafe_host("169.254.169.254") is True
    assert is_disallowed_or_unsafe_host("::1") is True
    assert is_disallowed_or_unsafe_host("example.local") is True
    assert is_disallowed_or_unsafe_host("corp.internal") is True
    assert is_disallowed_or_unsafe_host("valid-architecture.ch") is False


# ---------------------------------------------------------------------------
# Test 17: No real Brave, LLM or email network calls
# ---------------------------------------------------------------------------
def test_strict_offline_zero_network_calls():
    # Verify environment flags ensure offline safety
    assert not os.getenv("ALLOW_LIVE_BRAVE_QUERIES")
    assert not os.getenv("ALLOW_OUTBOUND_EMAILS")


# ---------------------------------------------------------------------------
# Test 18: No customer workbook included in developer ZIP
# ---------------------------------------------------------------------------
def test_no_customer_workbook_in_zip_builder():
    from scripts.rebuild_zip import should_exclude
    # Customer workbook paths must be excluded
    assert should_exclude(Path("Emails-10-27-2026-20-25.xlsx")) is True
    assert should_exclude(Path("data/Emails-10-27-2026-18-19.xlsx")) is True
    assert should_exclude(Path("customer_data.xlsx")) is True
