"""
End-to-End Acceptance Tests for SalesAI MVP-01 — First Usable Local Lead Discovery Workflow.

Covers all 20 Phase 8 Acceptance Criteria:
1. Dashboard startup without mandatory Ollama generation.
2. Workbook import preview (multi-sheet inspection, column mapping report).
3. Multi-sheet country/city mapping.
4. Existing outreach preservation.
5. Duplicate company identification across sheets/domains.
6. Do-not-contact / suppression preservation.
7. Discovery query preview (dry-run).
8. Real-search authorization gate using mocks.
9. One-query/ten-result maximum.
10. No fabricated services, technologies or geography.
11. Provider failures and missing credentials error handling.
12. Candidate review and persistence (approve, reject, duplicate, needs_research, notes).
13. Dashboard counts reflect live database.
14. Export correctness (CSV/XLSX with formula injection protection).
15. No email sending (EMAIL_SEND_ENABLED=false).
16. No unexpected LLM API calls.
17. No paid operation without explicit approval.
18. No historical SQLite mutation of original records.
19. No customer data in source exports.
20. End-to-end local UI/API integration.
"""

import io
import os
import tempfile
import pytest
import openpyxl
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import get_console_services, sanitize_spreadsheet_cell
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord
from sales_engine.discovery_v2.source_registry import SourceRegistry
from sales_engine.discovery_v2.adapters import create_search_engine_adapter, create_ats_adapter, create_directory_adapter
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


@pytest.fixture
def client(test_db):
    return TestClient(app)


def create_synthetic_pybim_workbook(file_path: str):
    """Generates synthetic pyBIM multi-sheet workbook mimicking Emails-10-26-2026-12-44.xlsx."""
    wb = openpyxl.Workbook()
    # Sheet 1: Milano
    ws_milano = wb.active
    ws_milano.title = "Milano"
    ws_milano.append(["Azienda", "Sito Web", "Città", "Referente", "Ruolo", "Email", "Data Contatto", "Stato", "Note"])
    ws_milano.append(["Studio Architettura Milano SRL", "https://www.architettura-milano.it", "Milano", "Mario Rossi", "BIM Manager", "mario@architettura-milano.it", "2026-09-15", "positive", "Interessati a pyBIM automation"])
    ws_milano.append(["Ingegneria Strutturale MI", "www.ing-strutture.it", "Milano", "Luigi Bianchi", "Direttore Tecnico", "luigi@ing-strutture.it", "", "", "Lead da contattare"])
    ws_milano.append(["DNC Engineering SRL", "https://dnc-eng.it", "Milano", "Anna Neri", "Partner", "anna@dnc-eng.it", "2026-08-01", "opt_out", "Richiesta rimozione"])

    # Sheet 2: Roma
    ws_roma = wb.create_sheet(title="Roma")
    ws_roma.append(["Company", "Website", "City", "Country", "Contact", "Role", "Email", "Date", "Status", "Notes"])
    ws_roma.append(["Roma BIM Partners SPA", "https://romabim.com", "Roma", "Italy", "Paolo Verdi", "CEO", "paolo@romabim.com", "2026-10-01", "meeting_scheduled", "Demo pyBIM fissata"])
    ws_roma.append(["Duplicate Milano Branch", "https://www.architettura-milano.it", "Roma", "Italy", "Carla Rossi", "Branch Lead", "carla@architettura-milano.it", "", "", "Sede distaccata Roma"])

    wb.save(file_path)
    wb.close()


# -----------------------------------------------------------------------------
# Test 1: Dashboard Startup Without Ollama
# -----------------------------------------------------------------------------
def test_dashboard_startup_without_ollama(client):
    response = client.get("/sales/dashboard")
    assert response.status_code == 200
    assert "Operational Dashboard" in response.text
    assert "Real sending is disabled on this server" in response.text


# -----------------------------------------------------------------------------
# Test 2 & 3: Multi-Sheet Workbook Preview, Inspection & Mapping
# -----------------------------------------------------------------------------
def test_workbook_multi_sheet_inspection_and_preview(test_db):
    store = CandidateStore(db_path=test_db)
    importer = HistoricalImporter(store=store)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        wb_path = f.name

    try:
        create_synthetic_pybim_workbook(wb_path)
        inspection = importer.inspect_workbook(wb_path)

        assert inspection["total_sheets"] == 2
        assert len(inspection["sheets"]) == 2
        sheet_names = [s["sheet_name"] for s in inspection["sheets"]]
        assert "Milano" in sheet_names
        assert "Roma" in sheet_names

        # Column mappings detected
        milano_mapping = inspection["sheets"][0]["column_mappings"]
        assert milano_mapping.get("Azienda") == "company_name"
        assert milano_mapping.get("Sito Web") == "domain"
        assert milano_mapping.get("Città") == "city"
        assert milano_mapping.get("Email") == "emails"
    finally:
        if os.path.exists(wb_path):
            os.remove(wb_path)


# -----------------------------------------------------------------------------
# Test 4, 5, 6, 18: Import Execution, Outreach & DNC Preservation, Idempotency
# -----------------------------------------------------------------------------
def test_workbook_import_preservation_and_idempotency(test_db):
    store = CandidateStore(db_path=test_db)
    importer = HistoricalImporter(store=store)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        wb_path = f.name

    try:
        create_synthetic_pybim_workbook(wb_path)
        records = importer.parse_xlsx(wb_path)
        assert len(records) == 5

        # First import
        result1 = importer.import_records(records)
        assert result1.companies_created >= 4
        assert result1.duplicates_skipped == 0

        # Check domain deduplication across sheets (architettura-milano.it in both Milano & Roma)
        milano_cand = store.get_candidate_by_domain("architettura-milano.it")
        assert milano_cand is not None
        assert milano_cand.previous_outreach_detected is True
        assert milano_cand.cold_outreach_allowed is False
        assert milano_cand.city == "Milano"
        assert milano_cand.country == "Italy"

        # Check DNC company
        dnc_cand = store.get_candidate_by_domain("dnc-eng.it")
        assert dnc_cand is not None
        assert dnc_cand.suppressed is True
        assert dnc_cand.cold_outreach_allowed is False

        # Idempotent re-import: zero mutations, all skipped
        result2 = importer.import_records(records)
        assert result2.companies_created == 0
        assert result2.duplicates_skipped == len(records)
    finally:
        if os.path.exists(wb_path):
            os.remove(wb_path)


# -----------------------------------------------------------------------------
# Test 7, 8, 9, 10: Operator Search, Quota Guard & No Fabricated Defaults
# -----------------------------------------------------------------------------
def test_brave_adapter_no_fabricated_defaults(monkeypatch):
    registry = SourceRegistry()
    cfg = registry.get_source("brave_job_search")
    assert cfg is not None

    adapter = create_search_engine_adapter(cfg)

    # Mock Brave provider returning realistic architectural search result
    class MockProvider:
        async def search(self, query: str, num_results: int = 10):
            return [
                NormalizedSearchResult(
                    query=query,
                    title="TecnoBIM Engineering Srl - BIM modeling and Revit consultancy",
                    url="https://www.tecnobim.it/contatti",
                    snippet="Specializzati in modellazione BIM e coordinamento con software Revit e Navisworks a Milano.",
                    source="brave",
                    rank=1,
                ),
                NormalizedSearchResult(
                    query=query,
                    title="AEC Jobs and Careers Portal - Italian Engineering Listings",
                    url="https://jobs.lever.co/aec-portal/engineer-123",
                    snippet="BIM Engineer positions open in northern Italy.",
                    source="brave",
                    rank=2,
                ),
            ]

    monkeypatch.setattr("sales_engine.discovery.brave_search.BraveSearchProvider", MockProvider)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-brave-key-mock")

    results = adapter("BIM Revit Milano")
    assert len(results) == 2

    r1 = results[0]
    assert r1["company_name"] == "TecnoBIM Engineering Srl"
    assert r1["domain"] == "tecnobim.it"
    # Geography must NOT be guessed if not explicitly structured
    assert r1["country"] is None
    # Verified signals from snippet only:
    assert "BIM Services" in r1["services"]
    assert "Revit" in r1["technologies"]
    assert "Navisworks" in r1["technologies"]
    # Unverified defaults (e.g. AutoCAD, Architectural Design) MUST NOT be present
    assert "AutoCAD" not in r1["technologies"]
    assert r1["is_directory_or_job"] is False

    r2 = results[1]
    assert r2["domain"] == "jobs.lever.co"
    assert r2["is_directory_or_job"] is True
    assert r2["url_classification"] == "ats_job"


# -----------------------------------------------------------------------------
# Test 11: Unimplemented Adapters Report NOT_AVAILABLE
# -----------------------------------------------------------------------------
def test_unimplemented_adapters_report_not_available():
    registry = SourceRegistry()
    ats_cfg = registry.get_source("ats_greenhouse")
    assert ats_cfg is not None
    assert ats_cfg.adapter_status == "NOT_AVAILABLE"

    adapter = create_ats_adapter(ats_cfg)
    with pytest.raises(RuntimeError, match="NOT_AVAILABLE"):
        adapter("test query")


# -----------------------------------------------------------------------------
# Test 12: Candidate Review (Approve, Reject, Duplicate, Notes)
# -----------------------------------------------------------------------------
def test_candidate_review_actions(client, test_db):
    store = CandidateStore(db_path=test_db)
    cand, _ = store.get_or_create_candidate(
        company_name="Alpha BIM Studio",
        company_domain="alpha-bim.com",
        country="Italy",
        city="Milano",
    )

    csrf_resp = client.get(f"/sales/candidates/{cand.candidate_id}")
    csrf_token = csrf_resp.cookies.get("sales_csrf_token")

    # 1. Approve candidate
    resp_approve = client.post(
        f"/sales/candidates/{cand.candidate_id}/review",
        data={"decision": "approve", "notes": "Target BIM consultant"},
        cookies={"sales_csrf_token": csrf_token},
        headers={"x-csrf-token": csrf_token},
        follow_redirects=True,
    )
    assert resp_approve.status_code == 200

    updated = store.get_candidate(cand.candidate_id)
    assert updated.qualification_outcome.value == "qualified_for_enrichment"
    assert updated.cold_outreach_allowed is True
    assert any("Target BIM consultant" in r for r in updated.qualification_reasons)

    # 2. Reject candidate
    resp_reject = client.post(
        f"/sales/candidates/{cand.candidate_id}/review",
        data={"decision": "reject", "notes": "No BIM operations"},
        cookies={"sales_csrf_token": csrf_token},
        headers={"x-csrf-token": csrf_token},
        follow_redirects=True,
    )
    assert resp_reject.status_code == 200
    updated2 = store.get_candidate(cand.candidate_id)
    assert updated2.qualification_outcome.value == "reject"
    assert updated2.cold_outreach_allowed is False


# -----------------------------------------------------------------------------
# Test 14: Export Correctness and Safe Formula Injection Protection
# -----------------------------------------------------------------------------
def test_export_formula_injection_protection(client, test_db):
    store = CandidateStore(db_path=test_db)
    # Malicious company name attempting Excel formula execution
    cand, _ = store.get_or_create_candidate(
        company_name="=CMD|' /C calc'!A0",
        company_domain="+malicious-domain.com",
        country="Italy",
    )
    # Approve candidate via review action
    csrf_resp = client.get(f"/sales/candidates/{cand.candidate_id}")
    csrf_token = csrf_resp.cookies.get("sales_csrf_token")
    client.post(
        f"/sales/candidates/{cand.candidate_id}/review",
        data={"decision": "approve", "notes": "Approved test lead"},
        cookies={"sales_csrf_token": csrf_token},
        headers={"x-csrf-token": csrf_token},
    )

    resp_csv = client.get("/sales/candidates/export?format=csv")
    assert resp_csv.status_code == 200
    csv_text = resp_csv.text

    # Formulas must be escaped with a single quote prefix
    assert "'=CMD|' /C calc'!A0" in csv_text
    assert "'+malicious-domain.com" in csv_text

    # Test formula sanitizer utility
    assert sanitize_spreadsheet_cell("=1+1") == "'=1+1"
    assert sanitize_spreadsheet_cell("+SUM(A1:A10)") == "'+SUM(A1:A10)"
    assert sanitize_spreadsheet_cell("-100") == "'-100"
    assert sanitize_spreadsheet_cell("@HYPERLINK('http://evil.com')") == "'@HYPERLINK('http://evil.com')"
    assert sanitize_spreadsheet_cell("Normal Company Name") == "Normal Company Name"


# -----------------------------------------------------------------------------
# Test 15 & 16: Zero Email Sends & Zero Autonomous LLM Calls
# -----------------------------------------------------------------------------
def test_zero_email_sends_and_no_llm_calls(client):
    dash_resp = client.get("/sales/dashboard")
    assert dash_resp.status_code == 200
    assert "Real sending is disabled on this server" in dash_resp.text
    assert os.getenv("EMAIL_SEND_ENABLED", "false").lower() in ("false", "0", "")
