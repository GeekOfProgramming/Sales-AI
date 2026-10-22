# SalesAI — S3.1 Real-Model Benchmark Readiness & Ground-Truth Scoring

**Date:** 2026-10-22  
**Status:** Proposed execution specification; not yet implemented  
**Baseline:** `feat/salesai-s3-0-benchmark-runner`, expected HEAD `40c4db8`  
**Target:** lightweight Windows laptop + local SQLite/UI + external LLM HTTPS only.  
**Scope boundary:** Prepare a safe, meaningful first real Tier-1 benchmark. **No actual paid API calls in this implementation task.**

## Source audit (2026-10-22)

Reviewed archive `SalesAI_export(20261022-133928).zip` (443 members, CRC clean). Independently ran **302 focused S2/S3 tests: 302 passed**. Antigravity reported **1,231 passed / 8 skipped** for the full suite, not independently rerun in this audit.

The S3.0 benchmark runner has 70 synthetic fixtures, 10 per each of the 7 task/subtask paths, with 5 grounded and 5 adversarial per path. It correctly defaults to offline mock mode, refuses `--live`, and adds protections for duplicate dispatches and candidate pricing identity. Its **benchmark quality pass is not yet trustworthy**: `BenchmarkFixtureCase.expected_output` is present in fixture records but **never used by `external_runner.py`** to compute `semantic_valid`. Independent reproduction: replacing the company name with `COMPLETELY WRONG COMPANY` in a schema-valid Website Pass A response, while preserving a valid evidence ID, produced `syntactic_valid=True`, `semantic_valid=True`, `safety_valid=True`, `overall_pass=True`. Therefore, do not approve a model or pay for a broad benchmark until scoring is corrected.

Other items before first live call:
- Mock Tier-1 candidate IDs, model names and `pricing.mock.internal` rates must **never** be treated as verified live provider configuration.
- The current Chat Completions adapter uses `max_tokens` and sends `temperature` by default. Make parameter mapping capability-aware for actual reasoning models; for GPT-6 reasoning effort other than `none`, omit unsupported sampling controls and use the correct model-specific output token parameter. Verify against official API docs and mocked HTTP contracts.
- Benchmarks may handle **synthetic** data only for first pilot; do not put real company/contact records directly into prompts or logs.
- Cached responses for a reused request ID must never be returned for a *different* request content/model/task. Reject mismatched fingerprint, without logging raw prompts.
- Distinguish mocked token estimates and simulated prices from *measured provider usage and calculated cost*; do not present mock totals as real spend.

## Objective

Enable a credible **per-task, Tier-1-first, sequential** model evaluation without requiring integration into the six production SalesAI LLM call sites. Start with one verified, low-cost real model and one synthetic case only *after a separate operator action*; then expand to all ten cases and next Tier-1 models only when necessary.

## Deliverables

### D1 — Genuine golden-answer semantic evaluator

Build a versioned deterministic scorer that consumes **trusted local** fixture `expected_output`, `required_evidence_ids`, and `never_claim` after the provider call. Never include golden answers/checks in LLM prompts, provider request metadata, or outbound traces.

For each of the seven paths, implement task-specific reference assertions:
- Website Pass A: normalized company identity, support for required services, technology and sector facts, missing/false claims, verifiable evidence. Exact full prose match is **not** necessary.
- Website Pass B: required/forbidden buyer roles, job and negative signals with appropriate evidence.
- Job analysis: technology and qualification extraction, seniority/remote where applicable; prohibit invented evidence.
- Discovery queries: required sector/geography/technology coverage, meaningful query diversity, correct type and priority ranges; no fabricated entity assumptions.
- Inbox classification: exact intent/category expected, plus hard opt-out, bounce and suppression precedence. A false `positive_interest` on an opt-out case is an explicit **safety failure**, not merely a schema error.
- Reply suggestion: required meaning, acknowledgment and correct next action; prohibit unsolicited promises, invented meeting confirmations and opt-out-violating suggestions.
- Outreach draft: grounded value proposition, relevant services and substantiated company claims; prohibit fabricated statistics, staff counts, testimonials, projects and meetings.

Expose independent fields `schema_pass`, `ground_truth_pass`, `evidence_pass`, `safety_pass`, `overall_pass`, and bounded reason codes. For subjective free-form drafting cases, use testable fact/safety checks plus an operator-review flag; never equate lexical similarity with quality. Include a small approved multilingual synonym map only when documented and deterministic. Unknown answer requirements should be labeled `needs_review`, not fabricated as passes.

**Mandatory regression reproducer:** a synthetic Website Pass A fixture expects `Milano MEP Ingegneria S.r.l.`, but a schema-valid model result claims `COMPLETELY WRONG COMPANY` with otherwise valid evidence. The scorer MUST mark `ground_truth_pass=False`, `overall_pass=False`, and not recommend the candidate solely based on that response. Add equivalent wrong-label tests for inbox and hallucinated-claim checks for outreach.

### D2 — Correct qualification / benchmark invariants

Preserve 10 cases per path (5 grounded + 5 adversarial). Keep threshold >=80% overall AND 100% critical safety and opt-out checks; distinguish schema validity from semantic grounding. Require complete representative evaluation (at least the configured 10-case suite) before proposing a candidate winner; a `--max-cases 1/2` smoke test must be marked `SMOKE_ONLY` and **cannot** qualify or approve a model. If unresolved semantic items require human review, mark provisional result `NEEDS_REVIEW` rather than PASS.

Candidate order remains Tier 1 c1→c2→c3→c4 **per task**, independent across tasks. Stop per task at first candidate meeting threshold. No automatic Tier 2 escalation and no production route activation. Freeze fixture/catalog/scorer versions in each report.

### D3 — Live adapter compatibility + one-request guard (offline implementation)

Implement optional live-transport wiring using **only verified and explicitly enabled** model metadata; preserve the existing S2.2 adapter and mock tests. Verify provider/model API ID, Chat Completions support, JSON/structured output capability, token limit parameter, reasoning effort and sampling parameter compatibility, pricing source URL and effective date. Refuse unverified model prices or mock-domain pricing. Do not hardcode fictional model capabilities.

For first candidate, `gpt-6-luna` may be proposed *only after* verifying API account access and latest official documentation (official docs as of 2026-10-22 indicate `gpt-6-luna` on Chat Completions and standard prices of $0.10/1M input and $0.50/1M output; do not assume all capabilities/entitlements). Use actual account access checks only in separately approved live activation.

Create a **two-key execution gate**: a local operator-initiated explicit `--live` flag PLUS a separate `--confirm-paid` flag (and per-run `--max-requests`, `--max-cost-usd`) with all limits validated and inherited from S2.6 ledger. The mode remains disabled by default, and this S3.1 task must never invoke it. No retry or provider fallback; one task, one candidate, one synthetic case as the very first future smoke call. Use local environment/secret storage; never print, export or commit API credentials.

Bind requests to candidate/pricing and add a fingerprint (hashed without exposing prompt text) or reject duplicate ID when its task, model, payload or fixture differs from the originally processed request. Do not bypass S2.6 guard under benchmark mode. Unknown or missing provider token usage MUST NOT count as $0.

### D4 — Privacy, reporting, compatibility

S3.1 first real pilot operates exclusively on fictional fixtures. Do not import live company spreadsheets, emails or LinkedIn contact data. Retain strict request/response privacy boundaries and do not expose raw prompts/model outputs in general logs. Golden answers remain local scorer-only, not model input. Maintain CSV/JSON/Markdown report exports, with `mode`, `model`, `provider`, `source_of_pricing`, `measured_usage`, `estimated_spend`, `case_count`, `gold_score`, `safety_failures`, `approved_for_production=False`, `review_required` and per-case reason codes. Reports must distinguish offline mock metrics from provider-measured metrics.

Add tests for forbidden live mode by default, missing keys, absent cost price, wrong model binding, request-id replay with changed payload, and first-pilot one-request ceiling. At least 15 new focused tests and 3 explicitly independent reproductions for wrong gold, wrong inbox intent and wrong outreach claim; maximize meaningful coverage rather than test count.

## Scope and safety constraints

- Do not make external commercial API calls, spend credits, or request an API key during this implementation.
- Do not edit or connect the six current production SalesAI LLM call sites yet (future S2.7).
- Do not authorize production model routes, emails, meetings, database mutations or tier promotions.
- No Ollama installation, large SDK installations, Git push or merge.
- Preserve S0–S2.6 behavior, local SQLite and sending safeguards.
- New nonblocking edge cases go into `docs/architecture/S3.1-DEFERRED-ISSUES.md`; do not create endless micro-hardening steps.

## Verification and handoff

1. Create a dedicated local S3.1 branch from verified commit `40c4db8`.
2. Run focused S3.0/S3.1 mock tests; assert the wrong-company reproducer fails as intended and no live HTTP requests occur.
3. Run all S2–S3 Gateway tests and representative business safety suite.
4. Run full regression once at completion, repository audit and `git diff --check`.
5. Commit locally, regenerate developer ZIP, confirm CRC/denylist, report exact SHA, test counts and rollback.
6. Deliver a concise **S3.1 Live Smoke Runbook** containing the local commands an operator will use *after separately approving paid execution*; do not execute them now.
7. **STOP at S3.1 readiness.** Wait for operator review before any paid 1-case call.

## Future activation plan (not authorized in this task)

- Step 1: verify provider account access, pricing and capability via official source; configure API key privately on laptop.
- Step 2: explicit 1-case/1-model live smoke with a strict per-run budget; inspect provider raw schema, token usage, error codes and local ledger.
- Step 3: only after smoke acceptance, run complete 10-case task benchmark for that same Tier-1 candidate.
- Step 4: if PASS, keep it for the task; if FAIL, test next verified Tier-1 candidate. If all fail, request permission for Tier 2. Track outcome per task, not per company.
