# SalesAI Phase 6 QA Scaffold — Company & Contact Enrichment

## Purpose

Validate Phase 6 independently from live providers and upstream LLM quality.

Phase 6 must be testable offline using frozen CompanyLead fixtures and mocked provider responses.

Split QA into:

1. Deterministic / mocked enrichment QA — run now
2. Live provider QA — prepare now, run later when Apollo/Hunter keys are configured

Core subsystems to isolate:

- company enrichment
- buyer role ranking
- contact discovery
- work-email discovery
- email-status normalization
- cross-provider contact deduplication
- best-contact selection
- enrichment status
- provider error handling
- provider usage tracking
- request limits
- privacy / data-minimization behavior

---

# Files to Create

```text
tests/golden/
├── phase6_enrichment.json
└── snapshots/
    └── enrichment/
        ├── company_leads/
        ├── apollo/
        ├── hunter/
        └── combined/

tests/acceptance/
└── test_phase6_acceptance.py

tests/reports/
└── phase6_latest.json
```

README-TEST.md must include Phase 6 cases.

---

# Frozen Input Contract

Use frozen CompanyLead fixtures.

Do NOT call Phase 5 dynamically from Phase 6 Golden tests.

Minimum CompanyLead input example:

```json
{
  "company_name": "Acme Engineering",
  "company_domain": "acme.com",
  "qualified": true,
  "total_score": 84,
  "reasons": [
    "Hiring BIM Manager",
    "Hiring Revit API Developer"
  ],
  "evidence": [
    "We are hiring a BIM Manager...",
    "Looking for a Revit API Developer..."
  ]
}
```

Optional WebsiteProfile fixture:

```json
{
  "buyer_roles": [
    "BIM Manager",
    "Technical Director",
    "Head of Digital Delivery",
    "Innovation Lead",
    "Operations Manager"
  ]
}
```

---

# P6-ENRICH-001 — Strong Mocked Enrichment

Input:
Qualified company lead with domain.

Mock Apollo / Hunter responses include multiple contacts.

Expected:
- company enrichment preserved
- buyer_roles_searched populated
- contacts normalized
- contacts deduplicated
- best_contact selected deterministically
- business/work email only
- enrichment_status = complete only when completion requirements are truly met
- providers_used reflects actual outbound mocked calls
- enrichment_errors empty

---

# P6-COMPANY-001 — Company Enrichment

Mock provider returns:

- canonical company name
- domain
- employee count
- industry
- country
- provider organization id

Expected:
normalized internal company enrichment schema.

Provider-specific raw field names must not leak into public schema unless intentionally retained in metadata.

---

# P6-ROLE-001 — Buyer Role Ranking

Input buyer roles:

- BIM Manager
- Technical Director
- Head of Digital Delivery
- Innovation Lead
- Operations Manager

Mock contacts:

- BIM Manager
- Senior BIM Manager
- Head of Digital Delivery
- Marketing Manager
- HR Specialist

Expected:
relevant buyer-role contacts outrank unrelated roles.

Ranking must be deterministic.

Do not use universal BIM-specific fallback if WebsiteProfile provides role context.

---

# P6-ROLE-002 — No Buyer Roles

Input WebsiteProfile:

buyer_roles = []

Expected:
controlled behavior.

Do not invent arbitrary BIM-specific buyer roles.

Allowed:
- empty searched roles
- explicitly configured generic fallback if product contract defines one

Not allowed:
hidden hardcoded BIM-specific fallback.

---

# P6-CONTACT-001 — Contact Normalization

Normalize provider data into ContactCandidate:

- first_name
- last_name
- full_name
- title
- seniority
- department
- company
- domain
- work_email
- email_status
- email_confidence
- linkedin_url
- provider
- provider_person_id
- buyer_role_match
- contact_score
- data_sources

Missing optional fields must not crash normalization.

---

# P6-DEDUP-001 — Same Email Across Providers

Apollo:

Jane Smith
provider_person_id = apollo_123
work_email = jane.smith@acme.com

Hunter:

Jane Smith
provider_person_id = hunter_987
work_email = jane.smith@acme.com

Expected:
ONE logical ContactCandidate.

Cross-provider dedup must merge by strongest identity.

Email match must override provider-specific ids as evidence of same contact.

data_sources should contain both providers.

---

# P6-DEDUP-002 — Same Person by LinkedIn

Two provider records:

same LinkedIn profile
different provider ids
one missing email

Expected:
one merged logical contact.

---

# P6-DEDUP-003 — Same Name, Different Emails

Two contacts:

Alex Morgan
alex.morgan@acme.com

Alex Morgan
alex.morgan@other.com

Expected:
do NOT merge solely by name.

---

# P6-DEDUP-004 — Same Name, Same Company, No Strong Identifier

Two provider records with same name/title/company but no email, no LinkedIn, no shared provider id.

Expected:
conservative behavior.

Do not over-merge unless current contract explicitly defines a safe rule.

---

# P6-EMAIL-001 — Hunter Domain Search Confidence

Hunter Domain Search returns an email with confidence score but no verifier result.

Expected:
confidence alone must NOT become:

verified

Allowed normalized statuses:
likely
unknown
risk-based equivalent defined by current schema

---

# P6-EMAIL-002 — Verifier Status Mapping

Verify deterministic mapping.

Examples:

valid -> verified

accept_all -> likely or risky according to product contract

invalid -> risky / not_valid

webmail -> risky

disposable -> risky

unknown -> unknown

Do not classify all non-empty emails as verified.

---

# P6-EMAIL-003 — Missing Email

Valid contact with no work email.

Expected:
email_status = not_found or unknown according to schema.

enrichment may still be partial.

Do not fabricate an email pattern.

---

# P6-EMAIL-004 — Personal Email Rejection

Mock provider response contains:

john@gmail.com

Expected:
do not accept as business work_email by default.

Personal/public email domains must not become approved outreach emails.

---

# P6-EMAIL-005 — Company Domain Match

Contact email:

person@acme.com

Company domain:

acme.com

Expected:
domain consistency recognized.

Contact email on unrelated domain must not receive full trust merely because provider returned it.

---

# P6-SCORE-001 — Contact Score Determinism

Run same ContactCandidate inputs multiple times.

Expected:
same contact_score
same ranking
same best_contact

No LLM numeric scoring.

---

# P6-SCORE-002 — Contact Score Bounds

Assert:

0 <= contact_score <= 100

---

# P6-SCORE-003 — Buyer Role Match Weighting

Relevant buyer title should outrank irrelevant title when email quality is otherwise similar.

Do not let a verified but irrelevant contact automatically beat a strongly matched buyer unless scoring contract explicitly says so.

---

# P6-BEST-001 — Best Contact Selection

Mock contacts:

1. strong buyer role + verified work email
2. strong buyer role + unknown email
3. unrelated role + verified email
4. strong role + no email

Expected:
best_contact chosen by deterministic scoring contract.

Expected result must be manually specified in Golden fixture.

---

# P6-STATUS-001 — Complete Status

`complete` must require meaningful completion.

Recommended contract:

- company successfully enriched or sufficiently identified
- at least one useful contact exists
- best_contact exists
- best_contact has acceptable work email state according to product rules

Do NOT mark complete merely because provider returned some contact.

Unknown/unverified email should normally remain partial unless contract explicitly allows otherwise.

---

# P6-STATUS-002 — Partial Status

Examples:

- company enrichment success, no contacts
- contact found, no usable email
- one provider failed, another returned partial data

Expected:
partial

---

# P6-STATUS-003 — Failed Status

Examples:

- all configured providers fail
- no usable company identity and no contacts
- unrecoverable provider/auth configuration failure

Expected:
failed or equivalent explicit terminal failure state.

Do not return empty success.

---

# P6-PROVIDER-001 — No Provider Configured

APOLLO_API_KEY missing
HUNTER_API_KEY missing

Expected:
controlled result.

Must clearly distinguish:

no_provider_configured

from:

provider returned no results

Do not silently report:

contacts = []

as if enrichment succeeded.

---

# P6-PROVIDER-002 — Authentication Error

Mock 401 / 403.

Expected:
structured error type:

auth_error

Provider name preserved.

---

# P6-PROVIDER-003 — Rate Limit

Mock 429.

Expected:
rate_limit

Do not convert to empty result.

---

# P6-PROVIDER-004 — Timeout

Mock provider timeout.

Expected:
timeout

Other providers may still continue.

---

# P6-PROVIDER-005 — Provider Server Error

Mock 5xx.

Expected:
provider_error

Partial enrichment remains valid if another provider succeeds.

---

# P6-PROVIDER-006 — Empty Result

Successful provider request returns no contacts.

Expected error/diagnostic type:

empty_result

This must remain distinguishable from:

auth_error
rate_limit
timeout
provider_error
not_configured

---

# P6-USAGE-001 — Provider Usage Tracking

providers_used must track ACTUAL outbound calls.

Do not count only successful providers.

Example:

Apollo called -> timeout
Hunter called -> success

Expected:

providers_used:
- apollo
- hunter

And errors contain Apollo timeout.

---

# P6-APOLLO-001 — Company Enrichment Contract

Mock Apollo organization enrichment using the current adapter contract.

Verify request construction and response normalization.

Do not hardcode an outdated API route in QA expected data unless adapter version is explicitly versioned.

Store provider adapter contract version if useful.

---

# P6-APOLLO-002 — People Search Contract

Mock people search.

Verify:
- organization-domain filter
- title filters
- pagination/per_page behavior where implemented
- no personal email / phone reveal request

People Search result alone must not be treated as verified-email enrichment.

---

# P6-APOLLO-003 — Personal Data Minimization

If Apollo supports enrichment flags:

ensure personal email reveal = disabled
ensure phone reveal = disabled

Business/work email only by default.

Critical FAIL if code requests personal email/phone unnecessarily.

---

# P6-HUNTER-001 — Domain Search Normalization

Mock Hunter Domain Search.

Verify:
- domain request
- contacts normalized
- confidence retained
- confidence does not equal verification

---

# P6-HUNTER-002 — Email Verifier Normalization

Mock Hunter verifier responses.

Verify status mapping independently from domain search.

---

# P6-LIMIT-001 — Max Leads Per Request

Contract:

MAX_LEADS_PER_REQUEST = 50

Test:
50 -> accepted
51 -> rejected / bounded according to API contract

Do not silently process unlimited leads.

---

# P6-LIMIT-002 — Max Contacts Per Lead

Contract:

MAX_CONTACTS_PER_LEAD = 5

Provider returns 20 contacts.

Expected:
at most 5 final contacts per lead.

Selection must be deterministic and rank-aware.

---

# P6-LIMIT-003 — qualified_only

Default:

qualified_only = true

Input contains:
- qualified lead
- unqualified lead

Expected:
only qualified lead enriched by default.

When explicitly false:
both may be processed.

---

# P6-PARTIAL-001 — One Provider Failure

Apollo timeout
Hunter success

Expected:
- lead still returned
- contacts from Hunter preserved
- enrichment_status = partial or complete depending on usable data contract
- Apollo timeout exposed in enrichment_errors
- providers_used includes both

---

# P6-PARTIAL-002 — Contact Provider Success, Email Provider Failure

Contact identified
email verification provider fails

Expected:
contact retained
email status remains unknown/not_found
no fabricated verification
partial status

---

# P6-PRIVACY-001 — Work Email Only

Final EnrichedLead should not expose/store:

personal_email
personal_phone
mobile_phone

unless product policy explicitly changes later.

Current default:
business contact data only.

---

# P6-EXPLAIN-001 — Best Contact Explainability

For best_contact expose enough deterministic diagnostics to explain selection:

- buyer_role_match
- title
- email_status
- email_confidence
- contact_score
- data_sources

QA must be able to determine why this contact won.

---

# P6-CROSS-001 — Provider Order Independence

Run equivalent provider responses in reversed order.

Expected:
same logical deduplicated contacts
same best_contact
same contact scores

Provider call order must not alter final identity/ranking.

---

# P6-CROSS-002 — Duplicate Email Case Normalization

Emails:

Jane.Smith@Acme.com
jane.smith@acme.com

Expected:
same normalized email identity.

---

# Live Tests — Prepare, Do Not Require Now

## P6-LIVE-APOLLO-001

Run only if APOLLO_API_KEY configured.

Use 1–2 manually approved companies.

Record:
- provider request success
- contacts returned
- role relevance
- business email availability
- errors
- latency if desired

Initial result:
REVIEW

Do not automatically alter Golden expected values.

## P6-LIVE-HUNTER-001

Run only if HUNTER_API_KEY configured.

Use manually approved domain/email cases.

Record:
- domain search results
- verifier status
- normalized status
- confidence
- discrepancies

Initial result:
REVIEW

---

# Metrics Per Lead

Capture:

```text
company_name
company_domain
qualified_input
buyer_roles_requested
providers_configured
providers_called
provider_errors
raw_contacts_count
deduplicated_contacts_count
final_contacts_count
contacts_with_work_email
verified_contacts
likely_contacts
risky_contacts
unknown_contacts
best_contact
best_contact_score
enrichment_status
```

Optional:

```text
dedup_merge_events
email_domain_mismatches
provider_empty_results
discarded_personal_emails
```

---

# README-TEST Output

For each Phase 6 Golden case show:

```markdown
### P6-ENRICH-001 — Strong Mocked Enrichment

Input CompanyLead:
...

Buyer Roles:
...

Provider Configuration:
Apollo: mocked
Hunter: mocked

Provider Calls:
...

Provider Errors:
...

Raw Contacts:
...

Deduplicated Contacts:
...

Final Contacts:
...

Best Contact:
Name:
Title:
Buyer Role Match:
Work Email:
Email Status:
Confidence:
Contact Score:
Data Sources:

Enrichment Status:
...

Expected:
...

Actual:
...

Differences:
...

Result:
PASS / FAIL / REVIEW

Human Notes:
...
```

---

# Critical FAIL Conditions

FAIL immediately if:

- personal/public email is accepted as default work email
- Hunter confidence alone becomes verified
- verifier statuses are normalized incorrectly
- same email from Apollo/Hunter remains duplicate contacts
- same LinkedIn identity remains duplicate contacts
- same-name different-email people are incorrectly merged
- provider errors become silent empty successes
- no-provider configuration looks like successful no-result enrichment
- providers_used omits failed-but-called providers
- unverified/unknown contact incorrectly causes complete status under current contract
- request limits are ignored
- unqualified leads are enriched when qualified_only=true
- LLM directly assigns numeric contact score
- provider order changes final logical result
- personal email/phone reveal is requested unnecessarily

---

# Ground Truth Versioning

Each Golden case should include:

```json
{
  "ground_truth_version": 1,
  "enrichment_rules_version": 1,
  "provider_contract_version": 1,
  "human_approved": true
}
```

If rules or provider adapters change intentionally:

- increment appropriate version
- update expected manually
- record change_reason

Never rewrite Expected automatically from Actual.

---

# Model Policy

Phase 6 core QA must NOT depend on qwen2.5:1.5b.

Buyer-role context should come from frozen WebsiteProfile fixtures.

Contact ranking, deduplication, status normalization, limits, provider error handling,
and best-contact selection must be deterministic.

Live provider QA is separate from offline CI.

---

# Definition of Phase 6 QA Complete

Phase 6 offline QA is acceptable when:

1. company enrichment normalization passes
2. buyer-role ranking passes
3. contact normalization passes
4. cross-provider dedup passes
5. email status normalization passes
6. personal-email rejection passes
7. best-contact selection is deterministic
8. contact score bounds/determinism pass
9. enrichment status rules pass
10. provider error taxonomy passes
11. provider usage tracking passes
12. no-provider behavior is explicit
13. request limits pass
14. qualified_only behavior passes
15. partial provider failures degrade gracefully
16. privacy/data-minimization rules pass
17. provider-order independence passes
18. README exposes provider calls, errors, dedup, best-contact rationale
19. no critical unresolved deterministic bug remains
