from typing import List, Dict, Any, Optional
from backend.schemas import CompanyLead, EnrichedLead, ProviderUsageTracker
from sales_engine.enrichment.apollo_provider import ApolloProvider
from sales_engine.enrichment.hunter_provider import HunterProvider
from sales_engine.enrichment.company_enricher import CompanyEnricher
from sales_engine.enrichment.contact_enricher import ContactEnricher
from sales_engine.enrichment.buyer_role_ranker import BuyerRoleRanker

class EnrichmentOrchestrator:
    def __init__(self):
        pass
        
    async def enrich_leads(self, leads: List[CompanyLead], website_profile: Optional[Dict[str, Any]] = None, 
                           qualified_only: bool = True, max_contacts_per_lead: int = 5,
                           providers: List[str] = None) -> Dict[str, Any]:
                           
        if providers is None:
            providers = ["apollo", "hunter"]
            
        tracker = ProviderUsageTracker()
        
        # Init providers
        comp_providers = []
        cont_providers = []
        for p in providers:
            if p == "apollo":
                ap = ApolloProvider()
                comp_providers.append(ap)
                cont_providers.append(ap)
            elif p == "hunter":
                hp = HunterProvider()
                comp_providers.append(hp)
                cont_providers.append(hp)
            else:
                return {
                    "status": "error",
                    "leads_received": len(leads),
                    "leads_attempted": 0,
                    "leads_enriched": 0,
                    "partial": 0,
                    "failed": len(leads),
                    "provider_usage": tracker,
                    "leads": [],
                    "errors": [f"Unknown provider: {p}"]
                }
            
        company_enricher = CompanyEnricher(comp_providers, tracker)
        
        buyer_roles = []
        if website_profile and "buyer_roles" in website_profile:
            buyer_roles = website_profile["buyer_roles"]
            
        ranker = BuyerRoleRanker(custom_roles=buyer_roles if buyer_roles else None)
        contact_enricher = ContactEnricher(cont_providers, ranker, tracker)
        
        results = []
        attempted = 0
        enriched = 0
        partial = 0
        failed = 0
        global_errors = []
        
        for lead in leads:
            if qualified_only and not lead.qualified:
                continue
                
            attempted += 1
            enriched_lead = EnrichedLead(
                base_lead=lead,
                buyer_roles_searched=ranker.target_roles,
                providers_used=providers
            )
            
            try:
                # 1. Company Enrichment
                comp_enrich, comp_errors = await company_enricher.enrich(lead)
                enriched_lead.enrichment_errors.extend(comp_errors)
                
                if comp_enrich:
                    enriched_lead.company_enrichment = comp_enrich
                    
                    # 2. Contact Discovery
                    # Prefer resolved domain if available
                    domain_to_search = comp_enrich.company_domain or lead.company_domain
                    if domain_to_search:
                        contacts = await contact_enricher.discover_and_enrich(
                            domain_to_search, max_contacts_per_lead, enriched_lead.enrichment_errors
                        )
                        enriched_lead.contacts = contacts
                        if contacts:
                            enriched_lead.best_contact = contacts[0]
                            
                # Determine status
                has_comp = bool(enriched_lead.company_enrichment and enriched_lead.company_enrichment.source != "internal")
                has_cont = len(enriched_lead.contacts) > 0
                
                # "complete" should require: company enrichment, at least one suitable contact, 
                # and a sufficiently verified/usable work email
                has_verified_email = any(
                    c.work_email and c.email_status in ["verified", "likely"] 
                    for c in enriched_lead.contacts
                )
                
                if has_comp and has_cont and has_verified_email:
                    enriched_lead.enrichment_status = "complete"
                    enriched += 1
                elif has_comp or has_cont:
                    enriched_lead.enrichment_status = "partial"
                    partial += 1
                else:
                    if enriched_lead.enrichment_errors:
                        if any("is not configured" in err for err in enriched_lead.enrichment_errors):
                            if "no_provider_configured" not in enriched_lead.enrichment_errors:
                                enriched_lead.enrichment_errors.append("no_provider_configured")
                        enriched_lead.enrichment_status = "provider_error"
                    else:
                        enriched_lead.enrichment_status = "not_found"
                    failed += 1
                    
            except Exception as e:
                enriched_lead.enrichment_status = "provider_error"
                enriched_lead.enrichment_errors.append(f"Unexpected orchestrator error: {str(e)}")
                failed += 1
                
            results.append(enriched_lead)
            
        return {
            "status": "success",
            "leads_received": len(leads),
            "leads_attempted": attempted,
            "leads_enriched": enriched,
            "partial": partial,
            "failed": failed,
            "provider_usage": tracker,
            "leads": results,
            "errors": global_errors
        }
