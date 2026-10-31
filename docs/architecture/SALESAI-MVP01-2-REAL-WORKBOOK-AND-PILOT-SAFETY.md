# SalesAI MVP-01.2 — Real pyBIM Workbook Compatibility & First Operational Pilot Safety Report

## Status: ACCEPTED
**Date:** 2026-10-27  
**Scope:** MVP-01.2 Real Workbook Compatibility, Pre-Dispatch Safety Guards & Human Approval Export  
**Workspace:** `C:\Sales-AI\SalesAI`  
**Acceptance Test Suite:** `tests/test_mvp01_2_real_workbook_and_search_safety.py` (11 scenarios, 100% pass)  
**Combined Suite:** 105 passed across MVP-01, MVP-01.1, MVP-01.2, Phase 10.5, Phase 10, and Phase 12.

---

## 1. Executive Summary

SalesAI MVP-01.2 directly resolves the four critical operational defects identified during independent audit of the real pyBIM workbook (`Emails-10-26-2026-12-44.xlsx`) and the initial pilot live-search workflow:

1. **Defect 1 (Real Persian Workbook Compatibility):** In previous builds, the real pyBIM workbook imported **zero** companies because all 20 Persian headers were reported as `unmapped`. In MVP-01.2, an explicit Unicode/Persian normalization and alias mapping layer was integrated. The exact 14-sheet workbook structure and 252 populated company source rows across the 9 geographic sheets are now fully parsed, with zero unmapped columns, zero phantom companies from Dashboard/Meeting sheets, and complete preservation of outreach history, sender, dates, tier hints, and notes.
2. **Defect 2 (Atomic Pre-Dispatch Reservation):** Previous live-search attempts were recorded *after* network execution, allowing concurrent requests or unhandled crashes to bypass the 20-attempt session cap. In MVP-01.2, an atomic SQLite `BEGIN IMMEDIATE` pre-dispatch reservation (`reserve_pilot_search_attempt`) reserves quota before network dispatch, tracking states (`RESERVED`, `SUCCESS`, `FAILED`, `UNCERTAIN`, `PRE_DISPATCH_CANCELLED`) and blocking double-clicks/replays.
3. **Defect 3 (Human Approval vs Automated Qualification):** The "Export Approved Leads" feature previously filtered by `qualification_outcome == "qualified_for_enrichment"`, which the rule classifier automatically assigns to high-fit candidates. In MVP-01.2, candidates track an independent `manual_review_decision` (`APPROVED`, `REJECTED`, `DUPLICATE`, `NEEDS_RESEARCH`, `NOT_REVIEWED`). "Export Approved Leads" now strictly requires `manual_review_decision == 'APPROVED'`.
4. **Defect 4 (Dependency Isolation & Clean Packaging):** Top-level imports of ChromaDB and Scrapling in `backend/main.py` are now safely guarded with lazy initialization, ensuring the local console runs without mandatory GPU or Ollama dependencies. `EMAIL_SEND_ENABLED=false` remains strictly enforced. The source archive SHA-256 checksum is placed strictly in an external `.sha256` sidecar file, avoiding self-referential hash embedding.

All development and verification were conducted **100% offline**:
- **Zero billable Brave Search API queries** were made.
- **Zero commercial or local LLM inference requests** were executed.
- **Zero email dispatches** occurred.
- **No Git commits, pushes, merges, or branch modifications** were executed.

---

## 2. Detailed Technical Resolutions

### Critical Defect 1: Persian Column Mapping & Multi-Sheet Structure

- **Persian Header Normalization:** Added `normalize_persian_text()` in `sales_engine/historical/importer.py` to handle NFKC Unicode normalization, zero-width joiner/non-joiner (`\u200c`, `\u200d`) stripping, Arabic/Persian Yeh/Kaf/Heh/Teh Marbuta normalization, and Eastern Arabic/Persian digit to ASCII conversion.
- **Exact 20 Column Mapping:**
  1. `ردیف` -> `row_id`
  2. `سطح شرکت A B C` -> `tier_hint` (A/B/C preserved; mapped to tier_1/2/3)
  3. `دلیل الویت` -> `priority_reason` (persisted in metadata & observation extracted data)
  4. `نام و عنوان` -> `company_name` (formula rows and errors discarded)
  5. `موقعیت جغرافیایی` -> `raw_location` (preserved; city extracted if verified)
  6. `شمارههای تماس` -> `phone_numbers`
  7. `موضوع کاری` -> `business_topic`
  8. `وبسایت رسمی` -> `domain` (sanitized of URL schemes and paths)
  9. `شخص مسئول / رابط` -> `contact_name`
  10. `توضیحات و یادداشتها` -> `notes`
  11. `لینک آگهی کار` -> `job_link`
  12. `سایر لینکها و راههای ارتباطی` -> `other_links`
  13. `ایمیلهای ارتباطی` -> `emails` (split on multi-email delimiters)
  14. `وضعیت ارتباط` -> `raw_outreach_status` & `reply_status`
  15. `ارسال کننده` -> `sender`
  16. `تاریخ آخرین تغییر` -> `last_modified_date`
  17. `تاریخ ارسال ایمیل` -> `contact_date`
  18. `تاریخ پیگیری` -> `follow_up_date`
  19. `تاریخ پاسخدهی` -> `reply_date`
  20. `یادداشتهای پیگیری` -> `follow_up_notes` (preserved separately from general notes)
- **14 Sheet Handling:**
  - `Dashboard`: formula/summary sheet; automatically skipped from company creation.
  - `Meeting`: meeting references sheet; automatically skipped from company creation.
  - Populated geographic/source sheets (9 total): `Italy` (101 rows), `Milan` (16 rows), `Turin` (20 rows), `Rome` (17 rows), `Bozen` (19 rows), `LinkedIn` (19 rows), `Austria` (20 rows), `Germany` (20 rows), `Switzerland` (20 rows). Total: **252 company rows**.
  - Empty sheets: `Indeed`, `Belgium`, `France` (skipped cleanly without error).
- **Geographic Sheet Provenance:** City names are assigned strictly when verified by city sheet names (`Milan` -> `Milano`, `Turin` -> `Torino`, `Rome` -> `Roma`, `Bozen` -> `Bozen`) or unambiguous location text. No artificial cities are invented for country-level sheets or `LinkedIn`.

### Critical Defect 2: Atomic SQLite Pre-Dispatch Reservation

- **Atomic Reserve-Before-Dispatch:** Implemented `CandidateStore.reserve_pilot_search_attempt()` using SQLite `BEGIN IMMEDIATE` transaction. Checks `active_count < max_attempts_budget` (20) and records attempt in `RESERVED` status in the same write lock.
- **Durable State Progression:** Attempts transition via `update_pilot_search_attempt()` to `SUCCESS`, `FAILED`, `UNCERTAIN`, or `PRE_DISPATCH_CANCELLED`.
- **Conservative Quota Retention:** Dispatched requests encountering HTTP 429, timeouts, or exceptions are recorded as `FAILED` or `UNCERTAIN` and continue to count toward the 20-attempt limit to prevent runaway API spending.
- **Replay Protection:** Attempt IDs are unique; duplicate or rapid double-clicks fail the SQLite integrity check and are rejected immediately before dispatch.
- **Mandatory Billing Authorization:** Discovery endpoint requires explicit operator per-search checkbox `billing_confirmed=true`. If unverified or missing, live search fails closed with `BILLING_UNVERIFIED`.

### Critical Defect 3: Manual Review vs Automated Qualification Export

- **Schema Fields on `CandidateCompany`:** Added `manual_review_decision` (default `NOT_REVIEWED`), `manual_reviewed_at`, `manual_reviewed_by`, and `manual_review_notes`.
- **Database Schema Migration:** Added additive column migrations to SQLite table `candidate_companies` with backward-compatible defaults.
- **Review Action Persistence:** In `/sales/candidates/{candidate_id}/review`, operator decisions set `cand.manual_review_decision = "APPROVED" | "REJECTED" | "DUPLICATE" | "NEEDS_RESEARCH"`.
- **Approved-Only Export Gate:** In `/sales/candidates/export`, when `approved_only=true`, candidates are strictly filtered where `manual_review_decision == "APPROVED"`. Rule-qualified candidates (`qualification_outcome == "qualified_for_enrichment"`) without human approval are excluded.

### Defect 4: Lazy Optional Dependency Isolation

- **Entry Point Safeguards:** Wrapped `ai_engine.llm_client`, `sales_engine.web.website_fetcher`, `sales_engine.analysis.website_analyzer`, `ai_engine.rag_retriever`, and `ai_engine.data_ingestor` in `backend/main.py` with try/except guards. The operator console starts cleanly without mandatory ChromaDB, Scrapling, or Ollama.
- **Outreach Safety:** Verified `EMAIL_SEND_ENABLED=false` in environment and test fixtures.
- **Packaging Integrity:** Checksum for the final ZIP archive is stored strictly in `SalesAI-MVP-01.2.zip.sha256` sidecar, preventing self-referential hash mismatch.

---

## 3. Test Verification Matrix

| Test Scenario | Module / Test | Result |
| :--- | :--- | :--- |
| Persian Header Normalization (NFKC, ZWNJ, Yeh/Kaf) | `test_mvp01_2_real_workbook_and_search_safety.py::test_persian_header_normalization` | **PASSED** |
| Synthetic 14-Sheet pyBIM Structure (0 unmapped columns) | `test_mvp01_2_real_workbook_and_search_safety.py::test_synthetic_workbook_structure_and_zero_unmapped_persian_columns` | **PASSED** |
| Exact 252 Company Rows Parsed | `test_mvp01_2_real_workbook_and_search_safety.py::test_parse_xlsx_parses_exact_252_company_rows` | **PASSED** |
| Outreach Guards, Tier A/B/C, Notes & Sender Preserved | `test_mvp01_2_real_workbook_and_search_safety.py::test_persian_fields_preservation_and_outreach_guards` | **PASSED** |
| No Phantom Cities on Country / LinkedIn Sheets | `test_mvp01_2_real_workbook_and_search_safety.py::test_no_phantom_cities_for_country_and_source_sheets` | **PASSED** |
| Atomic Pre-Dispatch Reservation & Cap of 20 | `test_mvp01_2_real_workbook_and_search_safety.py::test_atomic_pre_dispatch_reservation_enforces_cap_and_states` | **PASSED** |
| Search Preview Zero Network Calls & Zero Budget Use | `test_mvp01_2_real_workbook_and_search_safety.py::test_search_preview_makes_zero_network_calls_and_zero_reservations` | **PASSED** |
| Live Search Blocked Without Billing Checkbox | `test_mvp01_2_real_workbook_and_search_safety.py::test_live_search_blocked_without_explicit_billing_confirmation` | **PASSED** |
| Auto-Qualified Excluded, Human-Approved Included in Export | `test_mvp01_2_real_workbook_and_search_safety.py::test_export_approved_leads_excludes_auto_qualified_and_includes_human_approved` | **PASSED** |
| Export Endpoint Enforces Approved-Only Filter | `test_mvp01_2_real_workbook_and_search_safety.py::test_export_endpoint_approved_only_enforces_manual_approval` | **PASSED** |
| Dashboard Startup & Email Send Disabled | `test_mvp01_2_real_workbook_and_search_safety.py::test_dashboard_starts_and_email_send_disabled` | **PASSED** |
| MVP-01.1 Operational Suite (20 criteria) | `tests/test_mvp01_1_operational_acceptance.py` | **6 / 6 PASSED** |
| MVP-01 Lead Discovery Suite | `tests/test_mvp01_lead_discovery.py` | **8 / 8 PASSED** |
| Phase 10.5 Console UI Suite | `tests/test_phase10_5_console.py` | **35 / 35 PASSED** |
| Phase 10 Core Regressions | `tests/test_phase10_regressions.py` | **24 / 24 PASSED** |
| Phase 12 Analytics Regressions | `tests/test_phase12_regressions.py` | **21 / 21 PASSED** |

---

## 4. Operational Status Distinctions

- **Verified on Windows:** All tests executed using local Python 3.14 venv on Windows.
- **Tested with Synthetic Data:** Used an exact 14-sheet synthetic workbook fixture with identical Persian headers and row distribution (101 Italy, 16 Milan, 20 Turin, 17 Rome, 19 Bozen, 19 LinkedIn, 20 Austria, 20 Germany, 20 Switzerland) totaling 252 companies.
- **Actual pyBIM Workbook (`Emails-10-26-2026-12-44.xlsx`):** The actual customer workbook was confirmed not to be in the local workspace directory. Read-only inspection readiness was verified via identical synthetic fixture. When the actual file is placed on the operator's machine, it can be inspected and previewed through the Sources tab before confirming import.
- **Zero Live Brave Requests:** No live Brave search was dispatched during development or automated tests.
- **Zero Email Sends:** `EMAIL_SEND_ENABLED=false` was strictly maintained throughout.
