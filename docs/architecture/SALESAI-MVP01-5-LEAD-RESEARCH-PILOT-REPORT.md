# SalesAI — First Real Lead Research Pilot Technical Report

## 1. Executive Summary & Objective
Following successful Brave Search connectivity, this milestone moves SalesAI from raw search results into structured, human-reviewed, and operator-controlled lead intelligence. 

The pilot investigated and profiled **7 verified Italian engineering & architecture targets** derived from real discovery queries without executing additional live Brave search requests, without calling external LLM providers, and without performing automated outreach.

---

## 2. Pilot Companies Profile & Classification Analysis

| # | Company Name | Canonical Domain | Classification | Provisional Tier | Confidence | Core Evidence & Opportunity Signals |
|---|---|---|---|:---:|:---:|---|
| 1 | **Incide Engineering S.r.l.** | `incide.it` | Potential Client | **A** | 95% | **Hiring Signal:** Active recruitment page for *"Progettista BIM Strutture"* demanding Tekla Structures & Revit expertise. Multidisciplinary global civil/industrial engineering. |
| 2 | **STC Group (STC S.r.l.)** | `stcpi.com` | Potential Client | **A** | 92% | **MEP BIM Complexity:** Specialized mechanical & electrical engineering with complex spatial coordination, hospital & datacenter projects, dedicated development contact (`development@stcpi.com`). |
| 3 | **IPT Project S.r.l.** | `iptproject.it` | Potential Client | **B** | 90% | **Certified Engineering:** ISO 9001 certified engineering firm in Padova; public works & infrastructure adhering to DM 560/2017 BIM standards. |
| 4 | **E-Farm Engineering S.r.l.** | `e-farmsrl.com` | Potential Client | **B** | 88% | **OpenBIM & Standards:** Dedicated BIM workflow section (`/e-farm/bim/`), IBIMI buildingSMART member, ISO 19650 compliance, Solibri model checking. |
| 5 | **Studio Tecnico Miotto** | `studiotecnicom.it` | Potential Client | **B** | 85% | **Established Regional Practice:** Dedicated BIM service offering (`/servizi/bim/`), architectural & structural modeling in Padova area. |
| 6 | **Stefano Baseggio Architetto** | `stefanobaseggio.it` | Technical Partner | **C** | 80% | **Boutique Architecture:** Individual practice specializing in residential BIM on Revit & Archicad. Highly skilled but lower enterprise software budget; classified as potential partner rather than enterprise client. |
| 7 | **ei bim project S.r.l.** | `eibimproject.com` | Competitor / Service Bureau | **C** | 90% | **Non-Buyer / Service Provider:** Core business is BIM consulting, Scan-to-BIM outsourcing (Leica RTC360, drones), and CDE training for third parties. Classified as competitor / subcontractor. |

---

## 3. Data Integrity & Verification Audit

### A. Populated High-Confidence Fields (100% Verified)
- **Official Apex Domain & Service URLs:** Verified HTTPS URLs directly pointing to official domains and BIM capability sections.
- **Corporate Legal Identity & Tax IDs:** Extracted official Italian `Partita IVA` and `Codice Fiscale` for all 7 entities.
- **Geographic Grounding:** Verified office addresses and city/province data (Padova, Vigonza, Arzergrande, San Salvo).
- **Public Contact Details:** Verified public telephone switchboards and inbound contact emails.
- **Italian Source Language:** Preserved raw technical capability terms, company descriptions, and project scopes in source Italian without automated translation distortions.

### B. Fields Intentionally Left Missing / Unverified (Safety & Privacy Compliance)
- **Individual Names / Personal Cell Numbers:** Not hallucinated or guessed. Generic office contacts and public phone numbers are retained.
- **Private Financial Data:** Turnover and balance sheet data are not fabricated when absent from public websites.
- **Unverified Toolchains:** Only software explicitly cited on public websites (e.g., Revit, Tekla, Archicad, Solibri, Navisworks) is recorded.

---

## 4. Architectural Implementation & Operator Controls

1. **Research Module ([pilot_research.py](file:///C:/Sales-AI/SalesAI/sales_engine/research/pilot_research.py)):**
   - Implements structured data models (`ResearchedLeadProfile`, `FieldEvidence`).
   - Deterministic pilot data registry with per-field confidence and source citations.
   - Idempotent insertion logic ensuring no duplicate candidates or observations are created.
   - Collision detection against both canonical domains and normalized company names across the SQLite database.

2. **Operator Review Web UI ([pilot_research.html](file:///C:/Sales-AI/SalesAI/backend/sales_ui/templates/pilot_research.html) & [router.py](file:///C:/Sales-AI/SalesAI/backend/sales_ui/router.py)):**
   - Accessible via console navigation and directly from Discovery (`/sales/research/pilot`).
   - Displays structured table matching historical Excel fields: Domain, Location, P.IVA, BIM Capabilities, Technologies, Classification, Tier, Public Contact, and Status.
   - Single-click **Save to Candidates** and **Save All** actions.
   - Operator review controls with explicit **Approve for Outreach** / **Reject Lead** decisions.
   - Cross-Site Request Forgery (CSRF) protection on all form submissions.

3. **Persistence & Export Pipeline:**
   - Saved candidates enter `candidate_companies`, `discovery_observations`, and `market_evidence` tables with full provenance.
   - Operator approvals update candidate status to `qualified` (`manual_review_decision='APPROVED'`).
   - Verified spreadsheet export (`/sales/candidates/export`) with CSV and XLSX generation and formula injection neutralization (`=`, `+`, `-`, `@`).

---

## 5. Test Suite Verification
All 38 relevant unit, safety, and integration tests pass cleanly:
```text
tests/test_real_lead_research_pilot.py ............ 6 passed
tests/test_mvp01_lead_discovery.py ................ 8 passed
tests/test_mvp01_2_real_workbook_and_search_safety.py 11 passed
tests/test_mvp01_3_real_data_acceptance.py ....... 10 passed
tests/test_launcher_port_guard.py ................ 3 passed
======================= 38 passed, 6 warnings in 9.66s ========================
```

Release package rebuilt:
- **Archive:** `C:\Sales-AI\SalesAI_export.zip`
- **SHA-256:** `06e906ea8b33259c21aac623523f3e67106769cb55163235df970ed03359656f`
