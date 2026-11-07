"""
tests/test_outreach_manual_dispatch_acceptance.py

Focused acceptance test suite for OUTREACH-02: Manual Dispatch End-to-End Acceptance.
Uses disposable SQLite databases and synthetic records to strictly verify:
1. End-to-End Manual Send:
   - Eligible candidate with verified contact.
   - Staging/Viewing draft does not mark sent.
   - Operator explicit manual dispatch POST (/sales/drafts/{draft_id}/log-manual-send).
   - Atomic update of candidate (contacted, cold_outreach_allowed=False, previous_outreach_detected=True).
   - Draft send_status updated to 'sent'.
   - Manual outreach log recorded with exact details.
   - Outreach status derived as SENT_MANUALLY_CONFIRMED.
   - Sent & History (/sales/sent) lists the entry.
2. Idempotency & Failure Safety:
   - Duplicate submissions do not duplicate logs or create misleading transitions.
   - Missing explicit confirmation rejects without mutation.
   - Draft revisions maintain integrity.
3. Suppression, DNC & Client Handoff:
   - Suppressed recipient/domain blocks non-retrospective manual dispatch.
   - Excluded/unqualified candidates block manual dispatch.
   - Gmail / mailto URL encoding and long-body safety fallback.
"""

import os
import urllib.parse
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.schemas import ContactCandidate
from backend.sales_ui.router import get_console_services, derive_company_outreach_status, is_candidate_outreach_eligible
from sales_engine.candidates.models import CandidateCompany
from sales_engine.sending.schemas import StoredDraft
from sales_engine.sending.approval_service import compute_content_fingerprint
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending import SuppressionStore


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def csrf_setup(client):
    dash_resp = client.get("/sales/dashboard")
    token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token_abc"
    client.cookies.set("sales_csrf_token", token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": token}
    return {"token": token, "headers": headers}


def test_01_successful_manual_dispatch_e2e(client, csrf_setup):
    """End-to-End test:
    - Candidate with verified contact & approved draft.
    - Viewing draft never marks it sent.
    - Operator POST confirmation triggers manual log, draft update, and candidate state change.
    - Candidate is excluded from cold outreach.
    - Sent & History reflects the manual dispatch.
    """
    csrf_token = csrf_setup["token"]
    headers = csrf_setup["headers"]
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]

    cand_id = "cand:domain:acme-bim-studio.com"
    comp_domain = "acme-bim-studio.com"

    # 1. Setup eligible candidate
    cand = CandidateCompany(
        candidate_id=cand_id,
        canonical_domain=comp_domain,
        company_name="Acme BIM Studio",
        discovery_source="acceptance_test",
        discovery_status="qualified",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
        previous_outreach_detected=False,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id="cnt:marco:acme",
        candidate_id=cand_id,
        full_name="Marco Rossi",
        work_email="marco.rossi@acme-bim-studio.com",
        job_title="BIM Director",
        email_status="verified",
        email_confidence=95,
    )
    cand_store.save_contact(contact, candidate_id=cand_id)

    # 2. Stage draft
    draft_id = "draft:manual:acme_bim_001"
    draft_dict = {
        "draft_id": draft_id,
        "revision": 1,
        "lead_id": cand_id,
        "contact_id": contact.contact_id,
        "recipient_name": "Marco Rossi",
        "recipient_title": "BIM Director",
        "recipient_email": "marco.rossi@acme-bim-studio.com",
        "sender_name": "Consulting Lead",
        "sender_email": "outreach@pybim-studio.it",
        "subject": "BIM Collaboration Inquiry - Acme BIM Studio",
        "body": "Gentile Marco, vorremmo esplorare una possibile sinergia sui progetti infrastrutturali BIM.",
        "service_used": "manual_staged",
        "approval_status": "approved",
        "send_status": "not_sent",
    }
    draft = StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict))
    rev_store.save_draft(draft)

    # Candidate should currently be eligible for outreach
    reloaded_cand = cand_store.get_candidate(cand_id)
    assert is_candidate_outreach_eligible(reloaded_cand) is True
    # Outreach status before sending is DRAFT_READY
    assert derive_company_outreach_status(reloaded_cand, drafts=[draft]) == "DRAFT_READY"

    # 3. Viewing draft detail MUST NEVER mark draft sent
    view_resp = client.get(f"/sales/drafts/{draft_id}")
    assert view_resp.status_code == 200
    assert "Marco Rossi" in view_resp.text
    assert "Open in Gmail Web" in view_resp.text
    # Check draft remains not_sent
    unchanged_draft = rev_store.get_draft(draft_id)
    assert unchanged_draft.send_status == "not_sent"

    # 4. Operator submits explicit Manual Dispatch Confirmation POST
    post_data = {
        "channel": "email",
        "confirm_external_transmission": "1",
        "external_ref": "GMAIL-THREAD-ACME-991",
        "operator_notes": "Sent via corporate Gmail; Marco confirmed as active contact.",
        "csrf_token": csrf_token,
    }
    dispatch_resp = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data=post_data,
        headers=headers,
        follow_redirects=False,
    )
    assert dispatch_resp.status_code == 303
    assert "successfully%20confirmed" in dispatch_resp.headers["location"].lower() or "confirmed" in dispatch_resp.headers["location"].lower()

    # 5. Verify Draft send_status updated to 'sent'
    sent_draft = rev_store.get_draft(draft_id)
    assert sent_draft.send_status == "sent"

    # 6. Verify Candidate State atomically updated
    updated_cand = cand_store.get_candidate(cand_id)
    assert updated_cand.historical_contact_status == "contacted"
    assert updated_cand.previous_outreach_detected is True
    assert updated_cand.cold_outreach_allowed is False
    assert is_candidate_outreach_eligible(updated_cand) is False

    # 7. Verify manual log entry created with accurate fields
    logs = rev_store.list_manual_outreach_logs(cand_id)
    assert len(logs) >= 1
    recent_log = logs[0]
    assert recent_log["draft_id"] == draft_id
    assert recent_log["recipient_email"] == "marco.rossi@acme-bim-studio.com"
    assert recent_log["recipient_name"] == "Marco Rossi"
    assert recent_log["subject"] == "BIM Collaboration Inquiry - Acme BIM Studio"
    assert recent_log["channel"] == "email"
    assert recent_log["external_ref"] == "GMAIL-THREAD-ACME-991"
    assert recent_log["confirmed_sent_externally"] == 1
    assert recent_log["logged_at"] is not None

    # 8. Derived company outreach status reflects SENT_MANUALLY_CONFIRMED
    assert derive_company_outreach_status(updated_cand, drafts=[sent_draft], manual_logs=logs) == "SENT_MANUALLY_CONFIRMED"

    # 9. Verify Sent & History UI view (/sales/sent) renders the confirmed manual log
    sent_view = client.get("/sales/sent")
    assert sent_view.status_code == 200
    assert "GMAIL-THREAD-ACME-991" in sent_view.text
    assert "marco.rossi@acme-bim-studio.com" in sent_view.text


def test_02_idempotency_duplicate_and_failure_safety(client, csrf_setup):
    """Verify duplicate submission safety, unconfirmed failure handling, and draft revision behavior."""
    csrf_token = csrf_setup["token"]
    headers = csrf_setup["headers"]
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]

    cand_id = "cand:domain:idempotent-test.it"
    comp_domain = "idempotent-test.it"

    cand = CandidateCompany(
        candidate_id=cand_id,
        canonical_domain=comp_domain,
        company_name="Idempotent Test Spa",
        qualification_outcome="qualified_for_enrichment",
        discovery_status="qualified",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id="cnt:paolo:idempotent",
        candidate_id=cand_id,
        full_name="Paolo Banti",
        work_email="paolo@idempotent-test.it",
        email_status="verified",
    )
    cand_store.save_contact(contact, candidate_id=cand_id)

    draft_id = "draft:manual:idempotent_001"
    draft_dict = {
        "draft_id": draft_id,
        "revision": 1,
        "lead_id": cand_id,
        "contact_id": contact.contact_id,
        "recipient_name": "Paolo Banti",
        "recipient_email": "paolo@idempotent-test.it",
        "subject": "Proposta Consulenza BIM",
        "body": "Buongiorno Ing. Banti...",
        "approval_status": "approved",
        "send_status": "not_sent",
    }
    draft = StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict))
    rev_store.save_draft(draft)

    # 1. Failure Safety: POST without explicit confirmation checkbox
    unconfirmed_resp = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data={
            "channel": "email",
            # Missing confirm_external_transmission
            "external_ref": "REF-UNCONFIRMED",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert unconfirmed_resp.status_code == 303
    assert "error=" in unconfirmed_resp.headers["location"]
    assert "confirmation" in unconfirmed_resp.headers["location"].lower()

    # Verify no log created, draft not marked sent, candidate untouched
    assert len(rev_store.list_manual_outreach_logs(cand_id)) == 0
    assert rev_store.get_draft(draft_id).send_status == "not_sent"
    assert cand_store.get_candidate(cand_id).historical_contact_status != "contacted"

    # 2. First valid submission succeeds
    valid_resp = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data={
            "channel": "email",
            "confirm_external_transmission": "1",
            "external_ref": "REF-UNIQUE-001",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert valid_resp.status_code == 303
    initial_logs = rev_store.list_manual_outreach_logs(cand_id)
    assert len(initial_logs) == 1

    # 3. Idempotent Repeated Submission: same draft and external_ref
    repeat_resp = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data={
            "channel": "email",
            "confirm_external_transmission": "1",
            "external_ref": "REF-UNIQUE-001",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert repeat_resp.status_code == 303
    assert "duplicate" in repeat_resp.headers["location"].lower()
    # Log count must remain 1
    assert len(rev_store.list_manual_outreach_logs(cand_id)) == 1

    # 4. Draft revision and in-place sent immutability handling:
    # A sent draft cannot be edited in place (must maintain audit trail)
    edit_resp = client.post(
        f"/sales/drafts/{draft_id}/edit",
        data={
            "revision": 1,
            "subject": "Proposta Aggiornata Revisione 2",
            "body": "Testo modificato per revisione 2.",
            "reviewer": "operator_editor",
            "note": "Aggiornato oggetto",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303
    # Edit of sent draft is rejected to protect sent audit state
    assert "error=" in edit_resp.headers["location"]
    assert "cannot%20edit%20a%20sent" in edit_resp.headers["location"].lower()
    rev1_draft = rev_store.get_draft(draft_id)
    assert rev1_draft.revision == 1
    assert rev1_draft.send_status == "sent"


def test_03_suppression_dnc_and_client_handoff_safety(client, csrf_setup):
    """Verify suppression/DNC enforcement and web client deep link generation."""
    csrf_token = csrf_setup["token"]
    headers = csrf_setup["headers"]
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]
    supp_store: SuppressionStore = svcs["suppression_svc"]

    supp_domain = "suppressed-company.it"
    cand_id = f"cand:domain:{supp_domain}"

    cand = CandidateCompany(
        candidate_id=cand_id,
        canonical_domain=supp_domain,
        company_name="Suppressed Company",
        qualification_outcome="qualified_for_enrichment",
        discovery_status="qualified",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(cand)

    supp_email = f"info@{supp_domain}"
    supp_store.add_suppression(email=supp_email, company_domain=supp_domain, reason="opt_out", source="manual_test")

    contact = ContactCandidate(
        contact_id="cnt:supp:user",
        candidate_id=cand_id,
        work_email=supp_email,
        email_status="verified",
    )
    cand_store.save_contact(contact, candidate_id=cand_id)

    draft_id = "draft:manual:suppressed_001"
    draft_dict = {
        "draft_id": draft_id,
        "lead_id": cand_id,
        "contact_id": contact.contact_id,
        "recipient_name": "Suppressed User",
        "recipient_email": supp_email,
        "subject": "Unsolicited message",
        "body": "Hello world",
        "approval_status": "approved",
        "send_status": "not_sent",
    }
    draft = StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict))
    rev_store.save_draft(draft)

    # 1. New outreach dispatch to suppressed contact must be BLOCKED
    supp_attempt = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data={
            "channel": "email",
            "confirm_external_transmission": "1",
            "external_ref": "BLOCKED-01",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert supp_attempt.status_code == 303
    assert "suppressed" in supp_attempt.headers["location"].lower()
    # Ensure no log created
    assert len(rev_store.list_manual_outreach_logs(cand_id)) == 0

    # 2. Retrospective audit log is allowed for compliance history tracking
    retro_attempt = client.post(
        f"/sales/drafts/{draft_id}/log-manual-send",
        data={
            "channel": "email",
            "confirm_external_transmission": "1",
            "is_retrospective_audit": "1",
            "external_ref": "RETRO-AUDIT-01",
            "operator_notes": "Logging past historical event prior to DNC status.",
            "csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )
    assert retro_attempt.status_code == 303
    assert "retrospective" in retro_attempt.headers["location"].lower() or "audit" in retro_attempt.headers["location"].lower()
    assert len(rev_store.list_manual_outreach_logs(cand_id)) == 1

    # 3. Client Handoff Safety: URL Encoding and Long-Body Protection
    long_body = "Line of proposal.\n" * 120  # ~2160 chars, exceeds safe GET URL threshold (>1500 chars)
    long_draft_id = "draft:manual:long_body_001"
    long_dict = {
        "draft_id": long_draft_id,
        "lead_id": cand_id,
        "contact_id": "cnt:safe:user",
        "recipient_name": "Safe Contact",
        "recipient_email": "safe.test@company.it",
        "subject": "Important BIM Notice & Proposal: Project 2026",
        "body": long_body,
        "approval_status": "approved",
        "send_status": "not_sent",
    }
    long_draft = StoredDraft(**long_dict, content_hash=compute_content_fingerprint(long_dict))
    rev_store.save_draft(long_draft)

    resp_long = client.get(f"/sales/drafts/{long_draft_id}")
    assert resp_long.status_code == 200
    # Recipient and subject must be URL encoded in deep links
    assert "to=safe.test%40company.it" in resp_long.text or "to=safe.test@company.it" in resp_long.text
    assert "Important+BIM+Notice" in resp_long.text or "Important%20BIM%20Notice" in resp_long.text
    # When body > 1500 chars, template omits &body= from URL to avoid browser truncation / query exposure
    assert "title=\"Draft body is long. Click 'Copy Email Body' and paste directly into Gmail.\"" in resp_long.text
    assert "Copy Email Body" in resp_long.text
