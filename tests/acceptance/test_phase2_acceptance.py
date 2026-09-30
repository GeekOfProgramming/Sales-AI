import json
import re
import pytest
from pathlib import Path
from sales_engine.analysis.website_analyzer import is_valid_job_title

def load_golden_cases(filename):
    p = Path(__file__).parent.parent / "golden" / filename
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def match_service_concepts(expected_services: list, actual_services: list, concept_aliases: dict):
    """Semantic comparison using human-owned concept aliases rather than literal equality."""
    missing_concepts = []
    satisfied_concepts = set()
    actual_matched = set()

    for concept, aliases in concept_aliases.items():
        concept_found = False
        for act in actual_services:
            act_clean = act.lower().strip()
            if any(alias.lower().strip() in act_clean or act_clean in alias.lower().strip() for alias in aliases):
                concept_found = True
                actual_matched.add(act)
                break
        if concept_found:
            satisfied_concepts.add(concept)
        else:
            missing_concepts.append(concept)

    # Any actual service not matching any known concept
    unmatched_actual = [a for a in actual_services if a not in actual_matched]
    return missing_concepts, unmatched_actual

def compute_detailed_diffs(expected: dict, actual: dict, concept_aliases: dict = None):
    diff_lines = []
    missing = []
    extra = []
    different = []
    semantic_review = []
    
    # Check Company Name
    if expected.get("company_name", "").lower() != actual.get("company_name", "").lower():
        different.append(f"company_name: expected '{expected.get('company_name')}' vs actual '{actual.get('company_name')}'")
        
    # Check Services via Concept Aliases if available
    if concept_aliases:
        missing_concepts, extra_services = match_service_concepts(
            expected.get("services", []),
            actual.get("services", []),
            concept_aliases
        )
        for mc in missing_concepts:
            missing.append(f"service_concept: '{mc}'")
        for es in extra_services:
            extra.append(f"services: '{es}'")
    else:
        exp_services = expected.get("services", [])
        act_services = actual.get("services", [])
        for s in exp_services:
            if not any(s.lower() in a.lower() or a.lower() in s.lower() for a in act_services):
                missing.append(f"services: '{s}'")
        for s in act_services:
            if not any(s.lower() in e.lower() or e.lower() in s.lower() for e in exp_services):
                extra.append(f"services: '{s}'")
            
    # Check Industries (word-overlap tolerant)
    exp_ind = [i.lower() for i in expected.get("target_industries", [])]
    act_ind = [i.lower() for i in actual.get("target_industries", [])]
    for ei in exp_ind:
        if not any(ei in ai or ai in ei for ai in act_ind):
            missing.append(f"target_industries: '{ei}'")
    for ai in act_ind:
        if not any(ai in ei or ei in ai for ei in exp_ind):
            extra.append(f"target_industries: '{ai}'")
        
    # Check Buyer roles
    exp_roles = [r.lower() for r in expected.get("buyer_roles", [])]
    act_roles = [r.lower() for r in actual.get("buyer_roles", [])]
    for er in exp_roles:
        if not any(er in ar or ar in er for ar in act_roles):
            missing.append(f"buyer_roles: '{er}'")
    for ar in act_roles:
        if not any(ar in er or er in ar for er in exp_roles):
            extra.append(f"buyer_roles: '{ar}'")
        
    # Semantic traps & checks
    act_primary = actual.get("primary_job_signals", [])
    invalid_job_signals = [sig for sig in act_primary if not is_valid_job_title(sig)]
    has_action_signals = len(invalid_job_signals) > 0
    if has_action_signals:
        semantic_review.append(f"primary_job_signals: Model extracted invalid job signals (actions/tasks rather than roles): {invalid_job_signals}")
        
    act_neg = actual.get("negative_signals", [])
    has_inverted_negatives = any(any(kw in s.lower() for kw in ["tender", "mapping", "clash", "manual", "compliance", "iso"]) for s in act_neg)
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
        
    is_critical_fail = has_action_signals or has_inverted_negatives
    return diff_lines, is_critical_fail

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
    
    # Compute detailed diffs with human-owned concept aliases
    diffs, is_critical_fail = compute_detailed_diffs(
        case["expected"],
        actual_profile,
        case.get("concept_aliases")
    )
    qa_logger["differences"] = diffs
    qa_logger["human_notes"] = case.get("human_notes", "")
    
    # QA Policy: If critical semantic fields are wrong, fail the test and report
    if is_critical_fail:
        qa_logger["status"] = "FAIL"
        qa_logger["reason"] = "Critical semantic failure: primary_job_signals contains business activities/goals instead of job titles, or negative_signals inverts pain points."
        pytest.fail(qa_logger["reason"])
    elif diffs:
        qa_logger["status"] = "REVIEW"
    else:
        qa_logger["status"] = "PASS"
        
    # Assert company name matches (basic sanity)
    assert actual_profile.get("company_name", "").lower() == case["expected"]["company_name"].lower()
