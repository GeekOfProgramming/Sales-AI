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

    def fetch_website_content(self, base_url: str) -> Dict[str, Any]:
        """Fetch and extract clean text from a website, starting from the homepage and prioritizing specific pages."""
        print(f"[SalesAI Web] Website fetch started for {base_url}")
        
        # Ensure base URL has scheme
        if not base_url.startswith("http"):
            base_url = "https://" + base_url
            
        try:
            home_resp = self.fetcher.get(base_url, timeout=REQUEST_TIMEOUT)
            home_html = home_resp.body.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[SalesAI Web] Error fetching {base_url}: {e}")
            raise ValueError(f"Failed to fetch {base_url}")
            
        base_domain = urllib.parse.urlparse(base_url).netloc
        
        # Extract links
        page = Selector(home_html)
        links = page.css("a::attr(href)").getall()
        
        internal_links = set()
        for link in links:
            if not link:
                continue
                
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
                r"/(login|signup|signin|privacy|terms|cookie|legal|careers|jobs)",
                r"^(mailto|tel|javascript):",
                r"/(facebook|twitter|linkedin|instagram|youtube)\.com"
            ]
            
            if any(re.search(pat, clean_link, re.IGNORECASE) for pat in ignore_patterns):
                continue
                
            internal_links.add(clean_link)
            
        # Prioritize URLs
        priority_terms = ["services", "solutions", "about", "industries", "capabilities", "expertise", "case-studies", "projects", "what-we-do", "products"]
        
        def link_score(l: str) -> int:
            score = 0
            l_lower = l.lower()
            if l_lower.rstrip('/') == base_url.rstrip('/'):
                score += 100 # Home page first
            for term in priority_terms:
                if term in l_lower:
                    score += 10
            return score
            
        sorted_links = sorted(list(internal_links), key=link_score, reverse=True)
        # Always make sure homepage is there
        home_clean = urllib.parse.urlunparse(urllib.parse.urlparse(base_url)._replace(query='', fragment=''))
        if home_clean not in sorted_links:
            sorted_links.insert(0, home_clean)
            
        urls_to_fetch = sorted_links[:MAX_PAGES]
        print(f"[SalesAI Web] Pages discovered: {len(internal_links)}. Fetching top {len(urls_to_fetch)} pages.")
        
        pages_content = []
        total_chars = 0
        
        for url in urls_to_fetch:
            if total_chars >= MAX_TOTAL_CHARS:
                break
                
            print(f"[SalesAI Web] Fetching page: {url}")
            try:
                resp = self.fetcher.get(url, timeout=REQUEST_TIMEOUT)
                html = resp.body.decode("utf-8", errors="replace")
                
                clean_text = self._clean_and_convert(html, url)
                
                if len(clean_text) > MAX_CHARS_PER_PAGE:
                    clean_text = clean_text[:MAX_CHARS_PER_PAGE] + "... [TRUNCATED]"
                    
                pages_content.append({
                    "url": url,
                    "text": clean_text
                })
                
                total_chars += len(clean_text)
                
            except Exception as e:
                print(f"[SalesAI Web] Failed to fetch {url}: {e}")
                
        print(f"[SalesAI Web] Pages fetched: {len(pages_content)}, Characters extracted: {total_chars}")
        
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
