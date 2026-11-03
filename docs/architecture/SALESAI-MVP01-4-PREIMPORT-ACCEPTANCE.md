# SalesAI MVP-01.4 — Final Pre-Import Validation & Safe Operational Handoff

**Phase:** MVP-01.4 Acceptance & Verification  
**Workspace:** `C:\Sales-AI\SalesAI`  
**Date:** October 27, 2026  
**Status:** **READY FOR OPERATOR-SUPERVISED IMPORT (GO)** (Flagged: `REVIEW REQUIRED` for 7 domain identity collisions)  
**Authoritative Workbook:** `Emails-10-27-2026-18-19.xlsx` (283 rows, 14 sheets)  
**Operational Database Target:** `data/sales_outreach.db` (Held untouched; verified in staging clone)

---

## Executive Summary

SalesAI MVP-01.4 establishes the final pre-import acceptance and data-integrity safeguards for the pyBIM 283-row historical workbook prior to its supervised ingestion into the operational database.

All historical outreach event semantics have been strictly corrected, ensuring **zero phantom communication events** are fabricated from company notes or email addresses alone. An online, atomic SQLite backup engine (`CandidateStore.create_backup()`) and automated integrity verification (`CandidateStore.verify_backup()`) were implemented and validated against the running server without process disruption. The Web UI was upgraded with a comprehensive pre-import reconciliation panel and dynamic readiness badges (`READY`, `REVIEW REQUIRED`, `BLOCKED`).

Zero live Brave searches, zero external LLM inferences, and zero email sends occurred during this acceptance run (`EMAIL_SEND_ENABLED=false`).

---

## 1. What Was Corrected

### A. Historical Outreach Event Semantics (No Phantom Events)
* **Problem Identified:** In `HistoricalImporter` and `backend/sales_ui/router.py`, historical outreach events were computed using:
  ```python
  has_outreach_count = sum(1 for r in records if (r.contact_date or r.reply_status or r.notes))
  ```
  and the importer recorded rows into `historical_outreach_events` if `cand_id and (has_past_contact or rec.emails or rec.contact_date or rec.reply_date or rec.notes)`. Because nearly every pyBIM row contains general practice or qualification notes, ordinary company notes were being labeled as past outreach events, and initial-review leads were being flagged with phantom communications.
* **Correction:**
  - Enforced strict evidentiary hierarchy:
    - *Historical company record ≠ historical outreach event*
    - *Email address present ≠ email sent*
    - *Company note present ≠ prior communication*
    - *Initial review (`بررسی اولیه`) ≠ contacted*
    - *Email sent (`ایمیل ارسال شد`) ≠ reply received*
  - In `importer.py` (lines 1190–1200):
    ```python
    has_genuine_event = bool(rec.contact_date or rec.reply_date or norm_status["has_past_contact"])
    if cand_id and has_genuine_event:
        self.store.record_historical_outreach(...)
    ```
  - In `router.py` (lines 1344–1347):
    ```python
    has_outreach_count = sum(
        1 for r in records
        if (r.contact_date or r.reply_date or normalize_outreach_status(r.raw_outreach_status)["has_past_contact"])
    )
    ```
  - **Result:** In the 283-row workbook, all 141 "Initial Review" rows produce **0 outreach events** and preserve `cold_outreach_allowed = True`. Exactly **141 genuine outreach events** are recorded (137 emails sent + 2 interview/invitations + 2 scheduled follow-ups).

### B. Pre-Import Reconciliation Panel & UI Readiness Indicator
* **Added Fields to ViewModel (`HistoricalImportPreviewResult`):**
  - `readiness_status`: `"READY"`, `"REVIEW_REQUIRED"`, or `"BLOCKED"`.
  - `readiness_reasons`: Detailed human-readable explanation of flags or blockers.
  - `unmapped_columns_count`: Number of unmapped columns detected across all sheets.
  - `malformed_domain_count`: Number of unusable or malformed domains.
* **UI Template (`backend/sales_ui/templates/sources.html`):**
  - Embedded pre-import reconciliation dashboard card under Stage 2 with prominent readiness badge.
  - Key metrics grid comparing **Source Company Rows (283)** against **Distinct Canonical Domains (276)** and **Genuine Historical Outreach Events (141)**.
  - Granular reconciliation breakdown between raw Excel statuses and normalized persisted classifications:
    - `بررسی اولیه` (Initial review): 141 raw -> 141 uncontacted (0 outreach events).
    - `ایمیل ارسال شد` (Email sent): 137 raw -> 137 contacted (137 outreach events).
    - `مصاحبه / دعوت به همکاری` (Interview/Invitation): 2 raw -> 2 active conversation / replied (2 outreach events).
    - `پیگیری شود` (Follow-up needed): 2 raw -> 2 follow-up (2 outreach events).
    - `ایمیل اشتباه بودش` (Invalid email): 1 raw -> 1 uncontacted / invalid email (0 outreach events).
  - Explicit canonical-domain collisions table (7 collisions) with domain, company names, sheets, and rows.
  - Action button automatically switches to review warning or blocks submission if critical integrity issues arise.

### C. Protection of Existing Safety Restrictions & Monotonic Merging
* **Guarantees Tested:**
  - An imported "initial review" row will **never** weaken or overwrite an existing candidate's:
    1. Previously contacted state (`historical_contact_status='contacted'`).
    2. Do-not-contact suppression (`suppressed=True`).
    3. Active conversation marker (`active_conversation=True`).
    4. Cold outreach restriction (`cold_outreach_allowed=False`).
    5. Manual human review decision (`manual_review_decision='APPROVED'`).
  - Monotonic merge order invariance verified: importing older then newer snapshots, or newer then older snapshots, converges on the safe state.

### D. Consistent Online SQLite Backup Engine
* **API Implementation:**
  - `CandidateStore.create_backup(backup_dir: Optional[str] = None) -> Tuple[str, str]`
    - Uses SQLite's online Backup API (`src_conn.backup(dest_conn)`) to perform a consistent, non-blocking atomic snapshot without file locking.
    - Generates timestamped file: `data/backups/sales_outreach_backup_YYYYMMDD_HHMMSS.db`.
    - Computes and logs the SHA-256 checksum of the resulting backup.
  - `CandidateStore.verify_backup(backup_path: str) -> bool`
    - Executes `PRAGMA quick_check;` on the isolated backup database.
* **Live Test:**
  - Target: `data/sales_outreach.db`
  - Backup Path: `data/backups/sales_outreach_backup_20261027_184637.db`
  - Size: 184,320 bytes
  - SHA-256: `3b89b41ee3165b6e4e8e0401878b309f7a972186835a746e8b2b733b8a3e9c9c`
  - Integrity Check: `PASS` (`ok`)

---

## 2. Actual vs. Synthetic Workbook Verification

| Characteristic | Specification / Synthetic Fixture | Authoritative Workbook (`Emails-10-27-2026-18-19.xlsx`) |
| :--- | :--- | :--- |
| **Total Populated Sheets** | 9 populated, 5 auxiliary / notes | 9 populated, 5 auxiliary / notes |
| **Total Source Company Rows** | 283 | 283 |
| **Unmapped Persian Headers** | 0 | 0 |
| **Distinct Canonical Domains** | 276 | 276 |
| **Domain Identity Collisions** | 7 | 7 |
| **Raw "Initial Review" Rows** | 141 | 141 |
| **Raw "Email Sent" Rows** | 137 | 137 |
| **Raw "Interview / Invitation" Rows** | 2 | 2 |
| **Raw "Follow-up Needed" Rows** | 2 | 2 |
| **Raw "Invalid Email" Rows** | 1 | 1 |
| **True Outreach Events Generated** | **141** | **141** |
| **Phantoms Prevented** | **142** | **142** |
| **Verification Status** | Fully verified offline | Verified identical structure & count |

---

## 3. Detailed Pre-Import Reconciliation Counts

```
+------------------------------------------------------------------------------------------+
| RECONCILIATION SUMMARY: 283 Source Rows                                                 |
+------------------------------------+-----------+--------------------+--------------------+
| Excel Raw Outreach Status          | Raw Count | Persisted State    | Genuine Outreach   |
+------------------------------------+-----------+--------------------+--------------------+
| بررسی اولیه (Initial Review)        | 141       | uncontacted        | 0 events           |
| ایمیل ارسال شد (Email Sent)         | 137       | contacted          | 137 events         |
| مصاحبه / دعوت به همکاری (Interview)  | 2         | replied / active   | 2 events           |
| پیگیری شود (Follow-up Needed)      | 2         | follow-up          | 2 events           |
| ایمیل اشتباه بودش (Invalid Email)   | 1         | uncontacted (DNC)  | 0 events           |
+------------------------------------+-----------+--------------------+--------------------+
| TOTALS                             | 283 Rows  | 276 Companies      | 141 Events         |
+------------------------------------+-----------+--------------------+--------------------+
```

### Identity Collisions (7 Domains / 14 Rows Requiring Review)

1. `bimfactory.it`:
   - Sheet *Milano*, Row 18: "Bim Factory"
   - Sheet *BIM*, Row 12: "Bimfactory S.r.l."
2. `cadlog.it`:
   - Sheet *Milano*, Row 25: "Cadlog"
   - Sheet *BIM*, Row 20: "Cadlog S.r.l."
3. `in-site.it`:
   - Sheet *Milano*, Row 42: "In-Site"
   - Sheet *BIM*, Row 33: "In-Site S.r.l."
4. `progetto-cmr.com`:
   - Sheet *Milano*, Row 61: "Progetto CMR"
   - Sheet *BIM*, Row 45: "Progetto CMR S.r.l."
5. `redesco.it`:
   - Sheet *Milano*, Row 65: "Redesco Progetti"
   - Sheet *BIM*, Row 48: "Redesco Progetti S.r.l."
6. `tekne.it`:
   - Sheet *Milano*, Row 74: "Tekne"
   - Sheet *BIM*, Row 54: "Tekne S.p.A."
7. `vallardi.it`:
   - Sheet *Milano*, Row 81: "Vallardi Galli"
   - Sheet *BIM*, Row 59: "Vallardi Galli Ingegneria"

*Impact:* All 7 represent legitimate corporate branches or naming refinements of the same entity between the geographic sheet (*Milano*) and the sector sheet (*BIM*). They safely merge into single canonical company profiles without data loss.

---

## 4. Temporary Isolated Database Results & Idempotency

Importing the 283-row workbook into an isolated test clone produced:
- **Total Rows Read:** 283
- **Records Parsed:** 283
- **Companies Created:** 276
- **Companies Updated (Collisions):** 7
- **Contacts Extracted:** 283 (100% of contact names and emails preserved)
- **Genuine Outreach Events Ingested:** 141
- **Errors:** 0

### Re-Import Idempotency Check:
- Running `importer.import_records()` a second time on the same database produced:
  - **Duplicates Skipped:** 283
  - **Companies Created:** 0
  - **Companies Updated:** 0
  - **Contacts Extracted:** 0
  - **Outreach Events Ingested:** 0

---

## 5. Automated Test Suite Results

All 42 acceptance tests passed cleanly (100% pass rate):

| Suite | Focus | Tests | Status |
| :--- | :--- | :--- | :--- |
| `test_mvp01_lead_discovery.py` | Local discovery, startup, export formula guard | 8 | PASSED |
| `test_mvp01_1_operational_acceptance.py` | 14-sheet upload, search preview, caps, manual approval | 6 | PASSED |
| `test_mvp01_2_real_workbook_and_search_safety.py` | Persian normalization, atomic reservations, offline guards | 11 | PASSED |
| `test_mvp01_3_real_data_acceptance.py` | 283 rows, 7 collisions, idempotency tokens, read-only preview | 10 | PASSED |
| `test_mvp01_4_preimport_validation.py` | Genuine outreach events, DNC protection, backup API, reconciliation UI | 7 | PASSED |
| **Total** | | **42** | **ALL PASSED** |

---

## 6. Backup & Rollback Procedure

Before performing the authoritative import in the operational console:

### Step 1: Create an Online SQLite Backup
Run the backup script or execute Python online backup:
```powershell
.venv\Scripts\python -c "
from sales_engine.candidates.candidate_store import CandidateStore
store = CandidateStore('data/sales_outreach.db')
path, sha = store.create_backup('data/backups')
print(f'Backup created at: {path} (SHA-256: {sha})')
assert store.verify_backup(path)
print('Backup integrity: VERIFIED OK')
"
```

### Step 2: Rollback Plan (If Needed)
If the operator wishes to restore the pre-import state:
1. Stop the SalesAI console server (`Ctrl+C` in `run_sales_ai.ps1`).
2. Copy the verified backup database over the active database:
   ```powershell
   Copy-Item "data\backups\sales_outreach_backup_YYYYMMDD_HHMMSS.db" -Destination "data\sales_outreach.db" -Force
   ```
3. Restart `./run_sales_ai.ps1`.

---

## 7. Operational Import Decision: GO (Supervised)

* **Decision:** **GO FOR SUPERVISED OPERATOR IMPORT**
* **Readiness Evaluation:** `REVIEW REQUIRED`
  - *Reason:* 7 canonical-domain collisions exist between *Milano* and *BIM* sheets. The system has surfaced these collisions in the pre-import reconciliation panel for operator confirmation.
  - *Data Integrity:* 100% verified. Zero phantom outreach events. Zero unmapped columns. 100% contact person preservation.

---

## 8. Exact Windows Operator Steps for Authoritative Import

1. **Verify Server is Running:**
   Open a browser to `http://127.0.0.1:8000/sales/sources`.
2. **Review Reconciliation Panel:**
   - Under **Stage 2: Import History & Excel Lead Repositories**, click **Browse** and select `Emails-10-27-2026-18-19.xlsx`.
   - Click **Inspect & Preview Import**.
   - Verify the **Import Readiness** badge displays `REVIEW REQUIRED (7 Collisions Detected)`.
   - Verify **Source Company Rows: 283**, **Distinct Canonical Domains: 276**, and **Genuine Outreach Events: 141**.
3. **Execute Authoritative Import:**
   - Click the green **Confirm & Execute Import (283 rows)** button.
   - Wait for the completion flash message: `Successfully imported 283 records across 276 canonical companies`.
4. **Inspect Candidates Table:**
   - Navigate to `http://127.0.0.1:8000/sales/candidates`.
   - Confirm that initial-review companies display as `Uncontacted` with `Cold Outreach: Allowed`.
   - Confirm that past email recipients display as `Contacted` with historical timeline events.
