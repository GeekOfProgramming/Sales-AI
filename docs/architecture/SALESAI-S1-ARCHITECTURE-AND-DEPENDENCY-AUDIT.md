# SalesAI — S1: Lightweight Laptop Architecture & Dependency Audit

**Status:** Ready for Antigravity implementation (audit-first).  
**Date:** 2026-10-21  
**Base:** S0 cleanup branch `chore/salesai-legacy-retirement`, commit `1b6c50e8f1a615cf462bce296b604b69b915d721`. Verify actual Git HEAD and clean working tree before starting.  
**Next phase after sign-off:** S2 external multi-provider LLM gateway. **Do not implement S2 in S1.**

## 1. Fixed business architecture

- The SalesAI application remains on the developer's Windows laptop: FastAPI localhost UI, deterministic discovery/scheduling, source-management, company profiles, scoring, local DB, evidence, draft/review, inbox/replies, analytics and logs.
- All **LLM inference** is delegated to remote HTTPS model APIs in S2. No local Ollama installation, downloading or running models; no reliance on the Hamed laptop. The laptop still does ordinary Python, lightweight web requests, DB operations and validation.
- Manual/operator-initiated runs; capacity target **10 qualified companies per active day** (planning estimate ~300/month), not necessarily just 10 discovered candidates. Enforce future query/crawl and budget caps.
- The local UI binds only to `127.0.0.1` by default. No public tunnel or LAN exposure. Explicitly report any ways the current code still supports an external tunnel or wide-area bind.
- Local database and files are authoritative. When remote LLM APIs become enabled, only the minimum necessary prompt/evidence is sent outside the device. Store API secrets outside Git, Excel, user-facing logs and released ZIPs.
- The existing Phase 9 human approval, suppression, contact-rights checks, no-autosend controls and Phase 11 reply safeguards must remain intact. No live sending, live scraping, inbox sync or account/API key changes in S1.
- User's separate Hamid - Email project is **out of scope**.

## 2. Scope and required outputs

This is an **inventory, reuse/retirement decision and lightweight architecture plan**. Do not rebuild Phase 1-12, migrate DB, implement remote providers, or silently remove valuable operational code. Produce:

1. `docs/architecture/S1-CURRENT-ARCHITECTURE-MAP.md` — modules, imports, entrypoints, complete workflow, data paths, source boundaries and security controls. Map all Phase 1-12 plus Production Steps 1-6 to current code. Explicitly flag active, partially implemented, mock-only and legacy code.
2. `docs/architecture/S1-DEPENDENCY-AUDIT.md` — audit direct/transitive dependency versions, Python Windows compatibility, import-time side effects, disk/RAM/CPU resource classes. Group into core lightweight, optional browser/scraping, optional legacy/Ollama/RAG, test/dev. Detect unpinned, unused and Windows-only modules. Propose pinned `requirements-core`, `requirements-optional`, `requirements-dev` and a clean venv plan **without force-upgrading packages yet**.
3. `docs/architecture/S1-LLM-CALL-MAP.json` and `.md` — for every real LLM call: originating route/job, module/function, data sent, model method, expected output/schema, prompt construction, parsing, failures, fallback, legal/personal-data classification, latency/cost estimates (unmeasured marked as estimates), and affected tests.
4. `docs/architecture/S1-LOCAL-SECURITY-AND-RESOURCE-REVIEW.md` — localhost bind and startup, tunnel risks, health probes, credentials, secret-leakage paths, data/log retention, process count, IO and concurrency/crawl limit recommendations. Measure baseline idle startup RSS and startup time **only if safe locally**; otherwise label not_measured. Never infer usage from synthetic values.
5. `docs/architecture/S1-S2-MIGRATION-BACKLOG.md` — small, ordered S2 implementation tasks for remote-provider interface/adapters, validation and schema enforcement, minimum-data payload builder, task-based routing, four price/quality tiers, per-request token/price logging, budget caps, retries/timeouts, circuit breaker, provider outages, offline deterministic mode and audit trail. Include no uncontrolled automatic escalation. Prioritize 1 provider first and stage others.
6. `docs/architecture/S1-ACCEPTANCE-REPORT.md` — exact branch/commit, checks done, potential blockers, test baseline and risk register, deferred items, next stage entry criteria.

## 3. Known high-value inspection targets (verify against live repository)

- `ai_engine/llm_client.py`: old `BIMLLMClient` and `ollama` import; **do not delete yet**. Six core SalesAI modules directly use this interface:
  - `sales_engine/analysis/website_analyzer.py`
  - `sales_engine/analysis/job_analyzer.py`
  - `sales_engine/discovery/query_generator.py`
  - `sales_engine/inbox/classifier.py`
  - `sales_engine/inbox/suggestion.py`
  - `sales_engine/outreach/email_generator.py`
- `backend/main.py`: `/health` currently probes local Ollama/ChromaDB and has legacy RAG ingestion/admin-gate logic. Distinguish business-required admin security from obsolete RAG features. Specify how to make health independent of local Ollama once S2 arrives.
- `run_sales_ai.bat`: no Ollama auto-start or auto-pull as of S0, but still performs a local Ollama status probe. S2 should eliminate this entirely.
- `start_server.py`: default localhost bind is good, but old optional Cloudflare Quick Tunnel support (`ENABLE_PUBLIC_TUNNEL` or `--tunnel`) remains. **Never enable it during this stage; propose safe removal or explicit guarded disabling before any production usage.** Old module docstring is outdated.
- `ai_engine/data_ingestor.py`, `rag_retriever.py`, `ingest_queue.py` and `data/rules/*`: inspect runtime/test coupling and plan removal or archival only after preserving admin-gate and functional safeguards.
- Historical Step 5/6 hardware and Ollama benchmarks: retain as evidence and developer archive; not a runtime dependency for the target product.
- `config`, `data`, `runtime` and `docs/architecture`: identify generated state vs source-controlled examples. Do not delete any genuine database, `.env`, authentication data, imported leads or outreach history.
- `backend/sales_ui` and `static/sales`: preserve routes, templates, workflows and UI.
- `release_manifest.json`, `repo_manifest.json`, `scripts/audit_repository.py`, `scripts/rebuild_zip.py`: ensure the developer-source/production-runtime profiles correctly reflect S0 and future migration without bundling personal data.

## 4. Reference scenarios and success criteria

- Reference rows: Milan 16, Turin 20, Rome 17, Bozen 3 and **the last 3 newer LinkedIn rows** = 59 reference records. They are illustrative; don't assume all inputs are verified true.
- Turin 3BA S.r.l. is an email-sourced *meeting awaiting date confirmation*, not yet a confirmed meeting. A separate LinkedIn-origin meeting must **not** count as email conversion. A new LinkedIn reply with “no project now, contact us later” is `not_now/future_follow_up`, not automatically `unsubscribe`.
- The `SalesAI_Golden_10_Synthetic_v1.json` and `SalesAI_Model_Inputs_10.jsonl` under `docs/architecture` form the synthetic benchmark set (five clean and five noisy/deceptive). Never expose ground-truth answers inside the candidate model input.
- Tasks anticipated for external LLMs: research condensation, website/job extraction, structured normalization, company classification, qualification/opportunity, contact-role inference, B2B email draft, grounded draft review, reply classification/suggestions and meeting-time extraction. Distinguish true LLM requirements from deterministic operations (URL fetch, dedup, provenance, suppression, sending authority, score formulas, CSV/XLSX export, budget enforcement).
- Future S2 golden tests must verify: correct enum/schema, correct business facts, grounded statements, no invented sender/company capacities/time commitments, source attribution, privacy minimization, opt-out/stop handling, prompt-injection resistance, timeout, rate limit and provider failures. Do not assume `json_valid == factually_correct`.

## 5. Work instructions and safeguards

1. Confirm existing backup, Git state, branch and HEAD. Do not touch the user's separate backup or unrelated projects. If uncommitted changes are present, preserve them and report before branching.
2. Create `audit/salesai-s1-laptop-dependency-map` from the S0 commit, unless the current branch is already a dedicated clean S1 branch.
3. Inspect real imports and dynamic references. Produce the six requested audit/backlog artefacts in `docs/architecture/`.
4. **Do not install, uninstall or upgrade** Python, Ollama, system tools or packages. Do not pull models, send requests to external LLM APIs, push Git, deploy to a public URL or contact live companies.
5. If small non-business-affecting changes are demonstrably needed to enable accurate audits (e.g., a read-only inspect script), add them with focused tests. Otherwise S1 is documentation-first. Never redesign or rename business APIs in this stage.
6. Run the prior 929-pass/8-skip full suite when practical and any focused tests touched. If all pass, report exact real test counts; don't simply repeat a past pass count.
7. Run repository hygiene audit; if a ZIP is rebuilt, verify exact path, file count and zero denylist items. Do not blindly bundle runtime secrets/state.
8. Commit S1 deliverables locally and supply rollback instructions; **do not push/merge** without explicit authorization.
9. Stop after S1. Do **not** begin S2 external gateway or ask for real provider API keys until S1 has been reviewed.

## 6. Acceptance gate

Accept S1 only if all six documents exist; all six LLM call sites and legacy health/tunnel behaviors are mapped; phase/step responsibilities and critical data/authority boundaries are explicit; the lightweight laptop dependency plan distinguishes installed from optional libraries; all unmeasured performance/cost values are identified as such; test results and status are traceable; rollback is documented; and no operational database/credentials or sending authority were altered.
