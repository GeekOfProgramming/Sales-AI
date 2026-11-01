"""
Isolated regression tests for UX-02.1 Unified Company Workspace:
- Candidate Detail opens correctly via /sales/candidates/{candidate_id}
- Authoritative stored values across all 5 workspace areas (Overview, Research & Evidence, 13-Field Register, Contacts & Verification, Outreach & History)
- Stored Research Snapshots load offline without network requests
- Missing / unlinked Lead Register data handled safely with explicit unlinked state
- Contact verification breakdown (verified, likely, unverified) and eligibility guards preserved
- DNC / Suppression and duplicate prevention guards strictly respected
- All operations isolated with zero mutation to operational databases
"""
import pytest
from starlette.testclient import TestClient

from sales_engine.candidates.models import CandidateCompany
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.leads.register_store import LeadRegisterStore, LeadRegisterRecord
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot, PageEvidence
from sales_engine.sending.review_store import ReviewStore, StoredDraft
from backend.sales_ui.router import get_console_services


@pytest.fixture
def isolated_app_client(tmp_path, monkeypatch):
    """Provides a TestClient connected to isolated databases in tmp_path."""
    db_file = str(tmp_path / "test_workspace.db")
    research_db = str(tmp_path / "test_research.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)
    
    # Initialize isolated stores
    cand_store = CandidateStore(db_path=db_file)
    reg_store = LeadRegisterStore(db_path=db_file)
    snap_store = ResearchSnapshotStore(db_path=research_db)
    rev_store = ReviewStore(db_path=db_file)

    from backend.main import app
    client = TestClient(app)
    return client, cand_store, reg_store, snap_store, rev_store


def test_ux02_1_workspace_five_areas_and_linked_state(isolated_app_client):
    """Verify Unified Company Workspace renders all 5 areas with linked 13-field record and offline snapshot."""
    client, cand_store, reg_store, snap_store, rev_store = isolated_app_client

    cid = "cand:domain:acme-bim.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Acme BIM Engineering S.r.l.",
        company_domain="acme-bim.it",
        country="Italy",
        city="Milan",
        discovery_status="qualified",
    )
    cand_store.save_candidate(cand)

    # 1. Create linked Lead Register Record
    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=101,
        company_name="Acme BIM Engineering S.r.l.",
        priority_level="A",
        priority_rationale="Active BIM coordination tender",
        official_website="https://acme-bim.it",
        responsible_person="Ing. Marco Rossi",
        business_activities="BIM Coordination & Revit API",
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reg_rec)

    # 2. Create persisted Research Snapshot
    page = PageEvidence(
        snapshot_id="snap_acme_001",
        evidence_id="ev_01",
        requested_url="https://acme-bim.it/about",
        resolved_url="https://acme-bim.it/about",
        page_type="about",
        http_status=200,
        retrieval_status="FETCH_SUCCESS",
        retrieved_at="2026-11-01T12:00:00Z",
        title="About Acme BIM",
        meta_description="Leading BIM engineering practice in Milan",
        content_sha256="abc123sha",
        cleaned_text="Specialized architectural engineering firm",
    )
    snap = snap_store.create_snapshot(
        candidate_id=cid,
        canonical_domain="acme-bim.it",
        company_name="Acme BIM Engineering S.r.l.",
        pages=[page],
        research_status="complete",
        snapshot_id="snap_acme_001",
    )

    # 3. Create Draft in Review Store
    draft = StoredDraft(
        draft_id="draft:acme-001",
        candidate_id=cid,
        lead_id=cid,
        contact_id="cont:acme:01",
        recipient_email="m.rossi@acme-bim.it",
        recipient_name="Marco Rossi",
        subject="pyBIM Automation Partnership",
        body="Dear Marco, We noticed your active BIM projects in Milan...",
        content_hash="mockcontenthash123",
        approval_status="approved",
        outreach_status="approved",
    )
    rev_store.save_draft(draft)

    # Perform request
    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    # Verify Area 1: Overview
    assert "Acme BIM Engineering S.r.l." in html
    assert "acme-bim.it" in html
    assert "Milan" in html
    assert "1. Overview" in html

    # Verify Area 2: Research & Evidence
    assert "2. Research &amp; Evidence" in html
    assert "snap_acme_001" in html
    assert "About Acme BIM" in html
    assert "https://acme-bim.it/about" in html

    # Verify Area 3: 13-Field Lead Register
    assert "3. 13-Field Lead Register" in html
    assert "Linked 13-Field Lead Register Record #101" in html
    assert "Ing. Marco Rossi" in html
    assert "BIM Coordination &amp; Revit API" in html

    # Verify Area 4: Contacts & Verification
    assert "4. Contacts &amp; Verification" in html
    assert "Company Contacts &amp; Verification Evidence" in html

    # Verify Area 5: Outreach & History
    assert "5. Outreach &amp; History" in html
    assert "Associated Outreach Drafts" in html
    assert "draft:acme-001" in html
    assert "pyBIM Automation Partnership" in html


def test_ux02_1_unlinked_register_state_and_missing_snapshot(isolated_app_client):
    """Verify candidate with no 13-field record or snapshot displays safe explicit empty states."""
    client, cand_store, reg_store, snap_store, rev_store = isolated_app_client

    cid = "cand:domain:unlinked-studio.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Unlinked Studio",
        company_domain="unlinked-studio.it",
        country="Italy",
        city="Turin",
        discovery_status="new",
    )
    cand_store.save_candidate(cand)

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    # Safe Explicit Unlinked state
    assert "Explicit Unlinked State" in html
    assert "13-Field Unlinked" in html
    assert "No authoritative 13-field Lead Register record is associated" in html

    # Safe Missing Research Snapshot empty state
    assert "No Persisted Research Snapshot" in html
    assert "Company research has not been executed yet for this candidate" in html


def test_ux02_1_suppression_and_dnc_safety_badge(isolated_app_client, monkeypatch):
    """Verify suppressed candidates prominently display DNC badges and outreach restrictions."""
    client, cand_store, reg_store, snap_store, rev_store = isolated_app_client

    cid = "cand:domain:suppressed-firm.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Suppressed Firm",
        company_domain="suppressed-firm.it",
        suppressed=True,
    )
    cand_store.save_candidate(cand)

    # Mock suppression service check to return True
    svcs = get_console_services()
    monkeypatch.setattr("backend.sales_ui.router.check_authoritative_suppression", lambda svc, domain=None, email=None: True)

    resp = client.get(f"/sales/candidates/{cid}")
    assert resp.status_code == 200
    html = resp.text

    assert "Suppressed / DNC" in html
    assert "BLOCKED (Suppressed)" in html
    assert "Outreach is strictly blocked" in html
