# SalesAI: Autonomous B2B Lead Intelligence & Automation Engine

**SalesAI** is an enterprise-grade, deterministic, and privacy-first **B2B Lead Generation and Outreach Intelligence Engine**. It automatically analyzes your Ideal Customer Profile (ICP), discovers high-intent companies from global job signals, extracts and normalizes hiring data across modern Applicant Tracking Systems (ATS), scores and qualifies leads, enriches decision-maker contacts with verified work emails, and exports human-reviewable workbooks and CRM-ready datasets.

---

## 📑 Table of Contents

1. [Architectural Overview](#-architectural-overview)
2. [End-to-End Pipeline (Phases 1–9)](#-end-to-end-pipeline-phases-19)
3. [Key Implemented Capabilities](#-key-implemented-capabilities)
   - [Phase 2: ICP & Website Intelligence](#phase-2-icp--website-intelligence)
   - [Phase 3: Intent Discovery Engine](#phase-3-intent-discovery-engine)
   - [Phase 4: Multi-ATS Job Extraction](#phase-4-multi-ats-job-extraction)
   - [Phase 5: Deterministic Lead Scoring & Aggregation](#phase-5-deterministic-lead-scoring--aggregation)
   - [Phase 6: Decision-Maker Enrichment](#phase-6-decision-maker-enrichment)
   - [Phase 7: Export & Human Review Workspace](#phase-7-export--human-review-workspace)
4. [Stable Identity & Deduplication Strategy](#-stable-identity--deduplication-strategy)
5. [Export Formats & Human Review Workspace](#-export-formats--human-review-workspace)
6. [API Gateway Reference](#-api-gateway-reference)
7. [Comprehensive Test Suite & QA Rigor](#-comprehensive-test-suite--qa-rigor)
8. [Quick Start & Running Locally](#-quick-start--running-locally)
9. [Repository Structure](#-repository-structure)

---

## 🏗️ Architectural Overview

SalesAI transforms raw web data into sales-ready qualified leads using a modular pipeline that strictly decouples AI semantic extraction from deterministic scoring and export logic:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        SalesAI Core Architecture                       │
│                                                                        │
│   ┌─────────────────────┐    ┌─────────────────────────────────────┐   │
│   │   ICP Analyzer      │───►│   Discovery Engine                  │   │
│   │   (Website Profile) │    │   (Brave / Search Query Generator)  │   │
│   └─────────────────────┘    └──────────────────┬──────────────────┘   │
│                                                 │                      │
│   ┌─────────────────────┐    ┌──────────────────▼──────────────────┐   │
│   │   Lead Scoring      │◄───│   ATS Job Extraction                │   │
│   │   (Deterministic)   │    │   (Lever / Greenhouse / Ashby / LD) │   │
│   └──────────┬──────────┘    └─────────────────────────────────────┘   │
│              │                                                         │
│   ┌──────────▼──────────┐    ┌─────────────────────────────────────┐   │
│   │ Contact Enrichment  │───►│ Human Review & Export Workspace     │   │
│   │ (Apollo / Hunter)   │    │ (Excel / CSV / Google Sheets / CRM) │   │
│   └─────────────────────┘    └─────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 End-to-End Pipeline (Phases 1–9)

| Phase | Module | Status | Description |
| :--- | :--- | :---: | :--- |
| **Phase 1** | Local AI Gateway & Setup | ✅ Completed | Fast Ollama LLM integration, health check, and environment setup. |
| **Phase 2** | Website Analyzer | ✅ Completed | Scrapes and analyzes business websites to extract ICP, buyer roles, and technology signals. |
| **Phase 3** | Discovery Engine | ✅ Completed | Generates targeted search queries and classifies hiring URLs. |
| **Phase 4** | Job Extraction | ✅ Completed | Parses career pages across Lever, Greenhouse, Ashby, and Generic HTML/JSON-LD. |
| **Phase 5** | Lead Scoring & Aggregation | ✅ Completed | Deterministic company entity resolution, dedup, recency, signal evidence, and 0–100 qualification scoring. |
| **Phase 6** | Company & Contact Enrichment | ✅ Completed | Discovers decision-makers (Apollo/Hunter), buyer-role ranking, and strict work email verification. |
| **Phase 7** | Export / Review Workspace | ✅ Completed | Multi-sheet formatted Excel (`.xlsx`), CSVs, CRM-neutral JSON, and optional Google Sheets Upsert/Snapshot. |
| **Phase 8** | Personalized Outreach | 🟡 Next | Generates tailored email drafts and value propositions on top of Phase 7 structure. |
| **Phase 9** | Approval & Outreach Dispatch | 🟡 Next | Human review interface, email sending controls, and campaign state tracking. |

---

## 🌟 Key Implemented Capabilities

### Phase 2: ICP & Website Intelligence
- Scrapes company landing pages and cleans HTML.
- Extracts `services`, `offerings`, `target_industries`, `pain_points`, `buyer_roles`, `primary_job_signals`, and `negative_signals`.

### Phase 3: Intent Discovery Engine
- Deterministically generates prioritized search queries across 4 categories: `general_job`, `company_career`, `ats_targeted`, and `keyword_signal`.
- Enforces strict vocabulary control (only verified ICP signals are queried).
- Classifies candidate URLs into `job_posting`, `careers_hub`, or `noise`.

### Phase 4: Multi-ATS Job Extraction
- Source detector recognizes **Lever**, **Greenhouse**, **Ashby**, and **Generic** career portals.
- Parses structured data: Schema.org `JobPosting` (JSON-LD, `@graph`, Microdata), requirements, seniority, and remote status.
- Prevents ATS hosting domain leakage (e.g. `lever.co` is never assigned as the employer domain).

### Phase 5: Deterministic Lead Scoring & Aggregation
- **Entity Resolution:** Merges name variants under the same canonical domain; keeps distinct domains separate even if company names are identical.
- **Explainable Scoring Breakdown:**
  - Fit Score (0–40)
  - Intent Score (0–40)
  - Recency Score (0–10)
  - Evidence Score (0–10)
  - Lead Score = Fit + Intent + Recency + Evidence (Threshold: ≥ 70 for qualification)
- Preserves exact source evidence snippets and scoring reasons for 100% auditability.

### Phase 6: Decision-Maker Enrichment
- Integrates **Apollo.io** and **Hunter.io** APIs (with mockable providers for offline testing).
- **Buyer Role Ranker:** Maps decision-maker job titles to target ICP roles (`exact`, `strong`, `relevant`, `weak`, `none`).
- **Strict Email Verification:** Prioritizes verified business emails; flags risky or unverified addresses.
- Enforces **Privacy Rules:** Never exports personal emails, personal phones, or mobile phone numbers.

### Phase 7: Export & Human Review Workspace
- Generates **SalesAI_Export_<run_id>.xlsx** with 5 structured sheets:
  - `Leads`: Flattened best contact columns for rapid sales review.
  - `Contacts`: All discovered candidate profiles linked to `lead_id`.
  - `Jobs`: Complete job signal audit trail.
  - `Errors_Audit`: Transparent logging of any enrichment warnings or provider errors.
  - `Summary`: Deterministic run metrics and lead score averages.
- Generates standard **CSVs** (`leads.csv`, `contacts.csv`, `jobs.csv`, `errors_audit.csv`).
- Produces **CRM-ready JSON** payloads ready for downstream HubSpot/Salesforce adapters.
- Provides **Google Sheets integration** with `snapshot` and idempotent `upsert` modes.

---

## 🔑 Stable Identity & Deduplication Strategy

SalesAI guarantees that re-running pipelines across different batches produces identical, stable entity keys without generating random UUIDs:

1. **`lead_id` Priority**:
   - `domain:<canonical_domain>` (e.g. `domain:acme-bim.com`)
   - `source_key:<ats_key>` (e.g. `source_key:lever:acme-eng`)
   - `name:<normalized_name>` (e.g. `name:acme_corporation`)
2. **`contact_id` Priority**:
   - `email:<normalized_work_email>` (e.g. `email:jane.doe@acme-bim.com`)
   - `linkedin:<canonical_profile>` (e.g. `linkedin:linkedin.com/in/janedoe-bim`)
   - `<provider>:<person_id>` (e.g. `apollo:person_123`)
   - Deterministic record fallback (`fallback:{lead_id}:{name}:{title}`)

---

## 📊 Export Formats & Human Review Workspace

### Excel Workbook Usability
- **Header Formatting:** Dark theme header (`#1E293B`) with white bold text.
- **Usability:** Frozen top header row (`A2`), auto-filters enabled on all columns.
- **Auto-Fit & Text Wrapping:** Dynamic column width with text wrapping enabled for evidence, reasons, and notes.
- **Auditability:** Zero formulas on source-of-truth fields; opens cleanly in Microsoft Excel and LibreOffice.

### Reserved Workflow Columns (Ready for Phases 8 & 9)
```text
outreach_status = "not_started"
approval_status = "pending_review"
send_status = "not_sent"
draft_subject = ""
draft_body = ""
personalization_notes = ""
last_outreach_at = ""
owner = ""
notes = ""
```

---

## 🌐 API Gateway Reference

FastAPI application running on port `8000`:

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `POST /api/sales/analyze-website` | `POST` | Scrapes and profiles website ICP. |
| `POST /api/sales/discover` | `POST` | Generates search queries and discovers job posting URLs. |
| `POST /api/sales/extract-jobs` | `POST` | Extracts structured job data from URLs. |
| `POST /api/sales/build-leads` | `POST` | Aggregates and scores company leads. |
| `POST /api/sales/enrich-leads` | `POST` | Enriches leads with Apollo/Hunter contacts and verified emails. |
| `POST /api/sales/export` | `POST` | Exports multi-sheet Excel, CSVs, CRM JSON, and syncs Google Sheets. |

---

## 🧪 Comprehensive Test Suite & QA Rigor

SalesAI enforces strict contract comparison and regression testing via pytest:

```bash
# Run all unit and acceptance tests
pytest tests/ -v
```

### Test Coverage Highlights:
- **177+ Passing Automated Tests:**
  - 14 Phase 7 Export & Workspace Tests
  - 45 Phase 6 Contact Enrichment Tests
  - 27 Phase 5 Lead Scoring & Aggregation Tests
  - 19 Phase 4 Job Extraction Tests
  - 15 Phase 3 Discovery Engine Tests
  - 5 QA Validator & Contract Self-Integrity Tests
- **Deterministic Golden Fixtures:** Tested offline against frozen snapshots in `tests/golden/`.

---

## ⚡ Quick Start & Running Locally

### 1. Prerequisites
- Python 3.11+
- Local Ollama instance (e.g. `qwen2.5-coder:1.5b` or `llama3`)

### 2. Setup Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Launch Backend Gateway
```powershell
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 📁 Repository Structure

```text
SalesAI/
├── backend/                  # FastAPI Application & Schemas
│   ├── main.py
│   └── schemas.py
├── sales_engine/             # Core Pipeline Modules
│   ├── analysis/             # Website Analyzer & Company Normalizer
│   ├── discovery/            # Search Query Generator & URL Classifier
│   ├── sources/              # Multi-ATS Parsers (Lever, Greenhouse, Ashby, Generic)
│   ├── leads/                # Aggregator, Scoring Engine & Identity Resolution
│   ├── enrichment/           # Apollo & Hunter Providers, Buyer Role Ranker
│   └── exports/              # Excel, CSV, Google Sheets & CRM Orchestrator
├── tests/                    # Acceptance & Unit Test Suite
│   ├── acceptance/           # Golden QA Acceptance Tests
│   ├── golden/               # Frozen Input & Snapshot Fixtures
│   └── test_*.py             # Unit Test Modules
├── exports/                  # Generated Export Workbooks & Datasets
└── README.md
```