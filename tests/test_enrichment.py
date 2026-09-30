import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import Response, TimeoutException
from backend.schemas import CompanyLead, CompanyEnrichment, ContactCandidate, ProviderUsageTracker
from sales_engine.enrichment.enrichment_orchestrator import EnrichmentOrchestrator
from sales_engine.enrichment.buyer_role_ranker import BuyerRoleRanker
from sales_engine.enrichment.contact_enricher import ContactEnricher
from sales_engine.enrichment.company_enricher import CompanyEnricher
from sales_engine.enrichment.apollo_provider import ApolloProvider
from sales_engine.enrichment.hunter_provider import HunterProvider
from sales_engine.enrichment.base_contact_provider import BaseContactProvider
from sales_engine.enrichment.exceptions import (
    ProviderConfigError, ProviderAuthError, ProviderRateLimitError, 
    ProviderTimeoutError, ProviderError, ProviderEmptyResult
)

def get_dummy_lead(domain="acme.com", name="Acme", qualified=True):
    return CompanyLead(
        company_domain=domain,
        company_name=name,
        source_company_key=name.lower(),
        job_count=1,
        relevant_job_count=1,
        lead_score=80,
        qualified=qualified,
        intent_score=10,
        fit_score=10,
        evidence_score=10,
        recency_score=10
    )

@pytest.fixture
def hunter_provider():
    hp = HunterProvider()
    hp.api_key = "test_hunter_key"
    return hp

@pytest.fixture
def apollo_provider():
    ap = ApolloProvider()
    ap.api_key = "test_apollo_key"
    return ap

@pytest.mark.asyncio
async def test_apollo_organization_endpoint(apollo_provider):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"organization": {"name": "Acme", "primary_domain": "acme.com"}}
        mock_get.return_value = mock_response
        
        res = await apollo_provider.enrich_company(domain="acme.com")
        assert res.company_domain == "acme.com"
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "https://api.apollo.io/api/v1/organizations/enrich"
        assert kwargs["params"]["domain"] == "acme.com"

@pytest.mark.asyncio
async def test_apollo_people_search_endpoint(apollo_provider):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"people": [{"first_name": "John", "email": "j@acme.com"}]}
        mock_post.return_value = mock_response
        
        res = await apollo_provider.search_contacts("acme.com", ["BIM Manager"], 5)
        assert len(res) == 1
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.apollo.io/api/v1/mixed_people/api_search"
        payload = kwargs["json"]
        assert "q_organization_domains_list" in payload
        assert "person_titles" in payload
        assert payload["per_page"] == 5

@pytest.mark.asyncio
async def test_apollo_auth_failure(apollo_provider):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(status_code=401)
        with pytest.raises(ProviderAuthError):
            await apollo_provider.enrich_company(domain="acme.com")

@pytest.mark.asyncio
async def test_apollo_rate_limit(apollo_provider):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=429)
        with pytest.raises(ProviderRateLimitError):
            await apollo_provider.search_contacts("acme.com", ["CEO"])

@pytest.mark.asyncio
async def test_apollo_timeout(apollo_provider):
    with patch("httpx.AsyncClient.get", side_effect=TimeoutException("Timeout")):
        with pytest.raises(ProviderTimeoutError):
            await apollo_provider.enrich_company(domain="acme.com")

@pytest.mark.asyncio
async def test_hunter_confidence_normalization(hunter_provider):
    # confidence > 90 -> likely. confidence <= 90 -> unknown
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "data": {"emails": [
                {"position": "CEO", "confidence": 95, "value": "a@c.com"},
                {"position": "CTO", "confidence": 85, "value": "b@c.com"}
            ]}
        }
        mock_get.return_value = mock_response
        
        res = await hunter_provider.search_contacts("acme.com", ["CEO"], 5)
        assert len(res) == 2
        assert res[0].email_status == "likely"
        assert res[1].email_status == "unknown"

@pytest.mark.asyncio
async def test_hunter_verifier_normalization(hunter_provider):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        def side_effect(*args, **kwargs):
            email = kwargs["params"]["email"]
            mapping = {
                "v@v.com": "valid",
                "i@v.com": "invalid",
                "a@v.com": "accept_all",
                "u@v.com": "unknown"
            }
            m = MagicMock(status_code=200)
            m.json.return_value = {"data": {"status": mapping[email]}}
            return m
        mock_get.side_effect = side_effect
        
        assert await hunter_provider.verify_email("v@v.com") == "verified"
        assert await hunter_provider.verify_email("i@v.com") == "not_valid"
        assert await hunter_provider.verify_email("a@v.com") == "risky"
        assert await hunter_provider.verify_email("u@v.com") == "unknown"

@pytest.mark.asyncio
async def test_hunter_timeout_rate_limit(hunter_provider):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MagicMock(status_code=429)
        with pytest.raises(ProviderRateLimitError):
            await hunter_provider.enrich_company("acme.com")

@pytest.mark.asyncio
async def test_cross_provider_deduplication():
    # Apollo and Hunter return the same email but different provider IDs
    c1 = ContactCandidate(first_name="John", last_name="Doe", job_title="BIM Manager", work_email="john@acme.com", provider="apollo", company_domain="acme.com", provider_person_id="ap_123")
    c2 = ContactCandidate(first_name="John", last_name="Doe", job_title="BIM Manager", work_email="john@acme.com", provider="hunter", company_domain="acme.com")
    
    class MockApollo(BaseContactProvider):
        def get_provider_name(self): return "apollo"
        async def search_contacts(self, domain, titles, limit): return [c1]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")

    class MockHunter(BaseContactProvider):
        def get_provider_name(self): return "hunter"
        async def search_contacts(self, domain, titles, limit): return [c2]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")
        
    tracker = ProviderUsageTracker()
    ranker = BuyerRoleRanker()
    enricher = ContactEnricher([MockApollo(), MockHunter()], ranker, tracker)
    errors = []
    
    contacts = await enricher.discover_and_enrich("acme.com", max_contacts=10, errors=errors)
    assert len(contacts) == 1 # Deduplicated!
    assert "apollo" in contacts[0].data_sources
    assert "hunter" in contacts[0].data_sources
    assert contacts[0].provider_person_id == "ap_123"

@pytest.mark.asyncio
async def test_same_name_different_company_stays_separate():
    c1 = ContactCandidate(first_name="John", last_name="Doe", full_name="John Doe", company_domain="acme.com", provider="apollo")
    c2 = ContactCandidate(first_name="John", last_name="Doe", full_name="John Doe", company_domain="other.com", provider="hunter")
    
    class MockP(BaseContactProvider):
        def get_provider_name(self): return "mock"
        async def search_contacts(self, domain, titles, limit): return [c1, c2]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")
        
    tracker = ProviderUsageTracker()
    ranker = BuyerRoleRanker()
    enricher = ContactEnricher([MockP()], ranker, tracker)
    errors = []
    
    contacts = await enricher.discover_and_enrich("acme.com", max_contacts=10, errors=errors)
    assert len(contacts) == 2

@pytest.mark.asyncio
async def test_same_name_same_domain_no_strong_id_stays_separate():
    """Regression: same full_name + same company_domain but no strong identity must NOT merge."""
    c1 = ContactCandidate(
        first_name="Alex", last_name="Smith", full_name="Alex Smith",
        job_title="Project Lead", company_domain="acme.com", provider="apollo"
    )
    c2 = ContactCandidate(
        first_name="Alex", last_name="Smith", full_name="Alex Smith",
        job_title="Design Director", company_domain="acme.com", provider="hunter"
    )

    class MockApollo(BaseContactProvider):
        def get_provider_name(self): return "apollo"
        async def search_contacts(self, domain, titles, limit): return [c1]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")

    class MockHunter(BaseContactProvider):
        def get_provider_name(self): return "hunter"
        async def search_contacts(self, domain, titles, limit): return [c2]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")

    tracker = ProviderUsageTracker()
    ranker = BuyerRoleRanker()
    enricher = ContactEnricher([MockApollo(), MockHunter()], ranker, tracker)
    errors = []

    contacts = await enricher.discover_and_enrich("acme.com", max_contacts=10, errors=errors)
    assert len(contacts) == 2, f"Expected 2 contacts, got {len(contacts)}: weak identity must not merge"

@pytest.mark.asyncio
async def test_same_name_same_title_different_provider_stays_separate():
    """Regression: same name + same title + same domain + different provider + no email/LinkedIn → 2 contacts."""
    c1 = ContactCandidate(
        first_name="Alex", last_name="Smith", full_name="Alex Smith",
        job_title="BIM Manager", company_domain="acme.com", provider="apollo"
    )
    c2 = ContactCandidate(
        first_name="Alex", last_name="Smith", full_name="Alex Smith",
        job_title="BIM Manager", company_domain="acme.com", provider="hunter"
    )

    class MockApollo(BaseContactProvider):
        def get_provider_name(self): return "apollo"
        async def search_contacts(self, domain, titles, limit): return [c1]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")

    class MockHunter(BaseContactProvider):
        def get_provider_name(self): return "hunter"
        async def search_contacts(self, domain, titles, limit): return [c2]
        async def find_work_email(self, fn, ln, dom, person_id=None): raise ProviderEmptyResult("none", "mock")
        async def verify_email(self, email): raise ProviderEmptyResult("none", "mock")

    tracker = ProviderUsageTracker()
    ranker = BuyerRoleRanker()
    enricher = ContactEnricher([MockApollo(), MockHunter()], ranker, tracker)
    errors = []

    contacts = await enricher.discover_and_enrich("acme.com", max_contacts=10, errors=errors)
    assert len(contacts) == 2, f"Expected 2 contacts even with same name+title, got {len(contacts)}"
    providers = sorted([c.provider for c in contacts])
    assert providers == ["apollo", "hunter"], f"Expected both providers, got {providers}"

@pytest.mark.asyncio
async def test_no_api_keys_configured():
    # Clear env vars if set
    import os
    if "APOLLO_API_KEY" in os.environ: del os.environ["APOLLO_API_KEY"]
    if "HUNTER_API_KEY" in os.environ: del os.environ["HUNTER_API_KEY"]
    
    lead = get_dummy_lead()
    orchestrator = EnrichmentOrchestrator()
    res = await orchestrator.enrich_leads([lead], providers=["apollo", "hunter"])
    
    # Both fail with ProviderConfigError -> status is provider_error.
    assert res["leads"][0].enrichment_status in ["provider_error", "failed"]
    assert any("not configured" in e or "no_provider_configured" in e for e in res["leads"][0].enrichment_errors)

@pytest.mark.asyncio
async def test_unknown_provider_rejected():
    orchestrator = EnrichmentOrchestrator()
    res = await orchestrator.enrich_leads([], providers=["invalid_provider"])
    assert res["status"] == "error"
    assert "Unknown provider" in res["errors"][0]

@pytest.mark.asyncio
async def test_email_unknown_partial_vs_verified_complete():
    # If email exists but unknown -> partial
    # If email exists and verified -> complete
    lead = get_dummy_lead("test.com", "Test")
    orchestrator = EnrichmentOrchestrator()
    
    with patch("sales_engine.enrichment.company_enricher.CompanyEnricher.enrich", new_callable=AsyncMock) as m_comp, \
         patch("sales_engine.enrichment.contact_enricher.ContactEnricher.discover_and_enrich", new_callable=AsyncMock) as m_search:
         
         m_comp.return_value = (CompanyEnrichment(company_name="Test", company_domain="test.com", source="apollo"), [])
         
         # 1. Unknown
         m_search.return_value = [ContactCandidate(first_name="A", last_name="B", work_email="a@test.com", email_status="unknown", company_domain="test.com")]
         res_unknown = await orchestrator.enrich_leads([lead], providers=["apollo"])
         assert res_unknown["leads"][0].enrichment_status == "partial"
         
         # 2. Verified
         m_search.return_value = [ContactCandidate(first_name="A", last_name="B", work_email="a@test.com", email_status="verified", company_domain="test.com")]
         res_verified = await orchestrator.enrich_leads([lead], providers=["apollo"])
         assert res_verified["leads"][0].enrichment_status == "complete"

@pytest.mark.asyncio
async def test_provider_failure_visible_in_errors_does_not_abort(apollo_provider, hunter_provider):
    lead = get_dummy_lead("acme.com", "Acme")
    orchestrator = EnrichmentOrchestrator()
    
    # Mock Apollo to fail with Auth Error, Hunter to succeed
    with patch("sales_engine.enrichment.apollo_provider.ApolloProvider.enrich_company", side_effect=ProviderAuthError("Auth failed", "apollo")), \
         patch("sales_engine.enrichment.hunter_provider.HunterProvider.enrich_company", new_callable=AsyncMock) as m_hunter_comp, \
         patch("sales_engine.enrichment.apollo_provider.ApolloProvider.search_contacts", side_effect=ProviderAuthError("Auth failed", "apollo")), \
         patch("sales_engine.enrichment.hunter_provider.HunterProvider.search_contacts", new_callable=AsyncMock) as m_hunter_search:
         
         m_hunter_comp.return_value = CompanyEnrichment(company_name="Acme", company_domain="acme.com", source="hunter")
         m_hunter_search.return_value = [ContactCandidate(first_name="John", work_email="j@acme.com", email_status="verified")]
         
         # We need to bypass the fact that API keys might be empty in the test env for orchestrator
         # We'll just patch the Providers init in orchestrator
         with patch("sales_engine.enrichment.enrichment_orchestrator.ApolloProvider", return_value=apollo_provider), \
              patch("sales_engine.enrichment.enrichment_orchestrator.HunterProvider", return_value=hunter_provider):
              
              res = await orchestrator.enrich_leads([lead], providers=["apollo", "hunter"])
              
              en_lead = res["leads"][0]
              assert en_lead.company_enrichment.source == "hunter" # Apollo failed, Hunter took over
              assert any("Auth failed" in e for e in en_lead.enrichment_errors) # Apollo error recorded
              assert en_lead.enrichment_status == "complete" # Overall success thanks to Hunter

@pytest.mark.asyncio
async def test_actual_provider_usage_tracking(apollo_provider, hunter_provider):
    lead = get_dummy_lead("acme.com", "Acme")
    orchestrator = EnrichmentOrchestrator()
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("sales_engine.enrichment.enrichment_orchestrator.ApolloProvider", return_value=apollo_provider), \
         patch("sales_engine.enrichment.enrichment_orchestrator.HunterProvider", return_value=hunter_provider):
         
         # Mock all to return Empty so we can trace calls
         mock_get.return_value = MagicMock(status_code=404)
         mock_post.return_value = MagicMock(status_code=404)
         
         res = await orchestrator.enrich_leads([lead], providers=["apollo", "hunter"])
         tracker = res["provider_usage"]
         # Both are called via `enrich_company`. Apollo fails (404), Hunter fails (404).
         assert tracker.apollo_company_calls == 1
         # hunter company call maps to hunter_domain_search_calls
         assert tracker.hunter_domain_search_calls == 2 # 1 for company, 1 for people search
         assert tracker.apollo_people_search_calls == 1
         # Since no contacts found, no find/verify calls made.
