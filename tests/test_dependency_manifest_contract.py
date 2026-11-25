"""
tests/test_dependency_manifest_contract.py
Isolated dependency-manifest contract tests for LEGACY-09.

Verifies:
- `requirements.txt` parses cleanly as valid PEP 508 requirements.
- Retired legacy packages (`chromadb`, `langchain*`) remain absent from `requirements.txt`.
- Confirmed active direct dependencies (`PyJWT`, `beautifulsoup4`, `PyYAML`, `openpyxl`,
  `python-dateutil`, `jsonschema`, `scrapling`, `markdownify`, `ollama`, `httpx`, etc.)
  are explicitly declared.
- Every direct non-stdlib, non-local production import across `backend/` and `sales_engine/`
  maps to a declared distribution in `requirements.txt`.

Zero network calls, zero operational DB access, zero package mutations.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"

IMPORT_TO_DIST = {
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "jwt": "pyjwt",
    "yaml": "pyyaml",
}

LOCAL_TOP_LEVEL_PACKAGES = {"backend", "sales_engine", "scripts", "config", "tests", "start_server"}

RETIRED_DISTRIBUTIONS = {
    "chromadb",
    "langchain",
    "langchain-core",
    "langchain-ollama",
    "langchain-chroma",
    "langchain-community",
}

EXPECTED_DECLARED_DISTRIBUTIONS = {
    "ollama",
    "pydantic",
    "httpx",
    "fastapi",
    "uvicorn",
    "python-dotenv",
    "jinja2",
    "python-multipart",
    "pyjwt",
    "scrapling",
    "markdownify",
    "curl-cffi",
    "playwright",
    "patchright",
    "beautifulsoup4",
    "pyyaml",
    "openpyxl",
    "python-dateutil",
    "jsonschema",
    "pytest",
    "pytest-asyncio",
}


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _parse_requirements() -> list[tuple[str, str]]:
    """Parse `requirements.txt` via PEP 508 parser and return (normalized_name, raw_line) pairs."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement  # type: ignore[no-redef]

    parsed: list[tuple[str, str]] = []
    for raw in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        parsed.append((_normalize_dist_name(req.name), line))
    return parsed


def test_requirements_manifest_syntax_expected_set_and_no_retired_packages() -> None:
    """Verify `requirements.txt` parses cleanly, declares all 21 expected direct deps, and excludes retired ones."""
    parsed = _parse_requirements()
    names = [name for name, _ in parsed]
    assert len(names) == len(set(names)), f"Duplicate entries in requirements.txt: {names}"

    declared = set(names)
    assert declared == EXPECTED_DECLARED_DISTRIBUTIONS
    assert RETIRED_DISTRIBUTIONS.isdisjoint(declared), (
        f"Retired packages unexpectedly declared: {RETIRED_DISTRIBUTIONS & declared}"
    )

    # Active WebsiteFetcher imports `from scrapling import Fetcher`, which requires the `fetchers` extra
    scrapling_lines = [raw for name, raw in parsed if name == "scrapling"]
    assert len(scrapling_lines) == 1 and "[fetchers]" in scrapling_lines[0]


def test_all_direct_production_third_party_imports_are_declared_in_requirements() -> None:
    """Every non-stdlib, non-local top-level import in `backend/` and `sales_engine/` must be declared."""
    declared = {name for name, _ in _parse_requirements()}
    stdlib = sys.stdlib_module_names

    undeclared: dict[str, list[str]] = {}
    for root in ("backend", "sales_engine"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                top: str | None = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        _check_import(top, rel, node.lineno, stdlib, declared, undeclared)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top = node.module.split(".")[0]
                    _check_import(top, rel, node.lineno, stdlib, declared, undeclared)

    assert undeclared == {}, f"Undeclared direct third-party imports found: {undeclared}"


def _check_import(
    top: str,
    rel: str,
    lineno: int,
    stdlib: frozenset[str],
    declared: set[str],
    undeclared: dict[str, list[str]],
) -> None:
    if top in stdlib or top in LOCAL_TOP_LEVEL_PACKAGES:
        return
    dist = _normalize_dist_name(IMPORT_TO_DIST.get(top, top))
    if dist not in declared:
        undeclared.setdefault(f"{top} -> {dist}", []).append(f"{rel}:{lineno}")
