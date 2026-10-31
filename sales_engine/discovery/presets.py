# sales_engine/discovery/presets.py
"""
SalesAI Search Presets and Quality Configuration.

Provides:
- Business Category query presets optimized for Italian regional engineering markets (Padova, Veneto, Northern Italy).
- Regional parameter mappings (country code, search language, multilingual country notes).
- Configurable negative domain and term exclusions for direct company discovery.
- Result triage classification: SEARCH_RESULT, POTENTIAL_COMPANY, VERIFIED_OFFICIAL_COMPANY, NEEDS_REVIEW, REJECTED_IRRELEVANT.
"""

from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse
import re


GENERIC_PAGE_PHRASES = [
    "home", "home page", "chi siamo", "servizi", "services", "service", "progetti", "projects", "contatti",
    "lavora con noi", "careers", "career", "about us", "contact", "bim",
    "progettazione bim", "progettazione bim integrata", "modellazione bim",
    "building information modeling", "consulenza bim", "bim specialist",
    "bim coordinator", "bim manager", "impianti", "progettazione impiantistica",
    "progettazione", "engineering", "architettura"
]


def extract_provisional_company_name(
    title: str,
    domain: Optional[str] = None,
    snippet: Optional[str] = None,
) -> str:
    """
    Extract a sensible, provisional company name from a web search result title, domain, and snippet.
    Separates the raw page title from the provisional legal/trade name.
    Guarantees:
    - Never asserts single letters (e.g. 'e' from 'Impianti - e-MEP S.R.L.') as company names.
    - Never asserts generic job titles (e.g. 'BIM SPECIALIST' from 'Lavora con noi - BIM SPECIALIST') as company names.
    - Does not split inside hyphenated brand names (e.g. 'e-MEP S.R.L.').
    - Prefers source-supported corporate names when reliable identity evidence exists.
    - Otherwise defaults to clean domain-based provisional label.
    """
    if not title:
        return domain or "Discovered Entity"

    # Use delimiters like pipe, en-dash, em-dash, colon, or spaced hyphens ' - ',
    # but strictly DO NOT split single-word hyphens like 'e-mep' or 'bs-eng'!
    raw_parts = [p.strip() for p in re.split(r'[|–—:]|\s+-\s+', title) if p.strip()]

    clean_domain = (domain or "").lower()
    for ext in [".it", ".com", ".net", ".org", ".studio", ".eu"]:
        clean_domain = clean_domain.replace(ext, "")
    clean_domain = clean_domain.replace("www.", "")

    # Filter out empty or generic parts and single characters
    clean_parts = []
    for p in raw_parts:
        p_clean = p.strip()
        if len(p_clean) <= 1:
            continue
        if p_clean.lower() in GENERIC_PAGE_PHRASES:
            continue
        clean_parts.append(p_clean)

    # 1. Check if snippet contains an explicit corporate declaration:
    # e.g. "BIM BS progetti, società di progettazione..." or "e-MEP S.r.l., studio di..."
    if snippet:
        snippet_m = re.search(r'([A-Za-z0-9\s\.\-]{3,35}),\s*(?:societ[aà]|studio|impresa)', snippet, re.IGNORECASE)
        if snippet_m:
            cand_name = snippet_m.group(1).strip()
            # Clean up leading tags like <strong>
            cand_name = re.sub(r'<[^>]+>', '', cand_name).strip()
            if len(cand_name) >= 3 and cand_name.lower() not in GENERIC_PAGE_PHRASES:
                return cand_name

    # 2. Check if any clean title part matches or contains the core domain name
    if clean_domain and len(clean_domain) >= 2:
        clean_d_chars = re.sub(r'[^a-z0-9]', '', clean_domain)
        for p in clean_parts:
            p_chars = re.sub(r'[^a-z0-9]', '', p.lower())
            if clean_d_chars and (clean_d_chars in p_chars or p_chars in clean_d_chars):
                return p

    # 3. Check for explicit corporate, engineering or studio indicators in title parts
    corporate_clues = [
        "s.r.l.", "srl", "s.p.a.", "spa", "studio", "ingegneria",
        "architettura", "engineering", "associati", "group", "progetti"
    ]
    for p in clean_parts:
        p_lower = p.lower()
        if any(c in p_lower for c in corporate_clues):
            return p

    # 4. Take first non-generic title part with at least 3 characters
    for p in clean_parts:
        if len(p) >= 3:
            return p

    # 5. Fallback to clean domain-based provisional label
    if domain:
        base_dom = domain.split(".")[0].replace("www.", "")
        if base_dom and len(base_dom) > 1:
            # Capitalize hyphenated parts (e.g. bs-eng -> Bs-Eng, e-mep -> E-Mep)
            return "-".join(part.capitalize() for part in base_dom.split("-"))

    return "Discovered Entity"



# Regional parameter mappings
# country -> (Brave country code, Brave search_lang, notes)
REGIONAL_MARKET_SETTINGS: Dict[str, Dict[str, str]] = {
    "Italy": {
        "country": "IT",
        "search_lang": "it",
        "notes": "Italian primary language",
    },
    "Germany": {
        "country": "DE",
        "search_lang": "de",
        "notes": "German primary language",
    },
    "Austria": {
        "country": "AT",
        "search_lang": "de",
        "notes": "Austrian market, German language",
    },
    "Switzerland": {
        "country": "CH",
        "search_lang": "de",  # Multilingual note: de default, can also be fr/it
        "notes": "Multilingual (German default; French/Italian also used by region)",
    },
    "France": {
        "country": "FR",
        "search_lang": "fr",
        "notes": "French primary language",
    },
    "United Kingdom": {
        "country": "GB",
        "search_lang": "en",
        "notes": "English primary language",
    },
    "Spain": {
        "country": "ES",
        "search_lang": "es",
        "notes": "Spanish primary language",
    },
}

DEFAULT_REGIONAL_SETTING = {
    "country": "IT",
    "search_lang": "it",
    "notes": "Default Italian market",
}


# Category presets optimized for Italy / Veneto / Padova
# Maps UI category to keyword phrases that reflect real Italian commercial engineering practice
CATEGORY_PRESETS: Dict[str, Dict[str, Any]] = {
    "BIM Engineering": {
        "core_keywords": ["progettazione BIM", "societa di ingegneria"],
        "default_query": "progettazione BIM ingegneria",
        "description": "BIM engineering consultancies and integrated technical design firms",
    },
    "Architectural Practice": {
        "core_keywords": ["studio di architettura", "progettazione architettonica BIM"],
        "default_query": "studio di architettura progettazione BIM",
        "description": "Architectural practices and building design studios using BIM",
    },
    "MEP Design": {
        "core_keywords": ["progettazione impianti", "studio termotecnico", "Revit MEP"],
        "default_query": "progettazione impianti termotecnici MEP BIM",
        "description": "Mechanical, electrical, plumbing and HVAC engineering consultants",
    },
    "General Contractor": {
        "core_keywords": ["impresa generale di costruzioni", "costruzioni edili BIM"],
        "default_query": "impresa generale di costruzioni BIM",
        "description": "General contractors, construction firms and design-build managers",
    },
    "Structural Engineering": {
        "core_keywords": ["progettazione strutturale", "ingegneria strutture BIM"],
        "default_query": "progettazione strutturale ingegneria BIM",
        "description": "Structural engineering consultancies and seismic/concrete/steel designers",
    },
}

# Standard exclusion domains for DIRECT company discovery
# (These aggregators crowd out direct engineering studio websites)
STANDARD_EXCLUSION_DOMAINS: List[str] = [
    "linkedin.com",
    "indeed.com",
    "jooble.org",
    "infojobs.it",
    "glassdoor.it",
    "glassdoor.com",
    "bakeca.it",
    "subito.it",
    "kijiji.it",
    "monster.it",
    "trovolavoro.it",
]

# Query-level negative terms to eliminate non-company results
# (Courses, master programs, general news, portals)
STANDARD_NEGATIVE_TERMS: List[str] = [
    "-corso",
    "-master",
    "-laurea",
    "-universita",
    "-formazione",
]


def get_regional_settings(country_name: Optional[str]) -> Dict[str, str]:
    """Retrieve Brave country code and language for a given country name."""
    if not country_name:
        return DEFAULT_REGIONAL_SETTING
    return REGIONAL_MARKET_SETTINGS.get(country_name.strip(), DEFAULT_REGIONAL_SETTING)


def compose_search_query(
    raw_query: str,
    city: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    discovery_mode: str = "company_first",
    include_exclusions: bool = True,
) -> Tuple[str, List[str]]:
    """
    Construct an optimized search query using category presets and regional awareness.
    
    Returns:
        (final_query_string, exclusion_terms_applied)
    """
    parts: List[str] = []
    applied_exclusions: List[str] = []

    clean_query = (raw_query or "").strip()
    clean_city = (city or "").strip()
    clean_country = (country or "").strip()
    clean_category = (category or "").strip()

    # 1. Base query or category default
    if clean_query:
        parts.append(clean_query)
    elif clean_category and clean_category in CATEGORY_PRESETS:
        parts.append(CATEGORY_PRESETS[clean_category]["default_query"])
    else:
        parts.append("progettazione BIM ingegneria")

    # 2. Inject category-specific refinement if category is selected and not already in query
    if clean_category and clean_category in CATEGORY_PRESETS:
        preset = CATEGORY_PRESETS[clean_category]
        # Check if any core keyword is already partially represented
        query_lower = " ".join(parts).lower()
        keywords_to_add = []
        for kw in preset["core_keywords"]:
            # If significant word from keyword is not in query, we can consider it
            kw_words = [w.lower() for w in kw.split() if len(w) > 3]
            if not any(w in query_lower for w in kw_words):
                keywords_to_add.append(kw)
                break  # Add at most one defining category phrase to keep query concise
        if keywords_to_add:
            parts.append(keywords_to_add[0])

    # 3. Geo location (city and country)
    current_text = " ".join(parts).lower()
    if clean_city and clean_city.lower() not in current_text:
        parts.append(clean_city)
    if clean_country and clean_country.lower() not in current_text:
        # For Italy, if an Italian city like Padova/Milano/Verona is present, avoid adding "Italy"
        # in english to prevent English-language tourist/portal results
        if clean_country.lower() == "italy" and ("italia" in current_text or clean_city):
            pass
        else:
            parts.append(clean_country)

    # 4. Exclusions (applied only in company_first / direct company mode)
    if include_exclusions and discovery_mode != "job_first":
        # Add top job board domain exclusions
        for dom in ["linkedin.com", "indeed.com"]:
            parts.append(f"-site:{dom}")
            applied_exclusions.append(f"-site:{dom}")
        # Add training / academic negative terms
        for neg in ["-corso", "-master"]:
            parts.append(neg)
            applied_exclusions.append(neg)

    final_query = " ".join(parts)
    return final_query, applied_exclusions


class ResultTriageStatus:
    SEARCH_RESULT = "SEARCH_RESULT"
    POTENTIAL_COMPANY = "POTENTIAL_COMPANY"
    VERIFIED_OFFICIAL_COMPANY = "VERIFIED_OFFICIAL_COMPANY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED_IRRELEVANT = "REJECTED_IRRELEVANT"


def triage_search_result(
    title: str,
    snippet: str,
    url: str,
    discovery_mode: str = "company_first",
) -> Tuple[str, List[str]]:
    """
    Triage a raw search result into an explicit status with reasons.
    
    Status levels:
    - REJECTED_IRRELEVANT: Training course, university, generic article, pure aggregator without company
    - SEARCH_RESULT: Found in search output, generic
    - POTENTIAL_COMPANY: Direct website of a plausible company
    - NEEDS_REVIEW: Ambiguous result, e.g. job posting that may contain an employer
    - VERIFIED_OFFICIAL_COMPANY: Reserved for verified corporate domains
    """
    reasons: List[str] = []
    title_lower = (title or "").lower()
    snippet_lower = (snippet or "").lower()
    full_text = f"{title_lower} {snippet_lower}"

    parsed = urlparse(url or "")
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    path = parsed.path.lower()

    # Distinguish company indicators in domain, title, and full_text
    company_domain_clues = [
        "studio", "ingegneria", "architettura", "engineering", "project",
        "associati", "progettazione", "srl", "spa"
    ]
    has_company_domain = any(c in domain for c in company_domain_clues)

    company_text_clues = [
        "societa di ingegneria", "società di ingegneria", "studio tecnico",
        "studio di architettura", "studio associato", "progettazione integrata",
        "progettazione impianti", "progettazione strutturale", "calcolo strutturale",
        "s.r.l.", "srl", "s.p.a.", "spa", "chi siamo", "i nostri progetti",
        "i nostri servizi", "il nostro team", "servizi di ingegneria"
    ]
    has_strong_company_signal = any(c in full_text for c in company_text_clues) or (
        has_company_domain and any(c in full_text for c in ["ingegneria", "architettura", "progettazione", "bim", "revit"])
    )

    # Check 1: Strong Academic / Training provider domain or dedicated course path
    # Dedicated academic/training domains or dedicated course URL endpoints:
    training_domain_or_path = (
        any(d in domain for d in ["universita", "unipd", "polimi", "polito", "unive", "accademia", "corsibim", "masterbim", "formazionebim", "istituto", "wikipedia.org", "scuola"]) or
        any(p in path for p in ["/corso/", "/corsi/", "/master/", "/iscrizione-corso", "/training-courses"])
    )

    # Strong training-primary title indicators (the page title itself indicates it is a course/master)
    primary_training_title = (
        ("corso" in title_lower and any(w in title_lower for w in ["formazione", "certificazione", "iscrizioni", "durata", "lezioni", "online", "base", "avanzato"])) or
        ("master" in title_lower and any(w in title_lower for w in ["bim", "universitario", "specialist", "management"])) or
        title_lower.startswith("corso ") or
        title_lower.startswith("corsi ") or
        title_lower.startswith("master ")
    )

    if training_domain_or_path or primary_training_title:
        # If the domain is an actual engineering company (e.g. 14p.it, etcstudio.it) that happens
        # to host a blog article or event about training, but is not an academic portal, route to NEEDS_REVIEW
        if has_strong_company_signal and not any(d in domain for d in ["universita", "polimi", "polito", "corsibim", "masterbim", "wikipedia.org"]):
            reasons.append("company_site_mentioning_course_or_training")
            return ResultTriageStatus.NEEDS_REVIEW, reasons

        reasons.append("academic_or_training_provider")
        return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # If snippet or title mentions 'corso', 'master', 'formazione' in passing:
    # A genuine engineering studio may discuss "formazione continua del personale" or "stage formativo"
    has_training_mention = any(w in full_text for w in ["corso", "corsi", "master", "formazione"])

    # Check 2: Professional Orders & Institutional Bodies
    # Professional orders (e.g. como.ordingegneri.it, architettimilano.it) are regulatory bodies, not design consultancies.
    order_clues = ["ordingegneri", "ordinearchitetti", "cnappc", "cni-online", "ordineingegneri", "collegio-geometri"]
    if any(oc in domain for oc in order_clues) or any(phrase in full_text for phrase in ["ordine degli ingegneri", "ordine degli architetti", "collegio dei geometri"]):
        reasons.append("professional_order_or_institutional_body")
        return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # Check 3: Architecture & Industry Portals / Magazine Blogs
    # Information portals (e.g. professionearchitetto.it, ingenio-web.it, edilportale.com)
    portal_domains = [
        "professionearchitetto.it", "ingenio-web.it", "edilportale.com", "archilovers.com",
        "archello.com", "archiportale.com", "guidafinestra.it", "teknoring.com"
    ]
    if any(pd in domain for pd in portal_domains):
        reasons.append("architecture_or_industry_information_portal")
        return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # Check 4: Software Manufacturers / Pure Tech Vendors
    # Global CAD/BIM software vendors (e.g. autodesk.com, graphisoft.com, trimble.com, bentley.com)
    software_vendor_domains = [
        "autodesk.com", "graphisoft.com", "trimble.com", "bentley.com",
        "nemetschek.com", "accasoftware.com", "str.it", "teamsystem.com"
    ]
    if any(sv in domain for sv in software_vendor_domains):
        reasons.append("software_manufacturer_not_service_buyer")
        return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # Check 5: Pure aggregators / job boards
    known_aggregators = [
        "linkedin.com", "indeed.com", "jooble.org", "infojobs.it",
        "glassdoor.com", "glassdoor.it", "bakeca.it", "subito.it",
        "kijiji.it", "monster.it", "paginegialle.it", "europages.com",
        "kompass.com", "tripadvisor.it", "facebook.com", "instagram.com",
        "youtube.com", "tiktok.com", "twitter.com", "x.com"
    ]
    if any(agg in domain for agg in known_aggregators):
        if discovery_mode == "job_first":
            reasons.append(f"job_board_signal_source: {domain}")
            return ResultTriageStatus.NEEDS_REVIEW, reasons
        else:
            reasons.append(f"aggregator_or_directory_domain: {domain}")
            return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # Check 3: News / Blogs / Generic portals
    if any(n in domain for n in ["ansa.it", "corriere.it", "repubblica.it", "ilsole24ore.com"]):
        reasons.append("news_portal")
        return ResultTriageStatus.REJECTED_IRRELEVANT, reasons

    # Check 4: Direct company evaluation
    if domain and "." in domain:
        # If strong company signal exists and it only mentions training in passing -> POTENTIAL_COMPANY
        if has_strong_company_signal:
            if has_training_mention:
                reasons.append("company_site_with_incidental_training_mention")
            else:
                reasons.append("direct_company_signals")
            return ResultTriageStatus.POTENTIAL_COMPANY, reasons

        # If it mentions training without strong company evidence, flag as NEEDS_REVIEW instead of outright rejection
        if has_training_mention:
            reasons.append("ambiguous_training_mention_without_strong_company_evidence")
            return ResultTriageStatus.NEEDS_REVIEW, reasons

        # General engineering / architecture clues
        company_clues = [
            "studio", "ingegneria", "architettura", "srl", "spa",
            "progettazione", "costruzioni", "associati", "engineering",
            "consulting", "team", "chi siamo", "servizi", "progetti"
        ]
        matching_clues = [c for c in company_clues if c in full_text]
        if matching_clues:
            reasons.append(f"direct_company_signals: {', '.join(matching_clues[:3])}")
            return ResultTriageStatus.POTENTIAL_COMPANY, reasons

        reasons.append("valid_domain_with_generic_content")
        return ResultTriageStatus.NEEDS_REVIEW, reasons

    reasons.append("no_valid_domain")
    return ResultTriageStatus.SEARCH_RESULT, reasons
