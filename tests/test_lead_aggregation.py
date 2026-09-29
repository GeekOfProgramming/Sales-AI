import pytest
import datetime
from backend.schemas import StructuredJob, JobSignal
from sales_engine.leads.company_identity import group_jobs_by_company
from sales_engine.leads.lead_orchestrator import process_leads

def test_company_identity_grouping():
    # 1. Connected components (domain on one, source key on another)
    job1 = StructuredJob(job_url="http://x1", source="test", company_domain="acme.com", company_name="Acme")
    job2 = StructuredJob(job_url="http://x2", source="gh", source_company_key="acme-inc")
    job3 = StructuredJob(job_url="http://x3", source="gh", company_domain="acme.com", source_company_key="acme-inc")
    
    # 2. Unknown companies should not merge
    job_un1 = StructuredJob(job_url="http://un1", source="test", company_name="Unknown A")
    job_un2 = StructuredJob(job_url="http://un2", source="test", company_name="Unknown B")
    
    # 3. Different domains should never merge
    job_diff1 = StructuredJob(job_url="http://diff1", source="test", company_domain="diff1.com")
    job_diff2 = StructuredJob(job_url="http://diff2", source="test", company_domain="diff2.com")
    
    jobs = [job1, job2, job3, job_un1, job_un2, job_diff1, job_diff2]
    
    grouped = group_jobs_by_company(jobs)
    
    assert len(grouped) == 5
    
    # Find the group with acme.com
    acme_group = next(g for g in grouped.values() if any(j.company_domain == "acme.com" for j in g))
    assert len(acme_group) == 3
    
    # Ensure unknowns are separate
    assert any(len(g) == 1 and g[0].job_url == "http://un1" for g in grouped.values())
    assert any(len(g) == 1 and g[0].job_url == "http://un2" for g in grouped.values())
    assert any(len(g) == 1 and g[0].job_url == "http://diff1" for g in grouped.values())

def test_deduplication_and_relevance():
    job1 = StructuredJob(
        job_url="http://dup", 
        source="test",
        company_domain="acme.com",
        job_title="Dev",
        relevant_signals=[JobSignal(signal="A", evidence="Ev1")]
    )
    job2 = StructuredJob(
        job_url="http://dup", # Duplicate
        source="test",
        company_domain="acme.com",
        job_title="Dev",
        relevant_signals=[JobSignal(signal="A", evidence="Ev1")]
    )
    job3 = StructuredJob(
        job_url="http://irrelevant",
        source="test",
        company_domain="acme.com",
        job_title="Janitor",
        relevant_signals=[],
        technologies=[]
    )
    
    res = process_leads([job1, job2, job3])
    
    assert len(res.leads) == 1
    lead = res.leads[0]
    
    # Deduplication means only 2 unique jobs
    assert lead.job_count == 2
    # Only the first one is relevant
    assert lead.relevant_job_count == 1
    
    assert lead.intent_score == 10 # First relevant job = 10
    assert lead.fit_score == 10 + 5 # 1 relevant job (10) + signals (5) = 15
    assert len(lead.evidence) == 1 # Deduplicated evidence

def test_recency_and_future_dates():
    now = datetime.datetime.now()
    
    # Adding relevant_signals so they are considered relevant
    sig = [JobSignal(signal="A", evidence="E")]
    job_7d = StructuredJob(job_url="1", source="test", company_domain="7d.com", relevant_signals=sig, posted_date=(now - datetime.timedelta(days=5)).isoformat())
    job_14d = StructuredJob(job_url="2", source="test", company_domain="14d.com", relevant_signals=sig, posted_date=(now - datetime.timedelta(days=12)).isoformat())
    job_30d = StructuredJob(job_url="3", source="test", company_domain="30d.com", relevant_signals=sig, posted_date=(now - datetime.timedelta(days=25)).isoformat())
    job_old = StructuredJob(job_url="4", source="test", company_domain="old.com", relevant_signals=sig, posted_date=(now - datetime.timedelta(days=100)).isoformat())
    job_unk = StructuredJob(job_url="5", source="test", company_domain="unk.com", relevant_signals=sig)
    job_future = StructuredJob(job_url="6", source="test", company_domain="fut.com", relevant_signals=sig, posted_date=(now + datetime.timedelta(days=10)).isoformat())
    
    res = process_leads([job_7d, job_14d, job_30d, job_old, job_unk, job_future])
    
    def get_recency(domain):
        return next(l.recency_score for l in res.leads if l.company_domain == domain)
        
    assert get_recency("7d.com") == 20
    assert get_recency("14d.com") == 15
    assert get_recency("30d.com") == 10
    assert get_recency("old.com") == 5
    assert get_recency("unk.com") == 5
    assert get_recency("fut.com") == 5 # Future treated as unknown

def test_leadership_and_score_bounds():
    job = StructuredJob(
        job_url="1",
        source="test",
        company_domain="lead.com",
        job_title="Principal Engineer", # Leadership keyword
        technologies=["T1", "T2", "T3", "T4", "T5", "T6"], # 6 tech = max tech score (15)
        relevant_signals=[JobSignal(signal="A", evidence=f"E{i}") for i in range(15)] # Max evidence score (20)
    )
    
    res = process_leads([job])
    lead = res.leads[0]
    
    # Fit: 10 (job) + 15 (max tech) + 5 (signal) = 30
    assert lead.fit_score == 30
    # Intent: 10 (1st job) + 10 (leadership) = 20
    assert lead.intent_score == 20
    # Evidence: 15 * 2 = 30 -> maxed at 20
    assert lead.evidence_score == 20
    # Recency: unknown = 5
    assert lead.recency_score == 5
    
    assert lead.lead_score == 75
    assert lead.lead_score <= 100
    assert lead.qualified == True # 75 >= 60

def test_qualification_threshold():
    # Only an irrelevant job
    job = StructuredJob(
        job_url="1",
        source="test",
        company_domain="fail.com"
    )
    # min_qualified_score can be injected via env, but process_leads defaults to 60 or passed arg
    res = process_leads([job], min_qualified_score=60)
    assert len(res.leads) == 1
    assert not res.leads[0].qualified
    assert res.leads[0].lead_score < 60

def test_domain_conflict_prevents_merge():
    # Both have the same normalized name, but different explicit domains
    job1 = StructuredJob(job_url="1", source="test", company_domain="a.com", company_name_normalized="acme")
    job2 = StructuredJob(job_url="2", source="test", company_domain="b.com", company_name_normalized="acme")
    
    # This one has no domain, so it CAN merge with one of them if it shares a key (e.g., name_norm)
    # The current Union-Find resolves this by merging it with the first one it encounters.
    job3 = StructuredJob(job_url="3", source="test", company_name_normalized="acme")
    
    res = process_leads([job1, job2, job3])
    
    # a.com and b.com must remain separate!
    domains_found = set(l.company_domain for l in res.leads if l.company_domain)
    assert "a.com" in domains_found
    assert "b.com" in domains_found
    assert len(res.leads) >= 2
    
def test_irrelevant_jobs_do_not_inflate_scores():
    now = datetime.datetime.now()
    
    # Relevant old job
    job1 = StructuredJob(
        job_url="1", source="test", company_domain="x.com", 
        job_title="Standard",
        relevant_signals=[JobSignal(signal="A", evidence="E1")],
        posted_date=(now - datetime.timedelta(days=100)).isoformat()
    )
    
    # Irrelevant recent leadership job with lots of tech
    job2 = StructuredJob(
        job_url="2", source="test", company_domain="x.com", 
        job_title="Principal Janitor", # Leadership keyword!
        technologies=["T1", "T2", "T3", "T4", "T5"], # Lots of tech!
        relevant_signals=[], # Irrelevant!
        posted_date=(now - datetime.timedelta(days=1)).isoformat() # Very recent!
    )
    
    res = process_leads([job1, job2])
    lead = res.leads[0]
    
    # Since job2 is irrelevant, it should NOT contribute to:
    # 1. Tech score (lead.technologies should be empty)
    assert len(lead.technologies) == 0
    # 2. Leadership intent (intent should only be 10 for the first relevant job)
    assert lead.intent_score == 10
    # 3. Recency (recency should be 5 because the relevant job is 100 days old)
    assert lead.recency_score == 5
    # 4. Relevant count
    assert lead.relevant_job_count == 1
    assert lead.job_count == 2
    
def test_url_normalization_dedupe():
    job1 = StructuredJob(job_url="http://x.com/job/1?utm_source=a", source="test", company_domain="x.com", relevant_signals=[JobSignal(signal="A", evidence="e")])
    job2 = StructuredJob(job_url="http://x.com/job/1?utm_medium=b", source="test", company_domain="x.com", relevant_signals=[JobSignal(signal="A", evidence="e")])
    job3 = StructuredJob(job_url="http://x.com/job/1#apply", source="test", company_domain="x.com", relevant_signals=[JobSignal(signal="A", evidence="e")])
    job4 = StructuredJob(job_url="http://x.com/job/1/", source="test", company_domain="x.com", relevant_signals=[JobSignal(signal="A", evidence="e")])
    
    res = process_leads([job1, job2, job3, job4])
    lead = res.leads[0]
    
    # All 4 URLs normalize to "http://x.com/job/1", so they should deduplicate to exactly 1 job!
    assert lead.job_count == 1
    assert lead.relevant_job_count == 1
