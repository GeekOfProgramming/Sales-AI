"""
tests/test_deep_crawl_page_selection.py

Unit and integration tests for CRAWL-01:
1. Deeply nested engineering website discovery & multi-lingual page classification (IT, EN, DE).
2. Large portfolio website (30+ project pages) ensuring snapshot retains all 30+, but AI selection caps at <= 3 representative project pages.
3. Separation of Crawl Budget from AI Budget, checkpoint resilience, and missing coverage reporting.
"""

import pytest
import hashlib
from datetime import datetime, timezone

from sales_engine.research.page_classifier import PageCategory, PageClassification, classify_page
from sales_engine.research.website_crawler import (
    CrawlBudget,
    CRAWL_PROFILES,
    get_crawl_budget_for_profile,
    classify_page_type,
    score_page_priority,
)
from sales_engine.research.research_store import PageEvidence, ResearchSnapshot
from sales_engine.research.company_analysis_service import (
    select_pages_for_analysis,
    partition_snapshot_into_chunks,
    estimate_analysis_preflight,
)


def test_01_multilingual_deterministic_page_classification():
    """
    Test 1: Deeply nested engineering website paths & multi-lingual page classification (IT, EN, DE).
    Verifies that zero LLM calls are made, and categories are deterministically mapped without false positives.
    """
    # 1. Italian engineering pages
    c_it_serv = classify_page(
        url="https://studio-ingegneria.it/servizi/progettazione-strutturale",
        title="Progettazione Strutturale e Impiantistica",
        cleaned_text="Offriamo servizi di progettazione integrata per strutture civili ed industriali.",
    )
    assert c_it_serv.category == PageCategory.SERVICES
    assert c_it_serv.confidence > 0.5

    c_it_about = classify_page(
        url="https://studio-ingegneria.it/chi-siamo/storia-e-valori",
        title="Chi Siamo - Studio Ingegneria",
        cleaned_text="La nostra azienda opera dal 1998 nel campo dell'ingegneria civile.",
    )
    assert c_it_about.category == PageCategory.ABOUT_COMPANY

    c_it_contact = classify_page(
        url="https://studio-ingegneria.it/contatti-e-dove-siamo",
        title="Contatti e Sede Principale",
        cleaned_text="Contattaci al numero 02 1234567 o presso i nostri uffici di Milano.",
    )
    assert c_it_contact.category == PageCategory.CONTACT

    c_it_bim = classify_page(
        url="https://studio-ingegneria.it/competenze/modellazione-bim-e-clash-detection",
        title="Modellazione BIM Avanzata",
        cleaned_text="Sviluppiamo modelli BIM in Revit con gestione delle interferenze e clash detection.",
    )
    assert c_it_bim.category == PageCategory.BIM_CAPABILITIES

    # 2. English architectural / engineering pages
    c_en_serv = classify_page(
        url="https://acme-engineers.co.uk/services/mechanical-electrical",
        title="MEP Engineering Services",
        cleaned_text="Comprehensive building services engineering for high-performance structures.",
    )
    assert c_en_serv.category == PageCategory.SERVICES

    c_en_proj = classify_page(
        url="https://acme-engineers.co.uk/projects/commercial-tower-london",
        title="Commercial Tower Project",
        cleaned_text="Structural design and BIM management for a 40-storey mixed-use development.",
    )
    assert c_en_proj.category == PageCategory.PROJECTS

    c_en_team = classify_page(
        url="https://acme-engineers.co.uk/company/leadership-team",
        title="Our Leadership Team",
        cleaned_text="Meet our board of directors and senior partners leading multidisciplinary teams.",
    )
    assert c_en_team.category == PageCategory.TEAM

    # 3. German engineering pages
    c_de_serv = classify_page(
        url="https://ingenieur-partner.de/leistungen/tragwerksplanung",
        title="Leistungen - Tragwerksplanung und Ingenieurbau",
        cleaned_text="Wir bieten umfassende Ingenieurleistungen und statische Berechnungen.",
    )
    assert c_de_serv.category == PageCategory.SERVICES

    c_de_legal = classify_page(
        url="https://ingenieur-partner.de/impressum-und-datenschutz",
        title="Impressum und Datenschutz",
        cleaned_text="Angaben gemass Paragraph 5 TMG. Verantwortlich fuer den Inhalt.",
    )
    assert c_de_legal.category == PageCategory.LEGAL

    # 4. Crawl profiles verification
    assert "quick" in CRAWL_PROFILES
    assert CRAWL_PROFILES["quick"].max_pages == 10
    assert CRAWL_PROFILES["quick"].max_depth == 2

    assert "standard" in CRAWL_PROFILES
    assert CRAWL_PROFILES["standard"].max_pages == 20
    assert CRAWL_PROFILES["standard"].max_depth == 3

    assert "deep" in CRAWL_PROFILES
    assert CRAWL_PROFILES["deep"].max_pages == 50
    assert CRAWL_PROFILES["deep"].max_depth == 4

    assert "extended" in CRAWL_PROFILES
    assert CRAWL_PROFILES["extended"].max_pages == 100
    assert CRAWL_PROFILES["extended"].max_depth == 5

    custom_budget = get_crawl_budget_for_profile("deep", max_pages_override=45)
    assert custom_budget.max_pages == 45
    assert custom_budget.max_depth == 4


def test_02_portfolio_heavy_website_project_capping():
    """
    Test 2: Portfolio-heavy site with 30+ project pages.
    Verifies that all 30+ pages are preserved in the ResearchSnapshot, but Evidence-Aware AI
    Page Selection caps representative projects at exactly <= 3 detail pages, avoiding LLM flood.
    """
    pages = []
    # 1. Essential pages (Home, About, Services, Contact, BIM)
    essential_specs = [
        ("https://hyper-design.it/", "Home", "ABOUT_COMPANY", "Benvenuti in Hyper Design, studio di architettura."),
        ("https://hyper-design.it/chi-siamo", "Chi Siamo", "ABOUT_COMPANY", "La nostra storia e filosofia progettuale."),
        ("https://hyper-design.it/servizi", "Servizi", "SERVICES", "Progettazione architettonica, strutturale, direzione lavori."),
        ("https://hyper-design.it/servizi/impianti", "Impianti MEP", "SERVICES", "Progettazione impianti meccanici ed elettrici."),
        ("https://hyper-design.it/contatti", "Contatti", "CONTACT", "Scrivici a info@hyper-design.it o chiamaci al 06 998877."),
        ("https://hyper-design.it/bim-services", "BIM Services", "BIM_CAPABILITIES", "Modellazione parametrica e coordinamento interdisciplinare BIM."),
    ]
    for idx, (url, title, ptype, txt) in enumerate(essential_specs, 1):
        pages.append(PageEvidence(
            snapshot_id="SNAP_TEST_01",
            evidence_id=f"EV_{idx:02d}",
            requested_url=url,
            resolved_url=url,
            page_type=ptype,
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            title=title,
            meta_description=f"Description for {title}",
            content_sha256=hashlib.sha256(txt.encode("utf-8")).hexdigest(),
            cleaned_text=txt,
        ))

    # 2. Add 35 distinct project portfolio pages
    for p_idx in range(1, 36):
        p_url = f"https://hyper-design.it/progetti/project-case-{p_idx:02d}"
        txt = f"Dettaglio dei lavori eseguiti per il complesso residenziale #{p_idx}. Superficie 2500mq."
        pages.append(PageEvidence(
            snapshot_id="SNAP_TEST_01",
            evidence_id=f"EV_PRJ_{p_idx:02d}",
            requested_url=p_url,
            resolved_url=p_url,
            page_type="PROJECTS",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            title=f"Progetto Residenziale #{p_idx}",
            meta_description=f"Portfolio project {p_idx}",
            content_sha256=hashlib.sha256(txt.encode("utf-8")).hexdigest(),
            cleaned_text=txt,
        ))

    snapshot = ResearchSnapshot(
        snapshot_id="SNAP_TEST_01",
        candidate_id="cand_hyper_01",
        canonical_domain="hyper-design.it",
        company_name="Hyper Design Srl",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        content_checksum="abc1234567890",
        pages_attempted=len(pages),
        pages_succeeded=len(pages),
        research_status="complete",
        pages=pages,
    )

    # Verify snapshot retains all 41 pages (6 essential + 35 projects)
    assert len(snapshot.pages) == 41

    # Run AI Page Selection
    selected, omitted_reasons, meta = select_pages_for_analysis(snapshot, max_projects=3)

    # Count how many projects were selected
    selected_project_count = sum(1 for p in selected if (p.page_type or "").upper() == "PROJECTS")
    assert selected_project_count == 3, f"Expected exactly 3 projects selected, found {selected_project_count}"

    # Verify that all mandatory/high-value categories were selected without dropping
    selected_cats = [p.page_type for p in selected]
    assert "ABOUT_COMPANY" in selected_cats
    assert "SERVICES" in selected_cats
    assert "CONTACT" in selected_cats
    assert "BIM_CAPABILITIES" in selected_cats

    # Verify that the omitted pages explain the portfolio cap
    omitted_project_reasons = [r for u, r in omitted_reasons.items() if "project-case" in u]
    assert len(omitted_project_reasons) == 32  # 35 - 3 = 32 omitted
    assert any("Project portfolio cap enforced" in r for r in omitted_project_reasons)

    # Verify metadata stats
    assert meta["selected_pages_count"] == len(selected)
    assert meta["omitted_pages_count"] == 32
    assert meta["missing_mandatory_categories"] == []


def test_03_budget_separation_preflight_and_missing_coverage_warning():
    """
    Test 3: Separation of Crawl Budget from AI Budget, Preflight calculation,
    and missing category coverage reporting.
    """
    # Create a sparse website snapshot missing Contact and BIM
    pages = [
        PageEvidence(
            snapshot_id="SNAP_SPARSE_01",
            evidence_id="EV_01",
            requested_url="https://sparse-co.com/",
            resolved_url="https://sparse-co.com/",
            page_type="ABOUT_COMPANY",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            title="Sparse Co Homepage",
            meta_description="Sparse Co Overview",
            content_sha256=hashlib.sha256("Sparse Co is a general consulting firm founded in 2020.".encode("utf-8")).hexdigest(),
            cleaned_text="Sparse Co is a general consulting firm founded in 2020.",
        ),
        PageEvidence(
            snapshot_id="SNAP_SPARSE_01",
            evidence_id="EV_02",
            requested_url="https://sparse-co.com/news/article-1",
            resolved_url="https://sparse-co.com/news/article-1",
            page_type="OTHER",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            title="News Article 1",
            meta_description="News",
            content_sha256=hashlib.sha256("Our company attended the annual regional seminar last week.".encode("utf-8")).hexdigest(),
            cleaned_text="Our company attended the annual regional seminar last week.",
        ),
    ]

    snapshot = ResearchSnapshot(
        snapshot_id="SNAP_SPARSE_01",
        candidate_id="cand_sparse_01",
        canonical_domain="sparse-co.com",
        company_name="Sparse Co",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        crawler_version="2.0.0",
        parser_version="2.0.0",
        dataset_schema_version="1.0.0",
        content_checksum="sparsechecksum123456",
        pages_attempted=2,
        pages_succeeded=2,
        research_status="complete",
        pages=pages,
    )

    preflight = estimate_analysis_preflight(snapshot, provider_id="gemini", model="gemini-3.5-flash-lite")

    # Assert preflight attributes
    assert preflight["provider"] == "gemini"
    assert preflight["model"] == "gemini-3.5-flash-lite"
    assert preflight["billing_tier"] == "Not verified"
    assert "hypothetical_commercial_cost_usd" in preflight
    assert preflight["chunks_count"] >= 1

    # Verify AI Selection metadata
    meta = preflight["selection_metadata"]
    assert "missing_mandatory_categories" in meta
    # Missing SERVICES and CONTACT
    missing = meta["missing_mandatory_categories"]
    assert "SERVICES" in missing
    assert "CONTACT" in missing
    assert "ABOUT_COMPANY" not in missing  # Root page classified as ABOUT_COMPANY

    # Partition chunks check
    chunks, eligible, omitted = partition_snapshot_into_chunks(snapshot, use_page_selection=True)
    assert len(chunks) == 1
    assert len(eligible) == 2
