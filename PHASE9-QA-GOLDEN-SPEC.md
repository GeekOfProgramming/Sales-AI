# SalesAI Phase 9 QA / Golden Acceptance Spec
## Human Review, Approval, Safe Sending & Audit Trail

# Purpose

Phase 9 QA validates the final safety boundary before outbound email delivery.

The test suite must prove that:

- only explicitly approved draft revisions can send
- approval applies to exact immutable content
- edits invalidate approval
- suppressed recipients cannot send
- dry-run performs zero provider/network sends
- the same revision cannot be delivered twice
- simultaneous requests cannot duplicate delivery
- audit history is complete and append-only
- secrets never leak
- no LLM is involved
- normal test execution can never send a live email

Phase 9 QA is fully deterministic except for one optional live SMTP integration case,
which must remain disabled by default.

---

# 1. Test Architecture

Create:

```text
tests/golden/
├── phase9_sending.json
└── snapshots/
    └── sending/
        ├── p9_pending_draft.json
        ├── p9_approved_draft.json
        ├── p9_suppressed_recipient.json
        ├── p9_stale_approval.json
        ├── p9_batch_mixed.json
        └── expected/
            ├── review_state.json
            ├── approval_state.json
            ├── send_results.json
            ├── audit_events.json
            └── suppression_state.json

tests/acceptance/
└── test_phase9_acceptance.py

tests/reports/
└── phase9_latest.json
```

Create/extend:

```text
tests/test_sending.py
tests/acceptance/test_qa_integrity.py
```

Use temporary SQLite databases for all deterministic tests.

Never use the production runtime database in tests.

---

# 2. Core Test Environment Safety

Normal test execution:

```text
python -m pytest tests/
```

must ALWAYS use:

```text
EMAIL_SEND_ENABLED=false
RUN_LIVE_EMAIL_TESTS=false
```

and must never make a real SMTP network call.

Any test requiring a provider must inject:

```text
MockEmailSender
```

explicitly.

Production code must not auto-resolve to MockEmailSender.

---

# 3. Golden Input Draft

Create a frozen valid Phase 8 EmailDraft fixture.

Example:

```json
{
  "draft_id": "draft:domain:acme.com:email:jane@acme.com:outreach_v1:r1",
  "revision": 1,
  "lead_id": "domain:acme.com",
  "contact_id": "email:jane@acme.com",
  "recipient_name": "Jane Smith",
  "recipient_title": "Head of Digital Delivery",
  "recipient_email": "jane@acme.com",
  "subject": "Revit automation support",
  "body": "Hi Jane,\n\nI noticed...",
  "service_used": "Tech-Enabled BIM Services",
  "approval_status": "pending_review",
  "send_status": "not_sent",
  "draft_status": "generated",
  "prompt_version": "outreach_v1"
}
```

Golden fixtures must not contain real credentials.

---

# 4. Main Golden Case

## P9-REVIEW-001 — Import Pending Draft

Input:
one Phase 8 EmailDraft.

Expected:

```text
draft persisted
revision = 1
approval_status = pending_review
send_status = not_sent
outreach_status = draft_ready
zero provider calls
one imported audit event
```

Imported draft must not auto-approve or auto-send.

---

# 5. Draft Persistence Tests

## P9-STORE-001 — Persist Draft

Expected:
draft is retrievable with all Phase 8 identity fields unchanged.


## P9-STORE-002 — Preserve Lead ID

`lead_id` must survive byte/value equivalent.


## P9-STORE-003 — Preserve Contact ID

`contact_id` must survive unchanged.


## P9-STORE-004 — Preserve Recipient Email

No replacement, guessing, normalization-to-another-address, or fallback.


## P9-STORE-005 — No Sent-History Overwrite

Importing same base draft must not overwrite a sent revision.


## P9-STORE-006 — Temporary DB Isolation

Tests use isolated temp DB and leave no production DB mutation.

---

# 6. Human Review Queue Tests

## P9-QUEUE-001 — Pending Review Default

New imported drafts appear in pending-review queue.


## P9-QUEUE-002 — Filter by Approval Status

Exact deterministic filtering.


## P9-QUEUE-003 — Filter by Send Status


## P9-QUEUE-004 — Filter by Lead ID


## P9-QUEUE-005 — Filter by Contact ID


## P9-QUEUE-006 — Filter by Recipient Email


## P9-QUEUE-007 — Deterministic Pagination

Stable ordering under repeated queries.

Use explicit ordering:

```text
created_at ASC
draft_id ASC
revision ASC
```

or the production-defined equivalent.

---

# 7. Edit / Revision Tests

## P9-EDIT-001 — Edit Subject Creates New Revision

Revision 1 remains historical.

New revision:

```text
revision = 2
approval_status = pending_review
send_status = not_sent
approved_content_hash = null
```


## P9-EDIT-002 — Edit Body Creates New Revision


## P9-EDIT-003 — Edit Recipient Email Creates New Revision

Recipient change MUST invalidate approval.


## P9-EDIT-004 — Edit Sender-Relevant Identity Invalidates Approval

If sender identity is editable/configurable per draft contract.


## P9-EDIT-005 — Personalization Notes Only

Test declared policy.

If notes do not affect sent content, they may or may not invalidate approval,
but policy must be explicit and tested.


## P9-EDIT-006 — Sent Revision Immutable

Editing sent revision must create a new revision.

Never mutate historical sent content.


## P9-EDIT-007 — Revision Monotonicity

1 -> 2 -> 3

No duplicate revision numbers.

---

# 8. Approval Tests

## P9-APPROVE-001 — Explicit Approval

Approval request must contain correct current revision and reviewer.

Expected:

```text
approval_status = approved
outreach_status = approved
approved_at != null
approved_content_hash != null
```


## P9-APPROVE-002 — Approval Does Not Send

Provider calls:

```text
0
```

Send attempts:

```text
0
```


## P9-APPROVE-003 — Wrong Revision Cannot Approve

Attempt to approve obsolete revision.

Expected:
controlled error.


## P9-APPROVE-004 — Rejected Draft Cannot Be Approved Implicitly

Requires new explicit approval action according to state contract.


## P9-APPROVE-005 — Reviewer Required

Missing reviewer should fail if reviewer is mandatory in schema.


## P9-APPROVE-006 — Approval Event Recorded

Append-only audit event includes reviewer, revision, timestamp.

---

# 9. Approval Fingerprint Tests

## P9-HASH-001 — Stable Fingerprint

Same canonical content -> same SHA-256.


## P9-HASH-002 — Subject Change Changes Hash


## P9-HASH-003 — Body Change Changes Hash


## P9-HASH-004 — Recipient Change Changes Hash


## P9-HASH-005 — Sender Email Change Changes Hash


## P9-HASH-006 — Revision Change Changes Hash


## P9-HASH-007 — Hash Does Not Include Mutable Non-Send Metadata

If reviewer note or display-only metadata changes, hash policy must remain explicit.


## P9-HASH-008 — Approved Hash Stored Exactly

At approval:

```text
approved_content_hash == current content_hash
```

---

# 10. Critical Regression: Stale Approval

## P9-REG-001 — Stale Approval Blocked

Flow:

1. import draft
2. approve revision 1
3. mutate send-relevant content through valid edit flow
4. attempt to send using prior approval

Expected:

```text
send blocked
error_type = approval_stale
provider_calls = 0
```

No bypass allowed.

---

# 11. Unapproved Send Tests

## P9-REG-002 — Pending Review Cannot Send

Expected:

```text
not_approved
provider_calls = 0
```


## P9-SEND-003 — Rejected Cannot Send


## P9-SEND-004 — Changes Requested Cannot Send


## P9-SEND-005 — Missing Approval Hash Cannot Send

Even if approval_status was corrupted to approved manually.

Defense in depth:
approved status alone is insufficient.

---

# 12. Dry Run Tests

## P9-REG-003 — Dry Run Zero Network Calls

Approved valid draft.

Request:

```text
dry_run = true
```

Expected:

```text
status = dry_run
provider_calls = 0
network_calls = 0
send_status != sent
```


## P9-DRY-002 — Dry Run Still Checks Suppression


## P9-DRY-003 — Dry Run Still Checks Approval Hash


## P9-DRY-004 — Dry Run Still Checks Sender Match


## P9-DRY-005 — Dry Run Creates Audit Diagnostic

But does not create a successful delivery record.

---

# 13. Sending Disabled Tests

## P9-REG-004 — EMAIL_SEND_ENABLED=false Blocks Real Send

Approved draft.
dry_run = false.

Expected:

```text
sending_disabled
provider_calls = 0
```


## P9-CONFIG-001 — Missing SMTP Config

Controlled configuration error.

No provider network attempt.


## P9-CONFIG-002 — Request Body Cannot Supply SMTP Credentials

API must reject/ignore untrusted SMTP host/user/password fields.

Production configuration is server-owned.

---

# 14. Sender Identity Tests

## P9-SENDER-001 — Matching Sender Accepted

Approved sender email equals trusted configured sender.


## P9-SENDER-002 — Mismatch Blocked

Expected:

```text
sender_mismatch
provider_calls = 0
```


## P9-SENDER-003 — Trusted Alias

Only if alias support exists.

Unknown alias rejected.


## P9-SENDER-004 — From Header Cannot Be LLM/User Arbitrary

Provider adapter receives trusted configured sender.

---

# 15. Suppression Tests

## P9-REG-005 — Suppressed Recipient Cannot Send

Add recipient to suppression list.

Approved draft.

Expected:

```text
blocked
error_type = suppressed_recipient
provider_calls = 0
```


## P9-SUPPRESS-002 — Case-Insensitive Email Matching

`Jane@Acme.com` == `jane@acme.com` for suppression.


## P9-SUPPRESS-003 — Suppression Persists

New process/store instance using same test DB still blocks.


## P9-SUPPRESS-004 — Manual Suppression Audit Event


## P9-SUPPRESS-005 — Suppression Cannot Be Silently Removed

Removal requires explicit supported action if removal exists.

Otherwise immutable in Phase 9 v1.


## P9-SUPPRESS-006 — Opt-Out Reason Preserved

Reason/source retained for audit.

---

# 16. Idempotency Tests

## P9-REG-006 — Already Sent Cannot Resend

Send approved revision successfully once.

Second send request:

```text
already_sent
provider_calls second attempt = 0
```


## P9-IDEMP-002 — Same Draft Different Revision

Revision 2 is a distinct send identity only after fresh approval.


## P9-IDEMP-003 — Same Recipient Different Draft

Distinct approved draft may have distinct send_key according to contract.


## P9-IDEMP-004 — send_key Deterministic

Same draft/revision/recipient -> same send_key.


## P9-IDEMP-005 — No force=true Bypass

API must not expose generic forced duplicate send.

---

# 17. Concurrency Tests

## P9-REG-007 — Concurrent Duplicate Send Prevented

Use two concurrent threads/tasks against SAME approved draft revision.

Inject thread-safe mock sender.

Expected:

```text
provider delivery count = 1
```

One request may return sent.

Other must return:

```text
already_sent
```

or controlled in-progress/conflict status.

Never 2 provider deliveries.


## P9-CONC-002 — Unique send_key Constraint

Database enforces uniqueness.


## P9-CONC-003 — Atomic State Transition

No invalid state such as:

```text
sent with no successful send_attempt
```


## P9-CONC-004 — Concurrent Different Drafts Allowed

Distinct drafts can send independently within rate limit.

---

# 18. Provider Success Tests

## P9-SMTP-001 — Explicit Mock Sender Success

Only via dependency injection.

Expected:

```text
send_status = sent
outreach_status = sent
sent_at != null
provider_message_id preserved
```


## P9-SMTP-002 — Production Does Not Auto-Mock

No SMTP provider configured.

Expected:
configuration error / sending disabled.

Never fake success.


## P9-SMTP-003 — Text/Plain Payload

Verify recipient, sender, subject, body passed exactly.

No attachment.

No tracking pixel.

No silent link rewriting.

---

# 19. Provider Error Mapping

## P9-SMTP-ERR-001 — Authentication Failure

Expected:

```text
smtp_auth_error
```


## P9-SMTP-ERR-002 — Connection Failure

Expected:

```text
smtp_connection_error
```


## P9-SMTP-ERR-003 — Timeout

Expected:

```text
smtp_timeout
```


## P9-SMTP-ERR-004 — Generic Provider Error

Expected:

```text
provider_error
```


## P9-SMTP-ERR-005 — Error Sanitization

Provider exception includes fake password/token.

Expected:
secret removed/redacted in API/audit logs.

---

# 20. Failure State Tests

## P9-FAIL-001 — Failed Send Not Marked Sent


## P9-FAIL-002 — Failed Attempt Recorded


## P9-FAIL-003 — No Automatic Retry

Single explicit send request -> at most one provider invocation.


## P9-FAIL-004 — Explicit Later Retry

If retry is allowed after failure:
new explicit request creates new attempt.

No hidden background retry.


## P9-FAIL-005 — Ambiguous Provider Failure

Must not create duplicate automatic delivery attempt.

---

# 21. Rate Limit Tests

## P9-RATE-001 — Within Per-Minute Limit

Allowed.


## P9-RATE-002 — Exceed Per-Minute Limit

Expected:

```text
rate_limit_exceeded
provider_calls = 0 for blocked request
```


## P9-RATE-003 — Batch Max Boundary

Exactly `MAX_SENDS_PER_REQUEST` accepted.


## P9-RATE-004 — Batch Max + 1 Rejected

No silent truncation.


## P9-RATE-005 — Rate Limit Deterministic Clock

Inject/test clock if possible.

Avoid sleep-based flaky tests.

---

# 22. Batch Send Tests

## P9-BATCH-001 — Mixed Batch Isolation

Draft A:
approved, valid -> sent/mock success

Draft B:
suppressed -> blocked

Draft C:
unapproved -> blocked

Draft D:
provider error -> failed

Expected exact counters.

Successful A remains sent.


## P9-BATCH-002 — Explicit Draft IDs Required

No `send_all_approved` behavior.


## P9-BATCH-003 — Duplicate Draft IDs in Request

Must not trigger duplicate provider calls.


## P9-BATCH-004 — Missing Draft ID

One missing record must not abort valid others.


## P9-BATCH-005 — Stable Result Ordering

Results align deterministically with requested IDs or explicit documented ordering.

---

# 23. Audit Trail Tests

## P9-AUDIT-001 — Imported Event


## P9-AUDIT-002 — Edited Event


## P9-AUDIT-003 — Approved Event


## P9-AUDIT-004 — Rejected Event


## P9-AUDIT-005 — Changes Requested Event


## P9-AUDIT-006 — Suppressed Event


## P9-AUDIT-007 — Send Requested Event


## P9-AUDIT-008 — Sent Event


## P9-AUDIT-009 — Failed Event


## P9-AUDIT-010 — Append-Only

Creating later events never overwrites prior events.


## P9-AUDIT-011 — Reviewer Preserved


## P9-AUDIT-012 — No Secrets In Audit

---

# 24. Send Attempt Tests

## P9-ATTEMPT-001 — Success Attempt Record

Contains:

```text
attempt_id
send_key
draft_id
revision
provider
recipient_email
sender_email
status
provider_message_id
attempted_at
completed_at
```


## P9-ATTEMPT-002 — Failure Attempt Record


## P9-ATTEMPT-003 — Dry Run Is Not Successful Provider Attempt

If a dry-run audit row exists, clearly classify it as dry_run.


## P9-ATTEMPT-004 — No Credentials Persisted

No SMTP password/token fields in DB schema or row values.

---

# 25. State Machine Tests

Allowed transitions should be explicit.

Example:

```text
pending_review -> approved
pending_review -> rejected
pending_review -> changes_requested

approved -> sending
sending -> sent
sending -> failed

approved -> pending_review
only through content-changing new revision
```

## P9-STATE-001 — Invalid Transition Rejected

Examples:

```text
rejected -> sent
pending_review -> sent
sent -> sending
```


## P9-STATE-002 — Sent State Terminal For Revision


## P9-STATE-003 — New Revision Reopens Review


## P9-STATE-004 — Suppressed Recipient Blocks Regardless of Approval

---

# 26. Phase 8 Validation Reuse

## P9-P8-001 — Placeholder Rejected After Human Edit

Human changes body to contain:

```text
{{first_name}}
```

Expected:
approval/send blocked according to reuse policy.


## P9-P8-002 — Empty Subject Rejected


## P9-P8-003 — Oversized Subject/Body Rejected

According to frozen Phase 8 hard rules unless Phase 9 explicitly defines a different
human-edit policy.


## P9-P8-004 — Recipient Identity Revalidated


## P9-P8-005 — No Phase 8 LLM Invocation

Reuse deterministic validator only.

---

# 27. Phase 7 Projection Tests

## P9-P7-001 — Approved Projection

Expected:

```text
approval_status = approved
outreach_status = approved
send_status = not_sent
```


## P9-P7-002 — Sent Projection

Expected:

```text
approval_status = approved
outreach_status = sent
send_status = sent
last_outreach_at = sent_at
```


## P9-P7-003 — Failed Projection

Expected:

```text
outreach_status = send_failed
send_status = failed
```


## P9-P7-004 — Suppressed Projection

Expected:

```text
outreach_status = do_not_contact
send_status = blocked
```

---

# 28. Security Tests

## P9-SEC-001 — SMTP Password Never Returned


## P9-SEC-002 — SMTP Password Never Logged


## P9-SEC-003 — OAuth/API Secrets Never Stored In DB


## P9-SEC-004 — API Cannot Override SMTP Host


## P9-SEC-005 — API Cannot Override SMTP Password


## P9-SEC-006 — Provider Exception Redaction


## P9-SEC-007 — SQL Parameterization

Malicious reviewer/note/subject input must not alter DB schema/query behavior.


## P9-SEC-008 — Header Injection Protection

Reject CR/LF injection in:

```text
recipient email
subject
sender name/email
```


## P9-SEC-009 — Invalid Recipient Email

Controlled invalid_recipient.


## P9-SEC-010 — No Personal/Hidden Recipient BCC

Phase 9 v1 must not silently add BCC/CC unless explicitly designed.

---

# 29. No LLM Tests

## P9-NOLLM-001 — Sending Module Has Zero LLM Imports

Static/code-path audit.


## P9-NOLLM-002 — Approve Does Not Call LLM


## P9-NOLLM-003 — Edit Does Not Call LLM


## P9-NOLLM-004 — Send Does Not Call LLM

Phase 9 is deterministic workflow/provider logic only.

---

# 30. Database Tests

## P9-DB-001 — Tables Initialize Cleanly


## P9-DB-002 — Reinitialization Idempotent


## P9-DB-003 — Unique Draft Revision Constraint


## P9-DB-004 — Unique send_key Constraint


## P9-DB-005 — Foreign/Logical References Preserved


## P9-DB-006 — Transaction Rollback On Internal Failure

No half-written state.


## P9-DB-007 — Production DB Not Packaged

Release ZIP must not contain:

```text
data/sales_outreach.db
```


## P9-DB-008 — Runtime DB Directory Created Safely

No path traversal from API input.

---

# 31. Packaging / Secret Regression

## P9-PACK-001 — .env Excluded

Release ZIP contains no:

```text
.env
.env.*
```


## P9-PACK-002 — Runtime DB Excluded


## P9-PACK-003 — Chroma Runtime DB Excluded


## P9-PACK-004 — Private Key Types Excluded

```text
*.pem
*.key
*.p12
*.pfx
```


## P9-PACK-005 — Source/Test Files Still Included

Security filtering must not accidentally remove required source/spec/test files.

---

# 32. Opt-Out Footer Tests

Only if configured.

## P9-OPTOUT-001 — Server-Owned Footer Appended

Exact configured text.


## P9-OPTOUT-002 — LLM Cannot Control Footer

Phase 9 has no LLM.


## P9-OPTOUT-003 — Footer Included In Approval Hash

If footer is part of actual sent body, it MUST be part of approved content fingerprint
or approval must be computed over the final send body.

Never send unapproved appended content.

This is critical.

---

# 33. Final-Body Fingerprint Rule

The exact bytes/normalized text to be sent must be fingerprinted.

If deterministic send-time transformations occur, such as:

- opt-out footer append
- line-ending normalization
- sender signature normalization

then approval must apply to the final normalized send payload.

Preferred architecture:

```text
prepare_final_send_payload()
→ compute hash
→ human approves that payload
→ send exact same payload
```

Do not approve one body and send a modified body.

---

# 34. API Acceptance Tests

Test:

```text
POST /api/sales/drafts/import

GET /api/sales/drafts

GET /api/sales/drafts/{draft_id}

POST /api/sales/drafts/{draft_id}/edit

POST /api/sales/drafts/{draft_id}/approve

POST /api/sales/drafts/{draft_id}/reject

POST /api/sales/drafts/{draft_id}/request-changes

POST /api/sales/drafts/{draft_id}/suppress

POST /api/sales/drafts/{draft_id}/send

POST /api/sales/send-batch
```

## P9-API-001 — Import Success

## P9-API-002 — Invalid Draft Validation

## P9-API-003 — Review List

## P9-API-004 — Edit Creates Revision

## P9-API-005 — Approve

## P9-API-006 — Approval Does Not Send

## P9-API-007 — Send Dry Run

## P9-API-008 — Real Send Disabled

## P9-API-009 — Suppression Blocks

## P9-API-010 — Batch Mixed Response

## P9-API-011 — Invalid Revision

## P9-API-012 — Missing Draft

---

# 35. Mandatory Critical Regressions

All MUST PASS before Phase 9 deterministic freeze:

```text
P9-REG-001 stale approval blocked
P9-REG-002 unapproved send blocked
P9-REG-003 dry-run zero network
P9-REG-004 sending disabled blocks real network
P9-REG-005 suppression cannot be bypassed
P9-REG-006 already-sent revision cannot resend
P9-REG-007 concurrent duplicate delivery prevented
P9-REG-008 approval itself never sends
P9-REG-009 recipient edit invalidates approval
P9-REG-010 sender mismatch blocks provider call
P9-REG-011 provider secrets sanitized
P9-REG-012 final sent payload hash equals approved payload hash
```

---

# 36. Live SMTP Test

Prepare:

```text
P9-LIVE-SMTP-001
```

Default status:

```text
NOT_RUN
```

It may run only when ALL conditions are true:

```text
RUN_LIVE_EMAIL_TESTS=true
EMAIL_SEND_ENABLED=true
SMTP credentials configured
LIVE_EMAIL_TEST_RECIPIENT configured
explicit live-test marker selected
```

Normal full test suite MUST NOT satisfy these conditions accidentally.

Recommended pytest marker:

```text
@pytest.mark.live_email
```

Default pytest config excludes or skips it.

Never use a real prospect as the live-test recipient.

---

# 37. Live-Test Safety Regression

## P9-LIVE-SAFE-001

Run normal:

```text
pytest tests/
```

with fake SMTP environment.

Expected:

```text
live provider sends = 0
```

Prove by monkeypatch/network guard that no socket/SMTP connection is attempted.

---

# 38. QA Reporting Integrity

Phase 9 must use the same execution-driven QA model established in Phase 8.

Golden catalog presence is NOT execution evidence.

Every deterministic case must map:

```text
case_id -> pytest node/evaluator
```

Report status comes only from actual execution.

No automatic PASS.

No copying Expected into Actual.

---

# 39. Required Phase 9 Report Fields

`tests/reports/phase9_latest.json`:

```text
phase
run_timestamp
git_commit

total_catalog_cases
mapped_executable_cases
executed_cases

passed
failed
review
not_run
deferred_model

unmapped_deterministic_cases
false_pass_count

live_smtp_status
live_smtp_executed

critical_regressions

provider_calls_in_dry_run
provider_calls_for_unapproved
duplicate_send_count

suppression_bypass_count
stale_approval_bypass_count

secrets_detected
llm_calls_detected
```

Expected deterministic freeze metrics:

```text
unmapped_deterministic_cases = 0
false_pass_count = 0
provider_calls_in_dry_run = 0
provider_calls_for_unapproved = 0
duplicate_send_count = 0
suppression_bypass_count = 0
stale_approval_bypass_count = 0
secrets_detected = 0
llm_calls_detected = 0
```

---

# 40. README-TEST Phase 9 Section

Include:

- execution mode
- DB mode: temporary SQLite
- sender mode: mock / disabled / live
- EMAIL_SEND_ENABLED
- RUN_LIVE_EMAIL_TESTS
- provider call counts
- critical regression table
- approval hash diagnostics
- duplicate-send diagnostics
- suppression diagnostics
- secrets diagnostics
- Expected
- Actual
- Differences
- Result

Never expose secret values.

---

# 41. QA Meta-Tests

Extend QA integrity tests to prove:

## P9-QA-001
Unmapped deterministic case cannot PASS.

## P9-QA-002
Empty `pass` test cannot count as real coverage.

## P9-QA-003
Skipped live SMTP becomes NOT_RUN.

## P9-QA-004
Failed node maps to FAIL.

## P9-QA-005
Expected and Actual independently sourced.

## P9-QA-006
false_pass_count computed dynamically.

## P9-QA-007
A mock provider success is labeled mock, not live SMTP success.

## P9-QA-008
A live-send case cannot run without explicit live flag.

---

# 42. Critical FAIL Conditions

Immediate FAIL if any of these occur:

- unapproved draft sends
- approval action sends
- stale approval sends
- changed recipient sends without new approval
- suppressed recipient sends
- dry-run causes SMTP/network activity
- EMAIL_SEND_ENABLED=false still sends
- sent revision sends twice
- concurrent same-revision requests produce two provider deliveries
- mock sender is auto-selected in production
- wrong sender identity is used
- provider secret appears in API/log/report/DB
- runtime DB or .env appears in release ZIP
- sent revision changes in place
- audit history is overwritten
- provider failure is marked sent
- automatic retry causes extra provider call
- Phase 9 calls LLM
- approval hash is computed over content different from actual send payload
- opt-out/footer is appended after approval without being fingerprinted

---

# 43. Suggested Coverage

Target approximately:

```text
Storage / queue:            13
Edit / revision:             7
Approval:                    6
Fingerprint:                 8
Critical send gates:        12
Dry run/config:              7
Sender:                      4
Suppression:                 6
Idempotency:                 5
Concurrency:                 4
Provider success/errors:     8
Failure/retry:               5
Rate limit:                  5
Batch:                       5
Audit:                      12
Attempts:                    4
State machine:               4
Phase 8 reuse:               5
Phase 7 projection:          4
Security:                   10
No LLM:                      4
Database:                    8
Packaging:                   5
Opt-out/final hash:          4
API:                        12
QA integrity:                8
Live SMTP:                   2
```

Parametrize where useful.

Do not inflate counts for appearance.

Each PASS must correspond to a real invariant.

---

# 44. Definition of Phase 9 Deterministic QA Complete

Phase 9 may be frozen only when:

1. Imported drafts remain pending review.
2. Approval is explicit and separate from send.
3. Approval fingerprint protects exact send content.
4. Any send-relevant edit invalidates approval.
5. Unapproved/rejected/changes-requested states cannot send.
6. Dry-run has zero provider/network calls.
7. EMAIL_SEND_ENABLED defaults false and blocks real sends.
8. Suppression cannot be bypassed.
9. Already-sent revision cannot resend.
10. Concurrent duplicate delivery is prevented.
11. Sender identity is trusted/config-bound.
12. Provider failures are classified and audited.
13. No automatic retry occurs.
14. Batch partial failures are isolated.
15. Audit history is append-only.
16. Send attempts are persisted without secrets.
17. Sent revisions are immutable.
18. Phase 8 deterministic validation is reused after human edits.
19. Phase 7 workflow projection is correct.
20. No LLM is used.
21. Runtime DB and secrets are excluded from ZIP.
22. Mandatory P9-REG-001..012 all PASS.
23. Every deterministic Golden case maps to executed QA.
24. false_pass_count = 0.
25. unmapped deterministic cases = 0.
26. live SMTP remains NOT_RUN unless explicitly enabled.
27. normal repository test run performs zero live sends.
