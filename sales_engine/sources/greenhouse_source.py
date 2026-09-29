import httpx
from typing import Dict, Any
from bs4 import BeautifulSoup
from sales_engine.sources.base_job_source import BaseJobSource

class GreenhouseSource(BaseJobSource):
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            
            soup = BeautifulSoup(html, "html.parser")
            
            title = ""
            title_el = soup.find("h1", class_="app-title")
            if title_el:
                title = title_el.get_text(strip=True)
                
            company = ""
            company_el = soup.find("span", class_="company-name")
            if company_el:
                # Typically "at Company"
                company_text = company_el.get_text(strip=True)
                if company_text.lower().startswith("at "):
                    company = company_text[3:].strip()
                else:
                    company = company_text
                    
            location = ""
            loc_el = soup.find("div", class_="location")
            if loc_el:
                location = loc_el.get_text(strip=True)
                
            description = ""
            desc_el = soup.find("div", id="content")
            if desc_el:
                description = desc_el.get_text(separator="\n", strip=True)
            else:
                description = soup.get_text(separator=" ", strip=True)
                
            return {
                "url": url,
                "source": "greenhouse",
                "title": title,
                "company": company,
                "location": location,
                "description": description[:10000],
                "raw_metadata": {}
            }
