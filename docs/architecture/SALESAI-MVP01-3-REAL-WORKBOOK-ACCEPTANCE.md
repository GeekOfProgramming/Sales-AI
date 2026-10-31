# SalesAI MVP-01.3 — Actual 283-Row Workbook Acceptance & Outreach State Integrity

**Status:** Completed & Offline Verified  
**Date:** October 27, 2026  
**Authoritative Workbook:** `Emails-10-27-2026-18-19.xlsx` (supersedes Oct 26 workbook)  
**Safety Constraints:** STRICTLY OFFLINE &bull; Zero live Brave searches &bull; Zero external LLM inference &bull; Zero email dispatches &bull; Zero modifications to operational `data/sales_outreach.db` during testing  

---

## Executive Summary

SalesAI MVP-01.3 resolves the six defects independently confirmed against the October 27, 2026 pyBIM workbook (`Emails-10-27-2026-18-19.xlsx`). This workbook expands the historical pyBIM dataset from 252 to **283 company rows**, with updated counts in Bozen (30) and Switzerland (40).

All 283 rows are parsed with **0 unmapped Persian headers**, **283 contact persons preserved**, and **conservative outreach status mapping** that properly classifies the 141 uncontacted initial-review leads without inventing prior contact or fake meetings. In addition, pilot search safety is upgraded with a deterministic **idempotency token** preventing rapid double-clicks from reserving duplicate queries.

---

## Verified Defects & Architectural Resolutions

### Defect 1 — Clean Backend Startup in Export Archive
- **Root Cause:** `backend/main.py` referenced `Dict` and `Any` in Pydantic models around line 883 without importing them from `typing`.
- **Resolution:** Added `Dict, Any, Union, Tuple` to the typing imports in `backend/main.py`. Verified clean import via `python -c "import backend.main"`. Clean-source extraction test passes without diagnostic patches.

### Defect 2 — Initial Review Outreach State Integrity
- **Root Cause:** `_row_to_record()` set `reply_status = raw_outreach_status`, and `import_records()` checked `bool(rec.contact_date or rec.reply_date or rec.reply_status)`. Because 141 rows had `raw_outreach_status = "بررسی اولیه"`, `reply_status` was populated, causing all 141 uncontacted companies to be misclassified as `contacted` with `cold_outreach_allowed = False`.
- **Resolution:** Implemented `normalize_outreach_status()` in `sales_engine/historical/importer.py` with conservative, explicit status mappings:
  - `بررسی اولیه` (141 rows) -> `historical_contact_status = "uncontacted"`, `cold_outreach_allowed = True`, `reply_status = None`.
  - `ایمیل ارسال شد` (137 rows) -> `historical_contact_status = "contacted"`, `cold_outreach_allowed = False`, `reply_status = None`.
  - `مصاحبه / دعوت به همکاری` (2 rows) -> `historical_contact_status = "replied"`, `reply_status = "positive_interview"`, `active_conversation = True`.
  - `پیگیری شود` (2 rows) -> `historical_contact_status = "contacted"`, `follow_up_needed = True`.
  - `ایمیل اشتباه بودش` (1 row) -> `is_wrong_email = True` for that specific address; does NOT suppress whole organization if alternate contacts exist.
- **Reconciliation against Workbook Dashboard:**
  - Workbook Dashboard displays: `تعداد کل شرکتها: 283`, `ایمیل ارسال شد: 141`, `مصاحبه / دعوت به همکاری: 2`, `باقی مانده برای ایمیل: 142`.
  - SalesAI reconciliation matches this exactly: 141 initial review + 1 invalid email = 142 remaining uncontacted companies.

### Defect 3 — Preservation of Persian Contact-Person Names
- **Root Cause:** In `sales_engine/historical/importer.py`, line 518 parsed Persian `شخص مسئول / رابط` into `contact_name_val`, but lines 564–570 executed a duplicate assignment `contact_name = norm_row.get("contact_name") ...` that checked only English/Italian keys and overwrote `contact_name` with `None`.
- **Resolution:** Removed the conflicting duplicate lookup. The structured liaison name (`شخص مسئول / رابط`) is now extracted and stored in `HistoricalRecord.contact_name`, preserving all 283 contact names across the entire dataset.

### Defect 4 — Identity Collisions & Pre-Commit Inspection
- **Baseline Observation:** 283 populated company source rows across 276 canonical domain values produce **7 domain collisions**.
- **Resolution:**
  - Enhanced `inspect_workbook()` to generate a pre-commit collision inspection report distinguishing `verified_duplicate`, `possible_duplicate`, and `separate_entities_or_branches`.
  - Never merges differing outreach histories or established lead statuses silently.
  - Requires operator review for ambiguous identity collisions.
  - Confirmed strict re-import idempotency (0 duplicates created on re-import).

### Defect 5 — Brave Double-Click Protection & Attempt Lifecycle
- **Root Cause:** `/sales/discovery/run` generated a new random UUID on every submission, allowing fast operator double-clicks to reserve multiple pilot attempts.
- **Resolution:**
  - Added an `idempotency_token` bound to submitted search parameters (`full_query`, `country`, `city`, `category`) and validated server-side.
  - Submitting duplicate tokens returns a non-dispatched error (`Duplicate search submission detected. Replay blocked`).
  - Added strict state transition validation: `DISPATCH_ATTEMPTED` cannot transition to `PRE_DISPATCH_CANCELLED` without proof of non-dispatch; instead it is preserved as `UNCERTAIN` to protect budget accounting.

### Defect 6 — Read-Only Acceptance Preview
- **Resolution:** Upgraded the read-only inspection preview (`/sales/sources/import-history/preview` and `HistoricalImportPreviewResult`) to surface worksheet counts, status distribution, classification breakdown, domain collisions, contact-person extraction completeness, and unmapped columns before committing to the database.

---

## Workbook Acceptance Distribution (October 27, 2026)

| Sheet | Populated Company Rows | Status Breakdown |
|---|---:|---|
| **Italy** | 101 | 50 `بررسی اولیه`, 49 `ایمیل ارسال شد`, 1 `مصاحبه`, 1 `پیگیری شود` |
| **Milan** | 16 | 8 `بررسی اولیه`, 8 `ایمیل ارسال شد` |
| **Turin** | 20 | 10 `بررسی اولیه`, 10 `ایمیل ارسال شد` |
| **Rome** | 17 | 8 `بررسی اولیه`, 8 `ایمیل ارسال شد`, 1 `مصاحبه` |
| **Bozen** | 30 | 15 `بررسی اولیه`, 14 `ایمیل ارسال شد`, 1 `ایمیل اشتباه بودش` |
| **LinkedIn** | 19 | 10 `بررسی اولیه`, 9 `ایمیل ارسال شد` |
| **Indeed** | 0 | (empty) |
| **Austria** | 20 | 10 `بررسی اولیه`, 10 `ایمیل ارسال شد` |
| **Germany** | 20 | 10 `بررسی اولیه`, 10 `ایمیل ارسال شد` |
| **Switzerland** | 40 | 20 `بررسی اولیه`, 19 `ایمیل ارسال شد`, 1 `پیگیری شود` |
| **Belgium** | 0 | (empty) |
| **France** | 0 | (empty) |
| **TOTAL** | **283** | **141 initial review, 137 sent, 2 interview, 2 follow-up, 1 invalid** |

---

## Verification & Test Results

```text
tests/test_mvp01_lead_discovery.py: 8 passed
tests/test_mvp01_1_operational_acceptance.py: 6 passed
tests/test_mvp01_2_real_workbook_and_search_safety.py: 11 passed
tests/test_mvp01_3_real_data_acceptance.py: 10 passed
======================= 35 passed in 13.48s =======================
```

All 35 MVP regression tests passed with zero failures and zero live network/LLM calls.
`git diff --check` passed cleanly with 0 whitespace errors.

---

## Release Verification & Packaging

The export archive `SalesAI_export.zip` was rebuilt with `scripts/rebuild_zip.py`.
- **Verified Clean Extraction:** Extracted to a clean directory without environment modifications.
- **Import Verification:** `python -c "import backend.main"` executed successfully in the fresh extracted copy.
- **Sidecar Checksum:** External `.sha256` sidecar matches the ZIP SHA-256 hash.
- **Zero Customer Records:** No customer records or operational database files packaged.
