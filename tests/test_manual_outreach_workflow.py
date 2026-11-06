import pytest
from unittest.mock import MagicMock
from backend.sales_ui.router import derive_company_outreach_status


class DummyCandidate:
    def __init__(
        self,
        candidate_id="cand_1",
        company_name="Test Corp",
        company_domain="test.com",
        historical_contact_status=None,
        reply_status=None,
        active_conversation=False,
        qualification_outcome=None,
        discovery_status=None,
        previous_outreach_detected=False,
    ):
        self.candidate_id = candidate_id
        self.company_name = company_name
        self.company_domain = company_domain
        self.historical_contact_status = historical_contact_status
        self.reply_status = reply_status
        self.active_conversation = active_conversation
        self.qualification_outcome = qualification_outcome
        self.discovery_status = discovery_status
        self.previous_outreach_detected = previous_outreach_detected


class DummyDraft:
    def __init__(self, approval_status="pending", send_status="unsent"):
        self.approval_status = approval_status
        self.send_status = send_status


def test_derive_outreach_status_reply_received():
    # Reply via reply_status
    c1 = DummyCandidate(reply_status="received")
    assert derive_company_outreach_status(c1) == "REPLY_RECEIVED"

    c2 = DummyCandidate(reply_status="positive_interview")
    assert derive_company_outreach_status(c2) == "REPLY_RECEIVED"

    # Reply via historical_contact_status
    c3 = DummyCandidate(historical_contact_status="replied")
    assert derive_company_outreach_status(c3) == "REPLY_RECEIVED"


def test_derive_outreach_status_historical_contact_review():
    # Candidate with reengagement_review qualification outcome
    c1 = DummyCandidate(qualification_outcome="reengagement_review")
    assert derive_company_outreach_status(c1) == "HISTORICAL_CONTACT_REVIEW"

    # Candidate with historical_pending discovery status
    c2 = DummyCandidate(discovery_status="historical_pending")
    assert derive_company_outreach_status(c2) == "HISTORICAL_CONTACT_REVIEW"


def test_derive_outreach_status_sent_manually_confirmed():
    # Manual log present
    c = DummyCandidate()
    assert derive_company_outreach_status(c, manual_logs=[{"id": "log_1"}]) == "SENT_MANUALLY_CONFIRMED"

    # Historical sent email present
    assert derive_company_outreach_status(c, historical_sent_emails=[{"msg_id": "123"}]) == "SENT_MANUALLY_CONFIRMED"

    # Historical contacted status
    c_hist = DummyCandidate(historical_contact_status="contacted")
    assert derive_company_outreach_status(c_hist) == "SENT_MANUALLY_CONFIRMED"

    # Previous outreach detected
    c_prev = DummyCandidate(previous_outreach_detected=True)
    assert derive_company_outreach_status(c_prev) == "SENT_MANUALLY_CONFIRMED"


def test_derive_outreach_status_draft_ready():
    c = DummyCandidate()
    # Active draft ready
    drafts = [DummyDraft(approval_status="pending")]
    assert derive_company_outreach_status(c, drafts=drafts) == "DRAFT_READY"

    # Approved draft
    drafts_app = [DummyDraft(approval_status="approved")]
    assert derive_company_outreach_status(c, drafts=drafts_app) == "DRAFT_READY"

    # Rejected draft only -> NOT_SENT
    drafts_rej = [DummyDraft(approval_status="rejected")]
    assert derive_company_outreach_status(c, drafts=drafts_rej) == "NOT_SENT"


def test_derive_outreach_status_not_sent():
    c = DummyCandidate()
    assert derive_company_outreach_status(c) == "NOT_SENT"
    assert derive_company_outreach_status(None) == "NOT_SENT"


def test_precedence_rules():
    # Reply takes precedence over manual sent logs
    c_reply_and_sent = DummyCandidate(reply_status="received")
    assert derive_company_outreach_status(c_reply_and_sent, manual_logs=[{"id": "1"}]) == "REPLY_RECEIVED"

    # Sent takes precedence over draft ready
    c_sent_and_draft = DummyCandidate(previous_outreach_detected=True)
    drafts = [DummyDraft(approval_status="approved")]
    assert derive_company_outreach_status(c_sent_and_draft, drafts=drafts) == "SENT_MANUALLY_CONFIRMED"
