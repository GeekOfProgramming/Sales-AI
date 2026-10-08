# SalesAI Phase 10 Build Spec
## Multi-Source Market Discovery, Historical Seed Registry & Company-First Qualification

# Phase Goal

Phase 10 expands SalesAI from a job-first lead discovery system into a
multi-source company discovery and market intelligence engine.

The system must be able to discover companies even when:

- no job posting is found
- no ATS result is found
- the company has no active career page
- the company has not mentioned BIM hiring publicly

Phase 10 must combine:

1. Job-first discovery
2. Company-first discovery
3. Project/tender-first discovery
4. Manual/historical seed discovery

and produce a deduplicated, evidence-backed company candidate registry.

Phase 10 must NOT send email.
Phase 10 must NOT auto-approve drafts.
Phase 10 must NOT replace Phase 9 review/send state.
Phase 10 must NOT infer business facts without evidence.

---

# 1. Mandatory Pre-Phase-10 Retrofit Gate

Before implementing new Phase 10 behavior, perform a backwards-compatible
retrofit of the frozen Phase 2-8 contracts.

Do NOT rewrite or invalidate existing deterministic Golden cases.

The goal is to make previous phases capable of receiving generalized
company/discovery evidence in addition to job evidence.

Required compatibility goals:

- Phase 3 keeps existing job/ATS discovery intact.
- Phase 4 keeps StructuredJob extraction intact.
- Phase 5 legacy job-led score remains unchanged for existing fixtures.
- Phase 6 existing enrichment/contact logic remains unchanged.
- Phase 7 can export new Phase 10 fields without mutating legacy columns.
- Phase 8 can consume generalized grounded evidence through existing lead evidence.
- Phase 9 remains the sole review/approval/send authority.

If a required change would alter a frozen score or historical Golden result,
add a new Phase 10 structure instead of silently changing the frozen contract.

---

# 2. New Core Concept: CandidateCompany

Introduce a company-level entity before CompanyLead.

Recommended schema:

```text
CandidateCompany

candidate_id

company_name
normalized_company_name
company_domain

country
region
city

company_types
industry_tags

legal_form
legal_form_raw

employee_size_hint
project_scale_hint

company_tier
tier_confidence
tier_evidence

bim_maturity
bim_maturity_confidence

direct_project_access
direct_project_access_confidence

outsourcing_potential
automation_potential

discovery_status

fit_assessment
intent_assessment

source_refs
evidence_refs

first_seen_at
last_seen_at
last_researched_at

historical_contact_status
last_contacted_at
reply_status
suppressed
```

`CandidateCompany` is not automatically a qualified lead.

---

# 3. Tier Model

Use global business behavior, not legal form alone.

Allowed values:

```text
tier_1
tier_2
tier_3
unknown
```

## Tier 3

Typical behavior:

- boutique architecture / engineering / MEP office
- smaller team
- receives projects directly from clients or as specialist subcontractor
- may have limited internal BIM production capability
- likely opportunity for BIM modeling, MEP BIM, coordination, documentation,
  conversion, project overflow

## Tier 2

Typical behavior:

- structured mid-sized architecture / engineering / MEP / construction company
- multiple simultaneous projects
- may bid for public/private work
- may have partial in-house BIM capability
- likely opportunity for delivery overflow, BIM coordination, specialist production,
  automation and project support

## Tier 1

Typical behavior:

- large contractor, engineering consultancy, EPC, infrastructure firm,
  enterprise design/construction group
- direct access to large projects
- usually has internal BIM/digital delivery capability
- likely opportunity for overflow capacity, specialist BIM execution,
  automation, Revit/API work, custom software and future enterprise AI

CRITICAL:

Legal form is evidence only.

Never implement rules such as:

```text
S.r.l. => tier_2
S.p.A. => tier_1
Ltd => tier_2
GmbH => tier_2
```

Legal form may contribute evidence but never determine tier alone.

---

# 4. Opportunity Is Separate From Tier

Do not use Tier as lead quality score.

Model separately:

```text
company_tier
fit_assessment
intent_assessment
outsourcing_potential
automation_potential
```

Example:

```text
Tier 3
Fit: High
Intent: Medium
Outsourcing Potential: High
Automation Potential: Low/Medium
```

or:

```text
Tier 1
Fit: High
Intent: Medium
Outsourcing Potential: Medium
Automation Potential: Very High
```

---

# 5. BIM Maturity

Recommended enum:

```text
unknown
low
medium
high
enterprise
```

Evidence may include:

- BIM service page
- Revit/IFC/Navisworks/ACC references
- ISO 19650 references
- BIM staff
- BIM manager / digital delivery roles
- project BIM requirements
- company case studies
- software/technology pages

Absence of BIM language does NOT mean irrelevant.

A company may be:

```text
Fit: high
BIM maturity: low/unknown
Outsourcing potential: high
```

Do not convert missing evidence into a negative fact.

---

# 6. Standards Capability

Seller capability may include:

```text
ISO 19650
UNI 11337
```

Do not assume both standards apply to every market/project.

Store standards as:

```text
seller_standards_capability
company_standard_signals
project_standard_requirements
```

Personalization must only mention standards supported by actual evidence/context.

---

# 7. Discovery Source Registry

Create:

```text
config/discovery_sources.yaml
```

Source types:

```text
search_engine
ats
job_board
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
```

Recommended source schema:

```yaml
sources:
  - id: source-id
    name: Human-readable name
    type: company_directory
    enabled: true

    countries:
      - Italy

    company_types:
      - architecture
      - engineering

    discovery_mode:
      - company_first

    priority: 80

    max_results_per_run: 100

    requires_api_key: false

    base_url: null

    notes: null
```

The registry must be configurable without code changes.

---

# 8. Source Safety

Source adapters must respect the access method intended for the source.

Prefer:

- official APIs
- public directories
- company websites
- user-supplied URLs/files
- search-engine discovery

Do not build brittle credential bypasses or authentication circumvention.

Do not treat failure to access one source as zero market demand.

Record explicit source errors.

---

# 9. Discovery Modes

Implement four discovery paths.

## A. Job-First

Existing Phase 3 behavior:

```text
WebsiteProfile
→ job queries
→ ATS/job URLs
→ StructuredJob
→ CompanyLead
```

Keep intact.

## B. Company-First

```text
Directory / Business Source
→ CandidateCompany
→ Resolve website/domain
→ Analyze company
→ inspect careers/projects/services
→ collect evidence
```

## C. Project/Tender-First

```text
Tender / project / award source
→ project evidence
→ participating company
→ CandidateCompany
→ company research
```

## D. Manual/Historical Seed

```text
CSV / XLSX / explicit domain / company name / URL
→ CandidateCompany
→ deduplicate
→ research
```

---

# 10. Candidate Discovery Record

Create:

```text
DiscoveryObservation
```

Fields:

```text
observation_id

candidate_id

source_id
source_type

source_url
source_external_id

observed_company_name
observed_domain

country
city

observation_type

raw_title
raw_snippet

observed_at
fetched_at

confidence

evidence_refs
```

A company may have many observations.

Do not create one Company entity per source.

---

# 11. Company Identity

Identity priority:

```text
1. canonical company domain
2. explicit trusted source company identity
3. normalized company name + geography
4. weak/manual candidate identity
```

Never merge two companies solely because their normalized names match
when domain/geography/source identity conflicts.

Maintain:

```text
company_aliases
source_identities
domain_history
```

---

# 12. Historical Seed Import

Build import support for existing human lead spreadsheets.

Support:

```text
.xlsx
.csv
```

The import must treat historical spreadsheets as source evidence,
not as perfect normalized truth.

Create:

```text
HistoricalImportBatch
HistoricalCompanyRecord
HistoricalContactRecord
HistoricalOutreachEvent
```

Preserve original source row reference.

---

# 13. Historical Import Behavior

Import:

- company names
- websites/domains
- country/city
- historical source
- human tier/category
- notes
- job/source URLs
- contact names
- work emails
- dates contacted
- follow-up dates
- reply/outcome status
- invalid/wrong-email state
- suppression/do-not-contact if present

Do not fabricate missing fields.

Do not automatically reinterpret old free-text notes as verified facts.

LLM extraction may suggest structure, but original text remains traceable.

---

# 14. Historical Deduplication

When imported historical company matches a newly discovered company:

```text
new discovery
→ existing company identity
→ append observation
```

Do not create a duplicate lead.

If historical data shows previous outreach:

```text
historical_contact_status = contacted
```

A new cold email must NOT automatically be generated.

Route to:

```text
known_company
reengagement_review
watchlist
```

according to policy.

---

# 15. Existing Outreach Guard

Before a candidate can enter cold outreach:

check:

```text
historical outreach
Phase 9 send history
suppression list
existing active conversation
```

If already contacted:

default:

```text
cold_outreach_allowed = false
```

unless an explicit re-engagement policy says otherwise.

No automatic re-engagement in Phase 10 v1.

---

# 16. Source Evidence vs Company Evidence

Separate:

```text
Discovery Evidence
```

Example:
"Found in architecture directory"

from:

```text
Business Evidence
```

Example:
"Company provides multidisciplinary design services"

and:

```text
Intent Evidence
```

Example:
"Hiring BIM Manager"

and:

```text
Project Evidence
```

Example:
"Recently awarded rail infrastructure project"

This distinction is mandatory.

---

# 17. Generalized Evidence Schema

Introduce:

```text
MarketEvidence
```

Fields:

```text
evidence_id

candidate_id

category

signal_type

text
source_url
source_type

observed_at

confidence

is_current

raw_ref
```

Categories:

```text
company_profile
service
technology
standard
job
project
tender
news
buyer
capacity
digital_maturity
historical
```

Phase 8 may consume safe grounded text through lead evidence after qualification.

---

# 18. Company Research Pipeline

For each candidate, research in order:

```text
1. official company website
2. services/capabilities
3. project portfolio
4. careers/jobs
5. BIM/digital/technology pages
6. public project/tender evidence
7. contact/enrichment when qualified
```

Do not perform contact enrichment for every raw candidate.

Enrichment is expensive and should occur after qualification.

---

# 19. Website Research

Extract deterministic/public facts:

```text
company name
domain
locations
company types
services
project sectors
project types
BIM terms
technology terms
standards
careers URL
contact page
```

Use LLM only for semantic normalization/classification.

Do not let LLM invent:

```text
employee count
revenue
budget
project value
outsourcing behavior
BIM capability
```

unless evidence exists.

---

# 20. Company Type Taxonomy

Recommended configurable values:

```text
architecture
engineering
mep_engineering
structural_engineering
civil_engineering
general_contractor
construction
epc
infrastructure
bim_consultancy
project_management
real_estate_developer
public_sector_contractor
multidisciplinary_consultancy
specialist_subcontractor
other
```

Multiple values allowed.

---

# 21. Project Sector Taxonomy

Recommended:

```text
residential
commercial
healthcare
education
industrial
data_center
rail
metro
airport
road
bridge
infrastructure
energy
oil_gas
public_building
hospitality
mixed_use
other
```

Use only when supported by public evidence.

---

# 22. Market Configuration

Create:

```text
config/markets.yaml
```

No country-specific code path should be required.

Example markets:

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

Each market may define:

```text
enabled
languages
preferred_source_ids
company_types
search_locales
standards_context
daily_candidate_limit
```

---

# 23. Discovery Strategy

Introduce:

```text
DiscoveryStrategy
```

Input:

```text
WebsiteProfile
MarketConfig
SourceRegistry
HistoricalRegistry
```

Output:

```text
DiscoveryPlan
```

A plan contains:

```text
source
query/seed
company type
country
priority
result budget
reason
```

---

# 24. Fallback Strategy

Discovery must not stop because job search is weak.

Example policy:

```text
Tier A:
ATS/job discovery

if results < target:
    company directory discovery

if qualified candidates still < target:
    company website/category discovery

if still weak:
    project/tender discovery

remaining good-fit/no-intent companies:
    watchlist
```

Use configurable thresholds.

Example:

```text
MIN_NEW_CANDIDATES_PER_RUN
TARGET_QUALIFIED_COMPANIES_PER_RUN
```

Do not guarantee arbitrary lead counts.

---

# 25. Watchlist

Create a persistent Watchlist.

Use for:

```text
strong fit + weak/unknown intent
```

Fields:

```text
candidate_id
watch_reason
watch_status
next_check_at
last_checked_at
signals_to_monitor
```

Examples:

```text
careers
projects
tenders
news
website changes
```

No outreach automatically generated from watchlist status alone.

---

# 26. Re-Research

Previously known companies may be researched again.

Store new observations with timestamps.

Do not overwrite historical evidence.

Allow signal lifecycle:

```text
new
current
stale
expired
```

---

# 27. Fit Assessment

Phase 10 may create a deterministic:

```text
MarketFitAssessment
```

This is NOT a replacement for frozen Phase 5 lead score.

Recommended fields:

```text
company_type_fit
project_sector_fit
market_fit
service_fit
direct_project_access_fit

fit_level:
low
medium
high

reasons
```

Use deterministic rules wherever possible.

---

# 28. Intent Assessment

Intent must be separate.

Signals may include:

```text
relevant hiring
new project
project award
tender
BIM requirement
digital delivery initiative
automation hiring
capacity growth
public procurement evidence
```

Output:

```text
intent_level:
none
unknown
low
medium
high

intent_signals
intent_evidence
```

Missing intent is not negative fit.

---

# 29. Outsourcing Potential

This is an assessment, not a fact.

Use:

```text
low
medium
high
unknown
```

Require explainable evidence/rules.

Examples of possible positive indicators:

- project complexity
- limited visible BIM team
- multidisciplinary project delivery
- temporary hiring spike
- multiple active project signals

Do not state:

"company outsources BIM"

unless direct evidence exists.

Use wording:

```text
outsourcing_potential = high
```

not:

```text
outsources_bim = true
```

---

# 30. Automation Potential

Separate:

```text
automation_potential
```

Potential indicators:

```text
large BIM/digital team
Revit/API roles
Python/C# roles
repetitive data/process language
digital transformation
large project portfolio
enterprise BIM operations
```

This supports future pyBIM automation/software/AI offering.

---

# 31. Qualification Bridge

Create:

```text
CandidateQualificationResult
```

Possible outcomes:

```text
reject
watchlist
research_more
qualified_for_enrichment
known_company
reengagement_review
suppressed
```

Only:

```text
qualified_for_enrichment
```

flows to Phase 6 contact enrichment.

---

# 32. Bridge to Existing CompanyLead

Do not fabricate jobs to satisfy Phase 5.

Never create pseudo `StructuredJob` records from generic company evidence.

Instead create a Phase 10 qualification bridge.

For job-led candidates:

```text
existing Phase 5 CompanyLead
```

may continue normally.

For non-job candidates:

create:

```text
MarketQualifiedLead
```

or backwards-compatible generalized lead wrapper.

The object must carry:

```text
company identity
fit assessment
intent assessment
evidence
tier
source history
historical contact status
```

and be accepted by Phase 6 through a minimal compatibility adapter.

---

# 33. No Fake Job Regression

Phase 10 must explicitly guarantee:

```text
directory entry != job
project award != job
company profile != job
```

Never generate fake StructuredJob rows to reuse old scoring code.

---

# 34. Contact Enrichment Gate

Phase 6 should only receive candidates after:

```text
qualified_for_enrichment
```

or existing job-led qualification.

Check historical contacts first.

If a valid historical work contact already exists,
reuse/deduplicate before provider calls.

---

# 35. Historical Contact Reuse

Historical work emails may be imported as:

```text
email_status = historical_unknown
```

unless current verification exists.

Do NOT automatically label old email as verified.

Optional verifier can refresh status later.

---

# 36. Export Retrofit

Extend Phase 7 exports with additive columns/sheets.

Recommended additional fields:

```text
company_tier
bim_maturity
fit_level
intent_level
outsourcing_potential
automation_potential

discovery_sources
first_seen_at
last_seen_at

historical_contact_status
last_contacted_at
reply_status

watchlist_status
qualification_route
```

Do not remove or rename frozen Phase 7 legacy columns.

---

# 37. New Export Sheets

Optional/additive:

```text
Candidates
Discovery_Evidence
Watchlist
Historical_Outreach
```

Existing:

```text
Leads
Contacts
Jobs
Errors_Audit
Summary
```

remain intact.

---

# 38. Phase 8 Bridge

Phase 8 email drafting may use only grounded Phase 10 evidence that survives
qualification.

Allowed examples:

```text
public project signal
job signal
public service/capability
technology
public standard reference
company sector
```

Do not include speculative fields such as:

```text
outsourcing_potential
automation_potential
```

as factual claims in the email.

They may choose strategy internally but must not be stated as facts.

---

# 39. Phase 9 Guard

Before generating/approving new cold outreach:

check:

```text
Phase 9 send history
historical import send history
suppression list
existing active conversation
```

Phase 10 cannot bypass Phase 9 safety.

---

# 40. Discovery Run

Create:

```text
DiscoveryRun
```

Fields:

```text
run_id
started_at
completed_at

markets
source_ids

observations_found
new_candidates
known_candidates
rejected_candidates
watchlist_candidates
qualified_candidates

errors

config_version
```

---

# 41. Daily Discovery

Support a callable daily cycle:

```text
run_daily_discovery()
```

Do not require a permanent scheduler in the core.

Expose:

```text
CLI command
API endpoint
```

so Windows Task Scheduler or a future local worker can invoke it.

No sending occurs as part of daily discovery.

---

# 42. API

Recommended endpoints:

```text
POST /api/sales/discovery/run

GET /api/sales/discovery/runs
GET /api/sales/discovery/runs/{run_id}

GET /api/sales/candidates
GET /api/sales/candidates/{candidate_id}

POST /api/sales/candidates/{candidate_id}/research
POST /api/sales/candidates/{candidate_id}/qualify
POST /api/sales/candidates/{candidate_id}/watch

POST /api/sales/sources/import
GET /api/sales/sources

POST /api/sales/history/import
GET /api/sales/watchlist
```

No endpoint should auto-send.

---

# 43. Local Persistence

Phase 10 may reuse the SalesAI SQLite database created by Phase 9.

Add tables such as:

```text
candidate_companies
discovery_observations
market_evidence
discovery_runs
watchlist
historical_import_batches
historical_company_records
historical_outreach_events
company_aliases
source_identities
```

Use migrations/schema versioning.

Never package runtime DB in release ZIP.

---

# 44. Idempotency

Repeated discovery of the same source/company must not create duplicate logical companies.

Stable identities for:

```text
candidate
observation
evidence
historical event
```

Repeated import of the same historical spreadsheet should be idempotent
when file fingerprint/source row identity is unchanged.

---

# 45. Source Provenance

Every fact used to qualify a candidate must be traceable.

At minimum:

```text
source_type
source_url
observed_at
evidence text
```

Never convert a source query result into a verified company fact without research.

---

# 46. Evidence Freshness

Track:

```text
observed_at
is_current
freshness_class
```

Suggested classes:

```text
fresh
recent
old
unknown
```

Do not silently discard historical evidence.

---

# 47. Research Limits

Add configurable limits:

```text
MAX_CANDIDATES_PER_RUN
MAX_RESEARCH_PAGES_PER_COMPANY
MAX_SOURCES_PER_COMPANY
MAX_WATCHLIST_CHECKS_PER_RUN
```

Prevent uncontrolled crawling.

---

# 48. Partial Failure

A failed source must not abort the entire discovery run.

Example:

```text
Directory A fails
Search B succeeds
Tender C succeeds
```

Keep successful candidates.

Record source-specific errors.

---

# 49. Source Health

Track:

```text
last_success
last_failure
last_error
results_last_run
new_candidates_last_run
```

This supports later operator UI.

---

# 50. Future Operator Console Compatibility

Phase 10 must expose sufficient APIs/data for a later local web UI.

The UI will eventually contain:

```text
Dashboard
Discoveries
Sources
Qualified Leads
Draft Review
Approved
Sent
Replies
Watchlist
Do Not Contact
```

Phase 10 only implements the backend data/API necessary for:

```text
Discoveries
Sources
Candidates
Watchlist
Qualification
```

Do not build the full UI unless explicitly requested.

---

# 51. Global Market Behavior

Country is context, not architecture.

The same CandidateCompany schema applies globally.

Legal forms and local terminology may be stored, but should not determine
qualification alone.

Examples of market-specific source config belong in YAML/configuration,
not branching business logic.

---

# 52. Manual Seed Input

Support:

```text
company name
domain
website URL
directory URL
CSV
XLSX
```

A manual seed is not automatically trusted as qualified.

It enters normal research/dedup/qualification flow.

---

# 53. Directory URL Input

For a user-supplied public directory page:

```text
source URL
→ extract company candidates
→ resolve domains
→ deduplicate
→ research
```

Do not assume every listed entity is relevant.

---

# 54. Historical Human Labels

If old spreadsheet has human category/tier labels:

preserve:

```text
historical_tier_raw
historical_category_raw
```

Optionally normalize into current schema.

Do not overwrite the original label.

---

# 55. Human Ground Truth Use

Historical human selections may inform:

```text
tests
fixtures
evaluation
rule design
```

Do NOT train or tune scoring automatically from a small historical sample.

Reply outcomes should be treated as observations until enough data exists.

---

# 56. Suggested Module Structure

```text
sales_engine/discovery_v2/
├── __init__.py
├── schemas.py
├── source_registry.py
├── market_config.py
├── candidate_identity.py
├── candidate_store.py
├── observation_store.py
├── evidence_store.py
├── source_adapter.py
├── search_source.py
├── directory_source.py
├── manual_source.py
├── historical_importer.py
├── company_researcher.py
├── tier_classifier.py
├── fit_assessor.py
├── intent_assessor.py
├── qualification.py
├── watchlist.py
├── discovery_strategy.py
└── discovery_orchestrator.py
```

Reuse existing Phase 3 modules where safe.

Do not fork duplicate generic web fetchers/LLM clients unnecessarily.

---

# 57. No Duplicate LLM Client

Reuse:

```text
ai_engine/llm_client.py
```

If semantic company classification uses LLM.

No second Ollama client.

LLM may classify/extract text.

LLM must NOT:

```text
assign deterministic numeric score
invent evidence
invent tier evidence
invent company facts
decide send approval
```

---

# 58. Tier Classification

Prefer deterministic evidence + rules first.

LLM may propose classification from grounded company data,
but output must include:

```text
tier
confidence
evidence_refs
```

If evidence weak:

```text
tier = unknown
```

Do not force a tier.

---

# 59. Minimum Candidate Evidence

A company may be stored with minimal discovery evidence.

But before qualification require at least:

```text
company identity
one real source
country/market when available
research status
```

Qualification should prefer resolved domain/official website.

---

# 60. Reject Reasons

Use structured reasons:

```text
irrelevant_industry
no_company_identity
duplicate
outside_market
consumer_only
no_project_relevance
invalid_business
insufficient_evidence
suppressed
already_contacted
```

Do not delete rejected candidates immediately.

Retain for dedup/audit.

---

# 61. Watchlist Reasons

Examples:

```text
good_fit_no_current_intent
strong_projects_unknown_bim
potential_future_automation
historically_contacted_wait
missing_contact
needs_more_research
```

---

# 62. Re-engagement

Phase 10 v1 must NOT automatically re-engage.

It may classify:

```text
reengagement_review
```

based on:

```text
last contacted date
new evidence
new job/project signal
reply history
```

Human/Phase 9 policy decides later.

---

# 63. Security / Privacy

Store only business/professional data needed for the workflow.

No personal phone scraping.

No personal email promotion.

No hidden credential storage in source configs.

API keys remain server-side environment/config.

Runtime DB excluded from release package.

---

# 64. Deterministic Test Areas

At minimum test:

```text
source registry
market config
candidate identity
domain canonicalization
same company across sources
different companies same name
historical import
historical import idempotency
contacted-company guard
tier does not derive solely from legal form
unknown tier fallback
BIM absence does not become negative fact
company-first discovery
job-first compatibility
project evidence
watchlist routing
qualification routing
no fake jobs
Phase 6 gate
Phase 7 additive export
Phase 8 evidence bridge
Phase 9 send-history guard
partial source failure
daily discovery run accounting
runtime DB exclusion
```

---

# 65. Critical Regressions

Prepare at minimum:

```text
P10-REG-001 Same company from Google/Directory/Job merges once
P10-REG-002 Same name different domains stay separate
P10-REG-003 Legal form alone never determines tier
P10-REG-004 No BIM mention does not imply no BIM capability
P10-REG-005 Directory result never becomes fake StructuredJob
P10-REG-006 Historical contacted company does not enter new cold outreach
P10-REG-007 Historical import is idempotent
P10-REG-008 Weak/no-intent good-fit company routes to watchlist
P10-REG-009 New job/project signal can promote watchlist candidate
P10-REG-010 Suppressed company/contact cannot qualify for outreach
P10-REG-011 Phase 5 frozen job score remains unchanged
P10-REG-012 Phase 8 email uses only grounded evidence
P10-REG-013 Failure of one source preserves other results
P10-REG-014 Repeated daily run does not duplicate candidates
P10-REG-015 Runtime database never enters release ZIP
```

---

# 66. Historical Excel Import Acceptance

Use a sanitized fixture modeled after the previous manual human workflow.

Test separate logical concepts:

```text
company
source
website
human tier
notes
contact
email
outreach event
reply status
```

Do not copy real sensitive rows into public test fixtures.

Test multi-email cells and normalize into distinct contact/email records.

Preserve raw value for audit.

---

# 67. Multi-Email Normalization

If historical cell contains several business emails:

split into normalized records.

Classify when possible:

```text
generic
business
named_work
certified/legal
unknown
```

Do not treat all as equivalent best contacts.

Do not automatically mark historical email verified.

---

# 68. Historical Reply Data

Store:

```text
reply_status
reply_date
reply_summary
```

when present.

If outcome exists in another historical workbook,
support later merge by company/contact identity.

Do not assume missing reply means rejection.

---

# 69. Discovery Analytics

Phase 10 backend should calculate:

```text
companies_seen
new_companies
known_companies
duplicates
rejected
watchlist
qualified

by:
source
country
company_type
tier
```

These are descriptive metrics only.

Do not optimize strategy automatically from small sample sizes.

---

# 70. Definition of Done

Phase 10 functional implementation is complete when:

1. Existing job-first discovery remains operational.
2. Company-first discovery works from configured sources/manual seeds.
3. Project/tender observations can create company candidates.
4. CandidateCompany identity and dedup are stable.
5. Same company across sources becomes one company with multiple observations.
6. Tier 1/2/3 is global and evidence-based.
7. Legal form alone cannot determine tier.
8. Fit and intent are separate.
9. BIM absence does not become a fabricated negative.
10. Historical XLSX/CSV import works.
11. Historical outreach prevents accidental new cold outreach.
12. Historical import is idempotent.
13. Watchlist persists good-fit/no-intent companies.
14. New evidence can update/promote a watchlist candidate.
15. No fake StructuredJob records are created.
16. Phase 5 frozen legacy score remains unchanged.
17. Qualified non-job candidates can reach Phase 6 through a compatibility bridge.
18. Phase 7 exports additive Phase 10 fields.
19. Phase 8 receives only grounded evidence.
20. Phase 9 suppression/send history cannot be bypassed.
21. Discovery run survives partial source failures.
22. Daily discovery entry point exists.
23. Source provenance is auditable.
24. Runtime databases/secrets stay out of release ZIP.
25. Deterministic Phase 10 tests pass.
