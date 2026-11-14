# Sales-AI CRM — SQLite Disaster Recovery Runbook (BACKUP-01)

This runbook documents the authoritative procedure for verifying and manually restoring the Sales-AI operational database (`data/sales_outreach.db`) from automated or manual backups.

> [!IMPORTANT]
> Sales-AI never restores backups over the active database automatically. All restores are operator-controlled and audited.

---

## 1. Backup Locations

Backups are stored under:
- **Automatic Backups (Scheduled Task):**
  `C:\Sales-AI\SalesAI\data\backups\automatic\`
  Filename format: `auto_sales_outreach_backup_YYYYMMDD_HHMMSS_UTC_<unique_id>.db`
- **Manual Backups (Operator On-Demand):**
  `C:\Sales-AI\SalesAI\data\backups\`
  Filename format: `manual_sales_outreach_backup_YYYYMMDD_HHMMSS_UTC_<unique_id>.db`

---

## 2. On-Demand Backup Command

Before performing bulk imports, schema migrations, or major batch operations, create a verified manual backup:

```powershell
.\backup_sales_ai.ps1
```

To run an immediate backup and verify disposable restore in one step:
```powershell
.\backup_sales_ai.ps1 -VerifyRestore
```

---

## 3. Verifying a Backup Before Restoration

Always verify the backup's internal SQLite integrity before using it:

```powershell
.\.venv\Scripts\python.exe scripts/backup_manager.py verify-restore --backup "data\backups\automatic\<backup_filename>.db"
```

Expected output:
- `Integrity Check: ok`
- `Foreign Keys: 0 violation(s)`
- Table row counts match between baseline and restored clone.

---

## 4. Manual Restoration Procedure (Step-by-Step)

If the active database becomes corrupted or an operator rollback is required:

### Step 1: Stop the Sales-AI Server
Ensure no active processes are writing to the database:
- Press `Ctrl+C` in the running PowerShell console.
- Confirm port 8000 is released:
  ```powershell
  Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
  ```

### Step 2: Quarantine the Damaged Live Database
Never overwrite or delete the damaged database without creating an audit copy:
```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Rename-Item "data\sales_outreach.db" "data\sales_outreach_damaged_$timestamp.db"
if (Test-Path "data\sales_outreach.db-wal") { Remove-Item "data\sales_outreach.db-wal" }
if (Test-Path "data\sales_outreach.db-shm") { Remove-Item "data\sales_outreach.db-shm" }
```

### Step 3: Copy the Approved Backup to the Live Location
Copy the verified backup to the canonical location:
```powershell
Copy-Item "data\backups\automatic\<chosen_backup>.db" "data\sales_outreach.db"
```

### Step 4: Verify Restored Live Database Integrity
Confirm the newly placed live database opens cleanly:
```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; conn = sqlite3.connect('data/sales_outreach.db'); print('Integrity:', conn.execute('PRAGMA integrity_check;').fetchall()); print('FKs:', conn.execute('PRAGMA foreign_key_check;').fetchall()); conn.close()"
```

### Step 5: Restart the Sales-AI Console
```powershell
.\run_sales_ai.ps1
```

Navigate to `http://127.0.0.1:8000/sales` and verify Candidate Review and Qualified Leads counters match the expected recovery state.

---

## 5. Storage Growth & Retention Policy Recommendations

- **Database Size:** ~95 MB.
- **Projected Daily Growth:** 1 automatic backup per day = ~95 MB/day (~2.8 GB/month).
- **Recommended Policy:**
  - Keep the last **14 daily backups** under `data/backups/automatic/`.
  - Keep weekly snapshots for 4 weeks.
  - Preserve all pre-import milestone backups under `data/backups/` permanently.
