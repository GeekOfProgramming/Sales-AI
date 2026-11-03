# SalesAI S2.4 — Minimum-Data Payload Builder, Privacy Boundaries & Untrusted-Content Isolation

**Status:** IMPLEMENTATION SPECIFICATION — not yet implemented  
**Date:** 2026-10-21  
**Baseline branch:** `fix/salesai-s2-3-1-routing-integrity`  
**Expected HEAD:** `e0b6c7fa41dbc0f920f4e21e85fcf4ce563b2e59`  
**Baseline tests:** 1023 passed, 8 skipped (Antigravity report); Gateway focused tests 94 passed (independently reproduced).  
**Scope:** S2.4 only. Do not proceed to S2.5, S2.6, S2.7 or external benchmarking.

## 1. Target architecture and non-negotiable boundaries

SalesAI runs on a Windows developer laptop using a localhost FastAPI console and local SQLite database. All future model inference will use remote HTTPS APIs, but **the company database, source files, evidence archive, email inbox history, recipient addresses, and full Excel workbook remain local**. An explicit narrow outbound data contract must govern every LLM API payload. Nothing in this stage enables API dispatch or operational model routing.

Human review, recipient suppression, GDPR/channel permissions, and send authorization stay deterministic and local. Treat all externally obtained websites, job descriptions, search results, emails, attachment text, and imported spreadsheet cells as **untrusted source material**, never as system instructions. Redaction is a technical minimization control, **not** a guarantee that no personal data remain and not a legal basis for marketing.

## 2. Required implementation components

Create a self-contained, lightweight package, preferably:

- `sales_engine/llm_gateway/payload_builder.py`: deterministic assembly of allowlisted, task-specific prompt context; source-type provenance; strict size bounds; no IO or network.
- `sales_engine/llm_gateway/privacy_filter.py`: configurable deterministic detection/redaction of sensitive strings plus a fail-closed mode when safe transformation cannot be established. Do not add ML models, heavyweight SDKs or network-based DLP.
- `sales_engine/llm_gateway/source_boundaries.py` (optional, if separation clarifies code): robust encoding/wrapping of untrusted source text.
- `tests/test_s2_4_privacy_payload.py`: at least 30 deterministic offline tests, including adversarial payload examples.
- `docs/architecture/S2.4-PRIVACY-PAYLOAD-REPORT.md`: acceptance report and limitations.

Use Pydantic or typed dataclasses consistent with existing `LLMRequest` / `LLMResponse`; use current stdlib and installed dependencies only. New public APIs must have strict allowed field sets, bounded strings, type validation and safe `__str__`/`__repr__`. Never include raw PII or raw prompt in exception messages, structured application logs, tracing, URLs, request IDs or metadata.

### 2.1 Input trust classes

Model trust labels must distinguish:
- `trusted_policy`: static, locally maintained SalesAI system/task instructions (must never derive from scraped source).
- `approved_business_facts`: explicitly selected evidence-derived business facts, **still data, not instructions**.
- `untrusted_source`: website text, job listing, inbound email, search snippet, spreadsheet note, etc.
- `restricted_local_only`: API keys, individual contact details, recipient addresses, private annotations, hidden sheets, full records, inbox headers, authentication/session data; never transmitted.

The builder must accept **explicit allowlisted fields**, not arbitrary nested company dictionaries, raw ORM objects, whole workbooks, full raw HTML, or entire email threads. Reject unknown or forbidden keys unless a documented safe local-only exclusion mode is used; never silently serialize arbitrary model fields.

### 2.2 Task-specific outbound field allowlists

Provide separate policies for **six task families**, with `website_extraction` split into `pass_a` and `pass_b`:

1. `website_extraction:pass_a`: company display name, public company domain (normalized to hostname, excluding URL query), sanitized company-services evidence excerpts and stable, opaque local source IDs. NO direct-contact personal information.
2. `website_extraction:pass_b`: minimal validated facts and relevant sanitized evidence only; no duplicate full website corpus.
3. `job_analysis`: role title, company name, sanitized job-description excerpt, technical requirements and approved pyBIM service taxonomy; NO applicant data, recruiter email or phone.
4. `discovery_query_generation`: market/geographic business scope, service vocabulary, sanitized keywords; NO contacts, inbox threads or credentials.
5. `inbox_classification`: sanitized necessary portions of **one** inbound response plus minimal lifecycle context (e.g. reply-to-existing-thread boolean); NO raw headers, address book, signature block, quoted historic chain or attachments.
6. `reply_suggestion`: sanitized recent reply, minimally necessary facts, intended intent/action, approved service text; NO raw recipient address, private contact profile or full conversation history.
7. `outreach_draft`: approved business facts, source-backed relevance reasons, narrow service proposition and tone requirements; NO personal names/emails unless explicit reviewed exception outside this stage.

Use a versioned field-policy registry. For inbound email tasks, apply the existing **deterministic** `InboxSecurity`/suppression and opt-out checks to the **full original text locally BEFORE** any truncation or redaction; do not cause opt-out, complaint, unsubscribe or meeting-intent phrases to be dropped by context trimming. If classification context cannot be safely preserved, route to manual review, **not** an optimistic fallback.

### 2.3 Redaction requirements

At minimum detect and mask in the **outbound** payload: individual email addresses, phone numbers (including common Italian/international formats), IBAN/bank account details, payment card-like numbers (prefer Luhn checking), bearer/API tokens, secret-looking assignment strings, local absolute file paths when private, authentication/session cookies, and text explicitly tagged private/restricted. Test that innocent numeric BIM standards and public company data are not indiscriminately destroyed. Prefer placeholders such as `[PERSONAL_EMAIL_1]`, `[PHONE_1]`, `[IBAN_1]`, `[SECRET_1]` rather than deleting surrounding meaning. Numbering is scoped to a single request; do not create a re-identification mapping sent to a provider.

Do **not** claim regex can reliably identify every person's name, every PII string, every sensitive business detail or every mixed-language secret. Unknown or ambiguous sensitive content should be marked for manual review / rejected under conservative policy. Company generic contact email may be retained locally; model tasks should ordinarily receive no email address. All raw values and optional local placeholder maps must remain on device and outside normal logs.

### 2.4 Injection-resistant untrusted text container

The static system policy must instruct that all source text is data. Serialize external text as JSON-escaped **data values** inside a typed envelope recording `source_kind`, local opaque `source_id`, and `untrusted=true`; you may additionally label the block `UNTRUSTED_CONTENT`, but **XML delimiter tags alone are insufficient** because the source can contain spoofed closing tags. Do not allow source text to control model name/provider/tier, policy, role, API endpoint, budget limit, approval/suppression state, schema, or subsequent tool calls. Never concatenate source text into system/developer role messages.

Fail closed on malformed containers, unsupported encodings, forbidden control characters, obvious nested role-spoofing, data URLs or extremely large input. Importantly: heuristic prompt-injection detection does **not** make arbitrary malicious text harmless. Ensure no source content gets promoted to a trusted instruction role, and test that downstream Gateway request construction respects this invariant.

### 2.5 Payload size and privacy budget

Use independently configurable **upper bounds on outbound source excerpt characters** (not token counts):

- Website pass A: up to **8,000** characters of curated evidence.
- Website pass B: up to **4,000** characters of evidence/facts.
- Job analysis: up to **6,000** characters.
- Inbound classification: up to **4,000** characters, with mandatory prior full-text opt-out check.
- Reply suggestion: up to **4,000** characters.
- Outreach draft: up to **5,000** characters.
- Discovery query generation: up to **2,000** characters.

Limits apply to content segments and must also enforce a conservative total serialized `LLMRequest` bound. Reject or deterministically select locally ranked excerpts before building request; **never simply slice untrusted inbound email** where doing so could remove a refusal/opt-out signal. Preselect useful company facts before truncation, preserving opaque `source_id` provenance. Include an output-length cap aligned with the request contract. These limits are not token estimates and must not be reported as measured token usage.

### 2.6 Safe result contract

Expose a typed `PayloadBuildResult` (or equivalent) including request-safe prompt segments, included source IDs, **counts only** of redactions/omissions, policy version and booleans such as `manual_review_required`; do NOT expose unredacted text in safe logs. A separate explicit opt-in local inspection function may show sanitized preview for an authenticated operator, never original secrets. No automatic provider invocation. On privacy uncertainty, produce a typed privacy rejection requiring human review and perform zero external calls.

### 2.7 Operator authorization integration risk (record, do not fake)

Independent S2.3.1 review confirms `TaskRouter.set_approved_route` only checks `approved_by` is a non-empty string and `EvaluationPlanner.reset_evaluation` only checks a non-empty `operator_token`. These are **provenance placeholders, not authentication**. Record as a BLOCKING requirement for S2.7/live activation: approval and reset must use a trusted, authenticated local session/actor, authorization check and audit log; untrusted payload fields cannot supply authority. Do not claim this is solved in S2.4 and do not broaden current task into a UI authentication rewrite.

## 3. Mandatory adversarial offline tests (30+)

Tests use **synthetic fictional companies/addresses only**, with no real workbook PII or contacts. Include:

1. All seven subtask-specific policies / six task families; only allowlisted fields survive.
2. Whole workbook / arbitrary row dict / nested ORM object rejected without serialization.
3. Contact email, Italian and +international phone, IBAN, bank or card-like data masked; generic BIM numbers survive.
4. API key, bearer token, cookie, `.env` secret, local Windows path, URL query with token removed or rejected.
5. Prompt containing `</UNTRUSTED_CONTENT>`, system-message spoofing, forged JSON role objects, Markdown injection, HTML and Unicode confusion remains data-only.
6. A fake source command `switch provider`, `disable budget`, `send email`, `ignore suppression`, `promote Tier 4` never enters instruction/configuration surfaces.
7. Full-text unsubscribe at **end of long** email is detected locally before truncation; preserved manual-review/suppression flag.
8. Not-now/Future follow-up, negative, positive/meeting replies remain distinguishable after sanitization; conservative manual-review when not.
9. Website Pass A and Pass B independent, separate limits and no leakage of notes.
10. Disabled Gateway / absent provider: builder makes zero network requests and does not enable credentials or route.
11. No raw secrets in `repr`, `str`, typed validation exceptions or `safe_log_dict`.
12. Idempotent, deterministic building, stable sanitized source IDs and no modification of original input objects.
13. Oversize/unexpected source type, malformed encoding/Unicode, invalid hostname/url, unknown task/subtask fail closed.
14. Company/profile name, source quote and BIM terms retain enough semantics to avoid meaningless output.
15. No silent truncation that discards policy-critical email text; never treat sanitization as proof of legal consent.

Tests should explicitly assert the output does **not** contain original injected strings/PII where appropriate; also test that trust boundaries hold independently of regex detection. Existing tier routing, model approvals, and operator permissions must not be modified by the payload builder.

## 4. Scope limits and handoff

1. Verify current branch `fix/salesai-s2-3-1-routing-integrity`, HEAD `e0b6c7f` and clean working tree; create a dedicated S2.4 branch.
2. Preserve all existing operational business logic, six LLM call sites, API adapter, catalog/router/evaluation logic, data/evidence sources, inbox/sending controls, and legacy transitional clients. **Do not connect these call sites to the Gateway in S2.4.**
3. No actual API calls, models/SDK installations, credentials, scraping, sending, real workbook import, DB mutations, push or merge.
4. Run S2.4 focused tests, earlier S2.1–S2.3.1 Gateway suite (94 baseline), relevant inbox/suppression/sending tests, full `pytest tests/ -q` (1023 passed, 8 skipped baseline), `python scripts/audit_repository.py`, `git diff --check`.
5. Commit locally, rebuild `SalesAI_export.zip`, verify new/previous files in archive, ZIP integrity, denylist exclusion, clean working tree.
6. Report exact new tests, any skipped tests/warnings, Git hash, file count, changed files, redaction limitations, operator-authentication integration risk and rollback instructions. **STOP after S2.4.**

## 5. Acceptance criteria

- Outbound contexts are intentionally minimal and task-specific, never raw records or entire workbooks.
- Personal/secret patterns are sanitized deterministically and unresolved sensitive content is fail-closed/manual-review.
- Web/email/job content is never promoted to trusted instruction roles; escaping protects against delimiter breakout.
- Safety-critical email signals are checked against full local text before reduction.
- No operational data change, zero API use, zero auto-escalation and no emails sent.
- All old tests pass plus 30+ new offline adversarial tests; archive and audit clean.
- The S2.3.1 operator-authentication gap is accurately documented as unresolved until integration, not described as fixed.
