"""
scripts/rebuild_zip.py
Authoritative release ZIP packager for SalesAI.
Strictly excludes runtime state, databases, credentials, environments,
and temporary build/test artifacts with an explicit security denylist
and allowlist roots defined in release_manifest.json.
"""

import os
import json
import zipfile
from pathlib import Path
from typing import Set, List, Dict, Any, Optional

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
    "runtime",
    "logs",
    "research_snapshots",
    "benchmark_results",
}

# Dangerous file extensions (compiled artifacts, databases, private keys, certificates, logs, executables)
EXCLUDED_EXTENSIONS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".wal",
    ".shm",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pkcs12",
    ".crt",
    ".cer",
    ".log",
    ".zip",
    ".sha256",
    ".exe",
    ".xlsx",
    ".xlsm",
    ".xls",
}

# Explicit sensitive filenames or tokens to reject
SENSITIVE_FILENAME_SUBSTRINGS: Set[str] = {
    "sales_outreach.db",
    "research_cache.sqlite3",
    "chroma.sqlite3",
    "candidates.db",
    "client_secret",
    "credentials",
    "oauth",
    "token",
    "smtp_pass",
}


def load_manifest(source_dir: Path) -> Optional[Dict[str, Any]]:
    """Loads release_manifest.json if available."""
    manifest_path = source_dir / "release_manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


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

    # 4. Dangerous extensions (compiled binaries, databases, certs, private keys, archives, executables)
    for ext in EXCLUDED_EXTENSIONS:
        if lower_fname.endswith(ext):
            return True

    # 5. Runtime provider credentials & OAuth/token files
    if lower_fname.endswith((".json", ".txt", ".dat", ".bin", ".pickle", ".session", ".yaml", ".yml")):
        if any(sub in lower_fname for sub in SENSITIVE_FILENAME_SUBSTRINGS):
            # Preserve valid test golden files and test catalogs
            if "golden" not in lower_rel_str and "report" not in lower_rel_str and "schema" not in lower_rel_str:
                return True

    # 6. Runtime databases anywhere in tree
    if lower_fname.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")):
        return True

    # 7. Temporary generated test reports, browser screenshots, recordings
    if "tests/reports" in lower_rel_str or "test_reports" in lower_rel_str:
        return True
    if lower_fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".webm", ".mp4")):
        return True

    return False


def build_release_zip_stats(
    source_dir: Path,
    output_zip: Path,
    profile: str = "developer_source",
) -> Dict[str, Any]:
    """
    Builds the release ZIP deterministically, recording full packaging metrics.
    """
    source_dir = Path(source_dir).resolve()
    output_zip = Path(output_zip).resolve()

    manifest = load_manifest(source_dir)
    include_roots = None
    if manifest and "profiles" in manifest and profile in manifest["profiles"]:
        include_roots = manifest["profiles"][profile].get("include_roots")

    all_candidates: List[Path] = []
    excluded_files: List[str] = []
    denylist_violations: List[str] = []

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
            rel_str = rel_path.as_posix()

            # Denylist check
            if should_exclude(rel_path):
                excluded_files.append(rel_str)
                continue

            # Check allowlist include roots if specified
            if include_roots:
                included_by_root = False
                for r in include_roots:
                    if rel_str == r or rel_str.startswith(r.rstrip("/") + "/"):
                        included_by_root = True
                        break
                if not included_by_root:
                    excluded_files.append(rel_str)
                    continue

            # Ensure we never recursively package self or any zip file
            if full_path == output_zip or full_path.suffix.lower() == ".zip":
                excluded_files.append(rel_str)
                continue

            all_candidates.append(full_path)

    # Sort deterministically
    all_candidates.sort(key=lambda p: p.relative_to(source_dir).as_posix())

    # Create temporary zip or write directly
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for full_path in all_candidates:
            rel_str = full_path.relative_to(source_dir).as_posix()
            zf.write(full_path, rel_str)

    # Verify output zip
    with zipfile.ZipFile(output_zip, "r") as zf:
        packed_names = zf.namelist()
        for name in packed_names:
            if should_exclude(Path(name)):
                denylist_violations.append(name)

    stats = {
        "profile": profile,
        "output_path": str(output_zip),
        "included_count": len(all_candidates),
        "excluded_count": len(excluded_files),
        "denylist_violations_count": len(denylist_violations),
        "denylist_violations": denylist_violations,
    }
    return stats


def build_release_zip(
    source_dir: Path,
    output_zip: Path,
    profile: str = "developer_source",
) -> int:
    """
    Builds the clean release ZIP archive from source_dir to output_zip.
    Returns the count of packed files.
    """
    stats = build_release_zip_stats(source_dir, output_zip, profile=profile)
    return stats["included_count"]


if __name__ == "__main__":
    import hashlib
    src = Path(__file__).parent.parent
    dest = src.parent / "SalesAI_export.zip"
    stats = build_release_zip_stats(src, dest, profile="developer_source")

    # Compute SHA-256 of the finalized archive
    sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
    sidecar_dest = dest.with_suffix(".zip.sha256")
    sidecar_dest.write_text(f"{sha256} *SalesAI_export.zip\n", encoding="utf-8")

    print(f"Package profile:       {stats['profile']}")
    print(f"Included file count:   {stats['included_count']}")
    print(f"Excluded file count:   {stats['excluded_count']}")
    print(f"Denylist violations:   {stats['denylist_violations_count']}")
    print(f"Final SHA-256:         {sha256}")
    print(f"Sidecar created:       {sidecar_dest}")
    print(f"Successfully packaged {stats['included_count']} files into {dest}")
