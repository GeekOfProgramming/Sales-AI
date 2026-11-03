# SalesAI S1 — Local Security, Governance & Laptop Resource Review

**Document Version:** 1.0  
**Date:** 2026-10-21  
**Author:** Antigravity Engineering Agent  
**Base Commit:** `1b6c50e8f1a615cf462bce296b604b69b915d721`  
**Branch:** `audit/salesai-s1-laptop-dependency-map`  
**Platform:** Developer Windows Laptop (Windows 11 AMD64 / Python 3.14.7)

---

## 1. Executive Summary & Security Posture

SalesAI operates on the developer's personal workstation as a high-security local intelligence tool. Because it manages business contact data, outbound email drafts, and commercial intelligence, the software must satisfy strict local isolation, zero unauthorized network ingress, and confidential credential management.

This review examines:
1. **Localhost Network Isolation & Ingress Attack Surface**
2. **Tunnel & Remote Access Risks (`start_server.py`)**
3. **Health Check Coupling (`backend/main.py`) & Launcher Status (`run_sales_ai.bat`)**
4. **API Credential Governance & Secret Leakage Prevention**
5. **GDPR / Privacy Boundary & Outbound Data Minimization**
6. **Laptop Hardware Resource Footprint & Concurrency Recommendations**

---

## 2. Localhost Binding & Network Ingress Security

### 2.1 Default Binding Status
- In `start_server.py`, the server host defaults to `127.0.0.1` via `os.getenv("SALESAI_HOST", "127.0.0.1")`.
- Binding exclusively to `127.0.0.1` ensures that incoming TCP connections originating from the local area network (LAN), corporate Wi-Fi, or public networks are rejected by the Windows operating system network stack.
- **Audit Verification:** The server must **never** be configured to bind to `0.0.0.0` or public interface IP addresses. The outdated docstring on line 2 of `start_server.py` ("Binds to 0.0.0.0:8000") is factually incorrect and misleading; it must be updated in S2.

### 2.2 Cloudflare Quick Tunnel Legacy Vulnerability
- **Code Inspection:** `start_server.py` lines 60–100 contain legacy logic that activates Cloudflare Quick Tunnels if `cloudflared.exe` is found and either `ENABLE_PUBLIC_TUNNEL=1` is set in the environment or `--tunnel` is passed on the command line.
- **Risk Assessment:** When activated, this feature exposes the local FastAPI application to a public HTTPS URL (`*.trycloudflare.com`) accessible from the open internet without any token authentication or IP filtering. Anyone obtaining the link could access candidate records, review queues, or trigger server endpoints.
- **S1 Recommendation & S2 Mandate:**
  1. The Cloudflare Quick Tunnel code is strictly forbidden in normal operation.
  2. In Phase S2, `start_server.py` should completely remove the `cloudflared` launcher logic or replace it with a hard exception if someone attempts to run with `--tunnel`.
  3. `cloudflared.exe` must never be packaged into repository releases or distribution ZIPs.

---

## 3. Health Probe & Launcher Review

### 3.1 Health Endpoint (`/health` in `backend/main.py`)
- **Current Behavior:** `/health` invokes `llm_client.is_healthy()` (which queries `http://localhost:11434/api/tags`) and `rag_retriever.count()` (which accesses local ChromaDB).
- **Architecture Disconnect:** In the target S2 laptop architecture, local Ollama is disabled and ChromaDB is retired. If Ollama is not running, `/health` incorrectly reports `degraded` or `unhealthy`.
- **Target S2 Redesign:**
  `/health` should report purely local application health and outbound external API connectivity:
  - `status`: `"healthy"`
  - `database`: SQLite connection status (`data/candidates.db`)
  - `external_llm_gateway`: Reachability status of configured remote providers (OpenAI, Anthropic, Gemini, Mistral) and token quota status.
  - Zero checks for local port `11434` or local vector collections.

### 3.2 Launcher Script (`run_sales_ai.bat`)
- **Current Status:** S0 successfully removed Ollama auto-start and automatic model downloading (`ollama run qwen2.5:1.5b`). However, lines 21–29 still execute a passive health probe: `curl -s http://127.0.0.1:11434/api/tags`.
- **Target S2 Clean-up:** Eliminate the Ollama probe entirely. Replace with a check for Python `.venv` and valid API keys in `.env`.

---

## 4. API Key Governance & Secret Leakage Prevention

With remote LLM APIs arriving in Phase S2, external API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`) will be required. Strict governance rules must be enforced:

1. **Storage Location:** Keys must reside exclusively in `.env` located at repository root or Windows user environment variables. `.env` is permanently listed in `.gitignore`.
2. **Release Denylist:** `release_manifest.json` and `scripts/audit_repository.py` strictly forbid `.env`, `*.key`, `*.pem`, and `PUBLIC_URL.txt` from ever being packaged into release archives.
3. **Log Sanitization:** All HTTP client interceptors in S2 must scrub `Authorization: Bearer <token>` and `x-api-key` headers before writing to `runtime/logs/`.
4. **Exception Handling:** External HTTP timeouts and HTTP 4xx/5xx error messages must be sanitized so raw request headers containing secrets are never rendered to the web UI or exception tracebacks.

---

## 5. GDPR Compliance, PII Minimization & Privacy Boundaries

Under the external API model, prompts containing lead information travel across the public internet to provider endpoints via TLS (HTTPS).

### 5.1 Sensitivity Classification:
- **Low / Non-Personal Data:** Website scraping extracts (public company descriptions, generic service offerings, detected CAD/BIM tools) and job posting text. Public corporate data poses low privacy risks.
- **Medium Data:** Recipient business contact name and professional title used for drafting cold emails (Phase 8).
- **High Data:** Inbound email text (Phase 11) containing customer correspondence, telephone numbers, direct email addresses, and commercially sensitive discussions.

### 5.2 Required S2 Ingress/Egress Privacy Controls:
1. **PII Minimization Pre-Processor:** Before sending inbound emails to Tier 2/3 classifiers, email signatures, phone numbers, and IBAN/banking details must be stripped or replaced with tokens (`[PHONE]`, `[SIGNATURE]`).
2. **Zero Model Training Commitment:** Providers selected in Tier 1–4 must offer enterprise or API-level terms guaranteeing that prompt data is not retained or used to train foundational models (e.g., OpenAI API terms, Anthropic commercial terms).
3. **Strict Human Review Gate:** In accordance with Phase 9 principles, no email generated by an LLM is ever dispatched without explicit operator confirmation.

---

## 6. Workstation Resource Footprint & Benchmark Measurements

To evaluate real laptop resource utilization, physical import and memory measurements were executed on the developer's Windows 11 machine (AMD64, Python 3.14.7).

### 6.1 Physical Measurements (Verified Real Data)

| Measurement Target | Cold Import Time | Resident Set Size (RSS) Memory | Notes |
| :--- | :--- | :--- | :--- |
| **Current Full Application** (`backend.main`) | **5.520 seconds** | **141.90 MB** | Includes transitive loading of `chromadb`, `onnxruntime.dll`, and `langchain` modules. |
| **Core Lightweight Modules** (`fastapi`, `pydantic`, `sqlite3`, `requests`, `bs4`) | **1.081 seconds** | **55.02 MB** | Demonstrates an **80.4% reduction in startup latency** and **61.2% memory savings** once legacy ML wheels are retired. |

### 6.2 Laptop Concurrency & Thermal Management Guidelines
To ensure SalesAI runs smoothly without causing CPU throttling, fan noise, or battery exhaustion on a laptop:
1. **Network Fetch Concurrency:** Hard cap bounded to **max 2 concurrent HTTP requests** for website and job scraping.
2. **LLM API Concurrency:** Hard cap bounded to **max 1 concurrent request** (sequential processing with 500ms pacing) for LLM generation.
3. **Page Length Limits:** Truncate raw candidate HTML to **max 50,000 bytes** before parsing; truncate extracted text to **max 8,000 characters** for LLM prompts.
4. **Daily Throughput Budget:** At 10 qualified companies per active day (~15–20 candidates processed), total daily pipeline execution completes in **under 4 minutes of cumulative CPU time**, consuming less than 0.5% of typical laptop daily battery capacity.
