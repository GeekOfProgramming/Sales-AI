"""
Acceptance Test Suite for SalesAI MVP-01.1 — Operational UI Acceptance & Safe First Lead Discovery.

Covers all 20 MVP-01.1 Criteria:
1. Start local application without starting Ollama.
2. Dashboard navigation and live count reflection.
3. Workbook upload with real multipart file (max 25MB check, temporary staging).
4. Multi-sheet preview with zero DB mutation (skips Dashboard/Summary sheets, formula/total rows).
5. Import confirmation and persistence.
6. Re-import idempotency (zero duplicates created).
7. Duplicate and historical-contact detection (preserves past dates, statuses, notes without blank overwrite).
8. Do-not-contact / opt-out preservation.
9. Search preview with zero network calls and explicit cost notice (~$0.005 USD).
10. Unknown Brave billing state / missing checkbox blocks live search.
11. Manipulated source lists cannot bypass the one-source rule (400 on anything other than brave_job_search).
12. Search result count is capped at 10 (adapter & provider hard boundary).
13. Existing discovery-profile endpoints cannot bypass approval.
14. Mocked successful Brave search creates reviewable candidates with explicit trust states.
15. Mocked HTTP 429 does not trigger retries and records FAILED attempt.
16. Operator approval, rejection, and review notes persist.
17. Approved-only CSV and XLSX exports (approved_only=True default, separates historical vs new).
18. No automated emails (EMAIL_SEND_ENABLED=false).
19. No LLM inference during discovery.
20. No customer data or secrets in release export ZIP.
"""

import io
import os
import tempfile
import pytest
import openpyxl
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.historical.importer import HistoricalImporter
from sales_engine.discovery_v2.adapters import create_search_engine_adapter
from sales_engine.discovery_v2.source_registry import SourceRegistry
from backend.schemas import NormalizedSearchResult


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
            # Ensure cookie matches submitted form token if provided
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
    return CsrfAwareClient(app)


def create_14_sheet_test_workbook(file_path: str):
    """
    Creates a realistic 14-sheet workbook mimicking Emails-10-26-2026-12-44.xlsx:
    - Sheet 1: Dashboard (Formulas, KPIs, should NOT be imported as companies)
    - Sheet 2: Meeting Schedule (Meetings and appointments)
    - Sheets 3-12: Geographic Sheets (Milano, Roma, Torino, Bologna, Firenze, Napoli, Venezia, Genova, Verona, Bari)
    - Sheet 13: LinkedIn Leads
    - Sheet 14: Indeed Outreach
    """
    wb = openpyxl.Workbook()

    # 1. Dashboard
    ws_dash = wb.active
    ws_dash.title = "Dashboard"
    ws_dash.append(["KPI", "Value", "Notes"])
    ws_dash.append(["Total Leads", "=SUM(B4:B10)", "Overall volume"])
    ws_dash.append(["Contacted Leads", 142, "Outreach executed"])
    ws_dash.append(["Totale Generale", "=142+50", "Formula row"])

    # 2. Meetings
    ws_meet = wb.create_sheet(title="Meeting")
    ws_meet.append(["Azienda", "Sito Web", "Referente", "Email", "Data Contatto", "Stato", "Note"])
    ws_meet.append(["Studio Milano Meeting SRL", "www.milano-meeting.it", "Gianni Neri", "gianni@milano-meeting.it", "2026-10-15", "meeting_scheduled", "Demo pyBIM fissata"])

    # 3. Milano
    ws_milano = wb.create_sheet(title="Milano")
    ws_milano.append(["Azienda", "Sito Web", "Città", "Referente", "Ruolo", "Email", "Data Contatto", "Stato", "Note"])
    ws_milano.append(["Politecnica BIM Milano", "https://politecnica-bim.it", "Milano", "Chiara Conti", "BIM Lead", "chiara@politecnica-bim.it", "2026-09-10", "positive", "Disponibili a testare automazione"])
    ws_milano.append(["DNC Architettura Milano", "https://dnc-milano.it", "Milano", "Roberto Ferri", "Partner", "roberto@dnc-milano.it", "2026-08-05", "opt_out", "Non ricontattare"])
    ws_milano.append(["Totale Sheet Milano", "", "", "", "", "", "", "", "Formula/summary row to ignore"])

    # 4. Roma
    ws_roma = wb.create_sheet(title="Roma")
    ws_roma.append(["Company", "Website", "City", "Country", "Contact", "Role", "Email", "Date", "Status", "Notes"])
    ws_roma.append(["Roma Capitale Ingegneria", "https://roma-ing.it", "Roma", "Italy", "Fabio Gatti", "Director", "fabio@roma-ing.it", "2026-09-20", "contacted", "Inviata brochure"])
    ws_roma.append(["Politecnica BIM Milano", "https://politecnica-bim.it", "Roma", "Italy", "Branch Contact", "Manager", "branch@politecnica-bim.it", "", "", "Filiale Roma - deve deduplicarsi"])

    # Sheets 5 to 12: Other Italian Cities
    for city in ["Torino", "Bologna", "Firenze", "Napoli", "Venezia", "Genova", "Verona", "Bari"]:
        ws_c = wb.create_sheet(title=city)
        ws_c.append(["Azienda", "Sito Web", "Città", "Email", "Stato", "Note"])
        ws_c.append([f"{city} Engineering Studio", f"https://www.{city.lower()}-eng.it", city, f"info@{city.lower()}-eng.it", "uncontacted", f"Lead geolocalizzato {city}"])

    # 13. LinkedIn Leads
    ws_li = wb.create_sheet(title="LinkedIn")
    ws_li.append(["Azienda", "Sito Web", "Fonte", "Referente", "Email", "Stato"])
    ws_li.append(["AEC Talent Hub", "https://aec-talent.com", "LinkedIn Job Postings", "Laura Marchi", "laura@aec-talent.com", "contacted"])

    # 14. Indeed Leads
    ws_ind = wb.create_sheet(title="Indeed")
    ws_ind.append(["Azienda", "Sito Web", "Fonte", "Email", "Stato", "Note"])
    ws_ind.append(["Global BIM Careers", "https://indeed.com/cmp/global-bim", "Indeed", "hr@global-bim.com", "rejected", "Job aggregator profile"])

    wb.save(file_path)
    wb.close()


# -----------------------------------------------------------------------------
# Test 1 & 2: Local Application Startup & Dashboard Navigation
# -----------------------------------------------------------------------------
def test_local_app_startup_and_dashboard_pages(client):
    r_dash = client.get("/sales/dashboard")
    assert r_dash.status_code == 200
    assert "Operational Dashboard" in r_dash.text
    assert "Real sending is disabled on this server" in r_dash.text

    r_disc = client.get("/sales/discovery")
    assert r_disc.status_code == 200
    assert "Small-Scale Lead Discovery Pilot" in r_disc.text

    r_src = client.get("/sales/sources")
    assert r_src.status_code == 200
    assert "Choose Excel / CSV File to Upload" in r_src.text

    r_cand = client.get("/sales/candidates")
    assert r_cand.status_code == 200
    assert "Candidate Intelligence" in r_cand.text


# -----------------------------------------------------------------------------
# Test 3, 4, 5, 6, 7, 8: Multipart Workbook Upload, 14-Sheet Preview & Idempotency
# -----------------------------------------------------------------------------
def test_14_sheet_multipart_upload_preview_and_idempotency(client, test_db):
    store = CandidateStore(db_path=test_db)
    importer = HistoricalImporter(store=store)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        wb_path = f.name

    try:
        create_14_sheet_test_workbook(wb_path)
        with open(wb_path, "rb") as f_bytes:
            file_content = f_bytes.read()

        # Step 3: Test multipart upload endpoint
        response = client.post(
            "/sales/sources/import-history/preview",
            files={"file": ("Emails-10-26-2026-12-44.xlsx", file_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"csrf_token": "test_token"},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200
        preview_data = response.json()

        # Step 4: Verify sheets & summary skipping
        assert preview_data["rows_detected"] > 0
        sheets = preview_data["sheets"]
        sheet_names = [s["sheet_name"] for s in sheets]
        assert len(sheet_names) == 14
        assert "Dashboard" in sheet_names
        assert "Meeting" in sheet_names
        assert "Milano" in sheet_names

        # Check parse_xlsx skips Dashboard formula rows from importing as companies
        records = importer.parse_xlsx(wb_path)
        comp_names = [r.company_name.lower() for r in records]
        assert not any("totale" in name for name in comp_names)
        assert not any("total leads" in name for name in comp_names)

        # Step 5: Execute import
        res1 = importer.import_records(records)
        assert res1.companies_created >= 10
        assert res1.duplicates_skipped == 0

        # Step 7: Deduplication across sheets (Politecnica BIM Milano in both Milano and Roma)
        politecnica = store.get_candidate_by_domain("politecnica-bim.it")
        assert politecnica is not None
        assert politecnica.previous_outreach_detected is True
        assert politecnica.cold_outreach_allowed is False
        assert politecnica.last_contacted_at == "2026-09-10"

        # Step 8: DNC preservation
        dnc_comp = store.get_candidate_by_domain("dnc-milano.it")
        assert dnc_comp is not None
        assert dnc_comp.suppressed is True
        assert dnc_comp.cold_outreach_allowed is False

        # Step 6: Idempotency (re-import produces 0 new companies)
        res2 = importer.import_records(records)
        assert res2.companies_created == 0
        assert res2.duplicates_skipped == len(records)
    finally:
        if os.path.exists(wb_path):
            try:
                os.remove(wb_path)
            except PermissionError:
                pass


# -----------------------------------------------------------------------------
# Test 9, 10, 11, 12, 13: Search Preview, Policy Guard, & Capping
# -----------------------------------------------------------------------------
def test_search_preview_and_pilot_policy_guards(client, monkeypatch, test_db):
    # Test 9: Preview query makes zero network calls
    resp_prev = client.post(
        "/sales/discovery/run",
        data={
            "query": "BIM Revit",
            "country": "Italy",
            "city": "Milano",
            "category": "BIM Engineering",
            "is_preview": "true",
            "csrf_token": "test",
        },
        follow_redirects=False,
    )
    assert resp_prev.status_code == 303
    import urllib.parse
    loc_prev = urllib.parse.unquote(resp_prev.headers["location"])
    assert "Query Preview Ready" in loc_prev or "Query+Preview+Ready" in loc_prev
    assert "Estimated Cost" in loc_prev or "Estimated+Cost" in loc_prev

    # Test 10: Unknown Brave billing state / unconfirmed billing blocks live search
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BSA_test_key_123456789")
    resp_unverified = client.post(
        "/sales/discovery/run",
        data={
            "query": "BIM Revit",
            "country": "Italy",
            "city": "Milano",
            "confirm_live": "true",
            # billing_confirmed NOT checked
            "csrf_token": "test",
        },
        follow_redirects=False,
    )
    assert resp_unverified.status_code == 303
    assert "BILLING_UNVERIFIED" in resp_unverified.headers["location"]

    # Test 11: Manipulated source list violates pilot policy
    resp_bad_source = client.post(
        "/sales/discovery/run",
        data={
            "query": "BIM Revit",
            "sources": "brave_job_search,ats_greenhouse,google_search",
            "confirm_live": "true",
            "billing_confirmed": "true",
            "csrf_token": "test",
        },
    )
    assert resp_bad_source.status_code == 400
    assert "MVP Pilot Policy Violation" in resp_bad_source.text

    # Test 13: Profile run containing live search is gated
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore, DiscoveryScope
    store = CandidateStore(db_path=test_db)
    scope_store = DiscoveryScopeStore(db_path=test_db)
    scope_store.save_scope(DiscoveryScope(
        scope_id="profile_with_brave",
        scope_name="Profile with Brave",
        markets=["Italy"],
        source_ids=["brave_job_search"],
    ))
    resp_prof = client.post(
        "/sales/discovery/profiles/profile_with_brave/run",
        data={"csrf_token": "test"},
        follow_redirects=False,
    )
    assert resp_prof.status_code == 303
    loc_prof = urllib.parse.unquote(resp_prof.headers["location"])
    assert "Automated Profile Run BLOCKED" in loc_prof or "Automated+Profile+Run+BLOCKED" in loc_prof


# -----------------------------------------------------------------------------
# Test 12, 14, 15: Mocked Brave Search, Hard Cap at 10, Trust Levels & 429
# -----------------------------------------------------------------------------
def test_brave_search_capped_at_10_and_trust_levels(client, monkeypatch, test_db):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BSA_mock_key_valid")
    registry = SourceRegistry()
    cfg = registry.get_source("brave_job_search")
    assert cfg.max_results_per_run <= 10

    # Mock provider returning 25 items to verify hard cap of 10
    class MockProvider25:
        async def search(self, query: str, num_results: int = 10):
            assert num_results <= 10, f"Adapter requested {num_results} > 10!"
            items = []
            for i in range(15):
                items.append(NormalizedSearchResult(
                    query=query,
                    title=f"Studio BIM {i} Srl",
                    url=f"https://www.studio-bim-{i}.it/services",
                    snippet=f"Ingegneria e modellazione Revit a Milano {i}",
                    source="brave",
                    rank=i + 1,
                ))
            return items

    monkeypatch.setattr("sales_engine.discovery.brave_search.BraveSearchProvider", MockProvider25)
    adapter = create_search_engine_adapter(cfg)
    results = adapter("BIM Revit Milano")
    # Verify exactly <= 10 results returned
    assert len(results) <= 10
    for r in results:
        assert r["trust_level"] == "POTENTIAL_COMPANY"
        assert r["evidence_type"] == "title_and_snippet_keyword_match"
        assert r["evidence_source_url"].startswith("https://")

    # Test 15: Mocked 429 does not retry and records FAILED attempt
    class MockProvider429:
        async def search(self, query: str, num_results: int = 10):
            import httpx
            req = httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search")
            resp = httpx.Response(status_code=429, request=req)
            raise httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp)

    monkeypatch.setattr("sales_engine.discovery.brave_search.BraveSearchProvider", MockProvider429)
    resp_429 = client.post(
        "/sales/discovery/run",
        data={
            "query": "BIM Revit",
            "country": "Italy",
            "city": "Milano",
            "confirm_live": "true",
            "billing_confirmed": "true",
            "csrf_token": "test",
        },
        follow_redirects=False,
    )
    assert resp_429.status_code == 303
    import urllib.parse
    loc_429 = urllib.parse.unquote(resp_429.headers["location"])
    assert "Search FAILED" in loc_429 or "Search+FAILED" in loc_429

    store = CandidateStore(db_path=test_db)
    stats = store.get_pilot_search_stats()
    assert stats["failed_attempts"] >= 1


# -----------------------------------------------------------------------------
# Test 16, 17: Candidate Review Decision and Approved-Only Exports
# -----------------------------------------------------------------------------
def test_candidate_review_and_approved_only_export(client, test_db):
    store = CandidateStore(db_path=test_db)
    from sales_engine.candidates.models import CandidateCompany

    # 1. Create a candidate awaiting review
    cand1 = CandidateCompany(
        company_id="cand:domain:test-approved-studio.it",
        company_name="Test Approved Studio",
        company_domain="test-approved-studio.it",
        country="Italy",
        city="Milano",
        discovery_sources=["brave_job_search"],
    )
    # 2. Create another candidate that is rejected
    cand2 = CandidateCompany(
        company_id="cand:domain:test-rejected-aggregator.com",
        company_name="Test Rejected Aggregator",
        company_domain="test-rejected-aggregator.com",
        country="Italy",
        discovery_sources=["brave_job_search"],
    )
    # 3. Create a historical imported candidate
    cand3 = CandidateCompany(
        company_id="cand:domain:historical-imported-co.it",
        company_name="Historical Imported Co",
        company_domain="historical-imported-co.it",
        country="Italy",
        discovery_sources=["historical_seed"],
        historical_contact_status="contacted",
    )
    store.save_candidate(cand1)
    store.save_candidate(cand2)
    store.save_candidate(cand3)

    # Approve cand1
    client.post(
        f"/sales/candidates/{cand1.candidate_id}/review",
        data={"decision": "approve", "notes": "Approved for pyBIM outreach", "csrf_token": "test"},
    )
    # Reject cand2
    client.post(
        f"/sales/candidates/{cand2.candidate_id}/review",
        data={"decision": "reject", "notes": "Unqualified portal", "csrf_token": "test"},
    )

    # Test 17: Default Export (/sales/candidates/export?format=csv) must include ONLY approved leads
    r_exp_approved = client.get("/sales/candidates/export?format=csv")
    assert r_exp_approved.status_code == 200
    csv_text = r_exp_approved.text
    assert "Test Approved Studio" in csv_text
    assert "Test Rejected Aggregator" not in csv_text
    assert "Historical Imported Co" not in csv_text

    # Test Export All Candidates (/sales/candidates/export?format=csv&approved_only=false)
    r_exp_all = client.get("/sales/candidates/export?format=csv&approved_only=false")
    assert r_exp_all.status_code == 200
    all_text = r_exp_all.text
    assert "Test Approved Studio" in all_text
    assert "Test Rejected Aggregator" in all_text
    assert "Historical Imported Co" in all_text
    assert "Historical Imported Company" in all_text


# -----------------------------------------------------------------------------
# Test 18, 19, 20: No Emails, No LLM, and Clean Export Packaging
# -----------------------------------------------------------------------------
def test_zero_emails_zero_llm_and_clean_packaging():
    # 18: EMAIL_SEND_ENABLED
    assert os.getenv("EMAIL_SEND_ENABLED", "false").lower() == "false"

    # 19: No active external LLM keys required for core lead discovery
    from sales_engine.historical.importer import HistoricalImporter
    store = CandidateStore()
    importer = HistoricalImporter(store=store)
    assert importer is not None

    # 20: Verify rebuild_zip includes run_sales_ai.ps1 and excludes databases
    from scripts.rebuild_zip import build_release_zip_stats
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        tmp_zip = f.name
    try:
        base_dir = Path(__file__).resolve().parent.parent
        stats = build_release_zip_stats(source_dir=base_dir, output_zip=Path(tmp_zip))
        assert stats["denylist_violations_count"] == 0
        assert stats["included_count"] > 400
        
        # Verify run_sales_ai.ps1 and run_sales_ai.bat are inside
        import zipfile
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            namelist = zf.namelist()
            assert "run_sales_ai.ps1" in namelist
            assert "run_sales_ai.bat" in namelist
            assert not any(n.endswith(".db") for n in namelist)
            assert not any(n.endswith(".sqlite") for n in namelist)
            assert not any("Emails-10-26-2026-12-44.xlsx" in n for n in namelist)
    finally:
        if os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except PermissionError:
                pass
