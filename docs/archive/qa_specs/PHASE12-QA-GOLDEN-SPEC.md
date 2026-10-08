# SalesAI Phase 12 QA / Golden Acceptance Spec
## Analytics / Learning Loop
### Practical Freeze Gate — No Autonomous Optimization

# 0. Acceptance Philosophy

Phase 12 is analytics and learning intelligence, NOT production authority.

Current local model:

```text
qwen2.5:1.5b
```

is temporary and weak.

Golden QA must be strict on deterministic analytics correctness and zero autonomous
mutation, but semantic/model-quality limitations may be:

```text
REVIEW
DEFERRED_MODEL
```

Practical freeze threshold:

```text
all critical no-mutation / counting / safety gates PASS
overall deterministic verified pass rate >= 60%
0 production-authority mutations
0 analytics-triggered sends
0 secret/runtime DB packaging violations
```

Do not reopen patch loops for cosmetic dashboard issues or weak-model semantics.

---

# 1. Baseline

Frozen/accepted:

```text
Phase 9    — outbound authority
Phase 10   — discovery / qualification
Phase 10.5 — operator console
Phase 11   — inbox / reply intelligence
```

Phase 12 is built, not frozen.

No live email or mailbox sends during Golden QA.

---

# 2. QA Artifacts

Create:

```text
tests/golden/phase12_analytics.json
tests/acceptance/test_phase12_acceptance.py
tests/reports/phase12_latest.json
```

Reuse:

```text
tests/test_phase12_regressions.py
```

Do not inflate case counts with duplicated assertions.

---

# 3. Critical No-Autonomy Gates

All must PASS.

## P12-GOLD-001 — Analytics Never Changes Phase 5 Score

Projection, metrics, recommendation generation and recommendation acceptance must leave
Phase 5 scoring data/config unchanged.

## P12-GOLD-002 — Analytics Never Changes Suppression

Phase 9 suppression state must remain unchanged unless changed by authoritative Phase 9/11
workflow, never by analytics.

## P12-GOLD-003 — Analytics Never Changes Source Configuration

No automatic enable/disable/priority/max-results mutation.

## P12-GOLD-004 — Analytics Never Changes Send Limits

No daily/hourly send-limit mutation.

## P12-GOLD-005 — Recommendation Acceptance Records Decision Only

`Accept recommendation` may update recommendation status/audit only.

It must not execute the proposed operational change.

## P12-GOLD-006 — Model Evaluation Sends Zero Emails

Offline model evaluation must produce:

```text
outbound provider calls = 0
```

## P12-GOLD-007 — Normal Analytics UI Uses Zero LLM Calls

Overview, funnel, source, market, contact, message, reply, opportunity and model-history
rendering must not invoke the LLM.

## P12-GOLD-008 — No Mutating GET

Analytics GET endpoints are read-only.

## P12-GOLD-009 — Operational Stores Remain Source of Truth

Analytics rebuild/projection may read authoritative Phase 9–11 stores but must not write
back into them.

## P12-GOLD-010 — Rebuild Is Read-Only

Rebuilding analytics may delete/reconstruct analytics projections only, never operational
records.

## P12-GOLD-011 — No Autonomous Outreach Optimization

Analytics cannot automatically change targeting, sender, qualification, prompt deployment,
send volume or follow-up behavior.

## P12-GOLD-012 — Release Contains No Runtime DB / Secrets

Denylist must remain clean.

---

# 4. Projection Idempotency

Repeated projection over unchanged operational state must produce:

```text
same analytics event count
0 duplicate logical events
0 double-counted sends/replies
```

Stable event IDs must derive from authoritative source identities.

---

# 5. New Operational State

When one new authoritative operational record is added:

```text
exactly the corresponding new analytics event(s)
```

should appear.

Previously projected events remain unchanged.

---

# 6. Canonical Funnel

Validate stages:

```text
Discovered
Qualified
Contact Selected
Draft Generated
Draft Approved
Sent
Human Reply
Positive Reply
Meeting Proposed
Meeting Scheduled
Qualified Opportunity
Closed Won / Lost
```

Not every company must pass every stage.

No inferred stages.

---

# 7. Correct Denominators

Every rate must expose:

```text
numerator
denominator
rate
sample_size
```

Examples:

```text
qualification_rate = qualified / discovered
draft_approval_rate = approved / generated
send_to_human_reply_rate = human replies / sent
send_to_positive_reply_rate = positive human replies / sent
reply_to_meeting_rate = meeting proposed / human replies
```

No unrelated denominator.

---

# 8. Human Reply Rate

Exclude:

```text
auto_reply
bounce
```

from the human-reply numerator.

Do not exclude legitimate:

```text
not_interested
not_now
question
referral
```

because they are still human replies.

---

# 9. Provenance

Analytics must distinguish:

```text
live_system
historical_import
```

Historical data must not be presented as live-provider telemetry.

---

# 10. Historical Missing Telemetry

For historical data without provider metadata:

```text
unknown / null
```

not fabricated delivery/open/provider state.

---

# 11. Small-Sample Safety

Verify:

```text
MIN_SAMPLE_FOR_COMPARISON = 10
MIN_SAMPLE_FOR_RECOMMENDATION = 20
```

or documented configured equivalents.

Below threshold:

```text
insufficient_sample
```

No labels like:

```text
best
winner
top-performing
recommended because of conversion rate
```

from undersized data.

---

# 12. Historical n=2 Safety

The two known positive historical examples may remain qualitative evidence only.

They must not create a statistical optimization rule.

---

# 13. Relationship Separation

Keep separate:

```text
service_buyer
technology_partner
channel_partner
strategic_partner
mixed
unknown
```

Do not call a technology-partner meeting a direct BIM-service sales conversion.

---

# 14. Opportunity Separation

Keep separate:

```text
direct_bim_delivery
mep_delivery
bim_coordination
overflow_capacity
automation
revit_api
data_parameter_automation
custom_software
technology_partnership
channel_partnership
strategic_collaboration
unknown
```

---

# 15. Source Analytics

For source slices, verify factual counts for:

```text
observations
unique companies
qualified
sent
human replies
positive replies
meetings
opportunities
```

No auto-disable/priority change.

---

# 16. Market Analytics

For market slices verify:

```text
discovered
qualified
sent
human reply rate
positive rate
meeting rate
sample size
```

No automatic market prioritization.

---

# 17. Contact Analytics

Allowed dimensions:

```text
business role
department
seniority
generic vs named business mailbox
email status
```

No protected/sensitive-person inference.

---

# 18. Message Analytics

Allowed metadata:

```text
subject length
body word count
language
tone
active service
evidence count
model
prompt version
```

No chain-of-thought.

No tracking pixels/open tracking/click tracking.

---

# 19. Reply Analytics

Keep distinct:

```text
positive_interest
meeting_proposed
referral
question
not_now
not_interested
unsubscribe
bounce
auto_reply
unknown
```

Human/auto/bounce metrics must remain separable.

---

# 20. Meeting / Opportunity Analytics

No meeting or opportunity state may be invented.

Only authoritative Phase 11/operator state counts.

No revenue forecasting.

---

# 21. Time Windows

Validate:

```text
7 days
30 days
90 days
all time
custom
```

Boundary filtering must be deterministic.

---

# 22. Unknown Values

Missing dimension values remain:

```text
unknown / null
```

Do not silently impute source, market, role, model or opportunity type.

---

# 23. Model Evaluation Registry

Validate persisted:

```text
model_name
model_version
quantization
hardware_label
task
fixture_set_version
cases
pass/review/fail/deferred
latency if measured
tokens/sec if measured
RAM/VRAM if measured
```

Unmeasured benchmark values remain null.

---

# 24. Task-Specific Model Comparison

Keep separate evaluation for:

```text
website_extraction
company_classification
email_draft_generation
reply_classification
reply_suggestion
meeting_time_extraction
```

Do not compress all capabilities into one unsupported global score.

---

# 25. Current Weak-Model Baseline

`qwen2.5:1.5b` baseline should preserve known deferred semantic limitations.

Do not rewrite deferred outcomes as PASS merely to improve model metrics.

---

# 26. Future Model Comparison

The same fixture-set version must support future comparisons such as:

```text
1.5B
14B
22B
32B
```

and hardware labels such as:

```text
current_weak_machine
workstation_4070
future_5080
```

No fabricated speed/VRAM numbers.

---

# 27. Model Evaluation Safety

Offline evaluation:

```text
0 outbound emails
0 mailbox sends
0 production approval changes
0 suppression changes
```

---

# 28. Human Edit Signal

Preserve explicit edit labels:

```text
none
minor
major
rewrite
rejected
```

Do not claim quality solely from character-count difference.

---

# 29. Human Acceptance Metrics

Validate:

```text
draft_acceptance_rate
reply_suggestion_acceptance_rate
unchanged_acceptance_rate
major_edit_rate
rejection_rate
```

Each includes sample size.

---

# 30. LearningRecommendation

Validate fields:

```text
evidence
sample_size
confidence
risk
proposed_change
status
review metadata
```

Allowed statuses:

```text
proposed
accepted
rejected
deferred
implemented_manually
```

---

# 31. Recommendation Evidence

Recommendations must be grounded in computed analytics.

No unsupported recommendation from an LLM summary.

---

# 32. Recommendation Minimum Sample

Recommendation engine must enforce configured minimum sample before performance-based
optimization claims.

Below threshold:

```text
insufficient_sample
```

or no recommendation.

---

# 33. Recommendation Acceptance

Accepting recommendation:

```text
status = accepted
reviewed_by
reviewed_at
```

No operational mutation.

---

# 34. API Surface

Smoke-test:

```text
GET /api/sales/analytics/overview
GET /api/sales/analytics/funnel
GET /api/sales/analytics/sources
GET /api/sales/analytics/markets
GET /api/sales/analytics/contacts
GET /api/sales/analytics/messages
GET /api/sales/analytics/replies
GET /api/sales/analytics/opportunities

GET /api/sales/analytics/models
POST /api/sales/analytics/models/evaluate

GET /api/sales/analytics/recommendations
POST /api/sales/analytics/recommendations/{id}/accept
POST /api/sales/analytics/recommendations/{id}/reject
```

No unsafe mutating GET.

---

# 35. Analytics UI

Smoke-test:

```text
/sales/analytics
```

Sections should include:

```text
Overview
Funnel
Sources
Markets
Contacts
Messages
Replies
Opportunities
Models
Recommendations
```

Empty states are valid.

---

# 36. UI Truthfulness

Low sample must display:

```text
Insufficient sample
```

Unknown values must not be rendered as definitive facts.

---

# 37. Analytics UI LLM Boundary

Normal page rendering:

```text
0 LLM calls
```

Explicit model evaluation is separate.

---

# 38. Export

Test sanitized:

```text
CSV
JSON
```

Optional XLSX if implemented.

No secrets/tokens/runtime paths.

Prefer aggregate data when contact-level details are unnecessary.

---

# 39. Data Quality Diagnostics

Validate truthful counts where implemented:

```text
unknown_market_count
unknown_source_count
unlinked_reply_count
missing_contact_count
missing_model_metadata_count
historical_provenance_count
```

No silent imputation.

---

# 40. Analytics Store Reinitialization

Schema initialization/reinitialization must be safe.

Indexes/tables must not duplicate or corrupt existing analytics records.

---

# 41. Concurrency / Duplicate Insert

Stable event uniqueness must prevent duplicate logical events under repeated insertion.

No need for a distributed analytics system.

---

# 42. Existing Mandatory Regressions

All must remain green:

```text
P12-REG-001 .. P12-REG-020
```

---

# 43. Reporting Integrity

Generate:

```text
tests/reports/phase12_latest.json
```

Required:

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
deferred_model

deterministic_pass_rate

false_pass_count
unmapped_deterministic_cases

critical_safety_total
critical_safety_passed
critical_safety_failed

duplicate_analytics_event_count
double_count_violation_count
denominator_violation_count
small_sample_claim_violation_count

phase5_mutation_count
suppression_mutation_count
source_config_mutation_count
send_limit_mutation_count

analytics_llm_render_call_count
model_evaluation_send_count

secret_exposure_count
runtime_db_packaged_count
```

Unmeasured counters should be:

```text
null / NOT_RUN
```

not decorative zeros.

Catalog presence alone is not execution evidence.

---

# 44. Weak-Model Deferred Items

Semantic quality for `qwen2.5:1.5b` may be:

```text
DEFERRED_MODEL
```

Examples:

```text
fine-grained reply intent
high-quality outbound wording
multilingual phrasing
complex extraction nuance
```

Do not let these block Phase 12 if deterministic analytics architecture is correct.

---

# 45. Full Repository Regression

Run:

```text
python -m pytest tests/ -q
```

Require:

```text
0 new deterministic failures
```

No live email/mailbox sending.

---

# 46. Release Packaging

ZIP denylist:

```text
.env
.env.*
*.db
*.sqlite
*.sqlite3
OAuth tokens
credentials
client secrets
private keys
runtime logs
Chroma runtime DB
.venv
__pycache__
*.pyc
```

Sanitized test fixtures allowed.

---

# 47. Practical Freeze Rule

Phase 12 may freeze when:

1. P12-GOLD-001..012 all PASS.
2. P12-REG-001..020 remain green.
3. Projection is idempotent.
4. No double counting is known.
5. Funnel denominators are correct.
6. Human replies exclude auto-replies/bounces correctly.
7. Low-sample claims are blocked.
8. Buyer/partner and opportunity categories remain separate.
9. No analytics path mutates Phase 5 / suppression / source config / send limits.
10. Recommendation acceptance is audit-only.
11. Model evaluation sends zero emails.
12. Normal analytics UI uses zero LLM calls.
13. Full repository has no new deterministic failure.
14. ZIP denylist is clean.
15. Deterministic verified pass rate >= 60%.
16. Weak-model limitations are documented rather than endlessly patched.

---

# 48. Non-Blocking Deferred Items

May be deferred:

```text
stronger-model evaluation
real 14B/22B/32B benchmarks
future 5080 hardware benchmarks
advanced statistics
Wilson/Bayesian intervals
complex experiments
CRM revenue integration
visual dashboard polish
tracking/open/click analytics
automatic optimization
```

---

# 49. Final QA Output

Return:

```text
total cases
mapped cases
executed cases
PASS
FAIL
REVIEW
NOT_RUN
DEFERRED_MODEL

deterministic pass rate

critical safety total/pass/fail

P12-GOLD-001..012
P12-REG-001..020

existing Phase 12 suite result
full repository result

known residual risks
weak-model deferred items

git commit
README-TEST commit
phase12_latest commit

ZIP file count
denylist result
final SalesAI_export.zip
```

If accepted:

```text
PHASE 12 FROZEN
```

Then the current SalesAI architecture is feature-complete through:

```text
Discovery
Qualification
Contacting
Drafting
Human Review
Sending
Inbox / Reply Intelligence
Analytics / Learning Loop
```

Future work should be treated as a new roadmap/version rather than silently altering
frozen Phase 1–12 behavior.
