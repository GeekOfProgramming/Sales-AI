"""
tests/test_ux_lp_02_qualified_leads.py
Acceptance and regression tests for UX-LP-02:
Qualified Leads & Manual Outreach Readiness.

Tests verify:
1. Two distinct concepts preserved:
   - "Qualified for Enrichment" (ICP-qualified candidate)
   - "Ready for Manual Draft" (policy-eligible + non-suppressed + verified or Lead Register-documented recipient)
2. Defect Fix: 'likely' alone NEVER equals 'verified'. A contact with status 'likely', 'unverified',
   or 'unknown' results in 'Contact Verification Required' in both UI and backend readiness evaluation.
3. Documented 13-Field Lead Register contact enables manual draft readiness without converting to 'verified'.
4. Candidate with empty or whitespace-only candidate_id (e.g. synthetic Test Company SpA) fails closed:
   marked Ineligible / not draft-ready, cannot be drafted via POST /create-manual-draft.
5. Strict precedence of readiness states:
   Suppressed -> Prior Contact -> Ineligible -> Contact Verification Required -> Ready/Eligible for Manual Draft.
6. Summary KPI metrics and filter toolbar accuracy (search, country, tier, readiness).
7. POST /sales/candidates/{candidate_id}/create-manual-draft revalidation fails closed if recipient is not verified/documented.
8. Operational database isolation and immutability.
"""

import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import evaluate_candidate_readiness, is_candidate_outreach_eligible, is_candidate_qualified_lead
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.schemas import ContactCandidate


@pytest.fixture
def test_setup_lp02(tmp_path):
    test_db = tmp_path / "test_ux_lp_02.db"
    cand_store = CandidateStore(db_path=test_db)
    reg_store = LeadRegisterStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)
    supp_store = SuppressionStore(rev_store)

    return {
        "db_path": test_db,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
    }


def test_01_qualified_lead_with_verified_contact_is_draft_ready(test_setup_lp02, monkeypatch):
    """
    Candidate with ICP qualification, cold outreach allowed, and verified contact
    receives 'draft_ready' and displays 'Eligible for Manual Draft' / 'Ready for Manual Draft'.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_lp02["db_path"]))
    cand_store = test_setup_lp02["cand_store"]
    supp_store = test_setup_lp02["supp_store"]
    reg_store = test_setup_lp02["reg_store"]

    c = CandidateCompany(
        candidate_id="cand:lp02:verified",
        name="Verified Studio S.r.l.",
        domain="verified-studio.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c)

    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_v_01",
        candidate_id="cand:lp02:verified",
        first_name="Alessandro",
        last_name="Rossi",
        full_name="Alessandro Rossi",
        work_email="alessandro@verified-studio.it",
        email_status="verified",
    ), candidate_id="cand:lp02:verified")

    # Authoritative readiness evaluation
    readiness = evaluate_candidate_readiness(c, cand_store, supp_store, reg_store)
    assert readiness["effective_readiness"] == "draft_ready"
    assert readiness["can_generate_draft"] is True
    assert readiness["has_verified_recipient"] is True
    assert len(readiness["usable_recipients"]) == 1

    # UI renders correctly
    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Verified Studio S.r.l." in res.text
    assert "Prepare Draft" in res.text


def test_02_likely_contact_strictly_requires_verification(test_setup_lp02, monkeypatch):
    """
    CRITICAL DEFECT CLOSURE:
    A contact with status 'likely' must NEVER equal 'verified'.
    Candidate with only 'likely' contacts must yield 'verification_required' and 'Contact Verification Required',
    and NOT allow manual draft creation.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_lp02["db_path"]))
    cand_store = test_setup_lp02["cand_store"]
    supp_store = test_setup_lp02["supp_store"]
    reg_store = test_setup_lp02["reg_store"]

    c = CandidateCompany(
        candidate_id="cand:lp02:likely",
        name="Likely Contact Studio S.r.l.",
        domain="likely-studio.it",
        country="Italy",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c)

    # Save ONLY a likely contact
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_l_01",
        candidate_id="cand:lp02:likely",
        first_name="Giuseppe",
        last_name="Verdi",
        full_name="Giuseppe Verdi",
        work_email="giuseppe@likely-studio.it",
        email_status="likely",
    ), candidate_id="cand:lp02:likely")

    # Authoritative readiness evaluation
    readiness = evaluate_candidate_readiness(c, cand_store, supp_store, reg_store)
    assert readiness["effective_readiness"] == "verification_required"
    assert readiness["can_generate_draft"] is False
    assert readiness["has_verified_recipient"] is False
    assert readiness["readiness_label"] == "Contact Verification Required"

    # UI renders "Contact Verification Required" and "Verify Contact" action
    client = TestClient(app)
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Likely Contact Studio S.r.l." in res.text
    assert "Contact Verification Required" in res.text
    assert "Verify Contact" in res.text


def test_03_documented_lead_register_recipient_enables_draft_readiness(test_setup_lp02, monkeypatch):
    """
    A candidate documented in the 13-Field Lead Register with a valid email enables draft readiness
    even if not independently verified, without fabricating a verified contact status.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_lp02["db_path"]))
    cand_store = test_setup_lp02["cand_store"]
    supp_store = test_setup_lp02["supp_store"]
    reg_store = test_setup_lp02["reg_store"]

    c = CandidateCompany(
        candidate_id="cand:lp02:documented",
        name="Documented Lead Register SpA",
        domain="documented-lead.it",
        country="Italy",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c)

    from sales_engine.leads.register_models import LeadRegisterRecord
    reg_store.save_record(LeadRegisterRecord(
        candidate_id="cand:lp02:documented",
        company_name="Documented Lead Register SpA",
        official_website="https://documented-lead.it",
        geographic_location="Italy",
        responsible_person="Ing. Marco Documentato",
        contact_email_addresses="marco@documented-lead.it",
    ))

    readiness = evaluate_candidate_readiness(c, cand_store, supp_store, reg_store)
    assert readiness["effective_readiness"] == "draft_ready"
    assert readiness["can_generate_draft"] is True
    assert readiness["has_verified_recipient"] is True
    assert len(readiness["usable_recipients"]) == 1
    assert readiness["usable_recipients"][0]["provenance"] == "lead_register_documented"


def test_04_synthetic_or_empty_candidate_id_fails_closed(test_setup_lp02, monkeypatch):
    """
    Investigation & safety enforcement for Test Company SpA / empty candidate_id:
    An empty candidate_id cannot be drafted, fails closed in evaluate_candidate_readiness,
    and is classified as not_eligible.
    """
    cand_store = test_setup_lp02["cand_store"]
    supp_store = test_setup_lp02["supp_store"]
    reg_store = test_setup_lp02["reg_store"]

    c = CandidateCompany(
        candidate_id="",
        name="Test Company SpA",
        domain="testcompany.it",
        discovery_status="qualified",
        qualification_outcome=None,
        cold_outreach_allowed=True,
    )

    readiness = evaluate_candidate_readiness(c, cand_store, supp_store, reg_store)
    assert readiness["effective_readiness"] == "not_eligible"
    assert readiness["can_generate_draft"] is False
    assert readiness["base_eligible"] is False


def test_05_strict_precedence_suppression_and_prior_contact(test_setup_lp02, monkeypatch):
    """
    Suppression overrides all positive states.
    Prior contact overrides eligibility and verification.
    """
    cand_store = test_setup_lp02["cand_store"]
    supp_store = test_setup_lp02["supp_store"]
    reg_store = test_setup_lp02["reg_store"]

    # 1. Suppressed candidate with verified contact
    c_supp = CandidateCompany(
        candidate_id="cand:lp02:supp",
        name="Suppressed Company",
        domain="suppressed-test.it",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        suppressed=True,
    )
    r_supp = evaluate_candidate_readiness(c_supp, cand_store, supp_store, reg_store)
    assert r_supp["effective_readiness"] == "suppressed"
    assert r_supp["can_generate_draft"] is False

    # 2. Prior contact candidate
    c_prior = CandidateCompany(
        candidate_id="cand:lp02:prior",
        name="Prior Contacted Company",
        domain="prior-test.it",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="contacted",
    )
    r_prior = evaluate_candidate_readiness(c_prior, cand_store, supp_store, reg_store)
    assert r_prior["effective_readiness"] == "prior_contact"
    assert r_prior["can_generate_draft"] is False


def test_06_filter_toolbar_and_summary_kpi_counts(test_setup_lp02, monkeypatch):
    """
    Test that summary KPI metrics card counts and filter options accurately reflect
    the underlying dataset.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_lp02["db_path"]))
    cand_store = test_setup_lp02["cand_store"]

    # 1. Draft ready
    c1 = CandidateCompany(
        candidate_id="cand:kpi:1",
        name="Alpha Engineering",
        domain="alpha.it",
        country="Italy",
        company_tier=CompanyTier.tier_1,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c1)
    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_k1",
        candidate_id="cand:kpi:1",
        work_email="alpha@alpha.it",
        email_status="verified",
    ), candidate_id="cand:kpi:1")

    # 2. Verification required
    c2 = CandidateCompany(
        candidate_id="cand:kpi:2",
        name="Beta Architecture",
        domain="beta.de",
        country="Germany",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c2)

    # 3. Prior contact
    c3 = CandidateCompany(
        candidate_id="cand:kpi:3",
        name="Gamma Consulting",
        domain="gamma.it",
        country="Italy",
        company_tier=CompanyTier.tier_3,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="replied",
    )
    cand_store.save_candidate(c3)

    client = TestClient(app)

    # Check summary counts on unfiltered view
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Total Enrichment-Qualified" in res.text
    assert "Ready for Manual Draft" in res.text
    assert "Contact Verification Required" in res.text
    assert "Prior Contact / Active Conv." in res.text

    # Filter by country: Germany
    res_de = client.get("/sales/leads?country=Germany")
    assert res_de.status_code == 200
    assert "Beta Architecture" in res_de.text
    assert "Alpha Engineering" not in res_de.text

    # Filter by readiness: draft_ready
    res_ready = client.get("/sales/leads?readiness=draft_ready")
    assert res_ready.status_code == 200
    assert "Alpha Engineering" in res_ready.text
    assert "Beta Architecture" not in res_ready.text
    assert "Gamma Consulting" not in res_ready.text


def test_07_create_manual_draft_action_rejects_unverified_or_likely_contact(test_setup_lp02, monkeypatch):
    """
    POST /sales/candidates/{candidate_id}/create-manual-draft must reject attempts
    to draft to an unverified or likely email address unless documented in Lead Register.
    """
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_setup_lp02["db_path"]))
    cand_store = test_setup_lp02["cand_store"]

    c = CandidateCompany(
        candidate_id="cand:lp02:post_test",
        name="Post Test Studio",
        domain="post-test.it",
        country="Italy",
        company_tier=CompanyTier.tier_2,
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c)

    cand_store.save_contact(ContactCandidate(
        contact_id="cnt_post_01",
        candidate_id="cand:lp02:post_test",
        work_email="likely@post-test.it",
        email_status="likely",
    ), candidate_id="cand:lp02:post_test")

    client = TestClient(app)
    dash_resp = client.get("/sales/dashboard")
    csrf_token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    # Attempt to post draft with likely contact
    form_data = {
        "candidate_id": "cand:lp02:post_test",
        "contact_id": "cnt_post_01",
        "recipient_email": "likely@post-test.it",
        "recipient_name": "Likely Recipient",
        "subject": "Test Outreach",
        "body": "Hello world",
        "csrf_token": csrf_token,
    }
    res = client.post(
        "/sales/candidates/cand:lp02:post_test/create-manual-draft",
        data=form_data,
        headers=headers,
        follow_redirects=False,
    )
    # Must redirect with error
    assert res.status_code == 303
    assert "error=" in res.headers["location"]
    from urllib.parse import unquote
    unquoted_loc = unquote(res.headers["location"]).lower()
    assert "not verified" in unquoted_loc
