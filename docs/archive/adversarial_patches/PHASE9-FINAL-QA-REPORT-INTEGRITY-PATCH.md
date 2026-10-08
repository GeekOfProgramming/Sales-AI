# Phase 9 Final QA Report-Integrity Patch
## Reporting-only patch before formal Phase 9 freeze

Production Phase 9 behavior is accepted.
Do NOT modify Phase 8.
Do NOT modify send-path behavior unless required by a failing regression.
Do NOT start Phase 10 yet.

Independent verification confirmed:

- final ZIP contains 255 files
- denylist is clean
- Golden catalog contains 185 unique cases
- 184 deterministic cases are mapped and PASS
- P9-LIVE-SMTP-001 is NOT_RUN
- P9-REG-001..021 pass independently
- no new send-path blocker was found

The only remaining issue is QA report integrity.

---

# 1. Remove Hardcoded Safety Counters

Current `scripts/update_phase9_report.py` writes literal values such as:

```python
"provider_calls_in_dry_run": 0,
"provider_calls_for_unapproved": 0,
"duplicate_send_count": 0,
"suppression_bypass_count": 0,
"stale_approval_bypass_count": 0,
"secrets_detected": 0,
"llm_calls_detected": 0,
"runtime_db_packaged": 0,
```

These values are currently true for the final build, but they are not execution-derived.

This violates the execution-driven reporting intent.

Replace hardcoded safety metrics with values derived from actual executed QA evidence.

Preferred implementation:

- create a structured Phase 9 metrics artifact during pytest/QA execution, OR
- derive each counter from dedicated test result nodes plus explicit diagnostics.

A failed or NOT_RUN safety test must never still produce a zero counter that implies successful validation.

For each metric include provenance, e.g.:

```json
{
  "provider_calls_in_dry_run": 0,
  "provider_calls_in_dry_run_source":
    "tests/test_sending.py::test_dry_run_invokes_zero_network_calls"
}
```

If exact runtime numeric data is unavailable, report:

```text
null / unknown
```

rather than a fabricated zero.

---

# 2. Remove Hardcoded Invariant Booleans

Current report also emits unconditional:

```python
"invariants": {
    "approval_never_triggers_send": True,
    "stale_approval_fingerprint_blocks_send": True,
    ...
}
```

Derive every boolean from the mapped executed regression result.

Example:

```text
approval_never_triggers_send
    <- P9-REG-008 PASS

stale_approval_fingerprint_blocks_send
    <- P9-REG-001 PASS

already_sent_idempotency_prevents_duplicate
    <- P9-REG-006 PASS

suppressed_recipient_blocks_send
    <- P9-REG-005 PASS

dry_run_invokes_zero_network_transmissions
    <- P9-REG-003 PASS

zero_llm_calls_in_phase9
    <- P9-NOLLM-001..004 PASS

secrets_never_logged_or_exposed
    <- P9-SEC-001..010 PASS
```

If required mapped tests fail or do not run, the invariant cannot be `true`.

---

# 3. Match Required Report Schema

`PHASE9-QA-GOLDEN-SPEC.md` requires report fields including:

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

unmapped_deterministic_cases
false_pass_count

live_smtp_status
live_smtp_executed

critical_regressions

provider_calls_in_dry_run
provider_calls_for_unapproved
duplicate_send_count
suppression_bypass_count
stale_approval_bypass_count
secrets_detected
llm_calls_detected
```

The current report uses aliases such as:

```text
generated_at
total_cases
deferred
```

and omits several required execution-summary fields.

Update `phase9_latest.json` to contain the required contract fields.

Backward-compatible aliases may remain if desired, but the required fields must exist.

Required derived values for this build should be:

```text
total_catalog_cases = 185
mapped_executable_cases = 185
executed_cases = 184
passed = 184
failed = 0
review = 0
not_run = 1
unmapped_deterministic_cases = 0
false_pass_count = 0

live_smtp_status = NOT_RUN
live_smtp_executed = false
```

Clarification:

The live case is mapped but intentionally not executed as a live SMTP delivery.

---

# 4. Critical Regressions Must Be Derived

Add:

```json
"critical_regressions": {
  "P9-REG-001": "PASS",
  ...
  "P9-REG-021": "PASS"
}
```

Populate from actual mapped pytest execution results.

Do not hardcode `PASS`.

---

# 5. Add Meta-Regression Tests

Add QA-integrity tests proving:

```text
P9-QA-009 safety counters are not unconditional literals
P9-QA-010 invariant booleans reflect mapped test failures
P9-QA-011 required report schema fields are present
P9-QA-012 critical_regressions are execution-derived
```

Suggested adversarial test:

Inject a synthetic failed `P9-REG-003` result.

Expected:

```text
dry-run invariant != true
provider_calls_in_dry_run must not misleadingly remain validated zero
```

Inject a NOT_RUN security node.

Expected:

```text
security invariant != true
```

---

# 6. Re-run QA

Run:

```text
python -m pytest tests/test_sending.py -v
python -m pytest tests/acceptance/test_phase9_acceptance.py -v
python -m pytest tests/acceptance/test_qa_integrity.py -v
python -m pytest tests/ -v
```

No live SMTP.

Regenerate:

```text
tests/reports/phase9_latest.json
README-TEST.md
SalesAI_export.zip
```

Require:

```text
final HEAD
==
phase9_latest git_commit
==
README-TEST commit
```

and recheck release denylist.

---

# 7. Freeze Rule

If:

- all deterministic Phase 9 cases remain PASS
- live SMTP remains NOT_RUN
- P9-REG-001..021 remain PASS
- safety counters/invariants are execution-derived
- required report schema is present
- false_pass_count = 0
- unmapped_deterministic_cases = 0
- full repository has zero failures
- ZIP denylist remains clean

then Phase 9 is formally FREEZE-ready.

No further production patch is requested.
