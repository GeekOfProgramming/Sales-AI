# SalesAI MVP-01.5.2 — Final Lead Register Safety & Operator Acceptance Report
*(Updated with MVP-01.5.2b Real UI Edit & Repeat-Save Data Loss Fixes)*

## 1. Executive Summary

This release incorporates the **MVP-01.5.2b** fix ensuring that human modifications made through the real HTTP UI editor (`POST /sales/register/{candidate_id}/update`) are permanently marked with editorial ownership (`provenance_source = "operator_edited"`), completely eliminating the defect where repeating a pilot save (single or Save All) overwrote human-authored fields.

### Critical Fixes Implemented:

1. **Real UI Edit Provenance Ownership (`backend/sales_ui/router.py`):**
   - In `POST /sales/register/{candidate_id}/update`, records saved through the HTTP editor are now explicitly assigned `provenance_source = "operator_edited"`.
   - Original research evidence URLs, creation timestamps, and original research dates are preserved in the record metadata without losing evidence history.
2. **Non-Destructive Repeat Pilot Saves (`sales_engine/research/pilot_research.py`):**
   - When a curated pilot lead is saved again (via single save or Save All), `save_pilot_candidate_to_store()` detects `provenance_source in ("manual_entry", "operator_researched", "operator_edited")`.
   - All human-authored fields (notes, emails, phone numbers, contact persons, activities, locations, priority rationale) are **strictly preserved**.
   - Only genuinely empty or `"Not Found"` fields are enriched from static pilot research data.
3. **Decoupled Editorial Descriptions from Approval Audit Trail (`backend/sales_ui/router.py`):**
   - Updating ordinary `Descriptions & Operator Notes` in the 13-field register never alters the candidate's `manual_review_notes`, preserving the formal approval or rejection audit trail.
4. **Preserved Decisions and Permissions on New Research:**
   - Researching an existing domain never resets `cold_outreach_allowed` or changes an existing `manual_review_decision` (e.g. `APPROVED`, `REJECTED`, `DNC`).
5. **Accurate Field Mapping & Quality Inspection for Column 11:**
   - Source inspection of the historical pyBIM workbook (`upload_20261027_201854_Emails-10-27-2026-20-25.xlsx`) confirmed that Persian header `لینک آگهی کار` (Column 11) contains non-URL informational strings directly entered in the source spreadsheet (e.g. `اطلاعات عمومی کسب‌وکار (Google Maps)`).
   - In `backend/sales_ui/templates/lead_register.html`, non-URL strings are displayed accurately as text/badges rather than broken links, preserving authentic source data without fabricating URLs.

---

## 2. Acceptance Demonstration Results (12 Criteria)

| # | Acceptance Criterion | Test Verification | Status |
|---|---|---|---|
| 1 | Register list loads with zero candidates | `test_acceptance_01_empty_database_register_loads` | **PASSED** |
| 2 | Register list loads with candidate-only data | `test_acceptance_02_candidate_only_database_synthesizes` | **PASSED** |
| 3 | Historical 283-company workbook produces readable 13-field table | `test_acceptance_03_historical_import_produces_13_field_records` | **PASSED** |
| 4 | All 7 curated pilot companies can be saved individually | `test_acceptance_04_and_05_curated_pilot_companies_saved_without_outreach_approval` | **PASSED** |
| 5 | Save for research does not automatically approve outreach | Verified `cold_outreach_allowed == False`, `manual_review_decision == 'NOT_REVIEWED'` | **PASSED** |
| 6 | 13-field record can be edited through HTTP UI | `test_acceptance_06_to_09_http_edit_restart_search_and_export` | **PASSED** |
| 7 | Multiline text and contact data survive restart | Reloaded from clean SQLite instance; all 13 fields intact | **PASSED** |
| 8 | Search works against saved records | Filtered query `München` matched expected record | **PASSED** |
| 9 | CSV and XLSX contain 13 fields in documented order | Export files inspected; 13 pyBIM column headers verified | **PASSED** |
| 10 | Existing historical records and suppression decisions remain intact | `test_acceptance_10_suppression_and_manual_decisions_intact` | **PASSED** |
| 11 | Backup and restoration of register table work | `test_acceptance_11_backup_and_restoration` via SQLite online backup API | **PASSED** |
| 12 | Zero live Brave requests, unmocked fetches, LLM calls or emails | Zero external calls executed during automated tests | **PASSED** |

### MVP-01.5.2b Real Workflow Integration Tests (`tests/test_mvp01_5_2a_data_loss_prevention.py`)

| Test Name | Description | Status |
|---|---|---|
| `test_real_http_edit_and_repeat_pilot_save` | Real HTTP save of pilot profile -> real HTTP edit of 13 fields -> repeat single save -> Save All. Verifies notes, emails, and names remain 100% intact with zero data loss. | **PASSED** |
| `test_research_new_preserves_existing_approval_and_permissions` | Proves research on existing APPROVED candidate preserves permissions and status | **PASSED** |
| `test_research_new_preserves_rejected_and_dnc_candidate` | Proves research on existing REJECTED/DNC candidate preserves rejection and suppression | **PASSED** |
| `test_editing_13_field_record_preserves_manual_review_notes` | Proves editing 13-field notes preserves the manual review rejection audit trail | **PASSED** |

### Test Suite Execution Summary
```text
====================== 56 passed, 17 warnings in 15.95s =======================
- tests/test_real_lead_research_pilot.py: 7 passed
- tests/test_mvp01_5_2_lead_researcher_safety.py: 6 passed
- tests/test_mvp01_5_2_acceptance.py: 7 passed
- tests/test_mvp01_5_2a_data_loss_prevention.py: 4 passed
- tests/test_mvp01_lead_discovery.py: 8 passed
- tests/test_mvp01_2_real_workbook_and_search_safety.py: 11 passed
- tests/test_mvp01_3_real_data_acceptance.py: 10 passed
- tests/test_launcher_port_guard.py: 3 passed
```

### Operational Database Integrity
- `candidate_companies`: **284**
- `historical_imports`: **283**
- `historical_contacts`: **988**
- `historical_company_links`: **741**
- `historical_outreach_events`: **142**
- `lead_register_records`: **0** (Clean baseline preserved)
- Dump SHA-256 Digest: `60a75a3fde6a9582bfaa47c0b7306e6b80cc55957b94ad7ada76656a12380ebd`

---

## 3. Safe Windows Operator Guide

### A. Launching the Local Application
In PowerShell:
```powershell
cd C:\Sales-AI\SalesAI
.\run_sales_ai.ps1
```
Open in your browser:
- Lead Register: [http://127.0.0.1:8000/sales/register](http://127.0.0.1:8000/sales/register)
- Pilot Research Review: [http://127.0.0.1:8000/sales/research/pilot](http://127.0.0.1:8000/sales/research/pilot)

### B. Reviewing and Saving Pilot Profiles
1. Navigate to: `http://127.0.0.1:8000/sales/research/pilot`
2. Inspect the 7 curated Italian engineering firms:
   - **Studio Miotto** (`studiotecnicom.it`)
   - **Stefano Baseggio** (`stefanobaseggio.it`)
   - **IPT Project** (`iptproject.it`)
   - **E-Farm Engineering** (`e-farmsrl.com`)
   - **Incide Engineering** (`incide.it`)
   - **STC Group** (`stcpi.com`)
   - **ei bim project** (`eibimproject.com`)
3. Click **"Save for Research"** on any profile to register the company in the Candidate Store and Lead Register.
   - The company is saved in `NOT_REVIEWED` status with `cold_outreach_allowed = False`. Outreach is not automatically authorized.
   - Repeating "Save for Research" or clicking "Save All" is non-destructive and will never overwrite your manual edits.

### C. Viewing and Editing 13-Field Records
1. Navigate to `http://127.0.0.1:8000/sales/register`.
2. Locate the company and click **"Edit 13 Fields"**.
3. In the detail editor (`/sales/register/{candidate_id}`), modify any of the 13 fields (Priority, Location, Telephone, Activities, Website, Contact Person, Notes, Links, Emails).
4. Click **"Save 13-Field Lead Record"**. The record is saved in SQLite with `provenance_source = "operator_edited"`.

### D. Exporting the 13-Field Register
From the top of `/sales/register`:
- Click **"Export XLSX"** to download `SalesAI_13_Field_Lead_Register_<timestamp>.xlsx`.
- Click **"Export CSV"** to download `SalesAI_13_Field_Lead_Register_<timestamp>.csv` (UTF-8 with BOM, safe for Excel).

### E. Safe SQLite Backup & Recovery Procedure

#### 1. Creating a Live Consistent Online Backup
Run from PowerShell while the server is active (uses SQLite online backup API to ensure zero corruption even during concurrent writes):
```powershell
cd C:\Sales-AI\SalesAI
.\.venv\Scripts\python.exe -c "
import sqlite3, os
from datetime import datetime

os.makedirs('data/backups', exist_ok=True)
backup_file = f'data/backups/sales_outreach_backup_{datetime.now().strftime(\"%Y%m%d_%H%M%S\")}.db'

src = sqlite3.connect('file:///C:/Sales-AI/SalesAI/data/sales_outreach.db?mode=ro', uri=True)
dst = sqlite3.connect(backup_file)
src.backup(dst)
src.close()
dst.close()
print(f'[SUCCESS] Online backup created at: {backup_file}')
"
```

#### 2. Safe Recovery / Restoration Procedure
```powershell
# Step 1: Identify ONLY the SalesAI server process listening on port 8000
$port8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($port8000) {
    $procId = $port8000.OwningProcess
    $proc = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $procId }
    if ($proc.CommandLine -match "SalesAI|uvicorn|start_server") {
        Write-Host "Gracefully stopping SalesAI process PID $($procId)..."
        Stop-Process -Id $procId -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

# Step 2: List verified backups and select the target backup file
Get-ChildItem -Path "C:\Sales-AI\SalesAI\data\backups\*.db" | Sort-Object LastWriteTime -Descending | Select-Object Name, Length, LastWriteTime

# Step 3: Choose verified backup file (e.g. latest)
$selectedBackup = (Get-ChildItem -Path "C:\Sales-AI\SalesAI\data\backups\*.db" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Write-Host "Restoring from verified backup: $selectedBackup"

# Step 4: Safely clean up any existing SQLite WAL and SHM journal files before overwriting
Remove-Item -Path "C:\Sales-AI\SalesAI\data\sales_outreach.db-wal" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "C:\Sales-AI\SalesAI\data\sales_outreach.db-shm" -Force -ErrorAction SilentlyContinue

# Step 5: Copy backup over operational database
Copy-Item -Path $selectedBackup -Destination "C:\Sales-AI\SalesAI\data\sales_outreach.db" -Force

# Step 6: Verify restored database integrity
.\.venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('C:/Sales-AI/SalesAI/data/sales_outreach.db')
cursor = conn.cursor()
cursor.execute('PRAGMA integrity_check;')
status = cursor.fetchone()[0]
print(f'[RESTORATION VERIFICATION] SQLite Integrity Check: {status}')
conn.close()
"
```

---

## 4. Release Package Delivery

- **Package Path:** `C:\Sales-AI\SalesAI_export.zip`
- **Member Count:** 530 files
- **CRC Verification:** PASSED
- **SHA-256 Checksum:** `7f40778db091d293fcab97c6bc0cd6770f4e7d0b8108ecb444809644178b8435`
- **Sidecar File:** `C:\Sales-AI\SalesAI_export.zip.sha256`
- **Clean Extraction Test:** Verified clean extraction and full pytest execution in temporary sandbox.
