import pytest
from backend.schemas import StructuredJob, JobSignal
from sales_engine.leads.company_identity import get_company_identity_key
from sales_engine.leads.lead_orchestrator import process_leads

def test_company_identity_grouping():
    # 1. By Domain
    job1 = StructuredJob(job_url="http://x", source="x", company_domain="abc.com", company_name="ABC")
    assert get_company_identity_key(job1) == "domain:abc.com"
    
    # 2. By Source Key
    job2 = StructuredJob(job_url="http://y", source="greenhouse", source_company_key="xyz-inc")
    assert get_company_identity_key(job2) == "source:greenhouse:xyz-inc"
    
    # 3. By Normalized Name
    job3 = StructuredJob(job_url="http://z", source="z", company_name_normalized="def corp")
    assert get_company_identity_key(job3) == "name:def corp"

def test_lead_aggregation_and_scoring():
    jobs = [
        StructuredJob(
            job_url="http://x1", 
            source="x", 
            company_domain="abc.com", 
            company_name="ABC Corp",
            job_title="BIM Manager",
            technologies=["Revit", "Dynamo"],
            posted_date="2024-01-01T00:00:00Z",
            relevant_signals=[JobSignal(signal="BIM Hiring", evidence="Looking for a manager")]
        ),
        StructuredJob(
            job_url="http://x2", 
            source="y", 
            company_domain="abc.com", 
            company_name="ABC Corporation",
            job_title="Revit Developer",
            technologies=["Revit", "Python", "C#"],
            posted_date="2024-01-05T00:00:00Z",
            relevant_signals=[JobSignal(signal="Automation", evidence="Develop plugins")]
        ),
        StructuredJob(
            job_url="http://other", 
            source="other", 
            company_domain="other.com", 
            company_name="Other LLC",
            job_title="Architect"
        )
    ]
    
    res = process_leads(jobs, min_qualified_score=50)
    
    assert res.status == "ok"
    assert res.jobs_received == 3
    assert res.companies_found == 2
    
    # Check ABC Corp
    abc_lead = next(l for l in res.leads if l.company_domain == "abc.com")
    assert abc_lead.job_count == 2
    assert "BIM Manager" in abc_lead.job_titles
    assert "Revit" in abc_lead.technologies
    assert "Python" in abc_lead.technologies
    assert len(abc_lead.technologies) == 4 # Revit, Dynamo, Python, C#
    assert len(abc_lead.signals) == 2
    
    # Fit Score: 10 (jobs) + 15 (max tech: 3*5) + 5 (signals) = 30
    assert abc_lead.fit_score == 30
    
    # Intent Score: 10 (primary) + 5 (secondary) + 10 (leadership from "Manager") = 25
    assert abc_lead.intent_score == 25
    
    # Recency & Evidence
    assert abc_lead.evidence_score == 4 # 2 evidences * 2
    
    assert abc_lead.lead_score == abc_lead.fit_score + abc_lead.intent_score + abc_lead.recency_score + abc_lead.evidence_score
    
    # Check Other LLC
    other_lead = next(l for l in res.leads if l.company_domain == "other.com")
    assert other_lead.job_count == 1
    assert other_lead.fit_score == 10 # Only jobs
    assert other_lead.intent_score == 10 # 1 job
    assert other_lead.recency_score == 5 # unknown date
    assert other_lead.evidence_score == 0
    assert other_lead.lead_score == 25
    assert not other_lead.qualified
