# SalesAI — سند مرجع اجرایی (Laptop + External LLM API)

**Project:** pyBIM / SalesAI  
**Version:** 1.1 — 2026-10-21  
**Status:** Architecture decision accepted as planning baseline; implementation and API model selection pending.  
**Supersedes:** `SalesAI_Master_Plan_Local_Or_Cloud_FA.md` v1.0 regarding deployment choice, monthly throughput, roadmap.  
**Rule:** Keep this file under version control and update decisions, evidence, accepted tests, actual costs and open issues at every stage.

## 1. Confirmed decisions

- [x] **Hosting:** SalesAI Python/FastAPI/localhost UI, orchestration, CRM, database and audit trails will operate on the developer's laptop. No server/cloud hosting is required for the first release.
- [x] **LLM inference:** external APIs only. Do **not** require Ollama, CUDA, GPU, or local model downloads for the new operating architecture. Earlier local benchmarks are historical evidence only.
- [x] **Daily target:** 10 **qualified, deduplicated, source-backed** companies on each active day. Planning assumption: 30 active days and 300 accepted prospects per month. The user controls start/stop; not a guaranteed number of results.
- [x] **Work hours:** runs only while the laptop is powered on and SalesAI is active. Do not imply background processing while sleeping/offline. A future server migration is optional, not in current scope.
- [x] **Emails:** the local web dashboard generates grounded **drafts** for human review. Sending remains governed by the existing Phase 9 authority and by legal/channel approval. No automatic send introduced by LLMs.
- [x] **Model evaluation:** four tiers × four verified API candidates; test affordable candidates first, escalate specific underperforming tasks only. No need to run every production company through all 16 models.
- [x] **Synthetic benchmark:** ten self-contained fictional cases (five clean; five misleading/adversarial), answer key isolated from model inputs.
- [x] **Real reference:** Milan (16), Turin (20), Rome (17), Bozen (3), and only last three LinkedIn records (3); **59 rows** as design/reference source, **not automatically labeled ground truth**.
- [x] **Outcome provenance:** 3BA in Turin is a real email-sourced opportunity with meeting being scheduled; do not mark `meeting_confirmed` before an agreed slot is logged. Separate LinkedIn-sourced meeting not included in email conversion stats. A LinkedIn `not_now / future_follow_up` reply is a separate event, not unsubscribe.
- [x] **Out of scope:** Hamid - Email project for now. Do not import or rewrite its seven-category workflow.

## 2. Runtime architecture

```text
Laptop (Windows)
  START / STOP / LOCAL DASHBOARD (127.0.0.1 only)
  FastAPI + Workflow Orchestrator
  Discovery (allowed sources) -> Candidate Normalizer -> Dedup
  Evidence Store -> Deterministic validators -> Qualification
              |                              ^
              v                              |
  LLM Gateway (per-task routing, budget, safety, redaction)
      | HTTPS + limited source excerpts + schema
      v
  External API providers (OpenAI / Google / Anthropic / Mistral ...)
      | response + reported token usage
      v
  JSON/schema and evidence validation
  Local DB + Event Log -> Draft Queue -> Human Approval
  Backup / Restore / Run Audit / Cost Ledger
```

**Low local compute does not mean zero local compute:** HTML extraction, requests, parsing, indexing, local database, file export and especially headless-browser scraping still use laptop resources. Default to requests + HTML parsing; disable unnecessary full-browser rendering, use bounded concurrency (initially ≤2 network fetches, ≤1-2 LLM requests simultaneously), capped page length and per-day budgets. Benchmark actual laptop load before increasing concurrency. Running ordinary, well-controlled software does not by itself imply hardware damage, but thermal/CPU load should be monitored.

**No port forwarding:** bind UI and API to `127.0.0.1`; no public IP necessary. Keep API keys in local OS-protected secrets / environment outside Git and Excel. Never log raw credentials. Encrypt or otherwise protect backups and use access restrictions for user accounts.

**Privacy boundary:** provider receives exactly the request payload. Keeping the master DB local does not make provider-side processing disappear. Record API provider, region, DPA/privacy choices and required-data policy; strip non-essential contact PII and confidential documents; store provider-response metadata locally.

## 3. Operational target and funnel

**Output KPI:** 10 **accepted prospects**/active day, not necessarily 10 raw search results; input discovery may need >10 candidates. Working month example: 450 raw candidates → 300 accepted prospects (assumed yield ~66.7%). Real acceptance rate must be learned from a limited pilot. Additional illustrative assumption: 150 email drafts/month (50% of accepted), not necessarily 150 sent messages.

**Local workflow each day:** operator presses START; pipeline fetches a bounded candidate batch, filters by region/service, finds permitted source evidence, asks external LLM to extract or interpret only when deterministic rules cannot suffice, validates results, stores accepted prospects, and pauses when ten new accepted leads are found or run/time/cost caps are reached. If fewer than ten satisfy criteria, report the shortfall rather than lowering quality standards or inventing records.

**Scheduling:** manual run initially. Optional Windows Task Scheduler only if laptop is on and user explicitly enables it. No assumed 24/7 availability.

## 4. LLM stages vs deterministic stages

| ID | Task | Model? | Local deterministic gate |
|---|---|---|---|
| Q01 | Search-query suggestions | Optional | Country, language, sector filters |
| Q02 | Page-relevant text extraction / normalization | Conditional yes | HTML fetch, clipping, source policy |
| Q03 | Profile structuring | Yes or rules if structured | Schema, source field provenance |
| Q04 | Qualification and company relationship | Yes | Whitelist taxonomy, evidence, operator override |
| Q05 | Opportunity matching | Yes | Dedup, positive and negative signals |
| Q06 | Email draft | Yes | Approved pyBIM facts only, no auto-send |
| Q07 | Claims cross-check | Primarily rules; LLM optional | Numerical commitments require explicit approved source |
| Q08 | Reply classification | Yes | Unsubscribe/suppression gate remains deterministic |
| Q09 | Reply suggestion | Yes | Human approval, no fabricated team/dates/cost |
| Q10 | Meeting details | Conditional yes | Confirm explicit date/time/time zone |
| Q11 | Reporting narratives | Optional | KPIs calculated locally, not LLM-generated facts |

No LLM needed for importing spreadsheets, deduplication, local validation, audit logging, sending permission, suppression, cost computation, backups or job scheduling.

## 5. Working monthly token scenario (300 accepted companies)

The table uses **estimated** call counts and tokens, NOT measurements from production; see `SalesAI_Golden_10_and_300_Monthly_Plan.xlsx` for editable formula-driven workbook.

| Task | Monthly calls | Input / request | Output / request |
|---|---:|---:|---:|
| Query generation | 15 | 500 | 140 |
| Page extraction (450 candidates × 1.5 pages) | 675 | 1,600 | 220 |
| Structured profile | 450 | 700 | 140 |
| Qualification | 450 | 900 | 160 |
| Opportunity matching | 300 | 1,000 | 170 |
| Draft generation | 150 | 1,300 | 350 |
| Optional claims review | 30 | 1,000 | 120 |
| Reply classification | 30 | 700 | 100 |
| Reply suggestion | 12 | 900 | 300 |
| Meeting extraction | 5 | 500 | 100 |
| Optional analytics summaries | 4 | 1,500 | 300 |
| **TOTAL** | **2,121** | **2,372,800 total input tokens** | **401,000 total output tokens** |

With **25% planning buffer:** ~2,966,000 input tokens + 501,250 output tokens. Text API cost = `input_M × provider_input_rate + output_M × provider_output_rate`. Example *hypothetical* rate pairs (USD per 1M input/output): `$0.30/$1.50 → ~$1.64`, `$1/$5 → ~$5.47`, `$3/$15 → ~$16.42`, `$10/$50 → ~$54.72`. **These are not advertised prices for named models.** Also budget Search API, model-specific tool use, thinking tokens, provider minimums, taxes and any contact enrichment separately. Before buying, verify live API availability and pricing.

## 6. Benchmark method (10 synthetic cases, independent answer key)

Files:
- `SalesAI_Golden_10_and_300_Monthly_Plan.xlsx` — six sheets including `MODEL_INPUTS`, `ANSWER_KEY`, `TEST_SCORECARD`, `MONTHLY_300`, `COST_EXAMPLES`.
- `SalesAI_Golden_10_Synthetic_v1.json` — machine-readable test cases, prompts and answer keys for local offline scoring. **Never forward `expected` or `checks` to LLM.**
- `SalesAI_Model_Inputs_10.jsonl` — safe-to-submit case inputs with no answer key.

**Composition:** 5 CLEAN scenarios (official-site extraction, Scan-to-BIM, qualification, grounded email drafting, meeting confirmation) and 5 MISLEADING/adversarial scenarios (outdated directory vs official website, unrelated BIM acronym, unverified numerical guarantees, `not_now` vs unsubscribe, malicious embedded instruction + unsubscribe).

**Scoring per task:** schema/JSON compliance, evidence-supported facts, semantic correctness, risk of unsupported claims, response handling and channel policies. Do **not** rely on string similarity to a golden email; use factual/semantic criteria and manual review for writing. Treat `unsubscribe`, data leakage attempts and unauthorized sending as **critical zero-tolerance safety failures**, independent of other scores.

**Benchmark process:** run mock plumbing tests first; select four confirmed providers/models in Tier 1; run all ten cases through each under the same prompt/schema settings where supported. Check 5 clean cases for extraction/quality and 5 misleading for refusals of unsupported claims. Record provider/model exact ID, API version, prompt hash, response, request/response token counts, actual charge where available, time, JSON, per-field semantic grading, evidence and errors. Compare with **task-specific** thresholds, not one global percent. Escalate **individual failed tasks**, not whole companies or all models, to Tier 2/3/4. Never let providers see answer keys. When real data is introduced, use permitted/minimized content and human-approved labels.

**Important:** a 10-case fixture is a smoke/gating screen, not statistical proof of model accuracy. Following it, use the real 59-record source set (properly labeled and split into development/holdout sets) and controlled new examples for acceptance. Do not use real sending to benchmark LLM generation.

## 7. Reference data and contact event model

Real source scope: Milan 16, Turin 20, Rome 17, Bozen 3, last 3 LinkedIn = 59 rows. Excel revisions need import-batch IDs, upsert/dedup, source URL/excerpt + collected_at, separate `email` and `linkedin` channel event logs, data confidence, status history and no silent overwrite. Real data is not automatically perfectly correct because it was collected or summarized manually or by AI.

**Sales outcomes:** 3BA / Turin = email-sourced meeting being coordinated; record `meeting_pending_scheduling` until explicit confirmation. LinkedIn external-to-sheet meeting = different channel, not part of email conversion denominator. `not_now` LinkedIn response = future follow-up possibility, not immediate opt-out or meeting; it also does not itself authorize future automated outreach.

## 8. Rights, privacy and sending

- Public company facts can be helpful for research, but **public availability ≠ unrestricted scraping/storage, lawful personal-data processing, or permission to send marketing email**.
- Website terms, robots and source-specific usage policies matter; LinkedIn automated scraping/messaging and search-provider storage constraints need separate assessment.
- Personal names, individually identifiable business emails and messages may trigger GDPR duties. Data passed to any external LLM provider is processed by that provider. Assess contracts, data retention, training policies and lawful purpose before personal data is sent.
- Lawful channel consent/exception, suppression, abuse controls, operator approval and Phase 9 sending authority remain independent of model decisions. Do not connect new LLM to real outbound until these gates are tested.

## 9. Implementation roadmap and acceptance

### S0 — Baseline frozen / decision registry
- [x] Prior SalesAI Phase 1–12 and Production Step 1–6 records retained.
- [x] Historical local Llama3B / Qwen7B benchmarks retained but not required at runtime.
- [x] Local execution + external LLMs chosen; target 10 qualified/day, 300/month.
- [x] `Hamid - Email` excluded.
- [ ] Commit this document and synthetic fixture workbook to developer repository, with proper secrets denylist.

### S1 — Code reuse and laptop footprint audit
- [ ] Identify which FastAPI, discovery, CRM, storage, LLM-client, Task Scheduler and Windows-only modules can be reused.
- [ ] Benchmark CPU/RAM of HTTP fetch, HTML parsing and SQLite operations without local inference.
- [ ] Define safe concurrency, page caps, retry policies and power/sleep behavior.
- **Acceptance:** reuse/refactor/defer matrix; full regression; no local model process.

### S2 — External provider gateway
- [ ] Add per-task API provider adapters, configurable exact model IDs, secrets, timeouts, retries, schema validation, cost ledger, monthly/day caps and opt-in tier escalation.
- [ ] Add data-minimization and prompt-injection boundary (untrusted webpages are evidence, never instructions).
- [ ] Use mock endpoints and synthetic inputs; no send, mail sync or public data before consent/policy review.
- **Acceptance:** 0 secret exposure; response/accounting provenance; budget stop works.

### S3 — Golden test set / benchmark
- [x] Synthetic 5 clean / 5 misleading input and answer-key fixture created.
- [ ] Benchmark Tier 1 (four **currently verified** API model IDs) with same tasks and model-independent scoring.
- [ ] Per-task quality / safety thresholds; hand-review failures; model-specific fallback by task.
- [ ] Label 59-row real reference set only after source and privacy review; use a holdout sample.
- **Acceptance:** reproducible comparisons, actual tokens/costs and explicit failures.

### S4 — Excel/evidence import + contact event consistency
- [ ] Import newer Milan/Turin/Rome/Bozen + 3 most recent LinkedIn rows; dedup, timestamp and provenance.
- [ ] Preserve outcomes and channels; real-event logs drive KPIs, not guessed spreadsheet statuses.
- **Acceptance:** repeated import doesn't duplicate company; histories stay intact.

### S5 — Daily run engine and local UI
- [ ] `START-SALESAI`, `STOP-SALESAI`, job progress, daily 10 accepted lead quota, bounded retries, pause/resume.
- [ ] Draft queue/approval, search-source policies, query limits and readable per-run cost + quality summary.
- **Acceptance:** 10 target when available; transparent shortfalls; laptop light-load; safe stop.

### S6 — Pilot / controlled review
- [ ] Run 10 then 30 real candidate companies, with operator review before any 300/month scale.
- [ ] Record true acceptance rate, time, RAM/CPU, search requests, API token/cost and accuracy.
- [ ] Refit monthly model from measured usage; correct extraction prompts only when meaningful.
- **Acceptance:** owner confirms useful leads; no unapproved outgoing messages.

### S7 — Backup, security and 300/month operation
- [ ] Test restore from encrypted local backup, secrets rotation, audit trail, network failure and cost stop.
- [ ] Validate legal/channel rules and operator send approvals before any outbound integration.
- [ ] Keep optional future server migration as an isolated deployment concern.
- **Acceptance:** production review/signoff; versioned runbook; rollback available.

## 10. Version decision log

| Date | Decision | Status |
|---|---|---|
| 2026-10-21 | No local LLM inference, external API for all semantic tasks | Accepted |
| 2026-10-21 | SalesAI + DB + Dashboard on laptop; no always-on server for MVP | Accepted |
| 2026-10-21 | 10 qualified leads per active day; 300/month example; 450 raw candidates assumption | Planning baseline |
| 2026-10-21 | Ten synthetic tests, 5 clean and 5 misleading, with independent gold labels | Created |
| 2026-10-21 | Four model tiers × four model candidates, escalate per task only | Accepted evaluation method |
| 2026-10-21 | Milan/Turin/Rome/Bozen + last three LinkedIn as reference; outcomes by channel | Confirmed |
| 2026-10-21 | Hamid - Email out of scope | Confirmed |

**Next authorized step:** S1 Code Reuse / Laptop Footprint Audit. **Do not immediately rewrite or discard existing Phase 1–12**.
