# SalesAI S1 — LLM Call Map & Task Routing Specification

**Document Version:** 1.0  
**Date:** 2026-10-21  
**Author:** Antigravity Engineering Agent  
**Companion File:** [`docs/architecture/S1-LLM-CALL-MAP.json`](file:///C:/Sales-AI/SalesAI/docs/architecture/S1-LLM-CALL-MAP.json)  
**Base Commit:** `1b6c50e8f1a615cf462bce296b604b69b915d721`  
**Branch:** `audit/salesai-s1-laptop-dependency-map`

---

## 1. Executive Summary & Audit Overview

SalesAI employs artificial intelligence strictly for unstructured natural language understanding, evidence extraction, and message generation. All core business rules, deduplication, qualification formulas, email suppression, and sending permissions are **100% deterministic**.

Across the entire SalesAI codebase, exactly **six active LLM call sites** exist. Currently, all six instantiate `ai_engine.llm_client.BIMLLMClient` directly. Under Phase S2, these call sites will be routed through a centralized `LLMProviderGateway` to commercial remote HTTPS APIs (OpenAI, Google Gemini, Anthropic Claude, Mistral) without local Ollama daemon requirements.

---

## 2. Comprehensive Inventory of the Six LLM Call Sites

### 2.1 LLM-SITE-01: Website Analyzer (`sales_engine/analysis/website_analyzer.py`)
- **Originating Route / Workflow:** Phase 3 Candidate Extraction; triggered by `POST /sales/candidates/{candidate_id}/analyze`.
- **Client Method:** `BIMLLMClient.generate_code_async` (2-pass architecture).
  - *Pass A (Factual Profile):* Extracts company name, primary services, BIM software mentions (Revit, Archicad, Tekla), team size.
  - *Pass B (Sales Opportunity Signals):* Identifies business pain points, hiring hints, and digital transformation maturity.
- **Input Data & Boundary:** Sanitized markdown/text stripped from candidate HTML (bounded to max 8,000 characters). Public web content only. Zero contact PII.
- **Output Schema:** `WebsiteAnalysisResult` aggregating `WebAnalysisPassA` and `WebAnalysisPassB` Pydantic models.
- **Failure & Fallback:** Single JSON repair retry. On persistent failure or timeout, returns partial result with error flag; scoring falls back to deterministic keyword frequency analysis.
- **Estimated Resource Profile:** ~1,600 input tokens, ~220 output tokens; ~2.2s latency.
- **Affected Tests:** `tests/test_website_analyzer.py`, `tests/test_analysis_pipeline.py`.

### 2.2 LLM-SITE-02: Job Posting Analyzer (`sales_engine/analysis/job_analyzer.py`)
- **Originating Route / Workflow:** Phase 4 Hiring Signal Detection; triggered by career page polling.
- **Client Method:** `BIMLLMClient.generate_code_async`.
- **Input Data & Boundary:** Public job posting text bounded to 6,000 characters. Public career listings only.
- **Output Schema:** `JobAnalysisResult` Pydantic model (`job_title`, `requires_bim`, `detected_software`, `seniority`, `urgency_score`).
- **Failure & Fallback:** Single repair retry. Fallback to deterministic regex dictionary matching for known BIM tool names.
- **Estimated Resource Profile:** ~1,000 input tokens, ~170 output tokens; ~1.6s latency.
- **Affected Tests:** `tests/test_job_analyzer.py`, `tests/test_hiring_signals.py`.

### 2.3 LLM-SITE-03: Discovery Query Generator (`sales_engine/discovery/query_generator.py`)
- **Originating Route / Workflow:** Phase 2 Search Automation; triggered by `POST /sales/discovery/generate-queries` when `DISCOVERY_USE_LLM=True`.
- **Client Method:** `BIMLLMClient.generate_code`.
- **Input Data & Boundary:** Target service definitions, regional Italian geographic focus (Milan, Turin, Rome, Bozen), industry sectors. Zero PII.
- **Output Schema:** JSON list of formatted Google/Bing search query strings.
- **Failure & Fallback:** Instant zero-cost fallback to `DiscoveryQueryGenerator.generate_deterministic_queries()` using pre-compiled combinatorial matrices.
- **Estimated Resource Profile:** ~500 input tokens, ~140 output tokens; ~1.2s latency.
- **Affected Tests:** `tests/test_query_generator.py`, `tests/test_discovery_flow.py`.

### 2.4 LLM-SITE-04: Inbound Email Classifier (`sales_engine/inbox/classifier.py`)
- **Originating Route / Workflow:** Phase 11 Inbound Processing; triggered by `POST /sales/inbox/sync` or manual reclassification.
- **Client Method:** `BIMLLMClient.generate_code` (executed only after deterministic unsubscribe rules pass).
- **Input Data & Boundary:** Inbound email subject and body wrapped in `<UNTRUSTED_INBOUND_EMAIL>` delimiters to neutralize prompt injection. **HIGH PII:** Contains sender name, email, and corporate context.
- **Output Schema:** `InboxClassificationResult` (`intent`, `confidence`, `sentiment`, `summary`, `extracted_dates`).
- **Failure & Fallback:** Categorizes message as `needs_human_triage` with `confidence=0.0`. *Hard safety:* Unsubscribe detection runs deterministically before the LLM is ever called.
- **Estimated Resource Profile:** ~700 input tokens, ~100 output tokens; ~1.1s latency.
- **Affected Tests:** `tests/test_inbox_classifier.py`, `tests/test_phase11_inbox.py`.

### 2.5 LLM-SITE-05: Reply Suggestion Generator (`sales_engine/inbox/suggestion.py`)
- **Originating Route / Workflow:** Phase 11 Grounded Reply Proposal; triggered by operator viewing inbox message.
- **Client Method:** `BIMLLMClient.generate_code`.
- **Input Data & Boundary:** Inbound inquiry, company verified knowledge base, prohibited claims list. **HIGH PII & Commercial Confidentiality.**
- **Output Schema:** `ReplySuggestionResult` (`suggested_subject`, `suggested_body`, `grounded_facts_used`, `validation_passed`).
- **Failure & Fallback:** Reverts to deterministic template replies. All drafts are scanned by `ProhibitedClaimsValidator` to ensure no fake team sizes, fictional delivery dates, or unauthorized discounts are suggested.
- **Estimated Resource Profile:** ~900 input tokens, ~300 output tokens; ~2.0s latency.
- **Affected Tests:** `tests/test_inbox_suggestion.py`, `tests/test_phase11_reply_safety.py`.

### 2.6 LLM-SITE-06: Cold Outreach Email Generator (`sales_engine/outreach/email_generator.py`)
- **Originating Route / Workflow:** Phase 8 Cold Email Draft Generation; triggered by `POST /sales/leads/{lead_id}/generate-draft`.
- **Client Method:** `BIMLLMClient.generate_code`.
- **Input Data & Boundary:** `OutreachContext` containing recipient business name, job title, company signals, and approved pyBIM service offering. Medium PII.
- **Output Schema:** `LLMEmailResponse` parsed into `EmailDraft` (`subject`, `body`, `service_used`, `personalization_notes`, `evidence_refs`, `cta`).
- **Failure & Fallback:** Single repair retry with `temperature=0.0`. Post-generation validation via `DraftValidator` checks for unauthorized claims, missing placeholders, and required unsubscribe language. **Never sends email automatically; drafts are staged for operator review.**
- **Estimated Resource Profile:** ~1,300 input tokens, ~350 output tokens; ~2.5s latency.
- **Affected Tests:** `tests/test_email_generator.py`, `tests/test_phase8_outreach.py`, `tests/test_phase9_approval.py`.

---

## 3. Four-Tier Model Architecture & Recommended Task Routing

In Phase S2, external models will be categorized into 4 tiers to optimize cost, speed, and reasoning depth:

| Tier | Tier Description | Indicative Models | Assigned SalesAI Tasks | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1** | **Fast & Budget Extraction** | GPT-4o-mini, Claude 3.5 Haiku, Gemini 2.0 Flash, Mistral Small | • Website Extraction (Pass A/B)<br>• Job Posting Extraction<br>• Discovery Query Expansion | Structured JSON extraction from clean text; sub-cent cost per request; low latency. |
| **Tier 2** | **Balanced Reasoning** | GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, Mistral Large | • Inbound Email Classification<br>• Candidate Signal Synthesis | Handles nuanced sentiments, ambiguous phrasing, and complex German/Italian/English nuances. |
| **Tier 3** | **High-Fidelity Generation** | Claude 3.5 Sonnet, GPT-4o | • Cold Outreach Email Drafting<br>• Inbound Reply Suggestions | Requires sophisticated B2B tone, high contextual relevance, strict adherence to negative constraints. |
| **Tier 4** | **Flagship / Complex Arbitration** | Claude 3 Opus, GPT-4.5 / o1 | • Operator Dispute Review (Optional)<br>• Strategic Account Deep Dives | Reserved exclusively for manual operator escalation on high-value enterprise accounts. |

---

## 4. Monthly Token & Cost Projection (300 Qualified Leads / Month)

Based on the target funnel (450 raw discoveries yielding 300 accepted prospects, resulting in ~150 generated drafts per month):

| Task ID | Task Description | Assigned Tier | Calls / Month | Avg Input Tok | Avg Output Tok | Monthly Input Tok | Monthly Output Tok |
| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| Q01 | Search Query Generation | Tier 1 | 15 | 500 | 140 | 7,500 | 2,100 |
| Q02 | Website Analysis (Pass A & B) | Tier 1 | 675 | 1,600 | 220 | 1,080,000 | 148,500 |
| Q03 | Job Signal Extraction | Tier 1 | 300 | 1,000 | 170 | 300,000 | 51,000 |
| Q04 | Inbound Classification | Tier 2 | 30 | 700 | 100 | 21,000 | 3,000 |
| Q05 | Reply Suggestion Generation | Tier 3 | 12 | 900 | 300 | 10,800 | 3,600 |
| Q06 | Outreach Email Drafting | Tier 3 | 150 | 1,300 | 350 | 195,000 | 52,500 |
| **Subtotal** | | | **1,182** | | | **1,614,300** | **260,700** |
| **+ 25% Buffer** | *Planning Margin* | | **1,478** | | | **2,017,875** | **325,875** |

*Estimated Monthly Cost:* At blended commercial API rates (~$0.30/1M input, ~$1.50/1M output for Tier 1; ~$3.00/1M input, ~$15.00/1M output for Tier 3), total projected monthly LLM expenditure is **$2.50 to $6.50 USD**, well below typical software subscription thresholds.
