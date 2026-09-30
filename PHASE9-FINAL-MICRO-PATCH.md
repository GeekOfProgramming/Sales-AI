# Phase 9 Final Micro-Patch
## Two residual issues before Golden QA and freeze

Phase 8 remains frozen.

Round-1 and Round-2 adversarial regressions are passing.
This micro-patch addresses two remaining contract mismatches found by independent verification.

---

# 1. Trusted Alias Is Approved But Cannot Send

Current behavior:

Server config:

```text
SMTP_FROM_EMAIL=primary@pybim.com
SMTP_ALLOWED_SENDERS=alias@pybim.com
```

Imported draft:

```text
sender_email=alias@pybim.com
```

Observed:

```text
import -> alias accepted
approve -> approved
send -> sender_mismatch
provider_calls = 0
```

Reason:

ApprovalService uses `get_trusted_senders()` and accepts both primary + allowed aliases.

SendOrchestrator only passes `SMTP_FROM_EMAIL` as the trusted sender to SendValidator.

So the current implementation has inconsistent trust semantics.

## Required fix

Choose ONE explicit policy and enforce it consistently:

### Preferred policy

Support trusted aliases.

Trusted sender set:

```text
SMTP_FROM_EMAIL
+
SMTP_ALLOWED_SENDERS
```

At send time:

```text
draft.sender_email must be a member of get_trusted_senders()
```

Do not require equality with only the primary sender.

The actual provider From email must remain exactly:

```text
draft.sender_email
```

No substitution.

If draft sender is not in the trusted set:

```text
sender_mismatch
provider_calls = 0
```

If trusted sender set is empty:

```text
sender_config_missing
provider_calls = 0
```

Add:

```text
P9-REG-018 trusted configured alias can approve and send
P9-REG-019 untrusted alias cannot approve or send
```

---

# 2. Sender Display Name Is Not Protected By Approval Fingerprint

Current behavior:

At approval:

```text
SMTP_FROM_NAME=Name At Approval
```

After approval but before send:

```text
SMTP_FROM_NAME=Changed After Approval
```

Observed provider payload:

```text
From: Changed After Approval <primary@pybim.com>
```

while:

```text
approved_content_hash
```

still passes because `sender_name/from_name` is not included in the fingerprint.

This violates the exact approved-payload rule in P9-REG-012.

## Required fix

Finalize sender display name BEFORE review/approval.

Recommended implementation:

Add to StoredDraft:

```text
sender_name: Optional[str]
```

At import/revision creation:

```text
sender_name = trusted server SMTP_FROM_NAME
```

Do not read a new sender name at send time.

Include sender_name in:

```text
compute_content_fingerprint()
```

Human review should expose:

```text
sender_name
sender_email
```

SendOrchestrator must use:

```text
from_name=draft.sender_name
from_email=draft.sender_email
```

not current environment values.

Therefore:

```text
approved logical payload
==
provider logical payload
```

for:

```text
From Name
From Email
To Email
Subject
Body
Revision
```

If server sender identity intentionally changes, create a new reviewable revision or require reapproval.

Do not silently change the From display name after approval.

Add regressions:

```text
P9-REG-020 sender display name is fingerprinted
P9-REG-021 changing SMTP_FROM_NAME after approval does not mutate provider payload
```

Preferred expected behavior for REG-021:

```text
provider uses the sender_name stored on the approved revision
```

Alternative acceptable behavior:

```text
send blocked approval_stale
```

but silently using the changed name is NOT acceptable.

---

# 3. Database Migration

If `sender_name` is added:

- additive migration only
- existing rows may be nullable
- new reviewable/imported rows resolve it from trusted server config
- sent historical revisions remain immutable
- no destructive migration

---

# 4. P9-REG-012 Expansion

The final exact-payload regression must compare:

```text
approved sender_name
approved sender_email
approved recipient_email
approved subject
approved body
```

against the exact logical fields passed to BaseEmailSender.

All must match.

---

# 5. Existing Invariants Must Remain

Do not regress:

```text
P9-REG-001..017
stale revision guards
sent terminal immutability
re-import sent protection
suppression
dry-run zero provider calls
concurrency/idempotency
opt-out pre-approval inclusion
secret sanitization
EMAIL_SEND_ENABLED=false
no LLM in Phase 9
release ZIP denylist
```

---

# 6. Targeted Tests

Run:

```text
P9-REG-018 trusted alias sends
P9-REG-019 untrusted alias blocked
P9-REG-020 sender display name fingerprinted
P9-REG-021 env sender-name change after approval cannot change provider payload
P9-REG-012 exact provider payload still matches approved payload
```

Then:

```text
python -m pytest tests/test_sending.py -v
python -m pytest tests/acceptance/test_phase9_acceptance.py -v
python -m pytest tests/acceptance/test_qa_integrity.py -v
python -m pytest tests/ -v
```

Do not run live SMTP.

Regenerate:

```text
tests/reports/phase9_latest.json
README-TEST.md
SalesAI_export.zip
```

Require report commit == README commit == final HEAD.

After these two residual issues are closed, proceed directly to comprehensive
PHASE9-QA-GOLDEN-SPEC.md. No further architecture patch is expected unless Golden QA finds a new deterministic violation.
