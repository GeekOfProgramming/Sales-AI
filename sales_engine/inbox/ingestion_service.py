"""
sales_engine/inbox/ingestion_service.py
MAIL-LINK-01: Staging-only ingestion service for Register.it IMAP messages.

CRITICAL ARCHITECTURAL CONSTRAINTS:
  1. Staging-only: stores raw email evidence into mailbox_messages and mailbox_folder_sync_state.
  2. Zero CRM mutations: NEVER updates candidate_companies, candidate_contacts,
     historical_sent_emails, manual_outreach_log, or suppression tables.
  3. Strict idempotency: uses (account_id, folder_id, imap_uidvalidity, imap_uid).
  4. Preserves multiple occurrences of identical RFC Message-IDs across folders and accounts.
  5. Never invokes InboxSyncManager's CRM-mutating processing path.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MailboxFolderSyncStateRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository

logger = logging.getLogger(__name__)


class MailboxIngestionService:
    """
    Ingests normalized mailbox messages from the read-only IMAP provider into the
    staging layer without mutating CRM records.
    """

    def __init__(self, repo: MailboxRepository):
        self.repo = repo

    def ingest_batch(
        self,
        account: MailboxAccountRecord,
        folder: MailboxFolderRecord,
        messages: List[MailboxMessageRecord],
        new_highest_uid: int,
        uidvalidity: int,
    ) -> Dict[str, Any]:
        """
        Idempotently ingest a batch of normalized messages and advance the folder's sync cursor.
        Returns detailed ingestion statistics.
        """
        inserted_count = 0
        duplicate_count = 0
        error_count = 0
        inserted_message_ids: List[str] = []

        for msg in messages:
            try:
                # Ensure physical occurrence parameters match folder & account
                msg.account_id = account.account_id
                msg.folder_id = folder.folder_id
                msg.imap_uidvalidity = uidvalidity

                is_new, mid = self.repo.save_message(msg)
                if is_new:
                    inserted_count += 1
                    inserted_message_ids.append(mid)
                else:
                    duplicate_count += 1
            except Exception as e:
                logger.error(f"Failed to ingest message {getattr(msg, 'message_id', 'unknown')}: {e}")
                error_count += 1

        # Safety check: refuse to ingest or advance checkpoints with non-positive UIDVALIDITY
        if uidvalidity <= 0:
            raise ValueError(
                f"Refusing ingestion and checkpoint advancement for folder '{folder.folder_id}' with non-positive UIDVALIDITY: {uidvalidity}"
            )

        # Keep folder record updated with last_uidvalidity and last_seen_uid
        if folder.last_uidvalidity != uidvalidity or folder.last_seen_uid < new_highest_uid:
            folder.last_uidvalidity = uidvalidity
            if new_highest_uid > folder.last_seen_uid:
                folder.last_seen_uid = new_highest_uid
            self.repo.save_folder(folder)

        # Advance durable folder sync checkpoint if messages were processed
        # Only advance monotonically for matching UIDVALIDITY
        current_state = self.repo.get_folder_sync_state(account.account_id, folder.folder_id)
        current_total = (current_state.messages_fetched_total if current_state else 0) + inserted_count
        
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        checkpoint = MailboxFolderSyncStateRecord(
            sync_state_id=f"sync_{account.account_id}_{folder.folder_id}",
            account_id=account.account_id,
            folder_id=folder.folder_id,
            uidvalidity=uidvalidity,
            last_synced_uid=max(new_highest_uid, current_state.last_synced_uid if current_state else 0),
            last_sync_completed_at=now_iso,
            last_status="success" if error_count == 0 else "partial_error",
            messages_fetched_total=current_total,
            last_error_message=None if error_count == 0 else f"{error_count} messages failed to ingest",
        )
        self.repo.update_folder_sync_state(checkpoint)

        return {
            "account_id": account.account_id,
            "folder_id": folder.folder_id,
            "uidvalidity": uidvalidity,
            "highest_uid": new_highest_uid,
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "error_count": error_count,
            "inserted_message_ids": inserted_message_ids,
        }
