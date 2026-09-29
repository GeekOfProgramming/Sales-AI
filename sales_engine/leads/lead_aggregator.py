from typing import List, Dict, Any
from datetime import datetime
from backend.schemas import StructuredJob, CompanyLead

def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        # Assuming format like "2023-10-25" or similar ISO
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        return None

def aggregate_jobs(jobs: List[StructuredJob]) -> CompanyLead:
    if not jobs:
        return CompanyLead()

    # Base identity from first job (or most complete)
    lead = CompanyLead()
    
    # We will pick the most complete identity fields across jobs
    for job in jobs:
        if not lead.company_name and job.company_name:
            lead.company_name = job.company_name
        if not lead.company_name_normalized and job.company_name_normalized:
            lead.company_name_normalized = job.company_name_normalized
        if not lead.company_domain and job.company_domain:
            lead.company_domain = job.company_domain
            
        if job.source_company_key and job.source_company_key not in lead.source_company_keys:
            lead.source_company_keys.append(job.source_company_key)
            
    lead.job_count = len(jobs)
    lead.relevant_job_count = len(jobs) # Currently all provided are deemed relevant
    
    job_titles = set()
    locations = set()
    technologies = set()
    signals_dict = {}
    evidence_set = set()
    
    dates = []
    
    now = datetime.now()
    
    for job in jobs:
        if job.job_title:
            job_titles.add(job.job_title)
        if job.location:
            locations.add(job.location)
        for tech in job.technologies:
            technologies.add(tech)
            
        for signal_obj in job.relevant_signals:
            sig = signal_obj.signal
            ev = signal_obj.evidence
            if sig not in signals_dict:
                signals_dict[sig] = 0
            signals_dict[sig] += 1
            if ev:
                evidence_set.add(ev)
                
        dt = parse_date(job.posted_date)
        if dt:
            dates.append(dt)
            days_ago = (now.replace(tzinfo=None) - dt.replace(tzinfo=None)).days
            if days_ago <= 7:
                lead.recent_jobs_7d += 1
            if days_ago <= 30:
                lead.recent_jobs_30d += 1
                
    lead.job_titles = list(job_titles)
    lead.locations = list(locations)
    lead.technologies = list(technologies)
    lead.evidence = list(evidence_set)
    
    lead.signals = [{"signal": k, "evidence_count": v} for k, v in signals_dict.items()]
    
    if dates:
        dates.sort()
        lead.oldest_job_date = dates[0].isoformat()
        lead.newest_job_date = dates[-1].isoformat()
        
    return lead
