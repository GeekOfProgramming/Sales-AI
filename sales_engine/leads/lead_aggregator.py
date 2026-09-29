from typing import List, Dict, Any
from datetime import datetime
from backend.schemas import StructuredJob, CompanyLead

def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        return None

def aggregate_jobs(jobs: List[StructuredJob]) -> CompanyLead:
    if not jobs:
        return CompanyLead()

    lead = CompanyLead()
    
    for job in jobs:
        if not lead.company_name and job.company_name:
            lead.company_name = job.company_name
        if not lead.company_name_normalized and job.company_name_normalized:
            lead.company_name_normalized = job.company_name_normalized
        if not lead.company_domain and job.company_domain:
            lead.company_domain = job.company_domain
            
        if job.source_company_key and job.source_company_key not in lead.source_company_keys:
            lead.source_company_keys.append(job.source_company_key)
            
    lead.source_company_keys.sort()
    lead.job_count = len(jobs)
    
    job_titles = set()
    locations = set()
    technologies = set()
    signals_dict = {}
    evidence_set = set()
    
    dates = []
    relevant_count = 0
    
    now = datetime.now().replace(tzinfo=None)
    
    for job in jobs:
        # A job is considered relevant ONLY if it has relevant_signals (or an explicit relevance flag if available)
        is_relevant = bool(job.relevant_signals)
        
        if is_relevant:
            relevant_count += 1
            
            # Only relevant jobs contribute to titles, locations, and technologies
            if job.job_title:
                job_titles.add(job.job_title)
            if job.location:
                locations.add(job.location)
            for tech in job.technologies:
                technologies.add(tech)
                
            # Only relevant jobs contribute to signals and evidence
            for signal_obj in job.relevant_signals:
                sig = signal_obj.signal
                ev = signal_obj.evidence
                if sig not in signals_dict:
                    signals_dict[sig] = 0
                signals_dict[sig] += 1
                if ev:
                    evidence_set.add(ev)
                    
            # Only relevant jobs contribute to recency scoring
            dt = parse_date(job.posted_date)
            if dt:
                dt_naive = dt.replace(tzinfo=None)
                days_ago = (now - dt_naive).days
                # Guard against future dates (days_ago < 0)
                if days_ago >= 0:
                    dates.append(dt)
                    if days_ago <= 7:
                        lead.recent_jobs_7d += 1
                    if days_ago <= 14:
                        lead.recent_jobs_14d += 1
                    if days_ago <= 30:
                        lead.recent_jobs_30d += 1
                
    lead.relevant_job_count = relevant_count
    lead.job_titles = sorted(list(job_titles))
    lead.locations = sorted(list(locations))
    lead.technologies = sorted(list(technologies))
    lead.evidence = sorted(list(evidence_set))
    
    signals_list = [{"signal": k, "evidence_count": v} for k, v in signals_dict.items()]
    signals_list.sort(key=lambda x: (-x["evidence_count"], x["signal"]))
    lead.signals = signals_list
    
    if dates:
        dates.sort()
        lead.oldest_job_date = dates[0].isoformat()
        lead.newest_job_date = dates[-1].isoformat()
        
    return lead
