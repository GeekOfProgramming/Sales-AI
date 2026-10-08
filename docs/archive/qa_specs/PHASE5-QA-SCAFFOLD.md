# SalesAI Phase 5 QA Scaffold — Lead Aggregation & Scoring

## Purpose

Validate Phase 5 independently from Phase 4 LLM quality.

Phase 5 input must use frozen StructuredJob fixtures.

Do NOT feed live Phase 4 output into the Golden test.

The QA must isolate these subsystems:

1. Company identity resolution
2. Job deduplication
3. Job relevance gating
4. Signal aggregation
5. Recency calculation
6. Evidence aggregation
7. Deterministic score calculation
8. Qualification threshold
9. Explainability / score breakdown

---

# Files to Create

```text
tests/golden/
├── phase5_leads.json
└── snapshots/
    └── leads/
        ├── p5_strong_lead_jobs.json
        ├── p5_weak_lead_jobs.json
        ├── p5_identity_collision_jobs.json
        ├── p5_duplicate_url_jobs.json
        ├── p5_recency_jobs.json
        └── p5_irrelevant_jobs.json

tests/acceptance/
└── test_phase5_acceptance.py

tests/reports/
└── phase5_latest.json
```

README-TEST.md must include Phase 5 results.

---

# P5-LEAD-001 — Strong Qualified Lead

Use a frozen set of jobs for one company.

Example company:

Acme Engineering

Jobs:
- BIM Manager
- Revit API Developer
- Digital Delivery Manager

Signals:
- Revit
- Python
- C#
- ISO 19650
- BIM Automation

Expected:
- exactly 1 CompanyLead
- correct company identity
- jobs grouped correctly
- relevant_job_count = 3
- qualified = true
- score breakdown is deterministic
- total_score equals sum of components
- reasons/evidence are preserved

Do NOT use a loose expected score range like 50–100.

Expected should define either:
- exact score, OR
- exact component scores with total

Example:

```json
{
  "fit_score": 26,
  "intent_score": 27,
  "recency_score": 18,
  "evidence_score": 17,
  "total_score": 88,
  "qualified": true
}
```

If scoring rules later change intentionally,
version the Golden fixture.

---

# P5-LEAD-002 — Weak / Unqualified Lead

Input:
one generic AEC role with weak relevance.

Example:
Project Administrator

Technologies:
Microsoft Office

Expected:
- lead may exist
- qualified = false
- score below MIN_QUALIFIED_SCORE
- no invented BIM intent
- no technology-only inflation

---

# P5-ID-001 — Same Name, Different Domains

Input jobs:

ABC Engineering
domain = abc-engineering.com

ABC Engineering
domain = abc-engineering.de

Expected:
2 separate CompanyLead records.

Critical:
same normalized company name must NOT merge when strong domain identities conflict.

---

# P5-ID-002 — Same Domain, Name Variants

Input:

Acme Engineering GmbH
ACME Engineering
Acme Engineering Ltd

All with:
acme.com

Expected:
1 CompanyLead.

Domain identity has priority over normalized name.

---

# P5-ID-003 — Source Key Identity

No domain available.

Jobs share:

source = greenhouse
source_company_key = acme-engineering

Expected:
1 CompanyLead.

Identity priority:

domain
→ source + source_company_key
→ normalized name

---

# P5-ID-004 — Conflicting Source Keys

Same normalized name but:

source_company_key = acme-eu
source_company_key = acme-us

No shared domain.

Expected:
do NOT aggressively merge.

---

# P5-DEDUP-001 — URL Variants

Input URLs:

https://example.com/jobs/bim-manager
https://example.com/jobs/bim-manager/
https://example.com/jobs/bim-manager?utm_source=linkedin
https://example.com/jobs/bim-manager#apply

Expected:
one logical job.

Duplicate URL variants must not inflate:

job_count
relevant_job_count
intent score
evidence score

---

# P5-REL-001 — Technology Alone Is Not Relevance

Input job:

Title:
Office Administrator

Description mentions:
Revit

Expected:
job is NOT automatically highly relevant only because one technology appears.

Technology match may be supporting evidence,
but job-role/business-context relevance must be required.

---

# P5-REL-002 — Relevant Role Without Technology

Input:

BIM Manager

No explicit Revit/Python technology list.

Expected:
role relevance still counts.

Do not require technology match for obvious primary-role relevance.

---

# P5-REL-003 — Irrelevant Leadership

Input:

Chief Marketing Officer
Operations Director

No BIM / digital construction context.

Expected:
do not boost intent merely because role is senior/leadership.

---

# P5-REC-001 — Relevant Recency Only

Input:
Job A:
BIM Manager
posted 120 days ago
relevant = true

Job B:
Sales Manager
posted 2 days ago
relevant = false

Expected:
recency score must be based on relevant buying-intent jobs,
not the newest irrelevant company job.

Critical regression check.

---

# P5-REC-002 — Missing Date

Relevant job has:
posted_date = null

Expected:
controlled/default recency behavior.

Do not fabricate a recent date.

Do not award maximum recency.

---

# P5-REC-003 — Future Date

Job posted_date is in the future.

Expected:
future date is treated as invalid/anomalous.

Do not award maximum recency due to negative age.

Record an anomaly/reason if supported by architecture.

---

# P5-EVID-001 — Grounded Evidence Required

Input contains:

relevant_signals:
- signal: "Hiring BIM Manager"
  evidence: "We are hiring a BIM Manager..."

Expected:
evidence preserved in CompanyLead.

Do not replace with unsupported summaries.

---

# P5-EVID-002 — Signal Without Evidence

Input signal has empty evidence.

Expected:
evidence_score must not receive full credit.

Do not fabricate supporting evidence.

---

# P5-SCORE-001 — Component Bounds

Assert:

Fit: 0–30
Intent: 0–30
Recency: 0–20
Evidence: 0–20

Total:
0–100

Every component must stay within bounds.

---

# P5-SCORE-002 — Total Arithmetic

Always assert:

total_score =
fit_score +
intent_score +
recency_score +
evidence_score

No hidden bonus.

No LLM adjustment.

No post-hoc multiplier unless explicitly part of versioned scoring rules.

---

# P5-SCORE-003 — Determinism

Run the exact same Lead input multiple times.

Expected:
identical score and qualification every time.

No temperature/model dependency.

---

# P5-SCORE-004 — LLM Must Not Set Numeric Score

Mock or inspect code path.

Expected:
LLM output cannot directly write:

fit_score
intent_score
recency_score
evidence_score
total_score

Numeric score must come from deterministic scorer only.

Critical FAIL if violated.

---

# P5-QUAL-001 — Threshold Boundary

If:

MIN_QUALIFIED_SCORE = 60

Test:

score = 59 → qualified = false
score = 60 → qualified = true
score = 61 → qualified = true

Do not use `>` when contract requires `>=`.

---

# P5-AGG-001 — Aggregated Reasons

For qualified lead verify:

- reasons are non-empty
- evidence is traceable
- reasons correspond to actual contributing jobs/signals
- no reason from unrelated/irrelevant job

---

# P5-AGG-002 — Job Count Integrity

Track separately where possible:

total_job_count
unique_job_count
relevant_job_count

Do not conflate them.

---

# P5-AGG-003 — Partial Bad Job

One malformed/low-quality job appears beside valid jobs.

Expected:
valid jobs still aggregate.

Bad job must not crash the entire company lead.

Bad job must not inflate score.

---

# Adversarial Cases

## P5-ADV-001 — Name Collision

"ABC Studio"
"ABC Studio"

Different domains, different cities, different source keys.

Expected:
separate.

---

## P5-ADV-002 — Domain Alias

www.acme.com
acme.com
https://acme.com/about

Expected:
same canonical company identity.

---

## P5-ADV-003 — High Volume Noise

Company has:
20 irrelevant jobs
1 highly relevant BIM Automation Engineer job

Expected:
irrelevant volume must not dominate score.

Scoring should reflect relevant evidence, not raw hiring volume.

---

## P5-ADV-004 — Duplicate Relevant Signal

Same signal/evidence repeated in multiple parser outputs for same job.

Expected:
no evidence-score inflation from exact duplicate evidence.

---

# Golden Metrics

For every Lead case capture:

```text
input_jobs
unique_jobs
companies_resolved
relevant_jobs
fit_score
intent_score
recency_score
evidence_score
total_score
qualified
qualification_threshold
reasons_count
evidence_count
identity_method
```

Optional useful diagnostics:

```text
deduplicated_urls
ignored_jobs
invalid_dates
identity_conflicts
```

---

# README-TEST Output

For each Phase 5 Golden case show:

```markdown
### P5-LEAD-001 — Strong Qualified Lead

Input Jobs:
...

Company Identity:
Method:
Domain:
Source Key:
Normalized Name:

Aggregation:
Total Jobs:
Unique Jobs:
Relevant Jobs:
Ignored Jobs:

Score Breakdown:
Fit: x/30
Intent: x/30
Recency: x/20
Evidence: x/20
Total: x/100

Threshold:
60

Qualified:
true

Reasons:
...

Evidence:
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

Immediately FAIL if:

- conflicting domains merge into one company
- exact same company/domain splits unexpectedly
- duplicate URL variants inflate counts or score
- irrelevant recent job boosts recency
- technology-only irrelevant job creates strong relevance
- irrelevant senior leadership boosts buying intent
- score exceeds component bounds
- total arithmetic is wrong
- LLM directly assigns numeric score
- qualification boundary is wrong
- evidence is fabricated
- malformed job crashes whole company aggregation

---

# Model Policy

Phase 5 numeric scoring and grouping must remain deterministic.

Do not defer these tests because of qwen2.5:1.5b.

The only model-dependent inputs are upstream StructuredJob semantics.

Therefore Golden Phase 5 tests must use frozen StructuredJob fixtures.

This isolates Phase 5 from Phase 4 model quality.

---

# Ground Truth Versioning

Every Phase 5 Golden scoring case should include:

```json
{
  "ground_truth_version": 1,
  "scoring_rules_version": 1,
  "human_approved": true
}
```

If score weights or rules intentionally change:

- increment scoring_rules_version
- update Expected manually
- record change_reason

Never silently rewrite Expected values to match Actual.

---

# Definition of Phase 5 QA Complete

Phase 5 deterministic QA is acceptable when:

1. Identity-resolution tests pass.
2. Deduplication tests pass.
3. Relevance-gating tests pass.
4. Recency tests pass.
5. Evidence tests pass.
6. Component bounds pass.
7. Score arithmetic passes.
8. Score determinism passes.
9. LLM cannot assign numeric score.
10. Threshold-boundary tests pass.
11. Aggregation survives partial bad input.
12. README exposes identity + score breakdown + evidence.
13. No critical unresolved deterministic bug remains.
