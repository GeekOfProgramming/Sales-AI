"""
Allowlisted Source-Only Exporter for SalesAI.
Strictly exports tracked source files and documentation, strictly excluding:
- .env and credentials
- SQLite databases (*.db, *.sqlite*, *.sqlite3)
- Spreadsheets and workbooks (*.xlsx, *.xls, *.csv)
- Binaries, virtual environments, caches (__pycache__, .venv, .pytest_cache)
- Temporary log or output directories
"""

import os
import zipfile
import hashlib
import subprocess
from pathlib import Path


EXCLUDE_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".csv",
    ".pyc", ".pyo", ".pyd", ".exe", ".bin", ".png", ".jpg", ".jpeg",
    ".log", ".env"
}

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".idea", ".vscode", "tests/reports", "data"
}


def build_source_export(repo_root: Path, output_zip: Path) -> str:
    # Use git ls-files to guarantee we only package clean, tracked repository source files
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True
    )
    tracked_files = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]

    included_count = 0
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path in sorted(tracked_files):
            p = repo_root / rel_path
            suffix = p.suffix.lower()
            parts = Path(rel_path).parts

            # Check exclusions
            if suffix in EXCLUDE_EXTENSIONS:
                continue
            if any(part in EXCLUDE_DIRS for part in parts):
                continue
            if ".env" in p.name:
                continue

            if p.is_file():
                zf.write(p, arcname=rel_path)
                included_count += 1

    # Compute SHA-256
    sha256 = hashlib.sha256()
    with open(output_zip, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)

    digest = sha256.hexdigest()
    sidecar_file = Path(str(output_zip) + ".sha256")
    sidecar_file.write_text(digest, encoding="utf-8")
    print(f"Export created: {output_zip} ({included_count} files, SHA-256: {digest})")
    print(f"SHA-256 sidecar written: {sidecar_file}")
    return digest


if __name__ == "__main__":
    repo_dir = Path(__file__).resolve().parent.parent
    out_file = repo_dir / "SalesAI_source_export.zip"
    build_source_export(repo_dir, out_file)
