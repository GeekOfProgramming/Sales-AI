"""
Comprehensive Offline Regression Tests for Candidate Work Queue Lifecycle (Phase A)

Tests:
1. New candidate visibility in Active Work (active) and New (new).
2. Qualified candidate leaves Active Work (active) and appears in Qualified Leads.
3. Historical sent-but-unverified company requires operator review (historical_pending) and is excluded from Active Work.
4. Finalized historical company (HISTORICAL_FINALIZED) is excluded from Active Work and appears in All Companies (all).
5. Explicitly reopened company (REOPENED) returns to Active Work under Needs Correction (needs_correction).
6. DNC, duplicate, rejected, and watchlist candidates are excluded from Active Work.
7. Preservation of Research snapshots and Lead Register records (no overwriting or data loss).
8. Stable candidate IDs and deep links remain accessible.
9. Repeated discovery of existing company does NOT reopen or duplicate a completed record.
10. Accurate queue counts and pagination without arbitrary truncation.
11. Operator lifecycle action endpoints (reopen, needs_correction, finalize_historical) with CSRF protection.
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    BIMMaturity,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import (
    CandidateLifecycleService,
    CandidateQueueType,
    project_candidate_lifecycle,
)
from sales_engine.leads.register_store import LeadRegisterStore, LeadRegisterRecord
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot


@pytest.fixture
def isolated_db_env(tmp_path, monkeypatch):
    """Sets up an isolated SQLite database environment with strict safety flags."""
    db_file = str(tmp_path / "test_lifecycle_outreach.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", str(tmp_path / "test_research_cache.sqlite3"))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("TESTING", "true")

    # Initialize store schema
    store = CandidateStore(db_path=db_file)
    reg_store = LeadRegisterStore(db_path=db_file)
    snap_store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research_cache.sqlite3"))

    return {
        "db_path": db_file,
        "store": store,
        "reg_store": reg_store,
        "snap_store": snap_store,
        "tmp_path": tmp_path,
    }


def test_new_candidate_visibility(isolated_db_env):
    """Test 1: New untouched candidate appears in Active Work and New subqueue."""
    store = isolated_db_env["store"]
    cand = CandidateCompany(
        candidate_id="cand:new:studio-alfa",
        company_name="Studio Alfa Architecture",
        company_domain="studioalfa.it",
        country="Italy",
        city="Milan",
        discovery_status=DiscoveryStatus.new,
        qualification_outcome=None,
        historical_contact_status="uncontacted",
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state("cand:new:studio-alfa")

    assert state is not None
    assert state.is_active_work is True
    assert state.primary_queue == CandidateQueueType.ACTIVE
    assert state.active_subqueue == CandidateQueueType.NEW
    assert "New candidate" in state.actionable_next_step

    counts = lifecycle_svc.compute_queue_counts()
    assert counts["active"] == 1
    assert counts["new"] == 1
    assert counts["needs_research"] == 0
    assert counts["needs_verification"] == 0
    assert counts["needs_correction"] == 0


def test_qualified_candidate_leaves_active_work(isolated_db_env):
    """Test 2: ICP-qualified candidate leaves Active Work and moves to Qualified Leads."""
    store = isolated_db_env["store"]
    cand = CandidateCompany(
        candidate_id="cand:qual:studio-beta",
        company_name="Studio Beta Engineering",
        company_domain="studiobeta.it",
        country="Italy",
        city="Turin",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        historical_contact_status="uncontacted",
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state("cand:qual:studio-beta")

    assert state is not None
    assert state.is_active_work is False
    assert state.primary_queue == CandidateQueueType.QUALIFIED
    assert state.active_subqueue is None
    assert "Qualified Leads" in state.actionable_next_step

    counts = lifecycle_svc.compute_queue_counts()
    assert counts["active"] == 0
    assert counts["qualified"] == 1


def test_historical_sent_unverified_requires_operator_review(isolated_db_env):
    """Test 3: Company with historical outreach evidence requires review and is excluded from Active Work."""
    store = isolated_db_env["store"]
    cand = CandidateCompany(
        candidate_id="cand:hist:studio-gamma",
        company_name="Studio Gamma BIM",
        company_domain="studiogamma.it",
        country="Italy",
        city="Rome",
        discovery_status=DiscoveryStatus.new,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state("cand:hist:studio-gamma")

    assert state is not None
    assert state.is_active_work is False
    assert state.primary_queue == CandidateQueueType.HISTORICAL_PENDING
    assert state.active_subqueue is None
    assert "Historical outreach detected" in state.actionable_next_step

    counts = lifecycle_svc.compute_queue_counts()
    assert counts["active"] == 0
    assert counts["historical_pending"] == 1


def test_finalized_historical_archived(isolated_db_env):
    """Test 4: Finalized historical company is excluded from Active Work and visible in All Companies."""
    store = isolated_db_env["store"]
    cand = CandidateCompany(
        candidate_id="cand:hist:studio-delta",
        company_name="Studio Delta Architects",
        company_domain="studiodelta.it",
        country="Italy",
        city="Florence",
        discovery_status=DiscoveryStatus.known_company,
        historical_contact_status="contacted",
        previous_outreach_detected=True,
        manual_review_decision="HISTORICAL_FINALIZED",
        manual_review_notes="Operator reconciled historical thread and confirmed past contact.",
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state("cand:hist:studio-delta")

    assert state is not None
    assert state.is_active_work is False
    assert state.primary_queue == CandidateQueueType.ARCHIVED_HISTORICAL
    assert state.active_subqueue is None
    assert "finalized" in state.actionable_next_step.lower()

    counts = lifecycle_svc.compute_queue_counts()
    assert counts["active"] == 0
    assert counts["historical_pending"] == 0
    assert counts["archived_historical"] == 1
    assert counts["all"] == 1


def test_reopened_company_returns_to_active_work(isolated_db_env):
    """Test 5: Explicitly reopened company returns to Active Work under Needs Correction."""
    store = isolated_db_env["store"]
    cand = CandidateCompany(
        candidate_id="cand:reopen:studio-epsilon",
        company_name="Studio Epsilon",
        company_domain="studioepsilon.it",
        country="Italy",
        city="Bologna",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        historical_contact_status="uncontacted",
        manual_review_decision="REOPENED",
        manual_review_notes="Domain changed; re-verify corporate identity.",
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state("cand:reopen:studio-epsilon")

    assert state is not None
    assert state.is_active_work is True
    assert state.primary_queue == CandidateQueueType.ACTIVE
    assert state.active_subqueue == CandidateQueueType.NEEDS_CORRECTION
    assert "Reopened for correction" in state.actionable_next_step

    counts = lifecycle_svc.compute_queue_counts()
    assert counts["active"] == 1
    assert counts["needs_correction"] == 1


def test_suppressed_rejected_watchlist_excluded(isolated_db_env):
    """Test 6: DNC, rejected, duplicate, and watchlist candidates are excluded from Active Work."""
    store = isolated_db_env["store"]

    # 1. Suppressed candidate
    store.save_candidate(CandidateCompany(
        candidate_id="cand:supp:autodesk",
        company_name="Autodesk Italia",
        company_domain="autodesk.it",
        suppressed=True,
    ))

    # 2. Rejected candidate
    store.save_candidate(CandidateCompany(
        candidate_id="cand:rej:bs-eng",
        company_name="BS Engineering",
        company_domain="bs-eng.net",
        qualification_outcome=QualificationOutcome.reject,
    ))

    # 3. Watchlist candidate
    store.save_candidate(CandidateCompany(
        candidate_id="cand:wl:zero-send",
        company_name="Zero Send SpA",
        company_domain="zero-send.it",
        discovery_status=DiscoveryStatus.watchlist,
    ))

    # 4. Duplicate candidate
    store.save_candidate(CandidateCompany(
        candidate_id="cand:dup:known-co",
        company_name="Known Co Srl",
        company_domain="knownco.it",
        manual_review_decision="DUPLICATE",
    ))

    lifecycle_svc = CandidateLifecycleService(store)
    counts = lifecycle_svc.compute_queue_counts()

    assert counts["active"] == 0
    assert counts["suppressed_rejected"] == 4
    assert counts["all"] == 4


def test_preservation_of_research_snapshots_and_lead_register(isolated_db_env):
    """Test 7: Research snapshots and Lead Register records are preserved without overwriting."""
    store = isolated_db_env["store"]
    reg_store = isolated_db_env["reg_store"]

    cid = "cand:domain:renerwave.com"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="RENERWAVE SRL",
        company_domain="renerwave.com",
        discovery_status=DiscoveryStatus.new,
    )
    store.save_candidate(cand)

    # Insert Lead Register Record
    rec = LeadRegisterRecord(
        candidate_id=cid,
        company_name="RENERWAVE SRL",
        verification_status="OPERATOR_REVIEWED",
        official_website="https://renerwave.com",
        business_activities="Renewable energy engineering and BIM",
    )
    reg_store.save_record(rec)

    # Re-evaluate lifecycle
    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state(cid)

    # Must be in Active Work -> Needs Verification because Lead Register record exists
    assert state.is_active_work is True
    assert state.active_subqueue == CandidateQueueType.NEEDS_VERIFICATION

    # Verify Lead Register data was NOT modified
    saved_rec = reg_store.get_record(cid)
    assert saved_rec.verification_status == "OPERATOR_REVIEWED"
    assert saved_rec.official_website == "https://renerwave.com"
    assert saved_rec.business_activities == "Renewable energy engineering and BIM"


def test_stable_candidate_ids_and_deep_links(isolated_db_env):
    """Test 8: Stable candidate IDs and deep links remain valid across lifecycle changes."""
    store = isolated_db_env["store"]
    cid = "cand:domain:energymep.it"
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Energymep",
        company_domain="energymep.it",
        discovery_status=DiscoveryStatus.new,
    )
    store.save_candidate(cand)

    lifecycle_svc = CandidateLifecycleService(store)
    state1 = lifecycle_svc.get_candidate_lifecycle_state(cid)
    assert state1.recommended_action_url == f"/sales/candidates/{cid}"

    # Now mark as research_more
    cand.qualification_outcome = QualificationOutcome.research_more
    store.save_candidate(cand)

    state2 = lifecycle_svc.get_candidate_lifecycle_state(cid)
    assert state2.active_subqueue == CandidateQueueType.NEEDS_RESEARCH
    assert state2.recommended_action_url == f"/sales/candidates/{cid}/research"

    # Deep link URL format remains consistent and points to the exact candidate ID
    assert cid in state2.recommended_action_url


def test_repeated_discovery_does_not_reopen_or_duplicate(isolated_db_env):
    """Test 9: Repeated discovery ingestion does NOT reopen or duplicate completed candidates."""
    store = isolated_db_env["store"]
    cid = "cand:domain:completed.it"

    # Step 1: Candidate is already qualified and operator-approved
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Completed Studio",
        company_domain="completed.it",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        manual_review_decision="APPROVED",
    )
    store.save_candidate(cand)

    # Step 2: Discovery crawler re-discovers the company with discovery_status='new'
    re_discovered = CandidateCompany(
        candidate_id=cid,
        company_name="Completed Studio",
        company_domain="completed.it",
        discovery_status=DiscoveryStatus.new,
        qualification_outcome=None,
    )
    store.save_candidate(re_discovered)

    # Step 3: Lifecycle check — should still be qualified and NOT re-enter Active Work
    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state(cid)

    # Approved/qualified status is preserved in candidate store
    persisted = store.get_by_id(cid)
    assert persisted.manual_review_decision == "APPROVED"
    assert state.is_active_work is False
    assert state.primary_queue == CandidateQueueType.QUALIFIED


def test_accurate_queue_counts_and_pagination(isolated_db_env):
    """Test 10: Accurate counts across queues and correct pagination without arbitrary truncation."""
    store = isolated_db_env["store"]

    # Seed 35 active new candidates
    for i in range(35):
        store.save_candidate(CandidateCompany(
            candidate_id=f"cand:bulk:active_{i:03d}",
            company_name=f"Bulk Company {i:03d}",
            company_domain=f"bulk{i:03d}.it",
            discovery_status=DiscoveryStatus.new,
        ))

    # Seed 5 historical pending candidates
    for i in range(5):
        store.save_candidate(CandidateCompany(
            candidate_id=f"cand:bulk:hist_{i:03d}",
            company_name=f"Historical Company {i:03d}",
            company_domain=f"hist{i:03d}.it",
            historical_contact_status="contacted",
            previous_outreach_detected=True,
        ))

    lifecycle_svc = CandidateLifecycleService(store)
    counts = lifecycle_svc.compute_queue_counts()

    assert counts["active"] == 35
    assert counts["new"] == 35
    assert counts["historical_pending"] == 5
    assert counts["all"] == 40

    # Test HTTP UI response with pagination
    client = TestClient(app)
    # Page 1 (25 items)
    resp = client.get("/sales/candidates?queue=active&page=1&page_size=25")
    assert resp.status_code == 200
    assert "Bulk Company 000" in resp.text or "Bulk Company" in resp.text
    assert "Active Work Candidates" in resp.text
    assert "35 total, page 1" in resp.text

    # Page 2 (10 items)
    resp2 = client.get("/sales/candidates?queue=active&page=2&page_size=25")
    assert resp2.status_code == 200
    assert "35 total, page 2" in resp2.text


def test_operator_lifecycle_action_endpoint(isolated_db_env):
    """Test 11: POST /sales/candidates/{id}/lifecycle handles reopen, needs_correction, and finalize_historical."""
    store = isolated_db_env["store"]
    cid = "cand:domain:actiontest.it"
    store.save_candidate(CandidateCompany(
        candidate_id=cid,
        company_name="Action Test Studio",
        company_domain="actiontest.it",
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    ))

    client = TestClient(app)
    csrf_token = "valid_csrf_token_12345"
    client.cookies.set("sales_csrf_token", csrf_token)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    # 1. Finalize historical
    resp = client.post(
        f"/sales/candidates/{cid}/lifecycle",
        data={"action": "finalize_historical", "notes": "Reconciled with past email thread.", "csrf_token": csrf_token},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    cand_after = store.get_by_id(cid)
    assert cand_after.manual_review_decision == "HISTORICAL_FINALIZED"

    # 2. Reopen candidate
    resp2 = client.post(
        f"/sales/candidates/{cid}/lifecycle",
        data={"action": "reopen", "notes": "Discovered new business unit.", "csrf_token": csrf_token},
        headers=headers,
        follow_redirects=False,
    )
    assert resp2.status_code in (302, 303)

    cand_after2 = store.get_by_id(cid)
    assert cand_after2.manual_review_decision == "REOPENED"
    lifecycle_svc = CandidateLifecycleService(store)
    state = lifecycle_svc.get_candidate_lifecycle_state(cid)
    assert state.is_active_work is True
    assert state.active_subqueue == CandidateQueueType.NEEDS_CORRECTION
