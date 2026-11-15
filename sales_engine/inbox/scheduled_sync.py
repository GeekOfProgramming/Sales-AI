"""
sales_engine/inbox/scheduled_sync.py
IMAP-AUTO-SYNC-01A: Safe Scheduled Mailbox Synchronization Engine.

Key Architectural Guarantees:
  1. Strict Scope Restriction:
     - ONLY permits the two primary Sales mailboxes:
         * info@pybim.com (acc_info_com)
         * info@pybim.it  (acc_info_it)
     - Secondary mailboxes (careers, support, hamed.sharifi, hamid.lotfalian) are
       strictly forbidden from unattended synchronization and will raise an error if targeted.
  2. Operating Windows & Timezone:
     - Scheduled at 12:15 and 18:00 daily in Europe/Rome timezone.
     - Robust fallback for Europe/Rome timezone resolution across Windows and Linux.
  3. Fail-Closed on UIDVALIDITY Rollover:
     - In unattended scheduled execution, any remote UIDVALIDITY change fails closed immediately.
     - Cursors are never reset automatically without operator intervention.
  4. Single-Instance Concurrency Guard:
     - File-based locking (.mailbox_sync.lock) prevents concurrent runs across processes or tasks.
  5. Bounded Catch-Up:
     - Capped batches (default: 2 batches of 50 = max 100 messages per folder per run).
  6. Strict Zero CRM Mutation:
     - Stages incoming/outgoing messages in mailbox repository tables only.
     - Zero mutations to candidate_companies, candidate_contacts, manual_outreach_log,
       historical_sent_emails, or suppression lists.
  7. DPAPI Store & Windows User Decoupling:
     - Validates DPAPI capability and runs cleanly under the current Windows user without
       embedded credentials or passwords in configuration files.
"""

import os
import sys
import json
import logging
import datetime
from typing import Dict, Any, List, Optional, Tuple

from sales_engine.inbox.models import MailboxAccountRecord
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.sync_service import MailboxSyncOrchestrator
from sales_engine.inbox.credential_store import MailboxCredentialStore

logger = logging.getLogger(__name__)

# Permitted accounts for automated scheduled synchronization
AUTHORIZED_SCHEDULED_ACCOUNTS = {
    "acc_info_com": "info@pybim.com",
    "acc_info_it": "info@pybim.it",
}

# Scheduled daily times (hour, minute) in Europe/Rome
SCHEDULED_RUN_TIMES = [
    (12, 15),
    (18, 0),
]


def get_europe_rome_tz() -> datetime.tzinfo:
    """
    Resolve Europe/Rome timezone robustly across Windows and Linux.
    Tries zoneinfo first, then dateutil.tz, then fallback to fixed offset.
    """
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo("Europe/Rome")
    except Exception:
        pass

    try:
        import dateutil.tz
        tz = dateutil.tz.gettz("Europe/Rome")
        if tz is not None:
            return tz
    except Exception:
        pass

    # Fallback to standard Central European Time (+01:00)
    return datetime.timezone(datetime.timedelta(hours=1), name="Europe/Rome_Fallback")


def calculate_next_scheduled_run(now_utc: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """
    Calculate the next scheduled sync execution window in Europe/Rome and UTC.
    """
    rome_tz = get_europe_rome_tz()
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    now_rome = now_utc.astimezone(rome_tz)
    candidates = []

    # Check today and tomorrow
    for day_offset in range(3):
        target_date = now_rome.date() + datetime.timedelta(days=day_offset)
        for hour, minute in SCHEDULED_RUN_TIMES:
            run_rome = datetime.datetime(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=rome_tz,
            )
            if run_rome > now_rome:
                candidates.append(run_rome)

    next_run_rome = sorted(candidates)[0]
    next_run_utc = next_run_rome.astimezone(datetime.timezone.utc)
    seconds_remaining = (next_run_utc - now_utc).total_seconds()

    return {
        "current_time_rome": now_rome.isoformat(),
        "current_time_utc": now_utc.isoformat(),
        "next_scheduled_run_rome": next_run_rome.isoformat(),
        "next_scheduled_run_utc": next_run_utc.isoformat(),
        "scheduled_window": f"{next_run_rome.strftime('%H:%M')} Europe/Rome",
        "seconds_until_next_run": max(0.0, seconds_remaining),
    }


class ScheduledSyncLock:
    """
    Process-level lock using a lockfile to prevent overlapping synchronization runs.
    """
    def __init__(self, lock_file_path: str):
        self.lock_file_path = lock_file_path
        self._acquired = False
        self._file_handle = None

    def acquire(self) -> bool:
        if os.path.exists(self.lock_file_path):
            try:
                # Check for stale lock (older than 30 minutes)
                mtime = os.path.getmtime(self.lock_file_path)
                if (datetime.datetime.now().timestamp() - mtime) > 1800:
                    logger.warning(f"Removing stale lock file: {self.lock_file_path}")
                    os.remove(self.lock_file_path)
                else:
                    return False
            except Exception:
                return False

        try:
            # Atomic creation via exclusive open mode
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(self.lock_file_path, flags)
            pid_info = json.dumps({
                "pid": os.getpid(),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            os.write(fd, pid_info.encode("utf-8"))
            os.close(fd)
            self._acquired = True
            return True
        except FileExistsError:
            return False
        except Exception as e:
            logger.warning(f"Could not create lockfile {self.lock_file_path}: {e}")
            return False

    def release(self):
        if self._acquired and os.path.exists(self.lock_file_path):
            try:
                os.remove(self.lock_file_path)
            except Exception as e:
                logger.warning(f"Could not remove lockfile {self.lock_file_path}: {e}")
            finally:
                self._acquired = False

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError(f"Another mailbox synchronization process is currently running (lock held at {self.lock_file_path}).")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class ScheduledSyncRunner:
    """
    Executes unattended scheduled synchronization or manual operator Sync Now runs
    for the authorized primary sales mailboxes only.
    """

    def __init__(
        self,
        mailbox_repo: MailboxRepository,
        credential_store: Optional[MailboxCredentialStore] = None,
        sync_orchestrator: Optional[MailboxSyncOrchestrator] = None,
        lock_dir: Optional[str] = None,
    ):
        self.repo = mailbox_repo
        self.credential_store = credential_store or MailboxCredentialStore()
        self.orchestrator = sync_orchestrator or MailboxSyncOrchestrator(
            mailbox_repo=self.repo,
            credential_store=self.credential_store,
        )
        base_dir = lock_dir or (os.path.dirname(self.repo.db_path) if self.repo.db_path else ".")
        self.lock_path = os.path.join(base_dir, ".mailbox_sync.lock")

    def execute_scheduled_sync(
        self,
        target_account_id: Optional[str] = None,
        max_batches_per_folder: int = 2,
        batch_size: int = 50,
        dry_run: bool = False,
        is_manual_sync_now: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute safe sync for authorized primary sales mailboxes.
        
        Args:
            target_account_id: Specific account or None for both primary accounts in sequence.
            max_batches_per_folder: Maximum batch count for bounded catch-up.
            batch_size: Messages per batch.
            dry_run: Simulate sync without writing or committing checkpoints.
            is_manual_sync_now: True if triggered by explicit operator action.
                                When False (unattended), skipped if is_enabled == 0.
        """
        lock = ScheduledSyncLock(self.lock_path)
        if not lock.acquire():
            return {
                "status": "skipped_locked",
                "error": f"Another mailbox sync is currently running (lock: {self.lock_path})",
                "accounts_processed": [],
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

        try:
            return self._run_sync_under_lock(
                target_account_id=target_account_id,
                max_batches_per_folder=max_batches_per_folder,
                batch_size=batch_size,
                dry_run=dry_run,
                is_manual_sync_now=is_manual_sync_now,
            )
        finally:
            lock.release()

    def _run_sync_under_lock(
        self,
        target_account_id: Optional[str],
        max_batches_per_folder: int,
        batch_size: int,
        dry_run: bool,
        is_manual_sync_now: bool,
    ) -> Dict[str, Any]:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        
        # Determine accounts to process
        if target_account_id:
            if target_account_id not in AUTHORIZED_SCHEDULED_ACCOUNTS:
                raise ValueError(
                    f"Account '{target_account_id}' is strictly forbidden from scheduled synchronization. "
                    f"Only authorized primary sales mailboxes: {list(AUTHORIZED_SCHEDULED_ACCOUNTS.keys())}"
                )
            accounts_to_sync = [target_account_id]
        else:
            accounts_to_sync = ["acc_info_com", "acc_info_it"]

        report: Dict[str, Any] = {
            "status": "success",
            "execution_mode": "manual_sync_now" if is_manual_sync_now else "unattended_scheduled",
            "dry_run": dry_run,
            "started_at": start_time.isoformat(),
            "completed_at": None,
            "total_messages_ingested": 0,
            "total_messages_deferred": 0,
            "accounts": {},
            "errors": [],
        }

        for acc_id in accounts_to_sync:
            account = self.repo.get_account(acc_id)
            if not account:
                err = f"Account {acc_id} not registered in mailbox repository"
                report["errors"].append(err)
                report["accounts"][acc_id] = {"status": "error", "error": err}
                continue

            # Verification of account enablement policy
            # Unattended scheduled sync requires is_enabled == 1
            if not is_manual_sync_now and not account.is_enabled:
                logger.info(f"Skipping account {acc_id} ({account.email_address}): is_enabled is 0 in unattended mode.")
                report["accounts"][acc_id] = {
                    "account_id": acc_id,
                    "email_address": account.email_address,
                    "status": "skipped_disabled",
                    "reason": "Account is_enabled=0. Unattended scheduled sync skipped.",
                    "messages_ingested": 0,
                    "messages_deferred": 0,
                }
                continue

            # Execute sync with fail-closed UIDVALIDITY protection for unattended runs
            try:
                acc_result = self.orchestrator.sync_account_folders(
                    account=account,
                    target_folder_roles=["inbox", "sent"],
                    batch_size=batch_size,
                    max_batches_per_folder=max_batches_per_folder,
                    auto_reconcile=True,
                    dry_run=dry_run,
                    fail_on_uidvalidity_change=not is_manual_sync_now,
                )
                
                report["accounts"][acc_id] = acc_result
                report["total_messages_ingested"] += acc_result.get("total_messages_ingested", 0)
                report["total_messages_deferred"] += acc_result.get("total_messages_deferred", 0)
                
                if acc_result.get("errors"):
                    report["errors"].extend(acc_result["errors"])
                    report["status"] = "partial_failure"

            except Exception as e:
                err_msg = f"Failed sync for {acc_id}: {str(e)}"
                logger.error(err_msg)
                report["errors"].append(err_msg)
                report["accounts"][acc_id] = {
                    "account_id": acc_id,
                    "email_address": account.email_address,
                    "status": "error",
                    "error": str(e),
                }
                report["status"] = "partial_failure"

        report["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if len(report["errors"]) == len(accounts_to_sync) and len(accounts_to_sync) > 0:
            report["status"] = "failed"

        return report

    def get_scheduler_status(self) -> Dict[str, Any]:
        """
        Produce a comprehensive status snapshot for the scheduled synchronization system.
        """
        schedule_info = calculate_next_scheduled_run()
        lock_active = os.path.exists(self.lock_path)
        
        accounts_status = {}
        for acc_id, email in AUTHORIZED_SCHEDULED_ACCOUNTS.items():
            acc = self.repo.get_account(acc_id)
            if not acc:
                accounts_status[acc_id] = {"email": email, "registered": False}
                continue

            folders_info = {}
            account_folders = self.repo.list_folders(acc_id)
            for role in ["inbox", "sent"]:
                matching = [f for f in account_folders if f.folder_role == role]
                folder = matching[0] if matching else None
                if folder:
                    sync_state = self.repo.get_folder_sync_state(acc_id, folder.folder_id)
                    folders_info[role] = {
                        "folder_name": folder.folder_name,
                        "uidvalidity": sync_state.uidvalidity if sync_state else folder.last_uidvalidity,
                        "last_synced_uid": sync_state.last_synced_uid if sync_state else 0,
                        "last_sync_completed_at": sync_state.last_sync_completed_at if sync_state else None,
                        "last_status": sync_state.last_status if sync_state else "not_synced",
                    }
                else:
                    folders_info[role] = None

            accounts_status[acc_id] = {
                "email": email,
                "registered": True,
                "is_enabled": acc.is_enabled,
                "credential_ref": acc.credential_ref,
                "folders": folders_info,
            }

        return {
            "schedule": schedule_info,
            "concurrency_lock": {
                "active": lock_active,
                "lock_path": self.lock_path,
            },
            "authorized_accounts": accounts_status,
            "permitted_operating_windows": ["12:15 Europe/Rome", "18:00 Europe/Rome"],
        }
