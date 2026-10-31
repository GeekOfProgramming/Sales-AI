"""
Search Quality Pilot v1 Tests.

Tests focused search quality improvements:
1. Category presets affect final queries with Italian terminology and regional terms.
2. Operator keywords refine without duplicating existing terms.
3. Brave regional parameters (country, search_lang) mapped correctly for IT, DE, AT, CH, FR, GB, ES.
4. Multilingual country note provided for Switzerland.
5. Direct company search applies negative domain exclusions and negative keyword exclusions.
6. Result triage accurately identifies:
   - REJECTED_IRRELEVANT: Training courses, university/masters, generic news, portals without companies.
   - POTENTIAL_COMPANY: Direct company engineering/architecture studio domains.
   - SEARCH_RESULT / NEEDS_REVIEW: Ambiguous or generic entries.
7. Test A Mock: 10 raw results dominated by BIM/Revit training websites -> verifies all 10 are rejected from being promoted as CandidateCompany entities.
8. Test B Mock: 10 raw results containing 7 plausible engineering/architecture websites + job boards/articles -> verifies 7 companies retained, job boards/articles filtered.
9. Dry Run UI message contains category, final query, exclusions, country/lang parameters, est cost, credential status, and executes zero external calls and zero DB mutations.
10. Operational database, pilot companies, and historical records are strictly protected.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas import NormalizedSearchResult
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.discovery.presets import (
    CATEGORY_PRESETS,
    REGIONAL_MARKET_SETTINGS,
    get_regional_settings,
    compose_search_query,
    triage_search_result,
    ResultTriageStatus,
)
from sales_engine.discovery.brave_search import BraveSearchProvider
from sales_engine.discovery_v2.adapters import create_search_engine_adapter
from sales_engine.discovery_v2.source_registry import SourceConfig


def test_category_presets_compose_query_correctly():
    """Verify that selecting Business Category modifies the actual Brave search query."""
    # 1. MEP Design in Padova
    query_mep, exclusions = compose_search_query(
        raw_query="",
        city="Padova",
        country="Italy",
        category="MEP Design",
        discovery_mode="company_first",
        include_exclusions=True,
    )
    assert "progettazione impianti" in query_mep
    assert "Padova" in query_mep
    assert "-site:linkedin.com" in query_mep
    assert "-site:indeed.com" in query_mep
    assert "-corso" in query_mep

    # 2. Architectural Practice without duplicating keywords
    query_arch, _ = compose_search_query(
        raw_query="studio architettura sostenibile",
        city="Verona",
        country="Italy",
        category="Architectural Practice",
        discovery_mode="company_first",
    )
    assert "studio architettura sostenibile" in query_arch
    assert "Verona" in query_arch
    # Does not redundantly duplicate "studio di architettura" when already specified
    assert query_arch.count("architettura") <= 2

    # 3. Structural Engineering
    query_struct, _ = compose_search_query(
        raw_query="",
        city="Vicenza",
        country="Italy",
        category="Structural Engineering",
    )
    assert "strutturale" in query_struct or "strutture" in query_struct
    assert "Vicenza" in query_struct


def test_brave_regional_parameter_mappings():
    """Verify country code and search language mapping for European target markets."""
    it_settings = get_regional_settings("Italy")
    assert it_settings["country"] == "IT"
    assert it_settings["search_lang"] == "it"

    de_settings = get_regional_settings("Germany")
    assert de_settings["country"] == "DE"
    assert de_settings["search_lang"] == "de"

    at_settings = get_regional_settings("Austria")
    assert at_settings["country"] == "AT"
    assert at_settings["search_lang"] == "de"

    ch_settings = get_regional_settings("Switzerland")
    assert ch_settings["country"] == "CH"
    assert ch_settings["search_lang"] == "de"
    assert "Multilingual" in ch_settings["notes"]

    fr_settings = get_regional_settings("France")
    assert fr_settings["country"] == "FR"
    assert fr_settings["search_lang"] == "fr"

    gb_settings = get_regional_settings("United Kingdom")
    assert gb_settings["country"] == "GB"
    assert gb_settings["search_lang"] == "en"


@pytest.mark.asyncio
async def test_brave_provider_transmits_regional_params():
    """Verify that BraveSearchProvider includes country and search_lang in request params."""
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"web": {"results": []}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test_pilot_key"}):
            provider = BraveSearchProvider()
            await provider.search(
                query="progettazione BIM Padova",
                num_results=10,
                country="IT",
                search_lang="it",
            )

            assert mock_get.called
            call_kwargs = mock_get.call_args[1]
            params = call_kwargs["params"]
            assert params["q"] == "progettazione BIM Padova"
            assert params["count"] == 10
            assert params["country"] == "IT"
            assert params["search_lang"] == "it"


def test_result_triage_classification():
    """Verify triage accuracy between direct companies, academic/training, and aggregators."""
    # 1. Training Course -> REJECTED_IRRELEVANT
    status, reasons = triage_search_result(
        title="Master BIM Specialist - Politecnico di Milano",
        snippet="Corso di formazione professionale e certificazione Revit per ingegneri e architetti.",
        url="https://www.masterbim-polimi.it/corso-revit",
    )
    assert status == ResultTriageStatus.REJECTED_IRRELEVANT
    assert any("academic_or_training" in r for r in reasons)

    # 2. LinkedIn Aggregator in company_first -> REJECTED_IRRELEVANT
    status_li, _ = triage_search_result(
        title="Offerte di Lavoro BIM Specialist Veneto | LinkedIn",
        snippet="Scopri le nuove posizioni aperte per ingegneri e modellatori BIM a Padova e Venezia.",
        url="https://it.linkedin.com/jobs/bim-specialist-veneto",
        discovery_mode="company_first",
    )
    assert status_li == ResultTriageStatus.REJECTED_IRRELEVANT

    # 3. Direct Architecture/Engineering Studio -> POTENTIAL_COMPANY
    status_co, reasons_co = triage_search_result(
        title="Studio Tecnico Miotto - Servizi di Progettazione BIM e Impianti",
        snippet="Studio associato di ingegneria specializzato in progettazione integrata, modellazione Revit e calcolo strutturale a Padova.",
        url="https://www.studiotecnicom.it/servizi/bim/",
        discovery_mode="company_first",
    )
    assert status_co == ResultTriageStatus.POTENTIAL_COMPANY
    assert any("direct_company_signals" in r for r in reasons_co)


def test_pilot_mock_test_a_training_dominated_search(tmp_path):
    """
    Test A: Ten raw results dominated by BIM/Revit training courses and university masters.
    Verifies that the triage guard rejects all 10 from being created as CandidateCompany entities.
    """
    test_db = str(tmp_path / "test_a_pilot.db")
    store = CandidateStore(db_path=test_db)

    # Mock 10 results modeled after training test outcome
    training_results = [
        NormalizedSearchResult(query="BIM", title=f"Corso Formazione Revit BIM {i}", url=f"https://www.corsibim{i}.it/corso", snippet=f"Corso professionale certificato per modellatori Revit {i}", source="brave", rank=i)
        for i in range(1, 11)
    ]

    with patch.object(BraveSearchProvider, "search", AsyncMock(return_value=training_results)):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test_pilot_key", "SALES_OUTREACH_DB": test_db}):
            cfg = SourceConfig(id="brave_job_search", name="Brave", type="search_engine", requires_api_key=True)
            adapter = create_search_engine_adapter(cfg, country="IT", search_lang="it")

            items = adapter("progettazione BIM Padova")
            assert len(items) == 10
            for item in items:
                assert item["trust_level"] == ResultTriageStatus.REJECTED_IRRELEVANT

            # Feed through discovery engine
            from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
            engine = DiscoveryEngineV2(candidate_store=store)
            run_rec = engine.run_multi_source_discovery(
                sources=["brave_job_search"],
                query="progettazione BIM Padova",
                fetcher_funcs={"brave_job_search": adapter},
                discovery_mode="company_first",
            )

            # Assert zero new CandidateCompany entities created
            assert run_rec.new_candidates == 0
            assert len(store.list_candidates()) == 0


def test_pilot_mock_test_b_seven_verified_firms_and_aggregators(tmp_path):
    """
    Test B: Ten raw results containing 7 plausible engineering/architecture websites
    plus 2 job boards and 1 training course.
    Verifies that 7 legitimate companies are retained and 3 irrelevant results rejected.
    """
    test_db = str(tmp_path / "test_b_pilot.db")
    store = CandidateStore(db_path=test_db)

    mixed_results = [
        # 7 Real Engineering / Architecture Candidates from Pilot
        NormalizedSearchResult(query="BIM", title="Studio Miotto - Progettazione BIM e Impianti", url="https://www.studiotecnicom.it/servizi/bim/", snippet="Studio di ingegneria a Padova specializzato in modellazione Revit e BIM coordination.", source="brave", rank=1),
        NormalizedSearchResult(query="BIM", title="Stefano Baseggio - Progettazione BIM Padova", url="https://stefanobaseggio.it/progettazione-bim-padova-venezia-treviso/", snippet="Ingegneria e architettura BIM per interventi residenziali e industriali in Veneto.", source="brave", rank=2),
        NormalizedSearchResult(query="BIM", title="IPT Project - Ingegneria e Consulenza", url="https://www.iptproject.it/", snippet="Societa di ingegneria multidisciplinare operante in grandi progetti strutturali e infrastrutturali.", source="brave", rank=3),
        NormalizedSearchResult(query="BIM", title="E-Farm Engineering - Progettazione Integrata", url="https://e-farmsrl.com/e-farm/bim/", snippet="Progettazione strutturale e impiantistica in ambiente BIM Revit.", source="brave", rank=4),
        NormalizedSearchResult(query="BIM", title="Incide Engineering - Progettista BIM Strutture", url="https://www.incide.it/progettista-bim-strutture/", snippet="Ingegneria integrata internazionale con sede a Padova: strutture complesse e facciate.", source="brave", rank=5),
        NormalizedSearchResult(query="BIM", title="STC Group - Ingegneria e Impianti", url="https://stcpi.com/", snippet="Ingegneria termotecnica e progettazione impianti per il settore industriale e civile.", source="brave", rank=6),
        NormalizedSearchResult(query="BIM", title="ei bim project - Studio Tecnico Associato", url="https://www.eibimproject.com/lo-studio/", snippet="Consulenza BIM, rilievi laser scanner e restituzione di modelli parametrici.", source="brave", rank=7),
        # 3 Irrelevant Results
        NormalizedSearchResult(query="BIM", title="Offerte Lavoro BIM Specialist Veneto | Indeed", url="https://it.indeed.com/q-bim-specialist-veneto.html", snippet="Candidati subito per posizioni aperte BIM su Indeed.", source="brave", rank=8),
        NormalizedSearchResult(query="BIM", title="LinkedIn Jobs - BIM Coordinator Padova", url="https://www.linkedin.com/jobs/view/bim-coordinator-padova", snippet="Opportunita di carriera per coordinatori BIM a Padova.", source="brave", rank=9),
        NormalizedSearchResult(query="BIM", title="Master Universitario BIM Management - Universita", url="https://www.universitaveneta.it/master-bim", snippet="Master universitario di primo livello per laureati in architettura e ingegneria.", source="brave", rank=10),
    ]

    with patch.object(BraveSearchProvider, "search", AsyncMock(return_value=mixed_results)):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test_pilot_key", "SALES_OUTREACH_DB": test_db}):
            cfg = SourceConfig(id="brave_job_search", name="Brave", type="search_engine", requires_api_key=True)
            adapter = create_search_engine_adapter(cfg, country="IT", search_lang="it")

            items = adapter("progettazione BIM Padova")
            assert len(items) == 10

            potential_co = [it for it in items if it["trust_level"] == ResultTriageStatus.POTENTIAL_COMPANY]
            rejected = [it for it in items if it["trust_level"] == ResultTriageStatus.REJECTED_IRRELEVANT]

            assert len(potential_co) == 7
            assert len(rejected) == 3

            # Run through discovery engine
            from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
            engine = DiscoveryEngineV2(candidate_store=store)
            run_rec = engine.run_multi_source_discovery(
                sources=["brave_job_search"],
                query="progettazione BIM Padova",
                fetcher_funcs={"brave_job_search": adapter},
                discovery_mode="company_first",
            )

            # Exactly 7 candidates ingested into CandidateStore
            assert run_rec.new_candidates == 7
            saved_candidates = store.list_candidates()
            assert len(saved_candidates) == 7

            saved_domains = [c.company_domain for c in saved_candidates]
            assert "studiotecnicom.it" in saved_domains
            assert "iptproject.it" in saved_domains
            assert "incide.it" in saved_domains
            assert "indeed.com" not in saved_domains
            assert "linkedin.com" not in saved_domains


def test_discovery_ui_dry_run_transparency(tmp_path):
    """Verify that Preview/Dry Run displays all search parameters without network calls or DB mutations."""
    test_db = str(tmp_path / "dry_run_test.db")
    store = CandidateStore(db_path=test_db)

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "BRAVE_SEARCH_API_KEY": "dummy_key_for_ui"}):
        with TestClient(app) as client:
            resp = client.get("/sales/discovery")
            assert resp.status_code == 200
            csrf_token = resp.cookies.get("sales_csrf_token")

            # Post preview (Dry Run) with MEP Design in Padova
            dry_run_resp = client.post(
                "/sales/discovery/run",
                data={
                    "query": "progettazione impianti",
                    "city": "Padova",
                    "country": "Italy",
                    "category": "MEP Design",
                    "is_preview": "true",
                    "billing_confirmed": "true",
                },
                cookies={"sales_csrf_token": csrf_token},
                headers={"X-CSRF-Token": csrf_token},
                follow_redirects=False,
            )

            assert dry_run_resp.status_code == 303
            redirect_url = dry_run_resp.headers["location"]
            from urllib.parse import unquote
            unquoted_url = unquote(redirect_url)
            assert "Query Preview Ready" in unquoted_url
            assert "Category: MEP Design" in unquoted_url
            assert "Region: Italy" in unquoted_url
            assert "code: IT" in unquoted_url
            assert "lang: it" in unquoted_url
            assert "Brave Source: ENABLED" in unquoted_url
            assert "Credential: CREDENTIAL_PRESENT" in unquoted_url
            assert "Max Results: 10" in unquoted_url
            assert "No network calls made" in unquoted_url

            # Zero attempts reserved in DB
            attempts = store.get_pilot_search_stats()
            assert attempts["total_attempts"] == 0
            # Zero candidates created
            assert len(store.list_candidates()) == 0


def test_triage_preserves_real_engineering_firms_mentioning_training():
    """
    CRITICAL QUALITY FIX:
    Real engineering companies mentioning 'formazione', 'corsi', or 'master'
    (such as 14p.it or etcstudio.it) must NEVER be rejected as REJECTED_IRRELEVANT.
    They must be classified as POTENTIAL_COMPANY (or NEEDS_REVIEW if ambiguous),
    while realistic dedicated BIM course/academy websites remain REJECTED_IRRELEVANT.
    """
    # 1. 14p.it — Real engineering company discussing professional training / team
    status_14p, reasons_14p = triage_search_result(
        title="14P Ingegneria - Progettazione Strutturale e BIM",
        snippet="Societa di ingegneria a Padova specializzata in strutture complesse e formazione continua del team in ambiente BIM.",
        url="https://www.14p.it/chi-siamo/",
    )
    assert status_14p == ResultTriageStatus.POTENTIAL_COMPANY
    assert status_14p != ResultTriageStatus.REJECTED_IRRELEVANT
    assert any("training_mention" in r or "direct_company_signals" in r for r in reasons_14p)

    # 2. etcstudio.it — Real engineering / architecture studio mentioning workshops/seminars
    status_etc, reasons_etc = triage_search_result(
        title="ETC Studio - Ingegneria Integrata e Servizi BIM",
        snippet="Studio associato di ingegneria: progettazione integrata, impianti, rilievi e corsi di aggiornamento tecnico per collaboratori.",
        url="https://www.etcstudio.it/servizi/",
    )
    assert status_etc == ResultTriageStatus.POTENTIAL_COMPANY
    assert status_etc != ResultTriageStatus.REJECTED_IRRELEVANT

    # 3. Dedicated training academy with /corso/ path -> REJECTED_IRRELEVANT
    status_academy, reasons_acad = triage_search_result(
        title="Corso Completo Revit BIM Specialist - Certificazione Ufficiale",
        snippet="Iscriviti al corso online con lezioni serali, durata 60 ore, rilascio attestato e crediti formativi professionali.",
        url="https://www.corsirevitbim.it/corsi/revit-specialist/",
    )
    assert status_academy == ResultTriageStatus.REJECTED_IRRELEVANT
    assert any("academic_or_training" in r for r in reasons_acad)

    # 4. Realistic university master -> REJECTED_IRRELEVANT
    status_master, _ = triage_search_result(
        title="Master Universitario di II Livello in BIM Management",
        snippet="Bando di ammissione per l'anno accademico. Dipartimento di Ingegneria Civile ed Ambientale.",
        url="https://www.unipd.it/master-bim-management",
    )
    assert status_master == ResultTriageStatus.REJECTED_IRRELEVANT

    # 5. Engineering studio hosting an event/course announcement -> classified as NEEDS_REVIEW (never rejected)
    status_ambiguous, reasons_ambig = triage_search_result(
        title="Corso di Modellazione Parametrica presso Studio Architetti Associati",
        snippet="Seminario tecnico organizzato dallo studio di ingegneria per giovani professionisti a Treviso.",
        url="https://www.studioarchitettitreviso.it/news/corso-parametrico",
    )
    assert status_ambiguous == ResultTriageStatus.NEEDS_REVIEW
    assert status_ambiguous != ResultTriageStatus.REJECTED_IRRELEVANT

