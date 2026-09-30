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


# =========================================================================
# PHASE 8 QA INTEGRITY & META-TEST SUITE
# =========================================================================

from scripts.update_phase8_report import (
    evaluate_catalog_case,
    calculate_false_pass_count,
    is_function_empty_pass,
)


def test_qa_meta_rule_a_test_function_containing_only_pass_cannot_generate_pass(tmp_path):
    """Requirement 15A: Proves that a test function containing only `pass` cannot generate PASS."""
    dummy_file = tmp_path / "test_dummy.py"
    dummy_file.write_text(
        """def test_empty_stub():
    # Stub without assertions
    pass
""",
        encoding="utf-8",
    )
    is_stub = is_function_empty_pass(str(dummy_file), "test_empty_stub")
    assert is_stub is True, "Must identify function with only 'pass' as empty pass stub"

    catalog_case = {"case_id": "P8-FAB-001", "title": "No Fake Metrics"}
    test_results = {"test_empty_stub": {"status": "PASS", "execution_time_s": 0.01}}
    mapping = {"P8-FAB-001": f"{dummy_file}::test_empty_stub"}

    status, expected, actual, reason, has_stub = evaluate_catalog_case(catalog_case, test_results, mapping)
    assert status != "PASS", "Empty pass stub MUST NEVER be assigned PASS"
    assert status == "FAIL"
    assert has_stub is True
    assert "empty pass stub" in actual.get("error", "").lower()


def test_qa_meta_rule_b_unmapped_golden_case_cannot_generate_pass():
    """Requirement 15B: Proves that a Golden case with no mapped test cannot generate PASS."""
    catalog_case = {"case_id": "P8-UNMAPPED-999", "title": "Unmapped Case"}
    test_results = {"some_test": {"status": "PASS"}}
    mapping = {}  # No mapping for this case

    status, expected, actual, reason, _ = evaluate_catalog_case(catalog_case, test_results, mapping)
    assert status != "PASS", "Unmapped catalog case must NEVER become PASS"
    assert status == "NOT_RUN"
    assert actual.get("error") == "no_mapped_test"


def test_qa_meta_rule_c_failed_pytest_node_forces_case_fail():
    """Requirement 15C: Proves that a failed pytest node forces corresponding case FAIL."""
    catalog_case = {"case_id": "P8-REG-001", "title": "Wrong-Company Same-Title Job"}
    test_results = {
        "test_p8_reg_001_wrong_company_same_title_job": {
            "status": "FAIL",
            "error": "AssertionError: job leakage detected",
        }
    }
    mapping = {
        "P8-REG-001": "tests/test_outreach.py::test_p8_reg_001_wrong_company_same_title_job"
    }

    status, expected, actual, reason, _ = evaluate_catalog_case(catalog_case, test_results, mapping)
    assert status == "FAIL"
    assert actual.get("status") == "FAIL"
    assert "AssertionError" in actual.get("error", "")


def test_qa_meta_rule_d_skipped_pytest_node_becomes_not_run_not_pass():
    """Requirement 15D: Proves that a skipped node becomes NOT_RUN, not PASS."""
    catalog_case = {"case_id": "P8-ELIG-001", "title": "Qualified Lead"}
    test_results = {
        "test_p8_elig_001_qualified_and_verified": {
            "status": "NOT_RUN",
            "reason": "Skipped due to missing environment variable",
        }
    }
    mapping = {
        "P8-ELIG-001": "tests/acceptance/test_phase8_acceptance.py::test_p8_elig_001_qualified_and_verified"
    }

    status, expected, actual, reason, _ = evaluate_catalog_case(catalog_case, test_results, mapping)
    assert status != "PASS"
    assert status == "NOT_RUN"
    assert actual.get("status") == "NOT_RUN"


def test_qa_meta_rule_e_semantic_deferred_remains_not_run_model_limitation():
    """Requirement 15E: Proves that semantic deferred cases remain NOT_RUN_MODEL_LIMITATION."""
    catalog_case = {
        "case_id": "P8-SEM-001",
        "title": "Strong BIM Automation Semantic Review",
        "mode": "local_live_llm",
    }
    test_results = {}
    mapping = {}

    status, expected, actual, reason, _ = evaluate_catalog_case(catalog_case, test_results, mapping)
    assert status == "NOT_RUN_MODEL_LIMITATION"
    assert expected["status"] == "NOT_RUN_MODEL_LIMITATION"
    assert actual["status"] == "NOT_RUN_MODEL_LIMITATION"


def test_qa_meta_rule_f_false_pass_count_is_calculated_not_hardcoded():
    """Requirement 15F: Proves false_pass_count is calculated dynamically from report cases."""
    # 1. Honest cases
    clean_cases = [
        {
            "case_id": "P8-ELIG-001",
            "status": "PASS",
            "mapped_test": "tests/test.py::test_1",
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": False,
        }
    ]
    assert calculate_false_pass_count(clean_cases) == 0

    # 2. Case fraudulently marked PASS with no mapped test
    unmapped_pass = [
        {
            "case_id": "P8-FAKE-001",
            "status": "PASS",
            "mapped_test": None,
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": False,
        }
    ]
    assert calculate_false_pass_count(unmapped_pass) == 1

    # 3. Case fraudulently marked PASS but actual test failed
    failed_actual = [
        {
            "case_id": "P8-FAKE-002",
            "status": "PASS",
            "mapped_test": "tests/test.py::test_2",
            "expected": {"status": "PASS"},
            "actual": {"status": "FAIL"},
            "has_stub_pass": False,
        }
    ]
    assert calculate_false_pass_count(failed_actual) == 1

    # 4. Case fraudulently marked PASS on an empty pass stub
    stub_pass = [
        {
            "case_id": "P8-FAKE-003",
            "status": "PASS",
            "mapped_test": "tests/test.py::test_3",
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": True,
        }
    ]
    assert calculate_false_pass_count(stub_pass) == 1


def test_qa_meta_rule_g_expected_and_actual_are_independently_sourced():
    """Requirement 15G: Proves report Expected and Actual are independently sourced."""
    catalog_case = {"case_id": "P8-ID-001", "title": "Lead ID Preserved"}
    test_results = {
        "test_p8_id_001_to_004_identity_fields_preserved": {
            "status": "PASS",
            "execution_time_s": 0.042,
        }
    }
    mapping = {
        "P8-ID-001": "tests/acceptance/test_phase8_acceptance.py::test_p8_id_001_to_004_identity_fields_preserved"
    }

    status, expected, actual, reason, _ = evaluate_catalog_case(catalog_case, test_results, mapping)
    # Expected is the catalog specification contract
    assert expected == {"status": "PASS"}
    # Actual contains the execution telemetry from pytest
    assert actual["status"] == "PASS"
    assert actual["test_node"] == mapping["P8-ID-001"]
    assert actual["execution_time_s"] == 0.042
    # Verify they are not a shallow or deep identical copy of metadata
    assert expected is not actual
    assert set(expected.keys()) != set(actual.keys())


# =========================================================================
# PHASE 9 QA INTEGRITY & META-TEST SUITE
# =========================================================================

from scripts.update_phase9_report import (
    evaluate_phase9_case,
    calculate_phase9_false_pass_count,
    is_test_empty_or_trivial,
)


def test_p9_qa_001_unmapped_deterministic_case_cannot_pass():
    """P9-QA-001: Proves that an unmapped deterministic Golden case cannot generate PASS."""
    catalog_case = {"case_id": "P9-STORE-999", "title": "Unmapped Storage Test"}
    test_results = {"some_test": {"status": "PASS"}}
    mapping = {}

    status, expected, actual, reason, _ = evaluate_phase9_case(catalog_case, test_results, mapping)
    assert status != "PASS", "Unmapped deterministic case must NEVER become PASS"
    assert status == "NOT_RUN"
    assert actual.get("error") == "no_mapped_test"


def test_p9_qa_002_empty_pass_test_cannot_count_as_coverage(tmp_path):
    """P9-QA-002: Proves that an empty `pass` test cannot count as real coverage and forces FAIL."""
    dummy_file = tmp_path / "test_dummy_p9.py"
    dummy_file.write_text(
        """def test_empty_pass():
    pass
""",
        encoding="utf-8",
    )
    assert is_test_empty_or_trivial(f"{dummy_file}::test_empty_pass") is True

    catalog_case = {"case_id": "P9-STORE-001", "title": "Persist Draft"}
    test_results = {"test_empty_pass": {"status": "PASS", "execution_time_s": 0.01}}
    mapping = {"P9-STORE-001": f"{dummy_file}::test_empty_pass"}

    status, expected, actual, reason, has_stub = evaluate_phase9_case(catalog_case, test_results, mapping)
    assert status == "FAIL"
    assert has_stub is True
    assert "empty pass stub" in actual.get("error", "").lower()


def test_p9_qa_003_skipped_live_smtp_becomes_not_run():
    """P9-QA-003: Proves that skipped optional live SMTP case becomes NOT_RUN, never PASS."""
    catalog_case = {"case_id": "P9-LIVE-SMTP-001", "title": "Optional Live SMTP Test", "mode": "live_optional"}
    test_results = {
        "test_p9_live_smtp_001_live_send_guardrail": {
            "status": "NOT_RUN",
            "reason": "Skipped: live email tests not explicitly enabled",
        }
    }
    mapping = {
        "P9-LIVE-SMTP-001": "tests/acceptance/test_phase9_acceptance.py::test_p9_live_smtp_001_live_send_guardrail"
    }

    status, expected, actual, reason, _ = evaluate_phase9_case(catalog_case, test_results, mapping)
    assert status == "NOT_RUN"
    assert actual.get("status") == "NOT_RUN"


def test_p9_qa_004_failed_node_maps_to_fail():
    """P9-QA-004: Proves that a failed pytest node maps to case FAIL."""
    catalog_case = {"case_id": "P9-REG-001", "title": "Stale Approval Blocked"}
    test_results = {
        "test_stale_approval_fingerprint_mismatch_blocks_send": {
            "status": "FAIL",
            "error": "AssertionError: stale approval allowed",
        }
    }
    mapping = {
        "P9-REG-001": "tests/test_sending.py::test_stale_approval_fingerprint_mismatch_blocks_send"
    }

    status, expected, actual, reason, _ = evaluate_phase9_case(catalog_case, test_results, mapping)
    assert status == "FAIL"
    assert actual.get("status") == "FAIL"
    assert "AssertionError" in actual.get("error", "")


def test_p9_qa_005_expected_and_actual_independently_sourced():
    """P9-QA-005: Proves report Expected and Actual are independently sourced."""
    catalog_case = {"case_id": "P9-STORE-001", "title": "Persist Draft"}
    test_results = {
        "test_p9_store_001_to_006_persistence_and_isolation": {
            "status": "PASS",
            "execution_time_s": 0.035,
        }
    }
    mapping = {
        "P9-STORE-001": "tests/acceptance/test_phase9_acceptance.py::test_p9_store_001_to_006_persistence_and_isolation"
    }

    status, expected, actual, reason, _ = evaluate_phase9_case(catalog_case, test_results, mapping)
    assert expected == {"status": "PASS"}
    assert actual["status"] == "PASS"
    assert actual["test_node"] == mapping["P9-STORE-001"]
    assert actual["execution_time_s"] == 0.035
    assert expected is not actual
    assert set(expected.keys()) != set(actual.keys())


def test_p9_qa_006_false_pass_count_computed_dynamically():
    """P9-QA-006: Proves false_pass_count is calculated dynamically from report cases."""
    clean_cases = [
        {
            "case_id": "P9-STORE-001",
            "status": "PASS",
            "mapped_test": "tests/acceptance/test_phase9_acceptance.py::test_store",
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": False,
        }
    ]
    assert calculate_phase9_false_pass_count(clean_cases) == 0

    unmapped_pass = [
        {
            "case_id": "P9-STORE-999",
            "status": "PASS",
            "mapped_test": None,
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": False,
        }
    ]
    assert calculate_phase9_false_pass_count(unmapped_pass) == 1

    stub_pass = [
        {
            "case_id": "P9-STORE-002",
            "status": "PASS",
            "mapped_test": "tests/dummy.py::test_stub",
            "expected": {"status": "PASS"},
            "actual": {"status": "PASS"},
            "has_stub_pass": True,
        }
    ]
    assert calculate_phase9_false_pass_count(stub_pass) == 1


def test_p9_qa_007_mock_provider_success_labeled_mock_not_live_smtp():
    """P9-QA-007: Proves that mock provider success is labeled mock provider, never live SMTP success."""
    from sales_engine.sending.mock_sender import MockEmailSender
    sender = MockEmailSender()
    res = sender.send_email(
        draft_id="draft:test-001",
        revision=1,
        send_key="key-001",
        to_email="test@domain.com",
        from_email="sender@domain.com",
        from_name="Sender",
        subject="Hello",
        body="World",
    )
    assert res.status == "sent"
    assert res.provider_message_id is not None
    assert res.provider_message_id.startswith("mock-"), "Mock provider message IDs must be labeled with mock- prefix"
    assert len(sender.sent_messages) == 1, "Mock provider must record in memory, not network"


def test_p9_qa_008_live_send_cannot_run_without_explicit_flag():
    """P9-QA-008: Proves that a live-send test cannot execute without explicit RUN_LIVE_EMAIL_TESTS=true."""
    import os
    run_live = os.getenv("RUN_LIVE_EMAIL_TESTS", "false").lower() == "true"
    email_enabled = os.getenv("EMAIL_SEND_ENABLED", "false").lower() == "true"
    assert not (run_live and email_enabled), (
        "Standard test environment MUST NOT have both RUN_LIVE_EMAIL_TESTS and EMAIL_SEND_ENABLED set to true"
    )

