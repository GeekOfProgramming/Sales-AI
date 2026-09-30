import os
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

# Store results across test runs
qa_results = []

from tests.acceptance.qa_validator import validate_expected_vs_actual

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # execute all other hooks to obtain the report object
    outcome = yield
    rep = outcome.get_result()

    if hasattr(item, "qa_data"):
        qa_data = item.qa_data
        
        # Handle skipped tests (e.g. offline live tests)
        if rep.skipped and rep.when in ["setup", "call"]:
            status = qa_data.get("status")
            if not status or status not in ["NOT_RUN", "NOT_RUN_MODEL_LIMITATION"]:
                status = "NOT_RUN"
            qa_results.append({
                "case_id": qa_data.get("case_id", item.name),
                "phase": qa_data.get("phase", "Unknown"),
                "title": qa_data.get("title", item.name),
                "source": qa_data.get("source_url", qa_data.get("source", "internal")),
                "snapshot_path": qa_data.get("snapshot_path", ""),
                "input": qa_data.get("input", {}),
                "expected": qa_data.get("expected", {}),
                "actual": qa_data.get("actual", {}),
                "status": status,
                "differences": qa_data.get("differences", []),
                "reason": qa_data.get("reason", "Test skipped / not run in this environment"),
                "human_notes": qa_data.get("human_notes", ""),
                "test_timestamp": datetime.now(timezone.utc).isoformat()
            })
            return

        if rep.when == "call":
            # Map pytest outcome to our status unless manually overridden
            status = qa_data.get("status")
            if not status or status not in ["PASS", "FAIL", "REVIEW", "SOURCE_CHANGED", "NOT_RUN", "NOT_RUN_MODEL_LIMITATION"]:
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

            # Strict Contractual Integrity Check: Validate expected vs actual
            expected = qa_data.get("expected")
            actual = qa_data.get("actual")
            existing_diffs = qa_data.get("differences", [])
            
            if expected and actual and status not in ["NOT_RUN", "NOT_RUN_MODEL_LIMITATION"]:
                computed_diffs = validate_expected_vs_actual(expected, actual)
                all_diffs = list(dict.fromkeys(existing_diffs + computed_diffs))
                qa_data["differences"] = all_diffs
                
                # Rule 1: A case must NOT report PASS when a declared Expected field differs from Actual
                if all_diffs and status == "PASS":
                    status = "FAIL"
                    diff_summary = "; ".join(all_diffs)
                    reason = f"Integrity Failure: Expected vs Actual mismatch: {diff_summary}"
                
            qa_results.append({
                "case_id": qa_data.get("case_id", item.name),
                "phase": qa_data.get("phase", "Unknown"),
                "title": qa_data.get("title", item.name),
                "source": qa_data.get("source_url", qa_data.get("source", "internal")),
                "snapshot_path": qa_data.get("snapshot_path", ""),
                "input": qa_data.get("input", {}),
                "expected": qa_data.get("expected", {}),
                "actual": qa_data.get("actual", {}),
                "status": status,
                "differences": qa_data.get("differences", []),
                "reason": reason,
                "human_notes": qa_data.get("human_notes", ""),
                "test_timestamp": datetime.now(timezone.utc).isoformat()
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

    # If any Phase 4 cases exist in final_results, write phase4_latest.json
    phase4_results = [r for r in final_results if str(r.get("phase", "")) in ["Phase 4", "4"] or str(r.get("case_id", "")).startswith("P4-")]
    if phase4_results:
        p4_file = reports_dir / "phase4_latest.json"
        with open(p4_file, "w", encoding="utf-8") as f:
            json.dump(phase4_results, f, indent=2, ensure_ascii=False)

    # If any Phase 5 cases exist in final_results, write phase5_latest.json
    phase5_results = [r for r in final_results if str(r.get("phase", "")) in ["Phase 5", "5"] or str(r.get("case_id", "")).startswith("P5-")]
    if phase5_results:
        p5_file = reports_dir / "phase5_latest.json"
        with open(p5_file, "w", encoding="utf-8") as f:
            json.dump(phase5_results, f, indent=2, ensure_ascii=False)

    # If any Phase 6 cases exist in final_results, write phase6_latest.json
    phase6_results = [r for r in final_results if str(r.get("phase", "")) in ["Phase 6", "6"] or str(r.get("case_id", "")).startswith("P6-")]
    if phase6_results:
        p6_file = reports_dir / "phase6_latest.json"
        with open(p6_file, "w", encoding="utf-8") as f:
            json.dump(phase6_results, f, indent=2, ensure_ascii=False)

    # If any Phase 7 cases exist in final_results, write phase7_latest.json
    phase7_results = [r for r in final_results if str(r.get("phase", "")) in ["Phase 7", "7"] or str(r.get("case_id", "")).startswith("P7-")]
    if phase7_results:
        p7_file = reports_dir / "phase7_latest.json"
        with open(p7_file, "w", encoding="utf-8") as f:
            json.dump(phase7_results, f, indent=2, ensure_ascii=False)

@pytest.fixture
def qa_logger(request):
    """Fixture to let tests attach QA data to the item."""
    request.node.qa_data = {}
    return request.node.qa_data
