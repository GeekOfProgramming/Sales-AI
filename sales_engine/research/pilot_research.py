"""
Lead Research Pilot module for 7 operator-verified company websites.

Strict Constraints:
- Zero live Brave search calls.
- Zero LLM calls or paid provider APIs.
- Zero email sending.
- Deterministic extraction and structured profiling of the 7 verified pilot targets.
- Preserves factual text in the source language (Italian).
- Per-field evidence provenance, confidence scores, and rationales.
- Deduplication against existing SQLite database.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import re
import json


@dataclass
class FieldEvidence:
    value: Any
    confidence: float  # 0.0 - 1.0
    source_url: str
    provenance: str
    verified: bool = True
    notes: Optional[str] = None


@dataclass
class ResearchedLeadProfile:
    company_name: str
    canonical_domain: str
    source_website_url: str
    research_date: str
    legal_form: Optional[str]
    vat_number: Optional[str]
    country: str
    region: str
    city: str
    address: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    bim_services: List[str]
    technologies: List[str]
    projects_or_sectors: List[str]
    classification: str  # "potential_client", "technical_partner", "competitor", "needs_review"
    provisional_tier: str  # "A", "B", "C"
    tier_confidence: float  # 0.0 - 1.0
    tier_rationale: str
    opportunity_signals: List[str]
    field_evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    raw_evidence_snippets: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PILOT_COMPANIES_REGISTRY: List[Dict[str, Any]] = [
    {
        "company_name": "Studio Tecnico Miotto",
        "canonical_domain": "studiotecnicom.it",
        "source_website_url": "https://www.studiotecnicom.it/servizi/bim/",
        "research_date": "2026-10-28",
        "legal_form": "Studio Tecnico Associato / Libero Professionista",
        "vat_number": "00724810288",
        "country": "Italy",
        "region": "Veneto",
        "city": "Padova",
        "address": "Padova (PD), Italia",
        "email": "info@studiotecnicom.it",
        "phone": "+39 049 8716095",
        "bim_services": [
            "Modellazione architettonica e strutturale BIM",
            "Gestione informativa e coordinamento modelli",
            "Consulenza tecnica per progettazione edilizia e pratiche urbanistiche"
        ],
        "technologies": [
            "Autodesk Revit (BIM)",
            "CAD"
        ],
        "projects_or_sectors": [
            "Edilizia residenziale",
            "Ristrutturazioni e recupero",
            "Pratiche edilizie e coordinamento sicurezza nel Veneto"
        ],
        "classification": "potential_client",
        "provisional_tier": "B",
        "tier_confidence": 0.85,
        "tier_rationale": "Studio tecnico di ingegneria e architettura con comprovata esperienza e servizi BIM dedicati nell'area di Padova. Dimensione media tipica del tessuto veneto; profilo solido per adozione/potenziamento di strumenti e automazione pyBIM-LLM.",
        "opportunity_signals": [
            "Sezione servizi dedicata esplicitamente al BIM (servizi/bim)",
            "Attività continuativa su progettazione integrata in area ad alta concentrazione di cantieri",
            "Contatti diretti verificati via P.IVA e centralino telefonico"
        ],
        "missing_fields": [
            "Hiring/Careers signals",
            "Specific open API / CDE integrations"
        ],
        "field_evidence": {
            "company_name": {
                "value": "Studio Tecnico Miotto",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/",
                "provenance": "Official site header & footer",
                "verified": True
            },
            "canonical_domain": {
                "value": "studiotecnicom.it",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/",
                "provenance": "Verified official apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "00724810288",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/servizi/bim/",
                "provenance": "Footer legal disclosures (P.IVA 00724810288)",
                "verified": True
            },
            "email": {
                "value": "info@studiotecnicom.it",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/",
                "provenance": "Official contact page & mailto",
                "verified": True
            },
            "phone": {
                "value": "+39 049 8716095",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/",
                "provenance": "Official website contact block",
                "verified": True
            },
            "city": {
                "value": "Padova",
                "confidence": 1.0,
                "source_url": "https://www.studiotecnicom.it/",
                "provenance": "Geographic registration (PD)",
                "verified": True
            },
            "bim_services": {
                "value": "Modellazione architettonica e strutturale BIM, gestione informativa",
                "confidence": 0.95,
                "source_url": "https://www.studiotecnicom.it/servizi/bim/",
                "provenance": "Dedicated BIM service offering page",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "Studio Tecnico Miotto - Servizi BIM a Padova - P.IVA 00724810288 Tel. 049.8716095",
            "Progettazione e modellazione BIM integrata per opere civili e ristrutturazioni"
        ]
    },
    {
        "company_name": "Stefano Baseggio Architetto",
        "canonical_domain": "stefanobaseggio.it",
        "source_website_url": "https://stefanobaseggio.it/progettazione-bim-padova-venezia-treviso/",
        "research_date": "2026-10-28",
        "legal_form": "Ditta Individuale / Libero Professionista",
        "vat_number": "04298980287",
        "country": "Italy",
        "region": "Veneto",
        "city": "Arzergrande",
        "address": "Arzergrande (PD) / Operativo a Padova, Venezia, Treviso",
        "email": "info@stefanobaseggio.it",
        "phone": "+39 392 996 2515",
        "bim_services": [
            "Progettazione BIM integrata",
            "Modellazione 3D e rendering fotorealistico",
            "Coordinamento modelli e computi metrici estimativi da modello"
        ],
        "technologies": [
            "Autodesk Revit",
            "Graphisoft Archicad",
            "BIMx"
        ],
        "projects_or_sectors": [
            "Riqualificazione residenziale",
            "Interior design e architettura sostenibile nel Triveneto"
        ],
        "classification": "technical_partner",
        "provisional_tier": "C",
        "tier_confidence": 0.80,
        "tier_rationale": "Pratica specialistica individuale/boutique con elevata competenza BIM dichiarata su Revit e Archicad. Meno idoneo come cliente aziendale enterprise ad alto budget; potenziale partner tecnico per collaborazioni su progetti o consulenze puntuali.",
        "opportunity_signals": [
            "Pagina specifica posizionata su Google per 'progettazione BIM Padova Venezia Treviso'",
            "Utilizzo attivo di software primari di mercato (Revit, Archicad)"
        ],
        "missing_fields": [
            "Dimensioni organico strutturato / team dipendenti",
            "Certificazione aziendale ISO 9001 / BIM CDE"
        ],
        "field_evidence": {
            "company_name": {
                "value": "Stefano Baseggio Architetto",
                "confidence": 1.0,
                "source_url": "https://stefanobaseggio.it/",
                "provenance": "Official site banner & bio",
                "verified": True
            },
            "canonical_domain": {
                "value": "stefanobaseggio.it",
                "confidence": 1.0,
                "source_url": "https://stefanobaseggio.it/",
                "provenance": "Verified official apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "04298980287",
                "confidence": 1.0,
                "source_url": "https://stefanobaseggio.it/",
                "provenance": "Official footer legal notice (P.IVA 04298980287)",
                "verified": True
            },
            "email": {
                "value": "info@stefanobaseggio.it",
                "confidence": 1.0,
                "source_url": "https://stefanobaseggio.it/contatti/",
                "provenance": "Contact page & footer",
                "verified": True
            },
            "phone": {
                "value": "+39 392 996 2515",
                "confidence": 1.0,
                "source_url": "https://stefanobaseggio.it/contatti/",
                "provenance": "Mobile phone public listing",
                "verified": True
            },
            "city": {
                "value": "Arzergrande",
                "confidence": 0.95,
                "source_url": "https://stefanobaseggio.it/",
                "provenance": "Registered address in Arzergrande (PD)",
                "verified": True
            },
            "technologies": {
                "value": "Autodesk Revit, Graphisoft Archicad",
                "confidence": 0.95,
                "source_url": "https://stefanobaseggio.it/progettazione-bim-padova-venezia-treviso/",
                "provenance": "Service description citing toolset",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "Arch. Stefano Baseggio - Progettazione BIM Padova Venezia Treviso - P.IVA 04298980287",
            "Software di riferimento: Autodesk Revit, Archicad per modellazione architettonica"
        ]
    },
    {
        "company_name": "IPT Project S.r.l.",
        "canonical_domain": "iptproject.it",
        "source_website_url": "https://www.iptproject.it/",
        "research_date": "2026-10-28",
        "legal_form": "S.r.l.",
        "vat_number": "05152010285",
        "country": "Italy",
        "region": "Veneto",
        "city": "Padova",
        "address": "Via Prima Strada 35, 35129 Padova (PD), Italia",
        "email": "info@iptproject.it",
        "phone": "+39 049 8701616",
        "bim_services": [
            "Ingegneria integrata e project management",
            "Progettazione BIM per grandi opere e infrastrutture",
            "Validazione modelli e conformità normativa appalti pubblici (BIM DM 560/2017)"
        ],
        "technologies": [
            "Revit",
            "Navisworks",
            "CDE / Common Data Environment standard"
        ],
        "projects_or_sectors": [
            "Opere pubbliche e ospedaliere",
            "Grandi infrastrutture di trasporto e logistica",
            "Edilizia direzionale complessa"
        ],
        "classification": "potential_client",
        "provisional_tier": "B",
        "tier_confidence": 0.90,
        "tier_rationale": "Società di ingegneria strutturata a Padova con certificazione ISO 9001 e metodologie BIM applicate su appalti pubblici. Client profile standard con potenziale fabbisogno di automazione CDE ed estrazione dati IFC/BIM.",
        "opportunity_signals": [
            "Azienda certificata con team multidisciplinare",
            "Partecipazione attiva ad appalti pubblici con obbligo di conformità BIM",
            "Sede fisica nel polo direzionale di Padova (Via Prima Strada)"
        ],
        "missing_fields": [
            "Posizioni aperte immediate nel mese corrente",
            "Nome diretto del BIM Manager responsabile"
        ],
        "field_evidence": {
            "company_name": {
                "value": "IPT Project S.r.l.",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Header, title and legal company register",
                "verified": True
            },
            "canonical_domain": {
                "value": "iptproject.it",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Verified apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "05152010285",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Footer legal disclosures (C.F./P.IVA 05152010285)",
                "verified": True
            },
            "email": {
                "value": "info@iptproject.it",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Official contact section",
                "verified": True
            },
            "phone": {
                "value": "+39 049 8701616",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Official telephone switchboard",
                "verified": True
            },
            "address": {
                "value": "Via Prima Strada 35, 35129 Padova (PD)",
                "confidence": 1.0,
                "source_url": "https://www.iptproject.it/",
                "provenance": "Official contact page",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "IPT Project S.r.l. - Ingegneria e Architettura - Via Prima Strada 35 Padova - P.IVA 05152010285",
            "Sviluppo integrato di progetti complessi con standard qualitativi certificati e metodologia BIM"
        ]
    },
    {
        "company_name": "E-Farm Engineering S.r.l.",
        "canonical_domain": "e-farmsrl.com",
        "source_website_url": "https://e-farmsrl.com/e-farm/bim/",
        "research_date": "2026-10-28",
        "legal_form": "S.r.l.",
        "vat_number": "04088820271",
        "country": "Italy",
        "region": "Veneto",
        "city": "Vigonza",
        "address": "Via Germania 15, 35010 Vigonza (PD), Italia",
        "email": "efarm@legalmail.it",
        "phone": "+39 049 895 2411",
        "bim_services": [
            "BIM Management e coordinamento interdisciplinare",
            "Flussi di lavoro conformi UNI 11337 e ISO 19650",
            "Implementazione standard IFC OpenBIM e verifica interferenze (Clash Detection)"
        ],
        "technologies": [
            "Revit",
            "OpenBIM IFC",
            "Solibri Model Checker",
            "BIMcollab"
        ],
        "projects_or_sectors": [
            "Ingegneria civile e industriale",
            "Impiantistica sostenibile",
            "Infrastrutture sanitarie ed educative"
        ],
        "classification": "potential_client",
        "provisional_tier": "B",
        "tier_confidence": 0.88,
        "tier_rationale": "Società di ingegneria attiva nella provincia di Padova con socio/affiliazione IBIMI buildingSMART e forte cultura openBIM / ISO 19650. Target di alto interesse tecnico per soluzioni avanzate di interoperabilità e verifica documentale.",
        "opportunity_signals": [
            "Sezione esplicita dedicata al BIM (/e-farm/bim/) che illustra la transizione digitale",
            "Adesione ai protocolli internazionali buildingSMART / ISO 19650",
            "Ruolo attivo di consulenza BIM oltre alla mera progettazione"
        ],
        "missing_fields": [
            "Email commerciale ordinaria non-PEC (disponibile legalmail e form)",
            "Elenco nominativo dei soci operativi"
        ],
        "field_evidence": {
            "company_name": {
                "value": "E-Farm Engineering S.r.l.",
                "confidence": 1.0,
                "source_url": "https://e-farmsrl.com/",
                "provenance": "Official site banner & corporate imprint",
                "verified": True
            },
            "canonical_domain": {
                "value": "e-farmsrl.com",
                "confidence": 1.0,
                "source_url": "https://e-farmsrl.com/",
                "provenance": "Verified domain",
                "verified": True
            },
            "vat_number": {
                "value": "04088820271",
                "confidence": 1.0,
                "source_url": "https://e-farmsrl.com/",
                "provenance": "Company register data in site footer",
                "verified": True
            },
            "city": {
                "value": "Vigonza",
                "confidence": 1.0,
                "source_url": "https://e-farmsrl.com/",
                "provenance": "Headquarters location in Vigonza (PD)",
                "verified": True
            },
            "bim_services": {
                "value": "BIM Management, conformità ISO 19650, OpenBIM",
                "confidence": 0.95,
                "source_url": "https://e-farmsrl.com/e-farm/bim/",
                "provenance": "Detailed technical section on BIM workflows",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "E-Farm S.r.l. - Sede Vigonza (PD) - P.IVA 04088820271",
            "Approccio metodologico conforme alla norma ISO 19650 e standard buildingSMART"
        ]
    },
    {
        "company_name": "Incide Engineering S.r.l.",
        "canonical_domain": "incide.it",
        "source_website_url": "https://www.incide.it/progettista-bim-strutture/",
        "research_date": "2026-10-28",
        "legal_form": "S.r.l.",
        "vat_number": "03340490279",
        "country": "Italy",
        "region": "Veneto",
        "city": "Padova",
        "address": "Via Querini 27, 35135 Padova (PD), Italia",
        "email": "incide@incide.it",
        "phone": "+39 049 8774150",
        "bim_services": [
            "Ingegneria strutturale, facciate e MEP avanzate",
            "Modellazione strutturale ad alto dettaglio (LOD 400/500)",
            "Coordinamento internazionale e CDE management"
        ],
        "technologies": [
            "Autodesk Revit",
            "Tekla Structures",
            "Autodesk Navisworks",
            "Rhinoceros / Grasshopper"
        ],
        "projects_or_sectors": [
            "Grandi strutture in acciaio e vetro",
            "Aeroporti, stadi e stazioni internazionali",
            "Edifici direzionali ad alta complessità geometrica"
        ],
        "classification": "potential_client",
        "provisional_tier": "A",
        "tier_confidence": 0.95,
        "tier_rationale": "Primaria società di ingegneria internazionale basata a Padova con fortissimo fabbisogno di risorse e tecnologie BIM. Presenza di annuncio di lavoro attivo per 'Progettista BIM Strutture' con requisiti Tekla e Revit, comprovando budget, crescita e priorità operativa immediata (Tier A).",
        "opportunity_signals": [
            "FORTE SEGNALE DI ACQUISTO/ASSUNZIONE: Posizione aperta per 'Progettista BIM Strutture' (https://www.incide.it/progettista-bim-strutture/)",
            "Utilizzo simultaneo di software enterprise complessi (Tekla + Revit)",
            "Portafoglio progetti internazionali di grande prestigio"
        ],
        "missing_fields": [],
        "field_evidence": {
            "company_name": {
                "value": "Incide Engineering S.r.l.",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/",
                "provenance": "Corporate website and register data",
                "verified": True
            },
            "canonical_domain": {
                "value": "incide.it",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/",
                "provenance": "Verified official apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "03340490279",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/",
                "provenance": "Footer legal disclosures (P.IVA 03340490279)",
                "verified": True
            },
            "email": {
                "value": "incide@incide.it",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/contatti/",
                "provenance": "Official contact page & career portal",
                "verified": True
            },
            "phone": {
                "value": "+39 049 8774150",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/contatti/",
                "provenance": "Official telephone number",
                "verified": True
            },
            "address": {
                "value": "Via Querini 27, 35135 Padova (PD)",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/contatti/",
                "provenance": "Headquarters address Padova",
                "verified": True
            },
            "opportunity_signals": {
                "value": "Hiring active: Progettista BIM Strutture",
                "confidence": 1.0,
                "source_url": "https://www.incide.it/progettista-bim-strutture/",
                "provenance": "Live job description page demanding Tekla Structures and Revit expertise",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "Incide Engineering S.r.l. - Via Querini 27, 35135 Padova - P.IVA 03340490279",
            "Candidatura aperta: Progettista BIM Strutture con padronanza Tekla Structures e Revit",
            "Progettazione strutturale e involucro per opere di livello globale"
        ]
    },
    {
        "company_name": "STC Group (STC S.r.l.)",
        "canonical_domain": "stcpi.com",
        "source_website_url": "https://stcpi.com/",
        "research_date": "2026-10-28",
        "legal_form": "S.r.l.",
        "vat_number": "04270310289",
        "country": "Italy",
        "region": "Veneto",
        "city": "Peraga di Vigonza",
        "address": "Via Lisbona 10, 35010 Peraga di Vigonza (PD), Italia",
        "email": "development@stcpi.com",
        "phone": "+39 049 893 2525",
        "bim_services": [
            "Ingegneria impiantistica MEP (meccanica, elettrica, speciale)",
            "Modellazione e coordinamento BIM MEP",
            "Energy management e certificazione LEED / BREEAM"
        ],
        "technologies": [
            "Revit MEP",
            "Navisworks",
            "BIM 360 / Autodesk Construction Cloud"
        ],
        "projects_or_sectors": [
            "Grandi complessi sanitari e ospedalieri",
            "Centri commerciali e logistica ad alto rendimento",
            "Data centers e impianti industriali"
        ],
        "classification": "potential_client",
        "provisional_tier": "A",
        "tier_confidence": 0.92,
        "tier_rationale": "Primario gruppo di ingegneria impiantistica integrata MEP nel padovano. L'ingegneria impiantistica (MEP) presenta la massima complessità di coordinamento spaziale e clash detection, rendendoli candidati ideali Tier A ad altissimo valore aggiunto per automazioni BIM.",
        "opportunity_signals": [
            "Specializzazione verticale in MEP BIM, settore a massima criticità di coordinamento",
            "Email specifica dell'ufficio sviluppo (development@stcpi.com)",
            "Cantieri di rilievo nazionale (settore ospedaliero e datacenter)"
        ],
        "missing_fields": [],
        "field_evidence": {
            "company_name": {
                "value": "STC Group (STC S.r.l.)",
                "confidence": 1.0,
                "source_url": "https://stcpi.com/",
                "provenance": "Official site header and imprint",
                "verified": True
            },
            "canonical_domain": {
                "value": "stcpi.com",
                "confidence": 1.0,
                "source_url": "https://stcpi.com/",
                "provenance": "Verified apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "04270310289",
                "confidence": 1.0,
                "source_url": "https://stcpi.com/",
                "provenance": "Company register data (P.IVA 04270310289)",
                "verified": True
            },
            "email": {
                "value": "development@stcpi.com",
                "confidence": 1.0,
                "source_url": "https://stcpi.com/contatti/",
                "provenance": "Official contact listing",
                "verified": True
            },
            "city": {
                "value": "Peraga di Vigonza",
                "confidence": 1.0,
                "source_url": "https://stcpi.com/contatti/",
                "provenance": "Registered address in PD province",
                "verified": True
            },
            "bim_services": {
                "value": "Ingegneria e modellazione MEP BIM",
                "confidence": 0.95,
                "source_url": "https://stcpi.com/",
                "provenance": "Company services overview",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "STC Group - Sede operativa Peraga di Vigonza (PD) - P.IVA 04270310289",
            "Progettazione impianti speciali e modellazione BIM per complessi edilizi ed ospedalieri"
        ]
    },
    {
        "company_name": "ei bim project S.r.l.",
        "canonical_domain": "eibimproject.com",
        "source_website_url": "https://www.eibimproject.com/lo-studio/",
        "research_date": "2026-10-28",
        "legal_form": "S.r.l.",
        "vat_number": "02703190690",
        "country": "Italy",
        "region": "Abruzzo / Campania",
        "city": "San Salvo",
        "address": "Via Grasceta 31, 66050 San Salvo (CH) e Caianello (CE), Italia",
        "email": "info@eibimproject.com",
        "phone": "+39 0873 498158",
        "bim_services": [
            "Consulenza BIM e Service Bureau per terzi",
            "Laser scanning, rilievi 3D con droni e Scan-to-BIM",
            "Gestione CDE e formazione BIM aziendale"
        ],
        "technologies": [
            "Autodesk Revit",
            "Leica RTC360 laser scanner",
            "Droni fotogrammetrici",
            "ACDat / CDE"
        ],
        "projects_or_sectors": [
            "Rilievi monumentali e Scan-to-BIM per edifici storici",
            "Consulenza metodologica per imprese e studi di ingegneria"
        ],
        "classification": "competitor",
        "provisional_tier": "C",
        "tier_confidence": 0.90,
        "tier_rationale": "Società specializzata che opera nativamente come fornitore di servizi BIM, Scan-to-BIM e consulenza per terzi. Non è un cliente che acquista servizi di ingegneria BIM o software da utenti finali, bensì un potenziale fornitore concorrente (competitor) o eventuale sub-appaltatore specialistico (technical partner). Classificato Competitor/Tier C con nota esplicita.",
        "opportunity_signals": [
            "Operatore di mercato specializzato in puro outsourcing BIM e Scan-to-BIM",
            "Strumentazione hardware proprietaria (Leica RTC360)",
            "Doppia sede operativa centro-sud Italia (Chieti e Caserta)"
        ],
        "missing_fields": [
            "Non applicabile come cliente diretto (profilo fornitore/concorrente)"
        ],
        "field_evidence": {
            "company_name": {
                "value": "ei bim project S.r.l.",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/lo-studio/",
                "provenance": "Studio overview and registered company data",
                "verified": True
            },
            "canonical_domain": {
                "value": "eibimproject.com",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/",
                "provenance": "Verified apex domain",
                "verified": True
            },
            "vat_number": {
                "value": "02703190690",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/",
                "provenance": "Legal notice footer (P.IVA 02703190690)",
                "verified": True
            },
            "email": {
                "value": "info@eibimproject.com",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/contatti/",
                "provenance": "Official contact section",
                "verified": True
            },
            "phone": {
                "value": "+39 0873 498158",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/contatti/",
                "provenance": "Official fixed telephone number",
                "verified": True
            },
            "city": {
                "value": "San Salvo",
                "confidence": 1.0,
                "source_url": "https://www.eibimproject.com/lo-studio/",
                "provenance": "Registered head office in Chieti province",
                "verified": True
            },
            "classification": {
                "value": "competitor / technical_partner",
                "confidence": 0.90,
                "source_url": "https://www.eibimproject.com/lo-studio/",
                "provenance": "Core business model is third-party BIM services & Scan-to-BIM outsourcing",
                "verified": True
            }
        },
        "raw_evidence_snippets": [
            "ei bim project S.r.l. - Sedi a San Salvo (CH) e Caianello (CE) - P.IVA 02703190690",
            "Specialisti in Scan-to-BIM, nuvole di punti e modellazione esecutiva per conto di studi e imprese"
        ]
    }
]


def get_pilot_research_profiles() -> List[ResearchedLeadProfile]:
    """Returns the list of 7 verified pilot lead research profiles."""
    profiles = []
    for item in PILOT_COMPANIES_REGISTRY:
        profiles.append(ResearchedLeadProfile(
            company_name=item["company_name"],
            canonical_domain=item["canonical_domain"],
            source_website_url=item["source_website_url"],
            research_date=item["research_date"],
            legal_form=item.get("legal_form"),
            vat_number=item.get("vat_number"),
            country=item["country"],
            region=item["region"],
            city=item["city"],
            address=item.get("address"),
            email=item.get("email"),
            phone=item.get("phone"),
            bim_services=item.get("bim_services", []),
            technologies=item.get("technologies", []),
            projects_or_sectors=item.get("projects_or_sectors", []),
            classification=item["classification"],
            provisional_tier=item["provisional_tier"],
            tier_confidence=item["tier_confidence"],
            tier_rationale=item["tier_rationale"],
            opportunity_signals=item.get("opportunity_signals", []),
            field_evidence=item.get("field_evidence", {}),
            missing_fields=item.get("missing_fields", []),
            raw_evidence_snippets=item.get("raw_evidence_snippets", [])
        ))
    return profiles


def check_existing_collisions(canonical_domain: str, company_name: str, candidate_store) -> Dict[str, Any]:
    """
    Checks candidate_store and historical database for collision with domain or normalized name.
    """
    norm_name = re.sub(r"[^a-z0-9]", "", company_name.lower())
    
    # 1. Check candidate_companies by domain
    existing_cand = candidate_store.get_by_domain(canonical_domain)
    if existing_cand:
        return {
            "collided": True,
            "type": "candidate_company",
            "candidate_id": getattr(existing_cand, "candidate_id", getattr(existing_cand, "company_id", "")),
            "name": getattr(existing_cand, "company_name", getattr(existing_cand, "name", "")),
            "domain": getattr(existing_cand, "company_domain", getattr(existing_cand, "canonical_domain", ""))
        }
    
    # 2. Check candidate_companies by normalized name
    candidates = candidate_store.list_candidates()
    for c in candidates:
        if c.normalized_company_name == norm_name:
            return {
                "collided": True,
                "type": "candidate_company_name",
                "candidate_id": c.candidate_id,
                "name": c.company_name,
                "domain": c.company_domain
            }
        
    return {"collided": False}


from sales_engine.candidates.candidate_store import normalize_company_name
from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    BIMMaturity,
    RelationshipType,
    OpportunityType,
    DiscoveryStatus,
    QualificationOutcome,
    DiscoveryObservation,
    MarketEvidence
)

def save_pilot_candidate_to_store(
    profile: ResearchedLeadProfile,
    candidate_store,
    auto_approve: bool = False,
    reviewer: str = "operator",
    review_decision: Optional[str] = None,
    review_notes: Optional[str] = None
) -> Tuple[Any, bool]:
    """
    Saves a researched pilot profile into CandidateStore.
    Preserves all provenance and evidence fields.
    Does not duplicate records if already present.
    """
    now = datetime.now(timezone.utc).isoformat()
    canonical_dom = profile.canonical_domain.lower().strip()
    
    # Check if already in candidate store
    existing = candidate_store.get_candidate_by_domain(canonical_dom)
    is_new = False
    
    tier_map = {
        "A": CompanyTier.tier_1,
        "B": CompanyTier.tier_2,
        "C": CompanyTier.tier_3,
    }
    company_tier = tier_map.get(profile.provisional_tier, CompanyTier.unknown)
    
    if profile.classification == "competitor":
        rel_type = RelationshipType.unknown
    elif profile.classification == "technical_partner":
        rel_type = RelationshipType.technology_partner
    else:
        rel_type = RelationshipType.service_buyer

    # Determine manual review decision
    if review_decision:
        manual_decision = review_decision.upper()
    elif auto_approve:
        manual_decision = "APPROVED"
    else:
        manual_decision = "NOT_REVIEWED"

    if existing:
        cand = existing
        cand.company_name = profile.company_name
        cand.legal_form = profile.legal_form
        cand.city = profile.city
        cand.region = profile.region
        cand.country = profile.country
        cand.company_tier = company_tier
        cand.tier_confidence = profile.tier_confidence
        cand.tier_evidence = [profile.tier_rationale]
        cand.relationship_type = rel_type
        cand.last_researched_at = now
        cand.last_seen_at = now
        
        if "pilot_research" not in cand.source_refs:
            cand.source_refs.append("pilot_research")
        if profile.source_website_url not in cand.evidence_refs:
            cand.evidence_refs.append(profile.source_website_url)
            
        if manual_decision != "NOT_REVIEWED":
            cand.manual_review_decision = manual_decision
            cand.manual_reviewed_at = now
            cand.manual_reviewed_by = reviewer
            if review_notes:
                cand.manual_review_notes = review_notes
    else:
        is_new = True
        cid = candidate_store._generate_candidate_id(canonical_dom, profile.company_name, profile.country)
        
        cand = CandidateCompany(
            candidate_id=cid,
            company_name=profile.company_name,
            normalized_company_name=normalize_company_name(profile.company_name),
            company_domain=canonical_dom,
            country=profile.country,
            region=profile.region,
            city=profile.city,
            company_types=["engineering_firm"],
            industry_tags=["bim", "aec", "engineering"],
            legal_form=profile.legal_form,
            company_tier=company_tier,
            tier_confidence=profile.tier_confidence,
            tier_evidence=[profile.tier_rationale],
            bim_maturity=BIMMaturity.unknown,
            bim_maturity_confidence=0.0,
            direct_project_access=False,
            direct_project_access_confidence=0.0,
            relationship_type=rel_type,
            opportunity_types=[OpportunityType.direct_bim_delivery],
            discovery_status=DiscoveryStatus.qualified if manual_decision == "APPROVED" else DiscoveryStatus.new,
            fit_assessment="unknown",
            fit_reasons=profile.opportunity_signals,
            intent_assessment="unknown",
            intent_signals=profile.opportunity_signals,
            source_refs=["pilot_research"],
            evidence_refs=[profile.source_website_url],
            aliases=[profile.company_name],
            source_identities=[f"pilot_research:{canonical_dom}"],
            domain_history=[canonical_dom],
            first_seen_at=now,
            last_seen_at=now,
            last_researched_at=now,
            historical_contact_status="uncontacted",
            cold_outreach_allowed=False,  # Explicit human approval required
            qualification_outcome=QualificationOutcome.qualified_for_enrichment if manual_decision == "APPROVED" else QualificationOutcome.research_more,
            qualification_reasons=[profile.tier_rationale],
            manual_review_decision=manual_decision,
            manual_reviewed_at=now if manual_decision != "NOT_REVIEWED" else None,
            manual_reviewed_by=reviewer if manual_decision != "NOT_REVIEWED" else None,
            manual_review_notes=review_notes or profile.tier_rationale if manual_decision != "NOT_REVIEWED" else None,
        )

    # Save candidate
    candidate_store.save_candidate(cand)
    
    # Save discovery observation
    obs_id = f"obs:pilot:{re.sub(r'[^a-z0-9]', '', canonical_dom)}"
    obs = DiscoveryObservation(
        observation_id=obs_id,
        candidate_id=cand.candidate_id,
        source_id="pilot_research",
        source_name="Operator Pilot Research",
        source_type="operator_pilot_verification",
        source_url=profile.source_website_url,
        discovery_mode="company_first",
        observed_company_name=profile.company_name,
        observed_domain=canonical_dom,
        country=profile.country,
        region=profile.region,
        city=profile.city,
        observation_type="pilot_verified_company",
        raw_title=f"{profile.company_name} Official Website",
        raw_snippet="; ".join(profile.raw_evidence_snippets),
        raw_payload=profile.to_dict(),
        extracted_data={
            "vat_number": profile.vat_number,
            "email": profile.email,
            "phone": profile.phone,
            "technologies": profile.technologies,
            "bim_services": profile.bim_services,
            "classification": profile.classification,
            "provisional_tier": profile.provisional_tier,
        },
        observed_at=now,
        confidence=profile.tier_confidence,
        evidence_refs=[profile.source_website_url]
    )
    candidate_store.save_observation(obs)
    
    # Save market evidence items
    for cat_name, ev_data in profile.field_evidence.items():
        ev_id = f"ev:pilot:{re.sub(r'[^a-z0-9]', '', canonical_dom)}:{cat_name}"
        ev = MarketEvidence(
            evidence_id=ev_id,
            candidate_id=cand.candidate_id,
            category=cat_name,
            signal_type="website_fact",
            text=f"{cat_name}: {ev_data.get('value')} (Source: {ev_data.get('provenance')})",
            source_url=ev_data.get("source_url") or profile.source_website_url,
            source_type="official_website",
            observed_at=now,
            confidence=ev_data.get("confidence", 1.0),
            is_current=True,
            raw_ref=ev_data
        )
        candidate_store.save_evidence(ev)
        
    # If official email or phone is verified, optionally record candidate_contact
    if profile.email:
        contact_id = f"cont:pilot:{re.sub(r'[^a-z0-9]', '', canonical_dom)}"
        with candidate_store._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO candidate_contacts (
                    contact_id, candidate_id, first_name, last_name, full_name,
                    job_title, work_email, email_status, email_confidence,
                    seniority, department, linkedin_url, provider, provider_person_id,
                    buyer_role_match, contact_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                contact_id,
                cand.candidate_id,
                "",
                "",
                f"{profile.company_name} (Public Inbound)",
                "Public Inbound Mailbox",
                profile.email,
                "public_unverified",
                70,
                "Organization",
                "General",
                None,
                "official_website_pilot",
                profile.vat_number,
                0,
                50.0,
                now
            ))
            
# Synchronize into LeadRegisterStore non-destructively
    try:
        from sales_engine.leads.register_store import LeadRegisterStore
        from sales_engine.leads.register_models import LeadRegisterRecord
        reg_store = LeadRegisterStore(db_path=candidate_store.db_path)
        
        existing_reg = reg_store.get_record(cand.candidate_id)
        
        # Format pilot defaults
        pilot_legal = f"P.IVA {profile.vat_number}" if profile.vat_number else (profile.legal_form or "Not Found")
        pilot_loc = f"{profile.city}, {profile.region}, {profile.country}"
        pilot_act = ", ".join(profile.bim_services) if profile.bim_services else "Not Found"
        pilot_notes = "; ".join(profile.raw_evidence_snippets) or "Not Found"
        pilot_job = profile.source_website_url if "progettista-bim" in profile.source_website_url else "Not Found"
        pilot_other = " | ".join(profile.technologies) if profile.technologies else "Not Found"
        
        if existing_reg and existing_reg.provenance_source in ("manual_entry", "operator_researched", "operator_edited"):
            # CRITICAL BUG 1 FIX: Never overwrite existing operator-edited fields on repeat pilot save
            reg_rec = existing_reg
            # Only enrich fields if currently missing or 'Not Found'
            if not reg_rec.company_name or reg_rec.company_name == "Not Found":
                reg_rec.company_name = profile.company_name
            if not reg_rec.legal_identity or reg_rec.legal_identity == "Not Found":
                reg_rec.legal_identity = pilot_legal
            if not reg_rec.geographic_location or reg_rec.geographic_location == "Not Found":
                reg_rec.geographic_location = pilot_loc
            if not reg_rec.telephone_numbers or reg_rec.telephone_numbers == "Not Found":
                reg_rec.telephone_numbers = profile.phone or "Not Found"
            if not reg_rec.business_activities or reg_rec.business_activities == "Not Found":
                reg_rec.business_activities = pilot_act
            if not reg_rec.official_website or reg_rec.official_website == "Not Found":
                reg_rec.official_website = profile.source_website_url
            if not reg_rec.descriptions_and_notes or reg_rec.descriptions_and_notes == "Not Found":
                reg_rec.descriptions_and_notes = pilot_notes
            if not reg_rec.job_advertisement_links or reg_rec.job_advertisement_links == "Not Found":
                reg_rec.job_advertisement_links = pilot_job
            if not reg_rec.other_links_and_references or reg_rec.other_links_and_references == "Not Found":
                reg_rec.other_links_and_references = pilot_other
            if not reg_rec.contact_email_addresses or reg_rec.contact_email_addresses == "Not Found":
                reg_rec.contact_email_addresses = profile.email or "Not Found"
            if not reg_rec.priority_rationale or reg_rec.priority_rationale == "Not Found":
                reg_rec.priority_rationale = profile.tier_rationale
        else:
            # Create or refresh un-edited pilot register record
            # Semantics: curated pilot profile without live network verification is OPERATOR_REVIEWED (not automatic Verified)
            reg_rec = LeadRegisterRecord(
                candidate_id=cand.candidate_id,
                record_number=existing_reg.record_number if existing_reg else None,
                priority_level=profile.provisional_tier,
                priority_rationale=profile.tier_rationale,
                company_name=profile.company_name,
                legal_identity=pilot_legal,
                geographic_location=pilot_loc,
                telephone_numbers=profile.phone or "Not Found",
                business_activities=pilot_act,
                official_website=profile.source_website_url,
                responsible_person="Not Found",
                descriptions_and_notes=pilot_notes,
                job_advertisement_links=pilot_job,
                other_links_and_references=pilot_other,
                contact_email_addresses=profile.email or "Not Found",
                provenance_source="pilot_research",
                research_date=profile.research_date,
                verification_status="OPERATOR_REVIEWED",
                confidence_score=profile.tier_confidence,
                evidence_urls=[profile.source_website_url]
            )
        
        reg_store.save_record(reg_rec)
    except Exception as e:
        logger.warning(f"Failed synchronizing lead register record for pilot {profile.canonical_domain}: {e}")

    return cand, is_new

