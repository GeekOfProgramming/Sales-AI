"""
Test Suite for pyBIM Sales-AI IMPORT-NEW-02: Operator-Approved Flexible Excel Import.

Verifies:
1. Companies without websites are imported with company_domain=None and verification_status='Unverified'.
2. Multiple plausible domains use the proposed primary website; alternative domains stored as reference links; no manual approval required.
3. Sent_Emails import handles incremental import (319 duplicates skipped, 290 inserted, 588 matched, 21 unmatched retained).
4. Wrong email addresses do not mark companies as DNC or suppress valid contacts.
5. BIM Coordinator is excluded; Received_Emails and Email_Conversations are deferred.
6. Preview and Confirm parity: Readiness is READY without operator_decisions_json.
"""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
import pytest

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    QualificationOutcome,
)
from sales_engine.historical.importer import (
    HistoricalImporter,
    HistoricalRecord,
    SentEmailRecord,
    parse_multiline_website_cell,
    normalize_outreach_status,
)
from sales_engine.leads.register_store import LeadRegisterStore
from backend.sales_ui.router import evaluate_import_readiness


@pytest.fixture
def temp_store(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = CandidateStore(db_path)
    importer = HistoricalImporter(store=store)
    yield store, importer, db_path


def test_companies_without_websites_imported_unverified(temp_store):
    """
    Policy A: Companies without verified websites are imported with company_domain=None,
    verification_status='Unverified', and discovery_status='researching'.
    """
    store, importer, db_path = temp_store
    rec = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Veneto",
        row_number=10,
        company_name="Studio Senza Sito",
        domain=None,
        country="Italy",
        city="Venezia",
        tier_hint="A",
        priority_reason="Strong regional architect",
        business_topic="Architettura e BIM",
        emails=["info@studiosenzasito.it"],
        raw_outreach_status="بررسی اولیه",
    )

    res = importer.import_records([rec])
    assert res.companies_created == 1
    assert res.companies_unverified_website == 1

    # Verify CandidateCompany in DB
    with store._get_connection() as conn:
        row = conn.execute("SELECT candidate_id, company_name, company_domain, discovery_status, cold_outreach_allowed, suppressed FROM candidate_companies WHERE company_name = 'Studio Senza Sito'").fetchone()
        assert row is not None
        assert row["company_domain"] is None
        assert row["discovery_status"] == DiscoveryStatus.researching.value
        assert row["cold_outreach_allowed"] == 1
        assert row["suppressed"] == 0

    # Verify LeadRegisterRecord in DB
    reg_store = LeadRegisterStore(db_path)
    reg = reg_store.get_record(row["candidate_id"])
    assert reg is not None
    assert reg.verification_status == "Unverified"
    assert reg.company_name == "Studio Senza Sito"


def test_multiple_plausible_domains_use_primary_host(temp_store):
    """
    Policy C: Multiple plausible domains use the proposed primary website;
    alternative domains are stored as reference links; no manual approval required.
    """
    store, importer, db_path = temp_store
    cell_val = "Main: https://www.acme-bim.com\nPortal: https://portal.acme-bim.org\nAlt: https://acme-group.it"
    web_info = parse_multiline_website_cell(cell_val, company_name="ACME BIM")
    assert web_info["multiple_plausible_domains"] is True
    assert web_info["primary_domain"] == "acme-bim.com"

    rec = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Lombardy",
        row_number=12,
        company_name="ACME BIM",
        domain=web_info["primary_domain"],
        country="Italy",
        city="Milano",
        tier_hint="B",
        emails=["contact@acme-bim.com"],
        raw_outreach_status="بررسی اولیه",
        website_links=web_info["links"],
        raw_website_cell=cell_val,
        primary_website_info=web_info,
    )

    # Ingest with NO operator_decisions (automatic operator-approved policy)
    res = importer.import_records([rec], operator_decisions=None)
    assert res.companies_created == 1
    assert res.multiple_plausible_domains_resolved == 1

    with store._get_connection() as conn:
        cand = conn.execute("SELECT company_domain FROM candidate_companies WHERE company_name = 'ACME BIM'").fetchone()
        assert cand["company_domain"] == "acme-bim.com"

        # Check that alternative domains are stored in historical_company_links
        links = conn.execute("SELECT raw_url, host FROM historical_company_links").fetchall()
        hosts = [l["host"] for l in links if l["host"]]
        assert "acme-bim.com" in hosts
        assert "portal.acme-bim.org" in hosts
        assert "acme-group.it" in hosts


def test_wrong_email_marks_invalid_without_marking_dnc(temp_store):
    """
    Policy E: Wrong email addresses mark the exact recipient email as invalid/bounced.
    Does NOT mark company as DNC or mark valid contacts invalid.
    """
    store, importer, db_path = temp_store
    rec = HistoricalRecord(
        source_file="test.xlsx",
        sheet_name="Piedmont",
        row_number=15,
        company_name="Torino Design Studio",
        domain="torinodesign.it",
        country="Italy",
        city="Torino",
        emails=["wrong@torinodesign.it"],
        raw_outreach_status="ایمیل اشتباه بودش",
    )

    res = importer.import_records([rec])
    assert res.companies_created == 1
    assert res.invalid_emails_marked == 1

    with store._get_connection() as conn:
        cand = conn.execute("SELECT cold_outreach_allowed, suppressed, historical_contact_status FROM candidate_companies WHERE company_name = 'Torino Design Studio'").fetchone()
        assert cand["suppressed"] == 0  # NOT marked as DNC!
        assert cand["cold_outreach_allowed"] == 1
        assert cand["historical_contact_status"] == "uncontacted"

        contact = conn.execute("SELECT work_email, email_status FROM candidate_contacts WHERE work_email = 'wrong@torinodesign.it'").fetchone()
        assert contact is not None
        assert contact["email_status"] == "invalid"

        hist_contact = conn.execute("SELECT email, email_status FROM historical_contacts WHERE email = 'wrong@torinodesign.it'").fetchone()
        assert hist_contact is not None
        assert hist_contact["email_status"] == "invalid"


def test_bim_coordinator_excluded_and_communications_deferred():
    """
    Policy F & G: BIM Coordinator is excluded from company import;
    Received_Emails and Email_Conversations are deferred.
    """
    importer = HistoricalImporter(None)
    src_file = Path("data/staging/upload_20261114_022404_Emails.xlsx")
    if not src_file.exists():
        pytest.skip("Authoritative workbook not present at staging path")

    inspection = importer.inspect_workbook(src_file)
    sheets_by_name = {s["sheet_name"]: s for s in inspection["sheets"]}

    # BIM Coordinator excluded
    assert "BIM Coordinator" in sheets_by_name
    assert sheets_by_name["BIM Coordinator"]["is_excluded_sheet"] is True
    assert sheets_by_name["BIM Coordinator"]["mapping_valid"] is True

    # Received_Emails and Email_Conversations deferred
    assert "Received_Emails" in sheets_by_name
    assert sheets_by_name["Received_Emails"]["is_deferred_sheet"] is True

    assert "Email_Conversations" in sheets_by_name
    assert sheets_by_name["Email_Conversations"]["is_deferred_sheet"] is True

    # Ensure parse_xlsx does NOT return records from excluded or deferred sheets
    records = importer.parse_xlsx(src_file)
    parsed_sheets = {r.sheet_name for r in records}
    assert "BIM Coordinator" not in parsed_sheets
    assert "Received_Emails" not in parsed_sheets
    assert "Email_Conversations" not in parsed_sheets
    assert "Worng Emails" not in parsed_sheets
    assert "Dashboard" not in parsed_sheets
    assert "Meeting" not in parsed_sheets
    assert len(records) == 1363


def test_preview_and_confirm_readiness_parity():
    """
    Parity: Under operator-approved policies, evaluate_import_readiness returns
    READY without requiring manual operator_decisions_json.
    """
    records = [object() for _ in range(1363)]
    status, reasons = evaluate_import_readiness(
        records=records,
        unmapped_columns_count=0,
        domain_collisions_count=95,
        distinct_canonical_domains=1000,
        multiple_plausible_domains_count=16,
        missing_domain_count=143,
        operator_approved_policy=True,
    )
    assert status == "READY"
    assert any("operator-approved policies" in r for r in reasons)
    assert any("Policy A" in r for r in reasons)
    assert any("Policy C" in r for r in reasons)
    assert any("Policy B" in r for r in reasons)


def test_sent_emails_reconciliation_exact_breakdown(tmp_path):
    """
    Policy D: Historical Sent Emails incremental import handles 609 rows:
    588 matched to candidate/domain, 21 unmatched retained in historical_sent_emails.
    """
    import shutil
    live_db = Path("data/sales_outreach.db")
    src_file = Path("data/staging/upload_20261114_022404_Emails.xlsx")
    if not live_db.exists() or not src_file.exists():
        pytest.skip("Authoritative database or workbook not present")

    # Clone live DB to temporary test DB so live DB is never mutated
    test_db = tmp_path / "test_sent.db"
    shutil.copy2(live_db, test_db)

    store = CandidateStore(str(test_db))
    importer = HistoricalImporter(store=store)

    records = importer.parse_xlsx(src_file)
    sent_msgs = importer.parse_sent_emails(src_file)
    assert len(sent_msgs) == 609

    reconciled = importer.reconcile_sent_emails(sent_msgs, staged_records=records)
    matched = [m for m in reconciled if m.reconciliation_status == "matched"]
    unmatched = [m for m in reconciled if m.reconciliation_status != "matched"]

    assert len(matched) == 588
    assert len(unmatched) == 21

    # Import sent emails incrementally into store (319 already imported in DB, 290 newly inserted)
    stats = importer.import_sent_emails(reconciled)
    assert stats["total"] == 609
    assert stats["duplicates"] == 319
    assert stats["imported"] == 290
    assert stats["matched"] == 284
    assert stats["unmatched"] == 6

    # Verify authoritative database state: all 609 messages present with exact breakdown
    with store._get_connection() as conn:
        total_in_db = conn.execute("SELECT COUNT(*) FROM historical_sent_emails").fetchone()[0]
        matched_in_db = conn.execute("SELECT COUNT(*) FROM historical_sent_emails WHERE reconciliation_status = 'matched'").fetchone()[0]
        unmatched_in_db = conn.execute("SELECT COUNT(*) FROM historical_sent_emails WHERE reconciliation_status != 'matched'").fetchone()[0]
        assert total_in_db == 609
        # Reconciled against staged records gives 588 matched / 21 unmatched
        # In DB with existing records preserved: 303 existing matched + 284 new matched = 587 matched
        assert matched_in_db == 587
        assert unmatched_in_db == 22

    # Idempotency check: re-import skips all 609 as duplicates
    stats2 = importer.import_sent_emails(reconciled)
    assert stats2["imported"] == 0
    assert stats2["duplicates"] == 609
