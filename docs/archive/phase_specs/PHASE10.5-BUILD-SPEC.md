# SalesAI Phase 10.5 Build Spec
## Local Sales Operator Console
### Human-in-the-Loop Web UI for Discovery, Qualification, Draft Review, Sending History and Sales Operations

# Phase Goal

Phase 10.5 builds the local operator-facing web application for SalesAI.

The console must provide one local place to see newly discovered companies, understand why each company was found, inspect discovery evidence and source provenance, review company tier / fit / intent / BIM maturity, manage watchlist and qualification, review contacts, review generated email drafts, approve/edit/reject/mark Do Not Contact, perform dry-run validation, explicitly send approved drafts through the Phase 9 send path, inspect sent-email history, view company/contact timelines, and prepare the UI/data contract for Phase 11 inbox/reply intelligence.

Phase 10.5 is an operator UI. It must NOT recreate Phase 9 sending logic, recreate Phase 10 discovery logic, bypass Phase 9 approval/suppression/idempotency, infer or fabricate sales facts in the frontend, or implement autonomous replies.

# 1. Dependency Gate

Phase 10.5 implementation must start only after Phase 9 is formally frozen and Phase 10 is functionally complete and QA accepted. If Phase 10 schemas/endpoints change before freeze, update Phase 10.5 contract first. Do not build the console against provisional Phase 10 data structures.

# 2. Architectural Principle

The browser UI is not a second business-logic layer.

```text
Browser
   ↓
Local Sales Console
   ↓
Existing FastAPI Sales APIs
   ├── Phase 10 Discovery / Candidates / Watchlist
   ├── Phase 7 Export / Workflow Projection
   ├── Phase 8 Draft Generation
   └── Phase 9 Review / Approval / Suppression / Send
           ↓
        SQLite
```

All authoritative mutations occur through server services/APIs. Never perform direct SQLite writes from frontend code.

# 3. Local-First Deployment

Primary address:

```text
http://127.0.0.1:8000/sales
```

Default server binding must be `127.0.0.1`, not `0.0.0.0`, unless explicitly configured by the operator. The application is intended to run on the same machine as SalesAI. No cloud dashboard is required.

# 4. Recommended UI Stack

Prefer minimal operational complexity:

```text
FastAPI
server-rendered HTML
Jinja2 templates
HTMX
small vanilla JavaScript where required
existing CSS or lightweight local stylesheet
```

Avoid introducing a large SPA framework unless clearly justified. Do not require Node.js for the default deployment unless explicitly approved later. No CDN dependency for core operation. Static assets required by the UI should be packaged locally.

# 5. Main Navigation

Create these primary sections:

```text
Dashboard
Discoveries
Sources
Candidates
Qualified Leads
Draft Review
Approved
Sent
Watchlist
Do Not Contact
Replies
Settings
```

`Replies` in Phase 10.5 is a placeholder/read-only future-compatible section unless historical reply data already exists. Actual mailbox synchronization and AI reply handling belong to Phase 11.

# 6. Dashboard

Routes:

```text
/sales
/sales/dashboard
```

Show operational metrics derived from backend state:

```text
new candidates
needs research
watchlist
qualified leads
contacts found
usable contacts
drafts pending review
approved not sent
sent today
sent this week
suppressed
historical known companies
```

Phase 11-compatible cards may display replies, positive replies, and meetings only from already imported historical data. Do not fabricate zero/positive outcomes when data is unavailable; use `not available` or `not connected` where appropriate.

# 7. Discovery Run Panel

Dashboard or Discoveries page must allow `Run Discovery`. The operator chooses market/country, source set, company types, result budget, and strategy. The UI calls only the Phase 10 discovery endpoint. No email generation or send occurs automatically after discovery.

# 8. Discoveries Tab

Route:

```text
/sales/discoveries
```

Show run ID, started/completed time, markets, sources, observations found, new companies, known companies, rejected, watchlist, qualified, errors, and status. Allow drill-down into the run. A run detail page shows which source produced which candidate/observation.

# 9. Sources Tab

Route:

```text
/sales/sources
```

Expose the Phase 10 Source Registry. Show source name, source type, countries, enabled, priority, last success, last failure, last error, results last run, and new candidates last run.

Allowed operator actions: enable/disable source, add supported source configuration, edit safe non-secret configuration, run source, import manual source/seed.

Do not expose API keys, passwords, OAuth tokens or other credentials. If a source requires a credential, display only `Configured` or `Not configured`.

# 10. Manual Seed / Source Input

Support entering company name, domain, website URL, public directory URL, CSV, and XLSX. Manual input enters normal Phase 10 research/dedup/qualification flow and does not automatically qualify or contact a company.

# 11. Historical Spreadsheet Import

Provide `Import Historical Data` for CSV/XLSX. Before final import show a preview containing rows detected, companies matched, new companies, possible duplicates, contacts detected, historical outreach events, invalid rows, and warnings. Require explicit operator confirmation. Do not silently overwrite current company/contact/outreach records.

# 12. Candidates Tab

Route:

```text
/sales/candidates
```

Default columns:

```text
company
domain
country
company type
tier
fit
intent
BIM maturity
outsourcing potential
automation potential
discovery sources
last signal
status
```

Support search, filter, sort, pagination. Filters include market, source, company type, tier, fit, intent, BIM maturity, qualification route, watchlist state, and historical-contact state.

# 13. Candidate Status Labels

Use backend states only, such as:

```text
new
needs_research
watchlist
qualified_for_enrichment
known_company
reengagement_review
rejected
suppressed
```

Do not invent UI-only business states that conflict with backend state.

# 14. Candidate Detail Page

Route:

```text
/sales/candidates/{candidate_id}
```

Sections:

```text
Company Summary
Qualification
Discovery Evidence
Business Evidence
Intent Signals
Projects / Tenders
Jobs
Contacts
Historical Outreach
Drafts
Sent History
Conversation History
Timeline
Audit
```

Only show sections supported by actual data.

# 15. Company Summary

Display company name, domain, location, company types, legal form, tier, tier confidence, BIM maturity, fit, intent, outsourcing potential, automation potential, and current qualification state. Internal assessments must be visually distinguishable from verified public facts using labels such as `Verified Evidence`, `System Assessment`, and `Historical Data`.

# 16. Why This Company?

Every candidate page must explain why found, why fit, why intent, and why watchlist/qualified/rejected using stored evidence/reasons. Do not ask an LLM to generate unsupported justification in the UI.

# 17. Evidence Panel

For each evidence item show category, signal type, evidence text, source, source URL, observed date, freshness, and confidence. Clicking evidence may open the public source in a new tab. No evidence item may exist only as an untraceable UI string.

# 18. Source Timeline

Show discovery observations chronologically. One company remains one company even when discovered from many sources.

# 19. Candidate Actions

Possible state-dependent actions:

```text
Research Again
Qualify
Move to Watchlist
Reject
Restore for Research
Enrich Contacts
Generate Draft
Do Not Contact
```

Actions must call existing backend services. No frontend state mutation without server confirmation.

# 20. Qualification Review

Show Tier, Fit, Intent, BIM Maturity, Opportunity Types, Evidence, and Qualification Recommendation. Operator may accept or override allowable human-review fields. Every manual override must be audited with old value, new value, timestamp, reviewer label, and optional reason. Reviewer label is not authentication.

# 21. Qualified Leads Tab

Route:

```text
/sales/leads
```

Show company, tier, fit, intent, best opportunity type, best contact, email status, lead score/route, draft status, and last activity. For job-led legacy leads show Phase 5 score. For Phase 10 non-job leads do not fabricate a Phase 5 score; show Phase 10 qualification route separately.

# 22. Contact Panel

Display name, title, seniority, department, work email, email status, confidence, LinkedIn, provider, buyer-role match, contact score, and data sources. Clearly distinguish verified/likely, historical_unknown, risky, unknown, and not_found. Historical email does not appear verified unless revalidated.

# 23. Best Contact Selection

Show backend-selected best contact and allow a human to select another existing eligible contact. Manual selection must not bypass work email validation, suppression, identity checks, or Phase 9 recipient rules.

# 24. Draft Review Tab

Route:

```text
/sales/drafts
```

Default queue is `pending_review`. Each review card/page must show company, why this company, source/signals, contact, recipient email, sender identity, subject, full final body, evidence used, draft revision, created time, and status.

The body displayed must be the exact Phase 9 reviewable final body, including server-owned signature/opt-out content when applicable.

# 25. Draft Review Actions

Allowed:

```text
Approve
Edit
Reject
Request Changes
Do Not Contact
Dry Run
```

Sending must be separate. Never combine `Approve & Send` as one button.

# 26. Approve Behavior

Approve calls Phase 9 approval endpoint. After success show Approved, revision, approval timestamp, reviewer label, and approved fingerprint short ID. Approval must never trigger provider delivery.

# 27. Edit Behavior

Edit allows recipient if policy allows, subject, and body. Sender identity should not be freely typed if Phase 9 treats it as trusted server identity. Editing creates a new revision through Phase 9 and resets approval to pending_review and send_status to not_sent. Old revision remains visible.

# 28. Reject / Request Changes

These actions call Phase 9 and may include notes according to backend contract. Sent revisions cannot expose mutable actions.

# 29. Do Not Contact

DNC must use Phase 9 suppression. Operator chooses supported scope such as email/domain and reason such as manual, opt_out, wrong_person, invalid_recipient, complaint, or other supported value. After suppression, relevant send actions become unavailable.

# 30. Dry Run

Dry Run calls Phase 9 dry-run validation. Display checks for approved revision, approval hash, sender, recipient, suppression, already sent, body, subject, rate limit, and `provider/network call = 0`. Dry-run never sends.

# 31. Explicit Send

Only show Send when backend reports the exact revision is eligible. Require explicit confirmation showing company, recipient, sender, subject, revision and the sentence `This will send one real email.` Then call only Phase 9 send endpoint. No client-side force/bypass parameters.

# 32. Sending Disabled

If `EMAIL_SEND_ENABLED=false`, visibly show `Real sending is disabled on this server.` Dry Run remains available.

# 33. Batch Sending

Phase 10.5 v1 should prioritize single-draft review/send. If batch send is exposed, only explicit selected IDs are allowed, obey Phase 9 batch max, show selected count and recipients, no `Send All Approved`, isolate failures, and never hidden-retry.

# 34. Approved Tab

Route:

```text
/sales/approved
```

Show approved but not sent drafts with company, recipient, subject, revision, approved time, dry-run status, send eligibility, and blocked reason.

# 35. Sent Tab

Route:

```text
/sales/sent
```

Show immutable send history: company, contact, recipient, subject, revision, sent time, provider, provider message ID if available, send attempt ID, status. Sent content cannot be edited.

# 36. Sent Detail

Show approved payload, sent payload identity, approval hash, send attempt, provider result, and audit events. Never expose provider passwords/tokens.

# 37. Watchlist Tab

Route:

```text
/sales/watchlist
```

Show company, fit, intent, watch reason, signals monitored, last checked, next check, new evidence. Actions: Research Now, Remove From Watchlist, Qualify, Reject. No send action directly from Watchlist.

# 38. Do Not Contact Tab

Route:

```text
/sales/do-not-contact
```

Show Phase 9 suppression records. Allow removal only if Phase 9 backend supports explicit safe unsuppress behavior; otherwise read-only.

# 39. Replies Tab — Phase 10.5 Scope

Route:

```text
/sales/replies
```

Phase 10.5 does not connect to mailbox yet. It may show historical imported replies, historical positive outcomes, and conversation placeholders. Display `Live inbox synchronization is not connected yet.` Phase 11 extends this section.

# 40. Historical Positive Outcomes

Historical imported outcomes such as positive reply, meeting proposed, modeling opportunity, or strategic collaboration should appear in company timeline and Replies. Do not use tiny samples to display misleading conversion predictions.

# 41. Company Timeline

Every company detail page should have unified chronological events such as discovered, researched, signal_found, qualified, watchlisted, contact_found, draft_created, draft_edited, approved, rejected, suppressed, dry_run, sent, historical_reply, meeting, manual_note. Phase 11 later adds inbound_reply, reply_classified, reply_draft_generated, reply_sent.

# 42. Audit Visibility

Show important audit events to operator: event, actor/reviewer label, timestamp, revision, safe note. Do not expose secrets or stack traces.

# 43. Global Search

Add search across company name, domain, contact name, business email, and draft subject. Results link to canonical entities.

# 44. Filters and Saved Views

Useful fixed filters: New Today, Needs Research, High Fit, High Intent, Watchlist, Drafts Pending Review, Approved Not Sent, Sent Today, Previously Contacted, Suppressed. Custom persisted filters are optional later.

# 45. Pagination

All large tables use server-side pagination. Do not load entire database tables into browser memory.

# 46. Error UX

Map structured backend errors such as approval_stale, stale_revision, sender_config_missing, sender_mismatch, suppressed_recipient, already_sent, sending_disabled, rate_limit_exceeded, validation_failed into clear operator messages. Show technical detail only in expandable diagnostics.

# 47. Concurrency / Stale UI Protection

Every mutation should include/verify draft_id, revision, and current state/version. On stale_revision, approval_stale or already_sent, refresh and show conflict. Never overwrite with stale browser state.

# 48. CSRF / Browser Mutation Safety

Mutations must use POST/PUT/PATCH/DELETE, never GET. Enforce same-origin policy and CSRF protection where applicable. Real send and suppression require explicit confirmation. Never accept send through query strings.

# 49. Local Authentication Boundary

Phase 10.5 v1 is local-first. Reviewer labels are not authentication. If bound only to localhost, single local operator mode is acceptable. If remote/LAN binding is enabled later, require a separate authentication design first. Never expose an unauthenticated send console to LAN/Internet by default.

# 50. Settings Page

Route:

```text
/sales/settings
```

Show only safe status: LLM model, Ollama reachable/unreachable, database path, EMAIL_SEND_ENABLED, SMTP provider configured yes/no, trusted sender addresses, sender display name, Google Sheets configured yes/no, discovery source credential status. Never display SMTP password, API keys, OAuth refresh tokens or client secrets.

# 51. Email Provider Status

UI relies on Phase 9 provider abstraction. Current SMTP mode can show Provider, Configured, Sending enabled, Trusted senders. Future Gmail API and Microsoft Graph providers must be addable without redesigning Draft Review.

# 52. Export / Spreadsheet Access

Provide actions such as Export current filtered view, Export Phase 7 workbook, Export CSV, and Open/copy Google Sheet link if available. Excel/Sheets remain secondary views; SQLite/API-backed system is source of truth.

# 53. Import vs Export Clarity

Keep `Import Historical Data`, `Import Manual Seeds`, `Export Current Leads`, and `Export Review Workspace` clearly separate.

# 54. Performance

Target comfortable local operation for thousands of companies, tens of thousands of observations/evidence rows, and thousands of drafts/send events. Use indexed queries, server-side pagination, and lazy-loading detail sections where useful.

# 55. Accessibility / Usability

Minimum: keyboard-reachable controls, clear focus state, meaningful labels, readable status badges, no state communicated by color alone, confirmation text for destructive/send actions, and normal laptop responsiveness.

# 56. No LLM Requirement For Console Rendering

UI rendering must not require an LLM. Pages render from structured backend data. No LLM call just to show Dashboard/Candidate/Draft data.

# 57. No Autonomous Actions

Phase 10.5 must never auto-approve, auto-send, auto-unsuppress, auto-reengage, or auto-reply.

# 58. API Reuse

Prefer existing endpoints. Add UI aggregation endpoints only if necessary. Do not duplicate service logic. Candidate detail, dashboard summary, timeline, and draft review aggregations must call authoritative domain services.

# 59. Recommended UI Module Structure

```text
backend/
└── sales_ui/
    ├── __init__.py
    ├── router.py
    ├── view_models.py
    ├── dashboard_service.py
    ├── timeline_service.py
    └── templates/
        ├── base.html
        ├── dashboard.html
        ├── discoveries.html
        ├── sources.html
        ├── candidates.html
        ├── candidate_detail.html
        ├── leads.html
        ├── drafts.html
        ├── draft_detail.html
        ├── approved.html
        ├── sent.html
        ├── watchlist.html
        ├── suppression.html
        ├── replies.html
        └── settings.html

static/
└── sales/
    ├── sales.css
    └── sales.js
```

# 60. Dashboard Aggregation Service

Create deterministic `DashboardSummary` fields for new_candidates, needs_research, watchlist, qualified, contacts, usable_contacts, drafts_pending_review, approved_not_sent, sent_today, sent_week, suppressed, historical_positive_replies. Read-only only.

# 61. Timeline Service

Create `CompanyTimelineEvent` with event_id, company/candidate ID, event_type, occurred_at, title, summary, source_ref, entity_ref, revision, safe_metadata. Timeline aggregates existing events; it must not rewrite original audit data.

# 62. UI Status Source of Truth

Never infer authoritative send/review status in JavaScript. Prefer server-derived action availability such as can_approve, can_edit, can_reject, can_request_changes, can_dry_run, can_send, can_suppress. UI may hide/disable actions, but backend still enforces them.

# 63. Real Send Confirmation

Before real send, show final immutable preview: From Name, From Email, To, Company, Subject, Full Body, Revision. Require deliberate confirmation. No one-click send from table rows in v1.

# 64. Exact Review Payload

Review UI must display the same logical payload protected by Phase 9: sender_name, sender_email, recipient_email, subject, body, revision. Do not display one body while sending another.

# 65. Draft Evidence Visibility

Expose service_used, evidence refs, job/project/company evidence, generation model metadata, language, tone. Evidence IDs should link back to source evidence when possible. Do not expose chain-of-thought.

# 66. Draft Comparison

When revision > 1, allow comparison of previous/new subject, body, and recipient. A simple line-level diff is sufficient. Sent revision remains immutable.

# 67. Historical Outreach Visibility

If company was contacted before the new system, show Previously contacted, last contacted date, historical channel, historical outcome, reply state. This makes known_company/reengagement_review clear.

# 68. Re-engagement UI

Phase 10.5 may display Re-engagement Review but must not create/send re-engagement automatically. Show old outreach, new signal, time since last contact, historical reply/outcome. Human chooses next action.

# 69. Opportunity Types

Display opportunity types such as direct_bim_delivery, mep_delivery, bim_coordination, overflow_capacity, automation, technology_partnership, channel_partnership, strategic_collaboration. Do not collapse all opportunities into one generic lead type.

# 70. Tier Presentation

Display Tier separately from Fit, Intent, and Opportunity. Never imply Tier 1 = best lead.

# 71. BIM Maturity Presentation

If unknown, display `Unknown`, not `No BIM`, unless explicit evidence supports that conclusion.

# 72. Data Provenance UI

Any key public fact used for qualification should have `View source` or `View evidence` affordance.

# 73. Empty States

Use explicit empty states such as `No candidates found in this run.`, `No drafts are awaiting review.`, `No sent messages yet.`, and `Live inbox is not connected yet.`

# 74. Audit of Human Actions

UI-triggered actions preserve reviewer/operator label, timestamp, action, entity, revision, optional note. No silent mutation.

# 75. Packaging

Release ZIP includes templates, static UI assets, source code, tests, docs. It excludes .env, runtime DB, SQLite, tokens, credentials, OAuth files, private keys, logs, browser session data.

# 76. Phase 10.5 API/Route Boundary Tests

Test that GET pages do not mutate state, POST actions call backend services, no UI route sends without Phase 9, no UI route approves without Phase 9, and no UI route writes SQLite directly.

# 77. Critical Regressions

```text
P10_5-REG-001 UI cannot approve and send in one action
P10_5-REG-002 Edit creates new Phase 9 revision and invalidates approval
P10_5-REG-003 Sent revision exposes no mutable workflow actions
P10_5-REG-004 Suppressed recipient has no usable send action
P10_5-REG-005 Dry Run creates zero provider/network calls
P10_5-REG-006 Real Send requires explicit confirmation path
P10_5-REG-007 Stale browser revision cannot overwrite current state
P10_5-REG-008 UI cannot bypass Phase 9 sender trust
P10_5-REG-009 UI cannot bypass Phase 9 send-disabled flag
P10_5-REG-010 UI never exposes secrets
P10_5-REG-011 Candidate page distinguishes verified evidence from assessments
P10_5-REG-012 Unknown BIM maturity is not displayed as "No BIM"
P10_5-REG-013 Historical contacted company is visibly marked before new draft action
P10_5-REG-014 Source failure is visible without hiding successful discoveries
P10_5-REG-015 UI performs zero LLM calls for normal page rendering
P10_5-REG-016 localhost is default bind
P10_5-REG-017 no mutating GET endpoints
P10_5-REG-018 Phase 10.5 cannot autonomously re-engage
P10_5-REG-019 Replies tab does not claim live inbox connection
P10_5-REG-020 release ZIP excludes runtime/browser secrets
```

# 78. Acceptance Tests

Cover Dashboard, Discoveries, Sources, Candidates list/filter/detail, Watchlist, Qualified Leads, Draft Review, Approve, Edit, Reject, Request Changes, Suppress, Dry Run, Approved queue, Explicit Send, Sent history, Settings safe-status, Replies placeholder, Historical timeline, Error rendering, Pagination, Search. Use temp DB and mock sender. No live email.

# 79. Visual Smoke Tests

For key pages verify 200 response, page title, primary navigation, main table/card, critical state badges, expected action buttons by state. Pixel-perfect screenshot matching is optional and not the primary correctness mechanism.

# 80. Security Acceptance

Verify localhost default, no secret rendered, no credentials in HTML source, no secrets in JS payload, no mutating GET, CSRF/same-origin protection, send requires POST, suppression requires POST, no force=true control, no direct DB mutation.

# 81. Definition of Done

Phase 10.5 is complete when all of the following hold:

1. Local console is available at `/sales`.
2. Default bind is localhost.
3. Dashboard reflects real backend state.
4. Discovery runs are visible.
5. Sources can be viewed/configured safely.
6. Historical/manual imports have preview + confirmation.
7. Candidate list/filter/detail works.
8. Evidence provenance is visible.
9. Tier/Fit/Intent/BIM maturity remain separate.
10. Watchlist works through Phase 10.
11. Qualified leads are visible.
12. Contacts are visible with email status.
13. Draft review shows exact Phase 9 review payload.
14. Approve never sends.
15. Edit creates a new revision.
16. Reject/request-changes follow Phase 9.
17. Suppression follows Phase 9.
18. Dry Run performs zero provider calls.
19. Real send is explicit and separately confirmed.
20. Sent revisions are read-only.
21. Sent history is visible.
22. Historical outreach is visible.
23. Company timeline is available.
24. Replies tab accurately states live inbox is not yet connected.
25. No normal UI rendering requires LLM.
26. No frontend path bypasses Phase 9.
27. No secret is exposed in UI or release artifacts.
28. P10_5-REG-001..020 all pass.
29. Existing Phase 1–10 regression suite remains green.
30. Release ZIP denylist remains clean.

# 82. Explicit Non-Goals

Do NOT implement in Phase 10.5:

```text
live mailbox synchronization
IMAP polling
Gmail API inbox ingestion
Microsoft Graph inbox ingestion
automatic reply classification
AI suggested reply generation
automatic replies
meeting/calendar automation
open tracking
click tracking
autonomous follow-up sequences
cloud multi-user authentication
CRM replacement
```

These belong to later phases, primarily Phase 11 and beyond.

# 83. Future Phase 11 Compatibility

Design the UI so Phase 11 can add Conversation Thread, Inbound Reply, Reply Classification, Suggested Response, Approve/Edit/Reject Reply, Send Reply, Meeting Status without structural rewrite.

# 84. Final Implementation Report

When implementation is complete, report files created, routes added, templates added, view models, dashboard aggregation, timeline implementation, candidate UI, source UI, historical import UI, draft review UI, approval/edit/reject/DNC flow, dry-run flow, send-confirmation flow, sent-history UI, settings security, replies placeholder, critical regressions, full repository test result, and release ZIP security result.

Do not declare Phase 10.5 frozen until its dedicated QA specification is executed.
