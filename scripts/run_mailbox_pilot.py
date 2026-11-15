#!/usr/bin/env python3
"""
scripts/run_mailbox_pilot.py
IMAP-SYNC-02B: Single-Mailbox Live Pilot Preparation & Controlled Execution.

Approved Pilot Target:
  Account: info@pybim.com
  Host: pop.securemail.pro:993
  Safety Mode: Strictly Read-Only (EXAMINE, BODY.PEEK, RFC822.SIZE, zero STORE)
  Limits: Max 10 messages from Inbox, max 10 messages from Sent

Exit Codes:
  0 - Success (Action completed as requested)
  1 - Configuration or validation error (e.g. wrong account, missing credentials)
  2 - Remote protocol or connection failure (IMAP/TLS error, timeout)
  3 - Safety check failed (ambiguous sent folder, unconfirmed live action)
  4 - Database integrity / backup check failure

Default behavior is OFFLINE / DIAGNOSTIC. No live IMAP connection or staging occurs
without explicit flags (--live-connect, --stage-sample).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.credential_store import MailboxCredentialStore, WindowsDPAPI
from sales_engine.inbox.providers.register_it import RegisterItReadOnlyIMAPProvider
from sales_engine.inbox.sync_service import MailboxSyncOrchestrator
from sales_engine.inbox.reconciliation_service import MailboxReconciliationService
from sales_engine.inbox.scheduled_sync import (
    ScheduledSyncRunner,
    calculate_next_scheduled_run,
    get_europe_rome_tz,
    AUTHORIZED_SCHEDULED_ACCOUNTS,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.candidates.backup_engine import (
    resolve_configured_db_path,
    verify_sqlite_backup_integrity,
    verify_restore_in_disposable_clone,
)

logger = logging.getLogger(__name__)

# Approved Mailbox Targets Configuration
AUTHORIZED_MAILBOX_CONFIGS: Dict[str, Dict[str, Any]] = {
    "info@pybim.com": {
        "account_id": "acc_info_com",
        "email_address": "info@pybim.com",
        "display_name": "pyBIM Info (COM)",
        "credential_ref": "win_cred_acc_info_com",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 1,
    },
    "info@pybim.it": {
        "account_id": "acc_info_it",
        "email_address": "info@pybim.it",
        "display_name": "pyBIM Info (IT)",
        "credential_ref": "win_cred_acc_info_it",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 2,
    },
    "careers@pybim.com": {
        "account_id": "acc_careers_com",
        "email_address": "careers@pybim.com",
        "display_name": "pyBIM Careers",
        "credential_ref": "win_cred_acc_careers_com",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 3,
    },
    "support@pybim.com": {
        "account_id": "acc_support_com",
        "email_address": "support@pybim.com",
        "display_name": "pyBIM Support",
        "credential_ref": "win_cred_acc_support_com",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 4,
    },
    "hamed.sharifi@pybim.it": {
        "account_id": "acc_hamed_it",
        "email_address": "hamed.sharifi@pybim.it",
        "display_name": "Hamed Sharifi",
        "credential_ref": "win_cred_acc_hamed_it",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 5,
    },
    "hamid.lotfalian@pybim.it": {
        "account_id": "acc_hamid_it",
        "email_address": "hamid.lotfalian@pybim.it",
        "display_name": "Hamid Lotfalian",
        "credential_ref": "win_cred_acc_hamid_it",
        "imap_host": "pop.securemail.pro",
        "imap_port": 993,
        "sync_priority": 6,
    },
}

# Constants strictly enforcing IMAP-SYNC-02B constraints for default/pilot account
PILOT_ACCOUNT_ID = "acc_info_com"
PILOT_EMAIL_ADDRESS = "info@pybim.com"
PILOT_DISPLAY_NAME = "pyBIM Info (COM)"
PILOT_CREDENTIAL_REF = "win_cred_acc_info_com"
PILOT_IMAP_HOST = "pop.securemail.pro"
PILOT_IMAP_PORT = 993
PILOT_MAX_INBOX_MESSAGES = 10
PILOT_MAX_SENT_MESSAGES = 10


def get_pilot_account_config() -> MailboxAccountRecord:
    """Returns the immutable MailboxAccountRecord for the approved pilot account."""
    return get_account_config_by_email(PILOT_EMAIL_ADDRESS)


def get_account_config_by_email(email_address: str) -> MailboxAccountRecord:
    """
    Returns the MailboxAccountRecord for one of the six authorized Register.it accounts.
    Raises ValueError if email is not in the authorized list.
    """
    clean_email = email_address.strip().lower()
    if clean_email not in AUTHORIZED_MAILBOX_CONFIGS:
        raise ValueError(
            f"Unauthorized account: '{email_address}'. "
            f"Authorized accounts: {list(AUTHORIZED_MAILBOX_CONFIGS.keys())}"
        )
    cfg = AUTHORIZED_MAILBOX_CONFIGS[clean_email]
    return MailboxAccountRecord(
        account_id=cfg["account_id"],
        email_address=cfg["email_address"],
        display_name=cfg["display_name"],
        provider="register_it",
        imap_host=cfg["imap_host"],
        imap_port=cfg["imap_port"],
        use_ssl=True,
        credential_ref=cfg["credential_ref"],
        is_enabled=False,  # Disabled by default until individually authorized
        sync_priority=cfg["sync_priority"],
        connection_status="configured",
    )


# Primary Sales Mailboxes authorized for historical backfill (IMAP-BACKFILL-01A)
HISTORICAL_BACKFILL_ALLOWED_ACCOUNTS: List[str] = [
    "info@pybim.com",
    "info@pybim.it",
]
MAX_HISTORICAL_BATCH_LIMIT = 50


class MailboxPilotRunner:
    """
    Operator-controlled runner for single-mailbox read-only pilot and sequential rollouts.
    Encapsulates diagnostic checks, remote folder discovery, metadata preview,
    and optional controlled sample staging.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        cred_store: Optional[MailboxCredentialStore] = None,
        provider_factory: Optional[Any] = None,
        account: Optional[MailboxAccountRecord] = None,
    ):
        self.db_path = db_path or str(resolve_configured_db_path())
        self.cred_store = cred_store or MailboxCredentialStore()
        self.repo = MailboxRepository(db_path=self.db_path)
        self.account = account or get_pilot_account_config()
        self.provider_factory = provider_factory or (
            lambda acc: RegisterItReadOnlyIMAPProvider(account=acc, credential_store=self.cred_store)
        )

    # -----------------------------------------------------------------------
    # Step 1: Pre-Flight Safety & Credential Validation
    # -----------------------------------------------------------------------

    def validate_preflight(self, check_credential: bool = True) -> Dict[str, Any]:
        """
        Verify account identity, OS DPAPI availability, credential configuration,
        and database integrity without opening network connections.
        """
        report: Dict[str, Any] = {
            "account_id": self.account.account_id,
            "email_address": self.account.email_address,
            "imap_host": self.account.imap_host,
            "imap_port": self.account.imap_port,
            "dpapi_available": WindowsDPAPI.is_available(),
            "credential_ref": self.account.credential_ref,
            "credential_configured": False,
            "database_path": self.db_path,
            "database_exists": os.path.exists(self.db_path),
            "safety_passed": False,
            "errors": [],
        }

        # 1. Target Account Lock: Must be in authorized accounts
        clean_email = (self.account.email_address or "").strip().lower()
        if clean_email not in AUTHORIZED_MAILBOX_CONFIGS:
            report["errors"].append(
                f"Unauthorized pilot target: {self.account.email_address}. "
                f"Authorized accounts: {list(AUTHORIZED_MAILBOX_CONFIGS.keys())}"
            )
        else:
            expected_cfg = AUTHORIZED_MAILBOX_CONFIGS[clean_email]
            if self.account.credential_ref != expected_cfg["credential_ref"]:
                report["errors"].append(
                    f"Credential reference mismatch for {clean_email}: expected '{expected_cfg['credential_ref']}', got '{self.account.credential_ref}'"
                )

        # 2. Credential Check
        if check_credential:
            configured_refs = self.cred_store.list_configured_refs()
            if self.account.credential_ref in configured_refs:
                report["credential_configured"] = True
            else:
                report["errors"].append(
                    f"Credential reference '{self.account.credential_ref}' not found in DPAPI store. "
                    f"Run: python scripts/manage_mailbox_credentials.py set --ref {self.account.credential_ref}"
                )

        report["safety_passed"] = len(report["errors"]) == 0
        return report

    # -----------------------------------------------------------------------
    # Step 2: Diagnostic Connection Check (Read-Only)
    # -----------------------------------------------------------------------

    def run_connection_diagnostics(self) -> Dict[str, Any]:
        """
        Perform a live read-only diagnostic connection to Register.it.
        Connects via TLS 993, authenticates, queries capabilities, and logs out immediately.
        """
        preflight = self.validate_preflight(check_credential=True)
        if not preflight["safety_passed"]:
            return {
                "success": False,
                "error_category": "PREFLIGHT_VALIDATION_FAILED",
                "errors": preflight["errors"],
                "details": preflight,
            }

        provider = self.provider_factory(self.account)
        try:
            diag = provider.run_diagnostics(self.account)
            success = diag.get("connected", False) and diag.get("authenticated", False)
            return {
                "success": success,
                "error_category": diag.get("error_category"),
                "error_message": diag.get("error_message"),
                "diagnostics": diag,
            }
        except Exception as e:
            return {
                "success": False,
                "error_category": type(e).__name__,
                "error_message": str(e),
            }

    # -----------------------------------------------------------------------
    # Step 3: Remote Folder Discovery & Sent Ambiguity Detection
    # -----------------------------------------------------------------------

    def discover_remote_folders(self) -> Dict[str, Any]:
        """
        Discover remote folders on the IMAP server.
        Identifies actual Inbox and Sent folder paths and detects if more than one
        Sent folder exists (requiring operator clarification).
        """
        preflight = self.validate_preflight(check_credential=True)
        if not preflight["safety_passed"]:
            return {
                "success": False,
                "error": "Pre-flight validation failed",
                "details": preflight,
            }

        provider = self.provider_factory(self.account)
        try:
            client = provider.connect(self.account)
            try:
                folders = provider.discover_folders(self.account, client=client)
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

            inbox_folders = [f for f in folders if f.folder_role == "inbox"]
            sent_folders = [f for f in folders if f.folder_role == "sent"]

            ambiguous_sent = len(sent_folders) > 1
            detected_inbox = inbox_folders[0].folder_name if inbox_folders else None
            detected_sent = sent_folders[0].folder_name if (sent_folders and not ambiguous_sent) else None

            return {
                "success": True,
                "total_folders": len(folders),
                "folders": [
                    {
                        "folder_id": f.folder_id,
                        "folder_name": f.folder_name,
                        "folder_role": f.folder_role,
                        "folder_delimiter": f.folder_delimiter,
                        "imap_flags": f.imap_flags,
                    }
                    for f in folders
                ],
                "detected_inbox": detected_inbox,
                "detected_sent": detected_sent,
                "sent_candidates": [f.folder_name for f in sent_folders],
                "ambiguous_sent": ambiguous_sent,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_category": type(e).__name__,
            }

    # -----------------------------------------------------------------------
    # Step 4: Metadata-Only Preview (Headers & Flags, Zero DB Writes)
    # -----------------------------------------------------------------------

    def preview_sample_metadata(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: Optional[str] = None,
        max_inbox: int = PILOT_MAX_INBOX_MESSAGES,
        max_sent: int = PILOT_MAX_SENT_MESSAGES,
    ) -> Dict[str, Any]:
        """
        Inspect the most recent headers and IMAP flags in Inbox and Sent folders
        using read-only EXAMINE and BODY.PEEK[HEADER].
        Zero SQLite writes, zero remote flag modifications.
        """
        max_inbox = min(max_inbox, PILOT_MAX_INBOX_MESSAGES)
        max_sent = min(max_sent, PILOT_MAX_SENT_MESSAGES)

        provider = self.provider_factory(self.account)
        client = None

        try:
            client = provider.connect(self.account)
            result: Dict[str, Any] = {
                "success": True,
                "account": self.account.email_address,
                "inbox_preview": [],
                "sent_preview": [],
                "flags_inspected": {},
            }

            # 1. Preview Inbox
            inbox_count, inbox_uv = provider.examine_folder(client, inbox_folder_name)
            typ, search_data = client.uid("SEARCH", "ALL")
            if typ == "OK" and search_data and search_data[0]:
                uids = [int(u) for u in search_data[0].split() if u.isdigit()]
                sample_uids = uids[-max_inbox:] if len(uids) > max_inbox else uids
                if sample_uids:
                    # Inspect flags
                    flags_map = provider.fetch_message_flags(inbox_folder_name, sample_uids, client=client)
                    result["flags_inspected"]["inbox"] = flags_map

                    # Fetch headers
                    fld_obj = MailboxFolderRecord(
                        folder_id=f"fld_preview_inbox",
                        account_id=self.account.account_id,
                        folder_name=inbox_folder_name,
                        folder_role="inbox",
                    )
                    msgs, _, _ = provider.fetch_messages_by_uid(
                        account=self.account,
                        folder=fld_obj,
                        since_uid=min(sample_uids) - 1 if sample_uids else 0,
                        batch_size=len(sample_uids),
                        client=client,
                    )
                    result["inbox_preview"] = [
                        {
                            "uid": m.imap_uid,
                            "subject": m.subject,
                            "from": m.from_address,
                            "to": m.to_addresses,
                            "date_utc": m.date_utc,
                            "parse_status": m.parse_status,
                            "flags": flags_map.get(m.imap_uid, []),
                        }
                        for m in msgs
                    ]

            # 2. Preview Sent (if specified and unambiguous)
            if sent_folder_name:
                sent_count, sent_uv = provider.examine_folder(client, sent_folder_name)
                typ, search_data = client.uid("SEARCH", "ALL")
                if typ == "OK" and search_data and search_data[0]:
                    uids = [int(u) for u in search_data[0].split() if u.isdigit()]
                    sample_uids = uids[-max_sent:] if len(uids) > max_sent else uids
                    if sample_uids:
                        flags_map = provider.fetch_message_flags(sent_folder_name, sample_uids, client=client)
                        result["flags_inspected"]["sent"] = flags_map

                        fld_obj = MailboxFolderRecord(
                            folder_id=f"fld_preview_sent",
                            account_id=self.account.account_id,
                            folder_name=sent_folder_name,
                            folder_role="sent",
                        )
                        msgs, _, _ = provider.fetch_messages_by_uid(
                            account=self.account,
                            folder=fld_obj,
                            since_uid=min(sample_uids) - 1 if sample_uids else 0,
                            batch_size=len(sample_uids),
                            client=client,
                        )
                        result["sent_preview"] = [
                            {
                                "uid": m.imap_uid,
                                "subject": m.subject,
                                "from": m.from_address,
                                "to": m.to_addresses,
                                "date_utc": m.date_utc,
                                "parse_status": m.parse_status,
                                "flags": flags_map.get(m.imap_uid, []),
                            }
                            for m in msgs
                        ]

            return result

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_category": type(e).__name__,
            }
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Step 5: Controlled Live Pilot Sample Staging (Max 10 Inbox, 10 Sent)
    # -----------------------------------------------------------------------

    def execute_pilot_sample_staging(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: Optional[str] = None,
        max_inbox: int = PILOT_MAX_INBOX_MESSAGES,
        max_sent: int = PILOT_MAX_SENT_MESSAGES,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute bounded read-only pilot synchronization for info@pybim.com.
        
        Guarantees:
          - Validates pre-flight conditions and pre-pilot backups.
          - Strictly enforces bounds: max 10 Inbox, max 10 Sent.
          - Pre-flight message size evaluated via RFC822.SIZE (10 MB limit).
          - Captures remote flags before and after fetch to verify ZERO mutations.
          - Modifies ONLY mailbox_* tables and message_crm_links proposals.
          - ZERO writes to candidate_companies, candidate_contacts, historical_sent_emails,
            or manual_outreach_log.
        """
        max_inbox = min(max_inbox, PILOT_MAX_INBOX_MESSAGES)
        max_sent = min(max_sent, PILOT_MAX_SENT_MESSAGES)

        # 1. Pre-flight check
        preflight = self.validate_preflight(check_credential=True)
        if not preflight["safety_passed"]:
            return {
                "success": False,
                "error": "Pre-flight validation failed",
                "details": preflight,
            }

        # 2. Verify schema readiness in target database
        try:
            self.repo.apply_migrations()
        except Exception as e:
            return {
                "success": False,
                "error": f"Schema initialization failed: {e}",
            }

        # Save pilot account record in repo
        self.repo.save_account(self.account)

        cand_store = CandidateStore(db_path=self.db_path)
        rev_store = ReviewStore(db_path=self.db_path)
        recon_svc = MailboxReconciliationService(
            mailbox_repo=self.repo,
            candidate_store=cand_store,
            review_store=rev_store,
        )

        orchestrator = MailboxSyncOrchestrator(
            mailbox_repo=self.repo,
            reconciliation_service=recon_svc,
            provider_factory=self.provider_factory,
        )

        provider = self.provider_factory(self.account)
        client = None

        flags_before: Dict[str, Dict[int, List[str]]] = {}
        flags_after: Dict[str, Dict[int, List[str]]] = {}

        try:
            client = provider.connect(self.account)

            # 3. Setup Folders
            # Inspect actual remote UIDVALIDITY prior to saving folder records
            _, inbox_uv = provider.examine_folder(client, inbox_folder_name)
            inbox_fld = MailboxFolderRecord(
                folder_id=f"fld_{self.account.account_id}_inbox",
                account_id=self.account.account_id,
                folder_name=inbox_folder_name,
                folder_role="inbox",
                last_uidvalidity=inbox_uv,
            )
            self.repo.save_folder(inbox_fld)

            sent_fld = None
            if sent_folder_name:
                _, sent_uv = provider.examine_folder(client, sent_folder_name)
                sent_fld = MailboxFolderRecord(
                    folder_id=f"fld_{self.account.account_id}_sent",
                    account_id=self.account.account_id,
                    folder_name=sent_folder_name,
                    folder_role="sent",
                    last_uidvalidity=sent_uv,
                )
                self.repo.save_folder(sent_fld)

            # Capture initial flags for up to max_inbox messages
            _, _ = provider.examine_folder(client, inbox_folder_name)
            typ, sdata = client.uid("SEARCH", "ALL")
            inbox_uids: List[int] = []
            if typ == "OK" and sdata and sdata[0]:
                uids = [int(u) for u in sdata[0].split() if u.isdigit()]
                inbox_uids = uids[-max_inbox:] if len(uids) > max_inbox else uids
                flags_before["inbox"] = provider.fetch_message_flags(inbox_folder_name, inbox_uids, client=client)

            sent_uids: List[int] = []
            if sent_fld:
                _, _ = provider.examine_folder(client, sent_folder_name)
                typ, sdata = client.uid("SEARCH", "ALL")
                if typ == "OK" and sdata and sdata[0]:
                    uids = [int(u) for u in sdata[0].split() if u.isdigit()]
                    sent_uids = uids[-max_sent:] if len(uids) > max_sent else uids
                    flags_before["sent"] = provider.fetch_message_flags(sent_folder_name, sent_uids, client=client)

            # 4. Execute sync
            inbox_res = orchestrator.sync_folder(
                account=self.account,
                folder=inbox_fld,
                batch_size=max_inbox,
                max_batches=1,
                dry_run=dry_run,
                provider=provider,
            )

            sent_res = None
            if sent_fld:
                sent_res = orchestrator.sync_folder(
                    account=self.account,
                    folder=sent_fld,
                    batch_size=max_sent,
                    max_batches=1,
                    dry_run=dry_run,
                    provider=provider,
                )

            # 5. Capture post-sync flags to verify flag preservation
            if inbox_uids:
                flags_after["inbox"] = provider.fetch_message_flags(inbox_folder_name, inbox_uids, client=client)
            if sent_uids and sent_fld:
                flags_after["sent"] = provider.fetch_message_flags(sent_folder_name, sent_uids, client=client)

            # Flag preservation verification
            flag_mutations_detected = False
            for f_key in flags_before:
                for uid_k, before_f in flags_before[f_key].items():
                    after_f = flags_after.get(f_key, {}).get(uid_k, [])
                    if set(before_f) != set(after_f):
                        flag_mutations_detected = True
                        logger.warning(
                            f"Flag divergence observed for {f_key} UID {uid_k}: before={before_f}, after={after_f}"
                        )

            return {
                "success": True,
                "account": self.account.email_address,
                "dry_run": dry_run,
                "inbox_sync": inbox_res,
                "sent_sync": sent_res,
                "flag_preservation": {
                    "verified": not flag_mutations_detected,
                    "mutations_detected": flag_mutations_detected,
                    "inbox_inspected_count": len(inbox_uids),
                    "sent_inspected_count": len(sent_uids),
                },
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_category": type(e).__name__,
            }
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Step 6: Read-Only Verification of Existing Pilot Messages (IMAP-UV-REPAIR-01A)
    # -----------------------------------------------------------------------

    def verify_existing_pilot_messages(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: str = "INBOX.Sent",
    ) -> Dict[str, Any]:
        """
        Narrowly scoped, read-only verification of existing pilot messages.
        
        Strict Safety Guarantees:
          - Restricted to info@pybim.com (PILOT_EMAIL_ADDRESS).
          - Uses read-only SQLite queries to inspect existing records.
          - Opens remote folders in strictly read-only mode via EXAMINE.
          - Retrieves positive server UIDVALIDITY independently for INBOX and Sent.
          - Fetches remote message headers using UID FETCH (UID BODY.PEEK[HEADER]).
          - Never downloads message bodies or attachments.
          - Zero SQLite modifications (no UPDATE, INSERT, or DELETE).
          - Zero remote flag changes.
          - Does not invoke sync or advance checkpoints.
        """
        # 1. Target restriction: info@pybim.com only
        if self.account.email_address != PILOT_EMAIL_ADDRESS:
            return {
                "success": False,
                "error": f"Verification is strictly restricted to {PILOT_EMAIL_ADDRESS}. Target was: {self.account.email_address}",
                "error_category": "UNAUTHORIZED_TARGET",
            }

        # 2. Validate pre-flight and credentials
        preflight = self.validate_preflight(check_credential=True)
        if not preflight["safety_passed"]:
            return {
                "success": False,
                "error": "Pre-flight validation failed",
                "details": preflight,
                "error_category": "PREFLIGHT_VALIDATION_FAILED",
            }

        # 3. Read existing operational records from SQLite in strictly read-only mode
        import sqlite3
        con_ro = sqlite3.connect(f"file:{os.path.abspath(self.db_path).replace(os.sep, '/')}?mode=ro", uri=True)
        con_ro.row_factory = sqlite3.Row
        cur = con_ro.cursor()

        inbox_records = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_messages WHERE account_id = ? AND folder_role = 'inbox' ORDER BY imap_uid ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]
        sent_records = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_messages WHERE account_id = ? AND folder_role = 'sent' ORDER BY imap_uid ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]

        # Get folder checkpoints
        inbox_sync = cur.execute(
            "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
            (self.account.account_id, f"fld_{self.account.account_id}_inbox"),
        ).fetchone()
        sent_sync = cur.execute(
            "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
            (self.account.account_id, f"fld_{self.account.account_id}_sent"),
        ).fetchone()

        con_ro.close()

        # 4. Connect to IMAP server and verify headers
        provider = self.provider_factory(self.account)
        client = None

        report: Dict[str, Any] = {
            "success": True,
            "account": self.account.email_address,
            "actual_inbox_uidvalidity": None,
            "actual_sent_uidvalidity": None,
            "inbox_verified_count": 0,
            "inbox_mismatched_count": 0,
            "inbox_missing_count": 0,
            "inbox_uncertain_count": 0,
            "sent_verified_count": 0,
            "sent_mismatched_count": 0,
            "sent_missing_count": 0,
            "sent_uncertain_count": 0,
            "checkpoint_consistency": {
                "inbox_checkpoint_uid": inbox_sync["last_synced_uid"] if inbox_sync else None,
                "sent_checkpoint_uid": sent_sync["last_synced_uid"] if sent_sync else None,
                "inbox_max_stored_uid": max([r["imap_uid"] for r in inbox_records]) if inbox_records else None,
                "sent_max_stored_uid": max([r["imap_uid"] for r in sent_records]) if sent_records else None,
                "is_consistent": False,
            },
            "safe_repair_eligible": False,
            "verifications": {
                "inbox": [],
                "sent": [],
            },
        }

        try:
            client = provider.connect(self.account)

            # --- A. Verify INBOX ---
            _, inbox_uv = provider.examine_folder(client, inbox_folder_name)
            report["actual_inbox_uidvalidity"] = inbox_uv

            inbox_verifs = self._verify_folder_messages(
                client=client,
                provider=provider,
                folder_name=inbox_folder_name,
                stored_records=inbox_records,
            )
            report["verifications"]["inbox"] = inbox_verifs
            for v in inbox_verifs:
                st = v["status"]
                if st == "VERIFIED":
                    report["inbox_verified_count"] += 1
                elif st == "MISMATCH":
                    report["inbox_mismatched_count"] += 1
                elif st == "MISSING":
                    report["inbox_missing_count"] += 1
                else:
                    report["inbox_uncertain_count"] += 1

            # --- B. Verify INBOX.Sent ---
            _, sent_uv = provider.examine_folder(client, sent_folder_name)
            report["actual_sent_uidvalidity"] = sent_uv

            sent_verifs = self._verify_folder_messages(
                client=client,
                provider=provider,
                folder_name=sent_folder_name,
                stored_records=sent_records,
            )
            report["verifications"]["sent"] = sent_verifs
            for v in sent_verifs:
                st = v["status"]
                if st == "VERIFIED":
                    report["sent_verified_count"] += 1
                elif st == "MISMATCH":
                    report["sent_mismatched_count"] += 1
                elif st == "MISSING":
                    report["sent_missing_count"] += 1
                else:
                    report["sent_uncertain_count"] += 1

            # --- C. Consistency & Repair Eligibility ---
            cp = report["checkpoint_consistency"]
            inbox_cp_ok = cp["inbox_checkpoint_uid"] == cp["inbox_max_stored_uid"] == 11
            sent_cp_ok = cp["sent_checkpoint_uid"] == cp["sent_max_stored_uid"] == 17
            cp["is_consistent"] = inbox_cp_ok and sent_cp_ok

            report["safe_repair_eligible"] = (
                report["inbox_verified_count"] == 10
                and report["sent_verified_count"] == 10
                and report["inbox_mismatched_count"] == 0
                and report["sent_mismatched_count"] == 0
                and report["inbox_missing_count"] == 0
                and report["sent_missing_count"] == 0
                and report["inbox_uncertain_count"] == 0
                and report["sent_uncertain_count"] == 0
                and cp["is_consistent"]
                and inbox_uv > 0
                and sent_uv > 0
            )

            return report

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_category": type(e).__name__,
                "details": report,
            }
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    def _verify_folder_messages(
        self,
        client: Any,
        provider: RegisterItReadOnlyIMAPProvider,
        folder_name: str,
        stored_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Fetch remote headers via UID FETCH (UID BODY.PEEK[HEADER]) and compare
        with stored SQLite records.
        """
        import email
        import re

        if not stored_records:
            return []

        uids = [r["imap_uid"] for r in stored_records]
        uid_str = ",".join(str(u) for u in uids)

        typ, fetch_data = client.uid("FETCH", uid_str, "(UID BODY.PEEK[HEADER])")
        remote_headers_by_uid: Dict[int, Any] = {}

        if typ == "OK" and fetch_data:
            for item in fetch_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                raw_hdr, raw_bytes = item[0], item[1]
                hdr_str = raw_hdr.decode("utf-8", errors="replace") if isinstance(raw_hdr, (bytes, bytearray)) else str(raw_hdr)
                m_uid = re.search(r"UID\s+(\d+)", hdr_str, re.IGNORECASE)
                if m_uid:
                    uid = int(m_uid.group(1))
                    parsed_email = email.message_from_bytes(raw_bytes)
                    remote_headers_by_uid[uid] = parsed_email

        verifications: List[Dict[str, Any]] = []

        for rec in stored_records:
            uid = rec["imap_uid"]
            if uid not in remote_headers_by_uid:
                verifications.append({
                    "uid": uid,
                    "status": "MISSING",
                    "details": "Remote message UID not found on server",
                })
                continue

            r_msg = remote_headers_by_uid[uid]

            # Extract normalized remote headers
            remote_rfc_id = provider._clean_header(r_msg.get("Message-ID"))
            remote_subject = provider._decode_mime_words(r_msg.get("Subject", ""))
            remote_from_raw = provider._decode_mime_words(r_msg.get("From", ""))
            remote_from_addr, _ = provider._parse_address_and_name(remote_from_raw)
            remote_date_raw = r_msg.get("Date")
            remote_date_utc = provider._normalize_date_to_utc(remote_date_raw)

            stored_rfc_id = rec.get("rfc_message_id")
            stored_subject = rec.get("subject")
            stored_from = rec.get("from_address")
            stored_date_utc = rec.get("date_utc")

            # Evidence comparison
            has_rfc_id = bool(stored_rfc_id and remote_rfc_id)
            rfc_id_matches = has_rfc_id and (stored_rfc_id.strip() == remote_rfc_id.strip())

            # Subject comparison (strip whitespace)
            subject_matches = (stored_subject or "").strip() == (remote_subject or "").strip()

            # From comparison
            from_matches = (stored_from or "").strip().lower() == (remote_from_addr or "").strip().lower()

            # Date comparison (timestamps normalized to UTC ISO format)
            # Compare prefixes up to seconds e.g. YYYY-MM-DDTHH:MM:SS
            def _clean_ts(ts: Optional[str]) -> str:
                if not ts:
                    return ""
                return ts.split(".")[0].replace("Z", "+00:00")

            date_matches = _clean_ts(stored_date_utc) == _clean_ts(remote_date_utc)

            if has_rfc_id:
                if rfc_id_matches and subject_matches and from_matches:
                    verifications.append({
                        "uid": uid,
                        "status": "VERIFIED",
                        "rfc_message_id": remote_rfc_id,
                        "evidence": "RFC Message-ID, Subject, and From address match",
                    })
                elif not rfc_id_matches:
                    verifications.append({
                        "uid": uid,
                        "status": "MISMATCH",
                        "stored_rfc_id": stored_rfc_id,
                        "remote_rfc_id": remote_rfc_id,
                        "evidence": "RFC Message-ID mismatch on server",
                    })
                else:
                    verifications.append({
                        "uid": uid,
                        "status": "MISMATCH",
                        "evidence": f"Subject/From divergence despite RFC Message-ID match: stored_from={stored_from}, remote_from={remote_from_addr}",
                    })
            else:
                # No RFC Message-ID: evaluate multi-attribute consensus
                if subject_matches and from_matches and date_matches and stored_subject:
                    verifications.append({
                        "uid": uid,
                        "status": "VERIFIED",
                        "evidence": "Multi-attribute consensus: Subject, From, and Date match",
                    })
                elif subject_matches and from_matches:
                    verifications.append({
                        "uid": uid,
                        "status": "INSUFFICIENT_EVIDENCE",
                        "evidence": "Subject and From match but Date inconclusive or RFC Message-ID missing",
                    })
                else:
                    verifications.append({
                        "uid": uid,
                        "status": "MISMATCH",
                        "evidence": "Header divergence on Subject, From, or Date without RFC Message-ID",
                    })

        return verifications

    # -----------------------------------------------------------------------
    # Step 7: Safe, Transactional Pilot UIDVALIDITY Repair (IMAP-UV-REPAIR-01B)
    # -----------------------------------------------------------------------

    def repair_pilot_uidvalidity(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: str = "INBOX.Sent",
        dry_run: bool = False,
        backup_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Narrowly scoped, operator-controlled transactional repair of existing pilot
        records with imap_uidvalidity=0.

        Strict Safety Guarantees:
          - Restricted strictly to info@pybim.com (PILOT_EMAIL_ADDRESS).
          - Requires dynamic re-verification against the live IMAP server.
          - Idempotency check: if all records already have correct UIDVALIDITY, returns ALREADY_REPAIRED.
          - Pre-repair online SQLite backup + disposable clone restore verification.
          - Dry-run mode produces zero database mutations and requires zero backup writes.
          - Atomic transaction: rolls back on any error, missing record, or integrity failure.
          - Post-repair assertions: exactly 10 inbox, 10 sent, intact checkpoints (11, 17),
            20 message_crm_links preserved, and PRAGMA integrity & FK checks passing.
        """
        # 1. Target restriction: info@pybim.com only
        if self.account.email_address != PILOT_EMAIL_ADDRESS:
            return {
                "success": False,
                "error": f"Repair is strictly restricted to {PILOT_EMAIL_ADDRESS}. Target was: {self.account.email_address}",
                "error_category": "UNAUTHORIZED_TARGET",
            }

        # 2. Check current database state in read-only mode for idempotency and pre-inspection
        import sqlite3
        con_ro = sqlite3.connect(f"file:{os.path.abspath(self.db_path).replace(os.sep, '/')}?mode=ro", uri=True)
        con_ro.row_factory = sqlite3.Row
        cur = con_ro.cursor()

        inbox_rows = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_messages WHERE account_id = ? AND folder_role = 'inbox' ORDER BY imap_uid ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]
        sent_rows = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_messages WHERE account_id = ? AND folder_role = 'sent' ORDER BY imap_uid ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]
        inbox_folder_row = cur.execute(
            "SELECT * FROM mailbox_folders WHERE account_id = ? AND folder_role = 'inbox' LIMIT 1;",
            (self.account.account_id,),
        ).fetchone()
        sent_folder_row = cur.execute(
            "SELECT * FROM mailbox_folders WHERE account_id = ? AND folder_role = 'sent' LIMIT 1;",
            (self.account.account_id,),
        ).fetchone()
        inbox_sync_row = cur.execute(
            "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
            (self.account.account_id, f"fld_{self.account.account_id}_inbox"),
        ).fetchone()
        sent_sync_row = cur.execute(
            "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ? LIMIT 1;",
            (self.account.account_id, f"fld_{self.account.account_id}_sent"),
        ).fetchone()
        crm_links_count = cur.execute(
            """
            SELECT COUNT(*) FROM message_crm_links l
            JOIN mailbox_messages m ON l.message_id = m.message_id
            WHERE m.account_id = ?;
            """,
            (self.account.account_id,),
        ).fetchone()[0]

        con_ro.close()

        # 3. Dynamic Re-Verification: Fetch positive server UIDVALIDITY & verify all 20 messages
        verify_res = self.verify_existing_pilot_messages(
            inbox_folder_name=inbox_folder_name,
            sent_folder_name=sent_folder_name,
        )

        if not verify_res.get("success"):
            return {
                "success": False,
                "error": f"Verification failed prior to repair: {verify_res.get('error')}",
                "error_category": verify_res.get("error_category", "VERIFICATION_FAILED"),
                "verification_details": verify_res,
            }

        actual_inbox_uv = verify_res.get("actual_inbox_uidvalidity")
        actual_sent_uv = verify_res.get("actual_sent_uidvalidity")

        if not actual_inbox_uv or actual_inbox_uv <= 0 or not actual_sent_uv or actual_sent_uv <= 0:
            return {
                "success": False,
                "error": f"Server returned non-positive UIDVALIDITY (Inbox: {actual_inbox_uv}, Sent: {actual_sent_uv})",
                "error_category": "INVALID_SERVER_UIDVALIDITY",
                "verification_details": verify_res,
            }

        # Check idempotency: If all rows already have the correct positive UIDVALIDITY
        inbox_already_repaired = (
            len(inbox_rows) == 10
            and all(r["imap_uidvalidity"] == actual_inbox_uv for r in inbox_rows)
            and (inbox_folder_row and inbox_folder_row["last_uidvalidity"] == actual_inbox_uv)
            and (inbox_sync_row and inbox_sync_row["uidvalidity"] == actual_inbox_uv)
        )
        sent_already_repaired = (
            len(sent_rows) == 10
            and all(r["imap_uidvalidity"] == actual_sent_uv for r in sent_rows)
            and (sent_folder_row and sent_folder_row["last_uidvalidity"] == actual_sent_uv)
            and (sent_sync_row and sent_sync_row["uidvalidity"] == actual_sent_uv)
        )

        if inbox_already_repaired and sent_already_repaired:
            return {
                "success": True,
                "status": "ALREADY_REPAIRED",
                "message": "All 20 pilot messages and sync state records are already consistent with server UIDVALIDITY.",
                "account": self.account.email_address,
                "inbox_uidvalidity": actual_inbox_uv,
                "sent_uidvalidity": actual_sent_uv,
                "dry_run": dry_run,
                "rows_updated": {
                    "inbox_messages": 0,
                    "sent_messages": 0,
                    "mailbox_folders": 0,
                    "mailbox_sync_state": 0,
                },
            }

        # Ensure safe repair eligibility
        if not verify_res.get("safe_repair_eligible"):
            return {
                "success": False,
                "error": "Message verification detected discrepancies or checkpoints are inconsistent. Repair aborted.",
                "error_category": "REPAIR_INELIGIBLE",
                "verification_details": verify_res,
            }

        # 4. Dry-Run Handling
        if dry_run:
            inbox_to_update = sum(1 for r in inbox_rows if r["imap_uidvalidity"] == 0)
            sent_to_update = sum(1 for r in sent_rows if r["imap_uidvalidity"] == 0)
            return {
                "success": True,
                "status": "DRY_RUN_SUCCESS",
                "dry_run": True,
                "account": self.account.email_address,
                "server_uidvalidity": {
                    "inbox": actual_inbox_uv,
                    "sent": actual_sent_uv,
                },
                "projected_updates": {
                    "inbox_messages": inbox_to_update,
                    "sent_messages": sent_to_update,
                    "mailbox_folders": 2,
                    "mailbox_sync_state": 2,
                },
                "crm_links_preserved": crm_links_count,
                "verification_summary": {
                    "inbox_verified": verify_res.get("inbox_verified_count"),
                    "sent_verified": verify_res.get("sent_verified_count"),
                },
            }

        # 5. Pre-Repair Online SQLite Backup & Disposable Clone Verification
        from sales_engine.candidates.backup_engine import (
            create_sqlite_online_backup,
            verify_restore_in_disposable_clone,
        )

        backup_res = create_sqlite_online_backup(
            source_db_path=self.db_path,
            dest_dir=backup_dir,
            mode="manual",
        )
        if not backup_res.success:
            return {
                "success": False,
                "error": f"Pre-repair online SQLite backup failed: {backup_res.error_message}",
                "error_category": "BACKUP_FAILED",
                "backup_details": backup_res.to_dict(),
            }

        restore_res = verify_restore_in_disposable_clone(
            backup_path=backup_res.backup_path,
            expected_tables=[
                "candidate_companies",
                "candidate_contacts",
                "historical_sent_emails",
                "manual_outreach_log",
                "mailbox_accounts",
                "mailbox_folders",
                "mailbox_messages",
                "mailbox_folder_sync_state",
                "message_crm_links",
            ],
        )
        if not restore_res.get("success"):
            return {
                "success": False,
                "error": f"Pre-repair backup disposable clone verification failed: {restore_res.get('error')}",
                "error_category": "RESTORE_VERIFICATION_FAILED",
                "restore_details": restore_res,
            }

        # 6. Atomic SQLite Transactional Repair
        now_utc = datetime.now(timezone.utc).isoformat()
        inbox_folder_id = f"fld_{self.account.account_id}_inbox"
        sent_folder_id = f"fld_{self.account.account_id}_sent"

        inbox_updated_count = 0
        sent_updated_count = 0
        folders_updated_count = 0
        sync_state_updated_count = 0

        conn_rw = sqlite3.connect(self.db_path, timeout=30.0)
        conn_rw.row_factory = sqlite3.Row
        try:
            with conn_rw:
                cur_rw = conn_rw.cursor()

                # A. Update INBOX messages
                cur_rw.execute(
                    """
                    UPDATE mailbox_messages
                    SET imap_uidvalidity = ?
                    WHERE account_id = ?
                      AND folder_role = 'inbox'
                      AND imap_uidvalidity = 0;
                    """,
                    (actual_inbox_uv, self.account.account_id),
                )
                inbox_updated_count = cur_rw.rowcount
                if inbox_updated_count != 10:
                    raise ValueError(
                        f"Expected exactly 10 INBOX messages to be updated, but {inbox_updated_count} were affected."
                    )

                # B. Update INBOX.Sent messages
                cur_rw.execute(
                    """
                    UPDATE mailbox_messages
                    SET imap_uidvalidity = ?
                    WHERE account_id = ?
                      AND folder_role = 'sent'
                      AND imap_uidvalidity = 0;
                    """,
                    (actual_sent_uv, self.account.account_id),
                )
                sent_updated_count = cur_rw.rowcount
                if sent_updated_count != 10:
                    raise ValueError(
                        f"Expected exactly 10 Sent messages to be updated, but {sent_updated_count} were affected."
                    )

                # C. Update mailbox_folders last_uidvalidity
                cur_rw.execute(
                    """
                    UPDATE mailbox_folders
                    SET last_uidvalidity = ?,
                        updated_at = ?
                    WHERE account_id = ?
                      AND folder_id = ?;
                    """,
                    (actual_inbox_uv, now_utc, self.account.account_id, inbox_folder_id),
                )
                folders_updated_count += cur_rw.rowcount

                cur_rw.execute(
                    """
                    UPDATE mailbox_folders
                    SET last_uidvalidity = ?,
                        updated_at = ?
                    WHERE account_id = ?
                      AND folder_id = ?;
                    """,
                    (actual_sent_uv, now_utc, self.account.account_id, sent_folder_id),
                )
                folders_updated_count += cur_rw.rowcount

                # D. Update mailbox_folder_sync_state uidvalidity
                cur_rw.execute(
                    """
                    UPDATE mailbox_folder_sync_state
                    SET uidvalidity = ?,
                        updated_at = ?
                    WHERE account_id = ?
                      AND folder_id = ?;
                    """,
                    (actual_inbox_uv, now_utc, self.account.account_id, inbox_folder_id),
                )
                sync_state_updated_count += cur_rw.rowcount

                cur_rw.execute(
                    """
                    UPDATE mailbox_folder_sync_state
                    SET uidvalidity = ?,
                        updated_at = ?
                    WHERE account_id = ?
                      AND folder_id = ?;
                    """,
                    (actual_sent_uv, now_utc, self.account.account_id, sent_folder_id),
                )
                sync_state_updated_count += cur_rw.rowcount

                # E. Transactional PRAGMAs
                integrity_rows = cur_rw.execute("PRAGMA integrity_check;").fetchall()
                if not integrity_rows or integrity_rows[0][0] != "ok":
                    raise RuntimeError(f"Integrity check failed during repair: {integrity_rows}")

                fk_rows = cur_rw.execute("PRAGMA foreign_key_check;").fetchall()
                if fk_rows:
                    raise RuntimeError(f"Foreign key violations detected during repair: {fk_rows}")

            # 7. Post-Repair Read Verification
            cur_post = conn_rw.cursor()

            # Verify inbox message count and positive UIDVALIDITY
            post_inbox = cur_post.execute(
                "SELECT COUNT(*) FROM mailbox_messages WHERE account_id = ? AND folder_role = 'inbox' AND imap_uidvalidity = ?;",
                (self.account.account_id, actual_inbox_uv),
            ).fetchone()[0]
            if post_inbox != 10:
                raise ValueError(f"Post-repair check failed: expected 10 inbox records with UIDVALIDITY={actual_inbox_uv}, found {post_inbox}")

            # Verify sent message count and positive UIDVALIDITY
            post_sent = cur_post.execute(
                "SELECT COUNT(*) FROM mailbox_messages WHERE account_id = ? AND folder_role = 'sent' AND imap_uidvalidity = ?;",
                (self.account.account_id, actual_sent_uv),
            ).fetchone()[0]
            if post_sent != 10:
                raise ValueError(f"Post-repair check failed: expected 10 sent records with UIDVALIDITY={actual_sent_uv}, found {post_sent}")

            # Check zero records have imap_uidvalidity = 0
            post_zero = cur_post.execute(
                "SELECT COUNT(*) FROM mailbox_messages WHERE account_id = ? AND imap_uidvalidity = 0;",
                (self.account.account_id,),
            ).fetchone()[0]
            if post_zero != 0:
                raise ValueError(f"Post-repair check failed: {post_zero} records still have imap_uidvalidity = 0")

            # Check checkpoints remain 11 and 17
            post_inbox_sync = cur_post.execute(
                "SELECT uidvalidity, last_synced_uid FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ?;",
                (self.account.account_id, inbox_folder_id),
            ).fetchone()
            if not post_inbox_sync or post_inbox_sync["last_synced_uid"] != 11 or post_inbox_sync["uidvalidity"] != actual_inbox_uv:
                raise ValueError(f"Post-repair inbox sync checkpoint altered: {dict(post_inbox_sync) if post_inbox_sync else None}")

            post_sent_sync = cur_post.execute(
                "SELECT uidvalidity, last_synced_uid FROM mailbox_folder_sync_state WHERE account_id = ? AND folder_id = ?;",
                (self.account.account_id, sent_folder_id),
            ).fetchone()
            if not post_sent_sync or post_sent_sync["last_synced_uid"] != 17 or post_sent_sync["uidvalidity"] != actual_sent_uv:
                raise ValueError(f"Post-repair sent sync checkpoint altered: {dict(post_sent_sync) if post_sent_sync else None}")

            # Check message_crm_links count remains untouched
            post_crm_links = cur_post.execute(
                """
                SELECT COUNT(*) FROM message_crm_links l
                JOIN mailbox_messages m ON l.message_id = m.message_id
                WHERE m.account_id = ?;
                """,
                (self.account.account_id,),
            ).fetchone()[0]
            if post_crm_links != crm_links_count:
                raise ValueError(f"Post-repair CRM links count altered: was {crm_links_count}, now {post_crm_links}")

            # Check zero duplicate messages
            dup_count = cur_post.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT account_id, folder_id, imap_uidvalidity, imap_uid, COUNT(*) as c
                    FROM mailbox_messages
                    WHERE account_id = ?
                    GROUP BY account_id, folder_id, imap_uidvalidity, imap_uid
                    HAVING c > 1
                );
                """,
                (self.account.account_id,),
            ).fetchone()[0]
            if dup_count != 0:
                raise ValueError(f"Post-repair duplicate detection failed: {dup_count} duplicate physical occurrences found")

            return {
                "success": True,
                "status": "REPAIR_COMMITTED",
                "account": self.account.email_address,
                "server_uidvalidity": {
                    "inbox": actual_inbox_uv,
                    "sent": actual_sent_uv,
                },
                "backup": {
                    "backup_path": backup_res.backup_path,
                    "sha256": backup_res.sha256_checksum,
                    "integrity_status": backup_res.integrity_status,
                },
                "rows_updated": {
                    "inbox_messages": inbox_updated_count,
                    "sent_messages": sent_updated_count,
                    "mailbox_folders": folders_updated_count,
                    "mailbox_sync_state": sync_state_updated_count,
                },
                "post_verification": {
                    "inbox_repaired_count": post_inbox,
                    "sent_repaired_count": post_sent,
                    "zero_uv_count": post_zero,
                    "crm_links_preserved": post_crm_links,
                    "checkpoints": {
                        "inbox": 11,
                        "sent": 17,
                    },
                    "duplicates": 0,
                },
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Repair transaction aborted and rolled back: {e}",
                "error_category": type(e).__name__,
            }
        finally:
            conn_rw.close()

    # -----------------------------------------------------------------------
    # Step 8: Historical Sync & Status Reporting (IMAP-BACKFILL-01A)
    # -----------------------------------------------------------------------

    def report_mailbox_status(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: str = "INBOX.Sent",
        live_connect: bool = False,
    ) -> Dict[str, Any]:
        """
        Read-only status reporting for the selected primary mailbox account:
        - Local stored message counts, UID ranges, deferred counts
        - Current local checkpoints and stored UIDVALIDITY
        - Optional remote inspection (if live_connect=True):
          remote message counts, remote UID ranges (min/max), remote UIDVALIDITY
        - Strictly read-only, zero mutations.
        """
        import sqlite3

        # 1. Inspect local database records in strictly read-only mode
        con_ro = sqlite3.connect(f"file:{os.path.abspath(self.db_path).replace(os.sep, '/')}?mode=ro", uri=True)
        con_ro.row_factory = sqlite3.Row
        cur = con_ro.cursor()

        acc_row = cur.execute(
            "SELECT * FROM mailbox_accounts WHERE account_id = ? LIMIT 1;",
            (self.account.account_id,),
        ).fetchone()

        # Folders
        folders = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_folders WHERE account_id = ? ORDER BY folder_role ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]

        # Sync states
        sync_states = [
            dict(r)
            for r in cur.execute(
                "SELECT * FROM mailbox_folder_sync_state WHERE account_id = ? ORDER BY folder_id ASC;",
                (self.account.account_id,),
            ).fetchall()
        ]

        # Messages stats per folder_role
        stats_rows = cur.execute(
            """
            SELECT folder_role,
                   COUNT(*) as message_count,
                   MIN(imap_uid) as min_uid,
                   MAX(imap_uid) as max_uid,
                   MIN(imap_uidvalidity) as min_uv,
                   MAX(imap_uidvalidity) as max_uv,
                   SUM(CASE WHEN parse_status = 'deferred_oversized' THEN 1 ELSE 0 END) as deferred_count
            FROM mailbox_messages
            WHERE account_id = ?
            GROUP BY folder_role;
            """,
            (self.account.account_id,),
        ).fetchall()

        msg_stats: Dict[str, Dict[str, Any]] = {}
        for r in stats_rows:
            msg_stats[r["folder_role"]] = {
                "message_count": r["message_count"],
                "min_uid": r["min_uid"],
                "max_uid": r["max_uid"],
                "min_uidvalidity": r["min_uv"],
                "max_uidvalidity": r["max_uv"],
                "deferred_count": r["deferred_count"] or 0,
            }

        con_ro.close()

        status_report: Dict[str, Any] = {
            "success": True,
            "account_id": self.account.account_id,
            "email_address": self.account.email_address,
            "is_primary_sales_mailbox": self.account.email_address in HISTORICAL_BACKFILL_ALLOWED_ACCOUNTS,
            "account_configured": bool(acc_row),
            "is_enabled": bool(acc_row["is_enabled"]) if acc_row else False,
            "folders": {
                "inbox": {
                    "folder_name": inbox_folder_name,
                    "stored_messages": msg_stats.get("inbox", {}).get("message_count", 0),
                    "stored_min_uid": msg_stats.get("inbox", {}).get("min_uid"),
                    "stored_max_uid": msg_stats.get("inbox", {}).get("max_uid"),
                    "stored_uidvalidity": msg_stats.get("inbox", {}).get("max_uidvalidity"),
                    "stored_deferred": msg_stats.get("inbox", {}).get("deferred_count", 0),
                    "checkpoint_uid": None,
                    "checkpoint_uidvalidity": None,
                    "remote_count": None,
                    "remote_uidvalidity": None,
                    "remote_min_uid": None,
                    "remote_max_uid": None,
                },
                "sent": {
                    "folder_name": sent_folder_name,
                    "stored_messages": msg_stats.get("sent", {}).get("message_count", 0),
                    "stored_min_uid": msg_stats.get("sent", {}).get("min_uid"),
                    "stored_max_uid": msg_stats.get("sent", {}).get("max_uid"),
                    "stored_uidvalidity": msg_stats.get("sent", {}).get("max_uidvalidity"),
                    "stored_deferred": msg_stats.get("sent", {}).get("deferred_count", 0),
                    "checkpoint_uid": None,
                    "checkpoint_uidvalidity": None,
                    "remote_count": None,
                    "remote_uidvalidity": None,
                    "remote_min_uid": None,
                    "remote_max_uid": None,
                },
            },
        }

        # Populate checkpoint info from sync_states
        inbox_fld_id = f"fld_{self.account.account_id}_inbox"
        sent_fld_id = f"fld_{self.account.account_id}_sent"

        for s in sync_states:
            if s["folder_id"] == inbox_fld_id:
                status_report["folders"]["inbox"]["checkpoint_uid"] = s["last_synced_uid"]
                status_report["folders"]["inbox"]["checkpoint_uidvalidity"] = s["uidvalidity"]
            elif s["folder_id"] == sent_fld_id:
                status_report["folders"]["sent"]["checkpoint_uid"] = s["last_synced_uid"]
                status_report["folders"]["sent"]["checkpoint_uidvalidity"] = s["uidvalidity"]

        # 2. Remote inspection if live_connect is enabled
        if live_connect:
            provider = self.provider_factory(self.account)
            client = None
            try:
                client = provider.connect(self.account)

                # Inbox
                cnt_inbox, uv_inbox = provider.examine_folder(client, inbox_folder_name)
                status_report["folders"]["inbox"]["remote_count"] = cnt_inbox
                status_report["folders"]["inbox"]["remote_uidvalidity"] = uv_inbox
                typ, sdata = client.uid("SEARCH", "ALL")
                if typ == "OK" and sdata and sdata[0]:
                    uids = [int(u) for u in sdata[0].split() if u.isdigit()]
                    if uids:
                        status_report["folders"]["inbox"]["remote_min_uid"] = min(uids)
                        status_report["folders"]["inbox"]["remote_max_uid"] = max(uids)

                # Sent
                if sent_folder_name:
                    cnt_sent, uv_sent = provider.examine_folder(client, sent_folder_name)
                    status_report["folders"]["sent"]["remote_count"] = cnt_sent
                    status_report["folders"]["sent"]["remote_uidvalidity"] = uv_sent
                    typ, sdata = client.uid("SEARCH", "ALL")
                    if typ == "OK" and sdata and sdata[0]:
                        uids = [int(u) for u in sdata[0].split() if u.isdigit()]
                        if uids:
                            status_report["folders"]["sent"]["remote_min_uid"] = min(uids)
                            status_report["folders"]["sent"]["remote_max_uid"] = max(uids)

            except Exception as e:
                status_report["remote_inspection_error"] = str(e)
            finally:
                if client:
                    try:
                        client.logout()
                    except Exception:
                        pass

        return status_report

    def execute_historical_backfill(
        self,
        inbox_folder_name: str = "INBOX",
        sent_folder_name: str = "INBOX.Sent",
        batch_size: int = MAX_HISTORICAL_BATCH_LIMIT,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Controlled, resumable historical backfill for the selected primary sales mailbox.
        Strict Safety Guarantees:
          - Restricted strictly to info@pybim.com and info@pybim.it.
          - Batch size capped at 50 messages per folder per execution.
          - Resumes strictly from existing checkpoints (does not reimport initial messages).
          - Uses EXAMINE and BODY.PEEK[] (strictly read-only on server).
          - Enforces RFC822.SIZE preflight (10 MB threshold; oversized are marked deferred).
          - Generates evidence-based CRM proposals without mutating operational tables.
          - Leaves unattended synchronization disabled.
        """
        # 1. Target Account Authorization: info@pybim.com and info@pybim.it ONLY
        if self.account.email_address not in HISTORICAL_BACKFILL_ALLOWED_ACCOUNTS:
            return {
                "success": False,
                "error": (
                    f"Historical backfill is restricted strictly to primary sales accounts: "
                    f"{HISTORICAL_BACKFILL_ALLOWED_ACCOUNTS}. Target '{self.account.email_address}' is rejected."
                ),
                "error_category": "UNAUTHORIZED_TARGET",
            }

        # 2. Bound batch size strictly to max 50
        effective_batch = max(1, min(batch_size, MAX_HISTORICAL_BATCH_LIMIT))

        # 3. Pre-flight check
        preflight = self.validate_preflight(check_credential=True)
        if not preflight["safety_passed"]:
            return {
                "success": False,
                "error": "Pre-flight validation failed",
                "details": preflight,
                "error_category": "PREFLIGHT_VALIDATION_FAILED",
            }

        # 4. Orchestrate sync using existing MailboxSyncOrchestrator
        cand_store = CandidateStore(db_path=self.db_path)
        rev_store = ReviewStore(db_path=self.db_path)
        recon_svc = MailboxReconciliationService(
            mailbox_repo=self.repo,
            candidate_store=cand_store,
            review_store=rev_store,
        )

        orchestrator = MailboxSyncOrchestrator(
            mailbox_repo=self.repo,
            reconciliation_service=recon_svc,
            provider_factory=self.provider_factory,
        )

        provider = self.provider_factory(self.account)
        client = None

        flags_before: Dict[str, Dict[int, List[str]]] = {}
        flags_after: Dict[str, Dict[int, List[str]]] = {}

        try:
            client = provider.connect(self.account)

            # Examine folders to retrieve remote UIDVALIDITY and counts
            inbox_count, inbox_uv = provider.examine_folder(client, inbox_folder_name)
            if inbox_uv <= 0:
                raise ValueError(f"Server returned non-positive UIDVALIDITY for {inbox_folder_name}: {inbox_uv}")

            # Ensure Inbox folder record exists in repository
            inbox_fld = MailboxFolderRecord(
                folder_id=f"fld_{self.account.account_id}_inbox",
                account_id=self.account.account_id,
                folder_name=inbox_folder_name,
                folder_role="inbox",
                last_uidvalidity=inbox_uv,
            )
            if not dry_run:
                self.repo.save_folder(inbox_fld)

            sent_fld = None
            sent_uv = None
            if sent_folder_name:
                sent_count, sent_uv = provider.examine_folder(client, sent_folder_name)
                if sent_uv <= 0:
                    raise ValueError(f"Server returned non-positive UIDVALIDITY for {sent_folder_name}: {sent_uv}")
                sent_fld = MailboxFolderRecord(
                    folder_id=f"fld_{self.account.account_id}_sent",
                    account_id=self.account.account_id,
                    folder_name=sent_folder_name,
                    folder_role="sent",
                    last_uidvalidity=sent_uv,
                )
                if not dry_run:
                    self.repo.save_folder(sent_fld)

            # Retrieve starting checkpoints before execution
            inbox_state = self.repo.get_folder_sync_state(self.account.account_id, inbox_fld.folder_id)
            inbox_starting_checkpoint = inbox_state.last_synced_uid if inbox_state else 0

            sent_starting_checkpoint = 0
            if sent_fld:
                sent_state = self.repo.get_folder_sync_state(self.account.account_id, sent_fld.folder_id)
                sent_starting_checkpoint = sent_state.last_synced_uid if sent_state else 0

            # Execute 1 bounded batch per folder (max 50 messages)
            inbox_res = orchestrator.sync_folder(
                account=self.account,
                folder=inbox_fld,
                batch_size=effective_batch,
                max_batches=1,
                auto_reconcile=True,
                dry_run=dry_run,
                provider=provider,
            )

            sent_res = None
            if sent_fld:
                sent_res = orchestrator.sync_folder(
                    account=self.account,
                    folder=sent_fld,
                    batch_size=effective_batch,
                    max_batches=1,
                    auto_reconcile=True,
                    dry_run=dry_run,
                    provider=provider,
                )

            # Ensure account remains disabled for unattended sync
            if not dry_run:
                acc_rec = self.repo.get_account(self.account.account_id)
                if acc_rec and acc_rec.is_enabled:
                    acc_rec.is_enabled = False
                    self.repo.save_account(acc_rec)

            return {
                "success": (inbox_res.get("status") == "success") and (sent_res is None or sent_res.get("status") == "success"),
                "account": self.account.email_address,
                "account_id": self.account.account_id,
                "dry_run": dry_run,
                "batch_size_limit": effective_batch,
                "inbox_sync": {
                    "folder_name": inbox_folder_name,
                    "starting_checkpoint": inbox_starting_checkpoint,
                    "ending_checkpoint": inbox_res.get("ending_uid"),
                    "messages_ingested": inbox_res.get("messages_ingested", 0),
                    "messages_deferred": inbox_res.get("messages_deferred", 0),
                    "uidvalidity": inbox_res.get("uidvalidity", inbox_uv),
                    "status": inbox_res.get("status"),
                },
                "sent_sync": {
                    "folder_name": sent_folder_name,
                    "starting_checkpoint": sent_starting_checkpoint,
                    "ending_checkpoint": sent_res.get("ending_uid") if sent_res else None,
                    "messages_ingested": sent_res.get("messages_ingested", 0) if sent_res else 0,
                    "messages_deferred": sent_res.get("messages_deferred", 0) if sent_res else 0,
                    "uidvalidity": sent_res.get("uidvalidity", sent_uv) if sent_res else None,
                    "status": sent_res.get("status") if sent_res else None,
                } if sent_res else None,
                "unattended_sync_disabled": True,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_category": type(e).__name__,
            }
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(
        description="pyBIM Sales-AI: Operator-Controlled Read-Only IMAP Pilot (IMAP-SYNC-02B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 1. Offline pre-flight safety & credential check (Zero network connection):
  python scripts/run_mailbox_pilot.py check

  # 2. Live read-only connection diagnostic (Requires DPAPI credential):
  python scripts/run_mailbox_pilot.py diagnose --live-connect

  # 3. Remote folder discovery & Sent ambiguity check:
  python scripts/run_mailbox_pilot.py discover-folders --live-connect

  # 4. Metadata-only preview (Max 10 messages, zero DB writes):
  python scripts/run_mailbox_pilot.py preview --live-connect --sent-folder "Sent"

  # 5. Execute bounded pilot sample staging (Max 10 Inbox, 10 Sent):
  python scripts/run_mailbox_pilot.py stage-sample --live-connect --confirm-pilot --sent-folder "Sent"
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Action to execute")

    # Command: check
    check_parser = subparsers.add_parser("check", help="Offline pre-flight check without network connection")

    # Command: diagnose
    diag_parser = subparsers.add_parser("diagnose", help="Live TLS/authentication diagnostic test")
    diag_parser.add_argument("--live-connect", action="store_true", help="Explicit confirmation to open live IMAP connection")

    # Command: discover-folders
    disc_parser = subparsers.add_parser("discover-folders", help="Discover server folders and check for ambiguous Sent paths")
    disc_parser.add_argument("--live-connect", action="store_true", help="Explicit confirmation to open live IMAP connection")

    # Command: preview
    prev_parser = subparsers.add_parser("preview", help="Preview message headers and flags without database mutations")
    prev_parser.add_argument("--live-connect", action="store_true", help="Explicit confirmation to open live IMAP connection")
    prev_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    prev_parser.add_argument("--sent-folder", help="Remote Sent folder name")
    prev_parser.add_argument("--limit-inbox", type=int, default=PILOT_MAX_INBOX_MESSAGES, help="Max inbox messages to preview (max 10)")
    prev_parser.add_argument("--limit-sent", type=int, default=PILOT_MAX_SENT_MESSAGES, help="Max sent messages to preview (max 10)")

    # Command: stage-sample
    stage_parser = subparsers.add_parser("stage-sample", help="Execute controlled live pilot sample staging into SQLite")
    stage_parser.add_argument("--live-connect", action="store_true", help="Explicit authorization for live connection")
    stage_parser.add_argument("--confirm-pilot", action="store_true", help="Explicit confirmation to write sample messages to mailbox tables")
    stage_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    stage_parser.add_argument("--sent-folder", help="Remote Sent folder name")
    stage_parser.add_argument("--dry-run", action="store_true", help="Test fetch and parsing without committing database records")
    stage_parser.add_argument("--limit-inbox", type=int, default=PILOT_MAX_INBOX_MESSAGES, help="Max inbox messages to stage (max 10)")
    stage_parser.add_argument("--limit-sent", type=int, default=PILOT_MAX_SENT_MESSAGES, help="Max sent messages to stage (max 10)")

    # Command: verify-messages (IMAP-UV-REPAIR-01A)
    verif_parser = subparsers.add_parser("verify-messages", help="Read-only verification of existing pilot messages against remote headers")
    verif_parser.add_argument("--live-connect", action="store_true", help="Explicit authorization to open live IMAP connection")
    verif_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    verif_parser.add_argument("--sent-folder", default="INBOX.Sent", help="Remote Sent folder name (default: INBOX.Sent)")

    # Command: repair-uidvalidity (IMAP-UV-REPAIR-01B)
    repair_parser = subparsers.add_parser("repair-uidvalidity", help="Safe, transactional repair of pilot records with imap_uidvalidity=0")
    repair_parser.add_argument("--live-connect", action="store_true", help="Explicit authorization to open live IMAP connection for verification")
    repair_parser.add_argument("--confirm-repair", action="store_true", help="Explicit confirmation to execute database updates")
    repair_parser.add_argument("--dry-run", action="store_true", help="Simulate repair without modifying SQLite or writing backups")
    repair_parser.add_argument("--backup-dir", help="Directory where pre-repair SQLite backup is stored")
    repair_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    repair_parser.add_argument("--sent-folder", default="INBOX.Sent", help="Remote Sent folder name (default: INBOX.Sent)")

    # Command: status (IMAP-BACKFILL-01A)
    status_parser = subparsers.add_parser("status", help="Read-only status reporting for the selected mailbox account")
    status_parser.add_argument("--live-connect", action="store_true", help="Include live remote message counts and UID ranges")
    status_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    status_parser.add_argument("--sent-folder", default="INBOX.Sent", help="Remote Sent folder name (default: INBOX.Sent)")

    # Command: backfill (IMAP-BACKFILL-01A)
    backfill_parser = subparsers.add_parser("backfill", help="Controlled, bounded historical backfill for primary Sales mailboxes")
    backfill_parser.add_argument("--live-connect", action="store_true", help="Explicit authorization to connect to remote IMAP server")
    backfill_parser.add_argument("--confirm-backfill", action="store_true", help="Explicit confirmation to persist backfilled messages to SQLite")
    backfill_parser.add_argument("--dry-run", action="store_true", help="Simulate historical backfill without committing database records")
    backfill_parser.add_argument("--limit-batch", type=int, default=MAX_HISTORICAL_BATCH_LIMIT, help=f"Max messages per folder per batch (capped at {MAX_HISTORICAL_BATCH_LIMIT})")
    backfill_parser.add_argument("--inbox-folder", default="INBOX", help="Remote Inbox folder name (default: INBOX)")
    backfill_parser.add_argument("--sent-folder", default="INBOX.Sent", help="Remote Sent folder name (default: INBOX.Sent)")

    # Command: scheduled-sync (IMAP-AUTO-SYNC-01A)
    sched_parser = subparsers.add_parser("scheduled-sync", help="Safe scheduled synchronization engine (unattended or manual Sync Now)")
    sched_parser.add_argument("--live-connect", action="store_true", help="Explicit authorization to connect to remote IMAP server")
    sched_parser.add_argument("--sync-now", action="store_true", help="Manual operator trigger ('Sync Now' override)")
    sched_parser.add_argument("--dry-run", action="store_true", help="Simulate sync without committing records or updating checkpoints")
    sched_parser.add_argument("--max-batches", type=int, default=2, help="Max batches of 50 messages per folder (default: 2)")
    sched_parser.add_argument("--batch-size", type=int, default=50, help="Messages per batch (default: 50)")

    # Command: scheduler-status (IMAP-AUTO-SYNC-01A)
    sched_stat_parser = subparsers.add_parser("scheduler-status", help="Read-only status of scheduled sync windows, locks, and account readiness")

    # Command: activate-scheduled (IMAP-AUTO-SYNC-01B-ACTIVATION-PREP)
    act_parser = subparsers.add_parser("activate-scheduled", help="Operator activation/enablement of primary sales mailboxes for scheduled sync")
    act_parser.add_argument("--confirm-activation", action="store_true", help="Explicit confirmation to update SQLite is_enabled=1 for primary accounts")

    # Command: disable-scheduled (IMAP-AUTO-SYNC-01B-ACTIVATION-PREP)
    dis_parser = subparsers.add_parser("disable-scheduled", help="Operator rollback/disablement of primary sales mailboxes for scheduled sync")
    dis_parser.add_argument("--confirm-disable", action="store_true", help="Explicit confirmation to update SQLite is_enabled=0 for primary accounts")

    # Global options
    parser.add_argument(
        "--account",
        default=PILOT_EMAIL_ADDRESS,
        help=f"Target Register.it mailbox address (default: {PILOT_EMAIL_ADDRESS}). Authorized: {list(AUTHORIZED_MAILBOX_CONFIGS.keys())}",
    )
    parser.add_argument("--db-path", help="Path to SQLite database (default: data/sales_outreach.db)")
    parser.add_argument("--json", action="store_true", help="Format output as JSON")

    args = parser.parse_args()

    # Resolve account configuration
    try:
        account_cfg = get_account_config_by_email(args.account)
    except ValueError as e:
        if args.json:
            print(json.dumps({"success": False, "error": str(e)}))
        else:
            print(f"ERROR: {e}")
        sys.exit(1)

    runner = MailboxPilotRunner(db_path=args.db_path, account=account_cfg)

    if not args.command or args.command == "check":
        res = runner.validate_preflight()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Mailbox Pre-Flight Check (OFFLINE)")
            print("=" * 70)
            print(f"Target Account:     {res['email_address']} ({res['account_id']})")
            print(f"IMAP Endpoint:      {res['imap_host']}:{res['imap_port']} (TLS 993)")
            print(f"Windows DPAPI:      {'AVAILABLE' if res['dpapi_available'] else 'UNAVAILABLE'}")
            print(f"Credential Ref:     {res['credential_ref']}")
            print(f"Credential Stored:  {'YES' if res['credential_configured'] else 'NO (Pending operator setup)'}")
            print(f"Target SQLite DB:   {res['database_path']} (Exists: {res['database_exists']})")
            if res["errors"]:
                print("\nPre-Flight Warnings / Required Actions:")
                for e in res["errors"]:
                    print(f"  [!] {e}")
            print("=" * 70)
            print(f"Status:             {'READY FOR PILOT' if res['safety_passed'] else 'REQUIRES OPERATOR ACTION'}")
            print("=" * 70 + "\n")
        sys.exit(0 if res["safety_passed"] else 1)

    # Command: status (can be run offline or with --live-connect)
    if args.command == "status":
        res = runner.report_mailbox_status(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
            live_connect=getattr(args, "live_connect", False),
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Read-Only Mailbox Status Report (IMAP-BACKFILL-01A)")
            print("=" * 70)
            print(f"Target Account:     {res['email_address']} ({res['account_id']})")
            print(f"Primary Sales:      {'YES (Authorized for Historical Backfill)' if res['is_primary_sales_mailbox'] else 'NO (Secondary - Excluded from Historical Backfill)'}")
            print(f"Account Enabled:    {'YES' if res['is_enabled'] else 'NO (Disabled for unattended sync)'}")
            print("-" * 70)
            for role in ["inbox", "sent"]:
                f_info = res["folders"][role]
                print(f"Folder [{role.upper()}] ({f_info['folder_name']}):")
                print(f"  Stored Messages:      {f_info['stored_messages']}")
                print(f"  Stored UID Range:     {f_info['stored_min_uid']} -> {f_info['stored_max_uid']}")
                print(f"  Stored UIDVALIDITY:   {f_info['stored_uidvalidity']}")
                print(f"  Deferred (Oversized): {f_info['stored_deferred']}")
                print(f"  Sync Checkpoint:      UID {f_info['checkpoint_uid']} (UV: {f_info['checkpoint_uidvalidity']})")
                if getattr(args, "live_connect", False):
                    print(f"  Remote Message Count: {f_info['remote_count']}")
                    print(f"  Remote UID Range:     {f_info['remote_min_uid']} -> {f_info['remote_max_uid']}")
                    print(f"  Remote UIDVALIDITY:   {f_info['remote_uidvalidity']}")
                print("-" * 70)
            if "remote_inspection_error" in res:
                print(f"Remote Inspection Error: {res['remote_inspection_error']}")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 1)

    # Command: scheduler-status (offline status report)
    if args.command == "scheduler-status":
        sched_runner = ScheduledSyncRunner(mailbox_repo=runner.repo, credential_store=runner.cred_store)
        res = sched_runner.get_scheduler_status()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Scheduled Mailbox Synchronization Status (IMAP-AUTO-SYNC-01A)")
            print("=" * 70)
            sch = res["schedule"]
            print(f"Current Time (Rome):    {sch['current_time_rome']}")
            print(f"Current Time (UTC):     {sch['current_time_utc']}")
            print(f"Next Scheduled Window:  {sch['scheduled_window']}")
            print(f"Next Run Time (Rome):   {sch['next_scheduled_run_rome']}")
            print(f"Next Run Time (UTC):    {sch['next_scheduled_run_utc']}")
            print(f"Seconds Until Next Run: {sch['seconds_until_next_run']:.0f}s")
            print("-" * 70)
            lock = res["concurrency_lock"]
            print(f"Concurrency Lock:       {'ACTIVE' if lock['active'] else 'IDLE'} ({lock['lock_path']})")
            print("-" * 70)
            print("Authorized Primary Sales Mailboxes:")
            for acc_id, acc_info in res["authorized_accounts"].items():
                print(f"  Account: {acc_info['email']} ({acc_id})")
                print(f"    Registered in DB: {'YES' if acc_info.get('registered') else 'NO'}")
                print(f"    Enabled for Sync: {'YES (1)' if acc_info.get('is_enabled') else 'NO (0 - Unattended sync skipped)'}")
                folders = acc_info.get("folders", {})
                for role in ["inbox", "sent"]:
                    f_st = folders.get(role)
                    if f_st:
                        print(f"    [{role.upper()}] Checkpoint UID: {f_st['last_synced_uid']}, UV: {f_st['uidvalidity']}, Status: {f_st['last_status']}")
                    else:
                        print(f"    [{role.upper()}] Not initialized")
            print("-" * 70)
            print("Permitted Daily Schedules: " + ", ".join(res["permitted_operating_windows"]))
            print("=" * 70 + "\n")
        sys.exit(0)

    # All other commands beyond check, status, scheduler-status, activate-scheduled, and disable-scheduled require --live-connect
    if args.command not in ("check", "status", "scheduler-status", "activate-scheduled", "disable-scheduled") and not getattr(args, "live_connect", False):
        msg = "Refusing connection: Live connection commands require explicit '--live-connect' flag."
        if args.json:
            print(json.dumps({"success": False, "error": msg}))
        else:
            print(f"ERROR: {msg}")
        sys.exit(3)

    if args.command == "diagnose":
        res = runner.run_connection_diagnostics()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Read-Only IMAP Connection Diagnostics")
            print("=" * 70)
            print(f"Connection Success: {res['success']}")
            if not res["success"]:
                print(f"Error Category:     {res.get('error_category')}")
                print(f"Error Message:      {res.get('error_message')}")
            else:
                diag = res.get("diagnostics", {})
                print(f"TLS Host Verified:  {diag.get('tls_verified')}")
                print(f"Authenticated:      {diag.get('authenticated')}")
                print(f"Capabilities:       {', '.join(diag.get('capabilities', [])[:6])}...")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 2)

    elif args.command == "discover-folders":
        res = runner.discover_remote_folders()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Remote IMAP Folder Discovery")
            print("=" * 70)
            if not res["success"]:
                print(f"ERROR: {res.get('error')}")
            else:
                print(f"Discovered Folders ({res['total_folders']}):")
                for f in res["folders"]:
                    print(f"  - {f['folder_name']} (Role: {f['folder_role']}, Delimiter: '{f['folder_delimiter']}')")
                print("\nFolder Classification:")
                print(f"  Detected Inbox:    {res['detected_inbox']}")
                print(f"  Detected Sent:     {res['detected_sent']}")
                if res["ambiguous_sent"]:
                    print(f"  [!] WARNING: Ambiguous Sent folders found: {res['sent_candidates']}")
                    print("      Operator must explicitly supply --sent-folder for staging.")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 2)

    elif args.command == "preview":
        res = runner.preview_sample_metadata(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
            max_inbox=args.limit_inbox,
            max_sent=args.limit_sent,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Read-Only Message Metadata Preview (Zero DB Writes)")
            print("=" * 70)
            if not res["success"]:
                print(f"ERROR: {res.get('error')}")
            else:
                print(f"Inbox Messages Previewed ({len(res['inbox_preview'])}):")
                for m in res["inbox_preview"]:
                    print(f"  UID {m['uid']}: [{m['date_utc']}] {m['from']} -> {m['subject']} (Status: {m['parse_status']})")
                if res["sent_preview"]:
                    print(f"\nSent Messages Previewed ({len(res['sent_preview'])}):")
                    for m in res["sent_preview"]:
                        print(f"  UID {m['uid']}: [{m['date_utc']}] To: {m['to']} -> {m['subject']}")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 2)

    elif args.command == "stage-sample":
        if not args.confirm_pilot:
            msg = "Safety check: Sample staging requires explicit confirmation flag '--confirm-pilot'."
            if args.json:
                print(json.dumps({"success": False, "error": msg}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        res = runner.execute_pilot_sample_staging(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
            max_inbox=args.limit_inbox,
            max_sent=args.limit_sent,
            dry_run=args.dry_run,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print(f"pyBIM Sales-AI: Bounded Pilot Sample Staging ({'DRY-RUN' if args.dry_run else 'COMMITTED'})")
            print("=" * 70)
            if not res["success"]:
                print(f"ERROR: {res.get('error')}")
            else:
                inbox_s = res.get("inbox_sync", {})
                print(f"Inbox Staged:       {inbox_s.get('messages_ingested', 0)} message(s) (UID range: {inbox_s.get('starting_uid')} -> {inbox_s.get('ending_uid')})")
                print(f"Inbox Deferred:     {inbox_s.get('messages_deferred', 0)} oversized message(s)")
                sent_s = res.get("sent_sync")
                if sent_s:
                    print(f"Sent Staged:        {sent_s.get('messages_ingested', 0)} message(s) (UID range: {sent_s.get('starting_uid')} -> {sent_s.get('ending_uid')})")
                flags_stat = res.get("flag_preservation", {})
                print(f"Flag Preservation:  {'PASS (Zero remote flags mutated)' if flags_stat.get('verified') else 'FLAG DIVERGENCE DETECTED'}")
                print("Live CRM Safety:    Zero mutations to candidate_companies, candidate_contacts, or historical records.")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 2)

    elif args.command == "verify-messages":
        res = runner.verify_existing_pilot_messages(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Read-Only Verification of Existing Pilot Messages (IMAP-UV-REPAIR-01A)")
            print("=" * 70)
            if not res["success"]:
                print(f"ERROR: {res.get('error')}")
                print(f"Category: {res.get('error_category')}")
            else:
                print(f"Target Account:          {res['account']}")
                print(f"Actual INBOX UIDVALIDITY:      {res['actual_inbox_uidvalidity']}")
                print(f"Actual INBOX.Sent UIDVALIDITY: {res['actual_sent_uidvalidity']}")
                print("-" * 70)
                print(f"INBOX Messages (Stored: 10):")
                print(f"  Verified:             {res['inbox_verified_count']}")
                print(f"  Mismatched:           {res['inbox_mismatched_count']}")
                print(f"  Missing:              {res['inbox_missing_count']}")
                print(f"  Uncertain:            {res['inbox_uncertain_count']}")
                print(f"INBOX.Sent Messages (Stored: 10):")
                print(f"  Verified:             {res['sent_verified_count']}")
                print(f"  Mismatched:           {res['sent_mismatched_count']}")
                print(f"  Missing:              {res['sent_missing_count']}")
                print(f"  Uncertain:            {res['sent_uncertain_count']}")
                print("-" * 70)
                cp = res["checkpoint_consistency"]
                print(f"Checkpoint Consistency:  {'PASS (Consistent with max stored UIDs)' if cp['is_consistent'] else 'FAIL (Inconsistent checkpoints)'}")
                print(f"  INBOX: Checkpoint={cp['inbox_checkpoint_uid']}, Max Stored UID={cp['inbox_max_stored_uid']}")
                print(f"  Sent:  Checkpoint={cp['sent_checkpoint_uid']}, Max Stored UID={cp['sent_max_stored_uid']}")
                print("-" * 70)
                print(f"Safe Repair Eligibility: {'ELIGIBLE (100% verified, positive UIDVALIDITY, consistent checkpoints)' if res['safe_repair_eligible'] else 'NOT ELIGIBLE (Discrepancies detected)'}")
            print("=" * 70 + "\n")
        sys.exit(0 if res["success"] else 2)

    elif args.command == "repair-uidvalidity":
        if not args.live_connect:
            msg = "Repair requires live verification. Pass '--live-connect' to authorize remote IMAP connection."
            if args.json:
                print(json.dumps({"success": False, "error": msg, "error_category": "LIVE_CONNECT_REQUIRED"}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        if not args.dry_run and not args.confirm_repair:
            msg = "Safety check: Persistent repair requires explicit confirmation flag '--confirm-repair' (or use '--dry-run')."
            if args.json:
                print(json.dumps({"success": False, "error": msg, "error_category": "CONFIRMATION_REQUIRED"}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        res = runner.repair_pilot_uidvalidity(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
            dry_run=args.dry_run,
            backup_dir=args.backup_dir,
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            status_title = "DRY-RUN SIMULATION" if args.dry_run else ("ALREADY REPAIRED" if res.get("status") == "ALREADY_REPAIRED" else "COMMITTED REPAIR")
            print(f"pyBIM Sales-AI: Pilot UIDVALIDITY Safe Repair ({status_title}) (IMAP-UV-REPAIR-01B)")
            print("=" * 70)
            if not res.get("success"):
                print(f"ERROR: {res.get('error')}")
                print(f"Category: {res.get('error_category')}")
            else:
                print(f"Status:                 {res.get('status')}")
                print(f"Target Account:         {res.get('account')}")
                if "server_uidvalidity" in res:
                    uv = res["server_uidvalidity"]
                    print(f"Server UIDVALIDITY:     INBOX={uv.get('inbox')}, INBOX.Sent={uv.get('sent')}")
                elif "inbox_uidvalidity" in res:
                    print(f"Server UIDVALIDITY:     INBOX={res.get('inbox_uidvalidity')}, INBOX.Sent={res.get('sent_uidvalidity')}")

                if "backup" in res:
                    b = res["backup"]
                    print(f"Verified Backup:        {b.get('backup_path')}")
                    print(f"Backup SHA-256:         {b.get('sha256')}")
                    print(f"Integrity Check:        {b.get('integrity_status')}")

                if "rows_updated" in res:
                    up = res["rows_updated"]
                    print("-" * 70)
                    print("Updated Records:")
                    print(f"  Inbox Messages:       {up.get('inbox_messages')}")
                    print(f"  Sent Messages:        {up.get('sent_messages')}")
                    print(f"  Mailbox Folders:      {up.get('mailbox_folders')}")
                    print(f"  Folder Sync State:    {up.get('mailbox_sync_state')}")

                if "post_verification" in res:
                    pv = res["post_verification"]
                    print("-" * 70)
                    print("Post-Repair Assertions:")
                    print(f"  Inbox Messages (UV>0): {pv.get('inbox_repaired_count')}/10")
                    print(f"  Sent Messages (UV>0):  {pv.get('sent_repaired_count')}/10")
                    print(f"  Records with UV=0:     {pv.get('zero_uv_count')} (Must be 0)")
                    print(f"  CRM Links Preserved:   {pv.get('crm_links_preserved')}")
                    print(f"  Checkpoints:           INBOX={pv['checkpoints']['inbox']}, Sent={pv['checkpoints']['sent']}")
                    print(f"  Physical Duplicates:   {pv.get('duplicates')}")
            print("=" * 70 + "\n")

        sys.exit(0 if res.get("success") else 2)

    elif args.command == "backfill":
        if not args.live_connect:
            msg = "Backfill requires live connection. Pass '--live-connect' to authorize remote IMAP connection."
            if args.json:
                print(json.dumps({"success": False, "error": msg, "error_category": "LIVE_CONNECT_REQUIRED"}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        if not args.dry_run and not args.confirm_backfill:
            msg = "Safety check: Persistent backfill requires explicit confirmation flag '--confirm-backfill' (or use '--dry-run')."
            if args.json:
                print(json.dumps({"success": False, "error": msg, "error_category": "CONFIRMATION_REQUIRED"}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        res = runner.execute_historical_backfill(
            inbox_folder_name=args.inbox_folder,
            sent_folder_name=args.sent_folder,
            batch_size=args.limit_batch,
            dry_run=args.dry_run,
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            status_title = "DRY-RUN SIMULATION" if args.dry_run else "COMMITTED BATCH"
            print(f"pyBIM Sales-AI: Historical Mailbox Backfill ({status_title}) (IMAP-BACKFILL-01A)")
            print("=" * 70)
            if not res.get("success"):
                print(f"ERROR: {res.get('error')}")
                print(f"Category: {res.get('error_category')}")
            else:
                print(f"Target Account:         {res.get('account')} ({res.get('account_id')})")
                print(f"Batch Limit per Folder: {res.get('batch_size_limit')}")
                print(f"Unattended Sync:        {'DISABLED (Safety enforced)' if res.get('unattended_sync_disabled') else 'ENABLED'}")
                print("-" * 70)
                inbox_s = res.get("inbox_sync", {})
                print(f"Inbox Folder:           {inbox_s.get('folder_name')}")
                print(f"  Checkpoint Movement:  UID {inbox_s.get('starting_checkpoint')} -> {inbox_s.get('ending_checkpoint')}")
                print(f"  Messages Ingested:    {inbox_s.get('messages_ingested')}")
                print(f"  Deferred (Oversized): {inbox_s.get('messages_deferred')}")
                print(f"  Folder UIDVALIDITY:   {inbox_s.get('uidvalidity')}")
                print(f"  Sync Status:          {inbox_s.get('status')}")

                sent_s = res.get("sent_sync")
                if sent_s:
                    print("-" * 70)
                    print(f"Sent Folder:            {sent_s.get('folder_name')}")
                    print(f"  Checkpoint Movement:  UID {sent_s.get('starting_checkpoint')} -> {sent_s.get('ending_checkpoint')}")
                    print(f"  Messages Ingested:    {sent_s.get('messages_ingested')}")
                    print(f"  Deferred (Oversized): {sent_s.get('messages_deferred')}")
                    print(f"  Folder UIDVALIDITY:   {sent_s.get('uidvalidity')}")
                    print(f"  Sync Status:          {sent_s.get('status')}")
                print("-" * 70)
                print("Operational CRM Safety: Zero mutations to candidate_companies, candidate_contacts, or historical records.")
            print("=" * 70 + "\n")

        sys.exit(0 if res.get("success") else 2)

    elif args.command == "scheduler-status":
        sched_runner = ScheduledSyncRunner(mailbox_repo=runner.repo, credential_store=runner.credential_store)
        res = sched_runner.get_scheduler_status()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Scheduled Mailbox Synchronization Status (IMAP-AUTO-SYNC-01A)")
            print("=" * 70)
            sch = res["schedule"]
            print(f"Current Time (Rome):    {sch['current_time_rome']}")
            print(f"Current Time (UTC):     {sch['current_time_utc']}")
            print(f"Next Scheduled Window:  {sch['scheduled_window']}")
            print(f"Next Run Time (Rome):   {sch['next_scheduled_run_rome']}")
            print(f"Next Run Time (UTC):    {sch['next_scheduled_run_utc']}")
            print(f"Seconds Until Next Run: {sch['seconds_until_next_run']:.0f}s")
            print("-" * 70)
            lock = res["concurrency_lock"]
            print(f"Concurrency Lock:       {'ACTIVE' if lock['active'] else 'IDLE'} ({lock['lock_path']})")
            print("-" * 70)
            print("Authorized Primary Sales Mailboxes:")
            for acc_id, acc_info in res["authorized_accounts"].items():
                print(f"  Account: {acc_info['email']} ({acc_id})")
                print(f"    Registered in DB: {'YES' if acc_info.get('registered') else 'NO'}")
                print(f"    Enabled for Sync: {'YES (1)' if acc_info.get('is_enabled') else 'NO (0 - Unattended sync skipped)'}")
                folders = acc_info.get("folders", {})
                for role in ["inbox", "sent"]:
                    f_st = folders.get(role)
                    if f_st:
                        print(f"    [{role.upper()}] Checkpoint UID: {f_st['last_synced_uid']}, UV: {f_st['uidvalidity']}, Status: {f_st['last_status']}")
                    else:
                        print(f"    [{role.upper()}] Not initialized")
            print("-" * 70)
            print("Permitted Daily Schedules: " + ", ".join(res["permitted_operating_windows"]))
            print("=" * 70 + "\n")
        sys.exit(0)

    elif args.command == "scheduled-sync":
        if not args.live_connect:
            msg = "Scheduled synchronization requires live connection. Pass '--live-connect' to authorize remote IMAP connection."
            if args.json:
                print(json.dumps({"success": False, "error": msg, "error_category": "LIVE_CONNECT_REQUIRED"}))
            else:
                print(f"ERROR: {msg}")
            sys.exit(3)

        sched_runner = ScheduledSyncRunner(mailbox_repo=runner.repo, credential_store=runner.cred_store)
        res = sched_runner.execute_scheduled_sync(
            target_account_id=args.account if args.account != PILOT_EMAIL_ADDRESS else None,
            max_batches_per_folder=args.max_batches,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            is_manual_sync_now=args.sync_now,
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            mode_title = "MANUAL SYNC NOW" if args.sync_now else "UNATTENDED SCHEDULED"
            if args.dry_run:
                mode_title += " (DRY-RUN)"
            print(f"pyBIM Sales-AI: Mailbox Sync Engine ({mode_title}) (IMAP-AUTO-SYNC-01A)")
            print("=" * 70)
            print(f"Overall Status:          {res.get('status')}")
            print(f"Execution Mode:          {res.get('execution_mode')}")
            print(f"Total Messages Ingested: {res.get('total_messages_ingested')}")
            print(f"Total Deferred:          {res.get('total_messages_deferred')}")
            print("-" * 70)
            for acc_id, acc_res in res.get("accounts", {}).items():
                print(f"Account: {acc_id} ({acc_res.get('email_address')})")
                print(f"  Status: {acc_res.get('status')}")
                if "reason" in acc_res:
                    print(f"  Reason: {acc_res.get('reason')}")
                if "folders_synced" in acc_res:
                    for f_name, f_res in acc_res["folders_synced"].items():
                        print(f"  Folder {f_name}: Ingested={f_res.get('messages_ingested')}, StartingUID={f_res.get('starting_uid')}, EndingUID={f_res.get('ending_uid')}, UV={f_res.get('uidvalidity')}")
                if "error" in acc_res:
                    print(f"  Error: {acc_res.get('error')}")
            if res.get("errors"):
                print("-" * 70)
                print("Errors:")
                for err in res["errors"]:
                    print(f"  [!] {err}")
            print("=" * 70 + "\n")

        sys.exit(0 if res.get("status") in ["success", "skipped_disabled"] else 2)

    elif args.command == "activate-scheduled":
        target_accounts = ["acc_info_com", "acc_info_it"]
        if not args.confirm_activation:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Account Activation Check (IMAP-AUTO-SYNC-01B)")
            print("=" * 70)
            print("DRY-RUN / PRE-ACTIVATION VALIDATION:")
            print("The following PRIMARY SALES accounts will be enabled (is_enabled = 1):")
            for aid in target_accounts:
                acc = runner.repo.get_account(aid)
                if acc:
                    print(f"  [+] {aid}: {acc.email_address} (Current is_enabled: {acc.is_enabled})")
                else:
                    print(f"  [!] {aid}: NOT FOUND in database")
            print("\nSecondary accounts remain strictly disabled (is_enabled = 0).")
            print("To execute activation, re-run with '--confirm-activation'.")
            print("=" * 70 + "\n")
            sys.exit(0)

        # Validate accounts exist before updating
        updated = []
        for aid in target_accounts:
            acc = runner.repo.get_account(aid)
            if not acc:
                print(f"ERROR: Cannot activate account {aid} - not found in mailbox repository.")
                sys.exit(1)
            ok = runner.repo.set_account_enabled(aid, True)
            if ok:
                updated.append(aid)

        print("\n" + "=" * 70)
        print("pyBIM Sales-AI: Primary Mailboxes Activated (IMAP-AUTO-SYNC-01B)")
        print("=" * 70)
        for aid in updated:
            acc = runner.repo.get_account(aid)
            print(f"  [ENABLED] {aid} ({acc.email_address}) -> is_enabled={acc.is_enabled}")
        print("All secondary mailboxes remain disabled.")
        print("=" * 70 + "\n")
        sys.exit(0)

    elif args.command == "disable-scheduled":
        target_accounts = ["acc_info_com", "acc_info_it"]
        if not args.confirm_disable:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Account Disable Check (IMAP-AUTO-SYNC-01B)")
            print("=" * 70)
            print("DRY-RUN / PRE-DISABLE VALIDATION:")
            print("The following PRIMARY SALES accounts will be disabled (is_enabled = 0):")
            for aid in target_accounts:
                acc = runner.repo.get_account(aid)
                if acc:
                    print(f"  [-] {aid}: {acc.email_address} (Current is_enabled: {acc.is_enabled})")
                else:
                    print(f"  [!] {aid}: NOT FOUND in database")
            print("\nTo execute rollback/disable, re-run with '--confirm-disable'.")
            print("=" * 70 + "\n")
            sys.exit(0)

        updated = []
        for aid in target_accounts:
            acc = runner.repo.get_account(aid)
            if acc:
                runner.repo.set_account_enabled(aid, False)
                updated.append(aid)

        print("\n" + "=" * 70)
        print("pyBIM Sales-AI: Primary Mailboxes Disabled (IMAP-AUTO-SYNC-01B)")
        print("=" * 70)
        for aid in updated:
            acc = runner.repo.get_account(aid)
            print(f"  [DISABLED] {aid} ({acc.email_address}) -> is_enabled={acc.is_enabled}")
        print("Scheduled synchronization will skip these accounts.")
        print("=" * 70 + "\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
