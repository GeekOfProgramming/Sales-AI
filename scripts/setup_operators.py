#!/usr/bin/env python3
"""
Sales-AI Interactive Local Account Setup & Administration CLI (SEC-01).

Usage:
    python scripts/setup_operators.py [init-admin | create-user | list-users | deactivate-user]
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.operator_auth import get_auth_service, OperatorUser


def prompt_password(prompt_text: str = "Enter password: ") -> str:
    """Prompts securely for password without echo."""
    while True:
        p1 = getpass.getpass(prompt_text)
        if not p1 or len(p1) < 8:
            print("[ERROR] Password must be at least 8 characters long.")
            continue
        p2 = getpass.getpass("Confirm password: ")
        if p1 != p2:
            print("[ERROR] Passwords do not match. Please try again.")
            continue
        return p1


def cmd_init_admin():
    """Initializes the primary administrator account if no users exist."""
    svc = get_auth_service()
    count = svc.get_user_count()
    if count > 0:
        print(f"[INFO] System already has {count} user account(s) registered.")
        print("To create additional accounts, run: python scripts/setup_operators.py create-user")
        return 0

    print("=" * 70)
    print("Sales-AI Initial Administrator Account Setup (SEC-01)")
    print("=" * 70)
    print("Create the primary administrator account for this Sales-AI instance.")
    print("This operation must be run locally on the primary server laptop.\n")

    while True:
        username = input("Administrator username (min 3 characters): ").strip()
        if len(username) >= 3:
            break
        print("[ERROR] Username must be at least 3 characters.")

    full_name = input("Administrator full name (optional): ").strip() or None
    password = prompt_password("Administrator password (min 8 characters): ")

    try:
        user = svc.create_user(
            username=username,
            password=password,
            role="admin",
            full_name=full_name,
        )
        print("\n" + "=" * 70)
        print(f"✅ Administrator '{user.username}' created successfully!")
        print(f"Role:      ADMIN")
        print(f"User ID:   {user.user_id}")
        print("You can now log in at: http://127.0.0.1:8000/sales/login")
        print("=" * 70 + "\n")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to create administrator: {e}")
        return 1


def cmd_create_user():
    """Creates a new operator or admin user account."""
    svc = get_auth_service()
    print("=" * 70)
    print("Sales-AI User Account Creation")
    print("=" * 70)

    while True:
        username = input("Username: ").strip()
        if len(username) >= 3:
            if not svc.get_user_by_username(username):
                break
            print(f"[ERROR] User '{username}' already exists. Choose another.")
            continue
        print("[ERROR] Username must be at least 3 characters.")

    while True:
        role = input("Role (operator/admin) [operator]: ").strip().lower() or "operator"
        if role in ("operator", "admin"):
            break
        print("[ERROR] Role must be 'operator' or 'admin'.")

    full_name = input("Full Name (optional): ").strip() or None
    password = prompt_password(f"Password for '{username}': ")

    try:
        user = svc.create_user(
            username=username,
            password=password,
            role=role,
            full_name=full_name,
        )
        print("\n" + "=" * 70)
        print(f"✅ User '{user.username}' ({user.role.upper()}) created successfully!")
        print(f"User ID: {user.user_id}")
        print("=" * 70 + "\n")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to create user: {e}")
        return 1


def cmd_list_users():
    """Lists all registered operator and admin accounts."""
    svc = get_auth_service()
    users = svc.list_users()
    print("\n" + "=" * 70)
    print(f"Sales-AI Registered Accounts ({len(users)})")
    print("=" * 70)
    if not users:
        print("No user accounts found. Run: python scripts/setup_operators.py init-admin")
        print("=" * 70 + "\n")
        return 0

    print(f"{'Username':<15} {'Role':<10} {'Status':<10} {'Full Name':<20} {'Last Login':<22}")
    print("-" * 70)
    for u in users:
        status_str = "ACTIVE" if u.is_active else "DISABLED"
        last_login = u.last_login_at[:19] if u.last_login_at else "Never"
        fn = u.full_name or "-"
        print(f"{u.username:<15} {u.role.upper():<10} {status_str:<10} {fn:<20} {last_login:<22}")
    print("=" * 70 + "\n")
    return 0


def cmd_deactivate_user(username: Optional[str] = None):
    """Deactivates a user account and revokes all active sessions."""
    if not username:
        username = input("Username to deactivate: ").strip()
    if not username:
        print("[ERROR] Username required.")
        return 1
    svc = get_auth_service()
    try:
        ok = svc.set_user_active(username, is_active=False)
        if ok:
            print(f"[SUCCESS] User '{username}' deactivated and all active sessions revoked.")
            return 0
        else:
            print(f"[ERROR] User '{username}' not found.")
            return 1
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1


def cmd_activate_user(username: Optional[str] = None):
    """Activates a user account."""
    if not username:
        username = input("Username to activate: ").strip()
    if not username:
        print("[ERROR] Username required.")
        return 1
    svc = get_auth_service()
    try:
        ok = svc.set_user_active(username, is_active=True)
        if ok:
            print(f"[SUCCESS] User '{username}' activated.")
            return 0
        else:
            print(f"[ERROR] User '{username}' not found.")
            return 1
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1


def main():
    if len(sys.argv) < 2:
        # Default interactive choice
        print("Sales-AI User Management CLI (SEC-01)")
        print("Commands:")
        print("  1. init-admin      - Create initial primary administrator account")
        print("  2. create-user     - Create second operator or administrator account")
        print("  3. list-users      - Display registered operator accounts")
        print("  4. deactivate-user - Deactivate an operator account and revoke sessions")
        print("  5. activate-user   - Activate an operator account")
        choice = input("\nEnter choice (1/2/3/4/5) or command: ").strip()
        if choice in ("1", "init-admin"):
            sys.exit(cmd_init_admin())
        elif choice in ("2", "create-user"):
            sys.exit(cmd_create_user())
        elif choice in ("3", "list-users"):
            sys.exit(cmd_list_users())
        elif choice in ("4", "deactivate-user"):
            sys.exit(cmd_deactivate_user())
        elif choice in ("5", "activate-user"):
            sys.exit(cmd_activate_user())
        else:
            print("Invalid choice.")
            sys.exit(1)

    cmd = sys.argv[1].lower()
    arg_val = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd in ("init-admin", "init"):
        sys.exit(cmd_init_admin())
    elif cmd in ("create-user", "add-user"):
        sys.exit(cmd_create_user())
    elif cmd in ("list-users", "list"):
        sys.exit(cmd_list_users())
    elif cmd in ("deactivate-user", "deactivate"):
        sys.exit(cmd_deactivate_user(arg_val))
    elif cmd in ("activate-user", "activate"):
        sys.exit(cmd_activate_user(arg_val))
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python scripts/setup_operators.py [init-admin | create-user | list-users | deactivate-user | activate-user]")
        sys.exit(1)


if __name__ == "__main__":
    main()
