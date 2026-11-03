# Phase 10 Adversarial Round-2 Patch
## Residual blockers found after verification of the first adversarial remediation

Status:

```text
Phase 9: FROZEN
Phase 10 Build: PATCHED, BUT NOT YET GOLDEN-QA READY
Phase 10 Golden QA: DO NOT RUN YET
Phase 10.5: DO NOT START
```

Independent verification of the new ZIP confirmed:

```text
Archive files: 274
README-TEST commit: 4e1ce95
phase10_latest commit: 4e1ce95
filename denylist: clean
tests/test_phase10_regressions.py: 24/24 PASS independently
```

The submitted Phase 10 adversarial suite could not be collected in the independent
environment because it imports `backend.main` at module import time and the independent
environment does not contain `chromadb`. This is an environment-only collection issue.

However, direct independent attack scripts against the Phase 10 production modules
reproduced the blockers below.

Do not alter frozen Phase 1–9 behavior.

---

# BLOCKER 1 — Actual Qualification Path Still Bypasses Real Phase 9 Domain Suppression

The first patch fixed `Phase9AuthorityBridge`, but the actual qualification function still does:

```python
suppression_store.is_suppressed(
    email=None,
    company_domain=candidate.company_domain
)
```

Frozen Phase 9 `SuppressionStore.is_suppressed()` immediately returns `False` when
`email` is empty.

Worse, the FastAPI/CLI `CompanyClassifier` is created with no suppression store at all.

Independent reproduction:

```text
real Phase 9 suppression:
email = optout@blocked.test
company_domain = blocked.test

CompanyClassifier(suppression_store=real_store)
→ qualified_for_enrichment
→ cold_outreach_allowed = true
→ suppressed = false
```

This is a real safety bypass.

## Required fix

Create ONE read-only Phase 9 suppression adapter and use it everywhere Phase 10
qualifies or exposes qualification:

```text
Phase9AuthorityBridge
qualify_candidate
CompanyClassifier
FastAPI qualification endpoint
CLI qualification command
future Phase 10.5 qualification path
```

Do not call the frozen Phase 9 API incorrectly with `email=None`.

Domain suppression must be checked from explicit domain suppression state.

Email suppression must be checked only when an actual recipient/business email is known.

Add:

```text
P10-ADV2-001 real Phase 9 domain suppression blocks classifier qualification
P10-ADV2-002 FastAPI qualification path uses authoritative Phase 9 suppression
P10-ADV2-003 CLI qualification path uses authoritative Phase 9 suppression
```

---

# BLOCKER 2 — Individual Email Suppression Incorrectly Suppresses Entire Domain

Current `Phase9AuthorityBridge.is_suppressed(domain=...)` contains:

```sql
SELECT 1 FROM suppression_list
WHERE LOWER(email) LIKE '%@domain'
```

and also a synthetic:

```text
domain-check@domain
```

fallback.

This changes Phase 9 suppression scope.

Independent reproduction:

```text
Phase 9 suppression entry:
person@example.com
company_domain = NULL
```

Frozen Phase 9 result:

```text
someoneelse@example.com + example.com
→ NOT suppressed
```

Current Phase 10 bridge:

```text
domain=example.com
→ suppressed
```

A single email-level opt-out therefore becomes an unintended whole-domain block.

## Required fix

Preserve Phase 9 scope exactly:

```text
email suppression -> exact email only
domain suppression -> explicit company_domain only
```

Remove:

```text
email LIKE %@domain
synthetic domain-check@domain
```

as domain-suppression mechanisms.

Add:

```text
P10-ADV2-004 email-only suppression does not suppress unrelated addresses on same domain
P10-ADV2-005 explicit company_domain suppression blocks the domain
```

---

# BLOCKER 3 — Stale / Non-Current Job Evidence Still Produces High Intent

`evaluate_fit_and_intent()` currently scores all evidence regardless of:

```text
is_current = false
freshness_status = old
```

Independent reproduction:

```text
MarketEvidence:
category = job
freshness_status = old
is_current = false
```

Observed:

```text
intent = high
```

This violates the Phase 10 freshness contract.

## Required fix

Intent scoring must ignore or heavily downgrade evidence that is explicitly:

```text
is_current = false
freshness_status = old
expired
stale
```

Historical evidence remains stored and visible but cannot represent current intent.

Add:

```text
P10-ADV2-006 old/non-current job evidence does not produce current high intent
P10-ADV2-007 fresh current job evidence can produce current intent
P10-ADV2-008 newer current evidence can supersede current assessment without deleting history
```

---

# BLOCKER 4 — Source Credential Requirements Are Modeled But Not Enforced

`brave_job_search` currently has:

```text
requires_api_key = true
credential_env_var = null
```

and discovery execution does not enforce missing credentials.

A fetcher can run even when the required credential is not configured.

## Required fix

Every credentialed source must declare:

```text
credential_env_var
```

Example:

```text
BRAVE_SEARCH_API_KEY
```

Before execution:

```text
requires_api_key = true
+
missing environment credential
→ controlled source-specific error
→ fetcher/adaptor NOT invoked
```

No secret value may enter logs/reports.

Add:

```text
P10-ADV2-009 missing required source credential produces controlled source failure
P10-ADV2-010 configured credential allows source execution without exposing the secret
```

---

# BLOCKER 5 — Source `max_results_per_run` Is Not Enforced

Independent reproduction against:

```text
brave_job_search
max_results_per_run = 50
```

with a deterministic fetcher returning 60 records:

```text
observations_found = 60
new_candidates = 60
```

The configured limit is ignored.

## Required fix

Enforce:

```text
SourceConfig.max_results_per_run
```

before candidate ingestion.

Do not silently overrun source budgets.

Add:

```text
P10-ADV2-011 source max_results_per_run is enforced
P10-ADV2-012 run metrics reflect only accepted/processed results
```

---

# BLOCKER 6 — Unknown Source Types Are Accepted

A synthetic configuration containing:

```yaml
type: totally_unknown
```

loads successfully into `SourceRegistry`.

Golden QA requires unknown source types to be rejected.

## Required fix

Validate source type against the Phase 10 supported taxonomy:

```text
job_board
ats
company_directory
business_directory
map_business
professional_association
tender_portal
project_database
industry_news
company_website
manual_seed
historical_import
search_engine
```

Unknown type:

```text
configuration error / controlled source error
```

not silently accepted.

Add:

```text
P10-ADV2-013 unknown source type is rejected
```

---

# BLOCKER 7 — Discovery API/CLI Has No Actual Source Adapter Execution

`POST /api/sales/discovery/run` and CLI `run` call:

```python
run_multi_source_discovery(...)
```

without `fetcher_funcs`.

Inside the engine:

```python
fetcher = fetcher_funcs.get(src)
if not fetcher:
    continue
```

Therefore a normal API/CLI discovery request can finish successfully while executing
zero configured discovery sources and returning zero discoveries.

This is not a functional multi-source discovery run.

## Required fix

Introduce a real source-adapter registry.

At minimum wire:

```text
existing Phase 3 / ATS job discovery
manual/historical seed path
supported company-first adapters
supported project/tender adapters
```

For configured sources that do not yet have an adapter:

```text
explicit NOT_AVAILABLE / source error
```

Do not mark a no-op source as successful.

`enabled source runs` must mean an actual adapter invocation or an explicit controlled failure.

Add:

```text
P10-ADV2-014 API discovery invokes registered adapter
P10-ADV2-015 CLI discovery invokes registered adapter
P10-ADV2-016 configured source without adapter is explicit failure/not_available, not silent success
P10-ADV2-017 existing job-first Phase 3 path is actually bridged, not simulated
```

---

# BLOCKER 8 — Phase 8 Grounding Still Trusts Arbitrary `extracted_data` If a URL Exists

The patch only blocks:

```text
manual_seed without source_url
```

but any other arbitrary observation can provide a URL and be treated as verified.

Independent reproduction:

```text
source_type = internal_note
source_id = internal_note
source_url = https://example.com
extracted_data = {
  project_title: "Fabricated Project",
  technologies: ["ImaginaryTech"]
}
```

Observed:

```text
verified_projects includes "Fabricated Project"
verified_technologies includes "ImaginaryTech"
```

A URL alone is not provenance verification.

## Required fix

Phase 8 bridge should prefer/require `MarketEvidence` objects or observations meeting
an explicit trusted provenance policy.

Require:

```text
recognized source type
traceable source ID
public source URL or stable public source reference
eligible evidence category/type
non-internal evidence
```

Internal/manual assessment content must never become a verified external fact solely
because a URL string exists.

Add:

```text
P10-ADV2-018 arbitrary/internal observation cannot become verified Phase 8 fact
P10-ADV2-019 eligible sourced MarketEvidence can become grounded fact
P10-ADV2-020 source URL alone is insufficient verification
```

---

# BLOCKER 9 — Historical Event Hash Does Not Detect Several Material Changes

The new content-stable hash correctly ignores filename/sheet/row movement, but currently
hashes only approximately:

```text
company
domain
emails
contact_date
reply_date
reply_status
```

It ignores material event content such as:

```text
follow_up_date
respondent identity
meeting status
opportunity status
intent evidence
relationship type
opportunity types
```

Independent reproduction:

Same company/contact/date but changed:

```text
follow_up_date
respondent_name
notes
```

produced the exact same hash.

## Required fix

Separate:

```text
stable company/contact identity
```

from:

```text
historical outreach event identity
```

Historical event hash should remain file/row independent but change when a materially
different outreach/reply event exists.

Add:

```text
P10-ADV2-021 renamed/reordered identical event remains idempotent
P10-ADV2-022 materially changed follow-up/reply event gets a distinct event identity
P10-ADV2-023 duplicate identical event remains deduplicated
```

Do not use free-form notes alone as the only identity discriminator.

---

# BLOCKER 10 — Historical Import API Accepts Arbitrary Server File Paths

Current API accepts:

```json
{"file_path": "..."}
```

and directly executes:

```python
Path(req.file_path)
```

If the path exists and has an allowed extension, the server reads it.

This violates:

```text
P10-SEC-003 Historical File Path Traversal Rejected
```

## Required fix

Preferred:

```text
multipart/uploaded file
```

or use a strict configured import root and resolve/validate:

```text
resolved path must remain inside allowed root
```

Reject:

```text
../
absolute arbitrary filesystem paths
symlink escapes
```

Add:

```text
P10-ADV2-024 historical import rejects traversal/out-of-root path
P10-ADV2-025 allowed import-root CSV/XLSX works
```

---

# BLOCKER 11 — Phase 10 QA Report Generator Is Not Execution-Driven

Current:

```text
scripts/generate_phase10_report.py
```

creates every case as:

```python
status = "PASS"
critical_regressions[id] = "PASS"
```

and emits invariant booleans as unconditional:

```python
True
```

It does not derive results from actual pytest execution.

This is exactly the false-pass pattern forbidden by the Phase 10 Golden QA contract.

A failing test could still produce a fully green Phase 10 report.

## Required fix

Port the hardened execution-driven reporting approach from Phase 9.

Required behavior:

```text
case ID -> actual pytest node/evaluator result
failed node -> FAIL
skipped optional -> NOT_RUN
unmapped deterministic -> NOT_RUN
no automatic PASS
no unconditional invariant=True
no hardcoded false_pass_count
```

Safety/diagnostic counters must be derived from execution artifacts.

Add QA meta-tests:

```text
P10-ADV2-026 failed test cannot appear PASS in phase10_latest
P10-ADV2-027 unmapped deterministic case cannot appear PASS
P10-ADV2-028 Phase 10 invariants derive from mapped execution
P10-ADV2-029 Phase 10 safety counters are dynamic, not literal constants
```

---

# BLOCKER 12 — Phase 10 Report Schema Is Still Incomplete

Current pre-Golden `phase10_latest.json` has the 50 tests, but required Phase 10 report
fields are absent/null, including:

```text
review
not_run
unmapped_deterministic_cases
false_pass_count

duplicate_company_count
overmerge_count
fake_job_count
historical_duplicate_count
cold_outreach_bypass_count
suppression_bypass_count

phase5_regression_count
phase8_grounding_violation_count
phase9_send_violation_count

runtime_db_packaged_count
source_partial_failure_loss_count
```

## Required fix

Make these fields execution-derived.

Do not fill unknown/unexecuted checks with misleading zero.

During final Golden QA all required deterministic metrics must resolve.

---

# BLOCKER 13 — Phase 10.5 UI Bridge Is Still Missing Contract Data

The candidate-list summary currently omits important fields such as:

```text
country
qualification reason
historical outreach summary
```

`Phase10ConsoleBridge.get_candidate_detail()` returns observations, but evidence is only
manually appended by one FastAPI route rather than being part of the bridge contract.

The Golden QA contract explicitly requires:

```text
P10-UIBRIDGE-001 Candidate List Has Summary Fields
P10-UIBRIDGE-002 Candidate Detail Has Evidence
P10-UIBRIDGE-003 Source Health Available
P10-UIBRIDGE-004 Watchlist Status Available
P10-UIBRIDGE-005 Qualification Reason Available
```

## Required fix

Make the Phase 10.5 bridge itself return a stable UI-ready contract.

Persist or deterministically derive qualification reason.

Add:

```text
P10-ADV2-030 candidate list exposes required summary fields
P10-ADV2-031 candidate detail bridge includes evidence/provenance directly
P10-ADV2-032 qualification reason is available after qualification and process restart
```

---

# BLOCKER 14 — Persistent Source Health Is Not Used By The Console Bridge After Restart

The patch added SQLite `source_health`, but:

```python
Phase10ConsoleBridge.get_source_health_summary()
```

still returns:

```text
SourceRegistry in-memory health_map
```

After restart, the persisted health table exists but the UI bridge loses historical health.

## Required fix

Hydrate SourceRegistry from persisted source health or have the console bridge query
CandidateStore persistence.

Add:

```text
P10-ADV2-033 source health survives restart in Phase 10.5 bridge output
```

---

# BLOCKER 15 — `/api/sales/sources/import` Does Not Import Or Validate Input

The endpoint currently ignores its `payload` and only calls:

```python
registry._load_configs()
```

This does not satisfy:

```text
POST /api/sales/sources/import
P10-API-008 source import validation
```

## Required fix

Define the intended behavior explicitly:

Option A:
- import/validate supported source configuration submitted by operator

Option B:
- remove/rename this endpoint if the contract is actually "reload config"

Do not expose an endpoint named `import` that ignores its payload.

No secret values may be accepted into source-controlled YAML.

Add proper validation/error tests.

---

# BLOCKER 16 — Candidate Classification Wrapper Has A Broken Method Signature

`CompanyClassifier.classify_bim_maturity()` calls the module-level function with:

```python
classify_bim_maturity(evidence, technologies, services)
```

but the module-level `classify_bim_maturity()` accepts only:

```python
evidence
```

This wrapper will raise `TypeError` when used.

## Required fix

Make wrapper/function signatures consistent.

Add:

```text
P10-ADV2-034 CompanyClassifier.classify_bim_maturity public method works
```

---

# BLOCKER 17 — Default Runtime Candidate DB Is Disconnected From The Frozen Phase 9 Store

FastAPI and CLI default Phase 10 database to:

```text
candidates.db
```

Phase 9 safety state lives in the frozen sales-outreach store.

A separate candidate DB is acceptable only if Phase 10 explicitly wires a read-only Phase 9
safety adapter using the authoritative Phase 9 database path.

Currently that wiring is absent from FastAPI/CLI classification services.

## Required fix

Choose and document one architecture:

### Preferred safe option

Keep Phase 10 tables in the shared configured SQLite database:

```text
data/sales_outreach.db
```

with additive migrations.

OR:

Keep a separate candidate DB, but explicitly instantiate and inject the frozen Phase 9
ReviewStore/SuppressionStore from the authoritative Phase 9 DB for every qualification path.

Do not silently create a disconnected `candidates.db` in the working directory.

Add startup/config tests proving that Phase 10 qualification sees actual Phase 9 suppression.

---

# ADDITIONAL DISCOVERY SEMANTICS

Also correct run accounting where appropriate:

```text
observations_found = source observations returned/accepted
observations_created = genuinely new persisted observation IDs
new_candidates = new logical companies
known_candidates = existing logical companies observed again
updated_candidates = existing candidates materially updated
```

Do not increment `observations_created` for an idempotent already-existing observation.

---

# REQUIRED ROUND-2 TEST SET

Add at least:

```text
P10-ADV2-001..034
```

as defined above.

Keep all existing:

```text
P10-REG-001..024
P10-ADV-001..026
```

green.

---

# EXECUTION ORDER AFTER PATCH

Run:

```text
python -m pytest tests/test_phase10_regressions.py -v
python -m pytest tests/test_phase10_adversarial.py -v
python -m pytest <new round-2 adversarial tests> -v

python -m pytest tests/acceptance/test_phase9_acceptance.py -v
python -m pytest tests/acceptance/test_qa_integrity.py -v

python -m pytest tests/ -q
```

Do not execute live SMTP or other opt-in external tests.

---

# REPORT / METADATA

Regenerate an execution-derived:

```text
tests/reports/phase10_latest.json
README-TEST.md
SalesAI_export.zip
```

Require:

```text
final Git HEAD
==
phase10_latest git_commit
==
README-TEST Git Commit
```

Keep the release denylist clean.

---

# FREEZE / GOLDEN RULE

After this Round-2 patch:

```text
DO NOT declare Phase 10 frozen.
```

Return the ZIP for one more short independent attack verification.

If the above residual blockers are closed, then proceed immediately to:

```text
PHASE10-QA-GOLDEN-SPEC.md
```

and execute the comprehensive deterministic Golden QA.

Only after Golden QA passes with:

```text
false_pass_count = 0
unmapped_deterministic_cases = 0
all mandatory P10-REG-001..015 PASS
all safety counters valid
full frozen regression suite green
release ZIP clean
```

may Phase 10 be formally frozen.
