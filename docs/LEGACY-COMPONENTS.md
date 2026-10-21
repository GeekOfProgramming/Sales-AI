# Legacy Components & Retirement Decisions (S0)

This document audits all legacy components inherited from the initial pyBIM-LLM workstation project and documents their retired status under **SalesAI S0 — Safe Legacy pyBIM-LLM Retirement**.

---

## 1. Revit Plugin (`revit_plugin/`)
- **Origin**: Initial pyBIM workstation project. Contained C# commands (`Commands.cs`), Revit manifest (`pyBIM.addin`), and pyRevit pushbuttons (`pyBIM.extension/`).
- **Role in SalesAI**: Not part of SalesAI.
- **S0 Action**: **REMOVED VIA TRACKED GIT RM (31 FILES)**.
  - Zero imports in Python runtime, zero test references.
  - Safely preserved in Git history and user backup archives.

## 2. Legacy Workstation Launchers (`run_pybim.bat`, `stop_pybim.bat`)
- **Origin**: Launchers for the old pyBIM multi-laptop studio server.
- **Role in SalesAI**: Obsolete. SalesAI uses `run_sales_ai.bat` as its primary developer entrypoint.
- **S0 Action**: **REMOVED VIA TRACKED GIT RM**.
  - `run_sales_ai.bat` updated to eliminate Ollama auto-starts, model downloads, and unconstrained taskkills.

## 3. Diagnostic & Manual Verification Scripts (`scripts/legacy_diagnostics/`)
- **Origin**: Early standalone verification scripts (`test_backend.py`, `test_crew_orchestrator.py`, `test_inference.py`, `test_ingestion.py`, `test_rag.py`, `test_revit_bridge.py`, `test_web_ui.py`, `ingest_doc.py`).
- **Role in SalesAI**: Superseded by automated test suites under `tests/`.
- **S0 Action**: **REMOVED VIA TRACKED GIT RM**.

## 4. Legacy Cloud BIM & Orchestrators (`ai_engine/acc_client.py`, `cloud_auditor.py`, `crew_orchestrator.py`)
- **Origin**: Autodesk Construction Cloud client, ISO 19650 Revit parameter auditor, and CrewAI Revit code writer.
- **Role in SalesAI**: Not part of SalesAI lead generation or outreach.
- **S0 Action**: **REMOVED VIA TRACKED GIT RM**.
  - Endpoints detached from `backend/main.py`.

## 5. Legacy Studio UI Assets (`backend/static/index.html`, `app.js`, `style.css`)
- **Origin**: pyBIM-LLM Web Studio single-page app.
- **Role in SalesAI**: Superseded by Sales Operator Console at `/sales`.
- **S0 Action**: **REMOVED VIA TRACKED GIT RM**.
  - Root `/` and `/ui` redirect to `/sales`.

## 6. Retained Domain Reference Rules (`data/rules/`)
- **Origin**: Domain reference rules for Revit API and ISO 19650 standards.
- **Role in SalesAI**: Referenced by `BIMDataIngestor` (`ai_engine/data_ingestor.py`) and asserted by `tests/test_repository_cleanup.py`.
- **S0 Decision**: **RETAINED IN REPO & PACKAGED IN SOURCE RELEASE**.

## 7. Retained Transitional Interfaces (`ai_engine/llm_client.py`, `data_ingestor.py`, `rag_retriever.py`, `ingest_queue.py`)
- **Role in SalesAI**: Consumed by active `sales_engine` modules and `tests/test_admin_gate.py`.
- **S0 Decision**: **KEEP_SHARED_TRANSITIONAL**.
  - Preserved until S1/S2 replace Ollama with external LLM API adapters and complete admin-gate test refactoring.
