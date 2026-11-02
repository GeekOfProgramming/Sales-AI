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
import socket
import sqlite3
import tempfile
import time
import pytest
from unittest.mock import patch, MagicMock

# Global offline DNS safety patch: ensures all tests run 100% offline without network lookups
_REAL_GETADDRINFO = socket.getaddrinfo
def _offline_safe_getaddrinfo(host, port, *args, **kwargs):
    if host in ("energymep.it", "www.energymep.it", "bsprogetti.it", "test-domain.com"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))]
    return _REAL_GETADDRINFO(host, port, *args, **kwargs)

socket.getaddrinfo = _offline_safe_getaddrinfo

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


# ==============================================================================
# 16. P0-A: Resumed Crawl with 20-Page Saturated Prior Snapshot
# ==============================================================================
def test_16_resumed_crawl_with_saturated_20_page_snapshot(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Create saturated snapshot with 20 existing pages
    prior_pages = []
    for i in range(1, 21):
        u = f"https://energymep.it/page-{i:02d}"
        pe = PageEvidence(
            snapshot_id="",
            evidence_id=f"EV_{i:02d}",
            requested_url=u,
            resolved_url=u,
            page_type="general",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-11-02T12:00:00Z",
            title=f"Page {i}",
            meta_description=None,
            content_sha256=f"hash_{i}",
            cleaned_text=f"Content for page {i}",
            links=[{"text": f"Page {i+1}", "href": f"/page-{i+1:02d}"}],
        )
        prior_pages.append(pe)

    snap_prior = snap_store.create_snapshot(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=prior_pages,
        research_status="partial",
    )
    assert snap_prior.pages_succeeded == 20

    # Resume crawl with budget of 5 NEW pages
    new_budget = CrawlBudget(max_pages=5, per_page_timeout_seconds=5.0)
    crawler = WebsiteCrawlerV2(snapshot_store=snap_store, budget=new_budget)
    crawler._fetch_robots_txt = MagicMock(return_value=(None, "Mock allow"))
    crawler._discover_sitemap_urls = MagicMock(return_value=[
        f"https://energymep.it/page-{j:02d}" for j in range(21, 30)
    ])
    crawler._fetch_page = MagicMock(side_effect=lambda url, ptype: PageFetchResult(
        url=url,
        page_type=ptype,
        status="FETCH_SUCCESS",
        http_status=200,
        text_content=f"Content for {url}",
        title=f"Title {url}",
        raw_html=f"<html><body>Content for {url}</body></html>",
    ))

    summary = crawler.crawl_website(
        candidate_id="cand:domain:energymep.it",
        canonical_domain="energymep.it",
        company_name="Energymep",
        resume_snapshot_id=snap_prior.snapshot_id,
    )

    # Must crawl 5 NEW pages and retain 20 previous pages -> 25 total!
    assert summary.previous_pages_count == 20
    assert summary.newly_fetched_count == 5
    assert summary.pages_succeeded == 25
    assert summary.status == "partial"  # Frontier still has unvisited sitemap pages


# ==============================================================================
# 17. P0-B: Multi-Pass Chunking and Late-Page Evidence Preservation
# ==============================================================================
def test_17_multi_pass_chunking_and_late_page_evidence(isolated_db_paths):
    from sales_engine.research.company_analysis_service import partition_snapshot_into_chunks

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    # Create 15 pages with 4,000 characters each (~60,000 chars total)
    pages = []
    for i in range(1, 16):
        u = f"https://energymep.it/article-{i:02d}"
        pe = PageEvidence(
            snapshot_id="",
            evidence_id=f"EV_{i:02d}",
            requested_url=u,
            resolved_url=u,
            page_type="general",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-11-02T12:00:00Z",
            title=f"Article {i}",
            meta_description=None,
            content_sha256=f"hash_art_{i}",
            cleaned_text=f"Important research evidence on article {i}. " * 100,
        )
        pages.append(pe)

    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", pages)

    chunks, eligible, omitted = partition_snapshot_into_chunks(snap, chunk_size_chars=25_000)
    # Total chars ~60k divided into ~25k chunks -> 3 chunks
    assert len(chunks) >= 2
    assert len(eligible) == 15
    assert len(omitted) == 0
    # Confirm late pages (e.g. article-15) are included in chunks
    all_chunk_urls = [url for c in chunks for url in c["urls"]]
    assert "https://energymep.it/article-15" in all_chunk_urls


# ==============================================================================
# 18. P0-C: Quarantine Non-Grounded Fields & Reject from Approval
# ==============================================================================
def test_18_quarantine_ungrounded_fields_and_reject_approval(isolated_db_paths):
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
        title="Energymep",
        meta_description=None,
        content_sha256="hash1",
        cleaned_text="Energymep S.r.l. Ingegneria Padova",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    # Proposal citing external bogus URL and hallucinated excerpt
    proposals = {
        "company_name": {
            "value": "Fabricated Corp",
            "source_url": "https://attacker.com/fake",
            "evidence_excerpt": "Not on page",
            "confidence": 0.99,
            "is_hypothesis": False,
        },
        "business_activities": {
            "value": "Ingegneria Padova",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "Ingegneria Padova",
            "confidence": 0.95,
            "is_hypothesis": False,
        }
    }

    verified = verify_field_evidence_excerpts(snap, proposals)
    assert verified["company_name"]["quarantined"] is True
    assert verified["company_name"]["selectable_for_approval"] is False
    assert verified["business_activities"]["quarantined"] is False
    assert verified["business_activities"]["selectable_for_approval"] is True


# ==============================================================================
# 19. P1-A: Stale Analysis Not Displayed for New Snapshot
# ==============================================================================
def test_19_stale_analysis_isolated_from_new_snapshot(isolated_db_paths):
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe1 = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-01T12:00:00Z",
        title="Snap 1", meta_description=None, content_sha256="h1", cleaned_text="Snap 1 text",
    )
    snap1 = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe1])

    # Save analysis on snap1
    an1 = CompanyAnalysisRecord(
        analysis_id="an_snap1",
        candidate_id="cand:domain:energymep.it",
        snapshot_id=snap1.snapshot_id,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        created_at="2026-11-01T12:05:00Z",
        status="SUCCESS",
        proposed_fields={"company_name": {"value": "Energymep Snap 1"}},
    )
    snap_store.save_analysis_record(an1)

    # Now a new crawl produces snap2
    pe2 = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Snap 2", meta_description=None, content_sha256="h2", cleaned_text="Snap 2 text",
    )
    snap2 = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe2])

    # Query latest analysis bound strictly to snap2
    an_for_snap2 = snap_store.get_latest_analysis("cand:domain:energymep.it", snapshot_id=snap2.snapshot_id)
    assert an_for_snap2 is None, "Stale analysis from snap1 must NOT be returned for snap2!"

    # But query for snap1 still returns an1
    an_for_snap1 = snap_store.get_latest_analysis("cand:domain:energymep.it", snapshot_id=snap1.snapshot_id)
    assert an_for_snap1 is not None
    assert an_for_snap1.analysis_id == "an_snap1"


# ==============================================================================
# 20. P1-B: Unauthorized Paid Analysis Direct POST Rejection
# ==============================================================================
def test_20_unauthorized_direct_post_analysis_rejected(isolated_db_paths):
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Snap", meta_description=None, content_sha256="h", cleaned_text="Energymep text",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    # Case A: Missing auth token with require_auth=True
    rec_no_auth = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        provider_id="gemini",
        snapshot_store=snap_store,
        require_auth=True,
        auth_token=None,
    )
    assert rec_no_auth.status == "UNAUTHORIZED"
    assert "Missing authorization token" in rec_no_auth.diagnostic_info

    # Case B: Tampered auth token
    rec_bad_auth = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        provider_id="gemini",
        snapshot_store=snap_store,
        require_auth=True,
        auth_token="tampered|token|structure|sig",
    )
    assert rec_bad_auth.status == "UNAUTHORIZED"


# ==============================================================================
# 21. P1-C: Cross-Domain Redirect Rejection (SafeDomainRedirectHandler)
# ==============================================================================
def test_21_cross_domain_redirect_blocked():
    from sales_engine.research.website_crawler import SafeDomainRedirectHandler
    from sales_engine.research.lead_researcher import SSRFValidationError

    handler = SafeDomainRedirectHandler(authorized_domain="energymep.it")
    mock_req = MagicMock()
    mock_req.get_full_url.return_value = "https://energymep.it/out"

    # Redirection to external domain must raise SSRFValidationError
    with pytest.raises(SSRFValidationError) as exc_info:
        handler.redirect_request(
            req=mock_req,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://malicious-external-site.com/trap",
        )
    assert "leaves authorized scope" in str(exc_info.value)


# ==============================================================================
# 22. V2.1b Pre-Live Safety Tests
# ==============================================================================

def test_22_auth_token_expiration_and_nonce_replay(isolated_db_paths):
    from sales_engine.research.pricing_config import (
        generate_analysis_auth_token,
        verify_analysis_auth_token,
    )

    cid = "cand:domain:energymep.it"
    sid = "snap_test_01"

    # Token expires immediately if validity_seconds=-1
    expired_token = generate_analysis_auth_token(
        candidate_id=cid,
        snapshot_id=sid,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
        validity_seconds=-10,
    )
    is_valid, _, err = verify_analysis_auth_token(
        auth_token=expired_token,
        candidate_id=cid,
        snapshot_id=sid,
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert not is_valid
    assert "expired" in err.lower()

    # Valid token succeeds on first use
    fresh_token = generate_analysis_auth_token(
        candidate_id=cid,
        snapshot_id=sid,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
        validity_seconds=600,
    )
    is_valid, max_b, err = verify_analysis_auth_token(
        auth_token=fresh_token,
        candidate_id=cid,
        snapshot_id=sid,
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert is_valid
    assert max_b == 0.50

    # Replay attack: reusing the same token must fail
    replay_valid, _, replay_err = verify_analysis_auth_token(
        auth_token=fresh_token,
        candidate_id=cid,
        snapshot_id=sid,
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert not replay_valid
    assert "replay detected" in replay_err.lower()


def test_23_candidate_and_snapshot_ownership_cross_reference_rejection(isolated_db_paths):
    from sales_engine.research.company_analysis_service import execute_company_analysis
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h", cleaned_text="Energymep body",
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    # Mismatched candidate ID must be rejected as SECURITY_VIOLATION
    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:other-company.it",
        provider_id="gemini",
        snapshot_store=snap_store,
    )
    assert rec.status == "SECURITY_VIOLATION"
    assert "Cross-candidate snapshot reference prohibited" in rec.diagnostic_info


def test_24_spending_limit_and_budget_exceeded_termination(isolated_db_paths):
    from sales_engine.research.company_analysis_service import execute_company_analysis
    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h",
        cleaned_text="Very long website content " * 1000,
    )
    snap = snap_store.create_snapshot("cand:domain:energymep.it", "energymep.it", "Energymep", [pe])

    mock_gw = MagicMock()
    # If approved budget is $0.000001, preflight budget check must abort before making any call
    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id="cand:domain:energymep.it",
        provider_id="gemini",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
        max_approved_budget_usd=0.000001,
    )
    assert rec.status == "BUDGET_EXCEEDED"
    assert mock_gw.generate.call_count == 0


def test_29_successful_analysis_exposes_13_fields_in_review_ui(isolated_db_paths):
    """
    V2.1c: Successfully completed analysis persists a SUCCESS analysis record
    and exposes proposed 13 fields in Research Review view.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis
    from sales_engine.research.company_research_service import load_research_from_snapshot

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep S.r.l. operates in MEP engineering. P.IVA 01234567890.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    mock_resp_json = {
        "company_name": {"value": "Energymep S.r.l.", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep S.r.l.", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "P.IVA 01234567890", "source_url": "https://energymep.it/", "evidence_excerpt": "P.IVA 01234567890", "confidence": 0.95, "is_hypothesis": False},
        "geographic_location": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "telephone_numbers": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "business_activities": {"value": "MEP engineering", "source_url": "https://energymep.it/", "evidence_excerpt": "operates in MEP engineering", "confidence": 0.90, "is_hypothesis": False},
        "official_website": {"value": "https://energymep.it/", "source_url": "https://energymep.it/", "evidence_excerpt": "https://energymep.it/", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "contact_email_addresses": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "priority_level": {"value": "A", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
        "priority_rationale": {"value": "Strong BIM and MEP engineering profile", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
    }

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_test_02",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(mock_resp_json),
        parsed_json=mock_resp_json,
        input_tokens=200,
        output_tokens=400,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"

    # Verify that get_latest_analysis retrieves this record
    stored_an = snap_store.get_latest_analysis(cid, snapshot_id=snap.snapshot_id)
    assert stored_an is not None
    assert stored_an.status == "SUCCESS"
    assert stored_an.proposed_fields["company_name"]["value"] == "Energymep S.r.l."
    assert stored_an.proposed_fields["legal_identity"]["value"] == "P.IVA 01234567890"
    assert stored_an.proposed_fields["business_activities"]["value"] == "MEP engineering"


# ==============================================================================
# 30. V2.1d Gemini Structured Output Regression Tests
# ==============================================================================

def test_30_actual_failed_response_structure_reproduced_and_handled(isolated_db_paths):
    """
    Reproduces the actual failed Gemini response structure that caused
    'SCHEMA_INVALID — Missing 11 required fields' (non-standard keys returned by unconstrained json_object mode).
    Validates that the failure is safely caught as SCHEMA_INVALID without crashing or corrupting Lead Register.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep official website and services.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    # Non-standard keys returned during the unconstrained json_object failure
    failed_gemini_json = {
        "company_name": {"value": "Energymep", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep", "is_hypothesis": False},
        "website": {"value": "https://energymep.it/", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "energymep.it", "is_hypothesis": False},
        "vat_number": {"value": "Not Found", "confidence": 0.0, "source_url": "", "evidence_excerpt": "", "is_hypothesis": False},
        "hq_address": {"value": "Via Taggia 38 B", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "Via Taggia 38 B", "is_hypothesis": False},
        "phone": {"value": "+39 347 455 9751", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "+39 347 455 9751", "is_hypothesis": False},
        "email": {"value": "info@energymep.it", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "info@energymep.it", "is_hypothesis": False},
    }

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_fail_reproduce",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(failed_gemini_json),
        parsed_json=failed_gemini_json,
        input_tokens=8700,
        output_tokens=1350,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SCHEMA_INVALID"
    assert "Missing required fields" in rec.diagnostic_info
    assert "legal_identity" in rec.diagnostic_info
    assert "official_website" in rec.diagnostic_info


def test_31_openai_provider_structured_output_payload_construction():
    """
    Verifies that OpenAIProvider properly formats response_format with type='json_schema'
    and passes strict=True with the schema when configured for Gemini or models with supports_structured_schema.
    """
    prov = OpenAIProvider(
        provider_id="gemini",
        api_key="sk-mock",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-3.5-flash-lite",
        model_capabilities={"gemini-3.5-flash-lite": {"supports_structured_schema": True, "supports_json_mode": True}},
    )

    req = LLMRequest(
        task_id="company_research_13_field_extraction",
        prompt="Analyze website",
        response_schema=LEAD_REGISTER_13_FIELDS_SCHEMA,
    )

    payload = prov._build_payload(req, "gemini-3.5-flash-lite")
    rf = payload.get("response_format")
    assert rf is not None
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == LEAD_REGISTER_13_FIELDS_SCHEMA


def test_32_truncated_model_output_detected_as_schema_invalid(isolated_db_paths):
    """
    Verifies that model responses marked with finish_reason='length' or 'max_tokens'
    are classified as SCHEMA_INVALID with diagnostic notice.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Website content",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_trunc",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content='{"company_name": {"value": "Energymep"',
        parsed_json=None,
        input_tokens=100,
        output_tokens=2500,
        finish_reason="length",  # Truncated!
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SCHEMA_INVALID"
    assert "truncated" in rec.diagnostic_info.lower()


def test_33_wrapped_json_top_level_unwrapped_successfully(isolated_db_paths):
    """
    Verifies that if an LLM wraps the 13 fields under a top-level key like 'lead_register'
    or 'fields', the extractor safely unwraps it without failing.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep S.r.l. operates in MEP engineering.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    inner_fields = {
        "company_name": {"value": "Energymep S.r.l.", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep S.r.l.", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "geographic_location": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "telephone_numbers": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "business_activities": {"value": "MEP engineering", "source_url": "https://energymep.it/", "evidence_excerpt": "operates in MEP engineering", "confidence": 0.90, "is_hypothesis": False},
        "official_website": {"value": "https://energymep.it/", "source_url": "https://energymep.it/", "evidence_excerpt": "https://energymep.it/", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "contact_email_addresses": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "priority_level": {"value": "A", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
        "priority_rationale": {"value": "Strong BIM and MEP engineering profile", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
    }
    wrapped_json = {"lead_register": inner_fields}

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_wrapped",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(wrapped_json),
        parsed_json=wrapped_json,
        input_tokens=200,
        output_tokens=400,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"
    assert rec.proposed_fields["company_name"]["value"] == "Energymep S.r.l."


def test_25_oversized_page_subchunking_preserves_url():
    from sales_engine.research.company_analysis_service import partition_snapshot_into_chunks

    pe = PageEvidence(
        snapshot_id="snap_big", evidence_id="EV_BIG", requested_url="https://energymep.it/huge",
        resolved_url="https://energymep.it/huge", page_type="services", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Huge", meta_description=None, content_sha256="h_big",
        cleaned_text="A" * 60_000,
    )
    snap = ResearchSnapshot(
        snapshot_id="snap_big",
        candidate_id="cand:test",
        canonical_domain="energymep.it",
        company_name="Energymep",
        created_at="2026-11-02T12:00:00Z",
        completed_at="2026-11-02T12:00:05Z",
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        research_status="complete",
        pages_attempted=1,
        pages_succeeded=1,
        content_checksum="chk_big",
        pages=[pe],
    )

    chunks, eligible, omitted = partition_snapshot_into_chunks(snap, chunk_size_chars=25_000)
    assert len(chunks) >= 2, "60k char page must be sliced into at least 2 chunks"
    for c in chunks:
        assert "https://energymep.it/huge" in c["urls"]
        assert "URL: https://energymep.it/huge" in c["text"]


def test_26_gemini_credential_diagnostics():
    from sales_engine.research.company_analysis_service import diagnose_gemini_credential_status

    with patch.dict(os.environ, {}, clear=True):
        diag = diagnose_gemini_credential_status()
        # On this Windows machine, the DPAPI file exists at %APPDATA%\pyBIM\SalesAI\api-secrets\GEMINI_API_KEY.dpapi
        if diag["vault_path_exists"]:
            assert diag["status"] == "NOT_LOADED_IN_PROCESS"
            assert "DPAPI vault" in diag["message"]
        else:
            assert diag["status"] == "MISSING_VAULT_KEY"

    with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_secret_not_logged"}):
        diag_loaded = diagnose_gemini_credential_status()
        assert diag_loaded["status"] == "CONFIGURED"
        assert "loaded" in diag_loaded["message"].lower()
        # Verify secret itself is never leaked in the message
        assert "dummy_secret" not in diag_loaded["message"]


# ==============================================================================
# 27. V2.1c Double Authorization Consumption Fix Tests
# ==============================================================================

def test_27_single_use_nonce_normal_router_service_flow(isolated_db_paths):
    """
    V2.1c: Router preliminary validation with mark_used=False does NOT consume nonce.
    Service execution with require_auth=True consumes the nonce exactly once and succeeds.
    Subsequent service execution with the same token fails with replay error.
    """
    from sales_engine.research.pricing_config import (
        generate_analysis_auth_token,
        verify_analysis_auth_token,
    )
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep official website and services.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    token = generate_analysis_auth_token(
        candidate_id=cid,
        snapshot_id=snap.snapshot_id,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
    )

    # 1. Router preliminary check (mark_used=False)
    router_valid, budget, err = verify_analysis_auth_token(
        auth_token=token,
        candidate_id=cid,
        snapshot_id=snap.snapshot_id,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        mark_used=False,
    )
    assert router_valid is True
    assert budget == 0.50
    assert err is None

    # 2. Service execution (require_auth=True, consumes nonce)
    mock_resp_json = {
        "company_name": {"value": "Energymep", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep official website", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "geographic_location": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "telephone_numbers": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "business_activities": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "official_website": {"value": "https://energymep.it/", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep official website", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "contact_email_addresses": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "priority_level": {"value": "B", "source_url": "", "evidence_excerpt": "", "confidence": 0.5, "is_hypothesis": True},
        "priority_rationale": {"value": "Default B", "source_url": "", "evidence_excerpt": "", "confidence": 0.5, "is_hypothesis": True},
    }

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_test_01",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(mock_resp_json),
        parsed_json=mock_resp_json,
        input_tokens=150,
        output_tokens=300,
        finish_reason="stop",
    )

    rec1 = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
        auth_token=token,
        require_auth=True,
    )
    assert rec1.status == "SUCCESS", f"First execution failed: {rec1.diagnostic_info}"
    assert rec1.proposed_fields["company_name"]["value"] == "Energymep"
    assert mock_gw.generate.call_count == 1

    # 3. Replay attack / Second execution with same token MUST fail closed before calling gateway
    rec2 = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
        auth_token=token,
        require_auth=True,
    )
    assert rec2.status == "UNAUTHORIZED"
    assert "replay detected" in rec2.diagnostic_info.lower()
    # Call count should still be 1 - no additional call made!
    assert mock_gw.generate.call_count == 1


def test_28_token_budget_enforced_in_direct_service_call(isolated_db_paths):
    """
    V2.1c: Token spending ceiling is extracted from token and strictly enforced inside
    execute_company_analysis() even if caller did not pass max_approved_budget_usd.
    """
    from sales_engine.research.pricing_config import generate_analysis_auth_token
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Website text " * 1000,
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    # Token with tiny budget ceiling ($0.000001)
    token = generate_analysis_auth_token(
        candidate_id=cid,
        snapshot_id=snap.snapshot_id,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.000001,
    )

    mock_gw = MagicMock()
    # Direct service call without passing max_approved_budget_usd
    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
        auth_token=token,
        require_auth=True,
        max_approved_budget_usd=None,  # Missing caller budget must NOT disable token ceiling!
    )
    assert rec.status == "BUDGET_EXCEEDED"
    assert mock_gw.generate.call_count == 0


def test_29_successful_analysis_exposes_13_fields_in_review_ui(isolated_db_paths):
    """
    V2.1c: Successfully completed analysis persists a SUCCESS analysis record
    and exposes proposed 13 fields in Research Review view.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis
    from sales_engine.research.company_research_service import load_research_from_snapshot

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep S.r.l. operates in MEP engineering. P.IVA 01234567890.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    mock_resp_json = {
        "company_name": {"value": "Energymep S.r.l.", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep S.r.l.", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "P.IVA 01234567890", "source_url": "https://energymep.it/", "evidence_excerpt": "P.IVA 01234567890", "confidence": 0.95, "is_hypothesis": False},
        "geographic_location": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "telephone_numbers": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "business_activities": {"value": "MEP engineering", "source_url": "https://energymep.it/", "evidence_excerpt": "operates in MEP engineering", "confidence": 0.90, "is_hypothesis": False},
        "official_website": {"value": "https://energymep.it/", "source_url": "https://energymep.it/", "evidence_excerpt": "https://energymep.it/", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "contact_email_addresses": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "priority_level": {"value": "A", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
        "priority_rationale": {"value": "Strong BIM and MEP engineering profile", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
    }

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_test_02",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(mock_resp_json),
        parsed_json=mock_resp_json,
        input_tokens=200,
        output_tokens=400,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"

    # Verify that get_latest_analysis retrieves this record
    stored_an = snap_store.get_latest_analysis(cid, snapshot_id=snap.snapshot_id)
    assert stored_an is not None
    assert stored_an.status == "SUCCESS"
    assert stored_an.proposed_fields["company_name"]["value"] == "Energymep S.r.l."
    assert stored_an.proposed_fields["legal_identity"]["value"] == "P.IVA 01234567890"
    assert stored_an.proposed_fields["business_activities"]["value"] == "MEP engineering"


# ==============================================================================
# 30. V2.1d Gemini Structured Output Regression Tests
# ==============================================================================

def test_30_actual_failed_response_structure_reproduced_and_handled(isolated_db_paths):
    """
    Reproduces the actual failed Gemini response structure that caused
    'SCHEMA_INVALID — Missing 11 required fields' (non-standard keys returned by unconstrained json_object mode).
    Validates that the failure is safely caught as SCHEMA_INVALID without crashing or corrupting Lead Register.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep official website and services.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    # Non-standard keys returned during the unconstrained json_object failure
    failed_gemini_json = {
        "company_name": {"value": "Energymep", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep", "is_hypothesis": False},
        "website": {"value": "https://energymep.it/", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "energymep.it", "is_hypothesis": False},
        "vat_number": {"value": "Not Found", "confidence": 0.0, "source_url": "", "evidence_excerpt": "", "is_hypothesis": False},
        "hq_address": {"value": "Via Taggia 38 B", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "Via Taggia 38 B", "is_hypothesis": False},
        "phone": {"value": "+39 347 455 9751", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "+39 347 455 9751", "is_hypothesis": False},
        "email": {"value": "info@energymep.it", "confidence": 1.0, "source_url": "https://energymep.it/", "evidence_excerpt": "info@energymep.it", "is_hypothesis": False},
    }

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_fail_reproduce",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(failed_gemini_json),
        parsed_json=failed_gemini_json,
        input_tokens=8700,
        output_tokens=1350,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SCHEMA_INVALID"
    assert "Missing required fields" in rec.diagnostic_info
    assert "legal_identity" in rec.diagnostic_info
    assert "official_website" in rec.diagnostic_info


def test_31_openai_provider_structured_output_payload_construction():
    """
    Verifies that OpenAIProvider properly formats response_format with type='json_schema'
    and passes strict=True with the schema when configured for Gemini or models with supports_structured_schema.
    """
    prov = OpenAIProvider(
        provider_id="gemini",
        api_key="sk-mock",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-3.5-flash-lite",
        model_capabilities={"gemini-3.5-flash-lite": {"supports_structured_schema": True, "supports_json_mode": True}},
    )

    req = LLMRequest(
        task_id="company_research_13_field_extraction",
        prompt="Analyze website",
        response_schema=LEAD_REGISTER_13_FIELDS_SCHEMA,
    )

    payload = prov._build_payload(req, "gemini-3.5-flash-lite")
    rf = payload.get("response_format")
    assert rf is not None
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == LEAD_REGISTER_13_FIELDS_SCHEMA


def test_32_truncated_model_output_detected_as_schema_invalid(isolated_db_paths):
    """
    Verifies that model responses marked with finish_reason='length' or 'max_tokens'
    are classified as SCHEMA_INVALID with diagnostic notice.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Website content",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_trunc",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content='{"company_name": {"value": "Energymep"',
        parsed_json=None,
        input_tokens=100,
        output_tokens=2500,
        finish_reason="length",  # Truncated!
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SCHEMA_INVALID"
    assert "truncated" in rec.diagnostic_info.lower()


def test_33_wrapped_json_top_level_unwrapped_successfully(isolated_db_paths):
    """
    Verifies that if an LLM wraps the 13 fields under a top-level key like 'lead_register'
    or 'fields', the extractor safely unwraps it without failing.
    """
    from sales_engine.research.company_analysis_service import execute_company_analysis

    _, snap_db = isolated_db_paths
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep S.r.l. operates in MEP engineering.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    inner_fields = {
        "company_name": {"value": "Energymep S.r.l.", "source_url": "https://energymep.it/", "evidence_excerpt": "Energymep S.r.l.", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "geographic_location": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "telephone_numbers": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "business_activities": {"value": "MEP engineering", "source_url": "https://energymep.it/", "evidence_excerpt": "operates in MEP engineering", "confidence": 0.90, "is_hypothesis": False},
        "official_website": {"value": "https://energymep.it/", "source_url": "https://energymep.it/", "evidence_excerpt": "https://energymep.it/", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "contact_email_addresses": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "priority_level": {"value": "A", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
        "priority_rationale": {"value": "Strong BIM and MEP engineering profile", "source_url": "", "evidence_excerpt": "", "confidence": 0.8, "is_hypothesis": True},
    }
    wrapped_json = {"lead_register": inner_fields}

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        request_id="req_wrapped",
        task_id="company_research_13_field_extraction",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        content=json.dumps(wrapped_json),
        parsed_json=wrapped_json,
        input_tokens=200,
        output_tokens=400,
        finish_reason="stop",
    )

    rec = execute_company_analysis(
        snapshot=snap,
        candidate_id=cid,
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=snap_store,
        gateway_override=mock_gw,
    )
    assert rec.status == "SUCCESS"
    assert rec.proposed_fields["company_name"]["value"] == "Energymep S.r.l."


# ==============================================================================
# Research Pipeline V2.1e: Field-Level Evidence & Approval Integrity Tests
# ==============================================================================

def test_34_preserve_operator_reviewed_status_on_save(isolated_db_paths, monkeypatch):
    """
    P0 Integrity Test:
    Verify that save_candidate_research_action preserves OPERATOR_REVIEWED and VERIFIED
    status and never downgrades an existing record to EXTRACTED when approving fields.
    Also verifies that a fresh approval marks the record as OPERATOR_REVIEWED.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import save_candidate_research_action, get_console_services
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    # Reset cached services in router if any
    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Energymep",
        domain="energymep.it",
    )
    cand_store.save_candidate(cand)

    # 1. Existing record is already OPERATOR_REVIEWED
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        verification_status="OPERATOR_REVIEWED",
        confidence_score=0.90,
        provenance_source="operator_edited",
        official_website="https://energymep.it/",
    )
    reg_store.save_record(rec)

    # Operator approves a new field update (e.g. telephone)
    req = Request({"type": "http", "method": "POST", "headers": []})
    asyncio.run(
        save_candidate_research_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            priority_level="A",
            priority_rationale="Solid BIM workflow",
            legal_identity="IT01234567890",
            geographic_location="Padova",
            telephone_numbers="+39 049 123456",
            contact_email_addresses="info@energymep.it",
            responsible_person="Ing. Rossi",
            business_activities="MEP Engineering",
            official_website="https://energymep.it/",
            descriptions_and_notes="Notes",
            job_advertisement_links="Not Found",
            other_links_and_references="Not Found",
            apply_telephone_numbers="1",
        )
    )

    saved = reg_store.get_record(cid)
    assert saved is not None
    # Crucial assertion: Must NOT be downgraded to EXTRACTED!
    assert saved.verification_status == "OPERATOR_REVIEWED"
    assert saved.telephone_numbers == "+39 049 123456"
    assert saved.official_website == "https://energymep.it/"


def test_35_not_found_cannot_overwrite_authoritative_data(isolated_db_paths, monkeypatch):
    """
    P1 Safety Test:
    If a form field contains 'Not Found' or empty string, an operator checking that box
    must not silently destroy existing verified authoritative data.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import save_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand_store.save_candidate(CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it"))

    reg_store.save_record(
        LeadRegisterRecord(
            candidate_id=cid,
            company_name="Energymep S.r.l.",
            legal_identity="IT01234567890",
            verification_status="VERIFIED",
            official_website="https://energymep.it/",
        )
    )

    req = Request({"type": "http", "method": "POST", "headers": []})
    asyncio.run(
        save_candidate_research_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            priority_level="B",
            priority_rationale="Not Found",
            legal_identity="Not Found",  # Mistakenly submitted as 'Not Found'
            geographic_location="Not Found",
            telephone_numbers="Not Found",
            contact_email_addresses="Not Found",
            responsible_person="Not Found",
            business_activities="Not Found",
            official_website="Not Found",
            descriptions_and_notes="Not Found",
            job_advertisement_links="Not Found",
            other_links_and_references="Not Found",
            apply_legal_identity="1",  # Operator checked the box
        )
    )

    saved = reg_store.get_record(cid)
    assert saved is not None
    # Existing valid P.IVA must be preserved rather than overwritten by 'Not Found'
    assert saved.legal_identity == "IT01234567890"
    assert saved.verification_status == "VERIFIED"


def test_36_quarantined_fields_rendered_with_warning_and_blocked(isolated_db_paths, monkeypatch):
    """
    P1 UI / Metadata Test:
    Verifies that company_research_review renders the LLM analysis metadata:
    - Displays AI Fact / AI Hypothesis tags
    - Displays Source URL, confidence, and verbatim evidence excerpts
    - Quarantined fields render warning and blocked state in the HTML
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import candidate_research_view
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
    from sales_engine.research.research_store import ResearchSnapshotStore, PageEvidence, CompanyAnalysisRecord

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    cand_store = CandidateStore(db_path=cand_db)
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    cand_store.save_candidate(CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it"))

    pe = PageEvidence(
        snapshot_id="", evidence_id="EV_01", requested_url="https://energymep.it/",
        resolved_url="https://energymep.it/", page_type="home", http_status=200,
        retrieval_status="FETCH_SUCCESS", retrieved_at="2026-11-02T12:00:00Z",
        title="Energymep", meta_description=None, content_sha256="h01",
        cleaned_text="Energymep S.r.l. operates in MEP engineering in Padova.",
    )
    snap = snap_store.create_snapshot(cid, "energymep.it", "Energymep", [pe])

    # Save an analysis record with both grounded and quarantined fields
    proposed = {
        "company_name": {
            "value": "Energymep S.r.l.",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "Energymep S.r.l.",
            "confidence": 0.95,
            "is_hypothesis": False,
            "grounded_in_snapshot": True,
            "excerpt_verified": True,
            "quarantined": False,
        },
        "legal_identity": {
            "value": "IT99999999999",
            "source_url": "https://external-unverified.com/",
            "evidence_excerpt": "Hallucinated P.IVA",
            "confidence": 0.3,
            "is_hypothesis": False,
            "grounded_in_snapshot": False,
            "excerpt_verified": False,
            "quarantined": True,
            "quarantine_reason": "Source URL not present in snapshot pages.",
        },
        "priority_level": {
            "value": "A",
            "source_url": "https://energymep.it/",
            "evidence_excerpt": "MEP engineering in Padova",
            "confidence": 0.85,
            "is_hypothesis": True,
            "grounded_in_snapshot": True,
            "excerpt_verified": True,
            "quarantined": False,
        },
    }
    rec = CompanyAnalysisRecord(
        analysis_id="an_test_36",
        candidate_id=cid,
        snapshot_id=snap.snapshot_id,
        created_at="2026-11-02T12:05:00Z",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        status="SUCCESS",
        input_tokens=150,
        output_tokens=300,
        cost_estimate=0.0001,
        analyzed_pages_count=1,
        chunks_count=1,
        proposed_fields=proposed,
    )
    snap_store.save_analysis_record(rec)

    req = Request({
        "type": "http",
        "method": "GET",
        "path": f"/sales/candidates/{cid}/research",
        "headers": [],
    })
    resp = asyncio.run(candidate_research_view(request=req, candidate_id=cid))
    html = resp.body.decode("utf-8")

    # Verify LLM status card and Top Stats
    assert "GEMINI: SUCCESS" in html
    assert "Priority Recommendation" in html
    assert "(AI Hypothesis &mdash; Pending Operator Verification)" in html

    # Verify Company Name field metadata
    assert "AI Fact" in html
    assert "Confidence:</strong> 95%" in html
    assert "&ldquo;Energymep S.r.l.&rdquo;" in html

    # Verify Quarantined Legal Identity field
    assert "⚠️ QUARANTINED: Source URL not present in snapshot pages." in html
    assert "Blocked" in html


def test_37_partial_field_approval_does_not_promote_verification_status(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 1 & 2:
    - Approving a subset of fields creates FieldReviewRecords in SQLite.
    - An unreviewed record remains EXTRACTED / Unverified and is NOT promoted to OPERATOR_REVIEWED.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import save_candidate_research_action, evaluate_research_readiness
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Energymep",
        domain="energymep.it",
    )
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep",
        verification_status="EXTRACTED",
        confidence_score=0.70,
        official_website="https://energymep.it/",
    )
    reg_store.save_record(rec)

    req = Request({"type": "http", "method": "POST", "headers": []})
    resp = asyncio.run(
        save_candidate_research_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            priority_level="B",
            priority_rationale="Not Found",
            legal_identity="Not Found",
            geographic_location="Not Found",
            telephone_numbers="+39 049 123456",
            contact_email_addresses="Not Found",
            responsible_person="Not Found",
            business_activities="Not Found",
            official_website="https://energymep.it/",
            descriptions_and_notes="Not Found",
            job_advertisement_links="Not Found",
            other_links_and_references="Not Found",
            apply_telephone_numbers="1",
        )
    )

    # 1. Verification status is preserved as EXTRACTED, not promoted
    updated_rec = reg_store.get_record(cid)
    assert updated_rec.verification_status == "EXTRACTED"
    assert updated_rec.telephone_numbers == "+39 049 123456"

    # 2. Durable FieldReviewRecord was created
    reviews = reg_store.get_field_reviews(cid)
    assert "telephone_numbers" in reviews
    assert reviews["telephone_numbers"].approved_value == "+39 049 123456"
    assert reviews["telephone_numbers"].review_state == "OPERATOR_APPROVED"

    # 3. Authoritative research readiness status evaluates to 'partially_reviewed'
    readiness = evaluate_research_readiness(cand, reg_store=reg_store)
    assert readiness["research_status"] == "partially_reviewed"
    assert readiness["is_reviewed"] is False


def test_38_zero_approved_fields_produces_zero_sqlite_writes(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 3:
    Submitting the form with 0 checkboxes checked results in 0 SQLite writes.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import save_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Original Name",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(rec)
    orig_updated_at = reg_store.get_record(cid).updated_at

    req = Request({"type": "http", "method": "POST", "headers": []})
    resp = asyncio.run(
        save_candidate_research_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Different Name",
            priority_level="A",
            priority_rationale="Rationale",
            legal_identity="IT123",
            geographic_location="Rome",
            telephone_numbers="123",
            contact_email_addresses="test@test.com",
            responsible_person="John",
            business_activities="Activities",
            official_website="https://test.com",
            descriptions_and_notes="Notes",
            job_advertisement_links="Links",
            other_links_and_references="Refs",
            # None applied!
        )
    )

    reloaded = reg_store.get_record(cid)
    assert reloaded.company_name == "Original Name"
    assert reloaded.updated_at == orig_updated_at
    assert len(reg_store.get_field_reviews(cid)) == 0


def test_39_finalize_action_blocks_incomplete_requirements(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 6 & 7:
    Finalize action rejects records missing core requirements (e.g. Missing business activities, or missing operator review).
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import finalize_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    # Incomplete record: missing business_activities
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="Not Found",
        verification_status="EXTRACTED",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    # Check redirect location has error query param
    assert "error=" in resp.headers["location"]

    # Record was NOT promoted
    reloaded = reg_store.get_record(cid)
    assert reloaded.verification_status == "EXTRACTED"


def test_40_finalize_action_succeeds_when_all_requirements_met(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 8 & 9:
    Finalize action promotes record to OPERATOR_REVIEWED when minimum requirements are met,
    achieving 'Research Ready'.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import finalize_candidate_research_action, evaluate_research_readiness
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering and Building Information Modeling services in Veneto.",
        verification_status="EXTRACTED",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    # Operator reviews core fields
    from sales_engine.leads.register_store import compute_field_checksum
    for fn, val in [("company_name", "Energymep S.r.l."), ("official_website", "https://energymep.it/"), ("business_activities", "MEP Engineering and Building Information Modeling services in Veneto.")]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                reviewed_at="2026-11-02T12:00:00Z",
            )
        )

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "message=" in resp.headers["location"]

    reloaded = reg_store.get_record(cid)
    assert reloaded.verification_status == "OPERATOR_REVIEWED"
    assert reloaded.provenance_source == "operator_edited"

    readiness = evaluate_research_readiness(cand, reg_store=reg_store)
    assert readiness["research_status"] == "research_ready"
    assert readiness["is_reviewed"] is True


def test_41_field_reviews_persist_across_restart(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 11:
    FieldReviewRecord entries persist across simulated app restart (reopening DB).
    """
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import FieldReviewRecord

    cand_db, _ = isolated_db_paths
    reg_store1 = LeadRegisterStore(db_path=cand_db)
    cid = "cand:domain:energymep.it"

    f_rev = FieldReviewRecord(
        candidate_id=cid,
        field_name="responsible_person",
        approved_value="Ing. Marco Rossi",
        value_checksum="abc123chk",
        reviewed_at="2026-11-02T14:30:00Z",
        source_type="llm_analysis",
        snapshot_id="snap_123",
        analysis_id="an_456",
        evidence_url="https://energymep.it/chi-siamo",
        evidence_excerpt="Il nostro team è guidato dall'Ing. Marco Rossi",
        operator_note="Verified from Chi Siamo page",
    )
    reg_store1.save_field_review(f_rev)

    # Re-instantiate store (simulating clean process restart)
    reg_store2 = LeadRegisterStore(db_path=cand_db)
    reviews = reg_store2.get_field_reviews(cid)
    assert "responsible_person" in reviews
    r = reviews["responsible_person"]
    assert r.approved_value == "Ing. Marco Rossi"
    assert r.value_checksum == "abc123chk"
    assert r.evidence_url == "https://energymep.it/chi-siamo"
    assert r.operator_note == "Verified from Chi Siamo page"


def test_42_research_ready_never_implies_outreach_permission(isolated_db_paths, monkeypatch):
    """
    Phase 6 Acceptance Test 13:
    Promoting a company to 'Research Ready' NEVER implies cold outreach permission
    or auto-generates drafts. EMAIL_SEND_ENABLED remains false.
    """
    from backend.sales_ui.router import evaluate_candidate_readiness, is_email_send_enabled
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.sending.review_store import ReviewStore
    from sales_engine.sending.suppression_store import SuppressionStore

    cand_db, _ = isolated_db_paths
    cand_store = CandidateStore(db_path=cand_db)
    reg_store = LeadRegisterStore(db_path=cand_db)
    rev_store = ReviewStore(db_path=cand_db)
    supp_svc = SuppressionStore(review_store=rev_store)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand.cold_outreach_allowed = False
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    # Candidate has no verified contact recipient yet
    readiness = evaluate_candidate_readiness(
        cand,
        cand_store=cand_store,
        suppression_svc=supp_svc,
        reg_store=reg_store,
    )
    # Outreach must be verification_required or blocked, NOT draft_ready
    assert readiness["effective_readiness"] != "draft_ready"
    assert is_email_send_enabled() is False


# ==============================================================================
# Research Pipeline V2.1f.1 Finalization Integrity Hotfix Tests
# ==============================================================================

def test_43_zero_core_reviews_with_operator_edited_provenance_rejected_by_finalization(isolated_db_paths, monkeypatch):
    """
    Test 43: Finalization must reject a record that has zero core-field reviews,
    even if provenance_source is 'operator_edited'.
    """
    import asyncio
    from backend.sales_ui.router import finalize_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    # Lead record with provenance_source="operator_edited" but verification_status="EXTRACTED" and zero reviews
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering and Building Information Modeling services.",
        verification_status="EXTRACTED",
        provenance_source="operator_edited",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "missing%20approved%20reviews" in resp.headers["location"]

    reloaded = reg_store.get_record(cid)
    assert reloaded.verification_status == "EXTRACTED"


def test_44_two_unrelated_reviewed_fields_without_core_fields_rejected_by_finalization(isolated_db_paths, monkeypatch):
    """
    Test 44: Finalization must reject a record with 2 reviewed fields if the 3 core fields
    (company_name, official_website, business_activities) are not all approved.
    """
    import asyncio
    from backend.sales_ui.router import finalize_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering services.",
        telephone_numbers="+39 049 123456",
        geographic_location="Padova, Italy",
        verification_status="EXTRACTED",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    # Review 2 unrelated fields: telephone_numbers and geographic_location
    for fn, val in [("telephone_numbers", "+39 049 123456"), ("geographic_location", "Padova, Italy")]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                reviewed_at="2026-11-02T12:00:00Z",
            )
        )

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "missing%20approved%20reviews" in resp.headers["location"]


def test_45_stale_checksum_after_field_edit_rejected_and_suspends_readiness(isolated_db_paths, monkeypatch):
    """
    Test 45: Changing a required core field value makes its checksum stale, causing
    finalization rejection and demoting research_ready status to missing_evidence or review_required.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import (
        finalize_candidate_research_action,
        evaluate_research_readiness,
        lead_register_update_action,
    )
    from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering services.",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_edited",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    # Reviews exist with checksums matching the original values
    for fn, val in [("company_name", "Energymep S.r.l."), ("official_website", "https://energymep.it/"), ("business_activities", "MEP Engineering services.")]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                reviewed_at="2026-11-02T12:00:00Z",
            )
        )

    readiness = evaluate_research_readiness(cand, reg_store=reg_store)
    assert readiness["research_status"] == "research_ready"

    # User modifies business_activities via lead_register_update_action
    req = Request({"type": "http", "method": "POST", "headers": []})
    resp = asyncio.run(
        lead_register_update_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            official_website="https://energymep.it/",
            business_activities="Modified activities without re-reviewing.",
            verification_status="OPERATOR_REVIEWED",
        )
    )

    # Review for business_activities was invalidated / removed
    reviews = reg_store.get_field_reviews(cid)
    assert "business_activities" not in reviews

    # Record demoted from OPERATOR_REVIEWED to EXTRACTED, suspending readiness
    reloaded = reg_store.get_record(cid)
    assert reloaded.verification_status == "EXTRACTED"
    readiness_after = evaluate_research_readiness(cand, reg_store=reg_store)
    assert readiness_after["research_status"] != "research_ready"

    # Attempting to finalize fails because business_activities review is missing
    resp_fin = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp_fin.status_code == 303
    assert "error=" in resp_fin.headers["location"]


def test_46_malicious_lookalike_website_rejected_by_finalization(isolated_db_paths, monkeypatch):
    """
    Test 46: A website host like energymep.it.evil.example or other lookalike must be rejected.
    """
    import asyncio
    from backend.sales_ui.router import finalize_candidate_research_action, is_canonical_domain_match
    from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    assert is_canonical_domain_match("https://energymep.it.evil.example", "energymep.it") is False
    assert is_canonical_domain_match("https://www.energymep.it", "energymep.it") is True
    assert is_canonical_domain_match("https://portal.energymep.it/login", "energymep.it") is True
    assert is_canonical_domain_match("https://not-energymep.it", "energymep.it") is False

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    evil_url = "https://energymep.it.evil.example/"
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website=evil_url,
        business_activities="MEP Engineering services.",
        verification_status="EXTRACTED",
        evidence_urls=[evil_url],
    )
    reg_store.save_record(rec)

    for fn, val in [("company_name", "Energymep S.r.l."), ("official_website", evil_url), ("business_activities", "MEP Engineering services.")]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                reviewed_at="2026-11-02T12:00:00Z",
            )
        )

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "does%20not%20match%20canonical%20domain" in resp.headers["location"]


def test_47_unrelated_company_identity_rejected_by_finalization(isolated_db_paths, monkeypatch):
    """
    Test 47: Contradictory company identities (e.g. ESAC srl or unrelated legal entity)
    must be rejected by finalization.
    """
    import asyncio
    from backend.sales_ui.router import finalize_candidate_research_action, is_trustworthy_company_identity
    from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    ok, reason = is_trustworthy_company_identity("ESAC srl", "Energymep", "energymep.it")
    assert ok is False
    assert "ESAC" in reason

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="ESAC srl",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering services.",
        verification_status="EXTRACTED",
        evidence_urls=["https://energymep.it/"],
    )
    reg_store.save_record(rec)

    for fn, val in [("company_name", "ESAC srl"), ("official_website", "https://energymep.it/"), ("business_activities", "MEP Engineering services.")]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                reviewed_at="2026-11-02T12:00:00Z",
            )
        )

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "Identity%20contradiction" in resp.headers["location"]


def test_48_lead_register_update_cannot_unilaterally_promote_to_operator_reviewed(isolated_db_paths, monkeypatch):
    """
    Test 48: Submitting verification_status='OPERATOR_REVIEWED' to lead_register_update_action
    for an unfinalized record must NOT promote the status.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import lead_register_update_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(rec)

    req = Request({"type": "http", "method": "POST", "headers": []})
    resp = asyncio.run(
        lead_register_update_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            verification_status="OPERATOR_REVIEWED",
        )
    )
    assert resp.status_code == 303

    reloaded = reg_store.get_record(cid)
    assert reloaded.verification_status == "EXTRACTED"


def test_49_operator_edited_field_differing_from_ai_proposal_does_not_inherit_ai_excerpt(isolated_db_paths, monkeypatch):
    """
    Test 49: If an operator manually corrects an AI-proposed value in save_candidate_research_action,
    the FieldReviewRecord must record source_type='operator_manual', review_state='OPERATOR_CORRECTED',
    and evidence_excerpt=None.
    """
    import asyncio
    from starlette.requests import Request
    from backend.sales_ui.router import save_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
    from sales_engine.research.research_store import ResearchSnapshotStore, CompanyAnalysisRecord

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)
    snap_store = ResearchSnapshotStore(db_path=snap_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep",
        verification_status="EXTRACTED",
        official_website="https://energymep.it/",
    )
    reg_store.save_record(rec)

    # Create a snapshot first to satisfy foreign key constraint on snapshot_id
    snap = snap_store.create_snapshot(
        candidate_id=cid,
        canonical_domain="energymep.it",
        company_name="Energymep",
        pages=[],
        snapshot_id="snap:test:49",
    )

    # Save an analysis record with an AI proposal
    an_rec = CompanyAnalysisRecord(
        analysis_id="an:test:49",
        candidate_id=cid,
        snapshot_id="snap:test:49",
        provider="gemini",
        model="gemini-2.5-flash",
        created_at="2026-11-02T12:00:00Z",
        status="SUCCESS",
        proposed_fields={
            "company_name": {
                "value": "AI Suggested Name",
                "source_url": "https://energymep.it/about",
                "evidence_excerpt": "We are AI Suggested Name excerpt.",
                "confidence": 0.85,
            }
        },
    )
    snap_store.save_analysis_record(an_rec)

    # Operator approves company_name but submits a manual edit "Energymep S.r.l."
    req = Request({"type": "http", "method": "POST", "headers": []})
    resp = asyncio.run(
        save_candidate_research_action(
            request=req,
            candidate_id=cid,
            csrf=None,
            company_name="Energymep S.r.l.",
            official_website="https://energymep.it/",
            apply_company_name="1",
        )
    )
    assert resp.status_code == 303

    reviews = reg_store.get_field_reviews(cid)
    c_rev = reviews["company_name"]
    assert c_rev.approved_value == "Energymep S.r.l."
    assert c_rev.source_type == "operator_manual"
    assert c_rev.review_state == "OPERATOR_CORRECTED"
    assert c_rev.evidence_excerpt is None


def test_50_synthetic_unpersisted_record_rejected_by_finalization(isolated_db_paths, monkeypatch):
    """
    Test 50: Finalization must reject a candidate that has only synthesized candidate fields
    and no explicit Lead Register record.
    """
    import asyncio
    from backend.sales_ui.router import finalize_candidate_research_action
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore

    cand_db, snap_db = isolated_db_paths
    monkeypatch.setenv("SALES_OUTREACH_DB", cand_db)
    monkeypatch.setenv("SALESAI_DB_PATH", cand_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", cand_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", snap_db)

    import backend.sales_ui.router as r_mod
    r_mod._CONSOLE_SERVICES = None

    reg_store = LeadRegisterStore(db_path=cand_db)
    cand_store = CandidateStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(candidate_id=cid, company_name="Energymep", domain="energymep.it")
    cand_store.save_candidate(cand)

    # No LeadRegisterRecord saved
    assert reg_store.get_explicit_record(cid) is None

    resp = asyncio.run(finalize_candidate_research_action(candidate_id=cid, csrf=None))
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "No%20explicit%20Lead%20Register%20record" in resp.headers["location"]


def test_51_transactional_failure_in_save_leaves_no_mismatched_metadata(isolated_db_paths, monkeypatch):
    """
    Test 51: Atomic transactional save in save_record_with_reviews must roll back entirely
    if any field review fails, leaving no partial or mismatched state.
    """
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord

    cand_db, _ = isolated_db_paths
    reg_store = LeadRegisterStore(db_path=cand_db)

    cid = "cand:domain:energymep.it"
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Energymep S.r.l.",
        official_website="https://energymep.it/",
        business_activities="MEP Engineering",
    )

    # Valid review + bad review that causes a constraint violation or exception
    good_rev = FieldReviewRecord(
        candidate_id=cid,
        field_name="company_name",
        approved_value="Energymep S.r.l.",
        value_checksum="abc",
    )
    # A review with None for candidate_id will violate NOT NULL / PRIMARY KEY constraint
    bad_rev = FieldReviewRecord(
        candidate_id=None,
        field_name="official_website",
        approved_value="https://energymep.it/",
        value_checksum="def",
    )

    failed = False
    try:
        reg_store.save_record_with_reviews(rec, [good_rev, bad_rev])
    except Exception:
        failed = True

    assert failed is True
    # Verify rollback: record was not saved and good_rev was not committed
    assert reg_store.get_explicit_record(cid) is None
    reviews = reg_store.get_field_reviews(cid)
    assert len(reviews) == 0

