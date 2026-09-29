import httpx
from typing import Dict, Any
from bs4 import BeautifulSoup
from sales_engine.sources.base_job_source import BaseJobSource

class AshbySource(BaseJobSource):
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            
            soup = BeautifulSoup(html, "html.parser")
            
            title = ""
            title_el = soup.find("h1")
            if title_el:
                title = title_el.get_text(strip=True)
                
            company = ""
            # Ashby often puts company name in title or meta tags
            if soup.title:
                parts = soup.title.string.split("-")
                if len(parts) > 1:
                    company = parts[-1].strip()
            
            location = ""
            # Look for typical location elements, Ashby uses a few different classes
            loc_els = soup.find_all("p")
            for el in loc_els:
                text = el.get_text(strip=True).lower()
                if "remote" in text or "hybrid" in text or "," in text:
                    # Very simple heuristic
                    location = el.get_text(strip=True)
                    break
                    
            description = ""
            # Ashby description is often just the main text container
            main_content = soup.find("main") or soup.find("div", role="main")
            if main_content:
                description = main_content.get_text(separator="\n", strip=True)
            else:
                # Remove header/footer
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.decompose()
                description = soup.get_text(separator="\n", strip=True)
                
            return {
                "url": url,
                "source": "ashby",
                "title": title,
                "company": company,
                "location": location,
                "description": description[:10000],
                "raw_metadata": {}
            }
