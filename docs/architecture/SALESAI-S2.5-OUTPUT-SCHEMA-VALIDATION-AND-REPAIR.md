# SalesAI S2.5 — Strict LLM Output Validation & Bounded Repair (Offline Foundation)

**Status:** Implementation specification / not executed  
**Version:** 1.0 — 2026-10-22  
**Project:** SalesAI / pyBIM  
**Base branch:** `fix/salesai-s2-4-1-privacy-boundary-hardening`  
**Expected base HEAD:** `2610cc2`  
**Regression baseline reported by Antigravity:** `1087 passed, 8 skipped`  
**Independent verified gateway baseline:** `158 passed` (Python 3.13)  

## 0. Purpose and limits

Implement a **deterministic, offline, provider-independent validation layer** for responses from the future remote LLM API. An HTTP 200 or parsable JSON object is **not** a validated response, and never by itself means a correct business recommendation. Reject unsafe, mismatched, or non-conforming outputs. Keep the earlier S0–S2.4.1 business and security safeguards intact.

**No real calls, API keys, model purchases, emailing, scraping, system package changes, or operational data edits in this stage.** Do not migrate the six active SalesAI call sites yet; that belongs to S2.7. Do not activate any production routing or tier escalation.

## 1. Baseline verification and branch

1. Run `git status`, `git rev-parse HEAD`, `git branch --show-current`; refuse to proceed on unexpected/dirty source without an explicit documented resolution.
2. Create a dedicated local branch, e.g. `feat/salesai-s2-5-output-validation`.
3. Read these existing modules before editing:
   - `sales_engine/llm_gateway/contracts.py`
   - `sales_engine/llm_gateway/interface.py`
   - `sales_engine/llm_gateway/providers/openai_provider.py`
   - `sales_engine/llm_gateway/payload_builder.py`
   - `sales_engine/llm_gateway/errors.py`
   - `sales_engine/llm_gateway/model_catalog.py`
   - `backend/schemas.py`
   - `sales_engine/outreach/schemas.py`, `sales_engine/outreach/draft_validator.py`
   - `sales_engine/inbox/classifier.py`, `sales_engine/inbox/models.py`
   - `sales_engine/discovery/query_generator.py`
   - `sales_engine/analysis/website_analyzer.py`, `sales_engine/analysis/job_analyzer.py`
4. Capture existing test baseline and preserve the local Git history and existing data.

## 2. Output contract and validation boundary

Create lightweight modules under `sales_engine/llm_gateway/` as appropriate:

- `output_validation.py` — `LLMOutputValidator` and typed result `OutputValidationResult`.
- `output_schemas.py` — task/subtask-specific schema registry and version metadata.
- `repair_policy.py` — bounded, auditable repair decision and plan **only**, with no network dispatch.

Exact names can adapt to repository conventions; document any deviation.

### 2.1 Identity and provenance

The validator must verify **exact match** of request/response `request_id`, canonical `task_id`, selected `provider`, and exact `model`, and where available tier/subtask/model catalog selection. A mismatched or missing identity is a hard failure. Do not trust `parsed_json` merely because the adapter populated it; S2.2 currently performs syntax parsing but does **not** validate business schemas. Never stamp validated status into `LLMResponse.parsed_json` on parse success alone.

Only the selected, operator-approved candidate (as resolved by S2.3.1) can later be used by an authorized production call. This stage does not implement operator authentication.

### 2.2 Strict syntax parsing

- Accept only one complete JSON object, with at most one **exact whole-response** Markdown JSON fence trimmed locally; strip ordinary leading/trailing whitespace.
- Reject commentary outside the JSON, multiple objects, array/scalar roots, duplicate keys (including nested duplicates), invalid UTF/control sequences, `NaN`/`Infinity`, inappropriate coercion, and excessive depth/length. Use safe bounds for bytes, collection sizes and nesting; record the selected caps in the implementation and tests.
- Reject `finish_reason` indicating truncated output (`length`) or refusal, even if a fragment accidentally parses.
- Do not implement permissive substring `{...}` extraction, greedy regex extraction, `eval`, or JSON-fixing libraries that silently change meaning.
- Redact/avoid echoing raw provider output in exceptions and application logs. Exception messages and scorecards should show safe **reason codes**, schema version and lengths only.

### 2.3 Task schemas (six task families; website has two independent passes)

Use the existing models/contracts as the authoritative source wherever appropriate, rather than inventing a new incompatible shape. Establish and test explicitly named, versioned schemas for:

1. `website_extraction:pass_a` — extracted facts and evidence.
2. `website_extraction:pass_b` — final synthesis/signals using Pass A context.
3. `job_analysis` — based on `backend.schemas.JobAnalysisResult` and actual production prompt structure.
4. `discovery_query_generation` — bounded, normalized query list; reject harmful or unrelated instructions.
5. `inbox_classification` — canonical intent, with priority for opt-out/bounce decisions derived **locally** from complete original message.
6. `reply_suggestion` — bounded proposed text; no sending authority; no fabricated commitments.
7. `outreach_draft` — align generated fields to `sales_engine.outreach.schemas.LLMEmailResponse` and preserve `DraftValidator` as a separate final business gate.

**Implementation requirement:** first create a mapping table of *actual existing prompt schema* → *S2.5 schema*, noting discrepancies and backward compatibility implications. Avoid changing any existing live call sites or Pydantic business models in S2.5. All task fields must have explicit types, requiredness, lengths, enum restrictions, extra-key policy, and nullability. A generic JSON object is never a sufficient pass criterion.

### 2.4 Evidence and safety checks

- When an output cites `evidence_refs`, every cited ID must be in the **locally supplied, approved evidence ID set**; reject unknown IDs, duplicate IDs where prohibited, and fabricated source identifiers.
- Do not assert that cited text proves a claim merely because the reference exists: evidence *support* cannot be established by schema validation alone. Mark such cases `needs_business_review` (or equivalent) and leave existing deterministic grounding/claim validators authoritative.
- A classifier must never override a local opt-out/bounce/suppression signal with a model response. An unsubscribe response must not become a positive sales opportunity merely because the model's JSON claims so.
- Reject unsafe instruction echoes, injected role markers in generated instructions, secrets, and unintended PII according to downstream output policy. Preserve local-only contact fields and approval authority.
- No output may directly authorize a send, alter suppression, promote tier, configure a provider, modify a budget, or create a confirmed meeting.

## 3. Bounded repair policy — no paid retry now

A deterministic local normalization step may remove leading/trailing whitespace and a single *whole-response* JSON code fence. This is not a second model call.

If output remains invalid:
- Return a **rejected validation result** and an **optional one-time repair proposal** for the *same* request, task, subtask and previously approved provider/model.
- A repair proposal may later be dispatched only after S2.6 authorization, cost budgeting, and per-task retry policy; **S2.5 itself must perform zero network calls**.
- Never auto-escalate to the next candidate or tier. Never silently retry indefinitely.
- Never include raw unsanitized company records, contact details, raw prior replies, hidden credentials, or unsafe original provider text in a future repair prompt. Future repair input must pass through S2.4 privacy boundaries again.
- Semantic/business/safety failures (invented facts, broken opt-out handling, forged citations, incorrect company) are **not** auto-repairable formatting errors. Send them to manual review or later benchmark escalation protocol.
- Persist safe reason codes, attempt counts (0 or 1), model identity, schema version, and measured metrics when available; unknown costs stay `None`, never guessed zero.

## 4. Security and isolation requirements

- Pure local processing, no I/O except explicit optional audited writes to test temp directories. No database changes.
- Do not import or start Ollama, ChromaDB, Torch, a browser, or a Cloudflare tunnel.
- No arbitrary class instantiation from caller-provided `response_schema`. Schema registry must be built from trusted static definitions.
- For user-provided/untrusted `response_schema`, either reject it entirely at this layer or validate against a narrowly permitted, versioned internal registry; document the policy. Do not let arbitrary schemas override task policies.
- Response content must not be written into logs, error strings, test reports, telemetry, or CLI output by default.
- Preserve S2.3.1 operator-approval limitations as a **blocking condition for live deployment**; nonempty `approved_by` is not actual authentication. Do not fake completion of operator auth.

## 5. Offline testing — minimum 35 new tests

Use strictly fictional data. Include successes and adversarial cases covering:

1. All seven task/subtask schemas: at least one valid and one invalid each.
2. Identity mismatch: `request_id`, task ID, provider, model, selected candidate/subtask.
3. JSON fence accepted only when it encloses the entire response; surrounding text rejected.
4. Duplicate JSON keys, multiple JSON objects, array roots, NaN, huge payload, excessive depth.
5. A parsable JSON object violating required fields, types, enum values, field bounds or extra-key policy.
6. A result with `finish_reason=length` is rejected.
7. Unknown evidence ref, fabricated citation, contradictory or unsupported claim, and false positive confidence.
8. Opt-out/bounce precedence over a contradictory model-positive classification.
9. Prompt injection reflected back into output; payloads containing secrets or PII.
10. Local normalization succeeds without an extra provider request.
11. Failed validation creates at most one disabled repair proposal; no retry network call or model/tier escalation.
12. Deterministic failure reason codes and safe `str`, `repr`, logger and serialized diagnostic outputs.
13. Sync/async validation parity if an async facade exists.
14. No mutations to S2.4 sanitized payload or to S2.3.1 routing approvals.

Reuse the ten-example synthetic benchmark concept (five straightforward, five misleading) as an evaluation **design**; do not substitute mock fixture scores for evidence of real model quality. Real paid benchmarking remains Phase S3.

## 6. Acceptance and handoff

Execute:
- New S2.5 focused offline tests (>=35).
- All S2.1 through S2.4.1 Gateway tests (baseline 158).
- Relevant outreach/email/inbox/admin safety tests.
- Full regression suite (reported starting baseline: 1087 passed, 8 skipped).
- `python scripts/audit_repository.py`, `git diff --check`.
- `python scripts/rebuild_zip.py`; verify S2.5 code, tests, report, and specification are present and ZIP has zero denylist violations.

Write `docs/architecture/S2.5-OUTPUT-VALIDATION-REPORT.md` including actual test results, schema mapping, failures/limits, Git HEAD, ZIP counts, rollback instructions, and remaining blockers.

Preserve all six live LLM call sites and operational data. Commit locally on the S2.5 branch. **No push, no merge, no real API calls, no sending email. STOP after S2.5; do not start S2.6.**
