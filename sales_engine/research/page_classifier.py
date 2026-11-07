"""
Sales-AI Page Classifier Engine.

Deterministic, multi-lingual (Italian, English, German) page classification
for engineering, architectural, and construction company websites.

Classifies crawled pages into standard high-level business categories without
making any LLM or external network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse


class PageCategory(str, Enum):
    SERVICES = "SERVICES"
    ABOUT_COMPANY = "ABOUT_COMPANY"
    CONTACT = "CONTACT"
    BIM_CAPABILITIES = "BIM_CAPABILITIES"
    PROJECTS = "PROJECTS"
    TEAM = "TEAM"
    CAREERS = "CAREERS"
    LEGAL = "LEGAL"
    OTHER = "OTHER"


@dataclass
class PageClassification:
    category: PageCategory
    confidence: float
    reasons: List[str] = field(default_factory=list)
    signals_matched: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, any]:
        return {
            "category": self.category.value,
            "confidence": round(self.confidence, 3),
            "reasons": self.reasons,
            "signals_matched": self.signals_matched,
        }


# Strict token boundary patterns for multi-lingual URL path, title, and body matching
# Avoids substring false positives like "service" in "disservice" or "bim" in "bimodal"

# --- BIM CAPABILITIES (Specialized high-value technical indicator) ---
BIM_PATTERNS = [
    r"\bbim\b",
    r"\bbuilding\s*information\s*modeling\b",
    r"\bbuilding\s*information\s*modelling\b",
    r"\bdigital\s*twin\b",
    r"\bcomputational\s*design\b",
    r"\brevit\b",
    r"\bnavisworks\b",
    r"\bifc\b",
    r"\bclash\s*detection\b",
    r"\bparametric\b",
    r"\bmodellazione\s*bim\b",
    r"\bprogettazione\s*bim\b",
    r"\bopenbim\b",
    r"\bpoint\s*cloud\b",
    r"\bscan\s*to\s*bim\b",
]

# --- SERVICES / CAPABILITIES ---
SERVICES_PATTERNS = [
    # Italian
    r"\bserviz[io]\b",
    r"\bcompetenze\b",
    r"\bcosa[-_ ]facciamo\b",
    r"\battivit[aà]\b",
    r"\bprestazioni\b",
    r"\bsettori\b",
    r"\bambiti\b",
    r"\bconsulenza\b",
    r"\bingegneria\b",
    r"\bprogettazione\b",
    r"\bimpianti\b",
    r"\bstrutture\b",
    r"\befficienza[-_ ]energetica\b",
    r"\bsostenibilit[aà]\b",
    r"\bsicurezza\b",
    # English
    r"\bservices?\b",
    r"\bcapabilities\b",
    r"\bwhat[-_ ]we[-_ ]do\b",
    r"\bexpertise\b",
    r"\bsolutions\b",
    r"\bdisciplines\b",
    r"\bpractice[-_ ]areas\b",
    r"\bconsulting\b",
    r"\bengineering\b",
    r"\bstructural\b",
    r"\bmechanical\b",
    r"\belectrical\b",
    r"\benergy[-_ ]efficiency\b",
    r"\bsustainability\b",
    # German
    r"\bleistungen\b",
    r"\bkompetenzen\b",
    r"\bwas[-_ ]wir[-_ ]tun\b",
    r"\bberatung\b",
    r"\bingenieursberatung\b",
    r"\bfachplanung\b",
    r"\btechnische[-_ ]ausr[uü]stung\b",
    r"\btragwerksplanung\b",
    r"\benergieberatung\b",
]

# --- ABOUT COMPANY ---
ABOUT_PATTERNS = [
    # Italian
    r"\bchi[-_ ]siamo\b",
    r"\bazienda\b",
    r"\bsociet[aà]\b",
    r"\bstoria\b",
    r"\bprofilo\b",
    r"\bmission\b",
    r"\bvision\b",
    r"\bfilosofia\b",
    r"\bvalori\b",
    r"\bgruppo\b",
    r"\bchi\s+siamo\b",
    # English
    r"\babout[-_ ]us\b",
    r"\babout\b",
    r"\bcompany\b",
    r"\bwho[-_ ]we[-_ ]are\b",
    r"\bhistory\b",
    r"\bour[-_ ]story\b",
    r"\bmission[-_ ]and[-_ ]vision\b",
    r"\bcorporate\b",
    r"\boverview\b",
    # German
    r"\b[uü]ber[-_ ]uns\b",
    r"\bunternehmen\b",
    r"\bfirma\b",
    r"\bgeschichte\b",
    r"\bphilosophie\b",
    r"\bleitbild\b",
    r"\bprofil\b",
]

# --- CONTACT ---
CONTACT_PATTERNS = [
    # Italian
    r"\bcontatt[io]\b",
    r"\bdove[-_ ]siamo\b",
    r"\brecapit[io]\b",
    r"\bsede\b",
    r"\bsedi\b",
    r"\bscrivici\b",
    # English
    r"\bcontact[-_ ]us\b",
    r"\bcontact\b",
    r"\bcontacts\b",
    r"\bfind[-_ ]us\b",
    r"\bget[-_ ]in[-_ ]touch\b",
    r"\blocations?\b",
    r"\boffices?\b",
    r"\breach[-_ ]us\b",
    # German
    r"\bkontakt\b",
    r"\banfahrt\b",
    r"\bstandort[e]?\b",
    r"\banschrift\b",
    r"\bkontaktieren\b",
]

# --- PROJECTS / PORTFOLIO / WORKS ---
PROJECTS_PATTERNS = [
    # Italian
    r"\bprogett[io]\b",
    r"\bportfolio\b",
    r"\bopere\b",
    r"\brealizzazioni\b",
    r"\blavori\b",
    r"\breferenze\b",
    r"\bcase[-_ ]histor(y|ies)\b",
    # English
    r"\bprojects?\b",
    r"\bportfolio\b",
    r"\bworks?\b",
    r"\bcase[-_ ]stud(y|ies)\b",
    r"\breferences?\b",
    r"\btrack[-_ ]record\b",
    # German
    r"\bprojekte\b",
    r"\breferenzen\b",
    r"\barbeiten\b",
    r"\bbauvorhaben\b",
    r"\bobjekte\b",
]

# --- TEAM / LEADERSHIP ---
TEAM_PATTERNS = [
    # Italian
    r"\bteam\b",
    r"\bsquadra\b",
    r"\bmanagement\b",
    r"\bdirezione\b",
    r"\bpartner[s]?\b",
    r"\bcollaboratori\b",
    r"\bfondator[ie]\b",
    # English
    r"\bour[-_ ]team\b",
    r"\bteam\b",
    r"\bleadership\b",
    r"\bpeople\b",
    r"\bexecutives\b",
    r"\bboard\b",
    r"\bfounders?\b",
    # German
    r"\bteam\b",
    r"\bf[uü]hrung\b",
    r"\bmitarbeiter\b",
    r"\bgesch[aä]ftsf[uü]hrung\b",
    r"\bleitung\b",
]

# --- CAREERS / JOBS ---
CAREERS_PATTERNS = [
    # Italian
    r"\blavora[-_ ]con[-_ ]noi\b",
    r"\bposizioni[-_ ]aperte\b",
    r"\bcarriere\b",
    r"\bofferte[-_ ]lavoro\b",
    r"\bcandidatur[ae]\b",
    # English
    r"\bcareers?\b",
    r"\bjobs?\b",
    r"\bjoin[-_ ]us\b",
    r"\bopenings\b",
    r"\bvacancies\b",
    r"\bwork[-_ ]with[-_ ]us\b",
    # German
    r"\bkarriere\b",
    r"\bjobs\b",
    r"\bstellenangebote\b",
    r"\boffene[-_ ]stellen\b",
    r"\bmitarbeiten\b",
]

# --- LEGAL / PRIVACY / IMPRESSUM ---
LEGAL_PATTERNS = [
    # Italian
    r"\bprivacy\b",
    r"\bprivacy[-_ ]policy\b",
    r"\bcookie[s]?[-_ ]policy\b",
    r"\bnote[-_ ]legali\b",
    r"\btermini[-_ ]e[-_ ]condizioni\b",
    r"\bdisclaimer\b",
    r"\bdati[-_ ]societar[io]\b",
    # English
    r"\bprivacy[-_ ]policy\b",
    r"\bterms[-_ ]of[-_ ]service\b",
    r"\bterms\b",
    r"\blegal[-_ ]notice\b",
    r"\bdisclaimer\b",
    r"\bcookie[-_ ]policy\b",
    r"\bcompliance\b",
    # German
    r"\bimpressum\b",
    r"\bdatenschutz\b",
    r"\bagb\b",
    r"\bhilfeseite\b",
    r"\brechtliches\b",
]


def _compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


COMPILED_PATTERNS = {
    PageCategory.BIM_CAPABILITIES: _compile_patterns(BIM_PATTERNS),
    PageCategory.SERVICES: _compile_patterns(SERVICES_PATTERNS),
    PageCategory.ABOUT_COMPANY: _compile_patterns(ABOUT_PATTERNS),
    PageCategory.CONTACT: _compile_patterns(CONTACT_PATTERNS),
    PageCategory.PROJECTS: _compile_patterns(PROJECTS_PATTERNS),
    PageCategory.TEAM: _compile_patterns(TEAM_PATTERNS),
    PageCategory.CAREERS: _compile_patterns(CAREERS_PATTERNS),
    PageCategory.LEGAL: _compile_patterns(LEGAL_PATTERNS),
}


def _match_patterns(text: str, category: PageCategory) -> List[str]:
    """Matches text against category regex patterns, returning matched tokens."""
    if not text:
        return []
    matches = []
    for pat in COMPILED_PATTERNS.get(category, []):
        found = pat.findall(text)
        if found:
            # findall may return tuples if grouping is used; normalize to strings
            for f in found:
                match_str = f[0] if isinstance(f, tuple) else f
                if match_str and match_str not in matches:
                    matches.append(match_str)
    return matches


def classify_page(
    url: str,
    title: Optional[str] = None,
    cleaned_text: Optional[str] = None,
    anchor_text: Optional[str] = None,
    headings: Optional[List[str]] = None,
) -> PageClassification:
    """
    Deterministically classifies a web page into a PageCategory using multi-lingual signals.

    Weighted scoring across URL path/slug (weight 4.0), anchor/title (weight 2.5),
    headings (weight 2.0), and body text sample (weight 1.0).
    """
    parsed = urlparse(url or "")
    path = parsed.path.lower()
    path_clean = path.replace("/", " ").replace("-", " ").replace("_", " ")

    title_clean = (title or "").lower()
    anchor_clean = (anchor_text or "").lower()
    headings_clean = " ".join(headings or []).lower()
    
    # Restrict body text scan to first 2,000 characters to stay fast and avoid false matches in footers
    body_sample = (cleaned_text or "")[:2000].lower()

    # Is it the home page?
    is_home = path.strip("/") == "" or path.strip("/") in ("index.html", "index.php", "home", "it", "en", "de")
    if is_home and not title_clean and not body_sample:
        return PageClassification(
            category=PageCategory.ABOUT_COMPANY,
            confidence=0.8,
            reasons=["Root / Homepage URL"],
            signals_matched=["root_path"],
        )

    # Score each category
    scores: Dict[PageCategory, float] = {cat: 0.0 for cat in PageCategory if cat != PageCategory.OTHER}
    reasons: Dict[PageCategory, List[str]] = {cat: [] for cat in PageCategory}
    signals: Dict[PageCategory, List[str]] = {cat: [] for cat in PageCategory}

    # Evaluate each category
    for cat in scores.keys():
        # 1. URL Path matches (Highest weight - strongly indicates intentional page topic)
        path_matches = _match_patterns(path_clean, cat)
        if path_matches:
            weight = 4.0
            scores[cat] += weight * len(path_matches)
            reasons[cat].append(f"URL path matches {cat.value}: {path_matches}")
            signals[cat].extend([f"url:{m}" for m in path_matches])

        # 2. Anchor text matches (from referring link)
        anchor_matches = _match_patterns(anchor_clean, cat)
        if anchor_matches:
            weight = 2.5
            scores[cat] += weight * len(anchor_matches)
            reasons[cat].append(f"Anchor text matches {cat.value}: {anchor_matches}")
            signals[cat].extend([f"anchor:{m}" for m in anchor_matches])

        # 3. Title matches
        title_matches = _match_patterns(title_clean, cat)
        if title_matches:
            weight = 2.5
            scores[cat] += weight * len(title_matches)
            reasons[cat].append(f"Page title matches {cat.value}: {title_matches}")
            signals[cat].extend([f"title:{m}" for m in title_matches])

        # 4. Headings matches
        headings_matches = _match_patterns(headings_clean, cat)
        if headings_matches:
            weight = 2.0
            scores[cat] += weight * min(len(headings_matches), 3)
            reasons[cat].append(f"Headings match {cat.value}: {headings_matches[:3]}")
            signals[cat].extend([f"heading:{m}" for m in headings_matches[:3]])

        # 5. Body sample matches (Lowest weight, capped to avoid footer link noise)
        body_matches = _match_patterns(body_sample, cat)
        if body_matches:
            weight = 1.0
            scores[cat] += weight * min(len(body_matches), 2)
            reasons[cat].append(f"Body matches {cat.value}: {body_matches[:2]}")
            signals[cat].extend([f"body:{m}" for m in body_matches[:2]])

    # BIM Capabilities boost: if BIM is explicitly matched in path or title, it is distinctive
    if scores[PageCategory.BIM_CAPABILITIES] >= 2.5:
        # Give strong consideration for BIM if it has high relevance
        scores[PageCategory.BIM_CAPABILITIES] *= 1.3

    # Legal/Privacy check: if URL explicitly matches legal/privacy, lock it in to prevent miscategorization
    legal_url_matches = _match_patterns(path_clean, PageCategory.LEGAL)
    if legal_url_matches:
        scores[PageCategory.LEGAL] += 10.0

    # Career check: if URL explicitly matches careers, boost it
    career_url_matches = _match_patterns(path_clean, PageCategory.CAREERS)
    if career_url_matches:
        scores[PageCategory.CAREERS] += 10.0

    # Contact check: if URL explicitly matches contact, boost it
    contact_url_matches = _match_patterns(path_clean, PageCategory.CONTACT)
    if contact_url_matches:
        scores[PageCategory.CONTACT] += 10.0

    # Find the best category
    best_cat = PageCategory.OTHER
    best_score = 0.0

    # Priority tie-breaker order if scores are close
    priority_order = [
        PageCategory.LEGAL,
        PageCategory.CAREERS,
        PageCategory.CONTACT,
        PageCategory.BIM_CAPABILITIES,
        PageCategory.SERVICES,
        PageCategory.PROJECTS,
        PageCategory.TEAM,
        PageCategory.ABOUT_COMPANY,
    ]

    for cat in priority_order:
        score = scores.get(cat, 0.0)
        if score > best_score and score >= 2.0:
            best_score = score
            best_cat = cat

    # Fallback check for homepage if no category stood out strongly
    if best_cat == PageCategory.OTHER and is_home:
        best_cat = PageCategory.ABOUT_COMPANY
        best_score = 2.0
        reasons[best_cat].append("Identified as homepage / company overview")
        signals[best_cat].append("home_fallback")

    # Calculate normalized confidence [0.0 - 1.0]
    confidence = min(best_score / 8.0, 1.0) if best_cat != PageCategory.OTHER else 0.0

    return PageClassification(
        category=best_cat,
        confidence=confidence,
        reasons=reasons[best_cat] if best_cat != PageCategory.OTHER else ["No strong category signals found"],
        signals_matched=signals[best_cat] if best_cat != PageCategory.OTHER else [],
    )
