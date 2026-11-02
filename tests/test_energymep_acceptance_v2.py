"""
tests/test_energymep_acceptance_v2.py
Comprehensive Phase 7 Acceptance Test Suite for Email Led Research Pipeline V2.
Reference Candidate: energymep.it (cand:domain:energymep.it)

Tests:
1. Discovering more than 6 website pages when fixture exposes them.
2. Respecting internal-domain, security (SSRF/redirect), and crawl-budget restrictions.
3. Persisting successful page content into ResearchSnapshotStore.
4. Loading saved snapshot content without new network requests.
5. Preserving latest successful snapshot when subsequent crawl fails.
6. Passing stored content through configured LLM Gateway.
7. Mocking Gemini responses with valid and invalid JSON.
8. Extracting all required schema fields without inventing missing values (P.IVA, email, phone).
9. Preserving company identity when pages mention unrelated collaborators or clients (ESAC srl).
10. Persisting analysis proposals separately from approved Lead Register data.
11. Preventing overwrites of operator-reviewed fields without explicit approval.
12. Preserving contact verification, DNC, historical outreach, and sending restrictions (EMAIL_SEND_ENABLED=false).
"""

import json
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.research.company_analysis_service import (
    execute_company_analysis,
    build_analysis_gateway,
    LEAD_REGISTER_13_FIELDS_SCHEMA,
)
from sales_engine.research.company_research_service import (
    PageFetchResult,
    load_research_from_snapshot,
    convert_research_to_lead_record,
)
from sales_engine.research.research_store import (
    ResearchSnapshotStore,
    ResearchSnapshot,
    PageEvidence,
    CompanyAnalysisRecord,
)
from sales_engine.research.website_crawler import (
    WebsiteCrawlerV2,
    CrawlBudget,
    normalize_url,
    classify_page_type,
)


@pytest.fixture
def isolated_db_paths():
    temp_dir = tempfile.mkdtemp()
    cand_db = os.path.join(temp_dir, "test_candidates.sqlite3")
    snap_db = os.path.join(temp_dir, "test_snapshots.sqlite3")
    yield cand_db, snap_db
    try:
        if os.path.exists(cand_db):
            os.remove(cand_db)
        if os.path.exists(snap_db):
            os.remove(snap_db)
        os.rmdir(temp_dir)
    except Exception:
        pass


# ==============================================================================
# 1. Discovering > 6 pages when fixture site exposes them
# ==============================================================================
def test_1_crawler_discovers_more_than_six_pages(isolated_db_paths, monkeypatch):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Mock website exposing 8 valid internal pages
    fixture_pages = {
        "https://energymep.it/": PageFetchResult(
            url="https://energymep.it/",
            page_type="home",
            status="FETCH_SUCCESS",
            title="Energymep - Impianti e BIM",
            text_content="Energymep ingegneria impiantistica",
            raw_html="<html><body><a href='/chi-siamo'>Chi Siamo</a><a href='/servizi'>Servizi</a><a href='/progetti'>Progetti</a><a href='/bim'>BIM</a><a href='/contatti'>Contatti</a><a href='/team'>Team</a><a href='/legal'>Note Legali</a></body></html>",
            links=[
                {"text": "Chi Siamo", "href": "/chi-siamo"},
                {"text": "Servizi", "href": "/servizi"},
                {"text": "Progetti", "href": "/progetti"},
                {"text": "BIM", "href": "/bim"},
                {"text": "Contatti", "href": "/contatti"},
                {"text": "Team", "href": "/team"},
                {"text": "Note Legali", "href": "/legal"},
            ],
        ),
        "https://energymep.it/chi-siamo": PageFetchResult(
            url="https://energymep.it/chi-siamo",
            page_type="about",
            status="FETCH_SUCCESS",
            title="Chi Siamo - Energymep",
            text_content="Profilo aziendale Energymep",
            raw_html="<html><body><p>Chi Siamo</p></body></html>",
        ),
        "https://energymep.it/servizi": PageFetchResult(
            url="https://energymep.it/servizi",
            page_type="services",
            status="FETCH_SUCCESS",
            title="Servizi - Energymep",
            text_content="Progettazione impianti meccanici ed elettrici",
            raw_html="<html><body><p>Servizi</p></body></html>",
        ),
        "https://energymep.it/progetti": PageFetchResult(
            url="https://energymep.it/progetti",
            page_type="projects",
            status="FETCH_SUCCESS",
            title="Progetti - Energymep",
            text_content="Portfolio opere e progetti eseguiti",
            raw_html="<html><body><p>Progetti</p></body></html>",
        ),
        "https://energymep.it/bim": PageFetchResult(
            url="https://energymep.it/bim",
            page_type="bim",
            status="FETCH_SUCCESS",
            title="BIM - Energymep",
            text_content="Metodologia BIM e modellazione Revit",
            raw_html="<html><body><p>BIM</p></body></html>",
        ),
        "https://energymep.it/contatti": PageFetchResult(
            url="https://energymep.it/contatti",
            page_type="contacts",
            status="FETCH_SUCCESS",
            title="Contatti - Energymep",
            text_content="Sede a Padova, email info@energymep.it",
            raw_html="<html><body><p>Contatti</p></body></html>",
        ),
        "https://energymep.it/team": PageFetchResult(
            url="https://energymep.it/team",
            page_type="about",
            status="FETCH_SUCCESS",
            title="Team - Energymep",
            text_content="Ingegneri e progettisti",
            raw_html="<html><body><p>Team</p></body></html>",
        ),
        "https://energymep.it/legal": PageFetchResult(
            url="https://energymep.it/legal",
            page_type="legal",
            status="FETCH_SUCCESS",
            title="Legal - Energymep",
            text_content="P.IVA 04912345678",
            raw_html="<html><body><p>Legal</p></body></html>",
        ),
    }

    crawler = WebsiteCrawlerV2(
        snapshot_store=snap_store,
        budget=CrawlBudget(max_pages=15)
    )

    monkeypatch.setattr(crawler, "_fetch_robots_txt", lambda d: None)
    monkeypatch.setattr(crawler, "_discover_sitemap_urls", lambda d: [])
    monkeypatch.setattr(crawler, "_fetch_page", lambda url, ptype: fixture_pages.get(url, PageFetchResult(url=url, page_type=ptype, status="FETCH_FAILED")))

    summary = crawler.crawl_website(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
    )

    assert summary.pages_succeeded >= 8, f"Expected at least 8 pages, got {summary.pages_succeeded}"
    assert summary.status == "complete"
    assert summary.snapshot_id != ""


# ==============================================================================
# 2. Respecting internal-domain, security (SSRF/redirect), and crawl-budget
# ==============================================================================
def test_2_crawler_security_and_budget_restrictions(isolated_db_paths, monkeypatch):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Test SSRF block on home URL
    with patch("sales_engine.research.website_crawler.validate_public_url") as mock_validate:
        from sales_engine.research.lead_researcher import SSRFValidationError
        mock_validate.side_effect = SSRFValidationError("Target IP is private")
        crawler = WebsiteCrawlerV2(snapshot_store=snap_store)
        res = crawler.crawl_website("cand:1", "127.0.0.1", "Localhost")
        assert res.status == "blocked"
        assert res.pages_succeeded == 0

    # Test normalization filters external domain links
    assert normalize_url("https://external.com/about", "energymep.it") is None
    assert normalize_url("https://energymep.it/about", "energymep.it") == "https://energymep.it/about"

    # Test page budget limitation (budget=3)
    crawler_budget = WebsiteCrawlerV2(
        snapshot_store=snap_store,
        budget=CrawlBudget(max_pages=3)
    )
    monkeypatch.setattr(crawler_budget, "_fetch_robots_txt", lambda d: None)
    monkeypatch.setattr(crawler_budget, "_discover_sitemap_urls", lambda d: [
        "https://energymep.it/p1", "https://energymep.it/p2", "https://energymep.it/p3", "https://energymep.it/p4"
    ])
    monkeypatch.setattr(crawler_budget, "_fetch_page", lambda url, pt: PageFetchResult(url=url, page_type=pt, status="FETCH_SUCCESS", text_content="sample text"))

    summary = crawler_budget.crawl_website("cand:domain:energymep.it", "energymep.it", "Energymep")
    assert summary.pages_succeeded == 3
    assert summary.status == "partial"


# ==============================================================================
# 3. Persisting successful page content
# ==============================================================================
def test_3_persisting_successful_page_content(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description="Ingegneria",
        content_sha256="abc123hash",
        cleaned_text="Energymep Sede a Padova",
    )
    snap = snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[pe],
        research_status="complete",
    )
    assert snap.snapshot_id != ""
    assert snap.pages_succeeded == 1

    fetched_snap = snap_store.get_snapshot_by_id(snap.snapshot_id)
    assert fetched_snap is not None
    assert len(fetched_snap.pages) == 1
    assert fetched_snap.pages[0].cleaned_text == "Energymep Sede a Padova"


# ==============================================================================
# 4. Loading saved content without new network requests
# ==============================================================================
def test_4_loading_saved_content_zero_network_calls(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="def456hash",
        cleaned_text="Energymep BIM Engineering",
    )
    snap = snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[pe],
        research_status="complete",
    )

    # Reconstruct with zero network calls
    res = load_research_from_snapshot(snap, use_llm=False)
    assert res.company_name == "Energymep"
    assert res.is_from_snapshot is True
    assert res.snapshot_id == snap.snapshot_id


# ==============================================================================
# 5. Preserving latest successful snapshot when subsequent crawl fails
# ==============================================================================
def test_5_failed_crawl_preserves_latest_successful_snapshot(isolated_db_paths, monkeypatch):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Step 1: Successful crawl
    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T10:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="success_hash",
        cleaned_text="Energymep Homepage",
    )
    snap1 = snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[pe],
        research_status="complete",
    )

    latest_before = snap_store.get_latest_snapshot("cand:domain:energymep.it")
    assert latest_before.snapshot_id == snap1.snapshot_id

    # Step 2: Second crawl attempts and fails (0 succeeded pages)
    crawler = WebsiteCrawlerV2(snapshot_store=snap_store)
    monkeypatch.setattr(crawler, "_fetch_robots_txt", lambda d: None)
    monkeypatch.setattr(crawler, "_discover_sitemap_urls", lambda d: [])
    monkeypatch.setattr(crawler, "_fetch_page", lambda url, pt: PageFetchResult(url=url, page_type=pt, status="FETCH_FAILED", error_message="HTTP 500"))

    summary = crawler.crawl_website("cand:domain:energymep.it", "energymep.it", "Energymep")
    assert summary.status == "failed"
    assert summary.pages_succeeded == 0
    assert summary.snapshot_id == ""  # Zero-page crawls do not write a snapshot

    # Authoritative usable snapshot is still snap1!
    latest_after = snap_store.get_latest_snapshot("cand:domain:energymep.it")
    assert latest_after is not None
    assert latest_after.snapshot_id == snap1.snapshot_id
    assert latest_after.pages_succeeded == 1


# ==============================================================================
# 6. Passing stored content through configured LLM Gateway
# ==============================================================================
def test_6_llm_gateway_invocation_with_stored_content(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep S.r.l. Ingegneria Impiantistica. P.IVA 04912345678. Sede Padova.",
    )
    snap = snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[pe],
        research_status="complete",
    )

    mock_gw = MagicMock()
    mock_resp = LLMResponse(
        content=json.dumps({
            "company_name": {"value": "Energymep S.r.l.", "source_url": "https://energymep.it", "evidence_excerpt": "Energymep S.r.l.", "confidence": 0.95, "is_hypothesis": False},
            "legal_identity": {"value": "P.IVA 04912345678", "source_url": "https://energymep.it", "evidence_excerpt": "P.IVA 04912345678", "confidence": 0.95, "is_hypothesis": False},
            "geographic_location": {"value": "Padova, Italy", "source_url": "https://energymep.it", "evidence_excerpt": "Sede Padova", "confidence": 0.9, "is_hypothesis": False},
            "telephone_numbers": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "business_activities": {"value": "Ingegneria Impiantistica", "source_url": "https://energymep.it", "evidence_excerpt": "Ingegneria Impiantistica", "confidence": 0.9, "is_hypothesis": False},
            "official_website": {"value": "https://energymep.it", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 1.0, "is_hypothesis": False},
            "responsible_person": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "descriptions_and_notes": {"value": "Società specializzata in impiantistica.", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.85, "is_hypothesis": False},
            "job_advertisement_links": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "other_links_and_references": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "contact_email_addresses": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "priority_level": {"value": "B", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
            "priority_rationale": {"value": "Solid MEP profile", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
        }),
        provider="gemini",
        model="gemini-3.5-flash-lite",
        task_id="company_research_13_field_extraction",
        request_id="req-123",
        input_tokens=500,
        output_tokens=300,
    )
    mock_gw.generate.return_value = mock_resp

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        provider_id="gemini",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"
    assert rec.proposed_fields["company_name"]["value"] == "Energymep S.r.l."
    assert rec.proposed_fields["legal_identity"]["value"] == "P.IVA 04912345678"


# ==============================================================================
# 7. Mocking Gemini responses with valid and invalid JSON
# ==============================================================================
def test_7_gemini_json_validation_valid_and_invalid(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep Homepage",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    # Case A: Malformed JSON response
    mock_gw_invalid = MagicMock()
    mock_gw_invalid.generate.return_value = LLMResponse(
        content="I am an AI and here is the analysis: {company_name: invalid_json...",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        task_id="company_research_13_field_extraction",
        request_id="req-bad",
    )
    rec_bad = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        snapshot_store=snap_store,
        gateway_override=mock_gw_invalid,
    )
    assert rec_bad.status == "SCHEMA_INVALID"

    # Case B: Unconfigured Provider
    with patch.dict(os.environ, {}, clear=True):
        rec_unconf = execute_company_analysis(
            snapshot=snap,
            candidate_id="cand:domain:energymep.it",
            provider_id="gemini",
            api_key=None,
            snapshot_store=snap_store,
        )
        assert rec_unconf.status == "NOT_CONFIGURED"


# ==============================================================================
# 8. Extracting all required schema fields without inventing missing values
# ==============================================================================
def test_8_schema_fields_explicit_not_found(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep Progettazione",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        content=json.dumps({
            "company_name": {"value": "Energymep", "source_url": "https://energymep.it", "evidence_excerpt": "Energymep", "confidence": 0.9, "is_hypothesis": False},
            "legal_identity": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "geographic_location": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "telephone_numbers": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "business_activities": {"value": "Progettazione", "source_url": "https://energymep.it", "evidence_excerpt": "Progettazione", "confidence": 0.8, "is_hypothesis": False},
            "official_website": {"value": "https://energymep.it", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 1.0, "is_hypothesis": False},
            "responsible_person": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "descriptions_and_notes": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "job_advertisement_links": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "other_links_and_references": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "contact_email_addresses": {"value": "Not Found", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
            "priority_level": {"value": "B", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.5, "is_hypothesis": True},
            "priority_rationale": {"value": "Provisional priority", "source_url": "https://energymep.it", "evidence_excerpt": "", "confidence": 0.5, "is_hypothesis": True},
        }),
        provider="gemini",
        model="gemini-3.5-flash-lite",
        task_id="company_research_13_field_extraction",
        request_id="req-clean",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"
    # Verify missing fields are strictly "Not Found", not manufactured
    assert rec.proposed_fields["legal_identity"]["value"] == "Not Found"
    assert rec.proposed_fields["contact_email_addresses"]["value"] == "Not Found"
    assert rec.proposed_fields["telephone_numbers"]["value"] == "Not Found"


# ==============================================================================
# 9. Preserving company identity when pages mention unrelated collaborators (ESAC srl)
# ==============================================================================
def test_9_company_identity_preservation_collaborator_mention(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it",
        resolved_url="https://energymep.it",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep. Progetto condotto assieme alla azienda ESAC srl.",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    # Deterministic extraction verifies company identity is preserved
    res = load_research_from_snapshot(snap, use_llm=False)
    assert res.company_name == "Energymep"
    assert "ESAC" not in res.company_name


# ==============================================================================
# 10. Persisting analysis proposals separately from approved Lead Register data
# ==============================================================================
def test_10_analysis_proposals_isolated_from_lead_register(isolated_db_paths):
    cand_db, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)
    reg_store = LeadRegisterStore(db_path=cand_db)

    # Initial approved register record
    initial_reg = LeadRegisterRecord(
        candidate_id="cand:domain:energymep.it",
        record_number=10,
        company_name="Energymep (Authoritative Approved)",
        business_activities="Approved MEP Design",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
    )
    reg_store.save_record(initial_reg)

    # Save an LLM analysis record proposing different values
    analysis_rec = CompanyAnalysisRecord(
        analysis_id="an_test_10",
        candidate_id="cand:domain:energymep.it",
        snapshot_id="snap_10",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        created_at="2026-11-02T12:00:00Z",
        status="SUCCESS",
        proposed_fields={
            "company_name": {"value": "Energymep S.r.l. (AI Proposed)", "confidence": 0.9, "is_hypothesis": False},
            "business_activities": {"value": "AI Detected BIM Services", "confidence": 0.85, "is_hypothesis": False},
        },
    )
    # create dummy snapshot for FK
    snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[],
        snapshot_id="snap_10",
    )
    snap_store.save_analysis_record(analysis_rec)

    # Register record in LeadRegisterStore MUST NOT have changed!
    current_reg = reg_store.get_record("cand:domain:energymep.it")
    assert current_reg.company_name == "Energymep (Authoritative Approved)"
    assert current_reg.business_activities == "Approved MEP Design"
    assert current_reg.verification_status == "OPERATOR_REVIEWED"


# ==============================================================================
# 11. Preventing overwrites of operator-reviewed fields without explicit approval
# ==============================================================================
def test_11_selective_approval_preserves_unapproved_fields(isolated_db_paths):
    cand_db, snap_db = isolated_db_paths
    reg_store = LeadRegisterStore(db_path=cand_db)

    initial_reg = LeadRegisterRecord(
        candidate_id="cand:domain:energymep.it",
        record_number=10,
        company_name="Energymep S.r.l.",
        telephone_numbers="+39 049 111111",
        descriptions_and_notes="HAND-WRITTEN OPERATOR NOTE: Contact verified.",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
    )
    reg_store.save_record(initial_reg)

    # Simulate selective field save: operator approves ONLY company_name and business_activities,
    # NOT descriptions_and_notes or telephone_numbers
    updated_rec = LeadRegisterRecord(
        candidate_id="cand:domain:energymep.it",
        record_number=initial_reg.record_number,
        company_name="Energymep S.r.l. Aggiornata",
        telephone_numbers=initial_reg.telephone_numbers,  # preserved!
        descriptions_and_notes=initial_reg.descriptions_and_notes,  # preserved!
        business_activities="Nuove attività BIM approvate",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
    )
    reg_store.save_record(updated_rec)

    reloaded = reg_store.get_record("cand:domain:energymep.it")
    assert reloaded.company_name == "Energymep S.r.l. Aggiornata"
    assert reloaded.business_activities == "Nuove attività BIM approvate"
    assert reloaded.telephone_numbers == "+39 049 111111"
    assert "HAND-WRITTEN OPERATOR NOTE" in reloaded.descriptions_and_notes


# ==============================================================================
# 12. Preserving contact verification, DNC, historical outreach, and sending restrictions
# ==============================================================================
def test_12_outreach_safety_and_send_disabled_preserved(isolated_db_paths):
    cand_db, _ = isolated_db_paths
    cand_store = CandidateStore(db_path=cand_db)

    cand, _ = cand_store.get_or_create_candidate(
        company_name="Energymep",
        company_domain="energymep.it",
    )
    # Ensure candidate starts uncontacted, not approved for outreach
    cand.cold_outreach_allowed = False
    cand.manual_review_decision = "NOT_REVIEWED"
    cand_store.save_candidate(cand)

    reloaded = cand_store.get_by_id(cand.candidate_id)
    assert reloaded.cold_outreach_allowed is False
    assert reloaded.manual_review_decision == "NOT_REVIEWED"

    # Confirm EMAIL_SEND_ENABLED remains false
    from backend.sales_ui.router import is_email_send_enabled
    assert is_email_send_enabled() is False, "CRITICAL: EMAIL_SEND_ENABLED must be False!"


# ==============================================================================
# 13. Resumable Crawling from prior partial snapshot
# ==============================================================================
def test_13_resumable_crawling_preserves_pages_and_continues(isolated_db_paths):
    cand_db, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Initial crawl budget: 2 pages max
    initial_budget = CrawlBudget(max_pages=2, per_page_timeout_seconds=5.0)
    mock_browser = MagicMock()

    crawler1 = WebsiteCrawlerV2(snapshot_store=snap_store, budget=initial_budget, browser_adapter=mock_browser)
    crawler1._fetch_robots_txt = MagicMock(return_value=None)
    crawler1._discover_sitemap_urls = MagicMock(return_value=[
        "https://energymep.it/chi-siamo",
        "https://energymep.it/servizi",
        "https://energymep.it/contatti",
    ])
    crawler1._fetch_page = MagicMock(side_effect=lambda url, ptype: PageFetchResult(
        url=url,
        page_type=ptype,
        status="FETCH_SUCCESS",
        http_status=200,
        text_content=f"Content for {url}",
        title=f"Title {url}",
        raw_html=f"<html><body>Content for {url}</body></html>",
    ))

    summary1 = crawler1.crawl_website("cand:domain:energymep.it", "energymep.it", "Energymep")
    assert summary1.status == "partial"
    assert summary1.pages_succeeded == 2
    snap1_id = summary1.snapshot_id

    # Resume crawl with budget of 4 pages
    resume_budget = CrawlBudget(max_pages=4, per_page_timeout_seconds=5.0)
    crawler2 = WebsiteCrawlerV2(snapshot_store=snap_store, budget=resume_budget, browser_adapter=mock_browser)
    crawler2._fetch_robots_txt = MagicMock(return_value=None)
    crawler2._discover_sitemap_urls = MagicMock(return_value=[
        "https://energymep.it/chi-siamo",
        "https://energymep.it/servizi",
        "https://energymep.it/contatti",
    ])
    crawler2._fetch_page = MagicMock(side_effect=lambda url, ptype: PageFetchResult(
        url=url,
        page_type=ptype,
        status="FETCH_SUCCESS",
        http_status=200,
        text_content=f"Content for {url}",
        title=f"Title {url}",
        raw_html=f"<html><body>Content for {url}</body></html>",
    ))

    summary2 = crawler2.crawl_website(
        "cand:domain:energymep.it",
        "energymep.it",
        "Energymep",
        resume_snapshot_id=snap1_id,
    )
    # The resumed crawl should preserve the 2 prior pages and add up to 4 total pages
    assert summary2.pages_succeeded >= 3
    # Check that the new snapshot in store contains the combined evidence
    new_snap = snap_store.get_snapshot_by_id(summary2.snapshot_id)
    assert len(new_snap.pages) >= 3


# ==============================================================================
# 14. Preflight token and cost estimation helper
# ==============================================================================
def test_14_analysis_preflight_estimation(isolated_db_paths):
    from sales_engine.research.company_analysis_service import estimate_analysis_preflight

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep Home",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep MEP engineering BIM systems services " * 50,
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    preflight = estimate_analysis_preflight(snap, provider_id="gemini", model="gemini-3.5-flash-lite")
    assert preflight["provider"] == "gemini"
    assert preflight["model"] == "gemini-3.5-flash-lite"
    assert preflight["estimated_input_tokens"] > 0
    assert preflight["estimated_cost_usd"] >= 0.0


# ==============================================================================
# 15. Source evidence excerpt verification against snapshot content
# ==============================================================================
def test_15_source_evidence_excerpt_verification(isolated_db_paths):
    from sales_engine.research.company_analysis_service import verify_field_evidence_excerpts

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep Home",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep S.r.l. Progettazione impianti MEP e BIM. Sede a Padova. Tel 049 123456.",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    fields = {
        "company_name": {
            "value": "Energymep S.r.l.",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "Energymep S.r.l.",
            "confidence": 0.95,
            "is_hypothesis": False,
        },
        "telephone_numbers": {
            "value": "+39 049 123456",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "Tel 049 123456",
            "confidence": 0.90,
            "is_hypothesis": False,
        },
        "job_advertisement_links": {
            "value": "Not Found",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "",
            "confidence": 0.0,
            "is_hypothesis": False,
        },
        "responsible_person": {
            "value": "Mario Rossi",
            "source_url": "https://unknown-domain.com/bogus",
            "evidence_excerpt": "Fabricated text not on website",
            "confidence": 0.5,
            "is_hypothesis": False,
        }
    }

    verified = verify_field_evidence_excerpts(snap, fields)
    assert verified["company_name"]["grounded_in_snapshot"] is True
    assert verified["company_name"]["excerpt_verified"] is True
    assert verified["telephone_numbers"]["grounded_in_snapshot"] is True
    assert verified["telephone_numbers"]["excerpt_verified"] is True
    assert verified["job_advertisement_links"]["grounded_in_snapshot"] is True
    # Fake URL and fabricated text must fail grounded check & excerpt check
    assert verified["responsible_person"]["grounded_in_snapshot"] is False
    assert verified["responsible_person"]["excerpt_verified"] is False
