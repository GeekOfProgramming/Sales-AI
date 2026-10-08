# Phase 9 Attack-Test Round 2 Patch
## Final blockers before comprehensive Phase 9 Golden QA

Phase 8 remains frozen.

The original five adversarial paths are substantially fixed and the targeted
adversarial tests pass. However, two remaining workflow/security violations
were independently reproduced against the current ZIP.

Do NOT start Phase 10 execution yet.
Do NOT run final Phase 9 Golden QA until these are fixed.

---

# BLOCKER 1 — Server Sender Is Still Silently Invented

Current production code still contains fallback behavior equivalent to:

```python
resolved_sender = (
    d.get("sender_email")
    or trusted_sender
    or "outreach@pybim.com"
)
```

and SendOrchestrator also contains:

```python
os.environ.get("SMTP_FROM_EMAIL", "outreach@pybim.com")
```

Independent reproduction with:

```text
SMTP_FROM_EMAIL unset
draft.sender_email = None
```

resulted in:

```text
import_sender   = outreach@pybim.com
approval        = approved
send_status     = sent
provider_calls  = 1
actual_from     = outreach@pybim.com
```

So the stated contract:

"without a valid configured sender, approval/send is blocked"

is not currently true.

The previous hash mismatch is fixed because both approval and send now use the
same invented fallback, but this still violates trusted sender configuration.

## Required fix

Remove all hardcoded production sender fallbacks.

No:

```text
outreach@pybim.com
```

may be silently synthesized by:

- ApprovalService
- SendOrchestrator
- SMTPEmailSender
- API request handling

Trusted sender resolution:

```text
SMTP_FROM_EMAIL
or explicitly configured trusted alias
```

only.

If neither exists:

```text
sender_config_missing
```

Provider calls:

```text
0
```

Recommended import policy:

- draft may enter review with unresolved sender only if UI clearly marks it invalid
  and approval is impossible
- preferred: resolve trusted sender at import
- if trusted sender is unavailable, import may fail/skip with a structured error

Approval MUST fail if sender is not trusted/configured.

Send MUST fail if trusted server sender configuration is absent.

Do not let a user-supplied sender become trusted merely because it is present
in the imported draft.

## Add regression

P9-REG-013 — Missing trusted sender configuration blocks approval/send.

Test:

```text
unset SMTP_FROM_EMAIL
draft.sender_email = None
```

Expected:

```text
approval blocked with sender_config_missing
provider_calls = 0
```

Also test:

```text
unset SMTP_FROM_EMAIL
draft.sender_email = attacker@example.com
```

Expected:

```text
approval/send blocked
```

unless that address is explicitly present in a trusted server-side alias config.

---

# BLOCKER 2 — Sent Revision Is Not Terminal

Independent reproduction:

1. Import revision 1.
2. Approve revision 1.
3. Send revision 1 successfully.
4. Call `reject_draft()` on revision 1.
5. The sent row changes from:

```text
approved / sent / sent
```

to:

```text
rejected / not_sent / draft_ready
```

while `sent_at` remains populated.

6. Because send_status is no longer `sent`, `edit_draft()` then permits creation
   of revision 2 from that historically sent draft.

This violates sent-revision immutability and the Phase 9 state machine.

The same class of issue applies to `request_changes()` unless explicitly blocked.

## Required fix

A successfully sent revision is terminal.

After:

```text
send_status = sent
```

the following must be rejected:

```text
approve
reject
request_changes
edit-in-place/state reset
import overwrite
manual state transition back to not_sent
sending
failed
blocked
```

Historical sent revision remains readable only.

If a real follow-up/re-engagement is needed, create an explicit NEW follow-up
draft/revision through a dedicated workflow, never by changing the state of the
sent revision.

## Service-level guard

Before:

```text
reject_draft
request_changes
approve_draft
```

if exact revision has:

```text
send_status == sent
```

raise:

```text
invalid_state_transition
```

or:

```text
sent_revision_immutable
```

## Store-level defense in depth

`ReviewStore.update_draft_status()` should reject mutations that attempt to
move a row whose current `send_status == sent` to another workflow/send state.

Allow only explicitly safe metadata reads; do not silently reset terminal state.

Be careful not to block the legitimate internal transition:

```text
sending -> sent
```

The immutability rule begins after `sent` has been persisted.

## Add regressions

P9-REG-014 — Sent revision cannot be rejected.

P9-REG-015 — Sent revision cannot request changes.

P9-REG-016 — Sent revision cannot transition back to not_sent.

P9-REG-017 — Sent revision remains unchanged after rejected invalid transition.

Assertions:

```text
approval_status remains approved
send_status remains sent
outreach_status remains sent
sent_at unchanged
provider delivery count unchanged
historical audit remains intact
```

---

# REPORT METADATA MISMATCH

The provided ZIP currently contains:

```text
README-TEST Git Commit: 60c580c
phase9_latest Git Commit: 60c580c
```

The delivery report states:

```text
final commit: 0e35f2f
```

The ZIP has no `.git` directory by design, so the actual repository HEAD cannot
be independently reconstructed from the archive.

Before final Golden QA, regenerate the report and README from the exact final
repository HEAD.

Require:

```text
README commit
==
phase9_latest commit
==
final HEAD used to build ZIP
```

Do not manually type the commit ID into the report.

---

# INDEPENDENT RESULTS ON CURRENT ZIP

Archive file count:

```text
241
```

Security denylist inspection:

```text
.env                 absent
runtime *.db         absent
runtime *.sqlite     absent
chroma_db            absent
private keys         absent
credential/token     absent
```

Independent tests:

```text
5 adversarial regression tests:
5 PASS

Phase 9 acceptance:
15 PASS
1 SKIP (live SMTP)

QA integrity:
12 PASS
```

`tests/test_sending.py` in the independent environment:

```text
28 PASS
1 FAIL
```

The single failure is environment-only during API import:

```text
ModuleNotFoundError: ollama
```

not a Phase 9 send-path assertion failure.

---

# ORIGINAL FIVE ATTACKS STATUS

Keep all existing fixes:

1. provider payload hash vs approved hash
2. obsolete revision approval/send block
3. sent revision re-import overwrite block
4. opt-out preservation after human body edit
5. centralized provider-secret sanitization

Do not weaken or remove their existing tests.

---

# TARGETED TESTS REQUIRED AFTER PATCH

Run:

1. missing trusted sender config -> approval/send blocked
2. untrusted draft sender cannot self-authorize
3. sent revision reject blocked
4. sent revision request-changes blocked
5. sent revision state remains unchanged
6. existing re-import sent revision exploit remains blocked
7. existing obsolete revision exploit remains blocked
8. P9-REG-012 exact payload hash remains passing
9. existing opt-out edit preservation remains passing
10. existing secret sanitization remains passing
11. concurrency/idempotency remains passing

Then:

```text
python -m pytest tests/test_sending.py -v
python -m pytest tests/acceptance/test_phase9_acceptance.py -v
python -m pytest tests/acceptance/test_qa_integrity.py -v
python -m pytest tests/ -v
```

Do NOT run live SMTP.

After this patch, regenerate:

```text
tests/reports/phase9_latest.json
README-TEST.md
SalesAI_export.zip
```

Then return the ZIP for one short verification pass.

If these blockers are closed, proceed immediately to comprehensive
`PHASE9-QA-GOLDEN-SPEC.md` and Phase 9 freeze.
