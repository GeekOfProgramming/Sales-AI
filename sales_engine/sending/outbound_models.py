"""
sales_engine/sending/outbound_models.py
MAIL-SEND-01A: Outbound Queue & Worker Data Models & Schemas.

Defines schemas and immutable data contracts for:
- OutboundQueueJob
- OutboundAttemptRecord
- MIMEGenerationPayload
- OutboundWorkerConfig
"""

import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

# Outbound Job Status Lifecycle
OutboundJobStatus = Literal[
    "scheduled",            # Staged in queue, waiting for target scheduled time
    "sending",              # Leased / claimed by an active worker turn
    "smtp_accepted",        # Successfully handed over to SMTP server with positive 250 response
    "failed",               # Permanent or max-retried delivery failure
    "acceptance_unknown",   # Ambiguous timeout/disconnect after DATA submission; requires operator audit
    "cancelled",            # Operator cancelled prior to transmission
    "requires_review",      # Flagged by safety guards (e.g., overdue dormancy, ambiguous state)
]

AttemptStatus = Literal[
    "success",
    "failed_retryable",
    "failed_permanent",
    "unknown",
]


class OutboundQueueJob(BaseModel):
    """
    Dedicated transactional outbound queue record.
    Decoupled from mailbox_drafts for durable multi-worker execution.
    """
    job_id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    source_draft_id: str
    account_id: str                          # Authorized sender account (e.g., acc_info_com, acc_info_it)
    sender_email: str                        # Authorized email (info@pybim.com or info@pybim.it)
    envelope_from: str                       # SMTP envelope sender
    envelope_recipients_json: str            # Clean RFC 5321 envelope recipient list as JSON string
    header_to_json: str                      # Visible To header recipient list as JSON string
    header_cc_json: str = "[]"               # Visible CC header recipient list as JSON string
    header_bcc_json: str = "[]"              # Audit record of BCC (never serialized into MIME headers)
    subject: str
    body_plain_text: str
    body_html_sanitized: Optional[str] = None
    rfc_message_id: str                      # Stable RFC 5322 Message-ID generated at queueing time
    candidate_id: Optional[str] = None
    contact_id: Optional[str] = None
    scheduled_for_utc: str                   # UTC instant when eligible for dispatch
    scheduled_local_date: str                # Preserved operator date (YYYY-MM-DD)
    scheduled_local_time: str                # Preserved operator time (HH:MM)
    scheduled_timezone: str = "Europe/Rome"  # Target operational timezone
    approved_by: str                         # Operator username who explicitly authorized queueing
    approved_at_utc: str                     # UTC timestamp of explicit human authorization
    status: OutboundJobStatus = "scheduled"
    version: int = 1                         # Optimistic concurrency version
    idempotency_key: str                     # Unique token to prevent duplicate queue submissions
    worker_lease_id: Optional[str] = None    # Token representing current worker lease
    worker_leased_at_utc: Optional[str] = None
    lease_expires_at_utc: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3
    last_attempt_at_utc: Optional[str] = None
    smtp_response_code: Optional[int] = None
    smtp_response_message: Optional[str] = None
    review_reason: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OutboundAttemptRecord(BaseModel):
    """
    Audit record for every discrete SMTP delivery attempt.
    """
    attempt_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:12]}")
    job_id: str
    attempt_number: int
    worker_id: str
    status: AttemptStatus
    smtp_host: str
    smtp_port: int
    smtp_code: Optional[int] = None
    smtp_message: Optional[str] = None
    error_type: Optional[str] = None
    error_details: Optional[str] = None
    duration_ms: Optional[int] = None
    started_at_utc: str
    completed_at_utc: str


class OutboundWorkerConfig(BaseModel):
    """
    Configuration parameters for outbound queue processing.
    """
    enabled: bool = False                    # Master killswitch (defaults to False)
    authorized_accounts: List[str] = Field(default_factory=lambda: ["info@pybim.com", "info@pybim.it"])
    batch_size: int = 10                     # Bounded fetch size per loop
    lease_duration_seconds: int = 300        # 5 minutes lease before stale job reclamation
    pacing_delay_seconds: float = 2.0        # Politeness spacing between SMTP submissions
    max_overdue_seconds: int = 7200          # 2 hours: if scheduled time was >2h ago, requires operator review
    smtp_timeout_seconds: float = 15.0       # Socket timeout for SMTP operations
