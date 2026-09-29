import json
import httpx
from typing import Dict, Any
from bs4 import BeautifulSoup
from sales_engine.sources.base_job_source import BaseJobSource

class GenericJobPage(BaseJobSource):
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
            
            # Try to find JSON-LD JobPosting
            soup = BeautifulSoup(html, "html.parser")
            json_ld_scripts = soup.find_all("script", type="application/ld+json")
            
            job_posting = None
            for script in json_ld_scripts:
                try:
                    data = json.loads(script.string)
                    # Handle both list and dict formats
                    if isinstance(data, list):
                        for item in data:
                            if item.get("@type") == "JobPosting":
                                job_posting = item
                                break
                    elif isinstance(data, dict):
                        if data.get("@type") == "JobPosting":
                            job_posting = data
                    
                    if job_posting:
                        break
                except Exception:
                    continue
                    
            if job_posting:
                # Extract from structured data
                company_name = ""
                org = job_posting.get("hiringOrganization")
                if isinstance(org, dict):
                    company_name = org.get("name", "")
                    
                title = job_posting.get("title", "")
                posted_date = job_posting.get("datePosted", "")
                
                # Try to get plain text description from HTML description field if available
                desc_html = job_posting.get("description", "")
                desc_soup = BeautifulSoup(desc_html, "html.parser") if desc_html else soup
                description = desc_soup.get_text(separator=" ", strip=True)
                
                return {
                    "url": url,
                    "source": "generic",
                    "title": title,
                    "company": company_name,
                    "location": "", # Location in JSON-LD can be complex, skip for simplicity unless needed
                    "posted_date": posted_date,
                    "description": description[:10000],
                    "raw_metadata": {"json_ld": job_posting}
                }
            
            # Fallback to DOM extraction
            title = soup.title.string if soup.title else ""
            
            # Remove scripts and styles before extracting text
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
                
            text = soup.get_text(separator=" ", strip=True)
            
            return {
                "url": url,
                "source": "generic",
                "title": title,
                "company": "",
                "location": "",
                "posted_date": "",
                "description": text[:10000],
                "raw_metadata": {}
            }
