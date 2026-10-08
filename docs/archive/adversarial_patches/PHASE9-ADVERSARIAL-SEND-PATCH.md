# Phase 9 Adversarial Send-Path Patch
## Required before Phase 9 Golden QA

Do NOT start Phase 10 implementation yet.
Phase 8 remains frozen.

This patch addresses concrete adversarial failures reproduced against the
current Phase 9 production code.

---

# 1. Critical: Approved Hash Can Differ From Actual Sender

Reproduction:

- Import a valid draft with `sender_email = None`.
- Approve it.
- `approved_content_hash` is computed with an empty sender email.
- SendOrchestrator sends using configured/default `SMTP_FROM_EMAIL`.
- Send succeeds.
- Recomputing the fingerprint from the actual provider payload does NOT equal
  `approved_content_hash`.

This violates P9-REG-012.

## Required fix

The sender identity must be finalized BEFORE review/approval.

Preferred contract:

1. Resolve trusted sender identity from trusted server configuration during
   import/final-payload preparation.
2. Store that exact sender identity on the draft revision.
3. Human review sees the sender identity.
4. Approval fingerprint includes the exact resolved sender identity.
5. Send uses exactly the same stored/approved sender identity.
6. Any trusted sender configuration change after approval must cause
   `approval_stale` or require a new revision/reapproval.

Do not allow `sender_email=None` to pass approval and later send from a
different address.

For real sending, do not silently invent a default sender such as
`outreach@pybim.com`.

If a trusted sender is not explicitly configured:

`sender_config_missing`

and provider call count must be zero.

Strengthen:

- P9-REG-010
- P9-REG-012
- P9-HASH-005

Add a case where the imported draft has no sender email.

Expected:
actual provider payload fingerprint == approved_content_hash.

---

# 2. Critical: Obsolete Revision Can Be Approved and Sent

Reproduction:

1. Import revision 1.
2. Edit it -> revision 2 becomes current review target.
3. Call `approve_draft(draft_id, revision=1)`.
4. Revision 1 becomes approved.
5. Send revision 1.
6. Provider sends successfully.

This violates the Phase 9 revision contract.

## Required fix

Approval must only be allowed for the current/latest reviewable revision.

Before approve/reject/request-changes/send:

resolve latest revision for `draft_id`.

If requested revision is not current/latest:

return controlled error:

`stale_revision`

or equivalent.

Do not allow an obsolete revision to be newly approved after a newer revision
exists.

Historical approved/sent revisions remain readable but not mutable.

Strengthen:

- P9-APPROVE-003
- P9-STATE-003
- P9-REG-009

---

# 3. Critical: Re-import Can Overwrite a Sent Revision

Current code obtains:

`existing = self.store.get_draft(draft_id, revision)`

but does not enforce it.

`save_draft()` uses `INSERT OR REPLACE`.

Reproduction:

1. Import draft revision 1.
2. Approve and send it.
3. Re-import the same `draft_id`, same revision 1, but with a new recipient
   email and body.
4. Existing sent revision is replaced with:
   `pending_review / not_sent`.
5. Approve it again.
6. Because recipient changed, `send_key` changes.
7. A second provider delivery succeeds.

This is a serious history and duplicate-send bypass.

## Required fix

Draft revisions are immutable identities.

Never use destructive `INSERT OR REPLACE` for an existing
`(draft_id, revision)`.

Recommended rules:

- new `(draft_id, revision)` -> INSERT
- existing identical revision -> idempotent no-op / return existing
- existing revision with different content -> reject:
  `revision_conflict`
- existing sent revision -> always immutable
- to change content -> create a new revision

Use database constraints and explicit comparison.

Never overwrite historical approved/sent content.

Strengthen:

- P9-STORE-005
- P9-DB-003
- P9-IDEMP cases
- sent revision immutability tests

Add adversarial regression proving changed recipient/body cannot overwrite
revision 1 after it was sent.

---

# 4. Critical If Opt-Out Is Configured: Human Edit Can Remove It

Reproduction:

1. Configure `OUTREACH_OPT_OUT_TEXT`.
2. Import draft -> footer is appended.
3. Edit body -> new revision body omits footer.
4. Approve revision 2.
5. Send.
6. Provider sends without configured opt-out footer.

If the opt-out policy is configured as server-owned, this is a bypass.

## Required fix

Create one canonical function:

`prepare_final_send_payload(...)`

It must run whenever a new revision is created.

Server-owned send transformations must be applied before the revision is
reviewed and fingerprinted:

- opt-out footer
- server-owned signature, if any
- deterministic normalization

A human body edit must produce a new final payload revision that still contains
mandatory server-owned content.

Do not append anything after approval.

Strengthen:

- P9-OPTOUT-001
- P9-OPTOUT-003
- P9-REG-012

Test body edit after import while opt-out is configured.

Expected:
the new reviewable revision includes the footer BEFORE approval.

---

# 5. Critical: Provider Error Secrets Are Not Sanitized Centrally

SMTPEmailSender sanitizes its own username/password.

But SendOrchestrator trusts arbitrary provider `error_message` and writes it
directly into:

- returned SendResult
- send_attempts.error_message
- review_events.review_note

Reproduction with an injected provider returning:

`SMTP_PASSWORD=supersecret Authorization: Bearer token123`

Result:
the complete secret appears in all three locations.

## Required fix

Secret/error sanitization must exist at the orchestration/audit boundary,
not only inside one provider.

Create a centralized sanitizer.

At minimum redact:

- configured SMTP password
- configured SMTP username where appropriate
- Authorization headers/tokens
- Bearer tokens
- common credential key/value forms
- known server-side secrets

Provider adapters may sanitize too, but the orchestrator must sanitize again
before persistence/API return.

Never store raw provider exceptions.

Strengthen:

- P9-REG-011
- P9-SEC-001..006
- P9-AUDIT-012
- P9-ATTEMPT-004

---

# 6. Pre-Approval Validation

Current approval fingerprints content but does not fully validate that the
revision is send-valid.

A human can approve an invalid subject/body, and sending later blocks it.

This is safe at send time but weak workflow semantics.

Before setting:

`approval_status = approved`

run deterministic draft validation excluding only send-time conditions such as:

- sending enabled
- suppression state
- rate limit
- already sent

Validate at approval:

- current/latest revision
- subject/body non-empty
- Phase 8 hard limits
- unresolved placeholders
- valid recipient email
- finalized sender identity

Invalid drafts should not become approved.

---

# 7. P9-REG-012 Must Cover All Actual Provider Fields

The current baseline test covers a fixture where `sender_email` already equals
the runtime sender.

Expand P9-REG-012 to cover:

- sender missing at import
- trusted sender changed after approval
- recipient tamper
- subject tamper
- body tamper
- opt-out edit flow
- final provider To
- final provider From
- final provider Subject
- final provider Body

If sender display name is part of the actual From identity, either include it
in the approval fingerprint or freeze it as an immutable trusted server
configuration associated with the revision.

The exact approved logical payload must equal the logical payload delivered to
the provider.

---

# 8. Reporting Metadata Is Stale

The current archive reports Phase 9 commit:

`dae1863`

while the stated implementation commit is:

`c393783`.

After this patch and before Golden QA:

regenerate:

- `tests/reports/phase9_latest.json`
- `README-TEST.md`

from final HEAD.

Require:

README commit == Phase 9 report commit == final HEAD.

---

# 9. Existing Verified Baseline Must Remain

Do not regress:

- stale body tamper -> approval_stale
- unapproved send -> blocked
- suppression -> blocked
- dry-run -> zero provider calls
- concurrency baseline -> one delivery
- approval itself -> zero delivery
- packaging denylist
- no runtime DB/.env in ZIP
- no LLM in send path

---

# 10. Required Targeted Tests Before Full Golden QA

Run targeted tests for:

1. missing sender identity cannot produce hash/send mismatch
2. obsolete revision cannot be approved
3. obsolete revision cannot be sent as current outreach
4. sent revision cannot be replaced by re-import
5. re-import changed recipient cannot generate second delivery on same revision
6. configured opt-out survives human edit into new revision
7. generic provider error secrets are redacted before API/audit persistence
8. final provider logical payload hash equals approved hash
9. current revision can still approve/send normally
10. existing concurrency/idempotency tests still pass

Then run:

```text
python -m pytest tests/test_sending.py -v
python -m pytest tests/acceptance/test_phase9_acceptance.py -v
python -m pytest tests/acceptance/test_qa_integrity.py -v
python -m pytest tests/ -v
```

Do not execute live SMTP.

Only after these issues are closed should the comprehensive
`PHASE9-QA-GOLDEN-SPEC.md` suite be considered for final Phase 9 freeze.
