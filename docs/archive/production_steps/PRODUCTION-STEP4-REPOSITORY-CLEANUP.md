# SalesAI Production Readiness — Step 4
## Repository Cleanup & Release Structure
### Safe Cleanup Without Breaking Frozen Phase 1–12

# 0. Baseline

Use the current repository after accepted:

```text
Step 1 — Search Scope & Discovery Control
Step 2 — Source Management
Step 3 — Discovery Control UI / Search Profiles
```

Frozen / accepted functional baseline:

```text
Phase 1–12
```

Current repository regression baseline:

```text
870 passed
7 skipped
0 failed
```

Do NOT redesign or refactor working business logic in this step.

This step is cleanup, organization, release hygiene, and maintainability.

---

# 1. Primary Goal

Transform the current development repository from:

```text
working historical project with many phase specs,
patch documents, legacy pyBIM files, root test scripts,
development artifacts and release artifacts
```

into:

```text
clean source repository
+
clear production runtime
+
clear development/test area
+
archived historical specifications
+
deterministic release package
```

while preserving all working functionality.

---

# 2. Core Safety Rule

Do NOT delete a file merely because it "looks old".

Before removing or relocating any executable/source file:

```text
1. search imports/references
2. search tests
3. search startup/install scripts
4. check FastAPI/router registration
5. check runtime file access
6. check GitHub/documentation references
```

If uncertain:

```text
KEEP
or
ARCHIVE / EXCLUDE FROM RELEASE
```

rather than delete.

---

# 3. Repository Classification

Classify every current file into one of:

```text
A. production_runtime
B. production_config
C. frozen_legacy_required
D. tests_and_qa
E. developer_tools
F. documentation_active
G. documentation_archive
H. sanitized_examples
I. generated_runtime_excluded
J. release_excluded
```

Create a machine-readable inventory, for example:

```text
repo_manifest.json
```

and a human-readable:

```text
docs/REPOSITORY-STRUCTURE.md
```

---

# 4. Current Root Cleanup

The current root contains many historical Phase specs and patch documents.

Move historical build/QA/patch docs from repository root into a structured archive.

Recommended:

```text
docs/
├── architecture/
├── operations/
├── development/
├── deployment/
└── archive/
    ├── phase_specs/
    ├── qa_specs/
    ├── adversarial_patches/
    └── production_steps/
```

Examples that should no longer clutter repository root:

```text
PHASE3-QA-GOLDEN-SPEC.md
PHASE4-QA-SCAFFOLD.md
...
PHASE12-QA-GOLDEN-SPEC.md

PHASE9-ADVERSARIAL-SEND-PATCH.md
PHASE10-ADVERSARIAL-PATCH.md
PHASE10.5-FINAL-PRE-QA-MICRO-PATCH.md

PRODUCTION-STEP2-SOURCE-MANAGEMENT.md
PRODUCTION-STEP3-DISCOVERY-CONTROL-UI.md
```

Preserve them in Git history / archive.
Do NOT silently delete the historical QA trail.

---

# 5. Root Files That Should Remain Minimal

Target root should contain only a small number of entry-point/project files, such as:

```text
README.md
README-TEST.md
requirements.txt
pytest.ini
pyrightconfig.json
pyrefly.toml
.gitignore

start_server.py

backend/
sales_engine/
ai_engine/
config/
data/
scripts/
setup/
tests/
docs/
static/
```

Final exact structure may follow existing repo conventions.

---

# 6. Legacy pyBIM Assets

Current repository still includes older pyBIM/Revit assets such as:

```text
revit_plugin/
data/rules/*Revit/pyRevit references*
run_pybim.bat
stop_pybim.bat
ingest_doc.py
legacy root test scripts
```

DO NOT delete these blindly.

Create a reference audit.

If still required by frozen functionality/tests:

```text
keep them
```

If not required by SalesAI production runtime but useful historically:

move/organize under:

```text
legacy/pybim/
```

or clearly classify as:

```text
frozen_legacy_required
```

Important:
Moving Python modules can break imports.
Only move executable modules if references/imports are updated and full tests stay green.

When relocation creates unnecessary risk, leave code in place and exclude it only from
the SalesAI production release manifest.

---

# 7. cloudflared / Public Tunnel Assets

Current root contains:

```text
cloudflared.exe
PUBLIC_URL.txt
```

SalesAI production target is localhost by default.

Audit whether these are referenced by any active SalesAI runtime/startup code.

If not required:

```text
exclude from SalesAI production release
```

Do not make Cloudflare tunnel part of default production setup.

If retained for legacy pyBIM:

classify under:

```text
legacy / optional tooling
```

No public tunnel should start automatically.

---

# 8. Root-Level Old Test Scripts

Current root contains files such as:

```text
test_backend.py
test_crew_orchestrator.py
test_inference.py
test_ingestion.py
test_rag.py
test_revit_bridge.py
test_web_ui.py
```

Audit each one.

If they are valid pytest tests and still useful:

move carefully into:

```text
tests/legacy/
```

only after verifying imports/path assumptions.

If they are obsolete/manual diagnostics:

move to:

```text
scripts/legacy_diagnostics/
```

or archive.

Do not simply delete without evidence.

---

# 9. Test Suite Preservation

All official current tests under `tests/` remain source-controlled.

Do NOT remove:

```text
Phase 1–12 regression tests
Golden tests
Production Step 1–3 tests
Acceptance tests
QA-integrity tests
```

They are part of the frozen behavioral contract.

Production release packaging may optionally exclude selected heavy QA documents,
but source repository must retain tests.

---

# 10. Test Reports

Organize generated/static reports.

Recommended:

```text
tests/reports/
```

Current authoritative latest reports may stay.

Historical generated report artifacts that are redundant may be archived or regenerated.

Do NOT delete a report used by QA-integrity tests without updating the contract.

---

# 11. Sanitized Sample Exports

Current repository contains:

```text
exports/sample_export/
```

These may remain only if confirmed sanitized.

Verify:

```text
no real emails
no real names if sensitive
no tokens
no credentials
no customer-confidential data
```

If truly sample fixtures:

classify as:

```text
sanitized_examples
```

Otherwise remove from release and replace with synthetic examples.

---

# 12. data/ Directory

Audit:

```text
data/pending_ingestions.json
data/rules/
```

Distinguish:

```text
static reference data
vs
runtime state
```

Runtime state must not be committed or packaged as source data.

If `pending_ingestions.json` is runtime-generated:

move runtime behavior toward:

```text
runtime/
```

or exclude it from Git/release as appropriate.

Do not change legacy behavior without tests.

---

# 13. Setup Scripts

Current:

```text
setup/full_install.bat
setup/install_requirements.bat
setup/pull_models.bat
```

Do NOT redesign installer yet.

That is a later Production Readiness step.

For Step 4:

```text
inventory
document
keep
```

Mark obsolete assumptions/TODOs for later installer work.

No broad installation rewrite yet.

---

# 14. Run Scripts

Current root includes:

```text
run_sales_ai.bat
run_pybim.bat
stop_pybim.bat
```

For this step:

- identify which is current SalesAI runtime
- identify which is legacy pyBIM runtime
- document them
- remove duplicate/obsolete root entry points only if proven safe

Do NOT create the final START/STOP installer ecosystem yet.
That belongs to later steps.

---

# 15. Runtime Data Boundary

Define one explicit future runtime-data location, for example:

```text
runtime/
```

or existing project convention.

Runtime data includes:

```text
SQLite databases
Chroma DB
logs
tokens
OAuth state
temporary exports
health state
cache
```

Do NOT package runtime state in release ZIP.

If moving actual runtime paths now risks regressions:

document the target boundary but defer physical migration until installer/runtime step.

---

# 16. .gitignore Cleanup

Ensure ignore coverage for:

```text
.env
.env.*
*.db
*.sqlite
*.sqlite3
*.db-wal
*.db-shm

__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/

.venv/
venv/

logs/
*.log

token.json
credentials*.json
client_secret*.json

chroma_db/
runtime/

temporary ZIPs
temporary export outputs
```

Be careful not to ignore intentionally committed sanitized fixtures.

---

# 17. Release Manifest

Create an explicit release allowlist/manifest.

Recommended:

```text
release_manifest.json
```

or equivalent Python data structure used by `scripts/rebuild_zip.py`.

Release packaging should no longer depend only on:

```text
"include everything except denylist"
```

Preferred:

```text
known-safe include roots
+
denylist defense-in-depth
```

Example include roots:

```text
backend/
sales_engine/
ai_engine/
config/
static/
scripts/ required runtime scripts
setup/ currently required
docs/ selected production docs
requirements.txt
README.md
README-TEST.md
start_server.py
run_sales_ai.bat
```

Tests may remain included in development/source release depending on current packaging strategy.

---

# 18. Release Profiles

Recommended distinction:

```text
SOURCE / DEVELOPER PACKAGE
PRODUCTION RUNTIME PACKAGE
```

For Step 4, implement at least the structure/manifest to support this distinction.

It is acceptable for current ZIP to remain a developer/source package for now if clearly
named/documented.

Do not accidentally remove tests before the installer/deployment phase is complete.

---

# 19. rebuild_zip.py

Harden `scripts/rebuild_zip.py`.

Required:

```text
deterministic file collection
stable exclusions
denylist
no runtime DB
no secrets
no caches
no nested generated ZIP
```

Output should print:

```text
included file count
excluded file count
denylist violations
package type/profile
```

---

# 20. Nested ZIP / Release Recursion

Ensure:

```text
SalesAI_export.zip
```

is never packaged inside itself or nested copied release artifacts.

Exclude:

```text
*.zip
```

from source traversal unless explicitly allowlisted.

---

# 21. Binary Policy

Inventory committed binaries.

Examples:

```text
cloudflared.exe
```

Production release should not contain unrelated executables.

Document any intentionally shipped binary with:

```text
purpose
source
version
license
checksum if appropriate
```

Do not redistribute unneeded third-party binaries.

---

# 22. Active Documentation

Do not write the final GitHub README in Step 4.

But create minimal structural docs:

```text
docs/REPOSITORY-STRUCTURE.md
docs/LEGACY-COMPONENTS.md
docs/RELEASE-CONTENTS.md
```

These become inputs for later documentation steps.

---

# 23. Deprecated / Archived Marker

For archived historical docs/components, clearly mark:

```text
ARCHIVED
NOT CURRENT IMPLEMENTATION SPEC
```

Avoid future AI agents mistaking old patch files for current instructions.

---

# 24. Agent Context Safety

Create:

```text
docs/archive/README.md
```

stating:

```text
Files in this folder are historical records.
Do not treat them as the current implementation contract.
Current runtime behavior is defined by code + current tests.
```

This is especially important for Antigravity/AI tooling.

---

# 25. No Business Logic Refactor

This Step must NOT:

```text
rewrite Discovery
rewrite SourceRegistry
rewrite scoring
rewrite send workflow
rewrite inbox
rewrite analytics
change APIs for style only
rename authoritative IDs
```

Cleanup is not an excuse for architecture churn.

---

# 26. Cleanup Audit Script

Create:

```text
scripts/audit_repository.py
```

It should report:

```text
root clutter
forbidden/runtime files
duplicate filenames of concern
unexpected binaries
large files
nested ZIPs
secret-like filenames
orphaned historical specs
release-manifest mismatches
```

No destructive actions.

---

# 27. Reference Audit

Before moving executable code/scripts, use static/reference searches.

Create a report such as:

```text
tests/reports/repository_cleanup_latest.json
```

Suggested fields:

```text
files_before
files_after

root_files_before
root_files_after

archived_docs_count
moved_legacy_count
deleted_files_count

unresolved_reference_count
unexpected_binary_count

release_file_count
denylist_violation_count

full_test_passed
```

---

# 28. Delete Policy

Actual deletion is allowed only for files proven to be:

```text
generated garbage
cache
duplicate generated archive
temporary runtime artifact
obsolete exact duplicate
```

For historical design/QA documentation:

```text
archive rather than delete
```

For uncertain code:

```text
keep
```

---

# 29. Git History

Use normal Git moves where possible so history is understandable.

Do not squash historical implementation history as part of this cleanup.

---

# 30. Required Tests — Focused Cleanup

Create:

```text
tests/test_repository_cleanup.py
```

Cover at least:

```text
PRD-CLEAN-001 release manifest contains required runtime modules
PRD-CLEAN-002 release excludes runtime DB files
PRD-CLEAN-003 release excludes .env and secret-like credential files
PRD-CLEAN-004 release excludes cache/compiled artifacts
PRD-CLEAN-005 release excludes nested ZIP artifacts
PRD-CLEAN-006 archived phase docs are not in repository root
PRD-CLEAN-007 historical archive has explicit archived warning
PRD-CLEAN-008 active README paths remain valid
PRD-CLEAN-009 SalesAI startup entrypoint remains present
PRD-CLEAN-010 frozen test suites remain present
PRD-CLEAN-011 Step1/2/3 tests remain present
PRD-CLEAN-012 source config remains present
PRD-CLEAN-013 UI templates/static assets remain present
PRD-CLEAN-014 no unresolved import caused by relocation
PRD-CLEAN-015 no unexpected production binary in release
PRD-CLEAN-016 sample exports contain no obvious secret patterns
PRD-CLEAN-017 rebuild_zip output is deterministic
PRD-CLEAN-018 release does not recursively include itself
PRD-CLEAN-019 repository audit script reports zero critical hygiene violations
PRD-CLEAN-020 cleanup does not change business behavior
```

---

# 31. Regression

Run:

```text
python -m pytest tests/test_repository_cleanup.py -v

python -m pytest tests/test_search_scope.py -v
python -m pytest tests/test_source_management.py -v
python -m pytest tests/test_discovery_control_ui.py -v

python -m pytest tests/ -q
```

Require:

```text
0 new deterministic failures
```

---

# 32. Output ZIP

Rebuild final ZIP after cleanup.

Report:

```text
files before cleanup
files after cleanup
release ZIP file count
denylist violations
archived files count
deleted files count
moved files count
```

A lower file count is NOT itself a success metric.

Safety and clarity matter more.

---

# 33. Completion Gate

Step 4 is accepted when:

```text
repository root is materially cleaner
historical phase/patch docs are archived
archive is clearly marked non-authoritative
legacy pyBIM components are classified
no active frozen code is accidentally removed
runtime/generated artifacts are clearly excluded
release manifest exists
release builder is deterministic
nested releases are excluded
no unneeded binary ships by default
Step 1–3 remain green
Phase 1–12 remain green
release denylist is clean
```

Do NOT proceed to Hamed laptop hardware audit yet.

Return ZIP for review first.

---

# 34. Final Response

Return:

1. before/after repository structure
2. files moved
3. files archived
4. files deleted and exact justification
5. legacy components retained and why
6. root files remaining
7. release manifest/profile
8. rebuild_zip changes
9. repository audit result
10. focused cleanup tests
11. Step 1/2/3 regression results
12. full repository result
13. Git commit
14. ZIP file count
15. denylist result
16. final SalesAI_export.zip
