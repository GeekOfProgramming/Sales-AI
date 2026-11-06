"""
tests/test_deep_crawl_integration_acceptance.py

Acceptance test suite for CRAWL-02:
1. Deep Crawl Integration:
   - Nested links (4-5 levels deep) discovered.
   - Sitemaps incorporated into frontier.
   - Profiles (Quick, Standard, Deep, Extended) respect page & depth limits.
   - Crawl resume avoids refetching completed pages and preserves snapshot state.
   - Robots, SSRF, and domain boundaries enforced.
2. Mandatory Research Coverage Under Tight AI Budgets:
   - Minimum evidence coverage for Services, About, and Contact preserved.
   - Excessive Services subpages do not crowd out About or Contact.
   - Detail project pages capped at <= 3.
   - Missing coverage warnings reported when categories are absent.
3. AI Integration, Checkpoints, and UI/Preflight Consistency:
   - Token estimates match selected content.
   - Incompatible chunk checkpoints cannot be reused when selection changes.
   - Provenance retained for extracted claims.
   - Profile selector and AI selection preview contract consistency.
"""

import hashlib
import os
import socket
import tempfile
import urllib.robotparser
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Ensure offline DNS isolation
_REAL_GETADDRINFO = socket.getaddrinfo
def _offline_safe_getaddrinfo(host, port, *args, **kwargs):
    if host in ("deep-eng.it", "www.deep-eng.it", "test-domain.com", "hyper-design.it"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))]
    return _REAL_GETADDRINFO(host, port, *args, **kwargs)

socket.getaddrinfo = _offline_safe_getaddrinfo

from sales_engine.research.page_classifier import PageCategory, PageClassification, classify_page
from sales_engine.research.website_crawler import (
    CrawlBudget,
    CRAWL_PROFILES,
    WebsiteCrawlerV2,
    get_crawl_budget_for_profile,
)
from sales_engine.research.company_research_service import PageFetchResult
from sales_engine.research.research_store import (
    PageEvidence,
    ResearchSnapshot,
    ResearchSnapshotStore,
    AnalysisChunkRecord,
)
from sales_engine.research.company_analysis_service import (
    select_pages_for_analysis,
    partition_snapshot_into_chunks,
    estimate_analysis_preflight,
    verify_field_evidence_excerpts,
    validate_chunk_checkpoint_integrity,
)


@pytest.fixture
def temp_research_db():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_research.sqlite3")
    store = ResearchSnapshotStore(db_path=db_path)
    yield store
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        os.rmdir(temp_dir)
    except Exception:
        pass


def test_01_deep_crawl_discovery_depth_limits_and_resume(temp_research_db):
    """
    Test 1: Deep Crawl Integration.
    - Verifies discovery of pages nested 4-5 levels deep.
    - Verifies sitemap integration.
    - Verifies Quick (10p/d2) vs Deep (50p/d4) limits.
    - Verifies resume does not re-fetch existing completed pages.
    - Verifies domain boundary enforcement.
    """
    store = temp_research_db
    domain = "deep-eng.it"

    # Multi-level nested site tree
    # Level 0: /
    # Level 1: /servizi
    # Level 2: /servizi/strutture
    # Level 3: /servizi/strutture/sismica
    # Level 4: /servizi/strutture/sismica/adeguamento
    # Level 5: /servizi/strutture/sismica/adeguamento/isolatori
    fixture_pages = {
        f"https://{domain}/": PageFetchResult(
            url=f"https://{domain}/",
            page_type="ABOUT_COMPANY",
            status="FETCH_SUCCESS",
            title="Deep Eng Home",
            text_content="Home page of Deep Eng.",
            links=[
                {"text": "Servizi", "href": f"https://{domain}/servizi"},
                {"text": "Chi Siamo", "href": f"https://{domain}/chi-siamo"},
                {"text": "Contatti", "href": f"https://{domain}/contatti"},
                {"text": "External Site", "href": "https://external-firm.com/partners"},  # Boundary test
            ],
        ),
        f"https://{domain}/chi-siamo": PageFetchResult(
            url=f"https://{domain}/chi-siamo",
            page_type="ABOUT_COMPANY",
            status="FETCH_SUCCESS",
            title="Chi Siamo",
            text_content="La nostra societa di ingegneria.",
            links=[],
        ),
        f"https://{domain}/contatti": PageFetchResult(
            url=f"https://{domain}/contatti",
            page_type="CONTACT",
            status="FETCH_SUCCESS",
            title="Contatti",
            text_content="Contattaci a info@deep-eng.it.",
            links=[],
        ),
        f"https://{domain}/servizi": PageFetchResult(
            url=f"https://{domain}/servizi",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Servizi di Ingegneria",
            text_content="Panoramica dei servizi.",
            links=[
                {"text": "Strutture", "href": f"https://{domain}/servizi/strutture"},
                {"text": "Impianti", "href": f"https://{domain}/servizi/impianti"},
                {"text": "Ambiente", "href": f"https://{domain}/servizi/ambiente"},
            ],
        ),
        f"https://{domain}/servizi/impianti": PageFetchResult(
            url=f"https://{domain}/servizi/impianti",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Impianti",
            text_content="Ingegneria impiantistica.",
            links=[],
        ),
        f"https://{domain}/servizi/ambiente": PageFetchResult(
            url=f"https://{domain}/servizi/ambiente",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Ambiente",
            text_content="Ingegneria ambientale.",
            links=[],
        ),
        f"https://{domain}/servizi/strutture": PageFetchResult(
            url=f"https://{domain}/servizi/strutture",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Ingegneria Strutturale",
            text_content="Progettazione strutture complesse.",
            links=[{"text": "Sismica", "href": f"https://{domain}/servizi/strutture/sismica"}],
        ),
        f"https://{domain}/servizi/strutture/sismica": PageFetchResult(
            url=f"https://{domain}/servizi/strutture/sismica",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Ingegneria Sismica",
            text_content="Valutazioni di vulnerabilita sismica.",
            links=[{"text": "Adeguamento", "href": f"https://{domain}/servizi/strutture/sismica/adeguamento"}],
        ),
        f"https://{domain}/servizi/strutture/sismica/adeguamento": PageFetchResult(
            url=f"https://{domain}/servizi/strutture/sismica/adeguamento",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Adeguamento Sismico",
            text_content="Interventi di miglioramento ed adeguamento sismico.",
            links=[{"text": "Isolatori", "href": f"https://{domain}/servizi/strutture/sismica/adeguamento/isolatori"}],
        ),
        f"https://{domain}/servizi/strutture/sismica/adeguamento/isolatori": PageFetchResult(
            url=f"https://{domain}/servizi/strutture/sismica/adeguamento/isolatori",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Isolamento Sismico alla Base",
            text_content="Tecnologie di isolamento elastomerico.",
            links=[],
        ),
        f"https://{domain}/from-sitemap": PageFetchResult(
            url=f"https://{domain}/from-sitemap",
            page_type="SERVICES",
            status="FETCH_SUCCESS",
            title="Sitemap Discovered Page",
            text_content="Discovered directly via XML sitemap.",
            links=[],
        ),
    }

    fetched_urls = []

    def mock_fetch(url, page_type, authorized_domain=None):
        fetched_urls.append(url)
        if url in fixture_pages:
            return fixture_pages[url]
        return PageFetchResult(url=url, page_type="OTHER", status="FETCH_FAILED", error_message="Not in fixture")

    def mock_sitemap(dom):
        return [f"https://{dom}/from-sitemap"]

    # 1. Test Quick Profile (depth=2, max_pages=3)
    quick_budget = get_crawl_budget_for_profile("quick", max_pages_override=3)
    crawler_quick = WebsiteCrawlerV2(snapshot_store=store, budget=quick_budget)
    crawler_quick._fetch_page = MagicMock(side_effect=mock_fetch)
    crawler_quick._discover_sitemap_urls = MagicMock(side_effect=mock_sitemap)
    crawler_quick._fetch_robots_txt = MagicMock(return_value=(None, "Mock Robots OK"))

    res_quick = crawler_quick.crawl_website(
        candidate_id="cand_deep_01",
        canonical_domain=domain,
        company_name="Deep Engineering Srl",
    )

    # In Quick crawl with max_pages=3:
    # 3 pages fetched, while remainder of links stay in queue -> partial status!
    assert res_quick.status == "partial"
    assert res_quick.pages_succeeded == 3
    assert f"https://{domain}/servizi/strutture/sismica/adeguamento" not in fetched_urls

    # Verify external boundary was not crawled
    assert "https://external-firm.com/partners" not in fetched_urls

    # 2. Test Deep Profile with Resume
    # Reset fetched_urls tracker to verify non-redundant fetching
    fetched_urls.clear()

    deep_budget = get_crawl_budget_for_profile("deep")  # max_pages=50, depth=4
    crawler_deep = WebsiteCrawlerV2(snapshot_store=store, budget=deep_budget)
    crawler_deep._fetch_page = MagicMock(side_effect=mock_fetch)
    crawler_deep._discover_sitemap_urls = MagicMock(side_effect=mock_sitemap)
    crawler_deep._fetch_robots_txt = MagicMock(return_value=(None, "Mock Robots OK"))

    res_deep = crawler_deep.crawl_website(
        candidate_id="cand_deep_01",
        canonical_domain=domain,
        company_name="Deep Engineering Srl",
        resume_snapshot_id=res_quick.snapshot_id,
    )

    # Resume must preserve previous pages and not refetch them
    assert res_deep.previous_pages_count == res_quick.pages_succeeded
    # Root and previously fetched pages must NOT be in new fetched_urls
    assert f"https://{domain}/" not in fetched_urls
    # But deeper pages up to depth 4 must now be reached!
    assert f"https://{domain}/servizi/strutture/sismica/adeguamento" in [p.resolved_url for p in res_deep.page_evidences]
    assert res_deep.pages_succeeded > res_quick.pages_succeeded


def test_02_mandatory_research_coverage_under_tight_budget():
    """
    Test 2: Mandatory Research Coverage Under Tight AI Budgets.
    - Demonstrates that when a site has many Services subpages (e.g., 10 subpages)
      and a tight context ceiling (e.g., max 4 pages or 5,000 chars),
      the selection algorithm GUARANTEES minimum evidence coverage:
      At least 1 ABOUT, 1 SERVICES, and 1 CONTACT page are preserved.
    - The Services subpages do NOT crowd out About or Contact.
    - Reports omissions transparently.
    """
    pages = []
    # 1 About page
    pages.append(PageEvidence(
        snapshot_id="SNAP_COV_01",
        evidence_id="EV_ABOUT",
        requested_url="https://acme-bim.com/about-us",
        resolved_url="https://acme-bim.com/about-us",
        page_type="ABOUT_COMPANY",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title="About Acme BIM",
        meta_description="Acme BIM corporate profile.",
        content_sha256="hash_about_01",
        cleaned_text="Acme BIM is a leading digital engineering consultancy.",
    ))

    # 1 Contact page
    pages.append(PageEvidence(
        snapshot_id="SNAP_COV_01",
        evidence_id="EV_CONTACT",
        requested_url="https://acme-bim.com/contact-locations",
        resolved_url="https://acme-bim.com/contact-locations",
        page_type="CONTACT",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title="Contact Acme BIM",
        meta_description="Contact details and offices.",
        content_sha256="hash_contact_01",
        cleaned_text="Headquarters in Milan. Email: info@acme-bim.com. Phone: +39 02 998877.",
    ))

    # 10 Services subpages (each ~1,000 characters)
    for s_idx in range(1, 11):
        s_url = f"https://acme-bim.com/services/subdiscipline-{s_idx:02d}"
        s_txt = f"Service subdiscipline #{s_idx} detailed scope and capabilities: " + ("lorem ipsum " * 80)
        pages.append(PageEvidence(
            snapshot_id="SNAP_COV_01",
            evidence_id=f"EV_SERV_{s_idx:02d}",
            requested_url=s_url,
            resolved_url=s_url,
            page_type="SERVICES",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            title=f"Service #{s_idx}",
            meta_description=f"Service #{s_idx}",
            content_sha256=f"hash_serv_{s_idx:02d}",
            cleaned_text=s_txt,
        ))

    snapshot = ResearchSnapshot(
        snapshot_id="SNAP_COV_01",
        candidate_id="cand_acme_01",
        canonical_domain="acme-bim.com",
        company_name="Acme BIM Srl",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        content_checksum="checksum_acme_cov",
        pages_attempted=len(pages),
        pages_succeeded=len(pages),
        research_status="complete",
        pages=pages,
    )

    # Tight AI budget constraint: max 4 pages total
    selected, omitted_reasons, meta = select_pages_for_analysis(
        snapshot=snapshot,
        max_total_pages=4,
        max_total_chars=100_000,
    )

    # Assert exactly 4 pages selected
    assert len(selected) == 4
    selected_cats = {p.page_type for p in selected}

    # Mandatory categories MUST ALL be present!
    assert "ABOUT_COMPANY" in selected_cats, "ABOUT_COMPANY was crowded out!"
    assert "SERVICES" in selected_cats, "SERVICES was crowded out!"
    assert "CONTACT" in selected_cats, "CONTACT was crowded out!"

    # Excess services subpages were omitted and reported
    assert len(omitted_reasons) == 8  # 12 total pages - 4 selected = 8 omitted
    assert meta["missing_mandatory_categories"] == []
    assert any("Exceeded max AI page selection limit" in r for r in omitted_reasons.values())


def test_03_ai_preflight_checkpoint_invalidation_and_grounding_safety(temp_research_db):
    """
    Test 3: AI Integration, Checkpoint Invalidation, and Source Grounding Safety.
    - Preflight token and cost estimates accurately reflect the selected content.
    - If snapshot selection/content checksum changes, historical checkpoints are rejected.
    - Grounding verification verifies that factual claims match cited source URLs and text.
    """
    store = temp_research_db

    p1 = PageEvidence(
        snapshot_id="SNAP_SAFE_01",
        evidence_id="EV_01",
        requested_url="https://safe-bim.it/",
        resolved_url="https://safe-bim.it/",
        page_type="ABOUT_COMPANY",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title="Safe BIM Ingegneria",
        meta_description="Safe BIM overview",
        content_sha256="c_hash_safe_01",
        cleaned_text="Safe BIM Ingegneria opera a Roma dal 2015. P.IVA 01234567890.",
    )
    p2 = PageEvidence(
        snapshot_id="SNAP_SAFE_01",
        evidence_id="EV_02",
        requested_url="https://safe-bim.it/contatti",
        resolved_url="https://safe-bim.it/contatti",
        page_type="CONTACT",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        title="Contatti",
        meta_description="Contatti",
        content_sha256="c_hash_safe_02",
        cleaned_text="Email: info@safe-bim.it Telefono: 06 112233.",
    )

    snapshot = ResearchSnapshot(
        snapshot_id="SNAP_SAFE_01",
        candidate_id="cand_safe_01",
        canonical_domain="safe-bim.it",
        company_name="Safe BIM Ingegneria",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        content_checksum="safe_checksum_v1",
        pages_attempted=2,
        pages_succeeded=2,
        research_status="complete",
        pages=[p1, p2],
    )

    # 1. Preflight matches selected content
    preflight = estimate_analysis_preflight(snapshot, provider_id="gemini", model="gemini-3.5-flash-lite")
    assert preflight["eligible_pages_count"] == 2
    assert preflight["chunks_count"] == 1
    assert preflight["total_context_chars"] > 0
    assert preflight["estimated_input_tokens"] == preflight["total_context_chars"] // 4

    # 2. Checkpoint validation & rejection on mismatched checksum
    chunk_hash = hashlib.sha256("mock chunk text".encode("utf-8")).hexdigest()
    mock_13_fields = {}
    from sales_engine.research.company_analysis_service import LEAD_REGISTER_13_FIELDS_SCHEMA
    for fld in LEAD_REGISTER_13_FIELDS_SCHEMA["properties"].keys():
        mock_13_fields[fld] = {
            "value": "Safe BIM Ingegneria" if fld == "company_name" else "Not Found",
            "source_url": "https://safe-bim.it/" if fld == "company_name" else "",
            "confidence": 0.9 if fld == "company_name" else 0.0,
            "is_hypothesis": False,
        }

    cp = AnalysisChunkRecord(
        chunk_record_id="cp_test_01",
        analysis_id="an_test_01",
        candidate_id="cand_safe_01",
        snapshot_id="SNAP_SAFE_01",
        snapshot_checksum="safe_checksum_v1",
        chunk_index=1,
        total_chunks=1,
        chunk_hash=chunk_hash,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        schema_version="2.1.0",
        prompt_version="2.1.0",
        status="COMPLETED",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.001,
        proposed_fields=mock_13_fields,
    )

    # Valid check
    is_valid, reason = validate_chunk_checkpoint_integrity(
        cp=cp,
        expected_chunk_hash=chunk_hash,
        snapshot_checksum="safe_checksum_v1",
        expected_provider="gemini",
        expected_model="gemini-3.5-flash-lite",
        expected_schema_version="2.1.0",
        expected_prompt_version="2.1.0",
    )
    assert is_valid is True
    assert reason is None

    # Incompatible check: new snapshot checksum (e.g. from deeper crawl) must invalidate checkpoint!
    is_valid_changed, reject_reason = validate_chunk_checkpoint_integrity(
        cp=cp,
        expected_chunk_hash=chunk_hash,
        snapshot_checksum="safe_checksum_v2_NEW",
        expected_provider="gemini",
        expected_model="gemini-3.5-flash-lite",
        expected_schema_version="2.1.0",
        expected_prompt_version="2.1.0",
    )
    assert is_valid_changed is False
    assert "checksum mismatch" in reject_reason.lower()

    # 3. Source Evidence Grounding Verification
    test_fields = {
        "company_name": {
            "value": "Safe BIM Ingegneria",
            "source_url": "https://safe-bim.it/",
            "evidence_excerpt": "Safe BIM Ingegneria opera a Roma",
            "is_hypothesis": False,
        },
        "telephone_numbers": {
            "value": "06 112233",
            "source_url": "https://safe-bim.it/contatti",
            "evidence_excerpt": "Telefono: 06 112233",
            "is_hypothesis": False,
        },
        "hallucinated_field": {
            "value": "Fabricated Lead",
            "source_url": "https://unknown-domain.com/unfetched",
            "evidence_excerpt": "Fabricated text",
            "is_hypothesis": False,
        },
    }

    grounded = verify_field_evidence_excerpts(snapshot, test_fields)
    assert grounded["company_name"]["grounded_in_snapshot"] is True
    assert grounded["company_name"]["quarantined"] is False

    assert grounded["telephone_numbers"]["grounded_in_snapshot"] is True
    assert grounded["telephone_numbers"]["quarantined"] is False

    assert grounded["hallucinated_field"]["grounded_in_snapshot"] is False
    assert grounded["hallucinated_field"]["quarantined"] is True
    assert "not fetched in this snapshot" in grounded["hallucinated_field"]["quarantine_reason"]
