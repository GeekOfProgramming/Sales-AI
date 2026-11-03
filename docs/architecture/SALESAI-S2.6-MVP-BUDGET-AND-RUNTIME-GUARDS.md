# SalesAI S2.6 — Minimal Budget Ledger & API Runtime Guards (MVP)

**Status:** Implementation specification — NOT implemented.
**Accepted starting point:** `fix/salesai-s2-5-2-safe-logging`, reported HEAD `bc5dabe`.
**Target:** SalesAI running on a Windows laptop, `127.0.0.1` UI, local SQLite, remote LLM inference only; ~10 qualified companies per active day / ~300 monthly.

## Purpose and development policy

Complete the **minimum operational safeguards** needed before small, explicitly authorized paid API pilots, without extending the chain of security-hardening subphases. Fix only verified blockers now; record low-risk issues in a deferred backlog. No real API call is allowed by this S2.6 implementation.

Blocking before a *live paid pilot*: uncontrolled or unbounded spending, accidental external transmission of real private records, credential leakage, bypass of operator consent, unapproved model routing, unsolicited autonomous email sending, and data corruption. Other edge cases can be documented and prioritized after model benchmarks.

## Deliverables

1. **Configurable spending policy**: Default all paid dispatch to disabled. Daily and monthly USD limits, per-request maximum projected spend, request timeout, maximum concurrent LLM calls (recommended 1–2 initially) and cooldowns; validate all configuration. Do **not** silently choose or hardcode a paid budget. An illustrative policy is $1/day, $15/month; actual values require explicit operator configuration before paid use. Treat limits as locally enforced safeguards, not as guarantees that provider billing matches local estimates.
2. **Local accounting**: A lightweight independent SQLite usage/reservation ledger (`data/llm_usage.db` or another clearly documented location under `data/`), kept out of source ZIP and Git. Record task, subtask, provider, exact model, correlation ID, timestamps, actual provider-reported input/output usage, price version/source/date, reservation, final calculated spend or unknown, status, and bounded non-sensitive failure codes. Never record raw prompts, completions, API keys, contact data, or raw provider metadata.
3. **Preflight reservation**: Before any future billable request, require an explicit provider/model allowlist, configured verified pricing with a currency and effective date, conservative declared input-token upper bound and maximum output-token cap; atomically reserve a conservative monetary upper bound against the daily/monthly limits. If a reliable reservation cannot be made, fail closed. A character-count heuristic alone is not adequate proof of maximum token cost. Keep reservations unique/idempotent per invocation. If no model is enabled, mocked/dry-run flows continue to function.
4. **Reconciliation**: On response, persist provider-reported usage; calculate price only from verified policy metadata, including provider-specific additional billed token categories if applicable. Unknown/ambiguous usage or interrupted transport must never be booked as free (`0`); preserve/flag outstanding reservation and stop subsequent paid dispatch for that scope until safe reconciliation or authenticated operator review. Safely cancel reservations only when no provider call was dispatched. Prevent duplicate charges in ledger from duplicate callbacks or async/sync races. Include bounded stale-reservation recovery requiring explicit review rather than automatically refunding uncertain calls.
5. **Local resource/rate controls**: One shared concurrency limiter and minimal per-provider cooldown/rate guard (sync and async parity); first 429 and 5xx should fail with a clear status. Do not automatically retry, rotate vendors, escalate tiers or send additional paid requests in this stage. If circuit breaker is needed, a simple configurable cooldown is enough; avoid a complex distributed system.
6. **Operator-visible overview**: Expose a *local-only*, read-only method or admin view to inspect today/month totals, outstanding reservations and request counts (not raw prompts). Do not make this publicly accessible via Cloudflare Tunnel; retain localhost-only default. No automatic emailing.
7. **Integration seam only**: Wrap existing `LLMProviderGateway` execution or offer a clearly defined preflight/dispatch wrapper. Do not alter the six SalesAI production LLM call sites yet, do not use commercial credentials, and do not activate the gateway by default. Maintain approved per-task Tier-1-first routing and the existing privacy and output validation layers.
8. **Offline tests**: Add focused deterministic tests (target >=15) using a fake provider and isolated temporary SQLite file: disabled default, exact limit, concurrent reservations, concurrent sync/async calls, unknown prices, missing or stale usage, timeouts, rate limit, idempotency and recovery, budget rollover, no raw PII in ledger, and no second paid dispatch on error. Ensure legacy regression tests remain unchanged.

## Scope exclusions

- No real paid LLM calls, real API keys, live company scraping, contact enrichment, model benchmarks, secondary providers, automatic retries, auto-escalation or migrations of the six operational call sites.
- No local model downloads/inference or mandatory heavyweight SDKs.
- No Git push, merge, destructive data modifications, or release of raw operational data.
- Do not introduce cloud infrastructure or background workers: local Windows workstation only.

## Verification and handoff

- Verify Git HEAD, clean tree and the actual baseline; create dedicated S2.6 branch.
- Run new focused tests plus existing Gateway and representative sending/privacy tests; run full regression once at the end and repository hygiene audit.
- Produce `docs/architecture/S2.6-MVP-BUDGET-REPORT.md` with limitations and a **deferred-issues register** divided into blocker vs non-blocker.
- Commit locally, rebuild the current developer-source ZIP and verify inclusion, archive integrity and zero sensitive data.
- STOP after S2.6. Next sequence is S2.7 controlled wiring/operator authentication, then S3 small real Tier-1 benchmark with explicitly authorized spend limits.

## Acceptance criteria

- Default application state cannot dispatch any paid API calls.
- Any intended dispatch without verified price/usage boundary, approved route or remaining budget is prevented *before* HTTP transmission.
- Uncertain usage is never treated as zero expenditure; accounting survives restarts and concurrency.
- Existing sending/suppression and SalesAI Phase 1–12 business logic remain intact.
- No unexpected network calls in tests; code remains lightweight on laptop.
