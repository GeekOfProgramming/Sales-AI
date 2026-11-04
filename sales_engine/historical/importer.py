"""
Historical Importer Module for SalesAI Phase 10.

Supports XLSX and CSV ingestion of historical outreach, contact, and company records.
Guarantees:
1. Strict idempotency (re-importing the same sheet/file does not duplicate companies or outreach records).
2. Multi-email cell normalization (splitting multiple emails into separate records, default status 'historical_unknown').
3. Actual respondent support (respondent_name, respondent_role, respondent_email, reply_at).
4. Previous outreach guard: if contacted in past, cold_outreach_allowed = False.
5. Active conversation guard: if active conversation detected, cold_outreach_allowed = False.
6. Preserves raw row, sheet, row number, notes, source URLs, missing values kept missing (no fabrication).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from enum import Enum
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import unicodedata
import urllib.parse
import ipaddress

try:
    import openpyxl
except ImportError:
    openpyxl = None

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryStatus,
    OpportunityType,
    RelationshipType,
)
from sales_engine.candidates.candidate_store import CandidateStore, normalize_company_name

logger = logging.getLogger(__name__)


class OperatorDecisionType(str, Enum):
    """
    Canonical row-level operator import decisions.
    Strictly unified across UI, Router, and Importer.
    """
    ACCEPT_DEFAULT = "ACCEPT_DEFAULT"
    SELECT_PRIMARY_HOST = "SELECT_PRIMARY_HOST"
    QUARANTINE_NO_PRIMARY = "QUARANTINE_NO_PRIMARY"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


EMAIL_SPLIT_REGEX = re.compile(r"[,;\n\r\t/|]+")
EMAIL_BASIC_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def normalize_persian_text(text: Any) -> str:
    """
    Normalizes Persian and Arabic text:
    - Normalizes Unicode (NFKC)
    - Strips Zero-Width Non-Joiner (ZWNJ \u200c), Zero-Width Joiner (\u200d), and soft hyphens
    - Normalizes Persian/Arabic 'ی' (Arabic Yeh \u064a / Persian Yeh \u06cc) -> \u06cc
    - Normalizes Persian/Arabic 'ک' (Arabic Kaf \u0643 / Persian Keheh \u06a9) -> \u06a9
    - Normalizes 'ه' / 'ة' -> 'ه'
    - Normalizes Arabic/Persian digits to standard ASCII digits
    - Collapses whitespace
    """
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    # Remove zero-width characters and invisible formatting
    s = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff\xad]", "", s)
    # Character substitutions
    trans = str.maketrans({
        "\u064a": "\u06cc",  # Arabic Yeh -> Persian Yeh
        "\u0649": "\u06cc",  # Alef Maksura -> Persian Yeh
        "\u0643": "\u06a9",  # Arabic Kaf -> Persian Keheh
        "\u0629": "\u0647",  # Teh Marbuta -> Heh
        "\u06c0": "\u0647",  # Heh with Yeh above -> Heh
        # Digits: Eastern Arabic & Persian to ASCII
        "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
        "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
        "\u06f0": "0", "\u06f1": "1", "\u06f2": "2", "\u06f3": "3", "\u06f4": "4",
        "\u06f5": "5", "\u06f6": "6", "\u06f7": "7", "\u06f8": "8", "\u06f9": "9",
    })
    s = s.translate(trans)
    # Strip and collapse internal spaces
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_bool(val: Any) -> bool:
    """
    Deterministically parses boolean values from spreadsheets and strings.
    Handles 'No', 'False', '0', 'sì', 'si', 'vero', 'falso' correctly.
    """
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in {"true", "yes", "y", "1", "si", "sì", "x", "vero", "v"}:
        return True
    if s in {"false", "no", "n", "0", "", "none", "null", "falso", "f"}:
        return False
    return False
def normalize_emails(email_cell: Optional[str]) -> List[str]:
    if not email_cell:
        return []
    chunks = EMAIL_SPLIT_REGEX.split(str(email_cell))
    clean_emails = []
    for ch in chunks:
        ch = ch.strip().strip("'\"<>").strip(" .\"'<>")
        if not ch:
            continue
        matches = EMAIL_BASIC_REGEX.findall(ch)
        for m in matches:
            m_clean = m.lower().strip().strip(" .\"'<>")
            if m_clean and m_clean not in clean_emails:
                clean_emails.append(m_clean)
    return clean_emails


LINK_CATEGORIES = [
    ("OFFICIAL_HOME", ["وبسایت رسمی", "وبسایت اصلی", "صفحه اصلی", "homepage", "official", "main website"]),
    ("PROJECTS", ["پروژه", "نمونه کار", "مراجع", "referenzen", "projekte", "progetti", "projects", "portfolio", "werke"]),
    ("SERVICES", ["خدمات", "leistungen", "servizi", "services", "competence"]),
    ("BIM_CAPABILITIES", ["bim", "revit", "archicad", "مدل سازی", "openbim"]),
    ("CAREERS", ["فرصت شغلی", "فرصتهای شغلی", "فرصت های شغلی", "استخدام", "شغل", "jobs", "karriere", "carriera", "careers", "lavora con noi"]),
    ("TEAM", ["تیم", "اعضا", "مدیران", "همکاران", "مؤسس", "mitarbeiter", "team", "persone", "chi siamo", "studio"]),
    ("ABOUT", ["درباره", "معرفی", "تاریخچه", "über uns", "uber uns", "unternehmen", "profil", "storia", "about", "impressum"]),
    ("CONTACT", ["تماس", "اطلاعات تماس", "دفاتر", "kontakt", "contatto", "contacts", "address", "standort"]),
    ("SOCIAL_PROFILE", ["linkedin", "instagram", "facebook", "twitter", "youtube", "behance", "vimeo"]),
    ("EXTERNAL_REFERENCE", ["google maps", "wikipedia", "kompass", "paginegialle"]),
    ("LANGUAGE_VARIANT", ["نسخه انگلیسی", "نسخه آلمانی", "نسخه ایتالیایی", "نسخه فرانسوی", "english", "deutsch", "italiano"]),
]

DISALLOWED_PRIMARY_DOMAINS = {
    "linkedin.com", "instagram.com", "facebook.com", "twitter.com", "x.com", "youtube.com",
    "behance.net", "vimeo.com", "google.com", "maps.google.com", "wikipedia.org", "kompass.com",
    "paginegialle.it", "indeed.com", "glassdoor.com", "stepstone.de"
}


def is_disallowed_or_unsafe_host(host: Optional[str]) -> bool:
    """
    Blocks social networks, job boards, private/reserved/loopback IP addresses,
    local hostnames, and unsafe network targets from becoming primary company domains
    or eligible targets for automated web-fetching.
    """
    if not host:
        return True
    h = str(host).lower().strip()
    if h.startswith("www."):
        h = h[4:]
    h = h.split(":")[0].rstrip(".")

    # Reject localhost / localdomain
    if h in ("localhost", "localhost.localdomain", "broadcasthost") or h.endswith(".local") or h.endswith(".internal"):
        return True

    # IP address safety checks (Loopback, private, link-local, reserved, non-global)
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast or not ip.is_global:
            return True
    except ValueError:
        pass

    # Reject disallowed domains and arbitrary subdomains (e.g. m.linkedin.com, api.indeed.com)
    for bad_dom in DISALLOWED_PRIMARY_DOMAINS:
        if h == bad_dom or h.endswith("." + bad_dom):
            return True

    # Must contain a valid domain dot with alphanumeric TLD (at least 2 chars)
    if "." not in h:
        return True

    return False


def classify_link(label: str, url: str) -> str:
    """Classifies a URL into a structured category based on its label and path."""
    lbl_low = (label or "").lower()
    u_low = (url or "").lower()

    # Prioritize domain/host-based social and external checks
    if any(s in u_low for s in ("linkedin.com", "instagram.com", "facebook.com", "twitter.com", "x.com", "youtube.com", "behance.net", "vimeo.com")):
        return "SOCIAL_PROFILE"
    if any(m in u_low for m in ("google.com/maps", "maps.google.com", "wikipedia.org", "kompass.com", "paginegialle.it")):
        return "EXTERNAL_REFERENCE"

    for c_name, kw_list in LINK_CATEGORIES:
        if any(k in lbl_low for k in kw_list):
            return c_name
    for c_name, kw_list in LINK_CATEGORIES:
        if any(k in u_low for k in kw_list):
            return c_name
    return "OTHER"


def parse_multiline_website_cell(cell_val: Optional[str], company_name: str = "") -> Dict[str, Any]:
    """
    Deterministically parses official website cells that may contain:
    - Single URLs or bare domains
    - Multiline link directories
    - Markdown links [label](url) or malformed markdown [label]url
    - [link removed] placeholders
    - Multiple pages/subdomains under the same domain
    - External social/directory links

    Selects an authoritative primary domain and URL while preserving all subsidiary links.
    Returns:
    {
        'primary_domain': Optional[str],
        'primary_url': Optional[str],
        'primary_method': str,
        'links': List[Dict[str, Any]],
        'raw_cell': str,
        'placeholders_count': int,
        'is_multiline': bool,
        'multiple_plausible_domains': bool,
        'plausible_domains': List[str]
    }
    """
    raw_str = str(cell_val or "").strip()
    if not raw_str:
        return {
            "primary_domain": None,
            "primary_url": None,
            "primary_method": "NONE",
            "links": [],
            "raw_cell": "",
            "placeholders_count": 0,
            "is_multiline": False,
            "multiple_plausible_domains": False,
            "plausible_domains": [],
        }

    lines = [l.strip() for l in raw_str.split("\n") if l.strip()]
    is_multiline = len(lines) > 1
    extracted_links: List[Dict[str, Any]] = []
    placeholders_count = 0

    for line in lines:
        if "[link removed]" in line.lower() or "[link_removed]" in line.lower():
            placeholders_count += 1
            # Try to recover label from prefix: label: [link removed] or markdown [label]([link removed])
            lbl = ""
            if ": [link" in line.lower() or ":[link" in line.lower() or " - [link" in line.lower() or " — [link" in line.lower():
                prefix = re.split(r"[:—\-]\s*\[link", line, flags=re.I)[0].strip()
                if prefix and not prefix.lower().startswith("[link"):
                    lbl = prefix.strip("[] ")
            if not lbl:
                m_label = re.search(r"\[([^\]]+)\]\s*\(\s*\[link removed\]", line, re.I)
                if m_label:
                    lbl = m_label.group(1).strip()
            if not lbl:
                m_bracket = re.search(r"\[([^\]]+)\]", line)
                if m_bracket and "link removed" not in m_bracket.group(1).lower():
                    lbl = m_bracket.group(1).strip()
            if not lbl:
                lbl = "link removed"

            extracted_links.append({
                "raw_label": lbl,
                "raw_url": "[link removed]",
                "normalized_url": None,
                "host": None,
                "category": "UNRESOLVED",
                "is_placeholder": True,
                "matches_primary_host": False,
                "is_external": False,
                "availability": "UNAVAILABLE_PLACEHOLDER",
            })
            continue

        # 1. Standard or unclosed Markdown [label](url
        m_md = re.findall(r"\[([^\]]+)\]\((https?://[^\s\)]+|www\.[^\s\)]+)", line)
        if m_md:
            for lbl, u in m_md:
                extracted_links.append({
                    "raw_label": lbl.strip(),
                    "raw_url": u.strip(),
                    "is_placeholder": False,
                })
            continue

        # 2. Malformed markdown [label]url
        m_md2 = re.findall(r"\[([^\]]+)\]\s*(https?://[^\s\)]+|www\.[^\s\)]+)", line)
        if m_md2:
            for lbl, u in m_md2:
                extracted_links.append({
                    "raw_label": lbl.strip(),
                    "raw_url": u.strip(),
                    "is_placeholder": False,
                })
            continue

        # 3. Key-value label: url or label - url
        m_kv = re.match(r"^([^:—\-]+)[:—\-]\s*(https?://[^\s]+|www\.[^\s]+)", line)
        if m_kv and not m_kv.group(1).strip().lower().startswith(("http", "www", "[")):
            extracted_links.append({
                "raw_label": m_kv.group(1).strip(),
                "raw_url": m_kv.group(2).strip(),
                "is_placeholder": False,
            })
            continue

        # 4. Bare URLs in line
        m_urls = re.findall(r"(https?://[^\s\)\],]+|www\.[^\s\)\],]+)", line)
        if m_urls:
            for u in m_urls:
                extracted_links.append({
                    "raw_label": "",
                    "raw_url": u.strip(),
                    "is_placeholder": False,
                })
            continue

        # 5. Bare domain token in line (e.g., ener.it, stiferite.com)
        tokens = re.split(r"[\s,;]+", line)
        found_token = False
        for t in tokens:
            t_clean = t.strip().rstrip(".,;:/")
            if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}$", t_clean, re.I):
                if not any(bad in t_clean.lower() for bad in ("gmail.com", "yahoo.com", "link.removed", "link_removed")):
                    extracted_links.append({
                        "raw_label": "",
                        "raw_url": t_clean,
                        "is_placeholder": False,
                    })
                    found_token = True
                    break
        if not found_token:
            # Unparseable text line preserved as unresolved note
            extracted_links.append({
                "raw_label": line,
                "raw_url": "",
                "normalized_url": None,
                "host": None,
                "category": "UNRESOLVED",
                "is_placeholder": False,
                "matches_primary_host": False,
                "is_external": False,
                "availability": "UNRESOLVED_TEXT",
            })

    # Normalize URLs and compute hosts
    for link in extracted_links:
        if link.get("is_placeholder") or not link.get("raw_url"):
            continue
        u = link["raw_url"]
        if not u.startswith(("http://", "https://")):
            u_norm = "https://" + u
        else:
            u_norm = u
        link["normalized_url"] = u_norm
        try:
            parsed = urllib.parse.urlparse(u_norm)
            host = parsed.netloc.lower().split(":")[0]
            if host.startswith("www."):
                host = host[4:]
            link["host"] = host if ("." in host and not host.endswith(".")) else None
        except Exception:
            link["host"] = None

        link["category"] = classify_link(link["raw_label"], link.get("normalized_url") or "")
        link["availability"] = "VALID_URL" if link["host"] else "INVALID_URL"

    # Evaluate Candidate Primary Hosts
    valid_hosts = []
    for l in extracted_links:
        h = l.get("host")
        if h and h not in valid_hosts and not is_disallowed_or_unsafe_host(h):
            valid_hosts.append(h)

    multiple_plausible = len(valid_hosts) > 1

    # Select Primary Host and URL
    primary_host = None
    primary_url = None
    primary_method = "NONE"

    # Rule 1: Prefer explicitly labeled OFFICIAL_HOME
    for l in extracted_links:
        if l.get("category") == "OFFICIAL_HOME" and l.get("host") and not is_disallowed_or_unsafe_host(l["host"]):
            primary_host = l["host"]
            primary_url = l.get("normalized_url")
            primary_method = "EXPLICIT_OFFICIAL_LABEL"
            break

    # Rule 2: Standalone or first valid non-social, non-job host
    if not primary_host:
        for l in extracted_links:
            h = l.get("host")
            if h and not is_disallowed_or_unsafe_host(h) and l.get("category") not in ("SOCIAL_PROFILE", "EXTERNAL_REFERENCE", "CAREERS"):
                primary_host = h
                primary_url = l.get("normalized_url")
                primary_method = "FIRST_VALID_HOST"
                break

    # Fallback: any host
    if not primary_host and valid_hosts:
        primary_host = valid_hosts[0]
        primary_url = next((l.get("normalized_url") for l in extracted_links if l.get("host") == primary_host), None)
        primary_method = "FALLBACK_HOST"

    # Update relative host relationships
    for l in extracted_links:
        if l.get("host"):
            l["matches_primary_host"] = bool(primary_host and (l["host"] == primary_host or l["host"].endswith("." + primary_host)))
            l["is_external"] = not l["matches_primary_host"]

    return {
        "primary_domain": primary_host,
        "primary_url": primary_url,
        "primary_method": primary_method,
        "links": extracted_links,
        "raw_cell": raw_str,
        "placeholders_count": placeholders_count,
        "is_multiline": is_multiline,
        "multiple_plausible_domains": multiple_plausible,
        "plausible_domains": valid_hosts,
    }


def normalize_outreach_status(raw_status: Optional[str]) -> Dict[str, Any]:
    """
    Conservative explicit mapping of raw outreach status strings (Persian and English/Italian).
    Preserves raw status verbatim while separating:
    - historical_contact_status: 'uncontacted', 'contacted', 'replied', or 'unknown'
    - has_past_contact: bool (True only if confirmed contact was actually made)
    - reply_status: Optional[str] (None unless an actual reply occurred)
    - has_active_conversation: bool
    - is_dnc: bool
    - is_wrong_email: bool
    - follow_up_needed: bool
    """
    res = {
        "historical_contact_status": "unknown",
        "has_past_contact": False,
        "reply_status": None,
        "has_active_conversation": False,
        "is_dnc": False,
        "is_wrong_email": False,
        "follow_up_needed": False,
    }
    if not raw_status or not str(raw_status).strip():
        return res

    raw_clean = str(raw_status).strip()
    norm = normalize_persian_text(raw_clean)
    norm_slug = re.sub(r"[\s\-_]+", "_", norm)

    # 1. Initial review / Under evaluation (بررسی اولیه)
    # Must mean uncontacted / under initial review. Cold outreach is ALLOWED.
    if norm in ("بررسی اولیه", "بررسی اولیه.") or "بررسی_اولیه" in norm_slug or norm in ("initial review", "under review", "in revisione", "da verificare"):
        res["historical_contact_status"] = "uncontacted"
        res["has_past_contact"] = False
        res["reply_status"] = None
        return res

    # 2. Email sent / Contacted (ایمیل ارسال شد)
    # Represents sent/contacted, but NOT a reply.
    if norm in ("ایمیل ارسال شد", "ارسال شد", "ایمیل فرستاده شد") or "ایمیل_ارسال_شد" in norm_slug or norm in ("email sent", "sent", "inviata", "inviato", "contattato", "contacted"):
        res["historical_contact_status"] = "contacted"
        res["has_past_contact"] = True
        res["reply_status"] = None
        return res

    # 3. Interview / Invitation to cooperate (مصاحبه / دعوت به همکاری)
    # Preserves positive engagement, without inventing a confirmed scheduled meeting.
    if any(k in norm for k in ("مصاحبه", "دعوت به همکاری", "دعوت به مصاحبه")) or "مصاحبه" in norm_slug or norm in ("interview", "colloquio", "invitation to interview", "collaboration invite"):
        res["historical_contact_status"] = "replied"
        res["has_past_contact"] = True
        res["reply_status"] = "positive_interview"
        res["has_active_conversation"] = True
        return res

    # 4. Follow-up needed (پیگیری شود)
    # Indicates follow-up needed. Confirmed outreach attempted/contacted.
    if any(k in norm for k in ("پیگیری شود", "پیگیری مجدد", "باید پیگیری شود")) or "پیگیری_شود" in norm_slug or norm in ("follow up", "follow-up", "da ricontattare", "da seguire"):
        res["historical_contact_status"] = "contacted"
        res["has_past_contact"] = True
        res["reply_status"] = None
        res["follow_up_needed"] = True
        return res

    # 5. Invalid / Bounced email (ایمیل اشتباه بودش)
    # Identifies an invalid/bounced email.
    if any(k in norm for k in ("ایمیل اشتباه بودش", "ایمیل اشتباه بود", "ایمیل اشتباه", "ایمیل نامعتبر")) or "ایمیل_اشتباه" in norm_slug or norm in ("invalid email", "wrong email", "bounced", "bounce", "email errata"):
        res["historical_contact_status"] = "uncontacted"
        res["has_past_contact"] = False
        res["reply_status"] = None
        res["is_wrong_email"] = True
        return res

    # 6. DNC / Opt out (عدم تمایل / لغو)
    if any(k in norm for k in ("عدم تمایل", "لغو", "حذف", "پیگیری نشود")) or any(k in norm_slug for k in ("opt_out", "dnc", "do_not_contact", "non_contattare", "disiscritto", "cancella")):
        res["historical_contact_status"] = "dnc"
        res["has_past_contact"] = True
        res["is_dnc"] = True
        return res

    # 7. Explicit general meeting / conversation / interest
    if any(k in norm for k in ("جلسه", "مذاکره", "صحبت", "قرار", "تماس تلفنی", "علاقهمند", "علاقه مند")) or any(k in norm_slug for k in ("meeting", "conversation", "in_discussion", "call_scheduled", "interested", "positive")):
        res["historical_contact_status"] = "replied"
        res["has_past_contact"] = True
        res["reply_status"] = "conversation_active" if "interested" not in norm_slug else raw_clean
        res["has_active_conversation"] = True
        return res

    # Fallback: unknown status
    res["historical_contact_status"] = "unknown"
    return res


@dataclass
class MeetingRecord:
    company_name: str
    domain: Optional[str]
    status: Optional[str] = None
    sender: Optional[str] = None
    contact_date: Optional[str] = None
    follow_up_date: Optional[str] = None
    reply_date: Optional[str] = None
    follow_up_notes: Optional[str] = None
    origin_sheet: Optional[str] = None
    notes: Optional[str] = None
    emails: List[str] = field(default_factory=list)
    contact_name: Optional[str] = None
    phone_numbers: Optional[str] = None
    raw_location: Optional[str] = None
    business_topic: Optional[str] = None
    job_link: Optional[str] = None
    other_links: Optional[str] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)
    source_file: str = ""
    source_sheet: str = "Meeting"
    matched_candidate_id: Optional[str] = None
    matched_company_name: Optional[str] = None
    reconciliation_status: str = "unmatched"  # 'matched' | 'unmatched'

    def compute_hash(self) -> str:
        c_name = self.company_name.strip().lower() if self.company_name else ""
        c_dom = self.domain.strip().lower() if self.domain else ""
        c_date = self.contact_date or ""
        r_date = self.reply_date or ""
        f_date = self.follow_up_date or ""
        f_notes = (self.follow_up_notes or "").strip().lower()
        ident_str = f"meeting:{c_name}:{c_dom}:{c_date}:{r_date}:{f_date}:{f_notes}"
        return hashlib.sha256(ident_str.encode("utf-8")).hexdigest()[:16]


@dataclass
class SentEmailRecord:
    message_id: str
    thread_id: str
    sender_account: str
    sender_group: Optional[str]
    from_address: str
    to_address: str
    recipient_emails: List[str]
    cc: Optional[str] = None
    bcc: Optional[str] = None
    sent_date: Optional[str] = None
    sent_time: Optional[str] = None
    subject: Optional[str] = None
    email_body: Optional[str] = None
    body_truncated: str = "NO"
    gmail_link: Optional[str] = None
    source_file: str = ""
    source_sheet: str = "Sent_Emails"
    matched_candidate_id: Optional[str] = None
    matched_company_name: Optional[str] = None
    reconciliation_status: str = "unmatched"  # 'matched' | 'ambiguous' | 'unmatched'


@dataclass
class HistoricalRecord:
    source_file: str
    sheet_name: str
    row_number: int
    company_name: str
    domain: Optional[str]
    tier_hint: Optional[str]
    notes: Optional[str]
    source_url: Optional[str]
    contact_name: Optional[str]
    contact_role: Optional[str]
    country: Optional[str] = None
    city: Optional[str] = None
    emails: List[str] = field(default_factory=list)
    contact_date: Optional[str] = None
    follow_up_date: Optional[str] = None
    reply_date: Optional[str] = None
    reply_status: Optional[str] = None
    is_wrong_email: bool = False
    is_dnc: bool = False
    has_active_conversation: bool = False
    respondent_name: Optional[str] = None
    respondent_role: Optional[str] = None
    respondent_email: Optional[str] = None
    priority_reason: Optional[str] = None
    raw_location: Optional[str] = None
    phone_numbers: Optional[str] = None
    business_topic: Optional[str] = None
    job_link: Optional[str] = None
    other_links: Optional[str] = None
    sender: Optional[str] = None
    last_modified_date: Optional[str] = None
    follow_up_notes: Optional[str] = None
    raw_outreach_status: Optional[str] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)
    website_links: List[Dict[str, Any]] = field(default_factory=list)
    raw_website_cell: Optional[str] = None
    primary_website_info: Dict[str, Any] = field(default_factory=dict)
    import_hash: str = ""

    def compute_communication_hash(self) -> str:
        """
        Calculates a hash identifying actual communication events.
        Decoupled from mutable row attributes (notes, phone numbers, links, location).
        Ensures editing non-communication metadata in a spreadsheet does not create
        duplicate historical outreach events.
        """
        c_name = self.company_name.strip().lower() if self.company_name else ""
        c_dom = self.domain.strip().lower() if self.domain else ""
        emails_str = ",".join(sorted(self.emails))
        c_date = self.contact_date or ""
        r_date = self.reply_date or ""
        r_status = self.reply_status or ""
        resp_name = (self.respondent_name or "").strip().lower()
        resp_email = (self.respondent_email or "").strip().lower()
        p_sender = (self.sender or "").strip().lower()
        ident_str = f"comm:{c_name}:{c_dom}:{emails_str}:{c_date}:{r_date}:{r_status}:{resp_name}:{resp_email}:{p_sender}"
        return hashlib.sha256(ident_str.encode("utf-8")).hexdigest()[:16]

    def compute_hash(self) -> str:
        # Content-stable hash: depends on domain/name, outreach data, and mutable descriptive fields
        # Used for raw row revision identity in historical_imports.
        c_name = self.company_name.strip().lower() if self.company_name else ""
        c_dom = self.domain.strip().lower() if self.domain else ""
        emails_str = ",".join(sorted(self.emails))
        c_date = self.contact_date or ""
        f_date = self.follow_up_date or ""
        r_date = self.reply_date or ""
        r_status = self.reply_status or ""
        resp_name = (self.respondent_name or "").strip().lower()
        resp_email = (self.respondent_email or "").strip().lower()
        resp_role = (self.respondent_role or "").strip().lower()
        dnc_flag = "1" if self.is_dnc else "0"
        act_flag = "1" if self.has_active_conversation else "0"
        wrong_flag = "1" if self.is_wrong_email else "0"
        p_sender = (self.sender or "").strip().lower()
        # Expanded mutable descriptive fields
        c_notes = (self.notes or "").strip().lower()
        c_phones = (self.phone_numbers or "").strip().lower()
        c_topic = (self.business_topic or "").strip().lower()
        c_job = (self.job_link or "").strip().lower()
        c_other = (self.other_links or "").strip().lower()
        c_fnotes = (self.follow_up_notes or "").strip().lower()
        c_rawloc = (self.raw_location or "").strip().lower()
        ident_str = (
            f"{c_name}:{c_dom}:{emails_str}:{c_date}:{f_date}:{r_date}:{r_status}:"
            f"{resp_name}:{resp_email}:{resp_role}:{dnc_flag}:{act_flag}:{wrong_flag}:{p_sender}:"
            f"{c_notes}:{c_phones}:{c_topic}:{c_job}:{c_other}:{c_fnotes}:{c_rawloc}"
        )
        return hashlib.sha256(ident_str.encode("utf-8")).hexdigest()[:16]



@dataclass
class HistoricalImportResult:
    total_rows_read: int = 0
    records_parsed: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    contacts_extracted: int = 0
    duplicates_skipped: int = 0
    sent_emails_total: int = 0
    sent_emails_imported: int = 0
    sent_emails_matched: int = 0
    sent_emails_unmatched: int = 0
    sent_emails_duplicates: int = 0
    errors: List[str] = field(default_factory=list)


class HistoricalImporter:
    """
    Importer for historical CSV/XLSX data with strict idempotency and outreach guards.
    """

    def __init__(self, store: Optional[CandidateStore] = None, file_path: Optional[str] = None):
        self.store = store or CandidateStore()
        self.file_path = file_path
        self._init_tables()

    def _init_tables(self) -> None:
        with self.store._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_imports (
                    import_hash TEXT PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    sheet_name TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    canonical_domain TEXT,
                    company_name TEXT NOT NULL,
                    emails_json TEXT NOT NULL,
                    has_outreach INTEGER NOT NULL DEFAULT 0,
                    has_reply INTEGER NOT NULL DEFAULT 0,
                    is_active_conversation INTEGER NOT NULL DEFAULT 0,
                    is_dnc INTEGER NOT NULL DEFAULT 0,
                    raw_row_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_hash TEXT NOT NULL,
                    canonical_domain TEXT,
                    contact_name TEXT,
                    contact_role TEXT,
                    email TEXT NOT NULL,
                    email_status TEXT NOT NULL DEFAULT 'historical_unknown',
                    is_respondent INTEGER NOT NULL DEFAULT 0,
                    reply_at TEXT,
                    FOREIGN KEY (import_hash) REFERENCES historical_imports(import_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_company_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_hash TEXT NOT NULL,
                    canonical_domain TEXT,
                    source_sheet TEXT NOT NULL,
                    source_row INTEGER NOT NULL,
                    raw_label TEXT,
                    raw_url TEXT,
                    normalized_url TEXT,
                    host TEXT,
                    category TEXT NOT NULL DEFAULT 'OTHER',
                    is_placeholder INTEGER NOT NULL DEFAULT 0,
                    matches_primary_host INTEGER NOT NULL DEFAULT 0,
                    is_external INTEGER NOT NULL DEFAULT 0,
                    availability TEXT NOT NULL DEFAULT 'VALID_URL',
                    FOREIGN KEY (import_hash) REFERENCES historical_imports(import_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_links_unique
                ON historical_company_links (import_hash, raw_url, raw_label)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_import_decisions (
                    decision_id TEXT PRIMARY KEY,
                    source_file_hash TEXT NOT NULL,
                    sheet_name TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    company_name TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    selected_host TEXT,
                    actor TEXT NOT NULL DEFAULT 'local_operator',
                    decided_at TEXT NOT NULL,
                    notes TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_decisions_row
                ON operator_import_decisions (source_file_hash, sheet_name, row_number)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_sent_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    sender_account TEXT NOT NULL,
                    sender_group TEXT,
                    from_address TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    recipient_emails_json TEXT NOT NULL,
                    cc TEXT,
                    bcc TEXT,
                    sent_date TEXT,
                    sent_time TEXT,
                    subject TEXT,
                    email_body TEXT,
                    body_truncated TEXT DEFAULT 'NO',
                    gmail_link TEXT,
                    source_file TEXT NOT NULL,
                    source_sheet TEXT NOT NULL,
                    matched_candidate_id TEXT,
                    matched_company_name TEXT,
                    reconciliation_status TEXT NOT NULL DEFAULT 'unmatched',
                    imported_at TEXT NOT NULL,
                    UNIQUE(sender_account, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hist_sent_emails_matched_cid
                ON historical_sent_emails(matched_candidate_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hist_sent_emails_status
                ON historical_sent_emails(reconciliation_status)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_meeting_activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_hash TEXT NOT NULL UNIQUE,
                    company_name TEXT NOT NULL,
                    canonical_domain TEXT,
                    status TEXT,
                    sender TEXT,
                    contact_date TEXT,
                    follow_up_date TEXT,
                    reply_date TEXT,
                    follow_up_notes TEXT,
                    origin_sheet TEXT,
                    notes TEXT,
                    matched_candidate_id TEXT,
                    matched_company_name TEXT,
                    source_file TEXT NOT NULL,
                    source_sheet TEXT NOT NULL DEFAULT 'Meeting',
                    raw_row_json TEXT,
                    imported_at TEXT NOT NULL
                )
                """
            )
            # Migration check: ensure raw_row_json column exists if table pre-existed
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(historical_meeting_activities);").fetchall()]
                if "raw_row_json" not in cols:
                    conn.execute("ALTER TABLE historical_meeting_activities ADD COLUMN raw_row_json TEXT;")
            except Exception:
                pass
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hist_meeting_matched_cid
                ON historical_meeting_activities(matched_candidate_id)
                """
            )

    @staticmethod
    def normalize_emails(email_cell: Optional[str]) -> List[str]:
        if not email_cell:
            return []
        chunks = EMAIL_SPLIT_REGEX.split(str(email_cell))
        clean_emails = []
        for ch in chunks:
            ch = ch.strip().strip("'\"<>")
            if not ch:
                continue
            matches = EMAIL_BASIC_REGEX.findall(ch)
            for m in matches:
                m_clean = m.lower().strip()
                if m_clean and m_clean not in clean_emails:
                    clean_emails.append(m_clean)
        return clean_emails

    def inspect_workbook(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Inspects an XLSX workbook without mutating anything.
        Returns sheet names, row counts, and detected column mapping report.
        """
        if openpyxl is None:
            raise RuntimeError("openpyxl is required to inspect XLSX files")
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheets_report = []
        total_rows = 0

        canonical_fields = {
            "company_name": ["company", "company_name", "company name", "nome azienda", "azienda", "نام و عنوان", "نام شرکت", "عنوان شرکت", "نام"],
            "domain": ["domain", "website", "url", "sito", "sito web", "وبسایت رسمی", "وبسایت", "سایت", "آدرس سایت"],
            "country": ["country", "paese", "nazione", "stato", "کشور"],
            "city": ["city", "città", "citta", "comune", "sede", "location", "شهر"],
            "raw_location": ["موقعیت جغرافیایی", "موقعیت", "مکان"],
            "tier_hint": ["tier", "company_tier", "category", "fascia", "سطح شرکت a b c", "سطح شرکت", "سطح"],
            "priority_reason": ["دلیل الویت", "دلیل اولویت", "اولویت"],
            "notes": ["notes", "note", "comments", "توضیحات و یادداشتها", "توضیحات", "یادداشتها", "یادداشت"],
            "contact_name": ["contact_name", "contact", "referente", "nome", "شخص مسئول / رابط", "شخص مسئول", "رابط", "مخاطب"],
            "contact_role": ["role", "title", "ruolo", "سمت", "عنوان شغلی"],
            "phone_numbers": ["شمارههای تماس", "شماره های تماس", "تلفن", "شماره تماس", "شماره تلفن"],
            "business_topic": ["موضوع کاری", "زمینه فعالیت", "موضوع فعالیت"],
            "job_link": ["لینک آگهی کار", "لینک اگهی کار", "لینک استخدام", "آگهی استخدام"],
            "other_links": ["سایر لینکها و راههای ارتباطی", "سایر لینک ها", "لینک های ارتباطی", "لینکها"],
            "emails": ["email", "emails", "e-mail", "mail", "ایمیلهای ارتباطی", "ایمیل های ارتباطی", "ایمیل", "ایمیلها"],
            "outreach_status": ["وضعیت ارتباط", "وضعیت تماس", "وضعیت پیگیری"],
            "sender": ["ارسال کننده", "ارسالکننده", "فرستنده", "مسئول ارسال"],
            "last_modified_date": ["تاریخ آخرین تغییر", "تاریخ اخرین تغییر", "تاریخ تغییر"],
            "contact_date": ["contact_date", "data contatto", "date", "data", "تاریخ ارسال ایمیل", "تاریخ ارسال"],
            "follow_up_date": ["follow_up_date", "followup", "follow-up", "تاریخ پیگیری"],
            "reply_date": ["reply_date", "data risposta", "replied_at", "تاریخ پاسخدهی", "تاریخ پاسخ دهی", "تاریخ پاسخ"],
            "follow_up_notes": ["یادداشتهای پیگیری", "یادداشت های پیگیری", "نکات پیگیری"],
            "reply_status": ["reply_status", "status", "esito", "stato"],
            "dnc": ["dnc", "do_not_contact", "suppressed", "opt_out"],
            "active_conversation": ["active_conversation", "in_discussion"],
            "meeting": ["meeting", "riunione", "appuntamento"],
            "row_id": ["ردیف", "شماره"],
        }

        for sname in wb.sheetnames:
            ws = wb[sname]
            first_row = []
            row_count = 0
            for r_idx, r_vals in enumerate(ws.iter_rows(values_only=True)):
                if r_idx == 0:
                    first_row = [str(v or "").strip() for v in r_vals if v is not None]
                else:
                    if any(r_vals):
                        row_count += 1

            total_rows += row_count
            mapped = {}
            for h in first_row:
                norm_h = normalize_persian_text(h)
                clean_h_norm = re.sub(r'[\s\-_]+', '_', norm_h)
                matched_field = None
                for canon_f, aliases in canonical_fields.items():
                    norm_aliases = [normalize_persian_text(a) for a in aliases]
                    norm_aliases_slug = [re.sub(r'[\s\-_]+', '_', a) for a in norm_aliases]
                    if norm_h in norm_aliases or clean_h_norm in norm_aliases_slug or norm_h in aliases:
                        matched_field = canon_f
                        break
                mapped[h] = matched_field or "unmapped"

            # Check if this sheet has company identifier
            has_company = any(canon == "company_name" for canon in mapped.values())
            has_domain = any(canon == "domain" for canon in mapped.values())
            sname_clean = sname.strip().lower()
            is_summary_sheet = sname_clean in ("dashboard", "summary", "overview", "riepilogo", "statistiche", "metrics", "meeting")
            is_communication_sheet = sname_clean in ("sent_emails", "sent", "sent emails", "outbox", "emails_sent")

            sheets_report.append({
                "sheet_name": sname,
                "row_count": row_count,
                "columns": first_row,
                "column_mappings": mapped,
                "has_company_column": has_company,
                "has_domain_column": has_domain,
                "is_summary_sheet": is_summary_sheet,
                "is_communication_sheet": is_communication_sheet,
                "mapping_valid": (has_company or has_domain) if (not is_summary_sheet and not is_communication_sheet) else True,
            })

        wb.close()

        # Parse records for detailed pre-commit acceptance and domain collision analysis
        parsed_records = []
        try:
            parsed_records = self.parse_xlsx(path)
        except Exception as e:
            logger.warning(f"inspect_workbook record parsing failed: {e}")

        # Collision & Status Analysis across all parsed company records
        domain_to_rows: Dict[str, List[Dict[str, Any]]] = {}
        status_distribution: Dict[str, int] = {}
        classification_counts = {
            "uncontacted": 0,
            "contacted": 0,
            "replied": 0,
            "dnc": 0,
            "unknown": 0,
            "positive_interviews": 0,
            "follow_up_needed": 0,
            "invalid_emails": 0,
        }
        populated_contact_name_count = 0
        missing_domain_count = 0
        missing_email_count = 0
        multiline_cells_count = 0
        total_extracted_links = 0
        placeholders_count = 0
        multiple_plausible_domains_count = 0
        valid_website_rows_count = 0

        ambiguous_rows = []
        missing_domain_rows = []

        for r in parsed_records:
            # Contact name preservation check
            if r.contact_name and str(r.contact_name).strip():
                populated_contact_name_count += 1

            if not r.domain or not str(r.domain).strip():
                missing_domain_count += 1
                missing_domain_rows.append({
                    "sheet": r.sheet_name,
                    "row_number": r.row_number,
                    "company_name": r.company_name,
                    "country": r.country,
                    "city": r.city,
                    "raw_website_cell": r.raw_website_cell,
                    "placeholders_count": getattr(r, "primary_website_info", {}).get("placeholders_count", 0),
                    "website_links": getattr(r, "website_links", []),
                })
            else:
                valid_website_rows_count += 1

            if not r.emails:
                missing_email_count += 1

            # Multi-link website stats
            web_info = getattr(r, "primary_website_info", {})
            links = getattr(r, "website_links", [])
            total_extracted_links += len(links)
            if web_info.get("is_multiline"):
                multiline_cells_count += 1
            placeholders_count += web_info.get("placeholders_count", 0)
            if web_info.get("multiple_plausible_domains"):
                multiple_plausible_domains_count += 1
                ambiguous_rows.append({
                    "sheet": r.sheet_name,
                    "row_number": r.row_number,
                    "company_name": r.company_name,
                    "country": r.country,
                    "city": r.city,
                    "selected_primary_host": r.domain,
                    "candidate_hosts": web_info.get("plausible_domains", []),
                    "raw_website_cell": r.raw_website_cell,
                    "website_links": getattr(r, "website_links", []),
                })

            # Domain collision tracking
            canon_dom = self.store.canonicalize_domain(r.domain) if r.domain else None
            if canon_dom:
                if canon_dom not in domain_to_rows:
                    domain_to_rows[canon_dom] = []
                domain_to_rows[canon_dom].append({
                    "sheet": r.sheet_name,
                    "row_number": r.row_number,
                    "company_name": r.company_name,
                    "country": r.country,
                    "city": r.city,
                    "raw_outreach_status": r.raw_outreach_status,
                })

            # Raw status distribution
            raw_st = r.raw_outreach_status or "(blank)"
            status_distribution[raw_st] = status_distribution.get(raw_st, 0) + 1

            # Conservative normalized classification
            norm_st = normalize_outreach_status(r.raw_outreach_status)
            c_status = norm_st["historical_contact_status"]
            if c_status in classification_counts:
                classification_counts[c_status] += 1
            else:
                classification_counts["unknown"] += 1

            if norm_st["reply_status"] == "positive_interview":
                classification_counts["positive_interviews"] += 1
            if norm_st["follow_up_needed"]:
                classification_counts["follow_up_needed"] += 1
            if norm_st["is_wrong_email"]:
                classification_counts["invalid_emails"] += 1

        # Identify collisions
        domain_collisions = []
        for dom, rows in domain_to_rows.items():
            if len(rows) > 1:
                # Distinguish verified duplicate vs separate branches/cities
                cities = {row["city"] for row in rows if row["city"]}
                names = {row["company_name"].strip().lower() for row in rows if row["company_name"]}
                is_exact_same_name = len(names) == 1
                is_different_city = len(cities) > 1
                collision_type = "verified_duplicate" if (is_exact_same_name and not is_different_city) else ("possible_duplicate" if is_exact_same_name else "separate_entities_or_branches")
                domain_collisions.append({
                    "domain": dom,
                    "occurrence_count": len(rows),
                    "collision_type": collision_type,
                    "rows": rows,
                    "requires_human_review": True,
                })

        return {
            "file_name": path.name,
            "total_sheets": len(sheets_report),
            "total_rows": total_rows,
            "sheets": sheets_report,
            "company_records_count": len(parsed_records),
            "distinct_canonical_domains": len(domain_to_rows),
            "domain_collisions_count": len(domain_collisions),
            "domain_collisions": domain_collisions,
            "status_distribution": status_distribution,
            "classification_counts": classification_counts,
            "populated_contact_name_count": populated_contact_name_count,
            "missing_domain_count": missing_domain_count,
            "valid_website_rows_count": valid_website_rows_count,
            "missing_email_count": missing_email_count,
            "multiline_cells_count": multiline_cells_count,
            "total_extracted_links": total_extracted_links,
            "placeholders_count": placeholders_count,
            "multiple_plausible_domains_count": multiple_plausible_domains_count,
            "ambiguous_rows": ambiguous_rows,
            "missing_domain_rows": missing_domain_rows,
        }

    def parse_csv(self, file_path: Union[str, Path]) -> List[HistoricalRecord]:
        path = Path(file_path)
        records = []
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):  # row 1 is header
                rec = self._row_to_record(row, source_file=path.name, sheet_name="default", row_num=idx)
                if rec:
                    records.append(rec)
        return records

    def parse_xlsx(self, file_path: Union[str, Path], sheet_name: Optional[str] = None) -> List[HistoricalRecord]:
        if openpyxl is None:
            raise RuntimeError("openpyxl is required to parse XLSX files")
        path = Path(file_path)
        records = []
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, data_only=True)
        # Skip Dashboard, Meeting, Summary, Overview, and Sent_Emails communication sheets from importing as companies
        skip_sheet_names = {
            "dashboard", "meeting", "summary", "overview", "riepilogo", "statistiche", "metrics",
            "sent_emails", "sent", "sent emails", "outbox", "emails_sent",
        }
        target_sheets = [sheet_name] if sheet_name and sheet_name in wb.sheetnames else [
            s for s in wb.sheetnames if s.strip().lower() not in skip_sheet_names
        ]

        for s_name in target_sheets:
            ws = wb[s_name]
            header = []
            for cell in ws[1]:
                header.append(str(cell.value or "").strip())

            for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if not any(row_cells):
                    continue
                # Skip total, formula, or summary rows
                first_non_empty = next((str(c).strip() for c in row_cells if c is not None and str(c).strip()), "")
                if first_non_empty.lower().startswith(("totale", "total", "somma", "sum", "average", "media", "=")):
                    continue
                row_dict = {}
                for h, val in zip(header, row_cells):
                    if h:
                        row_dict[h] = val
                rec = self._row_to_record(row_dict, source_file=path.name, sheet_name=s_name, row_num=row_idx)
                if rec:
                    records.append(rec)
        wb.close()
        return records

    def is_lead_research_workbook(self, file_path: Union[str, Path]) -> bool:
        """
        Detects if an Excel file matches the 13-column pyBIM Lead Research format
        based on exact or normalized header content.
        """
        if openpyxl is None:
            return False
        path = Path(file_path)
        if not path.exists() or path.suffix.lower() not in (".xlsx", ".xlsm"):
            return False
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
            for sname in wb.sheetnames:
                ws = wb[sname]
                headers = [normalize_persian_text(c.value) for c in ws[1] if c.value is not None]
                has_tier = any("سطح شرکت" in h for h in headers)
                has_reason = any("دلیل" in h for h in headers)
                has_job = any("آگهی کار" in h or "اگهی کار" in h for h in headers)
                if has_tier and has_reason and has_job:
                    wb.close()
                    return True
            wb.close()
        except Exception:
            return False
        return False

    def preview_lead_research_import(
        self,
        records: List[HistoricalRecord],
        excl_store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Analyzes 13-column Lead Research records against existing Candidates, Exclusions, and Research.
        Returns match counts, proposals, and previews with ZERO mutations.
        """
        from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore

        if excl_store is None:
            excl_store = ExcludedCompaniesStore(self.store.db_path)

        rows_parsed = len(records)
        new_proposals = 0
        new_candidates_with_domain = 0
        new_candidates_without_domain = 0
        existing_matches = 0
        ambiguous_unresolved = 0
        excluded_dnc = 0
        skipped_invalid = 0
        proposed_research_updates = 0

        # Query existing verified contacts and operator-reviewed records
        reviewed_cids = set()
        with self.store._get_connection() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lead_register_records' LIMIT 1").fetchone():
                reviewed_cids = set(
                    r[0] for r in conn.execute(
                        "SELECT candidate_id FROM lead_register_records WHERE verification_status = 'OPERATOR_REVIEWED'"
                    ).fetchall()
                )

        proposals_detail = []
        for r in records:
            cname = (r.company_name or "").strip()
            raw_dom = r.domain
            dom = self.store.canonicalize_domain(raw_dom) if raw_dom else None

            # 1. Genuinely ambiguous identity check
            if not cname or len(cname) < 2 or cname.lower() in ("none", "null", "unknown", "تأیید نشد", "#n/a"):
                ambiguous_unresolved += 1
                proposals_detail.append({
                    "company_name": cname,
                    "domain": dom,
                    "status": "AMBIGUOUS_UNRESOLVED",
                    "reason": "Missing or invalid company name identity; quarantined for operator review",
                })
                continue

            # 2. Respect Excluded Companies Registry
            if excl_store.is_company_excluded(company_domain=dom, company_name=cname):
                excluded_dnc += 1
                proposals_detail.append({
                    "company_name": cname,
                    "domain": dom,
                    "status": "EXCLUDED",
                    "reason": "Active exclusion in Excluded Companies Registry",
                })
                continue

            # 3. Match candidate:
            # - First by verified canonical domain if present
            # - Second by exact normalized company name
            existing = None
            if dom:
                existing = self.store.get_by_domain(dom)
            if not existing:
                existing = self.store.get_by_name(cname, country=r.country)

            if existing:
                cid = existing.candidate_id
                existing_matches += 1
                is_reviewed = cid in reviewed_cids
                if not is_reviewed:
                    proposed_research_updates += 1
                proposals_detail.append({
                    "company_name": cname,
                    "candidate_id": cid,
                    "domain": dom,
                    "status": "EXISTING_MATCH",
                    "operator_reviewed": is_reviewed,
                })
            else:
                cid = self.store.generate_company_id(cname, dom, r.country or "it")
                new_proposals += 1
                if dom:
                    new_candidates_with_domain += 1
                else:
                    new_candidates_without_domain += 1
                proposed_research_updates += 1
                proposals_detail.append({
                    "company_name": cname,
                    "candidate_id": cid,
                    "domain": dom,
                    "status": "NEW_PROPOSAL",
                    "has_verified_domain": bool(dom),
                })

        return {
            "rows_parsed": rows_parsed,
            "new_candidate_proposals": new_proposals,
            "new_candidates_with_domain": new_candidates_with_domain,
            "new_candidates_without_domain": new_candidates_without_domain,
            "existing_candidate_matches": existing_matches,
            "ambiguous_unresolved_count": ambiguous_unresolved,
            "excluded_dnc_count": excluded_dnc,
            "skipped_invalid_count": skipped_invalid,
            "proposed_research_updates": proposed_research_updates,
            "proposals_detail": proposals_detail,
        }

    def import_lead_research_records(
        self,
        records: List[HistoricalRecord],
        source_file_name: str = "pyBIM_Lead_Research.xlsx",
    ) -> HistoricalImportResult:
        """
        HV-02.6: Automatic Research Candidate Import.
        - Matches existing candidates using reliable canonical domain or exact company identity.
        - Creates new Candidates with Needs Research status when no reliable match exists.
        - Never invents or assigns an unverified domain (leaves company_domain=None).
        - Attaches all original research data as LeadRegisterRecord + DiscoveryObservation provenance.
        - Preserves operator-reviewed data, historical emails, DNC, and exclusions.
        - Separate from historical email import: ZERO fabricated outreach events.
        - Strictly idempotent.
        """
        from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore
        from sales_engine.candidates.models import (
            CandidateCompany,
            CompanyTier,
            DiscoveryStatus,
            QualificationOutcome,
            DiscoveryObservation,
        )
        from sales_engine.leads.register_store import LeadRegisterStore
        from sales_engine.leads.register_models import LeadRegisterRecord

        excl_store = ExcludedCompaniesStore(self.store.db_path)
        reg_store = LeadRegisterStore(self.store.db_path)
        reg_store._init_db()

        result = HistoricalImportResult(total_rows_read=len(records))
        now_iso = datetime.now(timezone.utc).isoformat()

        # Query operator-reviewed records and existing verified contacts
        reviewed_cids = set()
        with self.store._get_connection() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lead_register_records' LIMIT 1").fetchone():
                reviewed_cids = set(
                    r[0] for r in conn.execute(
                        "SELECT candidate_id FROM lead_register_records WHERE verification_status = 'OPERATOR_REVIEWED'"
                    ).fetchall()
                )

        for rec in records:
            result.records_parsed += 1
            cname = (rec.company_name or "").strip()
            raw_dom = rec.domain
            dom = self.store.canonicalize_domain(raw_dom) if raw_dom else None

            # 1. Genuinely ambiguous check
            if not cname or len(cname) < 2 or cname.lower() in ("none", "null", "unknown", "تأیید نشد", "#n/a"):
                result.duplicates_skipped += 1
                continue

            # 2. Check Excluded Companies Registry
            if excl_store.is_company_excluded(company_domain=dom, company_name=cname):
                result.duplicates_skipped += 1
                continue

            # 3. Match existing candidate or determine candidate_id
            existing = None
            if dom:
                existing = self.store.get_by_domain(dom)
            if not existing:
                existing = self.store.get_by_name(cname, country=rec.country)

            is_new = False
            if existing:
                cid = existing.candidate_id
                company = existing
                if dom and not company.company_domain:
                    company.company_domain = dom
                if rec.country and not company.country:
                    company.country = rec.country
                if rec.city and not company.city:
                    company.city = rec.city
                if cname not in company.aliases:
                    company.aliases.append(cname)
                if "research_lead_excel" not in company.source_refs:
                    company.source_refs.append("research_lead_excel")
                result.companies_updated += 1
            else:
                is_new = True
                cid = self.store.generate_company_id(cname, dom, rec.country or "it")
                tier_val = (
                    CompanyTier.tier_1 if rec.tier_hint == "A"
                    else (CompanyTier.tier_2 if rec.tier_hint == "B"
                          else (CompanyTier.tier_3 if rec.tier_hint == "C" else CompanyTier.unknown))
                )
                company = CandidateCompany(
                    candidate_id=cid,
                    company_name=cname,
                    normalized_company_name=normalize_company_name(cname),
                    company_domain=dom,  # Never invent domain: None if unverified
                    country=rec.country or "Italy",
                    city=rec.city,
                    company_tier=tier_val,
                    tier_evidence=[rec.priority_reason] if rec.priority_reason else [],
                    discovery_status=DiscoveryStatus.researching,  # Needs Research status
                    qualification_outcome=QualificationOutcome.research_more,
                    source_refs=["research_lead_excel"],
                    aliases=[cname],
                    domain_history=[dom] if dom else [],
                    first_seen_at=now_iso,
                    last_seen_at=now_iso,
                    historical_contact_status="uncontacted",
                    cold_outreach_allowed=True,
                )
                result.companies_created += 1

            self.store.save_candidate(company)

            # 4. Attach original research data as LeadRegisterRecord without overwriting OPERATOR_REVIEWED records
            if cid not in reviewed_cids:
                # Format emails string safely
                emails_str = ",".join(sorted(rec.emails)) if rec.emails else None
                phone_str = rec.phone_numbers if (rec.phone_numbers and "تأیید نشد" not in rec.phone_numbers) else None
                contact_str = rec.contact_name if (rec.contact_name and "تأیید نشد" not in rec.contact_name) else None
                job_link_str = rec.job_link if (rec.job_link and "موجود نیست" not in rec.job_link and "تأیید نشد" not in rec.job_link) else None
                other_links_str = rec.other_links if (rec.other_links and "تأیید نشد" not in rec.other_links) else None
                official_web_str = (f"https://{dom}" if dom else None) or rec.source_url

                reg_rec = LeadRegisterRecord(
                    candidate_id=cid,
                    priority_level=rec.tier_hint or "B",
                    priority_rationale=rec.priority_reason,
                    company_name=cname,
                    geographic_location=rec.raw_location or rec.city,
                    telephone_numbers=phone_str,
                    business_activities=rec.business_topic,
                    official_website=official_web_str,
                    responsible_person=contact_str,
                    descriptions_and_notes=rec.notes,
                    job_advertisement_links=job_link_str,
                    other_links_and_references=other_links_str,
                    contact_email_addresses=emails_str,
                    provenance_source="research_excel_import",
                    verification_status="Unverified",
                )
                reg_store.save_record(reg_rec)

            # 5. Attach original row payload as DiscoveryObservation for provenance
            obs_id = f"obs:research:{hashlib.sha256(f'{cid}:{rec.row_number}:{cname}'.encode()).hexdigest()[:16]}"
            observation = DiscoveryObservation(
                observation_id=obs_id,
                candidate_id=cid,
                source_id="research_lead_excel",
                source_name=source_file_name,
                source_type="excel_lead_research",
                observed_company_name=cname,
                observed_domain=dom,
                country=rec.country or "Italy",
                city=rec.city,
                observation_type="lead_research_row",
                raw_title=rec.business_topic,
                raw_snippet=rec.notes,
                raw_payload=rec.raw_row or {},
                confidence=1.0 if dom else 0.8,
            )
            self.store.add_observation(observation)

            # 6. Save unverified contacts if email was extracted (without verifying them)
            if rec.emails:
                for em in rec.emails:
                    clean_em = em.strip().lower()
                    if clean_em and "@" in clean_em and "تأیید نشد" not in clean_em:
                        contact_data = {
                            "contact_id": f"contact:{clean_em}",
                            "work_email": clean_em,
                            "full_name": rec.contact_name if (rec.contact_name and "تأیید نشد" not in rec.contact_name) else "Research Contact",
                            "job_title": rec.contact_role or rec.business_topic or "Unverified Contact",
                            "email_status": "unverified",
                            "email_confidence": 30,
                            "provider": "research_excel",
                        }
                        self.store.save_contact(contact_data, candidate_id=cid)
                        result.contacts_extracted += 1

        return result

    def parse_sent_emails(self, file_path: Union[str, Path], sheet_name: str = "Sent_Emails") -> List[SentEmailRecord]:
        """
        Parses Sent_Emails worksheet into SentEmailRecord objects.
        Preserves original fields, dates as supplied (no timezone fabrication), and body without modification.
        """
        if openpyxl is None:
            raise RuntimeError("openpyxl is required to parse XLSX files")
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, data_only=True)
        # Find sheet case-insensitively
        target_name = None
        for s in wb.sheetnames:
            if s.strip().lower() == sheet_name.strip().lower():
                target_name = s
                break

        if not target_name:
            wb.close()
            return []

        ws = wb[target_name]
        headers = []
        for cell in ws[1]:
            headers.append(str(cell.value or "").strip().lower())

        records = []
        email_regex = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

        for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row_cells):
                continue
            row_dict = {}
            for h, val in zip(headers, row_cells):
                if h:
                    row_dict[h] = val

            msg_id = str(row_dict.get("message id") or "").strip()
            if not msg_id:
                continue

            thread_id = str(row_dict.get("thread id") or "").strip()
            sender_account = str(row_dict.get("sender account") or "").strip().lower()
            sender_group = str(row_dict.get("sender group") or "").strip() or None
            from_addr = str(row_dict.get("from") or "").strip()
            to_addr = str(row_dict.get("to") or "").strip()
            raw_recips = str(row_dict.get("recipient emails") or "").strip()

            # Extract distinct normalized lowercase recipient emails
            found_recips = email_regex.findall(raw_recips) or email_regex.findall(to_addr)
            norm_recips = []
            seen = set()
            for r in found_recips:
                cl = r.strip().lower()
                if cl and cl not in seen:
                    seen.add(cl)
                    norm_recips.append(cl)

            # Dates and Times: preserve exactly as supplied (string or formatted), do not fabricate timezone
            sent_date_val = row_dict.get("sent date")
            sent_date = str(sent_date_val).strip() if sent_date_val is not None else None
            sent_time_val = row_dict.get("sent time")
            sent_time = str(sent_time_val).strip() if sent_time_val is not None else None

            cc_val = row_dict.get("cc")
            cc = str(cc_val).strip() if cc_val is not None else None
            bcc_val = row_dict.get("bcc")
            bcc = str(bcc_val).strip() if bcc_val is not None else None
            subj_val = row_dict.get("subject")
            subject = str(subj_val).strip() if subj_val is not None else None
            body_val = row_dict.get("email body")
            email_body = str(body_val) if body_val is not None else None
            trunc_val = row_dict.get("body truncated")
            body_truncated = str(trunc_val).strip().upper() if trunc_val is not None else "NO"
            gmail_link_val = row_dict.get("gmail link")
            gmail_link = str(gmail_link_val).strip() if gmail_link_val is not None else None

            records.append(
                SentEmailRecord(
                    message_id=msg_id,
                    thread_id=thread_id,
                    sender_account=sender_account,
                    sender_group=sender_group,
                    from_address=from_addr,
                    to_address=to_addr,
                    recipient_emails=norm_recips,
                    cc=cc,
                    bcc=bcc,
                    sent_date=sent_date,
                    sent_time=sent_time,
                    subject=subject,
                    email_body=email_body,
                    body_truncated=body_truncated,
                    gmail_link=gmail_link,
                    source_file=path.name,
                    source_sheet=target_name,
                )
            )

        wb.close()
        return records

    def parse_meeting_activities(self, file_path: Union[str, Path], sheet_name: str = "Meeting") -> List[MeetingRecord]:
        """
        Parses Meeting worksheet into MeetingRecord objects.
        Meeting sheet captures 5 company activity events with notes, follow-up notes, origin sheet, etc.
        """
        if openpyxl is None:
            raise RuntimeError("openpyxl is required to parse XLSX files")
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, data_only=True)
        target_name = None
        for s in wb.sheetnames:
            if s.strip().lower() == sheet_name.strip().lower():
                target_name = s
                break

        if not target_name:
            wb.close()
            return []

        ws = wb[target_name]
        headers = []
        for cell in ws[1]:
            headers.append(str(cell.value or "").strip())

        records = []
        for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row_cells):
                continue
            row_dict = {}
            for h, val in zip(headers, row_cells):
                if h:
                    row_dict[h] = val

            # Normalize lookup keys
            norm_dict = {}
            for k, v in row_dict.items():
                clean_k = str(k).strip()
                norm_dict[clean_k] = v
                norm_dict[clean_k.lower()] = v
                p_norm = normalize_persian_text(clean_k)
                norm_dict[p_norm] = v

            cname = (
                norm_dict.get(normalize_persian_text("نام و عنوان"))
                or norm_dict.get("نام و عنوان")
                or norm_dict.get("company")
                or norm_dict.get("company_name")
            )
            if not cname or not str(cname).strip():
                continue
            company_name = str(cname).strip()

            raw_web = (
                norm_dict.get(normalize_persian_text("وبسایت رسمی"))
                or norm_dict.get(normalize_persian_text("وب‌سایت رسمی"))
                or norm_dict.get("website")
                or norm_dict.get("domain")
            )
            web_info = parse_multiline_website_cell(raw_web, company_name=company_name)
            domain = web_info.get("primary_domain")

            status = norm_dict.get(normalize_persian_text("وضعیت ارتباط")) or norm_dict.get("status")
            sender = norm_dict.get(normalize_persian_text("ارسال کننده")) or norm_dict.get("sender")
            c_date = norm_dict.get(normalize_persian_text("تاریخ ارسال ایمیل")) or norm_dict.get("contact_date")
            f_date = norm_dict.get(normalize_persian_text("تاریخ پیگیری")) or norm_dict.get("follow_up_date")
            r_date = norm_dict.get(normalize_persian_text("تاریخ پاسخدهی")) or norm_dict.get("reply_date")
            f_notes = norm_dict.get(normalize_persian_text("یادداشتهای پیگیری")) or norm_dict.get("یادداشت‌های پیگیری") or norm_dict.get("follow_up_notes")
            origin = norm_dict.get(normalize_persian_text("شیت مبدا")) or norm_dict.get("شیت مبدأ") or norm_dict.get("origin_sheet")
            notes = norm_dict.get(normalize_persian_text("توضیحات و یادداشتها")) or norm_dict.get("توضیحات و یادداشت‌ها") or norm_dict.get("notes")
            c_name_person = norm_dict.get(normalize_persian_text("شخص مسئول / رابط")) or norm_dict.get("contact_name")
            phone = norm_dict.get(normalize_persian_text("شمارههای تماس")) or norm_dict.get("شماره‌های تماس") or norm_dict.get("phone_numbers")
            raw_loc = norm_dict.get(normalize_persian_text("موقعیت جغرافیایی")) or norm_dict.get("location")
            topic = norm_dict.get(normalize_persian_text("موضوع کاری")) or norm_dict.get("topic")
            job_link = norm_dict.get(normalize_persian_text("لینک آگهی کار")) or norm_dict.get("job_link")
            other_links = norm_dict.get(normalize_persian_text("سایر لینکها و راههای ارتباطی")) or norm_dict.get("سایر لینک‌ها و راه‌های ارتباطی") or norm_dict.get("other_links")
            raw_emails = norm_dict.get(normalize_persian_text("ایمیلهای ارتباطی")) or norm_dict.get("ایمیل‌های ارتباطی") or norm_dict.get("emails")
            emails = self.normalize_emails(str(raw_emails)) if raw_emails else []

            records.append(
                MeetingRecord(
                    company_name=company_name,
                    domain=domain,
                    status=str(status).strip() if status is not None else None,
                    sender=str(sender).strip() if sender is not None else None,
                    contact_date=str(c_date).strip() if c_date is not None else None,
                    follow_up_date=str(f_date).strip() if f_date is not None else None,
                    reply_date=str(r_date).strip() if r_date is not None else None,
                    follow_up_notes=str(f_notes).strip() if f_notes is not None else None,
                    origin_sheet=str(origin).strip() if origin is not None else None,
                    notes=str(notes).strip() if notes is not None else None,
                    emails=emails,
                    contact_name=str(c_name_person).strip() if c_name_person is not None else None,
                    phone_numbers=str(phone).strip() if phone is not None else None,
                    raw_location=str(raw_loc).strip() if raw_loc is not None else None,
                    business_topic=str(topic).strip() if topic is not None else None,
                    job_link=str(job_link).strip() if job_link is not None else None,
                    other_links=str(other_links).strip() if other_links is not None else None,
                    raw_row={k: str(v) for k, v in row_dict.items() if v is not None},
                    source_file=path.name,
                    source_sheet=target_name,
                )
            )

        wb.close()
        return records

    def _row_to_record(
        self, row: Dict[str, Any], source_file: str, sheet_name: str, row_num: int
    ) -> Optional[HistoricalRecord]:
        # Normalize column lookup with both standard and Persian normalizations
        norm_row = {}
        for k, v in row.items():
            if k is not None:
                clean_k = str(k).strip().lower()
                norm_row[clean_k] = v
                norm_row[re.sub(r'[\s\-_]+', '_', clean_k)] = v
                p_norm = normalize_persian_text(clean_k)
                norm_row[p_norm] = v
                norm_row[re.sub(r'[\s\-_]+', '_', p_norm)] = v

        # Company name: Persian 'نام و عنوان', 'نام شرکت'
        company_val = (
            norm_row.get("company")
            or norm_row.get("company_name")
            or norm_row.get("company name")
            or norm_row.get("nome azienda")
            or norm_row.get("azienda")
            or norm_row.get(normalize_persian_text("نام و عنوان"))
            or norm_row.get(normalize_persian_text("نام شرکت"))
            or norm_row.get(normalize_persian_text("عنوان شرکت"))
            or norm_row.get(normalize_persian_text("نام"))
        )
        if not company_val or not str(company_val).strip():
            return None
        company_name_str = str(company_val).strip()
        # Reject formulas, errors, or purely numeric row numbers accidentally placed in company name
        if company_name_str.startswith("=") or company_name_str.lower() in ("#n/a", "#ref!", "#value!", "none", "null"):
            return None
        company_name = company_name_str

        # Domain / Website: Persian 'وبسایت رسمی' (Supports structured multi-link parsing)
        raw_web_val = (
            norm_row.get("domain")
            or norm_row.get("website")
            or norm_row.get("url")
            or norm_row.get("sito")
            or norm_row.get("sito web")
            or norm_row.get(normalize_persian_text("وبسایت رسمی"))
            or norm_row.get(normalize_persian_text("وبسایت"))
            or norm_row.get(normalize_persian_text("سایت"))
            or norm_row.get(normalize_persian_text("آدرس سایت"))
        )
        web_info = parse_multiline_website_cell(raw_web_val, company_name=company_name)
        domain = web_info["primary_domain"]
        source_url_from_web = web_info["primary_url"]

        # Tier hint / category: Persian 'سطح شرکت A B C'
        raw_tier = (
            norm_row.get("tier")
            or norm_row.get("company_tier")
            or norm_row.get("category")
            or norm_row.get("fascia")
            or norm_row.get(normalize_persian_text("سطح شرکت A B C"))
            or norm_row.get(normalize_persian_text("سطح شرکت"))
            or norm_row.get(normalize_persian_text("سطح"))
        )
        tier_hint = str(raw_tier).strip() if raw_tier is not None and str(raw_tier).strip() else None

        # Priority reason: Persian 'دلیل الویت'
        priority_reason_val = (
            norm_row.get("priority_reason")
            or norm_row.get("priority_rationale")
            or norm_row.get(normalize_persian_text("دلیل الویت"))
            or norm_row.get(normalize_persian_text("دلیل اولویت"))
            or norm_row.get(normalize_persian_text("اولویت"))
        )
        priority_reason = str(priority_reason_val).strip() if priority_reason_val is not None and str(priority_reason_val).strip() else None

        # Raw location: Persian 'موقعیت جغرافیایی'
        raw_loc_val = (
            norm_row.get("raw_location")
            or norm_row.get("location")
            or norm_row.get(normalize_persian_text("موقعیت جغرافیایی"))
            or norm_row.get(normalize_persian_text("موقعیت"))
            or norm_row.get(normalize_persian_text("مکان"))
        )
        raw_location = str(raw_loc_val).strip() if raw_loc_val is not None and str(raw_loc_val).strip() else None

        # Phone numbers: Persian 'شمارههای تماس'
        phone_val = (
            norm_row.get("phone_numbers")
            or norm_row.get("phone")
            or norm_row.get(normalize_persian_text("شمارههای تماس"))
            or norm_row.get(normalize_persian_text("شماره های تماس"))
            or norm_row.get(normalize_persian_text("تلفن"))
            or norm_row.get(normalize_persian_text("شماره تماس"))
        )
        phone_numbers = str(phone_val).strip() if phone_val is not None and str(phone_val).strip() else None

        # Business topic: Persian 'موضوع کاری'
        topic_val = (
            norm_row.get("business_topic")
            or norm_row.get("topic")
            or norm_row.get(normalize_persian_text("موضوع کاری"))
            or norm_row.get(normalize_persian_text("زمینه فعالیت"))
            or norm_row.get(normalize_persian_text("موضوع فعالیت"))
        )
        business_topic = str(topic_val).strip() if topic_val is not None and str(topic_val).strip() else None

        # Contact person / liaison: Persian 'شخص مسئول / رابط'
        contact_name_val = (
            norm_row.get("contact_name")
            or norm_row.get("contact")
            or norm_row.get("referente")
            or norm_row.get("nome")
            or norm_row.get(normalize_persian_text("شخص مسئول / رابط"))
            or norm_row.get(normalize_persian_text("شخص مسئول"))
            or norm_row.get(normalize_persian_text("رابط"))
            or norm_row.get(normalize_persian_text("مخاطب"))
        )
        contact_name = str(contact_name_val).strip() if contact_name_val is not None and str(contact_name_val).strip() else None

        # Job posting link: Persian 'لینک آگهی کار'
        job_link_val = (
            norm_row.get("job_link")
            or norm_row.get(normalize_persian_text("لینک آگهی کار"))
            or norm_row.get(normalize_persian_text("لینک اگهی کار"))
            or norm_row.get(normalize_persian_text("لینک استخدام"))
        )
        job_link = str(job_link_val).strip() if job_link_val is not None and str(job_link_val).strip() else None

        # Other links: Persian 'سایر لینکها و راههای ارتباطی'
        other_links_val = (
            norm_row.get("other_links")
            or norm_row.get(normalize_persian_text("سایر لینکها و راههای ارتباطی"))
            or norm_row.get(normalize_persian_text("سایر لینک ها"))
            or norm_row.get(normalize_persian_text("راههای ارتباطی"))
        )
        other_links = str(other_links_val).strip() if other_links_val is not None and str(other_links_val).strip() else None

        # General notes: Persian 'توضیحات و یادداشتها'
        notes_val = (
            norm_row.get("notes")
            or norm_row.get("note")
            or norm_row.get("comments")
            or norm_row.get(normalize_persian_text("توضیحات و یادداشتها"))
            or norm_row.get(normalize_persian_text("توضیحات"))
            or norm_row.get(normalize_persian_text("یادداشتها"))
        )
        notes = str(notes_val).strip() if notes_val is not None and str(notes_val).strip() else None

        # Source URL
        source_url = norm_row.get("source_url") or norm_row.get("source")
        source_url = str(source_url).strip() if source_url else None

        contact_role = norm_row.get("role") or norm_row.get("title") or norm_row.get("ruolo")
        contact_role = str(contact_role).strip() if contact_role else None

        # Emails - split multi-email cell: Persian 'ایمیلهای ارتباطی'
        raw_email = (
            norm_row.get("email")
            or norm_row.get("emails")
            or norm_row.get("e-mail")
            or norm_row.get("mail")
            or norm_row.get(normalize_persian_text("ایمیلهای ارتباطی"))
            or norm_row.get(normalize_persian_text("ایمیل های ارتباطی"))
            or norm_row.get(normalize_persian_text("ایمیل"))
            or norm_row.get(normalize_persian_text("ایمیلها"))
        )
        emails = self.normalize_emails(str(raw_email)) if raw_email else []

        # Outreach status: Persian 'وضعیت ارتباط'
        raw_outreach_status_val = (
            norm_row.get("outreach_status")
            or norm_row.get(normalize_persian_text("وضعیت ارتباط"))
            or norm_row.get(normalize_persian_text("وضعیت تماس"))
            or norm_row.get(normalize_persian_text("وضعیت پیگیری"))
            or norm_row.get("reply_status")
            or norm_row.get("status")
            or norm_row.get("esito")
            or norm_row.get("stato")
        )
        raw_outreach_status = str(raw_outreach_status_val).strip() if raw_outreach_status_val is not None and str(raw_outreach_status_val).strip() else None

        # Normalize outreach status using conservative mapping
        norm_status = normalize_outreach_status(raw_outreach_status)

        # Sender: Persian 'ارسال کننده'
        sender_val = (
            norm_row.get("sender")
            or norm_row.get(normalize_persian_text("ارسال کننده"))
            or norm_row.get(normalize_persian_text("ارسالکننده"))
            or norm_row.get(normalize_persian_text("فرستنده"))
            or norm_row.get(normalize_persian_text("مسئول ارسال"))
        )
        sender = str(sender_val).strip() if sender_val is not None and str(sender_val).strip() else None

        # Last modified date: Persian 'تاریخ آخرین تغییر'
        last_mod_val = (
            norm_row.get("last_modified_date")
            or norm_row.get(normalize_persian_text("تاریخ آخرین تغییر"))
            or norm_row.get(normalize_persian_text("تاریخ اخرین تغییر"))
            or norm_row.get(normalize_persian_text("تاریخ تغییر"))
        )
        last_modified_date = str(last_mod_val).strip() if last_mod_val is not None and str(last_mod_val).strip() else None

        # Email send date: Persian 'تاریخ ارسال ایمیل'
        contact_date_val = (
            norm_row.get("contact_date")
            or norm_row.get("data contatto")
            or norm_row.get("date")
            or norm_row.get(normalize_persian_text("تاریخ ارسال ایمیل"))
            or norm_row.get(normalize_persian_text("تاریخ ارسال"))
        )
        contact_date = str(contact_date_val).strip() if contact_date_val is not None and str(contact_date_val).strip() else None

        # Follow up date: Persian 'تاریخ پیگیری'
        follow_up_val = (
            norm_row.get("follow_up_date")
            or norm_row.get("followup")
            or norm_row.get(normalize_persian_text("تاریخ پیگیری"))
        )
        follow_up_date = str(follow_up_val).strip() if follow_up_val is not None and str(follow_up_val).strip() else None

        # Reply date: Persian 'تاریخ پاسخدهی'
        reply_date_val = (
            norm_row.get("reply_date")
            or norm_row.get("data risposta")
            or norm_row.get("replied_at")
            or norm_row.get(normalize_persian_text("تاریخ پاسخدهی"))
            or norm_row.get(normalize_persian_text("تاریخ پاسخ دهی"))
            or norm_row.get(normalize_persian_text("تاریخ پاسخ"))
        )
        reply_date = str(reply_date_val).strip() if reply_date_val is not None and str(reply_date_val).strip() else None

        # Follow up notes: Persian 'یادداشتهای پیگیری'
        follow_up_notes_val = (
            norm_row.get("follow_up_notes")
            or norm_row.get(normalize_persian_text("یادداشتهای پیگیری"))
            or norm_row.get(normalize_persian_text("یادداشت های پیگیری"))
            or norm_row.get(normalize_persian_text("نکات پیگیری"))
        )
        follow_up_notes = str(follow_up_notes_val).strip() if follow_up_notes_val is not None and str(follow_up_notes_val).strip() else None

        # Normalized reply_status: None unless an actual reply occurred
        reply_status = norm_status["reply_status"]

        # Flags: combined from row indicators and normalized outreach status
        is_wrong_email = norm_status["is_wrong_email"] or parse_bool(norm_row.get("wrong_email") or norm_row.get("bounce") or norm_row.get("bounced"))
        is_dnc = norm_status["is_dnc"] or parse_bool(
            norm_row.get("dnc")
            or norm_row.get("do_not_contact")
            or norm_row.get("suppressed")
            or norm_row.get("opt_out")
        )
        has_active_conversation = norm_status["has_active_conversation"] or parse_bool(
            norm_row.get("active_conversation")
            or norm_row.get("in_discussion")
        )

        # Actual respondent
        resp_name = norm_row.get("respondent_name") or norm_row.get("rispondente")
        resp_name = str(resp_name).strip() if resp_name else None

        resp_role = norm_row.get("respondent_role") or norm_row.get("ruolo rispondente")
        resp_role = str(resp_role).strip() if resp_role else None

        resp_email = norm_row.get("respondent_email") or norm_row.get("email rispondente")
        resp_email = str(resp_email).strip().lower() if resp_email else None

        # Country & City parsing
        country = (
            norm_row.get("country")
            or norm_row.get("paese")
            or norm_row.get("nazione")
            or norm_row.get(normalize_persian_text("کشور"))
        )
        if not country and "stato" in norm_row:
            val = str(norm_row["stato"]).strip()
            # If "stato" is an outreach status (positive, opt_out, contattato, etc.), do not treat as country
            if val.lower() not in ("positive", "negative", "neutral", "replied", "opt_out", "dnc", "meeting", "contacted", "bounced"):
                country = val
        country = str(country).strip() if country else None

        city = (
            norm_row.get("city")
            or norm_row.get("città")
            or norm_row.get("citta")
            or norm_row.get("comune")
            or norm_row.get("sede")
            or norm_row.get(normalize_persian_text("شهر"))
        )
        city = str(city).strip() if city else None

        # If raw_location exists, try to extract city or country without fabricating
        if raw_location and not city:
            loc_norm = normalize_persian_text(raw_location).lower()
            for c_name, c_canon in [
                ("milan", "Milano"), ("milano", "Milano"), ("میلان", "Milano"),
                ("turin", "Torino"), ("torino", "Torino"), ("تورین", "Torino"),
                ("rome", "Roma"), ("roma", "Roma"), ("رم", "Roma"),
                ("bozen", "Bozen"), ("bolzano", "Bolzano"), ("بوزن", "Bozen"),
            ]:
                if c_name in loc_norm:
                    city = c_canon
                    break

        # Fallback city/country from geographic sheet name (Milano, Torino, Rome, Bozen, Italy, Austria, Germany, Switzerland)
        # BUT do NOT invent company cities for LinkedIn, Indeed, or country-level sheets!
        sname_lower = sheet_name.strip().lower()
        if not city:
            # Only assign city for explicit city sheets
            if "milan" in sname_lower or sname_lower == "milano":
                city = "Milano"
            elif "turin" in sname_lower or sname_lower == "torino":
                city = "Torino"
            elif "rome" in sname_lower or sname_lower == "roma":
                city = "Roma"
            elif "bozen" in sname_lower or sname_lower == "bolzano":
                city = "Bozen"

        if not country:
            if any(kc in sname_lower for kc in ["italy", "italia", "milan", "turin", "rome", "bozen", "bolzano"]):
                country = "Italy"
            elif "austria" in sname_lower or "österreich" in sname_lower:
                country = "Austria"
            elif "germany" in sname_lower or "deutschland" in sname_lower:
                country = "Germany"
            elif "switzerland" in sname_lower or "schweiz" in sname_lower or "suisse" in sname_lower:
                country = "Switzerland"
            elif "belgium" in sname_lower or "belgique" in sname_lower:
                country = "Belgium"
            elif "france" in sname_lower:
                country = "France"
            elif any(kc in sname_lower for kc in ["uk", "united kingdom", "london", "england"]):
                country = "United Kingdom"

        record = HistoricalRecord(
            source_file=source_file,
            sheet_name=sheet_name,
            row_number=row_num,
            company_name=company_name,
            domain=domain,
            tier_hint=tier_hint,
            notes=notes,
            source_url=source_url or source_url_from_web,
            contact_name=contact_name,
            contact_role=contact_role,
            country=country,
            city=city,
            emails=emails,
            contact_date=contact_date,
            follow_up_date=follow_up_date,
            reply_date=reply_date,
            reply_status=reply_status,
            is_wrong_email=is_wrong_email,
            is_dnc=is_dnc,
            has_active_conversation=has_active_conversation,
            respondent_name=resp_name,
            respondent_role=resp_role,
            respondent_email=resp_email,
            priority_reason=priority_reason,
            raw_location=raw_location,
            phone_numbers=phone_numbers,
            business_topic=business_topic,
            job_link=job_link,
            other_links=other_links,
            sender=sender,
            last_modified_date=last_modified_date,
            follow_up_notes=follow_up_notes,
            raw_outreach_status=raw_outreach_status,
            raw_row={k: str(v) for k, v in row.items() if v is not None},
            website_links=web_info.get("links", []),
            raw_website_cell=raw_web_val,
            primary_website_info=web_info,
        )
        record.import_hash = record.compute_hash()
        return record

    def import_records(
        self,
        records: List[HistoricalRecord],
        operator_decisions: Optional[List[Dict[str, Any]]] = None,
    ) -> HistoricalImportResult:
        result = HistoricalImportResult(total_rows_read=len(records))

        # Index operator decisions by (sheet_name, row_number)
        dec_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
        if operator_decisions:
            for d in operator_decisions:
                s_name = str(d.get("sheet_name") or "")
                r_num = int(d.get("row_number") or 0)
                dec_map[(s_name, r_num)] = d

        for rec in records:
            result.records_parsed += 1

            # Check if an explicit operator decision applies
            row_dec = dec_map.get((rec.sheet_name, rec.row_number))
            if row_dec:
                d_type = row_dec.get("decision_type")
                if d_type == OperatorDecisionType.ACCEPT_DEFAULT.value:
                    # Explicit operator approval of the default proposed host
                    # Ensure the host is validated against unsafe rules
                    if rec.domain and is_disallowed_or_unsafe_host(rec.domain):
                        rec.domain = None
                elif d_type == OperatorDecisionType.SELECT_PRIMARY_HOST.value:
                    chosen = row_dec.get("selected_host")
                    if chosen and not is_disallowed_or_unsafe_host(chosen):
                        rec.domain = chosen
                    else:
                        rec.domain = None
                elif d_type == OperatorDecisionType.QUARANTINE_NO_PRIMARY.value:
                    # Explicit quarantine: row is ingested with no trusted primary domain
                    rec.domain = None
                elif d_type == OperatorDecisionType.EXTERNAL_REFERENCE.value:
                    # Classify other URLs as external references only; no trusted primary domain
                    rec.domain = None
                else:
                    raise ValueError(f"Unsupported operator decision type '{d_type}' for row {rec.sheet_name} R{rec.row_number}")

            import_hash = rec.import_hash or rec.compute_hash()
            rec.import_hash = import_hash

            # Check idempotency: have we imported this exact row before?
            with self.store._get_connection() as conn:
                existing = conn.execute(
                    "SELECT import_hash FROM historical_imports WHERE import_hash = ?",
                    (import_hash,),
                ).fetchone()

            if existing:
                result.duplicates_skipped += 1
                continue

            # Process CandidateCompany in CandidateStore
            canon_domain = self.store.canonicalize_domain(rec.domain) if rec.domain else None
            company = None

            if canon_domain:
                company = self.store.get_by_domain(canon_domain)

            is_new = False
            if not company:
                company_id = self.store.generate_company_id(rec.company_name, canon_domain, rec.country)
                # Check by ID
                company = self.store.get_by_id(company_id)

            if not company:
                is_new = True
                company = CandidateCompany(
                    company_id=self.store.generate_company_id(rec.company_name, canon_domain, rec.country),
                    name=rec.company_name,
                    canonical_domain=canon_domain,
                    country=rec.country,
                    city=rec.city,
                    discovery_sources=["historical_seed"],
                    status=DiscoveryStatus.discovered,
                )
            else:
                if rec.country and not company.country:
                    company.country = rec.country
                if rec.city and not company.city:
                    company.city = rec.city

            # Preserve aliases & sources
            if rec.company_name not in company.aliases:
                company.aliases.append(rec.company_name)
            if "historical_seed" not in company.discovery_sources:
                company.discovery_sources.append("historical_seed")

            # PREVIOUS OUTREACH / ACTIVE CONVERSATION GUARD:
            # Separate raw outreach status from normalized contact status and normalized reply status.
            # Only factual send, reply, or other reliable contact evidence may establish previous outreach.
            norm_status = normalize_outreach_status(rec.raw_outreach_status)
            has_past_contact = bool(rec.contact_date or rec.reply_date or norm_status["has_past_contact"])

            if norm_status["historical_contact_status"] == "uncontacted":
                # Explicit uncontacted / under initial review (e.g. بررسی اولیه)
                # Keep cold outreach allowed unless independent contact date/reply exists!
                if not (rec.contact_date or rec.reply_date):
                    company.cold_outreach_allowed = True
                    company.historical_contact_status = "uncontacted"
                    company.previous_outreach_detected = False
                else:
                    company.cold_outreach_allowed = False
                    company.historical_contact_status = "contacted" if not rec.reply_date else "replied"
                    company.previous_outreach_detected = True
                    company.metadata["has_historical_outreach"] = True
            elif norm_status["historical_contact_status"] in ("contacted", "replied", "dnc") or has_past_contact:
                company.cold_outreach_allowed = False
                company.previous_outreach_detected = True
                company.metadata["has_historical_outreach"] = True
                if norm_status["historical_contact_status"] == "replied" or rec.reply_date:
                    company.historical_contact_status = "replied"
                elif norm_status["historical_contact_status"] == "dnc":
                    company.historical_contact_status = "dnc"
                else:
                    company.historical_contact_status = "contacted"

            if rec.contact_date:
                company.last_contacted_at = rec.contact_date
            if rec.reply_status:
                company.reply_status = rec.reply_status

            if rec.has_active_conversation or norm_status["has_active_conversation"]:
                company.cold_outreach_allowed = False
                company.active_conversation = True
                company.metadata["active_conversation"] = True

            if rec.is_dnc or norm_status["is_dnc"]:
                company.cold_outreach_allowed = False
                company.suppressed = True
                company.suppression_state["historical_dnc"] = True

            # Map tier hint to company_tier
            if rec.tier_hint:
                company.metadata["raw_tier"] = rec.tier_hint
                t_low = rec.tier_hint.strip().lower()
                if "a" in t_low or "1" in t_low:
                    company.company_tier = CompanyTier.tier_1
                elif "b" in t_low or "2" in t_low:
                    company.company_tier = CompanyTier.tier_2
                elif "c" in t_low or "3" in t_low:
                    company.company_tier = CompanyTier.tier_3

            if rec.priority_reason:
                company.metadata["priority_reason"] = rec.priority_reason
            if rec.follow_up_notes:
                company.metadata["follow_up_notes"] = rec.follow_up_notes
            if rec.sender:
                company.metadata["sender"] = rec.sender
            if rec.last_modified_date:
                company.metadata["last_modified_date"] = rec.last_modified_date
            if rec.business_topic:
                company.metadata["business_topic"] = rec.business_topic
            if rec.phone_numbers:
                company.metadata["phone_numbers"] = rec.phone_numbers
            if rec.job_link:
                company.metadata["job_link"] = rec.job_link
            if rec.other_links:
                company.metadata["other_links"] = rec.other_links

            # Observation creation (no fake jobs!)
            obs = DiscoveryObservation(
                observation_id=f"obs_hist_{import_hash}",
                company_id=company.company_id,
                observed_company_name=rec.company_name,
                country=rec.country,
                city=rec.city,
                source_name=f"historical:{rec.source_file}",
                discovery_mode="historical_seed",
                raw_payload=rec.raw_row,
                extracted_data={
                    "sheet": rec.sheet_name,
                    "row_number": rec.row_number,
                    "tier_hint": rec.tier_hint,
                    "priority_reason": rec.priority_reason,
                    "notes": rec.notes,
                    "follow_up_notes": rec.follow_up_notes,
                    "sender": rec.sender,
                    "last_modified_date": rec.last_modified_date,
                    "business_topic": rec.business_topic,
                    "phone_numbers": rec.phone_numbers,
                    "job_link": rec.job_link,
                    "other_links": rec.other_links,
                    "country": rec.country,
                    "city": rec.city,
                    "raw_location": rec.raw_location,
                    "contact_date": rec.contact_date,
                    "follow_up_date": rec.follow_up_date,
                    "reply_date": rec.reply_date,
                    "reply_status": rec.reply_status,
                    "emails_count": len(rec.emails),
                    "active_conversation": rec.has_active_conversation,
                    "raw_website_cell": rec.raw_website_cell,
                    "primary_website_info": rec.primary_website_info,
                    "website_links_count": len(getattr(rec, "website_links", [])),
                },
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            company.metadata["raw_website_cell"] = rec.raw_website_cell
            company.metadata["primary_website_info"] = rec.primary_website_info

            # Save company and observation
            saved_company = self.store.upsert_company(company, observation=obs)
            if is_new:
                result.companies_created += 1
            else:
                result.companies_updated += 1

            # Save to historical_imports table
            now_iso = datetime.now(timezone.utc).isoformat()
            with self.store._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO historical_imports (
                        import_hash, source_file, sheet_name, row_number, canonical_domain,
                        company_name, emails_json, has_outreach, has_reply,
                        is_active_conversation, is_dnc, raw_row_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_hash,
                        rec.source_file,
                        rec.sheet_name,
                        rec.row_number,
                        saved_company.canonical_domain,
                        rec.company_name,
                        json.dumps(rec.emails),
                        1 if has_past_contact else 0,
                        1 if rec.reply_date or rec.reply_status else 0,
                        1 if rec.has_active_conversation else 0,
                        1 if rec.is_dnc else 0,
                        json.dumps(rec.raw_row),
                        now_iso,
                    ),
                )

                # Contacts storage with deduplication
                for em in rec.emails:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO historical_contacts (
                            import_hash, canonical_domain, contact_name, contact_role,
                            email, email_status, is_respondent, reply_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                        """,
                        (
                            import_hash,
                            saved_company.canonical_domain,
                            rec.contact_name,
                            rec.contact_role,
                            em,
                            "historical_unknown",
                            rec.reply_date,
                        ),
                    )
                    result.contacts_extracted += 1

                # If there is an actual respondent differing or explicit
                if rec.respondent_email:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO historical_contacts (
                            import_hash, canonical_domain, contact_name, contact_role,
                            email, email_status, is_respondent, reply_at
                        ) VALUES (?, ?, ?, ?, ?, 'historical_unknown', 1, ?)
                        """,
                        (
                            import_hash,
                            saved_company.canonical_domain,
                            rec.respondent_name or rec.contact_name,
                            rec.respondent_role or "Respondent",
                            rec.respondent_email,
                            rec.reply_date,
                        ),
                    )
                    result.contacts_extracted += 1

                # Structured associated website references storage (historical_company_links)
                # When primary domain is cleared/quarantined, all child links are external and do not assume company-owned root
                is_quarantined = not bool(saved_company.canonical_domain)
                for lnk in getattr(rec, "website_links", []):
                    lnk_host = lnk.get("host")
                    if is_quarantined:
                        matches_pri = False
                        is_ext = True
                    else:
                        matches_pri = bool(lnk_host and saved_company.canonical_domain and (lnk_host == saved_company.canonical_domain or lnk_host.endswith("." + saved_company.canonical_domain)))
                        is_ext = not matches_pri

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO historical_company_links (
                            import_hash, canonical_domain, source_sheet, source_row,
                            raw_label, raw_url, normalized_url, host,
                            category, is_placeholder, matches_primary_host, is_external,
                            availability
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            import_hash,
                            saved_company.canonical_domain,
                            rec.sheet_name,
                            rec.row_number,
                            lnk.get("raw_label"),
                            lnk.get("raw_url"),
                            lnk.get("normalized_url"),
                            lnk_host,
                            lnk.get("category", "OTHER"),
                            1 if lnk.get("is_placeholder") else 0,
                            1 if matches_pri else 0,
                            1 if is_ext else 0,
                            lnk.get("availability", "VALID_URL"),
                        ),
                    )

            # Populate historical_outreach_events table in CandidateStore ONLY if genuine outreach evidence exists:
            # Genuine evidence: confirmed contact date, confirmed reply date, or recognized send/reply status.
            # Never record an outreach event from general notes, follow-up notes, or raw email presence alone!
            cand_id = getattr(saved_company, "candidate_id", getattr(saved_company, "company_id", None))
            has_genuine_event = bool(rec.contact_date or rec.reply_date or norm_status["has_past_contact"])
            if cand_id and has_genuine_event:
                comm_hash = rec.compute_communication_hash()
                self.store.record_historical_outreach(
                    candidate_id=cand_id,
                    source_ref=f"historical:{rec.source_file}",
                    contact_name=rec.contact_name,
                    contact_email=rec.emails[0] if rec.emails else None,
                    contacted_at=rec.contact_date,
                    channel="email",
                    reply_status=rec.reply_status,
                    reply_at=rec.reply_date,
                    respondent_name=rec.respondent_name,
                    respondent_role=rec.respondent_role,
                    respondent_email=rec.respondent_email,
                    notes=rec.notes,
                    raw_row_hash=comm_hash,
                    follow_up_at=rec.follow_up_date,
                    meeting_status="scheduled" if "meeting" in (rec.reply_status or "").lower() else None,
                    opportunity_status="active" if rec.has_active_conversation else None,
                )

            # Record operator decision provenance if one applied to this row
            if row_dec:
                with self.store._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO operator_import_decisions (
                            decision_id, source_file_hash, sheet_name, row_number,
                            company_name, decision_type, selected_host, actor, decided_at, notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"dec_{import_hash}",
                            row_dec.get("source_file_hash") or rec.source_file,
                            rec.sheet_name,
                            rec.row_number,
                            rec.company_name,
                            row_dec.get("decision_type", "CUSTOM"),
                            rec.domain,
                            row_dec.get("actor", "local_operator"),
                            datetime.now(timezone.utc).isoformat(),
                            row_dec.get("notes"),
                        ),
                    )

        return result

    def reconcile_sent_emails(
        self,
        sent_emails: List[SentEmailRecord],
        staged_records: Optional[List[HistoricalRecord]] = None,
    ) -> List[SentEmailRecord]:
        """
        Reconciles Sent_Emails messages against candidate companies in CandidateStore (and staged records if provided).
        Strict Safety Invariant:
        Matches ONLY by exact verified recipient address.
        Never matches by domain-only or name-similarity.
        Unmatched or ambiguous messages remain classified as 'unmatched' / 'ambiguous'.
        """
        reconciled = []
        cand_contacts_map: Dict[str, Set[str]] = {}
        cand_names: Dict[str, str] = {}

        with self.store._get_connection() as conn:
            # Candidate names and ID mapping
            name_rows = conn.execute("SELECT candidate_id, company_name FROM candidate_companies").fetchall()
            name_to_cid: Dict[str, str] = {}
            for r in name_rows:
                cid_str = str(r[0])
                cname = str(r[1] or "")
                cand_names[cid_str] = cname
                if cname:
                    name_to_cid[cname.strip().lower()] = cid_str

            # 1. candidate_contacts
            c_rows = conn.execute("SELECT work_email, candidate_id FROM candidate_contacts WHERE work_email IS NOT NULL").fetchall()
            for r in c_rows:
                em = (r[0] or "").strip().lower()
                cid = str(r[1] or "").strip()
                if em and cid:
                    cand_contacts_map.setdefault(em, set()).add(cid)

            # 2. historical_contacts & historical_imports
            h_rows = conn.execute(
                """
                SELECT hc.email, hi.canonical_domain, hi.company_name
                FROM historical_contacts hc
                JOIN historical_imports hi ON hc.import_hash = hi.import_hash
                WHERE hc.email IS NOT NULL
                """
            ).fetchall()
            for r in h_rows:
                em = (r[0] or "").strip().lower()
                dom = str(r[1] or "").strip().lower()
                cname = str(r[2] or "").strip().lower()
                if em:
                    if dom:
                        cand_contacts_map.setdefault(em, set()).add(f"cand:domain:{dom}")
                    elif cname and cname in name_to_cid:
                        cand_contacts_map.setdefault(em, set()).add(name_to_cid[cname])

        # 3. Incorporate staged company records if available (e.g. during pre-import inspection or import)
        if staged_records:
            for rec in staged_records:
                cid = None
                if rec.domain:
                    canon = self.store.canonicalize_domain(rec.domain)
                    if canon:
                        cid = f"cand:domain:{canon}"
                if not cid:
                    cname_clean = (rec.company_name or "").strip().lower()
                    cid = name_to_cid.get(cname_clean) or self.store.generate_company_id(rec.company_name, None, rec.country)
                if cid and rec.company_name:
                    cand_names.setdefault(cid, rec.company_name)
                for em in rec.emails:
                    em_clean = em.strip().lower()
                    if em_clean and cid:
                        cand_contacts_map.setdefault(em_clean, set()).add(cid)

        for m in sent_emails:
            # If the record has already been explicitly reconciled/matched, preserve it
            if m.matched_candidate_id and m.reconciliation_status == "matched":
                reconciled.append(m)
                continue

            matched_cids = set()
            for recip in m.recipient_emails:
                cids = cand_contacts_map.get(recip.lower(), set())
                matched_cids.update(cids)

            if len(matched_cids) == 1:
                cid = list(matched_cids)[0]
                m.matched_candidate_id = cid
                m.matched_company_name = cand_names.get(cid)
                m.reconciliation_status = "matched"
            elif len(matched_cids) > 1:
                m.matched_candidate_id = None
                m.matched_company_name = None
                m.reconciliation_status = "ambiguous"
            else:
                m.matched_candidate_id = None
                m.matched_company_name = None
                m.reconciliation_status = "unmatched"

            reconciled.append(m)

        return reconciled

    def import_sent_emails(
        self,
        sent_emails: List[SentEmailRecord],
    ) -> Dict[str, int]:
        """
        Persists Sent_Emails records idempotently into historical_sent_emails.
        Unique key: (sender_account, message_id).
        Does NOT automatically verify deliverability, authorize new outreach, or convert unsent drafts to sent.
        For matched companies, marks previous outreach detected and records outreach event idempotently.
        """
        stats = {
            "total": len(sent_emails),
            "imported": 0,
            "matched": 0,
            "unmatched": 0,
            "duplicates": 0,
        }
        reconciled = self.reconcile_sent_emails(sent_emails)
        # If input records are already reconciled, preserve their matched candidate ids
        for m_orig, m_rec in zip(sent_emails, reconciled):
            if m_orig.matched_candidate_id and m_orig.reconciliation_status == "matched":
                m_rec.matched_candidate_id = m_orig.matched_candidate_id
                m_rec.matched_company_name = m_orig.matched_company_name or m_rec.matched_company_name
                m_rec.reconciliation_status = "matched"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.store._get_connection() as conn:
            for m in reconciled:
                recips_json = json.dumps(m.recipient_emails)
                try:
                    conn.execute(
                        """
                        INSERT INTO historical_sent_emails (
                            message_id, thread_id, sender_account, sender_group,
                            from_address, to_address, recipient_emails_json, cc, bcc,
                            sent_date, sent_time, subject, email_body, body_truncated,
                            gmail_link, source_file, source_sheet, matched_candidate_id,
                            matched_company_name, reconciliation_status, imported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            m.message_id,
                            m.thread_id,
                            m.sender_account,
                            m.sender_group,
                            m.from_address,
                            m.to_address,
                            recips_json,
                            m.cc,
                            m.bcc,
                            m.sent_date,
                            m.sent_time,
                            m.subject,
                            m.email_body,
                            m.body_truncated,
                            m.gmail_link,
                            m.source_file,
                            m.source_sheet,
                            m.matched_candidate_id,
                            m.matched_company_name,
                            m.reconciliation_status,
                            now_iso,
                        ),
                    )
                    stats["imported"] += 1
                    if m.reconciliation_status == "matched":
                        stats["matched"] += 1
                    else:
                        stats["unmatched"] += 1
                except sqlite3.IntegrityError:
                    stats["duplicates"] += 1
                    continue

                # If matched to a company, update candidate outreach guard and record historical outreach event
                if m.matched_candidate_id and m.reconciliation_status == "matched":
                    cid = m.matched_candidate_id
                    # 1. Update CandidateCompany previous outreach status
                    conn.execute(
                        """
                        UPDATE candidate_companies
                        SET cold_outreach_allowed = 0,
                            historical_contact_status = CASE
                                WHEN historical_contact_status IN ('replied', 'converted', 'dnc') THEN historical_contact_status
                                ELSE 'contacted'
                            END,
                            last_contacted_at = COALESCE(last_contacted_at, ?)
                        WHERE candidate_id = ?
                        """,
                        (m.sent_date, cid),
                    )

                    # 2. Record historical outreach event idempotently, with combined-evidence Excel/Gmail correspondence
                    raw_hash = f"gmail:{m.sender_account}:{m.message_id}"
                    contact_email = (m.recipient_emails[0] if m.recipient_emails else m.to_address).strip().lower()
                    m_date_prefix = (m.sent_date or "")[:10]
                    gmail_note = f"Gmail message {m.message_id} (Thread {m.thread_id}) | Subject: {m.subject or 'No Subject'}"

                    # Combined evidence rule:
                    # Same recipient alone is insufficient (could be a distinct follow-up on another date).
                    # Same date alone is insufficient (could be sent to a different contact/email at same company).
                    # Legitimate correspondence requires BOTH matching date AND matching recipient email.
                    existing_ev = None
                    if m_date_prefix and contact_email:
                        ev_rows = conn.execute(
                            """
                            SELECT event_id, contacted_at, contact_email, source_ref, notes FROM historical_outreach_events
                            WHERE candidate_id = ?
                            """,
                            (cid,),
                        ).fetchall()
                        for er in ev_rows:
                            e_date = (er["contacted_at"] or "")[:10]
                            e_mail = (er["contact_email"] or "").strip().lower()
                            if e_date == m_date_prefix and e_mail == contact_email:
                                existing_ev = er
                                break

                    if existing_ev:
                        # Combined correspondence confirmed: Enrich existing outreach event with Gmail provenance rather than duplicating
                        cur_notes = existing_ev["notes"] or ""
                        new_notes = f"{cur_notes} | {gmail_note}" if gmail_note not in cur_notes else cur_notes
                        conn.execute(
                            """
                            UPDATE historical_outreach_events
                            SET notes = ?,
                                intent_evidence = COALESCE(intent_evidence, ?)
                            WHERE event_id = ?
                            """,
                            (new_notes, f"gmail:{m.message_id}", existing_ev["event_id"]),
                        )
                    else:
                        # Distinct follow-up or uncertain correspondence: retain as independent outreach event
                        # Preserves distinct Gmail Message IDs and full provenance
                        self.store.record_historical_outreach(
                            candidate_id=cid,
                            source_ref=f"gmail:{m.sender_account}",
                            contact_name=m.to_address,
                            contact_email=contact_email,
                            contacted_at=m.sent_date,
                            channel="gmail_sent",
                            reply_status="contacted",
                            notes=gmail_note,
                            raw_row_hash=raw_hash,
                        )

        return stats

    def get_sent_emails(
        self,
        status: Optional[str] = None,
        candidate_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query historical sent emails registry."""
        with self.store._get_connection() as conn:
            query = "SELECT * FROM historical_sent_emails WHERE 1=1"
            params: List[Any] = []
            if status:
                query += " AND reconciliation_status = ?"
                params.append(status)
            if candidate_id:
                query += " AND matched_candidate_id = ?"
                params.append(candidate_id)
            query += " ORDER BY sent_date DESC, sent_time DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                recips = []
                try:
                    recips = json.loads(r["recipient_emails_json"])
                except Exception:
                    pass
                results.append({
                    "id": r["id"],
                    "message_id": r["message_id"],
                    "thread_id": r["thread_id"],
                    "sender_account": r["sender_account"],
                    "sender_group": r["sender_group"],
                    "from_address": r["from_address"],
                    "to_address": r["to_address"],
                    "recipient_emails": recips,
                    "cc": r["cc"],
                    "bcc": r["bcc"],
                    "sent_date": r["sent_date"],
                    "sent_time": r["sent_time"],
                    "subject": r["subject"],
                    "email_body": r["email_body"],
                    "body_truncated": r["body_truncated"],
                    "gmail_link": r["gmail_link"],
                    "source_file": r["source_file"],
                    "source_sheet": r["source_sheet"],
                    "matched_candidate_id": r["matched_candidate_id"],
                    "matched_company_name": r["matched_company_name"],
                    "reconciliation_status": r["reconciliation_status"],
                    "imported_at": r["imported_at"],
                })
            return results

    def get_sent_emails_stats(self) -> Dict[str, Any]:
        """Aggregate stats on historical sent emails registry."""
        with self.store._get_connection() as conn:
            total = conn.execute("SELECT count(*) FROM historical_sent_emails").fetchone()[0]
            matched = conn.execute("SELECT count(*) FROM historical_sent_emails WHERE reconciliation_status = 'matched'").fetchone()[0]
            ambiguous = conn.execute("SELECT count(*) FROM historical_sent_emails WHERE reconciliation_status = 'ambiguous'").fetchone()[0]
            unmatched = conn.execute("SELECT count(*) FROM historical_sent_emails WHERE reconciliation_status = 'unmatched'").fetchone()[0]
            unique_threads = conn.execute("SELECT count(DISTINCT thread_id) FROM historical_sent_emails").fetchone()[0]
            unique_messages = conn.execute("SELECT count(DISTINCT message_id) FROM historical_sent_emails").fetchone()[0]
            return {
                "total": total,
                "matched": matched,
                "ambiguous": ambiguous,
                "unmatched": unmatched,
                "unique_threads": unique_threads,
                "unique_messages": unique_messages,
            }

    def reconcile_meeting_activities(
        self,
        meetings: List[MeetingRecord],
        staged_records: Optional[List[HistoricalRecord]] = None,
    ) -> List[MeetingRecord]:
        """
        Reconciles Meeting worksheet records against existing candidate companies.
        Safety Invariant:
        Matches existing candidates by canonical domain or normalized company name.
        Does NOT create duplicate company entities.
        """
        reconciled = []
        domain_to_cid: Dict[str, str] = {}
        name_to_cid: Dict[str, str] = {}
        cand_names: Dict[str, str] = {}

        with self.store._get_connection() as conn:
            rows = conn.execute("SELECT candidate_id, company_name, company_domain FROM candidate_companies").fetchall()
            for r in rows:
                cid = str(r[0])
                cname = str(r[1] or "")
                cdom = str(r[2] or "").strip().lower()
                cand_names[cid] = cname
                if cdom:
                    domain_to_cid[cdom] = cid
                if cname:
                    name_to_cid[cname.strip().lower()] = cid
                    name_to_cid[normalize_company_name(cname)] = cid

        if staged_records:
            for rec in staged_records:
                cid = None
                if rec.domain:
                    canon = self.store.canonicalize_domain(rec.domain)
                    if canon:
                        cid = f"cand:domain:{canon}"
                        domain_to_cid[canon] = cid
                if not cid and rec.company_name:
                    cname_clean = rec.company_name.strip().lower()
                    cid = name_to_cid.get(cname_clean) or self.store.generate_company_id(rec.company_name, None, rec.country)
                    name_to_cid[cname_clean] = cid
                    name_to_cid[normalize_company_name(rec.company_name)] = cid
                if cid and rec.company_name:
                    cand_names.setdefault(cid, rec.company_name)

        for m in meetings:
            matched_cid = None
            if m.domain:
                canon = self.store.canonicalize_domain(m.domain)
                if canon:
                    matched_cid = domain_to_cid.get(canon)
            if not matched_cid and m.company_name:
                clean_name = m.company_name.strip().lower()
                norm_name = normalize_company_name(m.company_name)
                matched_cid = name_to_cid.get(clean_name) or name_to_cid.get(norm_name)

            if matched_cid:
                m.matched_candidate_id = matched_cid
                m.matched_company_name = cand_names.get(matched_cid, m.company_name)
                m.reconciliation_status = "matched"
            else:
                m.matched_candidate_id = None
                m.matched_company_name = None
                m.reconciliation_status = "unmatched"

            reconciled.append(m)

        return reconciled

    def import_meeting_activities(
        self,
        meetings: List[MeetingRecord],
        staged_records: Optional[List[HistoricalRecord]] = None,
    ) -> Dict[str, int]:
        """
        Persists Meeting records into historical_meeting_activities idempotently,
        and records meeting outreach events for matched candidates without creating
        duplicate candidate company entities.
        """
        stats = {
            "total": len(meetings),
            "imported": 0,
            "matched": 0,
            "unmatched": 0,
            "duplicates": 0,
        }
        reconciled = self.reconcile_meeting_activities(meetings, staged_records=staged_records)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.store._get_connection() as conn:
            for m in reconciled:
                act_hash = m.compute_hash()
                try:
                    conn.execute(
                        """
                        INSERT INTO historical_meeting_activities (
                            activity_hash, company_name, canonical_domain, status, sender,
                            contact_date, follow_up_date, reply_date, follow_up_notes,
                            origin_sheet, notes, matched_candidate_id, matched_company_name,
                            source_file, source_sheet, raw_row_json, imported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            act_hash,
                            m.company_name,
                            self.store.canonicalize_domain(m.domain) if m.domain else None,
                            m.status,
                            m.sender,
                            m.contact_date,
                            m.follow_up_date,
                            m.reply_date,
                            m.follow_up_notes,
                            m.origin_sheet,
                            m.notes,
                            m.matched_candidate_id,
                            m.matched_company_name,
                            m.source_file,
                            m.source_sheet,
                            json.dumps(m.raw_row or {}),
                            now_iso,
                        ),
                    )
                    stats["imported"] += 1
                    if m.reconciliation_status == "matched":
                        stats["matched"] += 1
                    else:
                        stats["unmatched"] += 1
                except sqlite3.IntegrityError:
                    stats["duplicates"] += 1
                    continue

                if m.matched_candidate_id and m.reconciliation_status == "matched":
                    cid = m.matched_candidate_id
                    m_norm = normalize_outreach_status(m.status)
                    has_reply = bool(m.reply_date or m_norm["has_active_conversation"] or m_norm["historical_contact_status"] == "replied")
                    meet_status = "completed" if (m.reply_date or has_reply) else "scheduled"
                    contact_email = m.emails[0] if m.emails else None
                    self.store.record_historical_outreach(
                        candidate_id=cid,
                        source_ref=f"meeting:{m.source_file}:{m.origin_sheet or 'Meeting'}",
                        contact_name=m.contact_name or m.company_name,
                        contact_email=contact_email,
                        contacted_at=m.contact_date,
                        channel="meeting",
                        reply_status=m.status,
                        reply_at=m.reply_date,
                        notes=f"Meeting activity from {m.origin_sheet or 'Meeting'}: {m.follow_up_notes or m.notes or ''}",
                        raw_row_hash=act_hash,
                        follow_up_at=m.follow_up_date,
                        meeting_status=meet_status,
                        opportunity_status="active" if has_reply else None,
                    )
                    # Update candidate company status conservatively:
                    # Only mark as 'replied' and active_conversation=1 if actual reply/active conversation evidence exists
                    if has_reply:
                        conn.execute(
                            """
                            UPDATE candidate_companies
                            SET cold_outreach_allowed = 0,
                                active_conversation = 1,
                                historical_contact_status = CASE
                                    WHEN historical_contact_status IN ('replied', 'converted', 'dnc') THEN historical_contact_status
                                    ELSE 'replied'
                                END,
                                reply_status = COALESCE(reply_status, ?),
                                last_contacted_at = COALESCE(last_contacted_at, ?)
                            WHERE candidate_id = ?
                            """,
                            (m.status, m.contact_date, cid),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE candidate_companies
                            SET cold_outreach_allowed = 0,
                                historical_contact_status = CASE
                                    WHEN historical_contact_status IN ('replied', 'converted', 'dnc') THEN historical_contact_status
                                    ELSE 'contacted'
                                END,
                                last_contacted_at = COALESCE(last_contacted_at, ?)
                            WHERE candidate_id = ?
                            """,
                            (m.contact_date, cid),
                        )

        return stats

    def get_meeting_activities(
        self,
        candidate_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query historical meeting activities registry."""
        with self.store._get_connection() as conn:
            query = "SELECT * FROM historical_meeting_activities WHERE 1=1"
            params: List[Any] = []
            if candidate_id:
                query += " AND matched_candidate_id = ?"
                params.append(candidate_id)
            query += " ORDER BY contact_date DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                results.append({
                    "id": r["id"],
                    "activity_hash": r["activity_hash"],
                    "company_name": r["company_name"],
                    "canonical_domain": r["canonical_domain"],
                    "status": r["status"],
                    "sender": r["sender"],
                    "contact_date": r["contact_date"],
                    "follow_up_date": r["follow_up_date"],
                    "reply_date": r["reply_date"],
                    "follow_up_notes": r["follow_up_notes"],
                    "origin_sheet": r["origin_sheet"],
                    "notes": r["notes"],
                    "matched_candidate_id": r["matched_candidate_id"],
                    "matched_company_name": r["matched_company_name"],
                    "source_file": r["source_file"],
                    "source_sheet": r["source_sheet"],
                    "raw_row": json.loads(r["raw_row_json"] or "{}") if "raw_row_json" in r.keys() else {},
                    "imported_at": r["imported_at"],
                })
            return results

