"""
scripts/manage_mailbox_credentials.py
Operator CLI tool to manage local Windows DPAPI encrypted mailbox credentials.

Usage:
  # Set a password for an account credential reference
  python scripts/manage_mailbox_credentials.py set --ref win_cred_acc_info_com

  # List configured credential references (never prints passwords)
  python scripts/manage_mailbox_credentials.py list

  # Delete a credential reference
  python scripts/manage_mailbox_credentials.py delete --ref win_cred_acc_info_com
"""

import sys
import getpass
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sales_engine.inbox.credential_store import MailboxCredentialStore, WindowsDPAPI


def main():
    parser = argparse.ArgumentParser(description="Sales-AI Mailbox Credential Manager (Windows DPAPI)")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    set_parser = subparsers.add_parser("set", help="Set or update a mailbox password")
    set_parser.add_argument("--ref", required=True, help="Credential reference key (e.g. win_cred_acc_info_com)")

    list_parser = subparsers.add_parser("list", help="List configured credential references")

    del_parser = subparsers.add_parser("delete", help="Delete a credential reference")
    del_parser.add_argument("--ref", required=True, help="Credential reference key to delete")

    args = parser.parse_args()

    store = MailboxCredentialStore()

    if args.command == "set":
        print(f"Provisioning credential for: {args.ref}")
        print(f"Windows DPAPI Available: {WindowsDPAPI.is_available()}")
        password = getpass.getpass("Enter mailbox password: ")
        confirm = getpass.getpass("Confirm mailbox password: ")
        if password != confirm:
            print("ERROR: Passwords do not match.")
            sys.exit(1)
        if not password:
            print("ERROR: Password cannot be empty.")
            sys.exit(1)

        store.set_credential(args.ref, password)
        print(f"SUCCESS: Secure credential stored for '{args.ref}' (DPAPI encrypted).")

    elif args.command == "list":
        refs = store.list_configured_refs()
        print(f"Configured Mailbox Credentials ({len(refs)}):")
        for r in refs:
            print(f"  - {r}: [Configured & Encrypted]")

    elif args.command == "delete":
        ok = store.delete_credential(args.ref)
        if ok:
            print(f"SUCCESS: Credential '{args.ref}' deleted.")
        else:
            print(f"WARNING: Credential '{args.ref}' not found.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
