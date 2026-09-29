import os
import httpx
from typing import List, Optional
from backend.schemas import CompanyEnrichment, ContactCandidate
from sales_engine.enrichment.base_company_provider import BaseCompanyProvider
from sales_engine.enrichment.base_contact_provider import BaseContactProvider

class HunterProvider(BaseCompanyProvider, BaseContactProvider):
    def __init__(self):
        self.api_key = os.getenv("HUNTER_API_KEY")
        self.base_url = "https://api.hunter.io/v2"
        
    def get_provider_name(self) -> str:
        return "hunter"
        
    async def enrich_company(self, domain: Optional[str] = None, name: Optional[str] = None) -> Optional[CompanyEnrichment]:
        # Hunter is primarily for emails, but Domain Search gives some company data
        if not self.api_key or not domain:
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/domain-search",
                    params={"domain": domain, "api_key": self.api_key, "limit": 1},
                    timeout=10.0
                )
                
                if res.status_code != 200:
                    return None
                    
                data = res.json().get("data", {})
                if not data:
                    return None
                    
                return CompanyEnrichment(
                    company_name=data.get("organization"),
                    company_domain=data.get("domain"),
                    industry=data.get("industry"),
                    country=data.get("country"),
                    source="hunter",
                    domain_status="existing"
                )
        except Exception:
            return None

    async def search_contacts(self, company_domain: str, titles: List[str], limit: int = 5) -> List[ContactCandidate]:
        if not self.api_key or not company_domain:
            return []
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/domain-search",
                    params={"domain": company_domain, "api_key": self.api_key, "limit": limit},
                    timeout=10.0
                )
                
                if res.status_code != 200:
                    return []
                    
                emails = res.json().get("data", {}).get("emails", [])
                candidates = []
                for e in emails:
                    title = e.get("position")
                    if not title: continue
                    
                    candidates.append(ContactCandidate(
                        first_name=e.get("first_name"),
                        last_name=e.get("last_name"),
                        full_name=f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                        job_title=title,
                        department=e.get("department"),
                        company_domain=company_domain,
                        work_email=e.get("value"),
                        email_status="verified" if e.get("confidence") and e.get("confidence", 0) > 90 else "likely",
                        email_confidence=e.get("confidence", 0),
                        linkedin_url=e.get("linkedin"),
                        provider="hunter"
                    ))
                return candidates
        except Exception:
            return []

    async def find_work_email(self, first_name: str, last_name: str, company_domain: str) -> Optional[str]:
        if not self.api_key: return None
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/email-finder",
                    params={"domain": company_domain, "first_name": first_name, "last_name": last_name, "api_key": self.api_key},
                    timeout=10.0
                )
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    return data.get("email")
        except Exception:
            pass
        return None
        
    async def verify_email(self, email: str) -> Optional[str]:
        if not self.api_key: return None
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/email-verifier",
                    params={"email": email, "api_key": self.api_key},
                    timeout=10.0
                )
                if res.status_code == 200:
                    status = res.json().get("data", {}).get("status")
                    return status if status else "unknown"
        except Exception:
            pass
        return "unknown"
