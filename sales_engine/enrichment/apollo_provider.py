import os
import httpx
from typing import List, Optional
from backend.schemas import CompanyEnrichment, ContactCandidate
from sales_engine.enrichment.base_company_provider import BaseCompanyProvider
from sales_engine.enrichment.base_contact_provider import BaseContactProvider

class ApolloProvider(BaseCompanyProvider, BaseContactProvider):
    def __init__(self):
        self.api_key = os.getenv("APOLLO_API_KEY")
        self.base_url = "https://api.apollo.io/v1"
        
    def get_provider_name(self) -> str:
        return "apollo"
        
    async def enrich_company(self, domain: Optional[str] = None, name: Optional[str] = None) -> Optional[CompanyEnrichment]:
        if not self.api_key:
            return None
            
        payload = {}
        if domain:
            payload["domain"] = domain
        elif name:
            payload["name"] = name
        else:
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{self.base_url}/organizations/enrich",
                    json=payload,
                    headers={"Cache-Control": "no-cache", "X-Api-Key": self.api_key},
                    timeout=10.0
                )
                
                if res.status_code != 200:
                    return None
                    
                data = res.json().get("organization", {})
                if not data:
                    return None
                    
                return CompanyEnrichment(
                    company_name=data.get("name"),
                    company_domain=data.get("primary_domain"),
                    website=data.get("website_url"),
                    industry=data.get("industry"),
                    employee_count=data.get("estimated_num_employees"),
                    country=data.get("country"),
                    city=data.get("city"),
                    linkedin_company_url=data.get("linkedin_url"),
                    source="apollo",
                    domain_status="existing" if domain else "provider_resolved"
                )
        except Exception:
            return None

    async def search_contacts(self, company_domain: str, titles: List[str], limit: int = 5) -> List[ContactCandidate]:
        if not self.api_key or not company_domain:
            return []
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{self.base_url}/mixed_people/search",
                    json={
                        "q_organization_domains": company_domain,
                        "person_titles": titles,
                        "per_page": limit
                    },
                    headers={"Cache-Control": "no-cache", "X-Api-Key": self.api_key},
                    timeout=10.0
                )
                
                if res.status_code != 200:
                    return []
                    
                people = res.json().get("people", [])
                candidates = []
                for p in people:
                    candidates.append(ContactCandidate(
                        first_name=p.get("first_name"),
                        last_name=p.get("last_name"),
                        full_name=f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                        job_title=p.get("title"),
                        seniority=p.get("seniority"),
                        department=p.get("departments", [""])[0] if p.get("departments") else None,
                        company_domain=company_domain,
                        work_email=p.get("email"),
                        email_status=p.get("email_status"),
                        linkedin_url=p.get("linkedin_url"),
                        provider="apollo",
                        provider_person_id=p.get("id")
                    ))
                return candidates
        except Exception:
            return []

    async def find_work_email(self, first_name: str, last_name: str, company_domain: str) -> Optional[str]:
        # Apollo's main email finder is typically through enrichment if ID is known, 
        # or we just rely on Hunter for email finder.
        return None
        
    async def verify_email(self, email: str) -> Optional[str]:
        # Apollo doesn't have a standalone verifier endpoint in this MVP logic
        return None
