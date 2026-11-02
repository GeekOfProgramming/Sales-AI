"""
sales_engine/research/website_crawler.py
Comprehensive, budget-governed website crawler for SalesAI Research Pipeline V2.1.
P0/P1 Hardening Features:
- Independent newly-fetched crawl budget separated from prior hydrated pages.
- Resumable crawling preserves all previously stored pages, avoids re-fetching, and reconstructs queue.
- Reconstructible frontier tracking (pending URLs, discovered inventory, remaining queue).
- Strict in-scope domain redirect handler (SafeDomainRedirectHandler) verifying scheme, IP, and domain match.
- Robots.txt parsing with explicit failure policy, crawl-delay enforcement, and visible diagnostics.
- Multi-dimensional limits: page budget, depth limit, time limit, byte budget, and discovery inventory cap.
- Accurate completion status: never claims complete when frontier queue remains unexhausted.
- Zero LLM calls during crawling.
"""

from __future__ import annotations

import collections
import dataclasses
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple, Any

from bs4 import BeautifulSoup

from sales_engine.candidates.candidate_store import canonicalize_domain
from sales_engine.research.lead_researcher import (
    validate_public_url,
    SSRFValidationError,
    SafeRedirectHandler,
)
from sales_engine.research.company_research_service import PageFetchResult
from sales_engine.research.research_store import (
    PageEvidence,
    ResearchSnapshot,
    ResearchSnapshotStore,
)

logger = logging.getLogger(__name__)

# Section relevance patterns for priority scoring
SECTION_PATTERNS = {
    "contacts": [
        r"contatt", r"contact", r"recapit", r"dove-siamo", r"sede", r"reach-us",
        r"contatti\.html?", r"contact-us"
    ],
    "about": [
        r"chi-siamo", r"about", r"profilo", r"storia", r"azienda", r"company",
        r"chi_siamo", r"chi siamo", r"team", r"chi-siamo\.html?"
    ],
    "services": [
        r"serviz", r"service", r"attivita", r"competenze", r"attivita-e-servizi",
        r"what-we-do", r"capabilities"
    ],
    "projects": [
        r"progett", r"project", r"portfolio", r"opere", r"realizzazion", r"case-stud",
        r"lavori"
    ],
    "bim": [
        r"bim", r"building-information", r"digital-twin", r"computational-design",
        r"revit", r"ifc"
    ],
    "careers": [
        r"carrier", r"career", r"lavora-con-noi", r"job", r"posizioni-aperte",
        r"lavoraconnoi", r"opportunita", r"hiring"
    ],
    "legal": [
        r"legal", r"privacy", r"note-legali", r"termini", r"imprint", r"impressum",
        r"policy", r"dati-societari"
    ],
}

DISALLOWED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".webm",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dmg", ".iso", ".bin", ".css", ".js"
)


class SafeDomainRedirectHandler(SafeRedirectHandler):
    """
    Enhanced redirect handler that enforces:
    1. Base SafeRedirectHandler SSRF and IP safety checks.
    2. Strict authorized domain / subdomain boundary enforcement.
    """
    def __init__(self, authorized_domain: str, max_redirects: int = 3):
        super().__init__(max_redirects=max_redirects)
        self.authorized_domain = canonicalize_domain(authorized_domain)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        orig_url = req.get_full_url()
        resolved_url = urllib.parse.urljoin(orig_url, newurl)

        # 1. Enforce domain boundary first (before external DNS network lookups)
        parsed = urllib.parse.urlsplit(resolved_url)
        host = (parsed.hostname or "").lower()
        if host != self.authorized_domain and not host.endswith("." + self.authorized_domain):
            raise SSRFValidationError(
                f"Cross-domain redirect blocked: '{resolved_url}' leaves authorized scope '{self.authorized_domain}'."
            )

        # 2. Enforce SSRF validation (scheme, private IP literals, DNS checks)
        validate_public_url(resolved_url)

        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class CrawlBudget:
    """Configurable boundaries and limits for website crawling."""
    max_pages: int = 20
    max_depth: int = 3
    per_page_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 60.0
    max_page_bytes: int = 250_000
    max_total_bytes: int = 10_000_000
    max_discovered_urls: int = 1000
    crawl_delay_seconds: float = 0.0


@dataclass
class CrawlSummary:
    """Outcome report for a crawl execution."""
    snapshot_id: str
    status: str  # "complete", "partial", "failed", "blocked"
    pages_discovered: int
    pages_attempted: int
    pages_succeeded: int
    pages_failed: int
    total_bytes_downloaded: int
    duration_seconds: float
    started_at: str
    completed_at: str
    content_checksum: str
    diagnostic_info: Optional[str] = None
    page_evidences: List[PageEvidence] = field(default_factory=list)
    previous_pages_count: int = 0
    newly_fetched_count: int = 0
    remaining_urls_count: int = 0
    robots_status: str = "ok"


def normalize_url(url: str, base_domain: str) -> Optional[str]:
    """
    Normalizes a candidate URL:
    - Strips fragment (#...) and trailing whitespace.
    - Strips tracking/session query parameters (utm_*, ref, etc.).
    - Ensures scheme is http or https.
    - Disallows non-web extensions (images, documents, archives).
    - Ensures exact same-site domain or subdomain match.
    """
    if not url or not url.strip():
        return None

    cleaned = url.strip()
    if cleaned.startswith("//"):
        cleaned = "https:" + cleaned
    elif not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        return None

    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        return None

    host = (parsed.hostname or "").lower()
    clean_base = canonicalize_domain(base_domain)
    # Check domain match: host == clean_base or host ends with '.' + clean_base
    if host != clean_base and not host.endswith("." + clean_base):
        return None

    path = parsed.path or "/"
    lower_path = path.lower()
    if any(lower_path.endswith(ext) for ext in DISALLOWED_EXTENSIONS):
        return None

    # Filter out common infinite traps or asset paths
    if any(p in lower_path for p in ("/wp-json/", "/xmlrpc.php", "/cdn-cgi/", "/feed/", "/rss/")):
        return None

    # Strip query parameters that are tracking or session tokens
    query_parts = []
    if parsed.query:
        for qp in parsed.query.split("&"):
            if not qp:
                continue
            k = qp.split("=")[0].lower()
            if k.startswith("utm_") or k in ("ref", "fbclid", "gclid", "session_id", "lang_switch"):
                continue
            query_parts.append(qp)

    new_query = "&".join(query_parts)
    rebuilt = urllib.parse.urlunsplit((parsed.scheme.lower(), host, path, new_query, ""))
    return rebuilt


def classify_page_type(url: str, anchor_text: Optional[str] = None) -> str:
    """Classifies a URL into a high-level page type."""
    combined = (url + " " + (anchor_text or "")).lower()

    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.strip("/")
    if not path or path.lower() in ("index.html", "index.php", "home"):
        return "home"

    for ptype, patterns in SECTION_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, combined, re.I):
                return ptype

    return "general"


def score_page_priority(url: str, page_type: str, depth: int) -> int:
    """Computes a frontier priority score (higher = fetched first)."""
    base_scores = {
        "home": 100,
        "contacts": 90,
        "about": 85,
        "services": 80,
        "bim": 80,
        "careers": 75,
        "projects": 70,
        "legal": 65,
        "general": 40,
    }
    score = base_scores.get(page_type, 40)
    score -= (depth * 10)
    return max(1, score)


class WebsiteCrawlerV2:
    """
    High-integrity website crawler implementing budget boundaries, resumable crawling,
    and structured evidence persistence.
    """

    def __init__(
        self,
        snapshot_store: Optional[ResearchSnapshotStore] = None,
        budget: Optional[CrawlBudget] = None,
        browser_adapter: Optional[Any] = None,
    ):
        self.snapshot_store = snapshot_store or ResearchSnapshotStore()
        self.budget = budget or CrawlBudget()
        self.browser_adapter = browser_adapter

    def crawl_website(
        self,
        candidate_id: str,
        canonical_domain: str,
        company_name: str,
        resume_snapshot_id: Optional[str] = None,
    ) -> CrawlSummary:
        """
        Executes complete or bounded crawl of the company's verified domain.
        Returns CrawlSummary and writes snapshot to ResearchSnapshotStore.
        P0-A: Newly-fetched pages are governed by an independent budget (self.budget.max_pages),
        preserving all existing hydrated pages.
        """
        clean_domain = canonicalize_domain(canonical_domain)
        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()

        home_url = f"https://{clean_domain}"
        try:
            validate_public_url(home_url)
        except SSRFValidationError as e:
            logger.warning(f"Crawl aborted: SSRF validation failed for {home_url}: {e}")
            return CrawlSummary(
                snapshot_id="",
                status="blocked",
                pages_discovered=0,
                pages_attempted=0,
                pages_succeeded=0,
                pages_failed=1,
                total_bytes_downloaded=0,
                duration_seconds=time.time() - start_time,
                started_at=start_iso,
                completed_at=datetime.now(timezone.utc).isoformat(),
                content_checksum="",
                diagnostic_info=f"SSRF validation blocked domain: {e}",
            )

        # 1. Parse robots.txt
        r_res = self._fetch_robots_txt(clean_domain)
        if isinstance(r_res, tuple) and len(r_res) == 2:
            rp, robots_diag = r_res
        else:
            rp, robots_diag = r_res, 'Default allow' 

        # 2. Discover URLs from sitemap.xml
        sitemap_urls = self._discover_sitemap_urls(clean_domain)

        # 3. Frontier Queue: (priority, depth, url, page_type)
        discovered_urls: Set[str] = set()
        visited_urls: Set[str] = set()
        queue: List[Tuple[int, int, str, str]] = []

        pages_attempted = 0
        newly_succeeded = 0
        pages_failed = 0
        total_bytes = 0
        page_evidences: List[PageEvidence] = []
        skipped_urls: List[str] = []

        # If resuming from an existing snapshot, hydrate visited URLs and existing evidence
        previous_pages_count = 0
        if resume_snapshot_id and self.snapshot_store:
            prior_snap = self.snapshot_store.get_snapshot_by_id(resume_snapshot_id)
            if prior_snap and prior_snap.pages:
                logger.info(f"Resuming crawl for {clean_domain} from prior snapshot {resume_snapshot_id} ({len(prior_snap.pages)} pages)")
                for prev_p in prior_snap.pages:
                    if prev_p.retrieval_status == "FETCH_SUCCESS":
                        page_evidences.append(prev_p)
                        visited_urls.add(prev_p.resolved_url)
                        if prev_p.requested_url:
                            visited_urls.add(prev_p.requested_url)
                        discovered_urls.add(prev_p.resolved_url)
                        previous_pages_count += 1
                        # Re-enqueue unvisited outgoing links from prior successful pages
                        for lk in (prev_p.links or []):
                            abs_url = urllib.parse.urljoin(prev_p.resolved_url, lk.get("href", ""))
                            norm_l = normalize_url(abs_url, clean_domain)
                            if norm_l and norm_l not in discovered_urls and norm_l not in visited_urls:
                                discovered_urls.add(norm_l)
                                l_ptype = classify_page_type(norm_l, lk.get("text"))
                                queue.append((
                                    score_page_priority(norm_l, l_ptype, 2),
                                    2,
                                    norm_l,
                                    l_ptype
                                ))
                    elif prev_p.retrieval_status in ("BLOCKED_UNSAFE", "FETCH_FAILED"):
                        pages_failed += 1
                        visited_urls.add(prev_p.resolved_url)
                        if prev_p.requested_url:
                            visited_urls.add(prev_p.requested_url)

        # Add homepage if not already visited
        norm_home = normalize_url(home_url, clean_domain) or home_url
        if norm_home not in visited_urls:
            discovered_urls.add(norm_home)
            queue.append((score_page_priority(norm_home, "home", 0), 0, norm_home, "home"))

        # Add sitemap URLs
        for sm_url in sitemap_urls:
            norm_sm = normalize_url(sm_url, clean_domain)
            if norm_sm and norm_sm not in visited_urls and norm_sm not in discovered_urls:
                ptype = classify_page_type(norm_sm)
                discovered_urls.add(norm_sm)
                queue.append((score_page_priority(norm_sm, ptype, 1), 1, norm_sm, ptype))

        # Check crawl-delay from robots.txt if available
        effective_delay = self.budget.crawl_delay_seconds
        if rp:
            try:
                r_delay = rp.crawl_delay("SalesAI-Research")
                if r_delay and r_delay > effective_delay:
                    effective_delay = min(r_delay, 2.0)  # Bound crawl-delay to max 2s
            except Exception:
                pass

        # 4. Crawl Loop: Governed by newly_succeeded < self.budget.max_pages (P0-A Fix)
        while queue and newly_succeeded < self.budget.max_pages:
            # Check total timeout
            if (time.time() - start_time) > self.budget.total_timeout_seconds:
                logger.info(f"Crawl total timeout reached ({self.budget.total_timeout_seconds}s) for {clean_domain}")
                break

            # Check total byte budget
            if total_bytes >= self.budget.max_total_bytes:
                logger.info(f"Crawl total byte limit reached ({self.budget.max_total_bytes} bytes) for {clean_domain}")
                break

            # Check discovery inventory cap
            if len(discovered_urls) >= self.budget.max_discovered_urls:
                logger.info(f"Crawl discovered URL inventory cap reached ({self.budget.max_discovered_urls})")

            # Sort queue by priority descending
            queue.sort(key=lambda x: x[0], reverse=True)
            prio, depth, cur_url, cur_ptype = queue.pop(0)

            if cur_url in visited_urls:
                continue
            visited_urls.add(cur_url)

            # Check robots.txt rules
            if rp and not rp.can_fetch("SalesAI-Research", cur_url):
                skipped_urls.append(cur_url)
                continue

            # Respect rate limit / crawl delay
            if effective_delay > 0:
                time.sleep(effective_delay)

            # Fetch page
            pages_attempted += 1
            self._current_authorized_domain = clean_domain
            fetch_res = self._fetch_page(cur_url, cur_ptype)

            if fetch_res.status == "FETCH_SUCCESS":
                newly_succeeded += 1
                raw_bytes_len = len(fetch_res.raw_html.encode("utf-8")) if fetch_res.raw_html else 0
                total_bytes += raw_bytes_len

                # Record page evidence
                ev_id = f"EV_{len(page_evidences) + 1:02d}"
                c_hash = hashlib.sha256((fetch_res.text_content or "").encode("utf-8")).hexdigest()
                pe = PageEvidence(
                    snapshot_id="",
                    evidence_id=ev_id,
                    requested_url=cur_url,
                    resolved_url=fetch_res.url,
                    page_type=fetch_res.page_type,
                    http_status=fetch_res.http_status,
                    retrieval_status=fetch_res.status,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    title=fetch_res.title,
                    meta_description=fetch_res.meta_description,
                    content_sha256=c_hash,
                    cleaned_text=fetch_res.text_content or "",
                    raw_html=fetch_res.raw_html,
                    links=fetch_res.links,
                    forms=fetch_res.forms,
                    structured_data=fetch_res.structured_data,
                    error_message=fetch_res.error_message,
                )
                page_evidences.append(pe)

                # Discover new links if depth budget and inventory cap allow
                if depth < self.budget.max_depth and len(discovered_urls) < self.budget.max_discovered_urls:
                    for lk in fetch_res.links:
                        raw_href = lk.get("href", "")
                        abs_url = urllib.parse.urljoin(cur_url, raw_href)
                        norm_l = normalize_url(abs_url, clean_domain)
                        if norm_l and norm_l not in discovered_urls and norm_l not in visited_urls:
                            discovered_urls.add(norm_l)
                            l_ptype = classify_page_type(norm_l, lk.get("text"))
                            queue.append((
                                score_page_priority(norm_l, l_ptype, depth + 1),
                                depth + 1,
                                norm_l,
                                l_ptype
                            ))
            else:
                pages_failed += 1
                if fetch_res.status in ("BLOCKED_UNSAFE", "FETCH_FAILED"):
                    ev_id = f"EV_{len(page_evidences) + 1:02d}"
                    pe = PageEvidence(
                        snapshot_id="",
                        evidence_id=ev_id,
                        requested_url=cur_url,
                        resolved_url=cur_url,
                        page_type=cur_ptype,
                        http_status=getattr(fetch_res, "http_status", 0),
                        retrieval_status=fetch_res.status,
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        title=None,
                        meta_description=None,
                        content_sha256="0" * 64,
                        cleaned_text="",
                        raw_html=None,
                        links=[],
                        forms=[],
                        structured_data=[],
                        error_message=fetch_res.error_message,
                    )
                    page_evidences.append(pe)

        duration = time.time() - start_time
        completed_iso = datetime.now(timezone.utc).isoformat()
        total_succeeded = previous_pages_count + newly_succeeded

        # Determine crawl status:
        # P0-A: Never claim complete when frontier queue remains unexhausted
        if total_succeeded == 0:
            crawl_status = "failed"
        elif not queue:
            crawl_status = "complete"
        else:
            crawl_status = "partial"

        # Persist snapshot if at least 1 page succeeded
        snapshot_id = ""
        combined_checksum = ""
        diag = (
            f"Crawl {crawl_status}: Previous pages={previous_pages_count}, Newly fetched={newly_succeeded}, "
            f"Total succeeded={total_succeeded}, Remaining frontier={len(queue)}, Discovered inventory={len(discovered_urls)}, "
            f"Robots: {robots_diag}."
        )

        if total_succeeded > 0:
            snapshot = self.snapshot_store.create_snapshot(
                candidate_id=candidate_id,
                canonical_domain=clean_domain,
                company_name=company_name,
                pages=page_evidences,
                research_status=crawl_status,
                diagnostic_info=diag,
            )
            snapshot_id = snapshot.snapshot_id
            combined_checksum = snapshot.content_checksum
        else:
            combined_checksum = ""

        return CrawlSummary(
            snapshot_id=snapshot_id,
            status=crawl_status,
            pages_discovered=len(discovered_urls),
            pages_attempted=pages_attempted,
            pages_succeeded=total_succeeded,
            pages_failed=pages_failed,
            total_bytes_downloaded=total_bytes,
            duration_seconds=duration,
            started_at=start_iso,
            completed_at=completed_iso,
            content_checksum=combined_checksum,
            diagnostic_info=diag,
            page_evidences=page_evidences,
            previous_pages_count=previous_pages_count,
            newly_fetched_count=newly_succeeded,
            remaining_urls_count=len(queue),
            robots_status=robots_diag,
        )

    def _fetch_robots_txt(self, domain: str) -> Tuple[Optional[urllib.robotparser.RobotFileParser], str]:
        """Fetches and parses robots.txt with safe limits, returning (parser, status_diagnostic)."""
        robots_url = f"https://{domain}/robots.txt"
        try:
            validate_public_url(robots_url)
            opener = urllib.request.build_opener(SafeDomainRedirectHandler(authorized_domain=domain, max_redirects=3))
            req = urllib.request.Request(
                robots_url,
                headers={"User-Agent": "SalesAI-Research/2.0"}
            )
            with opener.open(req, timeout=5.0) as resp:
                if resp.status == 200:
                    lines = resp.read(65536).decode("utf-8", errors="replace").splitlines()
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(lines)
                    return rp, "Parsed successfully (HTTP 200)"
                elif resp.status in (401, 403):
                    return None, f"Access restricted (HTTP {resp.status}) - Default allow public"
                else:
                    return None, f"Not found / unavailable (HTTP {resp.status}) - Default allow public"
        except Exception as e:
            return None, f"Fetch error ({type(e).__name__}: {e}) - Policy: Default allow public"

    def _discover_sitemap_urls(self, domain: str) -> List[str]:
        """Discovers candidate URLs from standard sitemap.xml locations."""
        discovered: List[str] = []
        candidate_sitemaps = [
            f"https://{domain}/sitemap.xml",
            f"https://{domain}/sitemap_index.xml",
            f"https://{domain}/wp-sitemap.xml",
        ]

        for s_url in candidate_sitemaps:
            try:
                validate_public_url(s_url)
                opener = urllib.request.build_opener(SafeDomainRedirectHandler(authorized_domain=domain, max_redirects=3))
                req = urllib.request.Request(
                    s_url,
                    headers={"User-Agent": "SalesAI-Research/2.0"}
                )
                with opener.open(req, timeout=5.0) as resp:
                    if resp.status == 200:
                        raw_xml = resp.read(500_000)
                        urls = self._parse_sitemap_xml(raw_xml, domain)
                        discovered.extend(urls)
                        if discovered:
                            break
            except Exception:
                continue

        return list(dict.fromkeys(discovered))

    def _parse_sitemap_xml(self, xml_bytes: bytes, domain: str) -> List[str]:
        """Parses sitemap XML (both regular urlset and sitemapindex) safely."""
        urls: List[str] = []
        try:
            root = ET.fromstring(xml_bytes)
            tag = root.tag.lower()
            if "urlset" in tag:
                for elem in root.findall(".//{*}loc"):
                    if elem.text:
                        norm = normalize_url(elem.text.strip(), domain)
                        if norm:
                            urls.append(norm)
            elif "sitemapindex" in tag:
                sub_sitemaps = [elem.text.strip() for elem in root.findall(".//{*}loc") if elem.text]
                for sub_sm in sub_sitemaps[:3]:
                    try:
                        validate_public_url(sub_sm)
                        opener = urllib.request.build_opener(SafeDomainRedirectHandler(authorized_domain=domain, max_redirects=3))
                        req = urllib.request.Request(sub_sm, headers={"User-Agent": "SalesAI-Research/2.0"})
                        with opener.open(req, timeout=5.0) as resp:
                            if resp.status == 200:
                                sub_xml = resp.read(500_000)
                                urls.extend(self._parse_sitemap_xml(sub_xml, domain))
                    except Exception:
                        pass
        except Exception:
            pass
        return urls

    def _fetch_page(self, url: str, page_type: str, authorized_domain: Optional[str] = None) -> PageFetchResult:
        """Fetches an individual page with SSRF checks, in-scope redirect bounds, size caps, and DOM parsing."""
        res = PageFetchResult(url=url, page_type=page_type, status="FETCH_SUCCESS")

        # 1. SSRF check
        try:
            validate_public_url(url)
        except SSRFValidationError as e:
            res.status = "BLOCKED_UNSAFE"
            res.error_message = str(e)
            return res
        except Exception as e:
            res.status = "FETCH_FAILED"
            res.error_message = str(e)
            return res

        # 2. HTTP GET with SafeDomainRedirectHandler
        try:
            auth_dom = authorized_domain or getattr(self, '_current_authorized_domain', None) or urllib.parse.urlsplit(url).hostname or ''
            opener = urllib.request.build_opener(SafeDomainRedirectHandler(authorized_domain=auth_dom, max_redirects=3))
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SalesAI-Local-Research/2.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                }
            )
            with opener.open(req, timeout=self.budget.per_page_timeout_seconds) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type and "application/xhtml" not in content_type:
                    res.status = "FETCH_FAILED"
                    res.error_message = f"Unsupported content-type: {content_type}"
                    return res

                raw_bytes = resp.read(self.budget.max_page_bytes)
                html_text = raw_bytes.decode("utf-8", errors="replace")
                res.raw_html = html_text
                res.http_status = getattr(resp, "status", 200)

                soup = BeautifulSoup(html_text, "html.parser")

                # Extract JSON-LD
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

                # Extract forms
                for f in soup.find_all("form"):
                    action = f.get("action", "")
                    method = f.get("method", "GET").upper()
                    fields_list = [
                        inp.get("name")
                        for inp in f.find_all(["input", "textarea", "select"])
                        if inp.get("name")
                    ]
                    res.forms.append({"action": action, "method": method, "fields": fields_list})

                # Extract anchor links
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    link_text = re.sub(r"\s+", " ", a.get_text()).strip()
                    if href and not href.startswith("javascript:"):
                        res.links.append({"text": link_text, "href": href})

                # Clean DOM
                for tag in soup(["script", "style", "noscript", "svg"]):
                    tag.decompose()

                if soup.title and soup.title.string:
                    res.title = re.sub(r"\s+", " ", soup.title.string).strip()

                meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
                if meta_desc and meta_desc.get("content"):
                    res.meta_description = re.sub(r"\s+", " ", meta_desc["content"]).strip()

                res.text_content = re.sub(r"\s+", " ", soup.get_text()).strip()

        except SSRFValidationError as e:
            res.status = "BLOCKED_UNSAFE"
            res.error_message = str(e)
        except Exception as e:
            res.status = "FETCH_FAILED"
            res.error_message = str(e)

        return res
