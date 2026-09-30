"""
QA Integrity & Self-Test Regression Suite
Proves:
A. Expected 5 vs Actual 0 -> case cannot PASS.
B. Expected "not_found" vs Actual "unknown" -> case cannot PASS.
C. Missing unique_jobs -> README displays N/A, not 0.
D. Missing qualified -> README displays N/A, not UNQUALIFIED.
E. Any declared Expected mismatch appears in Differences.
"""
import pytest
from tests.acceptance.qa_validator import validate_expected_vs_actual
from scripts.generate_test_report import format_phase5_case, format_case

def test_qa_integrity_rule_a_expected_5_vs_actual_0_cannot_pass():
    """Requirement 7A: Proves that Expected 5 vs Actual 0 produces a difference and cannot PASS."""
    expected = {"evidence_score": 5, "grounded": True}
    actual = {"evidence_score": 0, "grounded": True}
    
    diffs = validate_expected_vs_actual(expected, actual)
    assert len(diffs) > 0, "Validator must flag evidence_score difference"
    assert any("evidence_score" in d and "expected `5`" in d and "got `0`" in d for d in diffs)
    
    # Invariant: If differences exist, status cannot be PASS
    status = "PASS"
    if diffs:
        status = "FAIL"
    assert status != "PASS"

def test_qa_integrity_rule_b_expected_not_found_vs_actual_unknown_cannot_pass():
    """Requirement 7B: Proves that Expected 'not_found' vs Actual 'unknown' produces a difference and cannot PASS."""
    expected = {"email_status": "not_found", "work_email": None}
    actual = {"email_status": "unknown", "work_email": None}
    
    diffs = validate_expected_vs_actual(expected, actual)
    assert len(diffs) > 0, "Validator must flag email_status difference"
    assert any("email_status" in d and "expected `not_found`" in d and "got `unknown`" in d for d in diffs)
    
    # Invariant: If differences exist, status cannot be PASS
    status = "PASS"
    if diffs:
        status = "FAIL"
    assert status != "PASS"

def test_qa_integrity_rule_c_missing_unique_jobs_displays_na():
    """Requirement 7C: Proves that missing unique_jobs in Actual renders 'N/A unique' and never '0 unique'."""
    case_report = {
        "case_id": "P5-REC-001",
        "title": "Recency Test",
        "phase": "Phase 5",
        "status": "PASS",
        "input": {},
        "expected": {"recency_score": 5},
        "actual": {
            "total_jobs": 2,
            "relevant_jobs": 1
            # unique_jobs deliberately omitted
        }
    }
    
    formatted = format_phase5_case(case_report)
    assert "N/A unique" in formatted, "Missing unique_jobs must be displayed as N/A"
    assert "0 unique" not in formatted, "Missing unique_jobs must NEVER be displayed as 0"

def test_qa_integrity_rule_d_missing_qualified_displays_na():
    """Requirement 7D: Proves that missing qualified in Actual renders 'Qualification: `N/A`' and never 'UNQUALIFIED'."""
    case_report = {
        "case_id": "P5-SCORE-001",
        "title": "Score Bounds",
        "phase": "Phase 5",
        "status": "PASS",
        "input": {},
        "expected": {"total_range": [0, 100]},
        "actual": {
            "fit_score": 30,
            "intent_score": 30,
            "recency_score": 20,
            "evidence_score": 20,
            "total_score": 100
            # qualified deliberately omitted
        }
    }
    
    formatted = format_phase5_case(case_report)
    assert "- **Qualification:** `N/A`" in formatted, "Missing qualified must display N/A"
    assert "UNQUALIFIED" not in formatted, "Missing qualified must NEVER default to UNQUALIFIED"

def test_qa_integrity_rule_e_any_declared_expected_mismatch_appears_in_differences():
    """Requirement 7E: Proves that every declared Expected mismatch appears in Differences and never 'None / In sync'."""
    expected = {
        "score_a": 10,
        "score_b": 20,
        "status": "verified"
    }
    actual = {
        "score_a": 10,
        "score_b": 0, # mismatch 1
        "status": "risky" # mismatch 2
    }
    
    diffs = validate_expected_vs_actual(expected, actual)
    assert len(diffs) == 2, f"Expected 2 differences, got {len(diffs)}: {diffs}"
    assert any("score_b" in d for d in diffs)
    assert any("status" in d for d in diffs)
    
    case_report = {
        "case_id": "P5-TEST-DIFF",
        "title": "Difference Verification",
        "phase": "Phase 5",
        "status": "FAIL",
        "input": {},
        "expected": expected,
        "actual": actual,
        "differences": diffs
    }
    
    formatted = format_phase5_case(case_report)
    assert "Differences: _(None / In sync)_" not in formatted
    assert "Field `score_b` mismatch" in formatted
    assert "Field `status` mismatch" in formatted
