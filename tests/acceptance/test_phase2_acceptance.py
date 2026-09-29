import json
import pytest
from pathlib import Path

def load_golden_cases(filename):
    p = Path(__file__).parent.parent / "golden" / filename
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_detailed_diffs(expected: dict, actual: dict):
    diff_lines = []
    missing = []
    extra = []
    different = []
    semantic_review = []
    
    # Check Company Name
    if expected.get("company_name") != actual.get("company_name"):
        different.append(f"company_name: expected '{expected.get('company_name')}' vs actual '{actual.get('company_name')}'")
        
    # Check Services
    exp_services = expected.get("services", [])
    act_services = actual.get("services", [])
    for s in exp_services:
        if not any(s.lower() in a.lower() or a.lower() in s.lower() for a in act_services):
            missing.append(f"services: '{s}'")
    for s in act_services:
        if not any(s.lower() in e.lower() or e.lower() in s.lower() for e in exp_services):
            extra.append(f"services: '{s}'")
            
    # Check Industries
    exp_ind = set(expected.get("target_industries", []))
    act_ind = set(actual.get("target_industries", []))
    for i in exp_ind - act_ind:
        missing.append(f"target_industries: '{i}'")
    for i in act_ind - exp_ind:
        extra.append(f"target_industries: '{i}'")
        
    # Check Buyer roles
    exp_roles = set(expected.get("buyer_roles", []))
    act_roles = set(actual.get("buyer_roles", []))
    for r in exp_roles - act_roles:
        missing.append(f"buyer_roles: '{r}'")
    for r in act_roles - exp_roles:
        extra.append(f"buyer_roles: '{r}'")
        
    # Semantic traps & checks
    act_primary = actual.get("primary_job_signals", [])
    has_action_signals = any(any(v in sig.lower() for v in ["identify", "evaluate", "deploy", "ensure", "reduce", "implement", "optimize", "writing", "using"]) for sig in act_primary)
    if has_action_signals:
        semantic_review.append("primary_job_signals: Model extracted business activities/goals instead of job titles (e.g. 'Implementing custom automation scripts...' vs 'BIM Manager').")
        
    act_neg = actual.get("negative_signals", [])
    has_inverted_negatives = any("tender" in s.lower() or "mapping" in s.lower() for s in act_neg)
    if has_inverted_negatives:
        semantic_review.append("negative_signals: Model misclassified target tender mandates and customer pain points as negative disqualifiers.")
        
    act_text = json.dumps(actual).lower()
    if "cloud connect" in act_text and "deployed" in act_text:
        semantic_review.append("Offering trap: Model claimed 'Cloud Connect' is deployed (actual status on site: IN DEVELOPMENT).")

    # Format into markdown sections
    if missing:
        diff_lines.append("**Missing:**")
        diff_lines.extend([f"- {m}" for m in missing])
    if extra:
        diff_lines.append("**Extra:**")
        diff_lines.extend([f"- {e}" for e in extra])
    if different:
        diff_lines.append("**Different:**")
        diff_lines.extend([f"- {d}" for d in different])
    if semantic_review:
        diff_lines.append("**Needs semantic review:**")
        diff_lines.extend([f"- {sr}" for sr in semantic_review])
        
    return diff_lines, has_action_signals or has_inverted_negatives

@pytest.mark.acceptance
@pytest.mark.parametrize("case", load_golden_cases("phase2_websites.json"), ids=lambda c: c["case_id"])
def test_phase2_website_golden(case, qa_logger):
    """Golden acceptance test for Phase 2: Website Analyzer."""
    qa_logger.update(case)
    
    if case.get("status") == "NOT_RUN":
        pytest.skip("Test case marked as NOT_RUN")
        
    # Locate actual output or run snapshot
    project_root = Path(__file__).parent.parent.parent
    actual_profile_path = project_root / "tests" / "golden" / "snapshots" / "websites" / "pybim_actual_profile.json"
    
    if not actual_profile_path.exists():
        pytest.fail(f"Actual profile not found at {actual_profile_path}")
        
    with open(actual_profile_path, "r", encoding="utf-8") as f:
        actual_profile = json.load(f)
        
    qa_logger["actual"] = actual_profile
    
    # Compute detailed diffs
    diffs, is_critical_fail = compute_detailed_diffs(case["expected"], actual_profile)
    qa_logger["differences"] = diffs
    
    # QA Policy: If critical semantic fields are wrong (e.g. actions as job signals), it is a FAIL
    if is_critical_fail:
        qa_logger["status"] = "FAIL"
        qa_logger["reason"] = "Critical semantic failure: primary_job_signals contains business activities/goals instead of job titles, which will corrupt downstream Phase 3 Discovery."
    elif diffs:
        qa_logger["status"] = "REVIEW"
    else:
        qa_logger["status"] = "PASS"
        
    qa_logger["human_notes"] = case.get("human_notes", "")
    
    # Assert company name matches (basic sanity)
    assert actual_profile.get("company_name") == case["expected"]["company_name"]
