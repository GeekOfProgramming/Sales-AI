# SalesAI S2.3.1 — Routing Integrity & Evaluation State Hardening

**Status:** Corrective security/quality gate for S2.3 v2. **Do not begin S2.4 yet.**

## 0. Baseline and invariants

- Expected local HEAD: `fb7ca18`, branch `feat/salesai-s2-3-task-first-routing-v2`; verify actual repo state and create a separate child branch (`fix/salesai-s2-3-1-routing-integrity`). No push/merge.
- Current reference report: 1007 passed, 8 skipped, 6 warnings (Antigravity report); independent ZIP review verified 406 members, healthy ZIP, and all 78 S2.1–S2.3 focused tests passing in a separate Python environment.
- Keep SalesAI Phases 1–12, local SQLite, 6 active LLM call sites, S2.1 and S2.2 gateway, send approval/suppression safeguards, and offline default. Never send email, make paid calls, activate model IDs, install local inference, or touch production data, `.env`, provider API keys, or real company Excel.
- All six task families start at Tier 1. Four ordered slots per tier. Per-task evaluation independence. Production never auto-switches provider/tier.

## 1. Verified blockers in the delivered S2.3 v2 code

### B1 — Untrusted / mismatched scorecards can approve arbitrary candidates (critical)

File: `sales_engine/llm_gateway/evaluation_policy.py`, `EvaluationPlanner.process_scorecard`.

Observed: For a `website_extraction/pass_a` state currently evaluating its Tier 1 first candidate, a `CandidateScorecard(task_id="website_extraction", subtask_id="pass_b", candidate_id="fabricated-id", tier=ModelTier.TIER_4_FLAGSHIP, passed=True, safety_critical_passed=True, score=1.0)` is ACCEPTED and stores `approved_candidate_id="fabricated-id"`.

**Required:** Before modifying history or status, validate an active `evaluating` state; canonical task and exact subtask; candidate ID equals the **current** candidate ID; scorecard tier equals state tier and candidate tier; eligible candidate belongs to task; candidate is legitimately available in the evaluation mode. Reject (raise typed domain error / ValueError) on any mismatch, with zero state/history mutation. If no eligible candidate remains, return a fail-closed unresolved/exhausted decision, not fake success. Explicitly distinguish **simulated/offline result** from an authenticated, evidenced benchmark result. A fake scorecard cannot grant production permission.

### B2 — Router can return a forged provider/model/tier as 'approved' (critical)

File: `sales_engine/llm_gateway/router.py`, `TaskRouter.set_approved_route` and `resolve_approved`.

Observed: enable `t1_c1_openai_mini` in an in-memory catalog, then set a route with candidate ID `t1_c1_openai_mini` but `provider_id="anthropic"`, `model="unverified-model"`, `tier=TIER_4_FLAGSHIP` and `approved_by=None`. `resolve_approved("inbox_classification")` currently returns this mismatched route.

**Required:** Validate on both registration **and resolution** that `decision.candidate_id` identifies an existing eligible/enabled candidate; normalized decision task matches requested task; candidate is allowed for that task; candidate tier/provider/exact model equals route tier/provider/model; verified capabilities satisfy task; approval has authenticated operator provenance and valid policy status, NOT just an untrusted string. Store an immutable/snapshotted route; protect against catalog changes after approval. Do not treat arbitrary external input as authoritative approval; explicitly document trust boundary and where backend auth will supply approval context later. An offline proposed winner is not an authorized production route. Any violation raises a typed fail-closed exception and does not call a provider.

### B3 — Terminal states accept replay / stale results (high)

File: `EvaluationPlanner.process_scorecard`.

Observed: after `status="approved"`, the same scorecard can be submitted again, history grows, and another `ACCEPT_CANDIDATE` is returned.

**Required:** Block mutations in terminal states (`approved`, `unresolved`) and while paused at `tier_exhausted`; reject duplicate or out-of-order scorecards; ensure replays produce no extra history entries. Explicit reset/rebenchmark action only, with versioned evaluation run ID, operator authorization and audit trail. Do not silently reset an approved task. Only process scores for the current planned candidate.

### B4 — Fake/unverified model IDs, pricing and capabilities presented as 'verified' (high)

Files: `model_catalog.py`, `config/llm_model_catalog.example.json`.

Observed: default catalog embeds concrete dated model IDs (including 2024–25 generations), asserts generic `supports_json_mode=True`, and example JSON includes 2024–25 token prices without proof of current availability. Some identical models are repeated at Tier 2/Tier 3, and an example Tier 4 contains an older budget model; tier classification is consequently not evidence-based.

**Required:** Keep 16 **disabled** configurable slots but treat names/prices/capabilities as unknown (`null`/empty) until separately verified from providers. Prefer neutral slot IDs like `t1_c1`...`t4_c4`, allow optional `provider_id` and `exact_model_id` for disabled placeholders, and validate complete verified configuration before enabling. Optional illustrative provider ordering must be labeled as illustrative, never claimed current. `source_url`, verification date, API contract and capability evidence are required when declaring facts verified. Do not fetch or pick live model names in this corrective stage. Update tests relying on stale fixture IDs to neutral IDs or explicitly-test-only fake IDs. Document migration/alias impact on existing S2.3 tests.

### B5 — State/candidate mutability, subtask and eligibility checks (medium/high)

- `ModelCandidate` is currently mutable even though S2.3 spec requested immutability; `ModelCatalog.candidates` and priority overrides are mutable containers. Guard catalog integrity through immutable candidate records, validated updates, and defensive copies/snapshots for approved routes.
- Validate `subtask_id` strictly: only `pass_a` / `pass_b` for `website_extraction`, otherwise `None` unless explicitly declared. `pass_a` and `pass_b` benchmark progress cannot affect each other. Decide/document whether production route can differ per website subtask; at minimum preserve separate evaluation outcomes and never silently collapse one subtask's approval to the other.
- Validate duplicate `(tier, priority)` slots; invalid candidate priority overrides; task allowances; disabled/unavailable/provider-adapter-not-implemented candidates. Dry run may **list** disabled placeholders for UI but they cannot be accepted as a verified model or used in production.
- Preserve explicit operator approval for promotion; a boolean passed by untrusted content is not an authenticated actor. Ensure exhaust Tier 4 -> unresolved and never auto-escalate.
- Pydantic models must not accept silent extra fields or preserve untrusted prompt strings as routing directives.

## 2. Mandatory focused regression tests (offline, no external calls)

Add a dedicated `tests/test_s2_3_1_routing_integrity.py`, at minimum:

1. Reject mismatched candidate ID even when `passed=True` and `score=1.0`.
2. Reject mismatched Tier, including Tier 4 scorecard during Tier 1.
3. Reject mismatched website subtask `pass_b` submitted for `pass_a` state.
4. Reject stale/out-of-order candidate scorecard after moving to candidate #2.
5. Reject replay after `approved`, preserving immutable history count.
6. Reject replay during `tier_exhausted` and after `unresolved`.
7. Reject scorecards for disabled or unavailable candidates unless explicitly simulated, in which case no production approval is possible.
8. Reject forged route provider, model, tier, task and disabled candidate individually.
9. Reject missing/untrusted operator authority; proposed benchmark PASS cannot set production route.
10. Verify route rejection on later catalog mutation / disablement.
11. Verify candidate verification metadata is absent by default; disabled slots cannot fabricate capabilities or prices.
12. Verify independent task state and website `pass_a`/`pass_b` state.
13. Verify operator promotion gate, no automatic paid failover, no HTTP, no Ollama.
14. Verify all existing S2.1/S2.2 gateway contracts and business/sending safeguards stay intact.

Tests must assert **no state/history mutation on rejection**, not just that exceptions occur. Add at least 15 focused negative/edge tests. Update tests that previously assumed old hardcoded vendor/model IDs, and document why.

## 3. Execution boundaries and acceptance

1. Verify current HEAD/worktree. Do not rewrite previous signed-off commits. New local branch.
2. Apply **small targeted corrections** in S2.3 files only, plus needed tests, example config, docs and public exports. Avoid restructuring S2.1/S2.2 provider contract.
3. Run the new adversarial tests; existing focused S2.1, S2.2, S2.3 tests; safeguard tests; full `pytest tests/ -q`; `python scripts/audit_repository.py`; report actual counts.
4. No network, no API charges, no email, no scraping, no new dependencies, no real data changes.
5. Commit locally; rebuild `SalesAI_export.zip`; ensure S2.3.1 report/spec/tests and all prior gateway files included, healthy ZIP, zero denylist violations; clean Git.
6. Deliver `docs/architecture/S2.3.1-ROUTING-INTEGRITY-REPORT.md` with observed vulnerabilities, patch and validation evidence, exact Git hash, file counts, security notes, rollback. **Stop after S2.3.1**; do not begin S2.4 before review.

**Acceptance gate:** None of the B1–B5 invalid operations can be accepted; accepted benchmark recommendations and real operator-authorized production routes are distinct; no model names/prices/capabilities are asserted verified without evidence; existing task-first Tier-1-first strategy remains intact.
