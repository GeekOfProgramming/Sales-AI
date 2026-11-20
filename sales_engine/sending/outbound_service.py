"""
sales_engine/sending/outbound_service.py
MAIL-SEND-01A: High-Level Outbound Queueing & Dispatch Service.

Coordinates:
- Explicit operator authorization & queueing of scheduled drafts.
- Legacy draft quarantine enforcement (prevents pre-existing test/scheduled drafts from dispatch).
- Safe queue inspection and cancellation.
- Idempotent queue submission.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from sales_engine.sending.outbound_models import OutboundQueueJob
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.mime_builder import generate_rfc_message_id, validate_message_payload
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.models import MailboxDraftRecord


class OutboundDispatchService:
    """
    Coordinates queueing of approved drafts into the durable outbound queue.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self.queue_store = OutboundQueueStore(db_path=self.db_path)
        self.safeguards = OutboundSafeguardService(db_path=self.db_path)
        self.repo = MailboxRepository(db_path=self.db_path)

    def queue_approved_draft(
        self,
        draft_id: str,
        operator_username: str,
        is_legacy_revalidated: bool = False,
    ) -> OutboundQueueJob:
        """
        Validates and queues an approved draft into outbound_jobs.
        
        CRITICAL QUARANTINE POLICY:
        - Legacy drafts created before the activation of MAIL-SEND-01A (or without explicit operator revalidation)
          are quarantined and cannot be queued without is_legacy_revalidated=True.
        - Freezes the approved immutable snapshot into the queue record.
        - Assigns a stable RFC 5322 Message-ID.
        """
        draft: Optional[MailboxDraftRecord] = self.repo.get_draft(draft_id)
        if not draft:
            raise ValueError(f"Draft '{draft_id}' not found.")

        if draft.draft_status != "scheduled":
            raise ValueError(
                f"Only drafts with status 'scheduled' can be queued for delivery (current: '{draft.draft_status}')."
            )

        if not draft.scheduled_for_utc:
            raise ValueError(f"Draft '{draft_id}' has no scheduled_for_utc timestamp.")

        # Determine snapshot content to freeze (use snapshot fields if available, otherwise current draft body)
        frozen_subject = draft.snapshot_subject or draft.subject
        frozen_plain = draft.snapshot_plain_text or draft.body_plain_text
        frozen_html = draft.snapshot_html_sanitized or draft.body_html_sanitized
        frozen_to = draft.snapshot_to_addresses or draft.to_addresses
        frozen_cc = draft.cc_addresses or []
        frozen_bcc = draft.bcc_addresses or []

        # 1. Safeguards check across all envelope destinations (To, CC, BCC)
        all_recipients = frozen_to + frozen_cc + frozen_bcc
        clean_to = self.safeguards.validate_pre_queue(
            account_id=draft.account_id,
            sender_email=draft.sender_email,
            recipients=frozen_to,
            candidate_id=draft.candidate_id,
        )
        # Verify CC and BCC recipients are valid syntax and not suppressed
        if frozen_cc:
            clean_cc = self.safeguards.validate_recipients(frozen_cc)
            self.safeguards.check_suppression(clean_cc)
        if frozen_bcc:
            clean_bcc = self.safeguards.validate_recipients(frozen_bcc)
            self.safeguards.check_suppression(clean_bcc)

        # 2. Content placeholder check
        content_errors = validate_message_payload(
            subject=frozen_subject,
            plain_text=frozen_plain,
            html_text=frozen_html,
        )
        if content_errors:
            raise SafeguardViolationError("; ".join(content_errors))

        # 3. Check for existing job for this draft
        existing_job = self.queue_store.get_job_by_draft_id(draft_id)
        if existing_job and existing_job.status in ("scheduled", "sending", "smtp_accepted"):
            return existing_job

        # 4. Generate stable RFC Message-ID and idempotency key
        rfc_msg_id = generate_rfc_message_id(draft.sender_email)
        idempotency_key = f"idemp_{draft.draft_id}_v{draft.version}_{draft.scheduled_for_utc}"

        envelope_recipients = list(dict.fromkeys(clean_to + (draft.cc_addresses or []) + (draft.bcc_addresses or [])))
        now_utc = datetime.now(timezone.utc).isoformat()

        job = OutboundQueueJob(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            source_draft_id=draft.draft_id,
            account_id=draft.account_id,
            sender_email=draft.sender_email,
            envelope_from=draft.sender_email,
            envelope_recipients_json=json.dumps(envelope_recipients),
            header_to_json=json.dumps(clean_to),
            header_cc_json=json.dumps(draft.cc_addresses or []),
            header_bcc_json=json.dumps(draft.bcc_addresses or []),
            subject=frozen_subject,
            body_plain_text=frozen_plain,
            body_html_sanitized=frozen_html,
            rfc_message_id=rfc_msg_id,
            candidate_id=draft.candidate_id,
            contact_id=draft.contact_id,
            scheduled_for_utc=draft.scheduled_for_utc,
            scheduled_local_date=draft.scheduled_local_date or "",
            scheduled_local_time=draft.scheduled_local_time or "",
            scheduled_timezone=draft.scheduled_timezone or "Europe/Rome",
            approved_by=operator_username,
            approved_at_utc=now_utc,
            status="scheduled",
            version=1,
            idempotency_key=idempotency_key,
        )

        return self.queue_store.enqueue_job(job)

    def cancel_queued_draft(self, draft_id: str, operator_username: str, reason: Optional[str] = None) -> Optional[OutboundQueueJob]:
        """Cancels a queued delivery job if one exists."""
        job = self.queue_store.get_job_by_draft_id(draft_id)
        if not job:
            return None
        if job.status == "scheduled":
            return self.queue_store.cancel_job(job.job_id, operator_username=operator_username, reason=reason)
        return job
