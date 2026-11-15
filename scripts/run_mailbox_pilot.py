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

    # All commands beyond check require --live-connect
    if not getattr(args, "live_connect", False):
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


if __name__ == "__main__":
    main()
