from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime

class GoogleSheetsConfig(BaseModel):
    enabled: bool = False
    mode: Literal["snapshot", "upsert"] = "snapshot"
    spreadsheet_id: Optional[str] = None
    credentials_json: Optional[str] = None
    title: Optional[str] = None

class LeadExportRow(BaseModel):
    export_run_id: str
    lead_id: str
    company_name: Optional[str] = ""
    company_domain: Optional[str] = ""
    industry: Optional[str] = ""
    country: Optional[str] = ""
    employee_count: Optional[int] = None
    lead_score: int = 0
    qualified: bool = False
    fit_score: int = 0
    intent_score: int = 0
    recency_score: int = 0
    evidence_score: int = 0
    qualification_threshold: int = 60
    total_job_count: int = 0
    relevant_job_count: int = 0
    lead_reasons: str = ""
    lead_evidence: str = ""
    enrichment_status: str = "pending"
    providers_used: str = ""
    enrichment_errors: str = ""
    best_contact_id: Optional[str] = ""
    best_contact_name: Optional[str] = ""
    best_contact_title: Optional[str] = ""
    best_contact_email: Optional[str] = ""
    best_contact_email_status: Optional[str] = ""
    best_contact_email_confidence: Optional[int] = None
    best_contact_linkedin: Optional[str] = ""
    best_contact_score: Optional[int] = None
    buyer_role_match: Optional[str] = ""
    outreach_status: str = "not_started"
    approval_status: str = "pending_review"
    send_status: str = "not_sent"
    draft_subject: Optional[str] = ""
    draft_body: Optional[str] = ""
    personalization_notes: Optional[str] = ""
    last_outreach_at: Optional[str] = ""
    owner: Optional[str] = ""
    notes: Optional[str] = ""
    created_at: str
    updated_at: str

class ContactExportRow(BaseModel):
    lead_id: str
    contact_id: str
    company_name: Optional[str] = ""
    company_domain: Optional[str] = ""
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    full_name: Optional[str] = ""
    job_title: Optional[str] = ""
    seniority: Optional[str] = ""
    department: Optional[str] = ""
    work_email: Optional[str] = ""
    email_status: Optional[str] = "unknown"
    email_confidence: Optional[int] = 0
    linkedin_url: Optional[str] = ""
    buyer_role_match: Optional[str] = ""
    contact_score: int = 0
    data_sources: str = ""
    provider_person_id: Optional[str] = ""
    is_best_contact: bool = False
    contact_rank: int = 0

class JobExportRow(BaseModel):
    lead_id: str
    company_name: Optional[str] = ""
    company_domain: Optional[str] = ""
    job_title: Optional[str] = ""
    job_url: Optional[str] = ""
    source: str = "generic"
    source_company_key: Optional[str] = ""
    location: Optional[str] = ""
    employment_type: Optional[str] = ""
    posted_date: Optional[str] = ""
    seniority: Optional[str] = ""
    remote_status: Optional[str] = ""
    technologies: str = ""
    relevant_signals: str = ""
    signal_evidence: str = ""
    is_relevant: bool = True

class ErrorAuditRow(BaseModel):
    export_run_id: str
    lead_id: Optional[str] = ""
    company_name: Optional[str] = ""
    stage: str = "enrichment"
    error_type: str = "provider_error"
    provider: Optional[str] = ""
    message: str = ""
    source_reference: Optional[str] = ""
    created_at: str

class ExportSummary(BaseModel):
    total_leads: int = 0
    qualified_leads: int = 0
    unqualified_leads: int = 0
    complete_enrichment: int = 0
    partial_enrichment: int = 0
    provider_errors: int = 0
    leads_with_best_contact: int = 0
    leads_with_verified_email: int = 0
    leads_with_likely_email: int = 0
    leads_without_work_email: int = 0
    average_lead_score: float = 0.0
    export_timestamp: str

class ExportRequest(BaseModel):
    leads: List[Any] = Field(..., description="List of EnrichedLead or CompanyLead dicts/objects")
    jobs: Optional[List[Any]] = Field(default=None, description="Optional raw StructuredJobs corresponding to leads")
    formats: List[Literal["xlsx", "csv", "json"]] = Field(default_factory=lambda: ["xlsx", "csv", "json"])
    qualified_only: bool = True
    include_all_contacts: bool = True
    include_jobs: bool = True
    google_sheets: Optional[GoogleSheetsConfig] = None
    output_dir: Optional[str] = None

class ExportResponse(BaseModel):
    export_run_id: str
    exported_leads: int
    exported_contacts: int
    exported_jobs: int
    files: Dict[str, str] = Field(default_factory=dict)
    google_sheet_result: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
