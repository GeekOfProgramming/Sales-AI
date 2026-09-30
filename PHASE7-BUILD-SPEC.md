# SalesAI Phase 7 Build Spec — Export / Excel / Google Sheets / CRM-ready Output

## Phase Goal

Phase 7 converts deterministic Phase 5/6 outputs into human-reviewable and integration-ready exports.

It must NOT:
- call an LLM
- change lead scores
- change qualification
- change best contact
- invent missing data
- enrich new data
- send email

It SHOULD:
- preserve source data and explainability
- create stable, repeatable exports
- support Excel/CSV locally
- support Google Sheets optionally
- expose a CRM-neutral schema for future connectors
- prepare rows for Phase 8 email generation and Phase 9 review/send

---

## Inputs

Primary:
- `EnrichedLead[]`

Optional:
- `WebsiteProfile`
- export metadata / run metadata

Phase 7 must not dynamically re-run Phase 5 or Phase 6.

---

## Outputs

### 1. Excel workbook
Recommended sheets:
- `Leads`
- `Contacts`
- `Jobs`
- `Errors_Audit`
- `Summary`

### 2. CSV
At minimum:
- `leads.csv`
- `contacts.csv`

### 3. Google Sheets
Optional provider.
Same logical columns as Excel.

### 4. CRM-ready JSON
Provider-neutral canonical export.
No Salesforce/HubSpot-specific business logic yet.

---

## Canonical Lead Export Columns

- export_run_id
- lead_id
- company_name
- company_domain
- industry
- country
- employee_count
- lead_score
- qualified
- fit_score
- intent_score
- recency_score
- evidence_score
- relevant_job_count
- total_job_count
- qualification_threshold
- lead_reasons
- lead_evidence
- enrichment_status
- providers_used
- enrichment_errors
- best_contact_id
- best_contact_name
- best_contact_title
- best_contact_email
- best_contact_email_status
- best_contact_email_confidence
- best_contact_linkedin
- best_contact_score
- buyer_role_match
- outreach_status
- approval_status
- owner
- notes
- created_at
- updated_at

Default workflow fields:
- `outreach_status = not_started`
- `approval_status = pending_review`

Do not auto-approve.

---

## Canonical Contact Export Columns

- lead_id
- contact_id
- company_name
- company_domain
- first_name
- last_name
- full_name
- job_title
- seniority
- department
- work_email
- email_status
- email_confidence
- linkedin_url
- buyer_role_match
- contact_score
- data_sources
- provider_person_id
- is_best_contact
- contact_rank

No personal email / phone fields.

---

## Canonical Job Export Columns

- lead_id
- company_name
- company_domain
- job_title
- job_url
- source
- source_company_key
- location
- employment_type
- posted_date
- seniority
- remote_status
- technologies
- relevant_signals
- signal_evidence
- is_relevant

---

## Errors / Audit Sheet

Columns:
- export_run_id
- lead_id
- company_name
- stage
- error_type
- provider
- message
- source_reference
- created_at

Never silently drop export failures.

---

## Summary Sheet

Useful deterministic totals:
- total_leads
- qualified_leads
- unqualified_leads
- complete_enrichment
- partial_enrichment
- provider_errors
- leads_with_best_contact
- leads_with_verified_email
- leads_with_likely_email
- leads_without_work_email
- average_lead_score
- export_timestamp

---

## Stable IDs

Use deterministic IDs.

Recommended:

`lead_id`
= hash/canonical key based on strongest company identity:
- canonical domain
- else source+source_company_key
- else normalized company name

`contact_id`
= hash/canonical key based on:
- normalized work email
- else canonical LinkedIn URL
- else provider + provider_person_id
- else deterministic record-local fallback

Do NOT generate random UUIDs for entity identity on each export.

`export_run_id` may be unique per export run.

---

## Serialization Rules

Lists must be deterministic.

For Excel/CSV:
- join scalar lists with ` | `
- sort only where ordering has no business meaning
- preserve ranked contact ordering
- preserve evidence text faithfully

For JSON:
- keep arrays as arrays

Booleans:
- true / false in JSON
- TRUE / FALSE in spreadsheet-compatible output

Null:
- empty cell in spreadsheet
- null in JSON

Dates:
- ISO 8601

---

## Excel Rules

Use `openpyxl`.

Required:
- freeze header row
- auto filter
- readable column widths
- wrapped text for reasons/evidence/errors
- one row per Lead in Leads
- one row per Contact in Contacts
- one row per Job in Jobs
- no merged cells in data tables
- no hidden production data

Workbook must open cleanly in Excel.

No formulas are required for source-of-truth fields.

---

## Google Sheets Architecture

Create provider abstraction:

- `BaseSheetExporter`
- `GoogleSheetsExporter`

Google Sheets must be optional.

Environment/config:
- `GOOGLE_SHEETS_ENABLED`
- credentials/config according to selected integration method
- spreadsheet_id or create-new mode

Do not make Google credentials mandatory for local export.

Modes:
- `snapshot`
- `upsert`

### Snapshot
Create a fresh export tab/workbook for the run.

### Upsert
Use stable `lead_id` / `contact_id`.
Update existing row instead of duplicating entity.

Do not use company name alone as upsert identity.

---

## CRM-ready Export

Create provider-neutral schema only.

Example:
```json
{
  "company": {...},
  "lead": {...},
  "contacts": [...],
  "jobs": [...],
  "audit": {...}
}
```

Do NOT build Salesforce, HubSpot, Pipedrive or other provider-specific APIs in this phase unless separately requested later.

---

## Export Service Structure

Suggested files:

```text
sales_engine/exports/
├── __init__.py
├── schemas.py
├── export_serializer.py
├── excel_exporter.py
├── csv_exporter.py
├── base_sheet_exporter.py
├── google_sheets_exporter.py
├── crm_exporter.py
└── export_orchestrator.py
```

---

## API

Recommended endpoint:

`POST /api/sales/export`

Request:
```json
{
  "leads": [...],
  "formats": ["xlsx", "csv"],
  "qualified_only": true,
  "include_all_contacts": true,
  "include_jobs": true,
  "google_sheets": {
    "enabled": false,
    "mode": "snapshot",
    "spreadsheet_id": null
  }
}
```

Response should include:
- export_run_id
- exported_leads
- exported_contacts
- exported_jobs
- files
- google_sheet_result
- errors

Do not return success if requested export format failed completely.

Partial export is allowed and must expose errors.

---

## Limits

Recommended:
- MAX_EXPORT_LEADS = 5000
- MAX_CONTACTS_PER_LEAD_EXPORT = existing Phase 6 limit unless `include_all_contacts` is explicitly supported
- reject pathological payloads cleanly

---

## Security / Privacy

Never export:
- personal email
- personal phone
- mobile phone
- API keys
- provider raw secrets

Work email and professional profile links only.

Do not write secrets into workbook metadata or audit rows.

---

## Idempotency

Same logical input must produce:
- same lead IDs
- same contact IDs
- same row ordering when sorting rules are fixed
- same canonical values

File timestamp/run id may differ.

For Google Sheets upsert:
re-running same dataset must not create duplicate leads/contacts.

---

## Phase 8 Handoff

Phase 7 should reserve workflow columns:

- outreach_status
- approval_status
- draft_subject
- draft_body
- personalization_notes
- last_outreach_at
- send_status

Phase 7 does NOT populate email drafts.

Phase 8 will populate draft fields.

Phase 9 will manage approval/send state.

---

## Critical Fail Conditions

- duplicate company rows from same stable lead identity
- same contact exported multiple times for same lead
- contact assigned to wrong company
- personal email/phone exported
- score or qualification changed during export
- best_contact changed during export
- evidence/reasons silently lost
- Google Sheets upsert duplicates entities
- export reports success when file creation failed
- workbook cannot be opened
- CSV row/column corruption
- unstable entity IDs across identical exports

---

## Phase 7 Definition of Done

1. XLSX export works locally.
2. CSV export works locally.
3. Canonical CRM JSON export works.
4. Google Sheets adapter exists and is optional.
5. Stable lead/contact IDs exist.
6. No scoring/enrichment logic is modified.
7. Explainability survives export.
8. Privacy rules survive export.
9. Partial failures are explicit.
10. Export is deterministic and idempotent.
11. Phase 8 workflow columns are reserved.
12. Unit + acceptance tests cover all critical paths.
