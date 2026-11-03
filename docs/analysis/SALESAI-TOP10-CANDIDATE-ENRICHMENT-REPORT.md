# SalesAI — Top 10 Candidate Enrichment & Strategic pyBIM Analysis Report

**Date:** 2026-10-31  
**Scope:** Enrichment of the 10 selected candidates from `SalesAI_30_Candidate_Screening_2026-10-31.xlsx` (`Top 10 Deep Dive` worksheet)  
**Production Database Status:** STRICTLY READ-ONLY (No modifications made to `data/sales_outreach.db`)  
**External API Consumption:** Zero Brave Search calls, Zero paid LLM calls  
**Companion Artifact:** `C:\Sales-AI\SalesAI\docs\analysis\SalesAI_Top10_Enriched_Lead_Register_2026-10-31.xlsx`

---

## 1. Executive Summary & Verification Matrix

All 10 target companies from the analyst screening sheet were cross-referenced against the operational SQLite database (`candidate_companies` table) and individually enriched through direct, official public web sources. 

### Database Identity & Verification Mapping

| Candidate ID | Domain | Verified Legal Entity Name | City / Region | Verified P.IVA | Priority | Outreach Eligibility |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: |
| `cand:domain:bbprogettimilano.it` | `bbprogettimilano.it` | **B&B Progetti S.r.l.** | Milano / Torino | `06650400960` | **Priority A** | Uncontacted / Eligible |
| `cand:domain:bdgroup.it` | `bdgroup.it` | **bim design group s.r.l.** | Venezia (Mestre) | `04637460272` | **Priority A** | Uncontacted / Eligible |
| `cand:domain:nxengineering.it` | `nxengineering.it` | **NX Engineering S.r.l.** | Loreto (AN) / Bologna (BO) | `02103040438` | **Priority A** | Uncontacted / Eligible |
| `cand:domain:bs-eng.net` | `bs-eng.net` | **BS Progetti S.r.l.** | Pianiga (VE) | `04095420271` | **Priority A** | Uncontacted / Eligible |
| `cand:domain:atiproject.com` | `atiproject.com` | **ATI Project S.r.l.** | Pisa / Milano | `02255140507` | **Priority A** | Uncontacted / Eligible |
| `cand:domain:mep-progetti.com` | `mep-progetti.com` | **MEP SOLUTIONS S.r.l.** | Mantova / North Italy | `02603720208` | **Priority B** | Web Form Only |
| `cand:domain:origineing.it` | `origineing.it` | **ORIGINE S.r.l. (ORIGINE Ingegneria)** | Fiesso d'Artico / Padova | `05640390281` | **Priority B** | Uncontacted / Eligible |
| `cand:domain:strastudio.it` | `strastudio.it` | **STRAstudio S.r.l. unipersonale** | Milano | `10633440960` | **Priority B** | Uncontacted / Eligible |
| `cand:domain:etcstudio.it` | `etcstudio.it` | **ETC Studio S.r.l.** | Vigonza (PD) / Noale (VE) | `05625210280` | **Priority B** | **COLD OUTREACH PROHIBITED** |
| `cand:domain:planimetro.it` | `planimetro.it` | **Planimetro S.r.l.** | Milano | `08344910966` | **Priority B** | Uncontacted / Eligible |

> [!WARNING] Critical Relationship Restriction
> **`etcstudio.it` (ETC Studio S.r.l.):** Existing relationship history identified. ARILVO (pyBIM partner) previously held a direct commercial meeting with this firm. In accordance with operational safety rules, **cold outreach is strictly prohibited**. Outreach status is set to `relationship_review_pending`. Any future engagement must be routed via internal relationship review.

---

## 2. In-Depth Candidate Profiles & Work-Package Hypotheses

### 1. B&B Progetti S.r.l. (`bbprogettimilano.it`)
- **Operational Candidate ID:** `cand:domain:bbprogettimilano.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** B&B Progetti S.r.l.
- **P.IVA / CF:** `06650400960`
- **Registered & Operating Offices:** 
  - Legal & Operational HQ: Via Fumagalli 3, 20143 Milano (MI)
  - Operational Office: Corso Alcide De Gasperi 32, 10129 Torino (TO)
- **Leadership & Key Contacts:**
  - Ing. Giampiero Brioni (Founder & CEO, Lecturer in Quantity Surveying at Politecnico di Milano)
  - Ing. Daniele Olimpieri (Partner & Project Manager)
- **Public Communication Channels:**
  - Email: `info@bbprogettimilano.it`
  - Phone: `+39 02 36636170`
- **Core Activities & Technology Stack:**
  - Integrated design, technical specifications, quantity surveying (Computi Metrici Estimativi), construction site management.
  - Native BIM modeling in Autodesk Revit, Navisworks, and Solibri. Active patron of BIMeet&Greet.
- **Portfolio & Project Evidence:**
  - Large corporate developments in Milan and Turin; hospital and educational public tenders.
- **Collaboration & Hiring Signals:**
  - Explicit ongoing recruitment on their careers page for external P.IVA Architects and Engineers specializing in BIM across Milano, Torino, and Pescara.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Revit Architectural and Structural modeling for executive construction packages and automated quantity takeoff extraction (QTO / Computi) directly from BIM models.
  - **Relationship / Priority:** Priority A. High volume staffing overflow potential.

---

### 2. bim design group s.r.l. (`bdgroup.it`)
- **Operational Candidate ID:** `cand:domain:bdgroup.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** bim design group s.r.l.
- **P.IVA / CF:** `04637460272`
- **Registered & Operating Office:** Viale Ancona 10/C, 30172 Mestre, Venezia (VE)
- **Leadership & Key Contacts:**
  - Ing. Martina Cerciello (Amministratore Unico, Project Manager, Head of Tenders)
  - Ing. Federico Saccarola (Direttore Tecnico, Head of Engineering, Safety Coordinator)
- **Public Communication Channels:**
  - Email: `info@bdgroup.it` (Careers: `hr@bdgroup.it`)
  - Phone: `+39 041 5711524`
- **Core Activities & Technology Stack:**
  - Pure-play integrated BIM design: BIM Management, CDE Management, multidisciplinary BIM Coordination, executive tender design.
  - Autodesk Revit, Navisworks, BIM 360 / ACC.
- **Portfolio & Project Evidence:**
  - Public infrastructure (e.g., Nuova scuola di via Bari a Bolzano, healthcare centers in Veneto).
- **Collaboration & Hiring Signals:**
  - Explicit careers page inviting continuous collaboration ("collaborazione continuativa con P.IVA") for BIM modelers and coordinators with flexible/hybrid presence.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Specialized BIM subcontracting support for high-detail LOD 350/400 architectural and MEP models for complex public tender submissions.
  - **Relationship / Priority:** Priority A. Exceptional cultural and geographic alignment.

---

### 3. NX Engineering S.r.l. (`nxengineering.it`)
- **Operational Candidate ID:** `cand:domain:nxengineering.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** NX Engineering S.r.l. (Capitale sociale i.v. €30.000, REA MC 288594)
- **P.IVA / CF:** `02103040438`
- **Operating Offices:**
  - Operational HQ: Via Pizzardeto 5/b, 60025 Loreto (AN)
  - Bologna Office: Via del Monte 1, 40126 Bologna (BO)
- **Leadership & Key Contacts:**
  - Technical Direction & HR Recruitment Team
- **Public Communication Channels:**
  - Email: `info@nxengineering.it`
  - Phone: `+39 0733 1826295`
- **Core Activities & Technology Stack:**
  - High-complexity energy engineering, electrical substations, industrial HVAC, renewable power, fire prevention engineering.
  - Advanced Revit MEP, BIM clash detection, specialist permitting studies.
- **Portfolio & Project Evidence:**
  - National infrastructure projects, utility-scale solar and industrial grid connections.
- **Collaboration & Hiring Signals:**
  - Published job openings for Revit BIM Specialists (MEP & Antincendio) to support projects across Italy.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Revit MEP detailed spatial routing, ductwork/piping clash resolution, and fire prevention 3D layouts for industrial facilities.
  - **Relationship / Priority:** Priority A. Strong demand signal for mechanical and fire-safety BIM modeling.

---

### 4. BS Progetti S.r.l. (`bs-eng.net`)
- **Operational Candidate ID:** `cand:domain:bs-eng.net` (Current DB Status: `new`)
- **Verifiable Legal Entity:** BS Progetti S.r.l. (Founded 2012, 30+ team members)
- **P.IVA / CF:** `04095420271`
- **Operating Office:** Via Roma 130, 30030 Pianiga (VE)
- **Leadership & Key Contacts:**
  - Ing. Andrea Berro (Partner, Head of Commercial & Business Development)
  - Ing. Gianluca Sartori (Partner, Head of Technical Operations & HR)
- **Public Communication Channels:**
  - Email: `info@bs-eng.net`
  - Phone: `+39 041 5195480`
- **Core Activities & Technology Stack:**
  - Multidisciplinary integrated design: architecture, structural, MEP, and urban planning.
  - Certified BIM Management System under **UNI PdR 74:2019**, ISO 9001/14001/45001.
- **Portfolio & Project Evidence:**
  - Major public works, school buildings, commercial retail centers, and residential redevelopments throughout Northern Italy.
- **Collaboration & Hiring Signals:**
  - Maintains an explicit **"Fornitori" (Supplier)** intake portal on their contact page specifically to register external technical partners and engineering firms.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Formal qualification as an approved external BIM supplier for architectural and structural drafting overflow on municipal public tenders.
  - **Relationship / Priority:** Priority A. Established formal vendor intake path.

---

### 5. ATI Project S.r.l. (`atiproject.com`)
- **Operational Candidate ID:** `cand:domain:atiproject.com` (Current DB Status: `new`)
- **Verifiable Legal Entity:** ATI Project S.r.l.
- **P.IVA / CF:** `02255140507`
- **Registered & Operating Offices:**
  - HQ: Via Italo Bargagna 80, 56124 Pisa (PI)
  - Major Hubs: Milano (Via San Gregorio), Belgrade, Paris, Odense, Copenhagen, Geneva, Tallinn.
- **Leadership & Key Contacts:**
  - Arch. Luca Serri (CEO & Co-founder)
  - Ing. Branko Zrnic (Co-founder)
- **Public Communication Channels:**
  - Email: `info@atiproject.com` (Legal PEC: `atiproject@legalmail.it`, HR: `hr@atiproject.com`)
  - Phone: `+39 050 578460`
- **Core Activities & Technology Stack:**
  - Massive-scale integrated design, healthcare architecture, educational campuses, sustainable bioclimatic engineering.
  - Native BIM lifecycle execution (Revit, Dynamo, Navisworks).
- **Portfolio & Project Evidence:**
  - Large regional hospitals (e.g., Ospedale Stella Maris Pisa, European research centers, major hospital complexes).
- **Collaboration & Hiring Signals:**
  - Constantly hiring across 8 European offices; regularly uses specialized tier-2 and tier-3 subcontractor packages for detailed modeling during peak project phases.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Subcontracted production of detailed interior architectural packages and MEP coordination blocks for healthcare or university campus deliverables.
  - **Relationship / Priority:** Priority A. Very large project scale; requires structured vendor introduction.

---

### 6. MEP SOLUTIONS S.r.l. (`mep-progetti.com`)
- **Operational Candidate ID:** `cand:domain:mep-progetti.com` (Current DB Status: `new`)
- **Verifiable Legal Entity:** MEP SOLUTIONS S.r.l. (SDI: X2PH38J)
- **P.IVA / CF:** `02603720208`
- **Operating Region:** North Italy (Mantova / Brescia / Milano operational axis)
- **Leadership & Key Contacts:**
  - Technical Director / Responsabile Progettazione MEP
- **Public Communication Channels:**
  - Email: Official web contact portal (Direct public email not listed on landing page)
  - Phone: Web portal submission
- **Core Activities & Technology Stack:**
  - Specialized mechanical and plumbing engineering, industrial HVAC, fire protection systems, energy efficiency retrofits.
  - Autodesk Revit MEP BIM modeling, coordination, and interference checking.
- **Portfolio & Project Evidence:**
  - Industrial manufacturing facilities, logistics hubs, commercial HVAC installations.
- **Collaboration & Hiring Signals:**
  - Dedicated recruitment messaging targeting BIM Coordinators and Modelers in mechanical/piping domains.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** CAD-to-BIM conversion of mechanical plant layouts, piping schematics 3D modeling, and clash resolution against structural models.
  - **Relationship / Priority:** Priority B. Strong technical fit, though primary initial contact requires web portal outreach.

---

### 7. ORIGINE S.r.l. / ORIGINE Ingegneria (`origineing.it`)
- **Operational Candidate ID:** `cand:domain:origineing.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** ORIGINE S.r.l.
- **P.IVA / CF:** `05640390281`
- **Operating Offices:**
  - Registered Office: Via Barbariga 47/B, Fiesso d'Artico (VE)
  - Operative Office: Via Pilade Bronzetti 46/c Int. 13, 35138 Padova (PD)
- **Leadership & Key Contacts:**
  - Ing. Stefano Paludetto (Partner & Co-founder, Structural Engineer & Works Director)
- **Public Communication Channels:**
  - Email: `info@origineing.it`
  - Phone: `+39 049 9070353`
- **Core Activities & Technology Stack:**
  - Structural engineering, civil and industrial works, seismic vulnerability assessment, construction management.
  - Structural BIM modeling and reinforcement detailing.
- **Portfolio & Project Evidence:**
  - Nuovo Teatro Auditorium di Albignasego (PD), Polo dell'Infanzia Istituto Comprensivo Vigasio (VR).
- **Collaboration & Hiring Signals:**
  - Publicly highlights strategic teaming with external architectural studios (e.g., MIDE architetti, Didonè Comacchio, Contineo, Technital, Steam).
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Structural BIM model authoring, rebar / RC detailing extraction, and architectural-structural coordination for municipal public tender works.
  - **Relationship / Priority:** Priority B. Collaborative culture with confirmed history of teaming with specialized firms.

---

### 8. STRAstudio S.r.l. unipersonale (`strastudio.it`)
- **Operational Candidate ID:** `cand:domain:strastudio.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** STRAstudio S.r.l. unipersonale (Founded 2019; relocated to Milano in 2025)
- **P.IVA / CF:** `10633440960`
- **Operating Office:** Corso Garibaldi 70, 20121 Milano (MI)
- **Leadership & Key Contacts:**
  - Arch. Chiara Pelagatti (Founder, Principal Architect, B2B Manager)
- **Public Communication Channels:**
  - Email: `info@strastudio.it`
  - Phone: `+39 02 32935177`
- **Core Activities & Technology Stack:**
  - Bespoke architectural design, interior architecture, corporate office fit-outs, comprehensive BIM management.
  - BIM Specialist and Coordinator certifications; Autodesk Revit and Navisworks.
- **Portfolio & Project Evidence:**
  - High-end residential interiors, corporate office spaces in central Milan, public project bids.
- **Collaboration & Hiring Signals:**
  - Studio methodology explicitly advertises teaming with external specialists for technical systems, structural calculations, and complex modeling.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Handling executive Revit architectural detail models, joinery/millwork BIM components, and MEP collision checks for high-end retail/residential fit-outs.
  - **Relationship / Priority:** Priority B. High-end Milanese practice with lean core staff relying on external specialists.

---

### 9. ETC Studio S.r.l. (`etcstudio.it`) — *RESTRICTED*
- **Operational Candidate ID:** `cand:domain:etcstudio.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** ETC Studio S.r.l.
- **P.IVA / CF:** `05625210280`
- **Operating Offices:**
  - Vigonza HQ: Via Roma 10/c, 35010 Vigonza (PD) (Tel: `+39 049 8304420`)
  - Noale Office: Noale (VE) (Tel: `+39 041 7793600`)
- **Leadership & Key Contacts:**
  - Studio Management / Technical Direction (Existing ARILVO relationship contact)
- **Public Communication Channels:**
  - Email: `info@etcstudio.it`, `vigonza@etcstudio.it`, `noale@etcstudio.it` (PEC: `etcstudio@pec.it`)
  - Phone: `+39 049 8304420`
- **Core Activities & Technology Stack:**
  - Multidisciplinary engineering, building envelope, MEP consulting, and energy certification.
- **Strategic Relationship Status:**
  - **RESTRICTION:** ARILVO (pyBIM partner) has already conducted a formal commercial introductory meeting with ETC Studio. 
  - **COLD OUTREACH IS STRICTLY PROHIBITED.** 
  - Status is flagged as `relationship_review_pending`. Any contact must be evaluated jointly with the ARILVO relationship lead.
- **Hypothetical Technical Fit:**
  - CAD-to-BIM conversion for existing building stock and energy retrofit packages.

---

### 10. Planimetro S.r.l. (`planimetro.it`)
- **Operational Candidate ID:** `cand:domain:planimetro.it` (Current DB Status: `new`)
- **Verifiable Legal Entity:** Planimetro S.r.l. (Over 40 years of operating history)
- **P.IVA / CF:** `08344910966`
- **Operating Office:** Alzaia Naviglio Grande 38, 20144 Milano (MI)
- **Leadership & Key Contacts:**
  - Founding Partners / BIM Expert Management
- **Public Communication Channels:**
  - Email: `info@planimetro.it`
  - Phone: `+39 02 45499914` / `+39 02 45499915`
- **Core Activities & Technology Stack:**
  - Integrated architectural and structural engineering, detailed construction phase drawings, site direction and construction management.
  - Senior BIM Expert project leaders, Solibri Model Checker, Autodesk Revit.
- **Portfolio & Project Evidence:**
  - Urban renewal, residential complexes, and historic building rehabilitations in Lombardia.
- **Collaboration & Hiring Signals:**
  - Practice explicitly relies on numerically sizing multidisciplinary working groups according to contract volume, emphasizing strict deadline compliance.
- **Strategic pyBIM Hypothesis:**
  - **Proposed Work Package:** Overflow production of construction-phase As-Built Revit models, technical detail drawing extractions, and model audit/quality control.
  - **Relationship / Priority:** Priority B. Solid engineering firm valuing execution precision.

---

## 3. Operational Implementation Guidelines

1. **Safety & Database Integrity:**
   The operational SQLite database (`data/sales_outreach.db`) was accessed purely in read-only mode during this investigation. No automated inserts were executed into `lead_register_records`.
2. **Reviewable Deliverable:**
   The validated 13-field data has been exported to:
   `C:\Sales-AI\SalesAI\docs\analysis\SalesAI_Top10_Enriched_Lead_Register_2026-10-31.xlsx`
3. **Operator Next Actions:**
   - Review Priority A targets (`bbprogettimilano.it`, `bdgroup.it`, `nxengineering.it`, `bs-eng.net`, `atiproject.com`) for potential vendor onboarding.
   - For `bs-eng.net`, review their dedicated online supplier portal (*Fornitori*) to submit a formal firm-to-firm capabilities profile.
   - Confirm internal handling notes for `etcstudio.it` with ARILVO before any follow-up communication occurs.
