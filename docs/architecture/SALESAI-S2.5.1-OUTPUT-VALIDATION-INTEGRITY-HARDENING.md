# SalesAI S2.5.1 — Output Validation Integrity & Local Safety Precedence Hardening

**Status:** Corrective implementation specification; NOT executed  
**Date:** 2026-10-22  
**Project:** SalesAI / pyBIM  
**Baseline branch (reported):** `feat/salesai-s2-5-output-validation`  
**Expected HEAD (reported):** `5a1ed4e` (docs finalization; prior implementation `7559c46`)  
**Reported full regression baseline:** 1145 passed, 8 skipped  
**Independently rerun Gateway tests:** 216 passed (on extracted `SalesAI_export(10).zip`)  
**ZIP baseline:** 424 files, archive integrity verified.

## 0. Why this corrective step is required

The previous S2.5 implementation has been completed, and its existing test suite passes. However, independent **offline execution of the delivered ZIP code** reproduced the following fail-open behaviors. These are NOT theoretical findings and MUST be corrected before S2.6 and before any live API integration.

| ID | Independently reproduced issue | Current incorrect behavior | Required behavior |
|---|---|---|---|
| B1 | Invented `evidence_refs` with no approved evidence set | `is_valid=True` because the membership check is skipped if the set is empty | Any nonempty cited references MUST be rejected when no explicit trusted allowlist is available |
| B2 | `approved_evidence_ids={"VERIFIED-01"}` but request metadata adds `"FAKE-0001"` | Untrusted/more permissive metadata is merged into the trusted set; `"FAKE-0001"` passes | An explicit trusted `approved_evidence_ids` argument is authoritative and MUST NOT be broadened by request metadata; define a single trusted provenance path |
| B3 | `local_signals={"is_opt_out_detected":True}` with model category `not_now` | Returns valid `not_now`, not an opt-out | Local unsubscribe/suppression takes precedence over **every** conflicting model classification, including `not_now`, `not_interested`, `unknown`, `auto_reply`, and `bounce`, with conservative operator review |
| B4 | `local_signals={"is_bounce_detected":True}` with model `meeting_proposed` | Returns valid meeting interest | Handle bounce and suppression as distinct authoritative local signals; never label a bounced lead as positive/meeting |
| B5 | Request `provider=None, model=None`, response with arbitrary identity | Returns valid result because comparison is conditional | Validation of a response for approved execution MUST require explicit pinned provider and model (and any required approved route/candidate context). Missing identity fails closed |
| B6 | Request `metadata.subtask_id=pass_a`, validator call `subtask_id=pass_b` | Accepts valid Pass B payload under a Pass A request | Detect and reject mismatched subtask declarations; do not let a caller silently override request-bound context |
| B7 | Schema-extra field key `email_alice@example.com` | The raw attacker-controlled field name appears in `failure_reasons` and `safe_log_dict()` | Use sanitized, bounded, deterministic reason codes. Never include output-controlled or PII-bearing raw schema keys in logging, repr, repair proposals, or diagnostics |
| B8 | JSON value `confidence: "0.98"` (string) | Pydantic silently coerces it to float and returns valid | Strict type validation must reject coercion of strings to numbers, numeric strings to ints, and similar unapproved conversions |

**Important:** Passing structural schemas cannot prove that evidence actually supports a factual claim. Keep `needs_business_review` and all downstream deterministic business validators in place; do not mark factual correctness automatically.

## 1. Non-destructive baseline and branch isolation

1. Verify `git status`, `git branch --show-current`, `git rev-parse HEAD` against the **actual working repository**. If unexpected or dirty, stop without rewriting or discarding unrelated work.
2. Create a new local correction branch, e.g. `fix/salesai-s2-5-1-output-validation-integrity`, from verified S2.5 HEAD.
3. Read existing `output_validation.py`, `output_schemas.py`, `repair_policy.py`, `contracts.py`, `payload_builder.py`, `model_catalog.py`, `sales_engine/inbox/security.py`, and all S2.1–S2.5 tests before editing.
4. Keep S0–S2.4.1 and the existing S2.5 successful behaviors intact. Do not alter the six live SalesAI LLM call sites.

## 2. Corrections and expected invariants

### 2.1 Evidence authority — B1/B2

- A response that cites `evidence_refs` MUST have an explicit, trusted, nonempty allowlist, or fail with `EVIDENCE_APPROVED_SET_REQUIRED` or equivalent stable code.
- For a provided approved set, **every** cited ID MUST be present; unknown, duplicated, malformed and blank references fail.
- `approved_evidence_ids` passed from a trusted caller is authoritative; metadata must not silently UNION additional identifiers into that set. If no trusted set exists, metadata must not be treated as equivalent authority unless it has an explicit documented trusted producer and validation boundary. Prefer requiring the explicit argument.
- Do not send full source files or original evidence into a model as part of a validation error or proposed repair.
- Keep the separate human factual-grounding review gate.

### 2.2 Local opt-out, bounce, suppression precedence — B3/B4

- Define explicit local signals, for example `is_opt_out_detected`, `is_bounce_detected`, `is_suppressed`, each sourced only from deterministic local checks (not model assertions).
- A detected local hard signal overrides **all** conflicting model categories, not just positive ones. In particular, `not_now` is **not** a substitute for `unsubscribe` and must not schedule a follow-up when the source opted out.
- When local bounce is authoritative, never return `meeting_proposed` or another positive category as actionable. Preserve a stable code for local bounce and opt-out separately.
- Decide precedence deterministically for multiple simultaneous signals and test it.
- For rejected conflicts, `validated_data=None` must be compatible with a structured **safe authoritative local signal/outcome** for future S2.7 consumers (e.g. `effective_local_disposition`), without giving the model any execution authority. Do not auto-send or auto-modify operational records in S2.5.1.
- Existing full-original-message checks from S2.4 remain in force.

### 2.3 Identity and subtask integrity — B5/B6

- If the request is not explicitly bound to a provider/model selected through the trusted route, fail closed. Distinguish `MISSING_APPROVED_MODEL_IDENTITY` from actual mismatches where useful.
- `request.metadata.subtask_id` and function argument `subtask_id` must be consistent if both exist. Missing website subtask still fails; non-website unexpected subtasks fail.
- Never infer an approved model or subtask from the untrusted LLM response itself.
- Preserve S2.3.1 operator authorization blocker. `approved_by`/`operator_token` strings are **not** authenticated sessions; do not claim production authorization is solved in S2.5.1.

### 2.4 Strict schemas and safe diagnostics — B7/B8

- Reject strings in numeric fields where numeric JSON values are required, and reject coercion that changes semantics. Use `strict=True` on relevant Pydantic models/fields or equivalent targeted strict validation, with regression tests.
- `failure_reasons`, `safe_log_dict()`, `__str__`, `__repr__`, and `RepairProposal` must never contain user/model-controlled field names, prompt text, contact details, keys, or raw content. Prefer generic reason codes (e.g. `SCHEMA_EXTRA_FIELD`, optionally followed by an index or a known registry-field ID), not arbitrary key paths.
- Keep reason codes deterministic, bounded, and suitable for audit logs; avoid echoing Pydantic validation `input` or input-containing exception text.
- Preserve safe measured usage and latency; do not invent costs.
- Do not turn semantic, security, provenance, evidence or suppression failures into eligible formatting-only repair proposals.

## 3. Required tests (fully offline)

Add **at least 20 new focused adversarial tests** (prefer 25+) exercising B1–B8, and first write exact reproductions of all eight failures so the previous version demonstrably fails them.

Minimum test matrix:

1. Nonempty invented evidence ref with `approved_evidence_ids=None` fails.
2. Nonempty invented ref with explicit empty set fails.
3. Explicit trusted set cannot be expanded by metadata.
4. Valid evidence ID passes and needs business review.
5. Duplicate and malformed evidence IDs fail.
6. Local opt-out + `not_now` fails and cannot schedule future follow-up.
7. Local opt-out + `not_interested` / `unknown` / `auto_reply` fails or is consistently overridden.
8. Local opt-out + `meeting_proposed` remains non-actionable.
9. Local bounce + `meeting_proposed` is blocked.
10. Local bounce + `positive_interest` is blocked.
11. Local suppression + `question` is blocked from actionable positive classification.
12. Multiple simultaneous local signals have deterministic precedence.
13. Missing provider in request fails closed.
14. Missing model in request fails closed.
15. Both provider and model pinned correctly still pass.
16. Website Pass A request cannot be validated as Pass B by argument override.
17. Valid same-subtask request passes.
18. Nonwebsite subtask injection fails.
19. Attacker-controlled schema key resembling an email never appears in `safe_log_dict` or repr.
20. Schema key containing a token-shaped secret cannot enter reason codes or repair plans.
21. Numeric string confidence is rejected.
22. Numeric string query priority is rejected.
23. JSON number confidence is accepted.
24. Rejected error cannot become auto-retryable or trigger tier escalation.
25. Sync and async results remain identical.
26. All older S2.5 positive fixtures still pass.

Use synthetic identities and sources only. Do not use `Emails.xlsx`, real contact data, or credentials in test fixtures. Preserve the original 58 S2.5 tests (adjust only where old behavior conflicts with a corrected security invariant, documenting the change).

## 4. Verification, security and packaging

- Run focused S2.1–S2.5.1 tests and all existing dedicated business/sending/admin tests.
- Run full repository regression suite. Expected reported baseline before adding new tests: **1145 passed, 8 skipped**.
- Run `python scripts/audit_repository.py`, `git diff --check` and clean release ZIP build.
- Confirm S2.5.1 code/tests/spec/report are included; check `.env`, `*.db`, auth secrets, local runtime data, and deprecated legacy assets are excluded.
- Report exact pass/fail/skip counts, newly added tests, commit, branch, ZIP member count, changed paths, known risks and rollback procedure.
- Keep all Gateway traffic disabled by default; no real OpenAI/Google/Anthropic/Mistral API calls, no expense, no model downloads, no scraping, no email send, no operational DB modifications, no Git push/merge.

## 5. Acceptance gate

**S2.5.1 passes only when all eight independent reproductions are blocked, all existing positive controls work, and all new and full regression suites pass.** S2.6 (Budget/Concurrency/Retry Controls) may begin only after independent review of the S2.5.1 ZIP and technical report. Operator session authentication still blocks live approvals in S2.7.

**STOP after S2.5.1.**
