# SalesAI Phase 10.5 QA / Golden Acceptance Spec
## Local Sales Operator Console
### Practical Freeze Gate for Current Weak Local LLM

# 0. Acceptance Philosophy

Phase 10.5 QA must validate the local operator console without reopening the
adversarial patch loop for cosmetic or model-quality issues.

Current model:

```text
qwen2.5:1.5b
```

is intentionally treated as a weak temporary local LLM.

Therefore:

```text
deterministic safety / workflow failures = real FAIL
semantic email-quality limitations caused by the weak LLM = REVIEW / DEFERRED_MODEL
cosmetic UI imperfections = non-blocking unless they hide or misrepresent safety state
```

Practical acceptance target:

```text
all CRITICAL SAFETY gates must PASS
overall deterministic QA should be >= 60% PASS/verified
0 critical send/suppression/approval bypasses
0 runtime DB / secret packaging violations
```

Do not create another large patch cycle for non-critical UI or weak-model quality.

---

# 1. Baseline

Required:

```text
Phase 9: FROZEN
Phase 10: ACCEPTED / CLOSED
Phase 10.5: Built + patched, not yet frozen
```

Do not alter frozen Phase 1–10 deterministic behavior during QA unless a genuine
critical safety blocker is discovered.

No live SMTP send belongs to Golden QA.

Use Mock provider / dry-run for send-path testing.

---

# 2. QA Files

Create:

```text
tests/acceptance/test_phase10_5_acceptance.py
tests/golden/phase10_5_console.json
tests/reports/phase10_5_latest.json
```

Extend where useful:

```text
tests/acceptance/test_qa_integrity.py
README-TEST.md
```

Reuse the existing implementation tests:

```text
tests/test_phase10_5_console.py
tests/test_phase10_5_adversarial.py
tests/test_phase10_5_final_patch.py
```

Do not duplicate tests merely to inflate counts.

---

# 3. Mandatory Critical Safety Gates

The following must PASS before Phase 10.5 freeze.

## P10_5-GOLD-001 — Approve Never Sends

Approval:

```text
pending_review -> approved
```

must produce:

```text
provider delivery count = 0
```

No approve-and-send action.

---

## P10_5-GOLD-002 — Edit Invalidates Approval

Editing send-relevant content must:

```text
create a new revision
review_status = pending_review
send_status = not_sent
old approval cannot authorize new revision
```

---

## P10_5-GOLD-003 — Sent Revision Is Immutable

A sent revision cannot be edited/rejected/request-changed/reset in place.

---

## P10_5-GOLD-004 — Suppressed Recipient Cannot Send

Verify both:

```text
exact email suppression
explicit company-domain suppression
```

through the authoritative Phase 9 send gate.

The UI must not bypass it.

---

## P10_5-GOLD-005 — Dry Run Has Zero Network Delivery

Dry Run executes pre-send validation only.

Expected:

```text
provider send calls = 0
network delivery = 0
```

---

## P10_5-GOLD-006 — Real Send Requires Explicit Confirmation

There must be no one-click real-send from list/detail pages.

Final confirmation must show the exact authoritative payload:

```text
From Name
From Email
To
Company
Subject
Full Body
Revision
```

and explicitly state:

```text
This will send one real email.
```

---

## P10_5-GOLD-007 — Send-Disabled Cannot Be Bypassed

When:

```text
EMAIL_SEND_ENABLED = false
```

real send is blocked.

---

## P10_5-GOLD-008 — Sender Trust Cannot Be Bypassed

UI cannot replace or invent an unauthorized sender to pass validation.

---

## P10_5-GOLD-009 — CSRF / Same-Origin

All mutating console actions must require valid CSRF/same-origin protection.

Mutating GET endpoints are forbidden.

---

## P10_5-GOLD-010 — Secrets Never Render

Pages/API responses must never render:

```text
SMTP_PASSWORD
API keys
OAuth tokens
client secrets
private keys
raw environment secret values
```

Configuration may show:

```text
Configured
Not configured
Configuration error
```

only.

---

## P10_5-GOLD-011 — No Autonomous Re-Engagement

Historical/known/reengagement candidates cannot silently restart a cold sequence.

---

## P10_5-GOLD-012 — Zero Autonomous Send

Normal UI rendering, discovery, watchlist, qualification, draft generation and review
must never automatically send email.

---

# 4. Phase 8 / Phase 6 / Phase 9 Integration

## P10_5-GOLD-013 — Real Phase 10 Qualification Recognized

A real:

```text
qualification_outcome = qualified_for_enrichment
```

candidate can enter the draft-generation workflow when all safety guards pass.

---

## P10_5-GOLD-014 — Ineligible Qualification Routes Stay Blocked

Block:

```text
watchlist
known_company
reengagement_review
suppressed
reject
research_more
```

from fresh cold drafting.

---

## P10_5-GOLD-015 — Existing Contact Required

Draft generation must resolve an existing backend contact.

Browser-typed arbitrary recipient identity cannot become authoritative.

---

## P10_5-GOLD-016 — Contact Email Status

Normal cold-draft path permits:

```text
verified
likely
```

and blocks:

```text
risky
unknown
not_found
```

unless a frozen preview-only Phase 8 mode explicitly permits otherwise.

---

## P10_5-GOLD-017 — Generate Draft Uses Phase 8

The Console must delegate subject/body generation to the frozen Phase 8 stack.

The UI router must not independently author sales-email copy.

---

## P10_5-GOLD-018 — Phase 8 Metadata Preserved

Phase 9 review draft preserves, where present:

```text
evidence_refs
active service
generation_model
prompt_version
language
tone
```

---

## P10_5-GOLD-019 — New Draft Enters Pending Review

Generated draft lands as:

```text
review_status = pending_review
send_status = not_sent
```

Never approved automatically.

---

## P10_5-GOLD-020 — Insufficient Grounding Is Safe

If Phase 8 cannot produce a grounded draft:

```text
no draft is created
operator receives a controlled message
```

---

# 5. Human Review Workflow

Validate:

```text
Approve
Edit
Reject
Request Changes
DNC / Suppress
Dry Run
Confirmed Send
```

Each must call the authoritative backend/service layer.

The browser must not implement duplicate state-machine logic.

---

# 6. DNC / Suppression UI

DNC action must:

```text
create Phase 9 suppression state
block later real send
remain visible in UI
```

No UI control may silently unsuppress a recipient.

---

# 7. Historical Import

Required workflow:

```text
Select historical CSV/XLSX
-> Preview
-> Operator Confirm
-> Import
```

Tests:

```text
allowed root file works
../ traversal rejected
outside-root absolute path rejected
unsupported extension rejected
cancel/no-confirm causes no import
confirmed import remains idempotent
```

Do not expose arbitrary server-file read capability.

---

# 8. Candidate Page

Candidate detail must clearly distinguish:

```text
raw evidence / observations
internal assessments
qualification
contacts
historical outreach
drafts
sent activity
audit timeline
```

Internal assessments must not be presented as externally verified facts.

---

# 9. BIM Display

If:

```text
bim_maturity = unknown
```

UI must display:

```text
Unknown
```

not:

```text
No BIM
No BIM capability
```

---

# 10. Watchlist

Validate:

```text
watchlist candidates visible
watch reason visible
no direct real-send action
no autonomous draft generation
```

Promotion remains a backend Phase 10 decision.

---

# 11. Source Health

Sources page should display:

```text
enabled/disabled
last success
last failure
sanitized error
credential status
```

Credential status must use:

```text
SourceConfig.credential_env_var
```

Never expose the credential value.

Failure of a source must not hide previously discovered candidates.

---

# 12. Evidence URL Safety

Only:

```text
http://
https://
```

may become clickable evidence links.

Unsafe schemes such as:

```text
javascript:
data:
file:
```

must not become executable/clickable links.

---

# 13. Settings

Settings should show truthful operational status where available.

Do not fabricate service health.

Allowed states include:

```text
Available
Unavailable
Configured
Not configured
Unknown
```

Never show secret values.

---

# 14. Replies Page

Phase 10.5 must clearly state that live mailbox synchronization is NOT connected.

Expected banner equivalent:

```text
Live mailbox synchronization is not connected yet.
Planned for Phase 11.
```

Do not fabricate reply threads.

---

# 15. Localhost / Deployment Boundary

Default console bind must remain:

```text
127.0.0.1:8000
```

No default public/LAN bind.

Remote/multi-user deployment is out of scope.

---

# 16. UI Rendering / LLM Boundary

Normal console pages must render with:

```text
0 LLM calls
```

LLM invocation belongs only to explicit draft-generation behavior through Phase 8.

---

# 17. Weak-LLM Semantic Evaluation

Because the current model is:

```text
qwen2.5:1.5b
```

email semantic quality is NOT a hard Phase 10.5 freeze gate.

Evaluate a small deterministic/sample set for:

```text
valid draft shape
grounding
no fabricated company facts
active service safety
reasonable subject/body structure
```

Results may be:

```text
PASS
REVIEW
DEFERRED_MODEL
```

Do NOT fail Phase 10.5 solely because:

```text
wording is generic
personalization is weak
style is mediocre
CTA is not ideal
```

provided deterministic grounding/safety rules remain intact.

Record these for re-evaluation when the system is moved to the stronger laptop / larger model.

---

# 18. Core Screen Smoke Tests

Verify HTTP 200 / expected state for:

```text
/sales
/sales/discoveries
/sales/sources
/sales/candidates
/sales/candidates/{id}
/sales/leads
/sales/drafts
/sales/drafts/{id}
/sales/approved
/sales/sent
/sales/watchlist
/sales/suppression
/sales/replies
/sales/settings
```

Expected empty-state pages are valid.

---

# 19. Pagination / Filters

For pages with potentially many records:

```text
search works
filtering works
pagination does not mutate data
empty result is handled safely
```

Do not require heavy frontend performance optimization in Phase 10.5.

---

# 20. Stale Revision Protection

A stale browser page must not overwrite or send a newer draft revision.

Expected:

```text
conflict / refresh required
```

not silent overwrite.

---

# 21. Audit Trail

Review/send-relevant operator actions should remain auditable:

```text
approve
edit
reject
request changes
suppress
dry run
send attempt
send result
```

Do not require a new analytics system.

---

# 22. Sent Page

Sent history must be read-only / immutable.

Show:

```text
recipient
subject
revision
timestamp
send status
safe attempt/audit details
```

No credentials.

---

# 23. Release Security

Release ZIP must exclude:

```text
.env
.env.*
*.db
*.sqlite
*.sqlite3
sales_outreach.db
chroma runtime DB
credentials
OAuth secrets
private keys
token.json
runtime logs
__pycache__
*.pyc
```

Sanitized test fixtures are allowed.

---

# 24. Phase 10.5 QA Report

Create:

```text
tests/reports/phase10_5_latest.json
```

Required fields:

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

pass_rate

critical_safety_total
critical_safety_passed
critical_safety_failed

false_pass_count
unmapped_deterministic_cases

provider_calls_in_dry_run
provider_calls_for_approve
unauthorized_send_count
suppression_bypass_count
stale_revision_bypass_count
csrf_bypass_count
secret_exposure_count
mutating_get_count
autonomous_reengagement_count
ui_llm_render_call_count
runtime_db_packaged_count
```

Counters should be execution-derived where feasible.

Do not fake precision for unavailable telemetry.

Unknown evidence should be:

```text
null / NOT_RUN
```

rather than a misleading zero.

---

# 25. Existing Regression Suites

All existing Phase 10.5 implementation suites should remain green:

```text
tests/test_phase10_5_console.py
tests/test_phase10_5_adversarial.py
tests/test_phase10_5_final_patch.py
```

Existing frozen Phase 1–10 suites must not gain new failures.

---

# 26. Full Repository Regression

Run:

```text
python -m pytest tests/ -q
```

No live SMTP.

External/network tests remain opt-in.

---

# 27. Practical Freeze Rule

Phase 10.5 may be accepted/frozen when:

```text
1. All critical safety gates P10_5-GOLD-001..012 PASS.
2. No real-send suppression bypass is found.
3. Approve/Edit/Dry Run/Send state machine remains Phase 9-authoritative.
4. Generate Draft delegates to Phase 8.
5. Contact selection obeys Phase 6 eligibility.
6. Normal UI render performs zero LLM calls.
7. No secret/runtime DB enters release ZIP.
8. Full repository has zero new deterministic failures.
9. Overall deterministic QA pass/verified rate is >= 60%.
10. Weak-LLM semantic limitations are documented as REVIEW/DEFERRED_MODEL rather than endlessly patched.
```

Non-critical failures may be documented and deferred.

Do not reopen a patch loop for:

```text
minor styling
copy wording
weak personalization
minor dashboard layout
non-essential convenience filters
```

---

# 28. Freeze Output

Final QA response must report:

```text
total cases
mapped cases
executed cases
PASS
FAIL
REVIEW
NOT_RUN
DEFERRED_MODEL
overall deterministic pass rate

critical safety PASS/FAIL

P10_5-GOLD-001..020 status
existing REG/ADV/FINAL suite results
full repository result

known residual risks
weak-model deferred items

git commit
README-TEST commit
phase10_5_latest commit
ZIP file count
denylist result
```

If accepted:

```text
PHASE 10.5 FROZEN
```

Then proceed to:

```text
Phase 11 — Inbox / Reply Intelligence
```

Phase 11 may assume:

```text
the operator console exists
Phase 9 owns outbound send
Phase 10 owns discovery/qualification
Phase 8 owns outbound draft generation
Phase 10.5 owns human operation UI
```
