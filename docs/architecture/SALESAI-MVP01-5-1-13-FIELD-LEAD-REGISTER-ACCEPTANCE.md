# SalesAI MVP-01.5.1 — 13-Field Lead Register & Pilot Hardening Acceptance Report

## 1. Executive Summary & Audit Resolution

This release completes **SalesAI MVP-01.5.1**, resolving all independent audit findings from MVP-01.5 and delivering the persistent, editable **13-Field Lead Register** matching the historical pyBIM workbook.

### Audit Issues Addressed
1. **Missing Typing Import (`Tuple`):**
   - Corrected in `sales_engine/research/pilot_research.py`. Unmodified extracted source now passes test collection and execution cleanly.
2. **Operational Database Isolation:**
   - Enforced session-level and per-test isolation in `tests/conftest.py`.
   - Every pytest execution runs against an isolated temporary SQLite database.
   - Accidental access to or modification of the operator's operational database (`data/sales_outreach.db`) is blocked and asserted against.
   - Operational baseline counts were verified before and after test runs and remained completely unchanged:
     - `candidate_companies`: **284**
     - `historical_imports`: **283**
     - `historical_contacts`: **988**
     - `historical_company_links`: **741**
     - `historical_outreach_events`: **142**
     - Operational DB dump SHA-256 digest: `60a75a3fde6a9582bfaa47c0b7306e6b80cc55957b94ad7ada76656a12380ebd`
3. **Removal of Unsupported Business Assumptions:**
   - In `sales_engine/research/pilot_research.py`, removed automatic assumptions: `direct_project_access` defaults to `False`, `bim_maturity` defaults to `unknown`, `intent_assessment` defaults to `unknown`, and `cold_outreach_allowed` defaults to `False` (requiring explicit human operator review).
   - Public business emails are stored as organization contact mailboxes rather than fabricating individual contact persons.
4. **Curated Seed Labeling vs Reusable Automated Research:**
   - Clarified that the 7 pilot companies are curated operator review seeds.
   - Built a reusable bounded research service in `sales_engine/research/lead_researcher.py` allowing operators to research arbitrary new websites without external LLMs or arbitrary crawling.
5. **Hotfix Hardening (MVP-01.5.2):**
   - Secured `lead_researcher.py` with strict SSRF guards, re-enabled normal TLS certificate validation, SafeRedirectHandler revalidation, and DNS checks.
   - Guarded `LeadRegisterStore` against missing optional tables on fresh or candidate-only databases.
   - Aligned verification semantics: HTTP fetch success yields `EXTRACTED` (not Verified); manual edit yields `OPERATOR_REVIEWED`.
   - Preserved candidate identity integrity and manual review decisions during edits.

---

## 2. The 13-Field Lead Register

The lead register implements the exact 13 information columns of the pyBIM workbook:

| # | Field Name | Description |
|---|---|---|
| 1 | **Row / Record Number** | Sequential or workbook row index |
| 2 | **Company Priority** | Tier A (High potential), Tier B (Standard), Tier C (Specialist/Low) |
| 3 | **Priority Rationale** | Factual business reasoning for tiering |
| 4 | **Company Name & Legal Identity** | Full legal name, corporate form, Partita IVA / Codice Fiscale |
| 5 | **Geographic Location** | Verified address, municipality, region, and country |
| 6 | **Telephone Numbers** | Public telephone switchboards or mobile numbers |
| 7 | **Business Activities / Services** | Scope of engineering, architecture, and BIM services |
| 8 | **Official Website** | Canonical apex domain and validated official URL |
| 9 | **Responsible Person / Contact** | Verified directors, partners, or BIM managers (or `Not Found`) |
| 10 | **Descriptions & Operator Notes** | Multiline qualitative text preserved in source language |
| 11 | **Job Advertisement Links** | Verified recruitment URLs or public job postings |
| 12 | **Other Links / Projects / References** | Multiline portfolio links, certifications (ISO 9001), references |
| 13 | **Contact Email Addresses** | Public business emails and published inquiry inboxes |

### Persistence & Separation of Concerns
- Persisted in SQLite table `lead_register_records` (`sales_engine/leads/register_store.py`).
- Readily editable via `/sales/register` (table view) and `/sales/register/{candidate_id}` (detail edit view).
- Complete separation of editorial fields from historical suppression/outreach constraints.
- Process restart persistence verified: edits survive application shutdowns and restarts.
- Full spreadsheet export to **XLSX** and **CSV (UTF-8 with BOM)** at `/sales/register/export` with spreadsheet formula injection protection.

---

## 3. Review Guardrails & Bounded Research

- **Review Guardrails:** Reject invalid decision strings and mismatched candidate-to-domain identities with HTTP 400.
- **Bounded Research Workflow:** Operator enters company name and website URL; system performs a single, bounded HTTP request to extract visible meta tags, contact emails, phone numbers, and P.IVA. Values not verified remain `Not Found` or `Unverified`.

---

## 4. Test Suite Execution & Acceptance

```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Sales-AI\SalesAI
configfile: pytest.ini
collected 52 items

tests/test_real_lead_research_pilot.py .......                           [ 13%]
tests/test_mvp01_5_2_lead_researcher_safety.py ......                    [ 25%]
tests/test_mvp01_5_2_acceptance.py .......                               [ 38%]
tests/test_mvp01_lead_discovery.py ........                              [ 53%]
tests/test_mvp01_2_real_workbook_and_search_safety.py ...........        [ 75%]
tests/test_mvp01_3_real_data_acceptance.py ..........                    [ 94%]
tests/test_launcher_port_guard.py ...                                    [100%]

====================== 52 passed, 10 warnings in 14.46s =======================
```

---

## 5. Release Archive Information

- **ZIP Archive:** `C:\Sales-AI\SalesAI_export.zip`
- **SHA-256 Checksum:** Generated following final package build.
- **Database Status:** Operational database untouched (`284` candidates, `283` historical imports).
