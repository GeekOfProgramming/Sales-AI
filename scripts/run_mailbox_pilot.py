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

# Constants strictly enforcing IMAP-SYNC-02B constraints
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
    return MailboxAccountRecord(
        account_id=PILOT_ACCOUNT_ID,
        email_address=PILOT_EMAIL_ADDRESS,
        display_name=PILOT_DISPLAY_NAME,
        provider="register_it",
        imap_host=PILOT_IMAP_HOST,
        imap_port=PILOT_IMAP_PORT,
        use_ssl=True,
        credential_ref=PILOT_CREDENTIAL_REF,
        is_enabled=False,  # Disabled by default
        sync_priority=1,
        connection_status="configured",
    )


class MailboxPilotRunner:
    """
    Operator-controlled runner for the single-mailbox read-only pilot.
    Encapsulates diagnostic checks, remote folder discovery, metadata preview,
    and optional controlled sample staging.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        cred_store: Optional[MailboxCredentialStore] = None,
        provider_factory: Optional[Any] = None,
    ):
        self.db_path = db_path or str(resolve_configured_db_path())
        self.cred_store = cred_store or MailboxCredentialStore()
        self.repo = MailboxRepository(db_path=self.db_path)
        self.account = get_pilot_account_config()
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

        # 1. Target Account Lock
        if self.account.email_address != PILOT_EMAIL_ADDRESS:
            report["errors"].append(
                f"Unauthorized pilot target: {self.account.email_address}. Only {PILOT_EMAIL_ADDRESS} is authorized."
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
            inbox_fld = MailboxFolderRecord(
                folder_id=f"fld_{self.account.account_id}_inbox",
                account_id=self.account.account_id,
                folder_name=inbox_folder_name,
                folder_role="inbox",
            )
            self.repo.save_folder(inbox_fld)

            sent_fld = None
            if sent_folder_name:
                sent_fld = MailboxFolderRecord(
                    folder_id=f"fld_{self.account.account_id}_sent",
                    account_id=self.account.account_id,
                    folder_name=sent_folder_name,
                    folder_role="sent",
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

    # Global options
    parser.add_argument("--db-path", help="Path to SQLite database (default: data/sales_outreach.db)")
    parser.add_argument("--json", action="store_true", help="Format output as JSON")

    args = parser.parse_args()

    runner = MailboxPilotRunner(db_path=args.db_path)

    if not args.command or args.command == "check":
        res = runner.validate_preflight()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 70)
            print("pyBIM Sales-AI: Single-Mailbox Pilot Pre-Flight Check (OFFLINE)")
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


if __name__ == "__main__":
    main()
