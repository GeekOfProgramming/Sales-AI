# Phase 10.5 Adversarial Patch
## Local Sales Operator Console — Pre-QA Safety & Contract Fixes

Status:

```text
Phase 9: FROZEN
Phase 10: ACCEPTED / FROZEN WITH DOCUMENTED RESIDUAL RISK
Phase 10.5 Build: IMPLEMENTED, NOT QA-READY YET
Phase 10.5 QA: DO NOT RUN UNTIL THIS PATCH PASSES
```

This is a focused UI/send-safety patch. Do not redesign the console and do not start Phase 11.

---

# 1. CRITICAL — Domain Suppression Is Not Enforced By The Actual Send Gate

Independent reproduction against the submitted build:

```text
suppression entry:
  email = anchor@blocked.test
  company_domain = blocked.test

SuppressionStore.is_suppressed("other@blocked.test")
→ False

SuppressionStore.is_suppressed("other@blocked.test", "blocked.test")
→ True

SendValidator.validate_pre_send(draft to other@blocked.test)
→ VALID / allowed
```

Current `SendValidator` calls:

```python
suppression_store.is_suppressed(recip_email)
```

without the recipient/company domain.

Therefore the previous assumption that the frozen Phase 9 send gate fully mitigates the Phase 10 domain-suppression residual is not true for domain-scoped suppression.

## Required fix

Preserve exact-email suppression and add real domain-scope enforcement at pre-send.

Derive the recipient domain safely from the approved recipient email and call:

```python
suppression_store.is_suppressed(
    recip_email,
    company_domain=recipient_domain,
)
```

Do not change email-only suppression into domain suppression.

Required tests:

```text
P10_5-ADV-001 exact suppressed email is blocked
P10_5-ADV-002 explicit suppressed domain blocks a different recipient on same domain
P10_5-ADV-003 email-only suppression does not block a different recipient on same domain
P10_5-ADV-004 UI draft/approved queues reflect the same authoritative send suppression result
```

---

# 2. Candidate Suppression UI Uses Domain As If It Were An Email

Current candidate list/detail does:

```python
suppression_svc.is_suppressed(dom)
```

where `dom` is a company domain.

That is not the `SuppressionStore` contract.

A domain-suppressed company can therefore display as not suppressed.

## Required fix

Create a small UI/read adapter for authoritative suppression state:

```text
exact email check when email known
domain check only against company_domain scope
```

Candidate list/detail, draft queue, approved queue and send eligibility should all consume one consistent backend-derived suppression state.

The DNC form must distinguish scope explicitly:

```text
email
domain
```

Do not ask for “Domain or Email” in one ambiguous field.

---

# 3. CRITICAL — No CSRF / Same-Origin Protection On Mutating Routes

The Build Spec explicitly requires CSRF/same-origin protection.

Current mutating forms have no CSRF token and the router has no Origin/Referer enforcement.

This matters even on localhost: an external web page can submit a cross-site HTML form to a localhost endpoint.

Critical routes include:

```text
/drafts/{id}/approve
/drafts/{id}/edit
/drafts/{id}/reject
/drafts/{id}/dry-run
/drafts/{id}/send
/drafts/{id}/suppress
/do-not-contact/add
/do-not-contact/remove
/candidates/{id}/suppress
/discoveries/run
```

## Required fix

Implement one consistent CSRF/same-origin mechanism.

Acceptable local-first design:

```text
server-generated CSRF token in session/cookie + hidden form token
```

or a robust same-origin token middleware equivalent.

Do not rely on CORS as CSRF protection.

Required tests:

```text
P10_5-ADV-005 mutation without CSRF token rejected
P10_5-ADV-006 valid local form token accepted
P10_5-ADV-007 cross-origin mutation rejected
P10_5-ADV-008 send cannot be triggered cross-site even with confirm_send=true
```

---

# 4. Historical Import Does Not Implement Preview + Confirmation

The Phase 10.5 Build Spec requires:

```text
preview
rows detected
companies matched
new companies
possible duplicates
contacts detected
historical outreach events
invalid rows
warnings
explicit operator confirmation
```

Current route immediately imports after submitting a `file_path`.

## Required fix

Implement a two-step workflow:

```text
POST /sales/sources/import-history/preview
→ read/parse only
→ no authoritative import mutation
→ render preview + stable import token/hash

POST /sales/sources/import-history/confirm
→ verify preview token/hash
→ execute import
```

No import on the preview request.

Required tests:

```text
P10_5-ADV-009 preview performs zero import mutations
P10_5-ADV-010 confirmation imports the exact previewed file/content
P10_5-ADV-011 changed file/content invalidates preview confirmation
```

---

# 5. Historical Import UI Does Not Enforce Allowed Import Root

The UI claims “root restriction”, but current route does:

```python
Path(file_path.strip())
```

and accepts any existing CSV/XLSX path.

## Required fix

Reuse the safe Phase 10 import-root/path validation rather than duplicating file-path logic.

Reject:

```text
absolute arbitrary paths
../ traversal
symlink escape
files outside configured import root
```

Prefer uploaded file handling later, but a strict configured local import root is sufficient for Phase 10.5.

Add:

```text
P10_5-ADV-012 out-of-root historical path rejected
P10_5-ADV-013 allowed-root CSV/XLSX can be previewed
```

---

# 6. Real-Send Confirmation Is Not The Required Final Immutable Preview

Current confirmation modal shows:

```text
Company
Recipient
Subject
Revision
```

The Build Spec requires the final exact preview to show:

```text
From Name
From Email
To
Company
Subject
Full Body
Revision
```

The operator must see the exact final content before real transmission.

## Required fix

Populate the confirmation modal from the server-rendered current approved revision or fetch a read-only server preview immediately before send.

Do not trust mutable client-only values as send input.

Send endpoint still uses only:

```text
draft_id
revision
confirm_send
```

and Phase 9 reloads authoritative payload.

Add:

```text
P10_5-ADV-014 send confirmation displays exact sender + recipient + subject + body + revision
P10_5-ADV-015 modal cannot alter authoritative payload
```

---

# 7. Request Changes Workflow Is Missing

Phase 10.5 acceptance contract includes:

```text
Approve
Edit
Reject
Request Changes
Suppress
Dry Run
```

There is no Request Changes UI route/action in the submitted console.

## Required fix

Add a UI action that calls the existing Phase 9 request-changes service/state transition.

It must not send, approve or mutate a sent revision.

Add:

```text
P10_5-ADV-016 request-changes transitions through Phase 9 only
P10_5-ADV-017 sent revision cannot request changes
```

---

# 8. Settings / Source Status Contains Hardcoded Or Misleading Health State

Current settings renders:

```text
ollama_reachable = True
ChromaDB = Local / Operational
```

without runtime verification.

Current Sources page also displays `Configured` for any source where `requires_api_key=true`, without checking whether the credential is actually configured.

## Required fix

Never claim operational/configured state without evidence.

Use:

```text
Configured / Not configured
Reachable / Offline / Unknown
Operational / Unavailable / Unknown
```

based on safe backend checks.

No secrets may be rendered.

Add:

```text
P10_5-ADV-018 missing source credential displays Not configured
P10_5-ADV-019 unavailable Ollama/Chroma is not reported as operational
```

---

# 9. Evidence URLs Need Safe-Scheme Validation

Candidate evidence currently renders arbitrary `source_url` directly into `href`.

Jinja escaping does not make a `javascript:` URI safe.

## Required fix

Only make evidence links clickable for:

```text
http://
https://
```

Everything else should render as non-clickable text / invalid source.

Add:

```text
P10_5-ADV-020 javascript/data/file evidence URLs are never clickable
P10_5-ADV-021 http/https public evidence URLs remain clickable
```

---

# 10. End-to-End Operator Workflow Is Incomplete

The current console can review already-existing drafts but does not expose a clear UI path to generate a draft from an eligible qualified lead/candidate.

The Build Spec includes candidate action:

```text
Generate Draft
```

and best-contact visibility/selection.

## Required fix

Add a safe operator action for eligible `qualified_for_enrichment` leads:

```text
Select/confirm eligible contact
→ Generate Draft through Phase 8 API/service
→ new draft enters pending_review
```

Guards:

```text
historically contacted / active conversation → no new cold draft
suppressed → no draft generation
missing usable work email → no draft generation
watchlist → no draft generation
```

No auto-approval or auto-send.

Add:

```text
P10_5-ADV-022 eligible qualified lead can explicitly generate pending-review draft
P10_5-ADV-023 watchlist/known/reengagement/suppressed cannot start fresh cold draft
P10_5-ADV-024 generation never approves or sends
```

---

# 11. Global Search / Pagination Contract

Current header search points only to `/sales/candidates` and filters only company/domain.

The Build Spec requires search across:

```text
company name
domain
contact name
business email
draft subject
```

Current candidate pagination first loads up to 1000 records and slices in memory, rather than true server-side pagination.

## Required fix

Before QA, at minimum:

- implement a deterministic global search aggregation route/service
- keep canonical entity links
- make candidate pagination query DB/service with limit/offset rather than loading 1000

Treat this as functional acceptance, not send-safety blocker.

---

# 12. Release Metadata Is Not Synchronized To The Claimed Phase 10.5 Commit

Submitted report claims:

```text
Git Commit: 41f7b23
```

but inside the ZIP:

```text
README-TEST.md Git Commit = ac5e51d
phase10_latest.json git_commit = ac5e51d
```

There is no dedicated Phase 10.5 QA report yet, which is acceptable before QA, but the build metadata must not claim commit parity that the archive does not contain.

## Required fix

Update README-TEST with the actual Phase 10.5 build commit.

For the later Phase 10.5 Golden report require:

```text
final HEAD
==
phase10_5_latest git_commit
==
README-TEST Git Commit
```

---

# REQUIRED PRE-QA TEST RUN

Keep existing:

```text
P10_5-REG-001..020
```

and add:

```text
P10_5-ADV-001..024
```

Then run:

```text
python -m pytest tests/test_phase10_5_console.py -v
python -m pytest tests/test_phase10_5_adversarial.py -v
python -m pytest tests/ -q
```

No live email.
Use mock provider for send-path tests.

Rebuild ZIP and re-run denylist.

---

# NEXT STEP

Do NOT declare Phase 10.5 frozen after this patch.

Return the ZIP for one short independent verification.

If these focused issues are closed, proceed to a dedicated:

```text
PHASE10.5-QA-GOLDEN-SPEC.md
```

Then freeze Phase 10.5 and move to Phase 11 Inbox / Reply Intelligence.
