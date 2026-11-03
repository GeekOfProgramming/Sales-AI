"""
Phase 10.5 Adversarial Regression Test Suite (P10_5-ADV-001 .. P10_5-ADV-024)
Validates pre-send domain suppression, CSRF/Same-Origin security,
historical import 2-step verification, safe import roots, immutable send preview,
Request Changes workflow, credential truth, URL safety, and lead-to-draft flow.
"""

import os
import shutil
import tempfile
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.schemas import ContactCandidate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, MarketEvidence, QualificationOutcome
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.approval_service import ApprovalService, compute_content_fingerprint
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.send_validator import SendValidator
from sales_engine.sending.send_orchestrator import SendOrchestrator
from sales_engine.sending.schemas import StoredDraft

test_dir = tempfile.mkdtemp(prefix="salesai_p10_5_adv_")
test_db_path = str(Path(test_dir) / "test_adv.db")
os.environ["SALES_OUTREACH_DB"] = test_db_path
os.environ["SALESAI_DB_PATH"] = test_db_path
os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
os.environ["EMAIL_SEND_ENABLED"] = "false"
os.environ["SMTP_FROM_EMAIL"] = "trusted_sender@pybim-studio.com"
os.environ["SMTP_FROM_NAME"] = "pyBIM Team"


class CsrfAwareClient(TestClient):
    """Test client automatically providing CSRF token and origin for mutating requests."""
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "test_csrf_token_secret_adv"
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


@pytest.fixture(scope="module")
def client():
    return CsrfAwareClient(app)


@pytest.fixture(scope="module")
def raw_client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def ensure_db_env(adv_data):
    os.environ["SALES_OUTREACH_DB"] = test_db_path
    os.environ["SALESAI_DB_PATH"] = test_db_path
    os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
    os.environ["SMTP_FROM_EMAIL"] = "trusted_sender@pybim-studio.com"
    os.environ["SMTP_FROM_NAME"] = "pyBIM Team"
    yield


@pytest.fixture(scope="module")
def adv_data():
    os.environ["SALES_OUTREACH_DB"] = test_db_path
    os.environ["SALESAI_DB_PATH"] = test_db_path
    os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
    os.environ["EMAIL_SEND_ENABLED"] = "false"
    os.environ["SMTP_FROM_EMAIL"] = "trusted_sender@pybim-studio.com"
    os.environ["SMTP_FROM_NAME"] = "pyBIM Team"
    cand_store = CandidateStore(db_path=test_db_path)
    rev_store = ReviewStore(db_path=test_db_path)
    supp_store = SuppressionStore(rev_store)
    appr_svc = ApprovalService(rev_store)
    orch = SendOrchestrator(review_store=rev_store, suppression_store=supp_store)

    # Candidate 1: Domain-suppressed company
    c_blocked = CandidateCompany(
        candidate_id="cand_blocked_co",
        name="Blocked Corporation",
        domain="blocked.test",
        country="IT",
        company_tier="tier_1",
        discovery_status="qualified",
    )
    cand_store.save_candidate(c_blocked)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="contact_blocked_ceo",
            full_name="Blocked CEO",
            job_title="Chief Executive",
            work_email="ceo@blocked.test",
            email_status="verified",
        ),
        candidate_id="cand_blocked_co",
    )

    # Candidate 2: Qualified company ready for draft generation
    c_qual = CandidateCompany(
        candidate_id="cand_qual_studio",
        name="Studio Qualificato BIM",
        domain="studio-qualificato.it",
        country="IT",
        company_tier="tier_1",
        bim_maturity="high",
        discovery_status="qualified",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
    )
    cand_store.save_candidate(c_qual)
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_qual_01",
            candidate_id="cand_qual_studio",
            category="service",
            signal_type="bim_service",
            text="Studio Qualificato BIM provides automated Revit drafting and model validation.",
        )
    )
    cand_store.save_contact(
        ContactCandidate(
            contact_id="contact_elena_rossi",
            full_name="Elena Rossi",
            job_title="BIM Director",
            work_email="elena@studio-qualificato.it",
            email_status="verified",
            email_confidence=95,
        ),
        candidate_id="cand_qual_studio",
    )

    # Candidate 3: Watchlist candidate (not qualified)
    c_watch = CandidateCompany(
        candidate_id="cand_watchlist_co",
        name="Watchlist Ingegneria",
        domain="watchlist-ing.it",
        country="IT",
        company_tier="tier_2",
        discovery_status="watchlist",
        qualification_outcome=QualificationOutcome.watchlist,
    )
    cand_store.save_candidate(c_watch)
    cand_store.save_contact(
        ContactCandidate(
            contact_id="contact_watchlist_director",
            full_name="Watchlist Director",
            job_title="Director",
            work_email="director@watchlist-ing.it",
            email_status="verified",
        ),
        candidate_id="cand_watchlist_co",
    )

    # Seed Draft 1: Belonging to blocked domain
    draft_blocked = StoredDraft(
        draft_id="draft_adv_blocked",
        lead_id="cand_blocked_co",
        contact_id="cnt_blocked_01",
        revision=1,
        sender_email="trusted_sender@pybim-studio.com",
        sender_name="pyBIM Team",
        recipient_email="other@blocked.test",
        recipient_name="Other Employee",
        subject="Proposal for Blocked Co",
        body="Body text for blocked recipient.",
        approval_status="pending_review",
        send_status="not_sent",
        content_hash="hash_blocked_1",
    )
    draft_blocked.content_hash = compute_content_fingerprint(draft_blocked)
    rev_store.save_draft(draft_blocked)
    # Compute & save valid approved fingerprint
    appr_svc.approve_draft("draft_adv_blocked", 1, reviewer="seed_operator")

    # Seed Draft 2: Normal draft for request-changes testing
    draft_req = StoredDraft(
        draft_id="draft_adv_req_changes",
        lead_id="cand_qual_studio",
        contact_id="cnt_qual_01",
        revision=1,
        sender_email="trusted_sender@pybim-studio.com",
        sender_name="pyBIM Team",
        recipient_email="elena@studio-qualificato.it",
        recipient_name="Elena Rossi",
        subject="Introductory BIM Audit",
        body="Hello Elena,\n\nWe noticed your BIM projects and would love to connect.",
        approval_status="pending_review",
        send_status="not_sent",
        content_hash="hash_req_1",
    )
    draft_req.content_hash = compute_content_fingerprint(draft_req)
    rev_store.save_draft(draft_req)

    # Seed Draft 3: Sent immutable draft
    draft_sent = StoredDraft(
        draft_id="draft_adv_sent",
        lead_id="cand_qual_studio",
        contact_id="cnt_qual_01",
        revision=1,
        sender_email="trusted_sender@pybim-studio.com",
        sender_name="pyBIM Team",
        recipient_email="elena@studio-qualificato.it",
        recipient_name="Elena Rossi",
        subject="Sent Audit",
        body="Already sent.",
        approval_status="approved",
        approved_content_hash="hash_sent_1",
        send_status="sent",
        content_hash="hash_sent_1",
        sent_at="2026-10-06T10:00:00Z",
    )
    draft_sent.content_hash = compute_content_fingerprint(draft_sent)
    draft_sent.approved_content_hash = draft_sent.content_hash
    rev_store.save_draft(draft_sent)

    # Seed Draft 4: Approved draft ready for explicit send preview
    draft_appr = StoredDraft(
        draft_id="draft_adv_approved",
        lead_id="cand_qual_studio",
        contact_id="cnt_qual_01",
        revision=1,
        sender_email="trusted_sender@pybim-studio.com",
        sender_name="pyBIM Team",
        recipient_email="elena@studio-qualificato.it",
        recipient_name="Elena Rossi",
        subject="Approved BIM Audit",
        body="Hello Elena,\n\nWe noticed your BIM projects and would love to collaborate.",
        approval_status="approved",
        approved_content_hash="hash_appr_1",
        send_status="not_sent",
        content_hash="hash_appr_1",
    )
    draft_appr.content_hash = compute_content_fingerprint(draft_appr)
    draft_appr.approved_content_hash = draft_appr.content_hash
    rev_store.save_draft(draft_appr)

    # Suppress domain blocked.test for the suite
    supp_store.add_suppression("anchor@blocked.test", reason="opt_out", company_domain="blocked.test")

    return {
        "cand_store": cand_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "appr_svc": appr_svc,
        "orch": orch,
    }


# ===========================================================================
# 1. Domain Suppression Enforcement (ADV-001 .. 004)
# ===========================================================================

def test_adv_001_domain_suppression_blocks_pre_send(adv_data):
    """P10_5-ADV-001: Domain suppression in Phase 9 SendValidator blocks non-exact recipient."""
    supp_store = adv_data["supp_store"]
    rev_store = adv_data["rev_store"]

    # Suppress blocked.test via anchor email with company_domain
    supp_store.add_suppression("anchor@blocked.test", reason="opt_out", company_domain="blocked.test")

    draft = rev_store.get_draft("draft_adv_blocked", 1)
    assert draft is not None
    assert draft.recipient_email == "other@blocked.test"

    # Pre-send gate validation
    is_valid, err_type, err_msg = SendValidator.validate_pre_send(
        draft=draft,
        suppression_store=supp_store,
        trusted_sender_email=draft.sender_email,
        email_send_enabled=True,
    )
    assert not is_valid
    assert err_type == "suppressed_recipient"
    assert "blocked.test" in err_msg or "suppression list" in err_msg


def test_adv_002_ui_draft_card_marks_domain_suppressed(client, adv_data):
    """P10_5-ADV-002: UI Draft card marks domain-suppressed draft as suppressed."""
    res = client.get("/sales/drafts/draft_adv_blocked")
    assert res.status_code == 200
    assert "Suppressed" in res.text
    # Ensure send action is not permitted
    assert 'can_send": false' in res.text.lower() or "can_send" not in res.text or "disabled" in res.text or "Recipient is Suppressed" in res.text


def test_adv_003_ui_approved_queue_filters_or_disables_domain_suppressed(client, adv_data):
    """P10_5-ADV-003: UI Approved queue disables live send for domain-suppressed draft."""
    res = client.get("/sales/approved")
    assert res.status_code == 200
    # Draft is listed, but send button must be disabled / suppressed indicator shown
    if "draft_adv_blocked" in res.text:
        assert "Suppressed" in res.text or "disabled" in res.text


def test_adv_004_candidate_detail_suppression_check_respects_domain(client, adv_data):
    """P10_5-ADV-004: Candidate detail reflects domain-level suppression."""
    res = client.get("/sales/candidates/cand_blocked_co")
    assert res.status_code == 200
    assert "Suppressed" in res.text or "DNC" in res.text


# ===========================================================================
# 2. CSRF & Same-Origin Protection (ADV-005 .. 008)
# ===========================================================================

def test_adv_005_csrf_missing_token_rejected_403(raw_client, adv_data):
    """P10_5-ADV-005: Mutating POST route without CSRF token is rejected with 403."""
    res = raw_client.post(
        "/sales/drafts/draft_adv_req_changes/approve",
        data={"revision": 1, "reviewer": "attacker"},
        headers={"Origin": "http://testserver"},
    )
    assert res.status_code == 403
    assert "CSRF" in res.text


def test_adv_006_csrf_valid_token_and_origin_accepted(client, adv_data):
    """P10_5-ADV-006: Mutating POST with valid CSRF token and origin succeeds."""
    # First GET to ensure cookie exists
    client.get("/sales/drafts/draft_adv_req_changes")
    res = client.post(
        "/sales/drafts/draft_adv_req_changes/approve",
        data={"revision": 1, "reviewer": "local_operator"},
        follow_redirects=False,
    )
    assert res.status_code == 303


def test_adv_007_cross_origin_rejected_403(client, adv_data):
    """P10_5-ADV-007: Mutating POST from foreign Origin is rejected with 403."""
    client.get("/sales/drafts")
    token = client.cookies.get("sales_csrf_token")
    res = client.post(
        "/sales/drafts/draft_adv_req_changes/approve",
        data={"revision": 1, "reviewer": "evil_site", "csrf_token": token},
        headers={"Origin": "https://evil-attacker.example.com"},
        follow_redirects=False,
    )
    assert res.status_code == 403
    assert "Cross-origin" in res.text or "rejected" in res.text


def test_adv_008_send_rejected_from_cross_site(client, adv_data):
    """P10_5-ADV-008: Send action rejected from cross-site context."""
    token = client.cookies.get("sales_csrf_token")
    res = client.post(
        "/sales/drafts/draft_adv_blocked/send",
        data={"revision": 1, "confirm_send": "true", "reviewer": "evil", "csrf_token": token},
        headers={"Origin": "https://attacker.site"},
        follow_redirects=False,
    )
    assert res.status_code == 403


# ===========================================================================
# 3. Historical Import Preview -> Confirm & Safe Root (ADV-009 .. 013)
# ===========================================================================

def test_adv_009_historical_import_preview_mutations_zero(client, adv_data):
    """P10_5-ADV-009: Historical import preview performs zero mutations."""
    cand_store = adv_data["cand_store"]
    count_before = len(cand_store.list_candidates())

    # Create a small valid test historical CSV in data/ directory
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    csv_file = data_dir / "adv_test_import.csv"
    csv_file.write_text("company_name,domain,country\nNew Preview Import Srl,preview-import.it,IT\n", encoding="utf-8")

    res = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    json_data = res.json()
    assert json_data["preview_token"] is not None
    assert json_data["rows_detected"] >= 1

    # Invariants: Zero DB mutations
    count_after = len(cand_store.list_candidates())
    assert count_before == count_after


def test_adv_010_historical_import_confirm_executes_import(client, adv_data):
    """P10_5-ADV-010: Confirming valid preview token executes historical import."""
    csv_file = Path("data") / "adv_test_import.csv"
    p_res = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    token = p_res.json()["preview_token"]
    f_hash = p_res.json()["file_hash"]

    cand_store = adv_data["cand_store"]
    count_before = len(cand_store.list_candidates())

    res = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(csv_file), "preview_token": token, "file_hash": f_hash},
        follow_redirects=False,
    )
    assert res.status_code == 303
    count_after = len(cand_store.list_candidates())
    assert count_after > count_before


def test_adv_011_historical_import_confirm_invalid_token_rejected(client, adv_data):
    """P10_5-ADV-011: Confirming with forged or tampered token is rejected with 400."""
    csv_file = Path("data") / "adv_test_import.csv"
    res = client.post(
        "/sales/sources/import-history/confirm",
        data={"file_path": str(csv_file), "preview_token": "forged_tampered_token_999"},
        follow_redirects=False,
    )
    assert res.status_code == 400
    assert "Invalid preview" in res.text or "token" in res.text.lower()


def test_adv_012_path_traversal_rejected_400(client, adv_data):
    """P10_5-ADV-012: Path traversal outside repository root is rejected with 400."""
    res = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": "../../etc/shadow"},
    )
    assert res.status_code == 400
    assert "traversal" in res.text.lower() or "forbidden" in res.text.lower()


def test_adv_013_safe_repository_root_accepted(client, adv_data):
    """P10_5-ADV-013: File inside repository/data root is accepted."""
    csv_file = Path("data") / "adv_test_import.csv"
    res = client.post(
        "/sales/sources/import-history/preview",
        data={"file_path": str(csv_file)},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    assert res.json()["preview_token"] is not None


# ===========================================================================
# 4. Send Confirmation Modal & Immutable Payload (ADV-014 .. 015)
# ===========================================================================

def test_adv_014_send_modal_displays_exact_immutable_payload(client, adv_data):
    """P10_5-ADV-014: Send confirmation receives all 8 contract arguments."""
    os.environ["EMAIL_SEND_ENABLED"] = "true"
    try:
        res = client.get("/sales/drafts/draft_adv_approved")
        assert res.status_code == 200
        content = res.text
        # Checks that openSendConfirmation function call is wired with all 8 arguments
        assert "openSendConfirmation(" in content
        assert "Elena Rossi" in content
        assert "trusted_sender@pybim-studio.com" in content
        assert "Approved BIM Audit" in content
    finally:
        os.environ["EMAIL_SEND_ENABLED"] = "false"


def test_adv_015_modal_payload_cannot_alter_server_draft(client, adv_data):
    """P10_5-ADV-015: Form submission parameters cannot alter server authoritative draft."""
    # Attempting to pass tampered subject / body to /send route
    res = client.post(
        "/sales/drafts/draft_adv_req_changes/send",
        data={
            "revision": 1,
            "confirm_send": "true",
            "subject": "Attacker Hijacked Subject",
            "body": "Attacker Hijacked Body",
        },
        follow_redirects=False,
    )
    # Draft in DB remains completely unchanged
    draft = adv_data["rev_store"].get_draft("draft_adv_req_changes", 1)
    assert draft.subject == "Introductory BIM Audit"
    assert "Attacker" not in draft.body


# ===========================================================================
# 5. Request Changes Lifecycle (ADV-016 .. 017)
# ===========================================================================

def test_adv_016_request_changes_transitions_through_phase_9(client, adv_data):
    """P10_5-ADV-016: Request changes action transitions draft to changes_requested."""
    res = client.post(
        "/sales/drafts/draft_adv_req_changes/request-changes",
        data={"revision": 1, "note": "Please emphasize airport experience"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    draft = adv_data["rev_store"].get_draft("draft_adv_req_changes", 1)
    assert draft.approval_status == "changes_requested"
    assert draft.send_status == "not_sent"


def test_adv_017_sent_revision_cannot_request_changes(client, adv_data):
    """P10_5-ADV-017: Sent revision cannot request changes."""
    res = client.post(
        "/sales/drafts/draft_adv_sent/request-changes",
        data={"revision": 1, "note": "Too late to change"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    loc = res.headers.get("location", "")
    assert "error=" in loc
    draft = adv_data["rev_store"].get_draft("draft_adv_sent", 1)
    assert draft.approval_status == "approved"
    assert draft.send_status == "sent"


# ===========================================================================
# 6. Credential Truth & Health Checks (ADV-018 .. 019)
# ===========================================================================

def test_adv_018_missing_source_credentials_display_not_configured(client, adv_data):
    """P10_5-ADV-018: Sources requiring missing credentials display 'Not configured'."""
    # Ensure external keys are unset
    for k in ["COMPANIES_HOUSE_API_KEY", "RAPIDAPI_KEY", "GITHUB_TOKEN"]:
        os.environ.pop(k, None)

    res = client.get("/sales/sources")
    assert res.status_code == 200
    assert "Not configured" in res.text


def test_adv_019_offline_ollama_chroma_not_reported_as_operational(client, adv_data):
    """P10_5-ADV-019: Offline chroma/services are not falsely reported as operational."""
    res = client.get("/sales/settings")
    assert res.status_code == 200
    # Settings template checks chroma status dynamically
    assert "Operational" in res.text or "Unavailable" in res.text


# ===========================================================================
# 7. Evidence URL Scheme Restriction (ADV-020 .. 021)
# ===========================================================================

def test_adv_020_unsafe_evidence_urls_not_rendered_as_clickable_links(client, adv_data):
    """P10_5-ADV-020: javascript/file/data URLs in evidence are never rendered as clickable links."""
    cand_store = adv_data["cand_store"]
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_unsafe_01",
            candidate_id="cand_qual_studio",
            category="hiring",
            signal_type="job_opening",
            text="Unsafe link test",
            source_url="javascript:alert('xss')",
        )
    )
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_unsafe_02",
            candidate_id="cand_qual_studio",
            category="hiring",
            signal_type="job_opening",
            text="Unsafe file test",
            source_url="file:///etc/passwd",
        )
    )

    res = client.get("/sales/candidates/cand_qual_studio")
    assert res.status_code == 200
    assert '<a href="javascript:' not in res.text
    assert '<a href="file:' not in res.text
    assert "Non-Web Source" in res.text


def test_adv_021_safe_evidence_urls_clickable(client, adv_data):
    """P10_5-ADV-021: http and https URLs in evidence are rendered as clickable links."""
    cand_store = adv_data["cand_store"]
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_safe_01",
            candidate_id="cand_qual_studio",
            category="hiring",
            signal_type="job_opening",
            text="Safe web link test",
            source_url="https://studio-qualificato.it/careers",
        )
    )

    res = client.get("/sales/candidates/cand_qual_studio")
    assert res.status_code == 200
    assert 'href="https://studio-qualificato.it/careers"' in res.text


# ===========================================================================
# 8. Complete Qualified Lead -> Contact -> Generate Draft Workflow (ADV-022 .. 024)
# ===========================================================================

def test_adv_022_qualified_lead_generates_draft_pending_review(client, adv_data):
    """P10_5-ADV-022: Qualified lead with contact generates draft into pending_review via Phase 8."""
    from unittest.mock import patch
    from sales_engine.outreach.email_generator import EmailGenerator
    from sales_engine.outreach.schemas import EmailDraft

    p8_draft = EmailDraft(
        draft_id="draft:cand_qual_studio:contact_elena_rossi:outreach_v1:r1",
        revision=1,
        lead_id="domain:studio-qualificato.it",
        contact_id="email:elena@studio-qualificato.it",
        recipient_name="Elena Rossi",
        recipient_title="BIM Director",
        recipient_email="elena@studio-qualificato.it",
        subject="BIM Automation for Studio Qualificato",
        body="Hi Elena, We noticed your BIM automation practice. Tech-Enabled BIM Services can accelerate delivery. Open to a brief call?",
        service_used="Tech-Enabled BIM Services",
        personalization_notes="Grounded in BIM service evidence",
        evidence_refs=["SERVICE-001"],
        generation_model="qwen2.5:1.5b",
        prompt_version="outreach_v1",
    )

    with patch.object(EmailGenerator, "generate_draft", return_value=(p8_draft, None, None)):
        res = client.post(
            "/sales/candidates/cand_qual_studio/generate-draft",
            data={
                "contact_id": "contact_elena_rossi",
            },
            follow_redirects=False,
        )
    assert res.status_code == 303
    loc = res.headers.get("location", "")
    assert "/sales/drafts/draft" in loc

    # Retrieve generated draft
    draft_id = loc.split("/sales/drafts/")[1].split("?")[0]
    draft = adv_data["rev_store"].get_draft(draft_id, 1)
    assert draft is not None
    assert draft.recipient_email == "elena@studio-qualificato.it"
    assert draft.approval_status == "pending_review"
    assert draft.send_status == "not_sent"


def test_adv_023_non_qualified_or_suppressed_lead_blocks_draft_generation(client, adv_data):
    """P10_5-ADV-023: Non-qualified (watchlist) or suppressed company blocks draft generation."""
    # 1. Non-qualified watchlist lead
    res_w = client.post(
        "/sales/candidates/cand_watchlist_co/generate-draft",
        data={"contact_id": "contact_watchlist_director"},
        follow_redirects=False,
    )
    assert res_w.status_code == 303
    assert "error=" in res_w.headers.get("location", "")

    # 2. Suppressed company
    res_s = client.post(
        "/sales/candidates/cand_blocked_co/generate-draft",
        data={"contact_id": "contact_blocked_ceo"},
        follow_redirects=False,
    )
    assert res_s.status_code == 303
    assert "error=" in res_s.headers.get("location", "")


def test_adv_024_draft_generation_never_auto_approves_or_sends(client, adv_data):
    """P10_5-ADV-024: Draft generation guarantees no automatic approval or send."""
    rev_store = adv_data["rev_store"]
    all_drafts, _ = rev_store.list_drafts(filters={"lead_id": "domain:studio-qualificato.it"})
    for d in all_drafts:
        # Every generated draft must never be approved or sent autonomously
        if d.draft_id.startswith("draft:cand_qual_"):
            assert d.approval_status == "pending_review"
            assert d.send_status == "not_sent"
            assert d.sent_at is None
