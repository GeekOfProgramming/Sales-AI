import os
import httpx
from typing import List, Optional
from backend.schemas import CompanyEnrichment, ContactCandidate
from sales_engine.enrichment.base_company_provider import BaseCompanyProvider
from sales_engine.enrichment.base_contact_provider import BaseContactProvider
from sales_engine.enrichment.exceptions import (
    ProviderConfigError, ProviderAuthError, ProviderRateLimitError, 
    ProviderTimeoutError, ProviderError, ProviderEmptyResult
)

class ApolloProvider(BaseCompanyProvider, BaseContactProvider):
    def __init__(self):
        self.api_key = os.getenv("APOLLO_API_KEY")
        self.base_url = "https://api.apollo.io/api/v1"
        
    def get_provider_name(self) -> str:
        return "apollo"
        
    def _handle_response_errors(self, res: httpx.Response, provider: str):
        if res.status_code == 401 or res.status_code == 403:
            raise ProviderAuthError(f"Authentication failed (HTTP {res.status_code})", provider)
        elif res.status_code == 429:
            raise ProviderRateLimitError("Rate limit exceeded", provider)
        elif res.status_code >= 500:
            raise ProviderError(f"Server error (HTTP {res.status_code})", provider)
        elif res.status_code >= 400:
            raise ProviderError(f"Client error (HTTP {res.status_code})", provider)
            
    async def enrich_company(self, domain: Optional[str] = None, name: Optional[str] = None) -> Optional[CompanyEnrichment]:
        if not self.api_key:
            raise ProviderConfigError("APOLLO_API_KEY is not configured", self.get_provider_name())
            
        params = {"api_key": self.api_key}
        if domain:
            params["domain"] = domain
        elif name:
            params["organization_name"] = name
        else:
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/organizations/enrich",
                    params=params,
                    timeout=10.0
                )
                self._handle_response_errors(res, self.get_provider_name())
                
                data = res.json().get("organization", {})
                if not data:
                    raise ProviderEmptyResult("No organization found", self.get_provider_name())
                    
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
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Company enrichment timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())

    async def search_contacts(self, company_domain: str, titles: List[str], limit: int = 5) -> List[ContactCandidate]:
        if not self.api_key:
            raise ProviderConfigError("APOLLO_API_KEY is not configured", self.get_provider_name())
        if not company_domain:
            return []
            
        try:
            payload = {
                "api_key": self.api_key,
                "q_organization_domains_list": [company_domain],
                "per_page": limit,
                "reveal_personal_emails": False,
                "reveal_phone_number": False
            }
            if titles:
                payload["person_titles"] = titles
                
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{self.base_url}/mixed_people/api_search",
                    json=payload,
                    timeout=15.0
                )
                self._handle_response_errors(res, self.get_provider_name())
                
                people = res.json().get("people", [])
                if not people:
                    raise ProviderEmptyResult("No contacts found", self.get_provider_name())
                    
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
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Contact search timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())

    async def find_work_email(self, first_name: str, last_name: str, company_domain: str, person_id: Optional[str] = None) -> Optional[str]:
        if not self.api_key:
            raise ProviderConfigError("APOLLO_API_KEY is not configured", self.get_provider_name())
            
        payload = {
            "api_key": self.api_key,
            "reveal_personal_emails": False,
            "reveal_phone_number": False
        }
        
        if person_id:
            payload["id"] = person_id
        else:
            payload["first_name"] = first_name
            payload["last_name"] = last_name
            payload["domain"] = company_domain
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{self.base_url}/people/match",
                    json=payload,
                    timeout=10.0
                )
                self._handle_response_errors(res, self.get_provider_name())
                
                data = res.json().get("person", {})
                if not data:
                    raise ProviderEmptyResult("Email enrichment returned no person", self.get_provider_name())
                return data.get("email")
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Email finding timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())
            
    async def verify_email(self, email: str) -> Optional[str]:
        # Apollo doesn't have a standalone verifier endpoint in this MVP logic
        return None
