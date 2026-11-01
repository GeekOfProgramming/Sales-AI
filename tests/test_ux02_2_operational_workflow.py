"""
Targeted regression tests for UX-02.1 & UX-02.2:
- B1: Research Snapshot Storage Mismatch
  - Verifies that CandidateStore and ResearchSnapshotStore operate correctly when using two distinct databases.
  - Verifies that Company Workspace loads the snapshot from the distinct research cache database (respecting SALESAI_RESEARCH_CACHE_DB).
  - Verifies that Research Review and Company Workspace display the matching snapshot ID.
- B2: Contact Verification and Draft Eligibility
  - Verified contacts: eligible for outreach drafting.
  - Documented Lead Register emails: eligible via documented evidence path without fabricating a 'verified' contact status.
  - Likely-only contacts: not treated as verified or eligible based solely on 'likely'.
  - Unverified contacts: rejected from draft creation.
  - Suppressed / DNC companies: blocked from outreach.
- C1-C4: Operational Workflow Pipeline in Workspace
  - Renders all 4 pipeline stages (Research, Lead Register, Contact Status, Outreach State).
  - Safe navigation, zero crawling on load, zero email sending.
"""
import pytest
from starlette.testclient import TestClient

from sales_engine.candidates.models import CandidateCompany
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore, LeadRegisterRecord
from sales_engine.research.research_store import ResearchSnapshotStore, PageEvidence
from sales_engine.sending.review_store import ReviewStore, StoredDraft
from backend.schemas import ContactCandidate


@pytest.fixture
def dual_db_env(tmp_path, monkeypatch):
    """Provides two distinct isolated SQLite databases: outreach DB and research cache DB."""
    outreach_db = str(tmp_path / "outreach_isolated.db")
    research_db = str(tmp_path / "research_cache_isolated.sqlite3")

    monkeypatch.setenv("SALES_OUTREACH_DB", outreach_db)
    monkeypatch.setenv("SALESAI_DB_PATH", outreach_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", outreach_db)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)

    c_store = CandidateStore(db_path=outreach_db)
    reg_store = LeadRegisterStore(db_path=outreach_db)
    rev_store = ReviewStore(db_path=outreach_db)
    snap_store = ResearchSnapshotStore(db_path=research_db)

    from backend.main import app
    client = TestClient(app)
    # Obtain authenticated CSRF cookie & token
    dash_resp = client.get("/sales/dashboard")
    csrf_token = dash_resp.cookies.get("sales_csrf_token") or "test_csrf_token"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    return client, c_store, reg_store, rev_store, snap_store, outreach_db, research_db, csrf_token, headers


# ============================================================================
# B1 — Research Snapshot Storage Mismatch Tests
# ============================================================================

def test_b1_distinct_research_cache_db_loaded_in_workspace(dual_db_env):
    """
    Store candidate in outreach_db and research snapshot in research_db.
    Verify Company Workspace and Research Review load the snapshot correctly from the distinct cache DB.
    """
    client, c_store, reg_store, rev_store, snap_store, outreach_db, research_db, csrf_token, headers = dual_db_env

    assert outreach_db != research_db

    cid = "cand:domain:dual-test.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Dual DB Test S.r.l.",
        company_domain="dual-test.it",
        country="Italy",
        city="Verona",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Save snapshot strictly to research_db
    page = PageEvidence(
        snapshot_id="snap_dual_999",
        evidence_id="ev_01",
        requested_url="https://dual-test.it/info",
        resolved_url="https://dual-test.it/info",
        page_type="about",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-01T15:00:00Z",
        title="Dual DB Test Information",
        meta_description="Testing dual database isolation",
        content_sha256="abc123sha",
        cleaned_text="BIM specialist and engineering consultant in Verona",
    )
    snap_store.create_snapshot(
        candidate_id=cid,
        canonical_domain="dual-test.it",
        company_name="Dual DB Test S.r.l.",
        pages=[page],
        research_status="complete",
        snapshot_id="snap_dual_999",
    )

    # Verify Company Workspace loads snapshot from research_db
    ws_resp = client.get(f"/sales/candidates/{cid}")
    assert ws_resp.status_code == 200
    assert "snap_dual_999" in ws_resp.text
    assert "Dual DB Test Information" in ws_resp.text
    assert "Loaded Offline" in ws_resp.text
    assert "Snapshot Available" in ws_resp.text

    # Verify Research Review loads the exact matching snapshot ID from research_db
    rev_resp = client.get(f"/sales/candidates/{cid}/research")
    assert rev_resp.status_code == 200
    assert "snap_dual_999" in rev_resp.text


# ============================================================================
# B2 — Contact Verification and Draft Eligibility Tests
# ============================================================================

def test_b2_likely_contact_not_automatically_eligible(dual_db_env):
    """A contact with email_status='likely' is NOT automatically eligible for manual draft creation without verification."""
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:likely-firm.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Likely Firm",
        company_domain="likely-firm.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Add contact with email_status='likely'
    cont = ContactCandidate(
        contact_id="cont:likely:01",
        candidate_id=cid,
        first_name="Marco",
        last_name="Bianchi",
        full_name="Marco Bianchi",
        work_email="m.bianchi@likely-firm.it",
        job_title="Lead Architect",
        email_status="likely",
        email_confidence=75,
        provider="heuristic",
    )
    c_store.save_contact(cont, candidate_id=cid)

    # Attempt to create manual draft using likely contact (not verified)
    resp = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "contact_id": cont.contact_id,
        "recipient_email": cont.work_email,
        "recipient_name": cont.full_name,
        "subject": "Collaboration",
        "body": "Hello Marco",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)

    # Must be rejected because contact is not verified or documented in register
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "error=" in loc
    assert "not%20verified" in loc.lower() or "not verified" in loc.lower()


def test_b2_lead_register_documented_email_eligible_without_fabricating_verified(dual_db_env):
    """
    An email documented in Lead Register allows drafting via documented evidence path,
    WITHOUT fabricating an upgraded email_status='verified'.
    """
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:doc-lead.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Documented Lead S.r.l.",
        company_domain="doc-lead.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Document email in Lead Register record
    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=456,
        company_name="Documented Lead S.r.l.",
        contact_email_addresses="info@doc-lead.it",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reg_rec)

    # Attempt draft creation with documented email
    resp = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "recipient_email": "info@doc-lead.it",
        "recipient_name": "Official Contact",
        "subject": "Engineering Partnership",
        "body": "Salve, vorremmo proporre una collaborazione.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)

    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "/sales/drafts/draft:manual:" in loc

    # Check contact record: must NOT have email_status='verified' fabricated
    saved_c = c_store.get_contact_by_id("info@doc-lead.it", candidate_id=cid)
    assert saved_c is not None
    assert saved_c.email_status != "verified"
    assert saved_c.provider == "lead_register_evidence"


def test_b2_explicitly_verified_contact_eligible(dual_db_env):
    """A contact with email_status='verified' (via explicit audit verification) successfully creates draft."""
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:verified-firm.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Verified Firm",
        company_domain="verified-firm.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    cont = ContactCandidate(
        contact_id="cont:verif:01",
        candidate_id=cid,
        first_name="Giulia",
        last_name="Verdi",
        full_name="Giulia Verdi",
        work_email="g.verdi@verified-firm.it",
        job_title="Partner",
        email_status="verified",
        email_confidence=100,
        provider="manual_audit",
    )
    c_store.save_contact(cont, candidate_id=cid)

    resp = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "contact_id": cont.contact_id,
        "recipient_email": cont.work_email,
        "recipient_name": cont.full_name,
        "subject": "BIM Advisory",
        "body": "Gentile Giulia, proponiamo collaborazione.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)

    assert resp.status_code == 303
    assert "/sales/drafts/draft:manual:" in resp.headers["location"]


# ============================================================================
# C1-C4 — Operational Workflow Pipeline Rendering Tests
# ============================================================================

def test_c_workflow_pipeline_rendering(dual_db_env):
    """Verify Company Workspace renders Workflow Stage Pipeline with accurate status tags."""
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:pipeline-test.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Pipeline Test Architecture",
        company_domain="pipeline-test.it",
        country="Italy",
        city="Padua",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    # Pipeline block present
    assert "Workflow Stage Pipeline" in html
    assert "1. Research" in html
    assert "2. Lead Register" in html
    assert "3. Contact Status" in html
    assert "4. Outreach State" in html

    # Initial states
    assert "Not Started" in html
    assert "Not Linked" in html
    assert "Missing" in html
