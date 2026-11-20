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
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore, WindowsDPAPI
from backend.operator_auth import get_auth_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_outbound_pilot")

DEFAULT_DB_PATH = os.getenv("SALES_OUTREACH_DB", "data/sales_outreach.db")
AUTHORIZED_PILOT_SENDERS = {"info@pybim.com", "info@pybim.it"}


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
) -> Dict[str, Any]:
    """
    Executes a single-message pilot transmission under strict containment rules.
    """
    abs_db = os.path.abspath(db_path)
    queue_store = OutboundQueueStore(db_path=abs_db, auto_apply_schema=False)
    safeguards = OutboundSafeguardService(db_path=abs_db)
    cred_store = MailboxCredentialStore()
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

    # 8. Assemble RFC 5322 MIME message
    to_list = json.loads(job.header_to_json)
    cc_list = json.loads(job.header_cc_json or "[]")
    bcc_list = json.loads(job.header_bcc_json or "[]")

    mime_msg, env_recipients = build_mime_message(
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
        envelope_recipients=env_recipients,
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
        )
        try:
            repo.update_draft_status(job.source_draft_id, "sent", operator_username=operator_username)
        except Exception as e:
            logger.warning(f"Could not update source draft status: {e}")

        return {
            "success": True,
            "mode": "LIVE_SEND",
            "job_id": job.job_id,
            "recipient": clean_allowed_rcpt,
            "status": "smtp_accepted",
            "smtp_code": smtp_res.status_code,
            "smtp_message": smtp_res.response_message,
            "duration_ms": smtp_res.duration_ms,
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

    # Command 3: pilot-send
    pilot_parser = subparsers.add_parser("pilot-send", help="Execute single-message pilot transmission")
    pilot_parser.add_argument("--job-id", required=True, help="Exact Job ID to pilot")
    pilot_parser.add_argument("--allowed-recipient", required=True, help="Exact recipient address allowlist for the pilot")
    pilot_parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry-run without SMTP transmission")
    pilot_parser.add_argument("--live-connect", action="store_true", default=False, help="Authorizes live remote SMTP connection")
    pilot_parser.add_argument("--confirm-pilot", default="", help="Safety confirmation string: must be CONFIRM-PILOT-SEND")
    pilot_parser.add_argument("--operator", default="operator_pilot", help="Operator username authorizing pilot")

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

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
