# SalesAI — Final Operational Acceptance Audit Report

**Date & Time:** October 30, 2026 — 09:56 UTC+1  
**Target Environment:** SalesAI MVP-01.5.2b (`C:\Sales-AI\SalesAI`)  
**Scope:** Read-Only Production Verification & Isolated Acceptance Testing  
**Audit Status:** COMPLETE — ZERO CODE MODIFICATIONS REQUIRED  

---

## 1. Executive Summary & Recommendation

### Final Recommendation: **READY FOR DAILY OPERATOR USE**

The independent operational audit of SalesAI MVP-01.5.2b is complete. The system has successfully transitioned from development into a reliable local operational state:
1. **Production Database Integrity:** The live production database was inspected strictly read-only. All 283 imported historical records, contact suppression rules, and historical outreach events remain 100% intact.
2. **Pilot Persistence & Restart Survival:** Both operator-saved pilot leads (`Studio Tecnico Miotto` and `IPT Project S.r.l.`) are safely stored. The operator's manual edit and test note on `IPT Project S.r.l.` survived server restart without loss or corruption.
3. **Data Loss Prevention Verified:** In an isolated test database, repeated single saves and "Save All" operations confirmed that human modifications are never overwritten.
4. **Export & Search Precision:** 13-column CSV and XLSX exports correctly preserve multiline text, Italian accents, and formula safety. Register search filtering operates cleanly without leaking unrelated records.
5. **Operational State:** The operator can immediately proceed to save the remaining five curated pilot companies and research additional businesses through the web application.

---

## 2. Section A: Production Database Audit (Strictly Read-Only)

Inspection was performed via SQLite read-only URI (`mode=ro`). The database was NOT altered.

### A.1 Table Record Counts
| Entity / Table | Historical Baseline | Current Count | Status / Notes |
|---|---|---|---|
| `candidate_companies` | 284 | **286** | Exactly +2 new candidates (`studiotecnicom.it` and `iptproject.it`) |
| `historical_imports` | 283 | **283** | Untouched, strictly preserved |
| `historical_contacts` | 988 | **988** | Untouched, strictly preserved |
| `historical_company_links` | 741 | **741** | Untouched, strictly preserved |
| `historical_outreach_events` | 142 | **142** | Untouched, strictly preserved |
| `lead_register_records` | 0 | **3** | Contains `Zero Send Co`, `studiotecnicom.it`, and `iptproject.it` |
| `discovery_observations` | 283 | **285** | Exactly +2 observations for the two saved pilot leads |

### A.2 Pilot Company Records
1. **IPT Project S.r.l.**
   - **Candidate ID:** `cand:domain:iptproject.it`
   - **Company Domain:** `iptproject.it`
   - **Tier:** `tier_2` (Priority B)
   - **Discovery Status:** `qualified`
   - **Cold Outreach Allowed:** `0` (`False` — outreach correctly blocked until explicit human approval)
   - **Manual Review Decision:** `APPROVED` (`Operator approved lead from verified pilot research.`)
   - **Lead Register Provenance:** `operator_edited`
   - **Survival of Operator Notes:** **VERIFIED**. The field `descriptions_and_notes` contains the operator's test text:
     ```text
     IPT Project S.r.l. - Ingegneria e Architettura - Via Prima Strada 35 Padova - P.IVA 05152010285; Sviluppo integrato di progetti complessi con standard qualitativi certificati e metodologia BIM

     SalesAI test - IPT Project - 29 October 2026
     ```
   - **Contact Email:** `info@iptproject.it`

2. **Studio Tecnico Miotto**
   - **Candidate ID:** `cand:domain:studiotecnicom.it`
   - **Company Domain:** `studiotecnicom.it`
   - **Tier:** `tier_2` (Priority B)
   - **Discovery Status:** `new`
   - **Cold Outreach Allowed:** `0` (`False`)
   - **Manual Review Decision:** `NOT_REVIEWED`
   - **Lead Register Provenance:** `pilot_research`
   - **Contact Email:** `info@studiotecnicom.it`

### A.3 Candidate Identity & Domain Integrity
- **Duplicate Candidate IDs:** `0` (None found)
- **Duplicate Non-Null Domains:** `0` (None found)
- **Null Domain Candidates:** Exactly `2` (the legacy test candidate `Zero Send Co` and `Studio di architettura Barzon-Ceola`, both expected historical artifacts)
- **Suppression State:** All historical DNC constraints, outreach history, and suppression rules remain intact.

---

## 3. Section B: Isolated End-to-End Acceptance Tests

Automated testing was conducted using isolated temporary SQLite databases (`tests/test_mvp01_5_2a_data_loss_prevention.py` and dedicated verification scripts) without touching operational data.

### B.1 Acceptance Verification Results
1. **Save Pilot Company:** Verified via real HTTP endpoint `/sales/research/pilot/save-single`. Record created with initial provenance `pilot_research`.
2. **HTTP 13-Field Edit:** Submitted modifications to notes (`HAND-WRITTEN OPERATOR NOTE...`) and contact email (`direct.vallerini@incide.invalid`) via `/sales/register/{candidate_id}/update`. Provenance transitioned to `operator_edited`.
3. **Restart & Repeat Pilot Save:** Reloaded from a clean SQLite connection and re-executed `save-single` for the same domain. **Operator-edited notes and email were 100% preserved**.
4. **Save All Idempotency:** Executed `/sales/research/pilot/save-all`. Candidate count was exactly 7; zero duplicate observations created. Human-authored notes remained intact.
5. **CSV & XLSX Export:** Both exports produced 13 columns. Multiline notes and Italian characters (`S.r.l.`, `Padova`, `Ingegneria`) rendered properly without corruption. Formula injection protection (`=`, `+`, `-`, `@`) remained active.
6. **Decoupled Audit Trail:** Editing 13-field register notes did not modify `cand.manual_review_notes` (formal approval/rejection rationale preserved).
7. **Permission Preservation:** Re-researching an approved candidate preserved `cold_outreach_allowed=True` and `manual_review_decision=APPROVED`. Re-researching a rejected candidate preserved `cold_outreach_allowed=False` and suppression status.
8. **Outreach Gate:** Saving for research defaulted to `cold_outreach_allowed=False` and `manual_review_decision=NOT_REVIEWED`.

### B.2 Search Filtering Audit
Register search filtering was verified:
- Querying `IPT Project` matched only `IPT Project S.r.l.` and `Zero Send Co` (which legitimately matched because the operator had tested editing IPT Project notes into Zero Send Co). It did not leak unrelated engineering firms.
- Querying `Studio Tecnico Miotto` returned exactly 1 matching record (`cand:domain:studiotecnicom.it`).

---

## 4. Section C: Operational Database Durability & Backup

### C.1 Database Configuration & Persistence
- **Operational Database Path:** `C:\Sales-AI\SalesAI\data\sales_outreach.db`
- **Journal Mode:** WAL (`Write-Ahead Logging`), synchronous `NORMAL`
- **Integrity Status:** `PRAGMA integrity_check` returned `ok`. `PRAGMA foreign_key_check` returned 0 errors.
- **Restart Durability:** Confirmed. Operator edits performed on October 29 were present and intact upon inspection on October 30 across multiple process restarts.

### C.2 Backup Verification
The SQLite Online Backup API was tested by streaming a live snapshot into a temporary backup file:
- **Backup Integrity:** `PRAGMA integrity_check` returned `ok`.
- **Row Verification:** All 286 candidate rows were verified present in the backup.
- **Existing Backups:** Four historical backups are preserved in `data\backups\` (ranging from 479 KB to 30.7 MB). None were overwritten or modified.

---

## 5. Section D: Research Capabilities vs. Limitations

To ensure realistic operator expectations, functional capabilities are distinguished from architectural boundaries:

### Functional Capabilities
- **Manual Lead Entry:** Operators can manually register any company and populate all 13 fields.
- **Bounded Single-Page Research:** The web app can fetch a designated company website URL, validating TLS certificates, blocking private/internal IP ranges (SSRF protection), and extracting visible contact data (P.IVA, public inboxes, phone switchboards).
- **Provenance Tracking:** Distinguishes `historical_import`, `pilot_research`, `operator_researched`, and `operator_edited`.
- **Field-Level Resilience:** Missing data is explicitly marked `Not Found` or `Unverified`.
- **Spreadsheet Interchange:** Full export to 13-column CSV and XLSX matching the pyBIM workbook layout.

### Architectural Boundaries & Limitations
- **No Autonomous Multi-Page Crawling:** The researcher performs a bounded single HTTP request (up to 250 KB) to the target page. It does not spider links or crawl entire domains.
- **No External LLM Inference:** Summaries and notes are extracted verbatim from meta description and HTML titles; no AI model parses or hallucinates data.
- **Curated Pilot Scope:** The seven pilot profiles are pre-researched curated seeds representing regional engineering firms, not dynamically fetched live crawl results.
- **No Automated Outreach:** SalesAI does not send autonomous cold emails. All outreach requires explicit operator review and manual release.

---

## 6. Test Suite Summary

All 56 automated tests across the test suite passed with zero failures:
- `tests/test_real_lead_research_pilot.py`: **7 passed**
- `tests/test_mvp01_5_2_lead_researcher_safety.py`: **6 passed**
- `tests/test_mvp01_5_2_acceptance.py`: **7 passed**
- `tests/test_mvp01_5_2a_data_loss_prevention.py`: **4 passed**
- `tests/test_mvp01_lead_discovery.py`: **8 passed**
- `tests/test_mvp01_2_real_workbook_and_search_safety.py`: **11 passed**
- `tests/test_mvp01_3_real_data_acceptance.py`: **10 passed**
- `tests/test_launcher_port_guard.py`: **3 passed**

**Total:** **56 passed, 0 failed, 17 Starlette deprecation warnings.**

---

## 7. Next Operator Steps

The operator can now proceed with confidence to:
1. Navigate to [http://127.0.0.1:8000/sales/research/pilot](http://127.0.0.1:8000/sales/research/pilot).
2. Save the remaining five curated pilot companies:
   - **Stefano Baseggio** (`stefanobaseggio.it`)
   - **E-Farm Engineering** (`e-farmsrl.com`)
   - **Incide Engineering** (`incide.it`)
   - **STC Group** (`stcpi.com`)
   - **ei bim project** (`eibimproject.com`)
3. Navigate to [http://127.0.0.1:8000/sales/register](http://127.0.0.1:8000/sales/register) to edit, enrich, or classify leads across the 13 fields.
4. Add new companies via the bounded research form or manual registration.
5. Export the updated lead register to XLSX or CSV at any time.
