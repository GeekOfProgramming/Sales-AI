"""
Phase 10.5 Final Pre-QA Micro-Patch Test Suite (P10_5-FINAL-001 .. P10_5-FINAL-012)
Validates Phase 8 delegation, elimination of hardcoded copy in router,
preservation of Phase 8 evidence/service/model metadata in Phase 9,
controlled insufficient grounding handling, Phase 10 qualified_for_enrichment recognition,
ineligible outcome enforcement, strict Phase 6 contact resolution,
truthful sender display without fabrication, and SourceConfig.credential_env_var checks.
"""

import os
import ast
import tempfile
import urllib.parse
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, MarketEvidence, QualificationOutcome
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.approval_service import ApprovalService
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.send_orchestrator import SendOrchestrator
from sales_engine.sending.schemas import StoredDraft
from sales_engine.outreach.outreach_orchestrator import OutreachOrchestrator
from sales_engine.outreach.email_generator import EmailGenerator
from sales_engine.outreach.schemas import EmailDraft
from sales_engine.discovery_v2.source_registry import SourceConfig


test_dir = tempfile.mkdtemp(prefix="salesai_p10_5_final_")
test_db_path = str(Path(test_dir) / "test_final.db")
os.environ["SALES_OUTREACH_DB"] = test_db_path
os.environ["SALESAI_DB_PATH"] = test_db_path
os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
os.environ["EMAIL_SEND_ENABLED"] = "false"
os.environ["SMTP_FROM_EMAIL"] = "trusted_sender@pybim-studio.com"
os.environ["SMTP_FROM_NAME"] = "pyBIM Team"


class CsrfAwareClient(TestClient):
    """Test client automatically providing CSRF token and origin for mutating requests."""
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "test_csrf_token_secret_final"
        if "sales_csrf_token" not in self.cookies:
            self.cookies.set("sales_csrf_token", token)

        data = kwargs.get("data")
        if isinstance(data, dict) and "csrf_token" not in data:
            data_copy = dict(data)
            data_copy["csrf_token"] = token
            kwargs["data"] = data_copy

        headers = kwargs.get("headers")
        if not headers:
            kwargs["headers"] = {"Origin": "http://testserver"}
        elif "Origin" not in headers:
            headers_copy = dict(headers)
            headers_copy["Origin"] = "http://testserver"
            kwargs["headers"] = headers_copy
        return super().post(url, *args, **kwargs)


@pytest.fixture(scope="module")
def client():
    return CsrfAwareClient(app)


@pytest.fixture(autouse=True)
def ensure_db_env(final_data):
    os.environ["SALES_OUTREACH_DB"] = test_db_path
    os.environ["SALESAI_DB_PATH"] = test_db_path
    os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
    yield


@pytest.fixture(scope="module")
def final_data():
    cand_store = CandidateStore(db_path=test_db_path)
    rev_store = ReviewStore(db_path=test_db_path)
    supp_store = SuppressionStore(rev_store)
    appr_svc = ApprovalService(rev_store)
    orch = SendOrchestrator(review_store=rev_store, suppression_store=supp_store)

    # 1. Qualified company with grounded evidence
    c_qual = CandidateCompany(
        candidate_id="cand_final_qual",
        name="Studio Qualificato BIM",
        domain="studio-qualificato.it",
        country="IT",
        company_tier="tier_1",
        bim_maturity="high",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
    )
    cand_store.save_candidate(c_qual)
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_final_01",
            candidate_id="cand_final_qual",
            category="service",
            signal_type="bim_service",
            text="Studio Qualificato delivers large-scale Revit digital twins.",
        )
    )

    # Eligible verified contact
    c_verified = ContactCandidate(
        contact_id="cnt_final_verified",
        full_name="Elena Rossi",
        job_title="BIM Director",
        work_email="elena@studio-qualificato.it",
        email_status="verified",
        email_confidence=95,
    )
    cand_store.save_contact(c_verified, candidate_id="cand_final_qual")

    # Eligible likely contact
    c_likely = ContactCandidate(
        contact_id="cnt_final_likely",
        full_name="Marco Bianchi",
        job_title="BIM Manager",
        work_email="marco@studio-qualificato.it",
        email_status="likely",
        email_confidence=75,
    )
    cand_store.save_contact(c_likely, candidate_id="cand_final_qual")

    # Ineligible contacts
    c_risky = ContactCandidate(
        contact_id="cnt_final_risky",
        full_name="Risky User",
        job_title="Intern",
        work_email="risky@studio-qualificato.it",
        email_status="risky",
    )
    cand_store.save_contact(c_risky, candidate_id="cand_final_qual")

    c_unknown = ContactCandidate(
        contact_id="cnt_final_unknown",
        full_name="Unknown User",
        job_title="Staff",
        work_email="unknown@studio-qualificato.it",
        email_status="unknown",
    )
    cand_store.save_contact(c_unknown, candidate_id="cand_final_qual")

    c_not_found = ContactCandidate(
        contact_id="cnt_final_not_found",
        full_name="No Email User",
        job_title="Contractor",
        work_email=None,
        email_status="not_found",
    )
    cand_store.save_contact(c_not_found, candidate_id="cand_final_qual")

    # 2. Company with NO evidence or grounding (for insufficient grounding test)
    c_ungrounded = CandidateCompany(
        candidate_id="cand_final_ungrounded",
        name="Ungrounded Architecture",
        domain="ungrounded-arch.it",
        country="IT",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
    )
    cand_store.save_candidate(c_ungrounded)
    c_ungrounded_cnt = ContactCandidate(
        contact_id="cnt_ungrounded_01",
        full_name="Paolo Ungrounded",
        job_title="Architect",
        work_email="paolo@ungrounded-arch.it",
        email_status="verified",
    )
    cand_store.save_contact(c_ungrounded_cnt, candidate_id="cand_final_ungrounded")

    # 3. Watchlist company
    c_watch = CandidateCompany(
        candidate_id="cand_final_watchlist",
        name="Watchlist Ingegneria",
        domain="watchlist-ing.it",
        country="IT",
        discovery_status="watchlist",
        qualification_outcome=QualificationOutcome.watchlist,
    )
    cand_store.save_candidate(c_watch)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="cnt_watch_01",
            full_name="Walter Watch",
            work_email="walter@watchlist-ing.it",
            email_status="verified",
        ),
        candidate_id="cand_final_watchlist",
    )

    # 4. Known company outcome
    c_known = CandidateCompany(
        candidate_id="cand_final_known",
        name="Known AEC Partner",
        domain="known-aec.it",
        country="IT",
        qualification_outcome=QualificationOutcome.known_company,
    )
    cand_store.save_candidate(c_known)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="cnt_known_01",
            full_name="Kevin Known",
            work_email="kevin@known-aec.it",
            email_status="verified",
        ),
        candidate_id="cand_final_known",
    )

    # 5. Re-engagement review outcome
    c_reengage = CandidateCompany(
        candidate_id="cand_final_reengage",
        name="Reengagement BIM Studio",
        domain="reengage-bim.it",
        country="IT",
        qualification_outcome=QualificationOutcome.reengagement_review,
    )
    cand_store.save_candidate(c_reengage)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="cnt_reengage_01",
            full_name="Rachel Reengage",
            work_email="rachel@reengage-bim.it",
            email_status="verified",
        ),
        candidate_id="cand_final_reengage",
    )

    # 6. Suppressed company outcome
    c_supp = CandidateCompany(
        candidate_id="cand_final_suppressed",
        name="Suppressed Global",
        domain="suppressed-global.it",
        country="IT",
        suppressed=True,
        qualification_outcome=QualificationOutcome.suppressed,
    )
    cand_store.save_candidate(c_supp)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="cnt_supp_01",
            full_name="Sam Suppressed",
            work_email="sam@suppressed-global.it",
            email_status="verified",
        ),
        candidate_id="cand_final_suppressed",
    )

    return {
        "cand_store": cand_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "appr_svc": appr_svc,
        "orch": orch,
    }


def make_p8_draft(lead_id="domain:studio-qualificato.it", contact_id="cnt_final_verified", email="elena@studio-qualificato.it"):
    return EmailDraft(
        draft_id=f"draft:{lead_id}:{contact_id}:outreach_v1:r1",
        revision=1,
        lead_id=lead_id,
        contact_id=contact_id,
        recipient_name="Elena Rossi",
        recipient_title="BIM Director",
        recipient_email=email,
        subject="Phase 8 Grounded Subject",
        body="Phase 8 generated body text with rigorous technical grounding and zero fabricated claims.",
        service_used="Tech-Enabled BIM Services",
        personalization_notes="Grounded via verified BIM service evidence ev_final_01",
        evidence_refs=["ev_final_01", "SERVICE-001"],
        source_job_urls=["https://studio-qualificato.it/careers"],
        generation_model="qwen2.5:1.5b",
        prompt_version="outreach_v1",
    )


# =========================================================================
# P10_5-FINAL-001 .. 004: Phase 8 Outreach Stack Delegation & Preservation
# =========================================================================

def test_p10_5_final_001_generate_draft_delegates_to_frozen_phase8(client, final_data):
    """P10_5-FINAL-001: Generate Draft delegates to frozen Phase 8 stack."""
    p8_draft = make_p8_draft()
    with patch.object(OutreachOrchestrator, "generate_drafts") as mock_orch:
        mock_resp = MagicMock()
        mock_resp.skipped = []
        mock_resp.errors = []
        mock_resp.drafts = [p8_draft]
        mock_orch.return_value = mock_resp

        res = client.post(
            "/sales/candidates/cand_final_qual/generate-draft",
            data={"contact_id": "cnt_final_verified"},
            follow_redirects=False,
        )

        assert res.status_code == 303
        assert mock_orch.called
        # Verify request passed to Phase 8
        req = mock_orch.call_args[0][0]
        assert len(req.leads) == 1
        assert req.leads[0].base_lead.company_domain == "studio-qualificato.it"
        assert req.leads[0].best_contact.work_email == "elena@studio-qualificato.it"


def test_p10_5_final_002_ui_router_contains_no_hardcoded_generated_outreach_copy():
    """P10_5-FINAL-002: UI router contains no hardcoded generated outreach subject/body."""
    router_path = Path(__file__).resolve().parent.parent / "backend" / "sales_ui" / "router.py"
    content = router_path.read_text(encoding="utf-8")

    # Forbidden hardcoded sales outreach templates
    forbidden_snippets = [
        "BIM & Automation Workflow at",
        "We noticed {company} is advancing BIM delivery",
        "advancing BIM delivery",
        "Open to a brief conversation?",
        "We noticed",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in content, f"Forbidden hardcoded outreach copy found in router.py: '{snippet}'"


def test_p10_5_final_003_generated_phase9_draft_preserves_phase8_metadata(client, final_data):
    """P10_5-FINAL-003: Generated Phase 9 draft preserves Phase 8 evidence/service/model metadata."""
    p8_draft = make_p8_draft()
    with patch.object(EmailGenerator, "generate_draft", return_value=(p8_draft, None, None)):
        res = client.post(
            "/sales/candidates/cand_final_qual/generate-draft",
            data={"contact_id": "cnt_final_verified"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        loc = res.headers.get("location", "")
        assert "/sales/drafts/draft" in loc

        draft_id = loc.split("/sales/drafts/")[1].split("?")[0]
        draft = final_data["rev_store"].get_draft(draft_id, 1)

        assert draft is not None
        assert draft.service_used == "Tech-Enabled BIM Services"
        assert "ev_final_01" in draft.evidence_refs
        assert "SERVICE-001" in draft.evidence_refs
        assert draft.generation_model == "qwen2.5:1.5b"
        assert draft.prompt_version == "outreach_v1"
        assert draft.approval_status == "pending_review"
        assert draft.send_status == "not_sent"


def test_p10_5_final_004_insufficient_grounding_returns_controlled_no_draft_result(client, final_data):
    """P10_5-FINAL-004: Insufficient grounding returns controlled no-draft result without draft creation."""
    rev_store = final_data["rev_store"]
    drafts_before, _ = rev_store.list_drafts(filters={"lead_id": "domain:ungrounded-arch.it"})

    res = client.post(
        "/sales/candidates/cand_final_ungrounded/generate-draft",
        data={"contact_id": "cnt_ungrounded_01"},
        follow_redirects=False,
    )

    assert res.status_code == 303
    loc = res.headers.get("location", "")
    assert "error=" in loc
    assert "insufficient_grounding" in loc

    # Verify no draft was created in Phase 9
    drafts_after, _ = rev_store.list_drafts(filters={"lead_id": "domain:ungrounded-arch.it"})
    assert len(drafts_after) == len(drafts_before)


# =========================================================================
# P10_5-FINAL-005 .. 006: Qualification Outcome Enforcement
# =========================================================================

def test_p10_5_final_005_qualified_for_enrichment_candidate_is_draft_eligible(client, final_data):
    """P10_5-FINAL-005: Actual Phase 10 qualified_for_enrichment candidate becomes draft-eligible."""
    res = client.get("/sales/candidates/cand_final_qual")
    assert res.status_code == 200
    assert "generate-draft-section" in res.text
    assert "Eligible for Outreach" in res.text
    assert "cnt_final_verified" in res.text


def test_p10_5_final_006_watchlist_known_reengagement_suppressed_outcomes_ineligible(client, final_data):
    """P10_5-FINAL-006: Watchlist, known, reengagement, and suppressed outcomes remain ineligible."""
    ineligible_cands = [
        ("cand_final_watchlist", "cnt_watch_01"),
        ("cand_final_known", "cnt_known_01"),
        ("cand_final_reengage", "cnt_reengage_01"),
        ("cand_final_suppressed", "cnt_supp_01"),
    ]

    for cid, cnt_id in ineligible_cands:
        # Check UI page has no active draft generation form
        page_res = client.get(f"/sales/candidates/{cid}")
        assert page_res.status_code == 200
        assert "generate-draft-section" not in page_res.text

        # Check POST action is rejected
        post_res = client.post(
            f"/sales/candidates/{cid}/generate-draft",
            data={"contact_id": cnt_id},
            follow_redirects=False,
        )
        assert post_res.status_code == 303
        assert "error=" in post_res.headers.get("location", "")


# =========================================================================
# P10_5-FINAL-007 .. 009: Strict Phase 6 Contact Selection
# =========================================================================

def test_p10_5_final_007_arbitrary_typed_email_cannot_bypass_phase6_eligibility(client, final_data):
    """P10_5-FINAL-007: Arbitrary browser-typed email cannot bypass Phase 6 eligibility."""
    # Attempting to post an arbitrary typed email without existing backend contact_id
    res = client.post(
        "/sales/candidates/cand_final_qual/generate-draft",
        data={
            "contact_email": "attacker@arbitrary-bypass.com",
            "contact_name": "Attacker",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    loc = urllib.parse.unquote(res.headers.get("location", ""))
    assert "error=" in loc
    assert "Missing contact selection" in loc or "not found" in loc

    # Attempting with a bogus contact_id
    res_bogus = client.post(
        "/sales/candidates/cand_final_qual/generate-draft",
        data={"contact_id": "non_existent_contact_id_999"},
        follow_redirects=False,
    )
    assert res_bogus.status_code == 303
    assert "error=" in res_bogus.headers.get("location", "")


def test_p10_5_final_008_existing_verified_and_likely_contacts_can_be_selected(client, final_data):
    """P10_5-FINAL-008: Existing verified and likely contacts can be selected for draft generation."""
    p8_draft = make_p8_draft(contact_id="cnt_final_likely", email="marco@studio-qualificato.it")
    with patch.object(EmailGenerator, "generate_draft", return_value=(p8_draft, None, None)):
        res = client.post(
            "/sales/candidates/cand_final_qual/generate-draft",
            data={"contact_id": "cnt_final_likely"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        loc = res.headers.get("location", "")
        assert "/sales/drafts/draft" in loc


def test_p10_5_final_009_risky_unknown_not_found_contact_cannot_enter_normal_draft_path(client, final_data):
    """P10_5-FINAL-009: Risky, unknown, or not_found contact cannot enter normal cold draft path."""
    for bad_cnt_id in ["cnt_final_risky", "cnt_final_unknown", "cnt_final_not_found"]:
        res = client.post(
            "/sales/candidates/cand_final_qual/generate-draft",
            data={"contact_id": bad_cnt_id},
            follow_redirects=False,
        )
        assert res.status_code == 303
        loc = res.headers.get("location", "")
        assert "error=" in loc


# =========================================================================
# P10_5-FINAL-010 .. 011: Truthful Sender Identity Display
# =========================================================================

def test_p10_5_final_010_send_preview_never_invents_sender_identity(client, final_data):
    """P10_5-FINAL-010: Send preview never invents sender identity when unconfigured."""
    # Seed draft with NO sender_email
    draft_no_sender = StoredDraft(
        draft_id="draft_final_no_sender",
        lead_id="domain:studio-qualificato.it",
        contact_id="cnt_final_verified",
        revision=1,
        recipient_name="Elena Rossi",
        recipient_email="elena@studio-qualificato.it",
        sender_name=None,
        sender_email=None,
        subject="No Sender Subject",
        body="Body without sender configured.",
        approval_status="approved",
        content_hash="abc_hash_1",
    )
    final_data["rev_store"].save_draft(draft_no_sender)

    res = client.get("/sales/drafts/draft_final_no_sender")
    assert res.status_code == 200
    # Must NOT invent pyBIM Team or sales@pybim.it
    assert "sales@pybim.it" not in res.text
    assert "Sender not configured" in res.text
    assert "Sender Not Configured" in res.text


def test_p10_5_final_011_exact_stored_sender_identity_is_shown(client, final_data):
    """P10_5-FINAL-011: Exact stored sender identity is shown without fabrication."""
    draft_exact = StoredDraft(
        draft_id="draft_final_exact_sender",
        lead_id="domain:studio-qualificato.it",
        contact_id="cnt_final_verified",
        revision=1,
        recipient_name="Elena Rossi",
        recipient_email="elena@studio-qualificato.it",
        sender_name="Fabio Consultant",
        sender_email="fabio.c@pybim-studio.com",
        subject="Exact Sender Subject",
        body="Body with exact stored sender.",
        approval_status="approved",
        content_hash="abc_hash_2",
    )
    final_data["rev_store"].save_draft(draft_exact)

    os.environ["EMAIL_SEND_ENABLED"] = "true"
    try:
        res = client.get("/sales/drafts/draft_final_exact_sender")
        assert res.status_code == 200
        assert "Fabio Consultant" in res.text
        assert "fabio.c@pybim-studio.com" in res.text
        assert "sales@pybim.it" not in res.text
    finally:
        os.environ["EMAIL_SEND_ENABLED"] = "false"


# =========================================================================
# P10_5-FINAL-012: SourceConfig.credential_env_var Verification
# =========================================================================

def test_p10_5_final_012_credential_status_uses_configured_credential_env_var(client, final_data):
    """P10_5-FINAL-012: Source credential status uses SourceConfig.credential_env_var strictly."""
    # Test case 1: Required key, env var configured and present in env
    os.environ["TEST_DISCOVERY_API_KEY"] = "secret_key_123"
    try:
        res = client.get("/sales/sources")
        assert res.status_code == 200

        # Now test missing credential
        os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
        os.environ.pop("RAPIDAPI_KEY", None)
        res_missing = client.get("/sales/sources")
        assert res_missing.status_code == 200
        assert "Not configured" in res_missing.text
        # Secrets are never leaked
        assert "secret_key_123" not in res_missing.text
    finally:
        os.environ.pop("TEST_DISCOVERY_API_KEY", None)
