# SalesAI Phase 3 QA — Golden Discovery Testing

## Goal

Validate Phase 3 Discovery Engine independently from Phase 2 model quality.

Phase 3 input must use a frozen, human-approved WebsiteProfile fixture.
Do NOT use the latest live WebsiteAnalyzer output as the Golden input.

The test must verify not only that queries are generated, but that they are useful for B2B job-intent discovery.

---

## Golden Case: P3-DISC-001 — pyBIM Discovery Query Generation

### Frozen Input

Use a human-approved fixture similar to:

```json
{
  "company_name": "pyBIM",
  "target_industries": [
    "AEC",
    "Architecture",
    "Engineering",
    "Construction",
    "Infrastructure"
  ],
  "target_company_types": [
    "Architecture firms",
    "Engineering firms",
    "General contractors",
    "Mid-to-large AEC enterprises",
    "Tier-1 contractors"
  ],
  "buyer_roles": [
    "BIM Manager",
    "Technical Director",
    "Head of Digital Delivery",
    "Innovation Lead",
    "Operations Manager"
  ],
  "primary_job_signals": [
    "BIM Manager",
    "Head of BIM",
    "Digital Delivery Manager",
    "Head of Digital Delivery",
    "BIM Automation Engineer",
    "Revit API Developer",
    "BIM Software Developer",
    "C# Revit Developer",
    "Python BIM Automation Engineer",
    "BIM Information Manager",
    "Digital Construction Manager"
  ],
  "secondary_job_signals": [
    "Navisworks",
    "Solibri",
    "IFC",
    "OpenBIM",
    "COBie",
    "Autodesk Construction Cloud",
    "CDE",
    "Computational Design",
    "BIM Data Management"
  ],
  "negative_signals": []
}
```

Countries:

```json
[
  "Italy",
  "United Kingdom",
  "Germany"
]
```

---

## Required Intent Coverage

The Golden test must not compare only literal query text.

It must evaluate query intent.

### A. Primary hiring intent

At least 3 distinct primary job-signal concepts must appear.

Examples:

- BIM Manager
- Head of BIM
- BIM Automation Engineer
- Revit API Developer
- Digital Delivery Manager
- BIM Information Manager

### B. Company careers intent

At least one query must explicitly target company careers/jobs pages.

Examples:

- `"BIM Manager" careers engineering`
- `"Revit API Developer" careers architecture`

Exact wording is not required.

### C. ATS coverage

When query budget allows, cover at least two of:

- Lever
- Greenhouse
- Ashby

Examples:

- `site:jobs.lever.co "BIM Manager"`
- `site:boards.greenhouse.io "Revit API Developer"`
- `site:jobs.ashbyhq.com "Digital Delivery Manager"`

### D. Secondary-signal support

Secondary technologies should support role-based queries.

GOOD:

- `"BIM Automation Engineer" Python Revit`
- `"BIM Information Manager" COBie`
- `"Digital Delivery Manager" ISO 19650`

BAD:

- `COBie jobs`
- `IFC company`
- `Navisworks hiring`

### E. Geography

Each requested country must receive meaningful coverage.

Do not require every query to contain a country if the query is intentionally global/ATS-targeted.

---

## Critical Failure Patterns

Any of these should make P3-DISC-001 FAIL.

### Business goals used as job titles

Examples:

- `Identify operational bottlenecks jobs`
- `Evaluate automation ROI hiring`
- `Reduce operational costs careers`
- `Deploy AI infrastructures jobs`

### Buyer-role pollution

Do not automatically turn every buyer role into a hiring signal.

For example:

- Operations Manager
- Innovation Lead

must not automatically become primary discovery queries unless present in approved primary_job_signals.

### Generic noisy queries

Flag weak queries like:

- `engineering jobs`
- `construction careers`
- `AI jobs`
- `BIM company`

unless combined with strong role/technology intent.

### Duplicate query waste

Normalize:

- case
- whitespace
- quote variants
- trivial `in <country>` wording differences

Near-duplicates must not consume query budget.

---

## Deterministic Metrics

For every Phase 3 Golden run calculate:

```text
total_queries
unique_queries
duplicate_queries
primary_signals_covered
countries_requested
countries_covered
ats_providers_covered
career_intent_present
secondary_supported_query_count
forbidden_pattern_count
```

Recommended acceptance rules:

```text
duplicate_queries == 0
forbidden_pattern_count == 0
primary_signals_covered >= 3
all requested countries covered
ats_providers_covered >= 2
career_intent_present == true
```

If semantics are valid but one non-critical coverage target is missed:

REVIEW

If a critical forbidden pattern occurs:

FAIL

If all critical checks and required coverage pass:

PASS

---

## Additional Offline Negative Cases

### P3-NEG-001 — Business Goal Pollution

Input includes invalid primary signal:

`Reduce operational costs`

Expected:

The Discovery layer rejects or ignores it.

It must not generate:

`"Reduce operational costs" jobs`

---

### P3-NEG-002 — Duplicate Signals

Input:

```text
BIM Manager
bim manager
BIM Manager 
Head of BIM
```

Expected:

No trivial duplicate query explosion.

---

### P3-NEG-003 — Empty Primary Signals

Input:

```json
"primary_job_signals": []
```

Expected:

Controlled partial/empty query result.

Do not hallucinate arbitrary job titles.

---

### P3-NEG-004 — Secondary Only

Input contains only:

```text
Revit
COBie
IFC
```

Expected:

Do not pretend these technologies are job titles.

No strong discovery queries should be fabricated.

---

### P3-NEG-005 — Geography Coverage

Countries:

```text
Italy
United Kingdom
Germany
```

Expected:

All three receive coverage without mechanically triplicating every query.

---

## README-TEST Output

The generated README-TEST.md must show:

```markdown
### P3-DISC-001 — pyBIM Discovery Query Generation

Source:
tests/golden/phase3_discovery.json

Input Profile:
...

Countries:
Italy
United Kingdom
Germany

Expected Intents:
- Primary hiring intent
- Company-career intent
- ATS discovery
- Secondary technology support
- Geography coverage

Actual Queries:
1. ...
2. ...
3. ...

Metrics:
Total Queries:
Unique Queries:
Duplicate Queries:
Primary Signals Covered:
Countries Covered:
ATS Providers Covered:
Career Intent Present:
Forbidden Patterns:

Missing Intents:
...

Noisy Queries:
...

Result:
PASS / FAIL / REVIEW

Human Notes:
...
```

---

## Optional Live Search Case: P3-LIVE-001

This is separate from offline CI.

Run only when Brave or another search provider is configured.

Use a small approved subset of 3 generated queries.

For each query inspect up to 10 results.

Record:

```text
query
results_checked
relevant_job_or_career_results
irrelevant_results
duplicates
provider_errors
```

Categorize results:

```text
company_career
lever
greenhouse
ashby
other_ats
job_board
irrelevant
```

Compute:

```text
Precision@10 = relevant_results / results_checked
```

Initial live runs should normally be REVIEW until a realistic baseline is established.

---

## Phase 3 Pass Definition

Phase 3 can be considered QA-acceptable when:

1. Existing unit tests pass.
2. P3-DISC-001 has zero critical forbidden patterns.
3. At least 3 approved primary job signals are covered.
4. Requested geography is covered.
5. Careers intent exists.
6. ATS intent exists.
7. Duplicate query count is zero.
8. Empty/invalid signals fail safely.
9. Actual queries and metrics are visible in README-TEST.md.
10. Live search quality is audited separately when a provider is configured.
