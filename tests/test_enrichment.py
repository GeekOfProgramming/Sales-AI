import pytest
import datetime
from unittest.mock import patch, AsyncMock, MagicMock
from backend.schemas import CompanyLead, CompanyEnrichment, ContactCandidate
from sales_engine.enrichment.enrichment_orchestrator import EnrichmentOrchestrator
from sales_engine.enrichment.buyer_role_ranker import BuyerRoleRanker
from sales_engine.enrichment.contact_enricher import ContactEnricher
from sales_engine.enrichment.company_enricher import CompanyEnricher
from sales_engine.enrichment.apollo_provider import ApolloProvider
from sales_engine.enrichment.hunter_provider import HunterProvider
from sales_engine.enrichment.base_contact_provider import BaseContactProvider

def get_dummy_lead(domain="acme.com", name="Acme"):
    return CompanyLead(
        company_domain=domain,
        company_name=name,
        source_company_key=name.lower(),
        job_count=1,
        relevant_job_count=1,
        lead_score=80,
        qualified=True,
        intent_score=10,
        fit_score=10,
        evidence_score=10,
        recency_score=10
    )

@pytest.mark.asyncio
async def test_company_enrichment_preserves_domain():
    # If lead has domain 'acme.com', provider returning 'other.com' shouldn't overwrite it
    lead = get_dummy_lead(domain="acme.com", name="Acme")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "organization": {
            "name": "Acme Inc",
            "primary_domain": "other.com",
            "industry": "Software"
        }
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        ap = ApolloProvider()
        ap.api_key = "test"
        enricher = CompanyEnricher([ap])
        
        res = await enricher.enrich(lead)
        assert res.company_domain == "acme.com"
        assert res.domain_status == "existing"
        assert res.industry == "Software"

@pytest.mark.asyncio
async def test_missing_domain_resolution():
    lead = get_dummy_lead(domain=None, name="Acme")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "organization": {
            "name": "Acme",
            "primary_domain": "acme.io"
        }
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        ap = ApolloProvider()
        ap.api_key = "test"
        enricher = CompanyEnricher([ap])
        
        res = await enricher.enrich(lead)
        assert res.company_domain == "acme.io"
        assert res.domain_status == "provider_resolved"

def test_buyer_role_ranking():
    ranker = BuyerRoleRanker(custom_roles=["BIM Manager"])
    
    # Exact
    res1 = ranker.evaluate("BIM Manager")
    assert res1["match"] == "exact"
    assert res1["score"] == 40
    
    # Strong
    res2 = ranker.evaluate("Senior BIM Manager")
    assert res2["match"] == "strong"
    assert res2["score"] == 30
    
    # Relevant
    res3 = ranker.evaluate("Head of Digital Delivery")
    assert res3["match"] == "relevant"
    assert res3["score"] == 20
    
    # Weak
    res4 = ranker.evaluate("CTO")
    assert res4["match"] == "weak"
    assert res4["score"] == 10
    
    # Irrelevant
    res5 = ranker.evaluate("Software Engineer")
    assert res5["match"] == "none"
    assert res5["score"] == 0

@pytest.mark.asyncio
async def test_contact_deduplication_and_scoring():
    # Setup mock candidates
    # Two candidates with same work email should deduplicate
    c1 = ContactCandidate(first_name="John", last_name="Doe", job_title="BIM Manager", work_email="john@acme.com", provider="apollo", company_domain="acme.com")
    c2 = ContactCandidate(first_name="John", last_name="Doe", job_title="BIM Manager", work_email="john@acme.com", provider="hunter", company_domain="acme.com")
    
    # Wrong company domain penalty
    c3 = ContactCandidate(first_name="Wrong", last_name="Guy", job_title="BIM Manager", company_domain="wrong.com")
    
    # Irrelevant title
    c4 = ContactCandidate(first_name="Ir", last_name="Rel", job_title="Janitor", company_domain="acme.com")
    
    class MockProvider(BaseContactProvider):
        def get_provider_name(self): return "mock"
        async def search_contacts(self, domain, titles, limit): return [c1, c2, c3, c4]
        async def find_work_email(self, fn, ln, dom): return None
        async def verify_email(self, email): return "verified" if email == "john@acme.com" else "unknown"
        
    from backend.schemas import ProviderUsageTracker
    tracker = ProviderUsageTracker()
    ranker = BuyerRoleRanker(["BIM Manager"])
    enricher = ContactEnricher([MockProvider()], ranker, tracker)
    
    contacts = await enricher.discover_and_enrich("acme.com", max_contacts=10)
    
    # Should be 3 contacts (c1/c2 merged, c3, c4)
    assert len(contacts) == 3
    
    best = contacts[0]
    assert best.work_email == "john@acme.com"
    assert "apollo" in best.data_sources
    assert "hunter" in best.data_sources
    assert best.email_source == "mock" # from verify
    assert best.email_status == "verified"
    
    # Check scoring: 40 (exact) + 10 (domain match) + 30 (verified) = 80
    assert best.contact_score == 80
    
    # c3 wrong domain should have 0 score despite exact title
    wrong_comp = next(c for c in contacts if c.first_name == "Wrong")
    assert wrong_comp.contact_score == 0
    
    # c4 irrelevant title should have 10 score (0 title + 10 domain)
    irrel = next(c for c in contacts if c.first_name == "Ir")
    assert irrel.contact_score == 10

@pytest.mark.asyncio
async def test_enrichment_orchestrator():
    lead = get_dummy_lead("test.com", "Test")
    
    orchestrator = EnrichmentOrchestrator()
    
    with patch("sales_engine.enrichment.apollo_provider.ApolloProvider.enrich_company", new_callable=AsyncMock) as m_comp, \
         patch("sales_engine.enrichment.apollo_provider.ApolloProvider.search_contacts", new_callable=AsyncMock) as m_search:
         
         m_comp.return_value = CompanyEnrichment(company_name="Test", company_domain="test.com", source="apollo")
         m_search.return_value = [ContactCandidate(first_name="A", last_name="B", job_title="BIM Manager", company_domain="test.com")]
         
         res = await orchestrator.enrich_leads([lead], providers=["apollo"])
         
         assert res["status"] == "success"
         assert res["leads_enriched"] == 0
         assert res["partial"] == 1 # Partial because no email
         
         en_lead = res["leads"][0]
         assert en_lead.company_enrichment.company_domain == "test.com"
         assert len(en_lead.contacts) == 1
         assert en_lead.contacts[0].contact_score > 0
