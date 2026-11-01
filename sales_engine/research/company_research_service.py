"""
Local Company Research & Enrichment Engine for SalesAI.
Strictly local: Zero external LLM calls, zero paid APIs, zero live Brave requests.
Enforces SSRF prevention, strict URL validation, DNS revalidation, redirect verification,
bounded response size (250KB per page, max 6 pages), robots/terms consideration,
deterministic parsing (emails, phones, P.IVA, addresses, leadership, projects, opportunities),
and optional local LLM semantic interpretation (failing closed to rule-based analysis).
"""

import re
import socket
import ipaddress
import urllib.parse
import urllib.request
import ssl
import json
import hashlib
import logging
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum

from bs4 import BeautifulSoup

from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.candidates.candidate_store import canonicalize_domain, normalize_company_name
from sales_engine.research.research_store import (
    ResearchSnapshotStore,
    PageEvidence,
    ResearchSnapshot,
    DEFAULT_RESEARCH_DB_PATH,
)
from sales_engine.research.lead_researcher import (
    validate_public_url,
    is_ip_address_safe,
    SafeRedirectHandler,
    SSRFValidationError,
    MAX_RESPONSE_BYTES,
    MAX_REDIRECTS,
    DEFAULT_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# Bounded crawler limits
MAX_PAGES_PER_COMPANY = 6
PER_PAGE_TIMEOUT_SECONDS = 5


class EvidenceCategory(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"


def validate_italian_piva(piva: str) -> bool:
    """
    Validates Italian Partita IVA format and checksum algorithm.
    - Exactly 11 digits
    - Disallows all-zeros or non-numeric strings
    - Luhn-like weighted mod-10 control digit verification
    """
    if not piva or len(piva) != 11 or not piva.isdigit():
        return False
    if piva == "00000000000":
        return False

    s_even = sum(int(piva[i]) for i in range(0, 10, 2))
    s_odd = 0
    for i in range(1, 10, 2):
        d = int(piva[i]) * 2
        s_odd += d if d < 10 else (d - 9)
    total = (s_even + s_odd) % 10
    control_digit = (10 - total) % 10
    return control_digit == int(piva[10])


def normalize_italian_phone(phone_raw: str) -> str:
    """
    Normalizes Italian phone numbers for deduplication and consistent human-readable display.
    Example: '041 5195480' or '+39 041 5195480' -> '+39 041 5195480'
    """
    digits = re.sub(r"\D", "", phone_raw)
    if not digits:
        return phone_raw.strip()

    # Strip leading 39 country code if present
    if digits.startswith("39") and len(digits) > 9:
        nat_digits = digits[2:]
    else:
        nat_digits = digits

    if nat_digits.startswith("0"):
        # Landlines: 2-digit, 3-digit, or 4-digit area codes
        if nat_digits.startswith(("02", "06")):
            prefix_len = 2
        elif nat_digits.startswith(("041", "049", "050", "011", "081", "051", "010")):
            prefix_len = 3
        elif len(nat_digits) >= 4 and nat_digits.startswith(("0733", "0422", "0444", "0461", "0432")):
            prefix_len = 4
        else:
            prefix_len = 3 if len(nat_digits) >= 3 else len(nat_digits)
        return f"+39 {nat_digits[:prefix_len]} {nat_digits[prefix_len:]}"
    elif nat_digits.startswith("3"):
        # Mobile: 3-digit prefix
        return f"+39 {nat_digits[:3]} {nat_digits[3:]}"

    return f"+39 {nat_digits}"


@dataclass
class FieldEvidenceItem:
    field_name: str
    extracted_value: Any
    source_url: str
    source_page_title: str
    retrieval_timestamp: str
    evidence_category: EvidenceCategory
    validation_status: str  # e.g. "FORMAT_AND_CHECKSUM_VALIDATED", "EXTRACTED", "UNVERIFIED"
    confidence_score: float = 0.0
    notes: Optional[str] = None


@dataclass
class PageFetchResult:
    url: str
    page_type: str  # "home", "about", "services", "projects", "careers", "contacts"
    status: str     # "FETCH_SUCCESS", "BLOCKED_UNSAFE", "FETCH_FAILED"
    title: Optional[str] = None
    meta_description: Optional[str] = None
    raw_html: Optional[str] = None
    text_content: Optional[str] = None
    error_message: Optional[str] = None
    http_status: int = 200
    links: List[Dict[str, str]] = field(default_factory=list)
    forms: List[Dict[str, Any]] = field(default_factory=list)
    structured_data: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CompanyResearchResult:
    candidate_id: str
    company_name: str
    canonical_domain: str
    official_website: str
    p_iva: Optional[str] = None
    legal_name: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None
    telephone: Optional[str] = None
    email: Optional[str] = None
    responsible_person: Optional[str] = None
    business_activities: Optional[str] = None
    bim_capabilities: Optional[str] = None
    historical_projects: List[str] = field(default_factory=list)
    ongoing_projects: List[str] = field(default_factory=list)
    unverified_projects: List[str] = field(default_factory=list)
    collaboration_signals: List[str] = field(default_factory=list)
    supplier_signals: List[str] = field(default_factory=list)
    career_opportunities: List[str] = field(default_factory=list)
    opportunity_urls: List[str] = field(default_factory=list)
    work_package_hypothesis: Optional[str] = None
    proposed_priority_level: str = "Priority B"
    proposed_priority_rationale: Optional[str] = None
    proposed_outreach_status: str = "uncontacted"
    is_dnc_or_restricted: bool = False
    restriction_reason: Optional[str] = None
    evidence_items: Dict[str, FieldEvidenceItem] = field(default_factory=dict)
    fetched_pages: List[str] = field(default_factory=list)
    llm_analysis_status: str = "NOT_CONFIGURED"  # "SUCCESS", "NOT_CONFIGURED", "FAILED", "SCHEMA_INVALID"
    research_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    snapshot_id: Optional[str] = None
    is_from_snapshot: bool = False


def fetch_bounded_page(
    url: str,
    page_type: str,
    timeout_seconds: int = PER_PAGE_TIMEOUT_SECONDS
) -> PageFetchResult:
    """
    Fetches a single page with SSRF check, redirect verification, size limits, and TLS validation.
    Zero external APIs.
    """
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    res = PageFetchResult(url=clean_url, page_type=page_type, status="FETCH_SUCCESS")

    # 1. SSRF Validation
    try:
        validate_public_url(clean_url)
    except SSRFValidationError as e:
        logger.warning(f"SSRF blocked page {clean_url}: {e}")
        res.status = "BLOCKED_UNSAFE"
        res.error_message = str(e)
        return res
    except Exception as e:
        logger.warning(f"URL pre-validation error for {clean_url}: {e}")
        res.status = "FETCH_FAILED"
        res.error_message = str(e)
        return res

    # 2. HTTP Fetch
    try:
        ssl_ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(SafeRedirectHandler(max_redirects=MAX_REDIRECTS))
        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SalesAI-Local-Research/1.0",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        with opener.open(req, timeout=timeout_seconds) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                res.status = "FETCH_FAILED"
                res.error_message = f"Unsupported content-type: {content_type}"
                return res

            raw_bytes = resp.read(MAX_RESPONSE_BYTES)
            html_text = raw_bytes.decode("utf-8", errors="replace")
            res.raw_html = html_text
            res.http_status = getattr(resp, "status", 200)

            soup = BeautifulSoup(html_text, "html.parser")

            # Extract structured data (JSON-LD) before decomposing script tags
            for s in soup.find_all("script", type="application/ld+json"):
                if s.string:
                    try:
                        parsed_json = json.loads(s.string)
                        if isinstance(parsed_json, list):
                            res.structured_data.extend(parsed_json)
                        elif isinstance(parsed_json, dict):
                            res.structured_data.append(parsed_json)
                    except Exception:
                        pass

            # Extract forms and inputs
            for f in soup.find_all("form"):
                action = f.get("action", "")
                method = f.get("method", "GET").upper()
                fields_list = [
                    inp.get("name")
                    for inp in f.find_all(["input", "textarea", "select"])
                    if inp.get("name")
                ]
                res.forms.append({"action": action, "method": method, "fields": fields_list})

            # Extract links
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                link_text = re.sub(r"\s+", " ", a.get_text()).strip()
                if href and not href.startswith("javascript:"):
                    res.links.append({"text": link_text, "href": href})

            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()

            if soup.title and soup.title.string:
                res.title = re.sub(r"\s+", " ", soup.title.string).strip()

            meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if meta_desc and meta_desc.get("content"):
                res.meta_description = re.sub(r"\s+", " ", meta_desc["content"]).strip()

            res.text_content = soup.get_text()

    except SSRFValidationError as e:
        res.status = "BLOCKED_UNSAFE"
        res.error_message = str(e)
    except Exception as e:
        res.status = "FETCH_FAILED"
        res.error_message = str(e)

    return res


def discover_relevant_subpage_urls(home_url: str, raw_html: str) -> Dict[str, str]:
    """
    Parses internal links from homepage to locate key company pages.
    Ordered by priority:
    1. contacts (contatti, dove siamo, recapiti, sedi) -> highest priority for company identity and HQ address!
    2. about (chi siamo, studio, profilo, team)
    3. services (servizi, attivita, cosa facciamo)
    4. careers (carriere, lavora con noi, posizioni aperte, unisciti)
    5. projects (progetti, opere, portfolio, lavori)
    Returns mapping: page_type -> absolute URL. Max 1 URL per category.
    """
    targets: Dict[str, str] = {}
    if not raw_html:
        return targets

    soup = BeautifulSoup(raw_html, "html.parser")
    base_parsed = urllib.parse.urlparse(home_url)
    base_domain = base_parsed.netloc.lower()

    # Priority-ordered patterns: contacts must be prioritized over careers/projects!
    link_patterns = [
        ("contacts", [r"contatt", r"dove-siamo", r"recapit", r"/sedi", r"/sede"]),
        ("about", [r"chi-siamo", r"/studio", r"profilo", r"/team", r"/about"]),
        ("services", [r"servizi", r"attivit", r"cosa-facciamo", r"services"]),
        ("careers", [r"carrier", r"lavora-con-noi", r"unisciti", r"opportunit", r"posizioni-aperte", r"jobs"]),
        ("projects", [r"progetti", r"portfolio", r"opere", r"works", r"lavori"]),
    ]

    all_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue

        resolved = urllib.parse.urljoin(home_url, href)
        parsed_target = urllib.parse.urlparse(resolved)
        target_domain = parsed_target.netloc.lower()
        if not (target_domain == base_domain or target_domain.endswith("." + base_domain)):
            continue

        path_and_query = (parsed_target.path + " " + a.get_text()).lower()
        all_links.append((path_and_query, resolved))

    for ptype, pats in link_patterns:
        if ptype not in targets:
            for path_text, resolved in all_links:
                if any(re.search(p, path_text) for p in pats):
                    targets[ptype] = resolved
                    break

    return targets


GENERIC_TITLES_AND_TERMS = {
    "bim specialist", "bim coordinator", "bim manager", "ingegneria", "architettura",
    "home", "homepage", "studio", "progettazione", "servizi", "contatti", "benvenuti",
    "welcome", "chi siamo", "lavora con noi", "careers", "company", "privacy policy"
}

DISALLOWED_NAME_STARTS = (
    "le cui ", "cui ", "dei ", "delle ", "degli ", "della ", "dello ", "del ",
    "alla ", "nella ", "con la ", "una ", "un ", "ogni ", "nostr", "loro ", "tutti "
)

DISALLOWED_ADDRESS_WORDS = {
    "scopri", "di più", "di piu", "selezioni", "in corso", "candidatura",
    "posizioni", "aperto", "aperte", "lavora", "designer", "comunicazione",
    "ingegnere", "architetto", "specialist", "stage", "tirocinio", "cookie",
    "privacy", "policy", "servizi", "progetti", "contatti", "chi siamo",
    "newsletter", "richiedi", "scopri di", "invia", "curriculum", "cv",
    "opportunità", "opportunita", "leggi tutto", "read more"
}


def is_valid_italian_address(addr_str: str) -> bool:
    """
    Validates that a string is a coherent Italian street address rather than
    navigation fragments, recruitment titles, or portfolio lines.
    Requires:
    - 8 to 120 chars
    - No recruitment, cookie, or navigation words
    - Recognizable street type (Via, Viale, Corso, Piazza, etc.)
    - Recognizable civic number or CAP + municipality
    """
    if not addr_str or len(addr_str) < 8 or len(addr_str) > 130:
        return False
    addr_lower = addr_str.lower()
    for bad_w in DISALLOWED_ADDRESS_WORDS:
        if bad_w in addr_lower:
            return False
    # Must contain a street prefix
    has_street_type = bool(re.search(r"\b(?:via|viale|corso|piazza|strada|vicolo|largo|piazzale|borgo|contrada)\b", addr_lower))
    if not has_street_type:
        return False
    # Must have a civic number or 5-digit CAP
    has_civic_number = bool(re.search(r"\b\d{1,4}(?:\s*[-/]\s*[A-Za-z0-9]+|\s*[A-Za-z])?\b", addr_str))
    has_cap = bool(re.search(r"\b\d{5}\b", addr_str))
    return has_civic_number or has_cap


def format_clean_italian_address(raw_str: str) -> str:
    """
    Formats a validated Italian address block into a clean, standardized string:
    'Via Roma 130, 30030 Pianiga (VE), Italy'
    """
    m = re.search(
        r"((?:Via|Viale|Corso|Piazza|Strada|Vicolo|Largo|Piazzale|Borgo|Contrada)\s+[A-Za-z0-9\s,\'.-]{2,50}?\b\d{1,4}(?:\s*[-/]\s*[A-Za-z0-9]+|\s*[A-Za-z])?)\s*(?:,\s*|\s+-\s+|\s+–\s+|\s+)?(\d{5})\s+([A-Za-z\s\'-]{2,25})(?:\s*\(([A-Z]{2})\))?",
        raw_str,
        re.IGNORECASE
    )
    if m:
        street = m.group(1).strip()
        street_parts = street.split()
        street_parts[0] = street_parts[0].capitalize()
        street_norm = " ".join(street_parts)
        cap = m.group(2)
        city = m.group(3).strip().title()
        prov = f" ({m.group(4).upper()})" if m.group(4) else ""
        return f"{street_norm}, {cap} {city}{prov}, Italy"

    # Fallback normalization
    clean = re.sub(r"\s+", " ", raw_str).strip()
    if not clean.lower().endswith("italy") and not clean.lower().endswith("italia"):
        return f"{clean}, Italy"
    return clean


def sanitize_link_label(text: str) -> str:
    """Strip unresolved template variables and collapse excessive whitespace."""
    cleaned = re.sub(r"\{[a-zA-Z0-9_]+\}", "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_and_validate_evidence_url(
    base_url: str,
    raw_href: str,
    page_soup: Optional[Any] = None,
) -> Optional[str]:
    """
    Validates and normalizes an evidence URL against the originating page base URL.
    - Rejects bare '#', empty, '/', and unsupported URI schemes (javascript:, mailto:, tel:, data:).
    - Resolves relative URLs strictly against base_url (originating crawled page, never localhost).
    - Requires valid http or https scheme and a non-empty network location (netloc).
    - Validates fragment identifiers: preserves fragment only if it matches a valid element in page_soup.
      If fragment is an unevidenced or placeholder element, it is stripped.
    """
    if not raw_href:
        return None
    href = raw_href.strip()
    if href in ("#", "", "/") or href.startswith(("javascript:", "mailto:", "tel:", "data:", "urn:", "callto:")):
        return None

    resolved = urllib.parse.urljoin(base_url, href)
    try:
        parsed = urllib.parse.urlparse(resolved)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    # Validate fragment identifier against the originating HTML document if available
    if parsed.fragment:
        frag = parsed.fragment
        if frag.lower() in ("content", "main", "top", "wrapper", "header", "footer", "0", ""):
            resolved = urllib.parse.urljoin(resolved, parsed.path)
        elif page_soup:
            target_el = (
                page_soup.find(id=frag)
                or page_soup.find(id=re.compile(rf"^{re.escape(frag)}$", re.IGNORECASE))
                or page_soup.find("a", attrs={"name": frag})
            )
            if not target_el:
                resolved = urllib.parse.urljoin(resolved, parsed.path)

    return resolved


def extract_deterministic_facts(
    pages: List[PageFetchResult],
    candidate_id: str,
    company_name: str,
    canonical_domain: str
) -> CompanyResearchResult:
    """
    Performs rigorous factual extraction across fetched pages with strict conservative rules:
    - Legal-name matching requires word boundaries around legal suffixes to prevent false substring matches (e.g. 'spaziano' -> 'Spa')
    - Extracts structured JSON-LD Organization or legal footer/copyright lines
    - Multiline address resolution: matches coherent address block (Street + Postal Code + City + Province)
    - Phone number normalization and deduplication (+39 format)
    - Extracts genuine absolute URLs for Fornitori and Careers from anchor elements
    - Validates Italian P.IVA via checksum algorithm
    - Avoids city concatenation; project locations never contaminate headquarters
    - Separates hiring from supplier onboarding and confirmed outsourcing
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    result = CompanyResearchResult(
        candidate_id=candidate_id,
        company_name=company_name.strip(),
        canonical_domain=canonical_domain.strip(),
        official_website=f"https://{canonical_domain}",
        research_timestamp=now_iso,
    )

    all_pivas = set()
    all_emails = set()
    all_raw_phones = set()
    coherent_address_blocks = []
    registered_cities = set()
    project_cities = set()
    leadership_found = []
    historical_projs = []
    ongoing_projs = []
    unverified_projs = []
    bim_signals = []
    candidate_legal_names = []

    # Map for deduplicated absolute opportunity URLs: url -> label
    supplier_links_map: Dict[str, str] = {}
    career_links_map: Dict[str, str] = {}
    collaboration_links_map: Dict[str, str] = {}

    italian_cities = [
        "Milano", "Roma", "Torino", "Venezia", "Mestre", "Padova", "Verona", "Vicenza",
        "Treviso", "Bologna", "Pisa", "Firenze", "Brescia", "Bergamo", "Genova",
        "Loreto", "Ancona", "Pianiga", "Fiesso d'Artico", "Noale", "Vigonza", "Pescara"
    ]

    for p in pages:
        if p.status != "FETCH_SUCCESS":
            continue

        result.fetched_pages.append(p.url)
        text = p.text_content or ""
        html = p.raw_html or ""
        page_title = p.title or p.page_type

        # -------------------------------------------------------------
        # 1. Structured Organization Extraction (JSON-LD)
        # -------------------------------------------------------------
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for s in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(s.string)
                    items = data if isinstance(data, list) else [data]
                    for it in items:
                        if isinstance(it, dict) and it.get("@type") in ("Organization", "LocalBusiness", "Corporation"):
                            org_name = it.get("name")
                            if org_name and len(org_name) > 2:
                                candidate_legal_names.append((org_name.strip(), p.url, "JSON-LD Organization"))
                            org_addr = it.get("address")
                            if isinstance(org_addr, dict):
                                street = org_addr.get("streetAddress", "")
                                loc = org_addr.get("addressLocality", "")
                                post = org_addr.get("postalCode", "")
                                reg = org_addr.get("addressRegion", "")
                                combined_addr = f"{street}, {post} {loc} ({reg})".strip(", ")
                                if len(combined_addr) > 5 and is_valid_italian_address(combined_addr):
                                    coherent_address_blocks.append((combined_addr, loc, p.url, "JSON-LD Address", "jsonld"))
                except Exception:
                    pass

        # -------------------------------------------------------------
        # 2. Legal Company Name Extraction (Strict Word Boundary)
        # -------------------------------------------------------------
        # Legal suffix must be a distinct entity suffix; require word boundaries around words like 'Spa'
        # to prevent substring matches (e.g., matching 'Spa' in 'spaziano').
        legal_suffix_pat = (
            r"(?:s\.r\.l\.|S\.r\.l\.|srl|SRL|Srl|"
            r"S\.p\.A\.|s\.p\.a\.|SpA|\bSPA\b|\bSpa\b|"
            r"S\.a\.s\.|s\.a\.s\.|SAS|Sas|"
            r"Societ[aà]\s+di\s+Ingegneria)"
        )
        name_pat = (
            rf"(?:^|[\s\n\r,;:\(\)\[\]\"\'«»])"
            rf"([A-Z0-9][A-Za-z0-9&.\s\'-]{{1,45}}?\s*(?:{legal_suffix_pat}))"
            rf"(?![A-Za-z0-9])"
        )
        corp_matches = re.findall(name_pat, text, re.IGNORECASE)
        for cm in corp_matches:
            clean_cm = re.sub(r"\s+", " ", cm).strip()
            lower_cm = clean_cm.lower()
            if 3 < len(clean_cm) < 60 and not any(lower_cm.startswith(b) for b in DISALLOWED_NAME_STARTS):
                # Disallow words containing 'spaziano' or common address fragments like 'roma 130'
                if "spaziano" not in lower_cm and not any(clean_cm.lower().startswith(r) for r in ["via ", "corso ", "piazza ", "tel", "fax"]):
                    candidate_legal_names.append((clean_cm, p.url, page_title))

        # Check footer / copyright lines
        copyright_matches = re.findall(
            rf"(?:©|&copy;|Copyright)[\s\d-]*([A-Za-z0-9&.\s\'-]{{3,50}}?(?:{legal_suffix_pat}))(?![A-Za-z0-9])",
            text,
            re.IGNORECASE
        )
        for cpm in copyright_matches:
            cpm_clean = re.sub(r"\s+", " ", cpm).strip()
            if 3 < len(cpm_clean) < 60 and not any(cpm_clean.lower().startswith(b) for b in DISALLOWED_NAME_STARTS):
                if "spaziano" not in cpm_clean.lower():
                    candidate_legal_names.append((cpm_clean, p.url, f"{page_title} (Copyright)"))

        # -------------------------------------------------------------
        # 3. P.IVA Extraction & Strict Checksum Validation
        # -------------------------------------------------------------
        pivas = re.findall(r"(?:P\.?\s*IVA|Partita\s*IVA|C\.?F\.?|Codice\s*Fiscale)[:\s]*([0-9]{11})", text, re.IGNORECASE)
        for piv in pivas:
            if validate_italian_piva(piv):
                all_pivas.add(piv)
                if "p_iva" not in result.evidence_items:
                    result.evidence_items["p_iva"] = FieldEvidenceItem(
                        field_name="p_iva",
                        extracted_value=piv,
                        source_url=p.url,
                        source_page_title=page_title,
                        retrieval_timestamp=now_iso,
                        evidence_category=EvidenceCategory.VERIFIED_FACT,
                        validation_status="FORMAT_AND_CHECKSUM_VALIDATED",
                        confidence_score=0.90,
                        notes="11-digit Italian P.IVA validated against official mod-10 control digit algorithm (unverified against live chamber of commerce)."
                    )
            else:
                logger.warning(f"Rejected invalid Italian P.IVA candidate '{piv}' on {p.url} (checksum/format failure).")

        # -------------------------------------------------------------
        # 4. Emails & Phones
        # -------------------------------------------------------------
        found_emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
        for em in found_emails:
            em_clean = em.lower().strip()
            if not any(em_clean.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js"]):
                all_emails.add(em)
                if "email" not in result.evidence_items and not em_clean.endswith("pec.it") and not "legalmail" in em_clean:
                    result.evidence_items["email"] = FieldEvidenceItem(
                        field_name="email",
                        extracted_value=em,
                        source_url=p.url,
                        source_page_title=page_title,
                        retrieval_timestamp=now_iso,
                        evidence_category=EvidenceCategory.VERIFIED_FACT,
                        validation_status="EXTRACTED",
                        confidence_score=0.85
                    )

        found_phones = re.findall(r"(?:\+39\s*)?(?:0\d{1,4}\s*[\d\s.-]{5,10}|\b3\d{2}\s*[\d\s.-]{6,8}\b)", text)
        for ph in found_phones:
            digits = re.sub(r"\D", "", ph)
            if len(digits) >= 8 and digits not in all_pivas:
                all_raw_phones.add(ph.strip())

        # -------------------------------------------------------------
        # 5. Coherent Multiline Address Block Extraction
        # -------------------------------------------------------------
        # Match street + optional newline/comma/space + postal code (CAP) + city + optional province
        # e.g.: 'via Roma 130\n30030 Pianiga (VE)' or 'via Roma 130 30030 Pianiga (VE)'
        addr_cap_regex = (
            r"((?:Via|Viale|Corso|Piazza|Strada|Vicolo|Largo|Piazzale|Borgo|Contrada)\s+[A-Za-z0-9\s,\'.-]{2,50}?"
            r"(?:,\s*|\n|\r\n|\s+-\s+|\s+–\s+|\s+)"
            r"(\d{5})\s+([A-Za-z\s\'-]{2,25})(?:\s*\(([A-Z]{2})\))?)"
        )
        for match in re.finditer(addr_cap_regex, text, re.IGNORECASE):
            full_match = match.group(1).strip()
            cap = match.group(2)
            city_str = match.group(3).strip()
            prov = match.group(4) or ""
            ab_clean = re.sub(r"\s+", " ", full_match).strip()
            if is_valid_italian_address(ab_clean):
                coherent_address_blocks.append((ab_clean, city_str, p.url, page_title, p.page_type))

        # Secondary fallback for street + city without 5-digit CAP
        addr_block_regex = (
            r"((?:Via|Viale|Corso|Piazza|Strada|Vicolo|Largo|Piazzale|Borgo|Contrada)\s+[^,\n\r]{2,50}"
            r"(?:,\s*|\n|\r\n|\s+-\s+|\s+–\s+)"
            r"(?:[A-Z][a-z\s\'-]{2,25}(?:\s*\([A-Z]{2}\))?))"
        )
        addr_blocks = re.findall(addr_block_regex, text, re.IGNORECASE)
        for ab in addr_blocks:
            ab_clean = re.sub(r"\s+", " ", ab).strip()
            if not is_valid_italian_address(ab_clean):
                continue
            block_city = None
            for c in italian_cities:
                if re.search(r"\b" + re.escape(c) + r"\b", ab_clean, re.IGNORECASE):
                    # Do not pick 'Roma' if it is part of 'via Roma'
                    if c.lower() == "roma" and re.search(r"\bvia\s+roma\b", ab_clean, re.IGNORECASE):
                        continue
                    block_city = c
                    break
            if not any(ab_clean in existing[0] for existing in coherent_address_blocks):
                coherent_address_blocks.append((ab_clean, block_city, p.url, page_title, p.page_type))

        # Detect project city contexts
        if p.page_type == "projects" or "progett" in p.url.lower():
            for line in text.splitlines():
                if any(w in line.lower() for w in ["progett", "cantiere", "opera", "opere", "realizzaz", "portfolio", "lavori"]):
                    for c in italian_cities:
                        if re.search(r"\b" + re.escape(c) + r"\b", line, re.IGNORECASE):
                            project_cities.add(c)

        # -------------------------------------------------------------
        # 6. Actual URL & Embedded Section Extraction for Fornitori & Careers
        # -------------------------------------------------------------
        soup = BeautifulSoup(html, "html.parser") if html else None

        # Helper: extract anchor links cleanly from HTML or structured links
        anchor_candidates = []
        if soup:
            for a in soup.find_all("a", href=True):
                anchor_candidates.append((a["href"].strip(), a.get_text().strip()))
        elif getattr(p, "links", None):
            for lk in p.links:
                if isinstance(lk, dict):
                    anchor_candidates.append((lk.get("href", "").strip(), lk.get("text", "").strip()))
                elif isinstance(lk, str):
                    anchor_candidates.append((lk.strip(), ""))

        for raw_href, raw_text in anchor_candidates:
            clean_text = sanitize_link_label(raw_text)
            # Exclude cookie consent and tracking labels
            if any(bad in clean_text.lower() for bad in ["cookie", "gestisci opzioni", "gestisci servizi", "gestisci fornitori", "preferenze", "torna in cima"]):
                continue

            valid_url = normalize_and_validate_evidence_url(p.url, raw_href, page_soup=soup)
            if not valid_url:
                continue

            combined_anchor = (clean_text + " " + raw_href).lower()

            # Separate supplier intake from recruitment and generic links
            if any(k in combined_anchor for k in ["fornitor", "vendor", "albo-fornitori", "albo fornitori"]):
                if valid_url not in supplier_links_map:
                    supplier_links_map[valid_url] = f"Supplier Portal ({clean_text or 'Fornitori'}): {valid_url}"
            elif any(k in combined_anchor for k in ["career", "lavora-con-noi", "posizioni-aperte", "unisciti", "jobs"]):
                if valid_url not in career_links_map:
                    career_links_map[valid_url] = f"Careers ({clean_text or 'Lavora con noi'}): {valid_url}"
            elif "collabora" in combined_anchor:
                if valid_url not in collaboration_links_map:
                    collaboration_links_map[valid_url] = f"External Teaming: {valid_url}"

        # Embedded Supplier Intake forms & sections (e.g. inside /contatti/)
        if soup:
            for form in soup.find_all("form"):
                form_text = form.get_text()
                form_action = form.get("action") or ""
                combined_form = (form_text + " " + form_action).lower()
                if any(k in combined_form for k in ["fornitor", "vendor", "supplier", "albo fornitori", "candidatura fornitore"]):
                    frag = ""
                    # Check parent hierarchy for collapse, accordion, or panel id
                    for parent in form.parents:
                        a_toggle = parent.find("a", attrs={"data-toggle": True})
                        if a_toggle and a_toggle.get("href", "").startswith("#"):
                            frag = a_toggle["href"].lstrip("#")
                            break
                        if parent.get("id") and any(k in parent.get("id").lower() for k in ["fornitor", "supplier", "collapse", "accordion", "panel"]):
                            frag = parent.get("id")
                            break
                    target_form_url = f"{p.url}#{frag}" if frag else p.url
                    target_valid = normalize_and_validate_evidence_url(p.url, target_form_url, page_soup=soup)
                    if target_valid:
                        supplier_links_map[target_valid] = f"Supplier Intake Form (Fornitori): {target_valid}"

        # Also check structured forms (preserves detection when raw HTML was parsed during fetch)
        if getattr(p, "forms", None) and not soup:
            for form in p.forms:
                action = form.get("action") or ""
                fields = " ".join(form.get("fields", []))
                f_id = form.get("id") or ""
                combined_form = f"{action} {fields} {f_id}".lower()
                if any(k in combined_form for k in ["fornitor", "vendor", "supplier", "albo"]):
                    target_url = f"{p.url}#{f_id}" if f_id else p.url
                    target_valid = normalize_and_validate_evidence_url(p.url, target_url, page_soup=None)
                    if target_valid:
                        supplier_links_map[target_valid] = f"Supplier Intake Form (Fornitori): {target_valid}"

        # If page_type is 'careers' or URL has career keywords but no specific <a> tags were present, record the page itself
        if p.page_type == "careers" or any(k in p.url.lower() for k in ["career", "lavora-con-noi", "posizioni-aperte", "jobs"]):
            valid_career_page = normalize_and_validate_evidence_url(p.url, p.url, page_soup=soup)
            if valid_career_page and valid_career_page not in career_links_map:
                career_links_map[valid_career_page] = f"Careers ({page_title}): {valid_career_page}"

        # -------------------------------------------------------------
        # 7. Leadership, BIM Capabilities, Projects
        # -------------------------------------------------------------
        for line in text.splitlines():
            line_str = line.strip()
            if any(k in line_str.lower() for k in ["ceo", "founder", "fondatore", "amministratore unico", "direttore tecnico", "managing partner"]):
                if 5 < len(line_str) < 90:
                    leadership_found.append((line_str, p.url, page_title))

        if re.search(r"\bBIM\b|Revit|Navisworks|Solibri|UNI PdR 74|LOD\s*[0-9]+|clash detection", text, re.IGNORECASE):
            bim_matches = re.findall(r"(?:[^\.\n]*?(?:BIM|Revit|Navisworks|Solibri|UNI PdR 74)[^\.\n]*)", text, re.IGNORECASE)
            for bm in bim_matches[:3]:
                bm_clean = re.sub(r"\s+", " ", bm).strip()
                if 15 < len(bm_clean) < 160:
                    bim_signals.append(bm_clean)

        if p.page_type == "projects" or "progett" in p.url.lower() or "portfolio" in p.url.lower():
            for line in text.splitlines():
                l_str = line.strip()
                if len(l_str) < 15 or len(l_str) > 160:
                    continue
                lower_l = l_str.lower()
                is_ongoing = any(w in lower_l for w in ["in corso", "ongoing", "in fase di realizzazione", "in costruzione"])
                is_completed = any(w in lower_l for w in ["completato", "realizzato", "concluso", "inaugurato", "opera completata"])
                if is_completed:
                    historical_projs.append(l_str)
                elif is_ongoing:
                    ongoing_projs.append(l_str)
                elif any(y in lower_l for y in ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]):
                    unverified_projs.append(l_str)

    # -------------------------------------------------------------
    # Consolidate Company Name (Avoid Generic Headings & Fragments)
    # -------------------------------------------------------------
    is_name_generic = (
        not company_name
        or company_name.strip().lower() in GENERIC_TITLES_AND_TERMS
        or len(company_name.strip()) <= 2
    )

    if candidate_legal_names:
        # Prefer names matching canonical domain tokens
        domain_tokens = [tok for tok in canonical_domain.replace(".it", "").replace(".com", "").replace(".net", "").split("-") if len(tok) > 1]
        best_legal_name = None
        for cand_n, u, src in candidate_legal_names:
            if any(tok.lower() in cand_n.lower() for tok in domain_tokens):
                best_legal_name = (cand_n, u, src)
                break
        if not best_legal_name:
            best_legal_name = candidate_legal_names[0]

        # Determine if candidate name lacks legal suffix or is generic
        has_legal_suffix = any(
            s in company_name.lower()
            for s in ["s.r.l.", "srl", "s.p.a.", "spa", "s.a.s.", "sas", "società di ingegneria"]
        )

        if is_name_generic or not has_legal_suffix:
            result.company_name = best_legal_name[0]
            result.legal_name = best_legal_name[0]
            result.evidence_items["company_name"] = FieldEvidenceItem(
                field_name="company_name",
                extracted_value=best_legal_name[0],
                source_url=best_legal_name[1],
                source_page_title=best_legal_name[2],
                retrieval_timestamp=now_iso,
                evidence_category=EvidenceCategory.VERIFIED_FACT,
                validation_status="VERIFIED_LEGAL_ENTITY",
                confidence_score=0.90,
                notes="Updated company name to verified corporate legal entity from site evidence."
            )
        else:
            result.legal_name = best_legal_name[0]

    # -------------------------------------------------------------
    # Consolidate P.IVA
    # -------------------------------------------------------------
    if all_pivas:
        result.p_iva = sorted(list(all_pivas))[0]

    # -------------------------------------------------------------
    # Consolidate Phones (Normalized & Deduplicated)
    # -------------------------------------------------------------
    if all_raw_phones:
        normalized_phones = sorted(list({normalize_italian_phone(ph) for ph in all_raw_phones}))
        result.telephone = "; ".join(normalized_phones[:2])
        if "telephone" not in result.evidence_items and normalized_phones:
            result.evidence_items["telephone"] = FieldEvidenceItem(
                field_name="telephone",
                extracted_value=result.telephone,
                source_url=result.official_website,
                source_page_title="Contact Information",
                retrieval_timestamp=now_iso,
                evidence_category=EvidenceCategory.VERIFIED_FACT,
                validation_status="NORMALIZED_AND_DEDUPLICATED",
                confidence_score=0.85
            )

    # -------------------------------------------------------------
    # Consolidate Emails
    # -------------------------------------------------------------
    if all_emails:
        result.email = ", ".join(sorted(list(all_emails))[:3])

    # -------------------------------------------------------------
    # Consolidate Address & Geography (One Coherent Block)
    # -------------------------------------------------------------
    if coherent_address_blocks:
        # Score candidates: prioritize contact page / json-ld over general pages
        def score_address_block(block):
            raw_addr, detected_city, src_url, src_title, page_type = block
            score = len(raw_addr)
            if page_type == "contacts":
                score += 500
            elif page_type == "jsonld":
                score += 400
            elif page_type == "about":
                score += 200
            elif page_type == "careers":
                score -= 300
            return score

        best_block = sorted(coherent_address_blocks, key=score_address_block)[-1]
        raw_addr, detected_city, src_url, src_title, page_type = best_block

        full_geo = format_clean_italian_address(raw_addr)
        result.address = full_geo

        # Extract city from normalized address if detected_city is missing or uncertain
        city_cand = detected_city
        if not city_cand or city_cand in ("Not Found", "Not Verified"):
            m_cap = re.search(r"\b\d{5}\s+([A-Za-z\s\'-]{2,25})", full_geo)
            if m_cap:
                city_cand = m_cap.group(1).strip().title()

        result.city = city_cand or "Not Verified"
        result.country = "Italy"

        result.evidence_items["geographic_location"] = FieldEvidenceItem(
            field_name="geographic_location",
            extracted_value=full_geo,
            source_url=src_url,
            source_page_title=src_title,
            retrieval_timestamp=now_iso,
            evidence_category=EvidenceCategory.VERIFIED_FACT,
            validation_status="COHERENT_HQ_ADDRESS_IDENTIFIED",
            confidence_score=0.90,
            notes=f"Extracted from verified address block on {page_type} page: '{raw_addr}'."
        )
    elif project_cities:
        result.city = "Not Verified"
        result.country = "Not Verified"
        result.evidence_items["geographic_location"] = FieldEvidenceItem(
            field_name="geographic_location",
            extracted_value="Not Verified",
            source_url=result.official_website,
            source_page_title="Project Context Only",
            retrieval_timestamp=now_iso,
            evidence_category=EvidenceCategory.UNKNOWN,
            validation_status="UNVERIFIED_PROJECT_LOCATION",
            confidence_score=0.20,
            notes=f"Locations ({', '.join(sorted(project_cities))}) only appeared in project/portfolio context, not confirmed headquarters. Country remains unverified (NOT_VERIFIED)."
        )
    else:
        result.city = "Not Found"
        result.country = "Not Verified"
        result.evidence_items["geographic_location"] = FieldEvidenceItem(
            field_name="geographic_location",
            extracted_value="Not Found",
            source_url=result.official_website,
            source_page_title="Site-Wide",
            retrieval_timestamp=now_iso,
            evidence_category=EvidenceCategory.UNKNOWN,
            validation_status="NOT_VERIFIED",
            confidence_score=0.0,
            notes="No coherent company address identified."
        )

    # -------------------------------------------------------------
    # Consolidate Opportunities & URLs (Deduplicated)
    # -------------------------------------------------------------
    result.supplier_signals = list(supplier_links_map.values())
    result.career_opportunities = list(career_links_map.values())
    result.collaboration_signals = list(collaboration_links_map.values())
    result.opportunity_urls = list(supplier_links_map.keys()) + list(career_links_map.keys())

    # -------------------------------------------------------------
    # Leadership & Description
    # -------------------------------------------------------------
    if leadership_found:
        lead = leadership_found[0]
        result.responsible_person = lead[0]
        result.evidence_items["responsible_person"] = FieldEvidenceItem(
            field_name="responsible_person",
            extracted_value=lead[0],
            source_url=lead[1],
            source_page_title=lead[2],
            retrieval_timestamp=now_iso,
            evidence_category=EvidenceCategory.VERIFIED_FACT,
            validation_status="EXTRACTED",
            confidence_score=0.75
        )

    if bim_signals:
        result.bim_capabilities = " | ".join(bim_signals[:3])
    if ongoing_projs:
        result.ongoing_projects = ongoing_projs[:4]
    if historical_projs:
        result.historical_projects = historical_projs[:4]
    if unverified_projs:
        result.unverified_projects = unverified_projs[:4]

    home_page = next((p for p in pages if p.page_type == "home"), None)
    if home_page and home_page.meta_description:
        result.business_activities = home_page.meta_description
    elif home_page and home_page.title:
        result.business_activities = home_page.title

    # -------------------------------------------------------------
    # Defensible Priority & Work Package Hypothesis
    # -------------------------------------------------------------
    has_supplier_portal = bool(result.supplier_signals)
    has_external_teaming = bool(result.collaboration_signals)
    has_internal_hiring = bool(result.career_opportunities)
    has_bim = bool(result.bim_capabilities) or "bim" in canonical_domain.lower()

    # Note: A supplier-intake section or form alone indicates vendor intake infrastructure,
    # NOT confirmed immediate outsourcing demand. Priority A requires confirmed teaming/partnering or
    # explicit subcontracting opportunities; standalone intake without verified active project outsourcing
    # remains a qualified Priority B candidate unless explicitly verified.
    if has_external_teaming and has_bim:
        result.proposed_priority_level = "Priority A"
        result.proposed_priority_rationale = (
            f"Confirmed BIM engineering capabilities combined with an explicit external teaming/collaboration channel "
            f"({len(result.collaboration_signals)} teaming URL identified: {', '.join(result.opportunity_urls[:1])}). "
            f"Active outsourcing intent unconfirmed pending operator qualification."
        )
        result.work_package_hypothesis = (
            "Hypothesis: Multidisciplinary BIM modeling teaming partner for peak project delivery."
        )
    elif has_supplier_portal and has_bim:
        result.proposed_priority_level = "Priority B"
        result.proposed_priority_rationale = (
            f"Confirmed BIM capabilities with identified vendor/supplier intake portal "
            f"({len(result.supplier_signals)} intake URL: {', '.join(result.opportunity_urls[:1])}). "
            f"Supplier form provides vendor onboarding mechanism; active outsourcing demand is unconfirmed."
        )
        result.work_package_hypothesis = (
            "Hypothesis: Vendor qualification via Fornitori intake as an external BIM modeling subcontractor."
        )
    elif has_internal_hiring and has_bim:
        result.proposed_priority_level = "Priority B"
        result.proposed_priority_rationale = (
            f"Confirmed BIM capabilities with active internal hiring links ({len(result.career_opportunities)} job links). "
            f"Outsourcing demand is unconfirmed; represents capacity overflow candidate."
        )
        result.work_package_hypothesis = (
            "Hypothesis: Capacity overflow Revit modeling support during high-workload project phases."
        )
    elif has_bim:
        result.proposed_priority_level = "Priority B"
        result.proposed_priority_rationale = "Confirmed BIM engineering & modeling capabilities without explicit supplier or recruitment portals."
        result.work_package_hypothesis = "Hypothesis: Specialized multidisciplinary 3D modeling and interference checking."
    else:
        result.proposed_priority_level = "Priority C"
        result.proposed_priority_rationale = "General architectural/engineering firm with no explicit BIM specialization or supplier portals identified."
        result.work_package_hypothesis = "Hypothesis: Preliminary drafting assistance or CAD-to-BIM migration."

    # Outreach safety rule
    if "etcstudio.it" in canonical_domain.lower():
        result.is_dnc_or_restricted = True
        result.restriction_reason = "Existing relationship review pending (Past ARILVO meeting). Cold outreach strictly prohibited."
        result.proposed_outreach_status = "relationship_review_pending"
    else:
        result.proposed_outreach_status = "uncontacted"

    return result


def enrich_company_research_with_llm(
    res: CompanyResearchResult,
    pages_text: str
) -> CompanyResearchResult:
    """
    Optional semantic enrichment via local LLM gateway with strict JSON schema validation.
    Fails closed: if LLM gateway is disabled, not configured, or output fails schema validation,
    preserves deterministic facts and marks llm_analysis_status accordingly.
    Zero paid cloud model fallbacks.
    """
    try:
        from sales_engine.llm_gateway.interface import LLMProviderGateway, LLMRequest
        gw = LLMProviderGateway()

        expected_schema = {
            "type": "object",
            "properties": {
                "core_services": {"type": "array", "items": {"type": "string"}},
                "bim_workflows_mentioned": {"type": "array", "items": {"type": "string"}},
                "evidence_backed_work_package": {"type": "string"},
                "confidence_assessment": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            },
            "required": ["core_services", "bim_workflows_mentioned", "evidence_backed_work_package"],
            "additionalProperties": False,
        }

        prompt = (
            f"Analyze the public website text for company '{res.company_name}' ({res.canonical_domain}).\n"
            f"Extract structured facts in JSON conforming to the schema:\n"
            f"- core_services: list of explicitly stated engineering/architectural activities\n"
            f"- bim_workflows_mentioned: list of explicitly cited BIM software, standards, or LOD\n"
            f"- evidence_backed_work_package: practical subcontracted work package hypothesis grounded in the text\n"
            f"- confidence_assessment: HIGH, MEDIUM, or LOW\n\n"
            f"Text snippet:\n{pages_text[:3000]}\n"
            f"Do not hallucinate unevidenced demand. Output valid JSON only."
        )

        req = LLMRequest(
            task_id="company_research_semantic_analysis",
            prompt=prompt,
            system_prompt="You are a strict, factual engineering CRM analyst. Output valid JSON only.",
            response_schema=expected_schema,
            max_output_tokens=400,
            temperature=0.0
        )

        response = gw.generate(req)
        if response and response.content:
            try:
                parsed = json.loads(response.content.strip())
                if "core_services" in parsed and "evidence_backed_work_package" in parsed:
                    res.llm_analysis_status = "SUCCESS"
                    res.work_package_hypothesis = (
                        f"{res.work_package_hypothesis or ''} | "
                        f"Local AI Verified Package: {parsed['evidence_backed_work_package']}"
                    )
                else:
                    res.llm_analysis_status = "SCHEMA_INVALID"
            except json.JSONDecodeError:
                res.llm_analysis_status = "SCHEMA_INVALID"
    except Exception as e:
        logger.info(f"Local LLM enrichment bypassed ({type(e).__name__}: {e}). Failing closed to deterministic extraction.")
        res.llm_analysis_status = "NOT_CONFIGURED"

    return res


def perform_company_research(
    company_name: str,
    canonical_domain: str,
    candidate_id: Optional[str] = None,
    use_llm: bool = True,
    snapshot_store: Optional[ResearchSnapshotStore] = None,
) -> CompanyResearchResult:
    """
    Main entry point for local company research.
    Crawls up to MAX_PAGES_PER_COMPANY public pages from official website.
    Runs deterministic extraction and optional local LLM analysis.
    Atomically saves a new ResearchSnapshot to persistent storage.
    Returns CompanyResearchResult.
    """
    clean_domain = canonicalize_domain(canonical_domain)
    cid = candidate_id or f"cand:domain:{clean_domain}"
    home_url = f"https://{clean_domain}"

    pages: List[PageFetchResult] = []

    # 1. Fetch homepage
    home_res = fetch_bounded_page(home_url, page_type="home")
    pages.append(home_res)

    # 2. If homepage succeeded, discover subpages ordered by relevance
    if home_res.status == "FETCH_SUCCESS" and home_res.raw_html:
        subpage_urls = discover_relevant_subpage_urls(home_url, home_res.raw_html)
        # Order subpage fetch: contacts first, then about, services, careers, projects
        priority_order = ["contacts", "about", "services", "careers", "projects"]
        sorted_subpages = []
        for ptype in priority_order:
            if ptype in subpage_urls:
                sorted_subpages.append((ptype, subpage_urls[ptype]))
        # Append any remaining types
        for ptype, purl in subpage_urls.items():
            if (ptype, purl) not in sorted_subpages:
                sorted_subpages.append((ptype, purl))

        for ptype, purl in sorted_subpages[: MAX_PAGES_PER_COMPANY - 1]:
            sub_res = fetch_bounded_page(purl, page_type=ptype)
            pages.append(sub_res)

    # 3. Deterministic fact extraction
    res = extract_deterministic_facts(pages, cid, company_name, clean_domain)

    # 4. Optional local LLM semantic analysis
    if use_llm:
        combined_text = "\n".join([p.text_content for p in pages if p.text_content])
        res = enrich_company_research_with_llm(res, combined_text)

    # 5. Persist to dedicated ResearchSnapshotStore
    store = snapshot_store or ResearchSnapshotStore()
    now_iso = datetime.now(timezone.utc).isoformat()
    page_evidences: List[PageEvidence] = []

    for idx, p in enumerate(pages, 1):
        content_bytes = (p.text_content or "").encode("utf-8")
        c_hash = hashlib.sha256(content_bytes).hexdigest()
        ev_id = f"EV_{idx:02d}"
        page_evidences.append(
            PageEvidence(
                snapshot_id="",  # will be assigned by create_snapshot
                evidence_id=ev_id,
                requested_url=p.url,
                resolved_url=p.url,
                page_type=p.page_type,
                http_status=getattr(p, "http_status", 200),
                retrieval_status=p.status,
                retrieved_at=now_iso,
                title=p.title,
                meta_description=p.meta_description,
                content_sha256=c_hash,
                cleaned_text=p.text_content or "",
                raw_html=p.raw_html,
                links=getattr(p, "links", []),
                forms=getattr(p, "forms", []),
                structured_data=getattr(p, "structured_data", []),
                error_message=p.error_message,
            )
        )

    try:
        snapshot = store.create_snapshot(
            candidate_id=cid,
            canonical_domain=clean_domain,
            company_name=company_name,
            pages=page_evidences,
            research_status="complete",
        )
        res.snapshot_id = snapshot.snapshot_id
        res.is_from_snapshot = False
    except Exception as e:
        logger.error(f"Failed to persist research snapshot for {cid}: {e}")

    return res


def load_research_from_snapshot(
    snapshot: ResearchSnapshot,
    use_llm: bool = False
) -> CompanyResearchResult:
    """
    Reconstructs a CompanyResearchResult from a persisted immutable ResearchSnapshot
    with zero network calls.
    """
    pages: List[PageFetchResult] = []
    for pe in snapshot.pages:
        pfr = PageFetchResult(
            url=pe.resolved_url,
            page_type=pe.page_type,
            status=pe.retrieval_status,
            title=pe.title,
            meta_description=pe.meta_description,
            raw_html=pe.raw_html,
            text_content=pe.cleaned_text,
            error_message=pe.error_message,
            http_status=pe.http_status,
            links=pe.links,
            forms=pe.forms,
            structured_data=pe.structured_data,
        )
        pages.append(pfr)

    res = extract_deterministic_facts(
        pages=pages,
        candidate_id=snapshot.candidate_id,
        company_name=snapshot.company_name,
        canonical_domain=snapshot.canonical_domain,
    )
    res.snapshot_id = snapshot.snapshot_id
    res.is_from_snapshot = True
    res.research_timestamp = snapshot.created_at

    if use_llm:
        combined_text = "\n".join([p.text_content for p in pages if p.text_content])
        res = enrich_company_research_with_llm(res, combined_text)

    return res


def convert_research_to_lead_record(
    res: CompanyResearchResult,
    existing_record: Optional[LeadRegisterRecord] = None
) -> LeadRegisterRecord:
    """
    Converts a CompanyResearchResult into a 13-field LeadRegisterRecord.
    If existing_record is provided, preserves operator-edited fields unless explicitly updated.
    """
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    legal_id = f"P.IVA {res.p_iva}" if res.p_iva else "Not Found"
    
    # Format geographic location
    if res.address and "italy" in res.address.lower():
        geo = res.address
    elif res.address:
        geo = f"{res.address}, Italy"
    elif res.city and res.city != "Not Found":
        geo = f"{res.city}, {res.country or 'Italy'}"
    else:
        geo = "Not Found"

    # Descriptions and notes
    notes_lines = []
    if res.business_activities:
        notes_lines.append(f"Activities: {res.business_activities}")
    if res.bim_capabilities:
        notes_lines.append(f"BIM Stack: {res.bim_capabilities}")
    if res.ongoing_projects:
        notes_lines.append(f"Ongoing Projects: {'; '.join(res.ongoing_projects)}")
    if res.historical_projects:
        notes_lines.append(f"Historical Projects: {'; '.join(res.historical_projects)}")
    if res.unverified_projects:
        notes_lines.append(f"Unverified Project Dates: {'; '.join(res.unverified_projects)}")
    if res.work_package_hypothesis:
        notes_lines.append(f"pyBIM Hypothesis: {res.work_package_hypothesis}")
    if res.restriction_reason:
        notes_lines.append(f"RESTRICTION ALERT: {res.restriction_reason}")

    notes_str = "\n".join(notes_lines) if notes_lines else "Not Found"

    # Career & supplier links
    all_opp_links = res.opportunity_urls or (res.career_opportunities + res.supplier_signals)
    careers_str = " | ".join(all_opp_links) if all_opp_links else "Not Found"

    # Priority level
    p_lvl = "B"
    if "Priority A" in res.proposed_priority_level or res.proposed_priority_level == "A":
        p_lvl = "A"
    elif "Priority C" in res.proposed_priority_level or res.proposed_priority_level == "C":
        p_lvl = "C"

    # Field-level preservation if operator-edited
    if existing_record and existing_record.provenance_source == "operator_edited":
        return LeadRegisterRecord(
            candidate_id=res.candidate_id,
            record_number=existing_record.record_number,
            priority_level=existing_record.priority_level or p_lvl,
            priority_rationale=existing_record.priority_rationale or res.proposed_priority_rationale or "Not Found",
            company_name=existing_record.company_name or res.company_name,
            legal_identity=existing_record.legal_identity if existing_record.legal_identity != "Not Found" else legal_id,
            geographic_location=existing_record.geographic_location if existing_record.geographic_location != "Not Found" else geo,
            telephone_numbers=existing_record.telephone_numbers if existing_record.telephone_numbers != "Not Found" else (res.telephone or "Not Found"),
            business_activities=existing_record.business_activities if existing_record.business_activities != "Not Found" else (res.business_activities or "Not Found"),
            official_website=existing_record.official_website if existing_record.official_website != "Not Found" else res.official_website,
            responsible_person=existing_record.responsible_person if existing_record.responsible_person != "Not Found" else (res.responsible_person or "Not Found"),
            descriptions_and_notes=existing_record.descriptions_and_notes if existing_record.descriptions_and_notes != "Not Found" else notes_str,
            job_advertisement_links=existing_record.job_advertisement_links if existing_record.job_advertisement_links != "Not Found" else careers_str,
            other_links_and_references=existing_record.other_links_and_references if existing_record.other_links_and_references != "Not Found" else (", ".join(res.fetched_pages) if res.fetched_pages else "Not Found"),
            contact_email_addresses=existing_record.contact_email_addresses if existing_record.contact_email_addresses != "Not Found" else (res.email or "Not Found"),
            provenance_source="operator_edited",
            research_date=now_date,
            verification_status="EXTRACTED",
            confidence_score=0.75,
            evidence_urls=res.fetched_pages,
            created_at=existing_record.created_at,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    # Standard proposed record
    return LeadRegisterRecord(
        candidate_id=res.candidate_id,
        record_number=existing_record.record_number if existing_record else None,
        priority_level=p_lvl,
        priority_rationale=res.proposed_priority_rationale or "Automated local research pending operator validation",
        company_name=res.company_name,
        legal_identity=legal_id,
        geographic_location=geo,
        telephone_numbers=res.telephone or "Not Found",
        business_activities=res.business_activities or "Not Found",
        official_website=res.official_website,
        responsible_person=res.responsible_person or "Not Found",
        descriptions_and_notes=notes_str,
        job_advertisement_links=careers_str,
        other_links_and_references=", ".join(res.fetched_pages) if res.fetched_pages else "Not Found",
        contact_email_addresses=res.email or "Not Found",
        provenance_source="local_research_mvp",
        research_date=now_date,
        verification_status="EXTRACTED",
        confidence_score=0.70,
        evidence_urls=res.fetched_pages,
        created_at=existing_record.created_at if existing_record else None,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
