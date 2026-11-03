"""
UX-LP-02a Readiness Consistency, Contact Approval Integrity & Release Closure Acceptance Suite.

Covers all 17 matrix scenarios across:
  - evaluate_candidate_readiness() authoritative evaluator
  - /sales/leads qualified leads directory view
  - /sales/candidates/{candidate_id} company workspace view
  - POST /sales/candidates/{candidate_id}/create-manual-draft POST action handler
"""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from backend.sales_ui.router import evaluate_candidate_readiness


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    """Creates isolated temporary databases and TestClient using backend.main.app for UX-LP-02a tests."""
    outreach_db = str(tmp_path / "test_lp02a_outreach.db")
    research_db = str(tmp_path / "test_lp02a_research.sqlite3")

    monkeypatch.setenv("SALES_OUTREACH_DB", outreach_db)
    monkeypatch.setenv("SALESAI_DB_PATH", outreach_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", outreach_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    cand_store = CandidateStore(db_path=outreach_db)
    reg_store = LeadRegisterStore(db_path=outreach_db)
    rev_store = ReviewStore(db_path=outreach_db)
    supp_store = SuppressionStore(rev_store)

    from backend.main import app
    client = TestClient(app)
    dash_resp = client.get("/sales/dashboard")
    csrf_token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    return {
        "client": client,
        "cand_store": cand_store,
        "reg_store": reg_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "csrf_token": csrf_token,
        "headers": headers,
        "db_path": outreach_db,
    }


# ==============================================================================
# SCENARIO MATRIX 1 - 17
# ==============================================================================

def test_scenario_01_qualified_verified_recipient(env_setup):
    """Scenario 1: Qualified, No Prior Contact, Not Suppressed, Verified recipient exists."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s1:verified"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Studio Alfa Srl",
        company_domain="studioalfa.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:alfa:1",
            candidate_id=cid,
            full_name="Mario Rossi",
            work_email="mario@studioalfa.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "draft_ready"
    assert r["readiness_label"] == "Eligible for Manual Draft"
    assert r["can_generate_draft"] is True
    assert r["has_verified_recipient"] is True

    # Leads view check
    resp_leads = client.get("/sales/leads")
    assert "Studio Alfa Srl" in resp_leads.text
    assert "Eligible for Manual Draft" in resp_leads.text

    # Detail view check
    resp_detail = client.get(f"/sales/candidates/{cid}")
    assert resp_detail.status_code == 200
    assert "Draft Eligible" in resp_detail.text
    assert "Stage Manual Draft to Review Queue" in resp_detail.text

    # POST draft check
    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:alfa:1",
            "recipient_email": "mario@studioalfa.it",
            "recipient_name": "Mario Rossi",
            "subject": "BIM Collaboration",
            "body": "Hello Mario, proposal details here.",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "/sales/drafts/draft:manual:" in resp_post.headers["location"]


def test_scenario_02_qualified_documented_unverified(env_setup):
    """Scenario 2: Qualified, No Prior Contact, Not Suppressed, Documented email in 13-Field Register (unverified)."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s2:documented"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Beta Ingegneria",
        company_domain="betaing.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_2,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    reg_store.save_record(
        LeadRegisterRecord(
            candidate_id=cid,
            record_number=102,
            company_name="Beta Ingegneria",
            contact_email_addresses="contatti@betaing.it",
            responsible_person="Ing. Laura Bianchi",
        )
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "verification_required"
    assert r["readiness_label"] == "Contact Verification Required"
    assert r["can_generate_draft"] is False
    assert r["has_verified_recipient"] is False
    assert any(x["work_email"] == "contatti@betaing.it" and x["is_documented"] is True and x["is_eligible"] is False for x in r["all_workspace_recipients"])

    # Workspace view check: draft staging is locked/disabled
    resp_detail = client.get(f"/sales/candidates/{cid}")
    assert "Verification Required" in resp_detail.text
    assert "Documented in Lead Register - Verification Required" in resp_detail.text
    assert "Disabled (Verification Required)" in resp_detail.text

    # POST draft check: MUST FAIL CLOSED
    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "recipient_email": "contatti@betaing.it",
            "recipient_name": "Ing. Laura Bianchi",
            "subject": "BIM Partnership",
            "body": "Collaboration proposal.",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in resp_post.headers["location"]


def test_scenario_03_qualified_unverified_contact(env_setup):
    """Scenario 3: Qualified, No Prior Contact, Not Suppressed, Unverified contact only."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s3:unverified"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Gamma Progetti",
        company_domain="gammaprogetti.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_2,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:gamma:1",
            candidate_id=cid,
            full_name="Giuseppe Verdi",
            work_email="g.verdi@gammaprogetti.it",
            email_status="unverified",
        ),
        candidate_id=cid,
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "verification_required"
    assert r["can_generate_draft"] is False

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:gamma:1",
            "recipient_email": "g.verdi@gammaprogetti.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in resp_post.headers["location"]


def test_scenario_04_qualified_no_contacts(env_setup):
    """Scenario 4: Qualified, No Prior Contact, Not Suppressed, No contacts."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s4:nocontact"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Delta Costruzioni",
        company_domain="deltacostruzioni.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_2,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "verification_required"
    assert r["can_generate_draft"] is False
    assert len(r["usable_recipients"]) == 0


def test_scenario_05_qualified_suppressed_domain(env_setup):
    """Scenario 5: Qualified, Domain Suppressed (verified recipient exists)."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s5:suppdom"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Epsilon Spa",
        company_domain="epsilon-suppressed.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:eps:1",
            candidate_id=cid,
            full_name="Elena Neri",
            work_email="elena@epsilon-suppressed.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )
    supp_store.add_suppression(email="info@epsilon-suppressed.it", company_domain="epsilon-suppressed.it", reason="opt_out")

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "suppressed"
    assert r["readiness_label"] == "Suppressed"
    assert r["can_generate_draft"] is False
    assert r["is_suppressed"] is True

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:eps:1",
            "recipient_email": "elena@epsilon-suppressed.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20or%20domain%20is%20suppressed" in resp_post.headers["location"]


def test_scenario_06_qualified_suppressed_recipient_email(env_setup):
    """Scenario 6: Qualified, Recipient Email Suppressed (company domain not suppressed)."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s6:suppemail"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Zeta Engineering",
        company_domain="zetaeng.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:zeta:1",
            candidate_id=cid,
            full_name="Paolo Gialli",
            work_email="paolo@zetaeng.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )
    supp_store.add_suppression(email="paolo@zetaeng.it", company_domain=None, reason="opt_out")

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    # Since recipient is individually suppressed, no usable verified recipient remains
    assert r["effective_readiness"] == "verification_required"
    assert r["can_generate_draft"] is False
    assert r["has_verified_recipient"] is False

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:zeta:1",
            "recipient_email": "paolo@zetaeng.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20or%20domain%20is%20suppressed" in resp_post.headers["location"]


def test_scenario_07_qualified_historical_status_contacted(env_setup):
    """Scenario 7: Qualified, Prior Contact (historical_contact_status='contacted', verified recipient exists)."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s7:histcontacted"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Eta Costruzioni",
        company_domain="etacostruzioni.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
        historical_contact_status="contacted",
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:eta:1",
            candidate_id=cid,
            full_name="Francesca Neri",
            work_email="francesca@etacostruzioni.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "prior_contact"
    assert r["readiness_label"] == "Prior Contact"
    assert r["can_generate_draft"] is False
    assert r["is_previously_contacted"] is True

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:eta:1",
            "recipient_email": "francesca@etacostruzioni.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Candidate%20has%20prior%20outreach%20history" in resp_post.headers["location"]


def test_scenario_08_qualified_historical_events_exist(env_setup):
    """Scenario 8: Qualified, Prior Contact via candidate_historical_outreach table records."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s8:histevent"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Theta Impianti",
        company_domain="thetaimpianti.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:theta:1",
            candidate_id=cid,
            full_name="Luca Marroni",
            work_email="luca@thetaimpianti.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )
    c_store.record_historical_outreach(
        candidate_id=cid,
        source_ref="test_historical_import",
        contact_name="Luca Marroni",
        contact_email="luca@thetaimpianti.it",
        contacted_at="2025-05-10T10:00:00Z",
        channel="email",
        reply_status="replied",
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "prior_contact"
    assert r["can_generate_draft"] is False
    assert r["is_previously_contacted"] is True


def test_scenario_09_qualified_cold_outreach_disallowed(env_setup):
    """Scenario 9: Qualified, cold_outreach_allowed=False."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s9:disallowed"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Iota Sistemi",
        company_domain="iotasistemi.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=False,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:iota:1",
            candidate_id=cid,
            full_name="Davide Rosa",
            work_email="davide@iotasistemi.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "not_eligible"
    assert r["can_generate_draft"] is False
    assert r["base_eligible"] is False

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:iota:1",
            "recipient_email": "davide@iotasistemi.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Candidate%20company%20is%20not%20eligible" in resp_post.headers["location"]


def test_scenario_10_unqualified_rejected_candidate(env_setup):
    """Scenario 10: Unqualified (status='rejected', discovery_status='rejected')."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s10:rejected"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Kappa NonQualificata",
        company_domain="kappanon.it",
        country="Italy",
        discovery_status="rejected",
        qualification_outcome="reject",
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "not_eligible"
    assert r["can_generate_draft"] is False


def test_scenario_11_empty_candidate_id_fails_closed(env_setup):
    """Scenario 11: Empty candidate ID fails closed."""
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cand = CandidateCompany(
        candidate_id="",
        company_name="Test Company SpA",
        company_domain="testco.it",
        country="Italy",
        discovery_status="qualified",
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "not_eligible"
    assert r["can_generate_draft"] is False


def test_scenario_12_cross_candidate_contact_id_rejected(env_setup):
    """Scenario 12: Cross-candidate contact reassignment rejected in POST draft handler."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]

    cid_a = "cand:s12:compA"
    cid_b = "cand:s12:compB"
    cand_a = CandidateCompany(
        candidate_id=cid_a,
        company_name="Comp A",
        company_domain="compa.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    cand_b = CandidateCompany(
        candidate_id=cid_b,
        company_name="Comp B",
        company_domain="compb.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand_a)
    c_store.save_candidate(cand_b)

    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:b:valid",
            candidate_id=cid_b,
            full_name="Bob Brown",
            work_email="bob@compb.it",
            email_status="verified",
        ),
        candidate_id=cid_b,
    )

    resp_post = client.post(
        f"/sales/candidates/{cid_a}/create-manual-draft",
        data={
            "contact_id": "cnt:b:valid",
            "recipient_email": "bob@compb.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Cross-candidate%20contact%20reassignment%20is%20rejected" in resp_post.headers["location"]


def test_scenario_13_arbitrary_unverified_email_in_post_fails_closed(env_setup):
    """Scenario 13: Arbitrary unverified email submitted in POST handler fails closed."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]

    cid = "cand:s13:arbitrary"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Arbitrary Srl",
        company_domain="arbitrary.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "recipient_email": "arbitrary_unverified@arbitrary.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in resp_post.headers["location"]


def test_scenario_14_documented_email_without_verification_in_post_fails_closed(env_setup):
    """Scenario 14: Documented email in Lead Register submitted in POST handler without verification fails closed."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]

    cid = "cand:s14:docpost"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Doc Post Srl",
        company_domain="docpost.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    reg_store.save_record(
        LeadRegisterRecord(
            candidate_id=cid,
            record_number=114,
            company_name="Doc Post Srl",
            contact_email_addresses="doconly@docpost.it",
        )
    )

    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "contact:doconly@docpost.it",
            "recipient_email": "doconly@docpost.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in resp_post.headers["location"]


def test_scenario_15_documented_email_after_operator_verification_succeeds(env_setup):
    """Scenario 15: Documented email verified via verify-contact route succeeds in draft creation."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]

    cid = "cand:s15:docverify"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Doc Verify Srl",
        company_domain="docverify.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    reg_store.save_record(
        LeadRegisterRecord(
            candidate_id=cid,
            record_number=115,
            company_name="Doc Verify Srl",
            contact_email_addresses="ceo@docverify.it",
        )
    )

    # 1. Verify contact via verify-contact
    resp_v = client.post(
        f"/sales/candidates/{cid}/verify-contact",
        data={
            "contact_id": "contact:ceo@docverify.it",
            "evidence_source": "Company Website Impressum",
            "evidence_reference": "https://docverify.it/legal",
            "notes": "Verified by operator",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_v.status_code == 303

    # 2. Post draft succeeds
    resp_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "contact:ceo@docverify.it",
            "recipient_email": "ceo@docverify.it",
            "recipient_name": "CEO",
            "subject": "BIM Implementation",
            "body": "Proposal text",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_post.status_code == 303
    assert "/sales/drafts/draft:manual:" in resp_post.headers["location"]


def test_scenario_16_multiple_contacts_one_verified_one_unverified(env_setup):
    """Scenario 16: Multiple contacts where only one is verified."""
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]
    supp_store = env_setup["supp_store"]

    cid = "cand:s16:mixed"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Mixed Contacts Spa",
        company_domain="mixedspa.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:mixed:1",
            candidate_id=cid,
            full_name="Verified Contact",
            work_email="verified@mixedspa.it",
            email_status="verified",
        ),
        candidate_id=cid,
    )
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:mixed:2",
            candidate_id=cid,
            full_name="Unverified Contact",
            work_email="unverified@mixedspa.it",
            email_status="unverified",
        ),
        candidate_id=cid,
    )

    r = evaluate_candidate_readiness(cand, c_store, supp_store, reg_store)
    assert r["effective_readiness"] == "draft_ready"
    assert r["can_generate_draft"] is True
    assert len(r["usable_recipients"]) == 1
    assert r["usable_recipients"][0]["work_email"] == "verified@mixedspa.it"

    # Workspace view check
    resp_detail = client.get(f"/sales/candidates/{cid}")
    assert resp_detail.status_code == 200
    assert "[Verified]" in resp_detail.text
    assert "[unverified - Verification Required]" in resp_detail.text

    # Drafting with unverified contact fails
    resp_fail = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:mixed:2",
            "recipient_email": "unverified@mixedspa.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_fail.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in resp_fail.headers["location"]

    # Drafting with verified contact succeeds
    resp_ok = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "contact_id": "cnt:mixed:1",
            "recipient_email": "verified@mixedspa.it",
            "subject": "Intro",
            "body": "Draft body",
            "csrf_token": env_setup["csrf_token"],
        },
        headers=env_setup["headers"],
        follow_redirects=False,
    )
    assert resp_ok.status_code == 303
    assert "/sales/drafts/draft:manual:" in resp_ok.headers["location"]


def test_scenario_17_cross_page_readiness_consistency(env_setup):
    """
    Scenario 17: Cross-page consistency.
    Verify identical readiness evaluation between /sales/leads and /sales/candidates/{cid}.
    """
    client = env_setup["client"]
    c_store = env_setup["cand_store"]
    reg_store = env_setup["reg_store"]

    cid_ready = "cand:s17:ready"
    cand_ready = CandidateCompany(
        candidate_id=cid_ready,
        company_name="Sync Ready Spa",
        company_domain="syncready.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_1,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand_ready)
    c_store.save_contact(
        ContactCandidate(
            contact_id="cnt:sync:1",
            candidate_id=cid_ready,
            full_name="Gianni Sync",
            work_email="gianni@syncready.it",
            email_status="verified",
        ),
        candidate_id=cid_ready,
    )

    cid_req = "cand:s17:required"
    cand_req = CandidateCompany(
        candidate_id=cid_req,
        company_name="Sync Required Spa",
        company_domain="syncreq.it",
        country="Italy",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        company_tier=CompanyTier.tier_2,
        cold_outreach_allowed=True,
    )
    c_store.save_candidate(cand_req)

    # 1. Inspect /sales/leads
    resp_leads = client.get("/sales/leads")
    assert resp_leads.status_code == 200
    assert "Sync Ready Spa" in resp_leads.text
    assert "Sync Required Spa" in resp_leads.text

    # Sync Ready Spa shows Eligible for Manual Draft in leads table
    assert "Eligible for Manual Draft" in resp_leads.text
    # Sync Required Spa shows Contact Verification Required in leads table
    assert "Contact Verification Required" in resp_leads.text

    # 2. Inspect /sales/candidates/{cid_ready}
    resp_r = client.get(f"/sales/candidates/{cid_ready}")
    assert resp_r.status_code == 200
    assert "Draft Eligible" in resp_r.text
    assert "ELIGIBLE FOR MANUAL DRAFT" in resp_r.text
    assert "Stage Manual Draft to Review Queue" in resp_r.text

    # 3. Inspect /sales/candidates/{cid_req}
    resp_req = client.get(f"/sales/candidates/{cid_req}")
    assert resp_req.status_code == 200
    assert "Verification Required" in resp_req.text
    assert "CONTACT VERIFICATION REQUIRED" in resp_req.text
    assert "Manual Outreach Staging (Locked)" in resp_req.text
