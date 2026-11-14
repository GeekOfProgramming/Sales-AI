#!/usr/bin/env python3
"""
Sales-AI Backup Manager CLI (BACKUP-01)
Usage:
    python scripts/backup_manager.py [options]
    python scripts/backup_manager.py manual
    python scripts/backup_manager.py automatic
    python scripts/backup_manager.py verify-restore --backup <path>
    python scripts/backup_manager.py schedule-task [--install | --remove | --status]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.candidates.backup_engine import (
    resolve_configured_db_path,
    create_sqlite_online_backup,
    verify_sqlite_backup_integrity,
    verify_restore_in_disposable_clone,
    BackupResult,
)

SCHEDULED_TASK_NAME = "SalesAI-DailyBackup"


def format_bytes(num_bytes: int) -> str:
    """Formats bytes into human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} TB"


def cmd_backup(mode: str, dest_dir: Path | None = None, json_output: bool = False) -> int:
    """Executes SQLite online backup and prints clear operator summary."""
    result = create_sqlite_online_backup(mode=mode, dest_dir=dest_dir)
    
    if json_output:
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.success else 1

    print("\n" + "=" * 70)
    print("Sales-AI Database Backup")
    print("=" * 70)
    print(f"Mode:             {mode.upper()}")
    print(f"Source Database:  {result.source_db_path}")
    print(f"Timestamp (UTC):  {result.timestamp_utc}")
    
    if result.success:
        print(f"Backup File:      {result.backup_path}")
        print(f"Backup Size:      {format_bytes(result.backup_size_bytes)} ({result.backup_size_bytes:,} bytes)")
        print(f"SHA-256 Checksum: {result.sha256_checksum}")
        print(f"Integrity Check:  {result.integrity_status}")
        print(f"Foreign Keys:     {result.fk_violations_count} violation(s)")
        if result.fk_violations:
            for v in result.fk_violations[:5]:
                print(f"  - {v}")
        print("Status:           SUCCESS")
        print("=" * 70 + "\n")
        return 0
    else:
        print(f"Integrity Status: {result.integrity_status}")
        print(f"Error Message:    {result.error_message}")
        print("Status:           FAILED")
        print("=" * 70 + "\n")
        return 1


def cmd_verify_restore(backup_path: Path, json_output: bool = False) -> int:
    """Executes isolated restore verification into disposable clone and compares counts."""
    print("\n" + "=" * 70)
    print("Sales-AI Backup Restore Verification (Disposable Clone)")
    print("=" * 70)
    print(f"Target Backup:    {backup_path}")
    
    restore_res = verify_restore_in_disposable_clone(backup_path)
    
    if json_output:
        print(json.dumps(restore_res, indent=2))
        return 0 if restore_res.get("success") else 1

    if not restore_res.get("success"):
        print(f"Status:           FAILED")
        print(f"Error:            {restore_res.get('error')}")
        print("=" * 70 + "\n")
        return 1

    print(f"Integrity Check:  {restore_res['integrity_status']}")
    print(f"Foreign Keys:     {restore_res['fk_violations_count']} violation(s)")
    print(f"Verified Tables:  {restore_res['verified_tables_count']}")
    print("-" * 70)
    print(f"{'Table Name':<35} {'Baseline':<12} {'Restored':<12} {'Match':<8}")
    print("-" * 70)
    
    baseline = restore_res["baseline_counts"]
    restored = restore_res["restored_counts"]
    for t in sorted(baseline.keys()):
        b_cnt = baseline.get(t, 0)
        r_cnt = restored.get(t, 0)
        match_str = "YES" if b_cnt == r_cnt else "MISMATCH"
        print(f"{t:<35} {b_cnt:<12} {r_cnt:<12} {match_str:<8}")
    
    print("-" * 70)
    print("Live DB Safety:   Untouched (data/sales_outreach.db preserved)")
    print("Status:           SUCCESS")
    print("=" * 70 + "\n")
    return 0


def cmd_schedule_task(action: str) -> int:
    """Manages Windows Task Scheduler task using PowerShell."""
    python_exe = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    script_path = REPO_ROOT / "scripts" / "backup_manager.py"

    if action == "status":
        ps_cmd = f"Get-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -ErrorAction SilentlyContinue | Select-Object TaskName, State"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.returncode == 0 and SCHEDULED_TASK_NAME in res.stdout:
            print(f"[OK] Task '{SCHEDULED_TASK_NAME}' is registered in Windows Task Scheduler.")
            print(res.stdout.strip())
            return 0
        else:
            print(f"[INFO] Task '{SCHEDULED_TASK_NAME}' is not currently registered.")
            return 1

    elif action == "remove":
        ps_cmd = f"Unregister-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -Confirm:$false -ErrorAction Stop"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[OK] Successfully removed scheduled task '{SCHEDULED_TASK_NAME}'.")
            return 0
        else:
            print(f"[ERROR] Failed to remove scheduled task: {res.stderr.strip()}")
            return 1

    elif action == "install":
        # Register task for daily at 20:00 local time
        # Under current Windows user account, runs without elevation, starts when available if missed
        ps_script = f"""
        $action = New-ScheduledTaskAction -Execute '{str(python_exe)}' -Argument 'scripts\\backup_manager.py --mode automatic' -WorkingDirectory '{str(REPO_ROOT)}'
        $trigger = New-ScheduledTaskTrigger -Daily -At '20:00'
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -Description 'Sales-AI CRM Automated Daily SQLite Backup' -ErrorAction Stop | Out-Null
        """
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[OK] Successfully registered Windows Task Scheduler task '{SCHEDULED_TASK_NAME}'.")
            print("  Schedule: Daily at 20:00 (Europe/Rome local time)")
            print("  Settings: StartWhenAvailable=True (runs missed backup on wake), MultipleInstances=IgnoreNew")
            print("  Security: Runs under current Windows user account without administrator elevation.")
            return 0
        else:
            print(f"[ERROR] Failed to register scheduled task: {res.stderr.strip()}")
            print("To manually install, run the following in PowerShell:")
            print(f"  Register-ScheduledTask -TaskName '{SCHEDULED_TASK_NAME}' -Action (New-ScheduledTaskAction -Execute '{python_exe}' -Argument 'scripts\\backup_manager.py --mode automatic' -WorkingDirectory '{REPO_ROOT}') -Trigger (New-ScheduledTaskTrigger -Daily -At '20:00') -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew)")
            return 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="Sales-AI Automated SQLite Backup and Restore Utility")
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # backup
    p_backup = subparsers.add_parser("backup", help="Create SQLite online backup")
    p_backup.add_argument("--mode", choices=["manual", "automatic"], default="manual", help="Backup mode (default: manual)")
    p_backup.add_argument("--dest", type=Path, default=None, help="Destination directory (optional)")
    p_backup.add_argument("--json", action="store_true", help="Output JSON result")

    # shortcuts for manual / automatic
    p_manual = subparsers.add_parser("manual", help="Create manual SQLite backup")
    p_manual.add_argument("--dest", type=Path, default=None, help="Destination directory (optional)")
    p_manual.add_argument("--json", action="store_true", help="Output JSON result")

    p_auto = subparsers.add_parser("automatic", help="Create automatic SQLite backup")
    p_auto.add_argument("--dest", type=Path, default=None, help="Destination directory (optional)")
    p_auto.add_argument("--json", action="store_true", help="Output JSON result")

    # verify-restore
    p_restore = subparsers.add_parser("verify-restore", help="Verify backup restore in disposable clone")
    p_restore.add_argument("--backup", type=Path, required=True, help="Path to backup .db file")
    p_restore.add_argument("--json", action="store_true", help="Output JSON result")

    # schedule-task
    p_sched = subparsers.add_parser("schedule-task", help="Manage Windows Task Scheduler daily backup")
    p_sched.add_argument("--action", choices=["install", "remove", "status"], default="status", help="Task action (install, remove, status)")

    # Legacy flags if invoked directly without subcommand
    parser.add_argument("--mode", choices=["manual", "automatic"], default="manual", help="Backup mode if no subcommand specified")
    parser.add_argument("--dest", type=Path, default=None, help="Destination directory")
    parser.add_argument("--json", action="store_true", help="Output JSON result")

    args = parser.parse_args()

    if args.subcommand == "manual":
        sys.exit(cmd_backup(mode="manual", dest_dir=args.dest, json_output=args.json))
    elif args.subcommand == "automatic":
        sys.exit(cmd_backup(mode="automatic", dest_dir=args.dest, json_output=args.json))
    elif args.subcommand == "backup":
        sys.exit(cmd_backup(mode=args.mode, dest_dir=args.dest, json_output=args.json))
    elif args.subcommand == "verify-restore":
        sys.exit(cmd_verify_restore(backup_path=args.backup, json_output=args.json))
    elif args.subcommand == "schedule-task":
        sys.exit(cmd_schedule_task(action=args.action))
    else:
        # Default behavior: run backup with provided flags
        sys.exit(cmd_backup(mode=args.mode, dest_dir=args.dest, json_output=args.json))


if __name__ == "__main__":
    main()
