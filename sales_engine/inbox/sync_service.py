"""
sales_engine/inbox/sync_service.py
IMAP-SYNC-02A: Resilient Read-Only Mailbox Synchronization Engine.

Key Architectural Guarantees:
  1. Strict Read-Only:
     - Leverages RegisterItReadOnlyIMAPProvider (EXAMINE, BODY.PEEK, RFC822.SIZE).
     - Never marks messages Seen/Unseen, deletes, copies, or alters remote mailbox state.
     - Never invokes legacy CRM-mutating sync paths (InboxSyncManager).
  2. Resilient Checkpointing & Interruption Recovery:
     - Checkpoints advance monotonically per (account_id, folder_id, uidvalidity).
     - Checkpoint commits occur strictly AFTER messages are safely persisted in SQLite.
     - Partial batch failure preserves the last committed UID so next run seamlessly resumes.
  3. Pre-Flight Large Message Protection:
     - RFC822.SIZE evaluated prior to full message retrieval (threshold: 10 MB).
     - Oversized messages retrieve headers only and are persisted with status 'deferred_oversized'.
     - Full attachment binary download is strictly avoided.
  4. Complete Separation of Evidence from CRM Mutations:
     - Populates mailbox_accounts, mailbox_folders, mailbox_folder_sync_state,
       mailbox_messages, and staged message_crm_links.
     - Never touches candidate_companies, candidate_contacts, historical_sent_emails,
       manual_outreach_log, or suppression lists.
  5. Concurrency & Overlap Guard:
     - Prevents simultaneous synchronization runs against the same account/folder.
  6. Disabled by Default:
     - Requires explicit operator invocation. Server does NOT run background sync on startup.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxFolderSyncStateRecord,
    MailboxMessageRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.ingestion_service import MailboxIngestionService
from sales_engine.inbox.reconciliation_service import MailboxReconciliationService
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.inbox.credential_store import MailboxCredentialStore

logger = logging.getLogger(__name__)

DEFAULT_SYNC_BATCH_SIZE = 50
MAX_RETRY_COUNT = 3


class MailboxSyncOrchestrator:
    """
    Orchestrates resilient, read-only multi-folder synchronization across authorized accounts.
    """

    def __init__(
        self,
        mailbox_repo: MailboxRepository,
        credential_store: Optional[MailboxCredentialStore] = None,
        ingestion_service: Optional[MailboxIngestionService] = None,
        reconciliation_service: Optional[MailboxReconciliationService] = None,
        provider_factory: Optional[Any] = None,
    ):
        self.repo = mailbox_repo
        self.credential_store = credential_store or MailboxCredentialStore()
        self.ingestion_service = ingestion_service or MailboxIngestionService(repo=self.repo)
        self.reconciliation_service = reconciliation_service or MailboxReconciliationService(mailbox_repo=self.repo)
        self.provider_factory = provider_factory or (lambda acc: RegisterItReadOnlyIMAPProvider(account=acc, credential_store=self.credential_store))
        
        # Concurrency guard: track active (account_id, folder_id) locks
        self._active_sync_locks: Set[Tuple[str, str]] = set()
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Core Synchronization Lifecycle
    # -----------------------------------------------------------------------

    def sync_account_folders(
        self,
        account: MailboxAccountRecord,
        target_folder_roles: Optional[List[str]] = None,
        batch_size: int = DEFAULT_SYNC_BATCH_SIZE,
        max_batches_per_folder: int = 10,
        auto_reconcile: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Synchronize specified folders (default: inbox, sent) for an account.
        Discovers folders if not yet recorded, then syncs each folder monotonically.
        """
        target_roles = target_folder_roles or ["inbox", "sent"]
        provider = self.provider_factory(account)

        # 1. Discover remote folders
        discovered_folders = provider.discover_folders(account)
        for f in discovered_folders:
            self.repo.save_folder(f)

        # Filter discovered folders matching target roles
        eligible_folders = [f for f in discovered_folders if f.folder_role in target_roles]
        if not eligible_folders:
            # Fallback to any active folders in repo
            eligible_folders = [f for f in self.repo.list_folders(account.account_id) if f.folder_role in target_roles]

        results = {
            "account_id": account.account_id,
            "email_address": account.email_address,
            "folders_synced": {},
            "total_messages_ingested": 0,
            "total_messages_deferred": 0,
            "dry_run": dry_run,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "errors": [],
        }

        for folder in eligible_folders:
            folder_res = self.sync_folder(
                account=account,
                folder=folder,
                batch_size=batch_size,
                max_batches=max_batches_per_folder,
                auto_reconcile=auto_reconcile,
                dry_run=dry_run,
                provider=provider,
            )
            results["folders_synced"][folder.folder_name] = folder_res
            results["total_messages_ingested"] += folder_res.get("messages_ingested", 0)
            results["total_messages_deferred"] += folder_res.get("messages_deferred", 0)
            if folder_res.get("error"):
                results["errors"].append(f"{folder.folder_name}: {folder_res['error']}")

        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        return results

    def sync_folder(
        self,
        account: MailboxAccountRecord,
        folder: MailboxFolderRecord,
        batch_size: int = DEFAULT_SYNC_BATCH_SIZE,
        max_batches: int = 10,
        auto_reconcile: bool = True,
        dry_run: bool = False,
        provider: Optional[RegisterItReadOnlyIMAPProvider] = None,
    ) -> Dict[str, Any]:
        """
        Synchronize a single folder with strict checkpoint monotonicity and interruption recovery.
        """
        lock_key = (account.account_id, folder.folder_id)
        with self._lock:
            if lock_key in self._active_sync_locks:
                return {
                    "folder_id": folder.folder_id,
                    "folder_name": folder.folder_name,
                    "status": "skipped_locked",
                    "error": "Synchronization already in progress for this account/folder",
                }
            self._active_sync_locks.add(lock_key)

        try:
            return self._execute_folder_sync(
                account=account,
                folder=folder,
                batch_size=batch_size,
                max_batches=max_batches,
                auto_reconcile=auto_reconcile,
                dry_run=dry_run,
                provider=provider,
            )
        finally:
            with self._lock:
                self._active_sync_locks.discard(lock_key)

    def _execute_folder_sync(
        self,
        account: MailboxAccountRecord,
        folder: MailboxFolderRecord,
        batch_size: int,
        max_batches: int,
        auto_reconcile: bool,
        dry_run: bool,
        provider: Optional[RegisterItReadOnlyIMAPProvider],
    ) -> Dict[str, Any]:
        p = provider or self.provider_factory(account)

        # Retrieve current durable checkpoint
        sync_state = self.repo.get_folder_sync_state(account.account_id, folder.folder_id)
        last_uid = sync_state.last_synced_uid if sync_state else 0
        last_uv = sync_state.uidvalidity if sync_state else 0

        total_ingested = 0
        total_deferred = 0
        current_cursor = last_uid
        client = None

        try:
            client = p.connect(account)
            # EXAMINE folder read-only
            remote_count, remote_uv = p.examine_folder(client, folder.folder_name)
            if remote_uv <= 0:
                raise ValueError(
                    f"Refusing to sync folder '{folder.folder_name}' with non-positive UIDVALIDITY: {remote_uv}"
                )

            # Keep folder record in sync with server's UIDVALIDITY
            if folder.last_uidvalidity != remote_uv:
                folder.last_uidvalidity = remote_uv
                self.repo.save_folder(folder)

            # Detect UIDVALIDITY rollover
            if last_uv > 0 and remote_uv != last_uv:
                logger.warning(
                    f"UIDVALIDITY rollover detected for {account.account_id}/{folder.folder_name}: "
                    f"was {last_uv}, remote is {remote_uv}. Resetting cursor safely to 0."
                )
                current_cursor = 0
                sync_state_reset = MailboxFolderSyncStateRecord(
                    sync_state_id=f"sync_{account.account_id}_{folder.folder_id}",
                    account_id=account.account_id,
                    folder_id=folder.folder_id,
                    uidvalidity=remote_uv,
                    last_synced_uid=0,
                    last_status="uidvalidity_reset",
                )
                self.repo.update_folder_sync_state(sync_state_reset)

            # If folder is empty and no sync state existed yet, initialize checkpoint at 0
            if sync_state is None:
                init_state = MailboxFolderSyncStateRecord(
                    sync_state_id=f"sync_{account.account_id}_{folder.folder_id}",
                    account_id=account.account_id,
                    folder_id=folder.folder_id,
                    uidvalidity=remote_uv,
                    last_synced_uid=0,
                    last_status="success",
                )
                self.repo.update_folder_sync_state(init_state)

            # Progress through bounded batches
            for batch_num in range(max_batches):
                messages, new_highest_uid, uv = p.fetch_messages_by_uid(
                    account=account,
                    folder=folder,
                    since_uid=current_cursor,
                    batch_size=batch_size,
                    client=client,
                )

                if not messages:
                    # Catch-up complete
                    break

                if dry_run:
                    total_ingested += len(messages)
                    current_cursor = new_highest_uid
                    continue

                # Durable Ingestion Staging
                ingest_res = self.ingestion_service.ingest_batch(
                    account=account,
                    folder=folder,
                    messages=messages,
                    new_highest_uid=new_highest_uid,
                    uidvalidity=remote_uv,
                )

                total_ingested += ingest_res["inserted_count"]
                current_cursor = new_highest_uid

                # Count deferred oversized messages
                for m in messages:
                    if m.parse_status == "deferred_oversized":
                        total_deferred += 1

                # Evidence-based reconciliation (staging proposals only)
                if auto_reconcile:
                    for m in messages:
                        try:
                            # 1. Organization matching
                            self.reconciliation_service.stage_organization_link(m)
                            # 2. Historical Sent reconciliation if outbound
                            if m.direction == "outbound":
                                self.reconciliation_service.reconcile_historical_sent(m)
                                self.reconciliation_service.reconcile_manual_outreach(m)
                            # 3. Thread linkage
                            self.reconciliation_service.link_thread_proposals(m)
                        except Exception as e:
                            logger.error(f"Error evaluating reconciliation for message {m.message_id}: {e}")

                if new_highest_uid <= current_cursor and len(messages) < batch_size:
                    break

            return {
                "folder_id": folder.folder_id,
                "folder_name": folder.folder_name,
                "folder_role": folder.folder_role,
                "uidvalidity": remote_uv,
                "starting_uid": last_uid,
                "ending_uid": current_cursor,
                "messages_ingested": total_ingested,
                "messages_deferred": total_deferred,
                "status": "success",
                "error": None,
            }

        except Exception as e:
            err_msg = f"Sync failed for {folder.folder_name}: {e}"
            logger.error(err_msg)
            # Record failed status on checkpoint without skipping uncommitted UIDs
            fail_state = MailboxFolderSyncStateRecord(
                sync_state_id=f"sync_{account.account_id}_{folder.folder_id}",
                account_id=account.account_id,
                folder_id=folder.folder_id,
                uidvalidity=remote_uv if 'remote_uv' in locals() else last_uv,
                last_synced_uid=current_cursor,
                last_status="failed",
                last_error_message=str(e),
            )
            self.repo.update_folder_sync_state(fail_state)
            return {
                "folder_id": folder.folder_id,
                "folder_name": folder.folder_name,
                "starting_uid": last_uid,
                "ending_uid": current_cursor,
                "messages_ingested": total_ingested,
                "messages_deferred": total_deferred,
                "status": "failed",
                "error": str(e),
            }

        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass
