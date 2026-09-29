import re
import urllib.parse
from typing import List, Dict, Set, Any
from scrapling import Fetcher, Selector
from markdownify import markdownify

MAX_PAGES = 8
MAX_CHARS_PER_PAGE = 12000
MAX_TOTAL_CHARS = 60000
REQUEST_TIMEOUT = 10

class WebsiteFetcher:
    def __init__(self):
        self.fetcher = Fetcher()

    def _is_safe_url(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            
            hostname = parsed.hostname or ""
            hostname = hostname.lower()
            
            if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                return False
            if hostname.endswith(".localhost") or hostname.endswith(".local"):
                return False
                
            # Basic check for IPv4 private blocks (not exhaustive for all formats, but catches common SSRF attempts)
            if re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.)", hostname):
                return False
                
            return True
        except Exception:
            return False

    def fetch_website_content(self, base_url: str) -> Dict[str, Any]:
        """Fetch and extract clean text from a website, starting from the homepage and prioritizing specific pages."""
        print(f"[SalesAI Web] Website fetch started for {base_url}")
        
        # Ensure base URL has scheme
        if not base_url.startswith("http"):
            base_url = "https://" + base_url
            
        if not self._is_safe_url(base_url):
            raise ValueError(f"URL is not allowed: {base_url}")
            
        try:
            home_resp = self.fetcher.get(base_url, timeout=REQUEST_TIMEOUT)
            home_html = home_resp.body.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[SalesAI Web] Error fetching {base_url}: {e}")
            raise ValueError(f"Failed to fetch {base_url}")
            
        base_domain = urllib.parse.urlparse(base_url).netloc
        
        # Extract links
        page = Selector(home_html)
        link_elements = page.css("a")
        
        internal_links = {}
        for el in link_elements:
            link = el.attrib.get("href")
            if not link:
                continue
            anchor = el.text.strip() if el.text else ""
                
            # Normalize link
            link = urllib.parse.urljoin(base_url, link)
            parsed_link = urllib.parse.urlparse(link)
            
            # Stay on the same domain
            if parsed_link.netloc != base_domain:
                continue
                
            # Remove fragments and queries for uniqueness
            clean_link = urllib.parse.urlunparse((parsed_link.scheme, parsed_link.netloc, parsed_link.path, '', '', ''))
            
            # Ignore some extensions and paths
            ignore_patterns = [
                r"\.(pdf|png|jpg|jpeg|svg|css|js|zip|tar|gz|mp4|webm)$",
                r"/(login|signup|signin|portal|dashboard|account|client|privacy|terms|cookie|legal|careers|jobs)",
                r"^(mailto|tel|javascript):",
                r"/(facebook|twitter|linkedin|instagram|youtube)\.com"
            ]
            
            if any(re.search(pat, clean_link, re.IGNORECASE) for pat in ignore_patterns):
                continue
                
            if clean_link not in internal_links:
                internal_links[clean_link] = anchor
            else:
                internal_links[clean_link] = internal_links[clean_link] + " " + anchor
            
        # Prioritize URLs
        priority_terms = ["services", "solutions", "about", "industries", "capabilities", "expertise", "case-studies", "projects", "what-we-do", "products"]
        
        def link_score(item) -> int:
            l, anchor = item
            score = 0
            score_text = f"{l} {anchor}".lower()
            if l.rstrip('/').lower() == base_url.rstrip('/').lower():
                score += 100 # Home page first
            for term in priority_terms:
                if term in score_text:
                    score += 10
            return score
            
        sorted_items = sorted(internal_links.items(), key=link_score, reverse=True)
        sorted_links = [item[0] for item in sorted_items]
        
        # Always make sure homepage is there
        home_clean = urllib.parse.urlunparse(urllib.parse.urlparse(base_url)._replace(query='', fragment=''))
        if home_clean not in sorted_links:
            sorted_links.insert(0, home_clean)
            
        print(f"[SalesAI Web] Pages discovered: {len(internal_links)}. Crawling candidate queue.")
        
        pages_content = []
        seen_content_hashes = set()
        total_chars = 0
        
        for url in sorted_links:
            if len(pages_content) >= MAX_PAGES:
                break
                
            remaining = MAX_TOTAL_CHARS - total_chars
            if remaining <= 0:
                break
                
            print(f"[SalesAI Web] Fetching page: {url}")
            try:
                resp = self.fetcher.get(url, timeout=REQUEST_TIMEOUT)
                html = resp.body.decode("utf-8", errors="replace")
                
                raw_clean_text = self._clean_and_convert(html, url)
                
                # Content deduplication: check text content hash
                content_hash = hash(raw_clean_text.strip())
                if content_hash in seen_content_hashes:
                    print(f"[SalesAI Web] Skipping duplicate content page: {url}")
                    continue
                seen_content_hashes.add(content_hash)
                
                original_chars = len(raw_clean_text)
                truncated = False
                
                if original_chars > remaining:
                    msg = "... [TRUNCATED DUE TO GLOBAL LIMIT]"
                    clean_text = raw_clean_text[:max(0, remaining - len(msg))] + msg
                    truncated = True
                elif original_chars > MAX_CHARS_PER_PAGE:
                    msg = "... [TRUNCATED]"
                    clean_text = raw_clean_text[:MAX_CHARS_PER_PAGE - len(msg)] + msg
                    truncated = True
                else:
                    clean_text = raw_clean_text
                    
                pages_content.append({
                    "url": url,
                    "text": clean_text,
                    "original_chars": original_chars,
                    "captured_chars": len(clean_text),
                    "truncated": truncated
                })
                
                total_chars += len(clean_text)
                
            except Exception as e:
                print(f"[SalesAI Web] Failed to fetch {url}: {e}")
                
        print(f"[SalesAI Web] Unique pages fetched: {len(pages_content)}, Characters extracted: {total_chars}")
        
        return {
            "base_url": base_url,
            "pages": pages_content
        }
        
    def _clean_and_convert(self, html_content: str, url: str) -> str:
        # Strip noisy elements
        cleaned_html = re.sub(
            r"<(script|style|nav|footer|header|aside|iframe|svg|form|noscript)[^>]*>.*?</\1>", 
            "", 
            html_content, 
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Convert to structured Markdown
        md_text = markdownify(
            cleaned_html,
            heading_style="ATX",
            code_language="python",
            strip=["a", "img"],
        )
        
        # Clean up excessive whitespace
        md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip()
        
        return md_text
