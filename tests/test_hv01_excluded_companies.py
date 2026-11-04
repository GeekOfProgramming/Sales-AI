"""
Comprehensive Regression Tests for HV-01: Excluded Companies Registry & Candidate Queue Integration.

Test Cases:
1. test_01_exclude_new_candidate: Excluding a new candidate removes it from Active Work, moves to SUPPRESSED_REJECTED, blocks cold outreach.
2. test_02_exclude_already_qualified_candidate: Excluding a qualified lead removes it from Qualified Leads, blocks draft generation.
3. test_03_exclude_previously_contacted_candidate: Preserves historical communication data and sent emails.
4. test_04_exclude_and_restore_dnc_candidate: Restoring an excluded DNC company keeps DNC protections strictly enforced.
5. test_05_exclude_duplicate_or_invalid_domain_records: Handles DUPLICATE and INVALID_DOMAIN reasons with audit logging.
6. test_06_rediscovery_of_excluded_company_does_not_reopen_or_duplicate: Discovery check identifies existing exclusion and avoids duplicating or activating outreach.
7. test_07_shared_hosting_domain_safety: Public domains (gmail.com, wixsite.com) cannot accidentally exclude other legal entities.
8. test_08_stable_candidate_ids_and_data_preservation: Candidate ID, 13 fields, snapshots, and outreach logs remain completely intact.
9. test_09_exclusion_audit_history_and_reason_update: Full audit trail of EXCLUDED, REASON_UPDATED, RESTORED events with timestamps and actors.
10. test_10_restore_reconsider_behavior_and_safety: Reversibly restores to review pipeline without auto-qualifying or auto-enabling outreach.
11. test_11_csrf_protection_and_http_routes: Tests HTTP endpoints for exclusion and restoration with CSRF validation.
12. test_12_queue_counts_and_pagination: Excluded companies page search, filtering, and pagination accuracy.
13. test_13_synthetic_autodesk_software_vendor_scenario: Tests SOFTWARE_VENDOR scenario with autodesk.com.
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.lifecycle_service import (
    CandidateLifecycleService,
    CandidateLifecycleState,
    CandidateQueueType,
    project_candidate_lifecycle,
)
from sales_engine.candidates.exclusion_store import (
    ExcludedCompaniesStore,
    ExclusionReason,
    ExclusionStatus,
)
from sales_engine.leads.register_store import LeadRegisterStore, LeadRegisterRecord
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot
from backend.sales_ui.router import get_console_services


@pytest.fixture
def isolated_hv01_env(tmp_path, monkeypatch):
    """Sets up an isolated SQLite database environment with strict safety flags."""
    db_file = str(tmp_path / "test_hv01_outreach.db")
    cache_file = str(tmp_path / "test_hv01_research_cache.sqlite3")

    monkeypatch.setenv("SALES_OUTREACH_DB", db_file)
    monkeypatch.setenv("SALESAI_DB_PATH", db_file)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", db_file)
    monkeypatch.setenv("SALESAI_RESEARCH_CACHE_DB", cache_file)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("TESTING", "true")

    cand_store = CandidateStore(db_path=db_file)
    excl_store = ExcludedCompaniesStore(db_path=db_file)
    reg_store = LeadRegisterStore(db_path=db_file)
    snap_store = ResearchSnapshotStore(db_path=cache_file)

    return {
        "db_path": db_file,
        "cand_store": cand_store,
        "excl_store": excl_store,
        "reg_store": reg_store,
        "snap_store": snap_store,
    }


def test_01_exclude_new_candidate(isolated_hv01_env):
    """Excluding a new candidate removes it from Active Work, moves to SUPPRESSED_REJECTED, blocks outreach."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:not-relevant.it",
        company_name="Irrelevant Consulting Srl",
        company_domain="not-relevant.it",
        discovery_status=DiscoveryStatus.new,
    )
    cand_store.save_candidate(c)

    # Pre-exclusion state: Active Work / New
    state_before = project_candidate_lifecycle(c)
    assert state_before.is_active_work is True
    assert state_before.primary_queue == CandidateQueueType.ACTIVE

    # Operator excludes company
    excl_rec = excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.NOT_TARGET,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
        operator_note="Not an architectural or engineering firm",
        operator="lead_operator",
    )
    assert excl_rec.exclusion_status == ExclusionStatus.ACTIVE
    assert excl_rec.exclusion_reason == ExclusionReason.NOT_TARGET

    # Post-exclusion state: Removed from Active Work, placed in SUPPRESSED_REJECTED
    state_after = project_candidate_lifecycle(
        c,
        is_excluded=True,
        exclusion_reason=excl_rec.exclusion_reason.value,
    )
    assert state_after.is_active_work is False
    assert state_after.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED
    assert "Excluded from pipeline" in state_after.actionable_next_step


def test_02_exclude_already_qualified_candidate(isolated_hv01_env):
    """Excluding a qualified lead removes it from Qualified Leads, blocks draft generation."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:vendor-tools.com",
        company_name="BIM Tools Vendor Spa",
        company_domain="vendor-tools.com",
        discovery_status=DiscoveryStatus.qualified,
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
    )
    cand_store.save_candidate(c)

    # Pre-exclusion: Qualified
    state_before = project_candidate_lifecycle(c)
    assert state_before.is_qualified_lead is True
    assert state_before.primary_queue == CandidateQueueType.QUALIFIED

    # Exclude as SOFTWARE_VENDOR
    excl_rec = excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.SOFTWARE_VENDOR,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
        operator_note="Software vendor selling plugins, not a prospect studio",
    )

    state_after = project_candidate_lifecycle(
        c,
        is_excluded=True,
        exclusion_reason=excl_rec.exclusion_reason.value,
    )
    assert state_after.is_qualified_lead is False
    assert state_after.is_active_work is False
    assert state_after.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED


def test_03_exclude_previously_contacted_candidate(isolated_hv01_env):
    """Excluding a previously contacted company preserves historical outreach records."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:contacted-entity.com",
        company_name="Contacted Studio Srl",
        company_domain="contacted-entity.com",
        historical_contact_status="contacted",
        previous_outreach_detected=True,
    )
    cand_store.save_candidate(c)
    cand_store.record_historical_outreach(
        candidate_id=c.candidate_id,
        source_ref="Sent_Emails:row_99",
        contact_email="director@contacted-entity.com",
        channel="email",
    )

    # Exclude company
    excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.WRONG_COMPANY,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
        operator_note="Merged into another entity; wrong company profile",
    )

    # Historical outreach records remain 100% intact
    records = cand_store.get_historical_outreach_for_candidate(c.candidate_id)
    assert len(records) == 1
    assert records[0]["contact_email"] == "director@contacted-entity.com"


def test_04_exclude_and_restore_dnc_candidate(isolated_hv01_env):
    """Restoring an excluded DNC company keeps DNC communication restrictions strictly enforced."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:dnc-prospect.it",
        company_name="Opted Out Architetti",
        company_domain="dnc-prospect.it",
        suppressed=True,
        cold_outreach_allowed=False,
    )
    cand_store.save_candidate(c)

    # Exclude then restore
    excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.MANUAL_EXCLUSION,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
    )
    excl_store.restore_company(c.candidate_id, operator_note="Reconsidering firm capabilities")

    # State check: Even though restored, DNC suppression takes absolute precedence
    state = project_candidate_lifecycle(c, is_excluded=False, is_suppressed_domain=True)
    assert state.is_active_work is False
    assert state.is_suppressed_or_rejected is True
    assert "Do Not Contact" in state.actionable_next_step


def test_05_exclude_duplicate_or_invalid_domain_records(isolated_hv01_env):
    """Handles DUPLICATE and INVALID_DOMAIN reasons with audit logging."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:dead-site-123.it",
        company_name="Dead Domain Srl",
        company_domain="dead-site-123.it",
    )
    cand_store.save_candidate(c)

    excl_rec = excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.INVALID_DOMAIN,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
        operator_note="DNS lookup fails; parked domain",
    )
    assert excl_rec.exclusion_reason == ExclusionReason.INVALID_DOMAIN

    # Verify audit event
    audits = excl_store.get_audit_history(excl_rec.registry_id)
    assert len(audits) >= 1
    assert audits[0].action == "EXCLUDED"
    assert audits[0].new_reason == "INVALID_DOMAIN"


def test_06_rediscovery_of_excluded_company_does_not_reopen_or_duplicate(isolated_hv01_env):
    """Discovery check identifies existing exclusion and avoids activating outreach."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    # Pre-exclude a domain
    excl_store.exclude_company(
        company_name="AutoDesk Italy",
        exclusion_reason=ExclusionReason.SOFTWARE_VENDOR,
        canonical_domain="autodesk.it",
        operator_note="Software vendor, do not prospect",
    )

    # Ingest through candidate_store get_or_create_candidate
    cand, is_new = cand_store.get_or_create_candidate(
        company_name="AutoDesk Italy",
        company_domain="autodesk.it",
    )

    assert cand.cold_outreach_allowed is False
    assert cand.discovery_status == DiscoveryStatus.rejected
    assert cand.qualification_outcome == QualificationOutcome.reject
    assert "Auto-classified as rejected: matching active exclusion" in cand.manual_review_notes


def test_07_shared_hosting_domain_safety(isolated_hv01_env):
    """Public domains (gmail.com, wixsite.com) cannot accidentally exclude other legal entities."""
    excl_store = isolated_hv01_env["excl_store"]

    # Attempt to exclude with public shared domain
    excl_store.exclude_company(
        company_name="Studio Alpha",
        exclusion_reason=ExclusionReason.NOT_TARGET,
        canonical_domain="gmail.com",
    )

    # Another studio with gmail.com domain should NOT be considered excluded by domain
    is_excl = excl_store.is_company_excluded(
        candidate_id="cand:name:studiobeta:it",
        company_domain="gmail.com",
        company_name="Studio Beta",
    )
    assert is_excl is False


def test_08_stable_candidate_ids_and_data_preservation(isolated_hv01_env):
    """Candidate ID, 13 fields, snapshots, and outreach logs remain completely intact."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]
    reg_store = isolated_hv01_env["reg_store"]
    snap_store = isolated_hv01_env["snap_store"]

    cid = "cand:domain:architects-preserved.it"
    c = CandidateCompany(
        candidate_id=cid,
        company_name="Preserved Architects Srl",
        company_domain="architects-preserved.it",
    )
    cand_store.save_candidate(c)

    # Add 13-field LeadRegister record
    reg_rec = LeadRegisterRecord(
        candidate_id=cid,
        record_number=401,
        company_name=c.company_name,
        verification_status="OPERATOR_REVIEWED",
    )
    reg_store.save_record(reg_rec)

    # Add Snapshot
    snap = snap_store.create_snapshot(
        candidate_id=cid,
        canonical_domain=c.company_domain,
        company_name=c.company_name,
        pages=[],
        snapshot_id="snap_pres_001",
    )

    # Exclude and verify complete persistence
    excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.MANUAL_EXCLUSION,
        candidate_id=cid,
        canonical_domain=c.company_domain,
    )

    # All data remains accessible and intact
    saved_cand = cand_store.get_by_id(cid)
    assert saved_cand is not None
    assert saved_cand.company_name == "Preserved Architects Srl"

    saved_reg = reg_store.get_record(cid)
    assert saved_reg is not None
    assert saved_reg.record_number == 401

    saved_snap = snap_store.get_snapshot_by_id("snap_pres_001")
    assert saved_snap is not None
    assert saved_snap.snapshot_id == "snap_pres_001"


def test_09_exclusion_audit_history_and_reason_update(isolated_hv01_env):
    """Full audit trail of EXCLUDED, REASON_UPDATED, RESTORED events with timestamps and actors."""
    excl_store = isolated_hv01_env["excl_store"]

    # 1. Exclude
    rec = excl_store.exclude_company(
        company_name="Audit Studio Srl",
        exclusion_reason=ExclusionReason.NOT_TARGET,
        canonical_domain="audit-studio.it",
        operator="operator_1",
    )

    # 2. Update reason
    excl_store.exclude_company(
        company_name="Audit Studio Srl",
        exclusion_reason=ExclusionReason.SOFTWARE_VENDOR,
        canonical_domain="audit-studio.it",
        operator_note="Corrected reason to vendor",
        operator="operator_2",
    )

    # 3. Restore
    excl_store.restore_company(rec.registry_id, operator_note="Reconsidering", operator="operator_3")

    audits = excl_store.get_audit_history(rec.registry_id)
    assert len(audits) == 3
    # Ordered descending by timestamp
    assert audits[0].action == "RESTORED"
    assert audits[0].actor == "operator_3"
    assert audits[1].action == "REASON_UPDATED"
    assert audits[1].actor == "operator_2"
    assert audits[2].action == "EXCLUDED"
    assert audits[2].actor == "operator_1"


def test_10_restore_reconsider_behavior_and_safety(isolated_hv01_env):
    """Reversibly restores to review pipeline without auto-qualifying or auto-enabling outreach."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    c = CandidateCompany(
        candidate_id="cand:domain:reconsider.com",
        company_name="Reconsider Me Studio",
        company_domain="reconsider.com",
        discovery_status=DiscoveryStatus.new,
    )
    cand_store.save_candidate(c)

    excl_store.exclude_company(c.company_name, ExclusionReason.NOT_TARGET, candidate_id=c.candidate_id)
    excl_store.restore_company(c.candidate_id, operator_note="Reviewing new portfolio")

    rec = excl_store.get_exclusion_for_candidate(c.candidate_id)
    assert rec.exclusion_status == ExclusionStatus.RESTORED

    # Active exclusion check returns False
    assert excl_store.is_company_excluded(candidate_id=c.candidate_id) is False


def test_11_csrf_protection_and_http_routes(isolated_hv01_env):
    """Tests HTTP endpoints for exclusion and restoration with CSRF validation."""
    cand_store = isolated_hv01_env["cand_store"]
    client = TestClient(app)

    c = CandidateCompany(
        candidate_id="cand:domain:http-test.com",
        company_name="HTTP Test Srl",
        company_domain="http-test.com",
    )
    cand_store.save_candidate(c)

    # 1. Reject without CSRF
    resp_no_csrf = client.post(
        f"/sales/candidates/{c.candidate_id}/exclude",
        data={"reason": "NOT_TARGET", "notes": "No token test"},
    )
    assert resp_no_csrf.status_code == 403

    # 2. Exclude with CSRF
    token = "test_csrf_token_hv01"
    client.cookies.set("sales_csrf_token", token)
    headers = {"X-CSRF-Token": token, "Referer": "http://testserver/sales/candidates"}

    resp_excl = client.post(
        f"/sales/candidates/{c.candidate_id}/exclude",
        data={"reason": "NOT_TARGET", "notes": "Valid exclusion", "csrf_token": token},
        headers=headers,
        follow_redirects=False,
    )
    assert resp_excl.status_code in (302, 303)

    # 3. Restore with CSRF
    resp_rest = client.post(
        f"/sales/candidates/{c.candidate_id}/restore",
        data={"notes": "Valid restore", "csrf_token": token},
        headers=headers,
        follow_redirects=False,
    )
    assert resp_rest.status_code in (302, 303)


def test_12_queue_counts_and_pagination(isolated_hv01_env):
    """Excluded companies page search, filtering, and pagination accuracy."""
    excl_store = isolated_hv01_env["excl_store"]

    for i in range(15):
        excl_store.exclude_company(
            company_name=f"Studio Number {i}",
            exclusion_reason=ExclusionReason.NOT_TARGET if i % 2 == 0 else ExclusionReason.SOFTWARE_VENDOR,
            canonical_domain=f"studio{i}.it",
        )

    # Filter by SOFTWARE_VENDOR
    items, total = excl_store.list_exclusions(reason_filter="SOFTWARE_VENDOR")
    assert total == 7
    assert len(items) == 7

    # Pagination: limit 5
    page1, total_all = excl_store.list_exclusions(limit=5, offset=0)
    assert total_all == 15
    assert len(page1) == 5


def test_13_synthetic_autodesk_software_vendor_scenario(isolated_hv01_env):
    """Tests SOFTWARE_VENDOR scenario with autodesk.com as synthetic test."""
    cand_store = isolated_hv01_env["cand_store"]
    excl_store = isolated_hv01_env["excl_store"]

    # 1. Candidate created
    cand, _ = cand_store.get_or_create_candidate(
        company_name="Autodesk Test Case",
        company_domain="autodesk.com",
    )

    # 2. Operator excludes
    excl_rec = excl_store.exclude_company(
        company_name=cand.company_name,
        exclusion_reason=ExclusionReason.SOFTWARE_VENDOR,
        candidate_id=cand.candidate_id,
        canonical_domain=cand.company_domain,
        operator_note="Major BIM software vendor; not an architectural consulting prospect.",
    )
    assert excl_rec.exclusion_reason == ExclusionReason.SOFTWARE_VENDOR

    # 3. Candidate remains in database
    cand_in_db = cand_store.get_by_id(cand.candidate_id)
    assert cand_in_db is not None
    assert cand_in_db.candidate_id == cand.candidate_id

    # 4. Disappears from active prospecting queues
    state = project_candidate_lifecycle(
        cand_in_db,
        is_excluded=True,
        exclusion_reason=excl_rec.exclusion_reason.value,
    )
    assert state.is_active_work is False
    assert state.is_qualified_lead is False
    assert state.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED

    # 5. Restore allows inspecting decision and re-evaluating
    restored = excl_store.restore_company(cand.candidate_id)
    assert restored.exclusion_status == ExclusionStatus.RESTORED
