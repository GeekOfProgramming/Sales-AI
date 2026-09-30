"""
scripts/rebuild_zip.py
Authoritative release ZIP packager for SalesAI.
Strictly excludes runtime state, databases, credentials, environments,
and temporary build/test artifacts with an explicit security denylist.
"""

import os
import zipfile
from pathlib import Path
from typing import Set

# Explicit security and runtime exclusion directory denylist
EXCLUDED_DIR_NAMES: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "chroma_db",
    "chroma_data",
}

# Dangerous file extensions (compiled artifacts, databases, private keys, certificates, logs)
EXCLUDED_EXTENSIONS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pkcs12",
    ".crt",
    ".cer",
    ".log",
}

# Explicit sensitive filenames or tokens to reject
SENSITIVE_FILENAME_SUBSTRINGS: Set[str] = {
    "sales_outreach.db",
    "chroma.sqlite3",
    "client_secret",
    "credentials",
    "oauth",
    "token",
    "smtp_pass",
}


def should_exclude(rel_path: Path) -> bool:
    """
    Checks if a relative path matches the security exclusion denylist.
    Preserves source-controlled non-sensitive test fixtures under tests/.
    """
    rel_str = rel_path.as_posix()
    parts = rel_path.parts
    fname = rel_path.name
    lower_fname = fname.lower()
    lower_rel_str = rel_str.lower()

    # 1. Check directory parts for excluded directories
    for part in parts[:-1]:
        lower_part = part.lower()
        if lower_part in EXCLUDED_DIR_NAMES:
            return True
        if lower_part.startswith((".git", ".venv", ".pytest_cache", "__pycache__", "chroma_db", "chroma_data")):
            return True
        if lower_part.startswith(".env") or lower_part == ".env":
            return True

    # 2. Check if the file is inside an excluded directory or is an excluded directory name
    if fname in EXCLUDED_DIR_NAMES:
        return True

    # 3. Environment files (.env, .env.*, *.env)
    if lower_fname == ".env" or lower_fname.startswith(".env.") or lower_fname.endswith(".env"):
        return True

    # 4. Dangerous extensions (compiled binaries, databases, certs, private keys)
    for ext in EXCLUDED_EXTENSIONS:
        if lower_fname.endswith(ext):
            return True

    # 5. Runtime provider credentials & OAuth/token files
    # E.g. token.json, credentials.json, oauth.txt, client_secrets.json
    # Only filter out credential / token files with sensitive extensions (json, txt, dat, bin, env, etc.)
    # Never filter valid python source code files (.py) under source or tests!
    if lower_fname.endswith((".json", ".txt", ".dat", ".bin", ".pickle", ".session", ".yaml", ".yml")):
        if any(sub in lower_fname for sub in SENSITIVE_FILENAME_SUBSTRINGS):
            # Preserve valid test golden files and test catalogs like tests/golden/phase8_outreach.json
            if "golden" not in lower_rel_str and "report" not in lower_rel_str and "schema" not in lower_rel_str:
                return True

    # 6. Runtime databases in data/ directory
    if lower_rel_str.startswith("data/") or "/data/" in lower_rel_str:
        if lower_fname.endswith((".db", ".sqlite", ".sqlite3")):
            return True

    return False


def build_release_zip(
    source_dir: Path,
    output_zip: Path,
) -> int:
    """
    Builds the clean release ZIP archive from source_dir to output_zip.
    Returns the count of packed files.
    """
    source_dir = Path(source_dir).resolve()
    output_zip = Path(output_zip).resolve()

    packed_count = 0
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            # Prune excluded directories in-place to avoid descending into them
            dirs[:] = [
                d
                for d in dirs
                if d not in EXCLUDED_DIR_NAMES
                and not any(
                    d.lower().startswith(p)
                    for p in [".git", ".venv", ".env", ".pytest_cache", "__pycache__", "chroma_db", "chroma_data"]
                )
            ]

            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(source_dir)

                if should_exclude(rel_path):
                    continue

                zf.write(full_path, rel_path.as_posix())
                packed_count += 1

    return packed_count


if __name__ == "__main__":
    src = Path(__file__).parent.parent
    dest = src.parent / "SalesAI_export.zip"
    count = build_release_zip(src, dest)
    print(f"Successfully packaged {count} files into {dest}")
