# SalesAI S3.2 — First Real API Smoke Wiring & Paid-Dispatch Enforcement

**Scope:** smallest production-relevant change needed to run **one explicitly approved, synthetic, paid LLM smoke case** in the independent Benchmark Runner. This task is **implementation and offline verification only**; it must **not** execute a commercial API request.

## Authoritative baseline

- Baseline Git branch: `feat/salesai-s3-1-ground-truth-benchmark`
- Expected HEAD: `6d47d3f`
- ZIP reviewed independently: `SalesAI_export(20261022-163426).zip`, 449 members, CRC clean
- Independent focused tests: `320 passed` covering S2 and S3
- Reported full-suite baseline: `1249 passed, 8 skipped`
- Create new local branch `feat/salesai-s3-2-first-live-smoke-wiring` only after verifying clean HEAD.

## Why this is necessary (independently reproduced findings)

**LIVE-01: Mock-only provider wiring.** `ExternalBenchmarkRunner` always registers only `openai_compatible_mock` and overwrites all four Tier-1 candidates with mock vendor/model IDs and `pricing.mock.internal`. `--live --confirm-paid` currently cannot reach a verified real model. Its published S3.1 runbook incorrectly implies that the present CLI can perform a real API call. Do not disable the mock-price safety check to circumvent this.

**LIVE-02: CLI per-run cost/request ceilings are not enforced.** `max_requests` and `max_cost_usd` are checked only against broad upper ceilings (20 and $1). They are not used to stop actual cumulative paid dispatch or bind the budget policy. Even `max_requests=0` and `max_cost_usd=0.0` are accepted for live runner initialization, while internal per-request cap remains `$0.05`. Enforce the user-selected ceilings over **all attempted dispatches and reserved/uncertain spend**, before every outbound request. Invalid/zero/negative/NaN/Inf limits must reject.

**LIVE-03: Runbook accounting is inaccurate.** Runner uses `:memory:` SQLite by default; the runbook queries a nonexistent SQLite file/table/columns. Use a persistent, locally excluded SQLite database for actual paid runs; update audit commands to actual schema (`llm_reservations` etc.) and never advise querying a fictitious path. Ensure live crash recovery and uncertain reservations are auditable.

**QUALITY-01: Golden coverage is not complete.** The 70 fictional cases exist, but `expected_output` is empty for `discovery_query_generation`, `reply_suggestion`, and `outreach_draft`, and some other paths are only partly semantically checked. **One-case live smoke must never be presented as model qualification.** Treat full task quality validation as an explicit follow-on before recommending a model as proven suitable; track it as a known limitation, not an extra blocker for smoke connectivity.

## Required minimal implementation

1. **Explicit provider configuration (live-only):** Add a small, typed runtime configuration for *one verified Tier-1 slot* (e.g. `t1_c1`) targeting the actual `OpenAIProvider` HTTPS adapter and exact official model ID `gpt-6-luna` only when explicitly selected. Verify its availability/capabilities from official provider documentation, not mock labels. Keep defaults disabled; do not put API keys in files, reports, Git, environment dumps, exception messages, or tests.
2. **Verified, versioned rate card:** Ground its standard price in current official provider docs (`https://developers.openai.com/api/docs/pricing`, as independently checked on 2026-10-22, for standard short-context `gpt-6-luna`: input `$0.10` and output `$0.50` per million tokens). Store source URL, effective/checked date, currency, and pricing mode. If pricing or eligibility cannot be verified in the execution environment, fail closed. Do not silently treat a cached or unknown price as `$0`.
3. **Correct provider registry:** Dry-run retains mock provider and fictitious slots. Live mode must register *only* the explicitly chosen real adapter/candidate, never use a mock as fallback, and never rewrite remaining Tier-1 slots. Preserve the four-tier architecture and per-task independent selection.
4. **Actual dispatch limits:** Enforce positive integral `max_requests`, positive finite `max_cost_usd`, runtime bounded at the requested values and at existing global daily/monthly caps. For the first smoke allow **one task, one candidate, one case, one paid request**, one concurrent call; no automatic retries, tool calls, provider switching, or tier escalation. Count attempts including failures/unknown outcomes; account for reserved and uncertain usage conservatively.
5. **Per-run budget isolation:** Couple the per-run cap to SQLite atomic preflight reservation, with verified input/output rates and conservative maximum output token assumption. If a request might exceed any remaining cap, do not send it. Expose a precise read-only summary from the same SQLite file after the run.
6. **Minimal adapter compatibility:** Create offline `httpx.MockTransport` tests for the exact model's request fields and parsed token usage. Do not guess unsupported fields or force JSON mode unless supported; clear structured failure on HTTP 400, 401/403, 429, 5xx, and malformed responses. Observe request/response identity and privacy filters.
7. **Fail-closed smoke preflight:** Without user opt-in, billing confirmation, credentials, verified candidate, valid rate card, synthetic-only fixture, persistent local ledger, and positive effective limits, perform zero outbound calls. Never automatically prompt to paste a secret into the terminal transcript or chat.
8. **Crash/timeout semantics:** Never erase an uncertain external billable request as free; mark `UNCERTAIN` on ambiguous execution and block replay of the same request identity. Separate failures before dispatch from failures after possible dispatch.
9. **Ground-truth status:** Tag the pilot `SMOKE_ONLY / NOT MODEL-QUALIFIED`, irrespective of schema success. Do not auto-approve production routes, send email, or change business records.
10. **Correct operator runbook:** Provide a fully accurate Windows PowerShell sequence: verify real provider and price from official docs, set key only in temporary session environment, explicitly opt into *one* paid smoke run, verify one dispatch/usage and local ledger from actual DB/schema, remove environment key. Distinguish an API-provider dashboard cap from an application budget cap. The revised runbook must **not** claim the current S3.1 command alone already works.

## Test and acceptance cases (all offline)

- `--live` alone: zero dispatch.
- `--live --confirm-paid` without verified live config, key, or price: zero dispatch.
- Numeric caps `0`, negatives, floats/NaN/Inf for request count, and zero/NaN/Inf cost: refused before dispatch.
- `--max-requests 1` with two possible candidates/cases: **at most one physical provider call**.
- `--max-cost-usd` less than conservative reservation: **zero calls** even if broad monthly budget permits it.
- Cumulative reconciled + reserved + uncertain per-run amount cannot exceed user cap.
- A mock candidate/price source in live mode is refused.
- A provider/model mismatch is refused pre-dispatch.
- Valid real test candidate + mocked HTTP response gives exactly one fake dispatch, correct token and cost bookkeeping, correct schema/evidence statuses, `SMOKE_ONLY` and no production approvals.
- Restart with a persistent test DB preserves audit records and prevents cost-free replay.
- Failed/timeout provider attempts do not result in silent `$0` accounting.
- No raw prompt, key, token or private records are logged or written into reports.
- Existing S3.1 ground-truth checks and 320 focused regression tests remain green.

## Scope boundaries

- Do NOT run any real/paying API calls in this implementation step; tests must use fake credentials and HTTP mocking.
- Do NOT modify any of the six operational SalesAI LLM call sites, send emails, run scraping, change business/CRM datasets, install local LLMs, push, or merge.
- Do NOT implement adapters for four vendors or fill all 16 candidate slots now. One verified candidate is sufficient for first smoke. More candidates will be added sequentially **only if task evaluation requires them**.
- Do NOT mark a Tier-1 model as selected on the basis of a single smoke case, generic JSON validity, or incomplete golden answers.
- Prefer minimal, auditable changes over broad refactors and extra subphases. Document nonblocking issues instead of expanding the scope.

## Deliverables

1. Exact changed files, new offline tests, runbook, source-verified price/capability assumptions and any blocking unknowns.
2. Tests: new focused paid-gate tests, all previous S2/S3 tests, relevant sending/suppression tests, full test suite, repository hygiene and ZIP CRC.
3. Local Git commit on S3.2 branch; rebuild and verify `SalesAI_export.zip` with sensitive data exclusions. No push or merge.
4. `docs/architecture/S3.2-FIRST-LIVE-SMOKE-REPORT.md`, including proof that live CLI is no longer wired to mocks and that *requested* cost/request limits are actually enforced.

**STOP after S3.2. Real paid execution requires a separate explicit operator decision after independent ZIP review.**
