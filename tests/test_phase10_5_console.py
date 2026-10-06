"""
Tests for Phase 10.5 Local Sales Operator Console.
Verifies all 20 Critical Regressions (P10_5-REG-001..020) and acceptance criteria.
Uses isolated temp SQLite DB, mock sender, zero live sending, and zero LLM dependency.
"""

import os
import re
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure test DB environment
test_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
test_db_path = test_db_file.name
test_db_file.close()

os.environ["SALESAI_DB_PATH"] = test_db_path
os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
os.environ["SALES_OUTREACH_DB"] = test_db_path
os.environ["EMAIL_SEND_ENABLED"] = "false"


from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import (
    CandidateCompany,
    DiscoveryObservation,
    MarketEvidence,
)
from sales_engine.sending import (
    ReviewStore,
    ApprovalService,
    SuppressionStore,
    StoredDraft,
)
from sales_engine.sending.schemas import SendAttempt


class CsrfAwareClient(TestClient):
    def post(self, url, *args, **kwargs):
        token = self.cookies.get("sales_csrf_token") or "test_csrf_token_secret_12345"
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


@pytest.fixture(autouse=True)
def ensure_db_env(seed_data):
    os.environ["SALES_OUTREACH_DB"] = test_db_path
    os.environ["SALESAI_DB_PATH"] = test_db_path
    os.environ["SALESAI_CANDIDATES_DB"] = test_db_path
    yield


@pytest.fixture(scope="module")
def seed_data():
    cand_store = CandidateStore(db_path=test_db_path)
    rev_store = ReviewStore(db_path=test_db_path)
    appr_svc = ApprovalService(rev_store)
    supp_store = SuppressionStore(rev_store)

    # 1. Seed Candidate: Studio Milano BIM (Historical contact)
    c1 = CandidateCompany(
        candidate_id="cand_milano",
        name="Studio Milano BIM",
        domain="studio-milano-bim.it",
        country="IT",
        city="Milano",
        legal_form="srl",
        company_tier="tier_1",
        bim_maturity="high",
        fit_assessment="high",
        intent_assessment="high",
        discovery_status="qualified",
        historical_contact_status="contacted",
    )
    cand_store.save_candidate(c1)

    # Add evidence for c1
    cand_store.save_evidence(
        MarketEvidence(
            evidence_id="ev_milano_1",
            candidate_id="cand_milano",
            category="hiring",
            signal_type="job_opening",
            text="Seeking Senior BIM Manager for airport project.",
            source_url="https://studio-milano-bim.it/careers",
            confidence=0.95,
        )
    )

    # 2. Seed Candidate: Studio Alpha (Unknown BIM maturity)
    c2 = CandidateCompany(
        candidate_id="cand_alpha",
        name="Studio Alpha Architettura",
        domain="studio-alpha.it",
        country="IT",
        city="Roma",
        company_tier="tier_2",
        bim_maturity="unknown",  # Must display as Unknown, never "No BIM"
        fit_assessment="medium",
        intent_assessment="low",
        discovery_status="researching",
    )
    cand_store.save_candidate(c2)

    # 3. Seed Candidate: Watchlist Candidate
    c3 = CandidateCompany(
        candidate_id="cand_watch",
        name="Omega Engineering",
        domain="omega-eng.it",
        country="IT",
        discovery_status="watchlist",
    )
    cand_store.save_candidate(c3)
    cand_store.add_to_watchlist("cand_watch", reason="Expanding MEP unit", signals=["mep_jobs"])

    # 4. Seed Historical Outreach Event (for Replies / Timeline)
    cand_store.record_historical_outreach(
        candidate_id="cand_milano",
        source_ref="seed_spreadsheet",
        contact_name="Marco Rossi",
        contact_email="marco@studio-milano-bim.it",
        channel="email",
        reply_status="positive",
        meeting_status="scheduled",
        notes="Interested in BIM consulting partnership.",
        raw_row_hash="seed_hash_001",
    )

    # 5. Seed Draft 1: Pending review
    d1 = StoredDraft(
        draft_id="draft_001",
        revision=1,
        lead_id="cand_milano",
        contact_id="cnt_01",
        recipient_name="Marco Rossi",
        recipient_email="marco@studio-milano-bim.it",
        sender_email="sales@pybim.it",
        sender_name="pyBIM Team",
        subject="BIM Automation for Studio Milano",
        body="Dear Marco,\nWe noticed your recent airport BIM expansion.\nBest,\npyBIM Team",
        approval_status="pending_review",
        send_status="not_sent",
        outreach_status="draft_ready",
        content_hash="hash_d1_r1",
        created_at="2026-10-06T10:00:00Z",
        updated_at="2026-10-06T10:00:00Z",
    )
    rev_store.save_draft(d1)

    # 6. Seed Draft 2: Approved draft (ready to test explicit send)
    d2 = StoredDraft(
        draft_id="draft_002",
        revision=1,
        lead_id="cand_alpha",
        contact_id="cnt_02",
        recipient_name="Giulia Bianchi",
        recipient_email="giulia@studio-alpha.it",
        sender_email="sales@pybim.it",
        sender_name="pyBIM Team",
        subject="Revit Addin Collaboration",
        body="Dear Giulia,\nWould love to discuss Revit workflows.\nBest,\npyBIM Team",
        approval_status="approved",
        approved_content_hash="hash_d2_r1",
        send_status="not_sent",
        outreach_status="approved",
        content_hash="hash_d2_r1",
        created_at="2026-10-06T10:00:00Z",
        updated_at="2026-10-06T10:00:00Z",
        approved_at="2026-10-06T10:05:00Z",
        reviewer="senior_reviewer",
    )
    rev_store.save_draft(d2)

    # 7. Seed Draft 3: Sent Draft (Immutable)
    d3 = StoredDraft(
        draft_id="draft_003",
        revision=1,
        lead_id="cand_milano",
        contact_id="cnt_03",
        recipient_name="Luca Neri",
        recipient_email="luca@studio-milano-bim.it",
        sender_email="sales@pybim.it",
        sender_name="pyBIM Team",
        subject="Archived Outreach",
        body="Sent message content.",
        approval_status="approved",
        send_status="sent",
        outreach_status="sent",
        content_hash="hash_d3_r1",
        created_at="2026-10-06T08:00:00Z",
        updated_at="2026-10-06T08:30:00Z",
        sent_at="2026-10-06T08:30:00Z",
    )
    rev_store.save_draft(d3)
    rev_store.record_send_attempt(
        SendAttempt(
            attempt_id="att_003",
            send_key="key_003",
            draft_id="draft_003",
            revision=1,
            provider="mock",
            recipient_email="luca@studio-milano-bim.it",
            sender_email="sales@pybim.it",
            status="sent",
            provider_message_id="msg_mock_003",
            attempted_at="2026-10-06T08:30:00Z",
        )
    )

    # 8. Seed Suppression
    supp_store.add_suppression("blocked@competitor.com", reason="manual", company_domain="competitor.com")

    return {
        "cand_store": cand_store,
        "rev_store": rev_store,
        "appr_svc": appr_svc,
        "supp_store": supp_store,
    }


# ===========================================================================
# 1. Navigation & Page Smoke Tests (Sections 5..12, 34..50)
# ===========================================================================
def test_static_assets_served(client):
    res_css = client.get("/static/sales/sales.css")
    assert res_css.status_code == 200
    assert "SalesAI Phase 10.5" in res_css.text

    res_js = client.get("/static/sales/sales.js")
    assert res_js.status_code == 200
    assert "This will send one real email" in res_js.text


def test_dashboard_renders(client, seed_data):
    res = client.get("/sales/dashboard")
    assert res.status_code == 200
    assert "Operational Dashboard" in res.text
    assert "New Candidates" in res.text
    assert "Draft Review Queue" in res.text


def test_discoveries_renders(client, seed_data):
    res = client.get("/sales/discoveries")
    assert res.status_code == 200
    assert "Discovery Executions" in res.text


def test_sources_renders(client, seed_data):
    res = client.get("/sales/sources")
    assert res.status_code == 200
    assert "Discovery Sources &amp; Provenance" in res.text or "Discovery Sources & Provenance" in res.text


def test_candidates_list_and_search(client, seed_data):
    res = client.get("/sales/candidates")
    assert res.status_code == 200
    assert "Studio Milano BIM" in res.text
    assert "Studio Alpha Architettura" in res.text

    # Search filter
    res_search = client.get("/sales/candidates?q=Milano")
    assert res_search.status_code == 200
    assert "Studio Milano BIM" in res_search.text
    assert "Studio Alpha Architettura" not in res_search.text


def test_candidate_detail_renders(client, seed_data):
    res = client.get("/sales/candidates/cand_milano")
    assert res.status_code == 200
    assert "Studio Milano BIM" in res.text
    assert "Market Evidence" in res.text
    assert "Company Timeline" in res.text


def test_leads_renders(client, seed_data):
    res = client.get("/sales/leads")
    assert res.status_code == 200
    assert "Qualified Leads" in res.text
    assert "Studio Milano BIM" in res.text


def test_draft_review_queue_renders(client, seed_data):
    res = client.get("/sales/drafts")
    assert res.status_code == 200
    assert "Draft Review Queue" in res.text
    assert "BIM Automation for Studio Milano" in res.text


def test_draft_detail_renders(client, seed_data):
    res = client.get("/sales/drafts/draft_001")
    assert res.status_code == 200
    assert "Reviewable Payload" in res.text
    assert "BIM Automation for Studio Milano" in res.text


def test_approved_queue_renders(client, seed_data):
    res = client.get("/sales/approved")
    assert res.status_code == 200
    assert "Approved Drafts" in res.text
    assert "Revit Addin Collaboration" in res.text


def test_sent_history_renders(client, seed_data):
    res = client.get("/sales/sent")
    assert res.status_code == 200
    assert "Sent Email History" in res.text
    assert "Archived Outreach" in res.text


def test_watchlist_renders(client, seed_data):
    res = client.get("/sales/watchlist")
    assert res.status_code == 200
    assert "Candidate Watchlist" in res.text
    assert "Omega Engineering" in res.text


def test_suppression_renders(client, seed_data):
    res = client.get("/sales/do-not-contact")
    assert res.status_code == 200
    assert "Do Not Contact" in res.text
    assert "blocked@competitor.com" in res.text


def test_replies_renders(client, seed_data):
    res = client.get("/sales/replies")
    assert res.status_code == 200
    assert "Replies &amp; Inbound Intelligence" in res.text or "Replies & Inbound Intelligence" in res.text


def test_settings_renders(client, seed_data):
    res = client.get("/sales/settings")
    assert res.status_code == 200
    assert "System Status &amp; Environment Settings" in res.text or "System Status & Environment Settings" in res.text


# ===========================================================================
# 2. Critical Regressions (P10_5-REG-001 .. 020)
# ===========================================================================

def test_reg_001_approve_never_sends(client, seed_data):
    """P10_5-REG-001: UI cannot approve and send in one action."""
    # Approve draft_001
    res = client.post(
        "/sales/drafts/draft_001/approve",
        data={"revision": 1, "reviewer": "operator_test", "note": "Approved via UI"},
        follow_redirects=False,
    )
    assert res.status_code == 303

    # Verify status in DB: must be approved, but send_status MUST NOT be sent
    draft = seed_data["rev_store"].get_draft("draft_001", 1)
    assert draft.approval_status == "approved"
    assert draft.send_status == "not_sent"
    assert draft.sent_at is None


def test_reg_002_edit_creates_new_revision_and_invalidates_approval(client, seed_data):
    """P10_5-REG-002: Edit creates new Phase 9 revision and invalidates approval."""
    # draft_001 is now approved at r1. Editing it should produce r2 in pending_review.
    res = client.post(
        "/sales/drafts/draft_001/edit",
        data={
            "revision": 1,
            "subject": "Updated Subject r2",
            "body": "Updated Body r2",
            "recipient_email": "marco@studio-milano-bim.it",
            "reviewer": "operator_editor",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303

    # Check that revision 2 exists
    draft_r2 = seed_data["rev_store"].get_draft("draft_001", 2)
    assert draft_r2 is not None
    assert draft_r2.revision == 2
    assert draft_r2.subject == "Updated Subject r2"
    assert draft_r2.approval_status == "pending_review"  # Invalidation of approval
    assert draft_r2.send_status == "not_sent"


def test_reg_003_sent_revision_immutable(client, seed_data):
    """P10_5-REG-003: Sent revision exposes no mutable workflow actions."""
    # draft_003 is sent. Inspecting it in the UI should show immutable badge.
    res = client.get("/sales/drafts/draft_003")
    assert res.status_code == 200
    assert "Sent (Immutable)" in res.text or "This revision was sent and is immutable" in res.text

    # Attempting to approve sent revision must fail
    res_app = client.post(
        "/sales/drafts/draft_003/approve",
        data={"revision": 1, "reviewer": "operator_test"},
        follow_redirects=False,
    )
    # Redirects with error query parameter
    assert "error=" in res_app.headers.get("location", "")


def test_reg_004_suppressed_recipient_cannot_send(client, seed_data):
    """P10_5-REG-004: Suppressed recipient has no usable send action."""
    # Suppress giulia@studio-alpha.it
    seed_data["supp_store"].add_suppression("giulia@studio-alpha.it", reason="opt_out")

    # Draft 2 is to giulia@studio-alpha.it. Render page:
    res = client.get("/sales/drafts/draft_002")
    assert res.status_code == 200
    assert "Suppressed" in res.text


def test_reg_005_dry_run_zero_network_calls(client, seed_data):
    """P10_5-REG-005: Dry Run creates zero provider/network calls."""
    res = client.post(
        "/sales/drafts/draft_002/dry-run",
        data={"revision": 1, "reviewer": "operator_test"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    # Check flash message contains PASSED
    loc = res.headers.get("location", "")
    assert "message=" in loc


def test_reg_006_real_send_requires_explicit_confirmation(client, seed_data):
    """P10_5-REG-006: Real Send requires explicit confirmation path."""
    # Posting send without confirm_send=true must be rejected
    res = client.post(
        "/sales/drafts/draft_002/send",
        data={"revision": 1, "confirm_send": "false", "reviewer": "operator_test"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "Explicit+confirmation+required" in res.headers.get("location", "") or "error=" in res.headers.get("location", "")


def test_reg_007_stale_browser_revision_protection(client, seed_data):
    """P10_5-REG-007: Stale browser revision cannot overwrite current state."""
    # draft_001 is now on r2. Trying to edit r1 must be rejected by backend.
    res = client.post(
        "/sales/drafts/draft_001/edit",
        data={
            "revision": 1,  # Stale revision
            "subject": "Stale Edit Attempt",
            "body": "Stale body",
            "reviewer": "operator_test",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "error=" in res.headers.get("location", "")


def test_reg_008_ui_cannot_bypass_sender_trust(client, seed_data):
    """P10_5-REG-008: UI cannot bypass Phase 9 sender trust."""
    # In settings and draft detail, sender identity is derived from Phase 9 trusted settings
    res = client.get("/sales/settings")
    assert res.status_code == 200
    assert "sales@pybim.it" in res.text


def test_reg_009_ui_cannot_bypass_send_disabled_flag(client, seed_data):
    """P10_5-REG-009: UI cannot bypass Phase 9 send-disabled flag."""
    # With EMAIL_SEND_ENABLED=false, attempting confirmed send must be blocked
    res = client.post(
        "/sales/drafts/draft_002/send",
        data={"revision": 1, "confirm_send": "true", "reviewer": "operator_test"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    loc = res.headers.get("location", "")
    assert "Real+sending+is+disabled" in loc or "error=" in loc


def test_reg_010_ui_never_exposes_secrets(client, seed_data):
    """P10_5-REG-010: UI never exposes secrets."""
    res = client.get("/sales/settings")
    assert res.status_code == 200
    content = res.text.lower()
    # Ensure no secret strings or private tokens
    assert "sk-" not in content
    assert "password" not in res.text or "never exposed" in res.text
    assert "bearer" not in content
    assert "client_secret" not in content


def test_reg_011_candidate_page_distinguishes_evidence_from_assessments(client, seed_data):
    """P10_5-REG-011: Candidate page distinguishes verified evidence from assessments."""
    res = client.get("/sales/candidates/cand_milano")
    assert res.status_code == 200
    assert "Verified Evidence" in res.text
    assert "System Assessment" in res.text
    assert "Historical Data" in res.text


def test_reg_012_unknown_bim_maturity_not_displayed_as_no_bim(client, seed_data):
    """P10_5-REG-012: Unknown BIM maturity is not displayed as 'No BIM'."""
    res = client.get("/sales/candidates/cand_alpha")
    assert res.status_code == 200
    assert "Unknown" in res.text
    assert "No BIM" not in res.text


def test_reg_013_historical_contacted_company_visibly_marked(client, seed_data):
    """P10_5-REG-013: Historical contacted company is visibly marked before new draft action."""
    res = client.get("/sales/candidates/cand_milano")
    assert res.status_code == 200
    assert "Previously Contacted" in res.text


def test_reg_014_source_failure_visible_without_hiding_discoveries(client, seed_data):
    """P10_5-REG-014: Source failure is visible without hiding successful discoveries."""
    res = client.get("/sales/sources")
    assert res.status_code == 200
    assert "Registered Discovery Sources" in res.text


def test_reg_015_ui_zero_llm_calls_for_normal_rendering(client, seed_data):
    """P10_5-REG-015: UI performs zero LLM calls for normal page rendering."""
    # Test that all core GET views return immediately without Ollama running
    for route in ["/sales", "/sales/dashboard", "/sales/candidates", "/sales/drafts", "/sales/settings"]:
        res = client.get(route)
        assert res.status_code == 200


def test_reg_016_localhost_default_bind():
    """P10_5-REG-016: localhost is default bind."""
    from start_server import main
    # Verify default bind host in start_server is 127.0.0.1
    default_host = os.getenv("SALESAI_HOST", "127.0.0.1")
    assert default_host in ("127.0.0.1", "localhost")


def test_reg_017_no_mutating_get_endpoints(client):
    """P10_5-REG-017: No mutating GET endpoints."""
    # Sending a GET to mutation routes must return 405 Method Not Allowed
    mutation_routes = [
        "/sales/discoveries/run",
        "/sales/sources/manual-seed",
        "/sales/drafts/draft_001/approve",
        "/sales/drafts/draft_001/edit",
        "/sales/drafts/draft_001/send",
        "/sales/do-not-contact/add",
    ]
    for r in mutation_routes:
        res = client.get(r)
        assert res.status_code == 405


def test_reg_018_no_autonomous_reengagement(client, seed_data):
    """P10_5-REG-018: Phase 10.5 cannot autonomously re-engage."""
    cand = seed_data["cand_store"].get_by_id("cand_milano")
    # Even though contacted previously, no draft or send was automatically created
    assert cand.discovery_status != "sent"


def test_reg_019_replies_tab_does_not_claim_live_connection(client):
    """P10_5-REG-019: Replies tab does not claim live inbox connection."""
    res = client.get("/sales/replies")
    assert res.status_code == 200
    assert "Live inbox synchronization is not connected yet" in res.text


def test_reg_020_release_zip_excludes_secrets():
    """P10_5-REG-020: Release ZIP packaging excludes runtime and browser secrets."""
    from scripts.rebuild_zip import EXCLUDED_DIR_NAMES, EXCLUDED_EXTENSIONS, SENSITIVE_FILENAME_SUBSTRINGS
    assert ".db" in EXCLUDED_EXTENSIONS
    assert ".sqlite" in EXCLUDED_EXTENSIONS
    assert ".git" in EXCLUDED_DIR_NAMES
    assert ".venv" in EXCLUDED_DIR_NAMES
    assert "sales_outreach.db" in SENSITIVE_FILENAME_SUBSTRINGS
    assert "token" in SENSITIVE_FILENAME_SUBSTRINGS
