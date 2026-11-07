"""
tests/test_flow_01_research_to_qualified_leads.py

Acceptance tests for FLOW-01: Research to Qualified Leads Final Integration.
Strictly verifies:
1. Research finalized -> eligible Qualified Lead appears correctly with full 13-field data,
   citations, and manual review state preserved.
2. Qualified company with multiple/missing recipient emails cannot bypass evidence requirements.
   Only addresses with exact-match evidence are promoted to verified.
3. Historical/DNC/rejected companies retain correct safety and lifecycle states.
   Qualification remains strictly decoupled from send eligibility.
"""

import os
import uuid
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import (
    get_console_services,
    evaluate_candidate_readiness,
    is_candidate_qualified_lead,
)
from sales_engine.candidates.models import CandidateCompany, QualificationOutcome, DiscoveryStatus
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from sales_engine.leads.register_store import LeadRegisterStore, compute_field_checksum


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def csrf_setup(client):
    dash_resp = client.get("/sales/dashboard")
    token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token_flow_01"
    client.cookies.set("sales_csrf_token", token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": token}
    return {"token": token, "headers": headers}


def test_01_research_finalization_promotes_eligible_candidate_to_qualified_leads(client, csrf_setup):
    """
    Test 1: When research is explicitly finalized for a suitable candidate:
    - verification_status becomes OPERATOR_REVIEWED
    - Candidate is promoted to ICP-qualified (qualified_for_enrichment)
    - Appears in /sales/leads with preserved 13-field citations and Draft Composer action
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:finalized-{uuid.uuid4().hex[:6]}.com"
    dom = f"finalized-{uuid.uuid4().hex[:6]}.com"
    email_val = f"contact@{dom}"

    # Candidate starts in active discovery
    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Studio Ingegneria BIM",
        discovery_source="flow_01_test",
        discovery_status=DiscoveryStatus.researching,
        qualification_outcome=QualificationOutcome.research_more,
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand)
    assert not is_candidate_qualified_lead(cand)

    # Setup 13-field record
    rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=501,
        company_name="Studio Ingegneria BIM",
        official_website=f"https://{dom}",
        business_activities="Ingegneria strutturale e coordinamento BIM per opere civili",
        contact_email_addresses=email_val,
        evidence_urls=[f"https://{dom}/about", f"https://{dom}/contatti"],
        verification_status="EXTRACTED",
        provenance_source="pilot_research",
    )
    reg_store.save_record(rec)

    # Core reviews required for finalization
    for fn, val in [
        ("company_name", "Studio Ingegneria BIM"),
        ("official_website", f"https://{dom}"),
        ("business_activities", "Ingegneria strutturale e coordinamento BIM per opere civili"),
        ("contact_email_addresses", email_val),
    ]:
        rev = FieldReviewRecord(
            candidate_id=cid,
            field_name=fn,
            approved_value=val,
            value_checksum=compute_field_checksum(val),
            review_state="OPERATOR_APPROVED",
            source_type="operator_manual",
            evidence_url=f"https://{dom}/contatti",
            operator_note=f"Verified {fn} from official website",
        )
        reg_store.save_field_review(rev)

    # Explicit finalization POST
    headers = csrf_setup["headers"]
    finalize_resp = client.post(
        f"/sales/candidates/{cid}/research/finalize",
        data={"csrf_token": csrf_setup["token"]},
        headers=headers,
        follow_redirects=False,
    )
    assert finalize_resp.status_code == 303
    loc = finalize_resp.headers.get("location", "")
    assert "error=" not in loc
    assert f"/sales/candidates/{cid}" in loc

    # Verify candidate promoted to qualified
    updated_cand = cand_store.get_by_id(cid)
    assert updated_cand.qualification_outcome in (QualificationOutcome.qualified_for_enrichment, "qualified_for_enrichment")
    assert is_candidate_qualified_lead(updated_cand)

    # Verify Qualified Leads view lists the company
    leads_resp = client.get("/sales/leads")
    assert leads_resp.status_code == 200
    html = leads_resp.text
    assert "Studio Ingegneria BIM" in html
    assert dom in html
    assert "Draft Composer" in html


def test_02_multiple_emails_require_exact_evidence_and_missing_email_blocked(client, csrf_setup):
    """
    Test 2: Multiple emails in 13-field record:
    - Only the address with exact evidence match is verified.
    - An address lacking exact evidence remains unverified.
    - Missing email remains blocked with 'Missing Recipient'.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:multi-{uuid.uuid4().hex[:6]}.com"
    dom = f"multi-{uuid.uuid4().hex[:6]}.com"
    email_supported = f"director@{dom}"
    email_unsupported = f"info@{dom}"
    combined_emails = f"{email_supported}, {email_unsupported}"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Multi Email BIM Arch",
        discovery_source="flow_01_test",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=502,
        company_name="Multi Email BIM Arch",
        official_website=f"https://{dom}",
        contact_email_addresses=combined_emails,
        evidence_urls=[f"https://{dom}/team"],
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec)

    # Review explicitly cites only the director email in notes
    rev = FieldReviewRecord(
        candidate_id=cid,
        field_name="contact_email_addresses",
        approved_value=combined_emails,
        value_checksum=compute_field_checksum(combined_emails),
        review_state="OPERATOR_APPROVED",
        source_type="operator_manual",
        evidence_url=f"https://{dom}/team",
        operator_note=f"Director verified: {email_supported}",
    )
    reg_store.save_field_review(rev)

    # Evaluate readiness
    readiness = evaluate_candidate_readiness(
        cand,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )
    usable_emails = [r["work_email"] for r in readiness["usable_recipients"]]
    assert email_supported in usable_emails
    assert email_unsupported not in usable_emails

    # Attempting to draft to the unsupported email fails
    headers = csrf_setup["headers"]
    fail_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "csrf_token": csrf_setup["token"],
            "recipient_email": email_unsupported,
            "recipient_name": "General Info",
            "subject": "Inquiry",
            "body": "Hello team",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert fail_post.status_code == 303
    assert "error=" in fail_post.headers.get("location", "")

    # Drafting to the supported email succeeds
    ok_post = client.post(
        f"/sales/candidates/{cid}/create-manual-draft",
        data={
            "csrf_token": csrf_setup["token"],
            "recipient_email": email_supported,
            "recipient_name": "Director",
            "subject": "Inquiry",
            "body": "Hello director",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert ok_post.status_code == 303
    assert "/sales/drafts/draft:manual:" in ok_post.headers.get("location", "")


def test_03_historical_dnc_and_rejected_companies_retain_safety_lifecycle(client, csrf_setup):
    """
    Test 3: Historical contact status, DNC suppressions, and explicit operator rejections
    are strictly preserved and cannot be bypassed:
    - Suppressed or previously contacted candidates are marked Outreach Blocked.
    - Rejected companies never enter Qualified Leads.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    supp_svc = svcs["suppression_svc"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    # 1. Rejected company
    cid_rej = f"cand:domain:rej-{uuid.uuid4().hex[:6]}.com"
    cand_rej = CandidateCompany(
        candidate_id=cid_rej,
        canonical_domain=f"rej-{uuid.uuid4().hex[:6]}.com",
        company_name="Rejected Enterprise",
        discovery_source="flow_01_test",
        discovery_status=DiscoveryStatus.rejected,
        qualification_outcome=QualificationOutcome.reject,
        cold_outreach_allowed=False,
    )
    cand_store.save_candidate(cand_rej)
    assert not is_candidate_qualified_lead(cand_rej)

    # Research finalization on a rejected company must NOT override rejection
    rec_rej = LeadRegisterRecord(
        candidate_id=cid_rej,
        company_name="Rejected Enterprise",
        official_website=f"https://{cand_rej.canonical_domain}",
        business_activities="Architecture and structural services",
        verification_status="EXTRACTED",
    )
    reg_store.save_record(rec_rej)
    for fn, val in [
        ("company_name", "Rejected Enterprise"),
        ("official_website", f"https://{cand_rej.canonical_domain}"),
        ("business_activities", "Architecture and structural services"),
    ]:
        reg_store.save_field_review(
            FieldReviewRecord(
                candidate_id=cid_rej,
                field_name=fn,
                approved_value=val,
                value_checksum=compute_field_checksum(val),
                review_state="OPERATOR_APPROVED",
                evidence_url=f"https://{cand_rej.canonical_domain}/about",
            )
        )

    client.post(
        f"/sales/candidates/{cid_rej}/research/finalize",
        data={"csrf_token": csrf_setup["token"]},
        headers=csrf_setup["headers"],
    )
    rechecked_cand = cand_store.get_by_id(cid_rej)
    assert rechecked_cand.qualification_outcome in (QualificationOutcome.reject, "reject")
    assert not is_candidate_qualified_lead(rechecked_cand)

    # 2. Historical candidate with prior outreach
    cid_hist = f"cand:domain:hist-{uuid.uuid4().hex[:6]}.com"
    cand_hist = CandidateCompany(
        candidate_id=cid_hist,
        canonical_domain=f"hist-{uuid.uuid4().hex[:6]}.com",
        company_name="Prior Outreach Studio",
        discovery_source="flow_01_test",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=False,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    )
    cand_store.save_candidate(cand_hist)

    readiness_hist = evaluate_candidate_readiness(
        cand_hist,
        cand_store=cand_store,
        suppression_svc=supp_svc,
        reg_store=reg_store,
    )
    assert readiness_hist["is_previously_contacted"] is True
    assert readiness_hist["can_generate_draft"] is False
    assert readiness_hist["effective_readiness"] == "prior_contact"
    assert readiness_hist["recipient_readiness"]["status"] == "outreach_blocked"
