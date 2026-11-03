# SalesAI — S3.0: Benchmark Runner & Paid-Dispatch Safety Gates

**Date:** 2026-10-22  
**Status:** Implementation work order (NOT yet implemented)  
**Input baseline:** branch `feat/salesai-s2-6-budget-runtime-guards`; reported HEAD `2b00b6d`. Verify HEAD and clean working tree rather than assuming.  
**Goal:** Prepare a reproducible, independent, task-by-task benchmark of Tier 1 external LLMs *before* wiring the six SalesAI production call sites. Windows laptop + localhost + local SQLite. No local LLMs, cloud server, automatic emailing or background operations.

## Development priorities

Ship the smallest useful, repeatable benchmark; avoid another general hardening phase. Two **confirmed budget-integrity blockers** in S2.6 must be fixed as part of S3.0 before any paid dispatch. All other non-blocking findings go into a deferred list. S3.0 itself must run fully offline using mocked HTTP. Do not request API keys, call paid providers, or enable paid execution during this work order. The operator will separately authorize the first live pilot after review.

## A. Confirmed paid-dispatch blockers from independent ZIP review

**A1 — Duplicate network dispatch using the same `request_id`.** Reproduced against extracted S2.6 code: calling `RuntimeGuard.guarded_call` twice with the same LLMRequest dispatched the fake provider **twice**, while `BudgetLedger.get_budget_summary()` reported only **one** reservation. `BudgetLedger.reserve` returns an existing `RECONCILED` record and the guard proceeds to send again. Fix end-to-end idempotency so one request ID can never generate a second paid HTTP request; a duplicate must either return a safely cached prior response (if one is safely available) or fail closed with a clear non-PII error. Do not cancel someone else's original reservation. Enforce SQLite uniqueness/transactional state with concurrent requests, including after restart. A new operator-authorized attempt must use a new request ID and its own reservation.

**A2 — Pricing/candidate identity mismatch.** Reproduced: budget reserved funds under an inexpensive `ModelCandidate` (`exact_model_id="economy-model"`), while the request dispatched `model="expensive-model"`; this was accepted. Before preflight and again before HTTP, verify exact equality of request provider/model/tier/task/subtask to approved candidate and authorized context; do not accept a caller-supplied arbitrary candidate to override production routing. Benchmark candidates may use a separate explicit test-only authorization object, but must still bind the actual API model and verified price to the same immutable candidate. On mismatch, zero HTTP calls and no billable reservation.

**Minimum further spend safeguards:** Enforce `RuntimeGuard` on **every** paid benchmark execution; never expose a direct provider path in the live runner. Use known provider rate limits and a conservative verified upper bound on token usage, not only character length. Explicitly cap output tokens and acknowledge provider-specific extra billed token categories. Unknown pricing, missing actual usage or unsupported response shape means stop further dispatch. Provider-side hard spending caps are strongly recommended because local ledger is not the provider's final invoice. Defaults: paid mode OFF, concurrency 1, retries 0, no automatic model fallback, no tier escalation.

## B. Benchmark runner deliverables

1. Add a focused `sales_engine/benchmark/external_runner.py` (or equivalent) and a local CLI `scripts/benchmark_external_llm.py`. Prefer reusing the existing `sales_engine/benchmark/` helpers and gateway rather than creating a second orchestration framework.
2. CLI supports `--dry-run` (default), `--task`, `--candidate`, `--fixture-file`, `--max-cases`, `--output-dir`, and safe output format. Stub/disabled live mode is allowed, but **must not perform real HTTP in S3.0**. Never add an unattended job, timer, public API route or background service.
3. Inputs come through `MinimumDataPayloadBuilder`; outputs go through S2.5 `LLMOutputValidator` and the existing business safety validators. Use exact task-specific schemas and evidence IDs; prohibit any model output from authorizing sending or modifying CRM.
4. Every run has an immutable `run_id`, `case_id`, `task_id`, `subtask_id`, `candidate_id`, exact provider/model, prompt version, schema version, fixture version, judge version, measured elapsed time, and provider token/cost fields as nullable when unmeasured. Keep raw sensitive prompts/results out of default logs; fixture/test outputs local only.
5. Benchmark scorecards must be generated from **actual, stored outputs and deterministic checks**, not fabricated pass/fail values. An optional human-review score may be recorded separately, with explicit reviewer identity from trusted local authorization (not a mere self-declared model field). Before operator authentication is available, evaluations are proposals only, never production approvals.
6. Selection is independent per task/subtask, **Tier 1 candidate #1, then #2, #3, #4**; stop at the first qualifying candidate for that task. Tier 2 only after Tier 1 exhausted and explicit operator approval. No running all 16 models for every task. Support different vendors/models for different tasks.
7. JSON/schema compliance, hard safety disqualifiers, evidence grounding, actual task-specific semantic correctness, and spending/latency are distinct metrics. Do not score free-text outputs by literal string similarity alone. Do not use an LLM judge in S3.0; start with deterministic evaluators and local human review.
8. Isolate test inputs from hidden gold labels and expected answers: the provider sees evidence and task instructions only. Keep expected responses solely inside the local scorer. Test fabricated claims, misleading source claims, opt-out, bounce, prompt injection and inconsistent evidence.
9. Produce a local report (CSV/JSON/Markdown) with one row per candidate x task x case; aggregate PASS/FAIL and categorized failure reasons per task, not one opaque global accuracy figure. No assertion of statistical certainty at 10 examples. Separate syntactic scores from semantic review results.
10. Do not modify existing six operational call sites, sending layer, current user data, reference Excel workbooks or production databases. Keep S3.0 additively isolated and reversible.

## C. Golden fixture strategy

Existing `docs/architecture/SalesAI_Golden_10_Synthetic_v1.json` contains **10 synthetic scenarios total**, with legacy task labels (`company_extraction`, `company_qualification`, `email_draft`, `meeting_time_extraction`, `reply_classification`). These DO NOT cover all seven new Gateway task/subtask contracts as-is. Build a deterministic converter/mapping that **preserves original expected meaning and flags unmappable cases**, and add local *fictional* cases where required. Do not relabel a meeting-time extraction case as an existing Gateway task without an explicit defensible mapping.

Target a small first pass (at least one positive and one adversarial case for each of the seven paths), then work toward **10 cases per path: 5 ordinary/grounded, 5 deceptive or contradictory**, each with correct gold labels. Keep dataset authoring distinct from scoring. Use Milan, Turin, Rome, Bozen and the newest 3 LinkedIn records only as *later* qualitative reference material under local privacy controls; avoid including genuine contacts or email text in API payloads until reviewed and minimized.

Seven paths: `website_extraction:pass_a`, `website_extraction:pass_b`, `job_analysis`, `discovery_query_generation`, `inbox_classification`, `reply_suggestion`, `outreach_draft`.

## D. Candidate activation and the first real pilot (future step, NOT S3.0)

Create a **non-executable checklist**, not active API calls: confirm exact model ID and the provider's current official capability/pricing terms; populate only the tested Tier 1 candidate; securely configure API key via Windows environment or ignored `.env`; verify user/operator approval; activate strict limited budget (illustrative $1/day, $15/month; must be explicitly accepted by operator); show sanitized payload preview; run ONE synthetic case first; inspect model usage, ledger, and output validity; only then gradually run additional synthetic cases. For Google/Anthropic/Mistral, add a native adapter only when the specific queued candidate needs it, rather than introducing four providers at once. Never invent model names or prices.

**Live release gate:** A1 and A2 independently retested and passing; no unmetered paid path; blocked request on invalid/absent pricing, model ID or unknown tokens; no secret leakage; explicit opt-in; no outreach sending. `S3.0` produces code and tests for operator review; it does not authorize a paid pilot itself.

## E. Tests and acceptance

- Add >=12 focused tests including repeat same request ID (serial, concurrent, after restart), mismatched candidate model/provider, status/idempotency, no call when budget disabled/unknown pricing, seven-path fixture loading, hidden answer separation, safety fail overriding high score, independent per-task selection, budget-off dry-run, and zero networking in default CLI.
- Run all Gateway tests, representative sending/privacy tests and full pytest regression once after changes; audit repository hygiene and zero secret/data exposure in ZIP.
- Save `docs/architecture/S3.0-BENCHMARK-READINESS-REPORT.md` with technical outcome, separate blocker/non-blocker list, exact counts, baseline branch, local commit, archive file count and rollback.
- Commit locally on a dedicated branch, rebuild `SalesAI_export.zip`, check archive CRC and denylist; **NO git push/merge**.

**STOP after S3.0. Return code/report/ZIP. Do not activate commercial API keys, run real LLM requests, start S2.7 or automate email sending.**
