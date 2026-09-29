from typing import List, Dict, Set
import uuid
from backend.schemas import StructuredJob

def group_jobs_by_company(jobs: List[StructuredJob]) -> Dict[str, List[StructuredJob]]:
    """Groups a list of jobs based on connected identity components and deduplicates by URL."""
    # 1. Deduplicate jobs by job_url
    unique_jobs: Dict[str, StructuredJob] = {}
    for job in jobs:
        url_key = job.job_url.lower().strip() if job.job_url else str(uuid.uuid4())
        if url_key not in unique_jobs:
            unique_jobs[url_key] = job
            
    deduped_jobs = list(unique_jobs.values())
    
    # 2. Extract keys for each job
    job_keys: List[Set[str]] = []
    for job in deduped_jobs:
        keys = set()
        if job.company_domain:
            keys.add(f"domain:{job.company_domain.lower().strip()}")
        if job.source and job.source_company_key:
            keys.add(f"source:{job.source.lower().strip()}:{job.source_company_key.lower().strip()}")
        if job.company_name_normalized:
            keys.add(f"name_norm:{job.company_name_normalized.lower().strip()}")
            
        if not keys:
            # Fallback to unique key for unknown companies so they don't merge
            fallback = job.job_url.lower().strip() if job.job_url else str(uuid.uuid4())
            keys.add(f"unknown:{fallback}")
            
        job_keys.append(keys)
        
    # 3. Connected Components via Disjoint Set Union (Union-Find)
    parent = {}
    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]
        
    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    key_to_job_idx = {}
    for i in range(len(deduped_jobs)):
        parent[i] = i
        for key in job_keys[i]:
            if key in key_to_job_idx:
                union(i, key_to_job_idx[key])
            else:
                key_to_job_idx[key] = i
                
    # 4. Group by root
    grouped: Dict[str, List[StructuredJob]] = {}
    for i, job in enumerate(deduped_jobs):
        root = find(i)
        group_key = f"group_{root}"
        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(job)
        
    return grouped
