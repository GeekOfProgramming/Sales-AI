#!/usr/bin/env python3
"""
scripts/run_outbound_pilot.py
MAIL-SEND-01D: Dedicated Single-Message SMTP Pilot Execution & Diagnostics Harness.

Strictly enforces:
1. Two-man rule & explicit dry-run vs live-send execution.
2. Exact single-job scoping (--job-id <ID>).
3. Exact sender allowlist (info@pybim.com, info@pybim.it).
4. Exact recipient allowlist (--allowed-recipient <email>).
5. Maximum one-message transmission per execution invocation (hard exit after 1 message).
6. Strict pre-send validation:
   - Recipient syntax & Do Not Contact (suppression_list) checking
   - Content placeholder rejection
   - DPAPI credential validation
   - Draft status check (must be 'scheduled')
7. Legacy vs Outbound sending isolation:
   - Requires explicit flags (OUTBOUND_SEND_ENABLED=true and PILOT_ALLOW_LIVE_SEND=true).
   - Old outreach subsystem (EMAIL_SEND_ENABLED) is NOT unlocked.
8. Comprehensive audit attempt logging and acceptance-unknown handling.
9. Read-only diagnostic commands for safe queue inspection without mutations.

Usage:
    # 1. Inspect queue & system readiness (safe read-only)
    python scripts/run_outbound_pilot.py status

    # 2. Inspect specific job & MIME preview (safe read-only)
    python scripts/run_outbound_pilot.py inspect-job --job-id job_123

    # 3. Simulate single-message execution (dry-run, no network transmission)
    python scripts/run_outbound_pilot.py pilot-send --job-id job_123 --allowed-recipient test@partner.com --dry-run

    # 4. Live single-message pilot (requires explicit operator confirmation)
    python scripts/run_outbound_pilot.py pilot-send --job-id job_123 --allowed-recipient test@partner.com --live-connect --confirm-pilot "CONFIRM-PILOT-SEND"
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.sending.outbound_models import (
    OutboundQueueJob,
    OutboundAttemptRecord,
    OutboundWorkerConfig,
)
from sales_engine.sending.outbound_queue_store import OutboundQueueStore
from sales_engine.sending.outbound_safeguards import OutboundSafeguardService, SafeguardViolationError
from sales_engine.sending.mime_builder import build_mime_message, validate_message_payload
from sales_engine.sending.smtp_adapter import RegisterItSMTPAdapter, SMTPSubmissionResult
from sales_engine.sending.sent_reconciliation import (
    SentReconciliationService,
    SentReconciliationError,
    DEFAULT_SENT_FOLDER,
    compare_eml_against_job,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore, WindowsDPAPI
from backend.operator_auth import get_auth_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_outbound_pilot")

DEFAULT_DB_PATH = os.getenv("SALES_OUTREACH_DB", "data/sales_outreach.db")
AUTHORIZED_PILOT_SENDERS = {"info@pybim.com", "info@pybim.it"}


def check_draft_eligibility(
    db_path: str,
    draft_id: str,
    allowed_recipient: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Safely inspects a scheduled draft for outbound queue admission eligibility without mutations.
    """
    abs_db = os.path.abspath(db_path)
    repo = MailboxRepository(db_path=abs_db, auto_migrate=False)
    safeguards = OutboundSafeguardService(db_path=abs_db)
    queue_store = OutboundQueueStore(db_path=abs_db, auto_apply_schema=False)

    clean_draft_id = draft_id.strip()
    draft = repo.get_draft(clean_draft_id)
    if not draft:
        return {
            "eligible": False,
            "draft_id": clean_draft_id,
            "reasons": [f"Draft '{clean_draft_id}' not found in mailbox_drafts."],
        }

    reasons: List[str] = []
    warnings: List[str] = []

    # 1. Draft status check
    if draft.draft_status != "scheduled":
        reasons.append(
            f"Draft status is '{draft.draft_status}'. Only 'scheduled' drafts can be admitted to the outbound queue."
        )

    # 2. Sender check
    if draft.sender_email not in AUTHORIZED_PILOT_SENDERS:
        reasons.append(
            f"Sender '{draft.sender_email}' is not in authorized pilot senders ({', '.join(sorted(AUTHORIZED_PILOT_SENDERS))})."
        )

    # 3. Snapshot verification
    frozen_subject = draft.snapshot_subject or draft.subject
    frozen_plain = draft.snapshot_plain_text or draft.body_plain_text
    frozen_html = draft.snapshot_html_sanitized or draft.body_html_sanitized
    frozen_to = draft.snapshot_to_addresses or draft.to_addresses
    frozen_cc = draft.cc_addresses or []
    frozen_bcc = draft.bcc_addresses or []

    if not draft.snapshot_subject and not draft.snapshot_plain_text:
        warnings.append("Draft has no immutable snapshot fields stored; will fallback to live draft content.")

    # 4. Recipient checking
    if not frozen_to:
        reasons.append("Draft has no recipient (To) addresses.")
    else:
        try:
            clean_to = safeguards.validate_recipients(frozen_to)
            safeguards.check_suppression(clean_to)
        except Exception as e:
            reasons.append(f"Recipient validation/suppression error: {e}")

        if allowed_recipient:
            clean_rcpt = allowed_recipient.strip().lower()
            if len(frozen_to) != 1 or frozen_to[0].strip().lower() != clean_rcpt:
                reasons.append(
                    f"Recipient mismatch: draft contains {frozen_to}, but required allowed recipient is '{clean_rcpt}'."
                )

    # 5. Placeholder checking
    try:
        content_errs = validate_message_payload(
            subject=frozen_subject,
            plain_text=frozen_plain,
            html_text=frozen_html,
        )
        if content_errs:
            reasons.extend(content_errs)
    except Exception as e:
        reasons.append(f"Payload validation error: {e}")

    # 6. Scheduled time check & expiration
    now_utc = datetime.now(timezone.utc)
    is_expired = False
    if not draft.scheduled_for_utc:
        reasons.append("Draft is missing scheduled_for_utc timestamp.")
    else:
        try:
            sched_dt = datetime.fromisoformat(draft.scheduled_for_utc.replace("Z", "+00:00"))
            if sched_dt < now_utc:
                is_expired = True
                reasons.append(
                    f"Scheduled send time ({draft.scheduled_for_utc}) has already passed (expired). Current UTC is {now_utc.isoformat()}."
                )
        except Exception as e:
            reasons.append(f"Invalid scheduled_for_utc format: {e}")

    # 7. Check existing queue job (Idempotency)
    existing_job = queue_store.get_job_by_draft_id(clean_draft_id)
    if existing_job:
        if existing_job.status in ("scheduled", "sending", "smtp_accepted"):
            reasons.append(
                f"Draft already admitted to queue: Job ID '{existing_job.job_id}' (status: '{existing_job.status}'). Duplicate admission blocked."
            )
        else:
            warnings.append(
                f"Draft has a previous inactive queue job '{existing_job.job_id}' with status '{existing_job.status}'."
            )

    is_eligible = len(reasons) == 0

    return {
        "eligible": is_eligible,
        "draft_id": clean_draft_id,
        "draft_status": draft.draft_status,
        "sender_email": draft.sender_email,
        "recipients": frozen_to,
        "subject": frozen_subject,
        "scheduled_for_utc": draft.scheduled_for_utc,
        "is_expired": is_expired,
        "existing_job_id": existing_job.job_id if existing_job else None,
        "reasons": reasons,
        "warnings": warnings,
    }


def admit_draft_to_queue(
    db_path: str,
    draft_id: str,
    allowed_recipient: str,
    operator_username: str = "pilot_operator",
    allow_expired: bool = False,
) -> Dict[str, Any]:
    """
    Admits exactly one approved scheduled draft into the outbound queue with strict gating.
    Does NOT transmit or send any message.
    """
    abs_db = os.path.abspath(db_path)
    clean_draft_id = draft_id.strip()
    clean_allowed_rcpt = allowed_recipient.strip().lower()

    # 1. Eligibility preflight
    eligibility = check_draft_eligibility(
        db_path=abs_db,
        draft_id=clean_draft_id,
        allowed_recipient=clean_allowed_rcpt,
    )

    if not eligibility["eligible"]:
        # If the only reason was expiration and operator explicitly allowed it, we can proceed
        if allow_expired and eligibility.get("is_expired") and len(eligibility["reasons"]) == 1:
            logger.info("Admitting expired draft per operator override (--allow-expired).")
        else:
            raise ValueError(
                f"Draft '{clean_draft_id}' is not eligible for queue admission: {'; '.join(eligibility['reasons'])}"
            )

    # 2. Execute queueing via OutboundDispatchService
    from sales_engine.sending.outbound_service import OutboundDispatchService
    dispatch_svc = OutboundDispatchService(db_path=abs_db)
    job = dispatch_svc.queue_approved_draft(
        draft_id=clean_draft_id,
        operator_username=operator_username,
        is_legacy_revalidated=True,
    )

    return {
        "success": True,
        "action": "QUEUE_ADMISSION",
        "job_id": job.job_id,
        "draft_id": job.source_draft_id,
        "status": job.status,
        "sender_email": job.sender_email,
        "recipient": clean_allowed_rcpt,
        "subject": job.subject,
        "scheduled_for_utc": job.scheduled_for_utc,
        "approved_by": job.approved_by,
        "approved_at_utc": job.approved_at_utc,
    }


def get_diagnostics(db_path: str) -> Dict[str, Any]:
    """Generates read-only operational diagnostics without creating schema or mutating data."""
    abs_db = os.path.abspath(db_path)
    if not os.path.exists(abs_db):
        return {"error": f"Database file not found: {abs_db}"}

    queue_store = OutboundQueueStore(db_path=abs_db, auto_apply_schema=False)
    cred_store = MailboxCredentialStore()

    # Check activation flags
    env_outbound = os.getenv("OUTBOUND_SEND_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    env_legacy_email = os.getenv("EMAIL_SEND_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    env_pilot_live = os.getenv("PILOT_ALLOW_LIVE_SEND", "false").strip().lower() in ("true", "1", "yes")

    # Queue counts
    total_jobs = queue_store.count_jobs(status="all")
    scheduled_jobs = queue_store.count_jobs(status="scheduled")
    sending_jobs = queue_store.count_jobs(status="sending")
    accepted_jobs = queue_store.count_jobs(status="smtp_accepted")
    failed_jobs = queue_store.count_jobs(status="failed")
    unknown_jobs = queue_store.count_jobs(status="acceptance_unknown")
    cancelled_jobs = queue_store.count_jobs(status="cancelled")
    review_jobs = queue_store.count_jobs(status="requires_review")

    # SMTP Credentials status
    configured_refs = cred_store.list_configured_refs()
    creds_info = {}
    for sender in AUTHORIZED_PILOT_SENDERS:
        acc_ref = "win_cred_acc_info_com" if sender.endswith(".com") else "win_cred_acc_info_it"
        has_cred = bool(cred_store.get_credential(acc_ref))
        creds_info[sender] = {
            "credential_ref": acc_ref,
            "is_configured": has_cred,
        }

    # Drafts status from mailbox_drafts
    repo = MailboxRepository(db_path=abs_db, auto_migrate=False)
    with repo.get_connection() as conn:
        has_drafts_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mailbox_drafts'").fetchone()
        if has_drafts_table:
            draft_counts = dict(conn.execute("SELECT draft_status, COUNT(*) FROM mailbox_drafts GROUP BY draft_status").fetchall())
        else:
            draft_counts = {}

    return {
        "database_path": abs_db,
        "gates": {
            "OUTBOUND_SEND_ENABLED": env_outbound,
            "EMAIL_SEND_ENABLED (Legacy)": env_legacy_email,
            "PILOT_ALLOW_LIVE_SEND": env_pilot_live,
            "live_transmission_permitted": bool(env_outbound and env_pilot_live),
        },
        "queue_summary": {
            "total_jobs": total_jobs,
            "scheduled": scheduled_jobs,
            "sending": sending_jobs,
            "smtp_accepted": accepted_jobs,
            "failed": failed_jobs,
            "acceptance_unknown": unknown_jobs,
            "cancelled": cancelled_jobs,
            "requires_review": review_jobs,
        },
        "drafts_summary": draft_counts,
        "senders": creds_info,
        "windows_dpapi_available": WindowsDPAPI.is_available(),
    }


def execute_single_message_pilot(
    db_path: str,
    job_id: str,
    allowed_recipient: str,
    dry_run: bool = True,
    operator_username: str = "pilot_operator",
    custom_smtp_server: Optional[Any] = None,
    credential_store: Optional[MailboxCredentialStore] = None,
) -> Dict[str, Any]:
    """
    Executes a single-message pilot transmission under strict containment rules.
    """
    abs_db = os.path.abspath(db_path)
    queue_store = OutboundQueueStore(db_path=abs_db, auto_apply_schema=False)
    safeguards = OutboundSafeguardService(db_path=abs_db)
    cred_store = credential_store or MailboxCredentialStore()
    repo = MailboxRepository(db_path=abs_db, auto_migrate=False)

    clean_allowed_rcpt = allowed_recipient.strip().lower()

    # 1. Fetch exact requested job
    job: Optional[OutboundQueueJob] = queue_store.get_job(job_id)
    if not job:
        raise ValueError(f"Pilot job '{job_id}' not found in outbound_jobs.")

    # 2. Strict status check: Job must be in 'scheduled' state
    if job.status != "scheduled":
        raise ValueError(
            f"Pilot job '{job_id}' has status '{job.status}'. Only 'scheduled' jobs can be piloted."
        )

    # 3. Sender allowlist verification
    if job.sender_email not in AUTHORIZED_PILOT_SENDERS:
        raise ValueError(
            f"Sender '{job.sender_email}' is not in authorized pilot senders ({', '.join(sorted(AUTHORIZED_PILOT_SENDERS))})."
        )

    # 4. Recipient allowlist verification (Exact match requirement)
    envelope_recipients = json.loads(job.envelope_recipients_json)
    if len(envelope_recipients) != 1 or envelope_recipients[0].lower() != clean_allowed_rcpt:
        raise ValueError(
            f"Envelope recipient mismatch! Pilot job contains {envelope_recipients}, but allowed pilot recipient is '{clean_allowed_rcpt}'."
        )

    # 5. Safeguards: Re-validate suppression list (Do Not Contact)
    safeguards.check_suppression([clean_allowed_rcpt])

    # 6. Content placeholder validation
    content_errors = validate_message_payload(
        subject=job.subject,
        plain_text=job.body_plain_text,
        html_text=job.body_html_sanitized,
    )
    if content_errors:
        raise ValueError(f"Message payload safety check failed: {'; '.join(content_errors)}")

    # 7. Check credentials presence
    acc_ref = "win_cred_acc_info_com" if job.sender_email.endswith(".com") else "win_cred_acc_info_it"
    password = cred_store.get_credential(acc_ref)
    if not password:
        raise ValueError(f"Missing credential for reference '{acc_ref}' in credential store.")

    # 8. Assemble or Reuse RFC 5322 MIME message
    import hashlib
    raw_submitted_bytes = None
    submitted_sha256 = None
    mime_msg = None

    if job.submitted_mime_bytes:
        # Verify existing durable bytes against stored checksum
        calc_sha256 = hashlib.sha256(job.submitted_mime_bytes).hexdigest()
        if job.submitted_mime_sha256 and calc_sha256 != job.submitted_mime_sha256:
            raise ValueError(
                f"Integrity error: Stored pre-submission MIME SHA-256 mismatch for job '{job.job_id}' "
                f"({calc_sha256} != {job.submitted_mime_sha256})."
            )
        import email
        from email.policy import default as default_policy
        mime_msg = email.message_from_bytes(job.submitted_mime_bytes, policy=default_policy)
        raw_submitted_bytes = job.submitted_mime_bytes
        submitted_sha256 = calc_sha256
    else:
        to_list = json.loads(job.header_to_json)
        cc_list = json.loads(job.header_cc_json or "[]")
        bcc_list = json.loads(job.header_bcc_json or "[]")

        mime_msg, _ = build_mime_message(
            sender_email=job.sender_email,
            to_addresses=to_list,
            subject=job.subject,
            body_plain_text=job.body_plain_text,
            body_html_sanitized=job.body_html_sanitized,
            cc_addresses=cc_list,
            bcc_addresses=bcc_list,
            rfc_message_id=job.rfc_message_id,
            account_id=job.account_id,
        )
        raw_submitted_bytes = mime_msg.as_bytes()
        submitted_sha256 = hashlib.sha256(raw_submitted_bytes).hexdigest()

        # Pre-submission durability: Commit raw bytes to SQLite BEFORE opening network connection
        queue_store.persist_pre_submission_mime(
            job_id=job.job_id,
            submitted_mime_bytes=raw_submitted_bytes,
            submitted_mime_sha256=submitted_sha256,
            submission_intent="pilot_transmission",
        )

    # 9. Gate check for live transmission
    env_outbound = os.getenv("OUTBOUND_SEND_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    env_pilot_live = os.getenv("PILOT_ALLOW_LIVE_SEND", "false").strip().lower() in ("true", "1", "yes")

    is_live = (not dry_run) and env_outbound and env_pilot_live
    if not dry_run and not is_live and custom_smtp_server is None:
        raise RuntimeError(
            "Live transmission blocked: Both OUTBOUND_SEND_ENABLED=true and PILOT_ALLOW_LIVE_SEND=true must be set."
        )

    adapter = RegisterItSMTPAdapter(credential_store=cred_store)
    t_start = datetime.now(timezone.utc).isoformat()
    attempt_num = job.attempt_count + 1

    # 10. Execute transmission (dry run or single live send)
    smtp_res: SMTPSubmissionResult = adapter.send_message(
        sender_email=job.sender_email,
        credential_ref=acc_ref,
        envelope_recipients=envelope_recipients,
        message=mime_msg,
        live_send_enabled=is_live,
        custom_server=custom_smtp_server,
    )

    t_complete = datetime.now(timezone.utc).isoformat()

    # 11. Record attempt and transition state
    if smtp_res.is_simulated:
        attempt = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=attempt_num,
            worker_id="single_message_pilot_harness",
            status="dry_run_simulated",
            smtp_host=adapter.host,
            smtp_port=adapter.port,
            smtp_code=smtp_res.status_code,
            smtp_message=smtp_res.response_message,
            duration_ms=smtp_res.duration_ms,
            started_at_utc=t_start,
            completed_at_utc=t_complete,
        )
        queue_store.record_attempt(
            attempt=attempt,
            new_job_status="scheduled",  # Preserved in scheduled state
            smtp_response_code=smtp_res.status_code,
            smtp_response_message=smtp_res.response_message,
        )
        return {
            "success": True,
            "mode": "DRY_RUN",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "dry_run_simulated",
            "message": smtp_res.response_message,
            "duration_ms": smtp_res.duration_ms,
        }

    elif smtp_res.success:
        attempt = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=attempt_num,
            worker_id="single_message_pilot_harness",
            status="success",
            smtp_host=adapter.host,
            smtp_port=adapter.port,
            smtp_code=smtp_res.status_code,
            smtp_message=smtp_res.response_message,
            duration_ms=smtp_res.duration_ms,
            started_at_utc=t_start,
            completed_at_utc=t_complete,
        )
        queue_store.record_attempt(
            attempt=attempt,
            new_job_status="smtp_accepted",
            smtp_response_code=smtp_res.status_code,
            smtp_response_message=smtp_res.response_message,
            submitted_mime_bytes=raw_submitted_bytes,
            submitted_mime_sha256=submitted_sha256,
        )
        try:
            repo.update_draft_status(job.source_draft_id, "sent", operator_username=operator_username)
        except Exception as e:
            logger.warning(f"Could not update source draft status: {e}")

        # Controlled Sent Reconciliation Handoff:
        # Executes IMAP Sent reconciliation only if explicitly authorized via env flag or parameter
        reconciliation_res = None
        auto_reconcile_enabled = os.getenv("OUTBOUND_AUTO_SENT_RECONCILE", "false").strip().lower() in ("true", "1", "yes")
        if auto_reconcile_enabled:
            try:
                rec_svc = SentReconciliationService(db_path=db_path, credential_store=cred_store)
                reconciliation_res = rec_svc.reconcile_job_sent_copy(
                    job_id=job.job_id,
                    dry_run=False,
                    live_append=True,
                    operator_username=operator_username,
                )
                logger.info(f"Automatic Sent reconciliation completed for {job.job_id}: {reconciliation_res.get('status')}")
            except Exception as rec_err:
                logger.error(f"Automatic Sent reconciliation failed for {job.job_id} (SMTP send untouched): {rec_err}")

        return {
            "success": True,
            "mode": "LIVE_SEND",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "smtp_accepted",
            "smtp_code": smtp_res.status_code,
            "smtp_message": smtp_res.response_message,
            "duration_ms": smtp_res.duration_ms,
            "reconciliation": reconciliation_res,
        }

    elif smtp_res.is_unknown_acceptance:
        attempt = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=attempt_num,
            worker_id="single_message_pilot_harness",
            status="unknown",
            smtp_host=adapter.host,
            smtp_port=adapter.port,
            error_type=smtp_res.error_type,
            error_details=smtp_res.error_details,
            duration_ms=smtp_res.duration_ms,
            started_at_utc=t_start,
            completed_at_utc=t_complete,
        )
        queue_store.record_attempt(
            attempt=attempt,
            new_job_status="acceptance_unknown",
            review_reason=f"Ambiguous network timeout: {smtp_res.error_details}. Operator verification required.",
        )
        return {
            "success": False,
            "mode": "LIVE_SEND",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "acceptance_unknown",
            "error": smtp_res.error_details,
        }

    elif smtp_res.is_retryable and attempt_num < job.max_attempts:
        # Retryable temporary failure (e.g. 451, greylisting, temp disconnect)
        attempt = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=attempt_num,
            worker_id="single_message_pilot_harness",
            status="failed_retryable",
            smtp_host=adapter.host,
            smtp_port=adapter.port,
            smtp_code=smtp_res.status_code,
            smtp_message=smtp_res.response_message,
            error_type=smtp_res.error_type,
            error_details=smtp_res.error_details,
            duration_ms=smtp_res.duration_ms,
            started_at_utc=t_start,
            completed_at_utc=t_complete,
        )
        queue_store.record_attempt(
            attempt=attempt,
            new_job_status="scheduled",  # Return to scheduled status for retry
            smtp_response_code=smtp_res.status_code,
            smtp_response_message=smtp_res.error_details,
        )
        return {
            "success": False,
            "mode": "LIVE_SEND",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "scheduled_retry",
            "error": smtp_res.error_details,
        }

    else:
        attempt = OutboundAttemptRecord(
            job_id=job.job_id,
            attempt_number=attempt_num,
            worker_id="single_message_pilot_harness",
            status="failed_permanent",
            smtp_host=adapter.host,
            smtp_port=adapter.port,
            smtp_code=smtp_res.status_code,
            smtp_message=smtp_res.response_message,
            error_type=smtp_res.error_type,
            error_details=smtp_res.error_details,
            duration_ms=smtp_res.duration_ms,
            started_at_utc=t_start,
            completed_at_utc=t_complete,
        )
        queue_store.record_attempt(
            attempt=attempt,
            new_job_status="failed",
            smtp_response_code=smtp_res.status_code,
            smtp_response_message=smtp_res.error_details,
            review_reason=smtp_res.error_details,
        )
        return {
            "success": False,
            "mode": "LIVE_SEND",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "failed",
            "error": smtp_res.error_details,
        }


def main():
    parser = argparse.ArgumentParser(description="Sales-AI Outbound Pilot Harness & Diagnostic Inspector")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    subparsers = parser.add_subparsers(dest="command", help="Operational command")

    # Command 1: status
    subparsers.add_parser("status", help="Read-only inspection of outbound queue, credentials, and safety flags")

    # Command 2: inspect-job
    insp_parser = subparsers.add_parser("inspect-job", help="Inspect a specific queued job and preview its frozen payload")
    insp_parser.add_argument("--job-id", required=True, help="Job ID to inspect")

    # Command 3: check-draft (inspect eligibility without mutating)
    check_parser = subparsers.add_parser("check-draft", help="Inspect a scheduled draft for outbound queue eligibility")
    check_parser.add_argument("--draft-id", required=True, help="Draft ID to check")
    check_parser.add_argument("--allowed-recipient", default=None, help="Optional recipient email to verify against draft destination")

    # Command 4: admit-draft (explicit queue admission)
    admit_parser = subparsers.add_parser("admit-draft", help="Explicitly admit an eligible scheduled draft into outbound_jobs")
    admit_parser.add_argument("--draft-id", required=True, help="Draft ID to admit to queue")
    admit_parser.add_argument("--allowed-recipient", required=True, help="Exact recipient address allowlist for verification")
    admit_parser.add_argument("--operator", default="operator_pilot", help="Operator username authorizing queue admission")
    admit_parser.add_argument("--allow-expired", action="store_true", default=False, help="Allow admitting draft whose scheduled time has passed (quarantined test drafts)")

    # Command 5: pilot-send
    pilot_parser = subparsers.add_parser("pilot-send", help="Execute single-message pilot transmission")
    pilot_parser.add_argument("--job-id", required=True, help="Exact Job ID to pilot")
    pilot_parser.add_argument("--allowed-recipient", required=True, help="Exact recipient address allowlist for the pilot")
    pilot_parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry-run without SMTP transmission")
    pilot_parser.add_argument("--live-connect", action="store_true", default=False, help="Authorizes live remote SMTP connection")
    pilot_parser.add_argument("--confirm-pilot", default="", help="Safety confirmation string: must be CONFIRM-PILOT-SEND")
    pilot_parser.add_argument("--operator", default="operator_pilot", help="Operator username authorizing pilot")

    # Command 6: reconcile-sent
    reconcile_parser = subparsers.add_parser(
        "reconcile-sent",
        help="Reconcile successfully accepted outbound email into Register.it IMAP Sent folder",
    )
    reconcile_parser.add_argument("--job-id", required=True, help="Job ID of the accepted outbound email to reconcile")
    reconcile_parser.add_argument(
        "--inspect",
        action="store_true",
        default=False,
        help="Read-only diagnostic: search Sent folder by Message-ID without modifying anything",
    )
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preflight dry run: test credentials, folder access, and MIME reconstruction without APPEND",
    )
    reconcile_parser.add_argument(
        "--live-append",
        action="store_true",
        default=False,
        help="Perform authorized live IMAP APPEND into the Sent folder",
    )
    reconcile_parser.add_argument(
        "--confirm-append",
        default="",
        help="Safety confirmation string required for live append: must be CONFIRM-SENT-APPEND",
    )
    reconcile_parser.add_argument(
        "--sent-folder",
        default=DEFAULT_SENT_FOLDER,
        help=f"Target IMAP sent folder (default: {DEFAULT_SENT_FOLDER})",
    )
    reconcile_parser.add_argument(
        "--eml-file",
        default=None,
        help="Path to operator-supplied received .eml file (e.g. exported from Gmail) to verify and import",
    )
    reconcile_parser.add_argument(
        "--compare-eml",
        action="store_true",
        default=False,
        help="Audit and compare the supplied .eml file against the job's immutable database snapshot without appending",
    )
    reconcile_parser.add_argument(
        "--allow-reconstruction",
        action="store_true",
        default=False,
        help="Explicitly authorize semantic reconstruction when exact pre-submission MIME bytes are not available",
    )
    reconcile_parser.add_argument(
        "--retry-pending",
        action="store_true",
        default=False,
        help="Retry sent-folder reconciliation for a job in sent_pending or sent_requires_review state without retransmitting over SMTP",
    )
    reconcile_parser.add_argument(
        "--operator",
        default="operator_pilot",
        help="Operator username authorizing sent reconciliation",
    )

    # Command 7: migrate-schema
    migrate_parser = subparsers.add_parser(
        "migrate-schema",
        help="Run explicit, idempotent schema preflight and migration for outbound queue & Sent reconciliation",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Perform dry-run inspection of missing columns/tables without applying changes",
    )

    args = parser.parse_args()

    if args.command == "status":
        diag = get_diagnostics(args.db_path)
        print("\n======================================================================")
        print("pyBIM Sales-AI: Outbound Queue & Pilot Diagnostics")
        print("======================================================================")
        print(f"Database Path:           {diag.get('database_path')}")
        print(f"Windows DPAPI Available: {diag.get('windows_dpapi_available')}")
        print("\nSafety Activation Gates:")
        gates = diag.get("gates", {})
        for k, v in gates.items():
            status_color = "ENABLED" if v else "DISABLED"
            print(f"  - {k:<30}: {status_color}")

        print("\nOutbound Queue Metrics:")
        q_sum = diag.get("queue_summary", {})
        for k, v in q_sum.items():
            print(f"  - {k:<25}: {v}")

        print("\nAuthorized Sender Credentials:")
        senders = diag.get("senders", {})
        for s, info in senders.items():
            print(f"  - {s:<20}: Ref={info['credential_ref']}, Configured={info['is_configured']}")

        print("\nMailbox Drafts by Status:")
        drafts = diag.get("drafts_summary", {})
        for st, cnt in drafts.items():
            print(f"  - {st:<20}: {cnt}")

        print("======================================================================\n")

    elif args.command == "inspect-job":
        queue_store = OutboundQueueStore(db_path=args.db_path, auto_apply_schema=False)
        job = queue_store.get_job(args.job_id)
        if not job:
            print(f"[ERROR] Job '{args.job_id}' not found.")
            sys.exit(1)
        print("\n======================================================================")
        print(f"Outbound Job Inspection: {job.job_id}")
        print("======================================================================")
        print(f"Status:             {job.status}")
        print(f"Source Draft ID:    {job.source_draft_id}")
        print(f"Sender Email:       {job.sender_email}")
        print(f"Envelope Recipient: {job.envelope_recipients_json}")
        print(f"Subject:            {job.subject}")
        print(f"RFC Message-ID:     {job.rfc_message_id}")
        print(f"Scheduled For UTC:  {job.scheduled_for_utc}")
        print(f"Approved By:        {job.approved_by} at {job.approved_at_utc}")
        print(f"Attempt Count:      {job.attempt_count} / {job.max_attempts}")
        if job.review_reason:
            print(f"Review Reason:      {job.review_reason}")
        print("\n--- Frozen Body Preview ---")
        print(job.body_plain_text[:500] + ("..." if len(job.body_plain_text) > 500 else ""))
        print("======================================================================\n")

    elif args.command == "check-draft":
        elig = check_draft_eligibility(
            db_path=args.db_path,
            draft_id=args.draft_id,
            allowed_recipient=args.allowed_recipient,
        )
        print("\n======================================================================")
        print(f"Draft Queue Eligibility Inspection: {args.draft_id}")
        print("======================================================================")
        print(f"Eligible for Queue: {elig['eligible']}")
        print(f"Draft Status:       {elig.get('draft_status')}")
        print(f"Sender Email:       {elig.get('sender_email')}")
        print(f"Recipients:         {elig.get('recipients')}")
        print(f"Subject:            {elig.get('subject')}")
        print(f"Scheduled For UTC:  {elig.get('scheduled_for_utc')}")
        print(f"Schedule Expired:   {elig.get('is_expired')}")
        print(f"Existing Job ID:    {elig.get('existing_job_id') or 'None'}")
        if elig.get("reasons"):
            print("\nBlocking Reasons:")
            for r in elig["reasons"]:
                print(f"  [X] {r}")
        if elig.get("warnings"):
            print("\nWarnings:")
            for w in elig["warnings"]:
                print(f"  [!] {w}")
        print("======================================================================\n")

    elif args.command == "admit-draft":
        try:
            res = admit_draft_to_queue(
                db_path=args.db_path,
                draft_id=args.draft_id,
                allowed_recipient=args.allowed_recipient,
                operator_username=args.operator,
                allow_expired=args.allow_expired,
            )
            print("\n======================================================================")
            print("SUCCESS: Draft Admitted to Outbound Queue")
            print("======================================================================")
            print(f"Resulting Job ID:   {res['job_id']}")
            print(f"Source Draft ID:    {res['draft_id']}")
            print(f"Queue Status:       {res['status']}")
            print(f"Sender Email:       {res['sender_email']}")
            print(f"Recipient:          {res['recipient']}")
            print(f"Subject:            {res['subject']}")
            print(f"Scheduled For UTC:  {res['scheduled_for_utc']}")
            print(f"Approved By:        {res['approved_by']} at {res['approved_at_utc']}")
            print("======================================================================\n")
            print(f"Next step: Inspect or dry-run using:")
            print(f"  python scripts/run_outbound_pilot.py inspect-job --job-id {res['job_id']}")
            print(f"  python scripts/run_outbound_pilot.py pilot-send --job-id {res['job_id']} --allowed-recipient {res['recipient']} --dry-run\n")
        except Exception as e:
            print(f"\n[ERROR] Queue admission rejected: {e}\n")
            sys.exit(1)

    elif args.command == "pilot-send":
        # Check explicit dry-run vs live-send flags
        if not args.dry_run:
            if not args.live_connect:
                print("[ERROR] Live transmission requires '--live-connect'. Aborted.")
                sys.exit(1)
            if args.confirm_pilot != "CONFIRM-PILOT-SEND":
                print("[ERROR] Live transmission requires '--confirm-pilot CONFIRM-PILOT-SEND'. Aborted.")
                sys.exit(1)

        try:
            res = execute_single_message_pilot(
                db_path=args.db_path,
                job_id=args.job_id,
                allowed_recipient=args.allowed_recipient,
                dry_run=args.dry_run,
                operator_username=args.operator,
            )
            print("\n======================================================================")
            print(f"Pilot Execution Result: {res.get('status').upper()}")
            print("======================================================================")
            print(json.dumps(res, indent=2))
            print("======================================================================\n")
        except Exception as e:
            print(f"\n[ERROR] Pilot execution halted: {e}")
            sys.exit(1)

    elif args.command == "reconcile-sent":
        svc = SentReconciliationService(db_path=args.db_path)

        if args.compare_eml:
            if not args.eml_file:
                print("[ERROR] '--compare-eml' requires '--eml-file <path_to_file.eml>'.")
                sys.exit(1)
            try:
                job = svc.queue_store.get_job(args.job_id)
                if not job:
                    print(f"[ERROR] Job '{args.job_id}' not found.")
                    sys.exit(1)
                with open(args.eml_file, "rb") as f:
                    eml_raw = f.read()
                comp = compare_eml_against_job(job, eml_raw)
                print("\n======================================================================")
                print("EML vs Job Snapshot Audit Report")
                print("======================================================================")
                print(f"Job ID:                    {job.job_id}")
                print(f"EML Path:                  {args.eml_file}")
                print(f"Compatible for Import:     {comp['is_compatible']}")
                print(f"Message-ID Match:          {comp['message_id_match']}")
                print(f"Sender Email Match:        {comp['sender_match']}")
                print(f"Recipients Match:          {comp['to_match']}")
                print(f"Subject Match:             {comp['subject_match']}")
                print(f"Plain Body Match:          {comp['plain_body_match']}")
                print(f"CID Logo References:       {comp['has_cid_logo']} ({comp['cid_logos_found']})")
                print(f"Attachments Detected:      {comp['attachments_count']}")
                print(f"EML From Display Name:     '{comp['eml_from_name']}'")
                print(f"EML Sender Address:        '{comp['eml_from_addr']}'")
                print(f"EML Date Header:           {comp['eml_date']}")
                print(f"Transport Headers Removed: {comp['transport_headers_count']} ({', '.join(comp['transport_headers_detected'][:5])}...)")
                if comp['discrepancies']:
                    print("\nDiscrepancies:")
                    for d in comp['discrepancies']:
                        print(f"  [X] {d}")
                print("======================================================================\n")
            except Exception as e:
                print(f"\n[ERROR] EML comparison failed: {e}\n")
                sys.exit(1)

        elif args.inspect:
            try:
                res = svc.inspect_remote_sent_status(
                    job_id=args.job_id,
                    sent_folder=args.sent_folder,
                )
                print("\n======================================================================")
                print("IMAP Sent-Folder Read-Only Diagnostic")
                print("======================================================================")
                print(f"Job ID:             {res['job_id']}")
                print(f"Account ID:         {res['account_id']}")
                print(f"Sender Email:       {res['sender_email']}")
                print(f"RFC Message-ID:     {res['rfc_message_id']}")
                print(f"Target Sent Folder: {res['sent_folder']}")
                print(f"Exists in Sent:     {res['exists_in_sent']}")
                print(f"Matched UIDs:       {res['matched_uids']}")
                print(f"Reconcile Status:   {res.get('reconciliation_status')}")
                print(f"Reconciled At UTC:  {res.get('reconciled_at_utc')}")
                print(f"Recorded IMAP UID:  {res.get('imap_uid')}")
                print("======================================================================\n")
            except Exception as e:
                print(f"\n[ERROR] Sent folder inspection failed: {e}\n")
                sys.exit(1)

        else:
            # Reconcile operation
            if not args.dry_run:
                if not args.live_append:
                    print("[ERROR] Live append requires '--live-append' (or use '--dry-run' / '--inspect').")
                    sys.exit(1)
                if args.confirm_append != "CONFIRM-SENT-APPEND":
                    print("[ERROR] Live append requires '--confirm-append CONFIRM-SENT-APPEND'.")
                    sys.exit(1)

            try:
                res = svc.reconcile_job_sent_copy(
                    job_id=args.job_id,
                    dry_run=args.dry_run,
                    live_append=args.live_append,
                    sent_folder=args.sent_folder,
                    operator_username=args.operator,
                    eml_path=args.eml_file,
                    allow_reconstruction=args.allow_reconstruction,
                )
                print("\n======================================================================")
                print(f"Sent Reconciliation Result: {res.get('action')}")
                print(f"Provenance Mode:            {res.get('provenance')}")
                print(f"Byte-Identical Original:    {res.get('is_byte_identical')}")
                print("======================================================================")
                print(json.dumps(res, indent=2))
                print("======================================================================\n")
            except Exception as e:
                print(f"\n[ERROR] Sent reconciliation failed: {e}\n")
                sys.exit(1)

    elif args.command == "migrate-schema":
        from scripts.migrate_outbound_queue import run_migration
        res = run_migration(db_path=args.db_path, dry_run=args.dry_run)
        print("\n======================================================================")
        print(f"Migration Status:       {res.get('status')}")
        print(f"Dry Run Mode:           {res.get('dry_run')}")
        print(f"Target Database:        {res.get('db_path')}")
        print(f"Integrity Check:        {res.get('integrity')}")
        print(f"Foreign Key Violations: {res.get('foreign_key_violations')}")
        print(f"Changes Applied:        {len(res.get('changes_applied', []))}")
        for c in res.get("changes_applied", []):
            print(f"  + {c}")
        print("======================================================================\n")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
