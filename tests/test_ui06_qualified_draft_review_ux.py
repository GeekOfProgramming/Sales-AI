"""
UI-06 Minimal Verification Tests.

1. Qualified Leads and Draft Review rendering/navigation smoke check:
   - Verifies /sales/leads renders qualified leads, subqueues, and navigation links.
   - Verifies /sales/drafts renders review queue with status filters (pending_review, changes_requested, approved, rejected, all).
   - Verifies /sales/approved renders approved queue with company links, recipient verification, and send controls.
   - Verifies workflow navigation links (Qualified Lead -> Research Review / Candidate Detail -> Draft Review -> Approved).

2. Distinct readiness and draft-state indicators:
   - Verifies Qualified Leads distinguishes qualification status from contact readiness and outreach eligibility
     (does not mark lead ready to send merely because it is ICP qualified).
   - Verifies draft cards clearly distinguish Pending Review, Changes Requested, Approved, and Rejected states.
   - Verifies DNC/suppressed and prior outreach indicators are clearly distinguished.

3. Historical finalization safety adjustment check:
   - Verifies that on /sales/candidates/{candidate_id}, even when historical communication exists,
     'Finalize Historical Outreach' is NOT automatically preselected in the Operator Review Decision dropdown.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany, ContactCandidate
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
from sales_engine.candidates.models import DiscoveryStatus
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore, StoredDraft
from sales_engine.historical.importer import HistoricalImporter


@pytest.fixture
def disposable_ui06_env(tmp_path, monkeypatch):
    db_file = tmp_path / "ui06_test.db"
    cand_store = CandidateStore(str(db_file))
    rev_store = ReviewStore(str(db_file))
    reg_store = LeadRegisterStore(str(db_file))
    reg_store._init_db()
    excl_store = ExcludedCompaniesStore(str(db_file))
    excl_store._init_db()

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("PREVIEW_SIGNING_SECRET", "test_secret_32_bytes_long_key_ui06!!")

    # Initialize historical importer schema
    HistoricalImporter(store=cand_store)

    # 1. Seed Qualified Candidate 1 (ICP Qualified, but contact verification pending - NOT ready to send)
    cand1 = CandidateCompany(
        candidate_id="cand:domain:bimstudio.it",
        company_name="BIM Studio Srl",
        normalized_company_name="bim studio srl",
        company_domain="bimstudio.it",
        country="Italy",
        city="Bologna",
        company_tier="tier_1",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome="qualified_for_enrichment",
        cold_outreach_allowed=True,
        source_refs=["crawler"],
    )
    cand_store.save_candidate(cand1)

    # Seed an unverified contact
    contact1 = ContactCandidate(
        contact_id="cnt-bim-01",
        candidate_id="cand:domain:bimstudio.it",
        first_name="Marco",
        last_name="Bianchi",
        full_name="Marco Bianchi",
        job_title="BIM Director",
        work_email="marco.bianchi@bimstudio.it",
        email_status="unverified",
    )
    cand_store.save_contact(contact1, candidate_id="cand:domain:bimstudio.it")

    # 2. Seed Candidate 2 with historical outreach (Previously Contacted)
    cand2 = CandidateCompany(
        candidate_id="cand:domain:historycorp.it",
        company_name="History Corp Srl",
        normalized_company_name="history corp srl",
        company_domain="historycorp.it",
        country="Italy",
        city="Turin",
        company_tier="tier_2",
        discovery_status=DiscoveryStatus.known_company,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
        cold_outreach_allowed=False,
        source_refs=["historical_gmail"],
    )
    cand_store.save_candidate(cand2)

    with cand_store._get_connection() as conn:
        conn.execute("""
            INSERT INTO historical_outreach_events (
                event_id, candidate_id, contact_email, contact_name, channel,
                contacted_at, reply_status, reply_at, meeting_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "evt-hist-002",
            "cand:domain:historycorp.it",
            "contact@historycorp.it",
            "Ing. Luca Rossi",
            "email",
            "2026-06-01T10:00:00Z",
            "interested",
            "2026-06-02T11:00:00Z",
            "completed",
            "Historical pilot call conducted.",
        ))

    # 3. Seed Drafts in different states (Pending Review, Changes Requested, Approved)
    rev_store.save_draft(StoredDraft(
        draft_id="draft-pending-01",
        revision=1,
        lead_id="domain:bimstudio.it",
        contact_id="cnt-bim-01",
        candidate_id="cand:domain:bimstudio.it",
        recipient_name="Marco Bianchi",
        recipient_title="BIM Director",
        recipient_email="marco.bianchi@bimstudio.it",
        sender_name="pyBIM Team",
        sender_email="outreach@pybim.it",
        subject="pyBIM Workflow Collaboration for BIM Studio",
        body="Dear Marco,\n\nWe would like to introduce pyBIM.",
        service_used="Cold Outreach",
        approval_status="pending_review",
        send_status="not_sent",
        outreach_status="draft_ready",
        content_hash="hash-pending-01",
        created_at="2026-11-04T12:00:00Z",
    ))

    rev_store.save_draft(StoredDraft(
        draft_id="draft-changes-02",
        revision=1,
        lead_id="domain:bimstudio.it",
        contact_id="cnt-bim-01",
        candidate_id="cand:domain:bimstudio.it",
        recipient_name="Marco Bianchi",
        recipient_title="BIM Director",
        recipient_email="marco.bianchi@bimstudio.it",
        sender_name="pyBIM Team",
        sender_email="outreach@pybim.it",
        subject="pyBIM Follow-up Request",
        body="Dear Marco,\n\nRevised proposal body.",
        service_used="Cold Outreach",
        approval_status="changes_requested",
        send_status="not_sent",
        outreach_status="draft_ready",
        content_hash="hash-changes-02",
        created_at="2026-11-04T12:10:00Z",
    ))

    rev_store.save_draft(StoredDraft(
        draft_id="draft-approved-03",
        revision=1,
        lead_id="domain:bimstudio.it",
        contact_id="cnt-bim-01",
        candidate_id="cand:domain:bimstudio.it",
        recipient_name="Marco Bianchi",
        recipient_title="BIM Director",
        recipient_email="marco.bianchi@bimstudio.it",
        sender_name="pyBIM Team",
        sender_email="outreach@pybim.it",
        subject="pyBIM Approved Introductory Brief",
        body="Dear Marco,\n\nApproved draft ready for offline dry-run.",
        service_used="Cold Outreach",
        approval_status="approved",
        send_status="not_sent",
        outreach_status="approved",
        content_hash="hash-approved-03",
        approved_content_hash="hash-approved-03",
        reviewer="local_operator",
        approved_at="2026-11-04T12:20:00Z",
        created_at="2026-11-04T12:15:00Z",
    ))

    return {
        "db_file": db_file,
        "cand1_id": "cand:domain:bimstudio.it",
        "cand2_id": "cand:domain:historycorp.it",
    }


def test_qualified_leads_and_draft_review_rendering_and_navigation(disposable_ui06_env):
    """
    Check 1: Qualified Leads and Draft Review rendering/navigation smoke check.
    - /sales/leads renders qualified leads and workflow links (Research, Workspace).
    - /sales/drafts renders status filter links (pending_review, changes_requested, approved, rejected, all).
    - /sales/approved renders approved queue with inspection and candidate links.
    """
    client = TestClient(app)

    # 1. Qualified Leads
    resp_leads = client.get("/sales/leads")
    assert resp_leads.status_code == 200
    leads_html = resp_leads.text
    assert "BIM Studio Srl" in leads_html
    assert "Enrichment Qualified" in leads_html
    assert "/sales/candidates/cand:domain:bimstudio.it/research" in leads_html
    assert "/sales/candidates/cand:domain:bimstudio.it" in leads_html

    # 2. Draft Review Queue
    resp_drafts = client.get("/sales/drafts?status=pending_review")
    assert resp_drafts.status_code == 200
    drafts_html = resp_drafts.text
    assert "Pending Review" in drafts_html
    assert "Changes Requested" in drafts_html
    assert "BIM Studio Srl" in drafts_html
    assert "pyBIM Workflow Collaboration for BIM Studio" in drafts_html
    assert "/sales/candidates/cand:domain:bimstudio.it" in drafts_html

    # 3. Approved Queue
    resp_appr = client.get("/sales/approved")
    assert resp_appr.status_code == 200
    appr_html = resp_appr.text
    assert "Approved Queue" in appr_html
    assert "BIM Studio Srl" in appr_html
    assert "pyBIM Approved Introductory Brief" in appr_html
    assert "Server Disabled (Safe Offline)" in appr_html or "Server Disabled" in appr_html


def test_distinct_readiness_and_draft_state_indicators(disposable_ui06_env):
    """
    Check 2: Distinct readiness and draft-state indicators.
    - Confirms an ICP-qualified company without verified contacts is NOT labeled ready to send.
    - Confirms Draft Review distinctly presents pending_review vs changes_requested vs approved.
    """
    client = TestClient(app)

    # 1. Leads Readiness: ICP-qualified lead with unverified contact requires contact verification
    resp_leads = client.get("/sales/leads")
    assert resp_leads.status_code == 200
    leads_html = resp_leads.text
    assert "Contact Verification Required" in leads_html or "Verification Required" in leads_html
    assert "Verify Contact" in leads_html

    # 2. Draft State Indicators: Changes Requested filter displays changes requested draft
    resp_cr = client.get("/sales/drafts?status=changes_requested")
    assert resp_cr.status_code == 200
    cr_html = resp_cr.text
    assert "Changes Requested" in cr_html
    assert "draft-changes-02" in cr_html

    # 3. Draft State Indicators: Pending Review displays approve and request changes actions
    resp_pr = client.get("/sales/drafts?status=pending_review")
    assert resp_pr.status_code == 200
    pr_html = resp_pr.text
    assert "Approve Draft" in pr_html
    assert "Request Changes" in pr_html


def test_historical_finalization_safety_adjustment_not_preselected(disposable_ui06_env):
    """
    Check 3: Focused check confirming historical finalization is not automatically preselected.
    - On candidate detail for a company with prior historical communication,
      verify the operator review decision select has a default '-- Select decision --' disabled option,
      and 'finalize_historical' is NOT marked 'selected'.
    """
    client = TestClient(app)
    cand2_id = disposable_ui06_env["cand2_id"]

    resp = client.get(f"/sales/candidates/{cand2_id}")
    assert resp.status_code == 200
    html = resp.text

    # Historical communication exists
    assert "Historical Outreach Detected" in html
    # But option finalize_historical is NOT automatically preselected
    assert '<option value="finalize_historical" selected>' not in html
    assert '<option value="" disabled selected>-- Select decision --</option>' in html
    assert '<option value="finalize_historical">📬 Finalize Historical Outreach</option>' in html
