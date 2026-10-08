# Phase 10 Adversarial Patch
## Independent Attack Review Findings Before Golden QA

Status:

```text
Phase 9: FROZEN
Phase 10 Build: IMPLEMENTED BUT NOT FREEZE-READY
Phase 10 Golden QA: DO NOT RUN YET
```

Independent verification of the submitted ZIP confirmed:

```text
Archive files: 269
tests/test_phase10_regressions.py: 24 PASS
Release filename denylist: clean
```

However, the current 24-case implementation test suite does not cover several
critical Phase 10 contract paths. The following issues were independently
reproduced against the submitted archive.

Do not modify frozen Phase 1–9 behavior unless explicitly noted.
Prefer fixing Phase 10 adapters/bridges rather than reopening Phase 9.

---

# BLOCKER 1 — Historical Cold-Outreach Guard Is Not Monotonic

## Reproduction

1. Import a historically contacted company.
2. Candidate is correctly stored as:

```text
historical_contact_status = contacted
cold_outreach_allowed = false
```

3. Upsert a fresh/stale `CandidateCompany` with the same candidate identity and
default fields.

Observed after upsert:

```text
historical_contact_status = uncontacted
cold_outreach_allowed = true
```

This reopens the exact cold-outreach path Phase 10 is required to block.

## Root cause

`CandidateStore.save_candidate()` uses unconditional conflict updates for:

```text
historical_contact_status
active_conversation
suppressed
cold_outreach_allowed
```

An incoming default model can therefore erase stronger historical safety state.

## Required fix

Generic discovery/upsert must never weaken safety state.

At minimum, make these sticky/monotonic during ordinary upsert:

```text
previous outreach detected
historical contacted/replied state
suppressed
active conversation
cold_outreach_allowed = false
```

A generic rediscovery must not turn false back to true.

If the product later needs to close an active conversation or remove suppression,
use a dedicated explicit workflow/service, never a generic candidate upsert.

Add adversarial regression:

```text
P10-ADV-001
historically contacted candidate + stale/default rediscovery
→ cold_outreach_allowed remains false
→ historical_contact_status cannot regress to uncontacted
```

Also test:

```text
suppressed true cannot become false through generic upsert
active_conversation true cannot become false through generic upsert
```

---

# BLOCKER 2 — Real Phase 9 Domain Suppression Is Bypassed

The current Phase 10 test uses a mock with:

```python
is_domain_suppressed(domain)
```

but the real frozen Phase 9 `SuppressionStore` does not expose that method.

The real method is:

```python
is_suppressed(email, company_domain=None)
```

and currently returns false when `email` is empty.

## Independent reproduction

A real Phase 9 suppression entry was created with:

```text
email = dummy@blocked.test
company_domain = blocked.test
```

Then Phase 10 checked the candidate by domain only.

Observed:

```text
Phase9AuthorityBridge:
cold_outreach_allowed = true
status = new

qualify_candidate with real SuppressionStore:
domain suppression was not recognized
```

The existing P10-REG-010 passes only because the mock API is stronger than the
real Phase 9 API.

## Required fix

Do NOT change frozen Phase 9 behavior.

Implement a Phase 10 read-only suppression adapter that understands the actual
Phase 9 storage/API.

It must support:

```text
email suppression
domain suppression
```

using the real frozen store.

Possible safe approach:

- use Phase 9 `list_suppressed()` / authoritative store access
- canonicalize domain
- compare against existing suppression entries
- never mutate Phase 9 suppression data

Both:

```text
Phase9AuthorityBridge
qualify_candidate
```

must use the same adapter.

Add:

```text
P10-ADV-002 real Phase 9 domain suppression blocks qualification
P10-ADV-003 real Phase 9 email suppression blocks qualification when contact known
```

Do not use a fake `is_domain_suppressed()` API in the regression.

---

# BLOCKER 3 — Historical Boolean Parsing Treats "No" As True

Current historical parser uses Python `bool(value)` for fields such as:

```text
DNC
Active Conversation
Wrong Email
```

For spreadsheet strings:

```text
"No"
"False"
"0"
```

Python truthiness is true because they are non-empty strings.

## Independent reproduction

Input:

```text
DNC = "No"
Active Conversation = "No"
Wrong Email = "No"
```

Observed:

```text
is_dnc = true
has_active_conversation = true
is_wrong_email = true
```

This can incorrectly suppress valid companies and corrupt historical state.

## Required fix

Create a deterministic boolean parser.

Example accepted truthy values:

```text
true
yes
y
1
si
sì
x
```

False values:

```text
false
no
n
0
empty/null
```

Unknown non-empty values should not silently become true.

Use the parser for every historical boolean/status field.

Add:

```text
P10-ADV-004 historical "No"/"False"/"0" parse as false
P10-ADV-005 historical "Yes"/"True"/"1" parse as true
```

---

# BLOCKER 4 — Phase 7 Additive Export Bridge Crashes

Current `CandidateCompany` stores:

```text
outsourcing_potential: str
automation_potential: str
```

but `Phase7ExportBridge` calls:

```python
candidate.outsourcing_potential.value
candidate.automation_potential.value
```

## Independent reproduction

Observed:

```text
AttributeError: 'str' object has no attribute 'value'
```

Therefore the Phase 7 retrofit is not currently production-valid.

## Required fix

Use a deterministic helper such as:

```python
value = field.value if hasattr(field, "value") else str(field)
```

or convert these fields to explicit enums consistently across storage/model/export.

Add actual bridge regressions for all additive Phase 10 columns.

Required:

```text
P10-ADV-006 Phase 7 export bridge succeeds for default candidate
P10-ADV-007 additive Phase 10 columns do not alter/remove frozen Phase 7 columns
```

---

# BLOCKER 5 — Phase 10.5 Watchlist Query Bridge Crashes

`CandidateStore.get_watchlist()` currently returns dictionaries.

`Phase10ConsoleBridge.get_watchlist_summary()` treats each result as an object:

```python
w.company_id
w.watchlist_id
w.reason
...
```

## Independent reproduction

Observed:

```text
AttributeError: 'dict' object has no attribute 'company_id'
```

The current P10-REG-021 tests candidate list/detail and source health only; it
does not test the Watchlist UI bridge.

## Required fix

Use the actual CandidateStore contract consistently.

The Phase 10.5 backend bridge must provide:

```text
candidate list summary
candidate detail
candidate evidence
source health
watchlist state
qualification reason
historical outreach state
relationship_type
opportunity_types
```

Add:

```text
P10-ADV-008 console watchlist summary works
P10-ADV-009 candidate detail includes evidence/provenance
P10-ADV-010 qualification reason is exposed to future UI
```

---

# BLOCKER 6 — Discovery Observation Provenance Is Lost On Persistence

The in-memory `DiscoveryObservation` contains fields like:

```text
source_name
discovery_mode
raw_payload
extracted_data
```

but the SQLite observation table and `add_observation()` do not persist them.

Also `DiscoveryEngineV2.discover_company()` does not correctly populate core
provenance fields such as:

```text
source_id
source_type
source_url
observed_company_name
observed_domain
country
```

## Independent reproduction

Create a manual/source observation with payload and extracted data, save it,
then reload from CandidateStore.

Observed after round-trip:

```text
source_name = null
source_id = unknown
source_type = unknown
source_url = null
observed_company_name = ""
raw_payload = {}
extracted_data = {}
```

This breaks:

```text
P10-OBS provenance
Phase 8 evidence bridge
Phase 10.5 candidate evidence UI
auditability
```

## Required fix

Extend model + SQLite migration + persistence to preserve the complete required
observation contract.

At minimum:

```text
source_id
source_name
source_type
source_url
source_external_id
discovery_mode

observed_company_name
observed_domain
country
region
city
observed_company_type

raw_title
raw_snippet
raw_payload
extracted_data

observed_at
published_at
fetched_at
confidence
evidence_refs
raw_ref
```

Do not silently discard source context.

Add:

```text
P10-ADV-011 observation round-trip preserves provenance
P10-ADV-012 job_first observation remains job_first after reload
P10-ADV-013 raw/extracted observation evidence survives persistence
```

---

# BLOCKER 7 — Phase 8 Grounding Bridge Trusts Arbitrary Extracted Data As "Verified"

Current `Phase8GroundingBridge.extract_grounded_facts()` accepts values from
`observation.extracted_data` and places them under keys such as:

```text
verified_technologies
verified_projects
verified_jobs
```

without requiring real provenance.

## Independent reproduction

A `manual_seed` observation was supplied with:

```json
{
  "project_title": "Fabricated Project",
  "technologies": ["Revit"]
}
```

Observed result:

```text
verified_projects = Fabricated Project
verified_technologies = Revit
```

No source URL or trusted evidence was required.

This can convert manual/internal data into factual Phase 8 email evidence.

## Required fix

The Phase 8 bridge must consume grounded `MarketEvidence` or observations that
meet explicit provenance requirements.

Recommended rule:

A factual claim may enter Phase 8 only if it has:

```text
traceable source
eligible evidence category
source type
source URL or stable public source reference
non-internal evidence classification
```

`manual_seed` labels/assessments alone cannot become verified external facts.

Do not rely only on phrase blacklists.

Add:

```text
P10-ADV-014 ungrounded manual extracted data cannot become verified fact
P10-ADV-015 sourced public project/technology evidence can become grounded fact
P10-ADV-016 internal opportunity/outsourcing assessments never become factual evidence
```

---

# BLOCKER 8 — Source Errors Leak Secrets

Current discovery error handling stores/logs raw exception text.

## Independent reproduction

Fetcher raised:

```text
Authorization: Bearer SUPERSECRET token=abc123
```

Observed in:

```text
DiscoveryRunRecord.errors
SourceHealth.last_error
logger output
```

This violates Phase 10 security/reporting requirements.

## Required fix

Use centralized sanitization before:

```text
logging
run error persistence
source health
API responses
reports
```

Reuse an appropriate existing sanitizer if possible without coupling source
logic to SMTP-specific semantics, or create a generic secret sanitizer.

Mask:

```text
Authorization headers
Bearer tokens
API keys
passwords
known configured secret values
```

Add:

```text
P10-ADV-017 source exception secrets are sanitized everywhere
```

---

# BLOCKER 9 — Source Registry Schema Is Only Partially Implemented

The YAML contains fields such as:

```text
countries
company_types
requires_api_key
max_results_per_run
discovery_mode[]
base_url
```

but `SourceConfig` currently persists only a subset.

Observed for `brave_job_search`:

```text
countries: not represented
company_types: not represented
requires_api_key: not represented
credential_env_var: not represented
max_results_per_run: not represented
base_url: not represented
discovery_mode field typed as str but receives list
```

Therefore source-limit/config/security behavior cannot be enforced correctly.

## Required fix

Implement and validate the actual source registry schema.

Include:

```text
source_id
name
type
enabled
priority
countries[]
company_types[]
discovery_modes[]
requires_api_key
credential_env_var
max_results_per_run
rate_limit_hint
base_url/public endpoint metadata
```

Validate known source types.

Missing required credential must result in a controlled source-specific error.

Add P10-SRC Golden cases at implementation level now.

---

# BLOCKER 10 — Markets Registry Is Broken / Incomplete

`markets.yaml` currently contains only:

```text
Italy
Germany
United Kingdom
Switzerland
```

The final Phase 10 contract requires the configured initial target set:

```text
Italy
Germany
Austria
Switzerland
France
Spain
Netherlands
United Kingdom
Ireland
United Arab Emirates
Saudi Arabia
Oman
Australia
Canada
United States
```

Also `SourceRegistry` stores `markets` directly as a list, while:

```python
get_market(...)
```

calls:

```python
self.markets.get(...)
```

which is invalid for a list.

## Required fix

Normalize markets into a deterministic mapping keyed by `market_id`.

Support enabled/disabled market lookup.

Add all required initial market configs.

Add:

```text
P10-ADV-018 market registry lookup works
P10-ADV-019 all required initial markets load
```

---

# BLOCKER 11 — Required Phase 10 APIs Are Missing

Independent static inspection found no actual FastAPI routes for:

```text
POST /api/sales/discovery/run
GET  /api/sales/discovery/runs
GET  /api/sales/discovery/runs/{run_id}

GET  /api/sales/candidates
GET  /api/sales/candidates/{candidate_id}

POST /api/sales/candidates/{candidate_id}/research
POST /api/sales/candidates/{candidate_id}/qualify
POST /api/sales/candidates/{candidate_id}/watch

GET  /api/sales/sources
POST /api/sales/sources/import

POST /api/sales/history/import
GET  /api/sales/watchlist
```

A Python bridge class is not equivalent to an API endpoint.

The reconciliation report must not call these "query endpoints" unless they are
actually wired into FastAPI.

## Required fix

Implement the Phase 10 APIs from the Build Spec.

Requirements:

```text
no send endpoint under Phase 10
structured errors
temporary/test DB override support
no secrets returned
Phase 9 suppression authority preserved
```

Add P10-API-001..013.

---

# BLOCKER 12 — Required CLI Entry Point Is Missing

No Phase 10 discovery CLI entry point was found.

Required:

```text
python -m sales_engine.discovery_v2.run
```

or equivalent project-standard command.

It must support local deterministic discovery arguments and perform zero send.

Add CLI smoke tests.

---

# BLOCKER 13 — Discovery Runs / Source Health Are Not Persisted

Current:

```text
DiscoveryEngineV2.runs
SourceRegistry.health_map
```

are in-memory only.

A process restart loses run history and source health.

The Build contract requires SQLite persistence for:

```text
discovery_runs
source_health
```

and the future Phase 10.5 UI relies on them.

## Required fix

Add additive SQLite tables/migrations.

Persist:

```text
run id
timestamps
markets/sources
counts
errors
status
config version

source health timestamps/counts/sanitized last error
```

Use stable unique run IDs (UUID or microsecond identity), not second-only timestamps.

Add restart-persistence tests.

---

# BLOCKER 14 — Historical Outreach Events Are Not Actually Imported

The importer writes:

```text
historical_imports
historical_contacts
```

but does not populate the existing:

```text
historical_outreach_events
```

table.

Therefore:

```text
contact event
follow-up
reply/outcome
historical timeline
```

are not represented as first-class outreach events.

This conflicts with the historical data contract and Phase 10.5 timeline needs.

## Required fix

For historical rows containing outreach/reply information, create idempotent
historical outreach events.

Preserve at minimum:

```text
contacted_at
follow_up_at
reply_at
reply_status
channel
contact identity
respondent identity
notes
source row reference
```

Add positive-outcome fields where applicable:

```text
meeting_status
opportunity_status
intent_evidence
relationship_type
opportunity_types
```

No statistical inference from these outcomes.

---

# BLOCKER 15 — Historical Import Identity Is Location-Based, Not Content-Stable

Current import hash is based on:

```text
source_file
sheet_name
row_number
company_name
domain
```

Renaming/copying the same workbook or moving a row can produce a new import ID.

That can duplicate historical events.

## Required fix

Use content-stable identity for historical records/events.

Include canonical business-relevant row content.

File/sheet/row remain provenance, not the sole identity.

Add:

```text
same row in renamed copy -> no duplicate event
same row moved to another row number -> no duplicate event
materially changed outreach event -> new event
```

---

# BLOCKER 16 — Missing Evidence Freshness Contract

`MarketEvidence` currently does not persist the complete final contract:

```text
published_at
freshness_status
```

and observation persistence is also missing several freshness/provenance fields.

## Required fix

Add additive model/database migration for:

```text
published_at
freshness_status
```

Allowed freshness:

```text
fresh
recent
old
unknown
```

Old/stale job evidence must not remain current intent.

History must remain append-only.

---

# BLOCKER 17 — Discovery Run Counters Are Semantically Wrong

`run_multi_source_discovery()` currently increments:

```text
candidates_discovered
```

for every returned source item, including already-known companies.

`candidates_updated` is not meaningfully populated.

This will produce misleading Phase 10 reports/UI metrics.

## Required fix

Track independently:

```text
observations_found
new_candidates
known_candidates / updated_candidates
watchlist_candidates
qualified_candidates
rejected_candidates
errors
```

Do not count repeated source observations as new companies.

---

# REPORT / RELEASE METADATA ISSUE

The submitted reconciliation report claims:

```text
Git Commit: 4fdd881
```

but the ZIP's current `README-TEST.md` still contains:

```text
Git Commit: 60c580c
```

There is also no:

```text
tests/reports/phase10_latest.json
```

in the archive.

This means Phase 10 reporting/metadata integration is not yet complete.

Before the ZIP is returned for the next attack review:

```text
README-TEST commit
==
Phase10 report commit
==
final repository HEAD
```

Do not manually type report success.

---

# PACKAGING

Current filename denylist inspection is clean.

Keep it clean after the patch.

Also exclude normal build/runtime cache artifacts where feasible:

```text
__pycache__
*.pyc
.pytest_cache
```

---

# REQUIRED ADVERSARIAL REGRESSION SET

Add at least:

```text
P10-ADV-001 historical guard cannot be weakened by generic upsert
P10-ADV-002 real Phase 9 domain suppression blocks qualification
P10-ADV-003 real Phase 9 email suppression blocks qualification
P10-ADV-004 "No"/"False"/"0" historical booleans parse false
P10-ADV-005 "Yes"/"True"/"1" historical booleans parse true
P10-ADV-006 Phase 7 additive export bridge does not crash
P10-ADV-007 Phase 7 legacy schema remains intact
P10-ADV-008 Phase 10.5 watchlist bridge works
P10-ADV-009 candidate detail exposes evidence/provenance
P10-ADV-010 qualification reason exposed
P10-ADV-011 observation provenance survives DB round-trip
P10-ADV-012 job_first mode survives DB round-trip
P10-ADV-013 raw/extracted evidence survives persistence
P10-ADV-014 ungrounded manual data cannot become verified Phase 8 fact
P10-ADV-015 sourced factual evidence can enter Phase 8 bridge
P10-ADV-016 internal assessments cannot enter factual evidence
P10-ADV-017 source errors redact secrets
P10-ADV-018 market registry lookup works
P10-ADV-019 all required initial markets load
P10-ADV-020 Phase 10 API surface exists and has no send endpoint
P10-ADV-021 CLI entry performs discovery with zero sends
P10-ADV-022 discovery run/source health survive process/store restart
P10-ADV-023 historical outreach events are imported idempotently
P10-ADV-024 renamed/reordered historical source does not duplicate events
P10-ADV-025 stale intent evidence is not current
P10-ADV-026 run new-vs-known counts are accurate
```

---

# DO NOT RUN GOLDEN QA YET

First apply this adversarial patch.

Then run:

```text
python -m pytest tests/test_phase10_regressions.py -v
<new adversarial Phase 10 tests> -v
existing frozen Phase 1–9 tests
full repository test suite
```

No live email.

Rebuild release ZIP.

Return:

```text
final ZIP
adversarial test results
full-suite results
git commit
README commit
Phase 10 report commit
denylist result
```

After independent verification of this patch, run:

```text
PHASE10-QA-GOLDEN-SPEC.md
```

Then, and only then, decide Phase 10 FREEZE.
