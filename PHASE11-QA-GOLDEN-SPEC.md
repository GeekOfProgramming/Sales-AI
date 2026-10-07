# SalesAI Phase 11 QA / Golden Acceptance Spec
## Inbox / Reply Intelligence & Human-Reviewed Conversation Workflow
### Practical Freeze Gate for Current Weak Local LLM

## Acceptance philosophy

Current model: `qwen2.5:1.5b`.

- Deterministic safety / identity / threading / send failures = FAIL.
- Weak semantic classification or reply wording = REVIEW / DEFERRED_MODEL.
- Missing live Gmail / Microsoft Graph / IMAP credentials = NOT_RUN / NOT_CONFIGURED.
- Practical freeze target: all critical safety gates pass, no autonomous send, no suppression bypass, no duplicate reply send, no unsafe HTML execution, no secret/runtime DB packaging violations, and >=60% deterministic verified pass rate.

No live mailbox sending during Golden QA. Use `MockMailboxProvider`, mock outbound provider, and temporary SQLite DBs.

## Critical safety gates

P11-GOLD-001 No autonomous reply send.
P11-GOLD-002 Suggested reply starts `pending_review`.
P11-GOLD-003 Human approval required before reply send.
P11-GOLD-004 Explicit send confirmation required.
P11-GOLD-005 `EMAIL_SEND_ENABLED=false` blocks live send.
P11-GOLD-006 Trusted sender required.
P11-GOLD-007 Phase 9 suppression blocks recipient.
P11-GOLD-008 Duplicate reply send blocked by idempotency.
P11-GOLD-009 Sent reply suggestion/revision is immutable.
P11-GOLD-010 Auto-reply cannot trigger reply send.
P11-GOLD-011 Bounce cannot trigger reply send.
P11-GOLD-012 Unsubscribe cannot trigger reply send and creates Phase 9 suppression.

## Inbox sync

Validate manual sync only, cursor persistence, last attempted/successful sync, `MAX_MESSAGES_PER_SYNC`, `SYNC_LOOKBACK_DAYS`, partial failure isolation, and duplicate-message idempotency.

Repeated provider message IDs must produce one inbound message and no duplicate conversation/timeline event.

## Threading

Priority:
1. `provider_thread_id`
2. `In-Reply-To`
3. `References`
4. exact known recipient + subject evidence
5. exact known contact email
6. unlinked/manual review

No fuzzy company-name/subject linkage. Ambiguous messages must remain unlinked or `needs_review`.

Preserve linked outbound draft ID, revision, send-attempt ID, company ID and contact ID when evidence supports them.

## Classification

Required taxonomy:

`positive_interest`, `meeting_proposed`, `referral`, `question`, `not_now`, `not_interested`, `unsubscribe`, `bounce`, `auto_reply`, `unknown`.

Deterministic-first for `unsubscribe`, `bounce`, `auto_reply`.

Ambiguous LLM output must fall back to `unknown` + `needs_review`.

## Prompt injection and HTML safety

Inbound email is untrusted source data. Test attempts to instruct the system to auto-send, approve, reveal secrets, or modify state. These must have no effect.

Unsafe HTML/scripts/`javascript:`/iframes/forms/etc. must not execute. Prefer sanitized plain-text rendering.

## Reply-state behavior

Unsubscribe:
- `do_not_contact`
- Phase 9 suppression created
- no suggestion/send

Bounce:
- recipient mailbox marked bounced/invalid
- no domain-wide suppression solely from one mailbox
- no send

Auto reply:
- no active-conversation promotion
- no suggestion/send by default

Positive human reply:
- `active_conversation=true`
- `cold_outreach_allowed=false`
- route to operator review

Not interested:
- close current conversation
- no automatic positive reinterpretation

Not now:
- `needs_review`
- optional follow-up note/date
- no autonomous future send

Meeting proposed:
- extract only explicit proposed times
- never invent timezone or time

Referral:
- preserve only explicitly supplied business contact details

## Actual respondent

Support original generic recipient differing from actual respondent. Preserve both. Apply Phase 6 strong identity rules. No same-name-only merge.

## Suggested reply grounding

May use inbound message, conversation history, prior outbound, verified company/contact facts, active pyBIM services, and explicit question/referral/meeting data.

Must not invent pricing, discounts, deadlines, scope commitments, availability, calendar bookings, capabilities, or prior relationships.

Generated suggestion must be human-reviewed.

## Reply workflow

Required:
`generated -> pending_review -> approved/rejected -> explicit send`.

Approve alone makes zero provider calls.

Operator edit preserves conversation/message linkage and audit trail.

Reply send validates:
- approved suggestion
- trusted sender
- recipient validity
- Phase 9 suppression
- safe conversation state
- send enabled
- idempotency
- explicit operator confirmation

Provider reply metadata should preserve thread identifiers where supported.

## Historical compatibility

Existing Phase 10 historical replies remain visible and must not be duplicated by mock/live sync.

## Provider boundary

`MockMailboxProvider` must be fully functional for deterministic QA.

Gmail / Graph / IMAP without credentials:
`NOT_CONFIGURED / NOT_RUN`.

Do not claim `connected` merely because environment variables exist if no real connection check occurred.

## UI and API

Smoke-test:
- `/sales/replies`
- `/sales/replies/{conversation_id}`

UI must show connection status, manual Sync Inbox, reply queue, category/status, respondent, received time, message body, suggestion, edit/approve/reject, explicit send, meeting/opportunity state and timeline. No Auto Reply control.

API endpoints:
- `GET /api/sales/inbox/status`
- `POST /api/sales/inbox/sync`
- `GET /api/sales/conversations`
- `GET /api/sales/conversations/{id}`
- `POST /api/sales/conversations/{id}/classify`
- `POST /api/sales/conversations/{id}/suggest-reply`
- `POST /api/sales/reply-suggestions/{id}/edit`
- `POST /api/sales/reply-suggestions/{id}/approve`
- `POST /api/sales/reply-suggestions/{id}/reject`
- `POST /api/sales/reply-suggestions/{id}/send`

No mutating GET. UI POSTs remain CSRF/same-origin protected.

## Security

Never expose OAuth tokens, refresh tokens, SMTP passwords, API keys, client secrets, private keys, raw Authorization headers or provider secrets.

Provider errors must be sanitized.

## Existing Phase 11 regressions

All `P11-REG-001..024` must remain green.

## Reporting

Generate `tests/reports/phase11_latest.json` with:

`phase`, `run_timestamp`, `git_commit`, `total_catalog_cases`, `mapped_executable_cases`, `executed_cases`, `passed`, `failed`, `review`, `not_run`, `deferred_model`, `deterministic_pass_rate`, `false_pass_count`, `unmapped_deterministic_cases`, `critical_safety_total`, `critical_safety_passed`, `critical_safety_failed`, `duplicate_inbound_count`, `thread_mislink_count`, `suppression_bypass_count`, `domain_over_suppression_count`, `auto_reply_send_count`, `autonomous_reply_send_count`, `duplicate_reply_send_count`, `prompt_injection_violation_count`, `secret_exposure_count`, `unsafe_html_render_count`, `runtime_db_packaged_count`.

Unknown/unmeasured counters should be `null`/`NOT_RUN`, not misleading hardcoded zero.

Catalog presence is not execution evidence. Every deterministic case should map to a real pytest node/evaluator. Unmapped deterministic cases are `NOT_RUN`, not PASS.

For practical acceptance, raw pytest evidence outranks cosmetic report-formatting defects.

## Full regression

Run:
`python -m pytest tests/ -q`

Require zero new deterministic failures. External/live-provider tests may remain skipped.

## Weak-model deferred evaluation

Small semantic evaluation for:
`positive_interest`, `meeting_proposed`, `question`, `referral`, `not_now`, `not_interested`.

Weak wording/classification quality may be `DEFERRED_MODEL` if deterministic fallback and safety are correct.

## Release packaging

Release ZIP must exclude `.env*`, `*.db`, `*.sqlite*`, OAuth tokens, `token.json`, credentials, client secrets, private keys, runtime logs, Chroma runtime DB, `.venv`, `__pycache__`, `*.pyc`.

## Practical freeze rule

Phase 11 may freeze when:
1. P11-GOLD-001..012 all PASS.
2. P11-REG-001..024 remain green.
3. Inbox sync is idempotent.
4. No dangerous threading mis-link is known.
5. Unsubscribe creates Phase 9 suppression.
6. Bounce does not domain-suppress.
7. Auto-reply cannot send.
8. Human reply activates conversation safely.
9. Suggested reply is human-reviewed.
10. Explicit reply send is trusted/idempotent/confirmed.
11. Prompt injection and unsafe HTML are contained.
12. Secrets/runtime DB stay out of ZIP.
13. Full repository has no new deterministic failure.
14. Deterministic verified pass rate >=60%.
15. Weak-model semantic issues are documented rather than endlessly patched.

Non-blocking deferred items: live Gmail OAuth, live Graph, IMAP interoperability, high-quality semantic reply generation, multilingual polish, calendar integration, automated follow-up scheduling, analytics polish.

## Final QA output

Return:
- total/mapped/executed cases
- PASS/FAIL/REVIEW/NOT_RUN/DEFERRED_MODEL
- deterministic pass rate
- critical safety total/pass/fail
- P11-GOLD-001..012
- P11-REG-001..024
- existing Phase 11 suite results
- full repository result
- live provider statuses
- known residual risks
- weak-model deferred items
- Git commit
- README-TEST commit
- phase11_latest commit
- ZIP file count
- denylist result
- final `SalesAI_export.zip`

If accepted: `PHASE 11 FROZEN`, then proceed to Phase 12 — Analytics / Learning Loop.
