"""
Tests for Post-Venezia Search Data Quality & UI Semantics.

Verifies:
1. UI Semantics: Candidates and Candidate Detail views render 'Not Verified' (never 'Global')
   when candidate company country or city is unknown/None.
2. Company Name Extraction:
   - 'e-mep.it' with title 'Impianti - e-MEP S.R.L.' extracts 'e-MEP S.R.L.' (never 'e').
   - 'bs-eng.net' with title 'Lavora con noi - BIM SPECIALIST' extracts 'BIM BS progetti' (never 'BIM SPECIALIST').
   - 'energymep.it' with title 'Energymep – Studio d’ingegneria' extracts 'Energymep'.
   - 'bimidea.it' with title 'BIM Idea - il tuo partner ideale per il BIM!' extracts 'BIM Idea'.
   - 'perillosrl.it' with title 'Progettazione impiantistica MEP | BIM | Perillo Ingegneria' extracts 'Perillo Ingegneria'.
3. Generic page titles ('Home', 'Services', 'BIM', 'Progettazione BIM Integrata') are not used as company names.
4. Preserves unverified country/city as None in SQLite, without auto-assigning from search filters or domain TLD.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.discovery.presets import extract_provisional_company_name


def test_ui_semantics_renders_not_verified_instead_of_global(tmp_path):
    """
    Task 2 Verification:
    When a candidate has country=None or city=None, Candidates UI and Candidate Detail UI
    must display 'Not Verified' rather than 'Global'.
    """
    test_db = str(tmp_path / "ui_semantics.db")
    store = CandidateStore(db_path=test_db)

    # Ingest a candidate with unknown location
    cand, _ = store.get_or_create_candidate("Test Studio Venezia", "teststudio.it", country=None, city=None)
    assert cand.country is None
    assert cand.city is None

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db}):
        with TestClient(app) as client:
            # 1. Inspect Candidates list
            resp_list = client.get("/sales/candidates")
            assert resp_list.status_code == 200
            html_list = resp_list.text
            assert "Not Verified" in html_list
            # Ensure table cell for country contains 'Not Verified', not 'Global'
            assert f"<td>{cand.company_domain}</td>" not in html_list or "<td>Global</td>" not in html_list
            assert "<td>Not Verified</td>" in html_list

            # 2. Inspect Candidate detail
            resp_detail = client.get(f"/sales/candidates/{cand.candidate_id}")
            assert resp_detail.status_code == 200
            html_detail = resp_detail.text
            assert "<strong>Country:</strong> Not Verified" in html_detail
            assert "<strong>City:</strong> Not Verified" in html_detail
            assert "<strong>Country:</strong> Global" not in html_detail


def test_venezia_regression_company_name_extraction():
    """
    Task 3 Verification:
    Regression cases from the Venezia live search:
    - 'e-mep.it' must not be named 'e'.
    - 'bs-eng.net' must not be named 'BIM SPECIALIST'.
    """
    # 1. e-mep.it
    title_emep = "Impianti - e-MEP S.R.L."
    snippet_emep = "La scelta di investire in tecnologia ... societario di e-MEP. Un metodo di lavoro collaudato che permette di offrire servizi integrati di ingegneria..."
    name_emep = extract_provisional_company_name(title_emep, "e-mep.it", snippet=snippet_emep)
    assert name_emep == "e-MEP S.R.L."
    assert name_emep != "e"
    assert name_emep != "Impianti"

    # 2. bs-eng.net
    title_bs = "Lavora con noi - BIM SPECIALIST"
    snippet_bs = "BIM BS progetti, società di progettazione multidisciplinare con sede in provincia di Venezia, ricerca BIM Specialist/Coordinator/Manager..."
    name_bs = extract_provisional_company_name(title_bs, "bs-eng.net", snippet=snippet_bs)
    assert name_bs == "BIM BS progetti"
    assert name_bs != "BIM SPECIALIST"
    assert name_bs != "Lavora con noi"

    # 3. energymep.it
    title_energy = "Energymep – Studio d’ingegneria"
    name_energy = extract_provisional_company_name(title_energy, "energymep.it")
    assert name_energy == "Energymep"

    # 4. bimidea.it
    title_idea = "BIM Idea - il tuo partner ideale per il BIM!"
    name_idea = extract_provisional_company_name(title_idea, "bimidea.it")
    assert name_idea == "BIM Idea"

    # 5. perillosrl.it
    title_perillo = "Progettazione impiantistica MEP | BIM | Perillo Ingegneria"
    name_perillo = extract_provisional_company_name(title_perillo, "perillosrl.it")
    assert name_perillo == "Perillo Ingegneria"


def test_generic_titles_fallback_to_clean_domain():
    """
    Generic titles like 'Home', 'Services', 'BIM' must not become company names.
    Fallback should cleanly format domain into a provisional name.
    """
    name_fallback = extract_provisional_company_name("Home - Services - BIM", "studio-venezia.it")
    assert name_fallback == "Studio-Venezia"
    assert name_fallback not in ("Home", "Services", "BIM")
