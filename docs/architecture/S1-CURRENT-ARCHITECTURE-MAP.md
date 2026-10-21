# SalesAI S1 — Current Architecture & Component Map

**Document Version:** 1.0  
**Date:** 2026-10-21  
**Author:** Antigravity Engineering Agent  
**Base Commit:** `1b6c50e8f1a615cf462bce296b604b69b915d721` (Accepted S0 Legacy pyBIM-LLM Retirement)  
**Branch:** `audit/salesai-s1-laptop-dependency-map`  
**Target Architecture:** Localhost Windows Laptop Orchestration + SQLite/JSON + Remote External HTTPS LLM Gateway (S2)

---

## 1. Executive Summary & Operating Model

SalesAI is an automated B2B sales development engine configured to run locally on a developer's Windows workstation. Its business mission is generating **10 qualified, deduplicated, source-backed target companies per active day** (~300 qualified companies/month across ~450 discovered candidates) for AEC/BIM architectural and engineering technology services.

### Core Operating Principles:
1. **Local Authoritative Host:** FastAPI server, SQLite database (`data/candidates.db`), local event logs, deterministic scheduling, and the operator web dashboard run entirely on `http://127.0.0.1:8000/sales`.
2. **External Remote LLM Inference (Target S2):** Zero local neural weights, zero GPU/CUDA requirements, and zero dependency on local Ollama processes or local model downloads (`qwen2.5:1.5b`). All generative NLP tasks will route via HTTPS to remote commercial model APIs with task-specific routing, budget caps, and privacy controls.
3. **Deterministic Authority & Hard Gating:** All sending permissions, contact suppression lists, domain blacklists, and human review steps are strictly deterministic. LLMs never send emails, never approve their own drafts, and never bypass legal/channel compliance checks.
4. **Channel & Outcome Provenance:** Real reference records (59 rows: Milan 16, Turin 20, Rome 17, Bozen 3, LinkedIn 3) and sales outcomes are tracked with strict channel isolation. Specifically, *Turin 3BA S.r.l.* is an email-sourced meeting currently awaiting date confirmation (`meeting_pending_scheduling`); separate LinkedIn meetings are excluded from email conversion denominators; and LinkedIn `not_now / future_follow_up` replies are never converted to automated `unsubscribe`.

---

## 2. Complete Phase 1–12 & Production Step 1–6 Architectural Map

The table below maps every SalesAI milestone to its physical codebase modules, current status, execution mode, and external dependencies.

| Phase / Step | Milestone Name | Codebase Modules & Entrypoints | Operational Status | Execution Mode | External / Local Dependencies |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | Target Market Definition & Ideal Customer Profile | `sales_engine/discovery/query_generator.py`, `sales_engine/scoring/opportunity_scorer.py` | **Active** | Deterministic + Optional LLM | Italian AEC sector taxonomy, keyword dictionaries |
| **Phase 2** | Discovery & Query Automation | `sales_engine/discovery/query_generator.py`, `sales_engine/discovery/candidate_dedup.py` | **Active** | Deterministic + LLM assistance | Search query templates, candidate hashing |
| **Phase 3** | Web Scraping & Evidence Extraction | `sales_engine/discovery/scraper.py`, `sales_engine/analysis/website_analyzer.py` | **Active** | Deterministic fetch + LLM analysis | `requests`, `BeautifulSoup4`, optional `playwright` / `scrapling` |
| **Phase 4** | Job Posting Signal Detection | `sales_engine/analysis/job_analyzer.py`, `sales_engine/analysis/schemas.py` | **Active** | Deterministic fetch + LLM extraction | Career portal scrapers, regex tokenizers |
| **Phase 5** | Company Scoring & Qualification Engine | `sales_engine/scoring/opportunity_scorer.py`, `sales_engine/scoring/calculator.py` | **Active** | Deterministic | Weighted scoring algorithms, ICP tier rules |
| **Phase 6** | CRM / Candidate Data Layer | `sales_engine/storage/database.py`, `sales_engine/storage/schema.sql` | **Active** | Deterministic | SQLite3, local file system |
| **Phase 7** | Contact Discovery & Role Mapping | `sales_engine/contacts/role_detector.py`, `sales_engine/contacts/enrichment.py` | **Active** | Deterministic rules | Regex, public team page extraction |
| **Phase 8** | Outreach Prompt Engineering & Generation | `sales_engine/outreach/email_generator.py`, `sales_engine/outreach/email_prompt_builder.py` | **Active** | LLM Generation + Strict Rules | `ai_engine.llm_client.BIMLLMClient` (Transitional) |
| **Phase 9** | Human Approval Gate & Sending Safeguards | `sales_engine/outreach/send_gate.py`, `sales_engine/outreach/suppression.py`, `sales_engine/outreach/draft_validator.py` | **Active / Critical Gate** | Deterministic (Strictly Non-LLM) | Human review UI, suppression database, SMTP dry-run |
| **Phase 10** | Inbox Ingestion & Automated Synchronization | `sales_engine/inbox/ingestion.py`, `sales_engine/inbox/schemas.py` | **Active** | Deterministic IMAP/API | Python standard `imaplib`, email parsers |
| **Phase 11** | Reply Classification & Suggestion Engine | `sales_engine/inbox/classifier.py`, `sales_engine/inbox/suggestion.py` | **Active** | Deterministic Rules + LLM fallback | Regex intent rules, LLM reply drafter |
| **Phase 12** | Analytics, Reporting & Dashboard UI | `backend/sales_ui.py`, `backend/main.py`, `static/sales/*`, `templates/sales/*` | **Active** | Deterministic Web UI | FastAPI, Jinja2, Chart.js, HTML5/CSS |
| **Step 1** | Verification & Inventory | `scripts/audit_repository.py`, `release_manifest.json`, `repo_manifest.json` | **Active** | Deterministic CLI | Manifest validators, Git CLI |
| **Step 2** | Codebase Cleanup & Archiving | `docs/cleanup/SALESAI-S0-LEGACY-PYBIM-LLM-RETIREMENT.md` | **Active (S0 Complete)** | Manual / Git | S0 Git commit `1b6c50e8` |
| **Step 3** | Golden Dataset & Synthetic Benchmark | `docs/architecture/SalesAI_Golden_10_Synthetic_v1.json`, `docs/architecture/SalesAI_Model_Inputs_10.jsonl` | **Active Baseline** | Synthetic Evaluation | 10 benchmark test cases (5 clean, 5 deceptive) |
| **Step 4** | Model Evaluation Framework (Tiers 1–4) | `docs/architecture/SalesAI_Cloud_LLM_Cost_Architecture_2026.xlsx` | **Design Stage (S2 Target)** | Multi-provider evaluation | 4 Quality/Price Tiers (Budget, Fast, Balanced, Flagship) |
| **Step 5** | Historical Workstation Benchmarking | `docs/benchmarks/*`, historical Step 5/6 archives | **Archival / Evidence Only** | Inactive (Historical) | Former Ollama RTX 3060/Laptop CUDA measurements |
| **Step 6** | Production Packaging & Runbooks | `run_sales_ai.bat`, `start_server.py`, `scripts/rebuild_zip.py` | **Active / Transitional** | Batch / Python launcher | FastAPI entrypoint, Uvicorn loopback bind |

---

## 3. End-to-End Operational Workflow & Data Flow

```mermaid
flowchart TD
    subgraph UI_Layer ["Developer Workstation Dashboard (127.0.0.1:8000)"]
        UI[FastAPI Sales UI / Web Dashboard]
        Admin[Console & Health Status]
    end

    subgraph Discovery_Phase ["Phase 1 - 4: Discovery & Enrichment"]
        QGen[Query Generator\nDeterministic / Optional LLM] --> Scrape[Web / Career Portal Scraper\nRequests / BeautifulSoup]
        Scrape --> WebA[Website Analyzer\nLLM 2-Pass: Facts + Signals]
        Scrape --> JobA[Job Analyzer\nLLM: Roles + Tech Stack]
    end

    subgraph Scoring_Phase ["Phase 5 - 7: Qualification & Staging"]
        WebA --> Score[Opportunity Scorer & Calculator\nStrictly Deterministic Rules]
        JobA --> Score
        Score --> DB[(Local SQLite DB\ncandidates.db)]
        Contact[Contact Role Detector] --> DB
    end

    subgraph Outreach_Phase ["Phase 8 - 9: Drafting & Sending Gate"]
        DB --> PromptB[Email Prompt Builder\nApproved pyBIM Context]
        PromptB --> Drafter[Email Generator\nLLM Draft Generation]
        Drafter --> Val[Draft Validator\nDeterministic Fact & Link Check]
        Val --> RevQ[Review Queue: Pending Approval]
        RevQ --> Operator{Operator Human Approval}
        Operator -- Approved --> Gate[Phase 9 Sending Gate\nSuppression & Rate Check]
        Operator -- Rejected --> DB
        Gate --> SMTP[Outreach Dispatcher\nDry-Run / Live SMTP]
    end

    subgraph Inbox_Phase ["Phase 10 - 11: Inbound Processing"]
        IMAP[IMAP Ingestion / Poll] --> Class[Inbox Classifier\nRules First -> LLM Fallback]
        Class --> Intent{Intent Hashed}
        Intent -- Unsubscribe --> Suppress[Add to Suppression DB]
        Intent -- Lead / Inquiry --> ReplySugg[Reply Suggestion Engine\nLLM + Prohibited Claims Filter]
        ReplySugg --> HumanInboxRev{Human Approval}
        HumanInboxRev -- Confirmed --> SendReply[Draft Dispatch]
    end

    subgraph Target_S2_Gateway ["Target S2 External LLM Gateway"]
        LLMGateway["Unified LLM Gateway\n(Task-Based Routing, 4 Tiers, Budget, PII Redaction)"]
        WebA -.->|HTTPS (Pass A/B)| LLMGateway
        JobA -.->|HTTPS (Jobs)| LLMGateway
        QGen -.->|HTTPS (Queries)| LLMGateway
        Drafter -.->|HTTPS (Draft)| LLMGateway
        Class -.->|HTTPS (Inbound)| LLMGateway
        ReplySugg -.->|HTTPS (Replies)| LLMGateway
        LLMGateway --> ExternalAPIs["Remote HTTPS LLM APIs\n(OpenAI, Anthropic, Google, Mistral)"]
    end
```

---

## 4. Component Classification: Active vs Transitional vs Mock-Only vs Legacy

To prevent regressions and identify precise boundaries for Phase S2, every component in `ai_engine/`, `backend/`, and `sales_engine/` is categorized below.

### 4.1 Active Core Business Components
These components represent tested, production-ready SalesAI business logic. They must be preserved with zero functional modifications.
- `sales_engine/scoring/*`: Pure deterministic algorithms for ICP scoring, lead prioritization, and qualification tiers.
- `sales_engine/outreach/send_gate.py`: Absolute sending authority gate. Verifies approval flags, opt-out status, and rate limits.
- `sales_engine/outreach/suppression.py`: Deterministic suppression lists (MD5/SHA256 email and domain hashing).
- `sales_engine/outreach/draft_validator.py`: Factual consistency, link verification, and forbidden promise detector.
- `sales_engine/storage/*`: SQLite database drivers, migration runners, and transaction managers.
- `backend/sales_ui.py`: Operator dashboard endpoints for pipeline management, review queues, and live analytics.

### 4.2 Transitional LLM Call Sites (Targeted for S2 Gateway Migration)
Currently import `ai_engine.llm_client.BIMLLMClient` directly. In S2, these will consume the abstract `LLMProviderGateway`.
1. `sales_engine/analysis/website_analyzer.py`: 2-pass website signal extraction.
2. `sales_engine/analysis/job_analyzer.py`: Hiring demand and BIM capability extractor.
3. `sales_engine/discovery/query_generator.py`: ICP search query generator (fallback to deterministic mode).
4. `sales_engine/inbox/classifier.py`: Inbound email sentiment and categorization.
5. `sales_engine/inbox/suggestion.py`: Context-grounded reply proposal generator.
6. `sales_engine/outreach/email_generator.py`: Initial cold outreach email generator.

### 4.3 Transitional Plumbing & Legacy Artifacts
- `ai_engine/llm_client.py`: Contains the legacy `BIMLLMClient` class wrapping `import ollama`. Retained in S0/S1 to avoid breaking active modules; will be refactored into or superseded by the external gateway in S2.
- `ai_engine/data_ingestor.py`, `ai_engine/rag_retriever.py`, `ai_engine/ingest_queue.py`: Legacy RAG ingestion modules. Only retained because `tests/test_admin_gate.py` asserts admin token controls across their entrypoints. S2 will decouple `test_admin_gate.py` from RAG so these can be safely retired.
- `backend/main.py` (`/health` route): Probes local Ollama (`ollama_ok`) and ChromaDB (`chroma_ok`). S2 will update `/health` to verify HTTPS outbound gateway reachability and token quota budgets instead of local daemons.
- `start_server.py`: Currently provides an optional `--tunnel` flag and `ENABLE_PUBLIC_TUNNEL` check that invokes Cloudflare Quick Tunnels. While defaulting safely to `127.0.0.1`, this legacy tunnel code will be removed or strictly disabled.
- `run_sales_ai.bat`: Contains an obsolete `curl -s http://127.0.0.1:11434` Ollama health probe. Will be eliminated in S2.

---

## 5. Data Boundaries & Persistence Layer

All persistent state is stored locally within the repository under `data/` and `runtime/`:
1. `data/candidates.db` (Primary SQLite Store):
   - `candidates`: Company metadata, domain, ICP score, qualification status, discovery timestamps.
   - `contacts`: Verified business email, full name, role, LinkedIn URL, provenance source.
   - `drafts`: Generated outreach emails, revision history, approval status (`pending_review`, `approved`, `rejected`), send status.
   - `inbox_messages`: Inbound messages, parsed headers, classification results, suggested replies.
   - `suppression_list`: Unsubscribed emails and domains, opt-out timestamps, suppression reasons.
2. `data/evidence/*`: Raw scraped HTML snapshots, extracted text snippets, and timestamps ensuring full source attribution for every LLM prompt.
3. `data/reference/*`: Historical real reference datasets (59 total rows: Milan 16, Turin 20, Rome 17, Bozen 3, LinkedIn 3). Strictly protected against automated overwrite.
4. `runtime/logs/*`: Operational logs with sensitive PII and credential masking.
