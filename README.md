# pyBIM-LLM: Local AI & ISO 19650 RAG Assistant for Autodesk Revit

**pyBIM-LLM** is an enterprise-grade, privacy-first, 100% offline Artificial Intelligence assistant and knowledge retriever for Autodesk Revit. It leverages local Large Language Models (LLMs) via Ollama and a vector database (ChromaDB) to generate deterministic, standard-compliant Revit Python (`pyRevit`) and Revit .NET C# code without any external cloud API dependencies.

---

## 🏗️ Architecture Overview

The system consists of 4 decoupled tiers:

1. **AI Core & Local Inference (`ai_engine/llm_client.py`)**:
   - Built on top of **Ollama** running locally.
   - Default Code Generation Model: `qwen2.5-coder:1.5b` (lightweight, rapid inference, code-specialized).
   - System prompts engineered specifically for Autodesk Revit API object model, Transactions, and Filters.

2. **Semantic Knowledge Base & RAG (`ai_engine/rag_retriever.py`, `data/rules/`)**:
   - Persistent vector database powered by **ChromaDB**.
   - Embeddings: Local `nomic-embed-text` running via Ollama.
   - Dynamic injection of ISO 19650 naming rules, Transaction safety guidelines, and category filtering logic.

3. **FastAPI Gateway & Web UI (`backend/`)**:
   - High-throughput REST API coordinating requests from Revit clients.
   - Glassmorphism dark-mode Web UI Studio at `/ui`.
   - Comprehensive endpoints:
     - `GET /`: Serves Web Studio dashboard.
     - `GET /health`: Real-time health check for Ollama & ChromaDB.
     - `POST /generate-script`: Accepts BIM prompts and element metadata, generates validated Revit scripts.
     - `GET /docs`: Interactive Swagger API documentation.

4. **Autodesk Revit Integration Bridge (`revit_plugin/`)**:
   - **pyRevit (`revit_plugin/pyRevitScript.py`)**: Direct toolbar script capturing active element selection, communicating with the AI gateway, and executing in-memory transactions.
   - **Native C# Add-in (`revit_plugin/Commands.cs` & `pyBIM.addin`)**: Production `.NET` `IExternalCommand` with native Revit UI dialogs.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/) installed and running.
- Pull the required local models:
  ```bash
  ollama pull qwen2.5-coder:1.5b
  ollama pull nomic-embed-text
  ```

### 2. Environment Setup
Activate the virtual environment and install dependencies:
```bash
cd C:\Projects\pyBIM-LLM
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Start the Server (1-Click or Command Line)

**Option A (1-Click Launcher):**
Simply double-click **`run_pybim.bat`** in the project folder. It will:
- Auto-start Ollama if offline.
- Activate the virtual environment.
- Start the server on `0.0.0.0:8000`.
- Automatically open `http://localhost:8000/ui` in your browser.

To stop the server at any time, double-click **`stop_pybim.bat`**.

**Option B (Manual Terminal):**
```bash
python start_server.py
```
This binds to `0.0.0.0:8000` and displays:
- **Local Access (Laptop 1):** `http://localhost:8000/ui`
- **LAN Access (Laptops 2 & 3):** `http://<your-lan-ip>:8000/ui`
- **Revit Plugin Target:** `http://<your-lan-ip>:8000/generate-script`

---

## 🧪 Running the Test Suite

All modules have comprehensive automated test suites:

### Run Everything with Pytest:
```bash
pytest -v -s
```

### Run Individual Test Suites:
| Test Script | Component Verified |
| :--- | :--- |
| `python test_inference.py` | Ollama connectivity & direct LLM code generation (Python & C#) |
| `python test_rag.py` | ChromaDB indexing, semantic search & ISO 19650 rule retrieval |
| `python test_backend.py` | FastAPI endpoints (`/health`, `/generate-script`, `/ui`) |
| `python test_revit_bridge.py` | Simulation of pyRevit & C# Add-in payloads against Gateway |
| `python test_web_ui.py` | Web UI serving and dashboard metric feeds |
| `python test_ingestion.py` | Scrapling HTML extraction, Markdown conversion & auto-indexing |

---

## 🌐 Online Knowledge Ingestion (Powered by Scrapling)

You can scrape and inject online Revit API docs or BIM guidelines into the ChromaDB vector database using the CLI tool:

```bash
# Ingest an online documentation page:
python ingest_doc.py https://example.com/revit-api/rooms --slug revit_api_rooms

# Ingest a local Markdown guide:
python ingest_doc.py path/to/company_standards.md --slug company_standards
```

---

## 📁 Repository Structure

```text
pyBIM-LLM/
├── ai_engine/
│   ├── __init__.py
│   ├── data_ingestor.py      # Scrapling web extraction & Markdown cleaner
│   ├── llm_client.py         # Ollama client & system prompt templates
│   └── rag_retriever.py      # ChromaDB retriever & Ollama embedding adapter
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI application gateway & route handlers
│   ├── schemas.py           # Pydantic data contracts (Requests/Responses)
│   └── static/              # Web UI Studio (HTML, CSS, JS)
├── chroma_db/               # Persistent ChromaDB vector storage
├── data/
│   └── rules/               # BIM rules & ISO standards in Markdown
│       ├── iso19650_naming.md
│       ├── revit_api_transactions.md
│       └── revit_categories_filtering.md
├── revit_plugin/
│   ├── install_pyrevit_extension.bat  # 1-click pyRevit extension installer for Laptop 3
│   ├── Commands.cs                    # Native C# Revit IExternalCommand
│   ├── pyBIM.addin                    # Revit Add-in manifest file
│   ├── pyRevitScript.py               # Standalone pyRevit script
│   └── pyBIM.extension/               # Complete pyRevit Ribbon Extension Package
│       ├── extension.json
│       └── pyBIM.tab/
│           ├── AI Automation.panel/
│           │   ├── Assistant.pushbutton/ (bundle.yaml, icon.png, script.py)
│           │   └── Config.pushbutton/    (bundle.yaml, icon.png, script.py)
│           └── Studio.panel/
│               └── OpenStudio.pushbutton/(bundle.yaml, icon.png, script.py)
├── requirements.txt         # Project dependencies
├── start_server.py          # Unified multi-laptop server launcher
├── test_backend.py          # Backend API tests
├── test_inference.py        # LLM inference tests
├── test_rag.py              # RAG knowledge base tests
├── test_revit_bridge.py     # Revit plugin simulation tests
└── test_web_ui.py           # Web UI serving tests
```

---

## 🔒 Privacy & Offline Guarantee

pyBIM-LLM does **not** transmit any code, model information, or metadata to external servers. All inference runs locally on the host machine using Ollama and ChromaDB.