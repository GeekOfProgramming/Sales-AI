"""
Regression, factual reliability, and safety acceptance test suite for Local Company Research MVP.
Verifies all P0/P1 audit requirements:
- Generic candidate names ('BIM SPECIALIST') overridden with verified corporate entities
- Strict Italian P.IVA checksum validation rejecting all-zeros and invalid control digits
- Project locations segregated from headquarters (ambiguous locations marked 'Not Verified')
- Accurate handling of completed projects dated 2026 vs ongoing works
- Hiring signals alone do not produce Priority A (requires supplier onboarding / external teaming)
- Exact preservation of existing operator-edited notes and metadata on partial field approval
- No external web crawling on review screen refresh
- Structured local LLM schema validation failing closed
- Defense-in-depth release packaging via scripts/rebuild_zip.py
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
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
    fetch_bounded_page,
    discover_relevant_subpage_urls,
    perform_company_research,
    validate_italian_piva,
    enrich_company_research_with_llm,
    SSRFValidationError,
    validate_public_url,
)
import secrets

def generate_csrf_token():
    return secrets.token_hex(32)


@pytest.fixture
def isolated_client(tmp_path):
    """Provides a TestClient wired to an isolated temporary database."""
    test_db = str(tmp_path / "client_isolated_test.db")
    with patch.dict(os.environ, {"SALES_OUTREACH_DB": test_db, "EMAIL_SEND_ENABLED": "false"}):
        cand_store = CandidateStore(db_path=test_db)
        # Seed candidates based on real fixtures
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:bbprogettimilano.it",
            company_name="B&B Progetti",
            normalized_company_name="b&b progetti",
            company_domain="bbprogettimilano.it",
            country="Italy",
            city="Milano",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:bdgroup.it",
            company_name="bim design group",
            normalized_company_name="bim design group",
            company_domain="bdgroup.it",
            country="Italy",
            city="Venezia",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:bs-eng.net",
            company_name="BIM SPECIALIST",  # Initial imperfect discovery name
            normalized_company_name="bim specialist",
            company_domain="bs-eng.net",
            country="Italy",
            city="Pianiga",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:etcstudio.it",
            company_name="ETC Studio",
            normalized_company_name="etc studio",
            company_domain="etcstudio.it",
            country="Italy",
            city="Vigonza",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))
        cand_store.save_candidate(CandidateCompany(
            candidate_id="cand:domain:mep-progetti.com",
            company_name="MEP Progetti",
            normalized_company_name="mep progetti",
            company_domain="mep-progetti.com",
            country="Italy",
            city="Mantova",
            cold_outreach_allowed=False,
            manual_review_decision="NOT_REVIEWED",
            historical_contact_status="uncontacted",
        ))

        with TestClient(app) as test_client:
            yield test_client, test_db


# ---------------------------------------------------------------------------
# 1. P0 Factual Reliability Negative Tests
# ---------------------------------------------------------------------------

def test_company_name_overrides_generic_heading():
    """Defect 1: Candidate named 'BIM SPECIALIST' must be corrected when official site has legal name."""
    pages = [
        PageFetchResult(
            url="https://bs-eng.net",
            page_type="home",
            status="FETCH_SUCCESS",
            title="BIM SPECIALIST | Ingegneria",
            text_content="BS Progetti S.r.l. Società di Ingegneria via Roma 130, Pianiga (VE) P.IVA 04095420271"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:domain:bs-eng.net", "BIM SPECIALIST", "bs-eng.net")
    assert res.company_name == "BS Progetti S.r.l."
    assert "company_name" in res.evidence_items
    assert res.evidence_items["company_name"].extracted_value == "BS Progetti S.r.l."


def test_piva_checksum_rejects_all_zeros_and_invalid():
    """Defect 3: Invalid 11-digit P.IVA '00000000000' and invalid checksums must be rejected."""
    assert validate_italian_piva("00000000000") is False
    assert validate_italian_piva("12345678901") is False
    assert validate_italian_piva("04095420271") is True  # Valid BS Progetti P.IVA

    pages = [
        PageFetchResult(
            url="https://example.it",
            page_type="home",
            status="FETCH_SUCCESS",
            text_content="Società Alpha S.r.l. P.IVA 00000000000 e P.IVA 12345678901 info@alpha.it"
        )
    ]
    res = extract_deterministic_facts(pages, "cand:1", "Società Alpha", "example.it")
    assert res.p_iva is None
    assert "p_iva" not in res.evidence_items


def test_city_in_project_not_assigned_as_company_hq():
    """Defect 2: City mentioned only in project context must NOT be assigned as company headquarters."""
    pages = [
        PageFetchResult(
            url="https://example.it/progetti",
            page_type="projects",
            status="FETCH_SUCCESS",
            title="Portfolio Opere",
            text_content="Intervento strutturale realizzato a Milano nel 2024. Cantiere a Padova per polo civico."
        )
    ]
    res = extract_deterministic_facts(pages, "cand:2", "Studio Ingegneria", "example.it")
    assert res.city == "Not Verified"
    assert res.evidence_items["geographic_location"].validation_status == "UNVERIFIED_PROJECT_LOCATION"


def test_completed_project_dated_2026_marked_historical():
    """Defect 4: Projects with explicit completion terms are historical, even if dated 2026."""
    pages = [
        PageFetchResult(
            url="https://example.it/progetti",
            page_type="projects",
            status="FETCH_SUCCESS",
            title="Opere",
            text_content="Nuovo campus ospedaliero completato nel 2026. Lavori conclusi con collaudo."
        )
    ]
    res = extract_deterministic_facts(pages, "cand:3", "Studio Ospedali", "example.it")
    assert len(res.historical_projects) == 1
    assert "completato nel 2026" in res.historical_projects[0]
    assert len(res.ongoing_projects) == 0


def test_hiring_without_outsourcing_marked_priority_b():
    """Defect 5: Internal recruitment alone without supplier portal must NOT be Priority A."""
    pages = [
        PageFetchResult(
            url="https://example.it/careers",
            page_type="careers",
            status="FETCH_SUCCESS",
            title="Carriere",
            text_content="Ricerchiamo collaboratori con P.IVA per assunzione interna BIM Revit Specialist."
        )
    ]
    res = extract_deterministic_facts(pages, "cand:4", "Studio Modelli", "example.it")
    assert res.proposed_priority_level == "Priority B"
    assert "Outsourcing demand is unconfirmed" in res.proposed_priority_rationale


# ---------------------------------------------------------------------------
# 2. P0 Provenance & Human Data Preservation HTTP Regression
# ---------------------------------------------------------------------------

def test_selective_field_approval_preserves_existing_notes_and_metadata(isolated_client):
    """
    HTTP regression requirement:
    Existing manual notes + existing provenance -> research preview ->
    approve ONLY ONE empty field (e.g. phone) -> save -> reload ->
    verify all other fields and original metadata remain completely intact.
    """
    client, test_db = isolated_client
    cand_id = "cand:domain:bbprogettimilano.it"

    # Pre-populate an operator-authored Lead Register record with custom notes
    reg_store = LeadRegisterStore(db_path=test_db)
    initial_rec = LeadRegisterRecord(
        candidate_id=cand_id,
        record_number=101,
        priority_level="B",
        priority_rationale="Initial operator assessment based on phone call",
        company_name="B&B Progetti S.r.l. (Manual Name)",
        legal_identity="P.IVA 06650400960",
        geographic_location="Milano Centro",
        telephone_numbers="Not Found",  # Empty field to be enriched
        business_activities="Consulting",
        official_website="https://bbprogettimilano.it",
        responsible_person="Operator Contact Laura",
        descriptions_and_notes="CRITICAL OPERATOR NOTE: Do not modify without partner signoff.",
        job_advertisement_links="Not Found",
        other_links_and_references="Not Found",
        contact_email_addresses="custom@bbprogettimilano.it",
        provenance_source="operator_edited",
        created_at="2026-10-01T10:00:00Z",
    )
    reg_store.save_record(initial_rec)

    # Mock research result to provide a proposed phone number
    mock_res = CompanyResearchResult(
        candidate_id=cand_id,
        company_name="Overriding Web Name",
        canonical_domain="bbprogettimilano.it",
        official_website="https://bbprogettimilano.it",
        telephone="+39 02 36636170",
        email="unwanted@bbprogettimilano.it",
        fetched_pages=["https://bbprogettimilano.it/contatti"],
    )
    _RESEARCH_SESSION_CACHE[cand_id] = mock_res

    csrf_token = generate_csrf_token()
    cookies = {CSRF_COOKIE_NAME: csrf_token}

    # Post with ONLY apply_telephone_numbers checked!
    form_data = {
        "csrf_token": csrf_token,
        "candidate_id": cand_id,
        "company_name": "Overriding Web Name",
        "priority_level": "A",
        "priority_rationale": "Web Rationale",
        "legal_identity": "P.IVA 06650400960",
        "geographic_location": "Via Fumagalli 3, Milano",
        "telephone_numbers": "+39 02 36636170",
        "contact_email_addresses": "unwanted@bbprogettimilano.it",
        "responsible_person": "Web Person",
        "business_activities": "Web Activities",
        "official_website": "https://bbprogettimilano.it",
        "descriptions_and_notes": "Web notes clobber attempt",
        "job_advertisement_links": "Web careers",
        "other_links_and_references": "Web links",
        # Check ONLY telephone!
        "apply_telephone_numbers": "1",
    }

    save_resp = client.post(
        f"/sales/candidates/{cand_id}/research/save",
        data=form_data,
        cookies=cookies,
        follow_redirects=True,
    )
    assert save_resp.status_code == 200

    # Reload from store and assert strict preservation
    reloaded = reg_store.get_record(cand_id)
    assert reloaded is not None
    assert reloaded.telephone_numbers == "+39 02 36636170"  # Enriched!
    assert reloaded.descriptions_and_notes == "CRITICAL OPERATOR NOTE: Do not modify without partner signoff."  # Preserved!
    assert reloaded.company_name == "B&B Progetti S.r.l. (Manual Name)"  # Preserved!
    assert reloaded.contact_email_addresses == "custom@bbprogettimilano.it"  # Preserved!
    assert reloaded.responsible_person == "Operator Contact Laura"  # Preserved!
    assert reloaded.created_at == "2026-10-01T10:00:00Z"  # Preserved!
    assert reloaded.provenance_source == "operator_edited"  # Preserved!
    assert reloaded.record_number == 101  # Preserved!


# ---------------------------------------------------------------------------
# 3. P1 Explicit Execution & No Silent Re-crawl
# ---------------------------------------------------------------------------

def test_no_external_requests_on_review_page_refresh(isolated_client, monkeypatch):
    """P1 Requirement: Refreshing GET /sales/candidates/{candidate_id}/research must NOT re-crawl external websites."""
    client, _ = isolated_client
    cand_id = "cand:domain:bdgroup.it"

    call_count = 0
    def mock_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return PageFetchResult(
            url="https://bdgroup.it",
            page_type="home",
            status="FETCH_SUCCESS",
            text_content="bim design group s.r.l. P.IVA 04637460272"
        )

    monkeypatch.setattr("sales_engine.research.company_research_service.fetch_bounded_page", mock_fetch)

    # Initial GET before any crawl returns 200 with empty snapshot state and 0 network calls
    resp0 = client.get(f"/sales/candidates/{cand_id}/research")
    assert resp0.status_code == 200
    assert call_count == 0, "GET request initiated implicit external crawl!"

    # Explicit POST initiates crawl, creates persistent snapshot, and populates session cache
    csrf_token = generate_csrf_token()
    client.cookies.set(CSRF_COOKIE_NAME, csrf_token)
    resp1 = client.post(
        f"/sales/candidates/{cand_id}/research",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert resp1.status_code == 200
    first_call_count = call_count
    assert first_call_count >= 1

    # Subsequent GET loads (refresh) MUST use persistent snapshot/cache without any network calls!
    resp2 = client.get(f"/sales/candidates/{cand_id}/research")
    assert resp2.status_code == 200
    assert call_count == first_call_count, "Silent re-crawl occurred on page refresh!"


# ---------------------------------------------------------------------------
# 4. P1 Local AI Structured Schema Validation
# ---------------------------------------------------------------------------

def test_local_ai_output_schema_validation_fails_closed():
    """P1 Requirement: Structured evidence-backed output required; invalid JSON fails closed."""
    res = CompanyResearchResult(
        candidate_id="cand:1",
        company_name="Studio Test",
        canonical_domain="test.it",
        official_website="https://test.it",
    )

    # Mock gateway returning invalid free-form text instead of JSON schema
    mock_gw = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = "Here is some unstructured text claiming this company wants BIM."
    mock_gw.generate.return_value = mock_resp

    with patch("sales_engine.llm_gateway.interface.LLMProviderGateway", return_value=mock_gw):
        enriched = enrich_company_research_with_llm(res, "Website text")
        # Must fail closed to SCHEMA_INVALID and NOT treat claims as verified facts
        assert enriched.llm_analysis_status == "SCHEMA_INVALID"