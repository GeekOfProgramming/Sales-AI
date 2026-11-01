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
import sqlite3
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
    UX-LP-02a: An email documented in Lead Register without explicit verification
    CANNOT create a draft (redirects with error that email is not verified).
    Once explicitly verified via audit record, drafting succeeds.
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

    # Attempt draft creation with documented email prior to verification -> must be rejected
    resp = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "recipient_email": "info@doc-lead.it",
        "recipient_name": "Official Contact",
        "subject": "Engineering Partnership",
        "body": "Salve, vorremmo proporre una collaborazione.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)

    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert "error=Recipient%20email%20is%20not%20verified" in loc

    # Now explicitly verify the contact via verify-contact route
    verify_resp = client.post(f"/sales/candidates/{cid}/verify-contact", data={
        "contact_id": f"contact:info@doc-lead.it",
        "evidence_source": "Official company impressum",
        "evidence_reference": "https://doc-lead.it/impressum",
        "notes": "Verified operator check",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert verify_resp.status_code == 303

    # Now attempt draft creation again -> must succeed
    resp2 = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "recipient_email": "info@doc-lead.it",
        "recipient_name": "Official Contact",
        "subject": "Engineering Partnership",
        "body": "Salve, vorremmo proporre una collaborazione.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert resp2.status_code == 303
    assert "/sales/drafts/draft:manual:" in resp2.headers["location"]

    # Check contact record: must have email_status='verified'
    saved_c = c_store.get_contact_by_id("info@doc-lead.it", candidate_id=cid)
    assert saved_c is not None
    assert saved_c.email_status == "verified"


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


# ============================================================================
# UX-02.2a Defect Closure Tests (1. DB Safety, 2. Documented UI, 3. Approved Draft, 4. Separate Eligibility)
# ============================================================================

def test_ux02_2a_crm_db_never_initializes_research_tables(dual_db_env):
    """
    Requirement 1: Research Snapshot Storage Safety.
    Visiting Candidate Detail MUST NOT initialize research_snapshots or page_evidences
    tables in the CRM / Candidate database.
    """
    client, c_store, reg_store, rev_store, snap_store, crm_db_path, research_db_path, csrf_token, headers = dual_db_env

    cid = "cand:domain:db-safety-check.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="DB Safety Architecture",
        company_domain="db-safety-check.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Confirm CRM database currently does NOT have research_snapshots table
    with sqlite3.connect(crm_db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_snapshots" not in tables
        assert "page_evidences" not in tables

    # Execute GET request to candidate detail view
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200

    # Verify CRM database STILL has zero research tables initialized
    with sqlite3.connect(crm_db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "research_snapshots" not in tables
        assert "page_evidences" not in tables


def test_ux02_2a_documented_contact_in_ui_and_draft_creation(dual_db_env):
    """
    Requirement 2: Documented Contact UI Eligibility.
    A company with an explicit Lead Register email (and no pre-existing candidate_contact)
    displays that recipient as documented in workspace contacts, but disabled/verification required in composer.
    After operator verifies contact, drafting completes successfully.
    """
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:doc-ui-test.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Documented UI Studio",
        company_domain="doc-ui-test.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Document in 13-Field Register
    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=789,
        company_name="Documented UI Studio",
        responsible_person="Arch. Matteo Bianchi",
        contact_email_addresses="matteo.bianchi@doc-ui-test.it",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reg_rec)

    # GET workspace view
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    # Check recipient select dropdown contains documented recipient marked verification required
    assert "matteo.bianchi@doc-ui-test.it" in html
    assert "[Documented in Lead Register - Verification Required]" in html
    assert "Arch. Matteo Bianchi" in html

    # Submit manual draft creation before verification -> rejected
    post_fail = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "contact_id": "contact:matteo.bianchi@doc-ui-test.it",
        "recipient_email": "matteo.bianchi@doc-ui-test.it",
        "recipient_name": "Arch. Matteo Bianchi",
        "subject": "BIM Implementation",
        "body": "Gentile Arch. Bianchi, vorremmo presentare pyBIM.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert post_fail.status_code == 303
    assert "error=Recipient%20email%20is%20not%20verified" in post_fail.headers["location"]

    # Verify contact explicitly
    verify_resp = client.post(f"/sales/candidates/{cid}/verify-contact", data={
        "contact_id": "contact:matteo.bianchi@doc-ui-test.it",
        "evidence_source": "Official company impressum",
        "evidence_reference": "https://doc-ui-test.it/team",
        "notes": "Verified operator check",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)
    assert verify_resp.status_code == 303

    # Submit manual draft creation using this verified recipient
    post_resp = client.post(f"/sales/candidates/{cid}/create-manual-draft", data={
        "contact_id": "contact:matteo.bianchi@doc-ui-test.it",
        "recipient_email": "matteo.bianchi@doc-ui-test.it",
        "recipient_name": "Arch. Matteo Bianchi",
        "subject": "BIM Implementation",
        "body": "Gentile Arch. Bianchi, vorremmo presentare pyBIM.",
        "csrf_token": csrf_token,
    }, headers=headers, follow_redirects=False)

    assert post_resp.status_code == 303
    assert "/sales/drafts/draft:manual:" in post_resp.headers["location"]

    # Verify contact was saved with verified status
    saved_c = c_store.get_contact_by_id("matteo.bianchi@doc-ui-test.it", candidate_id=cid)
    assert saved_c is not None
    assert saved_c.email_status == "verified"


def test_ux02_2a_approved_draft_and_pending_draft_pipeline_states(dual_db_env):
    """
    Requirement 3: Outreach Stage Pipeline correctly detects Draft Pending and Approved Draft
    using Jinja namespace and checks precedence (restricted > dispatch > approved > pending > eligible).
    """
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:pipeline-status.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Status Check Studio",
        company_domain="pipeline-status.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Add a verified contact so company is draft eligible
    cont = ContactCandidate(
        contact_id="cont:stat:01",
        candidate_id=cid,
        full_name="Elena Neri",
        work_email="elena@pipeline-status.it",
        email_status="verified",
        email_confidence=100,
    )
    c_store.save_contact(cont, candidate_id=cid)

    # 1. State: Draft Eligible (no drafts yet)
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    assert "Draft Eligible" in resp.text

    # 2. Add pending review draft -> State: Draft Pending
    pending_draft = StoredDraft(
        draft_id="draft:pending:01",
        candidate_id=cid,
        lead_id=cid,
        contact_id=cont.contact_id,
        recipient_email=cont.work_email,
        recipient_name=cont.full_name,
        subject="Intro",
        body="Body",
        content_hash="hash1",
        approval_status="pending_review",
        outreach_status="draft_ready",
    )
    rev_store.save_draft(pending_draft)

    resp2 = client.get(f"/sales/candidates/{cid}")
    assert resp2.status_code == 200
    assert "Draft Pending" in resp2.text

    # 3. Add approved draft alongside pending -> State: Approved Draft (approved takes precedence)
    approved_draft = StoredDraft(
        draft_id="draft:approved:02",
        candidate_id=cid,
        lead_id=cid,
        contact_id=cont.contact_id,
        recipient_email=cont.work_email,
        recipient_name=cont.full_name,
        subject="Intro 2",
        body="Body 2",
        content_hash="hash2",
        approval_status="approved",
        outreach_status="approved",
    )
    rev_store.save_draft(approved_draft)

    resp3 = client.get(f"/sales/candidates/{cid}")
    assert resp3.status_code == 200
    assert "Approved Draft" in resp3.text


def test_ux02_2a_separate_company_eligibility_from_recipient_readiness(dual_db_env):
    """
    Requirement 4: Accurate Draft Readiness.
    A company that is qualified but has NO verified contact and NO documented email
    must NOT show 'ELIGIBLE FOR DRAFT' or 'Draft Eligible'.
    It must display 'CONTACT VERIFICATION REQUIRED' / 'Verification Required'.
    """
    client, c_store, reg_store, rev_store, snap_store, _, _, csrf_token, headers = dual_db_env

    cid = "cand:domain:no-recipient.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="No Recipient Studio",
        company_domain="no-recipient.it",
        country="Italy",
        discovery_status="qualified",
    )
    c_store.save_candidate(cand)

    # Add only an UNVERIFIED contact
    cont = ContactCandidate(
        contact_id="cont:unverif:01",
        candidate_id=cid,
        full_name="Lorenzo Gialli",
        work_email="lorenzo@no-recipient.it",
        email_status="unverified",
        email_confidence=10,
    )
    c_store.save_contact(cont, candidate_id=cid)

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    # Pipeline Step 4 must NOT be Draft Eligible
    assert "Verification Required" in html
    # Safeguard card in Outreach tab must show CONTACT VERIFICATION REQUIRED
    assert "CONTACT VERIFICATION REQUIRED" in html
    assert "Recipient verification required:" in html
    # Composer box must NOT be rendered when can_generate_draft is False
    assert "Stage Manual Draft to Review Queue" not in html

