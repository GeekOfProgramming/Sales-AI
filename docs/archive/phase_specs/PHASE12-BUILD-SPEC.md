# SalesAI Phase 12 Build Specification
## Analytics / Learning Loop
### Deterministic Performance Intelligence Without Autonomous Optimization

# 0. Baseline

Frozen / accepted baseline:

```text
Phase 9   — review / approval / suppression / outbound send authority
Phase 10  — multi-source discovery / company qualification
Phase 10.5 — local operator console
Phase 11  — inbox / reply intelligence / human-reviewed replies
```

Phase 12 adds analytics, attribution, evaluation and human-reviewed learning.

Do NOT redesign or weaken Phase 1–11 contracts.

Current weak local LLM:

```text
qwen2.5:1.5b
```

must be treated as a temporary model.

Phase 12 should make it possible to compare future stronger models objectively without
changing production behavior automatically.

---

# 1. Primary Goal

Build an analytics layer that answers:

```text
Where did companies come from?
Which companies were qualified?
Which contacts were selected?
Which drafts were approved?
Which emails were actually sent?
Which messages received replies?
What type of reply was received?
Which conversations reached meetings/opportunities?
Which source / market / opportunity / contact / model patterns appear promising?
```

The learning loop must remain:

```text
observe
→ measure
→ compare
→ generate recommendation
→ human review
→ optional explicit configuration change
```

Never:

```text
measure
→ automatically change scoring
→ automatically change targeting
→ automatically send more email
```

---

# 2. Core Principle — Analytics Is Not Authority

Phase 12 may:

```text
aggregate
compare
rank evidence-backed historical performance
generate recommendations
evaluate model versions
```

Phase 12 may NOT directly modify:

```text
Phase 5 scoring weights
Phase 9 suppression
Phase 9 send state
Phase 10 qualification
Phase 11 conversation state
source enable/disable state
market targeting
email send volume
```

Any future change must be explicit, versioned and human-approved.

---

# 3. Canonical Outcome Funnel

Define the canonical funnel:

```text
CandidateCompany
→ Qualified Candidate
→ Contact Selected
→ Draft Generated
→ Draft Approved
→ Sent
→ Delivered / Failed / Bounced
→ Reply
→ Positive / Negative / Other
→ Meeting Proposed
→ Meeting Scheduled
→ Qualified Opportunity
→ Closed Won / Closed Lost
```

Not every record must pass every stage.

Preserve actual observed state.

---

# 4. AnalyticsEvent

Create a normalized append-only analytics event layer.

Recommended:

```python
AnalyticsEvent
```

Fields:

```text
event_id
event_type
occurred_at

company_id
candidate_id
lead_id
contact_id

draft_id
draft_revision
send_attempt_id

conversation_id
inbound_message_id
reply_suggestion_id

source_id
source_type
market_id

relationship_type
opportunity_types[]

company_tier
bim_maturity
fit_level
intent_level

sender_email
recipient_domain

model_name
model_version
prompt_version

metadata_json
source_ref

created_at
```

No secrets.

---

# 5. Event Types

Support at least:

```text
candidate_discovered
candidate_qualified
candidate_watchlisted
candidate_rejected

contact_selected

draft_generated
draft_approved
draft_rejected
draft_changed

email_sent
email_failed
email_bounced

reply_received
reply_classified

positive_interest
meeting_proposed
meeting_scheduled

opportunity_qualified
opportunity_closed_won
opportunity_closed_lost

suppressed
unsubscribed
```

Events should be generated from authoritative Phase 9–11 state.

Do not fabricate events.

---

# 6. Source of Truth

Analytics must derive from authoritative data stores.

Priority:

```text
Phase 9 send/review/audit state
Phase 10 candidate/discovery evidence
Phase 11 inbound/conversation state
historical imported outcomes
```

Analytics tables are projections.

They must not become the operational source of truth.

---

# 7. Idempotent Event Projection

Re-running analytics projection must not duplicate events.

Use stable event identity based on authoritative record IDs.

Examples:

```text
send_attempt_id
inbound_message_id
conversation event ID
draft ID + revision + event type
candidate ID + qualification event identity
```

---

# 8. Historical Data Compatibility

Historical Phase 10 imports should contribute where evidence exists.

Preserve:

```text
historical source
historical contact
historical sent state
historical reply state
historical positive outcome
historical meeting state
```

Mark analytics provenance:

```text
live_system
historical_import
```

Do not pretend historical records have modern provider telemetry if they do not.

---

# 9. Funnel Metrics

Compute deterministic counts:

```text
candidates discovered
qualified candidates
contacts selected
drafts generated
drafts approved
emails sent
replies received
positive replies
meeting proposals
meetings scheduled
qualified opportunities
closed won
closed lost
```

Also compute valid denominators.

Never divide by an unrelated population.

---

# 10. Conversion Metrics

Examples:

```text
qualification_rate
draft_approval_rate
send_to_reply_rate
send_to_positive_reply_rate
reply_to_meeting_proposed_rate
meeting_proposed_to_scheduled_rate
send_to_meeting_rate
```

Each metric must expose:

```text
numerator
denominator
rate
sample_size
```

No isolated percentage without sample size.

---

# 11. Small-Sample Safety

Critical requirement.

Do not make strong optimization claims from tiny samples.

Config:

```text
MIN_SAMPLE_FOR_COMPARISON
MIN_SAMPLE_FOR_RECOMMENDATION
```

Recommended default:

```text
MIN_SAMPLE_FOR_COMPARISON = 10
MIN_SAMPLE_FOR_RECOMMENDATION = 20
```

If below minimum:

```text
insufficient_sample
```

not:

```text
best source
best market
best subject
```

Historical n=2 positive examples remain qualitative only.

---

# 12. Statistical Safety

Phase 12 may compute descriptive statistics.

Optional:

```text
Wilson confidence interval
Bayesian credible interval
```

for proportions.

Do not require advanced statistics for build completion.

Do NOT claim causal impact from observational data.

Use language such as:

```text
observed higher reply rate
```

not:

```text
this source causes more replies
```

---

# 13. Dimensions

Support analytics by:

```text
market / country
source
source type
discovery mode
company tier
BIM maturity
fit level
intent level
relationship type
opportunity type
contact role
contact seniority
sender
language
tone
model
prompt version
service
time period
```

Only use dimensions actually present.

Missing values remain:

```text
unknown
```

---

# 14. Opportunity Type Analytics

Measure separately:

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

Do not mix direct service buyers and strategic partners into one success definition.

---

# 15. Relationship-Type Analytics

Compare separately:

```text
service_buyer
technology_partner
channel_partner
strategic_partner
mixed
unknown
```

A meeting with a technology partner is not the same outcome as a direct BIM service sale.

---

# 16. Source Analytics

For each source:

```text
observations
unique companies
qualified companies
sent emails
replies
positive replies
meetings
opportunities
errors
```

Also:

```text
source health
credential state
last success
```

Do not auto-disable a source based on analytics.

---

# 17. Market Analytics

For each market:

```text
companies discovered
qualification rate
sent volume
reply rate
positive reply rate
meeting rate
sample size
```

No automatic country prioritization.

---

# 18. Contact Analytics

Analyze business contact patterns such as:

```text
role
department
seniority
generic vs named work mailbox
verified vs likely
```

Never use sensitive personal attributes.

Do not infer protected traits.

---

# 19. Message Analytics

Track safe message metadata:

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

Do not store chain-of-thought.

Do not create tracking pixels.

No open/click tracking in Phase 12.

---

# 20. Reply Analytics

Track:

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

Separate:

```text
human replies
auto replies
bounces
```

when computing reply performance.

---

# 21. Meeting Analytics

Track:

```text
meeting_proposed
meeting_scheduled
meeting_completed
meeting_cancelled
```

No invented meeting state.

No calendar integration required.

---

# 22. Opportunity Analytics

Track:

```text
active_conversation
meeting
qualified_opportunity
closed_won
closed_lost
```

No automatic monetary forecasting.

Revenue fields may be added later only from explicit CRM/user data.

---

# 23. Time Windows

Support:

```text
last 7 days
last 30 days
last 90 days
all time
custom date range
```

Use occurred_at in a consistent timezone-aware format.

---

# 24. Cohort Analysis

Support simple cohorts:

```text
discovered month
sent month
market
source
opportunity type
relationship type
model version
```

No complex predictive cohorting required.

---

# 25. Model Evaluation Registry

Create:

```python
ModelEvaluationRun
```

Fields:

```text
evaluation_id
model_name
model_version
quantization
hardware_label

task
fixture_set_version

cases
passed
review
failed
deferred

latency_ms
tokens_per_second_optional
peak_ram_optional
peak_vram_optional

created_at
notes
```

This prepares migration from:

```text
qwen2.5:1.5b
```

to future stronger models.

---

# 26. Model Comparison Tasks

Evaluate models separately on:

```text
website profile extraction
company classification
email draft generation
reply classification
reply suggestion
meeting-time extraction
```

Do not use one score for all LLM capabilities.

---

# 27. Model Quality Metrics

Examples:

```text
schema validity
grounding validity
fabrication count
classification agreement
human accept rate
human edit distance / edit level
semantic review outcome
latency
```

Weak-model semantic limitations remain visible.

---

# 28. Human Edit Signal

Measure whether generated drafts/replies are:

```text
approved unchanged
approved after minor edit
approved after major edit
rejected
```

Recommended enum:

```text
none
minor
major
rewrite
rejected
```

Do not infer edit quality from character count alone unless explicitly documented.

---

# 29. Human Acceptance Metrics

Track:

```text
draft_acceptance_rate
reply_suggestion_acceptance_rate
unchanged_acceptance_rate
major_edit_rate
rejection_rate
```

Always display sample size.

---

# 30. LearningRecommendation

Create:

```python
LearningRecommendation
```

Fields:

```text
recommendation_id
created_at

category
scope
summary
evidence

sample_size
confidence_level

proposed_change
risk

status
reviewed_by
reviewed_at
```

Allowed status:

```text
proposed
accepted
rejected
deferred
implemented_manually
```

---

# 31. Recommendation Types

May propose:

```text
increase research on source X
review low-performing source Y
test a different contact role
test language/tone variant
upgrade model
review prompt version
research a market more deeply
review opportunity-type targeting
```

Must NOT automatically execute changes.

---

# 32. No Automatic Scoring Changes

Critical rule:

Phase 12 cannot modify Phase 5 numeric scoring.

Any recommendation to change a score must be:

```text
proposal only
```

and belongs to a future explicit scoring-version phase.

---

# 33. No Automatic Source Changes

Phase 12 cannot automatically:

```text
disable source
increase source limit
decrease source limit
change source priority
```

It may propose these for operator review.

---

# 34. No Automatic Outreach Changes

Phase 12 cannot automatically:

```text
send more emails
change sender
change daily send limit
change qualification
change suppression
re-engage contacts
```

---

# 35. Versioned Experiments

Support manually configured experiments.

Create:

```python
Experiment
ExperimentVariant
ExperimentAssignment
```

Optional but recommended.

Examples:

```text
tone A vs tone B
subject style A vs B
model 1.5B vs 14B
prompt version A vs B
```

Assignment must be deterministic where possible.

No experiment may bypass safety.

---

# 36. Experiment Safety

Never experiment on:

```text
suppression rules
sender trust
approval requirements
send confirmation
unsubscribe handling
bounce handling
```

Safety invariants are not A/B variables.

---

# 37. Offline Model Evaluation

Phase 12 should provide a mode to evaluate a stronger future model on sanitized fixtures
without changing production.

Example:

```text
python -m sales_engine.analytics.evaluate_model \
  --model <model> \
  --fixture-set phase12-v1
```

No email sends.

---

# 38. Hardware Evaluation

Allow operator to label hardware:

```text
current_weak_machine
workstation_4070
future_5080
```

Store measured:

```text
model load success
latency
tokens/sec where available
RAM usage
VRAM usage
```

This enables evidence-based model selection later.

Do not fabricate benchmark numbers.

---

# 39. Current Weak Model Baseline

Create an evaluation baseline for:

```text
qwen2.5:1.5b
```

Record:

```text
deterministic schema reliability
semantic deferred cases
latency if measured
hardware label
```

Do not attempt to make it look stronger than it is.

---

# 40. Analytics Store

Recommended additive tables:

```text
analytics_events
analytics_projection_state

model_evaluation_runs
model_evaluation_cases

learning_recommendations

experiments
experiment_variants
experiment_assignments
```

Use additive SQLite migrations.

---

# 41. Projection State

Persist:

```text
last_projected_event
last_projection_at
projection_version
```

Repeated projection must be idempotent.

---

# 42. Rebuild Analytics

Provide safe command:

```text
rebuild analytics projection
```

It may reconstruct analytics from operational source-of-truth tables.

It must not mutate operational records.

---

# 43. Analytics API

Add:

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

No mutating GET.

---

# 44. Operator Console

Add:

```text
/sales/analytics
```

Recommended tabs/sections:

```text
Overview
Funnel
Sources
Markets
Contacts
Messages
Replies
Meetings / Opportunities
Models
Recommendations
```

---

# 45. Analytics Overview

Show factual counts:

```text
companies discovered
qualified
sent
human replies
positive replies
meetings proposed
meetings scheduled
opportunities
```

Also show date range.

No fabricated "pipeline value".

---

# 46. Funnel View

Show stage counts and rates.

Every rate displays:

```text
numerator / denominator
sample size
```

---

# 47. Source / Market Tables

Support sorting/filtering.

Display:

```text
sample size
reply rate
positive rate
meeting rate
```

If sample is below threshold:

```text
Insufficient sample
```

---

# 48. Model View

Show model evaluation comparisons:

```text
model
hardware
task
cases
schema pass
grounding pass
human accept
deferred
latency
```

Do not compare unmeasured values.

---

# 49. Recommendation View

Display recommendations with:

```text
evidence
sample size
risk
confidence
proposed change
```

Actions:

```text
Accept recommendation
Reject
Defer
```

"Accept" does not automatically change frozen system configuration unless an explicit
future configuration workflow is separately defined.

For Phase 12:

```text
Accept = record human decision
```

---

# 50. Data Export

Support export of sanitized analytics:

```text
CSV
JSON
optional XLSX through existing export stack
```

No secrets.

Avoid personal data where aggregate data is sufficient.

---

# 51. Privacy

Analytics should prefer aggregate business metrics.

Do not analyze or expose sensitive personal attributes.

Contact-level analytics should use business role/seniority only.

---

# 52. Deletion / Suppression Compatibility

Suppression does not erase historical aggregate facts needed for audit.

Do not surface suppressed contact details unnecessarily in analytics.

---

# 53. Performance

Analytics queries must use aggregation/indexes.

Do not load all message bodies for overview dashboards.

Recommended indexes:

```text
event_type
occurred_at
company_id
source_id
market_id
model_name
conversation_id
```

---

# 54. Caching

Optional short-lived local analytics cache is allowed.

Operational state remains authoritative.

No cache may alter send decisions.

---

# 55. Data Quality Diagnostics

Track:

```text
unknown_market_count
unknown_source_count
unlinked_reply_count
missing_contact_count
missing_model_metadata_count
historical_provenance_count
```

Data-quality warnings are useful.

Do not silently impute missing values.

---

# 56. Phase 12 Critical Regressions

Mandatory:

```text
P12-REG-001 repeated analytics projection is idempotent
P12-REG-002 one send event is counted once
P12-REG-003 one inbound reply is counted once
P12-REG-004 historical and live provenance remain distinguishable
P12-REG-005 auto-replies/bounces are excluded from human reply rate
P12-REG-006 denominators are correct
P12-REG-007 low sample never labeled "best"
P12-REG-008 direct buyer and strategic partner outcomes remain separate
P12-REG-009 Phase 5 score is never modified by analytics
P12-REG-010 suppression is never modified by analytics
P12-REG-011 source config is never auto-modified
P12-REG-012 send limits are never auto-modified
P12-REG-013 recommendation acceptance records decision only
P12-REG-014 model evaluation performs zero outbound sends
P12-REG-015 model comparison preserves fixture version
P12-REG-016 weak model deferred results remain visible
P12-REG-017 missing metrics remain null/unknown, not fabricated
P12-REG-018 analytics API has no unsafe mutating GET
P12-REG-019 analytics UI renders with zero LLM calls
P12-REG-020 release ZIP excludes runtime DB/secrets
```

---

# 57. No LLM Requirement for Normal Analytics

Normal analytics:

```text
funnel
rates
counts
tables
charts
filters
```

must require:

```text
0 LLM calls
```

LLM is only allowed for explicit:

```text
model evaluation
recommendation drafting if implemented
```

Even recommendations can be deterministic in Phase 12.

---

# 58. Recommendation Generation

Preferred default:

```text
deterministic rules
```

Examples:

```text
if sample < minimum -> insufficient sample
if rejection rate high with adequate n -> suggest review
if model has high fabrication count -> suggest model/prompt review
```

LLM may summarize evidence but cannot generate unsupported recommendations.

---

# 59. Reporting

Create:

```text
tests/reports/phase12_latest.json
```

Fields:

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

false_pass_count
unmapped_deterministic_cases

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

Execution-derived where feasible.

---

# 60. Full Regression

Run all frozen Phase 1–11 tests after implementation.

Requirement:

```text
0 new deterministic failures
```

No live mailbox/email sends.

---

# 61. Release Packaging

Exclude:

```text
.env
.env.*
*.db
*.sqlite
*.sqlite3
tokens
OAuth credentials
private keys
runtime logs
Chroma runtime DB
.venv
__pycache__
*.pyc
```

Sanitized analytics fixtures allowed.

---

# 62. Explicit Non-Goals

Do NOT implement in Phase 12:

```text
automatic score retraining
automatic source priority changes
automatic target-market changes
automatic sender changes
automatic send-limit changes
automatic prompt deployment
automatic follow-up campaigns
deal/revenue forecasting
tracking pixels
click tracking
autonomous CRM actions
```

---

# 63. Build Completion Gate

Phase 12 Build is ready for QA when:

1. AnalyticsEvent projection exists.
2. Projection is idempotent.
3. Funnel metrics have correct denominators.
4. Source/market/contact/message/reply analytics exist.
5. Relationship/opportunity types remain separate.
6. Historical/live provenance remains distinct.
7. Small-sample safety exists.
8. ModelEvaluationRun exists.
9. qwen2.5:1.5b baseline can be recorded.
10. Future model/hardware evaluation can run offline.
11. LearningRecommendation exists.
12. Recommendations are human-reviewed.
13. No analytics recommendation auto-modifies frozen configuration.
14. Analytics UI/API exists.
15. Normal analytics uses zero LLM calls.
16. Phase 5 score remains untouched.
17. Phase 9 suppression/send authority remains untouched.
18. Phase 10/11 operational state remains authoritative.
19. Full repository regression stays green.
20. Release ZIP is clean.

After implementation:

```text
DO NOT declare Phase 12 frozen.
```

Return the ZIP for independent verification before Phase 12 Golden QA.
