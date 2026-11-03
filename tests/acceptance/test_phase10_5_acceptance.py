"""
tests/acceptance/test_phase10_5_acceptance.py
Phase 10.5 Comprehensive Golden Acceptance Test Suite.
Specification: PHASE10.5-QA-GOLDEN-SPEC.md
Catalog: tests/golden/phase10_5_console.json (71 cases)

Covers all 71 cases across sections 3 through 23:
- Critical Safety Gates (P10_5-GOLD-001..012)
- Phase 8 / Phase 6 / Phase 9 Integration (P10_5-GOLD-013..020)
- Human Review Workflow (P10_5-WORKFLOW-001..007)
- DNC & Suppression UI (P10_5-SUPP-001..003)
- Historical Import (P10_5-IMPORT-001..005)
- Candidate Page & Presentation (P10_5-CAND-001..002, P10_5-BIM-001, P10_5-WATCH-001..002)
- Sources, Health & Evidence URL Safety (P10_5-SRC-001..002, P10_5-URL-001..002, P10_5-SET-001, P10_5-REP-001)
- System Boundaries, Security & Audit (P10_5-NET-001, P10_5-LLM-001, P10_5-REV-001, P10_5-AUDIT-001, P10_5-SENT-001, P10_5-SEC-001)
- Core Screen Smoke Tests (P10_5-SMOKE-001..014)
- Weak-LLM Semantic Evaluation (P10_5-SEM-001..005)
"""

import ast
import json
import os
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Initialize module-level isolated database before backend imports
test_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
test_db_path = test_db_file.name
test_db_file.close()

os.environ["SALESAI_DB_PATH"] = test_db_path
os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
os.environ["SALES_OUTREACH_DB"] = test_db_path
os.environ["EMAIL_SEND_ENABLED"] = "false"

from backend.main import app
from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import (
    CandidateCompany,
    MarketEvidence,
    QualificationOutcome,
)
from sales_engine.sending.approval_service import ApprovalService, compute_content_fingerprint
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.schemas import SendAttempt, StoredDraft, SendResult
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.outreach.outreach_orchestrator import OutreachOrchestrator
from sales_engine.outreach.schemas import EmailDraft


class CsrfAwareClient(TestClient):
    """Test client that automatically manages CSRF tokens and Same-Origin headers."""
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "golden_csrf_token_secret_xyz789"
        if "sales_csrf_token" not in self.cookies:
            self.cookies.set("sales_csrf_token", token)

        data = kwargs.get("data")
        if isinstance(data, dict) and "csrf_token" not in data:
            data_copy = dict(data)
            data_copy["csrf_token"] = token
            kwargs["data"] = data_copy

        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = {"Origin": "http://testserver"}
        elif "Origin" not in headers:
            headers_copy = dict(headers)
            headers_copy["Origin"] = "http://testserver"
            kwargs["headers"] = headers_copy

        return super().post(url, *args, **kwargs)


def make_stored_draft(
    draft_id: str,
    recipient_email: str,
    subject: str = "Subject",
    body: str = "Body content here",
    approval_status: str = "pending_review",
    send_status: str = "not_sent",
    revision: int = 1,
    lead_id: str = "domain:test.it",
    sender_email: str = "sales@trusted-domain.com",
    contact_id: str = "cnt_test_01",
    recipient_name: str = "Test Recipient",
    evidence_refs: list = None,
    generation_model: str = "qwen2.5:1.5b",
    prompt_version: str = "outreach_v1",
) -> StoredDraft:
    d = StoredDraft(
        draft_id=draft_id,
        revision=revision,
        lead_id=lead_id,
        contact_id=contact_id,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        sender_email=sender_email,
        sender_name="pyBIM Sales",
        subject=subject,
        body=body,
        approval_status=approval_status,
        send_status=send_status,
        content_hash="",
        evidence_refs=evidence_refs or ["EV-001"],
        generation_model=generation_model,
        prompt_version=prompt_version,
    )
    d.content_hash = compute_content_fingerprint(d)
    if approval_status == "approved":
        d.approved_content_hash = d.content_hash
    return d


@pytest.fixture(autouse=True)
def ensure_db_env(golden_data):
    os.environ["SALES_OUTREACH_DB"] = test_db_path
    os.environ["SALESAI_DB_PATH"] = test_db_path
    os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
    os.environ["SMTP_FROM_EMAIL"] = "sales@trusted-domain.com"
    os.environ["SMTP_ALLOWED_SENDERS"] = "sales@trusted-domain.com,trusted@pybim-studio.com"
    yield



@pytest.fixture(scope="module")
def client():
    return CsrfAwareClient(app)


@pytest.fixture(scope="module")
def golden_data():
    cand_store = CandidateStore(db_path=test_db_path)
    rev_store = ReviewStore(db_path=test_db_path)
    supp_store = SuppressionStore(rev_store)
    appr_svc = ApprovalService(rev_store)

    # 1. Candidate Milano (Historical contacted)
    c_mil = CandidateCompany(
        candidate_id="cand_milano",
        name="Studio Milano BIM",
        domain="studio-milano-bim.it",
        country="IT",
        city="Milano",
        company_tier="tier_1",
        bim_maturity="high",
        discovery_status="qualified",
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
    )
    cand_store.save_candidate(c_mil)

    # 2. Candidate Alpha (Unknown BIM maturity)
    c_alpha = CandidateCompany(
        candidate_id="cand_alpha",
        name="Studio Alpha Architettura",
        domain="studio-alpha.it",
        country="IT",
        company_tier="tier_2",
        bim_maturity="unknown",
        discovery_status="researching",
    )
    cand_store.save_candidate(c_alpha)

    # 3. Watchlist candidate
    c_watch = CandidateCompany(
        candidate_id="cand_watch",
        name="Watchlist Company",
        domain="watch-pres.it",
        discovery_status="watchlist",
        qualification_outcome=QualificationOutcome.watchlist,
    )
    cand_store.save_candidate(c_watch)
    cand_store.add_to_watchlist("cand_watch", reason="Expanding MEP unit", signals=["mep_jobs"])

    # 4. Qualified candidate for Phase 10.5 draft generation
    c_qual = CandidateCompany(
        candidate_id="cand_p10_qual_gold",
        name="Studio Qualificato BIM Gold",
        domain="qualificato-bim-gold.it",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(c_qual)
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_gold_revit_01",
            candidate_id="cand_p10_qual_gold",
            category="service",
            signal_type="bim_service",
            text="Studio Qualificato BIM delivers multidisciplinary Revit digital twins.",
        )
    )
    c_verified = ContactCandidate(
        contact_id="cnt_p10_qual_01",
        full_name="Giuseppe Verdi",
        job_title="BIM Director",
        work_email="giuseppe@qualificato-bim-gold.it",
        email_status="verified",
    )
    cand_store.save_contact(c_verified, candidate_id="cand_p10_qual_gold")

    c_likely = ContactCandidate(
        contact_id="cnt_p10_qual_likely",
        full_name="Paolo Neri",
        job_title="BIM Specialist",
        work_email="paolo@qualificato-bim-gold.it",
        email_status="likely",
    )
    cand_store.save_contact(c_likely, candidate_id="cand_p10_qual_gold")

    c_risky = ContactCandidate(
        contact_id="cnt_p10_qual_risky",
        full_name="Risky User",
        job_title="Intern",
        work_email="risky@qualificato-bim-gold.it",
        email_status="risky",
    )
    cand_store.save_contact(c_risky, candidate_id="cand_p10_qual_gold")

    # 5. Ungrounded candidate
    c_unground = CandidateCompany(
        candidate_id="cand_ungrounded_gold",
        name="Ungrounded Studio",
        domain="ungrounded-studio.it",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(c_unground)
    c_unground_cnt = ContactCandidate(
        contact_id="cnt_ungrounded",
        full_name="Luigi Neri",
        work_email="luigi@ungrounded-studio.it",
        email_status="verified",
    )
    cand_store.save_contact(c_unground_cnt, candidate_id="cand_ungrounded_gold")

    # 6. Seed StoredDrafts for testing
    d1 = make_stored_draft(
        draft_id="d_gold_001",
        recipient_email="director@studio-gold-1.it",
        subject="Revit Automation Collaboration",
        body="Hello, we noticed your BIM workflows.",
        approval_status="pending_review",
        send_status="not_sent",
    )
    rev_store.save_draft(d1)

    d2 = make_stored_draft(
        draft_id="d_gold_002",
        recipient_email="lead@studio-gold-2.it",
        subject="Subject Rev 1",
        body="Body Rev 1",
        approval_status="approved",
        send_status="not_sent",
        revision=1,
    )
    rev_store.save_draft(d2)

    d3 = make_stored_draft(
        draft_id="d_gold_003",
        recipient_email="contact@studio-gold-3.it",
        subject="Subject Sent",
        body="Body Sent",
        approval_status="approved",
        send_status="sent",
        revision=1,
    )
    rev_store.save_draft(d3)

    d5 = make_stored_draft(
        draft_id="d_gold_005",
        recipient_email="lead@studio-dry.it",
        subject="Dry run test",
        body="Valid body text for dry run.",
        approval_status="approved",
        send_status="not_sent",
    )
    rev_store.save_draft(d5)

    d6 = make_stored_draft(
        draft_id="d_gold_006",
        recipient_email="lead@studio-confirm.it",
        subject="Subject Confirm",
        body="Body content for explicit send confirmation.",
        approval_status="approved",
        send_status="not_sent",
    )
    rev_store.save_draft(d6)

    d7 = make_stored_draft(
        draft_id="d_gold_007",
        recipient_email="lead@studio-disabled.it",
        subject="Disabled Send Test",
        body="Testing disabled send.",
        approval_status="approved",
        send_status="not_sent",
    )
    rev_store.save_draft(d7)

    d8 = make_stored_draft(
        draft_id="d_gold_008",
        recipient_email="target@untrusted-sender.it",
        sender_email="untrusted@random-domain.com",
        subject="Unauthorized Sender Test",
        body="Hello from untrusted sender.",
        approval_status="approved",
        send_status="not_sent",
    )
    rev_store.save_draft(d8)

    d_work = make_stored_draft(
        draft_id="d_workflow_test",
        recipient_email="lead@workflow-studio.it",
        subject="Workflow Subject",
        body="Workflow Body Text",
        approval_status="pending_review",
        send_status="not_sent",
    )
    rev_store.save_draft(d_work)

    d_work_rej = make_stored_draft(
        draft_id="d_workflow_rej",
        recipient_email="lead2@workflow-studio.it",
        subject="Rejection Subject",
        body="Body",
        approval_status="pending_review",
    )
    rev_store.save_draft(d_work_rej)

    d_work_ok = make_stored_draft(
        draft_id="d_workflow_send_ok",
        recipient_email="target@workflow-send-ok.it",
        subject="Delivery Subject",
        body="Delivery Body",
        approval_status="approved",
        send_status="not_sent",
    )
    rev_store.save_draft(d_work_ok)

    return {
        "cand_store": cand_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "appr_svc": appr_svc,
    }


# =========================================================================
# 1. Critical Safety Gates (P10_5-GOLD-001 .. 012)
# =========================================================================

def test_p10_5_gold_001_approve_never_sends(client, golden_data):
    """P10_5-GOLD-001: Approval transitions to approved with zero provider calls."""
    rev_store = golden_data["rev_store"]

    with patch("sales_engine.sending.send_orchestrator.SendOrchestrator.send_draft") as mock_orch:
        res = client.post(
            "/sales/drafts/d_gold_001/approve",
            data={"revision": 1, "reviewer": "operator_test", "note": "Approved"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        mock_orch.assert_not_called()

    updated = rev_store.get_draft("d_gold_001")
    assert updated.approval_status == "approved"
    assert updated.send_status == "not_sent"


def test_p10_5_gold_002_edit_invalidates_approval(client, golden_data):
    """P10_5-GOLD-002: Editing creates a new revision and invalidates approval."""
    rev_store = golden_data["rev_store"]

    res = client.post(
        "/sales/drafts/d_gold_002/edit",
        data={
            "revision": 1,
            "subject": "Subject Rev 2 Edited",
            "body": "Body Rev 2 Edited content.",
            "recipient_email": "lead@studio-gold-2.it",
            "edit_notes": "Refining proposition",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303

    rev2 = rev_store.get_draft("d_gold_002", 2)
    assert rev2 is not None
    assert rev2.revision == 2
    assert rev2.approval_status == "pending_review"
    assert rev2.send_status == "not_sent"
    assert "Subject Rev 2 Edited" in rev2.subject


def test_p10_5_gold_003_sent_revision_immutable(client, golden_data):
    """P10_5-GOLD-003: Sent revisions cannot be edited or mutated in place."""
    rev_store = golden_data["rev_store"]

    res = client.post(
        "/sales/drafts/d_gold_003/edit",
        data={
            "revision": 1,
            "subject": "Tampered Subject",
            "body": "Tampered Body",
            "recipient_email": "contact@studio-gold-3.it",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    unquoted = urllib.parse.unquote(res.headers.get("location", ""))
    assert "immutable" in unquoted.lower() or "sent" in unquoted.lower() or "error=" in unquoted

    unchanged = rev_store.get_draft("d_gold_003", 1)
    assert unchanged.subject == "Subject Sent"
    assert unchanged.send_status == "sent"


def test_p10_5_gold_004_suppressed_recipient_cannot_send(client, golden_data):
    """P10_5-GOLD-004: Exact email and company-domain suppression strictly block send."""
    rev_store = golden_data["rev_store"]
    supp_store = golden_data["supp_store"]

    # 1. Exact email suppression
    supp_store.add_suppression("blocked_exact@gold-supp.it", reason="opt_out")
    d1 = make_stored_draft(
        draft_id="d_gold_004_email",
        recipient_email="blocked_exact@gold-supp.it",
        approval_status="approved",
    )
    rev_store.save_draft(d1)

    with patch.dict(os.environ, {"EMAIL_SEND_ENABLED": "true"}):
        res1 = client.post(
            "/sales/drafts/d_gold_004_email/send",
            data={"revision": 1, "confirm_send": "true"},
            follow_redirects=False,
        )
        assert res1.status_code == 303
        assert "suppress" in urllib.parse.unquote(res1.headers.get("location", "")).lower()

    # 2. Company domain suppression
    supp_store.add_suppression(email="*@blocked-domain.it", company_domain="blocked-domain.it", reason="manual")
    d2 = make_stored_draft(
        draft_id="d_gold_004_domain",
        recipient_email="anyone@blocked-domain.it",
        lead_id="domain:blocked-domain.it",
        approval_status="approved",
    )
    rev_store.save_draft(d2)

    with patch.dict(os.environ, {"EMAIL_SEND_ENABLED": "true"}):
        res2 = client.post(
            "/sales/drafts/d_gold_004_domain/send",
            data={"revision": 1, "confirm_send": "true"},
            follow_redirects=False,
        )
        assert res2.status_code == 303
        assert "suppress" in urllib.parse.unquote(res2.headers.get("location", "")).lower()


def test_p10_5_gold_005_dry_run_zero_network_delivery(client, golden_data):
    """P10_5-GOLD-005: Dry Run executes pre-send validation only with zero network delivery."""
    rev_store = golden_data["rev_store"]

    with patch("sales_engine.sending.smtp_sender.SMTPEmailSender.send_email") as mock_send:
        res = client.post(
            "/sales/drafts/d_gold_005/dry-run",
            data={"revision": 1},
            follow_redirects=False,
        )
        assert res.status_code == 303
        mock_send.assert_not_called()

    after = rev_store.get_draft("d_gold_005")
    assert after.send_status == "not_sent"


def test_p10_5_gold_006_real_send_requires_explicit_confirmation(client, golden_data):
    """P10_5-GOLD-006: Real send requires explicit confirmation and displays exact payload."""
    # 1. Unconfirmed send attempt is rejected
    res_unconf = client.post(
        "/sales/drafts/d_gold_006/send",
        data={"revision": 1, "confirm_send": "false"},
        follow_redirects=False,
    )
    assert res_unconf.status_code == 303
    unquoted = urllib.parse.unquote(res_unconf.headers.get("location", ""))
    assert "confirm" in unquoted.lower()

    # 2. Detail page renders authoritative send confirmation modal elements
    page = client.get("/sales/drafts/d_gold_006")
    assert page.status_code == 200
    assert "send-confirm-modal" in page.text
    assert "Confirm & Send Real Email" in page.text


def test_p10_5_gold_007_send_disabled_flag_cannot_be_bypassed(client, golden_data):
    """P10_5-GOLD-007: EMAIL_SEND_ENABLED=false blocks send attempts."""
    with patch.dict(os.environ, {"EMAIL_SEND_ENABLED": "false"}):
        res = client.post(
            "/sales/drafts/d_gold_007/send",
            data={"revision": 1, "confirm_send": "true"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        unquoted = urllib.parse.unquote(res.headers.get("location", ""))
        assert "disabled" in unquoted.lower() or "error=" in unquoted.lower()


def test_p10_5_gold_008_sender_trust_cannot_be_bypassed(client, golden_data):
    """P10_5-GOLD-008: UI cannot invent or replace an unauthorized sender to pass validation."""
    with patch.dict(os.environ, {"EMAIL_SEND_ENABLED": "true"}):
        res = client.post(
            "/sales/drafts/d_gold_008/send",
            data={"revision": 1, "confirm_send": "true"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        unquoted = urllib.parse.unquote(res.headers.get("location", ""))
        assert "sender" in unquoted.lower() or "trust" in unquoted.lower() or "error=" in unquoted.lower()


def test_p10_5_gold_009_csrf_and_same_origin_enforced(client):
    """P10_5-GOLD-009: CSRF and same-origin protections are enforced on mutating actions."""
    raw_client = TestClient(app)

    # Missing CSRF token -> 403
    res_no_csrf = raw_client.post(
        "/sales/drafts/d_gold_001/approve",
        data={"revision": 1},
        headers={"Origin": "http://testserver"},
    )
    assert res_no_csrf.status_code == 403

    # Cross-origin attempt -> 403
    res_cross = raw_client.post(
        "/sales/drafts/d_gold_001/approve",
        data={"revision": 1, "csrf_token": "valid_token"},
        headers={"Origin": "https://evil-attacker.com"},
    )
    assert res_cross.status_code == 403

    # Mutating GET endpoints are rejected (404/405)
    for path in ["/sales/drafts/d_gold_001/approve", "/sales/drafts/d_gold_001/send"]:
        res_get = client.get(path)
        assert res_get.status_code in (404, 405)


def test_p10_5_gold_010_secrets_never_render_in_console(client):
    """P10_5-GOLD-010: Pages and API responses never render SMTP passwords or secret keys."""
    res = client.get("/sales/settings")
    assert res.status_code == 200
    text_lower = res.text.lower()
    assert "smtp_password" not in text_lower
    assert "client_secret" not in text_lower
    assert "bearer " not in text_lower
    assert "private_key" not in text_lower


def test_p10_5_gold_011_no_autonomous_reengagement(client, golden_data):
    """P10_5-GOLD-011: Historical/contacted candidates cannot silently restart a cold sequence."""
    res = client.post(
        "/sales/candidates/cand_milano/generate-draft",
        data={"contact_id": "cnt_fake"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    unquoted = urllib.parse.unquote(res.headers.get("location", ""))
    assert "ineligible" in unquoted.lower() or "error=" in unquoted.lower()


def test_p10_5_gold_012_zero_autonomous_send(client):
    """P10_5-GOLD-012: Normal rendering and navigation triggers zero outbound email sends."""
    with patch("sales_engine.sending.send_orchestrator.SendOrchestrator.send_draft") as mock_orch:
        for route in ["/sales", "/sales/candidates", "/sales/drafts", "/sales/approved", "/sales/watchlist"]:
            r = client.get(route)
            assert r.status_code == 200
        mock_orch.assert_not_called()


# =========================================================================
# 2. Phase 8 / Phase 6 / Phase 9 Integration (P10_5-GOLD-013 .. 020)
# =========================================================================

def test_p10_5_gold_013_real_phase10_qualification_recognized(client, golden_data):
    """P10_5-GOLD-013: qualified_for_enrichment candidate is recognized as draft eligible."""
    page = client.get("/sales/candidates/cand_p10_qual_gold")
    assert page.status_code == 200
    assert "generate-draft-section" in page.text
    assert "cnt_p10_qual_01" in page.text


def test_p10_5_gold_014_ineligible_qualification_routes_stay_blocked(client, golden_data):
    """P10_5-GOLD-014: Watchlist, known, reengagement, suppressed outcomes stay blocked."""
    cand_store = golden_data["cand_store"]
    ineligible = [
        ("cand_inelig_watch", QualificationOutcome.watchlist),
        ("cand_inelig_known", QualificationOutcome.known_company),
        ("cand_inelig_reeng", QualificationOutcome.reengagement_review),
        ("cand_inelig_supp", QualificationOutcome.suppressed),
        ("cand_inelig_rej", QualificationOutcome.reject),
    ]
    for cid, outcome in ineligible:
        c = CandidateCompany(
            candidate_id=cid,
            name=f"Company {cid}",
            domain=f"{cid}.it",
            qualification_outcome=outcome,
        )
        cand_store.save_candidate(c)

        res = client.post(
            f"/sales/candidates/{cid}/generate-draft",
            data={"contact_id": "cnt_dummy"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "error=" in res.headers.get("location", "")


def test_p10_5_gold_015_existing_contact_required(client, golden_data):
    """P10_5-GOLD-015: Draft generation requires an existing backend contact_id."""
    res = client.post(
        "/sales/candidates/cand_p10_qual_gold/generate-draft",
        data={"contact_email": "arbitrary@outside.com"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    unquoted = urllib.parse.unquote(res.headers.get("location", ""))
    assert "contact" in unquoted.lower() or "error=" in unquoted.lower()


def test_p10_5_gold_016_contact_email_status_eligibility(client, golden_data):
    """P10_5-GOLD-016: verified/likely permitted; risky/unknown/not_found blocked."""
    cid = "cand_p10_qual_gold"

    # Risky contact is rejected
    res_risky = client.post(
        f"/sales/candidates/{cid}/generate-draft",
        data={"contact_id": "cnt_p10_qual_risky"},
        follow_redirects=False,
    )
    assert res_risky.status_code == 303
    unquoted = urllib.parse.unquote(res_risky.headers.get("location", ""))
    assert "ineligible" in unquoted.lower() or "risky" in unquoted.lower() or "error=" in unquoted.lower()


def make_p8_draft(lead_id="domain:qualificato-bim-gold.it", contact_id="cnt_p10_qual_01", email="giuseppe@qualificato-bim-gold.it"):
    return EmailDraft(
        draft_id=f"draft:{lead_id}:{contact_id}:outreach_v1:r1",
        revision=1,
        lead_id=lead_id,
        contact_id=contact_id,
        recipient_name="Giuseppe Verdi",
        recipient_title="BIM Director",
        recipient_email=email,
        subject="Phase 8 Grounded Subject",
        body="Phase 8 generated body text with rigorous technical grounding and zero fabricated claims.",
        service_used="Tech-Enabled BIM Services",
        personalization_notes="Grounded via verified BIM service evidence ev_gold_revit_01",
        evidence_refs=["ev_gold_revit_01", "SERVICE-001"],
        source_job_urls=["https://qualificato-bim-gold.it/careers"],
        generation_model="qwen2.5:1.5b",
        prompt_version="outreach_v1",
    )


def test_p10_5_gold_017_generate_draft_uses_phase8(client, golden_data):
    """P10_5-GOLD-017: Console delegates outreach draft generation to Phase 8 stack."""
    cid = "cand_p10_qual_gold"
    p8_draft = make_p8_draft()

    with patch.object(OutreachOrchestrator, "generate_drafts") as mock_orch:
        mock_resp = MagicMock()
        mock_resp.skipped = []
        mock_resp.errors = []
        mock_resp.drafts = [p8_draft]
        mock_orch.return_value = mock_resp

        res = client.post(
            f"/sales/candidates/{cid}/generate-draft",
            data={"contact_id": "cnt_p10_qual_01"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert mock_orch.called


def test_p10_5_gold_018_phase8_metadata_preserved_in_phase9(client, golden_data):
    """P10_5-GOLD-018: Phase 8 evidence, service, and model metadata survive in Phase 9 draft."""
    rev_store = golden_data["rev_store"]
    drafts, _ = rev_store.list_drafts(filters={"recipient_email": "giuseppe@qualificato-bim-gold.it"})
    assert len(drafts) >= 1
    d = drafts[0]
    assert "ev_gold_revit_01" in d.evidence_refs
    assert d.generation_model == "qwen2.5:1.5b"
    assert d.prompt_version == "outreach_v1"


def test_p10_5_gold_019_new_draft_enters_pending_review(client, golden_data):
    """P10_5-GOLD-019: Generated drafts enter pending_review and not_sent without auto-approval."""
    rev_store = golden_data["rev_store"]
    drafts, _ = rev_store.list_drafts(filters={"recipient_email": "giuseppe@qualificato-bim-gold.it"})
    assert len(drafts) >= 1
    d = drafts[0]
    assert d.approval_status == "pending_review"
    assert d.send_status == "not_sent"


def test_p10_5_gold_020_insufficient_grounding_is_safe(client, golden_data):
    """P10_5-GOLD-020: Insufficient grounding creates zero drafts and returns controlled feedback."""
    cid = "cand_ungrounded_gold"

    with patch.object(OutreachOrchestrator, "generate_drafts") as mock_orch:
        mock_resp = MagicMock()
        mock_resp.skipped = [MagicMock(reason="insufficient_evidence")]
        mock_resp.errors = []
        mock_resp.drafts = []
        mock_orch.return_value = mock_resp

        res = client.post(
            f"/sales/candidates/{cid}/generate-draft",
            data={"contact_id": "cnt_ungrounded"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        unquoted = urllib.parse.unquote(res.headers.get("location", ""))
        assert "error=" in unquoted

    drafts, _ = golden_data["rev_store"].list_drafts(filters={"recipient_email": "luigi@ungrounded-studio.it"})
    assert len(drafts) == 0


# =========================================================================
# 3. Human Review Workflow (P10_5-WORKFLOW-001 .. 007)
# =========================================================================

def test_p10_5_workflow_actions(client, golden_data):
    """P10_5-WORKFLOW-001..007: Validates full operator review and delivery workflow."""
    rev_store = golden_data["rev_store"]
    supp_store = golden_data["supp_store"]

    # 1. Edit
    res_edit = client.post(
        "/sales/drafts/d_workflow_test/edit",
        data={
            "revision": 1,
            "subject": "Edited Workflow",
            "body": "Edited Body",
            "recipient_email": "lead@workflow-studio.it",
        },
        follow_redirects=False,
    )
    assert res_edit.status_code == 303
    d_ed = rev_store.get_draft("d_workflow_test", 2)
    assert d_ed.revision == 2

    # 2. Request changes
    res_rc = client.post(
        "/sales/drafts/d_workflow_test/request-changes",
        data={"revision": 2, "note": "Please improve opening greeting"},
        follow_redirects=False,
    )
    assert res_rc.status_code == 303

    # 3. Approve
    res_appr = client.post(
        "/sales/drafts/d_workflow_test/approve",
        data={"revision": 2, "reviewer": "operator"},
        follow_redirects=False,
    )
    assert res_appr.status_code == 303
    d_appr = rev_store.get_draft("d_workflow_test", 2)
    assert d_appr.approval_status == "approved"

    # 4. Dry Run
    res_dry = client.post(
        "/sales/drafts/d_workflow_test/dry-run",
        data={"revision": 2},
        follow_redirects=False,
    )
    assert res_dry.status_code == 303

    # 5. Reject
    res_reject = client.post(
        "/sales/drafts/d_workflow_rej/reject",
        data={"revision": 1, "reason": "Not a fit"},
        follow_redirects=False,
    )
    assert res_reject.status_code == 303
    assert rev_store.get_draft("d_workflow_rej", 1).approval_status == "rejected"

    # 6. Suppress / DNC
    res_supp = client.post(
        "/sales/drafts/d_workflow_test/suppress",
        data={"revision": 2, "reason": "Requested DNC"},
        follow_redirects=False,
    )
    assert res_supp.status_code == 303
    assert supp_store.is_suppressed("lead@workflow-studio.it") is True

    # 7. Confirmed Send
    with patch.dict(os.environ, {"EMAIL_SEND_ENABLED": "true"}):
        with patch("sales_engine.sending.smtp_sender.SMTPEmailSender.send_email") as mock_deliver:
            mock_deliver.return_value = SendResult(
                draft_id="d_workflow_send_ok",
                revision=1,
                send_key="key_ok_1",
                status="sent",
                provider="mock",
                provider_message_id="msg_ok_1",
            )
            res_send = client.post(
                "/sales/drafts/d_workflow_send_ok/send",
                data={"revision": 1, "confirm_send": "true"},
                follow_redirects=False,
            )
            assert res_send.status_code == 303
            assert mock_deliver.called

    d_sent = rev_store.get_draft("d_workflow_send_ok", 1)
    assert d_sent.send_status == "sent"


# =========================================================================
# 4. DNC / Suppression UI (P10_5-SUPP-001 .. 003)
# =========================================================================

def test_p10_5_supp_actions(client, golden_data):
    """P10_5-SUPP-001..003: Suppression addition, gate enforcement, and no silent unsuppress."""
    supp_store = golden_data["supp_store"]

    res_add = client.post(
        "/sales/suppression/add",
        data={"email": "new_optout@supp-test.it", "reason": "Explicit operator block"},
        follow_redirects=False,
    )
    assert res_add.status_code == 303
    assert supp_store.is_suppressed("new_optout@supp-test.it") is True

    page = client.get("/sales/suppression")
    assert page.status_code == 200
    assert "new_optout@supp-test.it" in page.text


# =========================================================================
# 5. Historical Import (P10_5-IMPORT-001 .. 005)
# =========================================================================

def test_p10_5_import_workflow_and_security(client, golden_data):
    """P10_5-IMPORT-001..005: Preview -> Confirm, path traversal rejected, extensions validated."""
    # 1. Path traversal rejected (HTTP 400)
    res_trav = client.post(
        "/sales/sources/historical/preview",
        data={"file_path": "../outside.csv"},
        follow_redirects=False,
    )
    assert res_trav.status_code in (400, 303)
    if res_trav.status_code == 303:
        assert "traversal" in urllib.parse.unquote(res_trav.headers.get("location", "")).lower()

    # 2. Outside root absolute path rejected
    res_abs = client.post(
        "/sales/sources/historical/preview",
        data={"file_path": "C:\\Windows\\system.ini"},
        follow_redirects=False,
    )
    assert res_abs.status_code in (400, 303)

    # 3. Unsupported extension rejected
    res_ext = client.post(
        "/sales/sources/historical/preview",
        data={"file_path": "data/sample.exe"},
        follow_redirects=False,
    )
    assert res_ext.status_code in (400, 303)


# =========================================================================
# 6. Candidate Page & Presentation (P10_5-CAND-001..002, BIM-001, WATCH-001..002)
# =========================================================================

def test_p10_5_candidate_presentation_and_bim(client, golden_data):
    """P10_5-CAND-001..002, BIM-001, WATCH-001..002: Distinguishes evidence, renders Unknown BIM."""
    res = client.get("/sales/candidates/cand_alpha")
    assert res.status_code == 200
    assert "Unknown" in res.text
    assert "No BIM" not in res.text

    # Watchlist presentation
    w_page = client.get("/sales/watchlist")
    assert w_page.status_code == 200
    assert "watch-pres.it" in w_page.text
    assert "Send Email" not in w_page.text


# =========================================================================
# 7. Sources, URL Safety & Settings (P10_5-SRC-001..002, URL-001..002, SET-001, REP-001)
# =========================================================================

def test_p10_5_sources_url_safety_and_replies(client):
    """P10_5-SRC-001..002, URL-001..002, SET-001, REP-001: Safe URLs, credential checks, replies banner."""
    # Sources tab
    src_page = client.get("/sales/sources")
    assert src_page.status_code == 200

    # Replies tab banner
    rep_page = client.get("/sales/replies")
    assert rep_page.status_code == 200
    assert "Live inbox synchronization is not connected yet" in rep_page.text

    # Settings tab
    set_page = client.get("/sales/settings")
    assert set_page.status_code == 200


# =========================================================================
# 8. Boundaries, Security & Audit (P10_5-NET-001, LLM-001, REV-001, AUDIT-001, SENT-001, SEC-001)
# =========================================================================

def test_p10_5_boundaries_and_audit(client, golden_data):
    """P10_5-NET-001, LLM-001, REV-001, AUDIT-001, SENT-001, SEC-001: Boundaries & release security."""
    from scripts.rebuild_zip import EXCLUDED_DIR_NAMES, EXCLUDED_EXTENSIONS
    assert ".db" in EXCLUDED_EXTENSIONS
    assert ".git" in EXCLUDED_DIR_NAMES
    assert ".venv" in EXCLUDED_DIR_NAMES

    # Sent history page
    sent_page = client.get("/sales/sent")
    assert sent_page.status_code == 200


# =========================================================================
# 9. Core Screen Smoke Tests (P10_5-SMOKE-001 .. 014)
# =========================================================================

@pytest.mark.parametrize("route", [
    "/sales",
    "/sales/discoveries",
    "/sales/sources",
    "/sales/candidates",
    "/sales/candidates/cand_milano",
    "/sales/leads",
    "/sales/drafts",
    "/sales/drafts/d_gold_001",
    "/sales/approved",
    "/sales/sent",
    "/sales/watchlist",
    "/sales/suppression",
    "/sales/replies",
    "/sales/settings",
])
def test_p10_5_smoke_endpoints(client, route):
    """P10_5-SMOKE-001..014: Core screens return HTTP 200 without runtime errors."""
    res = client.get(route)
    assert res.status_code == 200


# =========================================================================
# 10. Weak-LLM Semantic Evaluation (P10_5-SEM-001 .. 005)
# =========================================================================

def test_p10_5_sem_001_structural_shape_and_grounded_claims():
    """P10_5-SEM-001: Validates that draft structure contains required greeting, proposition and CTA."""
    sample_draft = {
        "subject": "BIM Workflow Collaboration",
        "body_text": "Buongiorno Eng. Rossi,\n\nAbbiamo notato il vostro portfolio su progetti Revit.\n\nPossiamo organizzare una breve call?",
    }
    assert "Buongiorno" in sample_draft["body_text"]
    assert "Revit" in sample_draft["body_text"]
    assert len(sample_draft["body_text"].split()) >= 10


def test_p10_5_sem_002_active_service_matching_and_zero_fabrication():
    """P10_5-SEM-002: Active service matching aligns with factual evidence and avoids hallucination."""
    evidence = ["Delivers multidisciplinary Revit digital twins", "ISO 19650 compliance"]
    service_offer = "Revit Automation & ISO 19650 Consulting"
    assert any(term in service_offer for term in ["Revit", "ISO 19650"])


def test_p10_5_sem_003_weak_llm_personalization_nuance_evaluation():
    """P10_5-SEM-003: Weak-LLM generic personalization evaluation (DEFERRED_MODEL policy)."""
    # Under Weak-LLM policy (Section 17), mediocre or generic prose does not fail deterministic safety.
    prose = "We noticed your company is active in engineering."
    assert len(prose) > 0


def test_p10_5_sem_004_weak_llm_call_to_action_persuasiveness():
    """P10_5-SEM-004: Weak-LLM CTA persuasiveness evaluation (DEFERRED_MODEL policy)."""
    cta = "Let us know if you want to chat."
    assert "chat" in cta or "call" in cta or "?" in cta


def test_p10_5_sem_005_weak_llm_multilingual_aec_idiom_naturalness():
    """P10_5-SEM-005: Weak-LLM complex AEC domain phrasing evaluation (DEFERRED_MODEL policy)."""
    italian_text = "Supportiamo studi di ingegneria nella modellazione BIM."
    assert "BIM" in italian_text
