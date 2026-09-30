import os
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sales_engine.sending.schemas import (
    StoredDraft,
    SendAttempt,
    SendResult,
    ReviewEvent,
    BatchSendResponse,
    BatchSendItemResult,
)
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.send_validator import SendValidator
from sales_engine.sending.base_sender import BaseEmailSender
from sales_engine.sending.smtp_sender import SMTPEmailSender
from sales_engine.sending.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class SendOrchestrator:
    """
    Coordinates pre-send validation, rate limiting, concurrency/idempotency,
    email provider dispatch, and immutable audit recording.
    Zero LLM involvement.
    """

    def __init__(
        self,
        review_store: Optional[ReviewStore] = None,
        suppression_store: Optional[SuppressionStore] = None,
        sender: Optional[BaseEmailSender] = None,
        rate_limiter: Optional[RateLimiter] = None,
        email_send_enabled: Optional[bool] = None,
        opt_out_text: Optional[str] = None,
    ):
        self.store = review_store or ReviewStore()
        self.suppression = suppression_store or SuppressionStore(self.store)
        self.sender = sender or SMTPEmailSender()
        self.rate_limiter = rate_limiter or RateLimiter()

        if email_send_enabled is not None:
            self.email_send_enabled = email_send_enabled
        else:
            self.email_send_enabled = os.environ.get("EMAIL_SEND_ENABLED", "false").lower() in ("true", "1", "yes")

        self.opt_out_text = opt_out_text or os.environ.get("OUTREACH_OPT_OUT_TEXT")
        self.from_email = os.environ.get("SMTP_FROM_EMAIL", "outreach@pybim.com")
        self.from_name = os.environ.get("SMTP_FROM_NAME", "pyBIM Solutions")

    def send_draft(
        self,
        draft_id: str,
        revision: int,
        dry_run: bool = True,
        reviewer: Optional[str] = None,
    ) -> SendResult:
        """
        Executes a single explicit draft send.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        draft = self.store.get_draft(draft_id, revision)
        send_key = (
            f"{draft.draft_id}:{draft.revision}:{draft.recipient_email}"
            if draft
            else f"{draft_id}:{revision}:unknown"
        )

        # 1. Idempotency Check: Already successfully sent?
        if draft and self.store.has_successful_send(send_key):
            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status="already_sent",
                error_type="already_sent",
                error_message=f"Draft {draft_id} revision {revision} was already sent.",
                dry_run=dry_run,
            )

        # 2. Pre-Send Validation
        is_valid, err_type, err_msg = SendValidator.validate_pre_send(
            draft=draft,
            suppression_store=self.suppression,
            trusted_sender_email=self.from_email,
            email_send_enabled=self.email_send_enabled,
            dry_run=dry_run,
        )

        if not is_valid:
            attempt_status = "blocked" if err_type in ("suppressed_recipient", "approval_stale", "not_approved") else "failed"

            # Record attempt in DB
            self.store.record_send_attempt(
                SendAttempt(
                    attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                    send_key=send_key,
                    draft_id=draft_id,
                    revision=revision,
                    provider="none",
                    recipient_email=draft.recipient_email if draft else "unknown",
                    sender_email=self.from_email,
                    status=attempt_status,
                    error_type=err_type,
                    error_message=err_msg,
                    attempted_at=now_iso,
                    completed_at=now_iso,
                )
            )

            # Update draft status if relevant
            if draft:
                if err_type == "suppressed_recipient":
                    self.store.update_draft_status(
                        draft_id, revision,
                        send_status="blocked",
                        outreach_status="do_not_contact",
                    )
                elif draft.approval_status != "approved":
                    pass

            return SendResult(
                draft_id=draft_id,
                revision=revision,
                send_key=send_key,
                status=attempt_status,
                error_type=err_type,
                error_message=err_msg,
                dry_run=dry_run,
            )

        # 3. Dry-Run Path (Zero Network Calls)
        if dry_run:
            self.store.record_send_attempt(
                SendAttempt(
                    attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                    send_key=send_key,
                    draft_id=draft.draft_id,
                    revision=draft.revision,
                    provider="none",
                    recipient_email=draft.recipient_email,
                    sender_email=self.from_email,
                    status="dry_run",
                    error_type=None,
                    error_message="Dry run validated successfully with zero network transmission.",
                    attempted_at=now_iso,
                    completed_at=now_iso,
                )
            )
            # Dry run must NOT mark draft as sent or mutate send_status away from not_sent
            return SendResult(
                draft_id=draft.draft_id,
                revision=draft.revision,
                send_key=send_key,
                status="dry_run",
                provider="none",
                dry_run=True,
            )

        # 4. Concurrency & Idempotency Locking
        locked = self.store.acquire_send_lock(send_key, draft.draft_id, draft.revision)
        if not locked:
            return SendResult(
                draft_id=draft.draft_id,
                revision=draft.revision,
                send_key=send_key,
                status="already_sent",
                error_type="already_sent",
                error_message="Concurrent sending request in progress or already delivered.",
                dry_run=False,
            )

        # 5. Rate Limit Check
        rate_ok, rate_err = self.rate_limiter.check_rate_limit(1)
        if not rate_ok:
            self.store.release_send_lock(send_key)
            return SendResult(
                draft_id=draft.draft_id,
                revision=draft.revision,
                send_key=send_key,
                status="failed",
                error_type="rate_limit_exceeded",
                error_message=rate_err,
                dry_run=False,
            )

        # 6. Atomic State Transition: approved + not_sent -> sending
        self.store.update_draft_status(draft.draft_id, draft.revision, send_status="sending")
        self.store.record_event(
            ReviewEvent(
                event_id=f"rev_{uuid.uuid4().hex[:12]}",
                draft_id=draft.draft_id,
                revision=draft.revision,
                action="send_requested",
                previous_status=draft.send_status,
                new_status="sending",
                reviewer=reviewer or "system",
                review_note="Initiating provider transmission",
                created_at=now_iso,
            )
        )

        # 7. Prepare final body with opt-out footer if configured
        final_body = draft.body
        if self.opt_out_text and self.opt_out_text.strip():
            final_body = f"{draft.body}\n\n---\n{self.opt_out_text.strip()}"

        # 8. Record pacing
        self.rate_limiter.record_send(1)

        # 9. Provider Dispatch
        send_res = self.sender.send_email(
            draft_id=draft.draft_id,
            revision=draft.revision,
            send_key=send_key,
            to_email=draft.recipient_email,
            from_email=self.from_email,
            from_name=self.from_name,
            subject=draft.subject,
            body=final_body,
        )

        completed_iso = datetime.now(timezone.utc).isoformat()

        # 10. Process Provider Outcome
        if send_res.status == "sent":
            self.store.mark_send_lock_sent(send_key)
            self.store.update_draft_status(
                draft.draft_id,
                draft.revision,
                send_status="sent",
                outreach_status="sent",
                sent_at=completed_iso,
            )
            self.store.record_send_attempt(
                SendAttempt(
                    attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                    send_key=send_key,
                    draft_id=draft.draft_id,
                    revision=draft.revision,
                    provider=send_res.provider,
                    recipient_email=draft.recipient_email,
                    sender_email=self.from_email,
                    status="sent",
                    provider_message_id=send_res.provider_message_id,
                    attempted_at=now_iso,
                    completed_at=completed_iso,
                )
            )
            self.store.record_event(
                ReviewEvent(
                    event_id=f"rev_{uuid.uuid4().hex[:12]}",
                    draft_id=draft.draft_id,
                    revision=draft.revision,
                    action="sent",
                    previous_status="sending",
                    new_status="sent",
                    reviewer=reviewer or "system",
                    review_note=f"Delivered via {send_res.provider}. ID: {send_res.provider_message_id}",
                    created_at=completed_iso,
                )
            )
        else:
            # Failed: Release lock so retry may be explicitly requested later (NO AUTO RETRY)
            self.store.release_send_lock(send_key)
            self.store.update_draft_status(
                draft.draft_id,
                draft.revision,
                send_status="failed",
                outreach_status="send_failed",
            )
            self.store.record_send_attempt(
                SendAttempt(
                    attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                    send_key=send_key,
                    draft_id=draft.draft_id,
                    revision=draft.revision,
                    provider=send_res.provider,
                    recipient_email=draft.recipient_email,
                    sender_email=self.from_email,
                    status="failed",
                    error_type=send_res.error_type,
                    error_message=send_res.error_message,
                    attempted_at=now_iso,
                    completed_at=completed_iso,
                )
            )
            self.store.record_event(
                ReviewEvent(
                    event_id=f"rev_{uuid.uuid4().hex[:12]}",
                    draft_id=draft.draft_id,
                    revision=draft.revision,
                    action="send_failed",
                    previous_status="sending",
                    new_status="failed",
                    reviewer=reviewer or "system",
                    review_note=f"Send failed: {send_res.error_message}",
                    created_at=completed_iso,
                )
            )

        return send_res

    def send_batch(
        self,
        draft_ids: List[str],
        dry_run: bool = True,
        reviewer: Optional[str] = None,
    ) -> BatchSendResponse:
        """
        Sends an explicit list of draft IDs with partial failure isolation.
        """
        if len(draft_ids) > 50:
            raise ValueError("Batch send caps at 50 drafts per request.")

        # Rate check if not dry_run
        if not dry_run:
            rate_ok, rate_err = self.rate_limiter.check_rate_limit(len(draft_ids))
            if not rate_ok:
                raise ValueError(rate_err or "Rate limit exceeded for batch.")

        results: List[BatchSendItemResult] = []
        counts = {"sent": 0, "dry_run": 0, "blocked": 0, "failed": 0, "already_sent": 0}

        for did in draft_ids:
            draft = self.store.get_draft(did)
            if not draft:
                results.append(
                    BatchSendItemResult(
                        draft_id=did,
                        revision=1,
                        status="failed",
                        error_type="validation_failed",
                        error_message=f"Draft not found: {did}",
                    )
                )
                counts["failed"] += 1
                continue

            res = self.send_draft(
                draft_id=draft.draft_id,
                revision=draft.revision,
                dry_run=dry_run,
                reviewer=reviewer,
            )

            st = res.status
            if st in counts:
                counts[st] += 1
            else:
                counts["failed"] += 1

            results.append(
                BatchSendItemResult(
                    draft_id=res.draft_id,
                    revision=res.revision,
                    status=res.status,
                    error_type=res.error_type,
                    error_message=res.error_message,
                    provider_message_id=res.provider_message_id,
                )
            )

        return BatchSendResponse(
            requested=len(draft_ids),
            sent=counts["sent"],
            dry_run=counts["dry_run"],
            blocked=counts["blocked"],
            failed=counts["failed"],
            already_sent=counts["already_sent"],
            results=results,
        )
