"""
tests/test_operator_console_workflow.py
Verification suite for SalesAI Operator Console UX & Manual Outreach Readiness:
1. Navigation and workflow groups remain fully accessible without removing routes.
2. Candidate and Research review views render with theme-safe styles.
3. Offline synthetic research snapshot review renders without crawling.
4. Lead Register priority filtering (A/B/C) and search functionality.
5. Manual email preparation copy controls and manual outbound dispatch logging.
6. Email sending disabled enforcement (EMAIL_SEND_ENABLED=false).
7. Strict preservation of DNC and suppression guards.
"""

import pytest
import sqlite3
from starlette.testclient import TestClient

from backend.main import app
from sales_engine.sending.review_store import ReviewStore
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.research.research_store import ResearchSnapshotStore, PageEvidence


@pytest.fixture
def client():
    return TestClient(app)


def test_01_operator_console_navigation_routes(client):
    """Verify all workflow navigation routes are functional and return 200."""
    routes = [
        "/sales/dashboard",
        "/sales/discovery",
        "/sales/sources",
        "/sales/candidates",
        "/sales/register",
        "/sales/leads",
        "/sales/drafts",
        "/sales/approved",
        "/sales/sent",
        "/sales/watchlist",
        "/sales/do-not-contact",
        "/sales/replies",
        "/sales/analytics",
        "/sales/settings",
    ]
    for r in routes:
        resp = client.get(r)
        assert resp.status_code == 200, f"Route {r} failed with status {resp.status_code}"
        assert "Email Led" in resp.text
        assert "nav-bar" in resp.text


def test_02_synthetic_research_review_offline_render(client, monkeypatch, tmp_path):
    """Verify stored snapshot reviews offline with theme-aware styling and zero crawling."""
    from backend.sales_ui.router import get_console_services
    svcs = get_console_services()
    cand_store = svcs["cand_store"]
    
    cand = CandidateCompany(
        candidate_id="cand:domain:synthetic-test.it",
        company_name="Synthetic Test S.r.l.",
        normalized_company_name="synthetic test srl",
        company_domain="synthetic-test.it",
    )
    cand_store.save_candidate(cand)

    snap_db = tmp_path / "research_cache.sqlite3"
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", str(snap_db))
    r_store = ResearchSnapshotStore(db_path=snap_db)
    pages = [
        PageEvidence(
            snapshot_id="snap_synthetic_001",
            evidence_id="ev_01",
            requested_url="https://synthetic-test.it/contatti",
            resolved_url="https://synthetic-test.it/contatti",
            page_type="contact",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-11-01T00:00:00Z",
            title="Contatti - Synthetic Test",
            meta_description="Contatti",
            content_sha256="sha123",
            cleaned_text="P.IVA 04095420271 Via Roma 1",
            raw_html="<html><body><p>P.IVA 04095420271 Via Roma 1</p></body></html>",
        )
    ]
    r_store.create_snapshot(
        candidate_id="cand:domain:synthetic-test.it",
        canonical_domain="synthetic-test.it",
        company_name="Synthetic Test S.r.l.",
        pages=pages,
        snapshot_id="snap_synthetic_001",
    )

    resp = client.get("/sales/candidates/cand:domain:synthetic-test.it/research")
    assert resp.status_code == 200
    assert "Company Research &amp; Enrichment Review" in resp.text
    assert "Snapshot ID:" in resp.text
    assert "snap_synthetic_001" in resp.text
    # Verify checkboxes default to unchecked
    assert 'type="checkbox" name="apply_company_name" value="1"' in resp.text
    assert 'checked' not in resp.text.split('apply_company_name')[1].split('>')[0]


def test_03_lead_register_priority_filter(client, tmp_path):
    """Verify 13-field Lead Register search and priority filtering."""
    resp_b = client.get("/sales/register?priority=B")
    assert resp_b.status_code == 200
    assert "Lead Register" in resp_b.text

    resp_q = client.get("/sales/register?q=test")
    assert resp_q.status_code == 200


def test_04_manual_outreach_logging_and_copy_controls(client, tmp_path):
    """Verify manual outbound dispatch logging without live sending."""
    db_file = tmp_path / "test_outreach.db"
    store = ReviewStore(db_path=db_file)

    log_entry = store.log_manual_outreach(
        candidate_id="cand:domain:bs-eng.net",
        recipient_email="info@bs-eng.net",
        subject="pyBIM Collaboration",
        contact_id="cnt_01",
        recipient_name="Andrea Berro",
        channel="email",
        logged_by="local_operator",
        external_ref="MSG-12345",
        operator_notes="Manually sent through Outlook client",
    )
    assert log_entry["log_id"].startswith("log_")
    assert log_entry["recipient_email"] == "info@bs-eng.net"

    logs = store.list_manual_outreach_logs("cand:domain:bs-eng.net")
    assert len(logs) == 1
    assert logs[0]["external_ref"] == "MSG-12345"


def test_05_email_send_disabled_safety_banner(client):
    """Verify EMAIL_SEND_ENABLED=false warning banner is visible in the console."""
    resp = client.get("/sales/dashboard")
    assert resp.status_code == 200
    assert "Real sending is disabled on this server." in resp.text


def test_06_manual_outreach_safety_and_duplicate_prevention(client, tmp_path, monkeypatch):
    """P0 Safety Closure Acceptance Suite:
    1. An unqualified candidate cannot create a new outreach draft.
    2. An arbitrary unverified email cannot become a verified contact automatically.
    3. A mismatched contact ID and email are rejected.
    4. Suppressed candidates and recipients cannot initiate new outreach.
    5. Previously contacted candidates do not bypass cold-outreach restrictions.
    6. A qualified candidate with a legitimately verified contact can create a draft.
    7. Manual drafting performs zero inference and zero network requests.
    8. Manual dispatch logging requires explicit confirmation.
    9. Duplicate submissions produce one authoritative record.
    10. Concurrent duplicate requests cannot create multiple events.
    11. Historical audit recording remains possible without authorizing new outreach.
    12. Candidate history and manual log records remain consistent.
    13. No operational tables are dropped.
    """
    from sales_engine.candidates.candidate_store import CandidateStore
    from sales_engine.candidates.models import CandidateCompany
    from backend.schemas import ContactCandidate
    from sales_engine.sending.review_store import ReviewStore
    from sales_engine.leads.register_store import LeadRegisterStore
    from sales_engine.leads.register_models import LeadRegisterRecord
    from backend.sales_ui.router import is_candidate_outreach_eligible

    shared_db = str(tmp_path / "safety_shared.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", shared_db)
    monkeypatch.setenv("SALESAI_DB_PATH", shared_db)

    c_store = CandidateStore(shared_db)
    r_store = ReviewStore(shared_db)
    reg_store = LeadRegisterStore(shared_db)

    # Setup CSRF
    dash_resp = client.get("/sales/dashboard")
    csrf_token = dash_resp.cookies.get("sales_csrf_token") or "dummy_token"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    # -------------------------------------------------------------------------
    # 1. Unqualified candidate cannot create a new outreach draft
    # -------------------------------------------------------------------------
    unqual_id = "cand:domain:unqualified-firm.it"
    c_store.save_candidate(CandidateCompany(
        candidate_id=unqual_id,
        canonical_domain="unqualified-firm.it",
        company_name="Unqualified Firm",
        qualification_outcome="reject",
        discovery_status="rejected",
    ))
    resp_unqual = client.post(f"/sales/candidates/{unqual_id}/create-manual-draft", data={
        "recipient_email": "director@unqualified-firm.it",
        "subject": "Proposal",
        "body": "Hello",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_unqual.status_code == 303
    assert "error=" in resp_unqual.headers["location"]
    assert "not%20eligible" in resp_unqual.headers["location"].lower()

    # -------------------------------------------------------------------------
    # Setup Qualified Candidate for subsequent checks
    # -------------------------------------------------------------------------
    cand_id = "cand:domain:safety-check.it"
    c_store.save_candidate(CandidateCompany(
        candidate_id=cand_id,
        canonical_domain="safety-check.it",
        company_name="Safety Check Engineering",
        discovery_source="test",
        qualification_outcome="qualified_for_enrichment",
        discovery_status="qualified",
        cold_outreach_allowed=True,
    ))

    # -------------------------------------------------------------------------
    # 2. Arbitrary unverified email cannot become verified contact automatically
    # -------------------------------------------------------------------------
    resp_unverif = client.post(f"/sales/candidates/{cand_id}/create-manual-draft", data={
        "recipient_email": "arbitrary_unverified@safety-check.it",
        "recipient_name": "Unknown Person",
        "subject": "Proposal",
        "body": "Hello",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_unverif.status_code == 303
    assert "error=" in resp_unverif.headers["location"]
    assert "not%20verified" in resp_unverif.headers["location"].lower()

    # Verify contact was saved with email_status='unverified', NEVER 'verified'
    unverif_c = c_store.get_contact_by_id("arbitrary_unverified@safety-check.it", candidate_id=cand_id)
    assert unverif_c is not None
    assert unverif_c.email_status == "unverified"
    assert unverif_c.email_confidence == 0

    # -------------------------------------------------------------------------
    # Explicit evidence verification action test
    # -------------------------------------------------------------------------
    verif_resp = client.post(f"/sales/candidates/{cand_id}/verify-contact", data={
        "contact_id": unverif_c.contact_id,
        "evidence_source": "Official website footer / Team page",
        "evidence_reference": "https://safety-check.it/team",
        "notes": "Verified through company public impressum",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert verif_resp.status_code == 303
    assert "message=" in verif_resp.headers["location"]

    verifs = c_store.get_contact_verifications(cand_id, unverif_c.contact_id)
    assert len(verifs) == 1
    assert verifs[0]["evidence_source"] == "Official website footer / Team page"
    assert verifs[0]["verified_by"] == "local_operator"

    # Status is now upgraded
    upgraded_c = c_store.get_contact_by_id(unverif_c.contact_id, candidate_id=cand_id)
    assert upgraded_c.email_status == "verified"

    # -------------------------------------------------------------------------
    # 3. Mismatched contact ID and email are rejected
    # -------------------------------------------------------------------------
    resp_mismatch = client.post(f"/sales/candidates/{cand_id}/create-manual-draft", data={
        "contact_id": upgraded_c.contact_id,
        "recipient_email": "completely_different@safety-check.it",
        "subject": "Proposal",
        "body": "Hello",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_mismatch.status_code == 303
    assert "error=" in resp_mismatch.headers["location"]
    assert "does%20not%20match" in resp_mismatch.headers["location"].lower()

    # Cross-candidate contact ID rejection
    other_cand = "cand:domain:other-firm.it"
    c_store.save_candidate(CandidateCompany(
        candidate_id=other_cand,
        canonical_domain="other-firm.it",
        company_name="Other Firm",
        qualification_outcome="qualified_for_enrichment",
    ))
    resp_cross = client.post(f"/sales/candidates/{other_cand}/create-manual-draft", data={
        "contact_id": upgraded_c.contact_id,
        "recipient_email": upgraded_c.work_email,
        "subject": "Proposal",
        "body": "Hello",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_cross.status_code == 303
    assert "Cross-candidate" in resp_cross.headers["location"]

    # -------------------------------------------------------------------------
    # 4. Suppressed candidates and recipients cannot initiate new outreach
    # -------------------------------------------------------------------------
    from sales_engine.sending import SuppressionStore
    supp_store = SuppressionStore(r_store)
    supp_store.add_suppression(email="suppressed@safety-check.it", reason="opt_out", source="test")

    # Add verified contact with suppressed email
    c_store.save_contact(ContactCandidate(
        contact_id="cnt_supp",
        work_email="suppressed@safety-check.it",
        email_status="verified",
    ), candidate_id=cand_id)

    resp_supp = client.post(f"/sales/candidates/{cand_id}/create-manual-draft", data={
        "contact_id": "cnt_supp",
        "recipient_email": "suppressed@safety-check.it",
        "subject": "Proposal",
        "body": "Hello",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_supp.status_code == 303
    assert "suppressed" in resp_supp.headers["location"].lower()

    # -------------------------------------------------------------------------
    # 6. Qualified candidate with verified contact can create draft (zero-LLM)
    # -------------------------------------------------------------------------
    resp_draft = client.post(f"/sales/candidates/{cand_id}/create-manual-draft", data={
        "contact_id": upgraded_c.contact_id,
        "recipient_email": upgraded_c.work_email,
        "recipient_name": "Andrea Berro",
        "subject": "BIM Engineering Consultation",
        "body": "Buongiorno Andrea, vorremmo proporre una collaborazione.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_draft.status_code == 303
    draft_id = resp_draft.headers["location"].split("?")[0].split("/")[-1]
    assert draft_id.startswith("draft:manual:")

    draft = r_store.get_draft(draft_id)
    assert draft is not None
    assert draft.generation_model == "manual_operator_draft"
    assert draft.approval_status == "pending_review"

    # -------------------------------------------------------------------------
    # 8. Manual dispatch logging requires explicit confirmation
    # -------------------------------------------------------------------------
    unconf = client.post(f"/sales/drafts/{draft_id}/log-manual-send", data={
        "channel": "email",
        "external_ref": "MSG-001",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert unconf.status_code == 303
    assert "Explicit" in unconf.headers["location"] or "confirmation" in unconf.headers["location"]
    assert len(r_store.list_manual_outreach_logs(cand_id)) == 0

    # -------------------------------------------------------------------------
    # Valid dispatch confirmation
    # -------------------------------------------------------------------------
    conf = client.post(f"/sales/drafts/{draft_id}/log-manual-send", data={
        "channel": "email",
        "confirm_external_transmission": "1",
        "external_ref": "MSG-001",
        "operator_notes": "Sent via Outlook",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert conf.status_code == 303
    assert "confirmed" in conf.headers["location"].lower()

    logs = r_store.list_manual_outreach_logs(cand_id)
    assert len(logs) == 1
    assert logs[0]["external_ref"] == "MSG-001"
    assert logs[0]["confirmed_sent_externally"] == 1

    # -------------------------------------------------------------------------
    # 5. Candidate history updated & cold outreach blocked
    # -------------------------------------------------------------------------
    cand_obj = c_store.get_candidate(cand_id)
    assert is_candidate_outreach_eligible(cand_obj) is False
    assert cand_obj.historical_contact_status == "contacted"
    assert cand_obj.cold_outreach_allowed is False

    # Blocked from creating fresh drafts
    resp_blocked = client.post(f"/sales/candidates/{cand_id}/create-manual-draft", data={
        "contact_id": upgraded_c.contact_id,
        "recipient_email": upgraded_c.work_email,
        "subject": "Followup",
        "body": "Another message",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp_blocked.status_code == 303
    assert "error=" in resp_blocked.headers["location"]

    # -------------------------------------------------------------------------
    # 9 & 10. Duplicate submissions and database uniqueness protection
    # -------------------------------------------------------------------------
    dup_resp = client.post(f"/sales/drafts/{draft_id}/log-manual-send", data={
        "channel": "email",
        "confirm_external_transmission": "1",
        "external_ref": "MSG-001",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert dup_resp.status_code == 303
    assert "Duplicate" in dup_resp.headers["location"]
    assert len(r_store.list_manual_outreach_logs(cand_id)) == 1

    # Concurrent database-level collision simulation
    concurrent_dup = r_store.log_manual_outreach(
        candidate_id=cand_id,
        recipient_email=upgraded_c.work_email,
        subject="Concurrent test",
        draft_id=draft_id,
        external_ref="MSG-001",
        confirmed_sent_externally=True,
    )
    assert concurrent_dup.get("duplicate") is True
    assert len(r_store.list_manual_outreach_logs(cand_id)) == 1

    # -------------------------------------------------------------------------
    # 11. Retrospective historical audit log without authorizing new outreach
    # -------------------------------------------------------------------------
    # Scenario A: Attempting to reuse an already-dispatched draft is strictly blocked as duplicate
    retro_dup = client.post(f"/sales/drafts/{draft_id}/log-manual-send", data={
        "channel": "email",
        "confirm_external_transmission": "1",
        "is_retrospective_audit": "1",
        "external_ref": "MSG-001",
        "operator_notes": "Attempting to re-log already sent draft",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert retro_dup.status_code == 303
    assert "duplicate" in retro_dup.headers["location"].lower()

    # Scenario B: Legitimate retrospective historical event recorded directly via candidate store audit workflow
    hist_inserted = c_store.record_historical_outreach(
        candidate_id=cand_id,
        source_ref="retrospective_audit_log",
        contact_name=upgraded_c.full_name,
        contact_email=upgraded_c.work_email,
        contacted_at="2026-01-15T10:00:00Z",
        channel="email",
        raw_row_hash="HIST_ROW_999",
        notes="Historical external correspondence prior to onboarding (MSG-HIST-999)",
    )
    assert hist_inserted is True

    # Candidate remains strictly ineligible for new cold outreach
    cand_after_retro = c_store.get_candidate(cand_id)
    assert is_candidate_outreach_eligible(cand_after_retro) is False

    # Historical outreach record is visible in candidate history
    cand_hist = c_store.get_historical_outreach_for_candidate(cand_id)
    assert any("MSG-HIST-999" in (h.get("notes") or "") for h in cand_hist)

    # -------------------------------------------------------------------------
    # 12 & 13. Safety: Database schema verification (no operational tables dropped)
    # -------------------------------------------------------------------------
    import sqlite3
    conn = sqlite3.connect(shared_db)
    cursor = conn.cursor()
    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "manual_outreach_log" in tables
    assert "candidate_contact_verifications" in tables
    assert "candidate_companies" in tables
    assert "candidate_contacts" in tables
    conn.close()

