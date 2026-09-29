import os
import json
import pytest
from datetime import datetime
from pathlib import Path

# Store results across test runs
qa_results = []

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # execute all other hooks to obtain the report object
    outcome = yield
    rep = outcome.get_result()

    if rep.when == "call":
        # Check if the test explicitly logged QA data via a fixture
        if hasattr(item, "qa_data"):
            qa_data = item.qa_data
            
            # Map pytest outcome to our status unless manually overridden (e.g. to REVIEW or NOT_RUN)
            status = qa_data.get("status")
            if not status or status not in ["PASS", "FAIL", "REVIEW", "SOURCE_CHANGED", "NOT_RUN"]:
                if rep.passed:
                    status = "PASS"
                elif rep.failed:
                    status = "FAIL"
                else:
                    status = "REVIEW"
            
            # Append reason if failed and no reason provided
            reason = qa_data.get("reason", "")
            if rep.failed and not reason:
                reason = str(rep.longrepr)
                
            qa_results.append({
                "case_id": qa_data.get("case_id", item.name),
                "phase": qa_data.get("phase", "Unknown"),
                "title": qa_data.get("title", item.name),
                "source": qa_data.get("source_url", "internal"),
                "input": qa_data.get("input", {}),
                "expected": qa_data.get("expected", {}),
                "actual": qa_data.get("actual", {}),
                "status": status,
                "differences": qa_data.get("differences", []),
                "reason": reason,
                "human_notes": qa_data.get("human_notes", ""),
                "test_timestamp": datetime.utcnow().isoformat() + "Z"
            })

def pytest_sessionfinish(session, exitstatus):
    """Save all collected QA results to JSON report."""
    if not qa_results:
        return
        
    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    report_file = reports_dir / "latest_test_report.json"
    
    # Load existing report if it exists to merge (so we don't overwrite phases we didn't run)
    existing_results = []
    if report_file.exists():
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                existing_results = json.load(f)
        except:
            pass
            
    # Update existing with new results
    new_cases = {r["case_id"]: r for r in qa_results}
    
    final_results = []
    for ex in existing_results:
        if ex["case_id"] in new_cases:
            final_results.append(new_cases.pop(ex["case_id"]))
        else:
            final_results.append(ex)
            
    # Add any entirely new cases
    final_results.extend(new_cases.values())
    
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

@pytest.fixture
def qa_logger(request):
    """Fixture to let tests attach QA data to the item."""
    request.node.qa_data = {}
    return request.node.qa_data
