# SalesAI MVP-01.4.1 — Multi-Link Workbook Intelligence & Final Safe Import Acceptance Report

**Date:** October 27, 2026  
**Workspace:** `C:\Sales-AI\SalesAI`  
**Supervised Workbook Evaluation:** `Emails-10-27-2026-18-19.xlsx` (Authoritative pyBIM Workbook)  
**Supersession Notice:** Evaluated against `Emails-10-27-2026-18-19.xlsx` (present on local machine, 1.9 MB). If `Emails-10-27-2026-20-25.xlsx` is supplied later, it inherits this exact deterministic multi-link parser and validation gate.  
**Mode:** STRICTLY OFFLINE. Zero live Brave searches, zero external LLM inference, zero emails sent, zero unmonitored production database modification.

---

## 1. Executive Summary

SalesAI MVP-01.4.1 introduces structured multi-link website intelligence and closes the loop on historical workbook ingestion. In pyBIM's multi-regional dataset (Switzerland, Bozen, Austria, Germany, Italy, Milan, Turin, Rome, LinkedIn), official website cells frequently contain rich multiline web references, markdown labels, and `[link removed]` audit placeholders. Previously, naive URL splitting caused malformed domains (such as trailing colons or broken schemes) or lost vital secondary URLs (projects, team, careers, BIM management).

With MVP-01.4.1:
1. **Deterministic Multi-Link Parser (`parse_multiline_website_cell`):** Deterministically categorizes all links into structured types (`OFFICIAL_HOME`, `PROJECTS`, `SERVICES`, `BIM_CAPABILITIES`, `CAREERS`, `TEAM`, `ABOUT`, `CONTACT`, `SOCIAL_PROFILE`, `EXTERNAL_REFERENCE`, `LANGUAGE_VARIANT`, `UNRESOLVED`, `OTHER`), while extracting a single authoritative canonical host without picking social networks or job boards.
2. **Normalized Child-Link Storage (`historical_company_links`):** All 741 subsidiary and related URLs/placeholders are preserved in a dedicated SQLite table and attached to candidate discovery observations, without overloading or polluting the canonical company domain.
3. **Parity in Server-Side Validation Gates:** The confirmation endpoint `/sales/sources/import-history/confirm` now enforces strict structural checks (rejecting 0-row attempts, missing files, and unmapped headers), matching the Preview gate.
4. **Authoritative Verification:** Full regression suite passes **50/50 tests**, verifying offline safety, idempotency, and data fidelity across all 283 rows and 14 sheets.

---

## 2. Dynamic Workbook Metrics (Real Data Inspection)

Inspecting `Emails-10-27-2026-18-19.xlsx` dynamically via `HistoricalImporter.inspect_workbook()` yields:

| Metric | Dynamically Calculated Value | Notes |
|---|---:|---|
| **Total Worksheets** | 14 | 9 active company sheets, 5 supplementary/summary sheets |
| **Total Company Source Rows** | 283 | Italy: 101, Milan: 16, Turin: 20, Rome: 17, Bozen: 30, Austria: 20, Germany: 20, Switzerland: 40, LinkedIn: 19 |
| **Rows with Valid Primary Website** | 277 | Clean canonical domains resolved without protocol prefixes |
| **Rows with Placeholder-Only / Missing Website** | 6 | Exactly 6 rows lack active URLs (see Section 4) |
| **Multiline Website Cells** | 71 | Bozen: 11, Austria: 20, Switzerland: 40 |
| **Cells with $\ge$2 Explicit URLs** | 65 | Multiple project, team, career, or language pages |
| **Total Extracted Link Entities** | 741 | 635 URLs + 106 placeholders/unresolved references |
| **Total `[link removed]` Placeholders** | 101 | Audit placeholders preserved with original Persian labels |
| **Multiple Competing Plausible Domains** | 7 | Flagged for operator review (e.g. dual Italian/German entities) |
| **Domain Collisions in Real Data** | 0 | Under clean host extraction, 277 distinct canonical domains |
| **Initial Review Rows (`بررسی اولیه`)** | 141 | Safely preserved as `uncontacted` with `has_past_contact = False` |
| **Genuine Contacted Outreach Rows** | 142 | 137 Email Sent + 2 Interview + 2 Follow-up + 1 Wrong Email |

---

## 3. Link Classification & Subpage Breakdown

Across all 283 company rows, 741 individual links were classified and persisted:

| Category | Count | Description / Example |
|---|---:|---|
| `OTHER` | 328 | Specific deep pages, sub-branches, and utility endpoints |
| `UNRESOLVED` | 106 | `[link removed]` placeholders with preserved Persian source labels |
| `TEAM` | 69 | Mitarbeiter, team, chi siamo, persone, founder profiles |
| `PROJECTS` | 57 | Referenzen, projekte, portfolio, progetti, mofaredat |
| `BIM_CAPABILITIES` | 47 | Revit, ArchiCAD, OpenBIM, digital survey, BIM management |
| `CONTACT` | 36 | Kontakt, info, address, standorte |
| `ABOUT` | 35 | Über uns, studio, history, profile |
| `SERVICES` | 29 | Leistungen, servizi, competence |
| `CAREERS` | 24 | Jobs, karriere, lavora con noi, hiring |
| `LANGUAGE_VARIANT` | 10 | English, German, Italian language switchers |
| **Total** | **741** | **All links stored with foreign-key linkage to canonical domain** |

---

## 4. Primary Website Selection Rules & Ambiguity Resolution

The multi-link parser implements strict precedence rules:

1. **Rule 1 (Explicit Official Marker):** If a line has an official homepage label (`وبسایت رسمی`, `صفحه اصلی`, `Official Website`, `Homepage`), that URL's host is preferred.
2. **Rule 2 (Social / Job Board Disqualification):** Domains in `DISALLOWED_PRIMARY_DOMAINS` (`linkedin.com`, `indeed.com`, `instagram.com`, `facebook.com`, `google.com/maps`, `wikipedia.org`, etc.) are never selected as a company's primary domain when any other candidate exists.
3. **Rule 3 (Subdomain Preservation):** Meaningful architectural subdomains (e.g. `atelier.tricarico.ch`, `riccardobarzon29.wixsite.com`) are preserved rather than aggressively truncated.
4. **Rule 4 (Competing Plausible Domains):** When a cell contains multiple distinct, non-affiliated domain names (e.g. `studio-alfa.it` and `beta-engineering.de`), the row is marked with `multiple_plausible_domains = True` and surfaced in the UI under **Review Required**.

### Rows Requiring Manual Operator Clarification (Placeholder Only):
Exactly 6 rows have no active URL, only `[link removed]` placeholders:
1. `Italy` R81: **Energek Srl** (`[link removed]`)
2. `Bozen` R29: **Oscar “Odd” Diodoro / Odd House** (Illustrator/Artist, 5 placeholders)
3. `Bozen` R31: **Carron Bau S.r.l. – G.m.b.H.** (Turnkey contractor, 4 placeholders)
4. `Austria` R4: **kiss architektur ZT GmbH** (Vienna architect, 7 placeholders)
5. `Switzerland` R31: **Martinelli e Rossi** (`[link removed]`)
6. `Switzerland` R33: **Atelier Tricarico** (Arch. Fabio Tricarico, 7 placeholders)

---

## 5. Cloned Ingestion & Idempotency Acceptance

The ingestion pipeline was validated against an isolated SQLite clone:
- **Companies Ingested:** 283 candidate companies created.
- **Link Entities Inserted:** 741 entries in `historical_company_links`.
- **Outreach Events Ingested:** Exactly 142 historical communication events (137 Email Sent, 2 Interview, 2 Follow-up, 1 Wrong Email).
- **Idempotency Proof:** Re-running `import_records()` on the identical dataset produced:
  - `duplicates_skipped = 283`
  - `historical_company_links` count unchanged at **741** (no duplicate child rows created thanks to unique index `idx_hist_links_unique`).
  - `historical_outreach_events` count unchanged at **142**.

---

## 6. Pre-Import Readiness & Operational Decision

- **Recommendation:** `REVIEW REQUIRED`
- **Rationale:** 
  - 277 companies are 100% ready for automatic ingestion with clean canonical domains and categorized child links.
  - 6 companies contain placeholder-only websites and 7 companies contain multiple distinct domains.
  - Per pyBIM safety guidelines, importing rows with ambiguous identities without prior operator acknowledgment could create synthetic domain stubs. The UI presents the reconciliation panel with actionable status badges.

---

## 7. Operator Instructions for Supervised Import

When the operator decides to execute the live import:
1. **Navigate to:** `http://127.0.0.1:8000/sales/sources`
2. **Stage 1 (Upload & Inspect):** Upload `Emails-10-27-2026-18-19.xlsx` (or `Emails-10-27-2026-20-25.xlsx`).
3. **Stage 2 (Multi-Link Review):**
   - Confirm **283 total rows**, **71 multiline cells**, **741 total links**.
   - Check the **7 Multiple Plausible Domains** and **6 Missing/Placeholder Only** rows.
4. **Stage 3 (Execute Supervised Import):**
   - Click **Confirm Import to Database**.
   - The server enforces validation gate parity, locks against double clicks via idempotency token, writes `data/backups/sales_outreach_backup_*.db`, and ingests candidates, observations, outreach events, and child links safely.
