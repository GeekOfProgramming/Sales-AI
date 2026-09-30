from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from sales_engine.exports.schemas import (
    LeadExportRow,
    ContactExportRow,
    JobExportRow,
    ErrorAuditRow,
    ExportSummary,
    GoogleSheetsConfig,
)

class BaseSheetExporter(ABC):
    """Abstract interface for spreadsheet cloud synchronizers."""

    @abstractmethod
    def export(
        self,
        config: GoogleSheetsConfig,
        lead_rows: List[LeadExportRow],
        contact_rows: List[ContactExportRow],
        job_rows: List[JobExportRow],
        error_rows: List[ErrorAuditRow],
        summary: ExportSummary,
    ) -> Dict[str, Any]:
        """Export or sync data to remote spreadsheet provider."""
        pass
