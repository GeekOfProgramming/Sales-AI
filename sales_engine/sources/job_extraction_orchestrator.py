from typing import List, Tuple
from urllib.parse import urlparse
from backend.schemas import StructuredJob, JobExtractionError, ExtractJobsResponse, WebsiteProfile
from sales_engine.sources.source_detector import detect_source
from sales_engine.sources.lever_source import LeverSource
from sales_engine.sources.greenhouse_source import GreenhouseSource
from sales_engine.sources.ashby_source import AshbySource
from sales_engine.sources.generic_job_page import GenericJobPage
from sales_engine.analysis.job_analyzer import JobAnalyzer
from sales_engine.analysis.company_normalizer import CompanyNormalizer

class JobExtractionOrchestrator:
    def __init__(self):
        self.sources = {
            "lever": LeverSource(),
            "greenhouse": GreenhouseSource(),
            "ashby": AshbySource(),
            "generic": GenericJobPage()
        }
        self.analyzer = JobAnalyzer()
        self.normalizer = CompanyNormalizer()
        
    async def extract_jobs(self, urls: List[str], profile: WebsiteProfile = None) -> ExtractJobsResponse:
        jobs = []
        errors = []
        
        for url in urls:
            try:
                # 1. Detect Source
                source_type = detect_source(url)
                fetcher = self.sources.get(source_type, self.sources["generic"])
                
                # 2. Fetch Job
                try:
                    raw_job = await fetcher.fetch_job(url)
                except TimeoutError as e:
                    errors.append(JobExtractionError(url=url, error_type="timeout", message=str(e)))
                    continue
                except Exception as e:
                    errors.append(JobExtractionError(url=url, error_type="fetch_failed", message=str(e)))
                    continue
                    
                if not raw_job.get("description"):
                    errors.append(JobExtractionError(url=url, error_type="parse_failed", message="No description extracted"))
                    continue
                    
                # 3. Analyze Job
                try:
                    structured_job = await self.analyzer.analyze_job(raw_job, profile)
                except Exception as e:
                    errors.append(JobExtractionError(url=url, error_type="analysis_failed", message=str(e)))
                    continue
                    
                # 4. Normalize Company
                if structured_job.company_name:
                    structured_job.company_name_normalized = self.normalizer.normalize(structured_job.company_name)
                    
                # Extract domain safely if company domain is empty
                if not structured_job.company_domain and structured_job.job_url:
                    company_same_as = raw_job.get("company_same_as")
                    if company_same_as:
                        try:
                            parsed_same_as = urlparse(company_same_as)
                            if parsed_same_as.netloc:
                                structured_job.company_domain = parsed_same_as.netloc.lower()
                        except Exception:
                            pass
                            
                    if not structured_job.company_domain and source_type == "generic":
                        parsed = urlparse(structured_job.job_url)
                        domain = parsed.netloc.lower()
                        # Very simple fallback: use the domain of the job URL if generic.
                        # Exclude known ATS and job boards if they somehow got classified as generic.
                        invalid_domains = ["lever.co", "greenhouse.io", "ashbyhq.com", "workable.com", "breezy.hr", "applytojob.com", "indeed.com", "linkedin.com"]
                        if not any(d in domain for d in invalid_domains):
                            structured_job.company_domain = domain
                            
                    # For ATS URLs, do NOT set company_domain to lever.co, etc. It remains null.
                
                jobs.append(structured_job)
                
            except Exception as e:
                errors.append(JobExtractionError(url=url, error_type="unhandled_error", message=str(e)))
                
        return ExtractJobsResponse(
            status="ok",
            requested=len(urls),
            processed=len(jobs),
            failed=len(errors),
            jobs=jobs,
            errors=errors
        )
