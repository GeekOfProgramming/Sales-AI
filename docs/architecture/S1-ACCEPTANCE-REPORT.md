# SalesAI S1 — Acceptance & Verification Report

**Milestone:** SalesAI S1 — Lightweight Laptop Architecture & Dependency Audit  
**Date:** 2026-10-21  
**Status:** Audit Complete / Ready for Review  
**Base Commit:** `1b6c50e8f1a615cf462bce296b604b69b915d721` (`chore/salesai-legacy-retirement`)  
**Working Branch:** `audit/salesai-s1-laptop-dependency-map`  
**Target Operating Architecture:** Developer Windows Laptop + Localhost UI + SQLite/JSON + External Remote HTTPS LLM APIs (S2)

---

## 1. Executive Summary & Verification of Ground Rules

The SalesAI S1 audit was executed strictly under non-destructive, documentation-first governance. Its goal is transitioning SalesAI from legacy local workstation neural inference (Ollama / CUDA) to a lightweight laptop architecture targeting **10 qualified companies per active day** (~300/month) backed by external remote HTTPS model APIs in Phase S2.

### 1.1 Strict Boundary Verification
- [x] **Zero External Model Network Calls:** No requests dispatched to commercial LLM APIs.
- [x] **Zero Package Modifications:** No packages installed, uninstalled, or upgraded in the active `.venv`.
- [x] **Zero Operational Data Alteration:** `data/candidates.db`, `.env`, CSV exports, and reference datasets remained untouched.
- [x] **Zero Email Transmission & Live Scraping:** No emails sent; no live external crawls conducted; Phase 9 human review and suppression gates strictly preserved.
- [x] **Git Isolation:** Operating entirely on dedicated branch `audit/salesai-s1-laptop-dependency-map` without pushing to remote origins.
- [x] **Reference Dataset & Outcome Integrity:** Verified preservation of the 59 reference rows (Milan 16, Turin 20, Rome 17, Bozen 3, LinkedIn 3). Turin 3BA is preserved as `meeting_pending_scheduling` (not confirmed); LinkedIn channel metrics remain strictly segregated from email conversion denominators.

---

## 2. Summary of S1 Architecture Deliverables

All six required technical deliverables have been authored, cross-verified, and placed in `docs/architecture/`:

1. [`docs/architecture/S1-CURRENT-ARCHITECTURE-MAP.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-CURRENT-ARCHITECTURE-MAP.md)  
   Complete map of Phases 1–12 and Steps 1–6 against live codebase modules. Identifies active business logic, transitional interfaces, mock-only tools, and legacy artifacts. Explicitly defines data flows and hard human approval boundaries.

2. [`docs/architecture/S1-DEPENDENCY-AUDIT.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-DEPENDENCY-AUDIT.md)  
   Full audit of installed Python 3.14.7 AMD64 packages. Identifies import-time side effects of heavy native C++ ML wheels (`chromadb`, `onnxruntime`). Proposes clean segregation into `requirements-core.txt`, `requirements-optional.txt`, `requirements-dev.txt`, and `requirements-legacy.txt` with a non-destructive venv migration plan.

3. [`docs/architecture/S1-LLM-CALL-MAP.json`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-LLM-CALL-MAP.json) & [`docs/architecture/S1-LLM-CALL-MAP.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-LLM-CALL-MAP.md)  
   Machine-readable JSON schema and companion markdown document mapping all **six real active LLM call sites**: Website Analyzer (Pass A & B), Job Posting Analyzer, Discovery Query Generator, Inbound Email Classifier, Reply Suggestion Generator, and Cold Outreach Email Generator. Formulates prompt contracts, schemas, error recovery, privacy classifications, and monthly token/budget projections ($2.50 to $6.50/month).

4. [`docs/architecture/S1-LOCAL-SECURITY-AND-RESOURCE-REVIEW.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-LOCAL-SECURITY-AND-RESOURCE-REVIEW.md)  
   Security assessment of localhost loopback binds (`127.0.0.1:8000`), exposure risks from legacy Cloudflare Quick Tunnel code in `start_server.py`, launcher probes in `run_sales_ai.bat`, and health coupling in `backend/main.py`. Includes real measured hardware metrics demonstrating an **80.4% reduction in import latency** (1.081s vs 5.520s) and **61.2% memory savings** (55 MB vs 142 MB) when decoupling legacy ML dependencies.

5. [`docs/architecture/S1-S2-MIGRATION-BACKLOG.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-S2-MIGRATION-BACKLOG.md)  
   Ten ordered, incremental tasks for Phase S2: Abstract Gateway Interface, Primary OpenAI-compatible HTTPS adapter, 4-tier task router, PII sanitizer, schema validator, budget ledger & circuit breaker, rewiring of 6 call sites, secondary adapters, health modernization, and legacy RAG decoupling.

6. [`docs/architecture/S1-ACCEPTANCE-REPORT.md`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-ACCEPTANCE-REPORT.md)  
   This document — comprehensive audit findings, risk register, test results, and S2 entry criteria.

---

## 3. High-Value Inspection Targets & Technical Findings

| Inspection Target | Current Live State | S1 Audit Finding | S2 Action Mandate |
| :--- | :--- | :--- | :--- |
| `ai_engine/llm_client.py` | Imports `ollama` directly; provides `BIMLLMClient` | Six core SalesAI modules consume this class. | Abstract into `LLMProviderGateway`. Do not delete until all 6 callers are rewired. |
| `start_server.py` | Defaults to `127.0.0.1:8000`, but retains Cloudflare Tunnel code | Legacy tunnel feature exposes local dashboard publicly if `--tunnel` or `ENABLE_PUBLIC_TUNNEL` is set. Docstring says `0.0.0.0`. | Permanently excise Cloudflare subprocess logic and fix docstring to `127.0.0.1:8000`. |
| `run_sales_ai.bat` | Ollama auto-start disabled in S0; probes port 11434 | Performs redundant `curl -s http://127.0.0.1:11434/api/tags`. | Eliminate the port 11434 probe entirely. |
| `backend/main.py` (`/health`) | Probes `llm_client.is_healthy()` & `rag_retriever.count()` | Reports `degraded` if local Ollama or ChromaDB is absent. | Update `/health` to verify SQLite and outbound API gateway status instead. |
| `tests/test_admin_gate.py` | Asserts token security on admin routes, but imports `BIMDataIngestor` | Coupled to legacy RAG modules `data_ingestor.py` and `ingest_queue.py`. | Refactor test to assert token protection against pure SalesAI admin endpoints, enabling RAG retirement. |
| `requirements.txt` | Monolithic unpinned file pulling `chromadb`, `onnxruntime`, etc. | Adds ~620 MB to disk and 4.4s to import latency. | Split into pinned `requirements-core.txt`, `requirements-optional.txt`, `requirements-dev.txt`. |

---

## 4. Workstation Benchmark & Resource Measurements

Physical hardware benchmark measurements executed locally on Windows 11 AMD64 (Python 3.14.7):
- **Full Backend Cold Import (`backend.main`):** `5.520s` | `RSS: 141.90 MB`  
  *(Overhead caused by loading `chromadb`, `onnxruntime.dll`, `langchain` modules)*
- **Core Lightweight Runtime Modules (`fastapi`, `pydantic`, `sqlite3`, `requests`, `bs4`):** `1.081s` | `RSS: 55.02 MB`  
- **Hardware Optimization Opportunity:** **80.4% faster cold boot** and **86.88 MB RAM reduction** upon completion of S2 decoupling.

---

## 5. Comprehensive Risk Register

| Risk ID | Severity | Risk Description | Proposed Mitigation & Safe Protocol |
| :--- | :--- | :--- | :--- |
| **RSK-S1-01** | Medium | Accidental tunnel activation exposing candidate PII | Ensure `ENABLE_PUBLIC_TUNNEL` is unset. S2 will excise `cloudflared` launcher completely. |
| **RSK-S1-02** | High | Uncontrolled cost escalation across commercial LLM APIs | S2 enforces daily budget cap ($1.00/day) and prohibits automatic escalation to Tier 4. |
| **RSK-S1-03** | High | Customer PII leakage in inbound email classification prompts | S2 payload builder strips phone numbers, banking details, and signatures before API dispatch. |
| **RSK-S1-04** | High | Untrusted inbound text executing prompt injection | Inbound email text wrapped in `<UNTRUSTED_INBOUND_EMAIL>` tags; strict JSON schema enforcement. |
| **RSK-S1-05** | Critical | LLM hallucinations generating unauthorized promises/pricing | Deterministic `DraftValidator` and `ProhibitedClaimsValidator` reject unauthorized claims; human approval required. |
| **RSK-S1-06** | Medium | Broken test suite during legacy RAG module retirement | Preserve legacy files until `test_admin_gate.py` is refactored and verified in isolated `.venv-lean`. |

---

## 6. Verification & Test Suite Execution Results

### 6.1 Repository Hygiene Audit
- **Command:** `python scripts/audit_repository.py`
- **Result:** `0 Violations`, `Status: ✅ CLEAN`.
- **Details:** Checked root clutter, forbidden runtime files, unexpected binaries, large files, nested zips, secret filenames, orphaned specs, and manifest roots.

### 6.2 Focused Integrity Tests
- **Command:** `pytest tests/test_repository_cleanup.py -q`
- **Result:** `20 passed, 1 warning in 11.86s` (100% pass rate).

### 6.3 Full Regression Suite
- **Command:** `pytest tests/ -q`
- **Baseline:** 929 passed, 8 skipped across 937 test items.
- **Execution Run:** `929 passed, 8 skipped, 6 warnings in 221.38s (0:03:41)`
- **Parity Status:** 100% test parity maintained on `audit/salesai-s1-laptop-dependency-map`. Zero test regressions.

---

## 7. Rollback Instructions

If it is necessary to revert all S1 architectural artifacts to the exact base commit `1b6c50e8`:

```bash
# 1. Switch back to base branch
git checkout chore/salesai-legacy-retirement

# 2. Delete the local S1 audit branch and its unmerged commits
git branch -D audit/salesai-s1-laptop-dependency-map

# 3. Verify working directory is pristine and at commit 1b6c50e8
git status
git log -1 --oneline
# Expected: 1b6c50e chore(cleanup): implement SalesAI S0 safe pyBIM-LLM legacy retirement
```

---

## 8. S2 Entry Criteria Gate Sign-Off

Phase S2 implementation may proceed immediately upon user review and approval based on satisfying all entry criteria:
- [x] All 6 architectural deliverables authored and verified in `docs/architecture/`.
- [x] All 6 real LLM call sites mapped with exact schemas, fallbacks, and token metrics.
- [x] Localhost security and tunnel risks documented with clear excision plans.
- [x] Dependency groups defined (`core`, `optional`, `dev`, `legacy`) with non-destructive migration roadmap.
- [x] 100% test pass parity maintained against baseline.
- [x] Operating rules intact: developer laptop deployment, 10 qualified companies/day target, zero local model downloads, strict human approval.
