from typing import List, Dict, Tuple
from backend.schemas import StructuredJob

def get_company_identity_key(job: StructuredJob) -> str:
    """
    Returns a deterministic grouping key for a job.
    Priority:
    1. company_domain
    2. source + source_company_key
    3. company_name_normalized
    4. "UNKNOWN_COMPANY"
    """
    if job.company_domain:
        return f"domain:{job.company_domain.lower()}"
    
    if job.source and job.source_company_key:
        return f"source:{job.source.lower()}:{job.source_company_key.lower()}"
        
    if job.company_name_normalized:
        return f"name:{job.company_name_normalized.lower()}"
        
    if job.company_name:
        return f"name:{job.company_name.lower().strip()}"
        
    return "UNKNOWN_COMPANY"

def group_jobs_by_company(jobs: List[StructuredJob]) -> Dict[str, List[StructuredJob]]:
    """Groups a list of jobs into a dictionary based on company identity."""
    grouped: Dict[str, List[StructuredJob]] = {}
    for job in jobs:
        key = get_company_identity_key(job)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(job)
    return grouped
