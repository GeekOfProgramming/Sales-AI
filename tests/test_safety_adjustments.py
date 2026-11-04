"""
Focused tests for Research Import Final Safety Adjustment.

Covers:
1. Company identity matching:
   - Does not merge based solely on exact normalized name when multiple candidates exist or verified domains conflict.
   - Returns ambiguous identity result for operator review instead.
   - Never overwrites an existing verified domain based on an uncertain match.
   - Preview and Confirm matching decisions are consistent.

2. Outreach eligibility:
   - All newly imported research-only Candidates start with cold_outreach_allowed=False.
   - Preserves genuine DNC and exclusion restrictions.
   - Research import never automatically authorizes sending.
"""

import pytest
from pathlib import Path

from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.models import DiscoveryStatus, QualificationOutcome
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord
from sales_engine.leads.register_store import LeadRegisterStore

RESEARCH_WORKBOOK_PATH = Path("data/staging/pyBIM_Lead_Research_13_Columns_Short_2026-11-04.xlsx")


@pytest.fixture
def disposable_env(tmp_path, monkeypatch):
    db_file = tmp_path / "safety_adjust_test.db"
    store = CandidateStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    importer = HistoricalImporter(store=store)

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    yield {
        "store": store,
        "reg_store": reg_store,
        "importer": importer,
        "db_file": db_file,
    }


def test_01_company_identity_matching_ambiguity_and_domain_conflict(disposable_env):
    """
    Test 1: Company identity matching safety.
    - Multiple existing candidates with same normalized name -> ambiguous, quarantined for operator review.
    - Domain conflict (row domain != candidate verified domain) -> ambiguous, quarantined for operator review.
    - Does not overwrite existing verified domain on uncertain match.
    - Preview and confirm matching decisions are consistent.
    """
    store = disposable_env["store"]
    importer = disposable_env["importer"]

    # Seed 2 candidates with the same normalized name but different candidate_ids
    cand1 = CandidateCompany(
        candidate_id="cand:dup1",
        company_name="Alpha Engineering",
        normalized_company_name="alpha engineering",
        company_domain="alpha-north.it",
        country="Italy",
        discovery_status=DiscoveryStatus.researching,
        cold_outreach_allowed=False,
    )
    cand2 = CandidateCompany(
        candidate_id="cand:dup2",
        company_name="Alpha Engineering",
        normalized_company_name="alpha engineering",
        company_domain="alpha-south.it",
        country="Italy",
        discovery_status=DiscoveryStatus.researching,
        cold_outreach_allowed=False,
    )
    store.save_candidate(cand1)
    store.save_candidate(cand2)

    # Seed 1 candidate with a verified domain to test domain conflict
    cand_conflict = CandidateCompany(
        candidate_id="cand:beta",
        company_name="Beta Architects",
        normalized_company_name="beta architects",
        company_domain="beta-verified.it",
        country="Italy",
        discovery_status=DiscoveryStatus.researching,
        cold_outreach_allowed=False,
    )
    store.save_candidate(cand_conflict)

    # Record A: matches normalized name "Alpha Engineering" without domain, but 2 candidates exist
    rec_mult = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Research",
        row_number=1,
        company_name="Alpha Engineering",
        domain=None,
        tier_hint="B",
        notes="Multiple candidates match",
        source_url=None,
        contact_name=None,
        contact_role=None,
        country="Italy",
    )

    # Record B: matches name "Beta Architects", but brings a conflicting domain "beta-new.it"
    rec_conf = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Research",
        row_number=2,
        company_name="Beta Architects",
        domain="beta-new.it",
        tier_hint="A",
        notes="Conflicting domain provided",
        source_url=None,
        contact_name=None,
        contact_role=None,
        country="Italy",
    )

    # 1. Preview checks
    preview_res = importer.preview_lead_research_import([rec_mult, rec_conf])
    assert preview_res["ambiguous_unresolved_count"] == 2
    assert preview_res["existing_candidate_matches"] == 0
    assert preview_res["new_candidate_proposals"] == 0

    reasons = [p["reason"] for p in preview_res["proposals_detail"] if p.get("status") == "AMBIGUOUS_UNRESOLVED"]
    assert any("Multiple existing candidates" in r for r in reasons)
    assert any("Domain conflict" in r for r in reasons)

    # 2. Confirm checks (import_lead_research_records)
    import_res = importer.import_lead_research_records([rec_mult, rec_conf])
    assert import_res.companies_created == 0
    assert import_res.companies_updated == 0
    assert import_res.duplicates_skipped == 2

    # Verify existing verified domain was NOT overwritten or corrupted
    beta_after = store.get_by_id("cand:beta")
    assert beta_after.company_domain == "beta-verified.it"


def test_02_outreach_eligibility_cold_outreach_false_and_exclusions(disposable_env):
    """
    Test 2: Outreach eligibility safety.
    - All newly imported research-only Candidates start with cold_outreach_allowed=False.
    - Active exclusions/DNC are strictly preserved and skipped.
    - Research import never automatically authorizes sending.
    """
    store = disposable_env["store"]
    importer = disposable_env["importer"]
    db_file = disposable_env["db_file"]

    # Register an exclusion for an excluded company
    excl_store = ExcludedCompaniesStore(str(db_file))
    excl_store._init_db()
    excl_store.exclude_company(
        canonical_domain="excluded-firm.it",
        company_name="Excluded Firm Srl",
        exclusion_reason="MANUAL_EXCLUSION",
        operator_note="DNC Operator Restriction",
    )

    rec_normal = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Research",
        row_number=1,
        company_name="Novel BIM Studio",
        domain="novel-bim.it",
        tier_hint="B",
        notes="Solid BIM portfolio",
        source_url="https://novel-bim.it",
        contact_name="Marco Rossi",
        contact_role="BIM Director",
        country="Italy",
        priority_reason="Solid BIM portfolio",
    )
    rec_unverified = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Research",
        row_number=2,
        company_name="Novel NoDomain Studio",
        domain=None,
        tier_hint="C",
        notes="Unverified web",
        source_url=None,
        contact_name=None,
        contact_role=None,
        country="Italy",
        priority_reason="Unverified web",
    )
    rec_excluded = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Research",
        row_number=3,
        company_name="Excluded Firm Srl",
        domain="excluded-firm.it",
        tier_hint="C",
        notes="Excluded",
        source_url=None,
        contact_name=None,
        contact_role=None,
        country="Italy",
    )

    # 1. Preview should exclude rec_excluded
    prev = importer.preview_lead_research_import([rec_normal, rec_unverified, rec_excluded])
    assert prev["excluded_dnc_count"] == 1
    assert prev["new_candidate_proposals"] == 2

    # 2. Import records
    res = importer.import_lead_research_records([rec_normal, rec_unverified, rec_excluded])
    assert res.companies_created == 2
    assert res.duplicates_skipped == 1  # excluded firm skipped

    # Verify cold_outreach_allowed is False on both newly created candidates
    cand_norm = store.get_by_domain("novel-bim.it")
    assert cand_norm is not None
    assert cand_norm.cold_outreach_allowed is False, "Research-only candidate must start with cold_outreach_allowed=False"

    cid_nodom = store.generate_company_id("Novel NoDomain Studio", None, "Italy")
    cand_nodom = store.get_by_id(cid_nodom)
    assert cand_nodom is not None
    assert cand_nodom.cold_outreach_allowed is False, "Research-only candidate without domain must start with cold_outreach_allowed=False"
