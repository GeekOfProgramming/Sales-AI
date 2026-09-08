# pyBIM-LLM: Local AI & ISO 19650 RAG Assistant for Autodesk Revit

**pyBIM-LLM** is an enterprise-grade, privacy-first, 100% offline Artificial Intelligence assistant and semantic knowledge retriever designed specifically for **Autodesk Revit** and **BIM Automation**. Operating entirely within an air-gapped or local corporate network, the system translates natural language engineering requirements into deterministic, syntax-validated, and standard-compliant Revit scripts across **pyRevit (Python)**, **Native C# (.NET)**, and **Dynamo Python Script Nodes**.

---

## 📑 Table of Contents

1. [Architectural Overview](#-architectural-overview)
2. [Key Implemented Capabilities](#-key-implemented-capabilities)
3. [System Architecture & Multi-Agent Pipeline](#-system-architecture--multi-agent-pipeline)
4. [Enterprise Security & Admin-Gate Ingestion](#-enterprise-security--admin-gate-ingestion)
5. [Autodesk Construction Cloud (ACC) Integration](#-autodesk-construction-cloud-acc-integration)
6. [Quick Start & Deployment](#-quick-start--deployment)
7. [API Gateway Reference](#-api-gateway-reference)
8. [Comprehensive Test Suite](#-comprehensive-test-suite)
9. [Project Status & Roadmap](#-project-status--roadmap)
10. [Repository Structure](#-repository-structure)
11. [Privacy & Air-Gapped Guarantee](#-privacy--air-gapped-guarantee)

---

## 🏗️ Architectural Overview

The platform operates as a distributed system across a 3-laptop / multi-workstation setup (or local single-machine development):

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                   Laptop 1: AI Core & Workstation Server                 │
│                                                                          │
│   ┌─────────────────────┐    ┌───────────────────────────────────────┐   │
│   │   Ollama Engine     │    │   ChromaDB Vector Store               │   │
│   │  • qwen2.5-coder    │◄───┤  • nomic-embed-text (308 chunks)      │   │
│   │  • llama3 (NLP)     │    │  • ISO 19650, Revit API, Dynamo rules │   │
│   └──────────▲──────────┘    └──────────────────▲────────────────────┘   │
│              │                                  │                        │
│   ┌──────────┴──────────────────────────────────┴────────────────────┐   │
│   │                  FastAPI Gateway & AI Orchestrator               │   │
│   │  • Semantic Intent Router (Code vs Explanation)                  │   │
│   │  • CrewAI Multi-Agent Pipeline (Developer + QA Reviewer)         │   │
│   │  • Strict Compliance Auto-Remediation (Namespaces/Transactions)  │   │
│   │  • Admin-Gate 2-Stage Review Queue (Anti-Poisoning Guard)        │   │
│   │  • JWT Authentication Layer (PBKDF2 Salted Passwords)            │   │
│   │  • Autodesk Construction Cloud (ACC) Read-Only Auditor           │   │
│   └──────────────────────────────▲───────────────────────────────────┘   │
└──────────────────────────────────┼───────────────────────────────────────┘
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       │ LAN Network (Port 8000) / Cloudflare Encrypted Tunnel │
       └───────────────────────────┬───────────────────────────┘
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         ▼                                                   ▼
┌───────────────────────────────────┐   ┌───────────────────────────────────┐
│   Laptop 2: Admin & Web Studio    │   │     Laptop 3: Revit Client        │
│                                   │   │                                   │
│ • Glassmorphism Studio (/ui)      │   │ • pyRevit Extension Toolbar       │
│ • Admin Knowledge Queue Review    │   │ • Native C# .NET Add-in           │
│ • User Submission & Live Tracking │   │ • Dynamo Python Node Bridge       │
│ • Cloud BIM (ACC) Audit Dashboard │   │ • In-Memory Active Model Context  │
└───────────────────────────────────┘   └───────────────────────────────────┘
```

---

## 🌟 Key Implemented Capabilities

### 1. Multi-Environment Code Generation
- **🐍 pyRevit (Python):** Native scripts utilizing `DB.FilteredElementCollector`, `DB.Transaction(doc)`, and pyRevit UI forms.
- **⚡ Native C# (.NET):** Production-ready `IExternalCommand` implementations with `[Transaction(TransactionMode.Manual)]`, LINQ queries, and complete Revit namespace imports.
- **⚙️ Dynamo Python Script:** Automatic injection of Dynamo execution contracts:
  - `clr.AddReference('RevitNodes')` & `clr.AddReference('RevitServices')`
  - `TransactionManager.Instance.EnsureInTransaction(doc)` and `TransactionTaskDone()`
  - Automatic `UnwrapElement(IN[0])` extraction and assignment to `OUT`.

### 2. Semantic Routing & Multi-Agent Crew Pipeline
- **Intent Router:** Automatically classifies user prompts using regex and keyword heuristics:
  - `text_generation`: Routes explanatory/theoretical queries directly to NLP models (`llama3`).
  - `code_generation`: Routes automation commands to the **CrewAI Multi-Agent Pipeline**.
- **Two-Agent Feedback Loop (`ai_engine/crew_orchestrator.py`):**
  - **BIM Developer Agent:** Drafts Revit automation code based on official RAG standards.
  - **QA Reviewer Agent:** Audits the generated script against a strict Revit API checklist (Transactions, unwrapping, imports, category validity).
- **Auto-Remediation Layer:** Automatically patches missing namespaces, unclosed transactions, or missing Dynamo `OUT` variables before delivering code to the client.

### 3. Enterprise Admin-Gate Ingestion (Anti-Poisoning Architecture)
- Prevents malicious or unverified documentation from corrupting the vector database.
- **Stage 1 (Submission):** Users submit documentation links; the server assigns a unique **Tracking ID** (`req_xxxx`) and isolates the request in `data/pending_ingestions.json` with status `pending`.
- **Public User Tracking:** Users query `GET /api/ingest/status/{tracking_id}` in real-time to see their request status (`Pending` 🟡, `Approved` 🟢, `Rejected` 🔴).
- **Stage 2 (Admin Cartable):** Administrators log in via JWT and review the **Knowledge Queue** in Web Studio. Clicking **Approve** triggers background Scrapling, chunking, and ChromaDB vector indexing. Clicking **Reject** archives the request with an audit justification.

### 4. JWT Authentication & Security Layer
- Module `backend/auth.py` utilizes **PBKDF2-HMAC-SHA256** password hashing with random salt and **PyJWT** (`HS256`).
- Admin endpoints (`/api/ingest/queue`, `/api/ingest/approve`, `/api/ingest/reject`, `/api/acc/apply-changes`) strictly enforce `Bearer` token verification.

### 5. Autodesk Construction Cloud (ACC) & Cloud BIM Integration
- Module `ai_engine/acc_client.py` and `ai_engine/cloud_auditor.py` connect to **Autodesk Platform Services (APS)** without downloading heavy `.rvt` files or running desktop Revit.
- **Strict Read-Only Mode:** Extracts Model Derivative parameter trees and evaluates:
  - **ISO 19650 Information Container Naming** (e.g. `PRJ-ZZ-00-M3-A-0001`).
  - **Mandatory BIM Parameters:** Verifies `FireRating` on Walls/Doors and classification codes (`OmniClass` / `UniFormat`).
- **Human-in-the-Loop Safety Gate:** The AI only generates non-destructive recommendations. Synchronization to cloud models requires explicit administrator confirmation via `POST /api/acc/apply-changes`.

---

## 🚀 Quick Start & Deployment

### 1. Prerequisites
- Python 3.10 to 3.12 (Tested on Python 3.12.4 x64).
- [Ollama](https://ollama.com/) running locally.
- Pull the primary models:
  ```bash
  ollama pull qwen2.5-coder:1.5b
  ollama pull nomic-embed-text
  ollama pull llama3:latest   # Optional: for explanatory text queries
  ```

### 2. Installation
Clone the repository and install dependencies in a virtual environment:
```powershell
cd C:\Projects\pyBIM-LLM
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Server Startup (1-Click or Manual)
**Option A — 1-Click Launcher (`run_pybim.bat`):**
Double-click `run_pybim.bat` in the project root. It will:
- Pre-kill any orphaned processes on port 8000.
- Verify Ollama and local models.
- Start the server on `0.0.0.0:8000`.
- Automatically launch the browser to `http://localhost:8000/ui`.

**Option B — Python Launcher:**
```powershell
python start_server.py
```

---

## 📡 API Gateway Reference

| Method | Endpoint | Access Level | Description |
| :--- | :--- | :---: | :--- |
| `GET` | `/health` | Public | Real-time health status of Ollama, ChromaDB, and indexed rule counts. |
| `GET` | `/ui` | Public | Interactive Web Studio dashboard. |
| `POST` | `/generate-script` | Public | Primary endpoint generating Revit code (pyRevit, C#, Dynamo) via Multi-Agent pipeline. |
| `POST` | `/api/auth/login` | Public | Authenticates administrator credentials and returns signed JWT access token. |
| `POST` | `/api/ingest` | Public | **Stage 1:** Submits a documentation URL to the Admin Review Queue (returns Tracking ID). |
| `GET` | `/api/ingest/status/{id}`| Public | Queries real-time status (`pending`, `approved`, `rejected`) of a submitted item. |
| `GET` | `/api/ingest/queue` | **Admin JWT** | Retrieves list of all pending or archived submissions in Knowledge Queue. |
| `POST` | `/api/ingest/approve` | **Admin JWT** | **Stage 2:** Approves item, launches background Scrapling, and injects vectors into ChromaDB. |
| `POST` | `/api/ingest/reject` | **Admin JWT** | Rejects unverified item without vector store modification. |
| `GET` | `/api/acc/hubs` | Public | Lists accessible Autodesk Construction Cloud hubs. |
| `GET` | `/api/acc/projects/{hub}`| Public | Lists active cloud projects inside an ACC Hub. |
| `GET` | `/api/acc/models/{proj}` | Public | Lists Revit models (`.rvt`) in cloud project without downloading. |
| `POST` | `/api/acc/audit` | Public | **Read-Only:** Audits cloud model metadata against ISO 19650 and mandatory parameters. |
| `POST` | `/api/acc/apply-changes`| **Admin JWT** | **Human-in-the-Loop:** Authorizes staged parameter corrections for cloud synchronization. |

---

## 🧪 Comprehensive Test Suite

The project includes unit, integration, and security test suites executed via `pytest`:

```powershell
# Run the entire test suite:
pytest tests/ -v

# Run individual test modules:
pytest tests/test_acc_integration.py -v   # ACC Cloud BIM & Read-Only Audit tests
pytest tests/test_admin_gate.py -v        # JWT Security & 2-Stage Queue tests
```

### Verified Test Matrix:
- ✅ `test_public_user_submission_creates_pending_request`: Verifies quarantine in pending queue with tracking ID.
- ✅ `test_unauthenticated_queue_access_blocked`: Enforces 401 Unauthorized on unauthenticated queue operations.
- ✅ `test_admin_authentication_and_rejection_flow`: Full login, token verification, and rejection workflow.
- ✅ `test_admin_approval_triggers_processing`: Full approval lifecycle and vector indexing trigger.
- ✅ `test_acc_hubs_and_projects_discovery`: Navigation of ACC Hubs, Projects, and Models.
- ✅ `test_cloud_model_read_only_rag_audit`: Read-Only audit validation (ISO 19650, FireRating, OmniClass).
- ✅ `test_human_in_the_loop_write_back_protection`: Blocks unauthorized cloud write-backs.

---

## 🗺️ Project Status & Roadmap

Based on the centralized master roadmap ([`C:\Projects\Roadmap-LLMs.md`](file:///c:/Projects/Roadmap-LLMs.md)):

### ✅ Completed Milestones (تکمیل‌شده و فعال)
1. **Local Inference Engine:** Ollama integration with `qwen2.5-coder:1.5b` and dynamic model routing.
2. **Persistent Vector RAG:** ChromaDB with `nomic-embed-text` and 308 verified BIM rule chunks.
3. **Scrapling Web Extraction:** Automated extraction and Markdown sanitization of online docs.
4. **FastAPI Gateway & Web Studio:** Dark-mode glassmorphism interface at `/ui` with element simulation.
5. **Revit Client Integration:** Native C# Add-in (`Commands.cs`) and complete `pyBIM.extension` toolbar.
6. **Dynamo Execution Node Support:** Prompt segregation for `TransactionManager`, `UnwrapElement(IN[x])`, and `OUT`.
7. **Multi-Agent System (CrewAI):** Parallel Developer + QA Reviewer agents with internal feedback cycles.
8. **Compliance Auto-Remediation:** Automated patching of missing transactions, namespaces, and Dynamo contracts.
9. **Authentication & Access Control (JWT):** PBKDF2 password encryption and protected admin endpoints.
10. **Admin-Gate Ingestion Workflow:** 2-stage verification queue preventing corporate Data Poisoning.
11. **Offline Document Parsing Foundation:** Direct multi-format extraction capabilities.
12. **Cloud BIM Integration (Autodesk Construction Cloud - ACC):** Read-Only metadata audit and Human-in-the-loop controls.

### ⏳ Pending Actions (معوقه / نیازمند اقدام)
1. **End-to-End Live Validation:** Deploying the plugin onto Laptop 3 and executing AI scripts on live Revit models.
2. **RAG Error Calibration:** Extracting Revit execution logs to fine-tune semantic chunking boundaries.
3. **Containerization:** Finalizing `docker-compose.yml` for unified deployment.
4. **Git Version Control:** Final staging, committing, and authorized repository synchronization.

### 🔮 Expansion Roadmap (مسیرهای توسعه و ارتقاء معماری)
- **Interactive Code Preview & Human-in-the-Loop:** Mandatory visual diff and parameter preview before code executes in Revit or synchronizes to the cloud.
- **Self-Healing Code Pipeline:** Automated feedback loop catching Revit execution exceptions, sending tracebacks to the server, and auto-correcting code without user intervention.
- **Knowledge Versioning & Rollback:** Diff and instant rollback mechanisms for ChromaDB and Markdown rules.
- **Semantic Caching:** High-speed vector similarity cache on FastAPI to return verified answers instantly for common queries.
- **Element Dependency Graph Analysis:** Pre-processing topological and geometric relationships (walls, columns, levels) to prevent physical model collisions.

---

## 📁 Repository Structure

```text
pyBIM-LLM/
├── ai_engine/
│   ├── __init__.py
│   ├── acc_client.py         # Autodesk Platform Services (APS / ACC) client & simulator
│   ├── cloud_auditor.py      # Read-only ISO 19650 & BIM parameter auditor
│   ├── crew_orchestrator.py  # Multi-Agent Developer + QA Reviewer pipeline
│   ├── data_ingestor.py      # Scrapling web extraction & Markdown cleaner
│   ├── ingest_queue.py       # Admin-Gate persistent queue manager (data/pending_ingestions.json)
│   ├── llm_client.py         # Ollama client with dynamic model routing
│   └── rag_retriever.py      # ChromaDB retriever & vector search adapter
├── backend/
│   ├── __init__.py
│   ├── auth.py               # JWT authentication & PBKDF2 password hashing
│   ├── main.py               # FastAPI application gateway & API controllers
│   ├── schemas.py            # Pydantic data contracts (Requests/Responses)
│   └── static/               # Web UI Studio
│       ├── app.js            # Client-side state, queue management & ACC auditor
│       ├── index.html        # Responsive Studio dashboard & admin cartable
│       └── style.css         # Glassmorphism dark-mode theme
├── chroma_db/                # Persistent ChromaDB vector database files
├── data/
│   ├── pending_ingestions.json # Admin-Gate quarantine storage
│   └── rules/                # Official ISO 19650 & Revit API Markdown rules
├── revit_plugin/
│   ├── Commands.cs           # Native C# Revit IExternalCommand
│   ├── pyBIM.addin           # Revit Add-in manifest file
│   ├── pyRevitScript.py      # Standalone pyRevit script
│   └── pyBIM.extension/      # Complete pyRevit Ribbon Extension Package
├── tests/
│   ├── test_acc_integration.py # ACC Cloud BIM & Read-Only audit tests
│   └── test_admin_gate.py     # JWT & Admin-Gate queue unit tests
├── requirements.txt          # Python dependencies
├── run_pybim.bat             # 1-click server & browser launcher
├── start_server.py           # Unified multi-laptop launcher & tunnel helper
└── stop_pybim.bat            # Clean process terminator
```

---

## 🔒 Privacy & Air-Gapped Guarantee

pyBIM-LLM does **not** transmit any model geometry, intellectual property, or code to third-party public AI providers (OpenAI, Anthropic, etc.). All model inference, semantic retrieval, and parameter evaluations are executed locally on your internal hardware. Cloud integrations with Autodesk Construction Cloud utilize direct, encrypted (TLS 1.3) communication exclusively with Autodesk's official servers in **Strict Read-Only Mode**.