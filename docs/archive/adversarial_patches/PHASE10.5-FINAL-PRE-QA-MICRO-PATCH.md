# Phase 10.5 Final Pre-QA Micro-Patch

## Scope
Focused integration fixes only. No redesign.

Status:
- Phase 9: FROZEN
- Phase 10: ACCEPTED / CLOSED
- Phase 10.5: BUILT + ADVERSARIAL PATCHED
- Phase 10.5 Golden QA: WAIT until this micro-patch passes

Independent verification confirmed the previous patch is materially present:
- domain suppression reaches Phase 9 SendValidator
- CSRF + Same-Origin checks exist
- historical import Preview -> Confirm exists
- Request Changes exists
- exact send confirmation modal exists
- safe evidence URL filtering exists
- localhost default bind exists
- release filename denylist is clean

## BLOCKER 1 — Generate Draft bypasses frozen Phase 8

Current:
`POST /sales/candidates/{candidate_id}/generate-draft`
constructs subject/body directly inside the UI router.

This violates the Phase 10.5 architecture:

Browser -> Console -> Phase 8 Draft Generation -> Phase 9 Review/Approval/Send

The UI must not become another email-generation engine.

Required:
- Delegate to the existing Phase 8 outreach stack:
  - OutreachOrchestrator
  - OutreachContextBuilder
  - EmailGenerator
  - DraftValidator
  - or the existing authoritative generate-drafts service layer.
- Prefer direct service invocation over an internal HTTP self-call.
- Phase 8 remains authoritative for:
  - grounding
  - active-service selection
  - recipient eligibility
  - subject/body generation
  - evidence_refs
  - language
  - tone
  - generation_model
  - prompt_version
  - draft validation
- Import the resulting Phase 8 EmailDraft into Phase 9 pending_review.
- Remove UI-authored outreach copy such as:
  `We noticed {company} is advancing BIM delivery.`

Add:
- P10_5-FINAL-001 Generate Draft delegates to frozen Phase 8
- P10_5-FINAL-002 UI router contains no hardcoded generated outreach subject/body
- P10_5-FINAL-003 generated Phase 9 draft preserves Phase 8 evidence/service/model metadata
- P10_5-FINAL-004 insufficient grounding returns controlled no-draft result

## BLOCKER 2 — Real Phase 10 qualification outcome is not recognized

Current UI checks roughly:
- discovery_status == "qualified"
- or qualification_outcome == "qualified"

But frozen Phase 10 uses:
`qualified_for_enrichment`

Required authoritative eligibility:
- qualification_outcome == qualified_for_enrichment
- cold_outreach_allowed == true
- suppressed == false
- active_conversation == false
- no historical cold-outreach guard
- not watchlist

Legacy `discovery_status == qualified` may remain only as compatibility projection.

Add:
- P10_5-FINAL-005 actual Phase 10 qualified_for_enrichment candidate becomes draft-eligible
- P10_5-FINAL-006 watchlist/known/reengagement/suppressed outcomes remain ineligible

## BLOCKER 3 — Arbitrary typed contact bypasses Phase 6 eligibility

Current Generate Draft accepts browser-typed:
- contact_email
- contact_name
- contact_title

This bypasses the Phase 6 contact contract.

Required:
- Select an existing backend contact by `contact_id`.
- Server resolves name/title/work_email/email_status/identity.
- Normal cold draft path requires an existing usable business work email.
- Default eligible statuses: verified / likely.
- risky / unknown / not_found must not enter the normal send-capable draft path.
- Suppression is checked server-side after contact resolution.
- Do not trust browser-submitted identity fields.

Add:
- P10_5-FINAL-007 arbitrary typed email cannot bypass Phase 6 eligibility
- P10_5-FINAL-008 existing verified/likely contact can be selected
- P10_5-FINAL-009 risky/unknown/not_found contact cannot enter normal cold draft path

## BLOCKER 4 — Do not fabricate sender identity in UI

Current templates contain visual fallbacks such as:
- `pyBIM Team`
- `sales@pybim.it`

The send confirmation must display the exact server-authoritative payload.

Required:
- If `draft.sender_email` is missing, show `Sender not configured`.
- Disable real-send action.
- Display stored/finalized Phase 9 sender_name only.
- Do not visually invent a sender.

Add:
- P10_5-FINAL-010 send preview never invents sender identity
- P10_5-FINAL-011 exact stored sender identity is shown

## BLOCKER 5 — Credential status must use SourceConfig.credential_env_var

Current Sources UI guesses env variable names from source ID/name.

Phase 10 already exposes:
`SourceConfig.credential_env_var`

Required:
```python
is_configured = bool(
    source.credential_env_var
    and os.getenv(source.credential_env_var)
)
```

Never expose the value.

If credentials are required but `credential_env_var` is missing:
`Configuration error`

Add:
- P10_5-FINAL-012 credential status uses configured credential_env_var

## Release metadata

The submitted report states:
- Git HEAD: b8ab456
- Release Code Commit: 1786acf
- README-TEST Commit: 1786acf
- phase10_latest Commit: 1786acf

For the final Phase 10.5 pre-QA release:
- final Git HEAD == README-TEST Git Commit

If phase10_latest intentionally identifies the frozen Phase 10 baseline, label it explicitly as:
`Phase 10 frozen baseline commit`
and do not present it as current HEAD.

When Phase 10.5 Golden QA is created, its report must reference current HEAD.

## Required execution

Run:
- python -m pytest tests/test_phase10_5_console.py -v
- python -m pytest tests/test_phase10_5_adversarial.py -v
- python -m pytest <phase10_5_final_micro_tests> -v
- python -m pytest tests/ -q

No live email.
No autonomous send.
Use mock provider for delivery-path tests.

## Gate to Golden QA

Require:
- P10_5-REG-001..020 PASS
- P10_5-ADV-001..024 PASS
- P10_5-FINAL-001..012 PASS
- Generate Draft uses Phase 8
- actual qualified_for_enrichment candidate is eligible
- contact comes from backend eligible records
- no arbitrary recipient bypass
- no invented sender identity
- credential status uses credential_env_var
- full repository has zero failures
- release denylist clean
- metadata synchronized

Do not declare Phase 10.5 frozen yet.

After this micro-patch passes, proceed directly to dedicated Phase 10.5 Golden QA.
