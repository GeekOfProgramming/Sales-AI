import pytest
import json
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.schemas import WebsiteAnalyzeResponse

client = TestClient(app)

@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
@patch('ai_engine.llm_client.BIMLLMClient.generate_code_async')
def test_successful_structured_analysis(mock_generate, mock_fetch):
    # Mock the fetcher
    mock_fetch.return_value = {
        "base_url": "https://example.com",
        "pages": [{"url": "https://example.com", "text": "We sell AI solutions for BIM."}]
    }
    
    # Mock the LLM response
    class MockResponse:
        def __init__(self):
            self.extracted_code = json.dumps({
                "company_name": "AI BIM Corp",
                "company_summary": "AI solutions for BIM",
                "services": ["BIM Automation"],
                "target_industries": ["AEC"],
                "target_company_types": ["Architecture Firms"],
                "pain_points": ["Manual workflows"],
                "buyer_roles": ["BIM Manager"],
                "primary_job_signals": ["BIM Developer"],
                "secondary_job_signals": ["BIM Coordinator"],
                "keywords": ["Revit", "API"],
                "negative_signals": []
            })
            
    mock_generate.return_value = MockResponse()
    
    response = client.post("/api/sales/analyze-website", json={"url": "https://example.com"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["profile"]["company_name"] == "AI BIM Corp"
    assert data["profile"]["services"] == ["BIM Automation"]

@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
@patch('ai_engine.llm_client.BIMLLMClient.generate_code_async')
def test_malformed_json_then_successful_retry(mock_generate, mock_fetch):
    mock_fetch.return_value = {
        "base_url": "https://example.com",
        "pages": [{"url": "https://example.com", "text": "We sell AI solutions for BIM."}]
    }
    
    # First response malformed, second response correct
    class BadMockResponse:
        def __init__(self):
            self.extracted_code = "This is not JSON at all."
            
    class GoodMockResponse:
        def __init__(self):
            self.extracted_code = json.dumps({
                "company_name": "AI BIM Corp",
                "company_summary": "AI solutions for BIM",
                "services": [], "target_industries": [], "target_company_types": [],
                "pain_points": [], "buyer_roles": [], "primary_job_signals": [],
                "secondary_job_signals": [], "keywords": [], "negative_signals": []
            })
            
    mock_generate.side_effect = [BadMockResponse(), GoodMockResponse()]
    
    response = client.post("/api/sales/analyze-website", json={"url": "https://example.com"})
    
    assert response.status_code == 200
    assert mock_generate.call_count == 2
    
    # Verify repair instructions were injected on the second call
    second_call_req = mock_generate.call_args_list[1][0][0]
    assert "IMPORTANT JSON REPAIR:" in second_call_req.user_prompt

@patch('sales_engine.web.website_fetcher.WebsiteFetcher.fetch_website_content')
@patch('ai_engine.llm_client.BIMLLMClient.generate_code_async')
def test_malformed_json_after_both_attempts(mock_generate, mock_fetch):
    mock_fetch.return_value = {
        "base_url": "https://example.com",
        "pages": [{"url": "https://example.com", "text": "We sell AI solutions for BIM."}]
    }
    
    class BadMockResponse:
        def __init__(self):
            self.extracted_code = "Still not JSON."
            
    mock_generate.side_effect = [BadMockResponse(), BadMockResponse()]
    
    response = client.post("/api/sales/analyze-website", json={"url": "https://example.com"})
    
    # The endpoint catches all analyzer exceptions and raises 500
    assert response.status_code == 500
    assert "Failed to parse LLM response into WebsiteProfile" in response.json()["detail"]
    assert mock_generate.call_count == 2
