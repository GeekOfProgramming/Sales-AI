# SalesAI MVP-01 — Lead Discovery Acceptance Report

## Status: ACCEPTED
**Date:** 2026-10-27  
**Scope:** MVP-01 First Usable Local Lead Discovery Workflow for pyBIM  
**Base Line:** S3.6B.3  

---

## 1. Executive Summary

SalesAI MVP-01 establishes the **first genuinely usable, human-in-the-loop operational lead discovery workflow** for pyBIM. The application shifts deliberately from infrastructure-first development to practical, local-first operations.

> **Operational Addendum (MVP-01.1):** Subsequent independent operational acceptance identified end-to-end enforcement gaps (config result cap of 50 vs adapter cap of 20 vs advertised cap of 10, lack of multipart file upload, lack of operator billing authorization acknowledgment, and default export including non-approved candidates). These were resolved in **SalesAI MVP-01.1**. See [SALESAI-MVP01-1-OPERATIONAL-ACCEPTANCE.md](file:///C:/Sales-AI/SalesAI/docs/architecture/SALESAI-MVP01-1-OPERATIONAL-ACCEPTANCE.md).

The complete operator loop is verified:
1. Local SalesAI operator dashboard startup without mandatory external dependencies (e.g. Ollama, Revit, ChromaDB).
2. Safe multi-sheet Excel workbook preview, column mapping, idempotency, and staging/commit without mutating original files.
3. Operator-controlled search parameterization (country, city, business category, search terms).
4. Strict Brave search authorization gate (dry-run preview default, 1 query / 10 result hard limit, billing/quota verification, zero fabricated defaults).
5. Candidate review interface with deduplication against historical outreach and do-not-contact records.
6. Operator manual decision buttons (Approve Lead, Reject Candidate, Mark Duplicate, Needs Research, Notes).
7. Safe export of approved candidates to CSV and XLSX with spreadsheet formula injection protection.
8. Email sending remains strictly disabled (`EMAIL_SEND_ENABLED=false`).

---

## 2. Invariants & Architecture Compliance

| Invariant | Specification | Verification Result |
| :--- | :--- | :--- |
| **Zero Autonomous Sends** | `EMAIL_SEND_ENABLED=false`. No background dispatch, no automated replies. | Verified (`test_zero_email_sends_and_no_llm_calls`) |
| **No Mandatory Ollama/Revit** | Startup continues smoothly in `backend/main.py` if local Ollama or ChromaDB is offline. | Verified (`test_dashboard_startup_without_ollama`) |
| **Excel Original Immutability** | Original Excel workbook is never edited; parsed in read-only mode into memory/staging. | Verified (`test_workbook_multi_sheet_inspection_and_preview`) |
| **Spreadsheet Idempotency** | Re-importing identical files/sheets skips duplicates with 0 duplicate company creation. | Verified (`test_workbook_import_preservation_and_idempotency`) |
| **Outreach & DNC Preservation** | Previously contacted companies and opt-outs/DNC flags are strictly preserved. | Verified (`test_workbook_import_preservation_and_idempotency`) |
| **No Fabricated Search Defaults**| Brave adapter removes hardcoded BIM modeling, Revit, AutoCAD, or fake locations. | Verified (`test_brave_adapter_no_fabricated_defaults`) |
| **Search Quota & Authorization Gate**| Operator dry-run preview is default; live search requires explicit button click and confirmed API key. | Verified (`test_candidate_review_actions`) |
| **Search Volume Caps** | Strictly 1 query, maximum 10 results per search batch. No autonomous pagination or retries. | Enforced in `Phase10ConsoleBridge` & router |
| **Formula Injection Protection** | Spreadsheet exports escape leading `=`, `+`, `-`, `@`, `\t`, `\r`. | Verified (`test_export_formula_injection_protection`) |
| **AI Boundary Integrity** | Missing AI analysis is marked as `NOT_ANALYZED`. No commercial LLM calls made during discovery. | Verified (`test_zero_email_sends_and_no_llm_calls`) |

---

## 3. End-to-End Acceptance Test Results

Suite: `tests/test_mvp01_lead_discovery.py`  
Command: `pytest tests/test_mvp01_lead_discovery.py -v`  
Status: **8 / 8 PASSED (100%)**

```
tests/test_mvp01_lead_discovery.py::test_dashboard_startup_without_ollama PASSED [ 12%]
tests/test_mvp01_lead_discovery.py::test_workbook_multi_sheet_inspection_and_preview PASSED [ 25%]
tests/test_mvp01_lead_discovery.py::test_workbook_import_preservation_and_idempotency PASSED [ 37%]
tests/test_mvp01_lead_discovery.py::test_brave_adapter_no_fabricated_defaults PASSED [ 50%]
tests/test_mvp01_lead_discovery.py::test_unimplemented_adapters_report_not_available PASSED [ 62%]
tests/test_mvp01_lead_discovery.py::test_candidate_review_actions PASSED [ 75%]
tests/test_mvp01_lead_discovery.py::test_export_formula_injection_protection PASSED [ 87%]
tests/test_mvp01_lead_discovery.py::test_zero_email_sends_and_no_llm_calls PASSED [100%]
```

Full Regression Suite:  
- `tests/test_phase10_5_console.py`: **25 / 25 PASSED**  
- `tests/test_phase10_regressions.py`: **34 / 34 PASSED**  
Total Regression: **59 / 59 PASSED**

---

## 4. Key Endpoints & Components Fixed

1. `backend/main.py`: `BIMLLMClient` initialization wrapped in non-blocking try/except block.
2. `backend/sales_ui/router.py`:
   - `/candidates/export`: Positioned before `/candidates/{candidate_id}` to resolve FastAPI route collision.
   - `POST /candidates/{candidate_id}/review`: Added manual decision review endpoint (Approve, Reject, Duplicate, Needs Research, Notes).
   - `POST /discovery/run`: Added quota check gate, preview dry-run support, and city/country parameters.
3. `sales_engine/discovery_v2/adapters.py`:
   - Stripped fabricated default capabilities and technologies.
   - Added SSRF URL safety validation rejecting RFC1918 private IPs and localhost.
   - Distinct classification of real websites vs directories/job portals.
   - Unconfigured ATS/directory adapters raise `RuntimeError("NOT_AVAILABLE")`.
4. `sales_engine/historical/importer.py`:
   - `inspect_workbook()` inspects multi-sheet workbooks and reports column mappings without database writes.
   - Robust detection of Italian and English headers for country, city, status, and opt-outs (`stato` vs country disambiguation).
5. `run_sales_ai.ps1`: Dedicated PowerShell launcher with port check and zero-send environment variables.
