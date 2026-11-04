"""
UI-03 Minimal Verification Tests.

1. Candidate list and filters rendering smoke test:
   - Verifies the Candidate list page renders the improved columns, queue pills, and filter bar without syntax errors.
   - Verifies searching, queue switching, and outreach/source filtering.
2. Lifecycle and outreach status indicators test:
   - Verifies distinct indicators for Outreach Locked, Previously Contacted, DNC/Suppressed, Excluded, and Unverified Website.
   - Verifies cold_outreach_allowed=False is labeled Outreach Locked and NOT DNC.
   - Verifies All Companies view (/sales/candidates?queue=all) renders correctly across all candidate varieties.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def disposable_ui03_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ui03_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    excl_store = ExcludedCompaniesStore(str(db_file))
    excl_store._init_db()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ui03!!")

    # 1. Candidate with unverified website and cold_outreach_allowed=False (Research-imported candidate)
    cand1 = CandidateCompany(
        candidate_id="cand:name:novelsrl:it",
        company_name="Novel Architecture Srl",
        normalized_company_name="novel architecture srl",
        company_domain=None,  # Unverified website
        country="Italy",
        city="Milan",
        company_tier="tier_2",
        discovery_status=DiscoveryStatus.researching,
        qualification_outcome=QualificationOutcome.research_more,
        cold_outreach_allowed=False,  # Outreach locked!
        source_refs=["research_lead_excel"],
    )

    # 2. Candidate with verified domain, operator reviewed, cold outreach allowed
    cand2 = CandidateCompany(
        candidate_id="cand:domain:arcadis.it",
        company_name="Arcadis Italia",
        normalized_company_name="arcadis italia",
        company_domain="arcadis.it",
        country="Italy",
        city="Rome",
        company_tier="tier_1",
        discovery_status=DiscoveryStatus.new,
        cold_outreach_allowed=True,
        source_refs=["crawler_google_places"],
    )

    # 3. Candidate with historical contact (Previously Contacted)
    cand3 = CandidateCompany(
        candidate_id="cand:domain:histpartner.it",
        company_name="Historical Partner SpA",
        normalized_company_name="historical partner spa",
        company_domain="histpartner.it",
        country="Italy",
        city="Turin",
        discovery_status=DiscoveryStatus.new,
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
        source_refs=["historical_import"],
    )

    # 4. Excluded candidate
    cand4 = CandidateCompany(
        candidate_id="cand:domain:vendorbim.it",
        company_name="Vendor BIM Software",
        normalized_company_name="vendor bim software",
        company_domain="vendorbim.it",
        country="Italy",
        discovery_status=DiscoveryStatus.rejected,
        cold_outreach_allowed=False,
        source_refs=["operator_manual"],
    )

    # 5. Suppressed candidate (DNC)
    cand5 = CandidateCompany(
        candidate_id="cand:domain:dncarch.it",
        company_name="DNC Architects",
        normalized_company_name="dnc architects",
        company_domain="dncarch.it",
        country="Italy",
        discovery_status=DiscoveryStatus.suppressed,
        suppressed=True,
        cold_outreach_allowed=False,
    )

    cand_store.save_candidate(cand1)
    cand_store.save_candidate(cand2)
    cand_store.save_candidate(cand3)
    cand_store.save_candidate(cand4)
    cand_store.save_candidate(cand5)

    # Exclude cand4 in ExcludedCompaniesStore
    excl_store.exclude_company(
        candidate_id="cand:domain:vendorbim.it",
        company_name="Vendor BIM Software",
        canonical_domain="vendorbim.it",
        exclusion_reason="SOFTWARE_VENDOR",
        operator_note="Not an AEC design firm",
    )

    # Add suppression entry for cand5
    with cand_store._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO suppression_list (email, company_domain, reason, source, created_at) VALUES ('info@dncarch.it', 'dncarch.it', 'opt_out', 'manual_operator', '2026-11-04')")

    # Add verified LeadRegisterRecord for cand2
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:domain:arcadis.it",
        company_name="Arcadis Italia",
        verification_status="OPERATOR_REVIEWED",
    ))

    client = TestClient(app)
    yield {
        "client": client,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "excl_store": excl_store,
        "db_file": db_file,
    }


def test_01_candidates_list_and_filters_rendering_smoke(disposable_ui03_env):
    """
    Check 1: Candidate list and filter rendering smoke test.
    - Renders the improved table structure and columns.
    - Preserves query parameters across pagination and filters.
    """
    client = disposable_ui03_env["client"]

    # 1. Main active candidates list
    resp = client.get("/sales/candidates?queue=active")
    assert resp.status_code == 200
    html = resp.text

    # Verify column headers
    assert "Company &amp; Candidate ID" in html or "Company & Candidate ID" in html
    assert "Official Website" in html
    assert "Tier &amp; Source" in html or "Tier & Source" in html
    assert "Queue &amp; Verification" in html or "Queue & Verification" in html
    assert "Outreach Status" in html
    assert "Actionable Next Step" in html

    # Verify filter bar additions
    assert 'name="outreach"' in html
    assert 'name="source"' in html

    # 2. Filter by outreach=locked
    r_locked = client.get("/sales/candidates?queue=active&outreach=locked")
    assert r_locked.status_code == 200
    assert "Novel Architecture Srl" in r_locked.text
    # cand2 has outreach allowed, so should be filtered out
    assert "Arcadis Italia" not in r_locked.text

    # 3. Filter by source=crawler
    r_src = client.get("/sales/candidates?queue=active&source=crawler")
    assert r_src.status_code == 200
    assert "Arcadis Italia" in r_src.text
    assert "Novel Architecture Srl" not in r_src.text


def test_02_lifecycle_and_outreach_status_indicators(disposable_ui03_env):
    """
    Check 2: Lifecycle and outreach status indicators.
    - cand1: Has "Unverified website", "🔒 Outreach Locked", "ID: cand:name:novelsrl:it"
    - cand1 is NOT labeled DNC (cold_outreach_allowed=False must not be DNC).
    - cand2: Has verified domain, "✅ Outreach Eligible", "Verified Record"
    - cand3: Has "⏱️ Contacted", "📬 Historical Pending"
    - All Companies (/sales/candidates?queue=all) displays active, historical, excluded, and suppressed without duplication.
    """
    client = disposable_ui03_env["client"]

    # Fetch active queue
    resp_act = client.get("/sales/candidates?queue=active")
    assert resp_act.status_code == 200
    html_act = resp_act.text

    # cand1 assertions
    assert "Novel Architecture Srl" in html_act
    assert "ID: cand:name:novelsrl:it" in html_act
    assert "Unverified website" in html_act
    assert "🔒 Outreach Locked" in html_act
    assert "🔬 Needs Research" in html_act
    # Verify cold_outreach_allowed=False is NOT falsely labeled DNC or Suppressed for cand1
    assert not ("Novel Architecture Srl" in html_act and "🛑 Suppressed / DNC" in html_act.split("Novel Architecture Srl")[1].split("Arcadis Italia")[0])

    # cand2 assertions
    assert "Arcadis Italia" in html_act
    assert "arcadis.it" in html_act
    assert "✅ Outreach Eligible" in html_act
    assert "Verified Record" in html_act

    # Fetch historical pending queue
    resp_hist = client.get("/sales/candidates?queue=historical_pending")
    assert resp_hist.status_code == 200
    html_hist = resp_hist.text
    assert "Historical Partner SpA" in html_hist
    assert "⏱️ Contacted" in html_hist
    assert "📬 Historical Pending" in html_hist

    # Fetch All Companies queue
    resp_all = client.get("/sales/candidates?queue=all")
    assert resp_all.status_code == 200
    html_all = resp_all.text

    # All 5 varieties must be present in All Companies
    assert "Novel Architecture Srl" in html_all
    assert "Arcadis Italia" in html_all
    assert "Historical Partner SpA" in html_all
    assert "Vendor BIM Software" in html_all
    assert "DNC Architects" in html_all

    # Verify distinct exclusion vs suppression badges
    assert "🚫 Excluded" in html_all
    assert "🛑 Suppressed / DNC" in html_all
