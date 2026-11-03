# SalesAI S2.3 v2 — Task-first Tier-1-First Sequential Model Evaluation & Routing

**Status:** Replacement implementation specification — not yet executed  
**Supersedes:** `SALESAI-S2.3-TASK-ROUTING-FOUR-TIERS.md` (the original S2.3 was NOT executed).  
**Required baseline:** S2.2 `feat/salesai-s2-2-openai-https-adapter`, expected commit `66acc92`; verify actual repository before proceeding.  
**Target architecture:** Lightweight SalesAI running on the developer Windows laptop; local FastAPI UI, local SQLite and operator review; remote commercial LLM APIs only after later explicit enablement; approximately 10 qualified prospects per active day / 300 per 30-day month.  
**Scope of S2.3 v2:** deterministic, purely local catalog/router/evaluation-planning foundation with fake test results — **zero live API calls, zero costs, zero automatic provider fallbacks in production**.

## 1. Exact product decision

**Every one of the six active LLM tasks STARTS at Tier 1 (Budget).** No task is assigned a higher tier as its default. Within each tier there are **up to four approved candidate model slots**, typically from different providers, and different tasks may end up using different candidates. No need to test all 16 models for every task: test candidates **one at a time**, stopping as soon as an evaluated, safe candidate meets the acceptance standard for that task.

If candidate #1 fails an evaluation for Task A, propose candidate #2 **within Tier 1 for Task A only**, then #3, then #4. Do not change the model for Task B if Task B passed. If all eligible Tier 1 candidates fail, mark Task A `TIER_EXHAUSTED` and propose Tier 2 candidate #1. Apply the same process across Tier 2, 3 and 4; Tier 4 exhaustion produces `UNRESOLVED_NEEDS_HUMAN_DECISION`, not a fabricated winner. **Tier elevation requires explicit operator approval and a budget limit during later real benchmarking.**

Two distinct modes MUST NOT be conflated:

- **Offline/staging model-selection evaluation:** baseline S2.3 implements a deterministic state machine that consumes **supplied mocked scorecards** and suggests/records next candidates; S3+ will run real APIs on synthetic/sanitized Goldens with explicit opt-in and cost ceilings. It can generate a next-step recommendation without issuing a paid call.
- **Normal production inference:** use the **operator-approved model per task**. A weak-looking or malformed response NEVER silently invokes four alternative paid models. Validators can fail closed, mark `needs_review`, and request re-benchmarking/reconfiguration. Future bounded retries or fallback require a separate approved policy and budget gate, and can never override legal/sending/suppression safeguards.

**No claim that a model is good/bad until actual S3 evaluations.** A valid JSON shape is not sufficient evidence of correct company qualification or absence of hallucinations.

## 2. Canonical task catalog: all start at Tier 1

Read the actual `docs/architecture/S1-LLM-CALL-MAP.json` rather than inventing call sites. Support exactly these six canonical task families, and treat website A/B separately for evaluation (one family, two subtasks):

| Task key | Existing call site | Initial tier | Evaluation focus |
| --- | --- | --- | --- |
| `website_extraction` (`pass_a`, `pass_b`) | `sales_engine/analysis/website_analyzer.py` | Tier 1 | factual extraction, source grounding, opportunity signals |
| `job_analysis` | `sales_engine/analysis/job_analyzer.py` | Tier 1 | hiring signals, real BIM/MEP skills vs irrelevant terms |
| `discovery_query_generation` | `sales_engine/discovery/query_generator.py` | Tier 1 | useful nonduplicated search queries, correct country/sector |
| `inbox_classification` | `sales_engine/inbox/classifier.py` | Tier 1 | intent accuracy, referral vs unsubscribe vs not-now |
| `reply_suggestion` | `sales_engine/inbox/suggestion.py` | Tier 1 | claims, capacity/deadline promises, human review |
| `outreach_draft` | `sales_engine/outreach/email_generator.py` | Tier 1 | evidence-based B2B writing, no invented sender or service claims |

Task and subtask IDs must be strict aliases to the accepted S1 map; unknown IDs fail closed. An individual task's approval/selection and progress state must not leak into other tasks.

## 3. Candidate catalog: four tiers, four ordered candidates per tier

Keep a version-controlled safe sample configuration under `config/` with **16 candidate slots** (4 x 4), each having at least:

`candidate_id`, `tier`, `priority` (1..4), `provider_id`, `exact_model_id`, `enabled`, `verified_capabilities` (structured output support, required output type, context window if verified), `provider_adapter_kind`, `allowed_tasks`, and optional versioned/dated **verified** pricing metadata (`currency`, `input_per_1m`, `output_per_1m`, `effective_date`, `source_url`). `unknown` prices must remain `null`, never `0`; do not invent model IDs, costs, performance or capability support. Leave placeholders disabled until verified.

Provider diversity is a goal, not an excuse to forge compatibility: if the native Anthropic/Gemini/Mistral adapters have not been implemented, these provider candidates can be **catalogued as disabled/unavailable**, not invoked through the OpenAI adapter unless their API actually supports the configured contract and endpoint allowlist. Candidate priorities within a tier are **operator-configurable per task**; the same Tier 1 model may pass website extraction but fail reply intent. Tasks may select different vendors at the same tier; a tier is a policy grouping, not a fixed universal model.

Do not bind a single globally selected model or globally increment the tier whenever one task fails. No auto-downloads or local LLM fallback.

## 4. Models and data contracts to implement in S2.3

Add/extend (without creating parallel providers or removing existing S2.1/S2.2 contracts):

- `sales_engine/llm_gateway/model_catalog.py`: validated immutable `ModelCandidate` and versioned `ModelCatalog`, deterministic per-task ordered candidate lookup, duplicate/disabled/unavailable rejection, optional verified prices, no HTTP.
- `sales_engine/llm_gateway/router.py`: immutable `TaskRouteDecision` and `TaskRouter`. `resolve_approved(task)` returns the **operator-approved** active provider/model only, if configured and enabled; `dry_run_candidates(task, tier)` lists eligible candidates without calling providers. All six tasks initially request Tier 1. Unknown/unsupported models fail closed.
- `sales_engine/llm_gateway/evaluation_policy.py` (or similarly named module): `TaskEvaluationState`, `EvaluationResult` / `CandidateScorecard`, `EvaluationPlanner` pure state transitions. Maintain `task_id`, `candidate_id`, tier, fixture/version, provenance, passed/failed criteria, reasoning codes, and approval status. Given **trusted evaluation results** for candidate N, return `ACCEPT_CANDIDATE`, `PROPOSE_NEXT_IN_SAME_TIER`, `TIER_EXHAUSTED_REQUIRES_APPROVAL`, or `UNRESOLVED_NEEDS_HUMAN_DECISION`. No network side effects, no paid-model probing, no writes to production data.
- A version-controlled `config/...example.json` or `.yaml` showing all six task policies `starting_tier=tier_1_budget`, 16 model slots, task-specific priority override, fail-closed default and staged approval. Never enable a real API model automatically.
- `tests/test_s2_3_sequential_task_routing.py`: **at least 25** deterministic unit tests across independent tasks, tier-1 default, candidate rotation, early-stop, exhausted tier, operator approval to promote, all-tier exhaustion, disabled/missing/incompatible model, provider diversity, capability fail-close, unknown pricing, prompt-injection resistant routing config, no API calls, approved-production routing, sync/async interface compatibility.
- `docs/architecture/S2.3-V2-TASK-FIRST-ROUTING-REPORT.md`: decisions, demonstration of state transitions, behavior, precise test results, ZIP, Git, rollback and explicit limitations.

If a simple in-memory fixture or ephemeral test-only persistence is needed for evaluation state, it MUST not modify operational SQLite, company outcomes, suppression, sending, or current SalesAI history. Durable production benchmark registry is deferred until the benchmarking phase unless already available and compatible.

## 5. What counts as an evaluation PASS in future S3?

Plan acceptance **separately by task**, using sanitized/synthetic Golden inputs and independent expected answers. Start with existing `SalesAI_Golden_10_Synthetic_v1.json` and `SalesAI_Model_Inputs_10.jsonl` **only if actually present in the project**; if absent, declare the dataset dependency and do not invent it. There are 5 clean and 5 misleading samples in the planned initial set. Ten global cases are an engineering smoke set, **not** sufficient to claim statistically validated quality for all six task families; expand relevant cases in S3 as necessary.

Scorecards should carry explicit denominators and differentiate: strict/lenient JSON; Pydantic schema and allowed enums; reference-fact match; citation/evidence grounding; unsupported claims; critical category mistakes (especially unsubscribe/referral/not-now); instruction-injection failures; latency and actual usage/cost when later measured. Email draft evaluation uses grounded fact checks and human rubric, **not string similarity**. All safety-critical failures block approval even if average quality >60%. Define per-task numeric thresholds only after reviewing realistic examples; S2.3 must not fake pass rates, judge quality by model size, or declare a model 'best'. `needs_human_review` is not a semantic PASS.

Model evaluation in S3 should stop on the first acceptable Tier 1 candidate **for each task**. For cost comparison, optional additional tests may be explicitly requested; otherwise do not test unnecessary candidates. Should all Tier 1 candidates fail, propose Tier 2 (and so forth) with explicit operator budget approval. Later benchmarking may run only within the approved scope, with per-model/per-task maximum calls, dollars, retries and timeouts.

## 6. Critical security and business restrictions

1. External gateway **disabled by default**, no live API calls or paid tokens in S2.3.
2. No silent expensive fallback, no automatic escalation into a higher tier, and no automatic production failover across vendors.
3. Candidate IDs or tiers cannot be selected by scraped pages, incoming emails, user-generated document content, prompt injection or unauthenticated HTTP requests. Only trusted config and authenticated operator approval can do so.
4. Do not conflate untrusted model self-reported confidence with independently validated quality.
5. Retain local DB, localhost-only access, operator approval and Phase 9 sending/suppression safeguards; no email sending, inbox sync, scraping or PII transfers.
6. No real credentials, model IDs or pricing invented or shipped as enabled defaults; do not change operational company data or `.env`.
7. No Ollama startup/download, Cloudflare Quick Tunnel enablement, GPU dependency or broad dependency installations.
8. `LLMRequest` and `LLMResponse` safe repr/str/logging from S2.2 must remain safe.
9. Correct the known test compatibility defect: `tests/test_s2_2_openai_https_adapter.py` annotates `Dict` without import on Python 3.13 and lower. Add `from typing import Dict` or modernize annotation safely; add a minimal Python 3.10+ compile/collection regression proof where practical.
10. Do not modify/re-wire the six live LLM call sites in S2.3, and do not advance to S2.4 or S3.

## 7. Mandatory dry-run examples (pure logic, no model execution)

Provide deterministic mocked demonstrations for at least:

**Example A:** `website_extraction` Tier1 #1 fails grounding -> Tier1 #2 passes all thresholds -> approval candidate #2 for `website_extraction` only; `inbox_classification` remains unselected. Stop testing further candidates for that task.

**Example B:** `inbox_classification` Tier1 #1..#4 fail critical intent classification -> plan `TIER_EXHAUSTED_REQUIRES_APPROVAL`; before operator approval, no Tier2 call or expense. Following *simulated* operator approval, Tier2 #1 can be suggested; no real API call.

**Example C:** `outreach_draft` Tier1 #1 provides valid JSON but invents team size -> safety FAIL, not PASS. Offer next Tier1 candidate; never send email.

**Example D:** `job_analysis` Tier1 #1 passes -> no need to test the remaining 15 candidates. Another task can independently choose a different Tier1 vendor/model.

**Example E:** all candidates unavailable / unconfigured -> `UNRESOLVED` or typed fail-closed error; never substitute Ollama or pretend it evaluated the models.

## 8. Validation, git and packaging

1. Verify actual baseline `66acc92`, branch and clean worktree; create dedicated child branch e.g. `feat/salesai-s2-3-task-first-routing-v2`.
2. Read accepted S1 call map, accepted S2.1/S2.2 contracts/adapters and existing tests. Implement without breaking imports or existing API contract.
3. Run focused S2.1/S2.2/S2.3 tests, appropriate security/sending/CRM tests, full `pytest tests/ -q`, and `python scripts/audit_repository.py`. Report actual results; S2.2 reference is **980 passed, 8 skipped** on developer Python 3.14; do not fabricate counts.
4. Commit **locally only**, no push/merge. Rebuild `SalesAI_export.zip` with the `developer_source` profile; verify S2.3 files and all earlier gateway files are included; 0 security denylist violations; exclude `.env`, API credentials, real customer Excel, live SQLite, `.venv` and model weights.
5. Supply technical acceptance report, exact branch/commit, tests, ZIP count and rollback. **STOP after S2.3 v2**.

## 9. Acceptance summary

S2.3 v2 is complete only when **all six tasks default to Tier 1**, **each task independently walks at most four candidate positions at its current tier**, **passes stop further evaluation**, **tier upgrades require approval**, **unconfigured providers fail closed**, **production uses only approved assignments**, **mock evaluation never performs API calls**, and all existing SalesAI safeguards and regression tests remain intact. Real provider comparisons, real token/cost measurements and model approval based on evidence are **future S3 work**.
