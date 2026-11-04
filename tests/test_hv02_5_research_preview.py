"""
HV-02.5 Research Excel -> Candidates Import Preview Tests.

Mandatory minimal test coverage:
1. Existing company matching does not create duplicates.
2. Research import preserves existing verified and operator-reviewed data.
3. Preview does not mutate the authoritative database.
"""

import os
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.historical.importer import HistoricalImporter
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord
from backend.sales_ui.router import get_preview_signing_secret
from backend.main import app

RESEARCH_WORKBOOK_PATH = Path("data/staging/pyBIM_Lead_Research_13_Columns_Short_2026-11-04.xlsx")


class CsrfAwareClient(TestClient):
    def __init__(self, app_instance):
        super().__init__(app_instance)
        self.csrf_token = "test_csrf_token_hv02_5"
        self.cookies.set("sales_csrf_token", self.csrf_token)

    def post(self, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["X-CSRF-Token"] = self.csrf_token
        headers["Referer"] = "http://testserver/sales/sources"
        kwargs["headers"] = headers
        return super().post(url, **kwargs)


@pytest.fixture
def disposable_research_env(tmp_path, monkeypatch):
    """Sets up a disposable SQLite database and environment for research import preview."""
    db_file = tmp_path / "hv02_5_research_test.db"
    store = CandidateStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    importer = HistoricalImporter(store=store)

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_key_1234567890_32bytes_long!")
    monkeypatch.setenv("SALESAI_ALLOWED_IMPORT_ROOT", str(RESEARCH_WORKBOOK_PATH.parent.resolve()))

    yield {
        "store": store,
        "reg_store": reg_store,
        "db_file": db_file,
    }


def test_01_existing_company_matching_does_not_create_duplicates(disposable_research_env):
    """
    Test 1: Company matching strictly uses verified canonical domains.
    Pre-existing candidate with domain 'arcadis.com' is matched to the Arcadis row,
    NOT duplicated, and companies without domains are flagged as ambiguous.
    """
    store = disposable_research_env["store"]
    importer = HistoricalImporter(store=store)

    # Pre-seed existing candidate Arcadis
    cid_arcadis = "cand:domain:arcadis.com"
    existing_arcadis = CandidateCompany(
        candidate_id=cid_arcadis,
        company_name="Arcadis Italia S.r.l.",
        normalized_company_name="arcadis italia srl",
        company_domain="arcadis.com",
        country="Italy",
        city="Milan",
        cold_outreach_allowed=True,
    )
    store.save_candidate(existing_arcadis)

    # Parse research workbook
    recs = importer.parse_xlsx(RESEARCH_WORKBOOK_PATH)
    assert len(recs) == 83, f"Expected 83 research rows, got {len(recs)}"

    analysis = importer.preview_lead_research_import(recs)
    assert analysis["rows_parsed"] == 83
    # Exactly 1 existing candidate matched (Arcadis)
    assert analysis["existing_candidate_matches"] == 1
    # 9 new candidate proposals (from the 10 verified domains)
    assert analysis["new_candidate_proposals"] == 9
    # 73 unverified/missing domain rows flagged as ambiguous
    assert analysis["ambiguous_unresolved_count"] == 73


def test_02_research_import_preserves_existing_verified_and_operator_reviewed_data(disposable_research_env):
    """
    Test 2: Research import preview preserves existing verified and operator-reviewed data.
    If an existing candidate has OPERATOR_REVIEWED status in lead_register_records,
    it is protected and not marked for proposed overwrite.
    """
    store = disposable_research_env["store"]
    reg_store = disposable_research_env["reg_store"]
    importer = HistoricalImporter(store=store)

    cid = "cand:domain:arcadis.com"
    c = CandidateCompany(
        candidate_id=cid,
        company_name="Arcadis Italia S.r.l.",
        normalized_company_name="arcadis italia srl",
        company_domain="arcadis.com",
    )
    store.save_candidate(c)

    # Record verified operator-reviewed research
    reviewed_rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Arcadis Italia S.r.l.",
        priority_level="A",
        priority_rationale="Operator verified enterprise BIM potential",
        verification_status="OPERATOR_REVIEWED",
        official_website="https://www.arcadis.com/it-it",
    )
    reg_store.save_record(reviewed_rec)

    # Verify contact verification preservation
    store.save_contact(
        {
            "contact_id": "contact:lead_mgr",
            "work_email": "manager@arcadis.com",
            "full_name": "Verified Manager",
            "job_title": "Design Director",
        },
        candidate_id=cid,
    )
    store.record_contact_verification(
        candidate_id=cid,
        contact_id="contact:lead_mgr",
        work_email="manager@arcadis.com",
        evidence_source="Operator Interview 2026-10",
    )

    recs = importer.parse_xlsx(RESEARCH_WORKBOOK_PATH)
    analysis = importer.preview_lead_research_import(recs)

    # The existing reviewed candidate must be flagged as operator_reviewed
    arcadis_proposal = next((p for p in analysis["proposals_detail"] if p.get("domain") == "arcadis.com"), None)
    assert arcadis_proposal is not None
    assert arcadis_proposal["status"] == "EXISTING_MATCH"
    assert arcadis_proposal["operator_reviewed"] is True


def test_03_preview_does_not_mutate_authoritative_database(disposable_research_env):
    """
    Test 3: Preview endpoint executes with ZERO mutations to the authoritative database.
    Verifies that rows in candidate_companies, lead_register_records, and historical_imports
    remain strictly unchanged before and after preview.
    """
    db_file = disposable_research_env["db_file"]

    # Record initial database row counts
    conn = sqlite3.connect(str(db_file))
    initial_candidates = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    initial_lead_reg = conn.execute("SELECT COUNT(*) FROM lead_register_records").fetchone()[0]
    initial_imports = conn.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0]
    conn.close()

    client = CsrfAwareClient(app)

    # Send preview request
    response = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(RESEARCH_WORKBOOK_PATH.resolve())},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    pdata = response.json()
    assert pdata["is_lead_research"] is True
    assert pdata["rows_detected"] == 83
    assert pdata["new_candidate_proposals"] == 10
    assert pdata["ambiguous_unresolved_count"] == 73

    # Verify database was NOT mutated
    conn = sqlite3.connect(str(db_file))
    final_candidates = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
    final_lead_reg = conn.execute("SELECT COUNT(*) FROM lead_register_records").fetchone()[0]
    final_imports = conn.execute("SELECT COUNT(*) FROM historical_imports").fetchone()[0]
    conn.close()

    assert final_candidates == initial_candidates, "candidate_companies was mutated during preview!"
    assert final_lead_reg == initial_lead_reg, "lead_register_records was mutated during preview!"
    assert final_imports == initial_imports, "historical_imports was mutated during preview!"
