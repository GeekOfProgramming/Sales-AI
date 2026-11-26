"""
tests/test_repository_audit_contract.py
Isolated synthetic filesystem contract tests for ARCH-01A (`scripts/audit_repository.py`).

Uses strictly synthetic temporary fixtures (`tmp_path`) — zero operational databases,
zero real credentials, zero backups, zero scans of the operational checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_repository import (
    ALLOWED_ROOT_FILES,
    APPROVED_CREDENTIAL_UTILITY_PATHS,
    audit_repository,
)
from scripts.rebuild_zip import should_exclude


def _build_synthetic_repo(root: Path, *, include_manifest_roots: bool = True) -> None:
    """Create a minimal clean synthetic repository tree with zero real project artifacts."""
    root.mkdir(parents=True, exist_ok=True)
    roots = [
        "sales_engine",
        "backend",
        "config",
        "data/rules",
        "static",
        "requirements.txt",
        "start_server.py",
        "run_sales_ai.bat",
    ]
    if include_manifest_roots:
        for item in roots:
            p = root / item
            if "." in p.name:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("# synthetic\n", encoding="utf-8")
            else:
                p.mkdir(parents=True, exist_ok=True)
                (p / "keep.txt").write_text("ok\n", encoding="utf-8")

    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "release_manifest.json").write_text(
        json.dumps({"profiles": {"developer_source": {"include_roots": roots}}}),
        encoding="utf-8",
    )


def test_legitimate_root_files_and_approved_credential_utility_pass_cleanly(tmp_path: Path) -> None:
    """Approved root files and exact credential CLI utility path do not trigger false positives."""
    _build_synthetic_repo(tmp_path)
    (tmp_path / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    (tmp_path / "backup_sales_ai.ps1").write_text("# synthetic backup script\n", encoding="utf-8")
    (tmp_path / "run_sales_ai.ps1").write_text("# synthetic launcher script\n", encoding="utf-8")
    (tmp_path / "scripts" / "manage_mailbox_credentials.py").write_text("# synthetic CLI\n", encoding="utf-8")

    assert {".gitattributes", "backup_sales_ai.ps1", "run_sales_ai.ps1"}.issubset(ALLOWED_ROOT_FILES)
    assert APPROVED_CREDENTIAL_UTILITY_PATHS == frozenset({"scripts/manage_mailbox_credentials.py"})

    report = audit_repository(tmp_path)
    assert report["clean"] is True, report["violations"]
    assert report["total_violations"] == 0


def test_unrelated_root_scripts_dotfiles_and_executables_remain_flagged(tmp_path: Path) -> None:
    """Allowing exact root launchers/gitattributes must not permit arbitrary scripts, dotfiles, or executables."""
    _build_synthetic_repo(tmp_path)
    (tmp_path / "rogue_helper.ps1").write_text("# bad\n", encoding="utf-8")
    (tmp_path / "unexpected_tool.exe").write_bytes(b"MZ")
    (tmp_path / ".custom_dotfile").write_text("x=1\n", encoding="utf-8")

    report = audit_repository(tmp_path)
    assert report["clean"] is False
    clutter = set(report["violations"]["root_clutter"])
    assert {"rogue_helper.ps1", "unexpected_tool.exe", ".custom_dotfile"}.issubset(clutter)


def test_credential_files_and_unapproved_credential_paths_remain_flagged(tmp_path: Path) -> None:
    """Synthetic credential files and identically named scripts outside the exact path stay flagged."""
    _build_synthetic_repo(tmp_path)
    (tmp_path / "scripts" / "other_credentials.py").write_text("# not exempted\n", encoding="utf-8")
    (tmp_path / "backend" / "manage_mailbox_credentials.py").write_text("# wrong directory\n", encoding="utf-8")
    (tmp_path / "config" / "smtp_pass.txt").write_text("dummy\n", encoding="utf-8")
    (tmp_path / "config" / "oauth_cache.json").write_text("{}\n", encoding="utf-8")

    report = audit_repository(tmp_path)
    assert report["clean"] is False
    flagged = set(report["violations"]["secret_filenames"])
    assert flagged == {
        "scripts/other_credentials.py",
        "backend/manage_mailbox_credentials.py",
        "config/smtp_pass.txt",
        "config/oauth_cache.json",
    }


def test_security_rules_databases_large_files_missing_roots_and_exclusions_preserved(tmp_path: Path) -> None:
    """Database, large-file, missing-root, and release-exclusion protections remain unweakened."""
    _build_synthetic_repo(tmp_path)
    # 1. Runtime DB outside data/ (e.g. backend/leak.db) -> forbidden_runtime_files
    (tmp_path / "backend" / "leak.db").write_bytes(b"SQLITE")
    # 2. Backup DB exceeding 10MB inside data/ -> large_files
    (tmp_path / "data" / "backups").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "backups" / "synthetic_backup.db").write_bytes(b"x" * (11 * 1024 * 1024))
    # 3. Missing manifest root
    (tmp_path / "config" / "keep.txt").unlink()
    (tmp_path / "config").rmdir()

    report = audit_repository(tmp_path)
    assert report["clean"] is False
    assert "backend/leak.db" in report["violations"]["forbidden_runtime_files"]
    assert any(item.startswith("data/backups/synthetic_backup.db") for item in report["violations"]["large_files"])
    assert "config" in report["violations"]["manifest_missing_roots"]

    # Release ZIP exclusions remain intact
    assert should_exclude(Path("data/sales_outreach.db")) is True
    assert should_exclude(Path("data/backups/backup.db")) is True
    assert should_exclude(Path("chroma_db/chroma.sqlite3")) is True
    assert should_exclude(Path("config/credentials.json")) is True
