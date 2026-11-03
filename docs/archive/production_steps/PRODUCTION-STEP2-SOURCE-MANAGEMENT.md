# SalesAI Production Readiness — Step 2
## Source Management
### Persistent Source Registry + Safe Operator Management

# 0. Baseline

Use the current repository after Production Readiness Step 1.

Step 1 is accepted:

```text
DiscoveryScope
DiscoveryScopeStore
DiscoveryRunner
run_discovery(scope_id)
```

Do NOT redesign frozen Phase 1–12 behavior.

Do NOT change Phase 5 scoring, Phase 9 suppression/send authority, Phase 10 qualification,
Phase 11 conversations, or Phase 12 analytics authority.

---

# 1. Goal

The operator must be able to manage discovery sources safely from the local SalesAI system.

Required capabilities:

```text
list sources
view source details
add source
edit source configuration
enable / disable source
set priority
set max results
define supported markets
define supported discovery modes
define credential requirement
show credential status
show source health
test connection / test availability
record source-specific query/config instructions
```

A source definition is configuration, NOT executable arbitrary code.

---

# 2. Source Model

Create or normalize one authoritative source configuration model.

Recommended fields:

```text
source_id
name
source_type
description

enabled
priority

supported_markets[]
supported_languages[]
supported_discovery_modes[]

base_url
search_url_template optional
query_template optional

requires_api_key
credential_env_var

max_results_per_run
timeout_seconds

adapter_id
adapter_status

operator_instructions
notes

created_at
updated_at
```

Optional where useful:

```text
rate_limit_per_minute
request_delay_ms
supports_company_first
supports_job_first
supports_project_tender_first
```

---

# 3. Allowed Source Taxonomy

Preserve the canonical Phase 10 source taxonomy.

At minimum support the existing documented categories such as:

```text
search_engine
ats
job_board
company_directory
business_directory
map_business
professional_association
tender_portal
project_database
news
company_site
manual_seed
historical_import
```

If the repository already has canonical names, use those exact names.

Unknown source types must be rejected.

Do not silently accept arbitrary source types.

---

# 4. Source Identity

`source_id` must be stable and unique.

Changing the display name must not create a new source.

Do not reuse one `source_id` for two unrelated providers.

---

# 5. Persistence

Persist source configuration in the existing SourceRegistry/config architecture.

Preferred:

```text
single authoritative registry
```

Do not create a second disconnected source registry.

If YAML remains the source of truth, mutations must update it safely/atomically.

If SQLite becomes the source of truth, provide deterministic import/export compatibility
with existing YAML source definitions.

Do not maintain two competing writable truths.

---

# 6. Add Source

Operator can add a new source using configuration fields only.

Adding a source must NOT allow:

```text
arbitrary Python
shell command
PowerShell command
JavaScript
SQL
template code execution
```

`operator_instructions` and `query_template` are text/config data only.

---

# 7. Adapter Binding

Each executable source must resolve to a known adapter:

```text
adapter_id
```

Examples:

```text
brave_search
generic_company_directory
generic_job_board
manual_seed
historical_import
```

If a source has no implementation/adapter:

```text
adapter_status = NOT_AVAILABLE
```

and discovery must report it honestly rather than silently pretending it ran.

Do not dynamically `eval()` or import arbitrary operator-supplied module paths.

---

# 8. Credential Safety

For sources requiring credentials, store ONLY:

```text
credential_env_var
```

Example:

```text
BRAVE_SEARCH_API_KEY
```

Never store or render the secret value in:

```text
SQLite
YAML committed to Git
HTML
API responses
logs
reports
ZIP
```

Credential status may be:

```text
configured
not_configured
configuration_error
not_required
```

Status is determined server-side.

---

# 9. API Key Entry Policy

For this step, do NOT create permanent raw-secret storage in the UI.

If an operator wants to configure a credential, the UI should provide:

```text
required environment variable name
setup instructions
current status
```

Example:

```text
Credential required:
BRAVE_SEARCH_API_KEY

Status:
Not configured
```

Actual secure credential onboarding will be handled in the installation/setup phase.

---

# 10. Enable / Disable

Operator can enable/disable a source.

Disabled source:

```text
never executes
```

even if selected in a DiscoveryScope.

Existing historical observations from that source remain visible.

Disabling a source does not delete its historical evidence.

---

# 11. Priority

Support deterministic priority, for example:

```text
1 = highest
100 = lowest
```

or the existing project convention.

Document the direction clearly.

Priority controls execution order only.

It must not alter Phase 5 score or lead qualification.

---

# 12. Max Results

Per-source:

```text
max_results_per_run
```

must be enforced.

Final execution budget is the strictest applicable limit between:

```text
DiscoveryScope.max_results
SourceConfig.max_results_per_run
adapter/provider limit
```

Do not let a source bypass the scope budget.

---

# 13. Supported Markets

Source may declare supported markets.

If scope selects:

```text
Italy
Germany
```

and source only supports:

```text
Italy
```

execute it only for Italy.

Record unsupported market skips explicitly.

---

# 14. Supported Discovery Modes

Source may support one or more:

```text
job_first
company_first
project_tender_first
manual_historical
```

Do not execute a source in an unsupported mode.

Do not convert project/company results into fake jobs.

---

# 15. Test Connection / Test Availability

Implement a safe test action.

Recommended states:

```text
AVAILABLE
NOT_CONFIGURED
NOT_AVAILABLE
AUTH_ERROR
NETWORK_ERROR
RATE_LIMITED
INVALID_CONFIG
```

The test must:

```text
use a very small request budget
not create CandidateCompany
not modify analytics
not send email
not mutate operational data
```

If deterministic local validation is enough, do that first.

External test calls should be explicit operator actions only.

---

# 16. Source Health

Persist/display health:

```text
last_test_at
last_success_at
last_failure_at
last_error_sanitized
consecutive_failures
last_result_count
```

Never expose credentials in errors.

Historical health must survive process restart.

---

# 17. Source Management API

Add or normalize:

```text
GET    /api/sales/sources
GET    /api/sales/sources/{source_id}
POST   /api/sales/sources
POST   /api/sales/sources/{source_id}/update
POST   /api/sales/sources/{source_id}/enable
POST   /api/sales/sources/{source_id}/disable
POST   /api/sales/sources/{source_id}/test
```

If REST conventions in the repo prefer PUT/PATCH, follow the existing style.

No mutating GET.

---

# 18. Local UI

Upgrade:

```text
/sales/sources
```

from a monitoring page into a management page.

Required UI:

```text
Source list
Enabled / Disabled badge
Source type
Priority
Supported markets
Discovery modes
Credential status
Adapter status
Last success/failure
Test Source
Edit
Enable / Disable
Add Source
```

Add/edit form must not expose raw credential values.

---

# 19. Add/Edit Source UI

Form fields:

```text
name
source_id (create only or immutable)
source_type
description
enabled
priority
supported markets
supported discovery modes
supported languages
base URL
query/search template
requires credential
credential_env_var
max results
timeout
adapter
operator instructions
notes
```

Validate server-side.

Browser values are untrusted.

---

# 20. Search Scope Compatibility

Step 1 `DiscoveryScope.source_ids` must resolve against the authoritative SourceRegistry.

If a saved scope references:

```text
deleted / unavailable / disabled source
```

the discovery run must not crash.

Record:

```text
source skipped
reason
```

Do not silently substitute another source.

---

# 21. Delete Policy

Prefer:

```text
disable / archive
```

over hard delete.

A source that already has observations/history should not be physically deleted by default.

If hard delete is supported for never-used sources, require explicit confirmation.

---

# 22. Source Import / Export

Support safe configuration export:

```text
JSON
YAML
```

Optional import is allowed if validated.

Reject:

```text
unknown source types
unknown adapters
secret values
unexpected executable fields
unsafe URLs where relevant
```

Never import API key values into committed source config.

---

# 23. URL Validation

For base/search URLs allow:

```text
https
http (only where explicitly needed for local/test)
```

Reject dangerous schemes:

```text
file:
javascript:
data:
ftp:
```

unless an existing trusted local adapter explicitly requires otherwise.

---

# 24. Existing Sources

Do not lose existing source definitions.

Existing production source IDs must remain stable.

Migrate/normalize existing configs rather than replacing them with a clean empty registry.

---

# 25. Search Engine Example

Existing Brave configuration should remain supported:

```text
source_id: brave_job_search or existing canonical ID
source_type: search_engine
requires_api_key: true
credential_env_var: BRAVE_SEARCH_API_KEY
```

Do not hardcode the actual API key.

---

# 26. Manual / Historical Sources

Manual and historical sources:

```text
manual_seed
historical_import
```

do not require a network Test Connection.

Their health/test state should truthfully indicate:

```text
LOCAL / AVAILABLE
```

or equivalent.

---

# 27. Security

Source Management must never allow the operator to turn the UI into an arbitrary command runner.

Explicitly prohibit:

```text
eval
exec
subprocess from operator instructions
shell=True from UI values
dynamic arbitrary Python imports
raw SQL from UI
```

---

# 28. Audit

Record source configuration changes:

```text
source_created
source_updated
source_enabled
source_disabled
source_tested
```

At minimum preserve:

```text
source_id
timestamp
action
safe changed fields
```

Do not log credential values.

---

# 29. Required Tests

Create focused tests:

```text
PRD-SOURCE-001 existing sources migrate/load without ID change
PRD-SOURCE-002 add valid source persists and reloads
PRD-SOURCE-003 unknown source type rejected
PRD-SOURCE-004 unknown adapter rejected or marked NOT_AVAILABLE safely
PRD-SOURCE-005 enable/disable controls execution
PRD-SOURCE-006 credential value never stored/rendered/logged
PRD-SOURCE-007 credential status uses credential_env_var
PRD-SOURCE-008 per-source max_results enforced
PRD-SOURCE-009 unsupported market is skipped explicitly
PRD-SOURCE-010 unsupported discovery mode is skipped explicitly
PRD-SOURCE-011 test connection creates no candidates/operational mutations
PRD-SOURCE-012 source health persists after restart
PRD-SOURCE-013 disabled source history remains visible
PRD-SOURCE-014 saved DiscoveryScope handles disabled/unavailable source safely
PRD-SOURCE-015 no mutating GET
PRD-SOURCE-016 unsafe URL scheme rejected
PRD-SOURCE-017 source instructions cannot execute arbitrary code
PRD-SOURCE-018 source config export contains no secrets
PRD-SOURCE-019 source management UI renders without LLM calls
PRD-SOURCE-020 full existing Phase 1–12 + Step 1 regression remains green
```

---

# 30. Full Regression

Run:

```text
python -m pytest tests/test_source_management.py -v
python -m pytest tests/test_search_scope.py -v
python -m pytest tests/ -q
```

No live email.

External source Test Connection should be mocked for deterministic tests.

---

# 31. Packaging

Rebuild:

```text
SalesAI_export.zip
```

Deny:

```text
.env*
*.db
*.sqlite*
credentials
tokens
private keys
runtime logs
.venv
__pycache__
*.pyc
```

---

# 32. Completion Gate

Step 2 is accepted when:

```text
existing sources preserved
new source can be added safely
source can be edited
enable/disable works
priority and max result work
market/mode compatibility works
credential status is truthful
no secret value is stored
health/test source works
Step 1 scopes continue to work
no arbitrary code execution exists
full repository has zero new deterministic failures
release ZIP is clean
```

Do NOT proceed to Step 3 yet.

Return the ZIP for review first.

---

# 33. Final Response

Return:

1. files/modules created or modified
2. authoritative SourceConfig schema
3. persistence/source-of-truth decision
4. existing-source migration behavior
5. Source Management API
6. `/sales/sources` UI changes
7. credential handling
8. source health/test behavior
9. Step 1 compatibility
10. focused test result
11. full repository result
12. Git commit
13. ZIP file count
14. denylist result
15. final `SalesAI_export.zip`
