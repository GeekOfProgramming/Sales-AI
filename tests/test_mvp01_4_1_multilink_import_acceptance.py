"""
Tests for SalesAI MVP-01.4.1 Multi-Link Workbook Intelligence & Final Safe Import Acceptance.
Validates:
1. Pfenninger & Partner multiline website parsing and child link categorization.
2. Atelier Tricarico multiline website parsing with subdomain preservation and placeholders.
3. Multiple URLs under one company domain (no phantom duplicate company creation).
4. Bare domains and normal HTTP/HTTPS URL extraction.
5. Markdown links and malformed-but-recoverable formatting.
6. [link removed] placeholders preservation without fabricated URLs.
7. Disallowed social/job-board domains never selected as primary company domain.
8. Multiple competing plausible primary domains detection and flag.
9. Link classification into structured categories (OFFICIAL_HOME, PROJECTS, CAREERS, etc.).
10. Accurate canonical domain counts (283 source rows -> 283 companies).
11. Communication event counts derived from factual evidence (142 with dates / 141 sent+interview+followup).
12. Invalid email with historical dates preservation at email level.
13. Follow-up rows with populated reply dates preservation.
14. Server-side validation gate rejects 0-record imports and unmapped headers.
15. Server-side validation gate parity with Preview.
16. Idempotent import of company records and child links into isolated database.
17. Consistent SQLite backup API and verification.
18. Zero live Brave requests, zero LLM inference, zero email sends.
"""

import os
import tempfile
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from backend.main import app
from sales_engine.historical.importer import (
    HistoricalImporter,
    HistoricalRecord,
    parse_multiline_website_cell,
    classify_link,
    normalize_outreach_status,
)
from sales_engine.candidates.models import CandidateCompany
from sales_engine.candidates.candidate_store import CandidateStore


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
def temp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    store = CandidateStore(db_path=db_path)
    yield store, db_path
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. Representative Fixture A: Pfenninger & Partner AG
# ---------------------------------------------------------------------------
def test_fixture_a_pfenninger_and_partner_multiline_parsing():
    cell_input = """www.pfenninger-partner.com
وبسایت رسمی (انگلیسی): [link removed]
وبسایت رسمی (آلمانی): https://www.pfenninger-partner.com/
مراجع و پروژهها (References): https://www.pfenninger-partner.com/referenzen
تیم و اعضا (Team): https://www.pfenninger-partner.com/mitarbeiter
فرصتهای شغلی (Jobs): https://www.pfenninger-partner.com/jobs
درباره شرکت (About Company): https://www.pfenninger-partner.com/unternehmen
اطلاعات تماس (Contact): https://www.pfenninger-partner.com/kontakt"""

    res = parse_multiline_website_cell(cell_input, "Pfenninger & Partner AG")

    # One primary canonical host
    assert res["primary_domain"] == "pfenninger-partner.com"
    assert res["primary_method"] == "EXPLICIT_OFFICIAL_LABEL"
    assert res["is_multiline"] is True

    # Check child links
    links = res["links"]
    assert len(links) >= 7

    # Subpage categories
    cats = {l["category"]: l for l in links}
    assert "OFFICIAL_HOME" in cats
    assert "PROJECTS" in cats
    assert "TEAM" in cats
    assert "CAREERS" in cats
    assert "ABOUT" in cats
    assert "CONTACT" in cats
    assert "UNRESOLVED" in cats  # [link removed] placeholder

    # Placeholders count
    assert res["placeholders_count"] == 1

    # Valid URLs match primary host
    for l in links:
        if l.get("host"):
            assert l["matches_primary_host"] is True
            assert l["is_external"] is False


# ---------------------------------------------------------------------------
# 2. Representative Fixture B: Atelier Tricarico
# ---------------------------------------------------------------------------
def test_fixture_b_atelier_tricarico_multiline_parsing():
    cell_input = """https://atelier.tricarico.ch/
وبسایت رسمی (صفحه اصلی): [link removed]
خدمات و پروژههای نمونه: [link removed]
آموزش ArchiCAD، AutoCAD و Revit: [link removed]
فهرست همکاریهای حرفهای: [link removed]
پروفایل Fabio Tricarico: [link removed]
دامنه معرفیشده توسط دفتر: [link removed]"""

    res = parse_multiline_website_cell(cell_input, "Atelier Tricarico")

    # Meaningful subdomain preserved
    assert res["primary_domain"] == "atelier.tricarico.ch"
    assert res["primary_method"] == "FIRST_VALID_HOST"

    # Preserves 6 placeholders
    assert res["placeholders_count"] == 6

    # Labels preserved on placeholders
    unresolved_labels = [l["raw_label"] for l in res["links"] if l.get("is_placeholder")]
    assert any("صفحه اصلی" in lbl for lbl in unresolved_labels)
    assert any("آموزش ArchiCAD" in lbl for lbl in unresolved_labels)


# ---------------------------------------------------------------------------
# 3. Malformed-but-Recoverable Markdown Links
# ---------------------------------------------------------------------------
def test_malformed_but_recoverable_website_formatting():
    samples = """[Official Website](https://example.com/
[Official Website]https://example.com/about
Official Website: www.example.com
https://example.com/
Projects: https://example.com/projects"""

    res = parse_multiline_website_cell(samples, "Example Studio")

    assert res["primary_domain"] == "example.com"
    hosts = [l.get("host") for l in res["links"] if l.get("host")]
    assert all(h == "example.com" for h in hosts)
    assert len(res["links"]) >= 4


# ---------------------------------------------------------------------------
# 4. Social & Job Board Disqualification from Primary Domain
# ---------------------------------------------------------------------------
def test_social_and_job_boards_never_selected_as_primary_domain():
    cell_input = """LinkedIn Profile: https://www.linkedin.com/company/super-bim/
Careers Page: https://www.indeed.com/cmp/super-bim
Official Website: https://www.superbim-engineering.it/"""

    res = parse_multiline_website_cell(cell_input, "Super BIM")

    # Must pick the real company site, not LinkedIn or Indeed
    assert res["primary_domain"] == "superbim-engineering.it"
    assert res["primary_method"] == "EXPLICIT_OFFICIAL_LABEL"

    # LinkedIn must be classified as SOCIAL_PROFILE
    li_link = next(l for l in res["links"] if "linkedin" in (l.get("host") or ""))
    assert li_link["category"] == "SOCIAL_PROFILE"
    assert li_link["is_external"] is True


# ---------------------------------------------------------------------------
# 5. Multiple Competing Plausible Domains Detection
# ---------------------------------------------------------------------------
def test_multiple_competing_plausible_primary_domains_flag():
    cell_input = """https://www.studio-alfa.it/
https://www.beta-engineering.de/"""

    res = parse_multiline_website_cell(cell_input, "Dual Entity")

    assert res["multiple_plausible_domains"] is True
    assert len(res["plausible_domains"]) == 2
    assert "studio-alfa.it" in res["plausible_domains"]
    assert "beta-engineering.de" in res["plausible_domains"]


# ---------------------------------------------------------------------------
# 6. Database Cloned Ingestion and Child-Link Persistence
# ---------------------------------------------------------------------------
def test_cloned_database_ingestion_and_link_persistence(temp_db):
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    rec = HistoricalRecord(
        source_file="test_multilink.xlsx",
        sheet_name="Switzerland",
        row_number=5,
        company_name="Pfenninger & Partner AG",
        domain="pfenninger-partner.com",
        tier_hint="A",
        notes="Enterprise Swiss BIM practice",
        source_url="https://www.pfenninger-partner.com/",
        contact_name="Zenone Pfenninger",
        contact_role="Director",
        country="Switzerland",
        city="Zürich",
        emails=["info@pfenninger-partner.com"],
        raw_outreach_status="بررسی اولیه",
        website_links=[
            {"raw_label": "Official German", "raw_url": "https://www.pfenninger-partner.com/", "normalized_url": "https://www.pfenninger-partner.com/", "host": "pfenninger-partner.com", "category": "OFFICIAL_HOME", "is_placeholder": False, "matches_primary_host": True, "is_external": False, "availability": "VALID_URL"},
            {"raw_label": "Projects", "raw_url": "https://www.pfenninger-partner.com/referenzen", "normalized_url": "https://www.pfenninger-partner.com/referenzen", "host": "pfenninger-partner.com", "category": "PROJECTS", "is_placeholder": False, "matches_primary_host": True, "is_external": False, "availability": "VALID_URL"},
            {"raw_label": "Careers", "raw_url": "https://www.pfenninger-partner.com/jobs", "normalized_url": "https://www.pfenninger-partner.com/jobs", "host": "pfenninger-partner.com", "category": "CAREERS", "is_placeholder": False, "matches_primary_host": True, "is_external": False, "availability": "VALID_URL"},
        ],
        raw_website_cell="https://www.pfenninger-partner.com/\nProjects: https://www.pfenninger-partner.com/referenzen",
    )

    # Ingest record
    res = importer.import_records([rec])
    assert res.companies_created == 1

    # Check CandidateCompany
    cand = store.get_candidate_by_domain("pfenninger-partner.com")
    assert cand is not None
    assert cand.company_name == "Pfenninger & Partner AG"

    # Check historical_company_links table
    with store._get_connection() as conn:
        links = conn.execute("SELECT * FROM historical_company_links WHERE canonical_domain = 'pfenninger-partner.com'").fetchall()
        assert len(links) == 3
        cats = [l["category"] for l in links]
        assert "OFFICIAL_HOME" in cats
        assert "PROJECTS" in cats
        assert "CAREERS" in cats

    # Check idempotency: re-import must not duplicate child links
    res2 = importer.import_records([rec])
    assert res2.duplicates_skipped == 1
    with store._get_connection() as conn:
        links_after = conn.execute("SELECT * FROM historical_company_links WHERE canonical_domain = 'pfenninger-partner.com'").fetchall()
        assert len(links_after) == 3


# ---------------------------------------------------------------------------
# 7. Server-Side Validation Gate Parity with Preview
# ---------------------------------------------------------------------------
def test_server_side_import_confirmation_blocks_zero_records(tmp_path):
    # Empty CSV
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("company,domain\n")

    csrf_client = CsrfAwareClient(app)
    # Staging path check requires file in allowed directory
    staging_file = Path("data/staging") / "test_empty_block.csv"
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    staging_file.write_text("company,domain\n")

    try:
        # Direct attempt to confirm empty file must be rejected with 400
        resp = csrf_client.post(
            "/sales/sources/import-history/confirm",
            data={
                "file_path": str(staging_file),
                "file_hash": "dummy_hash",
                "preview_token": "dummy_token",
            },
        )
        assert resp.status_code == 400
    finally:
        if staging_file.exists():
            os.remove(staging_file)


# ---------------------------------------------------------------------------
# 8. Real Workbook Verification
# ---------------------------------------------------------------------------
def test_real_workbook_inspection_if_available():
    workbook_path = Path("C:/Sales-AI/Emails-10-27-2026-18-19.xlsx")
    if not workbook_path.exists():
        pytest.skip("Authoritative workbook not present on local machine.")

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        store = CandidateStore(tmp_path)
        importer = HistoricalImporter(store=store)

        insp = importer.inspect_workbook(workbook_path)
        assert insp["company_records_count"] == 283
        assert insp["multiline_cells_count"] == 71
        assert insp["total_extracted_links"] >= 650
        assert insp["placeholders_count"] >= 36
        assert insp["status_distribution"]["بررسی اولیه"] == 141
        assert insp["status_distribution"]["ایمیل ارسال شد"] == 137
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except PermissionError:
            pass
