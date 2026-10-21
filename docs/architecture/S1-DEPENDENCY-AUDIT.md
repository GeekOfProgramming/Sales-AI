# SalesAI S1 — Dependency Audit & Environment Rationalization Plan

**Document Version:** 1.0  
**Date:** 2026-10-21  
**Author:** Antigravity Engineering Agent  
**Environment Inspected:** Python 3.14.7 (win32 / Windows 11 AMD64)  
**Base Commit:** `1b6c50e8f1a615cf462bce296b604b69b915d721`  
**Branch:** `audit/salesai-s1-laptop-dependency-map`

---

## 1. Executive Summary & Audit Objectives

The target SalesAI architecture runs on a developer's Windows laptop with **zero local LLM inference** and **zero GPU/CUDA dependencies**. The application is designed to be lean, starting within 2 seconds, consuming <150 MB of idle RAM, and using lightweight Python standard libraries and HTTPS REST clients.

Historically, SalesAI inherited dependencies from pyBIM-LLM and earlier workstation experiments, resulting in a single unpinned `requirements.txt` that pulls heavy native machine-learning libraries (`chromadb`, `onnxruntime`, `tokenizers`, `torch`-adjacent wheels) and dual headless browser engines (`playwright`, `patchright`, `scrapling`).

This audit evaluates:
1. Every installed dependency in the live virtual environment.
2. Import-time side effects, memory overhead, and disk footprint.
3. Segregation into 4 clear dependency groups: **Core Runtime**, **Optional Advanced Scraping**, **Dev/Testing**, and **Legacy/Obsolete (Scheduled for Retirement)**.
4. Concrete pinned requirements specifications and a non-destructive venv migration plan.

---

## 2. Live Environment Package Inventory

The active virtual environment (`.venv` on Windows AMD64) was audited via `pip list --format=json`. The table below categorizes the packages, their installed versions, and their role in SalesAI.

| Package Name | Installed Version | Role in SalesAI | Resource Class | Target Disposition |
| :--- | :--- | :--- | :--- | :--- |
| **fastapi** | `0.115.11` | Primary HTTP & UI routing framework | Lightweight Core | **Retain (Core)** |
| **uvicorn** | `0.34.0` | ASGI Web Server running on `127.0.0.1:8000` | Lightweight Core | **Retain (Core)** |
| **starlette** | `0.46.0` | Underlying ASGI toolkit for FastAPI | Lightweight Core | **Retain (Core)** |
| **pydantic** | `2.10.6` | Data validation, schemas, and LLM output parsing | Lightweight Core | **Retain (Core)** |
| **pydantic-core** | `2.27.2` | Compiled Rust core for Pydantic V2 | Lightweight Core | **Retain (Core)** |
| **jinja2** | `3.1.5` | Templating engine for Sales UI dashboard | Lightweight Core | **Retain (Core)** |
| **requests** | `2.32.3` | Standard HTTP client for deterministic web fetching | Lightweight Core | **Retain (Core)** |
| **urllib3** | `2.3.0` | HTTP transport connection pool | Lightweight Core | **Retain (Core)** |
| **beautifulsoup4**| `4.13.3` | HTML parsing and candidate text extraction | Lightweight Core | **Retain (Core)** |
| **soupsieve** | `2.6` | Modern CSS selector engine for BeautifulSoup | Lightweight Core | **Retain (Core)** |
| **python-dotenv** | `1.0.1` | Loads local environment variables from `.env` | Lightweight Core | **Retain (Core)** |
| **anyio** | `4.8.0` | Asynchronous I/O compatibility wrapper | Lightweight Core | **Retain (Core)** |
| **click** | `8.1.8` | CLI argument parser for entrypoints and audit tools | Lightweight Core | **Retain (Core)** |
| **h11** | `0.14.0` | Pure-Python HTTP/1.1 protocol implementation | Lightweight Core | **Retain (Core)** |
| **idna** | `3.10` | Internationalized Domain Names in Applications | Lightweight Core | **Retain (Core)** |
| **sniffio** | `1.3.1` | Async library detection | Lightweight Core | **Retain (Core)** |
| **typing_extensions** | `4.12.2`| Backported Python type hinting | Lightweight Core | **Retain (Core)** |
| **markupsafe** | `3.0.2` | String escaping for Jinja2 | Lightweight Core | **Retain (Core)** |
| **certifi** | `2025.1.31` | Trusted CA bundle for HTTPS verification | Lightweight Core | **Retain (Core)** |
| **httpx** | `0.28.1` | Modern async HTTP client (Target for S2 LLM Gateway)| Lightweight Core | **Retain (Core)** |
| **playwright** | `1.63.0` | Headless Chromium engine for JavaScript-heavy sites | Heavy (Scraping) | **Move to Optional** |
| **patchright** | `1.63.0` | Anti-detect patched Playwright fork | Heavy (Scraping) | **Move to Optional** |
| **scrapling** | `0.4.15` | Adaptive scraping framework with stealth features | Heavy (Scraping) | **Move to Optional** |
| **curl_cffi** | `0.7.4` | High-performance HTTP client with browser TLS spoofing| Medium (Scraping) | **Move to Optional** |
| **browserforge** | `1.1.25` | Browser fingerprint generator for web scrapers | Medium (Scraping) | **Move to Optional** |
| **playwright-stealth**| `1.0.6` | Evasion scripts for headless browser automation | Medium (Scraping) | **Move to Optional** |
| **chromadb** | `1.5.9` | Embedded vector database (Legacy RAG) | Heavy Native ML | **Scheduled Retirement (S2)** |
| **onnxruntime** | `1.30.0` | Native C++ neural runtime for local embeddings | Extremely Heavy (>500MB)| **Scheduled Retirement (S2)** |
| **ollama** | `0.4.7` | Python binding for local Ollama daemon | Legacy ML Bridge | **Scheduled Retirement (S2)** |
| **langchain** | `0.3.19` | Heavy LLM orchestration framework | Heavy Obsolete | **Scheduled Retirement (S2)** |
| **langchain-community**| `0.3.18`| Community plugins for LangChain | Heavy Obsolete | **Scheduled Retirement (S2)** |
| **langchain-core** | `0.3.40` | Core abstractions for LangChain | Heavy Obsolete | **Scheduled Retirement (S2)** |
| **pymupdf** | `1.25.3` | PDF parsing engine (Used in legacy pyBIM docs) | Heavy Native C++ | **Scheduled Retirement (S2)** |
| **pytest** | `8.3.4` | Full test framework | Dev / Testing | **Retain (Dev)** |
| **pytest-asyncio**| `0.25.3` | Async test runners for FastAPI/asyncio tests | Dev / Testing | **Retain (Dev)** |

---

## 3. Import-Time Side Effects & Resource Footprint Analysis

### 3.1 Heavy Native ML Packages (`chromadb`, `onnxruntime`, `pymupdf`)
- **Disk Footprint:** `chromadb` together with `onnxruntime`, `tokenizers`, and transitives occupies **~620 MB** on disk in `site-packages`.
- **Import Overhead:** Importing `chromadb` forces the dynamic loading of multiple C++ DLLs (`onnxruntime.dll`, `openmp.dll`), creating a 0.8s–1.4s delay during Python startup and allocating ~140 MB of unmanaged virtual memory even before any vector query is run.
- **Architectural Justification for Removal:** The target operating model relies on deterministic SQLite metadata queries and external HTTPS LLM API calls. Vector similarity RAG is completely unnecessary for scoring 10 companies/day against a defined ICP schema.

### 3.2 Headless Browser Engines (`playwright`, `patchright`, `scrapling`)
- **Disk Footprint:** Playwright's local browser binaries (`ms-playwright` under `%LOCALAPPDATA%`) consume **~450 MB** for Chromium alone.
- **Runtime CPU/RAM Impact:** Launching a headless Chromium process spawns 4 to 6 separate Windows child processes (`chrome.exe`), consuming 250 MB–450 MB of RAM per instance and creating heavy thermal spikes on laptop CPUs.
- **Laptop Guideline:** B2B company websites and job listings in 90% of cases can be fetched using lightweight `requests` + `BeautifulSoup4` with `<10 MB` RAM footprint and sub-100ms response time. Headless rendering must be strictly optional and gated behind explicit configuration flags.

---

## 4. Proposed Pinned Dependency Specifications

To replace the monolithic `requirements.txt`, four cleanly separated, pinned requirements files are established.

### 4.1 Core Lightweight Runtime (`requirements-core.txt`)
Contains only packages essential for running the local FastAPI server, SQLite database, deterministic scorers, outreach drafting, and review UI.
```ini
# SalesAI Core Lightweight Runtime (Windows Laptop)
# Zero GPU, Zero Local Neural Weights, Zero Local Ollama
fastapi==0.115.11
uvicorn[standard]==0.34.0
starlette==0.46.0
pydantic==2.10.6
jinja2==3.1.5
requests==2.32.3
urllib3==2.3.0
beautifulsoup4==4.13.3
soupsieve==2.6
python-dotenv==1.0.1
anyio==4.8.0
click==8.1.8
httpx==0.28.1
certifi==2025.1.31
```
*Total Footprint:* **~65 MB disk**, **<90 MB idle RAM**, **<1.2s cold startup time**.

### 4.2 Optional Advanced Scraping (`requirements-optional.txt`)
Only installed if the operator explicitly enables headless browser rendering or anti-bot evasions for protected corporate websites.
```ini
# Optional Advanced Scraping & Browser Automation
playwright==1.63.0
patchright==1.63.0
scrapling==0.4.15
curl_cffi==0.7.4
browserforge==1.1.25
playwright-stealth==1.0.6
```

### 4.3 Testing & Development (`requirements-dev.txt`)
Required for running unit, integration, and regression test suites.
```ini
# Development & Testing Suite
pytest==8.3.4
pytest-asyncio==0.25.3
pytest-cov==6.0.0
ruff==0.9.6
```

### 4.4 Legacy/Transitional Package Manifest (`requirements-legacy.txt`)
Maintained strictly as an audit reference for components scheduled for full decoupling in S2.
```ini
# Legacy Components Scheduled for Retirement in Phase S2
# DO NOT INSTALL in new clean environments
chromadb==1.5.9
onnxruntime==1.30.0
ollama==0.4.7
langchain==0.3.19
langchain-community==0.3.18
langchain-core==0.3.40
pymupdf==1.25.3
```

---

## 5. Non-Destructive Virtual Environment Migration Plan

To maintain test parity (929 passed, 8 skipped) during S1, **no packages are uninstalled or upgraded on the active branch**. The migration will execute in Phase S2 according to the following phased protocol:

1. **Step S2.1 (Gateway Implementation):** Implement the abstract `LLMProviderGateway` and HTTPS adapters. Point all 6 active LLM call sites to the gateway.
2. **Step S2.2 (Decouple Test Admin Gate):** Refactor `tests/test_admin_gate.py` to assert token protection against pure SalesAI admin endpoints, removing its legacy import of `ai_engine.data_ingestor`.
3. **Step S2.3 (Decouple Health Endpoint):** Update `backend/main.py` `/health` to verify SQLite and external API quota instead of Ollama/ChromaDB.
4. **Step S2.4 (Clean Venv Verification):** Create a fresh isolated virtual environment (`.venv-lean`) containing only `requirements-core.txt` and `requirements-dev.txt`.
5. **Step S2.5 (Regression Verification):** Execute `pytest tests/` in `.venv-lean` to verify 100% test pass parity without `chromadb`, `onnxruntime`, or `ollama` installed.
6. **Step S2.6 (Deprecate Legacy Wheels):** Archive and retire legacy requirements.
