# SalesAI — S2.2 Primary External LLM Provider Adapter

**Status:** Implementation specification — NOT yet implemented
**Target:** Windows localhost SalesAI + local SQLite + external HTTPS inference only
**Reference baseline:** Accepted S2.1 on branch `feat/salesai-s2-1-llm-gateway-foundation`, commit `99d8bd5` (verify live Git HEAD before changing anything)
**Sources of truth:** `docs/architecture/S1-S2-MIGRATION-BACKLOG.md`, `docs/architecture/S1-LLM-CALL-MAP.json`, `sales_engine/llm_gateway/` and accepted S2.1 tests.

## 0. Objective and strict stage boundary

Implement ONE production-grade, lightweight, **OpenAI API-compatible HTTPS REST adapter** with both sync and async entrypoints, using `httpx` and the existing S2.1 gateway contracts. No commercial calls in S2.2 tests. No API key purchase needed. No new local model. Keep the gateway disabled by default.

This is a *transport adapter* milestone, not a production cutover. The six active SalesAI LLM call sites, legacy `BIMLLMClient`, Ollama-dependent transitional modules, historical benchmarks, Phase 9/11 authorities, outreach and database must remain unchanged. Do not implement tier router, automatic escalation, PII minimization, schema repair retry, persistent budget ledger, secondary providers or launcher rewrites here.

## 1. Git and environment

1. Confirm branch, HEAD, clean tree and existing S2.1 files. Create `feat/salesai-s2-2-openai-https-adapter` from the verified S2.1 commit, without switching to `main` or auto-merging/pushing.
2. Existing lightweight Python dependencies only. Prefer existing `httpx`; if missing from manifest, add one bounded compatible dependency and document why. Do not add official heavyweight SDKs or install Ollama/ML wheels.
3. Python must be compatible with current development runtime; avoid syntax that unnecessarily blocks Python 3.10.
4. No unrequested changes to `.env`, SQLite, historic test reports, real recipients, client data or production configs.

## 2. Adapter implementation

Suggested package:

```
sales_engine/llm_gateway/providers/__init__.py
sales_engine/llm_gateway/providers/openai_compatible.py
# or openai_provider.py to match S1 backlog; choose one and document

tests/test_s2_2_openai_https_adapter.py
docs/architecture/S2.2-OPENAI-ADAPTER-REPORT.md
```

- Implement `BaseLLMProvider` contract, `generate()` and `generate_async()` backed by native `httpx.Client` / `httpx.AsyncClient` or injected transports; no `asyncio.run()` within a running event loop.
- Start with the documented OpenAI **Chat Completions** route `POST https://api.openai.com/v1/chat/completions`, as the minimal compatible text adapter. Do not assume its parameters are accepted by every model or every vendor. Newer provider-native `Responses` support may be added as a separate explicit adapter/transport later; do not silently convert response formats.
- Standard request mapping: validated `LLMRequest.model` (or explicit permitted `GatewayConfig.default_model` when the caller does not specify it), system/user messages, bounded completion token budget, selected content mode. Never replace unspecified models with hard-coded or invented model names.
- Provider-specific parameters such as `temperature`, `max_completion_tokens`, `response_format`, and supported schema mode **must be conditional on explicit model capability configuration**; do not infer support from model-name patterns. Unsupported combinations fail closed or omit only genuinely optional parameters with a documented rationale. No hard-coded fictitious SKU names.
- JSON mode can be requested when the model explicitly supports it. A `response_schema` must not be claimed enforced merely because JSON mode was used; if strict JSON Schema is not supported, clearly mark it unsupported in S2.2. Full local semantic/schema validation is S2.5.
- Parse response safely: content text, finish reason, upstream request ID where available, usage `prompt_tokens` and `completion_tokens` ONLY when actually supplied, measured wall time; preserve missing values as `None`. Cost remains `None` until a verified pricing registry/cost policy exists. Do not conflate output parse success with semantic grounding.
- Handle empty or missing choices, refusal, tool-call-only output, truncated/incomplete results, invalid JSON response body, oversized responses and request cancellation safely. These are errors or explicitly incomplete responses, NEVER fabricated successful completions.

## 3. Security requirements (non-negotiable before real API usage)

1. **Default disabled**. Loading/importing/constructing the provider does not send a request. The adapter must never pull models or start Ollama.
2. API key is acquired at execution/configuration time from a dedicated environment variable (`OPENAI_API_KEY`) or injected secret provider; never baked into source, test fixture, export ZIP, default config, CLI argument or debug log. If missing, raise `LLMNotConfiguredError`/`LLMAuthenticationError` before attempting a network request.
3. The production OpenAI endpoint is pinned to `https://api.openai.com/v1` unless a **separately approved allowlisted HTTPS hostname** is explicitly configured. Never accept arbitrary request-provided URLs. Reject `http:`, local/private/loopback/link-local addresses, userinfo URLs, and unsafe redirects. Keep redirects disabled; ensure user-controlled prompts cannot influence endpoint or headers. Future OpenAI-compatible vendors must be separate explicitly configured provider IDs/allowlists.
4. Set bounded connect/read/write/pool timeouts and output/request byte ceilings; use TLS certificate verification. Do not enable debug HTTP body tracing. Treat provider 4xx/5xx response bodies as untrusted and do not include them verbatim in exception strings.
5. **Fix S2.1 observed logging exposure:** `repr(LLMRequest)` currently redacts the prompt, but `str(LLMRequest)` and default Pydantic dumps may expose it. Introduce a dedicated safe audit/log representation and test that `str(request)`, `repr(request)` and ALL actual logs do not reveal prompts or secrets. `model_dump` may contain raw prompt ONLY for intentional provider payload building; never log that dump. Apply corresponding precautions to `LLMResponse.content` and error messages. If changing `__str__`, preserve intentional serialization needed by provider code.
6. S2.1 `LLMRequest.metadata` blocks suspicious keys but arbitrary nested values remain possible; do not serialize or log unvetted metadata, and do not forward it to vendor. Review/strengthen allowlist without breaking agreed contracts.
7. This step has **no automatic retries, redirects, provider failover or price-tier escalation**. Never retry billable ambiguous timeouts without a defined idempotency and budget policy. All downstream side effects remain disabled in tests.

## 4. Error mapping and observability

Map HTTP/API failures deterministically without leaking payloads:

- Missing key / disabled gateway → `LLMNotConfiguredError`.
- 401/403 → `LLMAuthenticationError` (distinguish permissions if warranted).
- 429 with insufficient quota / billing exhausted → `LLMQuotaExceededError`; ordinary throttling → `LLMRateLimitError`; classify by safe provider error codes, not arbitrary message substring alone.
- Network or timeout → `LLMProviderUnavailableError` / `LLMTimeoutError`.
- 5xx → `LLMProviderUnavailableError`.
- Malformed/empty structured payload or missing usable content → standardized safe exception (existing `LLMInvalidSchemaError` where applicable).

Provide sanitized log events with `request_id`, `task_id`, provider, model, response status, duration, token counts if measured; do not log prompt, email text, auth headers, personally identifying details or raw provider response bodies.

## 5. Tests: mocked transport only

Use `httpx.MockTransport` or equivalent deterministic injection. Include at least 20 S2.2-specific tests covering:

1. Import/construct has zero network requests and no heavy ML imports.
2. Gateway disabled, missing provider/model or missing API key fails closed *before transport*.
3. Sync/async payloads and results agree for a valid mocked completion.
4. Correct system/user roles and bounded token parameters.
5. Model capability mapping: supported/unsupported temperature, JSON mode, token limits; no invented default model.
6. Valid structured response and raw text response behavior.
7. Missing usage means `None`; real usage stays exact; cost `None` if no pricing source.
8. Measured latency comes from clock, never synthetic constant.
9. 401, 403, rate-limit 429, quota 429, timeout, connection failure, 5xx.
10. Empty choices, invalid JSON response, refusal, truncated output and overlong response.
11. Reject unsafe/non-HTTPS endpoints, redirects, loopback/metadata/private network destinations.
12. Secret/prompt exposure test across `str`, `repr`, logs, errors, and test reports; check request/response content is not accidentally logged.
13. No retries/fallback/escalation, no email send, no inbox sync, no discovery, no SQLite mutation.
14. Existing 24 S2.1 gateway tests remain green.

Run focused tests, relevant Phase 9/11 safeguards, full `pytest tests/ -q` using the actual repo interpreter, `python scripts/audit_repository.py`, and `git diff --check`. Accepted S2.1 regression baseline is **953 passed / 8 skipped**. Do not weaken tests to pass.

## 6. Deliverables and hard stop

- Commit only S2.2 adapter, limited gateway contract/security fixes as needed, tests, and documents on S2.2 local branch.
- Rebuild `developer_source` ZIP after commit. Verify exact nine S2.1 files plus all S2.2 files, correct file count, zero denylist violations, no `.env`/API key/SQLite/runtime artifacts.
- Provide technical report: exact paths, tests, error map, request/response mapping, credentials handling, supported protocol/models (without unverified SKUs), SHA, ZIP count, limitations and rollback.
- **STOP after S2.2.** No live API calls, no purchased credentials, no model comparison yet, no S2.3+ implementation, no GitHub push/merge, and no changes to six production call sites.

## 7. Later stages (out of scope)

S2.3 tier routing (operator-authorized), S2.4 minimum-data/PII treatment, S2.5 local schema validation + one repair, S2.6 persistent budgets/circuit breaker, S2.7 migrate six LLM sites, S2.8 native Gemini/Claude/Mistral adapters, S2.9 localhost and health cleanup, S2.10 optional legacy decoupling, S3 benchmark 10 synthetic + real reference dataset, 10 accepted companies/active day pilot. Legal contact-permission gate and Phase 9 sending authority remain mandatory.
