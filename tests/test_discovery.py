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
    assert data["candidate_job_urls"] == 2 # Lever is ats_job, careers is career_page, about is unknown

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
        "max_queries": 25, # Should be capped at 20
        "results_per_query": 15 # Should be capped at 10
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["queries_generated"] == 20
    assert mock_search.call_count == 20
