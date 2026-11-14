import pytest
import sqlite3
import json
import shutil
from pathlib import Path

from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord, OperatorDecisionType
from sales_engine.candidates.candidate_store import CandidateStore
from backend.sales_ui.router import evaluate_import_readiness


def test_real_workbook_preview_scope_and_readiness_ready():
    """
    Check A: Real Emails(4).xlsx Preview
    - 1,363 eligible company rows
    - 0 unresolved operator decisions / false blockers
    - No false unmapped-column blocker (56 occurrences were in non-company sheets)
    - All approved import exclusions/deferred sheets honored
    - Readiness status: READY
    """
    path = Path("data/staging/upload_20261114_022404_Emails.xlsx")
    assert path.exists(), f"Source workbook {path} must exist"

    importer = HistoricalImporter()
    inspection = importer.inspect_workbook(path)
    sheets = inspection.get("sheets", [])

    # Group unmapped occurrences across sheets
    unmapped_by_sheet = {}
    for s in sheets:
        s_name = s["sheet_name"]
        unmapped_cols = [col for col, m in s.get("column_mappings", {}).items() if m == "unmapped"]
        if unmapped_cols:
            unmapped_by_sheet[s_name] = unmapped_cols

    # Verify that the 56 unmapped occurrences are strictly in non-company worksheets
    assert "Sent_Emails" in unmapped_by_sheet
    assert "Received_Emails" in unmapped_by_sheet
    assert "Email_Conversations" in unmapped_by_sheet

    # Calculate unmapped columns and schema validity across eligible company worksheets
    unmapped_company_columns = 0
    missing_essential_identity_count = 0
    company_sheet_names = []

    for s in sheets:
        is_company = s.get("is_eligible_company")
        if is_company is None:
            is_company = not (
                s.get("is_summary_sheet")
                or s.get("is_communication_sheet")
                or s.get("is_deferred_sheet")
                or s.get("is_excluded_sheet")
            )
        if is_company and s.get("row_count", 0) > 0:
            company_sheet_names.append(s["sheet_name"])
            if not s.get("mapping_valid", True):
                missing_essential_identity_count += 1
            unmapped_company_columns += sum(
                1 for m in s.get("column_mappings", {}).values() if m == "unmapped"
            )

    # 22 eligible company worksheets
    assert len(company_sheet_names) == 22
    assert unmapped_company_columns == 0
    assert missing_essential_identity_count == 0

    records = importer.parse_xlsx(path)
    assert len(records) == 1363

    status, reasons = evaluate_import_readiness(
        records=records,
        unmapped_columns_count=unmapped_company_columns,
        domain_collisions_count=inspection.get("domain_collisions_count", 0),
        distinct_canonical_domains=inspection.get("distinct_canonical_domains", 0),
        multiple_plausible_domains_count=inspection.get("multiple_plausible_domains_count", 0),
        missing_domain_count=inspection.get("missing_domain_count", 0),
        missing_essential_identity_count=missing_essential_identity_count,
        operator_approved_policy=True,
    )

    assert status == "READY"
    assert any("1363 company rows ready for import" in r for r in reasons)
    assert any("Policy A" in r for r in reasons)
    assert any("Policy C" in r for r in reasons)
    assert any("Policy B" in r for r in reasons)


def test_confirm_gate_regression_on_disposable_database(tmp_path):
    """
    Check B: Confirm gate regression on a disposable database:
    - Valid nonessential columns do not block import.
    - Genuine critical validation failures still block.
    - Existing research, historical contact and DNC remain protected.
    """
    live_db = Path("data/sales_outreach.db")
    test_db = tmp_path / "test_sales_gate.db"
    shutil.copy2(live_db, test_db)

    store = CandidateStore(str(test_db))
    importer = HistoricalImporter(store=store)

    # Baseline assertions on existing state
    with store._get_connection() as conn:
        initial_reviewed = conn.execute(
            "SELECT COUNT(*) FROM lead_register_records WHERE verification_status = 'OPERATOR_REVIEWED'"
        ).fetchone()[0]
        initial_dnc = conn.execute(
            "SELECT COUNT(*) FROM candidate_companies WHERE qualification_outcome = 'DNC'"
        ).fetchone()[0]

    assert initial_reviewed == 17

    # 1. Nonessential unmapped column does NOT block under operator-approved policy
    dummy_records = [
        HistoricalRecord(
            source_file="test.xlsx",
            sheet_name="Italy",
            row_number=1001,
            company_name="Acme BIM Consulting",
            domain="acme-bim.com",
            country="Italy",
            city="Milan",
            raw_row={"company": "Acme BIM Consulting", "website": "acme-bim.com", "custom_tag": "vip_lead"},
        )
    ]
    status_nonessential, reasons_nonessential = evaluate_import_readiness(
        records=dummy_records,
        unmapped_columns_count=2,  # 2 nonessential unmapped metadata columns
        domain_collisions_count=0,
        distinct_canonical_domains=1,
        multiple_plausible_domains_count=0,
        missing_domain_count=0,
        missing_essential_identity_count=0,
        operator_approved_policy=True,
    )
    assert status_nonessential == "READY"
    assert any("2 nonessential metadata column(s) detected" in r for r in reasons_nonessential)

    # Import should succeed and preserve custom metadata in raw_row_json
    res = importer.import_records(dummy_records)
    assert res.companies_created == 1

    with store._get_connection() as conn:
        row = conn.execute(
            "SELECT raw_row_json FROM historical_imports WHERE canonical_domain = 'acme-bim.com'"
        ).fetchone()
        assert row is not None
        saved_raw = json.loads(row[0])
        assert saved_raw.get("custom_tag") == "vip_lead"

    # 2. Genuine critical validation failures STILL BLOCK
    # 2a: Zero records detected
    status_zero, reasons_zero = evaluate_import_readiness(
        records=[],
        unmapped_columns_count=0,
        domain_collisions_count=0,
        distinct_canonical_domains=0,
        multiple_plausible_domains_count=0,
        missing_domain_count=0,
        missing_essential_identity_count=0,
        operator_approved_policy=True,
    )
    assert status_zero == "BLOCKED"
    assert any("Zero company records detected" in r for r in reasons_zero)

    # 2b: Sheet missing essential company identifier columns
    status_no_ident, reasons_no_ident = evaluate_import_readiness(
        records=dummy_records,
        unmapped_columns_count=0,
        domain_collisions_count=0,
        distinct_canonical_domains=1,
        multiple_plausible_domains_count=0,
        missing_domain_count=0,
        missing_essential_identity_count=1,
        operator_approved_policy=True,
    )
    assert status_no_ident == "BLOCKED"
    assert any("lack required company identifier columns" in r for r in reasons_no_ident)

    # 3. Existing research, historical contact and DNC remain protected
    with store._get_connection() as conn:
        final_reviewed = conn.execute(
            "SELECT COUNT(*) FROM lead_register_records WHERE verification_status = 'OPERATOR_REVIEWED'"
        ).fetchone()[0]
        final_dnc = conn.execute(
            "SELECT COUNT(*) FROM candidate_companies WHERE qualification_outcome = 'DNC'"
        ).fetchone()[0]

    assert final_reviewed == initial_reviewed
    assert final_dnc == initial_dnc
