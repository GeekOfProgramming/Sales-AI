import httpx
import json
from typing import Dict, Any
from bs4 import BeautifulSoup
from sales_engine.sources.base_job_source import BaseJobSource

class LeverSource(BaseJobSource):
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        from urllib.parse import urlparse
        
        # Extract company and job_id from URL: https://jobs.lever.co/company/job_id
        path_parts = [p for p in urlparse(url).path.split('/') if p]
        
        api_data = None
        if len(path_parts) >= 2:
            company_slug = path_parts[0]
            job_id = path_parts[1]
            api_url = f"https://api.lever.co/v0/postings/{company_slug}/{job_id}"
            
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    api_resp = await client.get(api_url)
                    if api_resp.status_code == 200:
                        api_data = api_resp.json()
            except Exception:
                pass
                
        if api_data:
            # Prefer structured API data
            return {
                "url": url,
                "source": "lever",
                "title": api_data.get("text", ""),
                "company": "",  # Real name not available in this endpoint, will be resolved later
                "location": api_data.get("categories", {}).get("location", ""),
                "remote_status": api_data.get("workplaceType", ""),
                "employment_type": api_data.get("categories", {}).get("commitment", ""),
                "department": api_data.get("categories", {}).get("department", ""),
                "description": api_data.get("descriptionPlain", "")[:10000],
                "raw_metadata": {"api_data": api_data, "source_company_key": company_slug}
            }
            
        # Fallback to HTML
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            
            soup = BeautifulSoup(html, "html.parser")
            
            # Lever typically has a clean DOM structure for job postings
            title_el = soup.find(class_="posting-headline")
            title = title_el.find("h2").get_text(strip=True) if title_el and title_el.find("h2") else ""
            
            categories_el = soup.find(class_="posting-categories")
            location = ""
            department = ""
            employment_type = ""
            
            if categories_el:
                loc_el = categories_el.find(class_="location")
                if loc_el: location = loc_el.get_text(strip=True)
                dept_el = categories_el.find(class_="department")
                if dept_el: department = dept_el.get_text(strip=True)
                type_el = categories_el.find(class_="commitment")
                if type_el: employment_type = type_el.get_text(strip=True)
                
            # Description is often in a specific div
            desc_divs = soup.find_all("div", class_="section-wrapper")
            description = ""
            for d in desc_divs:
                description += d.get_text(separator="\n", strip=True) + "\n\n"
                
            # If empty, fallback
            if not description:
                description = soup.get_text(separator=" ", strip=True)
                
            # Sometimes company name is in the title tag like "Company Name - Job Title"
            company = ""
            if soup.title:
                parts = soup.title.string.split("-")
                if len(parts) > 1:
                    company = parts[0].strip()
            
            company_slug = path_parts[0] if path_parts else ""
            return {
                "url": url,
                "source": "lever",
                "title": title,
                "company": company,
                "location": location,
                "employment_type": employment_type,
                "department": department,
                "description": description[:10000],
                "raw_metadata": {"source_company_key": company_slug}
            }
