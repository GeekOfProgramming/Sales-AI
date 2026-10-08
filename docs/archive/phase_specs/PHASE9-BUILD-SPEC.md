# SalesAI Phase 9 Build Spec
## Human Review, Approval, Safe Sending & Audit Trail

# Phase Goal

Phase 9 takes Phase 8 `EmailDraft` objects and provides a controlled human-review
workflow followed by explicit, auditable email sending.

Phase 9 must NOT:
- discover leads
- score leads
- enrich companies or contacts
- re-rank contacts
- generate email copy with an LLM
- auto-approve drafts
- auto-send drafts
- silently resend already-sent drafts
- change recipient identity without invalidating approval

Human approval is mandatory before every first send of a specific draft revision.

---

# 1. High-Level Flow

```text
Phase 8 EmailDraft
      ↓
Persist Draft
      ↓
Human Review Queue
      ↓
Edit / Reject / Approve / Do Not Contact
      ↓
Approved Content Fingerprint
      ↓
Pre-Send Validation
      ↓
Explicit Send Request
      ↓
Email Provider Adapter
      ↓
Sent / Failed / Blocked
      ↓
Immutable Audit Trail
```

No LLM is required in Phase 9.

---

# 2. Input Contract

Primary input:

```text
EmailDraft
```

from Phase 8.

Required identity fields:

```text
draft_id
revision
lead_id
contact_id
recipient_name
recipient_email
subject
body
approval_status
send_status
prompt_version
```

Phase 9 must preserve upstream:

```text
lead_id
contact_id
recipient_email
```

unless a human explicitly edits recipient data.

If recipient identity is edited:
- increment revision
- invalidate approval
- require fresh human approval

---

# 3. State Model

Use explicit states.

## Review Status

```text
pending_review
approved
rejected
changes_requested
```

## Send Status

```text
not_sent
sending
sent
failed
blocked
dry_run
```

## Outreach Status

```text
draft_ready
approved
sent
send_failed
do_not_contact
```

Never infer approval from send status.

---

# 4. Persistence

Use SQLite for the first production implementation.

Recommended database:

```text
data/sales_outreach.db
```

Use standard `sqlite3` unless an existing database abstraction already exists.

Enable safe transactional behavior.

Recommended tables:

```text
drafts
review_events
send_attempts
suppression_list
```

Do not store:
- API passwords
- OAuth refresh tokens
- SMTP passwords

Secrets stay in environment/configuration only.

---

# 5. Draft Persistence

Persist Phase 8 drafts.

Recommended fields:

```text
draft_id
revision

lead_id
contact_id

recipient_name
recipient_title
recipient_email

subject
body

service_used
personalization_notes
evidence_refs
source_job_urls

language
tone
prompt_version
generation_model

approval_status
send_status
outreach_status

content_hash
approved_content_hash

created_at
updated_at
approved_at
sent_at
```

Do not silently overwrite historical approved/sent revisions.

---

# 6. Human Review Queue

Create deterministic review queue functionality.

Support:

```text
pending_review
approved
rejected
changes_requested
sent
failed
```

Default queue:

```text
pending_review
```

Useful filters:

```text
company
recipient
approval_status
send_status
lead_id
contact_id
```

No LLM ranking.

---

# 7. Human Edit Behavior

Allow human edit of:

```text
subject
body
personalization_notes
recipient_name
recipient_email
```

If ANY send-relevant content changes:

```text
recipient_email
subject
body
sender identity
```

then:

```text
revision += 1
approval_status = pending_review
send_status = not_sent
approved_content_hash = null
approved_at = null
```

Previous revision remains in audit/history.

Never edit a sent revision in place.

---

# 8. Approval Fingerprint

This is mandatory.

Build a deterministic content fingerprint.

Recommended canonical fields:

```text
draft_id base identity
revision
lead_id
contact_id
recipient_email
sender_email
subject
body
```

Canonicalize safely and compute:

```text
SHA-256
```

Store as:

```text
content_hash
```

At approval time:

```text
approved_content_hash = content_hash
```

Immediately before sending:
recalculate hash.

If:

```text
current_content_hash != approved_content_hash
```

block sending:

```text
error_type = approval_stale
```

This prevents sending edited content under an old approval.

---

# 9. Approval Rules

Approval must be an explicit human action.

Endpoint must receive an explicit approval request.

On approval:

```text
approval_status = approved
outreach_status = approved
approved_at = now
approved_content_hash = current content hash
```

Do not send automatically after approval.

Approval and sending are two separate actions.

---

# 10. Reject / Changes Requested

Reject:

```text
approval_status = rejected
send_status = not_sent
```

Optional reviewer note is allowed.

Changes requested:

```text
approval_status = changes_requested
send_status = not_sent
```

Neither state may be sent.

---

# 11. Do-Not-Contact / Suppression

Create a local suppression list.

At minimum support:

```text
email
```

Optional later:

```text
company_domain
```

Recommended fields:

```text
suppression_id
email
company_domain
reason
source
created_at
```

Example reasons:

```text
manual
opt_out
bounce
complaint
invalid_recipient
```

Before every send:
check suppression list.

If suppressed:

```text
send_status = blocked
error_type = suppressed_recipient
```

Never silently remove suppression.

---

# 12. Email Sender Abstraction

Create:

```text
BaseEmailSender
```

Recommended adapters:

```text
MockEmailSender
SMTPEmailSender
```

Optional future:

```text
GmailEmailSender
MicrosoftGraphEmailSender
```

Do NOT fake those future adapters.

Mock sender may be used only by explicit dependency injection in tests.

Production code must never automatically fall back to MockEmailSender.

---

# 13. SMTP Sender

Implement SMTP as the first real provider.

Environment/config:

```text
EMAIL_SEND_ENABLED=false

SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD

SMTP_USE_TLS=true

SMTP_FROM_EMAIL
SMTP_FROM_NAME
```

Default:

```text
EMAIL_SEND_ENABLED=false
```

Real email sending is impossible until explicitly enabled.

Never log:

```text
SMTP_PASSWORD
Authorization headers
credentials
```

---

# 14. Sender Identity Validation

The actual sending account must match the approved sender identity.

Before sending verify:

```text
approved sender email
==
configured SMTP_FROM_EMAIL
```

or an explicitly permitted alias.

Do not allow arbitrary model/user-supplied From addresses.

---

# 15. Dry Run

Support:

```text
dry_run = true
```

Dry run performs ALL checks but sends no network email.

Return:

```text
send_status = dry_run
```

with validation diagnostics.

Dry run must not mark the draft as sent.

Recommended API default:

```text
dry_run = true
```

for development/testing.

---

# 16. Pre-Send Validator

Create:

```text
send_validator.py
```

Before provider invocation verify:

- draft exists
- revision exists
- approval_status == approved
- send_status != sent
- recipient_email exists
- recipient email equals approved revision
- recipient is not suppressed
- approved_content_hash exists
- content hash still matches approval
- sender identity matches config
- subject exists
- body exists
- no unresolved placeholders
- Phase 8 hard validation still passes where reusable
- EMAIL_SEND_ENABLED == true unless dry_run
- no duplicate successful send exists for this draft revision

Any failed rule blocks provider call.

---

# 17. Idempotent Sending

A specific approved draft revision must not be sent twice accidentally.

Create deterministic send identity:

```text
send_key =
draft_id + revision + recipient_email
```

or hashed equivalent.

Once a successful send exists for the same send_key:

future send request must return:

```text
already_sent
```

without provider invocation.

Do not provide a generic `force=true` bypass.

If a genuine resend is needed:
create a new revision or future follow-up draft.

---

# 18. Concurrency Safety

Two simultaneous requests must not send the same draft twice.

Use transactional locking / unique database constraint on:

```text
send_key
```

Recommended:

```text
UNIQUE(send_key)
```

Transition atomically:

```text
approved/not_sent
→ sending
→ sent
```

or:

```text
sending
→ failed
```

---

# 19. Send Attempt Audit

Every provider attempt must create an auditable record.

Recommended fields:

```text
attempt_id
send_key
draft_id
revision

provider
recipient_email
sender_email

status
error_type
error_message

provider_message_id

attempted_at
completed_at
```

Do not store provider secrets.

---

# 20. Immutable Review Audit

Every review action creates an event:

```text
event_id
draft_id
revision
action
previous_status
new_status
reviewer
review_note
created_at
```

Actions:

```text
imported
edited
approved
rejected
changes_requested
suppressed
send_requested
sent
send_failed
```

Do not overwrite event history.

---

# 21. Reviewer Identity

For current local implementation:

allow an explicit reviewer label:

```text
reviewer
```

Example:

```text
"hamid"
```

Do not build full authentication/RBAC in this phase unless an existing auth system
already supports it cleanly.

Do not hardcode reviewer name.

---

# 22. Email Format

First implementation:

```text
text/plain
```

Optional simple HTML may be added only if deterministic and safe.

No attachments in Phase 9 v1.

No tracking pixels in Phase 9 v1.

No automatic link tracking.

Keep outbound email simple and auditable.

---

# 23. Opt-Out Text

Support optional server-owned configuration:

```text
OUTREACH_OPT_OUT_TEXT
```

Example behavior:

append a short opt-out sentence if configured.

Do not let LLM generate or control the opt-out policy.

Do not claim this feature by itself guarantees regulatory compliance.

---

# 24. Rate Limits

Add conservative deterministic sending limits.

Recommended defaults:

```text
MAX_SENDS_PER_REQUEST = 20
MAX_SENDS_PER_MINUTE = 5
```

Make configurable.

Do not silently queue thousands of messages.

If limit exceeded:

```text
rate_limit_exceeded
```

No provider call.

---

# 25. Batch Send

Support batch sending only for explicitly supplied draft IDs.

Do NOT implement:

```text
send_all_approved
```

without IDs.

Recommended request:

```json
{
  "draft_ids": [
    "draft:..."
  ],
  "dry_run": true
}
```

Each draft validated independently.

Partial failure allowed.

Return:

```text
sent
failed
blocked
already_sent
dry_run
```

per draft.

---

# 26. Failure Isolation

If Draft A sends successfully and Draft B fails:

Draft A remains sent.

Do not roll back successful provider delivery.

Draft B records failed attempt and remains auditable.

---

# 27. Provider Error Taxonomy

Use explicit errors:

```text
sending_disabled
not_approved
approval_stale
already_sent
suppressed_recipient
missing_recipient
invalid_recipient
sender_mismatch
smtp_auth_error
smtp_connection_error
smtp_timeout
provider_error
rate_limit_exceeded
validation_failed
```

Do not collapse all provider errors into one generic failure.

---

# 28. Retry Policy

Do NOT auto-retry sending in Phase 9 v1.

Reason:
automatic retries can duplicate delivery if provider state is ambiguous.

After failure:
human/system may explicitly request another send attempt.

Before retry:
idempotency/provider-message state must be checked.

Automatic retry may be designed later.

---

# 29. API Endpoints

Recommended endpoints:

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

Do not create an endpoint that sends every approved record automatically.

---

# 30. Import Endpoint

`POST /api/sales/drafts/import`

Input:

```text
EmailDraft[]
```

Behavior:

- preserve draft_id
- preserve revision
- do not approve
- do not send
- upsert only if safe
- never overwrite sent history

Imported generated draft:

```text
approval_status = pending_review
send_status = not_sent
outreach_status = draft_ready
```

---

# 31. Review Queue Endpoint

`GET /api/sales/drafts`

Support filters:

```text
approval_status
send_status
lead_id
contact_id
recipient_email
limit
offset
```

Default ordering:

```text
created_at ASC
draft_id ASC
```

or another explicitly deterministic ordering.

---

# 32. Edit Endpoint

`POST /api/sales/drafts/{draft_id}/edit`

Input example:

```json
{
  "subject": "...",
  "body": "...",
  "reviewer": "..."
}
```

Result:
- new revision
- pending_review
- not_sent
- content hash recalculated
- audit event added

Do not mutate old sent/approved revision in place.

---

# 33. Approve Endpoint

`POST /api/sales/drafts/{draft_id}/approve`

Input:

```json
{
  "revision": 2,
  "reviewer": "...",
  "note": "Approved for send"
}
```

Required:
revision must match current review target.

Store approval fingerprint.

No email is sent.

---

# 34. Send Endpoint

`POST /api/sales/drafts/{draft_id}/send`

Example:

```json
{
  "revision": 2,
  "dry_run": true
}
```

Behavior:

1. load exact draft revision
2. validate
3. check approval hash
4. check suppression
5. check idempotency
6. check rate limits
7. invoke provider only if dry_run=false and sending enabled
8. record attempt
9. update state

---

# 35. Batch API Response

Example:

```json
{
  "requested": 3,
  "sent": 1,
  "dry_run": 1,
  "blocked": 1,
  "failed": 0,
  "results": [
    {
      "draft_id": "...",
      "revision": 1,
      "status": "sent",
      "provider_message_id": "..."
    }
  ]
}
```

---

# 36. Phase 7 Integration

Phase 9 should expose deterministic workflow state suitable for Phase 7 export:

```text
approval_status
outreach_status
send_status
last_outreach_at
```

Do not require rewriting Excel files to send.

Structured database state is source of truth.

Exports can be regenerated later.

---

# 37. Phase 8 Integration

Reuse Phase 8 validation logic where safe.

Do NOT duplicate:

- placeholder rules
- recipient identity checks
- subject/body hard limits

Phase 9 must revalidate edited drafts before approval/send.

Human editing does not bypass safety invariants.

---

# 38. Security

Never expose in API responses/logs:

```text
SMTP_PASSWORD
OAuth token
API key
Authorization header
```

Sanitize provider exceptions.

Do not allow arbitrary SMTP host/user/password in request body.

Provider configuration comes from trusted server configuration only.

---

# 39. No LLM in Phase 9

Phase 9 must have zero LLM calls.

Static/code-path test should verify sending/review modules do not import/call the LLM client.

Email content editing in Phase 9 is human input only.

---

# 40. Files to Create

Suggested structure:

```text
sales_engine/sending/
├── __init__.py
├── schemas.py
├── review_store.py
├── approval_service.py
├── suppression_store.py
├── send_validator.py
├── base_sender.py
├── mock_sender.py
├── smtp_sender.py
├── audit_service.py
├── rate_limiter.py
└── send_orchestrator.py
```

If an existing storage/provider abstraction fits cleanly, reuse it.

---

# 41. Recommended Schemas

Create models such as:

```text
StoredDraft
DraftRevision
ReviewEvent
SuppressionEntry
SendAttempt
SendResult

DraftImportRequest
DraftEditRequest
DraftApprovalRequest
DraftRejectRequest
DraftSendRequest
BatchSendRequest
```

Use Pydantic validation.

---

# 42. Critical Tests

Add unit/acceptance tests for at least:

- import draft
- pending review default
- human edit creates new revision
- edit invalidates prior approval
- approval stores content fingerprint
- approval does NOT send
- stale approval blocks send
- rejected draft cannot send
- changes_requested cannot send
- suppressed email cannot send
- sending disabled blocks real send
- dry run performs zero provider network calls
- exact approved recipient preserved
- sender config mismatch blocks
- already-sent draft does not resend
- concurrent duplicate send prevented
- SMTP mock success
- SMTP auth failure
- SMTP timeout
- partial batch failure
- rate limit
- audit event creation
- send attempt logging
- secrets sanitized
- no auto retry
- no LLM calls
- sent revision immutable
- Phase 7 workflow projection
- Phase 8 validator reused after human edit

---

# 43. Golden Cases Preparation

Prepare Phase 9 Golden cases such as:

```text
P9-REVIEW-001 Import Pending Draft
P9-REVIEW-002 Edit Invalidates Approval
P9-APPROVE-001 Explicit Approval
P9-APPROVE-002 Approval Does Not Send
P9-SEND-001 Approved Dry Run
P9-SEND-002 Approved SMTP Mock Send
P9-SEND-003 Unapproved Blocked
P9-SEND-004 Stale Approval Blocked
P9-SEND-005 Already Sent Blocked
P9-SUPPRESS-001 Suppressed Recipient Blocked
P9-IDEMP-001 Concurrent Duplicate Prevented
P9-BATCH-001 Partial Failure Isolation
P9-SEC-001 Secrets Never Logged
P9-WF-001 Sent State Projection
P9-NOLLM-001 Zero LLM Code Path
```

Do not require a live SMTP account for deterministic QA.

Live SMTP test must remain optional / NOT_RUN until explicitly configured.

---

# 44. Live Send Safety

Create a separate optional live test:

```text
P9-LIVE-SMTP-001
```

Requirements before it can run:

```text
EMAIL_SEND_ENABLED=true
SMTP credentials configured
explicit test recipient configured
explicit live-test flag enabled
```

Never send a live email merely because normal tests are running.

Recommended separate flag:

```text
RUN_LIVE_EMAIL_TESTS=false
```

Default false.

---

# 45. Critical Fail Conditions

Immediate FAIL if:

- unapproved draft can send
- approval triggers automatic send
- edited content sends using old approval
- recipient changes after approval without reapproval
- suppressed recipient sends
- sent draft can be silently resent
- simultaneous requests can duplicate delivery
- mock sender activates automatically in production
- sending works while EMAIL_SEND_ENABLED=false
- dry_run sends network email
- provider secrets leak
- arbitrary SMTP credentials accepted from API request
- sent revision is silently overwritten
- audit history disappears
- Phase 9 invokes LLM
- batch failure corrupts successful send history

---

# 46. Definition of Done

Phase 9 functional implementation is complete when:

1. Phase 8 drafts can be persisted.
2. Human review queue works.
3. Human edits create revisions.
4. Approval is explicit.
5. Approval fingerprint is stored.
6. Any post-approval content change invalidates approval.
7. Sending is a separate explicit action.
8. EMAIL_SEND_ENABLED defaults false.
9. Dry run is supported.
10. SMTP adapter works behind provider abstraction.
11. Mock sender is test-only.
12. Suppression list blocks sends.
13. Idempotency prevents duplicate delivery.
14. Concurrent duplicate sends are prevented.
15. Rate limiting exists.
16. Send failures are isolated.
17. Audit trail is persistent.
18. Secrets are sanitized.
19. No LLM exists in send path.
20. Deterministic tests pass.
21. Live SMTP remains opt-in.
