import httpx
from typing import Dict, Any
from bs4 import BeautifulSoup
from sales_engine.sources.base_job_source import BaseJobSource

class GreenhouseSource(BaseJobSource):
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        from urllib.parse import urlparse
        import re
        
        # Extact company and job_id from URL: https://boards.greenhouse.io/company/jobs/123
        # or https://boards.greenhouse.io/embed/job_app?for=company&token=123
        path_parts = [p for p in urlparse(url).path.split('/') if p]
        
        api_data = None
        if len(path_parts) >= 3 and path_parts[1] == "jobs":
            company_slug = path_parts[0]
            job_id = path_parts[2]
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}"
            
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    api_resp = await client.get(api_url)
                    if api_resp.status_code == 200:
                        api_data = api_resp.json()
            except Exception:
                pass
                
        if api_data:
            # Strip html tags from description
            desc_html = api_data.get("content", "")
            desc_soup = BeautifulSoup(desc_html, "html.parser")
            description = desc_soup.get_text(separator="\n", strip=True)
            
            return {
                "url": url,
                "source": "greenhouse",
                "title": api_data.get("title", ""),
                "company": api_data.get("company_name", ""),
                "posted_date": api_data.get("first_published", api_data.get("updated_at", "")),
                "location": api_data.get("location", {}).get("name", ""),
                "description": description[:10000],
                "raw_metadata": {"api_data": api_data, "source_company_key": company_slug}
            }
            
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
