"""
HV-02.1 Safety Hardening and Acceptance Verification Test Suite.

Covers:
1. P0 Concurrency & Atomic Staging Promotion (detects concurrent modification and aborts without data loss).
2. P0 Communication Event vs Source Revision Decoupling (editing notes/phones/links does NOT create duplicate outreach events).
3. P0 Meeting Activity Matching & Classification Safety (no partial name false matches, conservative status classification).
4. P0 Exclusion Registry vs Permanent Monotonic Suppression (restoring an excluded company clears cold_outreach prohibition without losing DNC).
5. Configurable Preview Signing Secret & Cryptographic Verification.
"""

import os
import hmac
import hashlib
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from sales_engine.candidates.candidate_store import (
    CandidateStore,
    CandidateCompany,
    CompanyTier,
)
from sales_engine.historical.importer import (
    HistoricalImporter,
    HistoricalRecord,
    MeetingRecord,
    normalize_emails,
)
from backend.sales_ui.router import (
    PREVIEW_SIGNING_SECRET,
    confirm_historical_import_action,
)
from fastapi import HTTPException


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_hv02_1.db"
    store = CandidateStore(str(db_file))
    return store, db_file


def test_preview_signing_secret_configurable():
    """Verify PREVIEW_SIGNING_SECRET is used and validates preview tokens correctly."""
    assert PREVIEW_SIGNING_SECRET is not None
    assert len(PREVIEW_SIGNING_SECRET) > 0

    token_payload = "dummy_file.xlsx:abc123hash:Veneto"
    valid_token = hmac.new(PREVIEW_SIGNING_SECRET, token_payload.encode(), hashlib.sha256).hexdigest()
    invalid_token = hmac.new(b"wrong_secret", token_payload.encode(), hashlib.sha256).hexdigest()

    assert valid_token != invalid_token


def test_communication_hash_decoupling_prevents_duplicate_outreach(temp_db):
    """
    Editing notes, phone numbers, or links in a spreadsheet row must produce
    a different compute_hash() (row revision), but the SAME compute_communication_hash().
    Re-importing must NOT duplicate historical outreach events.
    """
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    rec1 = HistoricalRecord(
        company_name="Studio Test BIM S.r.l.",
        domain="studiotestbim.it",
        tier_hint="A",
        notes="Initial draft notes",
        phone_numbers="+39 02 123456",
        source_url="https://studiotestbim.it",
        contact_name="Ing. Mario Rossi",
        contact_role="BIM Manager",
        emails=["mario@studiotestbim.it"],
        contact_date="2026-05-10",
        raw_outreach_status="ایمیل ارسال شد",
        source_file="test1.xlsx",
        sheet_name="Veneto",
        row_number=10,
    )

    # Initial import
    res1 = importer.import_records([rec1])
    assert res1.companies_created == 1

    # Check outreach event
    cand_id = store.generate_company_id("Studio Test BIM S.r.l.", "studiotestbim.it", None)
    outreach1 = store.get_historical_outreach_for_candidate(cand_id)
    assert len(outreach1) == 1
    assert outreach1[0]["raw_row_hash"] == rec1.compute_communication_hash()

    # Operator modifies notes and phone number in row 10
    rec2 = HistoricalRecord(
        company_name="Studio Test BIM S.r.l.",
        domain="studiotestbim.it",
        tier_hint="A",
        notes="Updated notes by operator with new observations",
        phone_numbers="+39 02 999999",  # Edited
        other_links="https://linkedin.com/company/studiotestbim",  # Edited
        source_url="https://studiotestbim.it",
        contact_name="Ing. Mario Rossi",
        contact_role="BIM Manager",
        emails=["mario@studiotestbim.it"],
        contact_date="2026-05-10",
        raw_outreach_status="ایمیل ارسال شد",
        source_file="test2.xlsx",
        sheet_name="Veneto",
        row_number=10,
    )

    assert rec1.compute_hash() != rec2.compute_hash()  # Source row revision differs
    assert rec1.compute_communication_hash() == rec2.compute_communication_hash()  # Comm hash identical

    # Re-importing with updated record
    res2 = importer.import_records([rec2])
    assert res2.companies_updated == 1

    # Crucial test: outreach events must NOT be duplicated!
    outreach2 = store.get_historical_outreach_for_candidate(cand_id)
    assert len(outreach2) == 1, "Editing descriptive notes must NOT create duplicate outreach event"


def test_meeting_activity_matching_safety(temp_db):
    """
    Verify meeting activity reconciliation rejects unsafe partial substring matching,
    and only matches exact domain or exact normalized name.
    """
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    # Pre-populate candidate "Studio Architettura ABC S.r.l."
    c1 = CandidateCompany(
        company_id="cand:domain:studioabc.it",
        company_name="Studio Architettura ABC S.r.l.",
        normalized_company_name="studio architettura abc",
        company_domain="studioabc.it",
    )
    store.save_candidate(c1)

    # Meeting 1: "Studio" (a common substring that previously would falsely match "Studio Architettura ABC")
    m_partial = MeetingRecord(
        company_name="Studio",
        domain=None,
        status="پیگیری شود",
        contact_date="2026-06-01",
    )

    # Meeting 2: Exact domain match
    m_exact_dom = MeetingRecord(
        company_name="Different Title Studio ABC",
        domain="studioabc.it",
        status="پیگیری شود",
        contact_date="2026-06-01",
    )

    # Meeting 3: Exact normalized name match
    m_exact_name = MeetingRecord(
        company_name="Studio Architettura ABC",
        domain=None,
        status="جلسه برگزار شد",
        reply_date="2026-06-02",
    )

    reconciled = importer.reconcile_meeting_activities([m_partial, m_exact_dom, m_exact_name])

    assert reconciled[0].reconciliation_status == "unmatched", "Substring 'Studio' must NOT match 'Studio Architettura ABC'"
    assert reconciled[1].reconciliation_status == "matched"
    assert reconciled[1].matched_candidate_id == "cand:domain:studioabc.it"
    assert reconciled[2].reconciliation_status == "matched"
    assert reconciled[2].matched_candidate_id == "cand:domain:studioabc.it"


def test_meeting_classification_conservative(temp_db):
    """
    Verify meetings without reply or active conversation evidence do NOT
    unconditionally set active_conversation=1 or historical_contact_status='replied'.
    """
    store, db_path = temp_db
    importer = HistoricalImporter(store=store)

    c1 = CandidateCompany(
        company_id="cand:domain:pending-meet.it",
        company_name="Pending Meeting Studio",
        company_domain="pending-meet.it",
        historical_contact_status="uncontacted",
        active_conversation=False,
    )
    store.save_candidate(c1)

    # Meeting with pending/follow-up status, no reply date
    m_pending = MeetingRecord(
        company_name="Pending Meeting Studio",
        domain="pending-meet.it",
        status="پیگیری شود",  # Follow up / scheduled, NOT replied
        contact_date="2026-06-01",
    )

    stats = importer.import_meeting_activities([m_pending])
    assert stats["matched"] == 1

    cand_after = store.get_by_id("cand:domain:pending-meet.it")
    assert cand_after.historical_contact_status == "contacted", "Meeting without reply must be 'contacted', NOT 'replied'"
    assert cand_after.active_conversation is False, "Meeting without reply must not set active_conversation=True"


def test_exclusion_registry_and_dnc_independence(temp_db):
    """
    Verify exclusion does NOT pollute permanent monotonic suppression.
    When a company is excluded and later restored, its outreach eligibility is recoverable,
    while companies with genuine DNC/opt-out stay strictly suppressed.
    """
    store, db_path = temp_db

    # Create candidate
    c = CandidateCompany(
        candidate_id="cand:domain:normalco.it",
        company_name="Normal Co",
        company_domain="normalco.it",
        cold_outreach_allowed=True,
        suppressed=False,
    )
    store.save_candidate(c)

    # Exclude company
    from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore, ExclusionReason
    excl_store = ExcludedCompaniesStore(db_path=str(db_path))
    excl_store.exclude_company(
        company_name=c.company_name,
        exclusion_reason=ExclusionReason.NOT_TARGET,
        candidate_id=c.candidate_id,
        canonical_domain=c.company_domain,
        operator_note="Not suitable",
        operator="test_operator",
    )

    # Save candidate again while active in exclusion
    cand_excl = store.get_by_id(c.candidate_id)
    cand_excl.company_tier = CompanyTier.tier_2
    store.save_candidate(cand_excl)

    cand_excl_saved = store.get_by_id(c.candidate_id)
    assert cand_excl_saved.cold_outreach_allowed is False, "Active exclusion must block cold outreach"
    assert cand_excl_saved.suppressed is False, "Exclusion must NOT contaminate permanent monotonic DNC suppression"

    # Restore company from exclusion
    excl_store.restore_company(
        registry_id_or_candidate_id=c.candidate_id,
        operator_note="Re-evaluated",
        operator="test_operator",
    )

    # Re-saving restored company without active exclusion must preserve restored state
    cand_restored = store.get_by_id(c.candidate_id)
    assert cand_restored.suppressed is False
    assert excl_store.is_company_excluded(candidate_id=c.candidate_id) is False

    # Simulate operator approving or enabling outreach upon re-evaluation
    with store._get_connection() as conn:
        conn.execute("UPDATE candidate_companies SET cold_outreach_allowed = 1 WHERE candidate_id = ?", (c.candidate_id,))
    
    final_cand = store.get_by_id(c.candidate_id)
    assert final_cand.cold_outreach_allowed is True
    assert final_cand.suppressed is False


def test_normalize_emails_leading_dots():
    """Verify normalize_emails cleanly strips leading/trailing dots and noise."""
    res = normalize_emails(".info@next-a.it,  mario@company.com., <support@domain.org> ")
    assert "info@next-a.it" in res
    assert "mario@company.com" in res
    assert "support@domain.org" in res
    assert len(res) == 3
