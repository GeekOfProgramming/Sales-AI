# SalesAI — S0 pyBIM-LLM Legacy Inventory & Decision Matrix

**Date:** 2026-10-21  
**Working Repository:** `C:\Sales-AI\SalesAI`  
**Branch:** `chore/salesai-legacy-retirement`  
**Reference Specification:** `docs/cleanup/SALESAI-S0-LEGACY-PYBIM-LLM-RETIREMENT.md`  

---

## 1. Executive Summary

As part of **SalesAI S0 — Safe Legacy pyBIM-LLM Retirement**, a comprehensive pre-cleanup dependency scan, AST inspection, test-reference mapping, and runtime route verification was conducted across all components of the repository.

The goal is to retire provably isolated components of the original pyBIM-LLM / Revit workstation product, while strictly preserving:
- All SalesAI Phase 1–12 capabilities (Discovery, Scoring, Outreach, Sending Safeguards, Inbox, Analytics).
- Accepted Production Readiness Steps 1–6 records and test suites.
- The local Sales Operator Console UI (`/sales`).
- Domain reference data and transitional shared modules.
- Complete safety of user data, credentials, and business logic.

---

## 2. Decision Categories & Policy

Every scanned path is classified into one of five mutually exclusive categories:

1. **`REMOVE_VERIFIED_LEGACY`**: Completely isolated pyBIM-LLM workstation assets, old Revit plugins, or obsolete diagnostic scripts that have zero consumers in active SalesAI code or automated tests. Removed via tracked `git rm`.
2. **`KEEP_SHARED_TRANSITIONAL`**: Modules containing legacy naming or shared interfaces that are actively consumed by SalesAI services or tests (e.g. `ai_engine/llm_client.py`, `ai_engine/data_ingestor.py`). Must be preserved until future phases (S1/S2).
3. **`KEEP_SALESAI_ACTIVE`**: Core SalesAI runtime modules, operator console UI assets, schemas, routers, and essential launchers. Kept active and updated.
4. **`RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME`**: Historical benchmarks, capacity profiling scripts, and reference rules. Preserved as historical evidence and test fixtures, excluded from runtime distribution.
5. **`DEFER_UNCERTAIN`**: Any component where removal could cause ambiguous regressions. Kept in place pending dedicated review.

---

## 3. Inventory & Decision Matrix

| Path | Category | Purpose | Decision | Justification & Verification Proof |
| :--- | :--- | :--- | :--- | :--- |
| `revit_plugin/Commands.cs` | Legacy Workstation | C# Revit API command implementation | **REMOVE_VERIFIED_LEGACY** | Pure Revit plugin. Zero imports in Python or tests. |
| `revit_plugin/install_pyrevit_extension.bat` | Legacy Workstation | Batch script installing extension to AppData | **REMOVE_VERIFIED_LEGACY** | Workstation script. Unused by SalesAI. |
| `revit_plugin/pyBIM.addin` | Legacy Workstation | Autodesk Revit XML addin manifest | **REMOVE_VERIFIED_LEGACY** | Workstation manifest. Unused by SalesAI. |
| `revit_plugin/pyRevitScript.py` | Legacy Workstation | IronPython script for pyRevit ribbon | **REMOVE_VERIFIED_LEGACY** | Desktop ribbon script. Unused by SalesAI. |
| `revit_plugin/pyBIM.extension/**` (10 files) | Legacy Workstation | pyRevit extension definitions & pushbuttons | **REMOVE_VERIFIED_LEGACY** | Desktop UI assets. Zero consumers in SalesAI. |
| `run_pybim.bat` | Legacy Launcher | Old pyBIM multi-laptop studio launcher | **REMOVE_VERIFIED_LEGACY** | Auto-starts Ollama, kills port 8000. Replaced by `run_sales_ai.bat`. |
| `stop_pybim.bat` | Legacy Launcher | Old pyBIM taskkill script | **REMOVE_VERIFIED_LEGACY** | Unconstrained taskkill on port 8000. Replaced by standard process termination. |
| `scripts/legacy_diagnostics/ingest_doc.py` | Legacy Diagnostics | Manual CLI document scraper | **REMOVE_VERIFIED_LEGACY** | Manual script. Not run in automated test suite. |
| `scripts/legacy_diagnostics/test_backend.py` | Legacy Diagnostics | Manual script querying `/health` & `/ui` | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic. Automated tests in `tests/` provide full coverage. |
| `scripts/legacy_diagnostics/test_crew_orchestrator.py` | Legacy Diagnostics | Manual script testing CrewAI Revit coder | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic. Unused by SalesAI. |
| `scripts/legacy_diagnostics/test_inference.py` | Legacy Diagnostics | Manual script testing `/generate-script` | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic for retired endpoint. |
| `scripts/legacy_diagnostics/test_ingestion.py` | Legacy Diagnostics | Manual script testing RAG ingestion | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic. Unused in tests. |
| `scripts/legacy_diagnostics/test_rag.py` | Legacy Diagnostics | Manual script testing ChromaDB queries | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic. Unused in tests. |
| `scripts/legacy_diagnostics/test_revit_bridge.py` | Legacy Diagnostics | Simulated Revit client HTTP script | **REMOVE_VERIFIED_LEGACY** | Simulated client for retired endpoint. |
| `scripts/legacy_diagnostics/test_web_ui.py` | Legacy Diagnostics | Manual script testing Web UI Studio | **REMOVE_VERIFIED_LEGACY** | Manual diagnostic for retired Studio UI. |
| `setup/pull_models.bat` | Legacy Setup | Script pulling nomic-embed, qwen2.5, llama3 | **REMOVE_VERIFIED_LEGACY** | Auto-downloads multi-GB models. Violates lightweight laptop architecture. |
| `setup/full_install.bat` | Legacy Setup | Script calling pull_models | **REMOVE_VERIFIED_LEGACY** | Triggers model downloads. Replaced by clean `setup/install_requirements.bat`. |
| `ai_engine/acc_client.py` | Legacy Cloud BIM | Autodesk Construction Cloud REST client | **REMOVE_VERIFIED_LEGACY** | Cloud BIM client. Routes in `backend/main.py` were commented out. 0 tests. |
| `ai_engine/cloud_auditor.py` | Legacy Cloud BIM | ISO 19650 Revit parameter compliance auditor | **REMOVE_VERIFIED_LEGACY** | Cloud BIM auditor. Routes were commented out. 0 tests. |
| `ai_engine/crew_orchestrator.py` | Legacy Code Gen | CrewAI multi-agent Revit script writer | **REMOVE_VERIFIED_LEGACY** | Revit script writer. Detached with `/generate-script`. 0 test consumers. |
| `backend/static/index.html` | Legacy Studio UI | pyBIM-LLM Web Studio single-page UI | **REMOVE_VERIFIED_LEGACY** | Old Revit workstation UI. Replaced by `/sales` Operator Console. |
| `backend/static/app.js` | Legacy Studio UI | pyBIM-LLM Web Studio JavaScript | **REMOVE_VERIFIED_LEGACY** | Studio controller. Not used by `/sales` console. |
| `backend/static/style.css` | Legacy Studio UI | pyBIM-LLM Web Studio stylesheet | **REMOVE_VERIFIED_LEGACY** | Studio styling. Not used by `/sales` console. |
| `ai_engine/llm_client.py` | Shared Transitional | Transitional LLM interface | **KEEP_SHARED_TRANSITIONAL** | Actively imported by 7 `sales_engine` modules and unit tests. Kept until S1/S2 API adapters. |
| `ai_engine/data_ingestor.py` | Shared Transitional | Web documentation ingestion & slug helper | **KEEP_SHARED_TRANSITIONAL** | Actively imported by `tests/test_admin_gate.py` for smart slug testing. |
| `ai_engine/rag_retriever.py` | Shared Transitional | ChromaDB RAG retriever interface | **KEEP_SHARED_TRANSITIONAL** | Imported by `data_ingestor.py` at module level and referenced in health check. |
| `ai_engine/ingest_queue.py` | Shared Transitional | Review queue for documentation ingestion | **KEEP_SHARED_TRANSITIONAL** | Actively tested by `tests/test_admin_gate.py` in frozen regression suite. |
| `ai_engine/__init__.py` | Shared Transitional | Package exports for `ai_engine` | **KEEP_SALESAI_ACTIVE** | Cleanly exports retained `BIMLLMClient`, `BIMRAGRetriever`, `BIMDataIngestor`. |
| `data/rules/**` | Historical Reference | Domain Revit & ISO reference markdown files | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Asserted by `test_repository_cleanup.py` (PRD-CLEAN-001). Preserved for test stability. |
| `sales_engine/benchmark/**` | Historical Evidence | Step 6 Ollama benchmark runner & fixtures | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Historical acceptance evidence. Tested via mocks in `test_real_llm_benchmark.py`. |
| `scripts/benchmark_ollama.py` | Historical Tool | Standalone CLI for Ollama benchmark | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Developer diagnostic benchmark utility. |
| `scripts/hardware_profile.py` | Historical Tool | Step 5 hardware profiler script | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Tested in `tests/test_hardware_audit.py`. |
| `setup/HARDWARE-AUDIT.bat` / `.ps1` | Historical Tool | Step 5 hardware audit launchers | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Preserved developer diagnostics. |
| `setup/RUN-LLM-BENCHMARK.bat` / `.ps1` | Historical Tool | Step 6 benchmark launchers | **RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME** | Preserved developer diagnostics. |
| `setup/install_requirements.bat` | Developer Tool | Standard pip dependencies installer | **KEEP_SALESAI_ACTIVE** | Sets up virtual environment from `requirements.txt` cleanly. |
| `backend/schemas.py` | Production Runtime | Core Pydantic schemas | **KEEP_SALESAI_ACTIVE** | Essential data models across `sales_engine`. |
| `backend/auth.py` | Production Runtime | JWT authentication & credentials | **KEEP_SALESAI_ACTIVE** | Essential for administrative security and test suites. |
| `backend/sales_ui/**` | Production Runtime | Local Sales Operator Console UI & templates | **KEEP_SALESAI_ACTIVE** | The primary user-facing console for SalesAI. |
| `static/sales/**` | Production Runtime | CSS and JS assets for Sales Console | **KEEP_SALESAI_ACTIVE** | Directly asserted by `test_phase10_5_console.py`. |
| `backend/main.py` | Production Runtime | FastAPI application entrypoint | **KEEP_SALESAI_ACTIVE** | Detach `/generate-script`, unused ACC imports, pyBIM studio routes. Retain all SalesAI APIs. |
| `start_server.py` | Production Runtime | Server startup script | **KEEP_SALESAI_ACTIVE** | Updated to default to `127.0.0.1:8000`, display Sales console link, remove workstation prompts. |
| `run_sales_ai.bat` | Production Runtime | Batch launcher for SalesAI | **KEEP_SALESAI_ACTIVE** | Updated to eliminate Ollama auto-start, model auto-pull, and PID killing. |

---

## 4. Exact Removals Summary

Total files verified and selected for removal: **31 files**
- `revit_plugin/`: 14 files
- `scripts/legacy_diagnostics/`: 8 files
- `setup/`: 2 files (`pull_models.bat`, `full_install.bat`)
- `ai_engine/`: 3 files (`acc_client.py`, `cloud_auditor.py`, `crew_orchestrator.py`)
- `backend/static/`: 3 files (`index.html`, `app.js`, `style.css`)
- Root launchers: 2 files (`run_pybim.bat`, `stop_pybim.bat`)
