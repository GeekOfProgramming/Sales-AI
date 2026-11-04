"""
HV-02.6 Complete Automatic Research Candidate Import Tests.

Minimal focused test coverage:
1. Existing company matching (matches existing verified domains or company names without duplicating).
2. New candidate without verified domain (creates candidate with Needs Research status and NO invented domain).
3. Idempotent import preserving existing data (repeated imports do not duplicate candidates or mutate reviewed data).
"""

import os
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.lifecycle_service import project_candidate_lifecycle, CandidateQueueType
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.historical.importer import HistoricalImporter
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord
from backend.sales_ui.router import get_preview_signing_secret
from backend.main import app

RESEARCH_WORKBOOK_PATH = Path("data/staging/pyBIM_Lead_Research_13_Columns_Short_2026-11-04.xlsx")


class CsrfAwareClient(TestClient):
    def __init__(self, app_instance):
        super().__init__(app_instance)
        self.csrf_token = "test_csrf_token_hv02_6"
        self.cookies.set("sales_csrf_token", self.csrf_token)

    def post(self, url, **kwargs):
        headers = kwargs.get("headers", {})
        headers["X-CSRF-Token"] = self.csrf_token
        headers["Referer"] = "http://testserver/sales/sources"
        kwargs["headers"] = headers
        return super().post(url, **kwargs)


@pytest.fixture
def disposable_env(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging_root"
    staging_root.mkdir(parents=True, exist_ok=True)
    db_file = tmp_path / "hv02_6_test.db"

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
        "importer": importer,
        "db_file": db_file,
    }


def test_01_existing_company_matching_prevents_duplicate_candidates(disposable_env):
    """
    Test 1: Existing company matching.
    Matches existing candidates using reliable canonical domain or reliable company identity evidence.
    Does NOT create duplicate candidate rows.
    """
    store = disposable_env["store"]
    importer = disposable_env["importer"]

    # Pre-seed existing candidate: Arcadis with domain arcadis.com
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

    # Pre-seed existing candidate without domain: 3TI Progetti
    cid_3ti = "cand:name:3tiprogetti:it"
    existing_3ti = CandidateCompany(
        candidate_id=cid_3ti,
        company_name="3TI Progetti",
        normalized_company_name="3ti progetti",
        company_domain=None,
        country="Italy",
        city="Roma",
        discovery_status=DiscoveryStatus.researching,
    )
    store.save_candidate(existing_3ti)

    recs = importer.parse_xlsx(RESEARCH_WORKBOOK_PATH)
    res = importer.import_lead_research_records(recs)

    # 2 existing companies matched and updated (Arcadis by domain, 3TI Progetti by name)
    assert res.companies_updated == 2
    # 83 - 2 = 81 new companies created
    assert res.companies_created == 81

    # Verify Arcadis was updated without altering its stable candidate_id
    arcadis_after = store.get_by_id(cid_arcadis)
    assert arcadis_after is not None
    assert arcadis_after.candidate_id == cid_arcadis
    assert "Arcadis" in arcadis_after.aliases

    # Verify 3TI Progetti was updated without creating a duplicate candidate
    all_3ti = [c for c in store.list_candidates() if c.normalized_company_name == "3ti progetti"]
    assert len(all_3ti) == 1
    assert all_3ti[0].candidate_id == cid_3ti


def test_02_new_candidate_without_verified_domain_created_with_needs_research(disposable_env):
    """
    Test 2: New candidate without verified domain.
    Creates CandidateCompany with 'Needs Research' subqueue status.
    NEVER invents or assigns an unverified domain (leaves company_domain=None).
    Attaches original research row in DiscoveryObservation and LeadRegisterRecord.
    """
    store = disposable_env["store"]
    reg_store = disposable_env["reg_store"]
    importer = disposable_env["importer"]

    recs = importer.parse_xlsx(RESEARCH_WORKBOOK_PATH)
    # Find a record without verified domain, e.g. 3TI Progetti or ACPV ARCHITECTS
    unverified_rec = next((r for r in recs if r.company_name == "3TI Progetti"), None)
    assert unverified_rec is not None
    assert unverified_rec.domain is None

    res = importer.import_lead_research_records([unverified_rec])
    assert res.companies_created == 1

    expected_cid = store.generate_company_id("3TI Progetti", None, "it")
    cand = store.get_by_id(expected_cid)
    assert cand is not None
    assert cand.company_name == "3TI Progetti"
    assert cand.company_domain is None, "Must never invent or assign an unverified domain"
    assert cand.discovery_status == DiscoveryStatus.researching

    # Verify lifecycle subqueue is Needs Research
    lifecycle_state = project_candidate_lifecycle(cand)
    assert lifecycle_state.active_subqueue == CandidateQueueType.NEEDS_RESEARCH
    assert lifecycle_state.is_active_work is True

    # Verify LeadRegisterRecord was attached
    lr_rec = reg_store.get_record(expected_cid)
    assert lr_rec is not None
    assert lr_rec.company_name == "3TI Progetti"
    assert lr_rec.priority_level == unverified_rec.tier_hint
    assert lr_rec.verification_status == "Unverified"

    # Verify original raw payload attached in DiscoveryObservation
    obs_list = store.get_observations(expected_cid)
    assert len(obs_list) == 1
    assert obs_list[0].raw_payload.get("نام و عنوان") == "3TI Progetti"


def test_03_idempotent_import_preserving_existing_and_operator_reviewed_data(disposable_env):
    """
    Test 3: Idempotent import preserving existing data.
    - Repeated imports do NOT create duplicate candidate companies or lead register records.
    - Pre-existing operator-reviewed data is preserved verbatim and not downgraded.
    - Zero historical outreach events are fabricated.
    """
    store = disposable_env["store"]
    reg_store = disposable_env["reg_store"]
    importer = disposable_env["importer"]
    db_file = disposable_env["db_file"]

    # Pre-seed operator reviewed candidate
    cid_arcadis = "cand:domain:arcadis.com"
    reviewed_arcadis = LeadRegisterRecord(
        candidate_id=cid_arcadis,
        company_name="Arcadis (Reviewed Firm)",
        priority_level="A",
        priority_rationale="Operator verified priority rationale",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reviewed_arcadis)

    recs = importer.parse_xlsx(RESEARCH_WORKBOOK_PATH)

    # First import batch
    res1 = importer.import_lead_research_records(recs)
    assert res1.companies_created == 83
    assert res1.companies_updated == 0

    cands_after_1 = len(store.list_candidates())
    assert cands_after_1 == 83

    # Second import batch (repeated Preview -> Confirm simulation)
    res2 = importer.import_lead_research_records(recs)
    assert res2.companies_created == 0, "Repeated import must create 0 new companies"
    assert res2.companies_updated == 83, "Repeated import updates existing companies idempotently"

    cands_after_2 = len(store.list_candidates())
    assert cands_after_2 == cands_after_1, f"Candidate count changed on second import! {cands_after_1} vs {cands_after_2}"

    # Verify OPERATOR_REVIEWED data was strictly preserved
    arcadis_check = reg_store.get_record(cid_arcadis)
    assert arcadis_check.verification_status == "OPERATOR_REVIEWED"
    assert arcadis_check.company_name == "Arcadis (Reviewed Firm)"
    assert arcadis_check.priority_rationale == "Operator verified priority rationale"

    # Verify ZERO outreach events fabricated
    conn = sqlite3.connect(str(db_file))
    outreach_events_count = conn.execute("SELECT COUNT(*) FROM historical_outreach_events").fetchone()[0]
    conn.close()
    assert outreach_events_count == 0, "Research import must NEVER create historical outreach events"
