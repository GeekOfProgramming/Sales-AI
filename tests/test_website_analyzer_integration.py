import pytest
import requests
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

@pytest.mark.asyncio
@pytest.mark.integration
async def test_successful_structured_analysis_with_ollama():
    """
    Test a full successful analysis (Requires local Ollama running).
    This test hits a fast dummy domain to ensure the real LLM can parse and generate the schema.
    """
    try:
        requests.get("http://127.0.0.1:11434/")
    except requests.exceptions.ConnectionError:
        pytest.skip("Ollama is not running, skipping integration test.")
        
    response = client.post("/api/sales/analyze-website", json={"url": "https://example.com"})
    
    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code} - {response.text}"
    data = response.json()
    assert data["status"] == "ok"
    assert "profile" in data
    assert "company_name" in data["profile"]
    assert isinstance(data["profile"]["services"], list)
