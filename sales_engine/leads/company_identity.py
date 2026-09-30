from typing import List, Dict, Set
import uuid
from backend.schemas import StructuredJob
from sales_engine.discovery.url_classifier import URLClassifier
from sales_engine.analysis.company_normalizer import CompanyNormalizer

class JobList(list):
    raw_count: int = 0

def group_jobs_by_company(jobs: List[StructuredJob]) -> Dict[str, List[StructuredJob]]:
    """Groups a list of jobs based on connected identity components and deduplicates by URL."""
    # 1. Deduplicate jobs by job_url and track raw counts
    unique_jobs: Dict[str, StructuredJob] = {}
    url_raw_counts: Dict[str, int] = {}
    for job in jobs:
        if job.job_url:
            url_key = URLClassifier.normalize_url(job.job_url).lower().strip()
        else:
            url_key = str(uuid.uuid4())
            
        url_raw_counts[url_key] = url_raw_counts.get(url_key, 0) + 1
        if url_key not in unique_jobs:
            unique_jobs[url_key] = job
            
    deduped_jobs = list(unique_jobs.values())
    
    # 2. Extract keys for each job
    job_keys: List[Set[str]] = []
    for job in deduped_jobs:
        keys = set()
        canon_dom = CompanyNormalizer.canonicalize_domain(job.company_domain) if job.company_domain else None
        if canon_dom:
            keys.add(f"domain:{canon_dom}")
        if job.source and job.source_company_key:
            keys.add(f"source:{job.source.lower().strip()}:{job.source_company_key.lower().strip()}")
        if job.company_name_normalized:
            keys.add(f"name_norm:{job.company_name_normalized.lower().strip()}")
            
        if not keys:
            # Fallback to unique key for unknown companies so they don't merge
            fallback = URLClassifier.normalize_url(job.job_url).lower().strip() if job.job_url else str(uuid.uuid4())
            keys.add(f"unknown:{fallback}")
            
        job_keys.append(keys)
        
    # 3. Connected Components via Disjoint Set Union (Union-Find)
    parent = {}
    domains = {}
    source_keys = {}
    
    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]
        
    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            dom_i = domains[root_i]
            dom_j = domains[root_j]
            # Domain conflict: if both have domains, and they don't share ANY domain, they must not merge.
            if dom_i and dom_j and dom_i.isdisjoint(dom_j):
                return
                
            # Source key conflict: if both have keys on the same source, but keys differ, they must not merge.
            src_i = source_keys[root_i]
            src_j = source_keys[root_j]
            sources_i = {k.split(":", 1)[0]: k.split(":", 1)[1] for k in src_i if ":" in k}
            sources_j = {k.split(":", 1)[0]: k.split(":", 1)[1] for k in src_j if ":" in k}
            common_sources = set(sources_i.keys()).intersection(sources_j.keys())
            if any(sources_i[s] != sources_j[s] for s in common_sources):
                return
                
            parent[root_i] = root_j
            domains[root_j].update(dom_i)
            source_keys[root_j].update(src_i)

    key_to_job_idx = {}
    for i, job in enumerate(deduped_jobs):
        parent[i] = i
        canon_dom = CompanyNormalizer.canonicalize_domain(job.company_domain) if job.company_domain else None
        domains[i] = {canon_dom} if canon_dom else set()
        source_keys[i] = {f"{job.source.lower().strip()}:{job.source_company_key.lower().strip()}"} if (job.source and job.source_company_key) else set()
        
    for i in range(len(deduped_jobs)):
        for key in job_keys[i]:
            if key in key_to_job_idx:
                union(i, key_to_job_idx[key])
            else:
                key_to_job_idx[key] = find(i) # Update the key owner to root to avoid cascading issues
                
    # Re-eval unions just in case keys were added before domain conflicts were resolved
    # A cleaner 2-pass is better, but since union() safely ignores conflicts, this is generally sufficient.
    # To be extremely safe, we do it in a loop until no changes:
    changed = True
    while changed:
        changed = False
        for i in range(len(deduped_jobs)):
            for key in job_keys[i]:
                if key in key_to_job_idx:
                    root_i = find(i)
                    root_j = find(key_to_job_idx[key])
                    if root_i != root_j:
                        union(i, key_to_job_idx[key])
                        if find(i) != root_i:
                            changed = True
                key_to_job_idx[key] = find(i)
                
    # 4. Group by root
    grouped: Dict[str, JobList] = {}
    for i, job in enumerate(deduped_jobs):
        root = find(i)
        group_key = f"group_{root}"
        if group_key not in grouped:
            grouped[group_key] = JobList()
        grouped[group_key].append(job)
        
    for group_key, g_jobs in grouped.items():
        g_jobs.raw_count = sum(url_raw_counts.get(URLClassifier.normalize_url(j.job_url).lower().strip(), 1) if j.job_url else 1 for j in g_jobs)
        
    return grouped
