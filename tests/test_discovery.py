import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from backend.main import app
from sales_engine.discovery.url_classifier import URLClassifier
from backend.schemas import WebsiteProfile, GeneratedQueriesResponse, GeneratedQuery

client = TestClient(app)

def test_url_classifier_normalization():
    classifier = URLClassifier()
    # Test UTM stripping
    url = "https://jobs.lever.co/pybim/123?utm_source=linkedin&utm_campaign=hiring"
    assert classifier.normalize_url(url) == "https://jobs.lever.co/pybim/123"
    
    # Test trailing slash
    assert classifier.normalize_url("https://pybim.com/careers/") == "https://pybim.com/careers"
    
    # Test fragments
    assert classifier.normalize_url("https://pybim.com/jobs#frontend") == "https://pybim.com/jobs"
    
def test_url_classifier_ats_domains():
    classifier = URLClassifier()
    assert classifier.classify("https://jobs.lever.co/company/123") == "ats_job"
    assert classifier.classify("https://boards.greenhouse.io/company") == "ats_job"
    assert classifier.classify("https://jobs.ashbyhq.com/company") == "ats_job"

def test_url_classifier_path_clues():
    classifier = URLClassifier()
    assert classifier.classify("https://pybim.com/jobs") == "job_posting"
    assert classifier.classify("https://pybim.com/careers") == "career_page"
    assert classifier.classify("https://pybim.com/about-us") == "unknown"

@pytest.mark.asyncio
@patch('sales_engine.discovery.brave_search.BraveSearchProvider.search')
@patch('sales_engine.discovery.query_generator.QueryGenerator.generate_queries')
@patch('sales_engine.analysis.website_analyzer.WebsiteAnalyzer.analyze_website')
@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
async def test_discovery_endpoint_success(mock_fetch, mock_analyze, mock_generate, mock_search):
    mock_fetch.return_value = {"pages": []}
    mock_analyze.return_value = WebsiteProfile(
        company_name="PyBIM",
        services=["BIM automation"],
        target_industries=["AEC"]
    )
    
    mock_generate.return_value = GeneratedQueriesResponse(queries=[
        GeneratedQuery(query="BIM Manager jobs", type="general_job", priority=10),
        GeneratedQuery(query="Revit API jobs.lever.co", type="ats_targeted", priority=9)
    ])
    
    # Mock search results for both queries
    from backend.schemas import NormalizedSearchResult
    mock_search.side_effect = [
        [
            NormalizedSearchResult(query="BIM Manager jobs", title="BIM Manager", url="https://jobs.lever.co/bim/1", snippet="", source="brave", rank=1),
            NormalizedSearchResult(query="BIM Manager jobs", title="BIM Manager 2", url="https://pybim.com/careers", snippet="", source="brave", rank=2)
        ],
        [
            # Duplicate URL with different query parameters to test normalization/deduplication
            NormalizedSearchResult(query="Revit API", title="Revit Dev", url="https://jobs.lever.co/bim/1?utm_source=google", snippet="", source="brave", rank=1),
            NormalizedSearchResult(query="Revit API", title="About Us", url="https://pybim.com/about", snippet="", source="brave", rank=2)
        ]
    ]
    
    response = client.post("/api/sales/discover", json={
        "website_url": "https://pybim.com",
        "countries": ["Germany"],
        "max_queries": 2,
        "results_per_query": 2
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["queries_generated"] == 2
    assert data["raw_results"] == 4
    assert data["unique_results"] == 3 # https://jobs.lever.co/bim/1 is deduped
    assert data["candidate_urls"] == 2 # Lever is ats_job, careers is career_page, about is unknown

@pytest.mark.asyncio
@patch('sales_engine.discovery.brave_search.BraveSearchProvider.search')
@patch('sales_engine.discovery.query_generator.QueryGenerator.generate_queries')
@patch('sales_engine.analysis.website_analyzer.WebsiteAnalyzer.analyze_website')
@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
async def test_discovery_limits(mock_fetch, mock_analyze, mock_generate, mock_search):
    # Test that max_queries and results_per_query limits are enforced
    mock_fetch.return_value = {"pages": []}
    mock_analyze.return_value = WebsiteProfile()
    
    # Generator returns more queries than max
    queries = [GeneratedQuery(query=f"Q{i}", type="general_job", priority=1) for i in range(25)]
    mock_generate.return_value = GeneratedQueriesResponse(queries=queries)
    mock_search.return_value = []
    
    response = client.post("/api/sales/discover", json={
        "website_url": "https://pybim.com",
        "countries": [],
        "max_queries": 20, # Valid maximum
        "results_per_query": 10 # Valid maximum
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["queries_generated"] == 20
    assert mock_search.call_count == 20

@pytest.mark.asyncio
async def test_search_provider_errors():
    from sales_engine.discovery.brave_search import BraveSearchProvider
    from sales_engine.discovery.search_provider import ConfigurationError, AuthenticationError, RateLimitError, TimeoutError
    import httpx
    import os
    
    with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
        provider = BraveSearchProvider()
        with pytest.raises(ConfigurationError):
            await provider.search("test")
            
    with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "test"}):
        provider = BraveSearchProvider()
        
        with patch('httpx.AsyncClient.get', side_effect=httpx.TimeoutException("timeout")):
            with pytest.raises(TimeoutError):
                await provider.search("test")
                
        mock_resp_401 = httpx.Response(401, request=httpx.Request("GET", "url"))
        with patch('httpx.AsyncClient.get', side_effect=httpx.HTTPStatusError("auth", request=mock_resp_401.request, response=mock_resp_401)):
            with pytest.raises(AuthenticationError):
                await provider.search("test")
                
        mock_resp_429 = httpx.Response(429, request=httpx.Request("GET", "url"))
        with patch('httpx.AsyncClient.get', side_effect=httpx.HTTPStatusError("rate", request=mock_resp_429.request, response=mock_resp_429)):
            with pytest.raises(RateLimitError):
                await provider.search("test")

@pytest.mark.asyncio
async def test_sales_llm_model_env():
    import os
    from sales_engine.analysis.website_analyzer import WebsiteAnalyzer
    from sales_engine.discovery.query_generator import QueryGenerator
    
    with patch.dict(os.environ, {"SALES_LLM_MODEL": "test-sales-model"}):
        analyzer = WebsiteAnalyzer()
        generator = QueryGenerator()
        
        assert analyzer.model_name == "test-sales-model"
        assert generator.model_name == "test-sales-model"

def test_validation_limits():
    # Test valid
    response = client.post("/api/sales/discover", json={
        "website_url": "https://pybim.com",
        "max_queries": 25,
        "results_per_query": 15
    })
    # Should fail validation due to le=20 and le=10
    assert response.status_code == 422

    # If priority is invalid
    from backend.schemas import GeneratedQuery
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GeneratedQuery(query="test", type="general_job", priority=15)
        
    with pytest.raises(ValidationError):
        GeneratedQuery(query="test", type="general_job", priority=0)

@pytest.mark.asyncio
@patch('sales_engine.discovery.brave_search.BraveSearchProvider.search')
@patch('sales_engine.discovery.query_generator.QueryGenerator.generate_queries')
@patch('sales_engine.analysis.website_analyzer.WebsiteAnalyzer.analyze_website')
@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
async def test_discovery_priority_sorting(mock_fetch, mock_analyze, mock_generate, mock_search):
    mock_fetch.return_value = {"pages": []}
    mock_analyze.return_value = WebsiteProfile()
    
    # Generator returns 3 queries with different priorities
    mock_generate.return_value = GeneratedQueriesResponse(queries=[
        GeneratedQuery(query="Q1", type="general_job", priority=2),
        GeneratedQuery(query="Q2", type="general_job", priority=10),
        GeneratedQuery(query="Q3", type="general_job", priority=5)
    ])
    mock_search.return_value = []
    
    response = client.post("/api/sales/discover", json={
        "website_url": "https://pybim.com",
        "max_queries": 2, # Will only take top 2 priorities
    })
    
    # Q2 (10) and Q3 (5) should be executed, Q1 (2) should be dropped
    executed_queries = [call.args[0] for call in mock_search.call_args_list]
    assert "Q2" in executed_queries
    assert "Q3" in executed_queries
    assert "Q1" not in executed_queries
