# SalesAI Production Readiness — Step 3
## Discovery Control UI & Saved Search Profiles
### Local Operator Page for Configuring and Running Discovery

# 0. Baseline

Use the current repository after accepted:

```text
Production Readiness Step 1 — Search Scope & Discovery Control
Production Readiness Step 2 — Source Management
```

Step 1 already provides:

```text
DiscoveryScope
DiscoveryScopeStore
DiscoveryRunner
run_discovery(scope_id)
```

Step 2 already provides:

```text
authoritative SourceRegistry
source enable/disable
source priority
source max-results
supported markets/modes
credential status
adapter status
source health
```

Do NOT redesign frozen Phase 1–12 behavior.

---

# 1. Goal

Create one clear local operator page where the user can:

```text
create a Search Profile
edit a Search Profile
duplicate a Search Profile
enable/disable a Search Profile

choose:
- discovery mode
- markets
- sources
- relationship types
- opportunity types
- company tiers
- BIM maturity
- keywords/signals/exclusions
- languages
- max results

preview the execution plan
run discovery explicitly
see which sources will actually execute
see skipped sources and reasons
see recent discovery runs
see results / errors / run metadata
```

The page is operational control only.

It must not change scoring, suppression, sending or conversation authority.

---

# 2. Primary UI Route

Create or upgrade:

```text
/sales/discovery
```

This should become the main operator discovery-control page.

Optional detail routes:

```text
/sales/discovery/profiles/{scope_id}
/sales/discovery/runs/{run_id}
```

Use the existing FastAPI + Jinja2 + lightweight JS approach.

No Node build system required.

---

# 3. Search Profiles

The UI term should be:

```text
Search Profile
```

Internally it maps directly to the existing:

```text
DiscoveryScope
```

Do not create a second profile model.

Examples:

```text
Italy BIM Contractors
Germany Revit Automation
UAE MEP Delivery
European Technology Partners
UK Digital Construction
```

---

# 4. Profile List

On `/sales/discovery`, show saved profiles with:

```text
Profile name
Enabled / Disabled
Discovery modes
Markets
Selected sources count
Max results
Last run
Last run status
Last results count
```

Actions:

```text
Run
Edit
Duplicate
Enable / Disable
View Runs
```

Prefer archive/disable over delete.

---

# 5. Create / Edit Profile Form

Expose existing DiscoveryScope fields.

## Basic

```text
scope_name
enabled
notes
```

## Discovery Modes

Multi-select:

```text
job_first
company_first
project_tender_first
manual_historical
```

## Markets

Multi-select:

```text
Italy
Germany
Austria
Switzerland
France
Spain
Netherlands
UK
Ireland
UAE
Saudi Arabia
Oman
Australia
Canada
USA
```

## Sources

Source selection must come from the authoritative Step 2 SourceRegistry.

For each source show:

```text
name
enabled/disabled
source type
adapter status
credential status
supported markets
supported discovery modes
health
```

Disabled / unavailable sources may be visible but must be clearly marked and not silently executable.

## Relationship Types

```text
service_buyer
technology_partner
channel_partner
strategic_partner
mixed
unknown
```

## Opportunity Types

```text
direct_bim_delivery
mep_delivery
bim_coordination
overflow_capacity
automation
revit_api
data_parameter_automation
custom_software
technology_partnership
channel_partnership
strategic_collaboration
unknown
```

## Tier

```text
tier1
tier2
tier3
unknown
```

## BIM maturity

```text
high
medium
low
unknown
```

## Optional Filters

```text
company_name_keywords
required_signals
excluded_keywords
languages
```

## Budget

```text
max_results
```

---

# 6. Search Profile Validation

Server-side validation is authoritative.

Reject:

```text
no discovery mode
no market
max_results <= 0
unknown source_id
unknown relationship type
unknown opportunity type
unknown tier
unknown BIM maturity
```

Do not trust browser values.

A profile may intentionally contain disabled/unavailable sources, but execution must skip
them truthfully.

---

# 7. Execution Plan Preview

Before real discovery, provide:

```text
Preview Run
```

Preview must perform ZERO external discovery calls.

It should calculate the effective plan:

```text
selected markets
selected modes
selected sources

sources executable
sources disabled
sources unavailable
sources missing credentials
sources incompatible with market
sources incompatible with discovery mode

effective max-results limits
```

Example:

```text
Profile: Germany Revit Automation

Executable:
- Brave Search / Germany / company_first
- Greenhouse / Germany / job_first

Skipped:
- Italy Engineering Directory
  reason: unsupported market

- Tender Source
  reason: disabled

- Some API Source
  reason: credential not configured
```

Preview is planning only.

No candidates.
No observations.
No analytics mutation.
No network request.

---

# 8. Run Discovery

Real run requires explicit:

```text
Run Discovery
```

action.

Use the existing:

```python
run_discovery(scope_id)
```

or equivalent service.

Do not reimplement discovery logic in the UI.

---

# 9. Run Confirmation

Before starting a real discovery run, show a confirmation summary:

```text
Profile
Markets
Modes
Executable source count
Maximum results
```

Button text should clearly indicate:

```text
Start Discovery
```

This is not a destructive action, so no excessive confirmation UX is required.

---

# 10. Run Status

Use existing `DiscoveryRun` / `scope_discovery_runs`.

Display:

```text
run_id
profile
started_at
finished_at
status
markets
modes
sources requested
sources executed
results_found
companies_created
companies_updated
errors
```

Allowed statuses should reflect existing implementation, e.g.:

```text
pending
running
completed
partial
failed
```

Do not fabricate live progress percentages unless they are actually measured.

---

# 11. Recent Runs

On `/sales/discovery`, show recent runs.

Columns:

```text
Time
Profile
Markets
Sources executed
Results
New companies
Updated companies
Status
```

Action:

```text
View Details
```

---

# 12. Run Detail

`/sales/discovery/runs/{run_id}` should show:

```text
profile snapshot / scope ID
start / finish
duration if measurable
markets
modes
sources requested
sources actually executed
skipped sources + reasons
results_found
companies_created
companies_updated
sanitized errors
```

Provide links to:

```text
Candidates
Discoveries / Observations
```

where appropriate.

---

# 13. Source Compatibility Display

Use Step 2 registry.

A selected source can be marked:

```text
Ready
Disabled
Not configured
Adapter unavailable
Unsupported market
Unsupported mode
Degraded
```

Do not automatically enable or modify the source from this page.

Source configuration remains owned by:

```text
/sales/sources
```

Provide a link:

```text
Manage Sources
```

---

# 14. Credential Display

Never show raw credential values.

Allowed:

```text
Configured
Not configured
Configuration error
Not required
```

If a profile contains a source requiring a missing credential:

```text
preview -> skip / not ready
real run -> source skipped with reason
```

Do not crash the entire discovery run if other sources are usable.

---

# 15. Profile Duplication

Operator should be able to duplicate a profile.

Example:

```text
Germany Revit Automation
→ Duplicate
→ Germany Revit Automation Copy
```

New:

```text
scope_id
created_at
updated_at
```

Same configuration values unless operator edits them.

---

# 16. Profile Enable / Disable

Disabled profile:

```text
cannot be executed from UI
```

Existing run history remains visible.

Do not delete its historical run records.

---

# 17. Delete / Archive

Hard delete is not required.

Preferred behavior:

```text
disable / archive
```

This preserves run history and analytics provenance.

---

# 18. No LLM Requirement

Normal page rendering, profile creation, preview and run configuration must use:

```text
0 LLM calls
```

Discovery adapters may later invoke their normal Phase 10 logic as already designed.

The UI itself does not require the LLM.

---

# 19. Security

All mutations:

```text
POST
```

with existing CSRF / Same-Origin protections.

No mutating GET.

No arbitrary source instructions executed here.

No raw SQL / shell / Python from profile inputs.

---

# 20. API

Use existing Step 1 APIs where possible.

Normalize/add only what the UI needs, such as:

```text
GET  /api/sales/discovery/scopes
GET  /api/sales/discovery/scopes/{scope_id}

POST /api/sales/discovery/scopes
POST /api/sales/discovery/scopes/{scope_id}/update
POST /api/sales/discovery/scopes/{scope_id}/duplicate
POST /api/sales/discovery/scopes/{scope_id}/enable
POST /api/sales/discovery/scopes/{scope_id}/disable

POST /api/sales/discovery/scopes/{scope_id}/preview
POST /api/sales/discovery/run

GET  /api/sales/discovery/runs
GET  /api/sales/discovery/runs/{run_id}
```

Follow existing project routing conventions.

No duplicate business logic.

---

# 21. Profile Snapshot / Reproducibility

A discovery run should remain understandable even if the profile is edited later.

Preferred:

store or expose enough run metadata to know:

```text
scope_id
profile name
markets at execution
modes at execution
sources requested
sources executed
max-results budget
```

Do not require the run to reinterpret the current edited profile to explain historical behavior.

---

# 22. Empty States

UI must handle:

```text
no profiles
no runs
no ready sources
all sources disabled
missing credentials
zero discovery results
partial failure
```

without crashing.

---

# 23. Practical UX

This should be usable by a non-developer operator.

Avoid requiring JSON editing for normal use.

Prefer:

```text
checkboxes
multi-selects
badges
forms
tables
clear status labels
```

JSON/API remain available for developers.

---

# 24. Required Tests

Create:

```text
tests/test_discovery_control_ui.py
```

Cover at least:

```text
PRD-DISCUI-001 discovery page renders with zero LLM calls
PRD-DISCUI-002 create profile from UI persists DiscoveryScope
PRD-DISCUI-003 edit profile updates existing scope without new identity
PRD-DISCUI-004 duplicate profile creates new scope ID
PRD-DISCUI-005 disabled profile cannot execute
PRD-DISCUI-006 source options come from authoritative SourceRegistry
PRD-DISCUI-007 preview performs zero external source calls
PRD-DISCUI-008 preview reports disabled source reason
PRD-DISCUI-009 preview reports missing credential reason
PRD-DISCUI-010 preview reports unsupported market/mode
PRD-DISCUI-011 real run delegates to existing DiscoveryRunner
PRD-DISCUI-012 real run preserves scope max-results enforcement
PRD-DISCUI-013 one failing source does not hide successful source results
PRD-DISCUI-014 recent run history renders correctly
PRD-DISCUI-015 run detail shows requested/executed/skipped sources
PRD-DISCUI-016 profile edit does not rewrite historical run metadata
PRD-DISCUI-017 no mutating GET
PRD-DISCUI-018 CSRF/same-origin protects mutations
PRD-DISCUI-019 raw credentials never appear in UI
PRD-DISCUI-020 Step 1 and Step 2 tests remain green
```

---

# 25. Full Regression

Run:

```text
python -m pytest tests/test_discovery_control_ui.py -v
python -m pytest tests/test_search_scope.py -v
python -m pytest tests/test_source_management.py -v
python -m pytest tests/ -q
```

No live email.

External discovery network calls should be mocked in deterministic tests.

---

# 26. Packaging

Rebuild:

```text
SalesAI_export.zip
```

Keep denylist clean.

---

# 27. Completion Gate

Step 3 is accepted when:

```text
operator can create/edit/duplicate Search Profiles
profiles use existing DiscoveryScope
source list comes from Step 2 registry
preview shows the real effective execution plan
preview makes zero network calls
Run Discovery delegates to existing runner
disabled/unavailable/missing-credential sources are skipped truthfully
run history/detail are visible
no credentials are exposed
no LLM is needed for normal control UI
Step 1 + Step 2 remain green
full repository has zero new deterministic failures
ZIP denylist is clean
```

Do NOT proceed to repository cleanup yet.

Return ZIP for review first.

---

# 28. Final Response

Return:

1. files created/modified
2. `/sales/discovery` UI structure
3. Search Profile workflow
4. preview behavior
5. real-run behavior
6. run history/detail behavior
7. source-registry integration
8. security/credential handling
9. focused test results
10. Step 1 test result
11. Step 2 test result
12. full repository result
13. Git commit
14. ZIP file count
15. denylist result
16. final SalesAI_export.zip
