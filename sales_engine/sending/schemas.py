from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

# State Literals
ApprovalStatus = Literal["pending_review", "approved", "rejected", "changes_requested"]
SendStatus = Literal["not_sent", "sending", "sent", "failed", "blocked", "dry_run", "already_sent"]
OutreachStatus = Literal["draft_ready", "approved", "sent", "send_failed", "do_not_contact"]
SuppressionReason = Literal["manual", "opt_out", "bounce", "complaint", "invalid_recipient"]
ReviewAction = Literal[
    "imported", "edited", "approved", "rejected", "changes_requested",
    "suppressed", "send_requested", "sent", "send_failed"
]


class StoredDraft(BaseModel):
    """Database representation of an email draft revision."""
    draft_id: str = Field(..., description="Unique draft identifier.")
    revision: int = Field(default=1, description="Revision number.")

    lead_id: str = Field(..., description="Lead identifier.")
    contact_id: str = Field(..., description="Contact identifier.")

    recipient_name: str = Field(..., description="Full or first name of recipient.")
    recipient_title: Optional[str] = Field(default=None, description="Job title of recipient.")
    recipient_email: str = Field(..., description="Target work email address.")
    sender_email: Optional[str] = Field(default=None, description="Sender email address.")

    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Email body content.")

    service_used: str = Field(default="", description="The active service pitched.")
    personalization_notes: Optional[str] = Field(default=None, description="Personalization notes.")

    evidence_refs: List[str] = Field(default_factory=list, description="IDs of evidence referenced.")
    source_job_urls: List[str] = Field(default_factory=list, description="Job URLs referenced.")

    language: str = Field(default="en", description="Draft language (en, it, de).")
    tone: str = Field(default="professional_concise", description="Preset tone used.")
    prompt_version: str = Field(default="outreach_v1", description="Version of the prompt.")
    generation_model: str = Field(default="qwen2.5:1.5b", description="LLM used to generate draft.")

    approval_status: ApprovalStatus = Field(default="pending_review", description="Review approval status.")
    send_status: SendStatus = Field(default="not_sent", description="Send delivery status.")
    outreach_status: OutreachStatus = Field(default="draft_ready", description="Workflow outreach status.")

    content_hash: str = Field(..., description="Deterministic SHA-256 fingerprint of current content.")
    approved_content_hash: Optional[str] = Field(default=None, description="Fingerprint stored at approval.")

    reviewer: Optional[str] = Field(default=None, description="Reviewer who took the last action.")
    review_note: Optional[str] = Field(default=None, description="Optional note by reviewer.")

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved_at: Optional[str] = Field(default=None)
    sent_at: Optional[str] = Field(default=None)


class ReviewEvent(BaseModel):
    """Immutable audit event for human review and workflow transitions."""
    event_id: str = Field(..., description="Unique event identifier.")
    draft_id: str = Field(..., description="Draft identifier.")
    revision: int = Field(default=1, description="Draft revision.")
    action: ReviewAction = Field(..., description="Action performed.")
    previous_status: Optional[str] = Field(default=None, description="Previous status before action.")
    new_status: str = Field(..., description="New status after action.")
    reviewer: str = Field(default="system", description="Reviewer identifier.")
    review_note: Optional[str] = Field(default=None, description="Optional note or comment.")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SuppressionEntry(BaseModel):
    """Do-not-contact / suppression list entry."""
    suppression_id: str = Field(..., description="Unique suppression record ID.")
    email: str = Field(..., description="Suppressed email address (normalized lower).")
    company_domain: Optional[str] = Field(default=None, description="Optional company domain.")
    reason: SuppressionReason = Field(default="manual", description="Reason for suppression.")
    source: str = Field(default="manual", description="Source of suppression rule.")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SendAttempt(BaseModel):
    """Record of an explicit email sending attempt."""
    attempt_id: str = Field(..., description="Unique attempt record ID.")
    send_key: str = Field(..., description="Deterministic idempotency key.")
    draft_id: str = Field(..., description="Draft identifier.")
    revision: int = Field(..., description="Revision number.")
    provider: str = Field(..., description="Email provider adapter used (smtp, mock).")
    recipient_email: str = Field(..., description="Recipient email address.")
    sender_email: str = Field(..., description="Sender email address.")
    status: SendStatus = Field(..., description="Result status of attempt.")
    error_type: Optional[str] = Field(default=None, description="Error code if failed/blocked.")
    error_message: Optional[str] = Field(default=None, description="Sanitized error description.")
    provider_message_id: Optional[str] = Field(default=None, description="Message ID returned by provider.")
    attempted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = Field(default=None)


class SendResult(BaseModel):
    """Outcome of a single email send request."""
    draft_id: str
    revision: int
    send_key: str
    status: SendStatus
    provider: str = "none"
    provider_message_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    dry_run: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# API Request / Response schemas

class DraftImportRequest(BaseModel):
    drafts: List[Dict[str, Any]] = Field(..., description="List of Phase 8 EmailDraft objects or dictionaries.")


class DraftImportResponse(BaseModel):
    imported_count: int
    draft_ids: List[str]
    drafts: List[StoredDraft]


class DraftEditRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    personalization_notes: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_email: Optional[str] = None
    reviewer: str = Field(default="human_reviewer")
    note: Optional[str] = None


class DraftApprovalRequest(BaseModel):
    revision: int = Field(..., description="Revision being approved.")
    reviewer: str = Field(..., description="Human reviewer name or ID.")
    note: Optional[str] = Field(default=None, description="Optional approval note.")


class DraftRejectRequest(BaseModel):
    revision: int = Field(..., description="Revision being rejected.")
    reviewer: str = Field(..., description="Human reviewer name or ID.")
    note: Optional[str] = Field(default=None, description="Rejection reason or note.")


class DraftRequestChangesRequest(BaseModel):
    revision: int = Field(..., description="Revision requiring changes.")
    reviewer: str = Field(..., description="Human reviewer name or ID.")
    note: str = Field(..., description="Requested changes description.")


class DraftSendRequest(BaseModel):
    revision: int = Field(..., description="Specific approved revision to send.")
    dry_run: bool = Field(default=True, description="When true, executes full validation with zero network send.")
    reviewer: Optional[str] = Field(default=None, description="Sender operator identity.")


class BatchSendRequest(BaseModel):
    draft_ids: List[str] = Field(..., min_length=1, max_length=50, description="Explicit list of draft IDs to send.")
    dry_run: bool = Field(default=True, description="When true, executes full validation with zero network send.")
    reviewer: Optional[str] = Field(default=None, description="Sender operator identity.")


class BatchSendItemResult(BaseModel):
    draft_id: str
    revision: int
    status: SendStatus
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    provider_message_id: Optional[str] = None


class BatchSendResponse(BaseModel):
    requested: int
    sent: int
    dry_run: int
    blocked: int
    failed: int
    already_sent: int
    results: List[BatchSendItemResult]


class SuppressionAddRequest(BaseModel):
    email: str = Field(..., description="Email to suppress.")
    company_domain: Optional[str] = Field(default=None)
    reason: SuppressionReason = Field(default="manual")
    source: str = Field(default="manual")


class SuppressionCheckResponse(BaseModel):
    email: str
    is_suppressed: bool
    entry: Optional[SuppressionEntry] = None
