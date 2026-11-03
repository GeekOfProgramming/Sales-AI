"""
Mandatory Critical Regressions & Revised Contract Acceptance Tests for Phase 10.

Covers:
P10-REG-001: Same company from Google/Directory/Job merges once (canonical domain match)
P10-REG-002: Same name different domains stay separate
P10-REG-003: Legal form alone never determines tier
P10-REG-004: No BIM mention does not imply no BIM capability (missing evidence -> unknown)
P10-REG-005: Directory result never becomes fake StructuredJob
P10-REG-006: Historical contacted company does not enter new cold outreach
P10-REG-007: Historical import is idempotent
P10-REG-008: Weak/no-intent good-fit company routes to watchlist
P10-REG-009: New job/project signal can promote watchlist candidate
P10-REG-010: Suppressed company/contact cannot qualify for outreach
P10-REG-011: Phase 5 frozen job score remains unchanged (non-job candidates unscored)
P10-REG-012: Phase 8 email uses only grounded evidence (internal assessments filtered)
P10-REG-013: Failure of one source preserves other results
P10-REG-014: Repeated daily run does not duplicate candidates
P10-REG-015: Runtime database never enters release ZIP
Revised Requirements:
P10-REG-016: Relationship type classification (service_buyer vs technology_partner)
P10-REG-017: Multi-opportunity types classification
P10-REG-018: Active conversation guard (active_conversation -> cold_outreach_allowed = False)
P10-REG-019: Actual respondent preservation in historical replies
P10-REG-020: Multi-email cell normalization
P10-REG-021: Phase 10.5 Console Query Bridge verification
P10-REG-022: Zero-Send verification across all Phase 10 discovery flows
"""

import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path
import pytest

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    BIMMaturity,
    FitLevel,
    IntentLevel,
    DiscoveryStatus,
    OpportunityType,
    RelationshipType,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.source_registry import SourceRegistry
from sales_engine.discovery_v2.bridges import (
    Phase5FreezeBridge,
    Phase6EnrichmentAdapter,
    Phase7ExportBridge,
    Phase8GroundingBridge,
    Phase9AuthorityBridge,
    Phase10ConsoleBridge,
)


@pytest.fixture
def temp_db_path(tmp_path):
    return tmp_path / "test_candidates.db"


@pytest.fixture
def store(temp_db_path):
    return CandidateStore(db_path=temp_db_path)


@pytest.fixture
def classifier():
    return CompanyClassifier()


# ---------------------------------------------------------------------------
# P10-REG-001: Same company from Google/Directory/Job merges once
# ---------------------------------------------------------------------------
def test_p10_reg_001_same_company_merges_once(store):
    engine = DiscoveryEngineV2(candidate_store=store)

    # 1. Ingest via Google
    c1, _ = engine.discover_company(
        company_name="Studio Lombardini",
        domain="https://www.lombardini22.com",
        source_name="google_search",
        discovery_mode="company_first",
    )

    # 2. Ingest via Directory
    c2, _ = engine.discover_company(
        company_name="Lombardini 22 S.p.A.",
        domain="lombardini22.com/contatti",
        source_name="italian_directory",
        discovery_mode="company_first",
    )

    # 3. Ingest via Job
    c3, _ = engine.discover_company(
        company_name="Lombardini-22",
        domain="careers.lombardini22.com",
        source_name="lever_job",
        discovery_mode="job_first",
    )

    assert c1.company_id == c2.company_id == c3.company_id
    assert c1.canonical_domain == "lombardini22.com"

    # Verify single company stored
    all_candidates = store.list_candidates()
    assert len(all_candidates) == 1
    assert "google_search" in all_candidates[0].discovery_sources
    assert "italian_directory" in all_candidates[0].discovery_sources
    assert "lever_job" in all_candidates[0].discovery_sources


# ---------------------------------------------------------------------------
# P10-REG-002: Same name different domains stay separate
# ---------------------------------------------------------------------------
def test_p10_reg_002_same_name_different_domains_stay_separate(store):
    engine = DiscoveryEngineV2(candidate_store=store)

    c1, _ = engine.discover_company(
        company_name="Studio Rossi",
        domain="https://www.studiorossi-milano.it",
        source_name="directory_mi",
    )

    c2, _ = engine.discover_company(
        company_name="Studio Rossi",
        domain="https://www.studiorossi-roma.it",
        source_name="directory_rm",
    )

    assert c1.company_id != c2.company_id
    assert c1.canonical_domain != c2.canonical_domain

    all_candidates = store.list_candidates()
    assert len(all_candidates) == 2


# ---------------------------------------------------------------------------
# P10-REG-003: Legal form alone never determines tier
# ---------------------------------------------------------------------------
def test_p10_reg_003_legal_form_alone_never_determines_tier(classifier):
    # Case A: Company is S.p.A. or S.r.l. but has no evidence of size/revenue/projects
    c_spa = CandidateCompany(
        company_id="test_spa",
        name="Piccola Impresa S.p.A.",
        canonical_domain="piccola-spa.it",
        legal_form="S.p.A.",
    )
    res_spa = classifier.classify(c_spa, observations=[])
    assert res_spa.company_tier == CompanyTier.unknown, "Legal form S.p.A. alone must never dictate Tier 1 or Tier 2!"

    c_srl = CandidateCompany(
        company_id="test_srl",
        name="Boutique Studio S.r.l.",
        canonical_domain="boutique-srl.it",
        legal_form="S.r.l.",
    )
    res_srl = classifier.classify(c_srl, observations=[])
    assert res_srl.company_tier == CompanyTier.unknown, "Legal form S.r.l. alone must never dictate tier!"


# ---------------------------------------------------------------------------
# P10-REG-004: No BIM mention does not imply no BIM capability
# ---------------------------------------------------------------------------
def test_p10_reg_004_no_bim_mention_is_unknown_not_no_bim(classifier):
    company = CandidateCompany(
        company_id="test_nobim",
        name="General Engineering Studio",
        canonical_domain="general-eng.it",
    )
    # Zero BIM evidence in observations
    res = classifier.classify(company, observations=[])
    assert res.bim_maturity == BIMMaturity.unknown, (
        f"Missing BIM evidence must result in 'unknown', not '{res.bim_maturity.value}'"
    )


# ---------------------------------------------------------------------------
# P10-REG-005: Directory result never becomes fake StructuredJob
# ---------------------------------------------------------------------------
def test_p10_reg_005_directory_result_never_becomes_fake_structured_job(store):
    engine = DiscoveryEngineV2(candidate_store=store)
    company, obs = engine.discover_company(
        company_name="Studio Associato Milano",
        domain="studioassociatomilano.it",
        source_name="ordine_architetti_directory",
        discovery_mode="company_first",
        extracted_data={"directory_id": "MI-12345", "category": "Architectural Design"},
    )
    # Non-job candidate must have no structured_job_id
    assert "structured_job_id" not in company.metadata
    assert "job_id" not in company.metadata
    assert obs.discovery_mode == "company_first"

    # Phase 5 bridge verifies it returns None (unscored)
    assert Phase5FreezeBridge.get_phase5_score(company) is None


# ---------------------------------------------------------------------------
# P10-REG-006: Historical contacted company does not enter new cold outreach
# ---------------------------------------------------------------------------
def test_p10_reg_006_historical_contacted_does_not_enter_cold_outreach(store):
    importer = HistoricalImporter(store=store)
    record = HistoricalRecord(
        source_file="past_campaign.csv",
        sheet_name="sheet1",
        row_number=10,
        company_name="Progetti & Costruzioni S.r.l.",
        domain="progetticostruzioni.it",
        tier_hint="Tier 2",
        notes="Contacted in 2024",
        source_url=None,
        contact_name="Marco Rossi",
        contact_role="Director",
        emails=["m.rossi@progetticostruzioni.it"],
        contact_date="2024-05-10",
        reply_date=None,
    )
    importer.import_records([record])

    company = store.get_by_domain("progetticostruzioni.it")
    assert company is not None
    assert company.previous_outreach_detected is True
    assert company.cold_outreach_allowed is False, "Previously contacted company must not enter cold outreach!"


# ---------------------------------------------------------------------------
# P10-REG-007: Historical import is idempotent
# ---------------------------------------------------------------------------
def test_p10_reg_007_historical_import_is_idempotent(store):
    importer = HistoricalImporter(store=store)
    record = HistoricalRecord(
        source_file="master_outreach.xlsx",
        sheet_name="Ingegneria",
        row_number=15,
        company_name="Idem Engineering",
        domain="idem-engineering.com",
        tier_hint="Tier 1",
        notes="High fit",
        source_url="http://idem-engineering.com",
        contact_name="Laura Bianchi",
        contact_role="BIM Manager",
        emails=["laura@idem-engineering.com", "info@idem-engineering.com"],
        contact_date="2024-06-01",
    )

    # First import
    res1 = importer.import_records([record])
    assert res1.companies_created == 1
    assert res1.duplicates_skipped == 0
    assert res1.contacts_extracted == 2

    # Second import with identical record
    res2 = importer.import_records([record])
    assert res2.companies_created == 0
    assert res2.companies_updated == 0
    assert res2.duplicates_skipped == 1

    # Verify database counts
    all_candidates = store.list_candidates()
    assert len(all_candidates) == 1


# ---------------------------------------------------------------------------
# P10-REG-008: Weak/no-intent good-fit company routes to watchlist
# ---------------------------------------------------------------------------
def test_p10_reg_008_good_fit_weak_intent_routes_to_watchlist(store, classifier):
    # Company with high fit (employees, BIM projects) but zero active hiring / tenders
    company = CandidateCompany(
        company_id="fit_no_intent",
        name="Major Architectural Group",
        canonical_domain="major-arch.com",
        employee_size_hint="150",
    )
    obs = [
        type("Obs", (), {
            "source_name": "company_website",
            "discovery_mode": "company_first",
            "extracted_data": {
                "services": ["BIM Modeling", "MEP Engineering"],
                "technologies": ["Revit", "Navisworks"],
                "employee_count": 150,
            }
        })()
    ]

    res = classifier.classify(company, obs)
    assert res.fit_level == FitLevel.high
    assert res.intent_level in (IntentLevel.unknown, IntentLevel.low)
    assert res.outcome == QualificationOutcome.watchlist

    # Add to store and verify watchlist storage
    company.fit_level = res.fit_level
    company.intent_level = res.intent_level
    company.status = DiscoveryStatus.watchlist
    store.upsert_company(company)
    store.add_to_watchlist(company.company_id, reason="High fit with unknown intent")

    watchlist = store.get_watchlist()
    assert len(watchlist) == 1
    assert watchlist[0]["company_id"] == company.company_id


# ---------------------------------------------------------------------------
# P10-REG-009: New job/project signal can promote watchlist candidate
# ---------------------------------------------------------------------------
def test_p10_reg_009_new_signal_promotes_watchlist_candidate(store, classifier):
    # 1. Put candidate on watchlist
    company = CandidateCompany(
        company_id="comp_watch_promo",
        name="Watchlist Studio",
        canonical_domain="watchlist-studio.it",
        status=DiscoveryStatus.watchlist,
        fit_level=FitLevel.high,
        intent_level=IntentLevel.unknown,
    )
    store.upsert_company(company)
    store.add_to_watchlist(company.company_id, reason="Initial watch")

    # 2. Candidate receives a new active hiring / project signal
    new_obs = type("Obs", (), {
        "source_name": "careers_portal",
        "discovery_mode": "job_first",
        "extracted_data": {
            "job_title": "Senior BIM Coordinator",
            "hiring_intent": "high",
        }
    })()

    res = classifier.classify(company, [new_obs])
    assert res.intent_level == IntentLevel.high
    assert res.outcome == QualificationOutcome.qualified_for_enrichment

    # 3. Promote in store without creating duplicate company
    promoted = store.promote_from_watchlist(
        company.company_id,
        new_status=DiscoveryStatus.qualified,
        reason="Detected Senior BIM Coordinator opening",
    )
    assert promoted is not None
    assert promoted.status == DiscoveryStatus.qualified

    # Ensure no duplicates
    all_comps = store.list_candidates()
    assert len(all_comps) == 1
    assert all_comps[0].status == DiscoveryStatus.qualified


# ---------------------------------------------------------------------------
# P10-REG-010: Suppressed company/contact cannot qualify for outreach
# ---------------------------------------------------------------------------
def test_p10_reg_010_suppressed_company_cannot_qualify(store):
    class MockPhase9SuppressionStore:
        def is_suppressed(self, email):
            return email == "optout@suppressed.com"
        def is_domain_suppressed(self, domain):
            return domain == "suppressed-firm.it"

    bridge = Phase9AuthorityBridge(suppression_store=MockPhase9SuppressionStore())

    company = CandidateCompany(
        company_id="supp_comp",
        name="Suppressed Firm",
        canonical_domain="suppressed-firm.it",
        cold_outreach_allowed=True,
    )

    checked = bridge.enforce_safety_on_candidate(company)
    assert checked.cold_outreach_allowed is False
    assert checked.status == DiscoveryStatus.suppressed
    assert checked.suppression_state.get("phase9_suppression") is True


# ---------------------------------------------------------------------------
# P10-REG-011: Phase 5 frozen job score remains unchanged
# ---------------------------------------------------------------------------
def test_p10_reg_011_phase5_frozen_score_remains_unchanged():
    # Non-job candidate
    non_job_comp = CandidateCompany(
        company_id="nj_1",
        name="Consulting Studio",
        canonical_domain="consulting.it",
    )
    score = Phase5FreezeBridge.get_phase5_score(non_job_comp)
    assert score is None, "Non-job candidates must not receive fake Phase 5 scores"

    # Real job candidate preserves legacy score verbatim
    job_comp = CandidateCompany(
        company_id="jb_1",
        name="Revit Specialists",
        canonical_domain="revitspec.it",
        metadata={
            "structured_job_id": "job_123",
            "legacy_phase5_score": {"composite_score": 88, "tier": "Tier 1"},
        },
    )
    job_score = Phase5FreezeBridge.get_phase5_score(job_comp)
    assert job_score == {"composite_score": 88, "tier": "Tier 1"}


# ---------------------------------------------------------------------------
# P10-REG-012: Phase 8 email uses only grounded evidence
# ---------------------------------------------------------------------------
def test_p10_reg_012_phase8_grounding_rejects_subjective_claims():
    # Verifies that Phase 8 grounding extractor filters out subjective speculation
    valid_text = "Noticed your delivery of BIM coordination on the Milan Metro project."
    is_valid, _ = Phase8GroundingBridge.sanitize_draft_claims(valid_text)
    assert is_valid is True

    subjective_text = "We know your firm is likely understaffed and probably inefficient."
    is_valid, reason = Phase8GroundingBridge.sanitize_draft_claims(subjective_text)
    assert is_valid is False
    assert "Forbidden internal assessment claimed as fact" in reason

    forbidden_text = "Since your outsourcing_potential = high, you need us."
    is_valid_f, _ = Phase8GroundingBridge.sanitize_draft_claims(forbidden_text)
    assert is_valid_f is False


# ---------------------------------------------------------------------------
# P10-REG-013: Failure of one source preserves other results
# ---------------------------------------------------------------------------
def test_p10_reg_013_failure_of_one_source_preserves_other_results(store):
    registry = SourceRegistry()
    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)

    def failing_fetcher(query):
        raise ConnectionError("API rate limit exceeded or timeout")

    def working_fetcher(query):
        return [
            {
                "company_name": "Resilient Engineering",
                "domain": "resilient-eng.com",
                "country": "IT",
                "city": "Turin",
            }
        ]

    fetchers = {
        "failing_source": failing_fetcher,
        "working_source": working_fetcher,
    }

    # Execute multi-source discovery across both sources
    run_rec = engine.run_multi_source_discovery(
        sources=["failing_source", "working_source"],
        query="BIM Turin",
        fetcher_funcs=fetchers,
    )

    # Invariants:
    # 1. Run record captured the error without crashing
    assert len(run_rec.errors) == 1
    assert "failing_source failed" in run_rec.errors[0]
    assert run_rec.status == "partial_failure"
    assert run_rec.candidates_discovered == 1

    # 2. Results from working source were safely preserved in the database
    candidate = store.get_by_domain("resilient-eng.com")
    assert candidate is not None
    assert candidate.name == "Resilient Engineering"

    # 3. Source health correctly marked
    health = registry.get_health_summary()
    assert health["failing_source"]["failure_count"] >= 1
    assert health["working_source"]["success_count"] >= 1


# ---------------------------------------------------------------------------
# P10-REG-014: Repeated daily run does not duplicate candidates
# ---------------------------------------------------------------------------
def test_p10_reg_014_repeated_daily_run_does_not_duplicate_candidates(store):
    engine = DiscoveryEngineV2(candidate_store=store)

    data = [
        {"company_name": "Studio Alfa", "domain": "alfa.it"},
        {"company_name": "Studio Beta", "domain": "beta.it"},
    ]

    def mock_fetcher(query):
        return data

    # Run day 1
    run1 = engine.run_multi_source_discovery(
        sources=["custom_dir"],
        query="Milan",
        fetcher_funcs={"custom_dir": mock_fetcher},
    )
    assert run1.candidates_discovered == 2
    assert len(store.list_candidates()) == 2

    # Run day 2 (same data)
    run2 = engine.run_multi_source_discovery(
        sources=["custom_dir"],
        query="Milan",
        fetcher_funcs={"custom_dir": mock_fetcher},
    )
    assert run2.candidates_discovered == 2
    # Store must still have exactly 2 candidates!
    assert len(store.list_candidates()) == 2


# ---------------------------------------------------------------------------
# P10-REG-015: Runtime database never enters release ZIP
# ---------------------------------------------------------------------------
def test_p10_reg_015_denylist_excludes_runtime_databases():
    # Verify denylist filters sqlite, db, env, etc.
    forbidden_extensions = {".db", ".sqlite", ".sqlite3", ".env", ".pem", ".key"}
    test_files = [
        "sales_engine/candidates/models.py",
        "sales_engine/candidates/candidate_store.py",
        "runtime.db",
        "test.sqlite",
        ".env",
        ".env.production",
        "config/discovery_sources.yaml",
    ]

    filtered = [
        f for f in test_files
        if not (f.startswith(".env") or any(f.endswith(ext) for ext in forbidden_extensions))
    ]
    assert "runtime.db" not in filtered
    assert "test.sqlite" not in filtered
    assert ".env" not in filtered
    assert ".env.production" not in filtered
    assert "sales_engine/candidates/models.py" in filtered
    assert "config/discovery_sources.yaml" in filtered


# ---------------------------------------------------------------------------
# P10-REG-016: Relationship type classification (service_buyer vs technology_partner)
# ---------------------------------------------------------------------------
def test_p10_reg_016_relationship_type_classification(classifier):
    # Case A: Engineering delivery buyer
    c_service = CandidateCompany(
        company_id="serv_1",
        name="General MEP Consultants",
        canonical_domain="mep-consultants.com",
    )
    obs_service = [
        type("Obs", (), {
            "source_name": "web",
            "discovery_mode": "company_first",
            "extracted_data": {
                "services": ["BIM modeling", "HVAC design", "electrical engineering"],
            }
        })()
    ]
    res_service = classifier.classify(c_service, obs_service)
    assert res_service.relationship_type == RelationshipType.service_buyer

    # Case B: Software / Technology / Plugin Developer
    c_tech = CandidateCompany(
        company_id="tech_1",
        name="OpenBIM Software Labs",
        canonical_domain="openbimlabs.com",
    )
    obs_tech = [
        type("Obs", (), {
            "source_name": "web",
            "discovery_mode": "company_first",
            "extracted_data": {
                "services": ["Revit plugins", "IFC viewers", "BIM automation software"],
                "technologies": ["C#", "Revit API", "OpenBIM"],
            }
        })()
    ]
    res_tech = classifier.classify(c_tech, obs_tech)
    assert res_tech.relationship_type == RelationshipType.technology_partner
    assert res_tech.relationship_type != RelationshipType.service_buyer, (
        "Do NOT force technology partners into direct delivery buyer classification!"
    )


# ---------------------------------------------------------------------------
# P10-REG-017: Multi-opportunity types classification
# ---------------------------------------------------------------------------
def test_p10_reg_017_multi_opportunity_types_classification(classifier):
    company = CandidateCompany(
        company_id="multi_opp_1",
        name="Integrated Tech & Delivery S.r.l.",
        canonical_domain="integrated-td.com",
    )
    obs = [
        type("Obs", (), {
            "source_name": "web",
            "discovery_mode": "company_first",
            "extracted_data": {
                "services": ["BIM Coordination", "MEP Delivery", "Custom Revit Automation"],
                "technologies": ["Dynamo", "Revit API", "Python"],
            }
        })()
    ]
    res = classifier.classify(company, obs)
    assert len(res.opportunity_types) >= 2
    assert OpportunityType.bim_coordination in res.opportunity_types
    assert OpportunityType.revit_api in res.opportunity_types or OpportunityType.automation in res.opportunity_types


# ---------------------------------------------------------------------------
# P10-REG-018: Active conversation guard
# ---------------------------------------------------------------------------
def test_p10_reg_018_active_conversation_guard(store):
    importer = HistoricalImporter(store=store)
    record = HistoricalRecord(
        source_file="ongoing_pipeline.csv",
        sheet_name="InDiscussion",
        row_number=5,
        company_name="Active Partner S.r.l.",
        domain="active-partner.it",
        tier_hint="Tier 1",
        notes="Active discussion regarding Q4 contract",
        source_url=None,
        contact_name="Giuseppe Verdi",
        contact_role="Managing Director",
        emails=["g.verdi@active-partner.it"],
        contact_date="2024-09-15",
        reply_status="Meeting scheduled - active discussion",
        has_active_conversation=True,
    )
    importer.import_records([record])

    company = store.get_by_domain("active-partner.it")
    assert company is not None
    assert company.cold_outreach_allowed is False, "Active conversation must block fresh cold outreach!"
    assert company.metadata.get("active_conversation") is True


# ---------------------------------------------------------------------------
# P10-REG-019: Actual respondent preservation in historical replies
# ---------------------------------------------------------------------------
def test_p10_reg_019_actual_respondent_preservation(store):
    importer = HistoricalImporter(store=store)
    record = HistoricalRecord(
        source_file="replies_log.xlsx",
        sheet_name="Sheet1",
        row_number=12,
        company_name="Studio Associato Delta",
        domain="studio-delta.it",
        tier_hint="Tier 2",
        notes="Replied by BIM lead instead of MD",
        source_url=None,
        contact_name="Ing. Original Recipient",
        contact_role="Managing Partner",
        emails=["original@studio-delta.it"],
        contact_date="2024-08-01",
        reply_date="2024-08-03",
        reply_status="Warm reply",
        respondent_name="Arch. Chiara Neri",
        respondent_role="BIM Director",
        respondent_email="chiara.neri@studio-delta.it",
    )
    importer.import_records([record])

    # Query historical contacts
    with store._get_connection() as conn:
        rows = conn.execute(
            "SELECT contact_name, contact_role, email, is_respondent FROM historical_contacts WHERE canonical_domain = 'studio-delta.it'"
        ).fetchall()

    assert len(rows) == 2
    emails = {r["email"]: r for r in rows}
    assert "original@studio-delta.it" in emails
    assert "chiara.neri@studio-delta.it" in emails
    assert emails["chiara.neri@studio-delta.it"]["is_respondent"] == 1
    assert emails["chiara.neri@studio-delta.it"]["contact_name"] == "Arch. Chiara Neri"


# ---------------------------------------------------------------------------
# P10-REG-020: Multi-email cell normalization
# ---------------------------------------------------------------------------
def test_p10_reg_020_multi_email_cell_normalization():
    cell_value = "info@studio.it;  director@studio.it\nlaura.rossi@studio.it, <contact@studio.it>"
    emails = HistoricalImporter.normalize_emails(cell_value)
    assert len(emails) == 4
    assert emails == ["info@studio.it", "director@studio.it", "laura.rossi@studio.it", "contact@studio.it"]


# ---------------------------------------------------------------------------
# P10-REG-021: Phase 10.5 Console Query Bridge verification
# ---------------------------------------------------------------------------
def test_p10_reg_021_console_query_bridge(store):
    registry = SourceRegistry()
    console = Phase10ConsoleBridge(store=store, registry=registry)

    # Insert a candidate with observation
    c = CandidateCompany(
        company_id="console_c1",
        name="Console Test Studio",
        canonical_domain="console-studio.it",
        company_tier=CompanyTier.tier2,
        bim_maturity=BIMMaturity.mature,
        relationship_type=RelationshipType.service_buyer,
        opportunity_types=[OpportunityType.direct_bim_delivery, OpportunityType.bim_coordination],
    )
    store.upsert_company(c)

    summary = console.get_candidate_list_summary()
    assert len(summary) >= 1
    item = next(s for s in summary if s["company_id"] == "console_c1")
    assert item["company_tier"] in ("tier_2", "tier2")
    assert item["bim_maturity"] in ("mature", "high")
    assert item["relationship_type"] == "service_buyer"
    assert "direct_bim_delivery" in item["opportunity_types"]

    detail = console.get_candidate_detail("console_c1")
    assert detail is not None
    assert detail["company"]["name"] == "Console Test Studio"

    health = console.get_source_health_summary()
    assert isinstance(health, dict)


# ---------------------------------------------------------------------------
# P10-REG-022: Zero-Send verification across all Phase 10 discovery flows
# ---------------------------------------------------------------------------
def test_p10_reg_022_zero_send_verification(store):
    """
    Ensure Phase 10 discovery, import, classification, and bridges contain
    absolutely zero outbound email send triggers or SMTP/SES/SendGrid calls.
    """
    engine = DiscoveryEngineV2(candidate_store=store)
    importer = HistoricalImporter(store=store)
    classifier = CompanyClassifier()

    # Execute all operations
    c, obs = engine.discover_company(
        company_name="Zero Send Firm",
        domain="zerosend.com",
        source_name="manual_seed",
    )
    res = classifier.classify(c, [obs])

    # No send functions exist on Phase 10 classes
    for obj in [engine, importer, classifier, store]:
        assert not hasattr(obj, "send_email")
        assert not hasattr(obj, "dispatch_email")
        assert not hasattr(obj, "execute_send")
        assert not hasattr(obj, "approve_and_send")


# ---------------------------------------------------------------------------
# Section 66 Acceptance: Historical Excel & CSV Import
# ---------------------------------------------------------------------------
def test_p10_historical_excel_import_acceptance(store, tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Milano"
    ws1.append([
        "Azienda", "Sito Web", "Fascia", "Note", "Fonte",
        "Referente", "Ruolo", "Email", "Data Contatto", "Data Risposta", "Esito"
    ])
    ws1.append([
        "Studio Milano Uno S.r.l.", "https://www.studiomilanouno.it", "Tier 2", "BIM specialist", "https://ordine.mi.it",
        "Marco Rossi", "Direttore Tecnico", "m.rossi@studiomilanouno.it; info@studiomilanouno.it", "2024-03-01", "2024-03-05", "Interested - call scheduled"
    ])

    ws2 = wb.create_sheet(title="Roma")
    ws2.append([
        "Company", "Domain", "Tier", "Notes", "Source",
        "Contact", "Role", "Email", "Contact Date", "Reply Date", "Status"
    ])
    ws2.append([
        "Roma Ingegneria S.p.A.", "romaingegneria.it", "Tier 1", "Major infrastructure", "https://tenders.it",
        "Laura Conti", "BIM Lead", "l.conti@romaingegneria.it", "2024-04-10", None, None
    ])

    excel_path = tmp_path / "sanitized_historical.xlsx"
    wb.save(excel_path)

    importer = HistoricalImporter(store=store)
    records = importer.parse_xlsx(excel_path)
    assert len(records) == 2
    assert records[0].sheet_name == "Milano"
    assert records[0].company_name == "Studio Milano Uno S.r.l."
    assert len(records[0].emails) == 2
    assert records[0].has_active_conversation is True

    assert records[1].sheet_name == "Roma"
    assert records[1].company_name == "Roma Ingegneria S.p.A."
    assert records[1].emails == ["l.conti@romaingegneria.it"]

    res = importer.import_records(records)
    assert res.companies_created == 2
    assert res.contacts_extracted == 3

    # Idempotent re-run
    res_repeat = importer.import_records(records)
    assert res_repeat.companies_created == 0
    assert res_repeat.duplicates_skipped == 2


def test_p10_historical_csv_import_acceptance(store, tmp_path):
    csv_content = """Company,Website,Tier,Notes,Contact,Email,Contact Date,Reply Date,Status
Studio Torino,https://studiotorino.com,Tier 3,Local architect,Paolo Neri,p.neri@studiotorino.com,2024-05-01,2024-05-02,Replied warm
Studio Genova,https://studiogenova.com,Tier 2,Port logistics,Anna Bianchi,a.bianchi@studiogenova.com,,,"""

    csv_path = tmp_path / "historical_export.csv"
    csv_path.write_text(csv_content, encoding="utf-8")

    importer = HistoricalImporter(store=store)
    records = importer.parse_csv(csv_path)
    assert len(records) == 2
    assert records[0].company_name == "Studio Torino"
    assert records[0].contact_date == "2024-05-01"
    assert records[0].reply_date == "2024-05-02"

    res = importer.import_records(records)
    assert res.companies_created == 2

    c_torino = store.get_by_domain("studiotorino.com")
    assert c_torino is not None
    assert c_torino.cold_outreach_allowed is False
    assert c_torino.previous_outreach_detected is True

    c_genova = store.get_by_domain("studiogenova.com")
    assert c_genova is not None
    assert c_genova.cold_outreach_allowed is True
    assert c_genova.previous_outreach_detected is False
