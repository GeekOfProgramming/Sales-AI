import os
import shutil
import pytest
import openpyxl

from backend.schemas import CompanyLead, EnrichedLead, ContactCandidate, CompanyEnrichment, StructuredJob
from sales_engine.exports.schemas import ExportRequest, GoogleSheetsConfig
from sales_engine.exports.export_orchestrator import ExportOrchestrator
from sales_engine.exports.export_serializer import generate_lead_id, generate_contact_id
from sales_engine.exports.google_sheets_exporter import GoogleSheetsExporter, MockGoogleSheetClient

PHASE7_ACCEPTANCE_DIR = os.path.join(os.getcwd(), "tests", "scratch_phase7_acceptance")

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(PHASE7_ACCEPTANCE_DIR):
        shutil.rmtree(PHASE7_ACCEPTANCE_DIR, ignore_errors=True)
    yield
    if os.path.exists(PHASE7_ACCEPTANCE_DIR):
        shutil.rmtree(PHASE7_ACCEPTANCE_DIR, ignore_errors=True)

def build_golden_enriched_leads():
    # Lead 1: Qualified high-intent BIM automation lead
    l1 = CompanyLead(
        company_name="VDC Automation Lab GmbH",
        company_name_normalized="vdc automation lab",
        company_domain="vdc-autolab.de",
        source_company_keys=["lever:vdc-autolab"],
        job_count=4,
        total_job_count=4,
        unique_job_count=4,
        relevant_job_count=3,
        job_titles=["Head of Digital Practice", "Revit API Developer", "Dynamo Specialist"],
        locations=["Munich, Germany", "Remote"],
        technologies=["Revit", "Dynamo", "C#", "Python"],
        signals=[{"signal": "revit_api", "evidence_count": 3}],
        evidence=["Develop Revit C# addins", "Automate QA with Dynamo"],
        lead_score=94,
        fit_score=38,
        intent_score=36,
        recency_score=10,
        evidence_score=10,
        qualified=True,
        scoring_reasons=["High fit BIM role", "Active Revit automation hiring"]
    )
    c1_1 = ContactCandidate(
        first_name="Maximilian",
        last_name="Bauer",
        full_name="Maximilian Bauer",
        job_title="Head of BIM & Digital Delivery",
        seniority="Head",
        department="Digital Practice",
        company_name="VDC Automation Lab GmbH",
        company_domain="vdc-autolab.de",
        work_email="m.bauer@vdc-autolab.de",
        email_status="verified",
        email_confidence=98,
        linkedin_url="https://linkedin.com/in/max-bauer-bim",
        buyer_role_match="exact",
        contact_score=95,
        data_sources=["apollo"],
        is_best_contact=True,
    )
    c1_2 = ContactCandidate(
        first_name="Anna",
        last_name="Schmidt",
        full_name="Anna Schmidt",
        job_title="Lead BIM Software Engineer",
        seniority="Lead",
        department="Software",
        company_name="VDC Automation Lab GmbH",
        company_domain="vdc-autolab.de",
        work_email="a.schmidt@vdc-autolab.de",
        email_status="likely",
        email_confidence=80,
        linkedin_url="https://linkedin.com/in/anna-schmidt-eng",
        buyer_role_match="strong",
        contact_score=85,
        data_sources=["hunter"],
        is_best_contact=False,
    )
    e1 = EnrichedLead(
        base_lead=l1,
        company_enrichment=CompanyEnrichment(
            company_name="VDC Automation Lab GmbH",
            company_domain="vdc-autolab.de",
            industry="Architecture & Planning",
            employee_count=120,
            country="Germany"
        ),
        buyer_roles_searched=["Head of BIM", "BIM Director"],
        contacts=[c1_1, c1_2],
        best_contact=c1_1,
        enrichment_status="complete",
        providers_used=["apollo", "hunter"],
        enrichment_errors=[]
    )

    # Lead 2: Unqualified lead (e.g. low score / non-relevant)
    l2 = CompanyLead(
        company_name="Generic Construction Ltd",
        company_domain="generic-construct.co.uk",
        lead_score=35,
        qualified=False,
        scoring_reasons=["No BIM technologies mentioned"]
    )
    e2 = EnrichedLead(
        base_lead=l2,
        enrichment_status="not_found",
        enrichment_errors=["apollo: No company found"]
    )

    # Lead 3: Duplicate of Lead 1 to test company dedup
    e3 = EnrichedLead(
        base_lead=CompanyLead(
            company_name="VDC Automation Lab",
            company_domain="www.vdc-autolab.de",
            lead_score=94,
            qualified=True
        )
    )

    return [e1, e2, e3]

def test_phase7_end_to_end_acceptance():
    leads = build_golden_enriched_leads()
    mock_sheets_client = MockGoogleSheetClient(spreadsheet_id="acceptance-sheet-id")
    sheets_exporter = GoogleSheetsExporter(client=mock_sheets_client)

    orchestrator = ExportOrchestrator(sheets_exporter=sheets_exporter)

    req = ExportRequest(
        leads=leads,
        formats=["xlsx", "csv", "json"],
        qualified_only=True,
        include_all_contacts=True,
        include_jobs=True,
        google_sheets=GoogleSheetsConfig(enabled=True, mode="upsert", spreadsheet_id="acceptance-sheet-id"),
        output_dir=PHASE7_ACCEPTANCE_DIR
    )

    resp = orchestrator.export(req)

    # 1. Deduplication and qualification filtering
    # Out of 3 input leads: e2 is unqualified (filtered out), e3 is duplicate of e1 (deduplicated)
    # Exactly 1 qualified unique lead should be exported!
    assert resp.exported_leads == 1
    assert resp.exported_contacts == 2

    # 2. XLSX Workbook verification
    assert "xlsx" in resp.files
    xlsx_file = resp.files["xlsx"]
    assert os.path.exists(xlsx_file)
    wb = openpyxl.load_workbook(xlsx_file)
    assert set(wb.sheetnames) == {"Leads", "Contacts", "Jobs", "Errors_Audit", "Summary"}

    # Leads sheet inspection
    ws_leads = wb["Leads"]
    assert ws_leads.max_row == 2  # 1 header + 1 lead
    assert ws_leads.freeze_panes == "A2"
    assert ws_leads.cell(row=2, column=2).value == "domain:vdc-autolab.de"
    # Verify score preservation
    assert ws_leads.cell(row=2, column=8).value == 94
    # Verify best contact flattened
    assert ws_leads.cell(row=2, column=23).value == "Maximilian Bauer"
    assert ws_leads.cell(row=2, column=25).value == "m.bauer@vdc-autolab.de"

    # 3. CSV verification
    assert "leads_csv" in resp.files and os.path.exists(resp.files["leads_csv"])
    assert "contacts_csv" in resp.files and os.path.exists(resp.files["contacts_csv"])

    # 4. JSON CRM verification
    assert "json" in resp.files and os.path.exists(resp.files["json"])
    import json
    with open(resp.files["json"], encoding="utf-8") as f:
        crm_data = json.load(f)
    assert len(crm_data) == 1
    assert crm_data[0]["company"]["domain"] == "vdc-autolab.de"
    assert crm_data[0]["best_contact"]["work_email"] == "m.bauer@vdc-autolab.de"

    # 5. Google Sheets Upsert verification
    assert resp.google_sheet_result["status"] == "success"
    assert resp.google_sheet_result["leads_inserted"] == 1

    # Idempotency check: run again with same request -> leads_updated == 1, leads_inserted == 0
    resp_second_run = orchestrator.export(req)
    assert resp_second_run.google_sheet_result["leads_updated"] == 1
    assert resp_second_run.google_sheet_result["leads_inserted"] == 0

    # Stable ID assertion across both runs
    assert generate_lead_id(leads[0]) == "domain:vdc-autolab.de"
    assert generate_contact_id(leads[0].contacts[0], "domain:vdc-autolab.de") == "email:m.bauer@vdc-autolab.de"
