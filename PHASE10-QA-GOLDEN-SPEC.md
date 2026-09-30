# SalesAI Phase 10 QA / Golden Acceptance Spec
## Multi-Source Market Discovery, Historical Seed Import, Company-First Qualification

# Purpose

Phase 10 QA validates the transition from job-first discovery into a
multi-source, company-centric market discovery system without breaking frozen
Phases 1–9.

The test suite must prove that:

- one real company remains one logical company across multiple sources
- different companies with similar names remain separate
- legal form never determines Tier by itself
- absence of BIM evidence never becomes a fabricated negative fact
- directory/project/tender evidence never becomes a fake StructuredJob
- historical spreadsheets import idempotently
- previously contacted companies do not silently restart cold outreach
- watchlist companies can be promoted by new evidence
- source failures do not destroy successful results
- Phase 5 frozen job-led scores remain unchanged
- Phase 6/7/8/9 integrations remain safe
- Phase 10 never sends email
- source provenance remains auditable
- runtime DB/secrets stay out of release artifacts

Phase 10 QA is deterministic by default.

No live email send belongs to this phase.

---

# 1. Test Architecture

Create:

```text
tests/golden/
├── phase10_discovery.json
└── snapshots/
    └── phase10/
        ├── candidate_company.json
        ├── multi_source_company.json
        ├── historical_import.json
        ├── watchlist_candidate.json
        ├── project_first_candidate.json
        ├── same_name_different_domains.json
        └── expected/
            ├── dedup_result.json
            ├── qualification_result.json
            ├── watchlist_result.json
            ├── historical_outreach_guard.json
            └── discovery_run_summary.json

tests/acceptance/
└── test_phase10_acceptance.py

tests/reports/
└── phase10_latest.json
```

Create/extend:

```text
tests/test_discovery_v2.py
tests/test_historical_import.py
tests/test_candidate_identity.py
tests/test_phase10_bridges.py
tests/acceptance/test_qa_integrity.py
```

Use temporary SQLite databases only.

Never mutate the production runtime DB in tests.

---

# 2. Frozen-Phase Compatibility Gate

Before testing new behavior, prove that existing frozen contracts remain intact.

Required:

```text
Phase 3 job discovery unchanged
Phase 4 StructuredJob extraction unchanged
Phase 5 frozen scores unchanged
Phase 6 contact identity safety unchanged
Phase 7 legacy export columns unchanged
Phase 8 grounded-email constraints unchanged
Phase 9 approval/send/suppression authority unchanged
```

Any failure here is a Phase 10 FAIL.

---

# 3. Golden CandidateCompany Fixture

Create a sanitized canonical fixture:

```json
{
  "candidate_id": "domain:example-engineering.com",
  "company_name": "Example Engineering",
  "normalized_company_name": "example engineering",
  "company_domain": "example-engineering.com",
  "country": "Italy",
  "city": "Milan",
  "company_types": ["engineering", "mep_engineering"],
  "legal_form": "S.r.l.",
  "company_tier": "tier_2",
  "tier_confidence": 0.75,
  "bim_maturity": "medium",
  "fit_assessment": "high",
  "intent_assessment": "medium",
  "outsourcing_potential": "high",
  "automation_potential": "medium",
  "source_refs": [],
  "evidence_refs": []
}
```

This is synthetic test data only.

Do not use real confidential customer records in public fixtures.

---

# 4. Core Candidate Identity Tests

## P10-ID-001 — Domain Identity Is Primary

Same canonical domain from two sources -> one CandidateCompany.


## P10-ID-002 — Scheme/Path Normalization

These:

```text
https://example.com
http://www.example.com/
example.com/jobs
```

must resolve to canonical:

```text
example.com
```


## P10-ID-003 — Same Name, Different Domains Stay Separate

```text
ABC Engineering
abc-engineering.com

ABC Engineering
abcengineering.co.uk
```

must remain two companies unless explicit strong identity evidence proves otherwise.


## P10-ID-004 — Same Name + Different Country Stays Separate

Weak name equality alone cannot merge.


## P10-ID-005 — Trusted Source Identity Can Support Merge

Only when source identity is explicit and non-conflicting.


## P10-ID-006 — Weak Manual Name Identity Does Not Overmerge


## P10-ID-007 — Alias Preservation

Known alternate company names are stored as aliases, not new companies.


## P10-ID-008 — Domain History Does Not Delete Prior Identity

Historical domain changes remain auditable.

---

# 5. Mandatory Dedup Regression

## P10-REG-001 — Same Company Across Multiple Sources Merges Once

Input observations:

```text
Google/business directory
LinkedIn/ATS job
official website
project/tender source
```

All resolve to same canonical domain.

Expected:

```text
CandidateCompany count = 1
DiscoveryObservation count = 4
source refs = 4
```

No duplicate lead/company objects.

---

# 6. Same-Name Separation Regression

## P10-REG-002 — Similar Name, Different Trusted Domains Remain Separate

Expected:

```text
CandidateCompany count = 2
```

No fuzzy-name collapse.

---

# 7. DiscoveryObservation Tests

## P10-OBS-001 — One Source Appearance Creates One Observation


## P10-OBS-002 — Observation Stores Source Provenance

Must include:

```text
source_id
source_type
source_url
observed_company_name
observed_at
```


## P10-OBS-003 — Repeated Same Observation Is Idempotent


## P10-OBS-004 — New Observation Appends To Existing Company


## P10-OBS-005 — Observation Is Not Automatically Business Evidence

"found in directory" != "has BIM capability".


## P10-OBS-006 — Observation Timestamp Preserved

---

# 8. MarketEvidence Tests

## P10-EVID-001 — Business Evidence Category


## P10-EVID-002 — Intent Evidence Category


## P10-EVID-003 — Project Evidence Category


## P10-EVID-004 — Historical Evidence Category


## P10-EVID-005 — Provenance Required

Every qualification-driving evidence item must have traceable source data.


## P10-EVID-006 — Missing Source Cannot Become Verified Fact


## P10-EVID-007 — Evidence History Is Append-Only

---

# 9. Source Registry Tests

## P10-SRC-001 — Load discovery_sources.yaml


## P10-SRC-002 — Enabled Source Runs


## P10-SRC-003 — Disabled Source Does Not Run


## P10-SRC-004 — Unknown Source Type Rejected


## P10-SRC-005 — Source Requires API Key But Key Missing

Controlled source-specific error.


## P10-SRC-006 — Source Priority Deterministic


## P10-SRC-007 — max_results_per_run Enforced


## P10-SRC-008 — Source Config Does Not Contain Secrets

Secrets remain environment/server-side.

---

# 10. Market Config Tests

## P10-MKT-001 — Load markets.yaml


## P10-MKT-002 — Market Enable/Disable


## P10-MKT-003 — Country Is Configuration, Not Architecture Branch


## P10-MKT-004 — Languages Preserved


## P10-MKT-005 — Preferred Sources Deterministic


## P10-MKT-006 — Standards Context Is Context Only

No standards applicability fabricated.

---

# 11. Tier Tests

Allowed:

```text
tier_1
tier_2
tier_3
unknown
```

## P10-TIER-001 — Tier From Multi-Factor Evidence


## P10-TIER-002 — Weak Evidence -> unknown


## P10-TIER-003 — Tier Confidence Preserved


## P10-TIER-004 — Tier Evidence Refs Required For Non-Unknown Classification


## P10-REG-003 — Legal Form Alone Never Determines Tier

Test independently:

```text
S.r.l.
S.p.A.
GmbH
Ltd
LLC
```

with no other evidence.

Expected:

```text
company_tier = unknown
```

or equivalent non-forced classification.

---

# 12. BIM Maturity Tests

Allowed:

```text
unknown
low
medium
high
enterprise
```

## P10-BIM-001 — Explicit BIM Evidence Can Raise Maturity


## P10-BIM-002 — Revit/IFC/ISO Evidence Is Grounded


## P10-BIM-003 — BIM Team Evidence Preserved


## P10-REG-004 — No BIM Mention Does Not Mean No BIM Capability

Input:
valid company profile with no BIM keywords.

Expected:

```text
bim_maturity = unknown
```

not:

```text
low
none
false
no_bim
```

unless explicit negative evidence exists.

---

# 13. Fit vs Intent Tests

## P10-FIT-001 — Strong Fit, Unknown Intent

Expected:

```text
fit = high
intent = unknown
```


## P10-FIT-002 — Weak Fit, Strong Intent

Kept as separate dimensions.


## P10-FIT-003 — Missing Job Does Not Lower Fit Automatically


## P10-INT-001 — Relevant Job Is Intent Evidence


## P10-INT-002 — Project Award Is Intent Evidence


## P10-INT-003 — Tender Is Intent Evidence


## P10-INT-004 — Missing Intent Remains unknown/none According To Contract

No fabricated urgency.

---

# 14. Opportunity-Type Tests

Recommended opportunity types:

```text
direct_bim_delivery
mep_delivery
bim_coordination
overflow_capacity
automation
technology_partnership
channel_partnership
strategic_collaboration
unknown
```

## P10-OPP-001 — Direct Delivery Opportunity


## P10-OPP-002 — Technology Partnership Opportunity


## P10-OPP-003 — Multiple Opportunity Types Allowed


## P10-OPP-004 — Opportunity Type Is Not Stated As Verified Fact Without Evidence

Internal strategy != external factual claim.

---

# 15. Historical Import Tests

Support:

```text
.xlsx
.csv
```

## P10-HIST-001 — XLSX Import


## P10-HIST-002 — CSV Import


## P10-HIST-003 — Preserve Source Row Reference


## P10-HIST-004 — Preserve Raw Human Tier


## P10-HIST-005 — Preserve Notes


## P10-HIST-006 — Preserve Historical Source


## P10-HIST-007 — Preserve Contact Dates


## P10-HIST-008 — Preserve Follow-Up Dates


## P10-HIST-009 — Preserve Reply Status


## P10-HIST-010 — Preserve Wrong/Invalid Email State


## P10-HIST-011 — Missing Fields Stay Missing

Do not fabricate data.

---

# 16. Historical Import Idempotency

## P10-REG-007 — Same Historical File Imports Idempotently

Import exact same sanitized workbook twice.

Expected:

```text
company count unchanged
outreach event count unchanged
contact count unchanged
```

No duplicates.

---

# 17. Historical Multi-Email Tests

## P10-HIST-EMAIL-001 — Split Multiple Emails

One cell:

```text
info@example.com
commerciale@example.com
person.name@example.com
```

becomes distinct normalized email records.


## P10-HIST-EMAIL-002 — Email Classification

Possible categories:

```text
generic
business
named_work
certified_legal
unknown
```


## P10-HIST-EMAIL-003 — Historical Email Is Not Auto-Verified

Expected:

```text
historical_unknown
```

unless separate current verification exists.


## P10-HIST-EMAIL-004 — Raw Original Cell Preserved For Audit

---

# 18. Historical Outreach Guard

## P10-REG-006 — Previously Contacted Company Does Not Start New Cold Outreach

Input:

```text
historical outreach exists
new discovery signal appears
```

Expected:

```text
cold_outreach_allowed = false
qualification route =
known_company
or reengagement_review
```

No automatic new cold sequence.

---

# 19. Historical Reply Ground Truth Tests

Use sanitized fixtures modeled after known positive outcomes.

Do NOT copy real personal information.

Example A:

```text
job-led BIM/MEP company
positive reply
specific modeling opportunity
meeting requested
```

Example B:

```text
BIM/software ecosystem company
positive reply
meeting slots proposed
technology/strategic collaboration
```

## P10-HIST-OUTCOME-001 — Positive Reply Preserved


## P10-HIST-OUTCOME-002 — Actual Respondent May Differ From Original Mailbox


## P10-HIST-OUTCOME-003 — Reply Outcome Does Not Automatically Re-score Entire Market


## P10-HIST-OUTCOME-004 — Small Sample Not Used For Automatic Statistical Optimization

---

# 20. No Fake Jobs

## P10-REG-005 — Directory Result Never Creates StructuredJob


## P10-NOJOB-002 — Project Award Never Creates StructuredJob


## P10-NOJOB-003 — Tender Never Creates StructuredJob


## P10-NOJOB-004 — Website Company Profile Never Creates StructuredJob


## P10-NOJOB-005 — Actual Job Still Creates StructuredJob Through Existing Path

---

# 21. Job-First Compatibility

## P10-JOB-001 — Existing Phase 3 Job Query Still Works


## P10-JOB-002 — Existing Phase 4 StructuredJob Shape Preserved


## P10-REG-011 — Frozen Phase 5 Score Remains Unchanged

Use existing golden fixture.

Expected exact historical score.

No Phase 10 field may alter legacy Phase 5 arithmetic.

---

# 22. Company-First Discovery Tests

## P10-COMP-001 — Manual Domain Seed


## P10-COMP-002 — Manual Company Name Seed


## P10-COMP-003 — Directory Company Candidate


## P10-COMP-004 — Website Resolution


## P10-COMP-005 — Candidate Research


## P10-COMP-006 — No Job Required For Candidate Creation


## P10-COMP-007 — Qualification Depends On Company Evidence, Not Fake Job

---

# 23. Project/Tender-First Tests

## P10-PROJ-001 — Project Observation Creates CandidateCompany


## P10-PROJ-002 — Tender Observation Creates CandidateCompany


## P10-PROJ-003 — Participating Company Identity Resolved


## P10-PROJ-004 — Project Evidence Retains Source


## P10-PROJ-005 — Project Value Not Invented


## P10-PROJ-006 — Missing Company Role Stays Unknown

---

# 24. Company Research Tests

Research order should remain deterministic where applicable:

```text
official website
services
projects
careers
BIM/digital
technology
public project/tender evidence
```

## P10-RES-001 — Official Website Preferred


## P10-RES-002 — Services Extracted As Grounded Facts


## P10-RES-003 — Project Sectors Grounded


## P10-RES-004 — Careers URL Captured


## P10-RES-005 — BIM Terms Grounded


## P10-RES-006 — No Revenue Fabrication


## P10-RES-007 — No Employee Count Fabrication


## P10-RES-008 — No Outsourcing Fact Fabrication

---

# 25. Outsourcing Potential Tests

Allowed:

```text
unknown
low
medium
high
```

## P10-OUT-001 — Assessment Uses Explainable Signals


## P10-OUT-002 — High Potential Is Not Stored As "outsources BIM = true"


## P10-OUT-003 — Missing Evidence -> unknown


## P10-OUT-004 — Assessment Cannot Leak Into Email As Verified Fact

---

# 26. Automation Potential Tests

## P10-AUTO-001 — Revit/API Hiring Signal


## P10-AUTO-002 — Python/C# Signal


## P10-AUTO-003 — Large Digital Team Signal


## P10-AUTO-004 — Missing Evidence -> unknown


## P10-AUTO-005 — Potential Is Strategy, Not Factual Claim

---

# 27. Qualification Tests

Allowed outcomes:

```text
reject
watchlist
research_more
qualified_for_enrichment
known_company
reengagement_review
suppressed
```

## P10-QUAL-001 — Reject Irrelevant Company


## P10-QUAL-002 — Watchlist Good Fit/Weak Intent


## P10-QUAL-003 — Research More


## P10-QUAL-004 — Qualified For Enrichment


## P10-QUAL-005 — Known Company


## P10-QUAL-006 — Reengagement Review


## P10-QUAL-007 — Suppressed

---

# 28. Watchlist Tests

## P10-REG-008 — Good Fit + Weak Intent Routes To Watchlist


## P10-WATCH-002 — Watch Reason Preserved


## P10-WATCH-003 — next_check_at Preserved


## P10-WATCH-004 — No Outreach From Watchlist Alone


## P10-WATCH-005 — Watchlist Persists Across Process Restart

Use same temp DB.

---

# 29. Watchlist Promotion

## P10-REG-009 — New Job/Project Signal Can Promote Candidate

Flow:

```text
strong fit
weak intent
→ watchlist

later:
new BIM job / project / tender signal

→ reevaluate
→ qualified_for_enrichment
```

No duplicate company.

---

# 30. Suppression Guard

## P10-REG-010 — Suppressed Company/Contact Cannot Qualify For Outreach

Phase 10 must respect Phase 9 suppression state.

Expected route:

```text
suppressed
```

No Phase 6/8 cold-outreach continuation.

---

# 31. Phase 6 Bridge Tests

## P10-P6-001 — Qualified Non-Job Candidate Reaches Enrichment Adapter


## P10-P6-002 — Unqualified Candidate Does Not Reach Phase 6


## P10-P6-003 — Historical Contact Reused Before Provider Call


## P10-P6-004 — Historical Email Not Treated As Verified


## P10-P6-005 — Existing Strong Identity Merge Rules Preserved

No same-name contact merge.

---

# 32. Phase 7 Export Retrofit Tests

## P10-P7-001 — Legacy Columns Remain


## P10-P7-002 — Add company_tier


## P10-P7-003 — Add bim_maturity


## P10-P7-004 — Add fit_level


## P10-P7-005 — Add intent_level


## P10-P7-006 — Add outsourcing_potential


## P10-P7-007 — Add automation_potential


## P10-P7-008 — Add discovery_sources


## P10-P7-009 — Add historical state fields


## P10-P7-010 — Optional New Sheets Are Additive

No removal of frozen sheets.

---

# 33. Phase 8 Evidence Bridge

## P10-REG-012 — Phase 8 Receives Only Grounded Evidence

Allowed:

```text
real job
real project
real public service
real technology
real standard reference
real company sector
```

Disallowed as factual email content:

```text
outsourcing_potential = high
automation_potential = high
likely has budget
needs help
probably understaffed
```

No speculative assessment may become email factual evidence.

---

# 34. Phase 9 Historical/Send Guard

## P10-P9-001 — Existing Phase 9 Send History Blocks New Cold Sequence


## P10-P9-002 — Existing Active Conversation Blocks New Cold Sequence


## P10-P9-003 — Suppression Is Authoritative


## P10-P9-004 — Phase 10 Cannot Mark Draft Approved


## P10-P9-005 — Phase 10 Cannot Send Email


## P10-P9-006 — Phase 10 Cannot Reset Sent State

---

# 35. Source Partial Failure

## P10-REG-013 — One Source Failure Preserves Other Source Results

Example:

```text
Directory A -> failure
Search B -> success
Tender C -> success
```

Expected:

```text
B/C candidates preserved
A error recorded
run not globally failed
```

---

# 36. Discovery Run Tests

## P10-RUN-001 — Create DiscoveryRun


## P10-RUN-002 — started_at / completed_at


## P10-RUN-003 — Markets Preserved


## P10-RUN-004 — Source IDs Preserved


## P10-RUN-005 — observations_found Accurate


## P10-RUN-006 — new_candidates Accurate


## P10-RUN-007 — known_candidates Accurate


## P10-RUN-008 — watchlist_candidates Accurate


## P10-RUN-009 — qualified_candidates Accurate


## P10-RUN-010 — errors Accurate


## P10-RUN-011 — config_version Preserved

---

# 37. Repeated Daily Discovery

## P10-REG-014 — Repeated Daily Run Does Not Duplicate Companies

Run same deterministic source fixture twice.

Expected:

```text
company count unchanged
observation identity deduplicated per contract
run count increments
```

---

# 38. Daily Discovery Entry Point

## P10-DAILY-001 — CLI Entry Exists


## P10-DAILY-002 — API Entry Exists


## P10-DAILY-003 — Daily Discovery Sends Zero Email


## P10-DAILY-004 — Daily Discovery Creates No Approval


## P10-DAILY-005 — Daily Discovery Partial Failure Isolated

---

# 39. Fallback Discovery Strategy

## P10-FALLBACK-001 — Job Search Meets Target

No unnecessary fallback.


## P10-FALLBACK-002 — Weak Job Results Trigger Company-First


## P10-FALLBACK-003 — Weak Company Results Trigger Project/Tender


## P10-FALLBACK-004 — Good Fit/No Intent Goes Watchlist


## P10-FALLBACK-005 — No Arbitrary Lead Count Fabrication

If market yields fewer qualified leads, report actual result.

---

# 40. Source Health Tests

## P10-HEALTH-001 — last_success


## P10-HEALTH-002 — last_failure


## P10-HEALTH-003 — last_error Sanitized


## P10-HEALTH-004 — results_last_run


## P10-HEALTH-005 — new_candidates_last_run

---

# 41. API Acceptance Tests

Test:

```text
POST /api/sales/discovery/run

GET /api/sales/discovery/runs

GET /api/sales/discovery/runs/{run_id}

GET /api/sales/candidates

GET /api/sales/candidates/{candidate_id}

POST /api/sales/candidates/{candidate_id}/research

POST /api/sales/candidates/{candidate_id}/qualify

POST /api/sales/candidates/{candidate_id}/watch

GET /api/sales/sources

POST /api/sales/sources/import

POST /api/sales/history/import

GET /api/sales/watchlist
```

Required cases:

```text
P10-API-001 discovery run
P10-API-002 candidate list
P10-API-003 candidate detail
P10-API-004 research
P10-API-005 qualify
P10-API-006 watch
P10-API-007 source list
P10-API-008 source import validation
P10-API-009 historical import
P10-API-010 watchlist list
P10-API-011 missing candidate
P10-API-012 invalid source type
P10-API-013 no send endpoint under Phase 10 namespace
```

---

# 42. Security Tests

## P10-SEC-001 — Source API Keys Not Returned


## P10-SEC-002 — Source API Keys Not Persisted In Candidate Evidence


## P10-SEC-003 — Historical File Path Traversal Rejected


## P10-SEC-004 — SQL Injection Inputs Remain Data


## P10-SEC-005 — Source Error Secrets Sanitized


## P10-SEC-006 — No Personal Phone Scraping Added


## P10-SEC-007 — No Personal Email Promotion


## P10-SEC-008 — Business Data Only According To Contract

---

# 43. Runtime DB Packaging

## P10-REG-015 — Runtime Database Never Enters Release ZIP

Must exclude:

```text
data/sales_outreach.db
*.sqlite
*.sqlite3
*.db
.env
private keys
credentials
```

Keep source-controlled sanitized fixtures.

---

# 44. Database Tests

## P10-DB-001 — Candidate Tables Initialize


## P10-DB-002 — Reinitialization Idempotent


## P10-DB-003 — Candidate Identity Constraints


## P10-DB-004 — Observation Uniqueness


## P10-DB-005 — Evidence Persistence


## P10-DB-006 — Watchlist Persistence


## P10-DB-007 — Historical Batch Persistence


## P10-DB-008 — Transaction Rollback On Failure


## P10-DB-009 — No Half-Written Candidate On Internal Error


## P10-DB-010 — Schema Version/Migration Preserved

---

# 45. Historical Positive-Outcome Fixtures

Create sanitized fixtures representing two patterns discovered in prior human outreach.

Pattern A:

```text
job-led engineering company
active BIM/MEP signal
direct project relevance
positive response
specific modeling activity
meeting/conversation
```

Pattern B:

```text
BIM/CAD/OpenBIM ecosystem company
strategic/technology collaboration
positive response
meeting slots proposed
```

These fixtures are for regression/evaluation only.

Do not make statistical claims from two examples.

## P10-POS-001 — Direct Delivery Positive Outcome Preserved


## P10-POS-002 — Strategic Partnership Positive Outcome Preserved


## P10-POS-003 — Outcome Type Does Not Alter Historical Facts


## P10-POS-004 — Positive Outcome Can Inform Opportunity Type


## P10-POS-005 — Positive Outcome Does Not Auto-Approve Future Outreach

---

# 46. Known Company Re-Discovery

## P10-KNOWN-001 — Known Historical Company Found Again


## P10-KNOWN-002 — New Observation Appended


## P10-KNOWN-003 — No New Cold Sequence


## P10-KNOWN-004 — New Strong Signal -> reengagement_review


## P10-KNOWN-005 — No Auto Re-Engagement Send

---

# 47. Evidence Freshness Tests

## P10-FRESH-001 — observed_at Preserved


## P10-FRESH-002 — fresh/recent/old/unknown Classification


## P10-FRESH-003 — Historical Evidence Not Deleted


## P10-FRESH-004 — Stale Job Signal Not Treated As Current Intent


## P10-FRESH-005 — Newer Evidence Can Supersede For Current Assessment Without Deleting History

---

# 48. Research Limits

Test:

```text
MAX_CANDIDATES_PER_RUN
MAX_RESEARCH_PAGES_PER_COMPANY
MAX_SOURCES_PER_COMPANY
MAX_WATCHLIST_CHECKS_PER_RUN
```

## P10-LIMIT-001 — Candidate Limit


## P10-LIMIT-002 — Research Page Limit


## P10-LIMIT-003 — Source Limit


## P10-LIMIT-004 — Watchlist Check Limit


## P10-LIMIT-005 — No Silent Overrun

---

# 49. LLM Boundary Tests

If LLM-assisted classification exists:

## P10-LLM-001 — Reuse ai_engine/llm_client.py


## P10-LLM-002 — No Second Ollama Client


## P10-LLM-003 — LLM Cannot Invent Company Facts


## P10-LLM-004 — LLM Cannot Invent Jobs


## P10-LLM-005 — LLM Cannot Assign Frozen Numeric Lead Score


## P10-LLM-006 — Weak Semantic Output Falls Back Safely


## P10-LLM-007 — Tier Can Be unknown

No forced classification.

---

# 50. No-Send Static/Runtime Tests

## P10-NOSEND-001 — discovery_v2 Does Not Import SMTP Sender


## P10-NOSEND-002 — discovery_v2 Does Not Call SendOrchestrator


## P10-NOSEND-003 — Discovery API Has No Auto-Send Path


## P10-NOSEND-004 — Provider Delivery Count Always Zero

---

# 51. Operator Console Compatibility

Phase 10 backend must expose enough data for future Phase 10.5.

Test availability of fields required by future views:

```text
Discoveries
Sources
Candidates
Watchlist
Qualification
```

## P10-UIBRIDGE-001 — Candidate List Has Summary Fields


## P10-UIBRIDGE-002 — Candidate Detail Has Evidence


## P10-UIBRIDGE-003 — Source Health Available


## P10-UIBRIDGE-004 — Watchlist Status Available


## P10-UIBRIDGE-005 — Qualification Reason Available

Do not require frontend implementation in Phase 10.

---

# 52. QA Reporting Integrity

Use the hardened execution-driven model from Phases 8/9.

Golden catalog presence is NOT execution evidence.

Every deterministic case must map:

```text
case_id -> pytest node/evaluator
```

No automatic PASS.

No copying Expected into Actual.

No hardcoded false_pass_count.

Unmapped deterministic cases:

```text
NOT_RUN
```

not PASS.

---

# 53. Phase 10 Report Fields

`tests/reports/phase10_latest.json` should include:

```text
phase
run_timestamp
git_commit

total_catalog_cases
mapped_executable_cases
executed_cases

passed
failed
review
not_run

unmapped_deterministic_cases
false_pass_count

candidate_count
observation_count
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

Expected before freeze:

```text
unmapped_deterministic_cases = 0
false_pass_count = 0

duplicate_company_count = 0
overmerge_count = 0
fake_job_count = 0
historical_duplicate_count = 0
cold_outreach_bypass_count = 0
suppression_bypass_count = 0

phase5_regression_count = 0
phase8_grounding_violation_count = 0
phase9_send_violation_count = 0

runtime_db_packaged_count = 0
source_partial_failure_loss_count = 0
```

---

# 54. README-TEST Phase 10 Section

Include:

- candidate identity strategy
- source registry mode
- market config mode
- historical import fixture mode
- temporary DB mode
- source mock/live labeling
- candidate count
- observation count
- dedup diagnostics
- overmerge diagnostics
- fake job diagnostics
- historical outreach guard diagnostics
- watchlist promotion diagnostics
- Phase 5 invariant result
- Phase 8 grounding bridge result
- Phase 9 suppression/send-history guard result
- packaging security result
- Expected
- Actual
- Differences
- Result

No secrets.

---

# 55. QA Meta-Tests

Extend QA integrity:

## P10-QA-001 — Unmapped deterministic case cannot PASS


## P10-QA-002 — Empty pass test cannot count as coverage


## P10-QA-003 — Failed pytest node -> FAIL


## P10-QA-004 — Skipped optional/live source -> NOT_RUN


## P10-QA-005 — Expected and Actual independent


## P10-QA-006 — false_pass_count dynamic


## P10-QA-007 — Mock source success labeled mock


## P10-QA-008 — Fake job count computed, not hardcoded


## P10-QA-009 — Duplicate-company metric computed


## P10-QA-010 — Phase 5 regression metric computed

---

# 56. Mandatory Critical Regressions

All MUST PASS before Phase 10 freeze:

```text
P10-REG-001 same company across sources merges once
P10-REG-002 same name different trusted domains remain separate
P10-REG-003 legal form alone never determines tier
P10-REG-004 no BIM mention does not imply no BIM capability
P10-REG-005 directory/project/tender never becomes fake StructuredJob
P10-REG-006 historical contacted company cannot restart cold outreach
P10-REG-007 historical import is idempotent
P10-REG-008 good fit + weak intent routes to watchlist
P10-REG-009 new evidence can promote watchlist candidate
P10-REG-010 suppression prevents outreach qualification
P10-REG-011 frozen Phase 5 score remains unchanged
P10-REG-012 Phase 8 receives only grounded evidence
P10-REG-013 one source failure preserves successful sources
P10-REG-014 repeated daily run does not duplicate companies
P10-REG-015 runtime DB never enters release ZIP
```

---

# 57. Critical FAIL Conditions

Immediate FAIL if:

- same company becomes multiple logical companies solely due to source count
- different companies merge solely because names match
- S.r.l./S.p.A./GmbH/Ltd/LLC alone determines Tier
- no BIM text becomes a claim of no BIM capability
- directory/project/tender creates fake StructuredJob
- historical import duplicates outreach history
- previously contacted company enters fresh cold sequence automatically
- suppressed company/contact reaches outreach qualification
- watchlist alone generates outreach
- Phase 5 frozen score changes
- Phase 8 email receives speculative internal assessment as factual evidence
- Phase 10 bypasses Phase 9 approval/send/suppression
- one source failure deletes successful candidate results
- runtime database enters release package
- source credentials appear in reports/evidence
- Phase 10 performs real email send
- report marks unmapped case PASS
- false_pass_count is hardcoded

---

# 58. Suggested Coverage

Target approximately:

```text
Identity / dedup:              12
Observations / evidence:       13
Source / market config:        14
Tier / BIM maturity:           12
Fit / intent / opportunity:    14
Historical import:             22
No-fake-job / compatibility:   12
Company/project discovery:     16
Research / assessments:        18
Qualification / watchlist:     15
Phase 6/7/8/9 bridges:         22
Discovery runs / fallback:     18
Security / DB / packaging:     20
LLM / no-send:                 11
Operator-console bridge:        5
QA integrity:                  10
```

Do not inflate counts for appearance.

Each PASS must correspond to a real invariant.

---

# 59. Definition of Phase 10 QA Complete

Phase 10 may freeze only when:

1. Existing frozen Phase 1–9 behavior remains intact.
2. Same company across multiple sources becomes one CandidateCompany.
3. Similar names do not cause unsafe overmerge.
4. Source provenance is preserved.
5. Tier is evidence-based and legal-form-neutral.
6. BIM maturity supports unknown safely.
7. Fit and intent remain separate.
8. Historical XLSX/CSV import works.
9. Historical import is idempotent.
10. Multi-email historical cells normalize safely.
11. Historical emails are not auto-verified.
12. Previous outreach prevents accidental cold restart.
13. Positive historical replies are preserved as outcomes.
14. No statistical optimization is inferred from tiny samples.
15. Directory/project/tender evidence never becomes fake jobs.
16. Frozen Phase 5 score remains exact.
17. Company-first discovery works without jobs.
18. Project/tender-first discovery works.
19. Watchlist persists good-fit/weak-intent companies.
20. New evidence can promote watchlist candidates.
21. Suppression remains authoritative.
22. Qualified non-job candidates can reach Phase 6 safely.
23. Phase 7 receives additive fields only.
24. Phase 8 receives grounded evidence only.
25. Phase 9 history/approval/send state cannot be bypassed.
26. One source failure does not destroy other results.
27. Repeated daily discovery does not duplicate companies.
28. Runtime DB/secrets stay out of release ZIP.
29. Phase 10 performs zero outbound sends.
30. Mandatory P10-REG-001..015 all PASS.
31. Every deterministic Golden case maps to actual execution.
32. false_pass_count = 0.
33. unmapped_deterministic_cases = 0.
