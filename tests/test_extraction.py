import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from backend.main import app
from sales_engine.sources.source_detector import detect_source
from sales_engine.analysis.company_normalizer import CompanyNormalizer
from backend.schemas import ExtractJobsRequest, JobExtractionError, ExtractJobsResponse, StructuredJob

client = TestClient(app)

def test_source_detector():
    assert detect_source("https://jobs.lever.co/company/123") == "lever"
    assert detect_source("https://boards.greenhouse.io/company") == "greenhouse"
    assert detect_source("https://jobs.ashbyhq.com/company") == "ashby"
    assert detect_source("https://company.com/careers/456") == "generic"
    assert detect_source("invalid_url") == "generic"

def test_company_normalizer():
    normalizer = CompanyNormalizer()
    assert normalizer.normalize("AECOM GmbH") == "aecom"
    assert normalizer.normalize("Google LLC") == "google"
    assert normalizer.normalize("Google, LLC.") == "google"
    assert normalizer.normalize("Microsoft Corporation") == "microsoft"
    assert normalizer.normalize("Company  Ltd  ") == "company"
    assert normalizer.normalize("B.V. Test") == "test"
    assert normalizer.normalize("Test S.r.l.") == "test"
    
@pytest.mark.asyncio
async def test_job_analyzer_retry():
    from sales_engine.analysis.job_analyzer import JobAnalyzer
    from ai_engine.llm_client import CodeGenerationResponse
    
    with patch('os.getenv', return_value='test-model'):
        analyzer = JobAnalyzer()
        analyzer.llm_client.generate_code_async = AsyncMock(side_effect=[
            CodeGenerationResponse(raw_response="invalid", extracted_code="invalid json", model="test", duration_seconds=1.0),
            CodeGenerationResponse(raw_response="{}", extracted_code='{"technologies": ["Revit"]}', model="test", duration_seconds=1.0)
        ])
        
        job = await analyzer.analyze_job({"title": "Test", "description": "Test", "url": "http://test", "source": "generic"})
        assert "Revit" in job.technologies
        assert analyzer.llm_client.generate_code_async.call_count == 2
        
@pytest.mark.asyncio
async def test_job_analyzer_failure():
    from sales_engine.analysis.job_analyzer import JobAnalyzer
    from ai_engine.llm_client import CodeGenerationResponse
    
    with patch('os.getenv', return_value='test-model'):
        analyzer = JobAnalyzer()
        analyzer.llm_client.generate_code_async = AsyncMock(side_effect=[
            CodeGenerationResponse(raw_response="invalid", extracted_code="invalid json", model="test", duration_seconds=1.0),
            CodeGenerationResponse(raw_response="invalid", extracted_code="invalid json 2", model="test", duration_seconds=1.0)
        ])
        
        with pytest.raises(Exception, match="Failed to analyze job via LLM"):
            await analyzer.analyze_job({"title": "Test", "description": "Test", "url": "http://test", "source": "generic"})

@pytest.mark.asyncio
@patch('sales_engine.sources.lever_source.LeverSource.fetch_job')
@patch('sales_engine.sources.generic_job_page.GenericJobPage.fetch_job')
@patch('sales_engine.analysis.job_analyzer.JobAnalyzer.analyze_job')
async def test_extraction_orchestrator(mock_analyze, mock_generic_fetch, mock_lever_fetch):
    mock_lever_fetch.return_value = {"company": "Test LLC", "title": "BIM Manager", "url": "https://jobs.lever.co/test/1", "description": "Test description"}
    mock_generic_fetch.side_effect = Exception("Timeout")
    
    mock_analyze.return_value = StructuredJob(
        company_name="Test LLC",
        job_title="BIM Manager",
        job_url="https://jobs.lever.co/test/1",
        source="lever"
    )
    
    response = client.post("/api/sales/extract-jobs", json={
        "urls": [
            "https://jobs.lever.co/test/1",
            "https://company.com/jobs/1" # generic
        ]
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["requested"] == 2
    assert data["processed"] == 1
    assert data["failed"] == 1
    
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["company_name_normalized"] == "test"
    
    assert len(data["errors"]) == 1
    assert data["errors"][0]["url"] == "https://company.com/jobs/1"
    assert data["errors"][0]["error_type"] == "fetch_failed"

def test_extract_jobs_validation():
    # Test max URLs
    urls = [f"https://jobs.lever.co/test/{i}" for i in range(55)]
    response = client.post("/api/sales/extract-jobs", json={"urls": urls})
    assert response.status_code == 422 # Because max_length=50

@pytest.mark.asyncio
async def test_generic_job_page_json_ld():
    from sales_engine.sources.generic_job_page import GenericJobPage
    import httpx
    
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "datePosted": "2023-01-01",
            "hiringOrganization": {
                "@type": "Organization",
                "name": "Tech Corp"
            },
            "description": "<p>Great job</p>"
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    
    mock_resp = httpx.Response(200, text=html, request=httpx.Request("GET", "url"))
    
    with patch('httpx.AsyncClient.get', return_value=mock_resp):
        fetcher = GenericJobPage()
        job = await fetcher.fetch_job("https://company.com/job/1")
        assert job["title"] == "Software Engineer"
        assert job["company"] == "Tech Corp"
        assert job["posted_date"] == "2023-01-01"
        assert job["description"] == "Great job"

@pytest.mark.asyncio
async def test_generic_job_page_json_ld_graph_and_employment():
    from sales_engine.sources.generic_job_page import GenericJobPage
    import httpx
    
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@graph": [
                {
                    "@type": "JobPosting",
                    "title": "BIM Manager",
                    "employmentType": "FULL_TIME",
                    "hiringOrganization": {
                        "@type": "Organization",
                        "name": "Design Firm",
                        "sameAs": "https://designfirm.com"
                    },
                    "description": "<p>Looking for BIM expert.</p>"
                }
            ]
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    
    mock_resp = httpx.Response(200, text=html, request=httpx.Request("GET", "url"))
    
    with patch('httpx.AsyncClient.get', return_value=mock_resp):
        fetcher = GenericJobPage()
        job = await fetcher.fetch_job("https://careers.designfirm.com/job/1")
        assert job["title"] == "BIM Manager"
        assert job["employment_type"] == "FULL_TIME"
        assert job["company_same_as"] == "https://designfirm.com"
        
@pytest.mark.asyncio
@patch('sales_engine.sources.lever_source.LeverSource.fetch_job')
@patch('sales_engine.analysis.job_analyzer.JobAnalyzer.analyze_job')
async def test_ats_domain_not_used_as_company_domain(mock_analyze, mock_lever_fetch):
    from sales_engine.sources.job_extraction_orchestrator import JobExtractionOrchestrator
    
    # Mock successful fetch without description -> parse_failed
    mock_lever_fetch.return_value = {"company": "Test LLC", "title": "BIM Manager", "url": "https://jobs.lever.co/test/1", "description": ""}
    
    orchestrator = JobExtractionOrchestrator()
    response = await orchestrator.extract_jobs(["https://jobs.lever.co/test/1"])
    
    assert response.failed == 1
    assert response.errors[0].error_type == "parse_failed"
    
    # Now mock with description to test domain
    mock_lever_fetch.return_value = {"company": "Test LLC", "title": "BIM Manager", "url": "https://jobs.lever.co/test/1", "description": "Good job", "source": "lever"}
    mock_analyze.return_value = StructuredJob(
        company_name="Test LLC",
        job_title="BIM Manager",
        job_url="https://jobs.lever.co/test/1",
        source="lever"
    )
    
    response2 = await orchestrator.extract_jobs(["https://jobs.lever.co/test/1"])
    assert response2.processed == 1
    assert response2.jobs[0].company_domain is None # Should NOT be lever.co

