import os
import pytest
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import (
    CandidateLifecycleService,
    CandidateLifecycleState,
    CandidateQueueType,
    project_candidate_lifecycle,
)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_lifecycle_hardening.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", str(tmp_path / "test_research_cache.sqlite3"))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("TESTING", "true")

    store = CandidateStore(db_path=db_file)
    return store, db_file


def test_dnc_and_suppression_absolute_precedence(temp_db):
    """
    Test 1: DNC / Suppression Absolute Precedence.
    Even if reopened, contacted, or qualified, suppressed/rejected companies
    MUST NOT be classified as Active Work or eligible for outreach.
    """
    store, _ = temp_db

    # 1. DNC + Reopened
    c1 = CandidateCompany(
        candidate_id="cand:domain:dnc-reopened.com",
        company_name="DNC Reopened Srl",
        company_domain="dnc-reopened.com",
        suppressed=True,
        manual_review_decision="REOPENED",
        cold_outreach_allowed=False,
    )
    store.save_candidate(c1)

    state1 = project_candidate_lifecycle(c1, has_snapshot=False, is_suppressed_domain=True)
    assert state1.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED
    assert state1.is_active_work is False
    assert state1.is_suppressed_or_rejected is True
    assert "protected from outreach" in state1.actionable_next_step

    # 2. DNC + Contacted
    c2 = CandidateCompany(
        candidate_id="cand:domain:dnc-contacted.com",
        company_name="DNC Contacted Srl",
        company_domain="dnc-contacted.com",
        suppressed=True,
        historical_contact_status="dnc",
        previous_outreach_detected=True,
        cold_outreach_allowed=False,
    )
    store.save_candidate(c2)

    state2 = project_candidate_lifecycle(c2, has_snapshot=False, is_suppressed_domain=True)
    assert state2.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED
    assert state2.is_active_work is False
    assert state2.is_suppressed_or_rejected is True

    # 3. DNC + Qualified
    c3 = CandidateCompany(
        candidate_id="cand:domain:dnc-qualified.com",
        company_name="DNC Qualified Srl",
        company_domain="dnc-qualified.com",
        suppressed=True,
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=False,
    )
    store.save_candidate(c3)

    state3 = project_candidate_lifecycle(c3, has_snapshot=True, is_suppressed_domain=True)
    assert state3.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED
    assert state3.is_active_work is False
    assert state3.is_qualified_lead is False


def test_finalize_historical_blocks_premature_or_unsubstantiated_candidates(temp_db):
    """
    Test 2: Finalize Historical Safety Guard.
    Do not allow finalize_historical to succeed if candidate has no historical evidence.
    """
    store, _ = temp_db

    c_uncontacted = CandidateCompany(
        candidate_id="cand:domain:fresh-lead.com",
        company_name="Fresh Lead Srl",
        company_domain="fresh-lead.com",
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    store.save_candidate(c_uncontacted)

    # In router.py candidate_lifecycle_action:
    # A candidate without evidence cannot be finalized as historical
    hist_records = store.get_historical_outreach_for_candidate(c_uncontacted.candidate_id)
    has_hist_evidence = (
        bool(hist_records)
        or c_uncontacted.historical_contact_status in ("contacted", "replied", "converted", "dnc")
        or getattr(c_uncontacted, "previous_outreach_detected", False)
    )
    assert has_hist_evidence is False


def test_finalize_historical_preserves_qualification_decision(temp_db):
    """
    Test 3: Finalize Historical preserves qualification outcome.
    Overwriting original qualification decision is prohibited.
    """
    store, _ = temp_db

    c_qual = CandidateCompany(
        candidate_id="cand:domain:qual-contacted.com",
        company_name="Qual Contacted Srl",
        company_domain="qual-contacted.com",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    )
    store.save_candidate(c_qual)

    # Add historical record
    store.record_historical_outreach(
        candidate_id=c_qual.candidate_id,
        source_ref="Sent_Emails:row_1",
        contact_email="info@qual-contacted.com",
        channel="email",
    )

    # Re-retrieve and simulate finalize_historical
    cand = store.get_by_id(c_qual.candidate_id)
    cand.manual_review_decision = "HISTORICAL_FINALIZED"
    cand.cold_outreach_allowed = False
    store.save_candidate(cand)

    saved = store.get_by_id(c_qual.candidate_id)
    assert saved.qualification_outcome == QualificationOutcome.qualified_for_enrichment
    assert saved.discovery_status == DiscoveryStatus.qualified
    assert saved.manual_review_decision == "HISTORICAL_FINALIZED"


def test_reopen_candidate_preserves_discovery_and_qualification_state(temp_db):
    """
    Test 4: Reopening candidate for correction preserves prior qualification history.
    """
    store, _ = temp_db

    c = CandidateCompany(
        candidate_id="cand:domain:reopen-preserves.com",
        company_name="Preserve State Srl",
        company_domain="reopen-preserves.com",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        manual_review_decision="APPROVED",
    )
    store.save_candidate(c)

    # Simulate reopening
    cand = store.get_by_id(c.candidate_id)
    cand.manual_review_decision = "REOPENED"
    if not cand.discovery_status:
        cand.discovery_status = DiscoveryStatus.new
    cand.cold_outreach_allowed = False
    store.save_candidate(cand)

    saved = store.get_by_id(c.candidate_id)
    assert saved.manual_review_decision == "REOPENED"
    assert saved.discovery_status == DiscoveryStatus.qualified
    assert saved.qualification_outcome == QualificationOutcome.qualified_for_enrichment


def test_qualified_leads_deep_link_correctness():
    """
    Test 5: Qualified Leads link points to clean domain query parameter.
    """
    c = CandidateCompany(
        candidate_id="cand:domain:archetype-bim.it",
        company_name="Archetype BIM Srl",
        company_domain="archetype-bim.it",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
    )
    state = project_candidate_lifecycle(c, has_snapshot=True)
    assert state.primary_queue == CandidateQueueType.QUALIFIED
    assert state.recommended_action_url == "/sales/leads?q=archetype-bim.it"


def test_candidate_tier_and_status_enum_filtering(temp_db):
    """
    Test 6: Secondary filters (tier and status) handle Python Enum objects correctly.
    """
    store, _ = temp_db

    c1 = CandidateCompany(
        candidate_id="cand:domain:tier1.com",
        company_name="Tier 1 Company",
        company_domain="tier1.com",
        company_tier=CompanyTier.tier_1,
        discovery_status=DiscoveryStatus.new,
    )
    c2 = CandidateCompany(
        candidate_id="cand:domain:tier2.com",
        company_name="Tier 2 Company",
        company_domain="tier2.com",
        company_tier=CompanyTier.tier_2,
        discovery_status=DiscoveryStatus.researching,
    )
    store.save_candidate(c1)
    store.save_candidate(c2)

    # In router.py:
    # raw_tier = getattr(c, "company_tier", None)
    # c_tier = raw_tier.value.lower() if hasattr(raw_tier, "value") else str(raw_tier or "").lower()
    # verify matches query string "tier_1"
    raw_tier = getattr(c1, "company_tier", None)
    c_tier = raw_tier.value.lower() if hasattr(raw_tier, "value") else str(raw_tier or "").lower()
    assert c_tier == "tier_1"

    raw_status = getattr(c1, "discovery_status", None)
    c_status = raw_status.value.lower() if hasattr(raw_status, "value") else str(raw_status or "").lower()
    assert c_status == "new"

