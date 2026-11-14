"""
tests/test_flow_02_approval_to_manual_sent.py

Acceptance tests for FLOW-02: Simplified Approval-to-Manual-Sent Workflow.
Strictly verifies:
1. Operator Suitability Approval without research finalization cannot place a candidate into Ready to Email.
2. Finalized Research + Operator Approval + Verified Recipient promotes to Ready to Email without requiring a draft.
3. Historical Contact and DNC/Suppression prevent entering Ready to Email.
4. Operator Manual Send via Thunderbird:
   - Validates authorized pyBIM sender accounts.
   - Requires explicit confirmation checkbox.
   - Records Europe/Rome timestamp, Thunderbird client tag, and audit metadata.
   - Transitions candidate atomically to Sent Manually and Previously Contacted.
   - Enforces idempotency / double-click protection.
5. Audited Void Correction:
   - Requires operator rationale.
   - Marks record as voided without deleting history.
   - Restores candidate cold outreach eligibility when no other active outreach exists.
6. Register.it IMAP compatibility fields exist in database schema.
"""

import os
import uuid
import urllib.parse
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.schemas import ContactCandidate
from backend.sales_ui.router import (
    get_console_services,
    evaluate_candidate_readiness,
    AUTHORIZED_MANUAL_SENDER_ACCOUNTS,
    get_europe_rome_now,
)
from sales_engine.candidates.models import CandidateCompany
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def csrf_setup(client):
    dash_resp = client.get("/sales/dashboard")
    token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token_flow_02"
    client.cookies.set("sales_csrf_token", token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": token}
    return {"token": token, "headers": headers}


def test_01_unresearched_candidate_approved_suitability_cannot_become_ready_to_email(client, csrf_setup):
    """
    1. Unresearched Candidate:
       An operator approving suitability on an unresearched candidate must NOT place them
       into Ready to Email ('draft_ready'). The candidate requires research finalization first.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:flow02-unresearched-{uuid.uuid4().hex[:6]}.com"
    dom = f"flow02-unresearched-{uuid.uuid4().hex[:6]}.com"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Unresearched BIM Studio",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        research_status="unresearched",
        manual_review_decision="APPROVED",  # Operator clicked Approve for Outreach
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id=f"cnt:{uuid.uuid4().hex[:6]}",
        candidate_id=cid,
        full_name="Andrea Rossi",
        work_email=f"andrea@{dom}",
        job_title="Director",
        email_status="verified",
        email_confidence=95,
    )
    cand_store.save_contact(contact, candidate_id=cid)

    # Evaluate readiness
    eval_result = evaluate_candidate_readiness(
        cand=cand,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )

    # Must NOT be draft_ready / Ready to Email
    assert eval_result["effective_readiness"] != "draft_ready"
    assert eval_result["can_generate_draft"] is False
    assert eval_result["effective_readiness"] in ("needs_research", "review_required", "needs_approval")


def test_02_finalized_research_with_explicit_approval_becomes_ready_to_email_without_draft(client, csrf_setup):
    """
    2. Authoritative Ready to Email:
       When research is finalized (OPERATOR_REVIEWED in register) + suitability is APPROVED + verified recipient,
       the candidate becomes Ready to Email (draft_ready) WITHOUT requiring any StoredDraft or SMTP configuration.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:flow02-ready-{uuid.uuid4().hex[:6]}.com"
    dom = f"flow02-ready-{uuid.uuid4().hex[:6]}.com"
    recip_email = f"director@{dom}"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Ready to Email Engineering",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        research_status="finalized",
        manual_review_decision="APPROVED",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id=f"cnt:{uuid.uuid4().hex[:6]}",
        candidate_id=cid,
        full_name="Elena Ferrari",
        work_email=recip_email,
        job_title="Managing Partner",
        email_status="verified",
        email_confidence=98,
    )
    cand_store.save_contact(contact, candidate_id=cid)

    # Create finalized research record in register
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Ready to Email Engineering",
        official_website=f"https://{dom}",
        contact_email_addresses=recip_email,
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec)

    # Evaluate readiness without ANY drafts created
    eval_result = evaluate_candidate_readiness(
        cand=cand,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )

    assert eval_result["effective_readiness"] == "draft_ready"
    assert eval_result["readiness_label"] == "Ready to Email"
    assert eval_result["can_generate_draft"] is True
    assert eval_result["verified_recipient_email"] == recip_email

    # Check /sales/leads view includes candidate in ready_to_email queue
    leads_resp = client.get("/sales/leads?queue=ready_to_email")
    assert leads_resp.status_code == 200
    assert cid in leads_resp.text
    assert "Ready to Email" in leads_resp.text
    assert "Mark as Sent" in leads_resp.text


def test_03_restrictions_prevent_ready_to_email(client, csrf_setup):
    """
    3. Restrictions:
       Candidate with prior outreach or DNC/Suppression CANNOT enter Ready to Email,
       even with finalized research and approved suitability.
    """
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    reg_store = LeadRegisterStore(db_path=cand_store.db_path)

    cid = f"cand:domain:flow02-restricted-{uuid.uuid4().hex[:6]}.com"
    dom = f"flow02-restricted-{uuid.uuid4().hex[:6]}.com"
    recip_email = f"info@{dom}"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Restricted BIM Spa",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        research_status="finalized",
        manual_review_decision="APPROVED",
        cold_outreach_allowed=False,
        historical_contact_status="contacted",
        previous_outreach_detected=True,  # Restricted by previous outreach
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id=f"cnt:{uuid.uuid4().hex[:6]}",
        candidate_id=cid,
        full_name="Giorgio Bianchi",
        work_email=recip_email,
        job_title="CEO",
        email_status="verified",
        email_confidence=90,
    )
    cand_store.save_contact(contact, candidate_id=cid)

    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="Restricted BIM Spa",
        official_website=f"https://{dom}",
        contact_email_addresses=recip_email,
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(rec)

    eval_result = evaluate_candidate_readiness(
        cand=cand,
        cand_store=cand_store,
        suppression_svc=svcs["suppression_svc"],
        reg_store=reg_store,
    )

    assert eval_result["effective_readiness"] in ("prior_contact", "suppressed", "not_eligible")
    assert eval_result["can_generate_draft"] is False


def test_04_mark_as_sent_thunderbird_execution_and_idempotency(client, csrf_setup):
    """
    4. Thunderbird Manual Mark as Sent:
       - Rejects unauthorized sender account.
       - Rejects missing Thunderbird confirmation checkbox.
       - Records valid manual send with Europe/Rome timestamp.
       - Atomically transitions candidate to contacted and cold_outreach_allowed=False.
       - Protects against duplicate / double-click submission.
    """
    csrf_token = csrf_setup["token"]
    headers = csrf_setup["headers"]
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]

    cid = f"cand:domain:flow02-tb-{uuid.uuid4().hex[:6]}.com"
    dom = f"flow02-tb-{uuid.uuid4().hex[:6]}.com"
    recip_email = f"claudio@{dom}"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Thunderbird Tested Architetti",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        research_status="finalized",
        manual_review_decision="APPROVED",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id=f"cnt:{uuid.uuid4().hex[:6]}",
        candidate_id=cid,
        full_name="Claudio Neri",
        work_email=recip_email,
        job_title="Lead Architect",
        email_status="verified",
        email_confidence=95,
    )
    cand_store.save_contact(contact, candidate_id=cid)

    rome_time = get_europe_rome_now().strftime("%Y-%m-%d %H:%M:%S")

    # 4A. Reject unauthorized sender account
    resp_bad_sender = client.post(
        f"/sales/candidates/{cid}/mark-sent",
        data={
            "sender_account": "unauthorized@external.com",
            "recipient_email": recip_email,
            "sent_at": rome_time,
            "confirm_thunderbird_sent": "1",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_bad_sender.status_code == 303
    loc = urllib.parse.unquote(resp_bad_sender.headers["location"])
    assert "error=" in loc
    assert "authorized" in loc.lower() or "invalid sender" in loc.lower()

    # 4B. Reject missing confirmation checkbox
    resp_no_check = client.post(
        f"/sales/candidates/{cid}/mark-sent",
        data={
            "sender_account": "info@pybim.it",
            "recipient_email": recip_email,
            "sent_at": rome_time,
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_no_check.status_code == 303
    loc_check = urllib.parse.unquote(resp_no_check.headers["location"])
    assert "error=" in loc_check
    assert "confirmation" in loc_check.lower() or "thunderbird" in loc_check.lower()

    # 4C. Valid operator manual send
    idemp_token = f"test_idemp_{cid}_1"
    resp_valid = client.post(
        f"/sales/candidates/{cid}/mark-sent",
        data={
            "sender_account": "info@pybim.it",
            "recipient_email": recip_email,
            "sent_at": rome_time,
            "subject": "Proposta di Collaborazione BIM",
            "operator_notes": "Sent via Thunderbird by operator; personalized introductory portfolio attached.",
            "confirm_thunderbird_sent": "1",
            "idempotency_token": idemp_token,
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_valid.status_code == 303

    # Check ReviewStore record
    logs = rev_store.list_manual_outreach_logs(candidate_id=cid)
    assert len(logs) == 1
    log = logs[0]
    assert log["sender_account"] == "info@pybim.it"
    assert log["recipient_email"] == recip_email
    assert log["sent_at"] == rome_time
    assert log["client_app"] == "thunderbird"
    assert log["source"] in ("manual_thunderbird", "operator_manual_confirmation")
    assert log["is_voided"] == 0
    assert log["verification_status"] in ("operator_confirmed", "manually_confirmed")

    # Check CandidateStore candidate updated
    updated_cand = cand_store.get_candidate(cid)
    assert updated_cand.previous_outreach_detected is True
    assert updated_cand.cold_outreach_allowed is False
    assert updated_cand.historical_contact_status == "contacted"

    # Check historical outreach in cand_store
    hist_outreach = cand_store.get_historical_outreach_for_candidate(cid)
    assert len(hist_outreach) >= 1
    assert any("thunderbird" in h.get("source_ref", "").lower() for h in hist_outreach)

    # 4D. Idempotency test: duplicate submission does NOT create a duplicate log
    resp_dup = client.post(
        f"/sales/candidates/{cid}/mark-sent",
        data={
            "sender_account": "info@pybim.it",
            "recipient_email": recip_email,
            "sent_at": rome_time,
            "subject": "Proposta di Collaborazione BIM",
            "confirm_thunderbird_sent": "1",
            "idempotency_token": idemp_token,
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_dup.status_code == 303
    logs_after_dup = rev_store.list_manual_outreach_logs(candidate_id=cid)
    assert len(logs_after_dup) == 1  # Still exactly 1!


def test_05_audited_void_correction(client, csrf_setup):
    """
    5. Audited Void:
       - Rejects empty void reason.
       - Marks log as voided (is_voided=1, verification_status='voided', voided_reason recorded).
       - Never deletes row from database.
       - Restores candidate cold outreach eligibility if no other active outreach exists.
    """
    csrf_token = csrf_setup["token"]
    headers = csrf_setup["headers"]
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]

    cid = f"cand:domain:flow02-void-{uuid.uuid4().hex[:6]}.com"
    dom = f"flow02-void-{uuid.uuid4().hex[:6]}.com"
    recip_email = f"stefano@{dom}"

    cand = CandidateCompany(
        candidate_id=cid,
        canonical_domain=dom,
        company_name="Void Testing Studio",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        research_status="finalized",
        manual_review_decision="APPROVED",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    cand_store.save_candidate(cand)

    # Mark as sent
    rome_time = get_europe_rome_now().strftime("%Y-%m-%d %H:%M:%S")
    log = rev_store.log_operator_manual_sent(
        candidate_id=cid,
        sender_account="careers@pybim.com",
        recipient_email=recip_email,
        sent_at=rome_time,
        subject="Studio inquiry",
        operator_notes="Accidental click by operator",
    )
    log_id = log["log_id"]

    cand.previous_outreach_detected = True
    cand.cold_outreach_allowed = False
    cand.historical_contact_status = "contacted"
    cand_store.save_candidate(cand)

    # 5A. Reject empty void reason
    resp_empty_reason = client.post(
        f"/sales/manual-sent/{log_id}/void",
        data={
            "void_reason": "   ",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    # Redirects with error message or rejects
    assert resp_empty_reason.status_code == 303
    assert "error=" in resp_empty_reason.headers["location"]

    # Verify log is NOT voided yet
    raw_log = rev_store.get_manual_outreach_log(log_id)
    assert raw_log["is_voided"] == 0

    # 5B. Valid audited void
    resp_void = client.post(
        f"/sales/manual-sent/{log_id}/void",
        data={
            "void_reason": "Sent to incorrect contact; email was rejected by recipient server.",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert resp_void.status_code == 303

    # Verify log is marked voided and audited metadata recorded
    voided_log = rev_store.get_manual_outreach_log(log_id)
    assert voided_log is not None
    assert voided_log["is_voided"] == 1
    assert voided_log["verification_status"] == "voided"
    assert voided_log["voided_reason"] == "Sent to incorrect contact; email was rejected by recipient server."
    assert voided_log["voided_at"] is not None

    # Verify candidate restored
    restored_cand = cand_store.get_candidate(cid)
    assert restored_cand.cold_outreach_allowed is True
    assert restored_cand.previous_outreach_detected is False
    assert restored_cand.historical_contact_status == "uncontacted"


def test_06_register_it_imap_fields_present_in_schema():
    """
    6. Register.it IMAP Compatibility:
       Verify all required IMAP reconciliation columns exist in the manual_outreach_log table.
    """
    svcs = get_console_services()
    rev_store: ReviewStore = svcs["rev_store"]

    with rev_store._get_connection() as conn:
        cur = conn.execute("PRAGMA table_info(manual_outreach_log)")
        cols = {r["name"] for r in cur.fetchall()}

    expected_cols = {
        "sender_account",
        "sent_at",
        "source",
        "verification_status",
        "client_app",
        "is_voided",
        "voided_reason",
        "voided_at",
        "voided_by",
        "imap_reconciliation_status",
        "imap_message_id",
        "imap_uid",
        "imap_folder",
        "imap_uidvalidity",
        "imap_matched_at",
        "conversation_id",
        "actual_subject",
        "actual_body",
    }
    for col in expected_cols:
        assert col in cols, f"Expected column {col} missing from manual_outreach_log schema"
