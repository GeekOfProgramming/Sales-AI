import hashlib
import os
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.sales_ui.router import get_console_services
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier
from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.schemas import StoredDraft
from sales_engine.sending.approval_service import compute_content_fingerprint


@pytest.fixture
def isolation_setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sales_outreach_test.db")
    research_db = str(tmp_path / "research_cache_test.db")

    monkeypatch.setenv("SALES_OUTREACH_DB", db_path)
    monkeypatch.setenv("SALESAI_DB_PATH", db_path)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_path)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", research_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    cand_store = CandidateStore(db_path=db_path)
    rev_store = ReviewStore(db_path=db_path)
    reg_store = LeadRegisterStore(db_path=research_db)

    return {
        "db_path": db_path,
        "research_db": research_db,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "reg_store": reg_store,
    }


def test_01_candidate_workspace_strict_draft_isolation(isolation_setup):
    """
    Test 1: Candidate A cannot see Candidate B's drafts in Company Workspace.
    Candidate A: energymep.it (cand:domain:energymep.it)
    Candidate B: acme.com (cand:domain:acme.com)
    Draft for Candidate B must NOT appear in Candidate A's workspace.
    """
    cand_store = isolation_setup["cand_store"]
    rev_store = isolation_setup["rev_store"]

    # Save Candidate A (Energymep)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:energymep.it",
        company_name="Energymep Srl",
        company_domain="energymep.it",
        country="Italy",
        discovery_status="qualified",
    ))

    # Save Candidate B (Acme)
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:acme.com",
        company_name="Acme BIM Corp",
        company_domain="acme.com",
        country="Italy",
        discovery_status="qualified",
    ))

    # Create approved draft for Candidate B
    draft_dict = {
        "draft_id": "draft:test-001",
        "revision": 1,
        "candidate_id": "cand:domain:acme.com",
        "lead_id": "domain:acme.com",
        "contact_id": "contact:jane",
        "recipient_name": "Jane Acme",
        "recipient_email": "jane@acme.com",
        "subject": "Acme BIM Partnership",
        "body": "Hello Jane, let us collaborate on BIM.",
        "approval_status": "approved",
        "send_status": "not_sent",
        "outreach_status": "approved",
    }
    draft_b = StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict))
    rev_store.save_draft(draft_b)

    client = TestClient(app)

    # 1. View Candidate A (Energymep)
    res_a = client.get("/sales/candidates/cand:domain:energymep.it")
    assert res_a.status_code == 200
    assert "draft:test-001" not in res_a.text
    assert "jane@acme.com" not in res_a.text
    assert "Approved Draft" not in res_a.text
    assert "Associated Outreach Drafts (0)" in res_a.text

    # 2. View Candidate B (Acme)
    res_b = client.get("/sales/candidates/cand:domain:acme.com")
    assert res_b.status_code == 200
    assert "draft:test-001" in res_b.text
    assert "jane@acme.com" in res_b.text
    assert "Approved Draft" in res_b.text
    assert "Associated Outreach Drafts (1)" in res_b.text


def test_02_legacy_unlinked_draft_does_not_leak_to_arbitrary_candidates(isolation_setup):
    """
    Test 2: Legacy drafts with only lead_id='domain:acme.com' (candidate_id IS NULL)
    must NOT appear under candidate 'cand:domain:energymep.it'.
    """
    cand_store = isolation_setup["cand_store"]
    rev_store = isolation_setup["rev_store"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:energymep.it",
        company_name="Energymep Srl",
        company_domain="energymep.it",
        country="Italy",
        discovery_status="qualified",
    ))

    # Save legacy draft without candidate_id
    draft_dict = {
        "draft_id": "draft:legacy-001",
        "revision": 1,
        "lead_id": "domain:acme.com",
        "contact_id": "contact:legacy",
        "recipient_name": "Legacy Jane",
        "recipient_email": "jane@acme.com",
        "subject": "Legacy Subject",
        "body": "Legacy Body",
        "approval_status": "approved",
        "send_status": "not_sent",
        "outreach_status": "approved",
    }
    legacy_draft = StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict))
    rev_store.save_draft(legacy_draft)

    client = TestClient(app)
    res = client.get("/sales/candidates/cand:domain:energymep.it")
    assert res.status_code == 200
    assert "draft:legacy-001" not in res.text
    assert "Associated Outreach Drafts (0)" in res.text
    assert "Approved Draft" not in res.text


def test_03_timeline_draft_isolation(isolation_setup):
    """
    Test 3: TimelineService does not pull drafts from unrelated companies
    even if company_name or other fields are evaluated.
    """
    cand_store = isolation_setup["cand_store"]
    rev_store = isolation_setup["rev_store"]
    from backend.sales_ui.timeline_service import TimelineService

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:energymep.it",
        company_name="Energymep Srl",
        company_domain="energymep.it",
    ))
    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:acme.com",
        company_name="Acme BIM Corp",
        company_domain="acme.com",
    ))

    draft_dict = {
        "draft_id": "draft:acme-only",
        "revision": 1,
        "candidate_id": "cand:domain:acme.com",
        "lead_id": "domain:acme.com",
        "contact_id": "c1",
        "recipient_name": "Jane",
        "recipient_email": "jane@acme.com",
        "subject": "Hello Acme",
        "body": "Body Acme",
        "approval_status": "approved",
    }
    rev_store.save_draft(StoredDraft(**draft_dict, content_hash=compute_content_fingerprint(draft_dict)))

    tl_svc = TimelineService(cand_store, rev_store)
    events_energymep = tl_svc.get_company_timeline("cand:domain:energymep.it")
    for e in events_energymep:
        ref = getattr(e, "entity_ref", None) or ""
        summ = getattr(e, "summary", None) or ""
        assert "draft:acme-only" not in ref
        assert "Hello Acme" not in summ

    events_acme = tl_svc.get_company_timeline("cand:domain:acme.com")
    found_acme_draft = any(
        "draft:acme-only" in (getattr(e, "entity_ref", None) or "")
        for e in events_acme
    )
    assert found_acme_draft


def test_04_global_draft_review_remains_functional(isolation_setup):
    """
    Test 4: Global Draft Review (/sales/drafts) continues to list all drafts accurately.
    """
    rev_store = isolation_setup["rev_store"]

    draft_dict1 = {
        "draft_id": "draft:global-001",
        "revision": 1,
        "candidate_id": "cand:c1",
        "lead_id": "cand:c1",
        "contact_id": "contact:1",
        "recipient_name": "Recip 1",
        "recipient_email": "recip1@test.it",
        "subject": "Subject 1",
        "body": "Body 1",
        "approval_status": "pending_review",
    }
    draft_dict2 = {
        "draft_id": "draft:global-002",
        "revision": 1,
        "candidate_id": "cand:c2",
        "lead_id": "cand:c2",
        "contact_id": "contact:2",
        "recipient_name": "Recip 2",
        "recipient_email": "recip2@test.it",
        "subject": "Subject 2",
        "body": "Body 2",
        "approval_status": "pending_review",
    }
    rev_store.save_draft(StoredDraft(**draft_dict1, content_hash=compute_content_fingerprint(draft_dict1)))
    rev_store.save_draft(StoredDraft(**draft_dict2, content_hash=compute_content_fingerprint(draft_dict2)))

    client = TestClient(app)
    res = client.get("/sales/drafts")
    assert res.status_code == 200
    assert "draft:global-001" in res.text
    assert "draft:global-002" in res.text


def test_05_viewing_candidate_workspace_causes_zero_mutations(isolation_setup):
    """
    Test 5: GET /sales/candidates/{candidate_id} causes zero database mutations.
    """
    cand_store = isolation_setup["cand_store"]
    db_path = isolation_setup["db_path"]

    cand_store.save_candidate(CandidateCompany(
        candidate_id="cand:domain:read-only.it",
        company_name="Read Only Spa",
        company_domain="read-only.it",
        country="Italy",
        discovery_status="qualified",
    ))

    def get_hash(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    before_hash = get_hash(db_path)

    client = TestClient(app)
    res = client.get("/sales/candidates/cand:domain:read-only.it")
    assert res.status_code == 200

    after_hash = get_hash(db_path)
    assert before_hash == after_hash, "Viewing company workspace must not mutate database"


def test_06_manual_outreach_log_isolation(isolation_setup):
    """
    Test 6: Manual outreach log records remain strictly isolated by candidate_id.
    """
    rev_store = isolation_setup["rev_store"]

    rev_store.log_manual_outreach(
        candidate_id="cand:domain:company-a.it",
        recipient_email="contact@company-a.it",
        subject="Outreach A",
        channel="email",
        logged_by="operator",
    )
    rev_store.log_manual_outreach(
        candidate_id="cand:domain:company-b.it",
        recipient_email="contact@company-b.it",
        subject="Outreach B",
        channel="email",
        logged_by="operator",
    )

    logs_a = rev_store.list_manual_outreach_logs(candidate_id="cand:domain:company-a.it")
    assert len(logs_a) == 1
    assert logs_a[0]["candidate_id"] == "cand:domain:company-a.it"
    assert logs_a[0]["recipient_email"] == "contact@company-a.it"

    logs_b = rev_store.list_manual_outreach_logs(candidate_id="cand:domain:company-b.it")
    assert len(logs_b) == 1
    assert logs_b[0]["candidate_id"] == "cand:domain:company-b.it"
    assert logs_b[0]["recipient_email"] == "contact@company-b.it"
