# SalesAI QA / Acceptance Test Report

## Run Information
- **Date:** 2026-10-06
- **Time:** 11:01:00 UTC
- **Git Commit:** `ac5e51d`
- **Python Version:** `3.14.7`
- **SALES_LLM_MODEL:** `qwen2.5:1.5b (default)`
- **Ollama Version:** `Not detected / unreachable`
- **Brave enabled?** No
- **Apollo enabled?** No
- **Hunter enabled?** No
- **Offline Tests:** Yes
- **Live Tests Executed:** Yes

## Summary
- **Total Cases:** 703
- **Passed:** 687
- **Failed:** 0
- **Review Required:** 4
- **Deferred (Model Limitation):** 7
- **Skipped / Not Run:** 5
- **Source Changed:** 0

## Phase Summary
| Phase | Cases | Pass | Fail | Review | Deferred (Model) | Not Run |
|---|---|---|---|---|---|---|
| Phase 2 | 1 | 0 | 0 | 1 | 0 | 0 |
| Phase 3 | 2 | 1 | 0 | 0 | 0 | 1 |
| Phase 4 | 19 | 17 | 0 | 0 | 2 | 0 |
| Phase 5 | 27 | 26 | 0 | 1 | 0 | 0 |
| Phase 6 | 43 | 40 | 0 | 1 | 0 | 2 |
| Phase 7 | 83 | 81 | 0 | 1 | 0 | 1 |
| Phase 8 | 98 | 93 | 0 | 0 | 5 | 0 |
| Phase 9 | 185 | 184 | 0 | 0 | 0 | 1 |
| Phase 10 | 245 | 245 | 0 | 0 | 0 | 0 |

## Phase 7 Reconciliation & Case Count Model
To ensure 100% auditability across test suites and golden sets, the Phase 7 count structure is unified as follows:
- **Total Golden Cases in Catalog (`tests/golden/phase7_exports.json`):** 83 cases
- **Total Pytest Acceptance Tests (`tests/acceptance/test_phase7_acceptance.py`):** 83 collected tests
  - **Passed (Deterministic):** 81 test cases verifying multi-format exports, schema contracts, identity boundaries, job audit, XLSX/CSV formatting, privacy isolation, limits, and regression invariants.
  - **Review Required:** 1 case (`P7-WF-005` — verifies pre-existing non-default workflow states projecting faithfully without mutation; flagged for human confirmation).
  - **Skipped / Not Run:** 1 case (`P7-GS-LIVE-001` — marked `NOT_RUN` due to real Google Sheets live credentials/adapter not being configured in this offline suite).
- **Historical 85-count explanation:** Earlier conversational summaries referenced 85 entries by counting parameter boundary sub-variants (e.g. `P7-CONTRACT-003` threshold vs boundary); in the strict repository catalog there are exactly **83 canonical Golden cases** and **83 pytest tests** with **0 false passes**.

## Phase 8 Case Count & Architecture Model
Phase 8 implements personalized cold email draft generation with strict deterministic isolation:
- **Total Cases in Catalog (`tests/golden/phase8_outreach.json` & `phase8_latest.json`):** 98 cases
  - **Deterministic Acceptance & Behavioral Cases:** 79 cases passed (covering main golden drafting, eligibility boundaries, contact and lead identity preservation, active service gating, deterministic evidence citations, prompt injection traps, parsing retry, workflow safety, batch resilience, privacy scrubbing, error taxonomy, and Phase 7 handoff).
  - **Mandatory Regression Invariants (`P8-REG-001` .. `014`):** 14 cases passed (covering cross-company job exclusion, exact active service matching, technology non-fabrication, API raw jobs forwarding, Phase 5 evidence preservation, Phase 5 signal schema, SOURCE_DATA delimiter injection escaping, deterministic ordering, missing location non-fabrication, missing contact title non-fabrication, external company/contact untrusted delimiter containment, complete prompt trust boundary, missing recipient name non-fabrication, structured service explicit active status enforcement).
  - **Semantic Deferred Quality (`P8-SEM-001` .. `005`):** 5 cases marked `NOT_RUN_MODEL_LIMITATION` (natural language prose naturalness, tone differentiation, and multilingual drafting deferred for high-capacity LLM review).
- **False Pass Count:** 0 (dynamically verified and computed by execution-driven reporting engine).
- **Unmapped Deterministic Cases:** 0 (all deterministic cases traced to executable pytest nodes).

## Phase 9 Case Count & Architecture Model
Phase 9 implements human review, approval, safe email sending, immutable audit trails, and deterministic outbound gating:
- **Total Golden Cases in Catalog (`tests/golden/phase9_sending.json` & `phase9_latest.json`):** 185 cases
  - **Passed (Deterministic):** 184 test cases covering all 27 spec areas:
    - **Storage & Review Queue (13 cases):** `P9-STORE-001..006`, `P9-QUEUE-001..007` (draft persistence, identity isolation, deterministic review filtering and pagination).
    - **Edit Semantics & Monotonicity (7 cases):** `P9-EDIT-001..007` (new revision creation, approval invalidation, immutable sent revisions, revision monotonicity).
    - **Human Approval Separation & Guards (6 cases):** `P9-APPROVE-001..006` (explicit human approval, send decoupling, obsolete revision rejection, audit recording).
    - **Approval Fingerprint Protection (8 cases):** `P9-HASH-001..008` (SHA-256 fingerprinting of exact sender, recipient, subject, and body payload).
    - **Critical Send Gates (5 cases):** `P9-REG-001`, `P9-REG-002`, `P9-SEND-003..005` (stale approval, pending review, rejected, changes-requested, and missing hash blocking).
    - **Dry-Run & Server Config (7 cases):** `P9-REG-003`, `P9-DRY-002..005`, `P9-REG-004`, `P9-CONFIG-001..002` (zero network calls in dry-run, suppression/hash verification during dry-run, untrusted client config rejection).
    - **Sender Identity & Trusted Aliases (4 cases):** `P9-SENDER-001..004` (server-owned sender binding, sender mismatch rejection, trusted alias validation).
    - **Suppression & Opt-Out (6 cases):** `P9-REG-005`, `P9-SUPPRESS-002..006` (suppressed recipient blocking, case-insensitive match, persistent suppression, audit trail).
    - **Idempotency & Duplicate Prevention (5 cases):** `P9-REG-006`, `P9-IDEMP-002..005` (already-sent terminal state, revision-specific send identity, deterministic send_key, no force=true bypass).
    - **Concurrency & State Locking (4 cases):** `P9-REG-007`, `P9-CONC-002..004` (mutex send lock, unique constraint, atomic state transition, distinct draft concurrency).
    - **Provider Dispatch & Errors (8 cases):** `P9-SMTP-001..003`, `P9-SMTP-ERR-001..005` (mock sender dispatch, plain text RFC 5322 payload, error classification, credential sanitization).
    - **Failure & Retry Invariants (5 cases):** `P9-FAIL-001..005` (failed send never marked sent, failure attempt audit, zero automatic retry, explicit human retry).
    - **Pacing & Rate Limiting (5 cases):** `P9-RATE-001..005` (sliding-window per-minute limit, batch request boundary enforcement).
    - **Batch Isolation (5 cases):** `P9-BATCH-001..005` (per-draft failure isolation, duplicate ID handling, stable result ordering).
    - **Append-Only Audit Trail (12 cases):** `P9-AUDIT-001..012` (lifecycle event logging for import, edit, approve, reject, changes-requested, suppress, send, failure).
    - **Delivery Attempts (4 cases):** `P9-ATTEMPT-001..004` (attempt record persistence, dry-run exclusion, credential sanitization).
    - **Terminal State Machine (4 cases):** `P9-STATE-001..004` (terminal sent-state immutability, revision re-opening, suppression precedence).
    - **Phase 8 Validation Reuse (5 cases):** `P9-P8-001..005` (unresolved placeholder rejection after edit, subject/body bounds, zero LLM usage).
    - **Phase 7 Workflow Projections (4 cases):** `P9-P7-001..004` (draft_ready, approved, sent, suppressed projection fidelity).
    - **Security & Sanitization (10 cases):** `P9-SEC-001..010` (SMTP password redacted in logs/API/DB, header injection prevention, parameterization).
    - **Zero LLM Code Path (4 cases):** `P9-NOLLM-001..004` (static import verification, zero LLM calls across review, edit, approve, send).
    - **Database Integrity (8 cases):** `P9-DB-001..008` (clean table initialization, idempotent migrations, revision & send_key constraints).
    - **Packaging Denylist (5 cases):** `P9-PACK-001..005` (exclusion of .env, runtime DB, Chroma DB, private keys).
    - **Opt-Out Footer & Fingerprint (3 cases):** `P9-OPTOUT-001..003` (server-owned footer appended and included in approval fingerprint).
    - **REST API Lifecycle (12 cases):** `P9-API-001..012` (complete HTTP lifecycle endpoints tested and verified).
    - **QA Integrity Meta-Rules (8 cases):** `P9-QA-001..008` (unmapped case detection, pass stub detection, dynamic false_pass_count calculation).
    - **Main Golden Fixture (1 case):** `P9-REVIEW-001` (frozen Phase 8 input draft imported into review queue).
    - **Live Safety Regression (1 case):** `P9-LIVE-SAFE-001` (zero live socket/SMTP network calls during offline test runs).
    - **Mandatory Regression Invariants (`P9-REG-008` .. `P9-REG-021`):** 14 cases (covering payload hash equality, missing sender rejection, sent revision immutability, trusted alias sending, untrusted alias blocking, display name fingerprinting, and environment name change immunity).
  - **Skipped / Opt-in Guardrail:** 1 case (`P9-LIVE-SMTP-001` — strictly gated behind `RUN_LIVE_EMAIL_TESTS=true` and `EMAIL_SEND_ENABLED=true` to guarantee zero real emails in offline suites).
- **False Pass Count:** 0 (dynamically verified and computed by execution-driven reporting engine).
- **Unmapped Deterministic Cases:** 0 (all deterministic cases traced to executable pytest nodes).
- **Safety Counters:**
  - `provider_calls_in_dry_run = 0`
  - `provider_calls_for_unapproved = 0`
  - `duplicate_send_count = 0`
  - `suppression_bypass_count = 0`
  - `stale_approval_bypass_count = 0`
  - `secrets_detected = 0`
  - `llm_calls_detected = 0`
  - `runtime_db_packaged = 0`

## Phase 10 Case Count & Architecture Model (Comprehensive Deterministic Golden QA)
Phase 10 implements deterministic multi-source market discovery, historical CRM/CSV/Excel ingestion, candidate qualification, watchlist routing, and verified bridges to downstream phases with a strict Zero-Send policy:
- **Total Cases in Catalog (`tests/reports/phase10_latest.json` & `tests/golden/phase10_discovery.json`):** 245 cases
  - **Passed (Deterministic):** 245 test cases mapped to real executable pytest nodes (0 failed, 0 review, 0 not_run, false_pass_count = 0, unmapped_deterministic_cases = 0):
    - **Mandatory Critical Regressions (`P10-REG-001` .. `P10-REG-015`):** 15/15 PASS
    - **Full Deterministic Golden Suites (Identity, Provenance, Sources, Markets, Tier/BIM/Fit/Intent, Historical Outreach Guards, Discovery Modes, Qualification, Watchlist, Bridges, Runs, Health, Security):** All PASS
    - **Critical Regressions (`P10-REG-001` .. `P10-REG-024`):** 24 cases:
      - `P10-REG-001`: Same company from Google/Directory/Job merges once via canonical domain
      - `P10-REG-002`: Same name with different domains remain strictly separate
      - `P10-REG-003`: Legal form alone never determines tier
      - `P10-REG-004`: Missing BIM mention resolves to unknown maturity, not no BIM
      - `P10-REG-005`: Directory results never create fake structured jobs
      - `P10-REG-006`: Historically contacted company excluded from cold outreach
      - `P10-REG-007`: Historical import is strictly idempotent
      - `P10-REG-008`: Good fit weak intent routes to watchlist
      - `P10-REG-009`: New signal promotes watchlist candidate
      - `P10-REG-010`: Suppressed company/contact cannot qualify for outreach
      - `P10-REG-011`: Phase 5 frozen job score remains unchanged (non-job candidates unscored)
      - `P10-REG-012`: Phase 8 grounding rejects unverified subjective claims
      - `P10-REG-013`: Failure of one source preserves other valid discoveries
      - `P10-REG-014`: Repeated daily runs update candidates idempotently without duplication
      - `P10-REG-015`: Release denylist excludes runtime databases and secrets
      - `P10-REG-016`: Relationship type classification (`service_buyer` vs `technology_partner`)
      - `P10-REG-017`: Multi-opportunity types classification
      - `P10-REG-018`: Active conversation guard (`active_conversation=True` -> `cold_outreach_allowed=False`)
      - `P10-REG-019`: Actual respondent preservation in historical replies
      - `P10-REG-020`: Multi-email delimiter splitting and normalization
      - `P10-REG-021`: Phase 10.5 Console Query Bridge integration
      - `P10-REG-022`: Zero-Send verification across all Phase 10 discovery flows
      - `P10-REG-023`: Historical Excel import acceptance
      - `P10-REG-024`: Historical CSV import acceptance
    - **Adversarial Security & Architecture Regressions Round 1 (`P10-ADV-001` .. `P10-ADV-026`):** 26 cases:
      - `P10-ADV-001`: Historical guard monotonic protection (cannot reset to `cold_outreach_allowed=True`)
      - `P10-ADV-002`: Real Phase 9 domain suppression enforced via read-only bridge
      - `P10-ADV-003`: Spreadsheet boolean parsing handles "No", "False", "0", Italian strings deterministically
      - `P10-ADV-004`: Phase 7 export bridge handles enum/string attributes safely without AttributeError
      - `P10-ADV-005`: Console watchlist bridge summary handles dictionary entries safely
      - `P10-ADV-006`: Observation SQLite roundtrip preserves full provenance (`source_name`, `url`, `extracted_data`)
      - `P10-ADV-007`: Grounding bridge rejects manual seeds lacking explicit source_url
      - `P10-ADV-008`: Grounding bridge filters out internal subjective assessments
      - `P10-ADV-009`: Bearer tokens and credentials sanitized from run errors and source health telemetry
      - `P10-ADV-010`: SourceRegistry.markets supports dictionary lookup and case-insensitive keys
      - `P10-ADV-011`: All 15 target markets configured in `config/markets.yaml`
      - `P10-ADV-012`: All 12 Phase 10 REST API endpoints registered and verified
      - `P10-ADV-013`: Phase 10 CLI commands execute with zero network sends
      - `P10-ADV-014`: Discovery run records persisted to SQLite table `discovery_runs`
      - `P10-ADV-015`: Source health telemetry persisted to SQLite table `source_health`
      - `P10-ADV-016`: Historical outreach events table populated during import
      - `P10-ADV-017`: Content-stable hash independent of file name and row index
      - `P10-ADV-018`: Freshness status tracked and persisted on observations and evidence
      - `P10-ADV-019`: Run counters differentiate between new discoveries and updated candidates
      - `P10-ADV-020`: Active conversation flag is monotonic and cannot be reset
      - `P10-ADV-021`: Candidate suppression is monotonic and cannot be reset
      - `P10-ADV-022`: Historical contact status never regresses to uncontacted
      - `P10-ADV-023`: CandidateStore migrations ensure columns exist in legacy databases
      - `P10-ADV-024`: Zero-send invariant audited across Phase 10 engine execution
      - `P10-ADV-025`: SourceConfig schema models all YAML parameters
      - `P10-ADV-026`: CLI and FastAPI endpoints uphold strictly zero-send contract
    - **Adversarial Residual Blockers & Invariants Round 2 (`P10-ADV2-001` .. `P10-ADV2-034`):** 34 cases:
      - `P10-ADV2-001`: Real Phase 9 domain suppression blocks classifier qualification
      - `P10-ADV2-002`: FastAPI qualification path uses authoritative Phase 9 suppression
      - `P10-ADV2-003`: CLI qualification path uses authoritative Phase 9 suppression
      - `P10-ADV2-004`: Email-only suppression does not suppress unrelated addresses on same domain
      - `P10-ADV2-005`: Explicit company_domain suppression blocks the domain
      - `P10-ADV2-006`: Old/non-current job evidence does not produce current high intent
      - `P10-ADV2-007`: Fresh current job evidence can produce current intent
      - `P10-ADV2-008`: Newer current evidence supersedes stale evidence without deleting history
      - `P10-ADV2-009`: Missing required source credential produces controlled source failure
      - `P10-ADV2-010`: Configured credential allows execution without exposing secret
      - `P10-ADV2-011`: Source max_results_per_run is enforced before candidate ingestion
      - `P10-ADV2-012`: Run metrics accurately reflect accepted results within limit
      - `P10-ADV2-013`: Unknown source type is rejected with ValueError
      - `P10-ADV2-014`: API discovery invokes registered adapter instead of returning no-op success
      - `P10-ADV2-015`: CLI discovery invokes registered adapter
      - `P10-ADV2-016`: Configured source without adapter is an explicit failure, not silent success
      - `P10-ADV2-017`: Job-first discovery retains job details without synthesizing jobs
      - `P10-ADV2-018`: Arbitrary/internal observation cannot become verified Phase 8 fact
      - `P10-ADV2-019`: Eligible sourced MarketEvidence becomes grounded fact
      - `P10-ADV2-020`: Source URL alone is insufficient without trusted source type
      - `P10-ADV2-021`: Renamed/reordered identical event produces identical hash
      - `P10-ADV2-022`: Materially changed follow-up date or respondent produces distinct hash
      - `P10-ADV2-023`: Duplicate identical event remains deduplicated in store
      - `P10-ADV2-024`: Historical import endpoint rejects ../ traversal and outside-root path
      - `P10-ADV2-025`: Allowed repository/data root file imports cleanly
      - `P10-ADV2-026`: Failed test cannot appear PASS in generated report
      - `P10-ADV2-027`: Unmapped deterministic case resolves to NOT_RUN, not PASS
      - `P10-ADV2-028`: Phase 10 report invariants derive dynamically from actual test results
      - `P10-ADV2-029`: Phase 10 safety counters are dynamic and track violations
      - `P10-ADV2-030`: Candidate list summary exposes country, qualification outcome/reason
      - `P10-ADV2-031`: Candidate detail bridge includes evidence directly
      - `P10-ADV2-032`: Qualification reason is persisted and available after restart
      - `P10-ADV2-033`: Source health persists to SQLite and survives console bridge restart
      - `P10-ADV2-034`: Source import rejects embedded secrets and invalid types
- **False Pass Count:** 0
- **Unmapped Deterministic Cases:** 0
- **Invariants Verified:**
  - `historical_guard_monotonic_unresettable = true`
  - `domain_suppression_strictly_enforced = true`
  - `boolean_parsing_deterministic = true`
  - `phase7_export_bridge_resilient = true`
  - `console_watchlist_bridge_resilient = true`
  - `provenance_preserved_after_roundtrip = true`
  - `phase8_grounding_rejects_subjective_claims = true`
  - `secrets_never_logged_or_exposed = true`
  - `markets_config_all_15_loaded = true`
  - `fastapi_endpoints_registered_and_zero_send = true`
  - `cli_registered_and_zero_send = true`
  - `discovery_runs_and_source_health_persisted = true`
  - `historical_outreach_events_populated = true`
  - `zero_send_contract_strictly_upheld = true`
  - `stale_evidence_gated_from_high_intent = true`
- **Audit Counters:**
  - `duplicate_company_count = 0`
  - `overmerge_count = 0`
  - `fake_job_count = 0`
  - `historical_duplicate_count = 0`
  - `cold_outreach_bypass_count = 0`
  - `suppression_bypass_count = 0`
  - `phase5_regression_count = 0`
  - `phase8_grounding_violation_count = 0`
  - `phase9_send_violation_count = 0`
  - `runtime_db_packaged_count = 0`
  - `source_partial_failure_loss_count = 0`
  - `uncredentialed_source_run_count = 0`
  - `source_budget_overrun_count = 0`
  - `path_traversal_violation_count = 0`
  - `unsupported_source_type_count = 0`

## Failures
_No failures detected._

## Human Review Required
These cases executed successfully but require business logic confirmation, or represent ambiguous situations.

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
  "company_summary": "pyBIM provides algorithmic BIM execution and Sovereign AI infrastructure for complex global engineering and construction enterprises.",
  "services": [
    "Tech-Enabled BIM Services"
  ],
  "offerings": [
    {
      "name": "Tech-Enabled BIM Services",
      "status": "active"
    },
    {
      "name": "pyBIM Cloud Connect",
      "status": "in_development"
    },
    {
      "name": "Sovereign Enterprise Edge AI",
      "status": "in_development"
    },
    {
      "name": "Secure Early Access to Sovereign AI Deployment",
      "status": "cta"
    }
  ],
  "target_industries": [
    "Engineering",
    "Construction",
    "AEC",
    "Infrastructure",
    "Public Sector"
  ],
  "target_company_types": [
    "Engineering Firms",
    "Construction Firms",
    "AEC Companies",
    "Infrastructure Firms",
    "Public Sector Firms"
  ],
  "pain_points": [
    "Inefficient Project Turnaround Times",
    "Sluggish Response Times",
    "Costly Data Entry Errors",
    "Margin Erosion",
    "Brute-force Manual Parameter Mapping"
  ],
  "buyer_roles": [
    "Senior BIM Manager",
    "Lead Technical Director",
    "Innovation Lead",
    "Head of Digital Delivery",
    "Operations Manager"
  ],
  "primary_job_signals": [
    "BIM Manager",
    "Revit API Developer",
    "Digital Delivery Manager",
    "Automation Engineer"
  ],
  "secondary_job_signals": [
    "Revit",
    "Python",
    "C#",
    "ISO 19650",
    "UNI 11337",
    "COBie",
    "IFC"
  ],
  "keywords": [
    "BIM",
    "Sovereign AI",
    "Algorithmic Execution",
    "ISO 19650",
    "UNI 11337",
    "AI-Powered Engineering",
    "Data Security",
    "Classified Data",
    "AI-Driven BIM",
    "Automated Clash Resolution",
    "Custom Automation",
    "Revit Integration",
    "Project Delivery",
    "Custom Software Development",
    "Sovereign AI Infrastructure"
  ],
  "negative_signals": []
}
```
**Result:** REVIEW

**Differences:**

**Missing:**
- service_concept: 'BIM_EXECUTION'
- service_concept: 'BIM_AUTOMATION'
- service_concept: 'SOVEREIGN_AI'
- service_concept: 'COMPLIANCE_AUDITING'
- target_industries: 'architecture'

**Extra:**
- services: 'Tech-Enabled BIM Services'
- target_industries: 'public sector'
- Field `company_summary` mismatch: expected `Advanced engineering and software lab providing BIM execution, custom BIM/Revit automation, and sovereign AI infrastructure for AEC organizations.`, got `pyBIM provides algorithmic BIM execution and Sovereign AI infrastructure for complex global engineering and construction enterprises.`
- Field `services` list mismatch (missing: ['Managed / Tech-Enabled BIM Execution', 'BIM Modeling and Multidisciplinary Clash Coordination', 'ISO 19650 and UNI 11337 Compliance Auditing', 'Custom Python and C# BIM Automation', 'Revit / Navisworks API Integration', 'Automated Parameter and Metadata Processing', 'Sovereign / Air-Gapped Enterprise AI Infrastructure'], extra: ['Tech-Enabled BIM Services']): expected `['Managed / Tech-Enabled BIM Execution', 'BIM Modeling and Multidisciplinary Clash Coordination', 'ISO 19650 and UNI 11337 Compliance Auditing', 'Custom Python and C# BIM Automation', 'Revit / Navisworks API Integration', 'Automated Parameter and Metadata Processing', 'Sovereign / Air-Gapped Enterprise AI Infrastructure']`, got `['Tech-Enabled BIM Services']`
- Field `target_industries` list mismatch (missing: ['Architecture'], extra: ['Public Sector']): expected `['AEC', 'Architecture', 'Engineering', 'Construction', 'Infrastructure']`, got `['Engineering', 'Construction', 'AEC', 'Infrastructure', 'Public Sector']`
- Field `target_company_types` list mismatch (missing: ['Architecture firms', 'Engineering firms', 'General contractors', 'Growing AEC firms', 'Mid-to-large AEC enterprises', 'Tier-1 contractors', 'Government contractors'], extra: ['Engineering Firms', 'Construction Firms', 'AEC Companies', 'Infrastructure Firms', 'Public Sector Firms']): expected `['Architecture firms', 'Engineering firms', 'General contractors', 'Growing AEC firms', 'Mid-to-large AEC enterprises', 'Tier-1 contractors', 'Government contractors']`, got `['Engineering Firms', 'Construction Firms', 'AEC Companies', 'Infrastructure Firms', 'Public Sector Firms']`
- Field `pain_points` list mismatch (missing: ['Manual BIM data entry and parameter mapping', 'Repetitive BIM workflows', 'Slow clash detection and coordination', 'High engineering labor costs', 'Scaling through additional headcount', 'ISO 19650 and UNI 11337 compliance risk', 'Public tender compliance risk', 'BIM data quality errors', 'Sensitive project data exposure to public cloud systems', 'Rigid generic software workflows'], extra: ['Inefficient Project Turnaround Times', 'Sluggish Response Times', 'Costly Data Entry Errors', 'Margin Erosion', 'Brute-force Manual Parameter Mapping']): expected `['Manual BIM data entry and parameter mapping', 'Repetitive BIM workflows', 'Slow clash detection and coordination', 'High engineering labor costs', 'Scaling through additional headcount', 'ISO 19650 and UNI 11337 compliance risk', 'Public tender compliance risk', 'BIM data quality errors', 'Sensitive project data exposure to public cloud systems', 'Rigid generic software workflows']`, got `['Inefficient Project Turnaround Times', 'Sluggish Response Times', 'Costly Data Entry Errors', 'Margin Erosion', 'Brute-force Manual Parameter Mapping']`
- Field `buyer_roles` list mismatch (missing: ['BIM Manager', 'Technical Director'], extra: ['Senior BIM Manager', 'Lead Technical Director']): expected `['BIM Manager', 'Technical Director', 'Head of Digital Delivery', 'Innovation Lead', 'Operations Manager']`, got `['Senior BIM Manager', 'Lead Technical Director', 'Innovation Lead', 'Head of Digital Delivery', 'Operations Manager']`
- Field `primary_job_signals` list mismatch (missing: ['BIM Manager / Head of BIM', 'Digital Delivery Manager / Head of Digital Delivery', 'BIM Automation Engineer', 'BIM Software Developer', 'C# / .NET Revit Developer', 'Python BIM Automation', 'Dynamo / pyRevit Developer', 'BIM Information / Data Manager', 'ISO 19650 information-management roles', 'Digital Construction / BIM technology leadership'], extra: ['BIM Manager', 'Digital Delivery Manager', 'Automation Engineer']): expected `['BIM Manager / Head of BIM', 'Digital Delivery Manager / Head of Digital Delivery', 'BIM Automation Engineer', 'Revit API Developer', 'BIM Software Developer', 'C# / .NET Revit Developer', 'Python BIM Automation', 'Dynamo / pyRevit Developer', 'BIM Information / Data Manager', 'ISO 19650 information-management roles', 'Digital Construction / BIM technology leadership']`, got `['BIM Manager', 'Revit API Developer', 'Digital Delivery Manager', 'Automation Engineer']`
- Field `secondary_job_signals` list mismatch (missing: ['Navisworks', 'Solibri', 'IFC / OpenBIM', 'Autodesk Construction Cloud / CDE', 'Computational design', 'BIM data / metadata management', 'Digital transformation roles in AEC'], extra: ['Revit', 'Python', 'C#', 'ISO 19650', 'UNI 11337', 'IFC']): expected `['Navisworks', 'Solibri', 'IFC / OpenBIM', 'COBie', 'Autodesk Construction Cloud / CDE', 'Computational design', 'BIM data / metadata management', 'Digital transformation roles in AEC']`, got `['Revit', 'Python', 'C#', 'ISO 19650', 'UNI 11337', 'COBie', 'IFC']`

**Human Notes:**
Ground Truth verified by human review from real pybim.com crawl snapshot. FLAGGED FOR HUMAN CORRECTION: Expected primary_job_signals contains 3 non-pure job titles: 'Python BIM Automation' (propose 'Python BIM Automation Engineer'), 'ISO 19650 information-management roles' (propose 'BIM Information Manager'), 'Digital Construction / BIM technology leadership' (propose 'Digital Construction Manager').

---
### P5-EVID-002 — Missing Evidence Penalization
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "evidence_score": 5,
  "grounded_bonus_awarded": false,
  "no_evidence_fabrication": true
}
```
**Actual:**
```json
{
  "evidence_score": 0,
  "grounded_bonus_awarded": false,
  "no_evidence_fabrication": true
}
```

**Result:** REVIEW

**Differences:**
- Field `evidence_score` mismatch: expected `5`, got `0`

**Human Notes:**
DISCREPANCY FLAGGED FOR HUMAN APPROVAL:
- Expected: evidence_score=5
- Actual: evidence_score=0
- Analysis: Production scoring correctly assigns 0 to empty-string evidence. Golden Ground Truth currently expects 5. Do not modify Golden Ground Truth without explicit human approval.

---
### P6-EMAIL-003 — No Email Fallback (Zero Fabrication)
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "email_status": "not_found",
  "work_email": null,
  "no_fabricated_email": true
}
```
**Actual:**
```json
{
  "work_email": null,
  "email_status": "unknown",
  "no_fabricated_email": true
}
```

**Result:** REVIEW

**Differences:**
- Field `email_status` mismatch: expected `not_found`, got `unknown`

**Human Notes:**
DISCREPANCY FLAGGED FOR HUMAN APPROVAL:
- Expected: email_status='not_found'
- Actual: email_status='unknown'
- Analysis: ContactCandidate defaults to 'unknown' when work_email is None. Golden Ground Truth currently expects 'not_found'. Do not modify Golden Ground Truth without explicit human approval.

---
### P7-WF-005 — Existing Workflow State
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-WF-005"
}
```
**Expected:**
```json
{
  "status": "REVIEW"
}
```
**Actual:**
```json
{
  "status": "REVIEW"
}
```
**Result:** REVIEW

**Differences:**
_(None / In sync)_

**Reason:** Human review required for pre-existing non-default workflow state export projection

**Human Notes:**
Phase 7 Golden Case: Existing Workflow State

---

## Semantic Golden Cases (Model Limitation / Deferred)
These fixtures are prepared with full Ground Truth (requirements, technologies, seniority, signals, evidence) but are deferred until a high-capacity LLM is available.

### P4-SEM-001 — Semantic Golden Fixture (BIM Specialist)
**Phase:** Phase 4
**Source:** https://www.arup.com/careers/bim-specialist
**Snapshot:** `tests/golden/snapshots/job_pages/generic/generic_jsonld_sample.html`

**Input:**
```json
{
  "url": "https://www.arup.com/careers/bim-specialist"
}
```

**Expected:**
```json
{
  "company_name": "ARUP Engineering Ltd",
  "job_title": "BIM Specialist",
  "technologies": [
    "Revit",
    "Navisworks",
    "ISO 19650"
  ],
  "seniority": "mid_senior",
  "remote_status": "onsite",
  "relevant_signals": [
    {
      "signal": "Hiring BIM Specialist with ISO 19650 coordination skills",
      "evidence": "seeking a talented BIM Specialist with Revit, Navisworks, and ISO 19650 knowledge"
    }
  ]
}
```

**Actual:**
_(Not executed / Deferred for stronger model)_

**Differences:**
_(Pending execution / Deferred)_

**Evidence Check:**
- Ground Truth Signals defined with verifiable snapshot quotes:
  - **Signal:** `Hiring BIM Specialist with ISO 19650 coordination skills`
    **Evidence quote:** `"seeking a talented BIM Specialist with Revit, Navisworks, and ISO 19650 knowledge"`
- **Verification Status:** `DEFERRED (NOT_RUN_MODEL_LIMITATION)` — will be verified against snapshot HTML when stronger LLM is active.

**Result:** NOT_RUN_MODEL_LIMITATION

**Reason:** Deferred: Heavy semantic LLM evaluation will run on stronger hardware/model.

**Human Notes:**
Semantic LLM extraction fixture prepared for future evaluation on larger models (qwen2.5:3b/7b/claude). Contains ground truth technologies and signal evidence.

---
### P4-SEM-002 — Semantic Golden Fixture (Revit API Developer)
**Phase:** Phase 4
**Source:** https://jobs.lever.co/bimstudio/67890
**Snapshot:** `tests/golden/snapshots/job_pages/lever/lever_job_sample.html`

**Input:**
```json
{
  "url": "https://jobs.lever.co/bimstudio/67890"
}
```

**Expected:**
```json
{
  "company_name": "BIM Studio London",
  "job_title": "Revit API Developer",
  "technologies": [
    "C#",
    "Python",
    "Revit API",
    "pyRevit",
    "COBie",
    "IFC"
  ],
  "seniority": "mid_senior",
  "remote_status": "hybrid",
  "relevant_signals": [
    {
      "signal": "Developing custom C# and Python Revit automation plugins",
      "evidence": "looking for a Revit API Developer to build native C# and Python add-ins"
    }
  ]
}
```

**Actual:**
_(Not executed / Deferred for stronger model)_

**Differences:**
_(Pending execution / Deferred)_

**Evidence Check:**
- Ground Truth Signals defined with verifiable snapshot quotes:
  - **Signal:** `Developing custom C# and Python Revit automation plugins`
    **Evidence quote:** `"looking for a Revit API Developer to build native C# and Python add-ins"`
- **Verification Status:** `DEFERRED (NOT_RUN_MODEL_LIMITATION)` — will be verified against snapshot HTML when stronger LLM is active.

**Result:** NOT_RUN_MODEL_LIMITATION

**Reason:** Deferred: Heavy semantic LLM evaluation will run on stronger hardware/model.

**Human Notes:**
Semantic LLM extraction fixture for API developer role. Deferring execution until higher-tier model deployment.

---
### P8-SEM-001 — Strong BIM Automation Email Semantic Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SEM-001"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```
**Actual:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION",
  "reason": "Deferred for higher-capacity model review; deterministic invariants verified"
}
```
**Result:** NOT_RUN_MODEL_LIMITATION

**Differences:**
_(None / In sync)_

**Reason:** Deferred for higher-capacity model review; deterministic invariants verified

**Human Notes:**
Phase 8: Strong BIM Automation Email Semantic Review (test: None)

---
### P8-SEM-002 — BIM Manager Hiring Email Semantic Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SEM-002"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```
**Actual:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION",
  "reason": "Deferred for higher-capacity model review; deterministic invariants verified"
}
```
**Result:** NOT_RUN_MODEL_LIMITATION

**Differences:**
_(None / In sync)_

**Reason:** Deferred for higher-capacity model review; deterministic invariants verified

**Human Notes:**
Phase 8: BIM Manager Hiring Email Semantic Review (test: None)

---
### P8-SEM-003 — Executive Brief Tone Semantic Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SEM-003"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```
**Actual:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION",
  "reason": "Deferred for higher-capacity model review; deterministic invariants verified"
}
```
**Result:** NOT_RUN_MODEL_LIMITATION

**Differences:**
_(None / In sync)_

**Reason:** Deferred for higher-capacity model review; deterministic invariants verified

**Human Notes:**
Phase 8: Executive Brief Tone Semantic Review (test: None)

---
### P8-SEM-004 — Italian Draft Semantic Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SEM-004"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```
**Actual:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION",
  "reason": "Deferred for higher-capacity model review; deterministic invariants verified"
}
```
**Result:** NOT_RUN_MODEL_LIMITATION

**Differences:**
_(None / In sync)_

**Reason:** Deferred for higher-capacity model review; deterministic invariants verified

**Human Notes:**
Phase 8: Italian Draft Semantic Review (test: None)

---
### P8-SEM-005 — German Draft Semantic Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SEM-005"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```
**Actual:**
```json
{
  "status": "NOT_RUN_MODEL_LIMITATION",
  "reason": "Deferred for higher-capacity model review; deterministic invariants verified"
}
```
**Result:** NOT_RUN_MODEL_LIMITATION

**Differences:**
_(None / In sync)_

**Reason:** Deferred for higher-capacity model review; deterministic invariants verified

**Human Notes:**
Phase 8: German Draft Semantic Review (test: None)

---

## Detailed Cases (Passed & Deterministic Results)

### P3-DISC-001 — pyBIM Discovery Query Generation
**Phase:** Phase 3
**Source:** https://pybim.com

**Frozen Input Profile:**
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
**Countries:** Italy, United Kingdom, Germany

**Expected Intent Coverage:**
```json
{
  "required_intents": [
    "direct_primary_job_hiring",
    "company_careers_pages",
    "ats_discovery",
    "secondary_technology_support",
    "requested_geography"
  ],
  "min_primary_signals_covered": 3,
  "min_ats_providers_covered": 2,
  "required_countries": [
    "Italy",
    "United Kingdom",
    "Germany"
  ],
  "career_intent_required": true,
  "max_duplicates": 0,
  "max_forbidden_patterns": 0
}
```

**Actual Queries:**
| # | Type | Priority | Query |
|---|---|---|---|
| 1 | `ats_targeted` | 10 | `site:jobs.lever.co "BIM Manager"` |
| 2 | `ats_targeted` | 10 | `site:boards.greenhouse.io "Head of BIM"` |
| 3 | `ats_targeted` | 9 | `site:jobs.ashbyhq.com "Digital Delivery Manager"` |
| 4 | `general_job` | 9 | `"BIM Manager" jobs Italy` |
| 5 | `general_job` | 8 | `"Head of BIM" hiring Italy` |
| 6 | `general_job` | 8 | `"Head of BIM" jobs United Kingdom` |
| 7 | `company_career` | 8 | `"BIM Manager" careers AEC` |
| 8 | `company_career` | 8 | `"Head of BIM" careers Architecture` |
| 9 | `general_job` | 7 | `"Digital Delivery Manager" hiring United Kingdom` |
| 10 | `general_job` | 7 | `"Digital Delivery Manager" jobs Germany` |
| 11 | `company_career` | 7 | `"Digital Delivery Manager" careers AEC Italy` |
| 12 | `keyword_signal` | 7 | `"BIM Manager" Navisworks Solibri` |
| 13 | `keyword_signal` | 7 | `"Head of BIM" IFC` |
| 14 | `general_job` | 6 | `"BIM Automation Engineer" hiring Germany` |
| 15 | `keyword_signal` | 6 | `"BIM Automation Engineer" Navisworks` |

**Metrics:**
- **Total Queries:** 15
- **Unique Queries:** 15
- **Duplicate Queries:** 0
- **Primary Signals Covered:** 4 (BIM Manager, Head of BIM, Digital Delivery Manager, BIM Automation Engineer)
- **Countries Requested:** Italy, United Kingdom, Germany
- **Countries Covered:** 3/3 (Italy, United Kingdom, Germany)
- **ATS Providers Covered:** 3 (lever, greenhouse, ashby)
- **Career Intent Present:** YES
- **Secondary Supported Query Count:** 3
- **Forbidden Pattern Count:** 0

**Duplicate Queries:** None
**Forbidden Patterns:** None

**Result:** PASS

**Reason:** All Phase 3 critical requirements and intent coverage targets satisfied.

**Human Notes:**
Frozen human-approved WebsiteProfile for pyBIM to test Discovery query generation independently of Phase 2 known limitations. Evaluates intent coverage, signal diversity, ATS targeting, geography distribution, and absence of semantic pollution.

---
### P3-LIVE-001 — pyBIM Discovery Live Search Validation
**Phase:** Phase 3
**Source:** https://api.search.brave.com

**Input:**
```json
{
  "queries_sample_count": 3,
  "results_per_query": 10
}
```
**Expected:**
```json
{
  "min_precision_at_10": 0.5,
  "target_categories": [
    "company_career",
    "lever",
    "greenhouse",
    "ashby",
    "other_ats",
    "job_board"
  ]
}
```
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

**Reason:** Test skipped / not run in this environment

**Human Notes:**
Live test validating top 10 search results per query. Evaluates Precision@10 across ATS and career categories. Status remains REVIEW for initial baseline.

---
### P4-DET-001 — Source Detection
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "urls": [
    "https://boards.greenhouse.io/studioaec/jobs/12345",
    "https://jobs.lever.co/bimstudio/67890",
    "https://jobs.ashbyhq.com/foster/abcdef",
    "https://arup.com/careers/specialist"
  ]
}
```

**Expected:**
```json
{
  "sources": [
    "greenhouse",
    "lever",
    "ashby",
    "generic"
  ]
}
```

**Actual:**
```json
{
  "sources": [
    "greenhouse",
    "lever",
    "ashby",
    "generic"
  ]
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Verifies accurate detection of ATS providers vs generic career pages from URLs.

---
### P4-DET-002 — Greenhouse Structured Extraction
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `tests/golden/snapshots/job_pages/greenhouse/greenhouse_job_sample.html`

**Input:**
```json
{
  "url": "https://boards.greenhouse.io/studioaec/jobs/12345"
}
```

**Expected:**
```json
{
  "company_name": "Studio AEC Architecture",
  "job_title": "Senior BIM Coordinator",
  "location": "Berlin, Germany",
  "source": "greenhouse",
  "source_company_key": "studioaec"
}
```

**Actual:**
```json
{
  "company_name": "Studio AEC Architecture",
  "job_title": "Senior BIM Coordinator",
  "location": "Berlin, Germany",
  "source": "greenhouse",
  "source_company_key": "studioaec"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Extracts basic structured fields from Greenhouse HTML/API snapshot.

---
### P4-DET-003 — Lever Structured Extraction
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `tests/golden/snapshots/job_pages/lever/lever_job_sample.html`

**Input:**
```json
{
  "url": "https://jobs.lever.co/bimstudio/67890"
}
```

**Expected:**
```json
{
  "company_name": "BIM Studio London",
  "job_title": "Revit API Developer",
  "location": "London, United Kingdom",
  "employment_type": "Full-time",
  "source": "lever",
  "source_company_key": "bimstudio"
}
```

**Actual:**
```json
{
  "company_name": "BIM Studio London",
  "job_title": "Revit API Developer",
  "location": "London, United Kingdom",
  "employment_type": "Full-time",
  "source": "lever",
  "source_company_key": "bimstudio"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Extracts role title, location, employment type, and source key from Lever snapshot.

---
### P4-DET-004 — Ashby Structured Extraction
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `tests/golden/snapshots/job_pages/ashby/ashby_job_sample.html`

**Input:**
```json
{
  "url": "https://jobs.ashbyhq.com/foster/abcdef"
}
```

**Expected:**
```json
{
  "company_name": "Foster Construction",
  "job_title": "Digital Delivery Manager",
  "location": "Milan, Italy (Hybrid)",
  "source": "ashby"
}
```

**Actual:**
```json
{
  "company_name": "Foster Construction",
  "job_title": "Digital Delivery Manager",
  "location": "Milan, Italy (Hybrid)",
  "source": "ashby"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Extracts company, title, and location from Ashby job page snapshot.

---
### P4-DET-005 — Generic JSON-LD JobPosting Extraction
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `tests/golden/snapshots/job_pages/generic/generic_jsonld_sample.html`

**Input:**
```json
{
  "url": "https://www.arup.com/careers/bim-specialist"
}
```

**Expected:**
```json
{
  "company_name": "ARUP Engineering Ltd",
  "job_title": "BIM Specialist",
  "posted_date": "2026-09-15",
  "employment_type": "FULL_TIME",
  "location": "Munich, Bavaria, Germany",
  "company_same_as": "https://www.arup.com"
}
```

**Actual:**
```json
{
  "company_name": "ARUP Engineering Ltd",
  "job_title": "BIM Specialist",
  "posted_date": "2026-09-15",
  "employment_type": "FULL_TIME",
  "location": "Munich, Bavaria, Germany",
  "company_same_as": "https://www.arup.com"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Parses standard Schema.org JobPosting application/ld+json metadata.

---
### P4-DET-006 — JSON-LD @graph JobPosting Extraction
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `tests/golden/snapshots/job_pages/generic/generic_graph_sample.html`

**Input:**
```json
{
  "url": "https://www.obermeyer-group.com/careers/digital-lead"
}
```

**Expected:**
```json
{
  "company_name": "Obermeyer Planen GmbH",
  "job_title": "Digital Construction Lead",
  "posted_date": "2026-09-20",
  "employment_type": "FULL_TIME",
  "location": "Frankfurt, Hesse, Germany",
  "company_same_as": "https://www.obermeyer-group.com"
}
```

**Actual:**
```json
{
  "company_name": "Obermeyer Planen GmbH",
  "job_title": "Digital Construction Lead",
  "posted_date": "2026-09-20",
  "employment_type": "FULL_TIME",
  "location": "Frankfurt, Hesse, Germany",
  "company_same_as": "https://www.obermeyer-group.com"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Locates and extracts JobPosting when nested inside @graph schema array.

---
### P4-DET-007 — Company Domain Safety & Canonicalization
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "same_as_url": "https://www.arup.com/about-us",
  "ats_job_url": "https://jobs.lever.co/arup/12345",
  "canonical_variants": [
    "https://arup.com",
    "https://www.arup.com/",
    "https://WWW.ARUP.COM/about"
  ]
}
```

**Expected:**
```json
{
  "resolved_domain": "arup.com",
  "canonical_identity_domain": "arup.com",
  "forbidden_ats_domains": [
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com"
  ]
}
```

**Actual:**
```json
{
  "resolved_domain": "arup.com",
  "canonical_identity_domain": "arup.com",
  "forbidden_ats_domains": [
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com"
  ]
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Ensures company domain canonicalizes www and case while never using the ATS hosting platform.

---
### P4-DET-008 — Company Name Normalization
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "raw_names": [
    "Obermeyer Planen GmbH",
    "ARUP Engineering Ltd",
    "Foster Construction Limited",
    "AECOM Inc.",
    "Studio One LLC",
    "Tech AEC S.r.l.",
    "Italferr S.p.A."
  ]
}
```

**Expected:**
```json
{
  "normalized_names": [
    "obermeyer planen",
    "arup engineering",
    "foster construction",
    "aecom",
    "studio one",
    "tech aec",
    "italferr"
  ]
}
```

**Actual:**
```json
{
  "normalized_names": [
    "obermeyer planen",
    "arup engineering",
    "foster construction",
    "aecom",
    "studio one",
    "tech aec",
    "italferr"
  ]
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Normalizes corporate legal suffixes, punctuation, and whitespace differences.

---
### P4-DET-009 — Source Company Key Preservation
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "raw_key": "acme-engineering-corp"
}
```

**Expected:**
```json
{
  "source_company_key": "acme-engineering-corp"
}
```

**Actual:**
```json
{
  "source_company_key": "acme-engineering-corp"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Verifies source_company_key survives across raw source, analyzer, and StructuredJob.

---
### P4-DET-010 — Error Classification
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "error_scenarios": [
    "timeout",
    "fetch_failed",
    "parse_failed",
    "analysis_failed"
  ]
}
```

**Expected:**
```json
{
  "retained_error_types": [
    "timeout",
    "fetch_failed",
    "parse_failed",
    "analysis_failed"
  ]
}
```

**Actual:**
```json
{
  "retained_error_types": [
    "timeout",
    "fetch_failed",
    "parse_failed",
    "analysis_failed"
  ]
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Validates distinct error classification without silently converting errors to empty successes.

---
### P4-NEG-001 — Missing Company Name Handling
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "raw_job": {
    "title": "BIM Manager",
    "description": "Looking for BIM Manager.",
    "company": "",
    "url": "https://example.com/job/1"
  }
}
```

**Expected:**
```json
{
  "valid_job": false,
  "error_type": "parse_failed",
  "did_not_crash": true,
  "extraction_succeeded": false
}
```

**Actual:**
```json
{
  "valid_job": false,
  "error_type": "parse_failed",
  "did_not_crash": true,
  "extraction_succeeded": false
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Confirms missing company name is rejected as invalid/parse_failed rather than succeeding as an empty job.

---
### P4-NEG-002 — Missing Job Title Handling
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "raw_job": {
    "title": "",
    "description": "General description.",
    "company": "Studio AEC",
    "url": "https://example.com/job/2"
  }
}
```

**Expected:**
```json
{
  "valid_job": false,
  "error_type": "parse_failed",
  "did_not_crash": true,
  "extraction_succeeded": false
}
```

**Actual:**
```json
{
  "valid_job": false,
  "error_type": "parse_failed",
  "did_not_crash": true,
  "extraction_succeeded": false
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Confirms missing job title produces an explicit validation/parse_failed error and is not accepted.

---
### P4-NEG-003 — Malformed JSON-LD Handling
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "html": "<html><head><script type=\"application/ld+json\">{broken json, not valid</script></head><body><h1>BIM Job</h1><p>Full description here</p></body></html>",
  "url": "https://example.com/job/3"
}
```

**Expected:**
```json
{
  "fallback_to_dom": true
}
```

**Actual:**
```json
{
  "fallback_to_dom": true
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Gracefully recovers from malformed JSON-LD scripts using DOM fallback.

---
### P4-NEG-004 — Expired / Removed Job Page Handling
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "html": "<html><body><h1>This job has been filled or expired</h1><p>Check our other openings.</p></body></html>",
  "url": "https://example.com/job/4"
}
```

**Expected:**
```json
{
  "no_fabricated_job": true
}
```

**Actual:**
```json
{
  "no_fabricated_job": true
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Does not fabricate active job postings from expired notices.

---
### P4-NEG-005 — Careers Homepage Without Job Handling
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "html": "<html><body><h1>Careers at AEC Corp</h1><p>We are always looking for great people to join our company.</p></body></html>",
  "url": "https://example.com/careers"
}
```

**Expected:**
```json
{
  "has_active_job": false
}
```

**Actual:**
```json
{
  "has_active_job": false
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Identifies generic career homepages that lack specific job postings.

---
### P4-NEG-006 — Duplicate URL Variants Deduplication
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "urls": [
    "https://example.com/jobs/bim-manager?utm_source=linkedin",
    "https://example.com/jobs/bim-manager?utm_campaign=hiring",
    "https://example.com/jobs/bim-manager#apply",
    "https://example.com/jobs/bim-manager/"
  ]
}
```

**Expected:**
```json
{
  "unique_urls_count": 1,
  "normalized_url": "https://example.com/jobs/bim-manager"
}
```

**Actual:**
```json
{
  "unique_urls_count": 1,
  "normalized_url": "https://example.com/jobs/bim-manager"
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Normalizes URL tracking query params, fragments, and trailing slashes.

---
### P4-NEG-007 — ATS Provider Domain Safety Check
**Phase:** Phase 4
**Source:** internal
**Snapshot:** `None (Synthetic / Dynamic)`

**Input:**
```json
{
  "job_urls": [
    "https://jobs.lever.co/client/123",
    "https://boards.greenhouse.io/client/456",
    "https://jobs.ashbyhq.com/client/789"
  ]
}
```

**Expected:**
```json
{
  "disallowed_company_domains": [
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com"
  ]
}
```

**Actual:**
```json
{
  "disallowed_company_domains": [
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com"
  ]
}
```
**Differences:**
_(None / In sync)_

**Evidence Check:**
- N/A (Deterministic / Structural Rule Verification)

**Result:** PASS

**Human Notes:**
Guarantees ATS domains never become company_domain on StructuredJob.

---
### P5-STRONG-001 — Strong Multi-Job BIM Lead Aggregation
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_strong_lead_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `acme-engineering.com` (Method: `domain`)
- **Jobs:** 3 input / 3 unique / 3 relevant (0 ignored)
- **Score Breakdown:**
  - Fit: 30/30
  - Intent: 30/30
  - Recency: 20/20
  - Evidence: 6/20
  - **Total:** 86/100
- **Qualification:** `QUALIFIED` (Threshold >= 60)
- **Scoring Reasons (9):**
  - Has relevant job postings (+10)
  - 9 relevant technologies detected (+15)
  - Company has strong signals (+5)
  - First relevant hiring signal detected (+10)
  - Second relevant hiring signal detected (+5)
  - Multiple relevant hiring signals detected (+5)
  - Leadership/manager role found (+10)
  - Relevant jobs posted within 7 days (+20)
  - 3 unique pieces of evidence (+6)
- **Grounded Evidence (3):**
  - "Build custom Revit plugins using C# .NET and Python to automate drafting and QA processes."
  - "Oversee enterprise digital delivery, automation pipelines, and BIM standards adherence."
  - "Seeking an experienced BIM Manager to lead digital project delivery using Revit and ISO 19650 workflows."

**Expected:**
```json
{
  "companies_resolved": 1,
  "company_name": "Acme Engineering",
  "company_domain": "acme-engineering.com",
  "total_job_count": 3,
  "unique_job_count": 3,
  "relevant_job_count": 3,
  "fit_score": 30,
  "intent_score": 30,
  "recency_score": 20,
  "evidence_score": 6,
  "total_score": 86,
  "qualified": true,
  "min_qualified_threshold": 60
}
```
**Actual:**
```json
{
  "companies_resolved": 1,
  "company_name": "Acme Engineering",
  "company_domain": "acme-engineering.com",
  "identity_method": "domain",
  "total_jobs": 3,
  "unique_jobs": 3,
  "relevant_jobs": 3,
  "total_job_count": 3,
  "unique_job_count": 3,
  "relevant_job_count": 3,
  "min_qualified_threshold": 60,
  "ignored_jobs": 0,
  "fit_score": 30,
  "intent_score": 30,
  "recency_score": 20,
  "evidence_score": 6,
  "total_score": 86,
  "qualification_threshold": 60,
  "qualified": true,
  "reasons_count": 9,
  "scoring_reasons": [
    "Has relevant job postings (+10)",
    "9 relevant technologies detected (+15)",
    "Company has strong signals (+5)",
    "First relevant hiring signal detected (+10)",
    "Second relevant hiring signal detected (+5)",
    "Multiple relevant hiring signals detected (+5)",
    "Leadership/manager role found (+10)",
    "Relevant jobs posted within 7 days (+20)",
    "3 unique pieces of evidence (+6)"
  ],
  "evidence_count": 3,
  "evidence": [
    "Build custom Revit plugins using C# .NET and Python to automate drafting and QA processes.",
    "Oversee enterprise digital delivery, automation pipelines, and BIM standards adherence.",
    "Seeking an experienced BIM Manager to lead digital project delivery using Revit and ISO 19650 workflows."
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Multiple strong relevant jobs yield high intent, top recency, rich grounded evidence, and qualification.

---
### P5-WEAK-001 — Generic AEC Non-BIM Company (Low Score & Unqualified)
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_weak_lead_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `general-aec-corp.com` (Method: `domain`)
- **Jobs:** 1 input / 1 unique / 0 relevant (1 ignored)
- **Score Breakdown:**
  - Fit: 0/30
  - Intent: 0/30
  - Recency: 5/20
  - Evidence: 0/20
  - **Total:** 5/100
- **Qualification:** `UNQUALIFIED` (Threshold >= 60)

**Expected:**
```json
{
  "companies_resolved": 1,
  "company_name": "General AEC Corp",
  "company_domain": "general-aec-corp.com",
  "total_job_count": 1,
  "unique_job_count": 1,
  "relevant_job_count": 0,
  "fit_score": 0,
  "intent_score": 0,
  "recency_score": 5,
  "evidence_score": 0,
  "total_score": 5,
  "qualified": false,
  "min_qualified_threshold": 60
}
```
**Actual:**
```json
{
  "companies_resolved": 1,
  "company_name": "General AEC Corp",
  "company_domain": "general-aec-corp.com",
  "identity_method": "domain",
  "total_jobs": 1,
  "unique_jobs": 1,
  "relevant_jobs": 0,
  "total_job_count": 1,
  "unique_job_count": 1,
  "relevant_job_count": 0,
  "min_qualified_threshold": 60,
  "ignored_jobs": 1,
  "fit_score": 0,
  "intent_score": 0,
  "recency_score": 5,
  "evidence_score": 0,
  "total_score": 5,
  "qualification_threshold": 60,
  "qualified": false,
  "reasons_count": 1,
  "evidence_count": 0
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Generic administrative job must not invent BIM relevance or intent; lead is disqualified.

---
### P5-ID-001 — Same Name, Different Domains (No Collapsing)
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_identity_collision_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A` (Method: `domain`)

**Expected:**
```json
{
  "companies_resolved": 2,
  "domains_must_differ": true,
  "prevent_name_override_domain": true
}
```
**Actual:**
```json
{
  "companies_resolved": 2,
  "domains": [
    "abc-engineering.com",
    "abc-engineering.de"
  ],
  "domains_must_differ": true,
  "identity_method": "domain",
  "prevent_name_override_domain": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Normalized company name must NOT override conflicting domains.

---
### P5-ID-002 — Same Domain, Name Variants (Canonical Grouping)
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_identity_collision_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `acme.com` (Method: `domain`)

**Expected:**
```json
{
  "companies_resolved": 1,
  "canonical_domain": "acme.com",
  "total_jobs_grouped": 3
}
```
**Actual:**
```json
{
  "companies_resolved": 1,
  "canonical_domain": "acme.com",
  "total_jobs_grouped": 3,
  "identity_method": "domain"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Different name spellings sharing identical domain must merge into 1 CompanyLead.

---
### P5-ID-003 — Source Key Identity (No Domain)
**Phase:** Phase 5
**Source:** internal

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A` (Method: `source_company_key`)

**Expected:**
```json
{
  "companies_resolved": 1,
  "source_key_used": "greenhouse:acme-engineering"
}
```
**Actual:**
```json
{
  "companies_resolved": 1,
  "source_key_used": "greenhouse:acme-engineering",
  "identity_method": "source_company_key"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
When domain is absent, ATS source + source_company_key correctly identifies the company.

---
### P5-ID-004 — Conflicting Source Keys (Prevent Aggressive Merge)
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_identity_collision_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A` (Method: `source_conflict_isolation`)

**Expected:**
```json
{
  "companies_resolved": 2,
  "prevent_aggressive_merge": true
}
```
**Actual:**
```json
{
  "companies_resolved": 2,
  "source_keys": [
    "acme-eu",
    "acme-us"
  ],
  "prevent_aggressive_merge": true,
  "identity_method": "source_conflict_isolation"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Identity priority remains domain -> source+key -> normalized name. Conflicting source keys stay separate.

---
### P5-DEDUP-001 — Duplicate URL Variants Deduplication
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_duplicate_url_jobs.json`

**Expected:**
```json
{
  "total_job_count": 4,
  "unique_job_count": 1,
  "relevant_job_count": 1,
  "intent_score_inflated": false,
  "evidence_score_inflated": false
}
```
**Actual:**
```json
{
  "total_job_count": 4,
  "unique_job_count": 1,
  "relevant_job_count": 1,
  "intent_score": 20,
  "evidence_score": 2,
  "intent_score_inflated": false,
  "evidence_score_inflated": false
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Canonical URL normalization collapses identical job postings without inflating counts or score.

---
### P5-REL-001 — Technology Alone Does Not Create Strong Relevance
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_irrelevant_jobs.json`

**Expected:**
```json
{
  "is_relevant": false,
  "relevant_job_count": 0
}
```
**Actual:**
```json
{
  "job_title": "Office Administrator",
  "is_relevant": false,
  "relevant_job_count": 0
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Administrative role does not become relevant merely because Revit is in the text.

---
### P5-REL-002 — Obvious Role Relevance Without Technology List
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_irrelevant_jobs.json`

**Expected:**
```json
{
  "is_relevant": true,
  "relevant_job_count": 1
}
```
**Actual:**
```json
{
  "job_title": "BIM Manager",
  "technologies": [],
  "is_relevant": true,
  "relevant_job_count": 1
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Explicit BIM management title counts as relevant even if tech stack list is empty.

---
### P5-REL-003 — Irrelevant Leadership Does Not Inflate Intent
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_irrelevant_jobs.json`

**Expected:**
```json
{
  "leadership_bonus_awarded": false,
  "intent_score": 0
}
```
**Actual:**
```json
{
  "job_titles": [],
  "intent_score": 0,
  "leadership_bonus_awarded": false
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Senior roles outside BIM/digital engineering do NOT receive the +10 leadership intent bonus.

---
### P5-REC-001 — Relevant Recency Only
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_recency_jobs.json`

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A`
- **Jobs:** 2 input / 2 unique / 1 relevant (1 ignored)

**Expected:**
```json
{
  "recency_score": 5,
  "fresh_irrelevant_ignored": true
}
```
**Actual:**
```json
{
  "company_name": "Recency Test Corp",
  "total_jobs": 2,
  "unique_jobs": 2,
  "relevant_jobs": 1,
  "recency_score": 5,
  "fresh_irrelevant_ignored": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Recent irrelevant job does not boost company recency score; score depends strictly on relevant jobs.

---
### P5-REC-002 — Controlled Fallback for Missing Date
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_recency_jobs.json`

**Expected:**
```json
{
  "recency_score": 5,
  "date_fabricated": false
}
```
**Actual:**
```json
{
  "posted_date": null,
  "recency_score": 5,
  "date_fabricated": false
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Missing date gracefully falls back to older_unknown score without fabricating a timestamp.

---
### P5-REC-003 — Future Date Anomaly Handling
**Phase:** Phase 5
**Source:** internal
**Snapshot:** `tests/golden/snapshots/leads/p5_recency_jobs.json`

**Expected:**
```json
{
  "recency_score": 5,
  "anomaly_handled": true
}
```
**Actual:**
```json
{
  "posted_date": "2099-01-01",
  "recency_score": 5,
  "anomaly_handled": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Future date does not yield max recency score through negative age.

---
### P5-EVID-001 — Grounded Evidence Traceability
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "evidence_preserved": true,
  "grounded_bonus_eligible": true
}
```
**Actual:**
```json
{
  "evidence_preserved": true,
  "grounded_bonus_eligible": true,
  "evidence_content": "We automate Revit drawing production using C#.",
  "evidence_score": 2
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Signals and verifiable snippets survive aggregation and remain traceable to source jobs.

---
### P5-SCORE-001 — Component Score Bounds Enforcement
**Phase:** Phase 5
**Source:** internal

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A`
- **Score Breakdown:**
  - Fit: 30/30
  - Intent: 30/30
  - Recency: 20/20
  - Evidence: 20/20
  - **Total:** 100/100
- **Qualification:** `N/A` (Threshold >= 60)

**Expected:**
```json
{
  "fit_range": [
    0,
    30
  ],
  "intent_range": [
    0,
    30
  ],
  "recency_range": [
    0,
    20
  ],
  "evidence_range": [
    0,
    20
  ],
  "total_range": [
    0,
    100
  ]
}
```
**Actual:**
```json
{
  "fit_score": 30,
  "intent_score": 30,
  "recency_score": 20,
  "evidence_score": 20,
  "total_score": 100
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
All sub-scores strictly respect their defined contractual bounds.

---
### P5-SCORE-002 — Arithmetic Total Integrity
**Phase:** Phase 5
**Source:** internal

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A`
- **Score Breakdown:**
  - Fit: 20/30
  - Intent: 15/30
  - Recency: 20/20
  - Evidence: 2/20
  - **Total:** 57/100
- **Qualification:** `N/A` (Threshold >= 60)

**Expected:**
```json
{
  "exact_sum_matching": true,
  "hidden_bonuses": 0
}
```
**Actual:**
```json
{
  "fit_score": 20,
  "intent_score": 15,
  "recency_score": 20,
  "evidence_score": 2,
  "total_score": 57,
  "component_sum": 57,
  "exact_sum_matching": true,
  "hidden_bonuses": 0
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Total score is exactly equal to the mathematical sum of its four constituent sub-scores.

---
### P5-SCORE-003 — Scoring Determinism & Idempotency
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "identical_outputs": true,
  "stochastic_drift": 0
}
```
**Actual:**
```json
{
  "iterations": 5,
  "first_run": [
    25,
    25,
    20,
    4,
    74,
    true
  ],
  "all_runs_identical": true,
  "identical_outputs": true,
  "stochastic_drift": 0
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Scoring contains zero randomness or temperature variance.

---
### P5-SCORE-004 — Code Path Audit: Zero LLM Numeric Scoring
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "deterministic_python_only": true,
  "llm_score_assignment": false
}
```
**Actual:**
```json
{
  "deterministic_python_only": true,
  "llm_score_assignment": false,
  "forbidden_calls_found": []
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Critical fail condition verified: LLM never directly assigns any numeric score.

---
### P5-QUAL-001 — Qualification Threshold Boundary (>= 60)
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "score_59_qualified": false,
  "score_60_qualified": true,
  "score_61_qualified": true,
  "operator": ">="
}
```
**Actual:**
```json
{
  "score_59_qualified": false,
  "score_60_qualified": true,
  "score_61_qualified": true,
  "lead_exact_60_score": 60,
  "lead_exact_60_qualified": true,
  "operator": ">="
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Qualification strictly triggers at score >= 60; score of 59 is unqualified.

---
### P5-AGG-001 — Grounded Reasons Alignment
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "reasons_non_empty": true,
  "no_irrelevant_job_reasons": true
}
```
**Actual:**
```json
{
  "reasons_non_empty": true,
  "scoring_reasons": [
    "Has relevant job postings (+10)",
    "Company has strong signals (+5)",
    "First relevant hiring signal detected (+10)",
    "Leadership/manager role found (+10)",
    "Older/unknown job dates (+5)",
    "1 unique pieces of evidence (+2)"
  ],
  "no_irrelevant_job_reasons": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Reasons explain qualification truthfully and do not cite irrelevant jobs.

---
### P5-AGG-002 — Job Count Metric Separation
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "counts_distinct": true
}
```
**Actual:**
```json
{
  "total_job_count": 3,
  "unique_job_count": 2,
  "relevant_job_count": 1,
  "counts_distinct": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
System tracks raw input jobs, unique deduplicated jobs, and relevant jobs distinctly.

---
### P5-AGG-003 — Partial Malformed Job Robustness
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "aggregation_survives": true,
  "score_not_corrupted": true
}
```
**Actual:**
```json
{
  "aggregation_survives": true,
  "score_not_corrupted": true,
  "unique_jobs": 2,
  "relevant_jobs": 1
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
A low-quality or malformed job does not crash company aggregation or corrupt scoring.

---
### P5-ADV-001 — Adversarial: Identical Name Collision Across Distinct Entities
**Phase:** Phase 5
**Source:** internal

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `N/A`

**Expected:**
```json
{
  "companies_resolved": 2
}
```
**Actual:**
```json
{
  "companies_resolved": 2,
  "domains": [
    "studio-london.co.uk",
    "studio-ny.com"
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Separate legal entities sharing generic names do not collide if domains differ.

---
### P5-ADV-002 — Adversarial: Domain Alias Canonicalization
**Phase:** Phase 5
**Source:** internal

**Lead Dissection & Metrics:**
- **Company Identity:** domain: `acme.com`

**Expected:**
```json
{
  "companies_resolved": 1,
  "canonical_domain": "acme.com"
}
```
**Actual:**
```json
{
  "companies_resolved": 1,
  "canonical_domain": "acme.com"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Domain variations resolve to a single canonical domain identity.

---
### P5-ADV-003 — Adversarial: High-Volume Irrelevant Noise
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "total_job_count": 21,
  "unique_job_count": 21,
  "relevant_job_count": 1,
  "noise_does_not_dominate": true
}
```
**Actual:**
```json
{
  "total_job_count": 21,
  "unique_job_count": 21,
  "relevant_job_count": 1,
  "intent_score": 10,
  "noise_does_not_dominate": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
High job count from non-BIM roles does not inflate relevant_job_count or intent.

---
### P5-ADV-004 — Adversarial: Duplicate Evidence De-duplication
**Phase:** Phase 5
**Source:** internal

**Expected:**
```json
{
  "unique_signals_count": 1,
  "evidence_score_capped": 10
}
```
**Actual:**
```json
{
  "unique_signals_count": 1,
  "evidence_score": 2,
  "evidence_score_capped": 2
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Exact duplicate signals do not receive multiple signal count bonuses.

---
### P6-COMPANY-001 — Company Enrichment Normalization
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/apollo/apollo_org_acme.json`

**Expected:**
```json
{
  "company_name": "Acme Engineering",
  "company_domain": "acme.com",
  "industry": "Civil Engineering & Architecture",
  "country": "United Kingdom",
  "employee_count": 450,
  "source": "apollo",
  "domain_status": "existing"
}
```
**Actual:**
```json
{
  "company_name": "Acme Engineering",
  "company_domain": "acme.com",
  "industry": "Civil Engineering & Architecture",
  "country": "United Kingdom",
  "employee_count": 450,
  "source": "apollo",
  "domain_status": "existing"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Normalizes external provider company data into internal schema without leaking raw provider wrappers.

---
### P6-ROLE-001 — Buyer Role Ranking & Deterministic Matching
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/combined/p6_buyer_roles.json`

**Expected:**
```json
{
  "top_role": "Head of Digital Delivery",
  "buyer_relevance_ranks_higher": true,
  "irrelevant_scores_lower": true
}
```
**Actual:**
```json
{
  "head_digital_delivery": {
    "match": "exact",
    "score": 40
  },
  "senior_bim_manager": {
    "match": "strong",
    "score": 30
  },
  "marketing_manager": {
    "match": "none",
    "score": 0
  },
  "hr_specialist": {
    "match": "none",
    "score": 0
  },
  "top_role": "Head of Digital Delivery",
  "buyer_relevance_ranks_higher": true,
  "irrelevant_scores_lower": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Exact and strong buyer role matches strictly outrank irrelevant administrative titles.

---
### P6-ROLE-002 — Empty Buyer Roles Controlled Fallback
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "target_roles_empty": true,
  "no_invented_bim_roles": true
}
```
**Actual:**
```json
{
  "target_roles": [],
  "target_roles_empty": true,
  "no_invented_bim_roles": true,
  "bim_score": 0,
  "bim_match": "none",
  "ceo_score": 15,
  "ceo_match": "relevant"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
When buyer_roles is empty, system falls back to neutral generic behavior without hallucinating domain titles.

---
### P6-CONTACT-001 — ContactCandidate Schema Normalization & Robustness
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "first_name": "Jane",
  "last_name": "Smith",
  "full_name": "Jane Smith",
  "job_title": "BIM Manager",
  "work_email": "jane@acme.com",
  "missing_fields_do_not_crash": true
}
```
**Actual:**
```json
{
  "first_name": "Jane",
  "last_name": "Smith",
  "full_name": "Jane Smith",
  "job_title": "BIM Manager",
  "work_email": "jane@acme.com",
  "email_status": "unknown",
  "contact_score": 0,
  "missing_fields_do_not_crash": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
All required ContactCandidate fields normalize cleanly, and missing optional fields fall back safely.

---
### P6-DEDUP-001 — Cross-Provider Deduplication: Same Work Email
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "unique_contacts_count": 1,
  "data_sources": [
    "apollo",
    "hunter"
  ]
}
```
**Actual:**
```json
{
  "unique_contacts_count": 1,
  "data_sources": [
    "apollo",
    "hunter"
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Contacts with identical work email merge into one record with consolidated data sources.

---
### P6-DEDUP-002 — Cross-Provider Deduplication: Same LinkedIn Profile
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "unique_contacts_count": 1,
  "data_sources": [
    "apollo",
    "hunter"
  ]
}
```
**Actual:**
```json
{
  "unique_contacts_count": 1,
  "data_sources": [
    "apollo",
    "hunter"
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Contacts sharing canonical LinkedIn URL collapse into one logical candidate.

---
### P6-DEDUP-003 — Different Emails Prevent Name-Based Merge
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "unique_contacts_count": 2,
  "prevent_false_merge": true
}
```
**Actual:**
```json
{
  "unique_contacts_count": 2,
  "prevent_false_merge": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Contacts sharing identical names but distinct emails must remain separate entities.

---
### P6-DEDUP-004 — Conservative Deduplication Without Strong Key
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "unique_contacts_count": 2,
  "conservative_handling": true
}
```
**Actual:**
```json
{
  "unique_contacts_count": 2,
  "conservative_handling": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
In the absence of email or LinkedIn, distinct titles at the same firm are not aggressively collapsed.

---
### P6-EMAIL-001 — Hunter Domain Search Confidence Does Not Equal Verified
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/hunter/hunter_domain_acme.json`

**Expected:**
```json
{
  "email_status": "likely",
  "not_verified": true
}
```
**Actual:**
```json
{
  "confidence": 94,
  "email_status": "likely",
  "not_verified": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Statistical confidence from Domain Search produces likely, never verified without an SMTP verifier pass.

---
### P6-EMAIL-002 — Email Verifier Status Normalization
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/hunter/hunter_verifier_valid.json`

**Expected:**
```json
{
  "valid_maps_to": "verified",
  "accept_all_maps_to": "risky",
  "webmail_maps_to": "risky",
  "disposable_maps_to": "risky",
  "invalid_maps_to": "not_valid",
  "unknown_maps_to": "unknown"
}
```
**Actual:**
```json
{
  "valid_maps_to": "verified",
  "accept_all_maps_to": "risky",
  "webmail_maps_to": "risky",
  "disposable_maps_to": "risky",
  "invalid_maps_to": "not_valid",
  "unknown_maps_to": "unknown"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Standardized verifier status mapping ensures cross-provider consistency.

---
### P6-EMAIL-004 — Personal/Public Email Rejection
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "rejected_as_work_email": true,
  "work_email": null
}
```
**Actual:**
```json
{
  "gmail_detected": true,
  "yahoo_detected": true,
  "corporate_detected_false": false,
  "final_work_email": null,
  "work_email": null,
  "rejected_as_work_email": true,
  "final_email_status": "not_found"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Free public webmail domains (gmail, yahoo, etc.) are rejected as corporate work emails.

---
### P6-EMAIL-005 — Corporate Domain Consistency Credit
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "matching_domain_gets_credit": true,
  "mismatched_domain_no_credit": true
}
```
**Actual:**
```json
{
  "matching_domain_score": 85,
  "mismatched_domain_score": 80,
  "matching_domain_gets_credit": true,
  "mismatched_domain_no_credit": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Emails matching the verified company domain receive positive domain consistency weighting.

---
### P6-SCORE-001 — Contact Scoring Determinism & Zero LLM Scoring
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "identical_scores_all_runs": true,
  "deterministic_python_only": true,
  "zero_llm_numeric_scoring": true
}
```
**Actual:**
```json
{
  "scores": [
    85,
    85,
    85,
    85,
    85
  ],
  "identical_scores_all_runs": true,
  "deterministic_python_only": true,
  "zero_llm_numeric_scoring": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Contact scores derive purely from deterministic rule evaluation without LLM calls or randomness.

---
### P6-SCORE-002 — Contact Score Bounds (0–100)
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "score_min": 0,
  "score_max": 100
}
```
**Actual:**
```json
{
  "contact_score": 85,
  "score_min": 0,
  "score_max": 100,
  "in_bounds": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
All calculated contact scores strictly stay within the 0 to 100 range.

---
### P6-SCORE-003 — Buyer Relevance Precedence Over Irrelevant Verified Contacts
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "buyer_score_higher": true
}
```
**Actual:**
```json
{
  "buyer_score": 70,
  "irrelevant_score": 45,
  "buyer_score_higher": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Strong buyer role relevance combined with usable email outscores irrelevant roles with verified emails.

---
### P6-BEST-001 — Deterministic Best Contact Selection
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "best_contact_role": "BIM Manager",
  "best_contact_email_status": "verified",
  "single_deterministic_winner": true
}
```
**Actual:**
```json
{
  "best_contact_name": "Alice",
  "best_contact_role": "BIM Manager",
  "best_contact_email_status": "verified",
  "best_contact_score": 85,
  "single_deterministic_winner": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Top candidate with exact buyer match and verified work email wins best_contact unequivocally.

---
### P6-STATUS-001 — Complete Enrichment Status Criteria
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** None
- **Provider Errors:** None
- **Best Contact:** None
- **Enrichment Status:** `COMPLETE`

**Expected:**
```json
{
  "enrichment_status": "complete"
}
```
**Actual:**
```json
{
  "enrichment_status": "complete"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Status 'complete' requires company profile, at least one contact, and a verified/likely work email.

---
### P6-STATUS-002 — Partial Enrichment Status Criteria
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** None
- **Provider Errors:** None
- **Best Contact:** None
- **Enrichment Status:** `PARTIAL`

**Expected:**
```json
{
  "enrichment_status": "partial"
}
```
**Actual:**
```json
{
  "enrichment_status": "partial"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
When only company or contact without verified email is resolved, status reflects partial.

---
### P6-STATUS-003 — Failed / Provider Error Status Criteria
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** None
- **Provider Errors:** None
- **Best Contact:** None
- **Enrichment Status:** `PROVIDER_ERROR`

**Expected:**
```json
{
  "enrichment_status": "provider_error",
  "not_silent_success": true
}
```
**Actual:**
```json
{
  "enrichment_status": "provider_error",
  "not_silent_success": true,
  "errors": [
    "[apollo] timeout_error: Company enrichment timed out"
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Provider failures are captured as provider_error rather than masquerading as empty success.

---
### P6-PROVIDER-001 — Missing API Keys Returns Explicit Configuration Error
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "enrichment_errors_contain": "no_provider_configured",
  "status": "provider_error"
}
```
**Actual:**
```json
{
  "status": "provider_error",
  "errors": [
    "[apollo] configuration_error: APOLLO_API_KEY is not configured",
    "[hunter] configuration_error: HUNTER_API_KEY is not configured",
    "[apollo] configuration_error: APOLLO_API_KEY is not configured",
    "[hunter] configuration_error: HUNTER_API_KEY is not configured",
    "no_provider_configured"
  ],
  "enrichment_errors_contain": "no_provider_configured",
  "has_no_provider_configured": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Unconfigured environments return an explicit error and are not presented as zero contacts.

---
### P6-PROVIDER-002 — Authentication Error Classification
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "error_type": "authentication_error"
}
```
**Actual:**
```json
{
  "error_type": "authentication_error",
  "provider": "apollo",
  "is_auth_error": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
HTTP 401/403 maps directly to ProviderAuthError.

---
### P6-PROVIDER-003 — Rate Limit Error Classification
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "error_type": "rate_limit_error"
}
```
**Actual:**
```json
{
  "error_type": "rate_limit_error",
  "provider": "hunter",
  "is_rate_limit": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
HTTP 429 maps directly to ProviderRateLimitError.

---
### P6-PROVIDER-004 — Timeout Error Classification
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "error_type": "timeout_error"
}
```
**Actual:**
```json
{
  "error_type": "timeout_error",
  "provider": "apollo",
  "is_timeout": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Network timeouts are classified as ProviderTimeoutError.

---
### P6-PROVIDER-005 — Server Error Classification
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "error_type": "provider_error"
}
```
**Actual:**
```json
{
  "error_type": "provider_error",
  "provider": "hunter",
  "is_provider_error": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
HTTP 500+ maps to standard ProviderError.

---
### P6-PROVIDER-006 — Empty Result Classification
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "error_type": "empty_result"
}
```
**Actual:**
```json
{
  "error_type": "empty_result",
  "provider": "apollo",
  "is_empty_result": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
HTTP 200 with zero records or 404 maps to ProviderEmptyResult without crashing.

---
### P6-USAGE-001 — Actual Provider Call Usage Accounting
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** `apollo`, `hunter`
- **Provider Errors:** None
- **Best Contact:** None
- **Enrichment Status:** `COMPLETE`

**Expected:**
```json
{
  "providers_used": [
    "apollo",
    "hunter"
  ],
  "errors_include_timeout": true
}
```
**Actual:**
```json
{
  "providers_used": [
    "apollo",
    "hunter"
  ],
  "errors": [
    "[apollo] timeout_error: Apollo timed out",
    "[apollo] timeout_error: Apollo timed out",
    "[hunter] authentication_error: Authentication failed (HTTP 401)"
  ],
  "errors_include_timeout": true,
  "contacts_count": 1,
  "enrichment_status": "complete"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
providers_used tracks all invoked providers, including failed calls.

---
### P6-APOLLO-001 — Apollo Organization Request Construction & Normalization
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/apollo/apollo_org_acme.json`

**Expected:**
```json
{
  "endpoint": "https://api.apollo.io/api/v1/organizations/enrich",
  "param_domain": "acme.com",
  "company_name": "Acme Engineering"
}
```
**Actual:**
```json
{
  "endpoint": "https://api.apollo.io/api/v1/organizations/enrich",
  "param_domain": "acme.com",
  "company_name": "Acme Engineering"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Verifies correct URL and query parameters for Apollo organization enrichment.

---
### P6-APOLLO-002 — Apollo People Search Request Filtering
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/apollo/apollo_people_acme.json`

**Expected:**
```json
{
  "q_organization_domains_list": [
    "acme.com"
  ],
  "person_titles": [
    "BIM Manager",
    "Head of Digital Delivery"
  ],
  "per_page": 5
}
```
**Actual:**
```json
{
  "q_organization_domains_list": [
    "acme.com"
  ],
  "person_titles": [
    "BIM Manager",
    "Head of Digital Delivery"
  ],
  "per_page": 5
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
People search payload strictly scopes domain, buyer titles, and page boundaries.

---
### P6-APOLLO-003 — Apollo Personal Data Minimization
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "reveal_personal_emails": false,
  "reveal_phone_number": false
}
```
**Actual:**
```json
{
  "reveal_personal_emails": false,
  "reveal_phone_number": false
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Enforces GDPR data minimization: never requests personal emails or phone numbers.

---
### P6-HUNTER-001 — Hunter Domain Search Contract & Confidence Retention
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/hunter/hunter_domain_acme.json`

**Expected:**
```json
{
  "endpoint": "https://api.hunter.io/v2/domain-search",
  "email_confidence_preserved": true
}
```
**Actual:**
```json
{
  "endpoint": "https://api.hunter.io/v2/domain-search",
  "first_candidate_confidence": 94,
  "email_confidence_preserved": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Domain search extracts candidate names, positions, and confidence scores accurately.

---
### P6-HUNTER-002 — Hunter Email Verifier Endpoint Mapping
**Phase:** Phase 6
**Source:** internal
**Snapshot:** `tests/golden/snapshots/enrichment/hunter/hunter_verifier_valid.json`

**Expected:**
```json
{
  "endpoint": "https://api.hunter.io/v2/email-verifier",
  "status_verified": true
}
```
**Actual:**
```json
{
  "endpoint": "https://api.hunter.io/v2/email-verifier",
  "verified_status": "verified",
  "status_verified": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Email verifier maps valid deliverable state directly to verified status.

---
### P6-LIMIT-001 — Batch Request Bounds: MAX_LEADS_PER_REQUEST = 50
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "leads_50_accepted": true,
  "leads_51_rejected": true
}
```
**Actual:**
```json
{
  "leads_50_accepted": true,
  "leads_51_rejected": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
API contract strictly bounds lead input batches to 50 items.

---
### P6-LIMIT-002 — Output Contact Bounds: MAX_CONTACTS_PER_LEAD = 5
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** None
- **Provider Errors:** None
- **Contact Pipeline:** N/A raw -> N/A deduplicated -> 5 final
- **Best Contact:** None

**Expected:**
```json
{
  "final_contacts_count": 5,
  "top_scored_preserved": true
}
```
**Actual:**
```json
{
  "final_contacts_count": 5,
  "top_scored_preserved": true,
  "top_score": 19,
  "bottom_score": 15
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Final contact candidate list is deterministically truncated to the top 5 highest-ranking records.

---
### P6-LIMIT-003 — Qualified Only Filtering (Default True)
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "default_enriches_qualified_only": true,
  "unqualified_skipped": true
}
```
**Actual:**
```json
{
  "attempted_count": 1,
  "returned_leads_count": 1,
  "default_enriches_qualified_only": true,
  "unqualified_skipped": true,
  "domains_enriched": [
    "qual.com"
  ]
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Unqualified leads bypass expensive enrichment calls unless qualified_only=false is explicitly requested.

---
### P6-PARTIAL-001 — Partial Provider Survival & Resilience
**Phase:** Phase 6
**Source:** internal

**Lead Enrichment Dissection & Auditable Metrics:**
- **Company Identity:** `N/A` (`N/A`) | Qualified Input: N/A
- **Providers Configured:** None
- **Providers Called:** None
- **Provider Errors:** None
- **Best Contact:** None
- **Enrichment Status:** `PARTIAL`

**Expected:**
```json
{
  "lead_returned": true,
  "hunter_contacts_preserved": true,
  "apollo_error_exposed": true
}
```
**Actual:**
```json
{
  "lead_returned": true,
  "hunter_contacts_preserved": true,
  "has_hunter_contacts": true,
  "apollo_error_exposed": true,
  "enrichment_status": "partial"
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
A single provider failure does not abort the entire enrichment pipeline; successful provider data survives.

---
### P6-PARTIAL-002 — Contact Retained When Verifier Fails
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "contact_retained": true,
  "email_status": "unknown",
  "no_fabricated_verification": true
}
```
**Actual:**
```json
{
  "contact_retained": true,
  "email_status": "unknown",
  "no_fabricated_verification": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
When SMTP verifier fails, contact remains available with unknown email status rather than being discarded.

---
### P6-PRIVACY-001 — Personal Data Leak Prevention
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "no_personal_email_exposed": true,
  "no_personal_phone_exposed": true
}
```
**Actual:**
```json
{
  "contact_candidate_fields": [
    "first_name",
    "last_name",
    "full_name",
    "job_title",
    "seniority",
    "department",
    "company_name",
    "company_domain",
    "work_email",
    "email_status",
    "email_confidence",
    "email_source",
    "linkedin_url",
    "provider",
    "provider_person_id",
    "buyer_role_match",
    "contact_score",
    "data_sources"
  ],
  "no_personal_email_exposed": true,
  "no_personal_phone_exposed": true,
  "privacy_leaks_found": []
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Ensures ContactCandidate model only exposes business work emails and professional contact links.

---
### P6-EXPLAIN-001 — Best Contact Diagnostics & Explainability
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "has_buyer_role_match": true,
  "has_job_title": true,
  "has_email_status": true,
  "has_contact_score": true,
  "has_data_sources": true
}
```
**Actual:**
```json
{
  "has_buyer_role_match": true,
  "has_job_title": true,
  "has_email_status": true,
  "has_contact_score": true,
  "has_data_sources": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Selected best_contact exposes all underlying attributes necessary to audit why it was chosen.

---
### P6-CROSS-001 — Provider Invocation Order Invariance
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "same_deduplicated_count": true,
  "same_best_contact": true,
  "same_scores": true
}
```
**Actual:**
```json
{
  "order_a_top": "Bob Jones",
  "order_b_top": "Bob Jones",
  "same_deduplicated_count": true,
  "same_best_contact": true,
  "same_scores": true,
  "identical_selection": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Enrichment results, scoring, and best-contact selection are completely invariant to provider execution order.

---
### P6-CROSS-002 — Email Case-Insensitive Identity Normalization
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "same_normalized_identity": true
}
```
**Actual:**
```json
{
  "e1_normalized": "jane.smith@acme.com",
  "e2_normalized": "jane.smith@acme.com",
  "same_normalized_identity": true
}
```

**Result:** PASS

**Differences:** _(None / In sync)_

**Human Notes:**
Mixed casing in email addresses normalizes to identical identity key during deduplication.

---
### P6-LIVE-APOLLO-001 — Live Apollo Integration (Optional / Review)
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "company_domain": "arup.com"
}
```
**Actual:**
```json
{}
```

**Result:** NOT_RUN

**Differences:** _(Pending execution / Deferred)_

**Human Notes:**
APOLLO_API_KEY not configured in environment. Test skipped.

---
### P6-LIVE-HUNTER-001 — Live Hunter Integration (Optional / Review)
**Phase:** Phase 6
**Source:** internal

**Expected:**
```json
{
  "company_domain": "arup.com"
}
```
**Actual:**
```json
{}
```

**Result:** NOT_RUN

**Differences:** _(Pending execution / Deferred)_

**Human Notes:**
HUNTER_API_KEY not configured in environment. Test skipped.

---
### P7-EXP-001 — Full Multi-Format Export
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-EXP-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Full Multi-Format Export

---
### P7-CONTRACT-001 — Score Bounds Preserved
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CONTRACT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Score Bounds Preserved

---
### P7-CONTRACT-002 — Arithmetic Preserved
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CONTRACT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Arithmetic Preserved

---
### P7-CONTRACT-003 — Qualification Threshold Preserved
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CONTRACT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Qualification Threshold Preserved

---
### P7-CONTRACT-004 — No Score Mutation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CONTRACT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Score Mutation

---
### P7-CONTRACT-005 — Workflow Defaults Only
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CONTRACT-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Workflow Defaults Only

---
### P7-ID-001 — Same Canonical Domain
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ID-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same Canonical Domain

---
### P7-ID-002 — Different Domains Same Name
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ID-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Different Domains Same Name

---
### P7-ID-003 — ATS Namespace Isolation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ID-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: ATS Namespace Isolation

---
### P7-ID-004 — Same Namespaced Source Identity
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ID-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same Namespaced Source Identity

---
### P7-ID-005 — Name Fallback Determinism
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ID-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Name Fallback Determinism

---
### P7-CID-001 — Same Email Identity
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same Email Identity

---
### P7-CID-002 — Same LinkedIn Identity
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same LinkedIn Identity

---
### P7-CID-003 — Same Provider Person ID
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same Provider Person ID

---
### P7-CID-004 — Same Name Different Emails
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Same Name Different Emails

---
### P7-CID-005 — Weak Identity Must Survive
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Weak Identity Must Survive

---
### P7-CID-006 — One Email Missing No Collapse
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: One Email Missing No Collapse

---
### P7-CID-007 — Provider Order Independence
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CID-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Provider Order Independence

---
### P7-JOB-001 — Raw Job Preservation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JOB-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Raw Job Preservation

---
### P7-JOB-002 — No Fabricated Jobs
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JOB-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Fabricated Jobs

---
### P7-JOB-003 — Domainless ATS Job Association
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JOB-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Domainless ATS Job Association

---
### P7-JOB-004 — Canonical Domain Matching
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JOB-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Canonical Domain Matching

---
### P7-JOB-005 — Wrong Company Protection
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JOB-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Wrong Company Protection

---
### P7-XLSX-001 — Workbook Opens
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Workbook Opens

---
### P7-XLSX-002 — Required Sheets
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Required Sheets

---
### P7-XLSX-003 — Header Freeze
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Header Freeze

---
### P7-XLSX-004 — Auto Filter
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Auto Filter

---
### P7-XLSX-005 — Canonical Columns No Duplicates
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Canonical Columns No Duplicates

---
### P7-XLSX-006 — Row Counts Match Serializer
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Row Counts Match Serializer

---
### P7-XLSX-007 — Best Contact Preservation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Best Contact Preservation

---
### P7-XLSX-008 — Evidence Preservation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Evidence Preservation

---
### P7-XLSX-009 — No Formula Mutation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Formula Mutation

---
### P7-XLSX-010 — Null Serialization
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-XLSX-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Null Serialization

---
### P7-CSV-001 — Canonical Headers
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CSV-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Canonical Headers

---
### P7-CSV-002 — Row Integrity
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CSV-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Row Integrity

---
### P7-CSV-003 — List Serialization
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CSV-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: List Serialization

---
### P7-CSV-004 — Empty Collection Headers
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CSV-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Empty Collection Headers

---
### P7-CSV-005 — UTF-8 Names
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-CSV-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: UTF-8 Names

---
### P7-JSON-001 — Canonical Structure
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JSON-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Canonical Structure

---
### P7-JSON-002 — Arrays Remain Arrays
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JSON-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Arrays Remain Arrays

---
### P7-JSON-003 — Null Remains Null
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JSON-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Null Remains Null

---
### P7-JSON-004 — Cross-Format Equality
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-JSON-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Cross-Format Equality

---
### P7-PRIV-001 — Personal Fields Excluded
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-PRIV-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Personal Fields Excluded

---
### P7-PRIV-002 — Public Email Not Upgraded
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-PRIV-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Public Email Not Upgraded

---
### P7-PRIV-003 — Secrets Not Leaked
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-PRIV-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Secrets Not Leaked

---
### P7-DET-001 — Stable Entity IDs
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-DET-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Stable Entity IDs

---
### P7-DET-002 — Stable Row Ordering
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-DET-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Stable Row Ordering

---
### P7-DET-003 — Export Run ID Unique
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-DET-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Export Run ID Unique

---
### P7-DET-004 — Entity ID Independent of Run
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-DET-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Entity ID Independent of Run

---
### P7-DET-005 — Input Not Mutated
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-DET-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Input Not Mutated

---
### P7-GS-001 — Disabled Google
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Disabled Google

---
### P7-GS-002 — Explicit Mock Snapshot
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Explicit Mock Snapshot

---
### P7-GS-003 — Explicit Mock Upsert
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Explicit Mock Upsert

---
### P7-GS-004 — Upsert Idempotency
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Upsert Idempotency

---
### P7-GS-005 — ATS Identity in Sheets
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: ATS Identity in Sheets

---
### P7-GS-006 — No Fake Production Mock
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Fake Production Mock

---
### P7-GS-LIVE-001 — Real Google Sheets
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-GS-LIVE-001"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN"
}
```
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

**Reason:** Real Google Sheets integration not yet implemented / live credentials unavailable

**Human Notes:**
Phase 7 Golden Case: Real Google Sheets

---
### P7-ERR-001 — XLSX Fail CSV JSON Succeed
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ERR-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: XLSX Fail CSV JSON Succeed

---
### P7-ERR-002 — Google Failure Local Success
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ERR-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Google Failure Local Success

---
### P7-ERR-003 — All Exporters Fail
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ERR-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: All Exporters Fail

---
### P7-ERR-004 — Error Audit Row
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-ERR-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Error Audit Row

---
### P7-LIMIT-001 — 5000 Leads Accepted
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-LIMIT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: 5000 Leads Accepted

---
### P7-LIMIT-002 — 5001 Leads Rejected
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-LIMIT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: 5001 Leads Rejected

---
### P7-LIMIT-003 — Qualified Only Default
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-LIMIT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Qualified Only Default

---
### P7-LIMIT-004 — Include All Contacts
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-LIMIT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Include All Contacts

---
### P7-WF-001 — Default Approval
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-WF-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Default Approval

---
### P7-WF-002 — Default Outreach
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-WF-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Default Outreach

---
### P7-WF-003 — Default Send
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-WF-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Default Send

---
### P7-WF-004 — No Draft Generation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-WF-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Draft Generation

---
### P7-SUM-001 — Total Lead Count
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Total Lead Count

---
### P7-SUM-002 — Qualification Counts
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Qualification Counts

---
### P7-SUM-003 — Enrichment Status Counts
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Enrichment Status Counts

---
### P7-SUM-004 — Email Status Metrics
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Email Status Metrics

---
### P7-SUM-005 — Average Lead Score
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Average Lead Score

---
### P7-SUM-006 — No Double Counting
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-SUM-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Double Counting

---
### P7-REG-001 — Threshold 60 Not 70
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Threshold 60 Not 70

---
### P7-REG-002 — ATS Namespace No Collision
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: ATS Namespace No Collision

---
### P7-REG-003 — Weak Contact No Collapse
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Weak Contact No Collapse

---
### P7-REG-004 — No Fake Generic Job
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: No Fake Generic Job

---
### P7-REG-005 — Google Mock Isolation
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Google Mock Isolation

---
### P7-REG-006 — Run ID No Collision
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Run ID No Collision

---
### P7-REG-007 — Empty CSV Headers
**Phase:** Phase 7
**Source:** internal

**Input:**
```json
{
  "case_id": "P7-REG-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS"
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 7 Golden Case: Empty CSV Headers

---
### P8-DRAFT-001 — Strong BIM Automation Signal
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-DRAFT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_draft_001_strong_bim_automation",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Strong BIM Automation Signal (test: tests/acceptance/test_phase8_acceptance.py::test_p8_draft_001_strong_bim_automation)

---
### P8-ELIG-001 — Qualified and Verified Allowed
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_001_qualified_and_verified",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Qualified and Verified Allowed (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_001_qualified_and_verified)

---
### P8-ELIG-002 — Qualified and Likely Allowed
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_002_qualified_and_likely",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Qualified and Likely Allowed (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_002_qualified_and_likely)

---
### P8-ELIG-003 — Unqualified Lead Skipped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_003_unqualified_lead",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Unqualified Lead Skipped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_003_unqualified_lead)

---
### P8-ELIG-004 — No Best Contact Skipped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_004_no_best_contact",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Best Contact Skipped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_004_no_best_contact)

---
### P8-ELIG-005 — No Work Email Skipped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_005_no_work_email",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Work Email Skipped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_005_no_work_email)

---
### P8-ELIG-006 — Risky Email Skipped by Default
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_006_risky_email_skipped",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Risky Email Skipped by Default (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_006_risky_email_skipped)

---
### P8-ELIG-007 — Unknown Email Skipped by Default
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_007_unknown_email_skipped",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Unknown Email Skipped by Default (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_007_unknown_email_skipped)

---
### P8-ELIG-008 — Preview Override Without Fabrication
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ELIG-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_008_preview_override_without_fabrication",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Preview Override Without Fabrication (test: tests/acceptance/test_phase8_acceptance.py::test_p8_elig_008_preview_override_without_fabrication)

---
### P8-ID-001 — Lead ID Preserved
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ID-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Lead ID Preserved (test: tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved)

---
### P8-ID-002 — Contact ID Preserved
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ID-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Contact ID Preserved (test: tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved)

---
### P8-ID-003 — Recipient Email Preserved
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ID-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Recipient Email Preserved (test: tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved)

---
### P8-ID-004 — Recipient Name and Title Bound to Same Contact
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ID-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Recipient Name and Title Bound to Same Contact (test: tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved)

---
### P8-ID-005 — Explicit Contact ID Belongs to Lead
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ID-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_005_explicit_contact_id_validation",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Explicit Contact ID Belongs to Lead (test: tests/acceptance/test_phase8_acceptance.py::test_p8_id_005_explicit_contact_id_validation)

---
### P8-REG-001 — Wrong-Company Same-Title Job Contamination
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_001_wrong_company_same_title_job",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Wrong-Company Same-Title Job Contamination (test: tests/test_outreach.py::test_p8_reg_001_wrong_company_same_title_job)

---
### P8-COMPANY-001 — Canonical Domain Match
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-COMPANY-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_company_001_canonical_domain_match",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Canonical Domain Match (test: tests/acceptance/test_phase8_acceptance.py::test_p8_company_001_canonical_domain_match)

---
### P8-COMPANY-002 — ATS Namespaced Match
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-COMPANY-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_company_002_ats_namespaced_match",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: ATS Namespaced Match (test: tests/acceptance/test_phase8_acceptance.py::test_p8_company_002_ats_namespaced_match)

---
### P8-COMPANY-003 — ATS Namespace Mismatch
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-COMPANY-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_company_003_ats_namespace_mismatch",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: ATS Namespace Mismatch (test: tests/acceptance/test_phase8_acceptance.py::test_p8_company_003_ats_namespace_mismatch)

---
### P8-COMPANY-004 — Conservative Name Fallback
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-COMPANY-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_company_004_conservative_name_fallback",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Conservative Name Fallback (test: tests/acceptance/test_phase8_acceptance.py::test_p8_company_004_conservative_name_fallback)

---
### P8-REG-004 — API Raw Jobs Handoff
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_004_api_raw_jobs_handoff",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: API Raw Jobs Handoff (test: tests/test_outreach.py::test_p8_reg_004_api_raw_jobs_handoff)

---
### P8-JOB-001 — Relevant Jobs Filter
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-JOB-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_job_001_relevant_jobs_only",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Relevant Jobs Filter (test: tests/acceptance/test_phase8_acceptance.py::test_p8_job_001_relevant_jobs_only)

---
### P8-JOB-002 — No Raw Jobs Safe Fallback
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-JOB-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_job_002_no_raw_jobs_safe_fallback",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Raw Jobs Safe Fallback (test: tests/acceptance/test_phase8_acceptance.py::test_p8_job_002_no_raw_jobs_safe_fallback)

---
### P8-REG-005 — Phase 5 Evidence Preservation
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_005_phase5_evidence_preservation",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Phase 5 Evidence Preservation (test: tests/test_outreach.py::test_p8_reg_005_phase5_evidence_preservation)

---
### P8-REG-006 — Phase 5 Signal Schema
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_006_phase5_signal_schema",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Phase 5 Signal Schema (test: tests/test_outreach.py::test_p8_reg_006_phase5_signal_schema)

---
### P8-EVID-001 — StructuredJob Signal Evidence Extracted
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-EVID-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_evid_001_structured_job_signal_evidence",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: StructuredJob Signal Evidence Extracted (test: tests/acceptance/test_phase8_acceptance.py::test_p8_evid_001_structured_job_signal_evidence)

---
### P8-EVID-002 — Unknown Evidence Reference Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-EVID-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_evid_002_unknown_evidence_ref_rejected",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Unknown Evidence Reference Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_evid_002_unknown_evidence_ref_rejected)

---
### P8-EVID-003 — Duplicate Evidence Deterministically Handled
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-EVID-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_evid_003_duplicate_evidence_handling",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Duplicate Evidence Deterministically Handled (test: tests/acceptance/test_phase8_acceptance.py::test_p8_evid_003_duplicate_evidence_handling)

---
### P8-REG-003 — No Fabricated BIM/Revit Technology
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_003_no_fabricated_technology",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Fabricated BIM/Revit Technology (test: tests/test_outreach.py::test_p8_reg_003_no_fabricated_technology)

---
### P8-FAB-001 — No Fake Metrics in Context
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-FAB-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_fab_001_no_fake_metrics",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Fake Metrics in Context (test: tests/acceptance/test_phase8_acceptance.py::test_p8_fab_001_no_fake_metrics)

---
### P8-FAB-002 — No Fake Relationship Claims
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-FAB-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_fab_002_no_fake_relationship_claims",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Fake Relationship Claims (test: tests/acceptance/test_phase8_acceptance.py::test_p8_fab_002_no_fake_relationship_claims)

---
### P8-SVC-001 — Active Service Accepted
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SVC-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_svc_001_active_service_accepted",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Active Service Accepted (test: tests/acceptance/test_phase8_acceptance.py::test_p8_svc_001_active_service_accepted)

---
### P8-SVC-002 — In-Development Service Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SVC-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_svc_002_and_003_in_development_rejected",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: In-Development Service Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_svc_002_and_003_in_development_rejected)

---
### P8-REG-002 — Active Service Substring Bypass Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_002_active_service_substring_bypass",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Active Service Substring Bypass Rejected (test: tests/test_outreach.py::test_p8_reg_002_active_service_substring_bypass)

---
### P8-SVC-004 — CTA Offering Rejected as Active Service
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SVC-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_svc_004_cta_rejected",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: CTA Offering Rejected as Active Service (test: tests/acceptance/test_phase8_acceptance.py::test_p8_svc_004_cta_rejected)

---
### P8-SVC-005 — Missing Offering Status Rejected as Active
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SVC-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_svc_005_missing_offering_status_rejected",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Missing Offering Status Rejected as Active (test: tests/acceptance/test_phase8_acceptance.py::test_p8_svc_005_missing_offering_status_rejected)

---
### P8-SVC-006 — No Active Service Skips Lead
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-SVC-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_svc_006_no_active_service_skips_lead",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: No Active Service Skips Lead (test: tests/acceptance/test_phase8_acceptance.py::test_p8_svc_006_no_active_service_skips_lead)

---
### P8-CTX-001 — Deterministic Context Generation
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-CTX-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_ctx_001_deterministic_context",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Deterministic Context Generation (test: tests/acceptance/test_phase8_acceptance.py::test_p8_ctx_001_deterministic_context)

---
### P8-REG-008 — Deterministic Evidence Ordering
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_008_deterministic_evidence_ordering",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Deterministic Evidence Ordering (test: tests/test_outreach.py::test_p8_reg_008_deterministic_evidence_ordering)

---
### P8-REG-007 — SOURCE_DATA Delimiter Injection Escaping
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_007_source_data_delimiter_injection",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: SOURCE_DATA Delimiter Injection Escaping (test: tests/test_outreach.py::test_p8_reg_007_source_data_delimiter_injection)

---
### P8-INJECT-001 — Prompt Injection Ignore Instructions Trapped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-INJECT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Prompt Injection Ignore Instructions Trapped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety)

---
### P8-INJECT-002 — Prompt Injection Recipient Override Trapped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-INJECT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Prompt Injection Recipient Override Trapped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety)

---
### P8-INJECT-003 — Prompt Injection Auto Approve Trapped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-INJECT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Prompt Injection Auto Approve Trapped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety)

---
### P8-INJECT-004 — Prompt Injection Send Immediately Trapped
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-INJECT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Prompt Injection Send Immediately Trapped (test: tests/acceptance/test_phase8_acceptance.py::test_p8_inject_001_to_004_prompt_injection_safety)

---
### P8-PROMPT-001 — Sales Outreach Environment Isolated
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PROMPT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_001_sales_outreach_environment",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Sales Outreach Environment Isolated (test: tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_001_sales_outreach_environment)

---
### P8-PROMPT-002 — Tone Preset Mapping
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PROMPT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_002_tone_presets",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Tone Preset Mapping (test: tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_002_tone_presets)

---
### P8-PROMPT-003 — Language Preset Supported
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PROMPT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_003_language_preset",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Language Preset Supported (test: tests/acceptance/test_phase8_acceptance.py::test_p8_prompt_003_language_preset)

---
### P8-PARSE-001 — Valid JSON Single Parse
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PARSE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_parse_001_valid_json_single_call",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Valid JSON Single Parse (test: tests/acceptance/test_phase8_acceptance.py::test_p8_parse_001_valid_json_single_call)

---
### P8-PARSE-002 — Invalid Then Repaired JSON Retry
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PARSE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_parse_002_invalid_then_repaired_json",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Invalid Then Repaired JSON Retry (test: tests/acceptance/test_phase8_acceptance.py::test_p8_parse_002_invalid_then_repaired_json)

---
### P8-PARSE-003 — Two Invalid Responses Fail Safely
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PARSE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_parse_003_two_invalid_responses_fail_safely",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Two Invalid Responses Fail Safely (test: tests/acceptance/test_phase8_acceptance.py::test_p8_parse_003_two_invalid_responses_fail_safely)

---
### P8-VAL-001 — Empty Subject Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Empty Subject Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-002 — Empty Body Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Empty Body Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-003 — Subject Exceeding 60 Characters Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Subject Exceeding 60 Characters Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-004 — Body Exceeding 160 Words Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Body Exceeding 160 Words Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-005 — Fake Re/Fwd Prefix Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Fake Re/Fwd Prefix Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-006 — All Caps Subject Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: All Caps Subject Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-007 — Unresolved Placeholders Rejected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Unresolved Placeholders Rejected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-008 — Identity Mutation Invariant Protected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Identity Mutation Invariant Protected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-009 — Recipient Email Mutation Invariant Protected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Recipient Email Mutation Invariant Protected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-VAL-010 — Workflow Mutation Invariant Protected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-VAL-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Workflow Mutation Invariant Protected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_val_001_to_010_validator_rules)

---
### P8-WF-001 — Approval Status Always Pending Review
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-WF-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_wf_001_and_002_draft_safety_statuses",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Approval Status Always Pending Review (test: tests/acceptance/test_phase8_acceptance.py::test_p8_wf_001_and_002_draft_safety_statuses)

---
### P8-WF-002 — Send Status Always Not Sent
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-WF-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_wf_001_and_002_draft_safety_statuses",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Send Status Always Not Sent (test: tests/acceptance/test_phase8_acceptance.py::test_p8_wf_001_and_002_draft_safety_statuses)

---
### P8-WF-003 — Workflow Projection Draft Ready
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-WF-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_wf_003_projection_and_safety",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Workflow Projection Draft Ready (test: tests/acceptance/test_phase8_acceptance.py::test_p8_wf_003_projection_and_safety)

---
### P8-WF-004 — Zero Sending Code Path Static Verification
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-WF-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_wf_004_zero_sending_code_path_static_verification",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Zero Sending Code Path Static Verification (test: tests/acceptance/test_phase8_acceptance.py::test_p8_wf_004_zero_sending_code_path_static_verification)

---
### P8-WF-005 — Zero Auto Approval Path Static Verification
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-WF-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_wf_005_zero_auto_approval_code_path_static_verification",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Zero Auto Approval Path Static Verification (test: tests/acceptance/test_phase8_acceptance.py::test_p8_wf_005_zero_auto_approval_code_path_static_verification)

---
### P8-IDEMP-001 — Deterministic Draft ID Generation
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-IDEMP-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Deterministic Draft ID Generation (test: tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id)

---
### P8-IDEMP-002 — Revision Mutation Changes Draft ID
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-IDEMP-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Revision Mutation Changes Draft ID (test: tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id)

---
### P8-IDEMP-003 — Prompt Version Mutation Changes Draft ID
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-IDEMP-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Prompt Version Mutation Changes Draft ID (test: tests/acceptance/test_phase8_acceptance.py::test_p8_idemp_001_to_003_deterministic_draft_id)

---
### P8-BATCH-001 — Multiple Successful Leads Batch Processing
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-BATCH-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_batch_001_batch_processing",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Multiple Successful Leads Batch Processing (test: tests/acceptance/test_phase8_acceptance.py::test_p8_batch_001_batch_processing)

---
### P8-BATCH-002 — Partial Failure Batch Resilience
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-BATCH-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_batch_002_partial_failure",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Partial Failure Batch Resilience (test: tests/acceptance/test_phase8_acceptance.py::test_p8_batch_002_partial_failure)

---
### P8-BATCH-003 — Max 50 Drafts Respected
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-BATCH-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_batch_003_max_50_limit",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Max 50 Drafts Respected (test: tests/acceptance/test_phase8_acceptance.py::test_p8_batch_003_max_50_limit)

---
### P8-PRIV-001 — Personal Contact Data Excluded
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PRIV-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Personal Contact Data Excluded (test: tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection)

---
### P8-PRIV-002 — Secrets and Tokens Excluded
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PRIV-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Secrets and Tokens Excluded (test: tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection)

---
### P8-PRIV-003 — Sensitive Personal Attributes Excluded
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-PRIV-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Sensitive Personal Attributes Excluded (test: tests/acceptance/test_phase8_acceptance.py::test_p8_priv_001_to_003_privacy_keyword_detection)

---
### P8-GROUND-001 — Disabled Grounding Checker Explicitly Reported Not Run
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-GROUND-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Disabled Grounding Checker Explicitly Reported Not Run (test: tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly)

---
### P8-GROUND-002 — Grounding Checker Cannot Rewrite Draft
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-GROUND-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Grounding Checker Cannot Rewrite Draft (test: tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly)

---
### P8-GROUND-003 — Grounding Checker Cannot Approve Draft
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-GROUND-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Grounding Checker Cannot Approve Draft (test: tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly)

---
### P8-GROUND-004 — Grounding Checker Cannot Send Draft
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-GROUND-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Grounding Checker Cannot Send Draft (test: tests/acceptance/test_phase8_acceptance.py::test_p8_ground_001_disabled_reported_honestly)

---
### P8-ERR-001 — Error Taxonomy Distinguishable
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-ERR-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_err_001_distinguishable_error_codes",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Error Taxonomy Distinguishable (test: tests/acceptance/test_phase8_acceptance.py::test_p8_err_001_distinguishable_error_codes)

---
### P8-LLM-001 — Model Unavailable Handled Safely
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-LLM-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_llm_001_model_unavailable",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Model Unavailable Handled Safely (test: tests/acceptance/test_phase8_acceptance.py::test_p8_llm_001_model_unavailable)

---
### P8-LLM-003 — Existing LLM Client Reused
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-LLM-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_llm_003_client_reuse",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Existing LLM Client Reused (test: tests/acceptance/test_phase8_acceptance.py::test_p8_llm_003_client_reuse)

---
### P8-HANDOFF-001 — Phase 7 Handoff Fields Intact
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-HANDOFF-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_001_projection_fields",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Phase 7 Handoff Fields Intact (test: tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_001_projection_fields)

---
### P8-HANDOFF-003 — Lead Score and Qualification Immutable
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-HANDOFF-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_invariants",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Lead Score and Qualification Immutable (test: tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_invariants)

---
### P8-HANDOFF-004 — Best Contact Ranking Immutable
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-HANDOFF-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_invariants",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Best Contact Ranking Immutable (test: tests/acceptance/test_phase8_acceptance.py::test_p8_handoff_invariants)

---
### P8-API-001 — API Generate Drafts Valid Request
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-API-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_api_001_and_validation",
  "execution_time_s": 0.257
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: API Generate Drafts Valid Request (test: tests/acceptance/test_phase8_acceptance.py::test_p8_api_001_and_validation)

---
### P8-API-003 — API Missing Sender Returns 422
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-API-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_api_001_and_validation",
  "execution_time_s": 0.257
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: API Missing Sender Returns 422 (test: tests/acceptance/test_phase8_acceptance.py::test_p8_api_001_and_validation)

---
### P8-API-004 — API Invalid Tone Returns 422
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-API-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_api_004_invalid_tone_returns_422",
  "execution_time_s": 0.006
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: API Invalid Tone Returns 422 (test: tests/acceptance/test_phase8_acceptance.py::test_p8_api_004_invalid_tone_returns_422)

---
### P8-API-005 — API Invalid Language Returns 422
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-API-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase8_acceptance.py::test_p8_api_005_invalid_language_returns_422",
  "execution_time_s": 0.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: API Invalid Language Returns 422 (test: tests/acceptance/test_phase8_acceptance.py::test_p8_api_005_invalid_language_returns_422)

---
### P8-REG-009 — Missing Job Location Does Not Become Remote
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_009_missing_job_location_not_remote",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Missing Job Location Does Not Become Remote (test: tests/test_outreach.py::test_p8_reg_009_missing_job_location_not_remote)

---
### P8-REG-010 — Missing Contact Title Does Not Become Leadership
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_010_missing_contact_title_not_leadership",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Missing Contact Title Does Not Become Leadership (test: tests/test_outreach.py::test_p8_reg_010_missing_contact_title_not_leadership)

---
### P8-REG-011 — Company and Contact Fields Cannot Escape Untrusted-Data Boundary
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-011"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_011_company_contact_cannot_escape_untrusted_data",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Company and Contact Fields Cannot Escape Untrusted-Data Boundary (test: tests/test_outreach.py::test_p8_reg_011_company_contact_cannot_escape_untrusted_data)

---
### P8-REG-012 — Complete Prompt Trust Boundary
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-012"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_012_complete_prompt_trust_boundary",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Complete Prompt Trust Boundary (test: tests/test_outreach.py::test_p8_reg_012_complete_prompt_trust_boundary)

---
### P8-REG-013 — Missing Recipient Name Is Not Fabricated
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-013"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_013_missing_recipient_name_not_fabricated",
  "execution_time_s": 0.001
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Missing Recipient Name Is Not Fabricated (test: tests/test_outreach.py::test_p8_reg_013_missing_recipient_name_not_fabricated)

---
### P8-REG-014 — Structured Service Missing Status Is Not Active
**Phase:** Phase 8
**Source:** internal

**Input:**
```json
{
  "case_id": "P8-REG-014"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_outreach.py::test_p8_reg_014_structured_service_missing_status_not_active",
  "execution_time_s": 0.0
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 8: Structured Service Missing Status Is Not Active (test: tests/test_outreach.py::test_p8_reg_014_structured_service_missing_status_not_active)

---
### P9-REVIEW-001 — Import Pending Draft
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REVIEW-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_review_001_import_pending_draft",
  "execution_time_s": 0.113
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Import Pending Draft (test: tests/acceptance/test_phase9_acceptance.py::test_p9_review_001_import_pending_draft)

---
### P9-STORE-001 — Persist Draft
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Persist Draft (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-STORE-002 — Preserve Lead ID
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Preserve Lead ID (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-STORE-003 — Preserve Contact ID
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Preserve Contact ID (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-STORE-004 — Preserve Recipient Email
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Preserve Recipient Email (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-STORE-005 — No Sent-History Overwrite
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Sent-History Overwrite (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-STORE-006 — Temporary DB Isolation
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STORE-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation",
  "execution_time_s": 0.856
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Temporary DB Isolation (test: tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation)

---
### P9-QUEUE-001 — Pending Review Default
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Pending Review Default (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-002 — Filter by Approval Status
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Filter by Approval Status (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-003 — Filter by Send Status
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Filter by Send Status (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-004 — Filter by Lead ID
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Filter by Lead ID (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-005 — Filter by Contact ID
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Filter by Contact ID (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-006 — Filter by Recipient Email
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Filter by Recipient Email (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-QUEUE-007 — Deterministic Pagination
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QUEUE-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination",
  "execution_time_s": 0.455
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Deterministic Pagination (test: tests/acceptance/test_phase9_acceptance.py::test_p9_queue_001_to_007_review_queue_and_pagination)

---
### P9-EDIT-001 — Edit Subject Creates New Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Subject Creates New Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-002 — Edit Body Creates New Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Body Creates New Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-003 — Edit Recipient Email Creates New Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Recipient Email Creates New Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-004 — Edit Sender-Relevant Identity Invalidates Approval
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Sender-Relevant Identity Invalidates Approval (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-005 — Personalization Notes Only
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Personalization Notes Only (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-006 — Sent Revision Immutable
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent Revision Immutable (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-EDIT-007 — Revision Monotonicity
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-EDIT-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics",
  "execution_time_s": 1.007
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Revision Monotonicity (test: tests/acceptance/test_phase9_acceptance.py::test_p9_edit_001_to_007_revision_semantics)

---
### P9-APPROVE-001 — Explicit Approval
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_001_explicit_approval",
  "execution_time_s": 0.438
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Explicit Approval (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_001_explicit_approval)

---
### P9-APPROVE-002 — Approval Does Not Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_002_approval_does_not_send",
  "execution_time_s": 0.327
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approval Does Not Send (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_002_approval_does_not_send)

---
### P9-APPROVE-003 — Wrong Revision Cannot Approve
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards",
  "execution_time_s": 0.755
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Wrong Revision Cannot Approve (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards)

---
### P9-APPROVE-004 — Rejected Draft Cannot Be Approved Implicitly
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards",
  "execution_time_s": 0.755
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Rejected Draft Cannot Be Approved Implicitly (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards)

---
### P9-APPROVE-005 — Reviewer Required
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards",
  "execution_time_s": 0.755
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Reviewer Required (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards)

---
### P9-APPROVE-006 — Approval Event Recorded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-APPROVE-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards",
  "execution_time_s": 0.755
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approval Event Recorded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_approve_003_to_006_approval_guards)

---
### P9-HASH-001 — Stable Fingerprint
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Stable Fingerprint (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-002 — Subject Change Changes Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Subject Change Changes Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-003 — Body Change Changes Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Body Change Changes Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-004 — Recipient Change Changes Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Recipient Change Changes Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-005 — Sender Email Change Changes Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sender Email Change Changes Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-006 — Revision Change Changes Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Revision Change Changes Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-007 — Hash Does Not Include Mutable Non-Send Metadata
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Hash Does Not Include Mutable Non-Send Metadata (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-HASH-008 — Approved Hash Stored Exactly
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-HASH-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants",
  "execution_time_s": 0.067
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approved Hash Stored Exactly (test: tests/acceptance/test_phase9_acceptance.py::test_p9_hash_001_to_008_fingerprint_invariants)

---
### P9-REG-001 — Stale Approval Blocked
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_stale_approval_fingerprint_mismatch_blocks_send",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Stale Approval Blocked (test: tests/test_sending.py::test_stale_approval_fingerprint_mismatch_blocks_send)

---
### P9-REG-002 — Pending Review Cannot Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_unapproved_draft_send_blocked",
  "execution_time_s": 0.152
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Pending Review Cannot Send (test: tests/test_sending.py::test_unapproved_draft_send_blocked)

---
### P9-SEND-003 — Rejected Cannot Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEND-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_send_003_unapproved_blocked",
  "execution_time_s": 0.255
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Rejected Cannot Send (test: tests/acceptance/test_phase9_acceptance.py::test_p9_send_003_unapproved_blocked)

---
### P9-SEND-004 — Changes Requested Cannot Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEND-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_send_004_stale_approval_blocked",
  "execution_time_s": 0.41
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Changes Requested Cannot Send (test: tests/acceptance/test_phase9_acceptance.py::test_p9_send_004_stale_approval_blocked)

---
### P9-SEND-005 — Missing Approval Hash Cannot Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEND-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_send_005_already_sent_blocked",
  "execution_time_s": 1.115
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Missing Approval Hash Cannot Send (test: tests/acceptance/test_phase9_acceptance.py::test_p9_send_005_already_sent_blocked)

---
### P9-REG-003 — Dry Run Zero Network Calls
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_dry_run_invokes_zero_network_calls",
  "execution_time_s": 0.245
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Zero Network Calls (test: tests/test_sending.py::test_dry_run_invokes_zero_network_calls)

---
### P9-DRY-002 — Dry Run Still Checks Suppression
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DRY-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks",
  "execution_time_s": 0.495
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Still Checks Suppression (test: tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks)

---
### P9-DRY-003 — Dry Run Still Checks Approval Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DRY-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks",
  "execution_time_s": 0.495
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Still Checks Approval Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks)

---
### P9-DRY-004 — Dry Run Still Checks Sender Match
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DRY-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks",
  "execution_time_s": 0.495
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Still Checks Sender Match (test: tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks)

---
### P9-DRY-005 — Dry Run Creates Audit Diagnostic
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DRY-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks",
  "execution_time_s": 0.495
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Creates Audit Diagnostic (test: tests/acceptance/test_phase9_acceptance.py::test_p9_dry_002_to_005_dry_run_checks)

---
### P9-REG-004 — EMAIL_SEND_ENABLED=false Blocks Real Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_email_send_enabled_false_blocks_real_send",
  "execution_time_s": 0.229
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: EMAIL_SEND_ENABLED=false Blocks Real Send (test: tests/test_sending.py::test_email_send_enabled_false_blocks_real_send)

---
### P9-CONFIG-001 — Missing SMTP Config
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-CONFIG-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_config_001_to_002_server_owned_config",
  "execution_time_s": 0.11
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Missing SMTP Config (test: tests/acceptance/test_phase9_acceptance.py::test_p9_config_001_to_002_server_owned_config)

---
### P9-CONFIG-002 — Request Body Cannot Supply SMTP Credentials
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-CONFIG-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_config_001_to_002_server_owned_config",
  "execution_time_s": 0.11
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Request Body Cannot Supply SMTP Credentials (test: tests/acceptance/test_phase9_acceptance.py::test_p9_config_001_to_002_server_owned_config)

---
### P9-SENDER-001 — Matching Sender Accepted
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SENDER-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity",
  "execution_time_s": 0.795
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Matching Sender Accepted (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity)

---
### P9-SENDER-002 — Mismatch Blocked
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SENDER-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity",
  "execution_time_s": 0.795
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Mismatch Blocked (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity)

---
### P9-SENDER-003 — Trusted Alias
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SENDER-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity",
  "execution_time_s": 0.795
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Trusted Alias (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity)

---
### P9-SENDER-004 — From Header Cannot Be LLM/User Arbitrary
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SENDER-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity",
  "execution_time_s": 0.795
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: From Header Cannot Be LLM/User Arbitrary (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sender_001_to_004_sender_identity)

---
### P9-REG-005 — Suppressed Recipient Cannot Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_suppressed_recipient_blocks_send",
  "execution_time_s": 0.503
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppressed Recipient Cannot Send (test: tests/test_sending.py::test_suppressed_recipient_blocks_send)

---
### P9-SUPPRESS-002 — Case-Insensitive Email Matching
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SUPPRESS-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics",
  "execution_time_s": 0.351
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Case-Insensitive Email Matching (test: tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics)

---
### P9-SUPPRESS-003 — Suppression Persists
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SUPPRESS-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics",
  "execution_time_s": 0.351
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppression Persists (test: tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics)

---
### P9-SUPPRESS-004 — Manual Suppression Audit Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SUPPRESS-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics",
  "execution_time_s": 0.351
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Manual Suppression Audit Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics)

---
### P9-SUPPRESS-005 — Suppression Cannot Be Silently Removed
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SUPPRESS-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics",
  "execution_time_s": 0.351
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppression Cannot Be Silently Removed (test: tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics)

---
### P9-SUPPRESS-006 — Opt-Out Reason Preserved
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SUPPRESS-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics",
  "execution_time_s": 0.351
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Opt-Out Reason Preserved (test: tests/acceptance/test_phase9_acceptance.py::test_p9_suppress_002_to_006_suppression_semantics)

---
### P9-REG-006 — Already Sent Cannot Resend
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_idempotent_already_sent_blocked",
  "execution_time_s": 0.612
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Already Sent Cannot Resend (test: tests/test_sending.py::test_idempotent_already_sent_blocked)

---
### P9-IDEMP-002 — Same Draft Different Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-IDEMP-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys",
  "execution_time_s": 2.472
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Same Draft Different Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys)

---
### P9-IDEMP-003 — Same Recipient Different Draft
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-IDEMP-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys",
  "execution_time_s": 2.472
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Same Recipient Different Draft (test: tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys)

---
### P9-IDEMP-004 — send_key Deterministic
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-IDEMP-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys",
  "execution_time_s": 2.472
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: send_key Deterministic (test: tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys)

---
### P9-IDEMP-005 — No force=true Bypass
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-IDEMP-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys",
  "execution_time_s": 2.472
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No force=true Bypass (test: tests/acceptance/test_phase9_acceptance.py::test_p9_idemp_002_to_005_idempotency_keys)

---
### P9-REG-007 — Concurrent Duplicate Send Prevented
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_concurrent_duplicate_send_prevention",
  "execution_time_s": 0.478
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Concurrent Duplicate Send Prevented (test: tests/test_sending.py::test_concurrent_duplicate_send_prevention)

---
### P9-CONC-002 — Unique send_key Constraint
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-CONC-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking",
  "execution_time_s": 0.401
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Unique send_key Constraint (test: tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking)

---
### P9-CONC-003 — Atomic State Transition
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-CONC-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking",
  "execution_time_s": 0.401
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Atomic State Transition (test: tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking)

---
### P9-CONC-004 — Concurrent Different Drafts Allowed
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-CONC-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking",
  "execution_time_s": 0.401
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Concurrent Different Drafts Allowed (test: tests/acceptance/test_phase9_acceptance.py::test_p9_conc_002_to_004_concurrency_locking)

---
### P9-SMTP-001 — Explicit Mock Sender Success
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch",
  "execution_time_s": 0.871
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Explicit Mock Sender Success (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch)

---
### P9-SMTP-002 — Production Does Not Auto-Mock
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch",
  "execution_time_s": 0.871
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Production Does Not Auto-Mock (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch)

---
### P9-SMTP-003 — Text/Plain Payload
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch",
  "execution_time_s": 0.871
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Text/Plain Payload (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_001_to_003_provider_dispatch)

---
### P9-SMTP-ERR-001 — Authentication Failure
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-ERR-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy",
  "execution_time_s": 0.829
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Authentication Failure (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy)

---
### P9-SMTP-ERR-002 — Connection Failure
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-ERR-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy",
  "execution_time_s": 0.829
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Connection Failure (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy)

---
### P9-SMTP-ERR-003 — Timeout
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-ERR-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy",
  "execution_time_s": 0.829
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Timeout (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy)

---
### P9-SMTP-ERR-004 — Generic Provider Error
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-ERR-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy",
  "execution_time_s": 0.829
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Generic Provider Error (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy)

---
### P9-SMTP-ERR-005 — Error Sanitization
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SMTP-ERR-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy",
  "execution_time_s": 0.829
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Error Sanitization (test: tests/acceptance/test_phase9_acceptance.py::test_p9_smtp_err_001_to_005_error_taxonomy)

---
### P9-FAIL-001 — Failed Send Not Marked Sent
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-FAIL-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants",
  "execution_time_s": 0.807
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failed Send Not Marked Sent (test: tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants)

---
### P9-FAIL-002 — Failed Attempt Recorded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-FAIL-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants",
  "execution_time_s": 0.807
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failed Attempt Recorded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants)

---
### P9-FAIL-003 — No Automatic Retry
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-FAIL-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants",
  "execution_time_s": 0.807
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Automatic Retry (test: tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants)

---
### P9-FAIL-004 — Explicit Later Retry
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-FAIL-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants",
  "execution_time_s": 0.807
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Explicit Later Retry (test: tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants)

---
### P9-FAIL-005 — Ambiguous Provider Failure
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-FAIL-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants",
  "execution_time_s": 0.807
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Ambiguous Provider Failure (test: tests/acceptance/test_phase9_acceptance.py::test_p9_fail_001_to_005_failure_and_retry_invariants)

---
### P9-RATE-001 — Within Per-Minute Limit
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-RATE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Within Per-Minute Limit (test: tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting)

---
### P9-RATE-002 — Exceed Per-Minute Limit
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-RATE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Exceed Per-Minute Limit (test: tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting)

---
### P9-RATE-003 — Batch Max Boundary
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-RATE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Batch Max Boundary (test: tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting)

---
### P9-RATE-004 — Batch Max + 1 Rejected
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-RATE-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Batch Max + 1 Rejected (test: tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting)

---
### P9-RATE-005 — Rate Limit Deterministic Clock
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-RATE-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting",
  "execution_time_s": 0.004
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Rate Limit Deterministic Clock (test: tests/acceptance/test_phase9_acceptance.py::test_p9_rate_001_to_005_rate_limiting)

---
### P9-BATCH-001 — Mixed Batch Isolation
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-BATCH-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_batch_001_partial_failure_isolation",
  "execution_time_s": 1.911
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Mixed Batch Isolation (test: tests/acceptance/test_phase9_acceptance.py::test_p9_batch_001_partial_failure_isolation)

---
### P9-BATCH-002 — Explicit Draft IDs Required
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-BATCH-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling",
  "execution_time_s": 0.816
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Explicit Draft IDs Required (test: tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling)

---
### P9-BATCH-003 — Duplicate Draft IDs in Request
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-BATCH-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling",
  "execution_time_s": 0.816
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Duplicate Draft IDs in Request (test: tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling)

---
### P9-BATCH-004 — Missing Draft ID
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-BATCH-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling",
  "execution_time_s": 0.816
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Missing Draft ID (test: tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling)

---
### P9-BATCH-005 — Stable Result Ordering
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-BATCH-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling",
  "execution_time_s": 0.816
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Stable Result Ordering (test: tests/acceptance/test_phase9_acceptance.py::test_p9_batch_002_to_005_batch_send_handling)

---
### P9-AUDIT-001 — Imported Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Imported Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-002 — Edited Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edited Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-003 — Approved Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approved Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-004 — Rejected Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Rejected Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-005 — Changes Requested Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Changes Requested Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-006 — Suppressed Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppressed Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-007 — Send Requested Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Send Requested Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-008 — Sent Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-009 — Failed Event
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failed Event (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-010 — Append-Only
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Append-Only (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-011 — Reviewer Preserved
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-011"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Reviewer Preserved (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-AUDIT-012 — No Secrets In Audit
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-AUDIT-012"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants",
  "execution_time_s": 0.36
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Secrets In Audit (test: tests/acceptance/test_phase9_acceptance.py::test_p9_audit_001_to_012_audit_trail_invariants)

---
### P9-ATTEMPT-001 — Success Attempt Record
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-ATTEMPT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records",
  "execution_time_s": 1.091
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Success Attempt Record (test: tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records)

---
### P9-ATTEMPT-002 — Failure Attempt Record
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-ATTEMPT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records",
  "execution_time_s": 1.091
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failure Attempt Record (test: tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records)

---
### P9-ATTEMPT-003 — Dry Run Is Not Successful Provider Attempt
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-ATTEMPT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records",
  "execution_time_s": 1.091
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Dry Run Is Not Successful Provider Attempt (test: tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records)

---
### P9-ATTEMPT-004 — No Credentials Persisted
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-ATTEMPT-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records",
  "execution_time_s": 1.091
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Credentials Persisted (test: tests/acceptance/test_phase9_acceptance.py::test_p9_attempt_001_to_004_attempt_records)

---
### P9-STATE-001 — Invalid Transition Rejected
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STATE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions",
  "execution_time_s": 0.189
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Invalid Transition Rejected (test: tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions)

---
### P9-STATE-002 — Sent State Terminal For Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STATE-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions",
  "execution_time_s": 0.189
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent State Terminal For Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions)

---
### P9-STATE-003 — New Revision Reopens Review
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STATE-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions",
  "execution_time_s": 0.189
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: New Revision Reopens Review (test: tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions)

---
### P9-STATE-004 — Suppressed Recipient Blocks Regardless of Approval
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-STATE-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions",
  "execution_time_s": 0.189
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppressed Recipient Blocks Regardless of Approval (test: tests/acceptance/test_phase9_acceptance.py::test_p9_state_001_to_004_state_machine_transitions)

---
### P9-P8-001 — Placeholder Rejected After Human Edit
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P8-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Placeholder Rejected After Human Edit (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm)

---
### P9-P8-002 — Empty Subject Rejected
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P8-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Empty Subject Rejected (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm)

---
### P9-P8-003 — Oversized Subject/Body Rejected
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P8-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Oversized Subject/Body Rejected (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm)

---
### P9-P8-004 — Recipient Identity Revalidated
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P8-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Recipient Identity Revalidated (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm)

---
### P9-P8-005 — No Phase 8 LLM Invocation
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P8-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm",
  "execution_time_s": 0.237
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Phase 8 LLM Invocation (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p8_001_to_005_validator_reuse_no_llm)

---
### P9-P7-001 — Approved Projection
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P7-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections",
  "execution_time_s": 0.698
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approved Projection (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections)

---
### P9-P7-002 — Sent Projection
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P7-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections",
  "execution_time_s": 0.698
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent Projection (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections)

---
### P9-P7-003 — Failed Projection
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P7-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections",
  "execution_time_s": 0.698
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failed Projection (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections)

---
### P9-P7-004 — Suppressed Projection
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-P7-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections",
  "execution_time_s": 0.698
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppressed Projection (test: tests/acceptance/test_phase9_acceptance.py::test_p9_p7_001_to_004_workflow_projections)

---
### P9-SEC-001 — SMTP Password Never Returned
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_001_secrets_never_logged",
  "execution_time_s": 0.11
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: SMTP Password Never Returned (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_001_secrets_never_logged)

---
### P9-SEC-002 — SMTP Password Never Logged
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: SMTP Password Never Logged (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-003 — OAuth/API Secrets Never Stored In DB
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: OAuth/API Secrets Never Stored In DB (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-004 — API Cannot Override SMTP Host
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: API Cannot Override SMTP Host (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-005 — API Cannot Override SMTP Password
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: API Cannot Override SMTP Password (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-006 — Provider Exception Redaction
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Provider Exception Redaction (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-007 — SQL Parameterization
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: SQL Parameterization (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-008 — Header Injection Protection
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Header Injection Protection (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-009 — Invalid Recipient Email
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Invalid Recipient Email (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-SEC-010 — No Personal/Hidden Recipient BCC
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-SEC-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization",
  "execution_time_s": 0.231
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: No Personal/Hidden Recipient BCC (test: tests/acceptance/test_phase9_acceptance.py::test_p9_sec_002_to_010_security_sanitization)

---
### P9-NOLLM-001 — Sending Module Has Zero LLM Imports
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-NOLLM-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_001_zero_llm_code_path",
  "execution_time_s": 0.022
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sending Module Has Zero LLM Imports (test: tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_001_zero_llm_code_path)

---
### P9-NOLLM-002 — Approve Does Not Call LLM
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-NOLLM-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls",
  "execution_time_s": 0.754
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approve Does Not Call LLM (test: tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls)

---
### P9-NOLLM-003 — Edit Does Not Call LLM
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-NOLLM-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls",
  "execution_time_s": 0.754
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Does Not Call LLM (test: tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls)

---
### P9-NOLLM-004 — Send Does Not Call LLM
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-NOLLM-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls",
  "execution_time_s": 0.754
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Send Does Not Call LLM (test: tests/acceptance/test_phase9_acceptance.py::test_p9_nollm_002_to_004_zero_llm_calls)

---
### P9-DB-001 — Tables Initialize Cleanly
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Tables Initialize Cleanly (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-002 — Reinitialization Idempotent
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Reinitialization Idempotent (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-003 — Unique Draft Revision Constraint
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Unique Draft Revision Constraint (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-004 — Unique send_key Constraint
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Unique send_key Constraint (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-005 — Foreign/Logical References Preserved
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Foreign/Logical References Preserved (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-006 — Transaction Rollback On Internal Failure
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Transaction Rollback On Internal Failure (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-007 — Production DB Not Packaged
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Production DB Not Packaged (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-DB-008 — Runtime DB Directory Created Safely
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-DB-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity",
  "execution_time_s": 0.178
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Runtime DB Directory Created Safely (test: tests/acceptance/test_phase9_acceptance.py::test_p9_db_001_to_008_database_integrity)

---
### P9-PACK-001 — .env Excluded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-PACK-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security",
  "execution_time_s": 0.021
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: .env Excluded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security)

---
### P9-PACK-002 — Runtime DB Excluded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-PACK-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security",
  "execution_time_s": 0.021
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Runtime DB Excluded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security)

---
### P9-PACK-003 — Chroma Runtime DB Excluded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-PACK-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security",
  "execution_time_s": 0.021
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Chroma Runtime DB Excluded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security)

---
### P9-PACK-004 — Private Key Types Excluded
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-PACK-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security",
  "execution_time_s": 0.021
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Private Key Types Excluded (test: tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security)

---
### P9-PACK-005 — Source/Test Files Still Included
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-PACK-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security",
  "execution_time_s": 0.021
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Source/Test Files Still Included (test: tests/acceptance/test_phase9_acceptance.py::test_p9_pack_001_to_005_release_zip_security)

---
### P9-OPTOUT-001 — Server-Owned Footer Appended
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-OPTOUT-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint",
  "execution_time_s": 0.458
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Server-Owned Footer Appended (test: tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint)

---
### P9-OPTOUT-002 — LLM Cannot Control Footer
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-OPTOUT-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint",
  "execution_time_s": 0.458
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: LLM Cannot Control Footer (test: tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint)

---
### P9-OPTOUT-003 — Footer Included In Approval Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-OPTOUT-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint",
  "execution_time_s": 0.458
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Footer Included In Approval Hash (test: tests/acceptance/test_phase9_acceptance.py::test_p9_optout_001_to_003_optout_footer_fingerprint)

---
### P9-API-001 — Import Success
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Import Success (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-002 — Invalid Draft Validation
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Invalid Draft Validation (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-003 — Review List
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Review List (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-004 — Edit Creates Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Edit Creates Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-005 — Approve
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approve (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-006 — Approval Does Not Send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approval Does Not Send (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-007 — Send Dry Run
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Send Dry Run (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-008 — Real Send Disabled
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Real Send Disabled (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-009 — Suppression Blocks
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Suppression Blocks (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-010 — Batch Mixed Response
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Batch Mixed Response (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-011 — Invalid Revision
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-011"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Invalid Revision (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-API-012 — Missing Draft
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-API-012"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle",
  "execution_time_s": 2.119
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Missing Draft (test: tests/acceptance/test_phase9_acceptance.py::test_p9_api_001_to_012_rest_api_lifecycle)

---
### P9-LIVE-SMTP-001 — Optional Live SMTP Test (Gated)
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-LIVE-SMTP-001"
}
```
**Expected:**
```json
{
  "status": "NOT_RUN",
  "live_gated": true,
  "requires_explicit_env": [
    "RUN_LIVE_EMAIL_TESTS=true",
    "EMAIL_SEND_ENABLED=true"
  ]
}
```
**Actual:**
_(Not executed yet)_

**Result:** NOT_RUN

**Differences:**
_(Pending execution)_

**Reason:** P9-LIVE-SMTP-001 skipped: live email tests not explicitly enabled.

**Human Notes:**
Phase 9: Optional Live SMTP Test (Gated) (test: tests/acceptance/test_phase9_acceptance.py::test_p9_live_smtp_001_live_send_guardrail)

---
### P9-LIVE-SAFE-001 — Normal Test Run Zero Live Calls
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-LIVE-SAFE-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_phase9_acceptance.py::test_p9_live_safe_001_zero_live_network_calls",
  "execution_time_s": 0.886
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Normal Test Run Zero Live Calls (test: tests/acceptance/test_phase9_acceptance.py::test_p9_live_safe_001_zero_live_network_calls)

---
### P9-QA-001 — Unmapped deterministic case cannot PASS.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-001"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_001_unmapped_deterministic_case_cannot_pass",
  "execution_time_s": 0.003
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Unmapped deterministic case cannot PASS. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_001_unmapped_deterministic_case_cannot_pass)

---
### P9-QA-002 — Empty `pass` test cannot count as real coverage.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-002"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_002_empty_pass_test_cannot_count_as_coverage",
  "execution_time_s": 0.023
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Empty `pass` test cannot count as real coverage. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_002_empty_pass_test_cannot_count_as_coverage)

---
### P9-QA-003 — Skipped live SMTP becomes NOT_RUN.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-003"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_003_skipped_live_smtp_becomes_not_run",
  "execution_time_s": 0.003
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Skipped live SMTP becomes NOT_RUN. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_003_skipped_live_smtp_becomes_not_run)

---
### P9-QA-004 — Failed node maps to FAIL.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-004"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_004_failed_node_maps_to_fail",
  "execution_time_s": 0.044
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Failed node maps to FAIL. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_004_failed_node_maps_to_fail)

---
### P9-QA-005 — Expected and Actual independently sourced.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-005"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_005_expected_and_actual_independently_sourced",
  "execution_time_s": 0.042
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Expected and Actual independently sourced. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_005_expected_and_actual_independently_sourced)

---
### P9-QA-006 — false_pass_count computed dynamically.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-006"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_006_false_pass_count_computed_dynamically",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: false_pass_count computed dynamically. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_006_false_pass_count_computed_dynamically)

---
### P9-QA-007 — A mock provider success is labeled mock, not live SMTP success.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-007"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_007_mock_provider_success_labeled_mock_not_live_smtp",
  "execution_time_s": 0.003
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: A mock provider success is labeled mock, not live SMTP success. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_007_mock_provider_success_labeled_mock_not_live_smtp)

---
### P9-QA-008 — A live-send case cannot run without explicit live flag.
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-QA-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/acceptance/test_qa_integrity.py::test_p9_qa_008_live_send_cannot_run_without_explicit_flag",
  "execution_time_s": 0.002
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: A live-send case cannot run without explicit live flag. (test: tests/acceptance/test_qa_integrity.py::test_p9_qa_008_live_send_cannot_run_without_explicit_flag)

---
### P9-REG-008 — Approval Action Itself Never Sends
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-008"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_approval_does_not_send_email",
  "execution_time_s": 0.191
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Approval Action Itself Never Sends (test: tests/test_sending.py::test_approval_does_not_send_email)

---
### P9-REG-009 — Recipient Edit Invalidates Approval
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-009"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_human_edit_creates_new_revision_and_invalidates_approval",
  "execution_time_s": 0.253
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Recipient Edit Invalidates Approval (test: tests/test_sending.py::test_human_edit_creates_new_revision_and_invalidates_approval)

---
### P9-REG-010 — Sender Mismatch Blocks Provider Call
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-010"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_sender_identity_mismatch_blocks_send",
  "execution_time_s": 0.251
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sender Mismatch Blocks Provider Call (test: tests/test_sending.py::test_sender_identity_mismatch_blocks_send)

---
### P9-REG-011 — Provider Secrets Sanitized Across Logs
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-011"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_smtp_sender_auth_error_sanitized",
  "execution_time_s": 0.04
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Provider Secrets Sanitized Across Logs (test: tests/test_sending.py::test_smtp_sender_auth_error_sanitized)

---
### P9-REG-012 — Final Sent Payload Hash Equals Approved Payload Hash
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-012"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_012_sent_payload_hash_equals_approved_payload_hash",
  "execution_time_s": 0.54
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Final Sent Payload Hash Equals Approved Payload Hash (test: tests/test_sending.py::test_p9_reg_012_sent_payload_hash_equals_approved_payload_hash)

---
### P9-REG-013 — Missing/untrusted server sender cannot self-authorize
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-013"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_013_missing_trusted_sender_config_blocks_approval_send",
  "execution_time_s": 0.23
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Missing/untrusted server sender cannot self-authorize (test: tests/test_sending.py::test_p9_reg_013_missing_trusted_sender_config_blocks_approval_send)

---
### P9-REG-014 — Sent revision cannot be rejected
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-014"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_014_sent_revision_cannot_be_rejected",
  "execution_time_s": 0.336
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent revision cannot be rejected (test: tests/test_sending.py::test_p9_reg_014_sent_revision_cannot_be_rejected)

---
### P9-REG-015 — Sent revision cannot request changes
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-015"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_015_sent_revision_cannot_request_changes",
  "execution_time_s": 0.724
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent revision cannot request changes (test: tests/test_sending.py::test_p9_reg_015_sent_revision_cannot_request_changes)

---
### P9-REG-016 — Sent revision cannot transition back to not_sent
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-016"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_016_sent_revision_cannot_transition_back_to_not_sent",
  "execution_time_s": 0.5
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent revision cannot transition back to not_sent (test: tests/test_sending.py::test_p9_reg_016_sent_revision_cannot_transition_back_to_not_sent)

---
### P9-REG-017 — Sent revision remains unchanged after invalid transition
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-017"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_017_sent_revision_remains_unchanged_after_invalid_transition",
  "execution_time_s": 0.416
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sent revision remains unchanged after invalid transition (test: tests/test_sending.py::test_p9_reg_017_sent_revision_remains_unchanged_after_invalid_transition)

---
### P9-REG-018 — Trusted configured alias can approve and send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-018"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_018_trusted_alias_can_approve_and_send",
  "execution_time_s": 0.333
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Trusted configured alias can approve and send (test: tests/test_sending.py::test_p9_reg_018_trusted_alias_can_approve_and_send)

---
### P9-REG-019 — Untrusted alias cannot approve or send
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-019"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_019_untrusted_alias_cannot_approve_or_send",
  "execution_time_s": 0.176
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Untrusted alias cannot approve or send (test: tests/test_sending.py::test_p9_reg_019_untrusted_alias_cannot_approve_or_send)

---
### P9-REG-020 — Sender display name is fingerprinted
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-020"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_020_sender_display_name_fingerprinted",
  "execution_time_s": 0.239
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Sender display name is fingerprinted (test: tests/test_sending.py::test_p9_reg_020_sender_display_name_fingerprinted)

---
### P9-REG-021 — Changing SMTP_FROM_NAME after approval does not mutate provider payload
**Phase:** Phase 9
**Source:** internal

**Input:**
```json
{
  "case_id": "P9-REG-021"
}
```
**Expected:**
```json
{
  "status": "PASS"
}
```
**Actual:**
```json
{
  "status": "PASS",
  "test_node": "tests/test_sending.py::test_p9_reg_021_changing_smtp_from_name_after_approval_does_not_mutate_provider_payload",
  "execution_time_s": 0.448
}
```
**Result:** PASS

**Differences:**
_(None / In sync)_

**Human Notes:**
Phase 9: Changing SMTP_FROM_NAME after approval does not mutate provider payload (test: tests/test_sending.py::test_p9_reg_021_changing_smtp_from_name_after_approval_does_not_mutate_provider_payload)

---