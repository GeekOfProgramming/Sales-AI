# SalesAI Phase 11 Build Specification
## Inbox / Reply Intelligence & Human-Reviewed Conversation Workflow

# 0. Baseline

Frozen / accepted baseline:

```text
Phase 9   — outbound review / approval / suppression / send authority
Phase 10  — discovery / company qualification
Phase 10.5 — local operator console
```

Phase 11 adds inbound email and reply intelligence.

Do NOT redesign frozen Phase 1–10.5 behavior.

Current local LLM is weak:

```text
qwen2.5:1.5b
```

Therefore Phase 11 must be deterministic-first.

LLM semantic classification may assist, but deterministic identity, threading,
suppression, conversation state and send safety must not depend solely on the LLM.

---

# 1. Primary Goal

Phase 11 must support:

```text
Outbound message
→ inbound reply
→ thread / conversation
→ reply classification
→ actual respondent resolution
→ conversation state
→ suggested operator response
→ human review
→ explicit reply send
→ meeting / opportunity state
```

No autonomous reply sending.

No automatic sales commitments.

No automatic meeting booking.

---

# 2. Provider Architecture

Create an inbox-provider abstraction.

Recommended:

```python
BaseMailboxProvider
MockMailboxProvider
GmailMailboxProvider
MicrosoftGraphMailboxProvider
```

Optional fallback:

```python
IMAPMailboxProvider
```

Provider support may be implemented incrementally.

Production preference:

```text
Gmail API + OAuth
Microsoft Graph + OAuth
```

Do NOT require mailbox credentials for deterministic tests.

Mock provider is mandatory for QA.

---

# 3. No Password / Secret Leakage

Never store mailbox credentials in:

```text
SQLite rows
candidate evidence
logs
reports
release ZIP
HTML
API responses
```

OAuth tokens / refresh tokens remain encrypted or provider-managed/server-side
according to project conventions.

Never expose raw tokens in the console.

---

# 4. Inbox Connection State

Support safe connection status:

```text
not_configured
configured
connected
auth_expired
error
disabled
```

UI may display status only.

No secret values.

---

# 5. InboundMessage Model

Create:

```python
InboundMessage
```

Fields:

```text
inbound_message_id
provider
provider_message_id
provider_thread_id

internet_message_id
in_reply_to
references[]

from_name
from_email
to_emails[]
cc_emails[]

subject
body_text
body_html_present

received_at

linked_outbound_draft_id
linked_outbound_revision
linked_send_attempt_id

company_id
contact_id
conversation_id

classification
classification_confidence

is_auto_reply
is_bounce
is_unsubscribe

raw_headers_ref
raw_provider_ref

created_at
```

Store plain-text normalized body for analysis.

Do not store attachments in Phase 11 unless explicitly needed later.

---

# 6. Conversation Model

Create:

```python
Conversation
```

Fields:

```text
conversation_id
company_id
primary_contact_id

provider
provider_thread_id

status
reply_category
sentiment_optional

first_outbound_at
first_reply_at
last_message_at

actual_respondent_contact_id

meeting_status
opportunity_status

active_conversation

created_at
updated_at
```

Allowed status:

```text
new_reply
needs_review
waiting_on_us
waiting_on_them
meeting_proposed
meeting_scheduled
closed_positive
closed_negative
do_not_contact
auto_reply
bounce
unknown
```

---

# 7. Reply Categories

Required classification taxonomy:

```text
positive_interest
meeting_proposed
referral
question
not_now
not_interested
unsubscribe
bounce
auto_reply
unknown
```

Optional additional internal categories may be added only if clearly useful.

Do not merge:

```text
unsubscribe
not_interested
bounce
```

into one state.

They have different operational consequences.

---

# 8. Deterministic Classification First

Classify these deterministically where possible:

```text
unsubscribe
bounce
auto_reply
```

Use headers, provider metadata and explicit phrases.

LLM may assist with:

```text
positive_interest
meeting_proposed
referral
question
not_now
not_interested
unknown
```

Weak/invalid LLM output must fall back safely to:

```text
unknown
needs_review
```

Never auto-send based only on LLM classification.

---

# 9. Thread Matching

Thread/reply linkage priority:

```text
1. provider_thread_id
2. In-Reply-To -> Internet-Message-ID
3. References header
4. exact known outbound recipient + recent subject/thread evidence
5. manual review
```

Do not fuzzy-link inbound messages to a company solely by similar subject/name.

---

# 10. Outbound Linkage

Phase 11 must link an inbound reply to:

```text
Phase 9 draft
draft revision
send attempt
recipient
company
contact
```

when evidence permits.

Sent revision remains immutable.

---

# 11. Actual Respondent Resolution

Inbound respondent may differ from original outbound mailbox.

Example:

```text
Outbound:
info@company.com

Inbound:
named.person@company.com
```

Preserve both identities.

If respondent is a business contact and identity evidence is sufficient:

```text
create/update ContactCandidate
```

using existing Phase 6 identity rules.

Do NOT merge by name alone.

---

# 12. Active Conversation Guard

On a legitimate human inbound reply:

```text
active_conversation = true
```

for the relevant company/contact/conversation.

This state must block fresh cold outreach for that active relationship.

Phase 10 / 10.5 must respect this state.

No new cold sequence while active conversation exists.

---

# 13. Unsubscribe

If reply classification is:

```text
unsubscribe
```

Phase 11 must create authoritative Phase 9 suppression.

Expected:

```text
recipient suppressed
conversation = do_not_contact
no future cold send
```

No LLM override.

---

# 14. Bounce

For bounce:

```text
classification = bounce
```

Preserve bounce metadata.

Mark recipient email appropriately according to existing email-status contract.

Do not automatically suppress an entire domain because one mailbox bounced.

No retry loop to the same invalid recipient.

---

# 15. Not Interested

For:

```text
not_interested
```

default operational behavior:

```text
close conversation
do not continue current outreach
human may choose suppression / long-term no-contact policy
```

Do not automatically reinterpret as positive.

---

# 16. Not Now

For:

```text
not_now
```

support:

```text
follow-up date
operator note
watch/revisit state
```

No autonomous future send in Phase 11.

A later automation phase may schedule follow-up only with explicit product design.

---

# 17. Positive Interest

For:

```text
positive_interest
```

set:

```text
active_conversation = true
status = waiting_on_us / needs_review
```

No automatic response.

---

# 18. Meeting Proposed

For:

```text
meeting_proposed
```

extract only explicit candidate time information.

Store:

```text
meeting_status = proposed
proposed_times[]
timezone if explicit
```

Do not invent timezone.

Do not schedule calendar events automatically.

---

# 19. Referral

For:

```text
referral
```

preserve:

```text
referred_name
referred_email if explicitly present
referred_role if explicitly present
```

Do not scrape or infer personal contact details.

---

# 20. Question

For:

```text
question
```

preserve the question text/evidence and route:

```text
waiting_on_us
```

---

# 21. Suggested Response

Create:

```python
ReplySuggestion
```

Fields:

```text
suggestion_id
conversation_id
inbound_message_id

subject
body

evidence_refs[]
classification

generation_model
prompt_version
language
tone

status
created_at
```

Allowed statuses:

```text
generated
pending_review
approved
rejected
superseded
sent
```

---

# 22. Reply Draft Grounding

Suggested response may use:

```text
the inbound message
the existing conversation
verified company/contact context
previous outbound message
active pyBIM services
explicit meeting/question/referral details
```

It may NOT invent:

```text
pricing
delivery dates
project scope commitments
availability
legal commitments
discounts
capabilities not in WebsiteProfile
previous relationship
meeting times not offered
```

---

# 23. Human Review Is Mandatory

Workflow:

```text
Inbound Reply
→ classification
→ suggested response
→ operator review
→ edit if needed
→ approve
→ explicit send
```

No:

```text
auto-approve
auto-reply
auto-send
```

---

# 24. Reply Send Authority

Reply sending must use the same trusted sender/provider/safety architecture as Phase 9.

Recommended:

```text
Phase 11 ReplySendOrchestrator
```

may wrap/reuse Phase 9 provider and safety checks.

Must enforce:

```text
sender trust
send enabled
suppression
recipient validity
idempotency
stale revision protection
explicit operator confirmation
```

---

# 25. Reply Idempotency

Create stable reply-send key:

```text
conversation_id
suggestion/revision
recipient
```

Prevent duplicate accidental reply sends.

No force bypass.

---

# 26. No Reply To Automated/Bounce Messages

Default:

```text
auto_reply -> no response suggestion required
bounce -> no response
unsubscribe -> no response
```

Operator may view audit state.

No reply-send button where nonsensical/unsafe.

---

# 27. Conversation Timeline

Operator console should display a chronological timeline:

```text
outbound draft
approval
send attempt
sent
inbound reply
classification
actual respondent
reply suggestion
operator edit
reply send
meeting state
notes
```

Evidence and internal assessment must be visually distinct.

---

# 28. Phase 10.5 Replies Tab Upgrade

Replace the placeholder-only Replies page with functional views.

Required:

```text
/sales/replies
/sales/replies/{conversation_id}
```

List should show:

```text
company
respondent
category
received_at
status
needs_action
```

Detail should show:

```text
thread
message body
classification
classification evidence
contact identity
company
suggested reply
review actions
meeting state
audit timeline
```

---

# 29. Inbox Sync Controls

Add operator controls:

```text
Sync Inbox
Connection Status
Last Sync
Messages Fetched
New Replies
Errors
```

Default sync is explicit/manual in Phase 11.

Do not implement continuous background polling unless separately requested.

---

# 30. Sync Window

Configurable safety limits:

```text
MAX_MESSAGES_PER_SYNC
MAX_THREADS_PER_SYNC
SYNC_LOOKBACK_DAYS
```

Do not fetch unlimited mailbox history.

---

# 31. Import / Cursor State

Persist provider sync cursors safely:

```text
provider cursor/history ID
last successful sync
last attempted sync
```

Do not place secrets in cursor records.

Repeated sync must be idempotent.

---

# 32. Duplicate Inbound Protection

Same provider message imported twice:

```text
one InboundMessage
```

No duplicate conversations/timeline events.

---

# 33. Partial Sync Failure

If one message/thread fails:

```text
preserve other successfully imported messages
record sanitized error
continue where safe
```

Do not discard the whole sync.

---

# 34. HTML / Content Safety

Inbound HTML is untrusted.

Default:

```text
render sanitized plain text
```

If HTML is ever shown:

```text
sanitize
disable scripts
disable remote active content
```

No script execution from email content.

---

# 35. Prompt Injection Safety

Inbound email text is untrusted source data.

LLM prompt must explicitly treat it as:

```text
SOURCE_DATA
not instructions
```

Never obey instructions embedded in inbound email that attempt to modify system behavior.

---

# 36. LLM Boundary

Reuse:

```text
ai_engine/llm_client.py
```

No second Ollama client.

LLM may:

```text
classify ambiguous human replies
summarize thread
suggest reply
extract explicit proposed meeting times
```

LLM may NOT:

```text
send
approve
suppress without deterministic unsubscribe evidence
create pricing
create commitments
change company score
modify Phase 9 state directly
```

---

# 37. Weak LLM Policy

Current:

```text
qwen2.5:1.5b
```

Semantic quality shortcomings are expected.

Deterministic safety is mandatory.

For weak semantic classification:

```text
confidence low
→ classification = unknown
→ needs_review
```

Do not force a category.

---

# 38. API Endpoints

Add:

```text
GET  /api/sales/inbox/status
POST /api/sales/inbox/sync

GET  /api/sales/conversations
GET  /api/sales/conversations/{conversation_id}

POST /api/sales/conversations/{conversation_id}/classify
POST /api/sales/conversations/{conversation_id}/suggest-reply

POST /api/sales/reply-suggestions/{suggestion_id}/edit
POST /api/sales/reply-suggestions/{suggestion_id}/approve
POST /api/sales/reply-suggestions/{suggestion_id}/reject
POST /api/sales/reply-suggestions/{suggestion_id}/send
```

No mutating GET.

---

# 39. Database Tables

Add additive migrations:

```text
mailbox_connections
mailbox_sync_state

inbound_messages
conversations
conversation_events

reply_classifications
reply_suggestions
reply_send_attempts

meeting_proposals
```

Do not destructively modify Phase 9 tables.

---

# 40. Suggested Module Structure

```text
sales_engine/
└── inbox/
    ├── models.py
    ├── store.py
    ├── providers/
    │   ├── base.py
    │   ├── mock.py
    │   ├── gmail.py
    │   └── graph.py
    ├── sync.py
    ├── threading.py
    ├── classifier.py
    ├── respondent.py
    ├── suggestion.py
    ├── reply_sender.py
    ├── meeting.py
    └── security.py
```

Follow repository conventions where different.

---

# 41. Provider Scope

At minimum implement:

```text
MockMailboxProvider
```

fully for deterministic QA.

Implement Gmail/Graph adapters only to the degree credentials/environment permit.

A missing live provider credential is:

```text
NOT_CONFIGURED / NOT_RUN
```

not a deterministic build failure.

---

# 42. Gmail / Graph Safety

If implementing live providers:

```text
read minimum required mailbox scopes
send only explicit approved replies
no bulk mailbox modification
no delete/archive automation
no contact export
```

OAuth only.

Do not hardcode app/client secrets.

---

# 43. Reply Sender Identity

Use configured trusted business sender identity.

Reply should preserve thread headers/provider thread linkage where supported.

Do not invent sender name/email.

---

# 44. Subject Handling

For replies:

```text
preserve existing thread subject
```

Provider may handle `Re:` naturally.

Do not create deceptive:

```text
Fwd:
Re:
```

for unrelated messages.

---

# 45. Meeting State

Allowed:

```text
none
proposed
scheduled
completed
cancelled
unknown
```

Phase 11 may record status manually.

No calendar integration required.

---

# 46. Opportunity State

Allowed:

```text
unknown
active_conversation
meeting
qualified_opportunity
closed_lost
closed_won
```

Operator-controlled / evidence-backed.

No automatic revenue forecasting.

---

# 47. Contact Promotion

An actual inbound respondent may become the preferred conversation contact if strong identity evidence supports it.

Do not delete original generic mailbox contact.

Keep provenance:

```text
original recipient
actual respondent
```

---

# 48. Historical Reply Compatibility

Existing imported historical replies from Phase 10 must remain viewable.

Do not duplicate them when live provider synchronization begins.

If no provider message ID exists:

```text
historical_import source remains authoritative
```

---

# 49. Audit

Immutable/auditable events:

```text
mailbox sync
message import
thread link
classification
manual reclassification
suggestion generation
suggestion edit
approval
rejection
reply send attempt
reply sent/failed
suppression
meeting status change
opportunity status change
```

No chain-of-thought storage.

Store concise reasons/evidence only.

---

# 50. Security

Must test:

```text
OAuth/token secrecy
HTML/script sanitization
prompt injection
path/data injection
SQL input safety
CSRF
same-origin
no mutating GET
provider error sanitization
release denylist
```

---

# 51. Phase 11 Critical Regressions

Mandatory before freeze:

```text
P11-REG-001 duplicate inbox sync does not duplicate inbound messages
P11-REG-002 provider thread ID links reply to correct conversation
P11-REG-003 In-Reply-To links reply to correct outbound message
P11-REG-004 ambiguous reply does not fuzzy-link to wrong company
P11-REG-005 unsubscribe creates Phase 9 suppression
P11-REG-006 bounce does not suppress entire domain
P11-REG-007 auto-reply cannot trigger reply send
P11-REG-008 positive human reply activates conversation
P11-REG-009 active conversation blocks fresh cold outreach
P11-REG-010 actual respondent can differ from original recipient safely
P11-REG-011 weak LLM classification falls back to unknown/review
P11-REG-012 inbound prompt injection cannot change system behavior
P11-REG-013 reply suggestion cannot auto-approve
P11-REG-014 reply suggestion cannot auto-send
P11-REG-015 explicit reply send requires trusted sender and confirmation
P11-REG-016 duplicate reply send blocked by idempotency
P11-REG-017 sent reply revision immutable
P11-REG-018 live provider missing credentials is controlled NOT_CONFIGURED
P11-REG-019 one message failure does not lose successful sync results
P11-REG-020 inbound HTML cannot execute script
P11-REG-021 provider secrets never appear in UI/log/report
P11-REG-022 historical replies are not duplicated by live sync
P11-REG-023 meeting time not invented when absent
P11-REG-024 runtime DB/tokens never enter release ZIP
```

---

# 52. Operator Console Requirements

Upgrade Replies UI with:

```text
Inbox status panel
manual Sync Inbox button
reply queue
filters by category/status
conversation detail
classification
manual reclassification
suggested response
edit
approve
reject
confirmed send
meeting/opportunity state
timeline
```

No auto-reply switch in Phase 11.

---

# 53. Metrics

Dashboard may add factual metrics:

```text
new replies
needs review
positive interest
meeting proposed
not interested
unsubscribes
bounces
waiting on us
waiting on them
```

Do not fabricate:

```text
conversion probability
deal probability
expected revenue
```

---

# 54. Reporting

Create:

```text
tests/reports/phase11_latest.json
```

Suggested fields:

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

false_pass_count
unmapped_deterministic_cases

duplicate_inbound_count
thread_mislink_count
suppression_bypass_count
domain_over_suppression_count
auto_reply_send_count
autonomous_reply_send_count
duplicate_reply_send_count
prompt_injection_violation_count
secret_exposure_count
unsafe_html_render_count
runtime_db_packaged_count
```

Execution-derived where feasible.

---

# 55. Practical Acceptance Rule

As with Phase 10.5:

```text
critical deterministic safety must pass
weak LLM semantic quality may be REVIEW / DEFERRED_MODEL
overall deterministic QA >= 60% is acceptable if no critical safety blocker exists
```

Do not create endless patch loops for wording quality.

---

# 56. Explicit Non-Goals

Do NOT implement in Phase 11:

```text
automatic reply sending
automatic follow-up campaigns
calendar booking integration
automatic meeting scheduling
voice calling
WhatsApp outreach
LinkedIn messaging automation
open/click tracking
deal forecasting
autonomous CRM decisions
full CRM replacement
```

---

# 57. Build Completion Gate

Phase 11 Build is ready for QA when:

1. Mock inbox provider works.
2. Inbox sync is idempotent.
3. Conversation threading works.
4. Reply taxonomy exists.
5. Deterministic unsubscribe/bounce/auto-reply handling exists.
6. Actual respondent is preserved safely.
7. Active conversation is enforced.
8. Suggested reply generation exists.
9. Suggested reply is human-reviewed.
10. No auto-reply/auto-send exists.
11. Explicit reply send uses trusted sender safety.
12. Duplicate sends are blocked.
13. Replies UI is functional.
14. Live provider status is truthful.
15. Missing live credentials are safe.
16. Prompt injection is contained.
17. Email HTML is safe.
18. Historical replies remain compatible.
19. Phase 1–10.5 regressions remain green.
20. Release ZIP is clean.

After build:

```text
DO NOT declare Phase 11 frozen.
```

Return the release ZIP for independent verification before Phase 11 Golden QA.
