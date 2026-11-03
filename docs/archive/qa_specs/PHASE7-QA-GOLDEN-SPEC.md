# SalesAI Phase 7 QA / Golden Acceptance Scaffold
## Export / Human Review Workspace / Google Sheets / CRM-Ready Output

## Purpose

Phase 7 QA validates that export is a faithful, deterministic, privacy-safe projection
of frozen Phase 5 / Phase 6 data.

Phase 7 must NOT:
- change scoring
- change qualification
- change best_contact
- merge distinct companies
- merge distinct contacts using weak identity
- fabricate jobs
- fabricate provider success
- call an LLM
- expose personal/private contact fields

The QA must make it possible to identify whether a future export defect comes from:

1. Canonical serialization
2. Stable identity generation
3. Lead/contact/job association
4. XLSX generation
5. CSV generation
6. CRM JSON generation
7. Google Sheets adapter behavior
8. Privacy filtering
9. Partial failure handling
10. Cross-phase contract drift

All deterministic Phase 7 tests should run NOW.
No stronger LLM is required.

---

# 1. Files to Create

```text
tests/golden/
├── phase7_exports.json
└── snapshots/
    └── exports/
        ├── p7_valid_enriched_leads.json
        ├── p7_domainless_ats_leads.json
        ├── p7_contact_identity_cases.json
        ├── p7_raw_jobs.json
        ├── p7_privacy_input.json
        └── expected/
            ├── leads_rows.json
            ├── contacts_rows.json
            ├── jobs_rows.json
            ├── crm_payload.json
            └── summary_metrics.json

tests/acceptance/
└── test_phase7_acceptance.py

tests/reports/
└── phase7_latest.json
```

Keep existing:

```text
tests/test_exports.py
```

Add or extend:

```text
tests/test_qa_integrity.py
```

README-TEST.md must include Phase 7.

---

# 2. Frozen Upstream Contract

Golden Phase 7 tests must use frozen EnrichedLead / CompanyLead fixtures.

Do NOT dynamically run Phase 5 or Phase 6 in the Golden acceptance suite.

A separate cross-phase contract test may instantiate real schemas,
but Phase 7 acceptance should not depend on model/provider execution.

Required Phase 5 scoring contract:

```text
fit_score      0..30
intent_score   0..30
recency_score  0..20
evidence_score 0..20

lead_score =
fit + intent + recency + evidence

MIN_QUALIFIED_SCORE = 60

qualified =
lead_score >= qualification_threshold
```

Golden fixtures must obey these bounds.

Do NOT use impossible values such as:

```text
fit_score = 38
intent_score = 36
```

---

# 3. Main Golden Case

## P7-EXP-001 — Full Multi-Format Export

Input:

One qualified EnrichedLead with:
- valid Phase 5 score breakdown
- company identity
- two contacts
- one best_contact
- three raw StructuredJobs
- grounded reasons/evidence
- enrichment metadata

Example score:

```json
{
  "fit_score": 30,
  "intent_score": 30,
  "recency_score": 20,
  "evidence_score": 14,
  "lead_score": 94,
  "qualification_threshold": 60,
  "qualified": true
}
```

Expected:

- exactly 1 Lead row
- exactly 2 Contact rows
- exactly 3 Job rows
- one CRM company/lead object
- same values in XLSX, CSV and JSON
- score fields unchanged
- qualification unchanged
- best_contact unchanged
- evidence preserved
- workflow defaults present
- no personal fields
- no invented values

---

# 4. Cross-Phase Contract Tests

## P7-CONTRACT-001 — Score Bounds Preserved

Assert exported values remain:

```text
fit <= 30
intent <= 30
recency <= 20
evidence <= 20
```

Phase 7 must not clamp, normalize, or recompute valid scores.


## P7-CONTRACT-002 — Arithmetic Preserved

Assert:

```text
lead_score =
fit_score +
intent_score +
recency_score +
evidence_score
```


## P7-CONTRACT-003 — Qualification Threshold Preserved

Input:

```text
qualification_threshold = 60
lead_score = 60
qualified = true
```

Expected export:

```text
qualification_threshold = 60
qualified = true
```

Critical:
Phase 7 must not hardcode 70.


## P7-CONTRACT-004 — No Score Mutation

Freeze original input before serialization.

After export verify:
- original lead_score unchanged
- component scores unchanged
- qualified unchanged
- best_contact unchanged


## P7-CONTRACT-005 — Workflow Defaults Only

Phase 7 may add:

```text
approval_status = pending_review
outreach_status = not_started
send_status = not_sent
```

Phase 7 must not auto-populate:
- draft_subject
- draft_body
- personalization_notes
- last_outreach_at

---

# 5. Stable Lead Identity Tests

## P7-ID-001 — Same Canonical Domain

Inputs:

```text
https://www.acme.com/
acme.com
HTTPS://ACME.COM/about
```

Expected:

same canonical company identity
same lead_id


## P7-ID-002 — Different Domains, Same Name

```text
ABC Engineering — abc-engineering.com
ABC Engineering — abc-engineering.de
```

Expected:

2 lead_ids
2 Lead rows


## P7-ID-003 — ATS Namespace Isolation

No domain.

Lead A:

```text
source = lever
source_company_key = acme
```

Lead B:

```text
source = greenhouse
source_company_key = acme
```

Expected:

```text
lead_id A != lead_id B
```

Do NOT use raw:

```text
source_key:acme
```

without provider namespace.


## P7-ID-004 — Same Namespaced Source Identity

Multiple jobs/records with:

```text
lever:acme
```

Expected:

one logical Lead.


## P7-ID-005 — Name Fallback

No domain and no source identity.

Identical normalized company name:

Expected deterministic lead_id.

Do not use random UUID.

---

# 6. Stable Contact Identity Tests

Strong identity priority:

```text
normalized work email
→ canonical LinkedIn URL
→ provider + provider_person_id
```

Weak fallback identity must not be used to collapse humans.


## P7-CID-001 — Same Email

Apollo + Hunter:

```text
Jane.Smith@Acme.com
jane.smith@acme.com
```

Expected:

same canonical contact identity.


## P7-CID-002 — Same LinkedIn

Provider records with canonical-equivalent LinkedIn URL.

Expected:

same contact identity.


## P7-CID-003 — Same Provider Person ID

Same:

```text
provider = apollo
provider_person_id = person_123
```

Expected:

same identity.


## P7-CID-004 — Same Name, Different Strong Identity

Same name/title/company.

Different emails.

Expected:

2 contacts.


## P7-CID-005 — Weak Identity Must Survive

Two contacts:

```text
Alex Smith
BIM Manager
acme.com
Apollo
no email
no LinkedIn
```

and:

```text
Alex Smith
BIM Manager
acme.com
Hunter
no email
no LinkedIn
```

Expected:

2 exported Contact rows.

Phase 7 must NOT re-deduplicate contacts that Phase 6 intentionally kept separate.


## P7-CID-006 — One Email Missing

Contact A:
same name/company
no email

Contact B:
same name/company
work email exists

Expected:

do not collapse solely by name/company.

Do not attach the known email to the unidentified record.


## P7-CID-007 — Provider Order Independence

Serialize contacts in both input orders.

Expected:

same logical contact set
same stable IDs
same deterministic ranking/order where contract applies.

---

# 7. Job Audit Tests

## P7-JOB-001 — Raw Job Preservation

Input real frozen StructuredJob records.

Expected exported job fields exactly preserve:

- title
- URL
- source
- source_company_key
- posted date
- technologies
- relevant signals
- evidence


## P7-JOB-002 — No Fabricated Jobs

`include_jobs = true`

but no raw StructuredJob objects supplied.

Expected:

0 fabricated job rows.

Do NOT synthesize:

```text
source = generic
job_url = ""
```

from aggregate lead data.


Expected audit diagnostic:

```text
stage = export
error_type = jobs_not_provided
```


## P7-JOB-003 — Domainless ATS Job Association

Lead identity:

```text
lever:acme
```

Raw job:

```text
source = lever
source_company_key = acme
```

Expected:

job linked to correct lead_id.


## P7-JOB-004 — Canonical Domain Matching

Lead:

```text
acme.com
```

Job company domain:

```text
https://www.acme.com/about
```

Expected:

correct association after canonicalization.


## P7-JOB-005 — Wrong Company Protection

Two leads with similar names but distinct strong identities.

Expected:

job cannot attach to wrong lead.

Critical FAIL if violated.

---

# 8. XLSX Tests

## P7-XLSX-001 — Workbook Opens

Generate XLSX.

Load with openpyxl.

Expected:
no corruption / exception.


## P7-XLSX-002 — Required Sheets

Exactly or at minimum:

```text
Leads
Contacts
Jobs
Errors_Audit
Summary
```


## P7-XLSX-003 — Header Freeze

Assert first/header row freeze exists on data sheets.


## P7-XLSX-004 — Auto Filter

Assert filter ranges exist.


## P7-XLSX-005 — Canonical Columns

Required fields exist exactly once.

No duplicate header names.


## P7-XLSX-006 — Row Counts

Expected Lead/Contact/Job row counts equal canonical serializer counts.


## P7-XLSX-007 — Best Contact Preservation

Flattened best-contact fields in Leads match the actual best_contact exactly.


## P7-XLSX-008 — Evidence Preservation

Long reasons/evidence cells contain full source value.

No silent truncation.


## P7-XLSX-009 — No Formula Mutation

Source-of-truth score/contact fields should be literal values,
not formulas that could recompute differently.


## P7-XLSX-010 — Null Serialization

Null Python/JSON fields become empty cells,
not strings such as:

```text
None
null
N/A
```

unless N/A is an intentional diagnostic display field.

---

# 9. CSV Tests

## P7-CSV-001 — Canonical Headers

CSV headers match canonical schema.


## P7-CSV-002 — Row Integrity

Quoted text containing:
- commas
- newlines
- quotes

round-trips correctly through Python csv parser.


## P7-CSV-003 — List Serialization

Spreadsheet/CSV list fields use exactly:

```text
" | "
```


## P7-CSV-004 — Empty Collection Headers

Zero Contacts or zero Jobs:

CSV still contains canonical header row.

Do NOT create a zero-byte / completely blank CSV.


## P7-CSV-005 — UTF-8

Company/contact names with non-ASCII characters survive:

```text
Müller
S.p.A.
São Paulo
Élodie
```

---

# 10. CRM JSON Tests

## P7-JSON-001 — Canonical Structure

Each lead contains:

```json
{
  "company": {},
  "lead": {},
  "best_contact": {},
  "contacts": [],
  "jobs": [],
  "audit": {}
}
```


## P7-JSON-002 — Arrays Remain Arrays

Reasons/evidence/technologies/data_sources remain arrays in JSON.

Do not serialize them as `"a | b"`.


## P7-JSON-003 — Null Remains Null

Do not convert null into empty strings unnecessarily in JSON.


## P7-JSON-004 — Cross-Format Equality

Canonical semantic values across XLSX, CSV and JSON must agree.

Normalization differences allowed only by declared serialization rules.

---

# 11. Privacy Tests

## P7-PRIV-001 — Personal Fields Excluded

Fixture intentionally contains forbidden keys:

```text
personal_email
personal_phone
mobile_phone
phone_numbers
api_key
access_token
```

Expected:

none appear in:
- XLSX
- CSV
- CRM JSON
- Errors_Audit
- workbook metadata


## P7-PRIV-002 — Public Email Is Not Upgraded

A rejected Gmail/Yahoo address must not reappear as work_email during export.


## P7-PRIV-003 — Secrets Not Leaked Through Error Text

Mock provider/export error includes secret-like request metadata.

Sanitize before export if production error object contains secrets.

At minimum test known credential fields.

---

# 12. Determinism / Idempotency

## P7-DET-001 — Stable Entity IDs

Run same logical input five times.

Expected:
same lead_ids
same contact_ids.


## P7-DET-002 — Stable Row Ordering

Same logical data in different input order.

Expected:
canonical row ordering identical,
except where ranked business ordering is explicitly meaningful.


## P7-DET-003 — Export Run ID Unique

Run two exports immediately.

Expected:

```text
export_run_id_1 != export_run_id_2
```

even within same second.


## P7-DET-004 — Entity ID Independent of Export Run

lead_id/contact_id must not contain or depend on export_run_id.


## P7-DET-005 — Identical Input Does Not Mutate

Deep-copy input before export.

Assert after export:

input == original_copy
```

---

# 13. Google Sheets Adapter Tests

Google Sheets live integration is OPTIONAL.

Mocks may be used only by explicit dependency injection in tests.


## P7-GS-001 — Disabled Google

Google Sheets disabled.

Expected:
local exports succeed
no Google call.


## P7-GS-002 — Explicit Mock Snapshot

Inject MockGoogleSheetClient explicitly.

Expected:
snapshot behavior deterministic.


## P7-GS-003 — Explicit Mock Upsert

Existing lead_id/contact_id rows update.

No duplicate entities.


## P7-GS-004 — Upsert Idempotency

Run same dataset twice.

Expected:
second run updates/no-ops
does not duplicate rows.


## P7-GS-005 — Different ATS Identity

lever:acme and greenhouse:acme.

Expected:
two Sheet entities.


## P7-GS-006 — No Fake Production Mock Success

Google enabled,
no injected mock,
no real client implementation.

Expected:

explicit controlled result such as:

```text
google_sheets_not_implemented
```

or integration/configuration error.

Must NOT return success from MockGoogleSheetClient.


## P7-GS-LIVE-001 — Real Google Sheets

Prepare but do NOT require in offline CI.

Run only after a real Google integration exists and credentials are configured.

Initial status:

NOT_RUN

not PASS
not REVIEW.

---

# 14. Export Failure Tests

## P7-ERR-001 — XLSX Fail, CSV/JSON Succeed

Expected:
successful CSV/JSON files retained
XLSX failure returned in errors
overall partial state explicit.


## P7-ERR-002 — Google Failure, Local Success

Expected:
local export remains available
Google failure exposed.


## P7-ERR-003 — All Requested Exporters Fail

Expected:
request fails explicitly.

Do not report success.


## P7-ERR-004 — Error Audit Row

Export/provider errors map into Errors_Audit with:

```text
stage
error_type
provider
message
source_reference
```

No silent failures.

---

# 15. Limits

## P7-LIMIT-001 — 5000 Leads

Expected:
accepted.


## P7-LIMIT-002 — 5001 Leads

Expected:
controlled rejection.

Do not silently truncate.


## P7-LIMIT-003 — qualified_only

Qualified + unqualified inputs.

Default true:

only qualified lead exported.

Explicit false:

both exported.


## P7-LIMIT-004 — include_all_contacts

Validate current declared behavior.

If false:
export only intended contact subset according to contract.

If true:
preserve all Phase 6 contacts.

No silent ambiguity.

---

# 16. Human Review Workflow Tests

## P7-WF-001 — Default Approval

Expected:

```text
approval_status = pending_review
```


## P7-WF-002 — Default Outreach

Expected:

```text
outreach_status = not_started
```


## P7-WF-003 — Default Send

Expected:

```text
send_status = not_sent
```


## P7-WF-004 — No Draft Generation

Expected:

```text
draft_subject = empty/null
draft_body = empty/null
personalization_notes = empty/null
```

Phase 7 must never call LLM/email generation.


## P7-WF-005 — Existing Workflow State

If a future re-export input already contains explicit workflow state,
test declared policy:

preserve vs reset.

This policy must be explicit before Phase 9.

Do not silently overwrite manually approved state.

Mark REVIEW until product policy is selected if not yet defined.

---

# 17. Summary Sheet Tests

## P7-SUM-001 — Total Lead Count

Matches exported Lead rows.


## P7-SUM-002 — Qualification Counts

qualified + unqualified = total.


## P7-SUM-003 — Enrichment Status Counts

Counts complete / partial / provider_error correctly.


## P7-SUM-004 — Email Status Metrics

Verified/likely/no-email numbers derived from best contacts according to declared contract.


## P7-SUM-005 — Average Lead Score

Exact deterministic arithmetic.

Define rounding policy explicitly.


## P7-SUM-006 — No Double Counting

Duplicate input representing same canonical entity does not inflate Summary metrics.

---

# 18. Regression Cases From Actual Phase 7 Review

These are mandatory.

## P7-REG-001 — Threshold 60

Regression for previous hardcoded `70`.

Expected:

60 survives from Phase 5 to every Phase 7 output.


## P7-REG-002 — ATS Namespace Collision

Regression:

```text
lever:acme
greenhouse:acme
```

remain separate.


## P7-REG-003 — Weak Contact Collapse

Two same-name/title/company contacts without strong identity remain separate.


## P7-REG-004 — No Fake Generic Job

No raw jobs -> zero Job rows + audit diagnostic.


## P7-REG-005 — Google Mock Isolation

Production Google resolution cannot instantiate test Mock client automatically.


## P7-REG-006 — Run ID Collision

Two immediate export operations cannot overwrite each other.


## P7-REG-007 — Empty CSV Headers

Empty table still yields valid header-only CSV.

---

# 19. Acceptance Report Metrics

For each Phase 7 Golden run record:

```text
input_leads
qualified_input_leads
exported_leads
input_contacts
exported_contacts
input_jobs
exported_jobs

unique_lead_ids
unique_contact_ids

xlsx_created
xlsx_openable
xlsx_sheet_count

csv_files_created
json_created

score_contract_preserved
qualification_contract_preserved
best_contact_preserved
evidence_preserved

privacy_violations
identity_collisions
fabricated_jobs

google_mode
google_status

partial_failures
fatal_failures

export_run_id
```

---

# 20. README-TEST Output

Example:

```markdown
### P7-EXP-001 — Full Multi-Format Export

Input:
Leads: 1
Contacts: 2
Raw Jobs: 3

Identity:
Lead ID: domain:acme.com
Contacts:
- email:alice@acme.com
- linkedin:linkedin.com/in/bob

Phase 5 Contract:
Fit: 30/30
Intent: 30/30
Recency: 20/20
Evidence: 14/20
Total: 94/100
Threshold: 60
Qualified: true

Exports:
XLSX: PASS
CSV: PASS
JSON: PASS
Google Sheets: NOT_RUN

Rows:
Leads: 1
Contacts: 2
Jobs: 3
Errors: 0

Integrity:
Score Preserved: YES
Best Contact Preserved: YES
Evidence Preserved: YES
Privacy Violations: 0
Identity Collisions: 0
Fabricated Jobs: 0

Expected:
...

Actual:
...

Differences:
...

Result:
PASS / FAIL / REVIEW / NOT_RUN

Human Notes:
...
```

---

# 21. QA Integrity Rules

Reuse the QA self-validation framework.

A Phase 7 case may only PASS when every contractual Expected field matches Actual.

Never auto-edit Golden Expected.

Missing diagnostic fields must display:

```text
N/A
```

not 0/false.

If Expected != Actual:
- FAIL for deterministic contract violation
- REVIEW only for an explicit unresolved human product-policy decision

Examples of valid REVIEW:
- whether re-export preserves or resets manually changed workflow status

Examples that must FAIL:
- threshold changed from 60 to 70
- two companies merged
- two humans merged
- job fabricated
- private field exported
- fake Google success

---

# 22. Live / Deferred Policy

Phase 7 deterministic export QA:
RUN NOW.

Google Sheets live:
NOT_RUN until real integration exists.

No Phase 7 test should be:

NOT_RUN_MODEL_LIMITATION

because Phase 7 does not require an LLM.

---

# 23. Critical FAIL Conditions

Immediate FAIL if:

- Phase 5 score changes during export
- qualification threshold changes
- qualified flag changes
- best_contact changes
- same lead appears twice
- two distinct strong company identities collapse
- two weakly identified humans collapse
- contact assigned to wrong lead
- source job assigned to wrong lead
- fabricated job exported as real
- reasons/evidence lost
- personal email/phone leaks
- API credentials/secrets leak
- same logical input produces unstable lead/contact IDs
- Google upsert duplicates stable entities
- test mock is used as fake production Google client
- XLSX is corrupted
- CSV loses rows/columns
- JSON arrays become lossy flattened strings
- exporter claims full success after requested format failure
- input objects are mutated
- Phase 7 invokes an LLM

---

# 24. Suggested Minimum Test Count

Target approximately:

```text
Cross-phase contract:       5
Lead identity:              5
Contact identity:           7
Jobs/audit:                 5
XLSX:                      10
CSV:                        5
JSON:                       4
Privacy:                    3
Determinism/idempotency:    5
Google Sheets:              7
Failure behavior:           4
Limits:                     4
Workflow:                   5
Summary:                    6
Regression:                 7
```

Some can be parametrized.

Do not create meaningless test-count inflation.

Prefer one precise regression per real invariant.

---

# 25. Definition of Phase 7 QA Complete

Phase 7 may be frozen when:

1. Frozen upstream fixtures are valid.
2. Phase 5 score/threshold contracts survive unchanged.
3. Lead IDs are stable and namespace-safe.
4. Contact IDs are strong-identity safe.
5. Phase 6-surviving contacts are not re-collapsed.
6. Raw jobs retain exact audit linkage.
7. No job records are fabricated.
8. XLSX is valid and human-reviewable.
9. CSV is standards-compliant and header-safe.
10. CRM JSON is canonical and lossless.
11. Privacy exclusions pass.
12. Entity IDs are deterministic.
13. Export run IDs are collision-safe.
14. Google mocks cannot masquerade as live success.
15. Partial/fatal failures are explicit.
16. Limits and filters pass.
17. Workflow defaults are safe.
18. Summary calculations are correct.
19. QA integrity shows zero false passes.
20. Full repository test suite has no new deterministic failures.
