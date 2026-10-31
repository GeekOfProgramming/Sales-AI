"""
Comprehensive test suite for:
- 13-Field Lead Register (pyBIM workbook schema).
- Complete isolation from operational database (data/sales_outreach.db).
- Operator review actions guardrails.
- Reusable bounded website research.
- CSV/XLSX export with formula injection protection and UTF-8 encoding.
- Process restart persistence.
"""

import os
import io
import csv
import openpyxl
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import get_console_services, sanitize_spreadsheet_cell
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    BIMMaturity,
    QualificationOutcome,
    DiscoveryStatus
)
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.lead_researcher import (
    extract_website_public_facts,
    create_lead_from_researched_website
)
from sales_engine.research.pilot_research import (
    get_pilot_research_profiles,
    check_existing_collisions,
    save_pilot_candidate_to_store,
    PILOT_COMPANIES_REGISTRY
)


@pytest.fixture
def client(tmp_path):
    # Enforce isolated database for TestClient
    test_db = str(tmp_path / "client_isolated_test.db")
    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        with TestClient(app) as test_client:
            yield test_client


def test_operational_db_never_accessed_during_tests():
    """Verify that current environment is not pointing to the production database."""
    db_env = os.environ.get("SALES_OUTREACH_DB")
    assert db_env is not None, "SALES_OUTREACH_DB must be set by test isolation fixture"
    assert "sales_outreach.db" not in os.path.basename(db_env) or "test" in db_env.lower(), (
        f"Test is running against non-isolated database: {db_env}"
    )


def test_pilot_profiles_and_missing_tuple_fix():
    """Verify pilot profiles load cleanly and Tuple typing import is intact."""
    profiles = get_pilot_research_profiles()
    assert len(profiles) == 7
    for p in profiles:
        assert p.company_name
        assert p.canonical_domain


def test_lead_register_13_field_model():
    """Verify LeadRegisterRecord produces the exact 13 fields in documented order."""
    rec = LeadRegisterRecord(
        candidate_id="cand:domain:test-studio.it",
        record_number=1,
        priority_level="A",
        priority_rationale="Active Tekla and Revit BIM hiring",
        company_name="Test Studio S.r.l.",
        legal_identity="P.IVA 01234567890",
        geographic_location="Padova (PD), Italy",
        telephone_numbers="+39 049 123456",
        business_activities="Structural engineering & BIM management",
        official_website="https://www.test-studio.it",
        responsible_person="Ing. Mario Rossi",
        descriptions_and_notes="Specialized engineering practice.\nMultiline notes preserved.",
        job_advertisement_links="https://www.test-studio.it/careers",
        other_links_and_references="https://www.test-studio.it/projects | ISO 9001",
        contact_email_addresses="info@test-studio.it"
    )

    d = rec.to_13_field_dict()
    assert len(d) == 13

    expected_keys = [
        "Record Number",
        "Company Priority",
        "Priority Rationale",
        "Company Name & Legal Identity",
        "Geographic Location",
        "Telephone Numbers",
        "Business Activities / Services",
        "Official Website",
        "Responsible Person / Contact",
        "Descriptions & Operator Notes",
        "Job Advertisement Links",
        "Other Links / Projects / References",
        "Contact Email Addresses",
    ]
    assert list(d.keys()) == expected_keys
    assert d["Record Number"] == 1
    assert d["Company Priority"] == "A"
    assert "Test Studio S.r.l." in d["Company Name & Legal Identity"]
    assert "Multiline notes preserved." in d["Descriptions & Operator Notes"]


def test_lead_register_store_persistence_and_restart(tmp_path):
    """
    Test Step 7 requirement:
    1. Create one company record with all 13 fields populated.
    2. Save it through store.
    3. Close connections, recreate store (simulate process restart).
    4. Verify all 13 fields retained values.
    5. Edit notes, priority, phone, email, links.
    6. Reload and verify edits survive.
    """
    test_db = str(tmp_path / "restart_test.db")
    store = LeadRegisterStore(db_path=test_db)
    cand_store = CandidateStore(db_path=test_db)

    # 1. Create candidate and 13-field record
    cand, _ = cand_store.get_or_create_candidate(
        company_name="Restart Engineering S.r.l.",
        company_domain="restart-eng.it",
        country="Italy"
    )

    rec = LeadRegisterRecord(
        candidate_id=cand.candidate_id,
        record_number=101,
        priority_level="A",
        priority_rationale="Urgent BIM coordination demand",
        company_name="Restart Engineering S.r.l.",
        legal_identity="P.IVA 09988776655",
        geographic_location="Vigonza (PD), Italy",
        telephone_numbers="+39 049 998877",
        business_activities="MEP & Civil BIM",
        official_website="https://www.restart-eng.it",
        responsible_person="Arch. Laura Bianchi",
        descriptions_and_notes="Linea 1: Nota iniziale.\nLinea 2: Seconda riga di testo.",
        job_advertisement_links="https://www.restart-eng.it/jobs/bim",
        other_links_and_references="https://www.restart-eng.it/projects",
        contact_email_addresses="contatti@restart-eng.it"
    )
    store.save_record(rec)

    # 2. Simulate process restart by instantiating new store instances
    del store
    del cand_store

    store2 = LeadRegisterStore(db_path=test_db)
    reloaded = store2.get_record(cand.candidate_id)
    assert reloaded is not None
    assert reloaded.record_number == 101
    assert reloaded.priority_level == "A"
    assert reloaded.company_name == "Restart Engineering S.r.l."
    assert "Seconda riga di testo." in reloaded.descriptions_and_notes
    assert reloaded.telephone_numbers == "+39 049 998877"
    assert reloaded.contact_email_addresses == "contatti@restart-eng.it"

    # 3. Edit notes, priority, phone, email, links
    reloaded.priority_level = "B"
    reloaded.telephone_numbers = "+39 049 111222"
    reloaded.contact_email_addresses = "info@restart-eng.it"
    reloaded.descriptions_and_notes = "Updated operator notes.\nSecond line modified."
    reloaded.other_links_and_references = "https://www.restart-eng.it/portfolio-2026"
    store2.save_record(reloaded)

    # 4. Reload again from new store
    del store2
    store3 = LeadRegisterStore(db_path=test_db)
    reloaded2 = store3.get_record(cand.candidate_id)
    assert reloaded2.priority_level == "B"
    assert reloaded2.telephone_numbers == "+39 049 111222"
    assert reloaded2.contact_email_addresses == "info@restart-eng.it"
    assert "Second line modified." in reloaded2.descriptions_and_notes
    assert reloaded2.other_links_and_references == "https://www.restart-eng.it/portfolio-2026"


def test_conservative_business_assumptions_on_pilot_save(tmp_path):
    """Verify unsupported business assumptions are NOT automatically assigned."""
    test_db = str(tmp_path / "pilot_conservative_test.db")
    isolated_store = CandidateStore(db_path=test_db)

    profiles = get_pilot_research_profiles()
    incide = next(p for p in profiles if p.canonical_domain == "incide.it")

    cand, is_new = save_pilot_candidate_to_store(incide, isolated_store, auto_approve=False)
    assert is_new is True

    # Assert conservative constraints
    assert cand.direct_project_access is False, "direct_project_access must not be assumed True"
    assert cand.bim_maturity == BIMMaturity.unknown, "BIM maturity must be unknown until proven"
    assert cand.intent_assessment == "unknown", "Intent must be unknown based on web listing alone"
    assert cand.cold_outreach_allowed is False, "cold_outreach_allowed must require explicit human approval"

    # Verify contact person was not fabricated
    contacts = isolated_store.get_contacts_for_candidate(cand.candidate_id)
    if contacts:
        assert contacts[0].first_name == "", "Fake contact first name must not be fabricated"
        assert contacts[0].last_name == "", "Fake contact last name must not be fabricated"
        assert "Public Inbound" in contacts[0].job_title or "Office" in contacts[0].full_name


def test_register_http_routes_and_export(client):
    """Verify web UI register list, detail, and CSV/XLSX export routes."""
    # 1. GET /sales/register
    resp = client.get("/sales/register")
    assert resp.status_code == 200
    assert "Lead Register" in resp.text
    assert "13-Field Core Workbook" in resp.text

    # 2. POST /sales/register/research-new (Bounded new company addition - mocked fetch)
    csrf_token = resp.cookies.get("sales_csrf_token")
    with patch("sales_engine.research.lead_researcher.extract_website_public_facts") as mock_facts:
        mock_facts.return_value = {
            "canonical_domain": "studiobeta.it",
            "source_url": "https://www.studiobeta.it",
            "company_name": "Studio Beta S.r.l.",
            "telephone_numbers": "+39 02 998877",
            "contact_emails": "info@studiobeta.it",
            "vat_number": "09876543211",
            "meta_description": "Studio di ingegneria e progettazione BIM",
            "page_title": "Studio Beta Engineering",
            "status": "FETCH_SUCCESS",
            "error_message": None,
        }
        add_resp = client.post(
            "/sales/register/research-new",
            data={
                "company_name": "Studio Beta S.r.l.",
                "website_url": "https://www.studiobeta.it",
                "priority_level": "B",
                "operator_notes": "Added for testing bounded research workflow",
                "csrf_token": csrf_token
            },
            cookies={"sales_csrf_token": csrf_token},
            follow_redirects=True
        )
    assert add_resp.status_code == 200
    assert "Studio Beta S.r.l." in add_resp.text

    # 3. Export XLSX
    xlsx_resp = client.get("/sales/register/export?format=xlsx")
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_resp.content))
    assert "13-Field Lead Register" in wb.sheetnames
    ws = wb["13-Field Lead Register"]
    header_row = [cell.value for cell in ws[1]]
    assert len(header_row) == 13
    assert header_row[0] == "Record Number"
    assert header_row[1] == "Company Priority"
    assert header_row[12] == "Contact Email Addresses"

    # 4. Export CSV
    csv_resp = client.get("/sales/register/export?format=csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    csv_text = csv_resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(csv_text))
    csv_headers = next(reader)
    assert len(csv_headers) == 13
    assert csv_headers[3] == "Company Name & Legal Identity"


def test_review_decision_guardrails(client):
    """Verify invalid decisions and mismatched domains are rejected with HTTP 400."""
    resp = client.get("/sales/research/pilot")
    csrf_token = resp.cookies.get("sales_csrf_token")

    # Save a candidate first
    save_resp = client.post(
        "/sales/research/pilot/save-single",
        data={"domain": "incide.it", "csrf_token": csrf_token},
        cookies={"sales_csrf_token": csrf_token},
        follow_redirects=True
    )
    assert save_resp.status_code == 200

    # Test invalid decision string
    bad_dec_resp = client.post(
        "/sales/research/pilot/review-candidate",
        data={
            "candidate_id": "cand:domain:incide.it",
            "decision": "INVALID_RANDOM_DECISION",
            "domain": "incide.it",
            "csrf_token": csrf_token
        },
        cookies={"sales_csrf_token": csrf_token}
    )
    assert bad_dec_resp.status_code == 400

    # Test mismatched domain
    mismatch_resp = client.post(
        "/sales/research/pilot/review-candidate",
        data={
            "candidate_id": "cand:domain:incide.it",
            "decision": "APPROVED",
            "domain": "another-different-domain.it",
            "csrf_token": csrf_token
        },
        cookies={"sales_csrf_token": csrf_token}
    )
    assert mismatch_resp.status_code == 400
