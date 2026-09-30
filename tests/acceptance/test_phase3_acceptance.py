import os
import json
import pytest
from pathlib import Path
from backend.schemas import WebsiteProfile, GeneratedQuery
from sales_engine.discovery.query_generator import QueryGenerator
from sales_engine.discovery.query_evaluator import (
    compute_discovery_metrics,
    evaluate_discovery_acceptance
)

def load_golden_cases(filename):
    p = Path(__file__).parent.parent / "golden" / filename
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.mark.acceptance
@pytest.mark.parametrize("case", load_golden_cases("phase3_discovery.json"), ids=lambda c: c["case_id"])
@pytest.mark.asyncio
async def test_phase3_discovery_golden(case, qa_logger):
    """Golden acceptance test for Phase 3: Discovery Query Generation."""
    qa_logger.update(case)
    
    if case.get("status") == "NOT_RUN" and case["case_id"] != "P3-DISC-001":
        pytest.skip(f"Test case {case['case_id']} marked as NOT_RUN")
        
    case_id = case["case_id"]
    project_root = Path(__file__).parent.parent.parent
    
    if case_id == "P3-DISC-001":
        input_data = case["input"]
        raw_profile = input_data["website_profile"]
        countries = input_data["countries"]
        profile = WebsiteProfile(**raw_profile)
        
        # Snapshot path
        snapshots_dir = project_root / "tests" / "golden" / "snapshots" / "discovery"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshots_dir / "pybim_actual_queries.json"
        
        # Generate queries using QueryGenerator
        generator = QueryGenerator(max_queries=20)
        resp = await generator.generate_queries(profile, countries)
        
        # Calculate QA metrics
        metrics = compute_discovery_metrics(
            resp.queries,
            profile.primary_job_signals,
            countries,
            profile.buyer_roles,
            profile.secondary_job_signals
        )
        
        # Evaluate acceptance criteria
        status, diffs, reason = evaluate_discovery_acceptance(metrics, countries)
        
        actual_output = {
            "queries": [q.model_dump() for q in resp.queries],
            "metrics": metrics,
            "required_intents": [
                "direct_primary_job_hiring",
                "company_careers_pages",
                "ats_discovery",
                "secondary_technology_support",
                "requested_geography"
            ],
            "min_primary_signals_covered": metrics["primary_signals_covered_count"],
            "min_ats_providers_covered": len(metrics["ats_providers_covered"]),
            "required_countries": metrics["countries_covered"],
            "career_intent_required": metrics["career_intent_present"],
            "max_duplicates": metrics["duplicate_queries"],
            "max_forbidden_patterns": metrics["forbidden_pattern_count"]
        }
        
        # Save snapshot
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(actual_output, f, indent=2, ensure_ascii=False)
            
        qa_logger["actual"] = actual_output
        qa_logger["differences"] = diffs
        qa_logger["status"] = status
        qa_logger["reason"] = reason
        qa_logger["human_notes"] = case.get("human_notes", "")
        
        # Critical failure must fail pytest
        if status == "FAIL":
            pytest.fail(reason)
            
        assert len(resp.queries) >= 3, "Too few queries generated"
        assert metrics["duplicate_queries"] == 0, "Duplicate queries found"
        assert metrics["forbidden_pattern_count"] == 0, "Forbidden patterns detected"

    elif case_id == "P3-LIVE-001":
        brave_key = os.environ.get("BRAVE_API_KEY")
        if not brave_key:
            qa_logger["status"] = "NOT_RUN"
            qa_logger["reason"] = "Brave API key not configured in environment"
            pytest.skip("Brave API key not configured")
            
        from sales_engine.discovery.brave_search import BraveSearchProvider
        from sales_engine.discovery.url_classifier import URLClassifier
        
        search_provider = BraveSearchProvider(api_key=brave_key)
        classifier = URLClassifier()
        
        # Sample 3 approved queries
        sample_queries = [
            'site:jobs.lever.co "BIM Manager"',
            '"BIM Manager" jobs Germany',
            '"Revit API Developer" careers engineering'
        ]
        
        live_results = []
        total_relevant = 0
        total_checked = 0
        
        for query in sample_queries:
            results = await search_provider.search(query, num_results=10)
            query_items = []
            rel_count = 0
            for r in results:
                cat = classifier.classify(r.url)
                is_relevant = cat in ["ats_job", "career_page", "job_posting"]
                if is_relevant:
                    rel_count += 1
                query_items.append({
                    "title": r.title,
                    "url": r.url,
                    "classification": cat,
                    "relevant": is_relevant
                })
            total_relevant += rel_count
            total_checked += len(results)
            live_results.append({
                "query": query,
                "results_checked": len(results),
                "relevant_count": rel_count,
                "precision_at_10": round(rel_count / max(1, len(results)), 2),
                "items": query_items
            })
            
        precision_overall = round(total_relevant / max(1, total_checked), 2)
        actual_live = {
            "queries_evaluated": len(sample_queries),
            "total_results_checked": total_checked,
            "total_relevant": total_relevant,
            "precision_at_10": precision_overall,
            "details": live_results
        }
        
        qa_logger["actual"] = actual_live
        qa_logger["status"] = "REVIEW" # Baseline review
        qa_logger["reason"] = f"Live baseline evaluated with overall Precision@10: {precision_overall}"
