# SalesAI — S2.1 External LLM Gateway Foundation

**Status:** Implementation specification; NOT an implementation report  
**Date:** 2026-10-21  
**Authoritative input:** accepted S0/S1 baseline, specifically `docs/architecture/S1-S2-MIGRATION-BACKLOG.md` and `docs/architecture/S1-LLM-CALL-MAP.json` in the latest repository.  
**Target:** Windows developer laptop, localhost SalesAI UI, local operational data, **remote commercial LLM APIs in later substeps only**, target ~10 qualified companies/active day (~300/month).

## 0. Preconditions and source-of-truth rules

1. Inspect actual repository before editing: `git status`, `git branch --show-current`, `git rev-parse HEAD`, and required S1 docs. Expected last S1 commit from user report: `1f4d8a4`, branch `audit/salesai-s1-laptop-dependency-map`. Do not blindly assume HEAD if it differs; state divergences.
2. Create local S2.1 branch from verified S1 HEAD, e.g. `feat/salesai-s2-1-llm-gateway-foundation`. Do NOT check out `main` as a substitute or merge/push automatically.
3. Ensure working tree clean before proceeding. Preserve all untracked user data, `.env`, SQLite files, imports, reports, and mail state. Never touch actual recipient data.
4. S2.1 is **contracts and offline scaffolding only**. Do not activate a commercial provider, make HTTPS calls, purchase credits, or ask for real API keys. Do not migrate any of the six production LLM call sites yet.
5. Do not introduce additional heavy SDK dependencies, local LLM/model usage, browser execution, or installation of Ollama. Preserve Phase 1–12 business behavior, especially Phase 9 send controls and Phase 11 unsubscribe/triage authority.

## 1. Existing call-site inventory: preserve without rewiring

Based on S1 call map, the following **six** sites will migrate in S2.7 but must remain behaviorally intact for S2.1:

| ID | File | Invocation |
|---|---|---|
| LLM-SITE-01 | `sales_engine/analysis/website_analyzer.py` | async; two-pass website extraction |
| LLM-SITE-02 | `sales_engine/analysis/job_analyzer.py` | async; job signal extraction |
| LLM-SITE-03 | `sales_engine/discovery/query_generator.py` | sync; query suggestions |
| LLM-SITE-04 | `sales_engine/inbox/classifier.py` | sync; inbound reply classification |
| LLM-SITE-05 | `sales_engine/inbox/suggestion.py` | sync; draft reply suggestions |
| LLM-SITE-06 | `sales_engine/outreach/email_generator.py` | sync; outreach draft generation |

Current `ai_engine/llm_client.py` imports Ollama. **Keep it as transitional legacy at S2.1** so older tests continue to pass. Its removal is explicitly outside this step.

## 2. Implement a small, dependency-light package

Suggested files (adjust only with documented rationale):

```
sales_engine/llm_gateway/
    __init__.py
    contracts.py
    errors.py
    interface.py
    registry.py            # optional; local registration only, no network or autodiscovery
    config.py              # optional; fail-closed disabled-by-default config

tests/test_s2_1_llm_gateway_foundation.py

docs/architecture/S2.1-GATEWAY-FOUNDATION-REPORT.md
```

### 2a. Canonical request

Use project-compatible Pydantic 2 model or similarly strongly validated, serializable dataclass with fields:

- `task_id`: bounded nonempty canonical task label (allow six current tasks and future benchmark task IDs without hidden mapping)
- `prompt`: nonempty user/task text; explicit size bound
- `system_prompt`: optional system constraints
- `provider`: explicit logical provider ID or `None` while disabled; no silent provider selection
- `model`: explicit model ID or `None` while disabled; do not bake in unverified current SKU names
- `tier`: optional 1–4 quality/cost classification **metadata only** in S2.1; no tier routing
- `response_schema`: optional JSON Schema dictionary; avoid a Pydantic field literally called `schema` because it conflicts with model methods; if compatibility with S1 names is needed, expose a documented alias
- `temperature`: finite numeric 0–1 inclusive
- `max_output_tokens`: positive bounded integer
- `request_id`: opaque correlation ID (no emails, subject lines, URLs or personal data)
- `timeout_seconds`: positive bounded number, explicit default (no hidden unlimited waits)
- optional `metadata`: allowlisted operational identifiers only; no secrets or raw personal records

Avoid embedding request bodies in exceptions, traces or debug `repr` where this could leak PII. If a `model_dump()` serializes prompt as needed by future providers, redact prompt and system text in observability/log adapters.

### 2b. Canonical response

Strongly typed fields:

- `content` (raw text)
- `provider`, `model`, `task_id`, `request_id`
- `parsed_json` optional **only if actually parsed**
- `input_tokens`, `output_tokens` optional non-negative integers (absence -> `None`, never guessed)
- `cost_usd` optional non-negative decimal (unknown -> `None`, never silently zero)
- `latency_ms` optional non-negative number (unknown -> `None`, never synthetic)
- `finish_reason` optional string, and provider request ID optional opaque string

Constrain model types. Keep raw provider-specific metadata separate from core fields, scrub sensitive values, and never serialize API credentials. Do not claim semantic grounding from mere JSON validity.

### 2c. Interface and error taxonomy

Expose one clear abstract provider contract (`BaseLLMProvider`) and a gateway/facade interface (`LLMProviderGateway`) covering **both** synchronous and asynchronous invocations, since existing SalesAI call sites use both. Methods may be `generate(request)` and `generate_async(request)`. If a sync/async bridge is introduced, it must not invoke `asyncio.run()` inside an already running event loop or conceal blocking behavior.

Common typed exceptions (no leaking prompts/tokens/secrets in `str`/repr):

- `LLMError` base
- `LLMNotConfiguredError` (no explicitly chosen/activated provider)
- `LLMAuthenticationError`
- `LLMTimeoutError`
- `LLMRateLimitError`
- `LLMQuotaExceededError`
- `LLMBudgetExceededError` (defined now, enforced later in S2.6)
- `LLMInvalidSchemaError`
- `LLMProviderUnavailableError`

Preserve safe error codes and retryability flags, but implement **no automatic retries or tier escalation** in S2.1.

### 2d. Fail-closed behavior

- If provider/model or credential config is absent: explicit `LLMNotConfiguredError`; no network call, no Ollama fallback, no automatic vendor selection.
- An unregistered provider cannot be invoked.
- Provider registration must be local and explicit, using dependency injection; do not import any commercial SDK or open HTTP connection at module import.
- An `OfflineFakeProvider` may be supplied **in tests only**, with deterministic canned responses; it must not be selectable in the normal runtime by accident.
- Do not create a production stub that returns a fake successful completion or invented tokens/costs.
- Schema checking, PII scrubber, budgets, persistence and fallback implementations are reserved for S2.4–S2.6.

## 3. Test acceptance: focused, offline, deterministic

Add focused tests covering at least:

1. Typed request construction and defaults; boundaries for empty prompt/task, oversized prompt, NaN/Inf temperature, bad token cap/tier/timeouts.
2. Typed response parsing, optional measurement fields, zero vs unknown, decimals; prohibit negative counts/latency/cost.
3. Sync and async fake-provider invocation are equivalent in meaning and never perform network access.
4. Misconfigured/unregistered provider raises `LLMNotConfiguredError`; no Ollama fallback.
5. Error subclasses and stable error codes/retryability without leaking confidential input.
6. Provider registration rejects duplicates unless explicitly replaced by a test-only mechanism; no silent tier escalation.
7. Imports require only already-present lightweight packages; no import of `ollama`, `chromadb`, `torch`, commercial SDK, or local model client inside new gateway package.
8. No sending/inbox sync/discovery is triggered by constructing, importing or testing gateway contracts.
9. Python syntax/runtime compatibility with project's supported Python version(s) (at minimum current dev Python 3.14; prefer 3.10-compatible annotations when practical).
10. Existing tests remain unchanged, including Phase 9/11 and S0/S1 repository integrity.

Run new focused tests, related business safeguard tests, `python -m pytest tests/ -q`, `python scripts/audit_repository.py`, and `git diff --check`. Regression baseline from S1 report: **929 passed, 8 skipped**. If parity differs, report exact failures and stop; do not 'fix' business tests by weakening expectations.

## 4. Git, packaging, deliverables

- Commit **only** S2.1 files/docs/tests on a dedicated local branch.
- Rebuild release ZIP from *verified current S2.1 HEAD* with `scripts/rebuild_zip.py`. Verify that ZIP includes new `sales_engine/llm_gateway/*`, dedicated tests, and this specification/report, and that runtime data/secrets `.env`, SQLite DBs, tokens, `.git`, cache, and model weights do not appear.
- Provide: files added/changed, source SHA, focused/full pytest results, ZIP count, denylist count, any deferred risks, and rollback steps.
- If reference golden dataset has not been copied into repo, **do not invent it** and do not pull in user data; synthetic fixtures are reserved for subsequent benchmarking S3. Current S2.1 tests use inline fake fixtures only.

## 5. Hard stop and next stage

**Stop immediately after S2.1.** Do not implement S2.2 OpenAI adapter, S2.3 routing, S2.4 data minimization, S2.5 validation, S2.6 ledger, S2.7 six-site migration, S2.8 Gemini/Claude/Mistral, S2.9 health/launcher, or S2.10 RAG retirement. Do not ask for actual API keys. No `git push`, `git merge`, emails, web scraping, live provider requests, or background tasks.

**S2.1 acceptance means framework readiness ONLY, not that external LLM processing is operational yet.**
