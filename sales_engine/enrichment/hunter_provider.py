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

class HunterProvider(BaseCompanyProvider, BaseContactProvider):
    def __init__(self):
        self.api_key = os.getenv("HUNTER_API_KEY")
        self.base_url = "https://api.hunter.io/v2"
        
    def get_provider_name(self) -> str:
        return "hunter"
        
    def _handle_response_errors(self, res: httpx.Response, provider: str):
        if res.status_code == 401 or res.status_code == 403:
            raise ProviderAuthError(f"Authentication failed (HTTP {res.status_code})", provider)
        elif res.status_code == 429:
            raise ProviderRateLimitError("Rate limit exceeded", provider)
        elif res.status_code >= 500:
            raise ProviderError(f"Server error (HTTP {res.status_code})", provider)
        elif res.status_code >= 400:
            # Hunter returns 400 for bad requests (like no result found in some endpoints)
            # We will handle empty results cleanly if it's 200 with no data. If 4xx, it's an error.
            raise ProviderError(f"Client error (HTTP {res.status_code})", provider)
            
    async def enrich_company(self, domain: Optional[str] = None, name: Optional[str] = None) -> Optional[CompanyEnrichment]:
        if not self.api_key:
            raise ProviderConfigError("HUNTER_API_KEY is not configured", self.get_provider_name())
        if not domain:
            raise ProviderEmptyResult("Hunter requires a domain for company enrichment", self.get_provider_name())
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/domain-search",
                    params={"domain": domain, "api_key": self.api_key, "limit": 1},
                    timeout=10.0
                )
                
                # 404 from Hunter sometimes means no data found for domain.
                if res.status_code == 404:
                    raise ProviderEmptyResult("No organization found", self.get_provider_name())
                    
                self._handle_response_errors(res, self.get_provider_name())
                    
                data = res.json().get("data", {})
                if not data:
                    raise ProviderEmptyResult("No organization found", self.get_provider_name())
                    
                return CompanyEnrichment(
                    company_name=data.get("organization"),
                    company_domain=data.get("domain"),
                    industry=data.get("industry"),
                    country=data.get("country"),
                    source="hunter",
                    domain_status="existing"
                )
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Company enrichment timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())

    async def search_contacts(self, company_domain: str, titles: List[str], limit: int = 5) -> List[ContactCandidate]:
        if not self.api_key:
            raise ProviderConfigError("HUNTER_API_KEY is not configured", self.get_provider_name())
        if not company_domain:
            return []
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/domain-search",
                    params={"domain": company_domain, "api_key": self.api_key, "limit": limit},
                    timeout=15.0
                )
                
                if res.status_code == 404:
                    raise ProviderEmptyResult("No contacts found", self.get_provider_name())
                    
                self._handle_response_errors(res, self.get_provider_name())
                    
                emails = res.json().get("data", {}).get("emails", [])
                if not emails:
                    raise ProviderEmptyResult("No contacts found", self.get_provider_name())
                    
                candidates = []
                for e in emails:
                    title = e.get("position")
                    if not title: continue
                    
                    # Domain Search confidence > 90 -> likely, not verified (as requested)
                    conf = e.get("confidence", 0)
                    email_status = "likely" if conf > 90 else "unknown"
                    
                    candidates.append(ContactCandidate(
                        first_name=e.get("first_name"),
                        last_name=e.get("last_name"),
                        full_name=f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                        job_title=title,
                        department=e.get("department"),
                        company_domain=company_domain,
                        work_email=e.get("value"),
                        email_status=email_status,
                        email_confidence=conf,
                        linkedin_url=e.get("linkedin"),
                        provider="hunter"
                    ))
                return candidates
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Contact search timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())

    async def find_work_email(self, first_name: str, last_name: str, company_domain: str, person_id: Optional[str] = None) -> Optional[str]:
        if not self.api_key:
            raise ProviderConfigError("HUNTER_API_KEY is not configured", self.get_provider_name())
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/email-finder",
                    params={"domain": company_domain, "first_name": first_name, "last_name": last_name, "api_key": self.api_key},
                    timeout=10.0
                )
                if res.status_code == 404 or res.status_code == 400:
                    raise ProviderEmptyResult("No email found", self.get_provider_name())
                self._handle_response_errors(res, self.get_provider_name())
                
                data = res.json().get("data", {})
                return data.get("email")
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Email finding timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())
        
    async def verify_email(self, email: str) -> Optional[str]:
        if not self.api_key:
            raise ProviderConfigError("HUNTER_API_KEY is not configured", self.get_provider_name())
            
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/email-verifier",
                    params={"email": email, "api_key": self.api_key},
                    timeout=10.0
                )
                if res.status_code == 404 or res.status_code == 400:
                    return "unknown"
                self._handle_response_errors(res, self.get_provider_name())
                
                status = res.json().get("data", {}).get("status")
                
                # Normalization
                if status == "valid":
                    return "verified"
                elif status in ["accept_all", "webmail", "disposable"]:
                    return "risky"
                elif status == "invalid":
                    return "not_valid"
                else:
                    return "unknown"
                    
        except httpx.TimeoutException:
            raise ProviderTimeoutError("Email verification timed out", self.get_provider_name())
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {str(e)}", self.get_provider_name())
