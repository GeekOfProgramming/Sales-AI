import uuid
from typing import List, Optional, Dict
from backend.schemas import ContactCandidate, ProviderUsageTracker
from sales_engine.enrichment.base_contact_provider import BaseContactProvider
from sales_engine.enrichment.buyer_role_ranker import BuyerRoleRanker

class ContactEnricher:
    def __init__(self, providers: List[BaseContactProvider], ranker: BuyerRoleRanker, tracker: ProviderUsageTracker):
        self.providers = providers
        self.ranker = ranker
        self.tracker = tracker
        
    def _track_call(self, provider_name: str, call_type: str):
        if provider_name == "apollo":
            if call_type == "search": self.tracker.apollo_people_search_calls += 1
            elif call_type == "enrich": self.tracker.apollo_people_enrichment_calls += 1
        elif provider_name == "hunter":
            if call_type == "search": self.tracker.hunter_domain_search_calls += 1
            elif call_type == "find": self.tracker.hunter_email_finder_calls += 1
            elif call_type == "verify": self.tracker.hunter_email_verifier_calls += 1

    async def discover_and_enrich(self, domain: str, max_contacts: int) -> List[ContactCandidate]:
        if not domain:
            return []
            
        all_candidates = []
        titles = self.ranker.target_roles
        
        # 1. Search across providers
        for provider in self.providers:
            self._track_call(provider.get_provider_name(), "search")
            candidates = await provider.search_contacts(domain, titles, limit=max_contacts * 2)
            all_candidates.extend(candidates)
            
        # 2. Deduplicate
        unique_contacts = {}
        for c in all_candidates:
            # Strongest identifier: provider ID
            key = None
            if c.provider_person_id:
                key = f"{c.provider}_{c.provider_person_id}"
            elif c.work_email:
                key = f"email_{c.work_email.lower().strip()}"
            elif c.full_name and c.company_domain:
                key = f"name_{c.full_name.lower().strip()}_{c.company_domain.lower().strip()}"
            else:
                key = str(uuid.uuid4())
                
            if key not in unique_contacts:
                c.data_sources = [c.provider] if c.provider else []
                unique_contacts[key] = c
            else:
                # Merge logic
                existing = unique_contacts[key]
                if c.provider and c.provider not in existing.data_sources:
                    existing.data_sources.append(c.provider)
                if not existing.work_email and c.work_email:
                    existing.work_email = c.work_email
                    existing.email_status = c.email_status
                    existing.email_confidence = c.email_confidence
        
        candidates = list(unique_contacts.values())
        
        # 3. Email finding and verification for candidates without email or unverified
        for c in candidates:
            # If no email, try to find one
            if not c.work_email and c.first_name and c.last_name:
                for provider in self.providers:
                    self._track_call(provider.get_provider_name(), "find")
                    email = await provider.find_work_email(c.first_name, c.last_name, domain)
                    if email:
                        c.work_email = email
                        c.email_source = provider.get_provider_name()
                        c.email_status = "unknown"
                        break
                        
            # Verify if unknown
            if c.work_email and c.email_status in ["unknown", "likely", None]:
                for provider in self.providers:
                    self._track_call(provider.get_provider_name(), "verify")
                    status = await provider.verify_email(c.work_email)
                    if status and status != "unknown":
                        c.email_status = status
                        c.email_source = provider.get_provider_name()
                        break
                        
        # 4. Rank and Score
        for c in candidates:
            eval_res = self.ranker.evaluate(c.job_title or "")
            c.buyer_role_match = eval_res["match"]
            score = eval_res["score"]
            
            # Domain match bonus
            if c.company_domain and domain and c.company_domain.lower() == domain.lower():
                score += 10
            elif c.company_domain:
                # Wrong company penalty
                score = 0
                
            # Email bonus
            if c.work_email:
                if c.email_status == "verified":
                    score += 30
                elif c.email_status == "likely":
                    score += 15
                elif c.email_status == "unknown":
                    score += 5
                    
            c.contact_score = min(score, 100)
            
        # 5. Sort by score
        candidates.sort(key=lambda x: x.contact_score, reverse=True)
        return candidates[:max_contacts]
