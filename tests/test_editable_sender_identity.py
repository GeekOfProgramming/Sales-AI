import pytest
import os
import gc
import tempfile
from unittest.mock import patch

from sales_engine.sending.schemas import StoredDraft, DraftEditRequest
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.approval_service import (
    ApprovalService,
    get_trusted_senders,
    compute_content_fingerprint,
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    gc.collect()
    try:
        if os.path.exists(path):
            os.remove(path)
    except PermissionError:
        pass


@pytest.fixture
def sample_draft(temp_db):
    store = ReviewStore(db_path=temp_db)
    draft_dict = {
        "draft_id": "draft:test:001",
        "revision": 1,
        "lead_id": "domain:testcompany.it",
        "contact_id": "contact:test@testcompany.it",
        "recipient_name": "Mario Rossi",
        "recipient_email": "mario.rossi@testcompany.it",
        "sender_name": "pyBIM Team",
        "sender_email": "primary@company.com",
        "subject": "Original Subject Line",
        "body": "Original manually crafted body text without any changes.",
        "approval_status": "approved",
        "send_status": "not_sent",
        "outreach_status": "approved",
    }
    fp = compute_content_fingerprint(draft_dict)
    draft = StoredDraft(**draft_dict, content_hash=fp, approved_content_hash=fp)
    store.save_draft(draft)
    return store, draft


def test_get_trusted_senders_config(monkeypatch):
    """Test get_trusted_senders correctly parses primary and allowed senders with no hardcoded fallback."""
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_ALLOWED_SENDERS", raising=False)
    assert get_trusted_senders() == []

    monkeypatch.setenv("SMTP_FROM_EMAIL", "outreach@domain.com")
    assert get_trusted_senders() == ["outreach@domain.com"]

    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "sales@domain.com, direct@domain.com, outreach@domain.com")
    senders = get_trusted_senders()
    assert senders == ["outreach@domain.com", "sales@domain.com", "direct@domain.com"]


def test_edit_draft_with_authorized_sender_identity(sample_draft, monkeypatch):
    """Editing draft with an authorized sender email creates a new revision r2 and resets approval."""
    store, draft = sample_draft
    monkeypatch.setenv("SMTP_FROM_EMAIL", "primary@company.com")
    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "secondary@company.com, partner@company.com")

    service = ApprovalService(review_store=store)
    edit_req = DraftEditRequest(
        revision=1,
        sender_email="secondary@company.com",
        sender_name="Partner Director",
    )

    r2 = service.edit_draft(draft.draft_id, edit_req)

    assert r2.revision == 2
    assert r2.sender_email == "secondary@company.com"
    assert r2.sender_name == "Partner Director"
    # Approval must be reset to pending_review and approved_content_hash cleared
    assert r2.approval_status == "pending_review"
    assert r2.approved_content_hash is None
    # Subject and body must remain strictly unchanged
    assert r2.subject == draft.subject
    assert r2.body == draft.body
    # Content hash must be updated
    assert r2.content_hash != draft.content_hash

    # Verify fingerprint includes new sender info
    expected_fp = compute_content_fingerprint(r2)
    assert r2.content_hash == expected_fp


def test_edit_draft_rejects_unauthorized_sender_identity(sample_draft, monkeypatch):
    """Attempting to set an unauthorized sender email raises ValueError."""
    store, draft = sample_draft
    monkeypatch.setenv("SMTP_FROM_EMAIL", "primary@company.com")
    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "secondary@company.com")

    service = ApprovalService(review_store=store)
    edit_req = DraftEditRequest(
        revision=1,
        sender_email="spoofed@hacker.io",
        sender_name="Spoofer",
    )

    with pytest.raises(ValueError, match="Unauthorized sender identity"):
        service.edit_draft(draft.draft_id, edit_req)


def test_edit_draft_fails_closed_when_no_senders_configured(sample_draft, monkeypatch):
    """Attempting to specify a sender when no senders are configured raises ValueError."""
    store, draft = sample_draft
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_ALLOWED_SENDERS", raising=False)

    service = ApprovalService(review_store=store)
    edit_req = DraftEditRequest(
        revision=1,
        sender_email="someone@company.com",
    )

    with pytest.raises(ValueError, match="No authorized senders are configured"):
        service.edit_draft(draft.draft_id, edit_req)


def test_manual_draft_text_preservation(temp_db, monkeypatch):
    """Changing sender on a manual draft preserves verbatim subject and body without rewriting."""
    store = ReviewStore(db_path=temp_db)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "primary@company.com")
    monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "sales@company.com")

    original_body = (
        "Buongiorno Ing. Rossi,\n\n"
        "Abbiamo analizzato i vostri progetti recenti. Offriamo supporto BIM specialistico.\n\n"
        "Cordiali saluti,\nTeam pyBIM"
    )
    original_subject = "Collaborazione BIM Specialistica"

    draft_dict = {
        "draft_id": "draft:test:manual_preserve",
        "revision": 1,
        "lead_id": "domain:testcompany.it",
        "contact_id": "contact:test@testcompany.it",
        "recipient_name": "Mario Rossi",
        "recipient_email": "mario.rossi@testcompany.it",
        "sender_name": "pyBIM Team",
        "sender_email": "primary@company.com",
        "subject": original_subject,
        "body": original_body,
        "approval_status": "pending_review",
        "send_status": "not_sent",
        "outreach_status": "draft_ready",
    }
    fp = compute_content_fingerprint(draft_dict)
    draft = StoredDraft(**draft_dict, content_hash=fp)
    store.save_draft(draft)

    service = ApprovalService(review_store=store)
    edit_req = DraftEditRequest(
        revision=1,
        sender_email="sales@company.com",
        sender_name="Senior Account Exec",
    )

    r2 = service.edit_draft(draft.draft_id, edit_req)
    assert r2.revision == 2
    assert r2.subject == original_subject
    assert r2.body == original_body
    assert r2.send_status == "not_sent"
    assert r2.sender_email == "sales@company.com"
    assert r2.sender_name == "Senior Account Exec"


@pytest.mark.asyncio
async def test_manual_draft_creation_route_enforcement(monkeypatch, temp_db):
    """Test router create_manual_draft_action validation for authorized sender."""
    from backend.sales_ui.router import create_manual_draft_action
    from sales_engine.candidates.models import CandidateCompany
    from sales_engine.candidates.candidate_store import CandidateStore
    from backend.schemas import ContactCandidate
    from sales_engine.sending.review_store import ReviewStore
    from sales_engine.sending.suppression_store import SuppressionStore

    cand_store = CandidateStore(db_path=temp_db)
    rev_store = ReviewStore(db_path=temp_db)
    supp_svc = SuppressionStore(rev_store)

    cid = "cand:domain:acme.it"
    cand = CandidateCompany(
        candidate_id=cid,
        domain="acme.it",
        name="ACME Engineering",
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(cand)

    contact = ContactCandidate(
        contact_id="contact:acme_key",
        first_name="Marco",
        last_name="Polo",
        full_name="Marco Polo",
        work_email="marco@acme.it",
        email_status="verified",
    )
    cand_store.save_contact(contact, candidate_id=cid)

    # Set up mock services
    mock_svcs = {
        "cand_store": cand_store,
        "rev_store": rev_store,
        "suppression_svc": supp_svc,
        "approval_svc": ApprovalService(review_store=rev_store),
    }

    with patch("backend.sales_ui.router.get_console_services", return_value=mock_svcs):
        # 1. When no trusted senders configured -> redirect with error
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        monkeypatch.delenv("SMTP_ALLOWED_SENDERS", raising=False)

        resp = await create_manual_draft_action(
            candidate_id=cid,
            contact_id="contact:acme_key",
            recipient_email="marco@acme.it",
            subject="Test Subject",
            body="Test Body",
            sender_email="outreach@company.com",
            csrf=None,
        )
        assert resp.status_code == 303
        import urllib.parse
        assert "No authorized company sender configured" in urllib.parse.unquote(resp.headers["location"])

        # 2. When sender is unauthorized -> redirect with error
        monkeypatch.setenv("SMTP_FROM_EMAIL", "authorized@company.com")
        resp = await create_manual_draft_action(
            candidate_id=cid,
            contact_id="contact:acme_key",
            recipient_email="marco@acme.it",
            subject="Test Subject",
            body="Test Body",
            sender_email="spoofed@other.com",
            csrf=None,
        )
        assert resp.status_code == 303
        assert "Unauthorized sender identity" in urllib.parse.unquote(resp.headers["location"])

        # 3. When sender is authorized -> draft is created in Pending Review and NOT sent
        monkeypatch.setenv("SMTP_ALLOWED_SENDERS", "custom_sender@company.com")
        resp = await create_manual_draft_action(
            candidate_id=cid,
            contact_id="contact:acme_key",
            recipient_email="marco@acme.it",
            subject="Valid Subject",
            body="Verbatim manual text here",
            sender_name="Alice Smith",
            sender_email="custom_sender@company.com",
            csrf=None,
        )
        assert resp.status_code == 303
        loc = resp.headers["location"]
        assert "/sales/drafts/draft:manual:" in loc

        # Inspect persisted draft
        draft_id = loc.split("/sales/drafts/")[1].split("?")[0]
        created = rev_store.get_draft(draft_id)
        assert created is not None
        assert created.sender_name == "Alice Smith"
        assert created.sender_email == "custom_sender@company.com"
        assert created.subject == "Valid Subject"
        assert created.body == "Verbatim manual text here"
        assert created.approval_status == "pending_review"
        assert created.send_status == "not_sent"
