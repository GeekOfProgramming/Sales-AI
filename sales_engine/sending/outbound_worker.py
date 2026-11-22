"""
sales_engine/sending/outbound_worker.py
MAIL-SEND-01A: Safe Outbound Queue Worker with Transactional Leases.

Standalone Python worker decoupled from Uvicorn web processes and inbound IMAP tasks.
Features:
- Transactional job leasing and multi-worker race prevention.
- Bounded batch size and configurable polite inter-send pacing.
- Pre-submission suppression enforcement.
- Safe overdue-job handling (dormant/sleep recovery requires operator review).
- Comprehensive error classification:
    - Success -> smtp_accepted
    - Permanent Error -> failed
    - Retryable Error (attempts < max) -> returned to scheduled
    - Retryable Error (attempts >= max) -> failed
    - Unknown/ambiguous disconnect -> acceptance_unknown (manual audit required)
- Master offline killswitch (enabled=False by default).
- Zero automated mutations to candidate_companies, candidate_contacts, or historical_sent_emails.
"""

import os
import sys
import time
import uuid
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

from sales_engine.sending.outbound_models import (
    OutboundQueueJob,
    OutboundAttemptRecord,
    OutboundWorkerConfig,
    OutboundJobStatus,
)
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.mime_builder import build_mime_message
from sales_engine.sending.smtp_adapter import RegisterItSMTPAdapter, SMTPSubmissionResult
from sales_engine.inbox.mailbox_repository import MailboxRepository

logger = logging.getLogger(__name__)


class OutboundWorker:
    """
    Transactional processor for queued outbound emails.
    """

    def __init__(
        self,
        db_path: str,
        config: Optional[OutboundWorkerConfig] = None,
        smtp_adapter: Optional[RegisterItSMTPAdapter] = None,
        worker_id: Optional[str] = None,
    ):
        self.db_path = str(db_path)
        self.config = config or OutboundWorkerConfig()
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.queue_store = OutboundQueueStore(db_path=self.db_path)
        self.safeguards = OutboundSafeguardService(db_path=self.db_path)
        self.smtp_adapter = smtp_adapter or RegisterItSMTPAdapter()
        self.repo = MailboxRepository(db_path=self.db_path)

    def process_batch(
        self,
        now_utc: Optional[str] = None,
        custom_server: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Executes one worker pass:
        1. Checks master enablement killswitch.
        2. Leases a batch of eligible jobs.
        3. Processes each claimed job with safeguards, MIME assembly, and SMTP delivery.
        4. Records attempt audit history.
        """
        results = {
            "worker_id": self.worker_id,
            "processed": 0,
            "accepted": 0,
            "failed": 0,
            "unknown": 0,
            "review_required": 0,
            "jobs": [],
        }

        # 1. Master killswitch check: Fail closed if either switch is disabled or contradictory
        # Both EMAIL_SEND_ENABLED and OUTBOUND_SEND_ENABLED must be explicitly true to permit live sending.
        env_outbound = os.getenv("OUTBOUND_SEND_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        env_email = os.getenv("EMAIL_SEND_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        
        # If either switch is explicitly false, fail closed regardless of other settings
        is_live_enabled = (self.config.enabled or env_outbound) and env_email
        if not is_live_enabled and custom_server is None:
            # Worker is inert by default
            return {
                "worker_id": self.worker_id,
                "status": "disabled",
                "message": "Outbound worker is disabled by default (OUTBOUND_SEND_ENABLED=false or EMAIL_SEND_ENABLED=false). No jobs processed.",
                "processed": 0,
            }

        lease_id = f"lease_{uuid.uuid4().hex[:12]}"
        current_time = now_utc or datetime.now(timezone.utc).isoformat()
        current_dt = datetime.fromisoformat(current_time.replace("Z", "+00:00"))

        # 2. Claim batch
        claimed_jobs = self.queue_store.claim_next_jobs(
            worker_id=self.worker_id,
            lease_id=lease_id,
            lease_duration_seconds=self.config.lease_duration_seconds,
            batch_size=self.config.batch_size,
            now_utc=current_time,
        )

        if not claimed_jobs:
            return results

        # 3. Process claimed jobs sequentially
        for job in claimed_jobs:
            results["processed"] += 1
            res_summary = self._process_single_job(
                job=job,
                current_dt=current_dt,
                is_live_enabled=is_live_enabled,
                custom_server=custom_server,
            )
            results["jobs"].append(res_summary)

            if res_summary["final_status"] == "smtp_accepted":
                results["accepted"] += 1
            elif res_summary["final_status"] == "failed":
                results["failed"] += 1
            elif res_summary["final_status"] == "acceptance_unknown":
                results["unknown"] += 1
            elif res_summary["final_status"] == "requires_review":
                results["review_required"] += 1

            # Politeness spacing between deliveries
            if self.config.pacing_delay_seconds > 0 and len(claimed_jobs) > 1:
                time.sleep(self.config.pacing_delay_seconds)

        return results

    def _process_single_job(
        self,
        job: OutboundQueueJob,
        current_dt: datetime,
        is_live_enabled: bool,
        custom_server: Optional[Any],
    ) -> Dict[str, Any]:
        """Processes an individual claimed job through all validation and sending gates."""
        t_start = datetime.now(timezone.utc).isoformat()
        attempt_num = job.attempt_count + 1

        # Check authorized sender account
        if job.sender_email not in self.config.authorized_accounts:
            reason = f"Sender email '{job.sender_email}' is not in authorized worker accounts."
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="unauthorized_sender",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "requires_review", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "requires_review", "error": reason}

        # Check overdue threshold (sleep / offline protection)
        scheduled_dt = datetime.fromisoformat(job.scheduled_for_utc.replace("Z", "+00:00"))
        overdue_seconds = (current_dt - scheduled_dt).total_seconds()
        if overdue_seconds > self.config.max_overdue_seconds:
            reason = (
                f"Job is overdue by {int(overdue_seconds / 60)} minutes "
                f"(threshold: {int(self.config.max_overdue_seconds / 60)}m). "
                "Flagged for operator review to prevent sending stale messages."
            )
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="overdue_threshold_exceeded",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "requires_review", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "requires_review", "error": reason}

        # Envelope recipient deserialization
        try:
            envelope_recipients = json.loads(job.envelope_recipients_json)
            header_to = json.loads(job.header_to_json)
            header_cc = json.loads(job.header_cc_json) if job.header_cc_json else []
            header_bcc = json.loads(job.header_bcc_json) if job.header_bcc_json else []
        except Exception as e:
            reason = f"Corrupt recipient JSON: {e}"
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="json_decode_error",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "failed", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "failed", "error": reason}

        # Pre-send compliance & suppression re-check
        try:
            self.safeguards.validate_sender(job.account_id, job.sender_email)
            self.safeguards.check_suppression(envelope_recipients)
        except SafeguardViolationError as sve:
            reason = f"Pre-send safeguard rejection: {sve}"
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="safeguard_violation",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "requires_review", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "requires_review", "error": reason}

        # Build MIME message from frozen queue job
        try:
            email_msg, verified_envelope = build_mime_message(
                sender_email=job.sender_email,
                to_addresses=header_to,
                subject=job.subject,
                body_plain_text=job.body_plain_text,
                body_html_sanitized=job.body_html_sanitized,
                cc_addresses=header_cc,
                bcc_addresses=header_bcc,
                rfc_message_id=job.rfc_message_id,
                account_id=job.account_id,
            )
        except Exception as e:
            reason = f"MIME generator error: {e}"
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="mime_build_error",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "failed", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "failed", "error": reason}

        # Lookup account credential reference
        acc_rec = self.repo.get_account(job.account_id)
        if not acc_rec or not acc_rec.credential_ref:
            reason = f"Sender account '{job.account_id}' has no configured credential_ref."
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type="missing_credential_ref",
                error_details=reason,
                started_at_utc=t_start,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.queue_store.record_attempt(attempt, "requires_review", review_reason=reason)
            return {"job_id": job.job_id, "final_status": "requires_review", "error": reason}

        # Execute SMTP submission
        raw_submitted_bytes = email_msg.as_bytes()
        import hashlib
        submitted_sha256 = hashlib.sha256(raw_submitted_bytes).hexdigest()

        smtp_res: SMTPSubmissionResult = self.smtp_adapter.send_message(
            sender_email=job.sender_email,
            credential_ref=acc_rec.credential_ref,
            envelope_recipients=verified_envelope,
            message=email_msg,
            live_send_enabled=is_live_enabled,
            custom_server=custom_server,
        )

        t_complete = datetime.now(timezone.utc).isoformat()

        if smtp_res.success:
            if getattr(smtp_res, "is_simulated", False):
                # Simulated dry run: Record attempt as dry_run and NEVER mark draft as 'sent'
                # or job as 'smtp_accepted'. Keep job in scheduled status without consuming it.
                attempt = OutboundAttemptRecord(
                    job_id=job.job_id,
                    attempt_number=attempt_num,
                    worker_id=self.worker_id,
                    status="dry_run_simulated",
                    smtp_host=self.smtp_adapter.host,
                    smtp_port=self.smtp_adapter.port,
                    smtp_code=smtp_res.status_code,
                    smtp_message=smtp_res.response_message,
                    duration_ms=smtp_res.duration_ms,
                    started_at_utc=t_start,
                    completed_at_utc=t_complete,
                )
                self.queue_store.record_attempt(
                    attempt=attempt,
                    new_job_status="scheduled",
                    smtp_response_code=smtp_res.status_code,
                    smtp_response_message=smtp_res.response_message,
                )
                return {"job_id": job.job_id, "final_status": "dry_run_simulated"}

            # REAL LIVE SMTP ACCEPTED
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="success",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                smtp_code=smtp_res.status_code,
                smtp_message=smtp_res.response_message,
                duration_ms=smtp_res.duration_ms,
                started_at_utc=t_start,
                completed_at_utc=t_complete,
            )
            self.queue_store.record_attempt(
                attempt=attempt,
                new_job_status="smtp_accepted",
                smtp_response_code=smtp_res.status_code,
                smtp_response_message=smtp_res.response_message,
                submitted_mime_bytes=raw_submitted_bytes,
                submitted_mime_sha256=submitted_sha256,
            )
            # Update source draft status to 'sent' without mutating CRM leads
            try:
                self.repo.update_draft_status(job.source_draft_id, "sent", operator_username=job.approved_by)
            except Exception as e:
                logger.warning(f"Could not update source draft status: {e}")

            return {"job_id": job.job_id, "final_status": "smtp_accepted"}

        elif smtp_res.is_unknown_acceptance:
            # AMBIGUOUS TIMEOUT / DISCONNECT AFTER DATA
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="unknown",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                error_type=smtp_res.error_type,
                error_details=smtp_res.error_details,
                duration_ms=smtp_res.duration_ms,
                started_at_utc=t_start,
                completed_at_utc=t_complete,
            )
            self.queue_store.record_attempt(
                attempt=attempt,
                new_job_status="acceptance_unknown",
                review_reason=f"Ambiguous network timeout: {smtp_res.error_details}. Manual verification required before resending.",
            )
            return {"job_id": job.job_id, "final_status": "acceptance_unknown", "error": smtp_res.error_details}

        elif smtp_res.is_retryable and attempt_num < job.max_attempts:
            # RETRYABLE TEMPORARY FAILURE (attempt < max)
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_retryable",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                smtp_code=smtp_res.status_code,
                smtp_message=smtp_res.response_message,
                error_type=smtp_res.error_type,
                error_details=smtp_res.error_details,
                duration_ms=smtp_res.duration_ms,
                started_at_utc=t_start,
                completed_at_utc=t_complete,
            )
            self.queue_store.record_attempt(
                attempt=attempt,
                new_job_status="scheduled",  # Return to queue for next lease
                smtp_response_code=smtp_res.status_code,
                smtp_response_message=smtp_res.error_details,
            )
            return {"job_id": job.job_id, "final_status": "scheduled_retry", "error": smtp_res.error_details}

        else:
            # PERMANENT FAILURE
            attempt = OutboundAttemptRecord(
                job_id=job.job_id,
                attempt_number=attempt_num,
                worker_id=self.worker_id,
                status="failed_permanent",
                smtp_host=self.smtp_adapter.host,
                smtp_port=self.smtp_adapter.port,
                smtp_code=smtp_res.status_code,
                smtp_message=smtp_res.response_message,
                error_type=smtp_res.error_type,
                error_details=smtp_res.error_details,
                duration_ms=smtp_res.duration_ms,
                started_at_utc=t_start,
                completed_at_utc=t_complete,
            )
            self.queue_store.record_attempt(
                attempt=attempt,
                new_job_status="failed",
                smtp_response_code=smtp_res.status_code,
                smtp_response_message=smtp_res.error_details,
                review_reason=smtp_res.error_details,
            )
            return {"job_id": job.job_id, "final_status": "failed", "error": smtp_res.error_details}
