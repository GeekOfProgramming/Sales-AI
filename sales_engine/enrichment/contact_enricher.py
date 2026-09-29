import uuid
from typing import List, Optional, Dict
from backend.schemas import ContactCandidate, ProviderUsageTracker
from sales_engine.enrichment.base_contact_provider import BaseContactProvider
from sales_engine.enrichment.buyer_role_ranker import BuyerRoleRanker
from sales_engine.enrichment.exceptions import ProviderError, ProviderEmptyResult

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

    async def discover_and_enrich(self, domain: str, max_contacts: int, errors: List[str]) -> List[ContactCandidate]:
        if not domain:
            return []
            
        all_candidates = []
        titles = self.ranker.target_roles
        
        # 1. Search across providers
        for provider in self.providers:
            try:
                self._track_call(provider.get_provider_name(), "search")
                candidates = await provider.search_contacts(domain, titles, limit=max_contacts * 2)
                all_candidates.extend(candidates)
            except ProviderEmptyResult:
                pass # Normal, no results found
            except ProviderError as e:
                errors.append(str(e))
            except Exception as e:
                errors.append(f"Unexpected error from {provider.get_provider_name()}: {str(e)}")
            
        # 2. Deduplicate
        unique_contacts = []
        for c in all_candidates:
            c.data_sources = [c.provider] if c.provider else []
            
            # Find existing contact to merge with
            matched_existing = None
            for existing in unique_contacts:
                # 1. Work email match
                if c.work_email and existing.work_email and c.work_email.lower() == existing.work_email.lower():
                    matched_existing = existing
                    break
                # 2. Provider ID match
                if c.provider_person_id and existing.provider_person_id and c.provider_person_id == existing.provider_person_id and c.provider == existing.provider:
                    matched_existing = existing
                    break
                # 3. Name + Domain match
                if c.full_name and existing.full_name and c.company_domain and existing.company_domain:
                    if c.full_name.lower() == existing.full_name.lower() and c.company_domain.lower() == existing.company_domain.lower():
                        matched_existing = existing
                        break
                        
            if matched_existing:
                # Merge
                if c.provider and c.provider not in matched_existing.data_sources:
                    matched_existing.data_sources.append(c.provider)
                if not matched_existing.work_email and c.work_email:
                    matched_existing.work_email = c.work_email
                    matched_existing.email_status = c.email_status
                    matched_existing.email_confidence = c.email_confidence
                    matched_existing.email_source = c.email_source
                if not matched_existing.provider_person_id and c.provider_person_id:
                    matched_existing.provider_person_id = c.provider_person_id
                    matched_existing.provider = c.provider
            else:
                unique_contacts.append(c)
        
        candidates = unique_contacts
        
        # 3. Email finding and verification for candidates without email or unverified
        for c in candidates:
            # If no email, try to find one
            if not c.work_email and c.first_name and c.last_name:
                for provider in self.providers:
                    try:
                        call_type = "enrich" if provider.get_provider_name() == "apollo" else "find"
                        self._track_call(provider.get_provider_name(), call_type)
                        
                        # Pass person_id if available and it's the same provider
                        person_id = c.provider_person_id if c.provider == provider.get_provider_name() else None
                        
                        email = await provider.find_work_email(c.first_name, c.last_name, domain, person_id=person_id)
                        if email:
                            c.work_email = email
                            c.email_source = provider.get_provider_name()
                            c.email_status = "unknown"
                            if provider.get_provider_name() not in c.data_sources:
                                c.data_sources.append(provider.get_provider_name())
                            break
                    except ProviderEmptyResult:
                        pass
                    except ProviderError as e:
                        errors.append(str(e))
                    except Exception as e:
                        errors.append(f"Unexpected error from {provider.get_provider_name()}: {str(e)}")
                        
            # Verify if unknown
            if c.work_email and c.email_status in ["unknown", "likely", None]:
                for provider in self.providers:
                    try:
                        self._track_call(provider.get_provider_name(), "verify")
                        status = await provider.verify_email(c.work_email)
                        if status and status != "unknown":
                            c.email_status = status
                            c.email_source = provider.get_provider_name()
                            if provider.get_provider_name() not in c.data_sources:
                                c.data_sources.append(provider.get_provider_name())
                            break
                    except ProviderEmptyResult:
                        pass
                    except ProviderError as e:
                        errors.append(str(e))
                    except Exception as e:
                        errors.append(f"Unexpected error from {provider.get_provider_name()}: {str(e)}")
                        
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
