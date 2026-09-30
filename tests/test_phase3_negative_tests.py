import pytest
from backend.schemas import WebsiteProfile
from sales_engine.discovery.query_generator import QueryGenerator
from sales_engine.discovery.query_evaluator import (
    compute_discovery_metrics,
    normalize_query_for_qa,
    detect_forbidden_patterns
)

@pytest.mark.asyncio
async def test_p3_neg_001_business_goal_pollution():
    """P3-NEG-001: Reject business goals in primary_job_signals."""
    profile = WebsiteProfile(
        company_name="TestCo",
        target_industries=["AEC"],
        primary_job_signals=["Reduce operational costs", "Identify operational bottlenecks", "BIM Manager"],
        secondary_job_signals=["Revit"]
    )
    generator = QueryGenerator()
    resp = await generator.generate_queries(profile, ["Germany"])
    
    # Must NOT generate queries containing "Reduce operational costs" or business goals
    for q in resp.queries:
        assert "reduce" not in q.query.lower(), f"Business goal leaked into query: {q.query}"
        assert "bottleneck" not in q.query.lower(), f"Business goal leaked into query: {q.query}"
        issues = detect_forbidden_patterns(q.query, ["BIM Manager"], [], ["Revit"])
        assert len(issues) == 0, f"Forbidden pattern detected: {issues}"

@pytest.mark.asyncio
async def test_p3_neg_002_duplicate_signals():
    """P3-NEG-002: Deduplicate trivial variations of signals without query duplication."""
    profile = WebsiteProfile(
        company_name="TestCo",
        target_industries=["AEC"],
        primary_job_signals=["BIM Manager", "bim manager", "BIM Manager", "Head of BIM"],
        secondary_job_signals=["Revit"]
    )
    generator = QueryGenerator()
    resp = await generator.generate_queries(profile, ["Germany", "Italy"])
    
    metrics = compute_discovery_metrics(
        resp.queries,
        ["BIM Manager", "Head of BIM"],
        ["Germany", "Italy"],
        [],
        ["Revit"]
    )
    assert metrics["duplicate_queries"] == 0, f"Found duplicate queries: {metrics['duplicate_list']}"

@pytest.mark.asyncio
async def test_p3_neg_003_empty_primary_signals():
    """P3-NEG-003: Empty primary signals returns controlled empty/partial result, no hallucinations."""
    profile = WebsiteProfile(
        company_name="TestCo",
        target_industries=["AEC"],
        primary_job_signals=[],
        secondary_job_signals=["Revit"]
    )
    generator = QueryGenerator()
    resp = await generator.generate_queries(profile, ["Germany"])
    
    # Must NOT hallucinate random job titles
    assert len(resp.queries) == 0

@pytest.mark.asyncio
async def test_p3_neg_004_secondary_only_profile():
    """P3-NEG-004: Secondary-only profile does not treat technologies as job titles."""
    profile = WebsiteProfile(
        company_name="TestCo",
        target_industries=["AEC"],
        primary_job_signals=[],
        secondary_job_signals=["Revit", "COBie", "IFC"]
    )
    generator = QueryGenerator()
    resp = await generator.generate_queries(profile, ["Germany"])
    
    # Must not create standalone tech jobs like "COBie jobs"
    for q in resp.queries:
        for tech in ["Revit", "COBie", "IFC"]:
            assert q.query.lower() != f"{tech.lower()} jobs"
            assert q.query.lower() != f"{tech.lower()} hiring"
    assert len(resp.queries) == 0

@pytest.mark.asyncio
async def test_p3_neg_005_geography_coverage():
    """P3-NEG-005: All requested countries covered without blind triplication."""
    countries = ["Italy", "United Kingdom", "Germany"]
    profile = WebsiteProfile(
        company_name="pyBIM",
        target_industries=["AEC", "Engineering"],
        primary_job_signals=["BIM Manager", "Head of BIM", "Digital Delivery Manager"],
        secondary_job_signals=["Revit", "COBie"]
    )
    generator = QueryGenerator(max_queries=15)
    resp = await generator.generate_queries(profile, countries)
    
    metrics = compute_discovery_metrics(
        resp.queries,
        profile.primary_job_signals,
        countries,
        [],
        profile.secondary_job_signals
    )
    
    # All countries covered
    assert len(metrics["countries_covered"]) == 3
    for c in countries:
        assert c in metrics["countries_covered"]
        
    # Budget is controlled, no blind triplication
    assert len(resp.queries) <= 15
    assert metrics["duplicate_queries"] == 0

@pytest.mark.asyncio
async def test_p3_neg_006_buyer_role_pollution():
    """P3-NEG-006: Regression test: buyer_roles must not be promoted into primary hiring signals."""
    profile = WebsiteProfile(
        company_name="TestCo",
        target_industries=["AEC", "Engineering"],
        buyer_roles=["Head of Digital Delivery", "Operations Manager"],
        primary_job_signals=["BIM Manager", "Revit API Developer"],
        secondary_job_signals=["Revit", "Python"]
    )
    generator = QueryGenerator()
    resp = await generator.generate_queries(profile, ["Germany", "Italy"])
    
    # queries may contain BIM Manager, Revit API Developer
    # but must NOT generate primary hiring queries for Head of Digital Delivery or Operations Manager
    for q in resp.queries:
        assert "head of digital delivery" not in q.query.lower(), f"Buyer role leaked into query: {q.query}"
        assert "operations manager" not in q.query.lower(), f"Buyer role leaked into query: {q.query}"
        issues = detect_forbidden_patterns(
            q.query,
            profile.primary_job_signals,
            profile.buyer_roles,
            profile.secondary_job_signals
        )
        assert len(issues) == 0, f"Forbidden pattern detected: {issues}"
