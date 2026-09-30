import os
import csv
from typing import List, Dict
from sales_engine.exports.schemas import (
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
)

class CSVExporter:
    """Exports canonical data rows to standardized CSV files."""

    def __init__(self):
        pass

    def export(
        self,
        output_dir: str,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow],
    ) -> Dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        results = {}

        # 1. leads.csv
        leads_path = os.path.join(output_dir, "leads.csv")
        self._write_csv(leads_path, [r.model_dump() for r in lead_rows], default_headers=list(LeadExportRow.model_fields.keys()))
        results["leads_csv"] = leads_path

        # 2. contacts.csv
        contacts_path = os.path.join(output_dir, "contacts.csv")
        self._write_csv(contacts_path, [r.model_dump() for r in contact_rows], default_headers=list(ContactExportRow.model_fields.keys()))
        results["contacts_csv"] = contacts_path

        # 3. jobs.csv
        jobs_path = os.path.join(output_dir, "jobs.csv")
        self._write_csv(jobs_path, [r.model_dump() for r in job_rows], default_headers=list(JobExportRow.model_fields.keys()))
        results["jobs_csv"] = jobs_path

        # 4. errors_audit.csv
        errors_path = os.path.join(output_dir, "errors_audit.csv")
        self._write_csv(errors_path, [r.model_dump() for r in error_rows], default_headers=list(ErrorAuditRow.model_fields.keys()))
        results["errors_csv"] = errors_path

        return results

    def _write_csv(self, file_path: str, rows: List[Dict], default_headers: List[str] = None):
        headers = list(rows[0].keys()) if rows else (default_headers or [])
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            if headers:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                for r in rows:
                    # Format None as empty string for CSV portability
                    cleaned_row = {k: ("" if v is None else v) for k, v in r.items()}
                    writer.writerow(cleaned_row)
