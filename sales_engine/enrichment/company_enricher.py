from typing import List, Optional
from backend.schemas import CompanyLead, CompanyEnrichment
from sales_engine.enrichment.base_company_provider import BaseCompanyProvider

class CompanyEnricher:
    def __init__(self, providers: List[BaseCompanyProvider]):
        self.providers = providers
        
    async def enrich(self, lead: CompanyLead) -> Optional[CompanyEnrichment]:
        """Enrich company using trusted existing data or providers."""
        
        # Prefer existing trusted info
        domain = lead.company_domain
        name = lead.company_name or lead.source_company_key
        
        # If we have both, we can still enrich to get industry/employee count.
        # But we won't overwrite domain.
        
        for provider in self.providers:
            result = await provider.enrich_company(domain=domain, name=name)
            if result:
                # Merge logic: do not overwrite stronger existing data
                if domain:
                    result.company_domain = domain
                    result.domain_status = "existing"
                if name and not result.company_name:
                    result.company_name = name
                return result
                
        # If no provider succeeded, create partial from existing if domain exists
        if domain:
            return CompanyEnrichment(
                company_name=name,
                company_domain=domain,
                source="internal",
                domain_status="existing"
            )
            
        return None
