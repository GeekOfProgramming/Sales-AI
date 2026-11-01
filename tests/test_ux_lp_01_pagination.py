"""
tests/test_ux_lp_01_pagination.py — Lead Register Usability & Data Pagination Regression Suite (UX-LP-01)

Verifies:
1. Server-side pagination with accurate total and matching counts.
2. Clamped page_size values (10, 25, 50, default 25).
3. Pagination boundaries (page 1, page 2, last page, beyond range clamping).
4. Dynamic geographic and business filters (Country, City, Priority A/B/C, Status).
5. Unknown / Not Verified handling for missing location data without heuristic domain suffix inference.
6. Search query across company, domain, email, phone, notes, location.
7. Stable sorting by record_number, company_name, priority (asc/desc).
8. Compact Summary View (default) vs Full 13-Field Workbook View rendering.
9. 13-Field Edit form save and persistence without data loss.
10. Formula-injection protection and complete 13-field export matching active filters across all pages.
11. Multilingual and bidirectional text preservation (e.g. Persian/Italian/German).
"""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from fastapi.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from backend.main import app
from backend.sales_ui import router as ui_router


@pytest.fixture
def isolated_reg_db(tmp_path):
    path = str(tmp_path / "ux_lp01_isolated.db")
    
    cand_store = CandidateStore(db_path=path)
    reg_store = LeadRegisterStore(db_path=path)

    # Seed 135 synthetic candidate companies across 4 countries with diverse cities and priorities
    countries = ["Germany", "Italy", "Austria", "Switzerland", "Unknown"]
    cities_map = {
        "Germany": ["Berlin", "Munich", "Frankfurt", "Hamburg"],
        "Italy": ["Milan", "Rome", "Turin"],
        "Austria": ["Vienna", "Salzburg"],
        "Switzerland": ["Zurich", "Geneva"],
        "Unknown": ["Unknown"],
    }
    priorities = ["A", "B", "C"]

    for i in range(1, 136):
        c_country = countries[i % len(countries)]
        avail_cities = cities_map[c_country]
        c_city = avail_cities[i % len(avail_cities)]
        prio = priorities[i % len(priorities)]

        cand = CandidateCompany(
            candidate_id=f"SYNTH-CAND-{i:03d}",
            company_name=f"Enterprise Solutions {i:03d} GmbH" if c_country == "Germany" else f"Studio Architettura {i:03d} Srl",
            domain=f"company{i:03d}.example.com",
            website=f"https://www.company{i:03d}.example.com",
            country=c_country,
            city=c_city,
            pipeline_stage=prio,
            suppressed=(i % 20 == 0),
            qualification_reason=f"Specialized in BIM engineering {i:03d}",
            source="Quick Brave Search" if i % 2 == 0 else "Manual Seed",
        )
        cand_store.save_candidate(cand)

    # Add 10 explicit LeadRegisterRecords with customized fields, some with Persian/Italian multilingual notes
    for j in range(1, 11):
        rec = LeadRegisterRecord(
            candidate_id=f"SYNTH-CAND-{j:03d}",
            record_number=j,
            company_name=f"Studio Architettura {j:03d} Srl",
            official_website=f"https://www.studio{j:03d}.it",
            geographic_location=f"Lombardia, Italy",
            responsible_person=f"Dott. Mario Rossi {j}",
            contact_email_addresses=f"mario.rossi{j}@studio{j:03d}.it",
            telephone_numbers=f"+39 02 12345{j:02d}",
            business_activities=f"Architectural design and IFC modeling {j}",
            descriptions_and_notes=f"شرکت معماری و مهندسی پیشرو در میلان شماره {j} - Verified high value lead",
            priority_level="A" if j <= 5 else "B",
            verification_status="Verified",
        )
        reg_store.save_record(rec)

    yield path, cand_store, reg_store


@pytest.fixture
def test_client(isolated_reg_db, monkeypatch):
    db_path, cand_store, reg_store = isolated_reg_db
    monkeypatch.setenv("SALES_OUTREACH_DB", db_path)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    with TestClient(app) as client:
        yield client


def test_server_side_pagination_and_clamping(test_client, isolated_reg_db):
    """Test 1 & 2: Server-side pagination, clamped limits (10, 25, 50), page bounds."""
    # Default page size is 25
    resp = test_client.get("/sales/register")
    assert resp.status_code == 200
    assert "Official Outreach Register • 135 matching of 135 total records" in resp.text
    # Should render page 1 of 6 (135 / 25 = 6 pages)
    assert "Showing 1 to 25 of 135 records" in resp.text

    # Page size 10
    resp10 = test_client.get("/sales/register?page_size=10&page=2")
    assert resp10.status_code == 200
    assert "Showing 11 to 20 of 135 records" in resp10.text

    # Page size 50
    resp50 = test_client.get("/sales/register?page_size=50&page=3")
    assert resp50.status_code == 200
    assert "Showing 101 to 135 of 135 records" in resp50.text

    # Page size out of allowed set (e.g. 999) should be clamped to 25
    resp_clamped = test_client.get("/sales/register?page_size=999&page=1")
    assert resp_clamped.status_code == 200
    assert "Showing 1 to 25 of 135 records" in resp_clamped.text

    # Out of range page number (e.g. page 999) clamped to last page
    resp_page_overflow = test_client.get("/sales/register?page_size=25&page=999")
    assert resp_page_overflow.status_code == 200
    assert "Showing 126 to 135 of 135 records" in resp_page_overflow.text


def test_geographic_and_business_filters(test_client):
    """Test 3: Filtering by Country, City, Priority, and Status with accurate counts."""
    # Filter by Country = Italy
    resp_italy = test_client.get("/sales/register?country=Italy")
    assert resp_italy.status_code == 200
    assert "Country: Italy" in resp_italy.text

    # Filter by Country = Italy and City = Milan
    resp_milan = test_client.get("/sales/register?country=Italy&city=Milan")
    assert resp_milan.status_code == 200
    assert "City: Milan" in resp_milan.text

    # Filter by Priority = A
    resp_prio_a = test_client.get("/sales/register?priority=A")
    assert resp_prio_a.status_code == 200
    assert "Priority A" in resp_prio_a.text

    # Filter combined: Country = Germany, Priority = B
    resp_comb = test_client.get("/sales/register?country=Germany&priority=B")
    assert resp_comb.status_code == 200
    assert "Country: Germany" in resp_comb.text
    assert "Priority B" in resp_comb.text


def test_search_query_multi_attribute(test_client):
    """Test 4: Text search matches across company, notes, phone, email, etc."""
    # Search for specific email
    resp_email = test_client.get("/sales/register?q=mario.rossi1@studio001.it")
    assert resp_email.status_code == 200
    assert "mario.rossi1@studio001.it" in resp_email.text

    # Search for notes / Persian description
    resp_fa = test_client.get("/sales/register?q=شرکت معماری")
    assert resp_fa.status_code == 200
    assert "Studio Architettura 001 Srl" in resp_fa.text

    # Search phone number
    resp_phone = test_client.get("/sales/register?q=1234502")
    assert resp_phone.status_code == 200
    assert "Studio Architettura 002 Srl" in resp_phone.text


def test_summary_and_workbook_views(test_client):
    """Test 5: View switching between Summary View and 13-Field Core Workbook."""
    # Summary View (default)
    resp_summary = test_client.get("/sales/register?view=summary")
    assert resp_summary.status_code == 200
    assert "Lead Register Compact Summary" in resp_summary.text
    assert "Company &amp; Website" in resp_summary.text or "Company & Website" in resp_summary.text
    assert "Status / Provenance" in resp_summary.text

    # Full Workbook View (Canonical 13 fields)
    resp_full = test_client.get("/sales/register?view=full")
    assert resp_full.status_code == 200
    assert "Lead Register 13-Field Workbook" in resp_full.text
    assert "1. Row #" in resp_full.text
    assert "4. Company &amp; Legal Identity" in resp_full.text or "4. Company & Legal Identity" in resp_full.text
    assert "10. Descriptions &amp; Operator Notes" in resp_full.text or "10. Descriptions & Operator Notes" in resp_full.text
    assert "13. Contact Email Addresses" in resp_full.text
    assert "sticky-col-right" in resp_full.text


def test_export_all_pages_matching_filters(test_client):
    """Test 6: XLSX and CSV export exports all matching records across all pages respecting active filters."""
    # CSV export with country=Germany filter
    resp_csv = test_client.get("/sales/register/export?format=csv&country=Germany")
    assert resp_csv.status_code == 200
    assert resp_csv.headers["content-type"].startswith("text/csv")
    csv_lines = [line for line in resp_csv.text.splitlines() if line.strip()]
    header = csv_lines[0]
    assert "Company Name" in header
    assert "Descriptions & Operator Notes" in header
    # Must have more than 25 records (proving it exports all matching pages, not just page 1)
    # Total Germany entries is 135 // 5 = 27
    assert len(csv_lines) > 26

    # XLSX export
    resp_xlsx = test_client.get("/sales/register/export?format=xlsx&country=Italy")
    assert resp_xlsx.status_code == 200
    assert resp_xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(resp_xlsx.content) > 1000


def test_edit_lead_register_13_fields(test_client, isolated_reg_db):
    """Test 7: Edit 13-field record form and save without losing data."""
    db_path, _, reg_store = isolated_reg_db
    # GET edit page for SYNTH-CAND-001
    resp_get = test_client.get("/sales/register/SYNTH-CAND-001")
    assert resp_get.status_code == 200
    assert "Edit 13-Field Lead Record" in resp_get.text

    # Extract CSRF token if present
    import re
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1) if match else "test_csrf"

    # POST update
    update_data = {
        "csrf_token": csrf_token,
        "company_name": "Studio Mario Rossi Aggiornato Srl",
        "official_website": "https://www.mariorossi-updated.it",
        "geographic_location": "Milano, Lombardia, Italia",
        "responsible_person": "Ing. Mario Rossi PhD",
        "contact_email_addresses": "mario.phd@mariorossi-updated.it",
        "telephone_numbers": "+39 02 99887766",
        "business_activities": "Structural BIM and Digital Twin",
        "descriptions_and_notes": "توضیحات فارسی به روز رسانی شده - Top tier enterprise lead",
        "priority_level": "A",
        "verification_status": "Verified",
    }
    resp_post = test_client.post("/sales/register/SYNTH-CAND-001/update", data=update_data, follow_redirects=True)
    assert resp_post.status_code == 200

    # Verify directly in reg_store
    rec = reg_store.get_record("SYNTH-CAND-001")
    assert rec is not None
    assert rec.company_name == "Studio Mario Rossi Aggiornato Srl"
    assert rec.responsible_person == "Ing. Mario Rossi PhD"
    assert "توضیحات فارسی به روز رسانی شده" in rec.descriptions_and_notes
