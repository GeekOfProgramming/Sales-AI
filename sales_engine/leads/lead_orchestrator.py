from typing import List
import os
from backend.schemas import StructuredJob, CompanyLead, BuildLeadsResponse
from .company_identity import group_jobs_by_company
from .lead_aggregator import aggregate_jobs
from .lead_scorer import score_lead

def process_leads(jobs: List[StructuredJob], min_qualified_score: int = None) -> BuildLeadsResponse:
    if min_qualified_score is None:
        min_qualified_score = int(os.getenv("MIN_QUALIFIED_SCORE", "60"))
        
    # 1. Group Jobs
    grouped_jobs = group_jobs_by_company(jobs)
    
    leads = []
    qualified_count = 0
    
    # 2. Aggregate and Score each group
    for group_key, company_jobs in grouped_jobs.items():
        try:
            lead = aggregate_jobs(company_jobs)
            lead = score_lead(lead, min_qualified_score=min_qualified_score)
            leads.append(lead)
            if lead.qualified:
                qualified_count += 1
        except Exception as e:
            # Skip failing groups, continue with the rest
            print(f"Error processing lead for group {group_key}: {e}")
            
    return BuildLeadsResponse(
        status="ok",
        jobs_received=len(jobs),
        companies_found=len(grouped_jobs),
        qualified_leads=qualified_count,
        leads=leads
    )
