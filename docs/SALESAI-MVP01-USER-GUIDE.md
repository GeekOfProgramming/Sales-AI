# SalesAI MVP-01.3 — Operator User Guide

A practical, step-by-step guide for operators to run local lead discovery for pyBIM safely on Windows with full support for the authoritative 14-sheet Persian pyBIM workbook (`Emails-10-27-2026-18-19.xlsx`), 283-row acceptance, outreach state integrity, and idempotency-gated pilot search guards.

---

## 1. How to Start SalesAI on Windows

Open **Windows PowerShell** or **Command Prompt** in the repository root directory (`C:\Sales-AI\SalesAI`).

### Option A: Using PowerShell Launcher (Recommended)
```powershell
.\run_sales_ai.ps1
```

### Option B: Using Windows Batch Launcher
```cmd
run_sales_ai.bat
```

### Option C: Manual Launch
```powershell
.\.venv\Scripts\Activate.ps1
$env:EMAIL_SEND_ENABLED = "false"
$env:SALESAI_HOST = "127.0.0.1"
$env:SALESAI_PORT = "8000"
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

> **Operational Safeguards:** The server binds strictly to `127.0.0.1`. Real email sending is unconditionally disabled (`EMAIL_SEND_ENABLED=false`). No automated background LLM inference or billable calls occur on startup.

---

## 2. Opening the Operator Console

Open your web browser and navigate to:

👉 **[http://127.0.0.1:8000/sales/dashboard](http://127.0.0.1:8000/sales/dashboard)**

Console navigation:
- **Dashboard:** Live operational metrics (total leads, awaiting review, approved leads, search runs).
- **Discovery:** Small-scale pilot search runner, zero-network dry-run query preview, double-click idempotency token, and atomic pre-dispatch billing guards.
- **Candidates / Leads:** Filter and review discovered company records with human review decisions.
- **Sources & Import:** Upload local Excel/CSV files or inspect local file paths with full Persian header recognition.
- **Settings:** Environment variables, billing status, security flags, and safety limits.

---

## 3. How to Import the Real pyBIM Workbook Safely (October 27, 2026)

The authoritative pyBIM workbook (`Emails-10-27-2026-18-19.xlsx`, superseding the Oct 26 edition) has 14 sheets, 283 populated company rows across 9 populated geographic/source sheets, and 20 Persian columns.

### Step-by-Step Supervised Import Procedure:

1. **Restart Local Server:**
   ```powershell
   .\run_sales_ai.ps1
   ```
2. Navigate to **Sources & Import** (`/sales/sources`).
3. Under **Import Historical Company Workbook**:
   - Click **Browse...** to select `Emails-10-27-2026-20-25.xlsx`.
4. Click **Preview File**.
5. **READ-ONLY Inspection Results:**
   - 14 sheets detected (9 company-source sheets, 5 dashboard/summary sheets).
   - Exactly **283 company rows** analyzed across European markets.
   - Contact persons, multiline website links (741 links), and outreach history preserved.
   - Status badge displays **REVIEW REQUIRED**.
6. **Resolve Operator Decisions in UI:**
   - The interactive **Ambiguous Rows Requiring Decision (7)** and **Companies Missing Primary Domain (1)** panels will appear.
   - For each of the **7 Austria ambiguous rows** (R9, R11, R12, R13, R14, R15, R20):
     * Review the company name, proposed host, and candidate host URLs.
     * Select a decision: `Accept Default`, `Select Primary Host`, `Quarantine (No Primary)`, or `External Reference Only`.
     * If selecting a primary host, choose the verified domain from the candidate dropdown.
     * Enter an optional note.
   - For the **1 Italy missing primary host row** (R20: *Studio di architettura Barzon-Ceola*):
     * Choose `Quarantine (No Primary)` or `External Reference Only`.
   - As decisions are made, row badges change from `Unresolved` to `Resolved`.
7. **Confirm Execution Readiness:**
   - Once all 8 rows are resolved, the **Confirm & Import to Local Database** button becomes active.
   - Clicking Confirm automatically:
     1. Creates an offline SQLite backup in `data/backups/`.
     2. Verifies backup file integrity.
     3. Stages the 283 rows into an isolated SQLite database clone.
     4. Verifies concurrency invariants (`PRAGMA data_version;`).
     5. Atomically promotes the imported records to the target database.
   - If any decision is missing, malformed, or duplicated, the server returns HTTP 400 and halts before any write.
   - Re-importing the same workbook is strictly idempotent and preserves historical outreach statuses and operator decisions.

> **CRITICAL OPERATIONAL SAFEGUARD:** Only execute the authoritative operational database import when explicitly authorized by system leadership. Testing and previewing is always strictly safe and offline.

---

## 4. How to Run a Lead Discovery Search (Strictly Pilot-Gated)

1. Navigate to **Discovery** (`/sales/discovery`).
2. Select your target parameters:
   - **Country:** (e.g. `Italy`, `Austria`, `Germany`)
   - **City / Region:** (e.g. `Milano`, `Torino`, `Roma`, `Bozen`)
   - **Business Category:** (e.g. `BIM Engineering & Structural Consultancy`, `Architectural Design Studio`)
   - **Additional Keywords:** (e.g. `progettazione bim revit`)
3. **Step 1 — Preview Dry Run (Zero Network Calls):**
   - Click **Preview Search (Dry Run)**.
   - Formats the query string and displays the estimated cost (~$0.005 USD) with **zero billable or external network calls**.
4. **Step 2 — Live Search Execution:**
   - Note: Brave Search API does not offer a read-only billing/quota check endpoint.
   - Check the billing authorization checkbox:
     `[x] I acknowledge estimated search cost and confirm account billing authorization`
   - Click **Run Live Search (1 Query)**.
5. **Atomic Pre-Dispatch Safeguards & Double-Click Protection:**
   - SalesAI executes an atomic SQLite pre-dispatch reservation (`BEGIN IMMEDIATE`) against the 20-attempt session cap before dispatch.
   - A deterministic, parameter-bound **idempotency token** server-side blocks accidental double-clicks or browser back/forward replays from creating second requests.
   - Maximum 1 query, maximum 10 results returned.
   - Network timeout or ambiguous transport failures preserve uncertainty without silently cancelling dispatched requests.

---

## 5. Reviewing Candidates & Exporting Approved Leads

1. Navigate to **Candidates** (`/sales/candidates`).
2. Each candidate displays an explicit **Trust State** (`SEARCH_RESULT`, `POTENTIAL_COMPANY`, `VERIFIED_WEBSITE`, `APPROVED_LEAD`).
3. Click on a company to open its **Candidate Detail View** (`/sales/candidates/{candidate_id}`).
4. In the **Operator Review Decision** panel, select an action:
   - **Approve Lead:** Marks candidate as `APPROVED`.
   - **Reject Candidate:** Marks candidate as `REJECTED`.
   - **Mark Duplicate:** Marks candidate as `DUPLICATE`.
   - **Needs Research:** Marks candidate as `NEEDS_RESEARCH`.
   - Enter optional **Review Notes** and submit.
5. **Exporting Approved Leads:**
   - Click **✅ Export Approved Leads (CSV)** or **✅ Export Approved Leads (XLSX)**.
   - The export strictly filters by `manual_review_decision == 'APPROVED'`.
   - Automated rule qualification (`qualified_for_enrichment`) alone does **NOT** count as human approval and is excluded from approved exports.
   - To export all records for auditing, click **📥 Export All (CSV)** (`approved_only=false`).

---

## 6. How to Stop SalesAI Safely

In the PowerShell or Command Prompt terminal running the application:
1. Press `Ctrl + C`.
2. The Uvicorn server performs an orderly graceful shutdown.
3. All candidates, review decisions, pilot search attempt records, and historical imports are safely persisted in SQLite (`data/sales_outreach.db`).
