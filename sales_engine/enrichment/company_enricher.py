from typing import List, Optional, Tuple
from backend.schemas import CompanyLead, CompanyEnrichment, ProviderUsageTracker
from sales_engine.enrichment.base_company_provider import BaseCompanyProvider
from sales_engine.enrichment.exceptions import ProviderError, ProviderEmptyResult

class CompanyEnricher:
    def __init__(self, providers: List[BaseCompanyProvider], tracker: ProviderUsageTracker):
        self.providers = providers
        self.tracker = tracker
        
    def _track_call(self, provider_name: str):
        if provider_name == "apollo":
            self.tracker.apollo_company_calls += 1
        elif provider_name == "hunter":
            # Hunter domain search can also be considered company call
            # But the schema doesn't have a hunter_company_calls. We can just use hunter_domain_search_calls
            self.tracker.hunter_domain_search_calls += 1
            
    async def enrich(self, lead: CompanyLead) -> Tuple[Optional[CompanyEnrichment], List[str]]:
        """Enrich company using trusted existing data or providers. Returns (enrichment, errors)."""
        
        # Prefer existing trusted info
        domain = lead.company_domain
        name = lead.company_name or lead.source_company_key
        errors = []
        
        for provider in self.providers:
            try:
                self._track_call(provider.get_provider_name())
                result = await provider.enrich_company(domain=domain, name=name)
                if result:
                    # Merge logic: do not overwrite stronger existing data
                    if domain:
                        result.company_domain = domain
                        result.domain_status = "existing"
                    if name and not result.company_name:
                        result.company_name = name
                    return result, errors
            except ProviderEmptyResult:
                pass
            except ProviderError as e:
                errors.append(str(e))
            except Exception as e:
                errors.append(f"Unexpected error from {provider.get_provider_name()}: {str(e)}")
                
        # If no provider succeeded, create partial from existing if domain exists
        if domain:
            return CompanyEnrichment(
                company_name=name,
                company_domain=domain,
                source="internal",
                domain_status="existing"
            ), errors
            
        return None, errors
