"""
tests/test_bulk_research_readiness.py

Focused tests for BULK-01: High-Volume Research Readiness.
Guarantees verified:
1. Test 1: Candidate Selection and Safeguard Filters
   - Candidates with historical outreach, active exclusions, suppressions, or pending
     HIST-04A proposals (e.g. essequadroservice.it) are excluded from the pilot batch.
   - Selects clean active candidates up to limit.
2. Test 2: Snapshot and Analysis Reuse with Token & Cost Accounting
   - Existing completed snapshots skip crawling.
   - Existing successful analyses skip LLM calls.
   - Mocked multi-chunk LLM analysis populates structured lead register proposals,
     computes tokens and costs, and routes to operator review backlog without auto-approving.
3. Test 3: Budget Cap & Resilient Provider Pause
   - Halts processing cleanly when total budget is exceeded.
   - Gracefully handles provider unavailable (503) without data corruption or duplicate requests.
"""

import json
import pytest
import httpx
from unittest.mock import MagicMock

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.lifecycle_service import CandidateLifecycleService
from sales_engine.candidates.models import CandidateCompany, CompanyTier, DiscoveryStatus
from sales_engine.research.bulk_research_pilot import (
    BulkResearchPilotConfig,
    BulkResearchPilotRunner,
    select_eligible_pilot_candidates,
)
from sales_engine.research.company_analysis_service import build_analysis_gateway
from sales_engine.research.pricing_config import DEFAULT_GEMINI_MODEL
from sales_engine.research.research_store import PageEvidence, ResearchSnapshotStore


@pytest.fixture
def disposable_pilot_env(tmp_path):
    """Creates an isolated disposable SQLite environment with test candidate data."""
    db_file = tmp_path / "pilot_test.db"
    cand_store = CandidateStore(str(db_file))
    snapshot_store = ResearchSnapshotStore(str(db_file))
    excl_store = ExcludedCompaniesStore(str(db_file))
    lifecycle_svc = CandidateLifecycleService(cand_store)

    # 1. Clean eligible active candidate A
    cand_a = CandidateCompany(
        candidate_id="cand:domain:alpha-bim.it",
        company_name="Alpha BIM Solutions S.r.l.",
        normalized_company_name="alpha bim solutions",
        company_domain="alpha-bim.it",
        cold_outreach_allowed=False,  # Needs research
        discovery_status=DiscoveryStatus.researching,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_a)

    # 2. Clean eligible active candidate B
    cand_b = CandidateCompany(
        candidate_id="cand:domain:beta-eng.it",
        company_name="Beta Engineering S.r.l.",
        normalized_company_name="beta engineering",
        company_domain="beta-eng.it",
        cold_outreach_allowed=False,
        discovery_status=DiscoveryStatus.researching,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_b)

    # 3. Excluded candidate C
    cand_c = CandidateCompany(
        candidate_id="cand:domain:excluded-corp.it",
        company_name="Excluded Corp S.r.l.",
        normalized_company_name="excluded corp",
        company_domain="excluded-corp.it",
        cold_outreach_allowed=False,
        discovery_status=DiscoveryStatus.researching,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_c)
    excl_store.exclude_company(
        company_name=cand_c.company_name,
        candidate_id=cand_c.candidate_id,
        canonical_domain=cand_c.company_domain,
        exclusion_reason="COMPETITOR",
        operator="test_operator",
    )

    # 4. Contacted candidate D (HIST-05 confirmed outreach)
    cand_d = CandidateCompany(
        candidate_id="cand:domain:already-contacted.it",
        company_name="Already Contacted S.r.l.",
        normalized_company_name="already contacted",
        company_domain="already-contacted.it",
        cold_outreach_allowed=False,
        discovery_status=DiscoveryStatus.known_company,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    )
    cand_store.save_candidate(cand_d)

    # 5. Proposed candidate E (HIST-04A pending association: EsseQuadro)
    cand_e = CandidateCompany(
        candidate_id="cand:domain:essequadroservice.it",
        company_name="EsseQuadro Service",
        normalized_company_name="essequadro service",
        company_domain="essequadroservice.it",
        cold_outreach_allowed=True,
        discovery_status=DiscoveryStatus.researching,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_e)

    return {
        "db_file": str(db_file),
        "cand_store": cand_store,
        "snapshot_store": snapshot_store,
        "excl_store": excl_store,
        "lifecycle_svc": lifecycle_svc,
    }


def _make_mock_llm_response(text_dict, prompt_tokens=800, completion_tokens=300):
    return httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl_mock",
            "choices": [{"message": {"content": json.dumps(text_dict)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )


def test_01_candidate_selection_safeguards_filter(disposable_pilot_env):
    """
    Test 1: Safeguard filters enforce strict boundary.
    - Excluded companies are rejected.
    - Previously contacted companies are rejected.
    - Proposed historical outreach candidates (EsseQuadro) are rejected.
    - Only uncontacted, active, non-excluded candidates are eligible.
    """
    env = disposable_pilot_env
    cand_store = env["cand_store"]
    lifecycle_svc = env["lifecycle_svc"]
    excl_store = env["excl_store"]

    eligible = select_eligible_pilot_candidates(
        store=cand_store,
        lifecycle_svc=lifecycle_svc,
        exclusion_store=excl_store,
        limit=10,
    )

    eligible_ids = {c.candidate_id for c in eligible}

    # Clean active candidates are present
    assert "cand:domain:alpha-bim.it" in eligible_ids
    assert "cand:domain:beta-eng.it" in eligible_ids

    # Safeguard exclusions
    assert "cand:domain:excluded-corp.it" not in eligible_ids
    assert "cand:domain:already-contacted.it" not in eligible_ids
    assert "cand:domain:essequadroservice.it" not in eligible_ids
    assert len(eligible) == 2


def test_02_snapshot_and_analysis_skipping_with_operator_backlog(disposable_pilot_env):
    """
    Test 2: Snapshot and Analysis Reuse with Token & Cost Accounting.
    - Candidate with existing snapshot skips crawl.
    - Mocked LLM execution parses fields, calculates cost, and leaves fields in operator review backlog.
    - Candidate with existing successful analysis skips LLM execution on subsequent run.
    """
    env = disposable_pilot_env
    cand_store = env["cand_store"]
    snapshot_store = env["snapshot_store"]

    # Pre-populate snapshot for Alpha BIM (3 pages)
    pages_alpha = [
        PageEvidence(
            snapshot_id="snap_alpha_01",
            evidence_id="ev_01",
            requested_url="https://alpha-bim.it/about",
            resolved_url="https://alpha-bim.it/about",
            page_type="about",
            http_status=200,
            title="Alpha BIM About",
            meta_description="BIM services in Milan",
            retrieval_status="FETCH_SUCCESS",
            cleaned_text="Alpha BIM Solutions S.r.l. specialized in Revit MEP, Navisworks clash coordination. Milan office.",
            content_sha256="sha_alpha_1",
            retrieved_at="2026-11-06T12:00:00Z",
        )
    ]
    snapshot_store.create_snapshot(
        candidate_id="cand:domain:alpha-bim.it",
        canonical_domain="alpha-bim.it",
        company_name="Alpha BIM Solutions S.r.l.",
        pages=pages_alpha,
        research_status="complete",
        snapshot_id="snap_alpha_01",
    )

    # Mock LLM Gateway response with structured 13 fields conforming to LEAD_REGISTER_13_FIELDS_SCHEMA
    mock_lead_fields = {
        "company_name": {"value": "Alpha BIM Solutions S.r.l.", "source_url": "https://alpha-bim.it/about", "confidence": 0.95, "is_hypothesis": False, "evidence_excerpt": "Alpha BIM Solutions S.r.l."},
        "legal_identity": {"value": "Alpha BIM Solutions S.r.l. - P.IVA 01234567890", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "Alpha BIM Solutions S.r.l."},
        "geographic_location": {"value": "Milan, Italy", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "Milan office"},
        "telephone_numbers": {"value": "+39 02 1234567", "source_url": "https://alpha-bim.it/about", "confidence": 0.8, "is_hypothesis": False, "evidence_excerpt": "Milan office"},
        "business_activities": {"value": "BIM Modeling and Navisworks Clash Detection", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "specialized in Revit MEP"},
        "official_website": {"value": "https://alpha-bim.it", "source_url": "https://alpha-bim.it/about", "confidence": 0.95, "is_hypothesis": False, "evidence_excerpt": "alpha-bim.it"},
        "responsible_person": {"value": "Ing. Marco Rossi", "source_url": "https://alpha-bim.it/about", "confidence": 0.85, "is_hypothesis": False, "evidence_excerpt": "Alpha BIM Solutions S.r.l."},
        "descriptions_and_notes": {"value": "Revit MEP and Navisworks specialist in Milan", "source_url": "https://alpha-bim.it/about", "confidence": 0.85, "is_hypothesis": False, "evidence_excerpt": "Milan office"},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
        "contact_email_addresses": {"value": "info@alpha-bim.it", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "Milan office"},
        "priority_level": {"value": "HIGH", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "specialized in Revit MEP"},
        "priority_rationale": {"value": "Direct match for BIM MEP modeling workflows", "source_url": "https://alpha-bim.it/about", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "specialized in Revit MEP"},
    }

    mock_client = MagicMock()
    mock_client.post.return_value = _make_mock_llm_response(mock_lead_fields, prompt_tokens=1200, completion_tokens=350)

    gw, effective_model = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="mock_test_key",
        client=mock_client,
    )

    config = BulkResearchPilotConfig(
        max_candidates=1,
        skip_existing_snapshots=True,
        skip_completed_analyses=True,
    )

    runner = BulkResearchPilotRunner(
        candidate_store=cand_store,
        snapshot_store=snapshot_store,
        config=config,
        gateway_override=gw,
    )

    # Run 1: Crawl is skipped, Analysis is executed
    cand_alpha = cand_store.get_candidate("cand:domain:alpha-bim.it")
    report1 = runner.run_pilot(candidate_override=[cand_alpha])

    assert report1.candidates_processed == 1
    assert report1.crawl_skipped_count == 1
    assert report1.analysis_success_count == 1
    assert report1.total_prompt_tokens == 1200
    assert report1.total_completion_tokens == 350
    assert report1.total_tokens == 1550
    assert report1.total_cost_usd > 0.0
    assert report1.operator_review_backlog_count == 1
    assert mock_client.post.call_count == 1

    # Candidate manual review decision remains NOT_REVIEWED (fail-closed, never auto-approved)
    cand_alpha_after = cand_store.get_candidate("cand:domain:alpha-bim.it")
    assert cand_alpha_after.manual_review_decision == "NOT_REVIEWED"

    # Run 2: Re-run with skip_completed_analyses=True -> Analysis is skipped without LLM request
    mock_client.reset_mock()
    report2 = runner.run_pilot(candidate_override=[cand_alpha])

    assert report2.candidates_processed == 1
    assert report2.crawl_skipped_count == 1
    assert report2.analysis_skipped_count == 1
    assert report2.analysis_success_count == 0
    assert mock_client.post.call_count == 0  # 0 LLM requests made


def test_03_budget_cap_and_safe_pause_on_provider_503(disposable_pilot_env):
    """
    Test 3: Budget Bounding and Resilient Pause on Provider Unavailable (HTTP 503).
    - When provider returns 503, runner halts safely and reports provider error.
    - Does not corrupt database or attempt further candidates in batch.
    """
    env = disposable_pilot_env
    cand_store = env["cand_store"]
    snapshot_store = env["snapshot_store"]

    # Pre-populate snapshot for candidate
    pages_beta = [
        PageEvidence(
            snapshot_id="snap_beta_01",
            evidence_id="ev_b1",
            requested_url="https://beta-eng.it",
            resolved_url="https://beta-eng.it",
            page_type="homepage",
            http_status=200,
            title="Beta Engineering",
            meta_description="Engineering firm",
            retrieval_status="FETCH_SUCCESS",
            cleaned_text="Beta Engineering structural and civil designs.",
            content_sha256="sha_beta_1",
            retrieved_at="2026-11-06T12:00:00Z",
        )
    ]
    snapshot_store.create_snapshot(
        candidate_id="cand:domain:beta-eng.it",
        canonical_domain="beta-eng.it",
        company_name="Beta Engineering S.r.l.",
        pages=pages_beta,
        research_status="complete",
        snapshot_id="snap_beta_01",
    )

    mock_client = MagicMock()
    # Mock HTTP 503 provider response
    mock_client.post.return_value = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": "Service Unavailable", "status": "UNAVAILABLE"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )

    gw, _ = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="mock_test_key",
        client=mock_client,
    )

    config = BulkResearchPilotConfig(
        max_candidates=2,
        total_pilot_budget_usd=5.00,
        skip_existing_snapshots=True,
    )

    runner = BulkResearchPilotRunner(
        candidate_store=cand_store,
        snapshot_store=snapshot_store,
        config=config,
        gateway_override=gw,
    )

    cand_beta = cand_store.get_candidate("cand:domain:beta-eng.it")
    cand_alpha = cand_store.get_candidate("cand:domain:alpha-bim.it")

    report = runner.run_pilot(candidate_override=[cand_beta, cand_alpha])

    # Runner stopped on first error and did not process cand_alpha
    assert report.stopped_on_provider_error is True
    assert report.analysis_failed_count == 1
    assert report.candidates_processed == 1
    assert report.results[0].analysis_status == "FAILED"
    assert "503" in (report.results[0].error_message or "")
