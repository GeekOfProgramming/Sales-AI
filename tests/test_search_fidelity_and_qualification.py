"""
Search Fidelity & Real-Company Qualification Tests.

Verifies:
1. Preview -> Billing Confirmation -> Live: The executed Brave query is byte-for-byte identical
   to the previewed query. Form values (country, city, category, query) are strictly preserved across redirects.
2. If form parameters are altered after preview without a re-preview, live run is blocked.
3. No literal '&bull;' is rendered in the message.
4. Name extraction: Generic page titles ('BIM', 'Progettazione BIM Integrata', 'Modellazione BIM...')
   are separated from true provisional legal/studio names.
5. Obvious non-target sources (professionearchitetto.it, como.ordingegneri.it, autodesk.com)
   are categorized as REJECTED_IRRELEVANT and NOT promoted to CandidateCompany entities.
6. Legitimate firms (esa-engineering.it, bbprogettimilano.it, strastudio.it, bim-lab.net, planimetro.it)
   remain eligible as POTENTIAL_COMPANY or NEEDS_REVIEW.
7. Country 'Global' is never assigned to candidate entities without verified evidence.
8. Unverified companies are never marked as qualified or approved for outreach.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from urllib.parse import unquote
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas import NormalizedSearchResult
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.discovery.presets import (
    triage_search_result,
    ResultTriageStatus,
    extract_provisional_company_name,
    compose_search_query,
)
from sales_engine.discovery.brave_search import BraveSearchProvider
from sales_engine.discovery_v2.adapters import create_search_engine_adapter
from sales_engine.discovery_v2.source_registry import SourceConfig
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2


def test_preview_to_live_exact_query_preservation(tmp_path):
    """
    REGRESSION DEFECT 1:
    Operator previews MEP Design with custom keywords in Padova.
    Redirect preserves all form fields.
    Live run confirmation executes the exact byte-for-byte query that was previewed.
    """
    test_db = str(tmp_path / "query_preservation.db")
    store = CandidateStore(db_path=test_db)

    captured_searches = []

    async def mock_search(self, query, num_results=10, country=None, search_lang=None):
        captured_searches.append({
            "query": query,
            "country": country,
            "search_lang": search_lang,
        })
        return [
            NormalizedSearchResult(
                query=query,
                title="Studio Termotecnico Padova - Ingegneria Impianti",
                url="https://www.studiotermotecnicopadova.it/",
                snippet="Progettazione impianti meccanici ed elettrici in ambiente BIM.",
                source="brave",
                rank=1,
            )
        ]

    with patch.object(BraveSearchProvider, "search", mock_search):
        with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "BRAVE_SEARCH_API_KEY": "valid_test_key"}):
            with TestClient(app) as client:
                # 1. Fetch CSRF token
                get_resp = client.get("/sales/discovery")
                assert get_resp.status_code == 200
                csrf_token = get_resp.cookies.get("sales_csrf_token")

                # 2. Operator submits PREVIEW for MEP Design in Padova with custom query
                preview_resp = client.post(
                    "/sales/discovery/run",
                    data={
                        "query": "impianti termotecnici idronici",
                        "city": "Padova",
                        "country": "Italy",
                        "category": "MEP Design",
                        "is_preview": "true",
                    },
                    cookies={"sales_csrf_token": csrf_token},
                    headers={"X-CSRF-Token": csrf_token},
                    follow_redirects=False,
                )
                assert preview_resp.status_code == 303
                redirect_url = preview_resp.headers["location"]

                # Verify literal '&bull;' is NOT in the message (uses clean unicode •)
                assert "&bull;" not in redirect_url
                assert "%26bull%3B" not in redirect_url

                # Inspect redirect URL parameters
                unquoted_url = unquote(redirect_url)
                assert "category=MEP+Design" in unquoted_url or "category=MEP Design" in unquoted_url
                assert "city=Padova" in unquoted_url
                assert "preview_query=" in unquoted_url
                assert "preview_token=" in unquoted_url

                # Extract preview token and preview query from query string
                from urllib.parse import parse_qs, urlparse
                parsed_qs = parse_qs(urlparse(redirect_url).query)
                saved_preview_query = parsed_qs["preview_query"][0]
                saved_preview_token = parsed_qs["preview_token"][0]

                # Assert the preview page re-renders the preserved form fields
                follow_resp = client.get(redirect_url)
                assert follow_resp.status_code == 200
                html_body = follow_resp.text
                assert 'value="MEP Design" selected' in html_body
                assert 'value="Padova"' in html_body
                assert 'value="impianti termotecnici idronici"' in html_body
                assert 'name="idempotency_token"' in html_body

                # 3. Operator checks billing authorization and clicks LIVE SEARCH
                live_resp = client.post(
                    "/sales/discovery/run",
                    data={
                        "query": "impianti termotecnici idronici",
                        "city": "Padova",
                        "country": "Italy",
                        "category": "MEP Design",
                        "confirm_live": "true",
                        "billing_confirmed": "true",
                        "idempotency_token": saved_preview_token,
                    },
                    cookies={"sales_csrf_token": csrf_token},
                    headers={"X-CSRF-Token": csrf_token},
                    follow_redirects=False,
                )
                assert live_resp.status_code == 303
                assert "/sales/candidates" in live_resp.headers["location"]

                # Assert that executed query was byte-for-byte identical to the preview query
                assert len(captured_searches) == 1
                executed_query = captured_searches[0]["query"]
                assert executed_query == saved_preview_query
                assert "impianti termotecnici idronici" in executed_query
                assert "Padova" in executed_query
                assert captured_searches[0]["country"] == "IT"
                assert captured_searches[0]["search_lang"] == "it"


def test_tampered_form_parameters_block_live_search(tmp_path):
    """
    If the operator changes the city or category after previewing without clicking preview again,
    the live run must be blocked to prevent unintended search execution.
    """
    test_db = str(tmp_path / "tampered.db")
    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "BRAVE_SEARCH_API_KEY": "valid_test_key"}):
        with TestClient(app) as client:
            get_resp = client.get("/sales/discovery")
            csrf_token = get_resp.cookies.get("sales_csrf_token")

            # Submit live with mismatched idempotency token
            live_resp = client.post(
                "/sales/discovery/run",
                data={
                    "query": "ingegneria strutture",
                    "city": "Verona",  # Changed!
                    "country": "Italy",
                    "category": "Structural Engineering",
                    "confirm_live": "true",
                    "billing_confirmed": "true",
                    "idempotency_token": "old_preview_token_1234",
                },
                cookies={"sales_csrf_token": csrf_token},
                headers={"X-CSRF-Token": csrf_token},
                follow_redirects=False,
            )
            assert live_resp.status_code == 303
            redirect_url = live_resp.headers["location"]
            assert "error=" in redirect_url
            unquoted_err = unquote(redirect_url)
            assert "Parameters+changed+after+preview" in redirect_url or "parameters changed after preview" in unquoted_err.lower()


def test_company_name_extraction_separates_generic_titles():
    """
    Generic page titles ('BIM', 'Progettazione BIM Integrata', 'Modellazione BIM...')
    must NOT be assigned as the company name.
    """
    # 1. 'BIM – Planimetro: Studio di progettazione e ingegneria Milano' -> 'Planimetro'
    name_plani = extract_provisional_company_name(
        "BIM – Planimetro: Studio di progettazione e ingegneria Milano",
        "planimetro.it"
    )
    assert name_plani == "Planimetro"
    assert name_plani != "BIM"

    # 2. 'BIM - BB Progetti Milano' -> 'BB Progetti Milano'
    name_bb = extract_provisional_company_name(
        "BIM - BB Progetti Milano",
        "bbprogettimilano.it"
    )
    assert name_bb == "BB Progetti Milano"
    assert name_bb != "BIM"

    # 3. 'Progettazione BIM Integrata - ESA engineering' -> 'ESA engineering'
    name_esa = extract_provisional_company_name(
        "Progettazione BIM Integrata - ESA engineering",
        "esa-engineering.it"
    )
    assert name_esa == "ESA engineering"
    assert name_esa != "Progettazione BIM Integrata"

    # 4. 'Modellazione BIM a Milano: precisione e innovazione progettuale - Stra Studio' -> 'Stra Studio'
    name_stra = extract_provisional_company_name(
        "Modellazione BIM a Milano: precisione e innovazione progettuale - Stra Studio",
        "strastudio.it"
    )
    assert name_stra == "Stra Studio"
    assert not name_stra.startswith("Modellazione BIM")

    # 5. 'Home - Studio d'Ingegneria delle Strutture S.r.l. - Milano' -> 'Studio d'Ingegneria delle Strutture S.r.l.'
    name_si = extract_provisional_company_name(
        "Home - Studio d'Ingegneria delle Strutture S.r.l. - Milano",
        "sistudio.it"
    )
    assert "Studio d'Ingegneria delle Strutture S.r.l." in name_si
    assert "Home" not in name_si


def test_triage_filters_non_target_sources_and_retains_legitimate_firms():
    """
    Evaluates the 8 domains from the first live operator pilot:
    Non-targets (professionearchitetto.it, como.ordingegneri.it, autodesk.com) -> REJECTED_IRRELEVANT.
    Legitimate firms (esa-engineering.it, bbprogettimilano.it, strastudio.it, bim-lab.net, planimetro.it)
    -> POTENTIAL_COMPANY or NEEDS_REVIEW.
    """
    # 1. professionearchitetto.it (architecture information portal / job board)
    status_pa, _ = triage_search_result(
        title="Architect & BIM | Revit Specialist | Milano - bacheca lavoro",
        snippet="Architetto e Designer con esperienza nella redazione di documentazione tecnica. Cerco una nuova sfida professionale.",
        url="https://www.professionearchitetto.it/bacheca/lavoro/502050/architect-e-bim",
    )
    assert status_pa == ResultTriageStatus.REJECTED_IRRELEVANT

    # 2. como.ordingegneri.it (professional order / regulatory body)
    status_order, _ = triage_search_result(
        title="Consulenza specialistica BIM (Revit) e CAD – ORDINE DEGLI INGEGNERI DELLA PROVINCIA DI COMO",
        snippet="Ingegnere certificato BIM Specialist offre disponibilita per collaborazioni esterne.",
        url="https://como.ordingegneri.it/blog/2026/03/25/consulenza-specialistica-bim",
    )
    assert status_order == ResultTriageStatus.REJECTED_IRRELEVANT

    # 3. autodesk.com (software manufacturer)
    status_adsk, _ = triage_search_result(
        title="Autodesk Revit | Prezzi e acquisto del software Revit ufficiale",
        snippet="Autodesk Revit viene utilizzato per progettare, documentare, visualizzare e consegnare progetti.",
        url="https://www.autodesk.com/it/products/revit/overview",
    )
    assert status_adsk == ResultTriageStatus.REJECTED_IRRELEVANT

    # 4. esa-engineering.it (real multidisciplinary engineering company)
    status_esa, _ = triage_search_result(
        title="Progettazione BIM Integrata - ESA engineering",
        snippet="Un approccio progettuale che include la generazione e gestione di rappresentazioni digitali delle caratteristiche fisiche.",
        url="https://esa-engineering.it/servizi/progettazione-bim-integrata/",
    )
    assert status_esa == ResultTriageStatus.POTENTIAL_COMPANY

    # 5. bbprogettimilano.it (real engineering studio)
    status_bb, _ = triage_search_result(
        title="BIM - BB Progetti Milano",
        snippet="Studio associato di ingegneria: progettazione integrata, modellazione parametrica e coordinamento multidisicplinare.",
        url="https://www.bbprogettimilano.it/bim/",
    )
    assert status_bb == ResultTriageStatus.POTENTIAL_COMPANY

    # 6. strastudio.it (real architecture / BIM studio)
    status_stra, _ = triage_search_result(
        title="Modellazione BIM a Milano - Stra Studio",
        snippet="Studio di architettura e progettazione BIM specializzato in opere residenziali e commerciali.",
        url="https://www.strastudio.it/servizi-bim/",
    )
    assert status_stra == ResultTriageStatus.POTENTIAL_COMPANY

    # 7. planimetro.it (real engineering consultancy)
    status_plani, _ = triage_search_result(
        title="Planimetro: Studio di progettazione e ingegneria Milano",
        snippet="Utilizziamo Autodesk REVIT per la modellazione tridimensionale e SOLIBRI per il clash detection.",
        url="https://planimetro.it/bim/",
    )
    assert status_plani == ResultTriageStatus.POTENTIAL_COMPANY

    # 8. bim-lab.net (specialist BIM company / potential partner)
    status_bimlab, _ = triage_search_result(
        title="Bim Lab | Building Information Modeling",
        snippet="Il BIM e la rivoluzione digitale del settore dell'edilizia (AEC).",
        url="https://www.bim-lab.net/",
    )
    # Valid domain with generic content -> NEEDS_REVIEW (never rejected)
    assert status_bimlab in (ResultTriageStatus.POTENTIAL_COMPANY, ResultTriageStatus.NEEDS_REVIEW)
    assert status_bimlab != ResultTriageStatus.REJECTED_IRRELEVANT


def test_discovery_engine_does_not_promote_non_targets_to_database(tmp_path):
    """
    Verifies that when a live-like batch containing the 8 real domains is fed through
    the DiscoveryEngine, only genuine companies are ingested as CandidateCompany records.
    """
    test_db = str(tmp_path / "engine_fidelity.db")
    store = CandidateStore(db_path=test_db)

    results = [
        NormalizedSearchResult(query="BIM", title="Architect & BIM | professione Architetto", url="https://www.professionearchitetto.it/bacheca/lavoro/502", snippet="Offerta lavoro bacheca", source="brave", rank=1),
        NormalizedSearchResult(query="BIM", title="Consulenza BIM – ORDINE DEGLI INGEGNERI DI COMO", url="https://como.ordingegneri.it/blog/2026/03/25/consulenza", snippet="Avviso albo", source="brave", rank=2),
        NormalizedSearchResult(query="BIM", title="Autodesk Revit Ufficiale", url="https://www.autodesk.com/it/products/revit", snippet="Software BIM", source="brave", rank=3),
        NormalizedSearchResult(query="BIM", title="Progettazione BIM Integrata - ESA engineering", url="https://esa-engineering.it/servizi/bim", snippet="Societa di ingegneria a Milano", source="brave", rank=4),
        NormalizedSearchResult(query="BIM", title="BIM - BB Progetti Milano", url="https://www.bbprogettimilano.it/bim/", snippet="Studio di progettazione e ingegneria", source="brave", rank=5),
        NormalizedSearchResult(query="BIM", title="Modellazione BIM - Stra Studio", url="https://www.strastudio.it/servizi/", snippet="Studio di architettura e BIM", source="brave", rank=6),
        NormalizedSearchResult(query="BIM", title="BIM – Planimetro: Studio di progettazione e ingegneria Milano", url="https://planimetro.it/bim/", snippet="Studio di ingegneria", source="brave", rank=7),
    ]

    with patch.object(BraveSearchProvider, "search", AsyncMock(return_value=results)):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test_key", "SALES_OUTREACH_DB": test_db}):
            cfg = SourceConfig(id="brave_job_search", name="Brave", type="search_engine", requires_api_key=True)
            adapter = create_search_engine_adapter(cfg, country="IT", search_lang="it")

            engine = DiscoveryEngineV2(candidate_store=store)
            run_rec = engine.run_multi_source_discovery(
                sources=["brave_job_search"],
                query="BIM Revit Milano",
                fetcher_funcs={"brave_job_search": adapter},
                discovery_mode="company_first",
            )

            # Exactly 4 valid companies created
            assert run_rec.new_candidates == 4
            saved_candidates = store.list_candidates()
            assert len(saved_candidates) == 4

            saved_domains = [c.company_domain for c in saved_candidates]
            assert "esa-engineering.it" in saved_domains
            assert "bbprogettimilano.it" in saved_domains
            assert "strastudio.it" in saved_domains
            assert "planimetro.it" in saved_domains

            # The 3 non-targets must NOT be candidate companies
            assert "professionearchitetto.it" not in saved_domains
            assert "como.ordingegneri.it" not in saved_domains
            assert "autodesk.com" not in saved_domains

            # Ensure country is NOT fabricated as 'Global'
            for cand in saved_candidates:
                assert cand.country != "Global"
                # Outreach must not be approved automatically
                assert cand.discovery_status.value == "new"
                assert cand.historical_contact_status == "uncontacted"
