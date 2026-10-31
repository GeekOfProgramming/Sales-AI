# SalesAI MVP-01.4.2 — Final Import Transaction Safety & Operator Release Gate

**Document Version:** 1.0.0  
**Release Target:** MVP-01.4.2 Hotfix & Operator Release Gate  
**Authoritative Workbook:** `Emails-10-27-2026-20-25.xlsx` (Supersedes `Emails-10-27-2026-18-19.xlsx`)  
**Workspace:** `C:\Sales-AI\SalesAI`  
**Execution Mode:** STRICTLY OFFLINE (No live Brave API calls, no LLM inference, no email dispatches, zero production DB mutations)

---

## 1. Executive Summary & Authoritative Workbook Analysis

The authoritative pyBIM workbook for operational readiness is:
`C:\Sales-AI\Emails-10-27-2026-20-25.xlsx`

Inspection against `HistoricalImporter` yields the following baseline metrics:

| Metric | Measured Value | Operational Meaning |
|---|---:|---|
| **Total Worksheets** | **14** | 9 company-source sheets, 5 dashboard/summary/empty sheets |
| **Company-Source Rows** | **283** | Exactly 283 historical leads across all target European markets |
| **Primary Websites Extracted** | **282** | 282 valid, canonical root/landing company domains identified |
| **Rows Without Primary Website** | **1** | Italy R20 (`riccardobarzon29.wixsite.com/studio-barzon-ceola`, no standalone commercial root) |
| **Distinct Primary Hosts** | **282** | 282 unique canonical corporate hosts |
| **Cross-Row Host Collisions** | **0** | Clean company isolation; no two rows share the same corporate domain |
| **Multiple Plausible Host Rows** | **7** | All 7 in `Austria` (R9, R11, R12, R13, R14, R15, R20) containing corporate + secondary hosts |
| **Multiline Website Cells** | **71** | Cells containing multiple rich URLs, deep project paths, or social profiles |
| **Total Extracted Links** | **741** | Full child link catalog preserved in `historical_company_links` |
| **Placeholders (`[link removed]`)** | **95** | Preserved verbatim with Persian category labels for operator audit |
| **Populated Contact Person Names** | **283** | 100% liaison name capture (`شخص مسئول / رابط`) |
| **Historical Outreach Events** | **142** | 137 Email Sent, 141 Initial Review, 2 Interview, 1 Wrong Email, 2 Follow-up |

---

## 2. Defects Fixed in MVP-01.4.2

### Defect 1: Unenforced `REVIEW_REQUIRED` & Missing Operator Decision Schema
- **Prior Problem:** Preview returned `status: "REVIEW_REQUIRED"` when ambiguous rows or missing domains existed, but the server-side confirm endpoint (`/sales/sources/import-history/confirm`) only blocked `BLOCKED` states, allowing unreviewed ambiguous rows to be imported blindly.
- **Resolution:**
  - Implemented single shared evaluator function `evaluate_import_readiness()` used identically by both `/preview` and `/confirm`.
  - Unified canonical enum `OperatorDecisionType` across UI, Router, and Importer:
    * `ACCEPT_DEFAULT`: Keep proposed default host after human review.
    * `SELECT_PRIMARY_HOST`: Select one of the verified candidate hosts.
    * `QUARANTINE_NO_PRIMARY`: Quarantine company from primary domain trust (no verified corporate domain).
    * `EXTERNAL_REFERENCE`: Treat all links as external references only.
  - Added interactive controls in `backend/sales_ui/templates/sources.html` for all 7 Austria ambiguous rows and the 1 Italy missing-domain row (Studio di architettura Barzon-Ceola).
  - The confirm button remains disabled until all 8 required row decisions are explicitly resolved by the operator.
  - Added new SQLite table `operator_import_decisions` storing row-level operator instructions:
    * Provenance: `operator_id`, `resolved_domain`, `notes`, `created_at`.
    * Unique constraint on `(source_file, sheet_name, row_number)`.
  - The confirm endpoint strictly validates `operator_decisions_json`: rejects unknown decision types, duplicate rows, invalid row indices, mismatched company names, unlisted candidate hosts, and SSRF/unsafe domains. If any required decision is missing or invalid, it returns `HTTP 400 Bad Request` before initiating backup or import.

### Defect 2: Missing Automatic Pre-Import Backup & Verification
- **Prior Problem:** The confirm route mutated the database in-place without capturing a verified backup immediately before execution.
- **Resolution:**
  - Prior to any write operation, `confirm_historical_import_action` invokes `store.create_backup("pre_import_20_25")`.
  - The generated backup file is immediately validated using `store.verify_backup(backup_path)`.
  - If backup creation or SQLite verification fails for any reason, the request fails closed immediately with `HTTP 500` and aborts before altering a single row.

### Defect 3: Non-Atomic Multi-Row Import Operations
- **Prior Problem:** Importing 283 rows iterated through records sequentially. A database failure or exception on row 150 would leave the database in an inconsistent, partially imported state.
- **Resolution:**
  - Implemented an all-or-nothing staged transaction boundary using SQLite's native online backup API (`src_conn.backup(clone_conn)`).
  - The staged clone executes the full 283-record import transaction in isolation.
  - If all 283 rows succeed, the clone is promoted to the target database in an atomic swap (`clone_conn.backup(target_conn)`).
  - If any error or exception occurs at any point (tested on row 2, row 50, and row 282), the clone is discarded, and the original target database remains completely pristine and unmodified.

### Defect 4: Social Network Subdomains and Unsafe Network Targets
- **Prior Problem:** Subdomains of social networks (e.g., `m.linkedin.com`, `careers.instagram.com`) or private/loopback/SSRF targets (`127.0.0.1`, `10.0.0.1`, `192.168.1.1`, `localhost`) could potentially be parsed as primary company websites.
- **Resolution:**
  - Added `is_disallowed_or_unsafe_host()` to `sales_engine/historical/importer.py`.
  - Rejects social network subdomains (`*.linkedin.com`, `*.instagram.com`, `*.facebook.com`, `*.twitter.com`, `*.x.com`, `*.youtube.com`, `*.indeed.com`).
  - Rejects loopback addresses, link-local addresses, private IPv4/IPv6 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`), and localhost hostnames.
  - Applied across all URL extraction and primary candidate domain selection rules.

---

## 3. Operational Import Decision

**RELEASE GATE VERDICT:**  
⚠️ **REVIEW_REQUIRED — GO CONDITIONAL ON OPERATOR DECISION INPUTS**

The codebase and data engine are completely sound, hardened, and safe. However, before the first authoritative write to `data/sales_outreach.db`, the operator must review the following:

1. **7 Ambiguous Austria Rows:**
   - Austria R9, R11, R12, R13, R14, R15, R20 contain multiple corporate or associated links.
   - The UI `/sales/sources` renders a dedicated review card with dropdown choices (`Accept Default`, `Override Domain`, `Quarantine Row`, `External Ref Only`).
2. **1 Missing Standalone Corporate Domain:**
   - Italy R20: Studio Barzon-Ceola (`riccardobarzon29.wixsite.com/studio-barzon-ceola`).
   - Operator can choose to keep it as an external reference or override with a custom domain.

When the operator submits these decisions through the UI, the release gate clears to `GO` and commits atomically.

---

## 4. Test Suite & Verification Results

The automated regression suite confirms zero regressions:

```powershell
.venv\Scripts\pytest tests/test_mvp01_lead_discovery.py `
                     tests/test_mvp01_1_operational_acceptance.py `
                     tests/test_mvp01_2_real_workbook_and_search_safety.py `
                     tests/test_mvp01_3_real_data_acceptance.py `
                     tests/test_mvp01_4_preimport_validation.py `
                     tests/test_mvp01_4_1_multilink_import_acceptance.py `
                     tests/test_mvp01_4_2_import_transaction_gate.py -q
```
**Result:** `68 passed, 4 warnings in 44.49s` (100% pass rate).

Focused MVP-01.4.2 suite:
- `test_real_or_representative_20_25_workbook_counters`: PASSED
- `test_preview_returns_review_required`: PASSED
- `test_review_required_direct_confirm_post_cannot_call_import_records`: PASSED
- `test_blocked_direct_confirm_post_cannot_call_import_records`: PASSED
- `test_approved_row_level_quarantine_allows_safe_ingestion`: PASSED
- `test_stale_preview_or_changed_source_file_is_rejected`: PASSED
- `test_invalid_csrf_token_hash_is_rejected`: PASSED
- `test_backup_failure_prevents_import`: PASSED
- `test_verified_backup_exists_before_mutation`: PASSED
- `test_staged_transaction_rollback_on_failure[2]`: PASSED
- `test_staged_transaction_rollback_on_failure[50]`: PASSED
- `test_staged_transaction_rollback_on_failure[282]`: PASSED
- `test_no_duplicate_child_links_on_reimport`: PASSED
- `test_no_loss_of_outreach_opt_outs_or_manual_decisions`: PASSED
- `test_social_network_subdomains_cannot_become_primary_websites`: PASSED
- `test_local_private_ip_and_unsafe_targets_rejected`: PASSED
- `test_strict_offline_zero_network_calls`: PASSED
- `test_no_customer_workbook_in_zip_builder`: PASSED

---

## 5. Release Archive Integrity

The authoritative clean release ZIP was regenerated using `scripts/rebuild_zip.py`:
- **Archive Path:** `C:\Sales-AI\SalesAI_export.zip`
- **Sidecar Checksum:** `C:\Sales-AI\SalesAI_export.zip.sha256`
- **File Count:** 514 files
- **SHA-256 Checksum:** `3e7b3152413f4202c3159f86215b7c47864b5ee790e32fe104c20a202c6376ad`
- **Excluded Items:** No `.db`, `.sqlite`, `.xlsx`, `.log`, `.venv`, `.git`, or sensitive tokens.
