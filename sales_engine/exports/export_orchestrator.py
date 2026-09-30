import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

from sales_engine.exports.schemas import (
    ExportRequest,
    ExportResponse,
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
    ExportSummary,
    GoogleSheetsConfig,
)
from sales_engine.exports.export_serializer import (
    ExportSerializer,
    generate_lead_id,
    generate_contact_id,
)
from sales_engine.exports.excel_exporter import ExcelExporter
from sales_engine.exports.csv_exporter import CSVExporter
from sales_engine.exports.crm_exporter import CRMExporter
from sales_engine.exports.google_sheets_exporter import GoogleSheetsExporter

MAX_EXPORT_LEADS = 5000

class ExportOrchestrator:
    """
    Coordinates end-to-end export workflow across Excel, CSV, CRM JSON,
    and optional Google Sheets.
    """

    def __init__(
        self,
        excel_exporter: Optional[ExcelExporter] = None,
        csv_exporter: Optional[CSVExporter] = None,
        crm_exporter: Optional[CRMExporter] = None,
        sheets_exporter: Optional[GoogleSheetsExporter] = None,
    ):
        self.excel_exporter = excel_exporter or ExcelExporter()
        self.csv_exporter = csv_exporter or CSVExporter()
        self.crm_exporter = crm_exporter or CRMExporter()
        self.sheets_exporter = sheets_exporter or GoogleSheetsExporter()

    def export(self, request: ExportRequest) -> ExportResponse:
        # 1. Enforce payload safety limits
        if len(request.leads) > MAX_EXPORT_LEADS:
            raise ValueError(
                f"Export payload exceeds MAX_EXPORT_LEADS limit ({len(request.leads)} > {MAX_EXPORT_LEADS}). "
                "Export rejected to prevent resource exhaustion."
            )

        export_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        serializer = ExportSerializer(export_run_id=export_run_id)

        output_dir = request.output_dir or os.path.join(os.getcwd(), "exports", export_run_id)
        os.makedirs(output_dir, exist_ok=True)

        lead_rows: List[LeadExportRow] = []
        contact_rows: List[ContactExportRow] = []
        job_rows: List[JobExportRow] = []
        error_rows: List[ErrorAuditRow] = []
        crm_payloads: List[Dict[str, Any]] = []

        seen_lead_ids: Set[str] = set()

        # Build raw jobs index if raw jobs provided separately
        raw_jobs_by_domain: Dict[str, List[Any]] = {}
        if request.jobs:
            for j in request.jobs:
                d = getattr(j, "company_domain", None) or (j.get("company_domain") if isinstance(j, dict) else None)
                if d:
                    raw_jobs_by_domain.setdefault(str(d).lower().strip(), []).append(j)

        # 2. Process and serialize leads
        for lead_item in request.leads:
            base = getattr(lead_item, "base_lead", lead_item)
            is_qualified = getattr(base, "qualified", False) if hasattr(base, "qualified") else (base.get("qualified", False) if isinstance(base, dict) else False)
            if request.qualified_only and not is_qualified:
                continue

            lead_id = generate_lead_id(lead_item)
            if lead_id in seen_lead_ids:
                # Deduplicate identical lead entity across inputs
                continue
            seen_lead_ids.add(lead_id)

            # Determine best contact ID
            best_cont = getattr(lead_item, "best_contact", None) if hasattr(lead_item, "best_contact") else (lead_item.get("best_contact") if isinstance(lead_item, dict) else None)
            best_contact_id = generate_contact_id(best_cont, lead_id) if best_cont else None

            # Serialize Lead
            l_row = serializer.serialize_lead(lead_item, lead_id, best_contact_id)
            lead_rows.append(l_row)

            # Serialize Contacts
            c_rows = serializer.serialize_contacts(lead_item, lead_id, best_contact_id)
            if not request.include_all_contacts:
                c_rows = [c for c in c_rows if c.is_best_contact]
            contact_rows.extend(c_rows)

            # Serialize Jobs
            lead_domain = str(getattr(base, "company_domain", "") or (base.get("company_domain", "") if isinstance(base, dict) else "")).lower().strip()
            associated_jobs = raw_jobs_by_domain.get(lead_domain)
            j_rows = serializer.serialize_jobs(lead_item, lead_id, raw_jobs=associated_jobs) if request.include_jobs else []
            job_rows.extend(j_rows)

            # Serialize Errors
            e_rows = serializer.serialize_errors(lead_item, lead_id)
            error_rows.extend(e_rows)

            # Serialize CRM JSON payload
            crm_payload = serializer.to_crm_payload(l_row, c_rows, j_rows, e_rows)
            crm_payloads.append(crm_payload)

        # Summary
        summary = serializer.calculate_summary(lead_rows, contact_rows, error_rows)

        files_result: Dict[str, str] = {}
        errors_result: List[str] = []
        formats_attempted = 0
        formats_succeeded = 0

        # 3. Export XLSX
        if "xlsx" in request.formats:
            formats_attempted += 1
            try:
                xlsx_path = os.path.join(output_dir, f"SalesAI_Export_{export_run_id}.xlsx")
                self.excel_exporter.export(xlsx_path, lead_rows, contact_rows, job_rows, error_rows, summary)
                files_result["xlsx"] = xlsx_path
                formats_succeeded += 1
            except Exception as e:
                errors_result.append(f"XLSX export failed: {str(e)}")

        # 4. Export CSV
        if "csv" in request.formats:
            formats_attempted += 1
            try:
                csv_dir = os.path.join(output_dir, "csv")
                csv_files = self.csv_exporter.export(csv_dir, lead_rows, contact_rows, job_rows, error_rows)
                files_result.update(csv_files)
                formats_succeeded += 1
            except Exception as e:
                errors_result.append(f"CSV export failed: {str(e)}")

        # 5. Export CRM JSON
        if "json" in request.formats:
            formats_attempted += 1
            try:
                json_path = os.path.join(output_dir, "crm_export.json")
                self.crm_exporter.export(json_path, crm_payloads)
                files_result["json"] = json_path
                formats_succeeded += 1
            except Exception as e:
                errors_result.append(f"CRM JSON export failed: {str(e)}")

        # 6. Optional Google Sheets Sync
        sheets_result = None
        if request.google_sheets and request.google_sheets.enabled:
            formats_attempted += 1
            try:
                sheets_result = self.sheets_exporter.export(
                    request.google_sheets,
                    lead_rows,
                    contact_rows,
                    job_rows,
                    error_rows,
                    summary
                )
                if sheets_result.get("status") == "success":
                    formats_succeeded += 1
                else:
                    errors_result.append(f"Google Sheets sync: {sheets_result.get('message', 'unknown warning')}")
            except Exception as e:
                sheets_result = {"status": "error", "error": str(e)}
                errors_result.append(f"Google Sheets export failed: {str(e)}")

        # 7. Check complete failure
        if formats_attempted > 0 and formats_succeeded == 0:
            raise RuntimeError(f"All requested exports failed: {'; '.join(errors_result)}")

        return ExportResponse(
            export_run_id=export_run_id,
            exported_leads=len(lead_rows),
            exported_contacts=len(contact_rows),
            exported_jobs=len(job_rows),
            files=files_result,
            google_sheet_result=sheets_result,
            errors=errors_result,
        )
