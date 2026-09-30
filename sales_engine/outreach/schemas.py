from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from backend.schemas import EnrichedLead, StructuredJob

class SenderProfile(BaseModel):
    """Sender identity for cold email outreach."""
    sender_name: str = Field(..., description="Full name of the sender.")
    sender_company: str = Field(..., description="Company name of the sender.")
    sender_role: str = Field(..., description="Title/Role of the sender.")
    sender_email: str = Field(..., description="Professional email address of the sender.")
    sender_website: Optional[str] = Field(default=None, description="Sender company website.")
    signature: Optional[str] = Field(default=None, description="Optional text signature block.")


class EmailDraft(BaseModel):
    """Structured Email Draft generated during Phase 8."""
    draft_id: str = Field(..., description="Deterministic or unique ID for the draft.")
    revision: int = Field(default=1, description="Draft revision number.")

    lead_id: str = Field(..., description="Target lead identifier.")
    contact_id: str = Field(..., description="Target contact identifier.")

    recipient_name: str = Field(..., description="Full or first name of recipient.")
    recipient_title: Optional[str] = Field(default=None, description="Job title of recipient.")
    recipient_email: str = Field(..., description="Target work email address.")

    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Email body content.")

    service_used: str = Field(..., description="The single active service pitched.")
    personalization_notes: Optional[str] = Field(default=None, description="Brief justification/notes on why this was personalized.")

    evidence_refs: List[str] = Field(default_factory=list, description="IDs of evidence referenced (e.g. JOB-001, EVID-001).")
    source_job_urls: List[str] = Field(default_factory=list, description="Public job URLs referenced.")

    language: str = Field(default="en", description="Draft language (en, it, de).")
    tone: str = Field(default="professional_concise", description="Preset tone used.")
    draft_type: str = Field(default="initial_outreach", description="Type of email draft.")

    draft_status: str = Field(default="generated", description="draft status: generated, failed.")
    approval_status: str = Field(default="pending_review", description="ALWAYS pending_review in Phase 8.")
    send_status: str = Field(default="not_sent", description="ALWAYS not_sent in Phase 8.")

    validation_warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings recorded during validation.")

    generation_model: str = Field(..., description="Name of LLM model used.")
    prompt_version: str = Field(default="outreach_v1", description="Version of the prompt used.")

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LLMEmailResponse(BaseModel):
    """Strict schema expected from LLM output."""
    subject: str = Field(..., description="Cold email subject (3-8 words, <=60 chars).")
    body: str = Field(..., description="Email body (70-120 words, <=160 words).")
    service_used: str = Field(..., description="Name of active service selected from allowed list.")
    personalization_notes: Optional[str] = Field(default="", description="Why this trigger was chosen.")
    evidence_refs: List[str] = Field(default_factory=list, description="Evidence item IDs used.")
    cta: Optional[str] = Field(default=None, description="Low-friction call to action.")


class OutreachEvidenceItem(BaseModel):
    """Individual grounded evidence unit provided to the prompt."""
    id: str = Field(..., description="Stable ID like JOB-001, SIG-001, SERVICE-001, CONTACT-001, EVID-001.")
    category: str = Field(..., description="Type: job, signal, service, contact, company, evidence.")
    title: str = Field(..., description="Short title or label.")
    content: str = Field(..., description="Factual snippet or description.")
    url: Optional[str] = Field(default=None, description="Public source URL if applicable.")


class OutreachContext(BaseModel):
    """Deterministic context built for an outreach generation prompt."""
    lead_id: str
    contact_id: str
    company_name: str
    company_domain: Optional[str] = None
    recipient_name: str
    recipient_title: Optional[str] = None
    recipient_email: str
    active_services: List[str] = Field(default_factory=list)
    evidence_items: List[OutreachEvidenceItem] = Field(default_factory=list)
    sender: SenderProfile
    source_job_urls: List[str] = Field(default_factory=list)
    language: str = "en"
    tone: str = "professional_concise"


class GenerateDraftsRequest(BaseModel):
    """API payload for generating personalized cold email drafts."""
    leads: List[EnrichedLead] = Field(..., max_length=50, description="List of enriched leads.")
    jobs: List[StructuredJob] = Field(default_factory=list, description="Optional raw structured jobs for high-fidelity grounding.")
    website_profile: Optional[Dict[str, Any]] = Field(default=None, description="Website profile containing active services.")
    sender_profile: SenderProfile = Field(..., description="Sender identity.")
    language: str = Field(default="en", description="Draft language (en, it, de).")
    tone: str = Field(default="professional_concise", description="Preset tone.")
    require_usable_email: bool = Field(default=True, description="Only generate for verified/likely emails.")
    max_drafts: int = Field(default=50, ge=1, le=50, description="Max drafts to generate per request.")


class SkippedLeadRecord(BaseModel):
    """Details on why a lead was skipped for draft generation."""
    lead_id: str
    company_name: Optional[str] = None
    reason: str
    details: Optional[str] = None


class GenerationErrorRecord(BaseModel):
    """Details on a generation failure."""
    lead_id: str
    company_name: Optional[str] = None
    contact_id: Optional[str] = None
    error_type: str
    message: str


class GenerateDraftsResponse(BaseModel):
    """API response for draft generation."""
    generated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    drafts: List[EmailDraft] = Field(default_factory=list)
    skipped: List[SkippedLeadRecord] = Field(default_factory=list)
    errors: List[GenerationErrorRecord] = Field(default_factory=list)
