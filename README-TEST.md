# SalesAI QA / Acceptance Test Report

## Run Information
- **Date:** 2026-09-29
- **Time:** 20:38:01 UTC
- **Git Commit:** `708f72b`
- **Python Version:** `3.14.7`
- **SALES_LLM_MODEL:** `qwen2.5:1.5b (default)`
- **Ollama Version:** `0.33.3`
- **Brave enabled?** No
- **Apollo enabled?** No
- **Hunter enabled?** No
- **Live Tests:** Yes

## Summary
- **Total Cases:** 5
- **Passed:** 0
- **Failed:** 1
- **Review Required:** 0
- **Skipped / Not Run:** 4
- **Source Changed:** 0

## Phase Summary
| Phase | Cases | Pass | Fail | Review |
|---|---|---|---|---|
| Phase 2 | 1 | 0 | 1 | 0 |
| Phase 3 | 1 | 0 | 0 | 0 |
| Phase 4 | 1 | 0 | 0 | 0 |
| Phase 5 | 1 | 0 | 0 | 0 |
| Phase 6 | 1 | 0 | 0 | 0 |

## Failures
### P2-WEB-001 — pyBIM Website Analysis
**Phase:** Phase 2
**Source:** https://pybim.com

**Input:**
```json
{
  "url": "https://pybim.com"
}
```
**Expected:**
```json
{
  "company_name": "pyBIM",
  "company_summary": "Advanced engineering and software lab providing BIM execution, custom BIM/Revit automation, and sovereign AI infrastructure for AEC organizations.",
  "services": [
    "Managed / Tech-Enabled BIM Execution",
    "BIM Modeling and Multidisciplinary Clash Coordination",
    "ISO 19650 and UNI 11337 Compliance Auditing",
    "Custom Python and C# BIM Automation",
    "Revit / Navisworks API Integration",
    "Automated Parameter and Metadata Processing",
    "Sovereign / Air-Gapped Enterprise AI Infrastructure"
  ],
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
    "Growing AEC firms",
    "Mid-to-large AEC enterprises",
    "Tier-1 contractors",
    "Government contractors"
  ],
  "pain_points": [
    "Manual BIM data entry and parameter mapping",
    "Repetitive BIM workflows",
    "Slow clash detection and coordination",
    "High engineering labor costs",
    "Scaling through additional headcount",
    "ISO 19650 and UNI 11337 compliance risk",
    "Public tender compliance risk",
    "BIM data quality errors",
    "Sensitive project data exposure to public cloud systems",
    "Rigid generic software workflows"
  ],
  "buyer_roles": [
    "BIM Manager",
    "Technical Director",
    "Head of Digital Delivery",
    "Innovation Lead",
    "Operations Manager"
  ],
  "primary_job_signals": [
    "BIM Manager / Head of BIM",
    "Digital Delivery Manager / Head of Digital Delivery",
    "BIM Automation Engineer",
    "Revit API Developer",
    "BIM Software Developer",
    "C# / .NET Revit Developer",
    "Python BIM Automation",
    "Dynamo / pyRevit Developer",
    "BIM Information / Data Manager",
    "ISO 19650 information-management roles",
    "Digital Construction / BIM technology leadership"
  ],
  "secondary_job_signals": [
    "Navisworks",
    "Solibri",
    "IFC / OpenBIM",
    "COBie",
    "Autodesk Construction Cloud / CDE",
    "Computational design",
    "BIM data / metadata management",
    "Digital transformation roles in AEC"
  ]
}
```
**Actual:**
```json
{
  "company_name": "pyBIM",
  "company_summary": "pyBIM provides algorithmic engineering, software development, and sovereign AI infrastructures for the AEC industry. Our solutions automate complex workflows, ensure ISO 19650 compliance, and secure classified data, making our clients' operations more efficient and compliant.",
  "services": [
    "Custom Software & Revit Automation",
    "Sovereign AI & Technical Consultation",
    "Turnkey BIM Project Delivery"
  ],
  "target_industries": [
    "AEC Firms",
    "Infrastructure Contractors",
    "Public Sector BIM Projects"
  ],
  "target_company_types": [
    "Tier-One AEC Firms",
    "Public Sector Contractors",
    "Large Infrastructure Projects"
  ],
  "pain_points": [
    "Limited scalability and ROI for BIM execution.",
    "Manual BIM workflows lead to data entry errors and slow project execution.",
    "Traditional BIM teams struggle with complex clash detection and public tender compliance."
  ],
  "buyer_roles": [
    "Project Managers",
    "Engineering Teams",
    "BIM Coordinators"
  ],
  "primary_job_signals": [
    "Implementing custom automation scripts to reduce manual labor.",
    "Deploying Sovereign AI infrastructures for secure data processing.",
    "Optimizing BIM workflows for high-margin billable hours."
  ],
  "secondary_job_signals": [
    "Using pyBIM's API integration for Revit and Navisworks.",
    "Writing Python and C# scripts for Revit API automation.",
    "Deploying edge AI appliances for secure, on-premise AI execution."
  ],
  "keywords": [
    "pyBIM",
    "algorithmic engineering",
    "BIM execution",
    "SOVEREIGN AI",
    "ISO 19650",
    "UNI 11337",
    "Revit API",
    "C#",
    "Python",
    "Sovereign Edge AI",
    "Data Integrity",
    "Security Compliance",
    "BIM Automation",
    "Public Tender Compliance",
    "Project Delivery"
  ],
  "negative_signals": [
    "No public inquiries or inquiries from public email domains."
  ]
}
```
**Result:** FAIL

**Differences:**

**Missing:**
- services: 'Managed / Tech-Enabled BIM Execution'
- services: 'BIM Modeling and Multidisciplinary Clash Coordination'
- services: 'ISO 19650 and UNI 11337 Compliance Auditing'
- services: 'Custom Python and C# BIM Automation'
- services: 'Revit / Navisworks API Integration'
- services: 'Automated Parameter and Metadata Processing'
- services: 'Sovereign / Air-Gapped Enterprise AI Infrastructure'
- target_industries: 'Engineering'
- target_industries: 'AEC'
- target_industries: 'Infrastructure'
- target_industries: 'Architecture'
- target_industries: 'Construction'
- buyer_roles: 'Innovation Lead'
- buyer_roles: 'Operations Manager'
- buyer_roles: 'Head of Digital Delivery'
- buyer_roles: 'Technical Director'
- buyer_roles: 'BIM Manager'

**Extra:**
- services: 'Custom Software & Revit Automation'
- services: 'Sovereign AI & Technical Consultation'
- services: 'Turnkey BIM Project Delivery'
- target_industries: 'Public Sector BIM Projects'
- target_industries: 'Infrastructure Contractors'
- target_industries: 'AEC Firms'
- buyer_roles: 'Engineering Teams'
- buyer_roles: 'BIM Coordinators'
- buyer_roles: 'Project Managers'

**Needs semantic review:**
- primary_job_signals: Model extracted business activities/goals instead of job titles (e.g. 'Implementing custom automation scripts...' vs 'BIM Manager').

**Reason:** Critical semantic failure: primary_job_signals contains business activities/goals instead of job titles, which will corrupt downstream Phase 3 Discovery.

**Human Notes:**
Ground Truth verified by human review from real pybim.com crawl snapshot. Deployed services vs In-Development offerings (Cloud Connect & Sovereign Edge AI) serves as semantic accuracy trap.

---

## Human Review Required
These cases executed successfully but require business logic confirmation, or represent ambiguous situations.

_No review required._

## Detailed Cases (All Results)

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
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

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
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

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
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

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
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

**Human Notes:**
Mock provider response needed.

---