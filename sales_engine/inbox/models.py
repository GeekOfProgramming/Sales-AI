import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

ConversationStatus = Literal[
    "new_reply",
    "needs_review",
    "waiting_on_us",
    "waiting_on_them",
    "meeting_proposed",
    "meeting_scheduled",
    "closed_positive",
    "closed_negative",
    "do_not_contact",
    "auto_reply",
    "bounce",
    "unknown",
]

ReplyCategory = Literal[
    "positive_interest",
    "meeting_proposed",
    "referral",
    "question",
    "not_now",
    "not_interested",
    "unsubscribe",
    "bounce",
    "auto_reply",
    "unknown",
]

MeetingStatus = Literal[
    "none",
    "proposed",
    "scheduled",
    "completed",
    "cancelled",
    "unknown",
]

OpportunityStatus = Literal[
    "unknown",
    "active_conversation",
    "meeting",
    "qualified_opportunity",
    "closed_lost",
    "closed_won",
]

SuggestionStatus = Literal[
    "generated",
    "pending_review",
    "approved",
    "rejected",
    "superseded",
    "sent",
]

ConnectionState = Literal[
    "not_configured",
    "configured",
    "connected",
    "auth_expired",
    "error",
    "disabled",
]


class InboundMessage(BaseModel):
    """Normalized inbound email record."""
    inbound_message_id: str = Field(default_factory=lambda: f"inb_{uuid.uuid4().hex[:12]}")
    provider: str = Field(default="mock")
    provider_message_id: str
    provider_thread_id: Optional[str] = None

    internet_message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: List[str] = Field(default_factory=list)

    from_name: Optional[str] = None
    from_email: str
    to_emails: List[str] = Field(default_factory=list)
    cc_emails: List[str] = Field(default_factory=list)

    subject: str
    body_text: str
    body_html_present: bool = False

    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    linked_outbound_draft_id: Optional[str] = None
    linked_outbound_revision: Optional[int] = None
    linked_send_attempt_id: Optional[str] = None

    company_id: Optional[str] = None
    contact_id: Optional[str] = None
    conversation_id: Optional[str] = None

    classification: ReplyCategory = "unknown"
    classification_confidence: float = 0.0

    is_auto_reply: bool = False
    is_bounce: bool = False
    is_unsubscribe: bool = False

    raw_headers_ref: Optional[Dict[str, Any]] = None
    raw_provider_ref: Optional[Dict[str, Any]] = None

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Conversation(BaseModel):
    """Persistent representation of a message thread relationship."""
    conversation_id: str = Field(default_factory=lambda: f"conv_{uuid.uuid4().hex[:12]}")
    company_id: Optional[str] = None
    primary_contact_id: Optional[str] = None

    provider: str = "mock"
    provider_thread_id: Optional[str] = None

    status: ConversationStatus = "new_reply"
    reply_category: ReplyCategory = "unknown"
    sentiment_optional: Optional[str] = None

    first_outbound_at: Optional[str] = None
    first_reply_at: Optional[str] = None
    last_message_at: Optional[str] = None

    actual_respondent_contact_id: Optional[str] = None

    meeting_status: MeetingStatus = "none"
    opportunity_status: OpportunityStatus = "unknown"

    active_conversation: bool = False

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ConversationEvent(BaseModel):
    """Auditable timeline event within a conversation."""
    event_id: str = Field(default_factory=lambda: f"cev_{uuid.uuid4().hex[:12]}")
    conversation_id: str
    event_type: str
    actor: str = "system"
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReplySuggestion(BaseModel):
    """AI or template generated operator response draft pending human review."""
    suggestion_id: str = Field(default_factory=lambda: f"sug_{uuid.uuid4().hex[:12]}")
    conversation_id: str
    inbound_message_id: str

    subject: str
    body: str

    evidence_refs: List[str] = Field(default_factory=list)
    classification: ReplyCategory = "unknown"

    generation_model: str = "qwen2.5:1.5b"
    prompt_version: str = "reply_v1"
    language: str = "en"
    tone: str = "professional_concise"

    status: SuggestionStatus = "pending_review"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: Optional[str] = None
    approved_at: Optional[str] = None
    sent_at: Optional[str] = None
    approved_by: Optional[str] = None
    rejection_reason: Optional[str] = None


class MeetingProposal(BaseModel):
    """Explicit candidate times extracted from an inbound message."""
    proposal_id: str = Field(default_factory=lambda: f"mtg_{uuid.uuid4().hex[:12]}")
    conversation_id: str
    inbound_message_id: str
    proposed_times: List[str] = Field(default_factory=list)
    timezone: Optional[str] = None
    raw_text_snippet: Optional[str] = None
    status: str = "proposed"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MailboxConnectionInfo(BaseModel):
    """Connection information with zero secret leakage."""
    provider: str
    status: ConnectionState = "not_configured"
    account_email: Optional[str] = None
    last_synced_at: Optional[str] = None
    error_message: Optional[str] = None


class MailboxSyncSummary(BaseModel):
    """Summary of a manual or scheduled inbox synchronization run."""
    provider: str
    cursor: Optional[str] = None
    messages_fetched: int = 0
    new_replies: int = 0
    errors: List[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
