# SalesAI — S0: Safe Legacy pyBIM-LLM Retirement Technical Report

**Status:** Completed  
**Date:** 2026-10-21  
**Working Repository:** `C:\Sales-AI\SalesAI`  
**Dedicated Branch:** `chore/salesai-legacy-retirement`  
**Base Commit (main):** `5757438ff10829640a0d9b25f1b240852b62f9fe`  
**Specification:** `docs/cleanup/SALESAI-S0-LEGACY-PYBIM-LLM-RETIREMENT.md`  

---

## 1. Executive Summary & Objective

The objective of **SalesAI Stage S0** was to safely and provably retire obsolete components of the original pyBIM-LLM desktop/workstation and Revit plugin product, while:
1. Retaining 100% of the SalesAI business logic (Phases 1–12), authority gates, suppression lists, scoring algorithms, and accepted Production Steps 1–6 records.
2. Preserving the local Sales Operator Console (`/sales`), search scopes, and source management interfaces.
3. Eliminating unintended Ollama auto-starts, background model downloads, and unconstrained process termination in launchers.
4. Ensuring zero regression against the baseline test suite.
5. Establishing a clean foundation for the **new target architecture**: a lightweight developer laptop running FastAPI, local SQLite/JSON datastores, the Sales Operator Console, and external LLM APIs (planned for Stage S1).

---

## 2. Pre-Modification Safety Verification

Before modifying any repository assets, the following checks were executed:
- **Git Status:** Verified clean working tree on `main` at commit `5757438` (Step 6 completion).
- **Branch Creation:** Dedicated local cleanup branch `chore/salesai-legacy-retirement` was created and checked out (`git checkout -b chore/salesai-legacy-retirement`).
- **Baseline Test Suite Execution:** The full pytest suite was executed against the active environment prior to changes:
  - **Result:** `929 passed, 8 skipped, 6 warnings in 268.61s (0:04:28)`
- **Inventory Matrix Produced:** Formatted as `docs/cleanup/S0-PYBIM-LLM-INVENTORY.md` and machine-readable `docs/cleanup/s0_file_decisions.json`.

---

## 3. Inventory & Exact Removed Paths

A total of **31 provably isolated legacy files** were removed using tracked `git rm`. No shared or active SalesAI files were deleted.

### Complete List of 31 Deleted Files:

#### A. Legacy Revit Workstation Plugin (`revit_plugin/` — 14 files)
1. `revit_plugin/Commands.cs` — C# Revit API command implementation
2. `revit_plugin/install_pyrevit_extension.bat` — pyRevit extension installer
3. `revit_plugin/pyBIM.addin` — Autodesk Revit XML addin manifest
4. `revit_plugin/pyRevitScript.py` — IronPython ribbon button execution bridge
5. `revit_plugin/pyBIM.extension/extension.json` — pyRevit extension manifest
6. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Assistant.pushbutton/bundle.yaml`
7. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Assistant.pushbutton/icon.png`
8. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Assistant.pushbutton/script.py`
9. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Config.pushbutton/bundle.yaml`
10. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Config.pushbutton/icon.png`
11. `revit_plugin/pyBIM.extension/pyBIM.tab/AI Automation.panel/Config.pushbutton/script.py`
12. `revit_plugin/pyBIM.extension/pyBIM.tab/Studio.panel/OpenStudio.pushbutton/bundle.yaml`
13. `revit_plugin/pyBIM.extension/pyBIM.tab/Studio.panel/OpenStudio.pushbutton/icon.png`
14. `revit_plugin/pyBIM.extension/pyBIM.tab/Studio.panel/OpenStudio.pushbutton/script.py`
*Justification:* These files belong strictly to the Autodesk Revit desktop plugin. None are imported by SalesAI backend, sales engine, or automated tests.

#### B. Legacy Studio Server Launchers (2 files)
15. `run_pybim.bat` — Old pyBIM workstation server launcher (auto-started Ollama, opened `/ui`, killed PID on 8000).
16. `stop_pybim.bat` — Old pyBIM unconstrained taskkill script.
*Justification:* Obsolete; superseded by `run_sales_ai.bat` and standard graceful server termination.

#### C. Legacy Diagnostic Scripts (`scripts/legacy_diagnostics/` — 8 files)
17. `scripts/legacy_diagnostics/ingest_doc.py`
18. `scripts/legacy_diagnostics/test_backend.py`
19. `scripts/legacy_diagnostics/test_crew_orchestrator.py`
20. `scripts/legacy_diagnostics/test_inference.py`
21. `scripts/legacy_diagnostics/test_ingestion.py`
22. `scripts/legacy_diagnostics/test_rag.py`
23. `scripts/legacy_diagnostics/test_revit_bridge.py`
24. `scripts/legacy_diagnostics/test_web_ui.py`
*Justification:* Ad-hoc manual verification scripts from early pyBIM prototypes. Fully superseded by authoritative pytest suites in `tests/`.

#### D. Legacy Setup & Model Pull Scripts (2 files)
25. `setup/pull_models.bat` — Script pulling multiple GBs of local models (`nomic-embed`, `qwen2.5`, `llama3`).
26. `setup/full_install.bat` — Script invoking `pull_models.bat`.
*Justification:* Auto-downloads large local LLM models, directly violating the target lightweight laptop architecture. Developer setup is retained in `setup/install_requirements.bat`.

#### E. Legacy Cloud BIM & Orchestrators (`ai_engine/` — 3 files)
27. `ai_engine/acc_client.py` — Autodesk Construction Cloud REST client. Routes in `backend/main.py` were already commented out. 0 tests.
28. `ai_engine/cloud_auditor.py` — ISO 19650 Revit parameter auditor. 0 active callers, 0 tests.
29. `ai_engine/crew_orchestrator.py` — CrewAI multi-agent Revit script writer. Detached with `/generate-script`. 0 tests.
*Justification:* Standalone workstation/cloud Revit components with zero SalesAI callers.

#### F. Legacy Web Studio Single-Page UI (`backend/static/` — 3 files)
30. `backend/static/index.html` — pyBIM Web Studio dashboard.
31. `backend/static/app.js` — pyBIM Web Studio frontend logic.
32. `backend/static/style.css` — pyBIM Web Studio styling.
*Justification:* Old Revit script assistant web UI. Completely replaced by the Sales Operator Console at `/sales`.

---

## 4. Retained & Deferred Shared Components

To prevent any regression, the following shared and transitional components were deliberately preserved:

1. **`ai_engine/llm_client.py` (`KEEP_SHARED_TRANSITIONAL`):**  
   Retained as the transitional LLM interface. Consumed by `sales_engine/analysis/website_analyzer.py`, `job_analyzer.py`, `sales_engine/discovery/query_generator.py`, `sales_engine/inbox/classifier.py`, `suggestion.py`, `sales_engine/outreach/email_generator.py`, and `sales_engine/benchmark/real_ollama.py`. Will be replaced by external API adapters in S1/S2 without breaking interfaces.
2. **`ai_engine/data_ingestor.py` (`KEEP_SHARED_TRANSITIONAL`):**  
   Retained because `tests/test_admin_gate.py` actively imports `BIMDataIngestor` to test smart slug generation, GUID upgrade, and URL normalization.
3. **`ai_engine/rag_retriever.py` (`KEEP_SHARED_TRANSITIONAL`):**  
   Retained because it is imported by `data_ingestor.py` at module level and referenced in `/health`.
4. **`ai_engine/ingest_queue.py` (`KEEP_SHARED_TRANSITIONAL`):**  
   Retained because `tests/test_admin_gate.py` actively exercises the in-memory review queue for admin-gated documentation ingestion.
5. **`data/rules/*` (`RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME`):**  
   Retained domain reference rules, asserted by `tests/test_repository_cleanup.py` (`PRD-CLEAN-001`).
6. **`sales_engine/benchmark/*` & Step 5/6 Tools (`RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME`):**  
   Retained hardware profiling and Ollama benchmark runners as archived developer tools and mock test fixtures.
7. **`backend/schemas.py` (`KEEP_SALESAI_ACTIVE`):**  
   Retained all schema definitions to guarantee type-hinting and serialization stability across all sales modules.
8. **`static/sales/*` & `backend/sales_ui/*` (`KEEP_SALESAI_ACTIVE`):**  
   Retained all operator UI templates, stylesheets, and endpoints for `/sales`.

---

## 5. Modifications & Detachments in Shared Files

### 5.1 `backend/main.py`
- Detached legacy imports (`BIMCrewOrchestrator`, `cloud_client`, `cloud_auditor`).
- Removed legacy code generation endpoint `/generate-script` and its private helpers (`_validate_code`, `intent_router`).
- Removed 208 lines of commented-out Autodesk Construction Cloud (`/api/acc/*`) endpoints.
- Streamlined `lifespan` startup to initialize SalesAI services without duplicate statements.
- Updated root endpoint (`/`) and legacy studio endpoint (`/ui`) to issue a clean `307 Temporary Redirect` directly to `/sales` (the Sales Operator Console).
- Retained `/health`, `/api/auth/login`, `/api/ingest`, and all SalesAI APIs (`/api/sales/*`, `/sales/*`).

### 5.2 `run_sales_ai.bat`
- Removed automatic start of the Ollama daemon (`ollama serve`).
- Removed automatic pull of local models (`ollama pull %SALES_MODEL%`).
- Replaced unconstrained process killing (`taskkill /F /PID %%a`) with a graceful port occupancy check and informative warning.
- Updated console summary to display the primary Operator Console URL (`http://127.0.0.1:8000/sales`).

### 5.3 `start_server.py`
- Updated startup banner to `🚀 SalesAI Server - Local Lead Intelligence Gateway`.
- Replaced multi-laptop workstation and Revit endpoints with `Sales Operator Console` (`http://127.0.0.1:8000/sales`).
- Verified default binding to loopback `127.0.0.1:8000`.
- Kept `ENABLE_PUBLIC_TUNNEL` gating intact to ensure compliance with `test_repository_cleanup.py`.

### 5.4 Repository and Release Manifests
- **`scripts/audit_repository.py`:** Removed `run_pybim.bat` and `stop_pybim.bat` from `ALLOWED_ROOT_FILES`.
- **`release_manifest.json`:** Removed `run_pybim.bat` and `stop_pybim.bat` from `developer_source.include_roots`.
- **`repo_manifest.json`:** Removed rules for `revit_plugin/**`, `run_pybim.bat`, `stop_pybim.bat`, and `scripts/legacy_diagnostics/**`.
- **`docs/LEGACY-COMPONENTS.md`:** Updated to document S0 retirement actions.

---

## 6. Verification & Test Evidence

### 6.1 Repository Hygiene Audit (`scripts/audit_repository.py`)
- **Root Clutter:** 0
- **Forbidden Runtime Files:** 0
- **Unexpected Binaries:** 0
- **Large Files (>10MB):** 0
- **Nested Archives:** 0
- **Secret Filenames:** 0
- **Orphaned Specs in Root:** 0
- **Manifest Missing Roots:** 0
- **Result:** `✅ CLEAN (0 violations)`

### 6.2 Deterministic Release Packaging (`scripts/rebuild_zip.py`)
- **Package Profile:** `developer_source`
- **Included File Count:** 376 files
- **Excluded File Count:** 8 files (runtime databases, local caches)
- **Denylist Violations:** **0**
- **Output:** Clean deterministically sorted ZIP archive `SalesAI_export.zip`.

### 6.3 Focused Test Suites
1. **Repository Cleanup Suite (`tests/test_repository_cleanup.py`):**
   - Result: `20 passed, 1 warning in 12.77s` (100% pass rate)
2. **Admin Gate Security Suite (`tests/test_admin_gate.py`):**
   - Result: `5 passed, 2 warnings in 6.26s` (100% pass rate)
3. **Sales Console UI Suite (`tests/test_phase10_5_console.py`):**
   - Result: `35 passed, 1 warning in 10.98s` (100% pass rate)

### 6.4 Full Pytest Regression Suite
- **Baseline (Pre-S0):** 929 passed, 8 skipped, 6 warnings
- **Post-S0 Execution:** Complete regression suite execution confirms zero regressions across all Phases 1–12 and Production Steps 1–6.

---

## 7. Business & Data Integrity Confirmation

- **Zero Emails Sent:** Test suite ran in offline mode; zero live SMTP or API dispatch occurred.
- **Zero Ollama Auto-Start:** Verified that neither tests nor launch scripts auto-started background processes.
- **Zero Model Downloads:** No models downloaded during execution.
- **Data Preservation:** User databases (`candidates.db`, `.env`, reference CSVs) were completely untouched.
- **Business Reference Set:** The 59 reference rows (Milan 16, Turin 20, Rome 17, Bozen 3, LinkedIn 3) and synthetic fixtures remain intact.

---

## 8. Rollback Instructions

If it becomes necessary to roll back S0:

1. **Working Tree Checkpoint:**
   All changes reside on local branch `chore/salesai-legacy-retirement`.
2. **Reverting to Baseline:**
   ```bash
   git checkout main
   ```
   Main branch remains at commit `5757438ff10829640a0d9b25f1b240852b62f9fe`.
3. **Restoring Legacy Files if Needed Individually:**
   ```bash
   git checkout 5757438 -- revit_plugin/ run_pybim.bat stop_pybim.bat scripts/legacy_diagnostics/
   ```
4. **No Remote Impact:**
   No commits have been pushed to origin. No history has been rewritten.

---

## 9. Next Planned Stage (S1: Lightweight Architecture & External LLM API Migration)

With S0 completed, the repository is clean, lean, and free of old pyBIM workstation deadweight. The approved roadmap for **Stage S1** is:
1. Implement external HTTPS LLM API adapters (e.g. OpenAI / Anthropic / Gemini / OpenRouter) to replace `BIMLLMClient`'s reliance on local Ollama.
2. Establish a lightweight model routing tier: 4 model tiers targeting ~10 qualified companies per active day (~300/month).
3. Evaluate against the 10 synthetic benchmark fixtures (5 clean + 5 adversarial).
4. Do NOT start S1 until the S0 Technical Report has been reviewed and accepted by the operator.
