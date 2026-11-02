"""
Lead Register data models matching the historical pyBIM 13-field workbook.

Fields:
1. Row / Record Number
2. Company Priority A/B/C
3. Priority Rationale
4. Company Name & Legal Identity
5. Geographic Location
6. Telephone Numbers
7. Business Activities / Services
8. Official Website
9. Responsible Person / Contact
10. Descriptions & Operator Notes
11. Job Advertisement Links
12. Other Links / Projects / References
13. Contact Email Addresses
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone


@dataclass
class FieldReviewRecord:
    """
    Durable, auditable tracking of individual field reviews (Phase 2).
    Tracks exact field key, approved value, review state, timestamp, source,
    associated snapshot/analysis ID, and evidence URL.
    """
    candidate_id: str
    field_name: str
    approved_value: str
    value_checksum: str
    review_state: str = "OPERATOR_APPROVED"  # OPERATOR_APPROVED, OPERATOR_CORRECTED, PENDING, REJECTED
    reviewed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_type: str = "llm_analysis"  # llm_analysis, operator_manual, local_mvp, historical
    snapshot_id: Optional[str] = None
    analysis_id: Optional[str] = None
    evidence_url: Optional[str] = None
    evidence_excerpt: Optional[str] = None
    operator_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LeadRegisterRecord:
    candidate_id: str
    record_number: Optional[int] = None
    priority_level: str = "B"  # A, B, C, or UNKNOWN
    priority_rationale: Optional[str] = None
    company_name: str = ""
    legal_identity: Optional[str] = None  # P.IVA / C.F. / legal form
    geographic_location: Optional[str] = None
    telephone_numbers: Optional[str] = None
    business_activities: Optional[str] = None
    official_website: Optional[str] = None
    responsible_person: Optional[str] = None
    descriptions_and_notes: Optional[str] = None
    job_advertisement_links: Optional[str] = None
    other_links_and_references: Optional[str] = None
    contact_email_addresses: Optional[str] = None
    
    # Supplementary provenance & audit fields
    provenance_source: str = "manual_entry"  # historical_import, pilot_research, operator_researched, manual_entry
    research_date: Optional[str] = None
    verification_status: str = "Unverified"  # Verified, Unverified, Needs Review
    confidence_score: float = 0.0
    evidence_urls: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_13_field_dict(self) -> Dict[str, Any]:
        """Returns ordered dict corresponding to exact 13 fields of the historical pyBIM workbook."""
        return {
            "Record Number": self.record_number if self.record_number is not None else "Not Found",
            "Company Priority": self.priority_level or "Unverified",
            "Priority Rationale": self.priority_rationale or "Not Found",
            "Company Name & Legal Identity": f"{self.company_name} ({self.legal_identity})" if self.legal_identity and self.legal_identity != "Not Found" else self.company_name,
            "Geographic Location": self.geographic_location or "Not Found",
            "Telephone Numbers": self.telephone_numbers or "Not Found",
            "Business Activities / Services": self.business_activities or "Not Found",
            "Official Website": self.official_website or "Not Found",
            "Responsible Person / Contact": self.responsible_person or "Not Found",
            "Descriptions & Operator Notes": self.descriptions_and_notes or "Not Found",
            "Job Advertisement Links": self.job_advertisement_links or "Not Found",
            "Other Links / Projects / References": self.other_links_and_references or "Not Found",
            "Contact Email Addresses": self.contact_email_addresses or "Not Found",
        }
