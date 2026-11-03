"""
scripts/audit_repository.py
Non-destructive hygiene and safety auditor for SalesAI repository.
Reports root clutter, forbidden runtime artifacts, unexpected binaries,
large files, nested archives, secret-like filenames, and manifest validity.
"""

import sys
import io
import json
from pathlib import Path
from typing import List, Dict, Any

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ALLOWED_ROOT_FILES = {
    ".gitignore",
    "pyrefly.toml",
    "pyrightconfig.json",
    "pytest.ini",
    "requirements.txt",
    "README.md",
    "README-TEST.md",
    "run_sales_ai.bat",
    "start_server.py",
    "release_manifest.json",
    "repo_manifest.json",
}

FORBIDDEN_EXTENSIONS = {
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".wal",
    ".shm",
    ".pyc",
    ".pyo",
    ".pyd",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}

SENSITIVE_PATTERNS = {
    "client_secret",
    "credentials",
    "oauth",
    "token.json",
    "smtp_pass",
}


import subprocess
from typing import Set

def get_git_tracked_files(repo_root: Path) -> Set[str]:
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return {p.replace("\\", "/") for p in res.stdout.splitlines()}
    except Exception:
        return set()


def audit_repository(repo_root: Path) -> Dict[str, Any]:
    """Runs repository cleanliness and security checks."""
    repo_root = Path(repo_root).resolve()
    tracked_files = get_git_tracked_files(repo_root)

    violations: Dict[str, List[str]] = {
        "root_clutter": [],
        "forbidden_runtime_files": [],
        "unexpected_binaries": [],
        "large_files": [],
        "nested_zips": [],
        "secret_filenames": [],
        "orphaned_specs_in_root": [],
        "manifest_missing_roots": [],
    }

    # 1. Root Clutter & Orphaned Specs
    for item in repo_root.iterdir():
        if item.is_file():
            name = item.name
            if name.startswith(".env") or name == "cloudflared.exe" or name == "PUBLIC_URL.txt" or name == "candidates.db" or name.endswith(".zip"):
                # Excluded/ignored runtime files
                continue
            if name.startswith("PHASE") or name.startswith("PRODUCTION-STEP"):
                violations["orphaned_specs_in_root"].append(name)
            elif name not in ALLOWED_ROOT_FILES:
                violations["root_clutter"].append(name)

    # 2. Release Manifest Check
    manifest_path = repo_root / "release_manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            roots = data.get("profiles", {}).get("developer_source", {}).get("include_roots", [])
            for r in roots:
                target = repo_root / r
                if not target.exists():
                    violations["manifest_missing_roots"].append(r)
        except Exception as e:
            violations["manifest_missing_roots"].append(f"Manifest parse error: {e}")
    else:
        violations["manifest_missing_roots"].append("release_manifest.json missing")

    # 3. Scan source directories for forbidden files, unexpected binaries, large files
    scan_dirs = ["sales_engine", "backend", "ai_engine", "config", "data", "docs", "scripts", "tests", "static"]
    for d in scan_dirs:
        dir_path = repo_root / d
        if not dir_path.exists():
            continue
        for file_path in dir_path.rglob("*"):
            if not file_path.is_file():
                continue
            if "__pycache__" in file_path.parts or ".pytest_cache" in file_path.parts:
                continue
            rel = file_path.relative_to(repo_root).as_posix()
            lower_name = file_path.name.lower()
            lower_rel = rel.lower()

            # Forbidden extensions
            if file_path.suffix.lower() in FORBIDDEN_EXTENSIONS:
                # If it's a runtime database in data/ and not tracked in git, it is expected local runtime state
                if rel.startswith("data/") and file_path.suffix.lower() in (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"):
                    if rel in tracked_files:
                        violations["forbidden_runtime_files"].append(f"{rel} (tracked in git)")
                else:
                    violations["forbidden_runtime_files"].append(rel)

            # Unexpected binaries
            if file_path.suffix.lower() in (".exe", ".dll", ".so", ".dylib"):
                violations["unexpected_binaries"].append(rel)

            # Large files (> 10MB)
            try:
                if file_path.stat().st_size > 10 * 1024 * 1024:
                    violations["large_files"].append(f"{rel} ({file_path.stat().st_size // (1024*1024)}MB)")
            except Exception:
                pass

            # Nested ZIPs
            if file_path.suffix.lower() == ".zip":
                violations["nested_zips"].append(rel)

            # Secret-like filenames
            if any(p in lower_name for p in SENSITIVE_PATTERNS):
                if "golden" not in lower_rel and "report" not in lower_rel and "schema" not in lower_rel:
                    violations["secret_filenames"].append(rel)

    total_violations = sum(len(v) for v in violations.values())

    return {
        "repo_root": str(repo_root),
        "total_violations": total_violations,
        "violations": violations,
        "clean": total_violations == 0,
    }


def main():
    root = Path(__file__).parent.parent
    report = audit_repository(root)
    print("=" * 65)
    print("🔍 SalesAI Repository Hygiene & Safety Audit")
    print("=" * 65)
    print(f"Repository Root:    {report['repo_root']}")
    print(f"Total Violations:   {report['total_violations']}")
    print(f"Status:             {'✅ CLEAN' if report['clean'] else '❌ VIOLATIONS FOUND'}")
    print("-" * 65)

    for category, items in report["violations"].items():
        if items:
            print(f"⚠️  {category} ({len(items)}):")
            for item in items[:10]:
                print(f"    - {item}")
        else:
            print(f"✅  {category}: 0")

    print("=" * 65)
    sys.exit(0 if report["clean"] else 1)


if __name__ == "__main__":
    main()
