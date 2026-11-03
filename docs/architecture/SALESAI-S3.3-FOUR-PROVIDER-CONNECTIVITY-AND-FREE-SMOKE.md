# SalesAI S3.3 — Four-Provider API Onboarding & Free-First Connectivity Smoke (MVP)

Date: 2026-10-23
Baseline: `feat/salesai-s3-2-first-live-smoke-wiring`, HEAD `131c2d0` (verify, do not assume). Based on review of `SalesAI_export(20261023-105033).zip`.

## Goal
Enable controlled external API access to four separately credentialed providers — OpenAI, Google Gemini, xAI Grok, and GroqCloud — through the existing lightweight SalesAI Gateway and S3 Benchmark Runner. Keep all actual company, contact and email data local. Start with fictional test cases. The *setup work* must make ZERO real generation/API inference calls. Deliver runnable one-case connectivity smoke commands for the operator to execute afterwards.

**Scope limitation**: This is provider onboarding and connectivity evidence, NOT a parallel 4-model quality tournament or Tier-1 winner selection. Retain task-first ordered evaluation and the first-qualified-stop rule. A connectivity smoke may independently touch different providers, but its results must never count as a ten-case qualifying task benchmark.

## Baseline specifics
- Existing `ExternalBenchmarkRunner._configure_live_candidate` hardcodes `t1_c1`, `openai`, `gpt-6-luna` and `https://api.openai.com/v1`; live `run_benchmark` refuses all other candidate IDs.
- Existing `OpenAIProvider` is an `httpx` OpenAI-compatible Chat Completions adapter with HTTPS/host allowlist; reuse safely rather than installing heavyweight SDKs.
- CLI `scripts/benchmark_external_llm.py` accepts `--live --confirm-paid --task --candidate --max-cases --max-requests --max-cost-usd --db-path --output-dir` but no provider or free-only controls.
- SQLite budget ledger, reservation/idempotency checks, request-cache fingerprinting, safe logging, strict output schema and Golden evaluator are already present. Preserve them.
- The previous actual OpenAI `t1_c1` smoke on fictional Website Pass A passed 1/1 (`SMOKE_ONLY`); not a model qualification.

## Provider identity, secure endpoints, and keys

| Provider key | Base URL for Chat Completions | Environment key | Initial eligible model | Billing mode |
|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-6-luna` (previously validated) | May be paid; no free-tier assumption |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-2.5-flash-lite` IF the account's Free Tier supports it | Free only if eligibility is confirmed |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `openai/gpt-oss-20b` IF supported in Free Plan | Free Plan, verify quota and model eligibility |
| `xai` | `https://api.x.ai/v1` | `XAI_API_KEY` | Choose exact model from account's model listing and verified rate card; do not guess | **UNKNOWN / potentially billable**; disabled for generation until explicit operator approval and funded/free grant verification |

Do not confuse xAI Grok with GroqCloud. A Groq-hosted model named `openai/gpt-oss-20b` is billed/served under **Groq** and uses `GROQ_API_KEY`, not the OpenAI key.

Use strict endpoint allowlists per provider. No redirect following, localhost or arbitrary user URL destination, HTTP downgrade, key interpolation in URLs, keys in logs, or implicit environment-key fallback between vendors. Separate provider ID from model brand. Normalize non-OpenAI provider API usage carefully, preserve exact returned model IDs and reported tokens; missing usage -> UNCERTAIN rather than free.

Official references to confirm at implementation time:
- Google OpenAI compatibility: `https://ai.google.dev/gemini-api/docs/openai`
- Google free pricing and caveats: `https://ai.google.dev/gemini-api/docs/pricing`
- Groq chat API: `https://console.groq.com/docs/api-reference`
- Groq Free Plan limits: `https://console.groq.com/docs/rate-limits`
- xAI API: `https://docs.x.ai/developers/rest-api-reference/inference/chat-completions`
- xAI Billing: `https://docs.x.ai/console/billing`

## Delivery requirements
1. Build a **provider connection catalog** that explicitly pins `{provider_id, exact_model_id, base_url, env_var, capabilities, billing_mode, verified_source, checked_date}`. No key value in config or source. Provider endpoint input comes from approved static entries, not model prompts or arbitrary CLI strings.
2. Add exactly scoped multiple-provider registration to existing Gateway and benchmark. Retain `t1_c1` OpenAI, configure Gemini and Groq into distinct Tier-1 placeholder IDs (prefer `t1_c2` Gemini, `t1_c3` Groq), leaving `t1_c4` xAI disabled until verified affordable and enabled. The four-provider/slot plan remains independent per task; do not silently relabel a pricey xAI model as Tier 1. Keep the rest of the 16-slot catalog disabled by default.
3. Add or extend CLI `--provider {openai,gemini,groq,xai}` for explicit single-provider selection, `--connectivity-smoke` for one fictional-case run with `SMOKE_ONLY`, `--free-only` as a fail-closed option, and a non-generating `--probe-models`/`probe_llm_providers.py` command for **metadata/auth validation only** (GET models or equivalent) with key-safe output. Avoid auto-calling all providers to bypass normal ordered selection. For any free-only invocation, if Free Tier availability, remaining quota or billing class cannot be established, refuse generation with `FREE_ELIGIBILITY_UNCONFIRMED`, not a paid fallback.
4. Keep the existing **two-key approval** (`--live --confirm-paid`) even on free-provider generation; this is explicit permission to transmit the synthetic prompt, not evidence of payment. `--max-requests 1`, `--max-cases 1`, `--max-cost-usd 0.01` for first probes where those CLI semantics apply. Never launch automatic retries, paid repair calls, alternative providers or Tier promotion.
5. Verify exact official model IDs and per-model request-field compatibility: `max_tokens` vs `max_completion_tokens`, role support, temperature/reasoning settings, JSON format, response text field and usage mapping. Use `httpx.MockTransport` offline to test host selection and payloads. If a provider/model lacks JSON object mode, establish an explicit supported alternative or mark that candidate incompatible; **do not loosen validation**.
6. **Pricing / free-tier accounting**: Never treat *unknown price* as `$0`. Distinguish `VERIFIED_FREE_TIER`, `VERIFIED_PAID`, and `UNKNOWN`. If the existing ledger cannot represent zero-billed free-tier usage accurately, use a conservative verified published paid-rate upper bound for risk reservation and label it `ESTIMATED_LIST_PRICE_UPPER_BOUND`, NOT actual provider charges. Add correct provider-billed/free indicators only if verified. Keep a local per-provider request count and free-quota ceiling as well as the existing USD global budget ceiling. Any 429 or quota/billing failure stops that provider; never auto-switch to a paid path.
7. xAI onboarding **metadata-only** by default (verify API key, available model, pricing/credits from console). Enabling xAI generation requires a distinct explicit operator choice AFTER verifying available promotional credit or choosing a capped paid test. Do not assert all four APIs are free because four keys were created.
8. **Privacy**: Google Gemini Free Tier may use request content for service improvement. Use only the 70 purely fictional scenarios. No SalesAI actual Excel, contact data, inbox, personal email, pyBIM proprietary information or raw previous CRM artifacts. No logs containing authorization headers, raw prompts, emails, API keys or error bodies.
9. Preserve request identity uniqueness across provider/model/task/prompt, independent budget reservations and per-provider SQLite and reports. No cached response replay across providers. Restrict CLI to one provider and one explicit task per live smoke. Fail closed if multiple providers are requested in one invocation.
10. Each provider smoke writes separate output folder and produces machine-readable results containing provider, exact model, case ID, schema pass, reference pass, evidence pass, safety pass, token usage, latency, error code, SQLite reservation status, *estimated* vs *actual* spend provenance and `SMOKE_ONLY`. A smoke test CANNOT select a production model or update approval routes. Preserve missing-golden coverage warnings for three task paths.
11. Add >=20 offline tests: missing key, swapped key/provider, wrong/unsafe endpoint, free-only with unknown eligibility, xAI cannot generate unpaid by default, Groq URL, Gemini URL, JSON mode parameter compatibility, mock HTTP 200/400/401/403/429/5xx, usage missing -> uncertain, request-id collision, free quota/429, exact cost provenance, 1-call limit, no provider fallback, separate folder, and existing OpenAI unchanged.
12. No local model installs, no real generation in developer task, no Git push/merge, no edits to six operational production call sites, no outbound emails or DB business-record writes.

## Suggested operator smoke sequence AFTER offline implementation and review
A. Confirm ONLY the names of four temporary process environment variables exist; do not print key values. Make sure credentials are in the **same PowerShell process** used for running the CLI.
B. Run non-generating provider model-list/auth probe for Google, Groq, xAI, OpenAI (each provider independently, no key/HTTP error-body logging).
C. Run Gemini `gemini-2.5-flash-lite` one-case synthetic `website_extraction:pass_a` free-only smoke, hard cap one request. If successful, run Groq `openai/gpt-oss-20b` one-case smoke independently. Do not simultaneously run them as a qualification tournament.
D. Existing OpenAI successful smoke serves as connection evidence; optional new-organization OpenAI smoke ONLY after separate billing/credit verification and operator authorization.
E. xAI: do NOT generate until billing/free credit is confirmed and user authorizes a chargeable request explicitly. An API key alone never authorizes a charge.
F. Audit metadata, per-provider ledger and smoke results. Only after all desired connections work, resume **task-first sequential qualification** in the agreed order, starting with Tier 1 candidate 1 per task; evaluate next candidate only if previous failed/unavailable. At least 10 grounded/adversarial cases and human review for uncertain quality.

## Explicit handoff
Work from clean Git HEAD based on `131c2d0`; create a dedicated local S3.3 feature branch. Run focused gateway + benchmark tests and representative regression tests; run full suite once, repository audit and ZIP integrity check. Report exact CLI command examples based on implemented flags, tests, provider models enabled/disabled, known limitations, updated ZIP and SHA256, branch/commit, and remaining paid/free eligibility gates. STOP before ANY real inference. Do not prompt the operator for keys via the agent chat.
