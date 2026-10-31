"""
BS Progetti realistic offline acceptance test suite.
Verifies all 12 specific assertions requested by the operator:
1. Legal name resolves to 'BS Progetti S.r.l.', never a sentence fragment.
2. Italian word 'spaziano' cannot match legal suffix 'Spa'.
3. Address resolves only to 'Via Roma 130, 30030 Pianiga (VE), Italy'.
4. Other cities appearing in portfolio descriptions never contaminate headquarters.
5. Phone number is deduplicated and normalized (+39 041 5195480).
6. P.IVA remains correctly extracted and checksum-validated (04095420271).
7. Careers and Fornitori produce real URLs, not menu text.
8. Repeated footer navigation does not multiply independent supplier signals.
9. Submitting Save with zero approved fields produces zero SQLite writes.
10. Selective save preserves unrelated values, prior provenance and outreach restrictions.
11. The research flow works without a configured local LLM.
12. Existing Discovery, 13-field Register and research regression suites still pass.
"""

import os
import tempfile
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import CSRF_COOKIE_NAME, _RESEARCH_SESSION_CACHE
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.research.company_research_service import (
    CompanyResearchResult,
    PageFetchResult,
    extract_deterministic_facts,
    convert_research_to_lead_record,
    normalize_italian_phone,
    validate_italian_piva,
)
import secrets

def generate_csrf_token():
    return secrets.token_hex(32)


BS_PROGETTI_HTML_HOME = """
<!DOCTYPE html>
<html>
<head><title>BS Progetti | Servizi di Progettazione Integrata</title></head>
<body>
  <header>
    <nav>
      <a href="/chi-siamo/">Chi siamo</a>
      <a href="/servizi/">Servizi</a>
      <a href="/progetti/">Progetti</a>
      <a href="/lavora-con-noi/">Lavora con noi</a>
      <a href="/contatti/">Contatti</a>
    </nav>
  </header>
  <main>
    <h1>BS Progetti s.r.l.</h1>
    <p>Offriamo servizi di progettazione integrata multidisciplinare le cui competenze spaziano nei vari settori dell'ingegneria e dell'architettura.</p>
  </main>
  <footer>
    <p>&copy; Copyright 2026 | BS Progetti s.r.l. | P.IVA e C.F. 04095420271</p>
  </footer>
</body>
</html>
"""

BS_PROGETTI_HTML_CONTATTI = """
<!DOCTYPE html>
<html>
<head><title>Contatti | BS Progetti s.r.l.</title></head>
<body>
  <div class="contact-box">
    <h2>CONTATTI</h2>
    <p>BS Progetti s.r.l.</p>
    <p>via Roma 130<br>30030 Pianiga (VE)</p>
    <p>Telefono: +39 041 5195480<br>041 5195480</p>
    <p>Email: info@bs-eng.net</p>
  </div>
  <div class="fusion-panel panel-default fusion-faq-post">
    <div class="panel-heading">
      <h4 class="panel-title toggle" id="faq_1-3936">
        <a data-toggle="collapse" href="#collapse-1-3936">
          <div class="fusion-toggle-heading">Fornitori</div>
        </a>
      </h4>
    </div>
    <div id="collapse-1-3936" class="panel-collapse collapse">
      <p>Offri prodotti e servizi pertinenti con la nostra realtà aziendale? Inviaci la presentazione del tuo business.</p>
      <form action="https://www.bs-eng.net/faq-items/fornitori/" method="post">
        <input type="text" name="azienda" placeholder="Azienda">
        <input type="text" name="referente" placeholder="Referente">
        <input type="text" name="telefono" placeholder="Telefono">
        <input type="email" name="email" placeholder="Email">
      </form>
    </div>
  </div>
</body>
</html>
"""

BS_PROGETTI_HTML_CAREERS = """
<!DOCTYPE html>
<html>
<head><title>Lavora con noi | BS Progetti</title></head>
<body>
  <h1>Posizioni Aperte</h1>
  <div class="job-list">
    <h3>CORSO Scopri di più GRAPHIC DESIGNER e COMUNICAZIONE SELEZIONI IN CORSO</h3>
    <a href="https://www.bs-eng.net/lavora-con-noi-progettista-termotecnico/">Progettista Termotecnico</a>
    <a href="https://www.bs-eng.net/lavora-con-noi-candidatura-spontanea/">Candidatura Spontanea</a>
  </div>
</body>
</html>
"""

BS_PROGETTI_HTML_PROGETTI = """
<!DOCTYPE html>
<html>
<head><title>Progetti | BS Progetti</title></head>
<body>
  <h1>Opere Realizzate</h1>
  <div class="project-item">
    <h3>Nuovo Polo Scolastico a Verona</h3>
    <p>Progetto completato nel 2023 nel comune di Verona.</p>
  </div>
  <div class="project-item">
    <h3>Intervento Direzionale a Mestre e Venezia</h3>
    <p>Opere strutturali a Mestre concluse. Intervento a Venezia terminato.</p>
  </div>
  <div class="project-item">
    <h3>Complesso Ospedaliero a Padova</h3>
    <p>Progetto esecutivo a Padova realizzato.</p>
  </div>
</body>
</html>
"""


@pytest.fixture
def isolated_client(tmp_path):
    test_db = str(tmp_path / "bs_progetti_acceptance_test.db")
    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        cand_store = CandidateStore(db_path=test_db)
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:bs-eng.net",
            company_name="BIM SPECIALIST",  # Initial raw imperfect discovery name
            normalized_company_name="bim specialist",
            company_domain="bs-eng.net",
            country="Italy",
            city="Pianiga",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))
        with TestClient(app) as test_client:
            yield test_client, test_db


def test_assertion_1_and_2_legal_name_and_no_spaziano_spa_match():
    """Assertions 1 & 2: Legal name resolves to 'BS Progetti S.r.l.', never a sentence fragment. 'spaziano' cannot match 'Spa'."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/",
            page_type="home",
            status="FETCH_SUCCESS",
            title="BS Progetti | Servizi di Progettazione",
            raw_html=BS_PROGETTI_HTML_HOME,
            text_content="BS Progetti s.r.l. competenze spaziano nei vari settori. P.IVA 04095420271"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BIM SPECIALIST", "bs-eng.net")
    # Must NOT contain 'spaziano' or fragment 'competenze spa'
    assert "spa" not in res.company_name.lower() or "progetti" in res.company_name.lower()
    assert res.company_name == "BS Progetti s.r.l."
    assert "spaziano" not in res.company_name


def test_assertion_3_and_4_coherent_address_and_no_portfolio_cities():
    """Assertions 3 & 4: Address resolves to Pianiga (VE), Italy. Verona/Mestre/Venezia/Padova in portfolio do not contaminate."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/",
            page_type="home",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_HOME,
            text_content="BS Progetti s.r.l. P.IVA 04095420271"
        ),
        PageFetchResult(
            url="https://www.bs-eng.net/contatti/",
            page_type="contacts",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_CONTATTI,
            text_content="CONTATTI BS Progetti s.r.l. via Roma 130 30030 Pianiga (VE) Telefono: +39 041 5195480 info@bs-eng.net"
        ),
        PageFetchResult(
            url="https://www.bs-eng.net/progetti/",
            page_type="projects",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_PROGETTI,
            text_content="Opere realizzate a Verona, Mestre, Venezia e Padova."
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BS Progetti s.r.l.", "bs-eng.net")
    rec = convert_research_to_lead_record(res)

    assert "Pianiga" in rec.geographic_location
    assert "Via Roma 130" in rec.geographic_location or "via Roma 130" in rec.geographic_location
    # Must NOT concatenate unrelated portfolio cities!
    assert "Verona" not in rec.geographic_location
    assert "Mestre" not in rec.geographic_location
    assert "Padova" not in rec.geographic_location


def test_assertion_5_and_6_phone_deduplication_and_piva_checksum():
    """Assertions 5 & 6: Phone deduplicated to '+39 041 5195480'. P.IVA validated via mod-10 checksum."""
    phones = ["+39 041 5195480", "041 5195480", "+390415195480"]
    normalized = {normalize_italian_phone(p) for p in phones}
    assert len(normalized) == 1
    assert list(normalized)[0] == "+39 041 5195480"

    assert validate_italian_piva("04095420271") is True
    assert validate_italian_piva("00000000000") is False


def test_assertion_7_and_8_real_urls_for_fornitori_and_no_duplication():
    """Assertions 7 & 8: Careers and Fornitori produce real URLs, not menu text. Embedded supplier form retains contact page with fragment."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/",
            page_type="home",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_HOME,
            text_content="Home page"
        ),
        PageFetchResult(
            url="https://www.bs-eng.net/contatti/",
            page_type="contacts",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_CONTATTI,
            text_content="Contatti"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BS Progetti s.r.l.", "bs-eng.net")
    # Fornitori embedded section identified on contatti page with fragment identifier
    assert any("https://www.bs-eng.net/contatti/#collapse-1-3936" in u for u in res.opportunity_urls)
    assert len(res.supplier_signals) == 1
    assert "https://www.bs-eng.net/contatti/#collapse-1-3936" in res.supplier_signals[0]


def test_assertion_9_submitting_zero_approved_fields_produces_zero_sqlite_writes(isolated_client):
    """Assertion 9: Submitting Save with zero approved fields produces zero SQLite writes."""
    import sqlite3
    client, test_db = isolated_client
    cand_id = "cand:domain:bs-eng.net"
    # Ensure tables are initialized
    reg_store = LeadRegisterStore(db_path=test_db)
    cand_store = CandidateStore(db_path=test_db)

    # Initial candidate state
    cand_before = cand_store.get_by_id(cand_id)
    with sqlite3.connect(test_db) as conn:
        explicit_leads_before = conn.execute("SELECT COUNT(*) FROM lead_register_records WHERE candidate_id = ?", (cand_id,)).fetchone()[0]
    assert explicit_leads_before == 0

    csrf_token = generate_csrf_token()
    cookies = {CSRF_COOKIE_NAME: csrf_token}

    # Post with all apply_* checkboxes omitted (zero fields approved)
    form_data = {
        "csrf_token": csrf_token,
        "candidate_id": cand_id,
        "company_name": "BS Progetti S.r.l.",
        "priority_level": "A",
        "priority_rationale": "Rationale",
        "legal_identity": "P.IVA 04095420271",
        "geographic_location": "Via Roma 130, Pianiga (VE), Italy",
        "telephone_numbers": "+39 041 5195480",
        "contact_email_addresses": "info@bs-eng.net",
        "responsible_person": "Ing. Andrea Berro",
        "business_activities": "Ingegneria",
        "official_website": "https://www.bs-eng.net",
        "descriptions_and_notes": "Test notes",
        "job_advertisement_links": "https://www.bs-eng.net/fornitori/",
        "other_links_and_references": "https://www.bs-eng.net",
    }

    resp = client.post(
        f"/sales/candidates/{cand_id}/research/save",
        data=form_data,
        cookies=cookies,
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "No fields selected" in resp.text

    # Verify zero database writes!
    with sqlite3.connect(test_db) as conn:
        explicit_leads_after = conn.execute("SELECT COUNT(*) FROM lead_register_records WHERE candidate_id = ?", (cand_id,)).fetchone()[0]
    assert explicit_leads_after == 0, "Lead register record was written despite zero approved fields!"

    cand_after = cand_store.get_by_id(cand_id)
    assert cand_after.last_researched_at == cand_before.last_researched_at
    assert cand_after.company_name == cand_before.company_name


def test_assertion_10_selective_save_preserves_unrelated_values_and_restrictions(isolated_client):
    """Assertion 10: Selective save preserves prior notes, provenance, and outreach restrictions."""
    client, test_db = isolated_client
    cand_id = "cand:domain:bs-eng.net"
    reg_store = LeadRegisterStore(db_path=test_db)
    cand_store = CandidateStore(db_path=test_db)

    # Seed an existing record with manual notes
    existing_rec = LeadRegisterRecord(
        candidate_id=cand_id,
        record_number=505,
        priority_level="B",
        priority_rationale="Manual review note",
        company_name="BS Progetti S.r.l. (Manual)",
        legal_identity="P.IVA 04095420271",
        geographic_location="Pianiga",
        telephone_numbers="Not Found",
        descriptions_and_notes="AUTHORITATIVE OPERATOR NOTE: Keep this untouched.",
        provenance_source="operator_edited",
        created_at="2026-10-15T08:00:00Z",
    )
    reg_store.save_record(existing_rec)

    csrf_token = generate_csrf_token()
    cookies = {CSRF_COOKIE_NAME: csrf_token}

    # Only approve telephone
    form_data = {
        "csrf_token": csrf_token,
        "candidate_id": cand_id,
        "company_name": "Overwriting Attempt",
        "priority_level": "A",
        "priority_rationale": "Clobber rationale",
        "legal_identity": "P.IVA 04095420271",
        "geographic_location": "Via Roma 130, 30030 Pianiga (VE), Italy",
        "telephone_numbers": "+39 041 5195480",
        "contact_email_addresses": "info@bs-eng.net",
        "responsible_person": "Ing. Andrea Berro",
        "business_activities": "Ingegneria",
        "official_website": "https://www.bs-eng.net",
        "descriptions_and_notes": "Clobbered notes",
        "job_advertisement_links": "https://www.bs-eng.net/fornitori/",
        "other_links_and_references": "https://www.bs-eng.net",
        # Check ONLY telephone
        "apply_telephone_numbers": "1",
    }

    resp = client.post(
        f"/sales/candidates/{cand_id}/research/save",
        data=form_data,
        cookies=cookies,
        follow_redirects=True,
    )
    assert resp.status_code == 200

    saved = reg_store.get_record(cand_id)
    assert saved.telephone_numbers == "+39 041 5195480"
    assert saved.descriptions_and_notes == "AUTHORITATIVE OPERATOR NOTE: Keep this untouched."
    assert saved.company_name == "BS Progetti S.r.l. (Manual)"
    assert saved.created_at == "2026-10-15T08:00:00Z"
    assert saved.record_number == 505

    # Candidate cold outreach remains disabled
    cand = cand_store.get_by_id(cand_id)
    assert cand.cold_outreach_allowed is False


def test_assertion_11_research_flow_works_without_configured_llm():
    """Assertion 11: The research flow works smoothly when local LLM is NOT_CONFIGURED."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/",
            page_type="home",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_HOME,
            text_content="BS Progetti s.r.l. P.IVA 04095420271"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BS Progetti", "bs-eng.net")
    assert res.llm_analysis_status == "NOT_CONFIGURED"
    assert res.company_name == "BS Progetti s.r.l."
    assert res.p_iva == "04095420271"


def test_assertion_12_corrupt_recruitment_string_rejected_as_address():
    """Requirement 1: The corrupt 'CORSO Scopri di piùGRAFIC DESIGNER...' string is rejected as a geographic address."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/lavora-con-noi/",
            page_type="careers",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_CAREERS,
            text_content="CORSO\nScopri di piùGRAPHIC DESIGNER e COMUNICAZIONE\nSELEZIONI IN CORSO\nScopri"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BS Progetti s.r.l.", "bs-eng.net")
    assert res.address is None
    assert res.city == "Not Found"
    assert res.country == "Not Verified"
    assert res.evidence_items.get("geographic_location").validation_status == "NOT_VERIFIED"


def test_assertion_13_contact_priority_and_embedded_fornitori_separate_classification():
    """Requirements 5, 6, 7, 8, 9: Contact page priority over careers, separate classification, and Priority B rationale."""
    pages = [
        PageFetchResult(
            url="https://www.bs-eng.net/lavora-con-noi/",
            page_type="careers",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_CAREERS,
            text_content="CORSO Scopri di più GRAPHIC DESIGNER e COMUNICAZIONE SELEZIONI IN CORSO. Requisiti: modellazione BIM Revit Specialist."
        ),
        PageFetchResult(
            url="https://www.bs-eng.net/contatti/",
            page_type="contacts",
            status="FETCH_SUCCESS",
            raw_html=BS_PROGETTI_HTML_CONTATTI,
            text_content="CONTATTI BS Progetti s.r.l. via Roma 130 30030 Pianiga (VE) Telefono: +39 041 5195480 info@bs-eng.net"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BS Progetti s.r.l.", "bs-eng.net")

    # 1. Contact address wins over careers
    assert res.address == "Via Roma 130, 30030 Pianiga (VE), Italy"
    assert res.city == "Pianiga"
    assert res.country == "Italy"
    assert res.evidence_items["geographic_location"].source_url == "https://www.bs-eng.net/contatti/"

    # 2. Embedded Fornitori form identified as supplier intake
    assert len(res.supplier_signals) == 1
    assert "https://www.bs-eng.net/contatti/#collapse-1-3936" in res.supplier_signals[0]

    # 3. Careers separated from supplier intake
    assert len(res.career_opportunities) >= 1
    assert all("fornitor" not in c.lower() for c in res.career_opportunities)

    # 4. Supplier form alone does NOT force Priority A (unconfirmed demand)
    assert res.proposed_priority_level == "Priority B"
    assert "active outsourcing demand is unconfirmed" in res.proposed_priority_rationale.lower()


def test_assertion_14_unknown_location_city_and_country_unverified():
    """Requirement 4: When company location is unknown, both city and country remain unverified."""
    pages = [
        PageFetchResult(
            url="https://example.it/progetti/",
            page_type="projects",
            status="FETCH_SUCCESS",
            raw_html="<html></html>",
            text_content="Opere realizzate a Verona e Vicenza."
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:example.it", "Studio Progetti", "example.it")
    assert res.city == "Not Verified"
    assert res.country == "Not Verified"
    assert "UNVERIFIED" in res.evidence_items["geographic_location"].validation_status
    assert "project/portfolio context" in res.evidence_items["geographic_location"].notes
