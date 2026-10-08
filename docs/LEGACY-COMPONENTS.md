# Legacy Components & Audit Decisions

This document audits all legacy components inherited from the initial pyBIM-LLM project and clarifies their operational status in SalesAI.

## 1. Revit Plugin (`revit_plugin/`)
- **Origin**: Initial pyBIM project. Contains C# commands (`Commands.cs`), Revit manifest (`pyBIM.addin`), and pyRevit pushbuttons (`pyBIM.extension/`).
- **Role in SalesAI**: Not part of the core SalesAI automated pipeline (Discovery, Scoring, Outreach, Analytics).
- **Audit Decision**: **RETAINED IN REPO, EXCLUDED FROM PRODUCTION RELEASE**.
  - Moving or deleting could disrupt existing pyRevit setups.
  - Excluded from the production release manifest to keep the distribution lean.

## 2. BIM Domain Rules (`data/rules/`)
- **Origin**: Domain reference rules for Revit API and ISO 19650 compliance.
- **Role in SalesAI**: Directly referenced by `BIMDataIngestor` (`ai_engine/data_ingestor.py`) as the default markdown rule source.
- **Audit Decision**: **RETAINED IN REPO & PACKAGED IN RELEASE**.
  - Essential for vector indexing and BIM qualification grounding.

## 3. Legacy Launch Scripts (`run_pybim.bat`, `stop_pybim.bat`)
- **Origin**: Scripts to run the original pyBIM multi-laptop studio server.
- **Role in SalesAI**: Historical launcher. SalesAI uses `run_sales_ai.bat` as its primary production entrypoint.
- **Audit Decision**: **RETAINED FOR BACKWARD COMPATIBILITY**.

## 4. Diagnostic & Manual Verification Scripts (`scripts/legacy_diagnostics/`)
- **Origin**: Early standalone scripts located in the repository root (`test_backend.py`, `test_crew_orchestrator.py`, `test_inference.py`, `test_ingestion.py`, `test_rag.py`, `test_revit_bridge.py`, `test_web_ui.py`, `ingest_doc.py`).
- **Issues in Root**:
  - Cluttered the root directory.
  - Caused pytest to attempt running manual scripts that required live Ollama services or ChromaDB instances.
- **Audit Decision**: **RELOCATED TO `scripts/legacy_diagnostics/`**.
  - Preserves Git history via `git mv`.
  - Removes them from default `pytest tests/` discovery.

## 5. Cloudflare Tunnel (`cloudflared.exe`, `PUBLIC_URL.txt`)
- **Origin**: Binary and text output for creating Cloudflare quick tunnels.
- **Role in SalesAI**: SalesAI is designed strictly for local execution (`localhost:8000`). Public tunneling is not permitted by default.
- **Audit Decision**: **EXCLUDED FROM RELEASE & AUTO-START DISABLED**.
  - Excluded from git via `.gitignore` and release packaging via `rebuild_zip.py`.
  - `start_server.py` modified so the tunnel never starts automatically unless `ENABLE_PUBLIC_TUNNEL=true` or `--tunnel` is provided.
