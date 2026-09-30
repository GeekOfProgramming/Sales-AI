import os
import shutil
import pytest
import openpyxl
from datetime import datetime, timezone

from backend.schemas import CompanyLead, EnrichedLead, ContactCandidate, StructuredJob
from sales_engine.exports.schemas import (
    ExportRequest,
    GoogleSheetsConfig,
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
    ExportSummary,
)
from sales_engine.exports.export_serializer import (
    ExportSerializer,
    generate_lead_id,
    generate_contact_id,
    clean_scalar_list,
)
from sales_engine.exports.excel_exporter import ExcelExporter
from sales_engine.exports.csv_exporter import CSVExporter
from sales_engine.exports.crm_exporter import CRMExporter
from sales_engine.exports.google_sheets_exporter import GoogleSheetsExporter, MockGoogleSheetClient
from sales_engine.exports.export_orchestrator import ExportOrchestrator, MAX_EXPORT_LEADS

TEST_OUTPUT_DIR = os.path.join(os.getcwd(), "tests", "scratch_export_test")

@pytest.fixture(autouse=True)
def cleanup_scratch():
    if os.path.exists(TEST_OUTPUT_DIR):
        shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)
    yield
    if os.path.exists(TEST_OUTPUT_DIR):
        shutil.rmtree(TEST_OUTPUT_DIR, ignore_errors=True)

def create_sample_lead(name="Acme BIM Corp", domain="acme-bim.com", score=85, qualified=True):
    base_lead = CompanyLead(
        company_name=name,
        company_name_normalized=name.lower(),
        company_domain=domain,
        source_company_keys=["lever:acme-bim"],
        job_count=3,
        total_job_count=3,
        unique_job_count=3,
        relevant_job_count=2,
        job_titles=["BIM Director", "Revit API Developer"],
        locations=["Berlin, Germany"],
        technologies=["Revit", "Dynamo", "Python"],
        signals=[{"signal": "revit_api", "evidence_count": 2}],
        evidence=["Develop Revit C# plugins", "Automate Dynamo scripts"],
        lead_score=score,
        fit_score=35,
        intent_score=30,
        recency_score=10,
        evidence_score=10,
        qualified=qualified,
        scoring_reasons=["High fit BIM role", "Active Revit automation hiring"]
    )
    contact1 = ContactCandidate(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        job_title="Head of BIM",
        seniority="Head",
        department="Digital Delivery",
        company_name=name,
        company_domain=domain,
        work_email="jane.doe@acme-bim.com",
        email_status="verified",
        email_confidence=95,
        linkedin_url="https://www.linkedin.com/in/janedoe-bim/",
        buyer_role_match="exact",
        contact_score=90,
        data_sources=["apollo"],
        is_best_contact=True,
    )
    contact2 = ContactCandidate(
        first_name="Bob",
        last_name="Smith",
        full_name="Bob Smith",
        job_title="BIM Coordinator",
        seniority="Mid",
        department="Engineering",
        company_name=name,
        company_domain=domain,
        work_email="bob.smith@acme-bim.com",
        email_status="likely",
        email_confidence=75,
        linkedin_url="https://www.linkedin.com/in/bobsmith/",
        buyer_role_match="relevant",
        contact_score=70,
        data_sources=["hunter"],
        is_best_contact=False,
    )
    enriched = EnrichedLead(
        base_lead=base_lead,
        buyer_roles_searched=["Head of BIM", "BIM Director"],
        contacts=[contact1, contact2],
        best_contact=contact1,
        enrichment_status="complete",
        providers_used=["apollo", "hunter"],
        enrichment_errors=[]
    )
    return enriched

def test_stable_lead_ids():
    lead_a = create_sample_lead(name="Acme Corp", domain="www.Acme.com")
    lead_b = create_sample_lead(name="Acme Corp LLC", domain="acme.com")
    # Both canonicalize to domain:acme.com
    assert generate_lead_id(lead_a) == generate_lead_id(lead_b)
    assert generate_lead_id(lead_a) == "domain:acme.com"

    # Source key fallback
    lead_c = CompanyLead(company_name="Mystery Corp", source_company_keys=["greenhouse:mystery-123"])
    assert generate_lead_id(lead_c) == "source_key:greenhouse:mystery-123"

    # Normalized name fallback
    lead_d = CompanyLead(company_name="Alpha  Beta   GmbH")
    assert generate_lead_id(lead_d).startswith("name:alpha_beta")

def test_stable_contact_ids():
    lead_id = "domain:acme-bim.com"
    c1 = ContactCandidate(work_email="JANE.DOE@ACME-BIM.COM")
    c2 = ContactCandidate(work_email="  jane.doe@acme-bim.com  ")
    assert generate_contact_id(c1, lead_id) == generate_contact_id(c2, lead_id)
    assert generate_contact_id(c1, lead_id) == "email:jane.doe@acme-bim.com"

    # LinkedIn fallback
    c3 = ContactCandidate(linkedin_url="https://www.linkedin.com/in/janedoe-bim/?ref=share")
    assert generate_contact_id(c3, lead_id) == "linkedin:linkedin.com/in/janedoe-bim"

    # Provider ID fallback
    c4 = ContactCandidate(provider="apollo", provider_person_id="person_999")
    assert generate_contact_id(c4, lead_id) == "apollo:person_999"

def test_privacy_exclusion():
    # Verify forbidden sensitive keys are not exposed
    serializer = ExportSerializer(export_run_id="run_test")
    lead = create_sample_lead()
    l_row = serializer.serialize_lead(lead, "domain:acme-bim.com", "email:jane.doe@acme-bim.com")
    l_dict = l_row.model_dump()
    forbidden = ["personal_email", "personal_phone", "mobile_phone", "api_key", "password"]
    for k in forbidden:
        assert k not in l_dict

def test_xlsx_creation_and_openpyxl_usability():
    lead = create_sample_lead()
    serializer = ExportSerializer(export_run_id="run_100")
    lid = generate_lead_id(lead)
    bcid = generate_contact_id(lead.best_contact, lid)

    l_rows = [serializer.serialize_lead(lead, lid, bcid)]
    c_rows = serializer.serialize_contacts(lead, lid, bcid)
    j_rows = serializer.serialize_jobs(lead, lid)
    e_rows = serializer.serialize_errors(lead, lid)
    summary = serializer.calculate_summary(l_rows, c_rows, e_rows)

    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
    xlsx_path = os.path.join(TEST_OUTPUT_DIR, "SalesAI_Export_test.xlsx")

    exporter = ExcelExporter()
    exported = exporter.export(xlsx_path, l_rows, c_rows, j_rows, e_rows, summary)
    assert os.path.exists(exported)

    # Verify openpyxl can cleanly open the workbook
    wb = openpyxl.load_workbook(exported)
    expected_sheets = ["Leads", "Contacts", "Jobs", "Errors_Audit", "Summary"]
    assert wb.sheetnames == expected_sheets

    # Verify freeze header on Leads
    ws_leads = wb["Leads"]
    assert ws_leads.freeze_panes == "A2"
    assert ws_leads.auto_filter.ref is not None

    # Verify Best contact columns are populated on Leads sheet
    row2_vals = [ws_leads.cell(row=2, column=col).value for col in range(1, ws_leads.max_column + 1)]
    assert "jane.doe@acme-bim.com" in row2_vals
    assert "Head of BIM" in row2_vals

    # Verify Contacts sheet
    ws_contacts = wb["Contacts"]
    assert ws_contacts.max_row == 3  # Header + 2 contacts
    assert ws_contacts.cell(row=2, column=1).value == lid

def test_csv_creation():
    lead = create_sample_lead()
    serializer = ExportSerializer(export_run_id="run_200")
    lid = generate_lead_id(lead)
    bcid = generate_contact_id(lead.best_contact, lid)

    l_rows = [serializer.serialize_lead(lead, lid, bcid)]
    c_rows = serializer.serialize_contacts(lead, lid, bcid)
    j_rows = serializer.serialize_jobs(lead, lid)
    e_rows = serializer.serialize_errors(lead, lid)

    exporter = CSVExporter()
    csv_dir = os.path.join(TEST_OUTPUT_DIR, "csv")
    res = exporter.export(csv_dir, l_rows, c_rows, j_rows, e_rows)

    assert "leads_csv" in res and os.path.exists(res["leads_csv"])
    assert "contacts_csv" in res and os.path.exists(res["contacts_csv"])

    with open(res["leads_csv"], encoding="utf-8") as f:
        content = f.read()
        assert "lead_id" in content
        assert "domain:acme-bim.com" in content
        assert "jane.doe@acme-bim.com" in content

def test_crm_json_creation():
    lead = create_sample_lead()
    serializer = ExportSerializer(export_run_id="run_300")
    lid = generate_lead_id(lead)
    bcid = generate_contact_id(lead.best_contact, lid)

    l_row = serializer.serialize_lead(lead, lid, bcid)
    c_rows = serializer.serialize_contacts(lead, lid, bcid)
    j_rows = serializer.serialize_jobs(lead, lid)
    e_rows = serializer.serialize_errors(lead, lid)

    payload = serializer.to_crm_payload(l_row, c_rows, j_rows, e_rows)

    assert "company" in payload
    assert "lead" in payload
    assert "contacts" in payload
    assert "jobs" in payload
    assert "audit" in payload
    assert payload["company"]["domain"] == "acme-bim.com"
    assert len(payload["contacts"]) == 2
    assert payload["lead"]["lead_score"] == 85

def test_company_and_contact_deduplication():
    # Pass duplicate leads with same domain
    lead1 = create_sample_lead(name="Acme 1", domain="acme.com")
    lead2 = create_sample_lead(name="Acme 2", domain="acme.com")

    # Add duplicate contacts within lead1
    dup_contact = ContactCandidate(
        first_name="Jane", last_name="Doe", work_email="jane.doe@acme-bim.com"
    )
    lead1.contacts.append(dup_contact)

    req = ExportRequest(
        leads=[lead1, lead2],
        formats=["json"],
        output_dir=os.path.join(TEST_OUTPUT_DIR, "dedup_test")
    )
    orchestrator = ExportOrchestrator()
    resp = orchestrator.export(req)

    # Lead deduplication: only 1 lead should be exported
    assert resp.exported_leads == 1
    # Contact deduplication: only 2 unique contacts should be exported (duplicate excluded)
    assert resp.exported_contacts == 2

def test_google_sheets_snapshot_and_upsert_mock():
    mock_client = MockGoogleSheetClient(spreadsheet_id="test-sheet-id")
    sheets_exporter = GoogleSheetsExporter(client=mock_client)

    lead = create_sample_lead(name="Delta Design", domain="deltadesign.de", score=92)
    serializer = ExportSerializer(export_run_id="run_sheet")
    lid = generate_lead_id(lead)
    bcid = generate_contact_id(lead.best_contact, lid)

    l_rows = [serializer.serialize_lead(lead, lid, bcid)]
    c_rows = serializer.serialize_contacts(lead, lid, bcid)
    j_rows = serializer.serialize_jobs(lead, lid)
    e_rows = serializer.serialize_errors(lead, lid)
    summary = serializer.calculate_summary(l_rows, c_rows, e_rows)

    # 1. Snapshot mode
    cfg_snap = GoogleSheetsConfig(enabled=True, mode="snapshot", spreadsheet_id="test-sheet-id")
    res_snap = sheets_exporter.export(cfg_snap, l_rows, c_rows, j_rows, e_rows, summary)
    assert res_snap["status"] == "success"
    assert len(mock_client.get_sheet_values("Leads")) == 2  # header + 1 row

    # 2. Upsert mode: Re-running with same dataset must NOT duplicate rows
    cfg_upsert = GoogleSheetsConfig(enabled=True, mode="upsert", spreadsheet_id="test-sheet-id")
    res_upsert1 = sheets_exporter.export(cfg_upsert, l_rows, c_rows, j_rows, e_rows, summary)
    assert res_upsert1["leads_updated"] == 1
    assert res_upsert1["leads_inserted"] == 0
    assert len(mock_client.get_sheet_values("Leads")) == 2

    # 3. Upsert mode: Add new lead
    lead2 = create_sample_lead(name="Epsilon Eng", domain="epsilon-eng.com", score=80)
    lid2 = generate_lead_id(lead2)
    bcid2 = generate_contact_id(lead2.best_contact, lid2)
    l_rows2 = [serializer.serialize_lead(lead2, lid2, bcid2)]
    c_rows2 = serializer.serialize_contacts(lead2, lid2, bcid2)

    res_upsert2 = sheets_exporter.export(cfg_upsert, l_rows2, c_rows2, [], [], summary)
    assert res_upsert2["leads_inserted"] == 1
    assert res_upsert2["leads_updated"] == 0
    assert len(mock_client.get_sheet_values("Leads")) == 3  # header + 2 unique leads

def test_partial_exporter_failure_isolated():
    # Google Sheets enabled with no client/creds -> raises in google exporter
    req = ExportRequest(
        leads=[create_sample_lead()],
        formats=["xlsx", "csv"],
        google_sheets=GoogleSheetsConfig(enabled=True, mode="snapshot"),  # No creds -> will fail
        output_dir=os.path.join(TEST_OUTPUT_DIR, "partial_fail")
    )
    orchestrator = ExportOrchestrator()
    resp = orchestrator.export(req)

    # XLSX and CSV must succeed
    assert "xlsx" in resp.files
    assert "leads_csv" in resp.files
    # Google sheets error must be captured in errors list, not crash whole export
    assert any("Google Sheets export failed" in err for err in resp.errors)
    assert resp.google_sheet_result["status"] == "error"

def test_all_exporters_failure_raises():
    # Mock exporter that fails everything
    class FailingExcelExporter(ExcelExporter):
        def export(self, *args, **kwargs):
            raise RuntimeError("Disk full")

    orchestrator = ExportOrchestrator(excel_exporter=FailingExcelExporter())
    req = ExportRequest(
        leads=[create_sample_lead()],
        formats=["xlsx"],
        output_dir=os.path.join(TEST_OUTPUT_DIR, "all_fail")
    )
    with pytest.raises(RuntimeError) as exc_info:
        orchestrator.export(req)
    assert "All requested exports failed" in str(exc_info.value)

def test_max_export_leads_limit():
    orchestrator = ExportOrchestrator()
    oversized_leads = [create_sample_lead(name=f"Co {i}", domain=f"co{i}.com") for i in range(MAX_EXPORT_LEADS + 1)]
    req = ExportRequest(leads=oversized_leads)
    with pytest.raises(ValueError) as exc_info:
        orchestrator.export(req)
    assert "exceeds MAX_EXPORT_LEADS" in str(exc_info.value)

def test_workflow_default_fields():
    serializer = ExportSerializer(export_run_id="run_defaults")
    lead = create_sample_lead()
    lid = generate_lead_id(lead)
    row = serializer.serialize_lead(lead, lid, None)

    assert row.outreach_status == "not_started"
    assert row.approval_status == "pending_review"
    assert row.send_status == "not_sent"
    assert row.draft_subject == ""
    assert row.draft_body == ""
    assert row.personalization_notes == ""
    assert row.last_outreach_at == ""

def test_explainability_preservation():
    lead = create_sample_lead()
    serializer = ExportSerializer()
    lid = generate_lead_id(lead)
    row = serializer.serialize_lead(lead, lid, None)

    # Scoring breakdown preserved
    assert row.lead_score == 85
    assert row.fit_score == 35
    assert row.intent_score == 30
    assert row.recency_score == 10
    assert row.evidence_score == 10
    assert "High fit BIM role" in row.lead_reasons
    assert "Develop Revit C# plugins" in row.lead_evidence
