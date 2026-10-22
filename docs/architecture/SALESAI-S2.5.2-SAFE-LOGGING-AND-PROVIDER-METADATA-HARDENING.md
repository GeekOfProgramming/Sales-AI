# SalesAI S2.5.2 — Safe Logging & Untrusted Provider Metadata Hardening

**Status:** corrective work order; do not start S2.6 until acceptance.

**Source baseline:** `fix/salesai-s2-5-1-output-validation-integrity`, reported HEAD `9041c0f`. Verify locally; do not trust the reported commit without checking Git.

**Operating target:** laptop-local SalesAI; local SQLite and CRM; all future LLM inference through external HTTPS APIs; six operational LLM call sites remain untouched; no paid network calls.

## 1. Why this correction exists

An independent audit of `SalesAI_export(20261022-104627).zip` found that `LLMResponse.safe_log_dict()` directly includes the untrusted `provider_request_id` from an upstream response header/body. `LLMOutputValidator.validate()` copies it into `OutputValidationResult.metrics`, and `OutputValidationResult.safe_log_dict()` copies all `metrics` unfiltered. An injected value such as `private_person@example.invalid` therefore appears in both supposedly safe log dictionaries. This was reproduced using an otherwise schema-valid fictional inbox classification.

This is **not evidence of a real data breach**. The commercial API has not been enabled. The eight S2.5.1 target corrections passed independent checks. The new defect must be corrected before external API activation.

## 2. Scope and mandatory invariants

**B1 — Untrusted upstream correlation identifier.** Treat `x-request-id`, provider response `id`, `provider_request_id`, and provider `raw_metadata` as untrusted. Preserve unredacted raw values only in restricted in-memory transport objects where needed; never emit them through any `safe_log_dict()`, `__str__`, `__repr__`, exception, structured-log payload, or audit record. In safe logs, prefer `has_provider_request_id: bool` or `provider_request_id: null`. Do not merely mask the `@` character.

**B2 — Closed metrics allowlist.** `OutputValidationResult.safe_log_dict()` must build an explicit, shallow allowlist of known scalar measured fields (for example input/output token counts, latency, and cost, only when measured). It must not copy arbitrary `metrics` dictionary keys or nested payloads into logs. Handle `None` correctly; never turn an unknown cost into zero. Pydantic serialization, debug output, and safe log paths must remain distinct.

**B3 — Uniform safe logging.** Inspect and harden `LLMRequest`, `LLMResponse`, `OutputValidationResult`, `RepairProposal`, `OpenAIProvider`, and the relevant Gateway error classes for untrusted strings that may appear in log output (request IDs, task IDs, provider/model labels, headers, metadata, diagnostics). Use common bounded allowlisted formatting or redact the field outright. Preserve the existing public request/response contracts and preexisting tests. No raw prompts, response text, email addresses, phone numbers, API keys, tokens, or model-supplied JSON keys may appear in any method documented as log-safe. Do not expose raw exceptions from network libraries.

**B4 — Identity preservation without disclosure.** Continue exact internal request/response identity verification from S2.5.1 and exact operator-approved model routing from S2.3.1. Do not change provider, task, or model values simply to make logging safe. Sanitization occurs at the log/export boundary; internally retain exact values necessary for integrity checks. Never fabricate an operator or provider ID.

**B5 — Adversarial tests.** Add at least **15 new, fully offline tests** (in addition to the 248 existing Gateway tests). Cover: email in `x-request-id`; bearer token in response `id`; `sk-` secret; Windows path; multiline and control-character header data; nested `metrics` containing `private_notes`; provider `raw_metadata` with cookies; malicious model/metadata labels; empty and legitimate opaque IDs; identical results from sync/async validation; secret-free `repr`, `str`, and `safe_log_dict` for response and result; normal request correlation and usage accounting preserved; no `httpx` or provider network call. Assert the sensitive values do not appear ANYWHERE in serialized logs (including `json.dumps(safe_log_dict())`).

**B6 — Explicit residual limitations.** Document that automatic sanitizers do not guarantee detection of all PII. Record full authenticated operator session validation as a blocking requirement for S2.7 and real API activation. Preserve suppressions and human approval for email sending.

## 3. Prohibited work

- Do not change the six operational LLM call sites; do not modify actual company data, CRM, SQLite operational database, `.env`, or credentials.
- No real API requests, scraping, outreach, paid retries, automatic tier escalation, or model installation.
- Do not alter Tier-1-first sequential candidate evaluation, the four-tier model catalog, schema semantics, or evidence authority.
- Do not begin S2.6, connect the live Gateway, or add secondary provider adapters.
- No Git push or merge. Do not use a destructive `git reset --hard` on an operator's working branch.

## 4. Acceptance and reporting

1. Confirm baseline HEAD and clean working tree; create dedicated `fix/salesai-s2-5-2-safe-logging` branch.
2. First add regression reproductions for all proven exposures, then fix them with minimal changes.
3. Run new adversarial tests; all Gateway S2.1–S2.5.2 tests; sending/opt-out and admin gate tests; full `pytest` suite; `python scripts/audit_repository.py`; `git diff --check`.
4. Preserve or exceed full-test baseline **1177 passed, 8 skipped**; explicitly explain any count differences.
5. Rebuild `SalesAI_export.zip`, verify archive integrity, new files, packaging denylist, and absence of local database/credentials.
6. Produce `docs/architecture/S2.5.2-SAFE-LOGGING-REPORT.md` containing repro before/after, exact changes, tests, SHA/branch, remaining risks, and non-destructive rollback guidance.
7. Commit locally and stop after S2.5.2 for independent review. **Do not proceed to S2.6.**

## 5. Expected next stage, NOT part of this task

S2.6 will add a fail-closed **persistent SQLite cost/reservation ledger**, verified model pricing, fixed daily/monthly budget caps, rate limiting, concurrency limit, circuit breaker, and explicit bounded retry planning. All billed dispatches remain disabled until API integration is separately authorized. Avoid invented token costs or real vendor calls during development.
