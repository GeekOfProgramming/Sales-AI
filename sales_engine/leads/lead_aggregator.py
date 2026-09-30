from typing import List, Dict, Any
from datetime import datetime
from backend.schemas import StructuredJob, CompanyLead
from sales_engine.analysis.company_normalizer import CompanyNormalizer

def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        return None

def is_job_relevant(job: StructuredJob) -> bool:
    """Determine whether a job represents valid BIM/AEC buying intent."""
    if not job:
        return False
        
    title_lower = (job.job_title or "").lower().strip()
    
    # 1. Obvious BIM / VDC role keywords are relevant without explicit tech list (P5-REL-002)
    BIM_CORE_KEYWORDS = [
        "bim", "revit", "vdc", "virtual design", "digital delivery",
        "computational design", "digital construction", "navisworks", "openbim"
    ]
    if title_lower and any(kw in title_lower for kw in BIM_CORE_KEYWORDS):
        return True
        
    # 2. Obvious non-technical administrative/support/sales titles are never relevant,
    # even if technology happens to be mentioned in description (P5-REL-001, P5-REL-003)
    IRRELEVANT_TITLES = [
        "administrator", "admin", "receptionist", "marketing", "sales",
        "accountant", "accounting", "hr", "human resources", "legal",
        "driver", "cleaner", "cook", "janitor", "cmo", "chief marketing",
        "operations director", "sales manager"
    ]
    if title_lower and any(ir in title_lower for ir in IRRELEVANT_TITLES):
        return False
        
    # 3. If it has explicit relevant signals
    return bool(job.relevant_signals)

def aggregate_jobs(jobs: List[StructuredJob], total_jobs: int = None) -> CompanyLead:
    if not jobs:
        return CompanyLead()

    lead = CompanyLead()
    total_raw = getattr(jobs, "raw_count", None) or getattr(jobs, "_raw_count", None) or total_jobs or len(jobs)
    lead.total_job_count = total_raw
    lead.unique_job_count = len(jobs)
    lead.job_count = len(jobs)
    
    for job in jobs:
        if not lead.company_name and job.company_name:
            lead.company_name = job.company_name
        if not lead.company_name_normalized and job.company_name_normalized:
            lead.company_name_normalized = job.company_name_normalized
        if not lead.company_domain and job.company_domain:
            lead.company_domain = CompanyNormalizer.canonicalize_domain(job.company_domain)
            
        if job.source_company_key and job.source_company_key not in lead.source_company_keys:
            lead.source_company_keys.append(job.source_company_key)
            
        if job.source and job.source_company_key:
            identity = f"{job.source}:{job.source_company_key}".lower()
            if identity not in lead.source_company_identities:
                lead.source_company_identities.append(identity)
            
    lead.source_company_keys.sort()
    lead.source_company_identities.sort()
    
    job_titles = set()
    locations = set()
    technologies = set()
    signals_dict = {}
    evidence_set = set()
    
    dates = []
    relevant_count = 0
    
    now = datetime.now().replace(tzinfo=None)
    
    for job in jobs:
        # A job is considered relevant according to is_job_relevant rules
        is_relevant = is_job_relevant(job)
        
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
