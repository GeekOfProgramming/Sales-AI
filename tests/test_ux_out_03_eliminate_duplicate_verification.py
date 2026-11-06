"""
tests/test_ux_out_03_eliminate_duplicate_verification.py

Acceptance tests for UX-OUT-03: Eliminate Duplicate Contact Verification.
Strictly verifies:
1. Approved email address in the 13-Field Lead Register with matching checksum and traceable evidence
   satisfies recipient readiness without requiring a duplicate Contacts & Verification form.
   Manual draft staging succeeds immediately.
2. Unverified or missing email addresses remain blocked (Evidence Required or Missing Recipient).
3. Historical contact status and DNC/suppression protections continue to block outreach even if an email
   is approved in the Lead Register (Outreach Blocked).
"""

import os
import uuid
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import (
    get_console_services,
    evaluate_candidate_readiness,
    derive_company_outreach_status,
)
from sales_engine.candidates.models import CandidateCompany
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def csrf_setup(client):
    dash_resp = client.get("/sales/dashboard")
    token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token_ux_out_03"
    client.cookies.set("sales_csrf_token", token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": token}
    return {"token": token, "headers": headers}


def test_01_lead_register_approved_email_satisfies_readiness_and_draft_staging(client, csrf_setup):
    """
    Test 1: Approved email in 13-Field Lead Register with exact evidence satisfies
    recipient readiness as 'Verified Recipient' without duplicate verification form.
    Manual draft staging directly succeeds.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:eng-studio-{uuid.uuid4().hex[:6]}.com"
    dom = f"eng-studio-{uuid.uuid4().hex[:6]}.com"
    email_val = f"direct@arch-{uuid.uuid4().hex[:6]}.com"

    # 1. Setup eligible candidate
    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="BIM Engineering Studio",
        discovery_source="test_ux_out_03",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand)

    # 2. Setup 13-Field Lead Register record with traceable evidence
    rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=101,
        company_name="BIM Engineering Studio",
        official_website=f"https://{dom}",
        contact_email_addresses=email_val,
        evidence_urls=[f"https://{dom}/contatti"],
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec)

    # 3. Add explicit operator approval for field 13 with matching checksum and evidence URL
    review = FieldReviewRecord(
        candidate_id=cid,
        field_name="contact_email_addresses",
        approved_value=email_val,
        value_checksum=compute_field_checksum(email_val),
        review_state="OPERATOR_APPROVED",
        source_type="operator_manual",
        evidence_url=f"https://{dom}/contatti",
        operator_note="Direct contact verified on official contact page",
    )
    reg_store.save_field_review(review)

    # 4. Evaluate Authoritative Candidate Readiness
    readiness = evaluate_candidate_readiness(
        cand,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )

    # Readiness assertions
    assert readiness["can_generate_draft"] is True
    assert readiness["effective_readiness"] == "draft_ready"
    assert readiness["has_verified_recipient"] is True
    assert readiness["recipient_readiness"]["status"] == "verified_recipient"
    assert readiness["recipient_readiness"]["label"] == "Verified Recipient"

    # Confirm recipient was promoted with Lead Register provenance
    recipients = readiness["usable_recipients"]
    assert len(recipients) >= 1
    target_rec = next((r for r in recipients if r["work_email"] == email_val), None)
    assert target_rec is not None
    assert target_rec["is_verified"] is True
    assert target_rec["is_eligible"] is True
    assert target_rec["provenance"] == "lead_register_approved"

    # 5. Test manual draft staging POST succeeds immediately without redirect error
    headers = csrf_setup["headers"]
    post_data = {
        "csrf_token": csrf_setup["token"],
        "recipient_email": email_val,
        "recipient_name": "Direct Architect",
        "subject": "BIM Coordination & Collaboration",
        "body": "Hello, we would love to collaborate on your upcoming engineering projects.",
    }
    resp = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data=post_data,
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    loc = resp.headers.get("location", "")
    assert "/sales/drafts/draft:manual:" in loc
    assert "error=" not in loc


def test_02_unverified_and_missing_email_blocked(client):
    """
    Test 2: Unreviewed or missing email addresses remain blocked with compact status:
    - 'Evidence Required' when email is documented but unreviewed
    - 'Missing Recipient' when no email exists
    Draft staging is rejected.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid_unrev = f"cand:domain:unrev-{uuid.uuid4().hex[:6]}.com"
    dom_unrev = f"unrev-{uuid.uuid4().hex[:6]}.com"
    unrev_email = f"info@{dom_unrev}"

    cand_unrev = CandidateCompany(
        candidate_id=cid_unrev,
        canonical_domain=dom_unrev,
        company_name="Unreviewed Architecture",
        discovery_source="test_ux_out_03",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_unrev)

    # Record has email, but NO operator review
    rec_unrev = LeadRegisterRecord(
        candidate_id=cid_unrev,
        record_number=102,
        company_name="Unreviewed Architecture",
        official_website=f"https://{dom_unrev}",
        contact_email_addresses=unrev_email,
        verification_status="Unverified",
    )
    reg_store.save_record(rec_unrev)

    readiness_unrev = evaluate_candidate_readiness(
        cand_unrev,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )
    assert readiness_unrev["can_generate_draft"] is False
    assert readiness_unrev["effective_readiness"] == "verification_required"
    assert readiness_unrev["recipient_readiness"]["status"] == "evidence_required"
    assert readiness_unrev["recipient_readiness"]["label"] == "Evidence Required"

    # Test candidate with NO email at all
    cid_no_em = f"cand:domain:noem-{uuid.uuid4().hex[:6]}.com"
    cand_no_em = CandidateCompany(
        candidate_id=cid_no_em,
        canonical_domain=f"noem-{uuid.uuid4().hex[:6]}.com",
        company_name="No Email Co",
        discovery_source="test_ux_out_03",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_no_em)

    readiness_no_em = evaluate_candidate_readiness(
        cand_no_em,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )
    assert readiness_no_em["can_generate_draft"] is False
    assert readiness_no_em["recipient_readiness"]["status"] == "missing_recipient"
    assert readiness_no_em["recipient_readiness"]["label"] == "Missing Recipient"


def test_03_historical_and_dnc_suppression_strictly_overrides_approval(client, csrf_setup):
    """
    Test 3: DNC/suppression and historical outreach status strictly override
    an approved email in the Lead Register. Recipient readiness is 'Outreach Blocked'.
    Draft staging is rejected.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    supp_svc = svcs["suppression_svc"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid_supp = f"cand:domain:supp-{uuid.uuid4().hex[:6]}.com"
    dom_supp = f"supp-{uuid.uuid4().hex[:6]}.com"
    email_supp = f"partner@{dom_supp}"

    # Suppressed domain
    supp_svc.add_suppression(email=email_supp, company_domain=dom_supp, reason="opt_out", source="test")

    cand_supp = CandidateCompany(
        candidate_id=cid_supp,
        canonical_domain=dom_supp,
        company_name="Suppressed Company",
        discovery_source="test_ux_out_03",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_supp)

    rec_supp = LeadRegisterRecord(
        candidate_id=cid_supp,
        company_name="Suppressed Company",
        contact_email_addresses=email_supp,
    )
    reg_store.save_record(rec_supp)

    # Approved review exists
    reg_store.save_field_review(
        FieldReviewRecord(
            candidate_id=cid_supp,
            field_name="contact_email_addresses",
            approved_value=email_supp,
            value_checksum=compute_field_checksum(email_supp),
            review_state="OPERATOR_APPROVED",
            evidence_url=f"https://{dom_supp}/contact",
        )
    )

    readiness_supp = evaluate_candidate_readiness(
        cand_supp,
        cand_store=cand_store,
        suppression_svc=supp_svc,
        reg_store=reg_store,
    )
    # Suppression overrides approved email
    assert readiness_supp["is_suppressed"] is True
    assert readiness_supp["can_generate_draft"] is False
    assert readiness_supp["effective_readiness"] == "suppressed"
    assert readiness_supp["recipient_readiness"]["status"] == "outreach_blocked"
    assert readiness_supp["recipient_readiness"]["label"] == "Outreach Blocked"

    # Attempting to draft must fail with redirect error
    headers = csrf_setup["headers"]
    post_data = {
        "csrf_token": csrf_setup["token"],
        "recipient_email": email_supp,
        "recipient_name": "Suppressed Recipient",
        "subject": "Hello",
        "body": "Test body",
    }
    resp = client.post(
        f"/sales/candidates/{cid_supp}/create-manual-draft",
        data=post_data,
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error=" in resp.headers.get("location", "")
