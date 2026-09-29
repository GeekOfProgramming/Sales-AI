# SalesAI QA / Acceptance Test Report

## Run Information
- **Date:** 2026-09-29
- **Time:** 19:34:54 UTC

## Summary
- **Total Cases:** 5
- **Passed:** 0
- **Failed:** 0
- **Review Required:** 0
- **Skipped / Not Run:** 5
- **Source Changed:** 0

## Phase Summary
| Phase | Cases | Pass | Fail | Review |
|---|---|---|---|---|
| Phase 2 | 1 | 0 | 0 | 0 |
| Phase 3 | 1 | 0 | 0 | 0 |
| Phase 4 | 1 | 0 | 0 | 0 |
| Phase 5 | 1 | 0 | 0 | 0 |
| Phase 6 | 1 | 0 | 0 | 0 |

## Failures
_No failures detected._

## Human Review Required
These cases executed successfully but require business logic confirmation, or represent ambiguous situations.

_No review required._

## Detailed Cases (All Results)

### P2-WEB-001 — Placeholder: Architectural Firm Website
**Phase:** Phase 2
**Source:** https://example-architects.com

**Input:**
```json
{
  "url": "https://example-architects.com"
}
```
**Expected:**
```json
{
  "company_name": "Example Architects",
  "services": [
    "Architecture",
    "BIM"
  ],
  "target_industries": [
    "Commercial"
  ],
  "buyer_roles": [
    "BIM Manager",
    "Principal"
  ],
  "pain_points": [
    "Coordination",
    "Clash detection"
  ]
}
```

**Result:** NOT_RUN

**Human Notes:**
Needs human review of actual snapshot.

---
### P3-DISC-001 — Placeholder: Discovery Query Generation
**Phase:** Phase 3
**Source:** internal

**Input:**
```json
{
  "website_profile": {
    "company_name": "ABC BIM",
    "target_industries": [
      "Construction"
    ]
  },
  "countries": [
    "US",
    "UK"
  ]
}
```
**Expected:**
```json
{
  "generated_queries": [
    "Construction companies hiring BIM Manager in US"
  ],
  "expected_query_intents": [
    "Find construction companies hiring BIM professionals"
  ]
}
```

**Result:** NOT_RUN

**Human Notes:**
Needs live results verification or snapshot

---
### P4-JOB-001 — Placeholder: Greenhouse BIM Manager
**Phase:** Phase 4
**Source:** https://example.com/job/123

**Input:**
```json
{
  "html": "<html><body>BIM Manager at ABC Engineering. Requires Revit.</body></html>",
  "url": "https://example.com/job/123"
}
```
**Expected:**
```json
{
  "company": "ABC Engineering",
  "job_title": "BIM Manager",
  "technologies": [
    "Revit"
  ],
  "relevant_signals": [
    "BIM Manager",
    "Revit"
  ],
  "evidence": "Requires Revit"
}
```

**Result:** NOT_RUN

**Human Notes:**
Needs human verification.

---
### P5-LEAD-001 — Placeholder: Strong Lead Aggregation
**Phase:** Phase 5
**Source:** None

**Input:**
```json
{
  "jobs": [
    {
      "company": "ABC Engineering",
      "url": "https://example.com/job/1",
      "relevant_signals": [
        "Revit"
      ]
    }
  ]
}
```
**Expected:**
```json
{
  "expected_company_grouping": 1,
  "expected_qualified_state": true,
  "expected_score_range": [
    50,
    100
  ]
}
```

**Result:** NOT_RUN

**Human Notes:**
Needs review.

---
### P6-ENRICH-001 — Placeholder: Company and Contact Enrichment
**Phase:** Phase 6
**Source:** None

**Input:**
```json
{
  "company_domain": "acme.com",
  "company_name": "Acme Corp"
}
```
**Expected:**
```json
{
  "company_domain": "acme.com",
  "expected_contact_role": "BIM Manager",
  "expected_email_status": "verified"
}
```

**Result:** NOT_RUN

**Human Notes:**
Mock provider response needed.

---