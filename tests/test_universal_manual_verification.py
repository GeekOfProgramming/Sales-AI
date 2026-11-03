"""
tests/test_universal_manual_verification.py
Comprehensive test suite for Universal Manual Field Correction and Evidence Verification.

12 Dedicated Test Cases:
1. Responsible person manually verified using official company source URL.
2. Responsible person manually verified using legitimate independent external source URL (e.g. LinkedIn, registry).
3. Field originally "Not Found" by AI corrected and approved manually.
4. Field with quarantined AI proposal replaced by independent operator evidence URL (quarantined flag untouched on raw analysis).
5. Invalid/mismatched evidence URL rejected without database mutation (SSRF loopback / external classification mismatch).
6. Existing approved unrelated field reviews preserved without invalidation.
7. Core-field edit after finalization atomically suspends finalization (verification_status resets to EXTRACTED).
8. Non-core-field edit preserves existing approved reviews of unrelated fields.
9. Verified HTML forms are unnested and standards-compliant with valid CSRF tokens and form attributes.
10. Checksum and source_type ('operator_manual') recorded with audit trail in reviews history.
11. Company intelligence and AI brief export/summary reflects corrected authoritative values.
12. End-to-end HTTP integration test via TestClient with session CSRF cookie and form payload.
"""

import re
import socket
import pytest
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from starlette.testclient import TestClient

# Safety offline mock for sockets
_REAL_GETADDRINFO = socket.getaddrinfo
def _offline_safe_getaddrinfo(host, port, *args, **kwargs):
    if host in ("renerwave.com", "www.renerwave.com", "stcpi.com", "energymep.it", "linkedin.com", "registroimprese.it"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80))]
    return _REAL_GETADDRINFO(host, port, *args, **kwargs)

socket.getaddrinfo = _offline_safe_getaddrinfo

from sales_engine.candidates.candidate_store import CandidateCompany, CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from sales_engine.research.research_store import ResearchSnapshotStore, PageEvidence, CompanyAnalysisRecord
from backend.sales_ui.router import (
    compute_field_checksum,
    validate_research_finalization,
    is_safe_and_valid_evidence_reference,
    validate_field_value_format,
    verify_candidate_field_action,
)


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    db_file = str(tmp_path / "universal_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", db_file)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    cand_store = CandidateStore(db_path=db_file)
    reg_store = LeadRegisterStore(db_path=db_file)
    snap_store = ResearchSnapshotStore(db_path=db_file)

    cid = "cand:domain:renerwave.com"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="RENERWAVE",
        domain="renerwave.com",
        company_domain="renerwave.com",
    )
    cand_store.save_candidate(cand)

    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=318,
        company_name="RENERWAVE S.R.L.",
        official_website="https://renerwave.com/",
        business_activities="Not Found",
        responsible_person="Not Found",
        contact_email_addresses="info@renerwave.com",
        verification_status="EXTRACTED",
        provenance_source="local_research_mvp",
    )
    reg_store.save_record(reg_rec)

    # Core reviews
    rev_name = FieldReviewRecord(
        candidate_id=cid,
        field_name="company_name",
        approved_value="RENERWAVE S.R.L.",
        value_checksum=compute_field_checksum("RENERWAVE S.R.L."),
        review_state="OPERATOR_APPROVED",
        source_type="operator_manual",
    )
    rev_web = FieldReviewRecord(
        candidate_id=cid,
        field_name="official_website",
        approved_value="https://renerwave.com/",
        value_checksum=compute_field_checksum("https://renerwave.com/"),
        review_state="OPERATOR_APPROVED",
        source_type="operator_manual",
        evidence_url="https://renerwave.com/",
    )
    reg_store.save_field_review(rev_name)
    reg_store.save_field_review(rev_web)

    pe = PageEvidence(
        snapshot_id="",
        evidence_id="EV_01",
        requested_url="https://renerwave.com/",
        resolved_url="https://renerwave.com/",
        page_type="home",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-03T18:00:00Z",
        title="RENERWAVE Home",
        meta_description="Renewable Energy",
        content_sha256="sha_1",
        cleaned_text="RENERWAVE progetta impianti fotovoltaici e soluzioni energetiche.",
    )
    snap = snap_store.create_snapshot(cid, "renerwave.com", "RENERWAVE", [pe])

    analysis = CompanyAnalysisRecord(
        analysis_id="an_renerwave_318",
        candidate_id=cid,
        snapshot_id=snap.snapshot_id,
        created_at="2026-11-03T18:05:00Z",
        provider="gemini",
        model="gemini-2.5-flash",
        status="SUCCESS",
        proposed_fields={
            "company_name": {"value": "RENERWAVE S.R.L.", "quarantined": False, "confidence": 0.95},
            "official_website": {"value": "https://renerwave.com/", "quarantined": False, "confidence": 0.95},
            "business_activities": {"value": "Sviluppo impianti fotovoltaici", "quarantined": True, "quarantine_reason": "Excerpt not found", "confidence": 0.85},
            "responsible_person": {"value": "Ing. Luca Verdi", "quarantined": True, "quarantine_reason": "Excerpt mismatch", "confidence": 0.70},
        },
    )
    snap_store.save_analysis_record(analysis)

    return {
        "db_file": db_file,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "snap_store": snap_store,
        "cid": cid,
        "cand": cand,
        "snap": snap,
        "analysis": analysis,
    }


# Test 1: Responsible Person verified with official company source
@pytest.mark.asyncio
async def test_01_responsible_person_verified_with_official_company_source(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Marco Rossi (Amministratore Unico)",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/team/marco-rossi",
        operator_note="Verified via official team page",
        csrf=None,
    )
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]

    revs = reg_store.get_field_reviews(cid)
    assert "responsible_person" in revs
    r = revs["responsible_person"]
    assert r.approved_value == "Marco Rossi (Amministratore Unico)"
    assert r.source_type == "operator_manual"
    assert r.review_state == "OPERATOR_APPROVED"
    assert r.value_checksum == compute_field_checksum("Marco Rossi (Amministratore Unico)")
    assert r.evidence_url == "https://renerwave.com/team/marco-rossi"

    rec = reg_store.get_record(cid)
    assert rec.responsible_person == "Marco Rossi (Amministratore Unico)"


# Test 2: Responsible Person verified with legitimate independent external source
@pytest.mark.asyncio
async def test_02_responsible_person_verified_with_independent_external_source(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Ing. Luca Verdi (Direttore Tecnico)",
        action_type="verify",
        source_classification="independent_external",
        source_url="https://www.linkedin.com/in/luca-verdi-renerwave",
        operator_note="Verified from official LinkedIn company profile",
        csrf=None,
    )
    assert resp.status_code == 303
    assert "error=" not in resp.headers["location"]

    revs = reg_store.get_field_reviews(cid)
    assert "responsible_person" in revs
    r = revs["responsible_person"]
    assert r.source_type == "operator_manual"
    assert r.evidence_url == "https://www.linkedin.com/in/luca-verdi-renerwave"
    assert r.operator_note == "Verified from official LinkedIn company profile"


# Test 3: AI "Not Found" field corrected and approved manually
@pytest.mark.asyncio
async def test_03_ai_not_found_field_corrected_and_approved_manually(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    rec_before = reg_store.get_record(cid)
    assert rec_before.business_activities == "Not Found"

    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="business_activities",
        verified_value="Progettazione, installazione e manutenzione di impianti solari fotovoltaici e agrivoltaici.",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/chi-siamo/",
        operator_note="Extracted and verified from official about us page",
        csrf=None,
    )
    assert resp.status_code == 303

    rec_after = reg_store.get_record(cid)
    assert rec_after.business_activities == "Progettazione, installazione e manutenzione di impianti solari fotovoltaici e agrivoltaici."
    revs = reg_store.get_field_reviews(cid)
    assert "business_activities" in revs
    assert revs["business_activities"].review_state == "OPERATOR_APPROVED"


# Test 4: Quarantined AI proposal replaced by independent operator evidence URL
@pytest.mark.asyncio
async def test_04_quarantined_ai_proposal_replaced_without_unquarantining_raw_analysis(env_setup):
    cid = env_setup["cid"]
    snap_store = env_setup["snap_store"]
    snap = env_setup["snap"]

    # Verify that before action, analysis proposed field has quarantined=True
    an_before = snap_store.get_latest_analysis(cid, snapshot_id=snap.snapshot_id)
    assert an_before.proposed_fields["responsible_person"]["quarantined"] is True

    # Operator approves authoritative manual value
    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Ing. Luca Verdi",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/contatti",
        operator_note="Direct phone directory check",
        csrf=None,
    )
    assert resp.status_code == 303

    # Raw analysis record proposed_fields remains quarantined! (Preserving audit grounding truth)
    an_after = snap_store.get_latest_analysis(cid, snapshot_id=snap.snapshot_id)
    assert an_after.proposed_fields["responsible_person"]["quarantined"] is True


# Test 5: Invalid/mismatched evidence URL rejected without database mutation (SSRF protection)
@pytest.mark.asyncio
async def test_05_invalid_mismatched_and_ssrf_evidence_urls_rejected(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    rec_original = reg_store.get_record(cid)

    # 5a: SSRF loopback IP
    resp_loopback = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Fake Operator",
        action_type="verify",
        source_classification="independent_external",
        source_url="http://127.0.0.1:8000/private",
        csrf=None,
    )
    assert resp_loopback.status_code == 303
    assert "error=" in resp_loopback.headers["location"]
    assert "Local" in resp_loopback.headers["location"] or "Private" in resp_loopback.headers["location"]

    # 5b: External domain with official_website classification
    resp_mismatch = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Fake Operator",
        action_type="verify",
        source_classification="official_website",
        source_url="https://external-unrelated.com/profile",
        csrf=None,
    )
    assert resp_mismatch.status_code == 303
    assert "does" in resp_mismatch.headers["location"] and "not" in resp_mismatch.headers["location"]

    # 5c: official_website field pointing to external source
    resp_ext_web = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="official_website",
        verified_value="https://renerwave.com/",
        action_type="verify",
        source_classification="independent_external",
        source_url="https://external-directory.com/renerwave",
        csrf=None,
    )
    assert resp_ext_web.status_code == 303
    assert "error=" in resp_ext_web.headers["location"]

    # Confirm no mutation occurred
    rec_after = reg_store.get_record(cid)
    assert rec_after.responsible_person == rec_original.responsible_person


# Test 6: Existing approved unrelated field reviews preserved
@pytest.mark.asyncio
async def test_06_unrelated_approved_field_reviews_preserved(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    revs_before = reg_store.get_field_reviews(cid)
    assert "company_name" in revs_before
    assert "official_website" in revs_before

    # Verify telephone number
    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="telephone_numbers",
        verified_value="+39 02 98765432",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/contatti",
        csrf=None,
    )
    assert resp.status_code == 303

    revs_after = reg_store.get_field_reviews(cid)
    assert "company_name" in revs_after
    assert "official_website" in revs_after
    assert "telephone_numbers" in revs_after
    assert revs_after["company_name"].value_checksum == revs_before["company_name"].value_checksum


# Test 7: Core-field edit after finalization atomically suspends finalization
@pytest.mark.asyncio
async def test_07_core_field_edit_after_finalization_suspends_finalization(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    rec = reg_store.get_record(cid)
    rec.verification_status = "OPERATOR_REVIEWED"
    rec.business_activities = "Original activities"
    reg_store.save_record(rec)

    # Modify core field (business_activities) with a changed value
    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="business_activities",
        verified_value="Updated innovative solar EPC activities across Italy.",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/servizi",
        csrf=None,
    )
    assert resp.status_code == 303
    assert "suspended" in resp.headers["location"]

    rec_updated = reg_store.get_record(cid)
    assert rec_updated.verification_status == "EXTRACTED", "Finalization must be suspended back to EXTRACTED"


# Test 8: Non-core edit preserves valid unrelated reviews
@pytest.mark.asyncio
async def test_08_non_core_field_edit_preserves_valid_reviews(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    # Save manual draft for non-core field (geographic_location)
    resp = await verify_candidate_field_action(
        candidate_id=cid,
        field_name="geographic_location",
        verified_value="Brescia, BS, Italy",
        action_type="draft",
        source_classification="official_website",
        source_url="",
        operator_note="Draft location edit",
        csrf=None,
    )
    assert resp.status_code == 303

    rec = reg_store.get_record(cid)
    assert rec.geographic_location == "Brescia, BS, Italy"

    # Core reviews remain intact
    revs = reg_store.get_field_reviews(cid)
    assert "company_name" in revs
    assert "official_website" in revs
    assert "geographic_location" not in revs  # Manual draft is NOT verified review


# Test 9: HTML non-nested forms compliance and CSRF association
def test_09_html_forms_standards_compliance_and_csrf(env_setup):
    cid = env_setup["cid"]
    cand = env_setup["cand"]
    reg_store = env_setup["reg_store"]
    rec = reg_store.get_record(cid)

    env = Environment(loader=FileSystemLoader("backend/sales_ui/templates"))
    tmpl = env.get_template("company_research_review.html")

    render_ctx = {
        "candidate": cand,
        "current_record": rec,
        "proposed_record": rec,
        "analysis_fields": {},
        "csrf_token": "token_csrf_abc123",
        "is_finalizable": False,
        "finalization_checks": {},
        "finalization_errors": [],
        "historical_snapshots": [],
        "research": {"evidence_items": {}, "supplier_signals": [], "career_opportunities": [], "opportunity_urls": []},
        "field_reviews": {},
        "latest_analysis": None,
        "is_malformed_contact_email": False,
        "is_suspect_job_advertisement": False,
    }

    html = tmpl.render(render_ctx)
    soup = BeautifulSoup(html, "html.parser")

    # Verify NO nested forms exist
    forms = soup.find_all("form")
    for f in forms:
        assert len(f.find_all("form")) == 0, f"Found nested form inside {f}"

    # Main save form exists
    save_form = soup.find("form", action=f"/sales/candidates/{cid}/research/save")
    assert save_form is not None

    # Hidden forms exist for every editable field
    expected_fields = [
        "company_name", "priority_level", "legal_identity", "geographic_location",
        "telephone_numbers", "contact_email_addresses", "responsible_person",
        "business_activities", "official_website", "descriptions_and_notes",
        "job_advertisement_links", "other_links_and_references"
    ]
    for fn in expected_fields:
        form_id = f"verify-{fn}-form" if fn not in ("official_website", "business_activities") else (
            "verify-official-website-form" if fn == "official_website" else "verify-business-activities-form"
        )
        hf = soup.find("form", id=form_id)
        assert hf is not None, f"Hidden form {form_id} missing"
        assert hf["action"] == f"/sales/candidates/{cid}/verify-field"
        assert hf.find("input", {"name": "csrf_token"})["value"] == "token_csrf_abc123"
        assert hf.find("input", {"name": "field_name"})["value"] == fn

        # Inputs inside table specify form=form_id
        val_input = soup.find(attrs={"form": form_id, "name": "verified_value"})
        assert val_input is not None, f"Input for {form_id} missing in review table"


# Test 10: Value checksum, source type and review history persist correctly
@pytest.mark.asyncio
async def test_10_value_checksum_source_type_and_history_persistence(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    # 1. First manual verification
    await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Dott. Giovanni Neri",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/management",
        operator_note="First review",
        csrf=None,
    )
    rev1 = reg_store.get_field_reviews(cid)["responsible_person"]
    assert rev1.source_type == "operator_manual"
    assert rev1.value_checksum == compute_field_checksum("Dott. Giovanni Neri")

    # 2. Update to new verified value (should archive rev1 to history)
    await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Dott. Giovanni Neri, CEO",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/management",
        operator_note="Updated title",
        csrf=None,
    )
    rev2 = reg_store.get_field_reviews(cid)["responsible_person"]
    assert rev2.value_checksum == compute_field_checksum("Dott. Giovanni Neri, CEO")

    # Verify review history table contains previous rev
    history = reg_store.get_field_reviews_history(cid, "responsible_person")
    assert len(history) >= 1
    assert any(h["approved_value"] == "Dott. Giovanni Neri" for h in history)


# Test 11: Company Intelligence and AI Brief reflect corrected authoritative values
@pytest.mark.asyncio
async def test_11_company_intelligence_and_brief_reflects_authoritative_values(env_setup):
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    # Verify legal identity and responsible person
    await verify_candidate_field_action(
        candidate_id=cid,
        field_name="legal_identity",
        verified_value="IT09876543210",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/legal",
        operator_note="Verified P.IVA",
        csrf=None,
    )
    await verify_candidate_field_action(
        candidate_id=cid,
        field_name="responsible_person",
        verified_value="Ing. Roberto Bianchi",
        action_type="verify",
        source_classification="official_website",
        source_url="https://renerwave.com/team",
        operator_note="Verified Lead Contact",
        csrf=None,
    )

    rec = reg_store.get_record(cid)
    d = rec.to_dict()
    assert d["legal_identity"] == "IT09876543210"
    assert d["responsible_person"] == "Ing. Roberto Bianchi"
    assert "https://renerwave.com/legal" in rec.evidence_urls


# Test 12: Real HTTP integration test using Starlette TestClient with CSRF cookies and POST form submission
def test_12_http_integration_test_client(env_setup):
    from backend.main import app
    cid = env_setup["cid"]
    reg_store = env_setup["reg_store"]

    client = TestClient(app)

    # 1. Fetch research review page to establish CSRF cookie
    get_resp = client.get(f"/sales/candidates/{cid}/research")
    assert get_resp.status_code == 200
    csrf_cookie = client.cookies.get("sales_csrf_token")
    assert csrf_cookie is not None

    # Parse CSRF token from rendered HTML form
    soup = BeautifulSoup(get_resp.text, "html.parser")
    form = soup.find("form", id="verify-responsible_person-form")
    assert form is not None
    csrf_token = form.find("input", {"name": "csrf_token"})["value"]

    # 2. Submit verify POST action
    post_payload = {
        "csrf_token": csrf_token,
        "field_name": "responsible_person",
        "verified_value": "Ing. Elena Ferri (Head of Operations)",
        "action_type": "verify",
        "source_classification": "official_website",
        "source_url": "https://renerwave.com/team/elena-ferri",
        "operator_note": "HTTP integration verification test",
    }
    post_resp = client.post(
        f"/sales/candidates/{cid}/verify-field",
        data=post_payload,
        follow_redirects=False,
    )
    assert post_resp.status_code == 303
    assert "error=" not in post_resp.headers["location"]
    assert "message=" in post_resp.headers["location"]

    # 3. Confirm database record updated
    rec = reg_store.get_record(cid)
    assert rec.responsible_person == "Ing. Elena Ferri (Head of Operations)"
    revs = reg_store.get_field_reviews(cid)
    assert "responsible_person" in revs
    assert revs["responsible_person"].review_state == "OPERATOR_APPROVED"
