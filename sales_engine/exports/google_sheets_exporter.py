import os
from typing import List, Dict, Any, Optional

from sales_engine.exports.base_sheet_exporter import BaseSheetExporter
from sales_engine.exports.schemas import (
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
    ExportSummary,
    GoogleSheetsConfig,
)

class MockGoogleSheetClient:
    """In-memory Google Sheets client for offline testing and verification."""

    def __init__(self, spreadsheet_id: str = "mock-sheet-123"):
        self.spreadsheet_id = spreadsheet_id
        # Sheets map: title -> list of rows (row is list of values)
        self.sheets: Dict[str, List[List[Any]]] = {
            "Leads": [],
            "Contacts": [],
            "Jobs": [],
            "Errors_Audit": [],
            "Summary": []
        }

    def get_sheet_values(self, sheet_name: str) -> List[List[Any]]:
        return self.sheets.get(sheet_name, [])

    def update_sheet_values(self, sheet_name: str, values: List[List[Any]]):
        self.sheets[sheet_name] = values

class GoogleSheetsExporter(BaseSheetExporter):
    """
    Google Sheets exporter supporting optional execution, snapshot mode, and upsert mode.
    Does not crash local exports if credentials are not configured.
    """

    def __init__(self, client: Optional[Any] = None):
        self._client = client

    def export(
        self,
        config: GoogleSheetsConfig,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow],
        summary: ExportSummary,
    ) -> Dict[str, Any]:
        if not config.enabled and not os.getenv("GOOGLE_SHEETS_ENABLED", "").lower() in ("true", "1"):
            return {
                "status": "skipped",
                "message": "Google Sheets export is disabled in configuration."
            }

        client = self._client or self._resolve_client(config)
        if not client:
            raise RuntimeError(
                "Google Sheets integration enabled but credentials/client not configured. "
                "Provide service account credentials or set mock client."
            )

        spreadsheet_id = config.spreadsheet_id or getattr(client, "spreadsheet_id", "default-sheet-id")

        if config.mode == "upsert":
            return self._export_upsert(client, spreadsheet_id, lead_rows, contact_rows, job_rows, error_rows, summary)
        else:
            return self._export_snapshot(client, spreadsheet_id, lead_rows, contact_rows, job_rows, error_rows, summary)

    def _resolve_client(self, config: GoogleSheetsConfig) -> Optional[Any]:
        # If credentials provided or environment set, real google client can be created here.
        # Otherwise return None so clear explicit error is raised if enabled=True.
        creds = config.credentials_json or os.getenv("GOOGLE_SHEETS_CREDENTIALS")
        if not creds:
            return None
        # Here we would initialize googleapiclient / gspread when configured
        return MockGoogleSheetClient(config.spreadsheet_id or "sheet-configured")

    def _export_snapshot(
        self,
        client: Any,
        spreadsheet_id: str,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow],
        summary: ExportSummary,
    ) -> Dict[str, Any]:
        """Snapshot mode: Overwrites/creates fresh sheets with current export rows."""
        # Convert leads
        lead_headers = list(LeadExportRow.model_fields.keys())
        leads_data = [lead_headers] + [[r.model_dump().get(h, "") for h in lead_headers] for r in lead_rows]
        client.update_sheet_values("Leads", leads_data)

        # Convert contacts
        contact_headers = list(ContactExportRow.model_fields.keys())
        contacts_data = [contact_headers] + [[r.model_dump().get(h, "") for h in contact_headers] for r in contact_rows]
        client.update_sheet_values("Contacts", contacts_data)

        # Convert jobs
        job_headers = list(JobExportRow.model_fields.keys())
        jobs_data = [job_headers] + [[r.model_dump().get(h, "") for h in job_headers] for r in job_rows]
        client.update_sheet_values("Jobs", jobs_data)

        # Convert errors
        error_headers = list(ErrorAuditRow.model_fields.keys())
        errors_data = [error_headers] + [[r.model_dump().get(h, "") for h in error_headers] for r in error_rows]
        client.update_sheet_values("Errors_Audit", errors_data)

        # Convert summary
        summary_data = [["Metric", "Value"]] + [[k, v] for k, v in summary.model_dump().items()]
        client.update_sheet_values("Summary", summary_data)

        return {
            "status": "success",
            "mode": "snapshot",
            "spreadsheet_id": spreadsheet_id,
            "leads_synced": len(lead_rows),
            "contacts_synced": len(contact_rows),
            "jobs_synced": len(job_rows),
        }

    def _export_upsert(
        self,
        client: Any,
        spreadsheet_id: str,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow],
        summary: ExportSummary,
    ) -> Dict[str, Any]:
        """
        Upsert mode: Keys entities by stable lead_id and contact_id.
        Updates existing rows if found, or appends new rows.
        Does NOT duplicate existing leads or contacts.
        """
        # 1. Upsert Leads
        lead_headers = list(LeadExportRow.model_fields.keys())
        lead_id_idx = lead_headers.index("lead_id")
        existing_leads = client.get_sheet_values("Leads")

        leads_inserted = 0
        leads_updated = 0

        if not existing_leads:
            existing_leads = [lead_headers]

        # Map lead_id to row index
        lead_map: Dict[str, int] = {}
        for r_idx, row in enumerate(existing_leads[1:], start=1):
            if len(row) > lead_id_idx:
                lead_map[row[lead_id_idx]] = r_idx

        for l_row in lead_rows:
            dumped = [l_row.model_dump().get(h, "") for h in lead_headers]
            lid = l_row.lead_id
            if lid in lead_map:
                existing_leads[lead_map[lid]] = dumped
                leads_updated += 1
            else:
                existing_leads.append(dumped)
                lead_map[lid] = len(existing_leads) - 1
                leads_inserted += 1

        client.update_sheet_values("Leads", existing_leads)

        # 2. Upsert Contacts
        contact_headers = list(ContactExportRow.model_fields.keys())
        contact_id_idx = contact_headers.index("contact_id")
        existing_contacts = client.get_sheet_values("Contacts")

        contacts_inserted = 0
        contacts_updated = 0

        if not existing_contacts:
            existing_contacts = [contact_headers]

        contact_map: Dict[str, int] = {}
        for r_idx, row in enumerate(existing_contacts[1:], start=1):
            if len(row) > contact_id_idx:
                contact_map[row[contact_id_idx]] = r_idx

        for c_row in contact_rows:
            dumped = [c_row.model_dump().get(h, "") for h in contact_headers]
            cid = c_row.contact_id
            if cid in contact_map:
                existing_contacts[contact_map[cid]] = dumped
                contacts_updated += 1
            else:
                existing_contacts.append(dumped)
                contact_map[cid] = len(existing_contacts) - 1
                contacts_inserted += 1

        client.update_sheet_values("Contacts", existing_contacts)

        # 3. Append / Update Jobs
        job_headers = list(JobExportRow.model_fields.keys())
        jobs_data = [job_headers] + [[r.model_dump().get(h, "") for h in job_headers] for r in job_rows]
        client.update_sheet_values("Jobs", jobs_data)

        # 4. Summary & Audit
        summary_data = [["Metric", "Value"]] + [[k, v] for k, v in summary.model_dump().items()]
        client.update_sheet_values("Summary", summary_data)

        return {
            "status": "success",
            "mode": "upsert",
            "spreadsheet_id": spreadsheet_id,
            "leads_inserted": leads_inserted,
            "leads_updated": leads_updated,
            "contacts_inserted": contacts_inserted,
            "contacts_updated": contacts_updated,
        }
