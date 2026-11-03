"""
Bounded, SSRF-safe, TLS-verified public website factual researcher for SalesAI.
Strictly local: Zero external LLM calls, zero paid APIs, zero live Brave requests.
Enforces SSRF prevention, strict URL validation, DNS revalidation, redirect verification,
bounded response size (250KB), reasonable timeouts, and normal TLS certificate validation.
"""

import re
import socket
import ipaddress
import urllib.parse
import urllib.request
import ssl
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone

from sales_engine.leads.register_models import LeadRegisterRecord
from sales_engine.candidates.candidate_store import canonicalize_domain, normalize_company_name

logger = logging.getLogger(__name__)

# Constants for request boundaries
MAX_RESPONSE_BYTES = 250_000  # 250 KB limit
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_SECONDS = 5


class SSRFValidationError(ValueError):
    """Raised when a URL or its resolved IP violates SSRF or security boundaries."""
    pass


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    HTTP redirect handler that intercepts each redirect and strictly revalidates
    the target URL syntax, protocol, and resolved destination IP address.
    """
    def __init__(self, max_redirects: int = MAX_REDIRECTS):
        super().__init__()
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise SSRFValidationError(f"Too many redirects (exceeded limit of {self.max_redirects})")

        # Resolve relative redirect targets against the original URL
        orig_url = req.get_full_url()
        resolved_url = urllib.parse.urljoin(orig_url, newurl)

        # Validate syntax, scheme, and destination safety
        validate_public_url(resolved_url)

        return super().redirect_request(req, fp, code, msg, headers, resolved_url)


def is_ip_address_safe(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Verifies that an IP address is a publicly routable global address.
    Rejects private, loopback, link-local, reserved, multicast, and unspecified addresses.
    """
    if ip_obj.is_loopback:
        return False
    if ip_obj.is_private:
        return False
    if ip_obj.is_link_local:
        return False
    if ip_obj.is_reserved:
        return False
    if ip_obj.is_multicast:
        return False
    if ip_obj.is_unspecified:
        return False
    # Check IPv4 mapped or special addresses
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped and not is_ip_address_safe(ip_obj.ipv4_mapped):
            return False
        # Reject 6to4 or unique local if not caught by is_private
        if ip_obj.is_site_local:
            return False

    return True


def validate_public_url(url: str) -> Tuple[str, str, int]:
    """
    Validates a URL for SSRF safety:
    1. Only http and https schemes are permitted.
    2. Hostname must be present and valid.
    3. Reject localhost, 127.0.0.1, .local, internal domains.
    4. Resolves DNS and verifies all resolved IP addresses are safe public IPs.
    Returns (clean_url, hostname, port).
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFValidationError(f"Unsupported URL scheme '{scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("URL has no valid hostname.")

    hostname_clean = hostname.strip().lower()

    # Reject obvious local/internal hostnames
    if hostname_clean in ("localhost", "loopback", "127.0.0.1", "::1", "0.0.0.0"):
        raise SSRFValidationError(f"Target '{hostname_clean}' is a loopback or local address.")

    if hostname_clean.endswith(".local") or hostname_clean.endswith(".internal") or hostname_clean.endswith(".lan"):
        raise SSRFValidationError(f"Internal domain zone '{hostname_clean}' is not permitted.")

    # Check if hostname itself is an IP literal
    try:
        ip_obj = ipaddress.ip_address(hostname_clean)
        if not is_ip_address_safe(ip_obj):
            raise SSRFValidationError(f"Target IP '{hostname_clean}' is not a permitted public destination.")
    except ValueError:
        # Not an IP literal; perform DNS resolution check
        try:
            addr_info = socket.getaddrinfo(hostname_clean, None)
            if not addr_info:
                raise SSRFValidationError(f"Could not resolve host '{hostname_clean}'.")
            
            for entry in addr_info:
                sockaddr = entry[4]
                ip_str = sockaddr[0]
                resolved_ip = ipaddress.ip_address(ip_str)
                if not is_ip_address_safe(resolved_ip):
                    raise SSRFValidationError(
                        f"Host '{hostname_clean}' resolves to private/internal IP '{ip_str}'."
                    )
        except socket.gaierror as e:
            raise SSRFValidationError(f"DNS resolution failed for '{hostname_clean}': {e}")

    port = parsed.port or (443 if scheme == "https" else 80)
    return url, hostname_clean, port


def extract_website_public_facts(url: str, company_name: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """
    Performs an operator-authorized, bounded HTTP fetch of an official company website URL.
    Enforces SSRF validation, standard TLS certificate verification, and bounded content parsing.
    Returns status:
      - 'FETCH_SUCCESS': page fetched and parsed
      - 'BLOCKED_UNSAFE': destination rejected by SSRF guard (localhost, private IP, etc.)
      - 'FETCH_FAILED': network timeout, connection error, or TLS validation failure
    Zero external LLM calls. Never logs sensitive response data.
    """
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    canonical_dom = canonicalize_domain(clean_url)

    extracted = {
        "canonical_domain": canonical_dom,
        "source_url": clean_url,
        "company_name": company_name.strip(),
        "telephone_numbers": None,
        "contact_emails": None,
        "vat_number": None,
        "meta_description": None,
        "page_title": None,
        "status": "FETCH_SUCCESS",
        "error_message": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. SSRF Validation
    try:
        validate_public_url(clean_url)
    except SSRFValidationError as e:
        logger.warning(f"SSRF validation blocked request to {clean_url}: {e}")
        extracted["status"] = "BLOCKED_UNSAFE"
        extracted["error_message"] = str(e)
        return extracted
    except Exception as e:
        logger.warning(f"URL pre-validation error for {clean_url}: {e}")
        extracted["status"] = "FETCH_FAILED"
        extracted["error_message"] = str(e)
        return extracted

    # 2. Bounded, TLS-verified HTTP Fetch
    try:
        # Standard TLS certificate verification (ssl.create_default_context enforces CERT_REQUIRED and hostname check)
        ssl_ctx = ssl.create_default_context()

        # Build custom opener with SafeRedirectHandler to validate every redirect step
        opener = urllib.request.build_opener(SafeRedirectHandler(max_redirects=MAX_REDIRECTS))

        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SalesAI-Operator-Pilot/1.0",
                "Accept": "text/html,application/xhtml+xml",
            }
        )

        with opener.open(req, timeout=timeout_seconds) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                extracted["status"] = "NON_HTML_RESPONSE"
                return extracted

            raw_bytes = resp.read(MAX_RESPONSE_BYTES)
            html_text = raw_bytes.decode("utf-8", errors="replace")

            # Extract title
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
            if title_match:
                extracted["page_title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()

            # Extract meta description
            desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', html_text, re.IGNORECASE)
            if not desc_match:
                desc_match = re.search(r'<meta[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']', html_text, re.IGNORECASE)
            if desc_match:
                extracted["meta_description"] = re.sub(r"\s+", " ", desc_match.group(1)).strip()

            # Extract Italian P.IVA (11 digits, often prefixed with P.IVA or Partita IVA)
            piva_match = re.search(r"(?:P\.?\s*IVA|Partita\s*IVA|C\.?F\.?|Codice\s*Fiscale)[:\s]*([0-9]{11})", html_text, re.IGNORECASE)
            if piva_match:
                extracted["vat_number"] = piva_match.group(1)

            # Extract public emails (ignore common asset/image/font extensions)
            email_candidates = set(re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", html_text))
            valid_emails = []
            for em in email_candidates:
                em_l = em.lower()
                if not any(em_l.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".woff", ".woff2"]):
                    valid_emails.append(em)
            if valid_emails:
                extracted["contact_emails"] = ", ".join(sorted(valid_emails)[:3])

            # Extract phone numbers (Italian switchboard pattern)
            phones = set(re.findall(r"(?:\+39\s*)?(?:0\d{1,4}\s*[\d\s.-]{5,10}|\b3\d{2}\s*[\d\s.-]{6,8}\b)", html_text))
            clean_phones = [p.strip() for p in phones if len(re.sub(r"\D", "", p)) >= 8]
            if clean_phones:
                extracted["telephone_numbers"] = "; ".join(sorted(clean_phones)[:2])

            extracted["status"] = "FETCH_SUCCESS"

    except SSRFValidationError as e:
        logger.warning(f"SSRF violation during fetch or redirect for {clean_url}: {e}")
        extracted["status"] = "BLOCKED_UNSAFE"
        extracted["error_message"] = str(e)
    except Exception as e:
        logger.warning(f"Bounded website fetch for {clean_url} failed: {e}")
        extracted["status"] = "FETCH_FAILED"
        extracted["error_message"] = str(e)

    return extracted


def create_lead_from_researched_website(
    company_name: str,
    website_url: str,
    provisional_priority: str = "B",
    priority_rationale: Optional[str] = None,
    operator_notes: Optional[str] = None
) -> LeadRegisterRecord:
    """
    Performs bounded website research and returns an editable LeadRegisterRecord.
    Factual verification semantics:
    - Successful HTTP fetch sets verification_status to 'EXTRACTED' (NOT 'Verified').
    - Only explicit human operator review sets verification_status to 'OPERATOR_REVIEWED' or 'VERIFIED'.
    - If fetch fails or is blocked, verification_status is 'Unverified'.
    - Values not found on website remain 'Not Found'.
    - Zero external LLM calls.
    """
    clean_url = website_url.strip()
    if clean_url and not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    dom = canonicalize_domain(clean_url)
    clean_name = company_name.strip()
    clean_norm = normalize_company_name(clean_name)
    cid = f"cand:domain:{dom}" if dom else f"cand:name:{clean_norm}:manual"

    facts = extract_website_public_facts(clean_url, clean_name) if clean_url else {}

    p_iva = facts.get("vat_number")
    phones = facts.get("telephone_numbers")
    emails = facts.get("contact_emails")
    page_desc = facts.get("meta_description") or facts.get("page_title")
    fetch_status = facts.get("status")

    notes_parts = []
    if operator_notes:
        notes_parts.append(operator_notes.strip())
    if page_desc:
        notes_parts.append(f"Site Description: {page_desc}")
    if fetch_status == "BLOCKED_UNSAFE":
        notes_parts.append(f"Security Alert: Website fetch blocked ({facts.get('error_message')})")
    elif fetch_status == "FETCH_FAILED":
        notes_parts.append(f"Notice: Website fetch failed ({facts.get('error_message')})")

    combined_notes = "\n".join(notes_parts) if notes_parts else "Not Found"

    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Correct factual verification semantics:
    # A successful HTTP fetch is EXTRACTED with provisional confidence 0.50, NOT Verified.
    if fetch_status == "FETCH_SUCCESS":
        verification_status = "EXTRACTED"
        confidence_score = 0.50
    else:
        verification_status = "Unverified"
        confidence_score = 0.0

    return LeadRegisterRecord(
        candidate_id=cid,
        record_number=None,
        priority_level=provisional_priority if provisional_priority in ("A", "B", "C") else "B",
        priority_rationale=priority_rationale or "Provisional operator classification pending direct contact",
        company_name=clean_name,
        legal_identity=f"P.IVA {p_iva}" if p_iva else "Not Found",
        geographic_location="Not Found",
        telephone_numbers=phones or "Not Found",
        business_activities=page_desc or "Not Found",
        official_website=clean_url or "Not Found",
        responsible_person="Not Found",
        descriptions_and_notes=combined_notes,
        job_advertisement_links="Not Found",
        other_links_and_references="Not Found",
        contact_email_addresses=emails or "Not Found",
        provenance_source="operator_researched",
        research_date=now_date,
        verification_status=verification_status,
        confidence_score=confidence_score,
        evidence_urls=[clean_url] if clean_url else []
    )
