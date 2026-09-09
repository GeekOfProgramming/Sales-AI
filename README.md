# pyBIM-LLM: Local AI & ISO 19650 RAG Assistant for Autodesk Revit

**pyBIM-LLM** is an enterprise-grade, privacy-first, 100% offline Artificial Intelligence assistant and semantic knowledge retriever designed specifically for **Autodesk Revit** and **BIM Automation**. Operating entirely within an air-gapped or local corporate network, the system translates natural language engineering requirements into deterministic, syntax-validated, and standard-compliant Revit scripts across **pyRevit (Python)**, **Native C# (.NET)**, and **Dynamo Python Script Nodes**.

---

## 📑 Table of Contents

1. [Architectural Overview](#-architectural-overview)
2. [Key Implemented Capabilities](#-key-implemented-capabilities)
3. [System Architecture & Multi-Agent Pipeline](#-system-architecture--multi-agent-pipeline)
4. [Enterprise Security & Admin-Gate Ingestion](#-enterprise-security--admin-gate-ingestion)
5. [Autodesk Construction Cloud (ACC) Integration](#-autodesk-construction-cloud-acc-integration)
6. [Quick Start & Dual Execution Guide](#-quick-start--dual-execution-guide)
   - [Scenario 1: Client-Server Model (Recommended & Seamless Delivery)](#-scenario-1-client-server-model-recommended--seamless-delivery)
   - [Scenario 2: Standalone Full Setup (100% Offline Local Machine)](#-scenario-2-standalone-full-setup-100-offline-local-machine)
   - [Web Studio UI Guide](#-web-studio-ui-guide)
   - [Revit Extension Ribbon Guide](#-revit-extension-ribbon-guide)
   - [Troubleshooting & Quick Utilities](#️-troubleshooting--quick-utilities)
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
- **Stage 1 (Submission & Smart Slugging):** Users submit documentation links with optional custom slugs or automatic smart title slugification (with UUID fallback). The server assigns a unique **Tracking ID** (`req_xxxx`) and isolates the request in `data/pending_ingestions.json` with status `pending`.
- **Public User Tracking:** Users query `GET /api/ingest/status/{tracking_id}` in real-time to track lifecycle progression (`Pending` 🟡, `Processing` ⚙️, `Approved` 🟢, `Rejected` 🔴).
- **Stage 2 (Admin Review Cartable & Collapsible History):**
  - **Active Queue Cartable:** Displays only actionable, pending items with batch and individual **Approve** / **Reject** controls to avoid visual clutter.
  - **Expandable History Archive:** Collapsible log with real-time text search and status filters (`All`, `Approved`, `Rejected`).
  - **One-Click History Purge:** Admins can purge legacy logs via `POST /api/ingest/queue/clear-history` without affecting pending items.
- **Self-Healing Vectorization Auto-Recovery:** On server startup (`lifespan`), pyBIM-LLM inspects the queue for items stranded in `processing` (e.g. from an unexpected restart). If the scraped `.md` exists, it automatically vectorizes and chunks into ChromaDB and promotes the item to `approved`; if missing, it safely reverts status to `pending`.

### 4. JWT Authentication & Security Layer
- Module `backend/auth.py` utilizes **PBKDF2-HMAC-SHA256** password hashing with random salt and **PyJWT** (`HS256`).
- Admin endpoints (`/api/ingest/queue`, `/api/ingest/approve`, `/api/ingest/reject`, `/api/ingest/queue/clear-history`, `/api/acc/config`, `/api/acc/apply-changes`) strictly enforce `Bearer` token verification.

### 5. Autodesk Construction Cloud (ACC) & Cloud BIM Integration
- Module `ai_engine/acc_client.py` and `ai_engine/cloud_auditor.py` connect to **Autodesk Platform Services (APS)** without downloading heavy `.rvt` files or running desktop Revit.
- **In-App APS Credential Manager:** Web Studio includes an administrative modal to configure `APS_CLIENT_ID`, `APS_CLIENT_SECRET`, and toggle Simulation Mode with **zero-downtime hot reloading** and automatic `.env` persistence.
- **Strict Read-Only Mode:** Extracts Model Derivative parameter trees and evaluates:
  - **ISO 19650 Information Container Naming** (e.g. `PRJ-ZZ-00-M3-A-0001`).
  - **Mandatory BIM Parameters:** Verifies `FireRating` on Walls/Doors and classification codes (`OmniClass` / `UniFormat`).
- **Human-in-the-Loop Safety Gate:** The AI only generates non-destructive recommendations. Synchronization to cloud models requires explicit administrator confirmation via `POST /api/acc/apply-changes`.

### 6. Developer Experience & Type Checking (Pyrefly / Pyright)
- Includes root `pyrefly.toml` and `pyrightconfig.json` configurations bound directly to `.venv/Lib/site-packages`.
- Resolves IronPython / CPython hybrid imports for pyRevit extensions and provides zero-configuration type checking in modern IDEs.

---

## 🚀 Quick Start & Dual Execution Guide

To run and experience pyBIM-LLM across both the **Autodesk Revit Extension** and the **Web Studio Dashboard (`/ui`)**, two operational deployment models are supported. Choose the scenario that matches your deployment workflow:

---

### 🎯 Scenario 1: Client-Server Model (Recommended & Seamless Delivery)
> **Concept:** In this scenario, the AI inference server (FastAPI + Ollama + ChromaDB) runs on your primary workstation (or company server). Your colleague, client, or team member **only acts as a Revit Client**. They **do NOT need to install Python, Ollama, CUDA, or heavy AI dependencies**.

```text
┌──────────────────────────────────────┐          Wi-Fi / LAN / Cloudflare          ┌──────────────────────────────────────┐
│       AI Server Workstation          │ ─────────────────────────────────────────► │       Revit Client Workstation       │
│  • Ollama (qwen2.5-coder & nomic)    │          Port 8000 or HTTPS                │  • Autodesk Revit                    │
│  • ChromaDB Vector Store             │                                            │  • pyRevit Free CLI / Installer      │
│  • FastAPI Gateway (run_pybim.bat)   │                                            │  • pyBIM.extension Toolbar           │
└──────────────────────────────────────┘                                            └──────────────────────────────────────┘
```

#### 1. Server Side (AI Workstation):
1. Double-click **[`run_pybim.bat`](file:///c:/Projects/pyBIM-LLM/run_pybim.bat)** in the repository root. This automated launcher will:
   - Verify the Ollama AI daemon on port 11434 (starts it in the background if offline).
   - Terminate any orphaned processes holding port 8000.
   - Start the FastAPI gateway bound to `0.0.0.0:8000`.
   - Automatically launch a secure **Cloudflare Public HTTPS Tunnel** if internet access is detected, save the shareable link to **`PUBLIC_URL.txt`**, and copy it directly to your Windows clipboard!
2. **Obtain the Server Address:**
   - **LAN / Local Wi-Fi:** Run `ipconfig` in Command Prompt and note your IPv4 address (e.g., `http://192.168.1.50:8000` or `http://10.120.24.34:8000`).
   - **Remote / External Access:** Use the public HTTPS tunnel URL provided in the console, copied to your clipboard, and saved in `PUBLIC_URL.txt`.

#### 2. Client Side (Revit User Machine):
1. **Prerequisites:**
   - **Autodesk Revit** (Versions 2020 through 2026).
   - Free [pyRevit CLI / Installer](https://github.com/pyrevitlabs/pyRevit/releases).
2. **Install the pyBIM Extension:**
   Transfer the [`revit_plugin/pyBIM.extension/`](file:///c:/Projects/pyBIM-LLM/revit_plugin/pyBIM.extension) directory to the client machine (or zip and send it). Install using any of the following methods:
   - **1-Click Batch Installer (Fastest):** Run [`revit_plugin/install_pyrevit_extension.bat`](file:///c:/Projects/pyBIM-LLM/revit_plugin/install_pyrevit_extension.bat). It mirrors the extension into `%APPDATA%\pyRevit\Extensions\` and reloads pyRevit automatically.
   - **pyRevit CLI:** Open Command Prompt and run:
     ```cmd
     pyrevit extend ui pyBIM "C:\Path\To\pyBIM.extension"
     ```
   - **Manual Copy:** Copy the `pyBIM.extension` folder directly into `%APPDATA%\pyRevit\Extensions\`.
3. **Connect to the AI Server from Revit:**
   - Open Autodesk Revit; the dedicated **pyBIM** tab will appear in the top Ribbon.
   - In the **AI Automation** panel, click **Config**.
   - Enter your server address (e.g., `http://192.168.1.50:8000` or the Cloudflare HTTPS URL).
   - Click **Test & Save**. A green notification (`✅ Connection Successful`) confirms the connection and displays active models and knowledge vector counts.
4. **Execute Prompts with AI Assistant:**
   - Select one or more elements in the active Revit view (walls, doors, pipes, columns, etc.).
   - Click the **Assistant** button in the **AI Automation** panel.
   - Enter your instruction in plain English (e.g., `Change Unconnected Height of selected walls to 4000mm`).
   - Review the generated script and RAG standards cited in the Revit output window.
   - Click **Yes** to authorize dynamic execution inside an isolated `DB.Transaction`. All modifications support standard Revit Undo (`Ctrl+Z`).
5. **Open Web Studio directly from Revit:**
   - Click the **OpenStudio** button in the **Studio** panel to immediately launch the Web Studio dashboard in your default browser.

---

### 💻 Scenario 2: Standalone Full Setup (100% Offline Local Machine)
> **Concept:** All components (local LLM inference, vector database, FastAPI gateway, and Revit extension) run locally on a single workstation without external network requirements.

#### Step 1: Install System Prerequisites (One-Time)
1. **Python:** Install [Python 3.11 or 3.12](https://www.python.org/downloads/) (ensure *Add python.exe to PATH* is checked during installation).
2. **Ollama:** Install [Ollama](https://ollama.com/) and pull the necessary models:
   ```bash
   ollama pull qwen2.5-coder:1.5b
   ollama pull nomic-embed-text
   ollama pull llama3:latest
   ```
3. **pyRevit:** Install [pyRevit](https://github.com/pyrevitlabs/pyRevit/releases).

#### Step 2: Set Up Virtual Environment & Dependencies
Open a terminal in the project root `C:\Projects\pyBIM-LLM` and run:
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### Step 3: Launch Gateway Server & Web Studio
Double-click **[`run_pybim.bat`](file:///c:/Projects/pyBIM-LLM/run_pybim.bat)** (or run `python start_server.py`).
The server will initialize and automatically open the Web Studio in your default browser at `http://localhost:8000/ui`.

#### Step 4: Register Extension with Revit
Double-click [`revit_plugin/install_pyrevit_extension.bat`](file:///c:/Projects/pyBIM-LLM/revit_plugin/install_pyrevit_extension.bat) or execute:
```cmd
pyrevit extend ui pyBIM "C:\Projects\pyBIM-LLM\revit_plugin\pyBIM.extension"
```
The **Assistant** button in Revit will now connect directly to `http://localhost:8000` on the same workstation.

---

### 🌐 Web Studio UI Guide

The Web Studio enables interactive testing of AI code generation, RAG knowledge retrieval, and parameter auditing without opening Autodesk Revit:

* **Local Access:** `http://localhost:8000/ui`
* **LAN / Wi-Fi Access:** `http://<SERVER-IP>:8000/ui`
* **Interactive Swagger API Docs:** `http://localhost:8000/docs`

#### Key Web Studio Modules:
1. **Script Generator & Revit Element Simulator:**
   - Select the target environment: **pyRevit (Python)**, **Native C# (.NET)**, or **Dynamo Python Node**.
   - Input your engineering prompt and simulate selected Revit elements (Category, Element ID, parameters).
   - Click **Generate Script** to inspect multi-agent validated code and RAG citation sources in seconds.
2. **Admin-Gate Documentation Ingestion & Review Queue:**
   - Submit Revit documentation URLs with custom slugs for Scrapling extraction and vectorization.
   - Track processing progression with public Tracking IDs (`req_xxxx`).
   - Administrator authentication via JWT token to Approve or Reject staged items, preventing Data Poisoning.
3. **Autodesk Construction Cloud (ACC) & Cloud BIM Dashboard:**
   - In-app APS credential manager with zero-downtime hot reloading.
   - Toggle between Simulation Mode and Live Autodesk Platform Services connections.
   - Read-only audit of cloud model parameters against ISO 19650 standards and fire rating requirements.

---

### 🧩 Revit Extension Ribbon Guide

Upon installing the extension, the **pyBIM** tab appears in the top Revit Ribbon with two functional panels:

| Panel | Button | Description & Operational Flow |
| :--- | :--- | :--- |
| **AI Automation** | **`Assistant`** | Reads selected elements from the active document view, collects your natural language prompt, sends metadata to the gateway, displays the generated script for review, and executes inside a `DB.Transaction` upon approval. |
| **AI Automation** | **`Config`** | Tests live server health (`/health`), displays active Ollama models and indexed ChromaDB rule chunks, and persists the server URL in local pyRevit configuration. |
| **Studio** | **`OpenStudio`** | Instantly opens the browser to the Web Studio (`/ui`) with a single click from inside Revit. |

> [!TIP]
> **Human-in-the-Loop Safety:** Scripts are never executed blindly. Generated code is displayed in the Revit output window for inspection. Execution requires an explicit **Yes** confirmation and runs inside a rollback-protected transaction that supports full Undo (`Ctrl+Z`).

---

### 🛠️ Troubleshooting & Quick Utilities

| Task | Command / Tool | Description |
| :--- | :--- | :--- |
| **Stop Server Cleanly** | Double-click [`stop_pybim.bat`](file:///c:/Projects/pyBIM-LLM/stop_pybim.bat) | Kills any running processes listening on port 8000. |
| **Reload Revit Toolbar** | Run `pyrevit reload` in Command Prompt | Reloads pyBIM buttons without closing or restarting Revit. |
| **Server Health Check** | Open `http://localhost:8000/health` | Returns real-time status of Ollama, active model, and vector chunk counts. |
| **Run Test Suite** | Run `pytest tests/ -v` | Executes complete test matrix covering JWT security, queue cartable, and ACC integration. |
| **Revit Connection Issues** | Check port 8000 and Windows Firewall | Ensure Windows Defender / Firewall allows inbound connections on port 8000 for LAN clients. |

---

## 📡 API Gateway Reference

| Method | Endpoint | Access Level | Description |
| :--- | :--- | :---: | :--- |
| `GET` | `/health` | Public | Real-time health status of Ollama, ChromaDB, and indexed rule counts. |
| `GET` | `/ui` | Public | Interactive Web Studio dashboard. |
| `POST` | `/generate-script` | Public | Primary endpoint generating Revit code (pyRevit, C#, Dynamo) via Multi-Agent pipeline. |
| `POST` | `/api/auth/login` | Public | Authenticates administrator credentials and returns signed JWT access token. |
| `POST` | `/api/ingest` | Public | **Stage 1:** Submits a documentation URL to the Admin Review Queue (returns Tracking ID). |
| `GET` | `/api/ingest/status/{id}`| Public | Queries real-time status (`pending`, `processing`, `approved`, `rejected`) of a submitted item. |
| `GET` | `/api/ingest/queue` | **Admin JWT** | Retrieves list of all pending or archived submissions in Knowledge Queue. |
| `POST` | `/api/ingest/approve` | **Admin JWT** | **Stage 2:** Approves item, launches background Scrapling, and injects vectors into ChromaDB. |
| `POST` | `/api/ingest/reject` | **Admin JWT** | Rejects unverified item without vector store modification. |
| `POST` | `/api/ingest/queue/clear-history` | **Admin JWT** | Clears completed/rejected archive history while preserving pending items. |
| `GET` | `/api/acc/config` | Public | Retrieves current ACC / APS configuration state and active mode (Live vs Simulation). |
| `POST` | `/api/acc/config` | **Admin JWT** | Updates APS credentials, toggles simulation mode, and hot-reloads client with `.env` persistence. |
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
- ✅ `test_smart_slug_generation_and_guid_upgrade`: Validates intelligent URL title slugification and collision-proof GUID fallbacks.
- ✅ `test_acc_hubs_and_projects_discovery`: Navigation of ACC Hubs, Projects, and Models.
- ✅ `test_cloud_model_read_only_rag_audit`: Read-Only audit validation (ISO 19650, FireRating, OmniClass).
- ✅ `test_human_in_the_loop_write_back_protection`: Blocks unauthorized cloud write-backs.
- ✅ `test_acc_config_status_public_query`: Verifies public visibility of APS configuration mode (Live vs Simulation).
- ✅ `test_acc_config_save_requires_admin_token`: Enforces JWT security on APS credential modification.
- ✅ `test_acc_config_lifecycle_with_admin`: End-to-end hot-reloading lifecycle and `.env` persistence.

---

## 🗺️ Project Status & Roadmap

Based on the master roadmap ([`C:\Projects\Roadmap-LLMs.md`](file:///c:/Projects/Roadmap-LLMs.md)):

### ✅ Completed & Active Milestones
1. **Local Inference Engine:** Ollama integration with `qwen2.5-coder:1.5b` and dynamic model routing.
2. **Persistent Vector RAG:** ChromaDB with `nomic-embed-text` and 308+ verified BIM rule chunks.
3. **Scrapling Web Extraction:** Automated extraction and Markdown sanitization of online docs.
4. **FastAPI Gateway & Web Studio:** Dark-mode glassmorphism interface at `/ui` with element simulation.
5. **Revit Client Integration:** Native C# Add-in (`Commands.cs`) and complete `pyBIM.extension` toolbar.
6. **Dynamo Execution Node Support:** Prompt segregation for `TransactionManager`, `UnwrapElement(IN[x])`, and `OUT`.
7. **Multi-Agent System (CrewAI):** Parallel Developer + QA Reviewer agents with internal feedback cycles.
8. **Compliance Auto-Remediation:** Automated patching of missing transactions, namespaces, and Dynamo contracts.
9. **Authentication & Access Control (JWT):** PBKDF2 password encryption and protected admin endpoints.
10. **Admin-Gate Ingestion Workflow:** 2-stage verification queue preventing corporate Data Poisoning.
11. **Self-Healing Vectorization Recovery:** Auto-detection and recovery of interrupted ingestion jobs on startup.
12. **In-App APS/ACC Credential Hot-Reload:** Dynamic configuration modal with live testing and `.env` persistence.
13. **Cloud BIM Integration (Autodesk Construction Cloud - ACC):** Read-Only metadata audit and Human-in-the-loop controls.
14. **Type Checking & Tooling Suite:** Seamless IDE support via `pyrefly.toml` and `pyrightconfig.json`.

---

### 🔮 Under Active Development & Architectural Expansion

* **Interactive Code Preview & Human-in-the-Loop:**
  * **Scope:** Implementation of a mandatory safety gate for all AI-generated scripts and metadata updates prior to execution in the target software.
  * **Mechanism:** Proposed code or parameter adjustments are rendered as visual diffs and comparison tables in the client interface, requiring explicit engineer authorization before execution.

* **Self-Healing Code Pipeline:**
  * **Scope:** Establishment of a closed-loop feedback mechanism between the Revit client and the AI server.
  * **Mechanism:** When a runtime exception occurs in Revit, the complete exception traceback and offending code are returned to the server, enabling QA Reviewer agents to diagnose the root cause and generate a rectified script without requiring manual user intervention.

* **Knowledge Versioning & Rollback:**
  * **Scope:** Implementation of a version control layer for ChromaDB vector embeddings and corporate Markdown documentation.
  * **Mechanism:** Enables visual rule diffing, version archiving, and instant rollback to previous stable knowledge baselines if regression or output degradation is detected.

* **Semantic Caching Layer:**
  * **Scope:** Deployment of an intelligent vector-similarity caching layer within the FastAPI gateway.
  * **Mechanism:** Recurrent engineering prompts (such as standard category filters or area schedules) are served from cache in under 50ms without invoking redundant Ollama inference.

* **Element Dependency Graph Analysis:**
  * **Scope:** Development of a local client-side preprocessor to extract hierarchical and topological relationships of selected Revit elements (e.g., wall-to-column or slab-to-level attachments).
  * **Mechanism:** Injects physical connectivity context directly into AI prompts to prevent geometric disconnects, collision errors, or inadvertent deletion of dependent elements.

---

### ⏳ Pending Actions & Live Validation

* **End-to-End Live Validation:**
  * **Scope:** Deployment of the `pyBIM.extension` toolbar and native .NET add-in onto the production client workstation (Laptop 3) connected to live Autodesk Revit models.
  * **Objective:** Validation of real-world automation scenarios (parameter modification, fire rating enforcement, ISO 19650 container verification) on complex architectural and structural models over local network and encrypted tunnels.

* **RAG Error Calibration:**
  * **Scope:** Extraction of execution logs and tracebacks from live Revit sessions to refine semantic chunking parameters and retrieval boundaries.
  * **Objective:** Optimization of semantic retrieval precision and elimination of syntax incompatibilities across Revit API versions (2023 through 2026).

* **Containerization & Docker Orchestration:**
  * **Scope:** Authoring production `Dockerfile` and `docker-compose.yml` configurations for unified deployment of the FastAPI gateway, ChromaDB vector store, and dependencies.
  * **Objective:** Ensuring deployment reproducibility, process isolation, and single-command startup across corporate server environments.

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
├── pyrefly.toml              # Pyrefly type checker path resolution config
├── pyrightconfig.json        # Pyright / VS Code virtualenv environment config
├── requirements.txt          # Python dependencies
├── run_pybim.bat             # 1-click server & browser launcher
├── start_server.py           # Unified multi-laptop launcher & tunnel helper
└── stop_pybim.bat            # Clean process terminator
```

---

## 🔒 Privacy & Air-Gapped Guarantee

pyBIM-LLM does **not** transmit any model geometry, intellectual property, or code to third-party public AI providers (OpenAI, Anthropic, etc.). All model inference, semantic retrieval, and parameter evaluations are executed locally on your internal hardware. Cloud integrations with Autodesk Construction Cloud utilize direct, encrypted (TLS 1.3) communication exclusively with Autodesk's official servers in **Strict Read-Only Mode**.