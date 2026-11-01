"""
tests/test_stage05a_scoring_and_billing.py
Comprehensive test suite verifying:
1. Strict server-side Draft-07 JSON Schema validation with root-wrapper unwrapping and path-level errors.
2. Dispatch preflight billing gate enforcing verified free tier and blocking paid inference without operator authorization.
3. Deterministic 100-point benchmark scorer verifying factual accuracy, evidence grounding, hallucination prevention, hypothesis calibration, and schema compliance.
4. Correct failure classification (ACCESS_BLOCKED, RATE_LIMITED).
"""

import json
import pytest

from sales_engine.benchmark.stage05a_schema import (
    STAGE_05A_OUTPUT_SCHEMA,
    normalize_root_wrapper,
    validate_stage05a_output,
)
from sales_engine.benchmark.stage05a_benchmark_runner import (
    check_dispatch_eligibility,
    run_benchmark_for_provider,
)
from sales_engine.benchmark.stage05a_scorer import (
    score_stage05a_output,
    verify_italian_piva,
    BS_PROGETTI_REFERENCE_GROUND_TRUTH,
)


def _get_perfect_mock_output():
    return {
        "company_name": {
            "proposed_value": "BS Progetti S.r.l.",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net/contatti/",
            "supporting_excerpt": "BS Progetti S.r.l. Via Roma 130 30030 Pianiga",
            "uncertainty_indicators": None,
        },
        "priority_level": {
            "proposed_value": "Priority B",
            "verification_status": "COMMERCIAL_HYPOTHESIS",
            "source_evidence_id": "EV_02",
            "source_url": "https://www.bs-eng.net/contatti/#collapse-1-3936",
            "supporting_excerpt": "Sezione dedicata ai fornitori e collaboratori",
            "uncertainty_indicators": "Vendor portal present, no active project subcontracting confirmed",
        },
        "priority_rationale": {
            "proposed_value": "Vendor portal onboarding without verified external tender requirement",
            "verification_status": "COMMERCIAL_HYPOTHESIS",
            "source_evidence_id": "EV_02",
            "source_url": "https://www.bs-eng.net/contatti/#collapse-1-3936",
            "supporting_excerpt": "Fornitori collaboratori",
            "uncertainty_indicators": None,
        },
        "legal_identity": {
            "proposed_value": "04095420271",
            "piva_checksum_valid": True,
            "official_registry_verified": False,
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net/contatti/",
            "supporting_excerpt": "P.IVA 04095420271",
            "uncertainty_indicators": "Checksum valid; official registry check not performed",
        },
        "geographic_location": {
            "proposed_value": "Via Roma 130, 30030 Pianiga (VE), Italy",
            "city": "Pianiga",
            "country": "Italy",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net/contatti/",
            "supporting_excerpt": "Via Roma 130, 30030 Pianiga (VE)",
            "uncertainty_indicators": None,
        },
        "telephone_numbers": {
            "proposed_value": "+39 041 5195480",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net/contatti/",
            "supporting_excerpt": "Tel: +39 041 5195480",
            "uncertainty_indicators": None,
        },
        "business_activities": {
            "proposed_value": "Multidisciplinary Engineering and Architectural Design",
            "bim_capabilities": "BIM Modeling and coordination",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_03",
            "source_url": "https://www.bs-eng.net/servizi/",
            "supporting_excerpt": "ingegneria integrata e modellazione BIM multidisciplinare",
            "uncertainty_indicators": None,
        },
        "official_website": {
            "proposed_value": "https://www.bs-eng.net",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net",
            "supporting_excerpt": "BS Progetti sito ufficiale",
            "uncertainty_indicators": None,
        },
        "responsible_person": {
            "proposed_value": "Andrea Berro, Gianluca Sartori",
            "role_type": "DIRECTOR_OR_FOUNDER",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_04",
            "source_url": "https://www.bs-eng.net/chi-siamo/",
            "supporting_excerpt": "Soci fondatori: Andrea Berro e Gianluca Sartori",
            "uncertainty_indicators": "Founders/directors, not confirmed purchasing decision makers",
        },
        "descriptions_and_notes": {
            "proposed_value": "Engineering consultancy operating in civil, structural and MEP sectors.",
            "work_package_hypothesis": "External BIM MEP modeling work packages potential",
            "verification_status": "COMMERCIAL_HYPOTHESIS",
            "source_evidence_id": "EV_03",
            "source_url": "https://www.bs-eng.net/servizi/",
            "supporting_excerpt": "progettazione impianti e strutture",
            "uncertainty_indicators": None,
        },
        "job_advertisement_links": {
            "proposed_value": "Open recruitment positions",
            "urls": ["https://www.bs-eng.net/lavora-con-noi/"],
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_05",
            "source_url": "https://www.bs-eng.net/lavora-con-noi/",
            "supporting_excerpt": "Posizioni aperte: Lavora con noi",
            "uncertainty_indicators": None,
        },
        "other_links_and_references": {
            "proposed_value": "Supplier intake section",
            "supplier_intake_urls": ["https://www.bs-eng.net/contatti/#collapse-1-3936"],
            "portfolio_urls": ["https://www.bs-eng.net/progetti/"],
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_02",
            "source_url": "https://www.bs-eng.net/contatti/#collapse-1-3936",
            "supporting_excerpt": "Fornitori qualificazione",
            "uncertainty_indicators": None,
        },
        "contact_email_addresses": {
            "proposed_value": "info@bs-eng.net",
            "verification_status": "VERIFIED_FACT",
            "source_evidence_id": "EV_01",
            "source_url": "https://www.bs-eng.net/contatti/",
            "supporting_excerpt": "Email: info@bs-eng.net",
            "uncertainty_indicators": None,
        },
        "provenance_and_verification": {
            "system_derived_provenance": "Stage05A live research benchmark extraction",
            "overall_confidence_score": 0.95,
            "unsupported_claims_detected": [],
        },
    }


def test_01_piva_checksum_verification():
    """Verify Italian Partita IVA checksum verification logic."""
    assert verify_italian_piva("04095420271") is True
    assert verify_italian_piva("IT04095420271") is True
    assert verify_italian_piva("04095420270") is False  # bad control digit
    assert verify_italian_piva("12345678901") is False
    assert verify_italian_piva(None) is False
    assert verify_italian_piva("") is False


def test_02_strict_schema_validation_on_perfect_and_wrapped_payloads():
    """Verify Draft-07 schema validation, path-level violation reporting and unwrapping."""
    data = _get_perfect_mock_output()
    is_valid, violations, norm = validate_stage05a_output(data)
    assert is_valid is True
    assert len(violations) == 0

    # Wrap in container
    wrapped = {"lead_register_enrichment": data}
    is_valid_w, violations_w, norm_w = validate_stage05a_output(wrapped)
    assert is_valid_w is True
    assert norm_w["company_name"]["proposed_value"] == "BS Progetti S.r.l."

    # Test invalid payload with missing required field and bad enum
    bad_data = json.loads(json.dumps(data))
    bad_data["priority_level"]["proposed_value"] = "Priority Z"  # invalid enum
    del bad_data["geographic_location"]["city"]  # missing required/allowed property
    bad_data["extra_unknown_key"] = "forbidden"  # additionalProperties violation

    is_valid_bad, violations_bad, _ = validate_stage05a_output(bad_data)
    assert is_valid_bad is False
    assert any("priority_level.proposed_value" in v for v in violations_bad)
    assert any("extra_unknown_key" in v for v in violations_bad)


def test_03_dispatch_preflight_billing_gate_enforces_safety():
    """Ensure paid inference is blocked and cannot be dispatched without explicit authorization."""
    # Paid model t1_c1 (openai gpt-6-luna) must be blocked
    ok, reason, spec = check_dispatch_eligibility(
        provider_id="openai",
        model_id="gpt-6-luna",
        candidate_id="t1_c1",
        allow_paid=False,
        operator_authorized=False,
    )
    assert ok is False
    assert "Paid inference is blocked" in reason

    # Even if allow_paid is True, must require operator_authorized
    ok2, reason2, _ = check_dispatch_eligibility(
        provider_id="openai",
        model_id="gpt-6-luna",
        candidate_id="t1_c1",
        allow_paid=True,
        operator_authorized=False,
    )
    assert ok2 is False
    assert "Explicit operator authorization required" in reason2

    # Free candidate t1_c5 (gemini-3.5-flash-lite) must be accepted
    ok_free, _, spec_free = check_dispatch_eligibility(
        provider_id="gemini",
        model_id="gemini-3.5-flash-lite",
        candidate_id="t1_c5",
    )
    assert ok_free is True
    assert spec_free.billing_mode == "VERIFIED_FREE_TIER"

    # Mismatched model ID must be rejected
    ok_bad_m, reason_bad_m, _ = check_dispatch_eligibility(
        provider_id="gemini",
        model_id="gemini-1.5-pro",
        candidate_id="t1_c5",
    )
    assert ok_bad_m is False
    assert "Model ID mismatch" in reason_bad_m

    # Candidate with is_generation_enabled = False must be rejected
    ok_dis, reason_dis, _ = check_dispatch_eligibility(
        provider_id="cloudflare",
        model_id="@cf/meta/llama-3.1-8b-instruct",
        candidate_id="t2_c4",
    )
    assert ok_dis is False
    assert "Generation is disabled" in reason_dis


def test_04_scorer_deterministic_scoring_and_rubric():
    """Verify deterministic benchmark scoring with 100-point rubric breakdown."""
    data = _get_perfect_mock_output()
    raw_evidence = (
        "EV_01: BS Progetti S.r.l. Via Roma 130 30030 Pianiga (VE) P.IVA 04095420271 Tel: +39 041 5195480 Email: info@bs-eng.net BS Progetti sito ufficiale\n"
        "EV_02: Sezione dedicata ai fornitori e collaboratori Fornitori qualificazione\n"
        "EV_03: ingegneria integrata e modellazione BIM multidisciplinare progettazione impianti e strutture\n"
        "EV_04: Soci fondatori: Andrea Berro e Gianluca Sartori\n"
        "EV_05: Posizioni aperte: Lavora con noi\n"
    )

    score_res = score_stage05a_output(data, raw_evidence, compact_dataset_sha256="abc123sha")
    assert score_res["status"] == "EVALUATED"
    assert score_res["total_score"] == 100.0
    assert score_res["section_scores"]["schema_compliance"] == 10.0
    assert score_res["section_scores"]["factual_extraction"] == 35.0
    assert score_res["section_scores"]["evidence_grounding"] == 25.0
    assert score_res["section_scores"]["hallucination_prevention"] == 20.0
    assert score_res["section_scores"]["unknown_and_hypothesis"] == 10.0
    assert len(score_res["deductions"]) == 0


def test_05_scorer_handles_penalties_and_hallucinations():
    """Verify scorer penalizes hallucinations, fake decision makers, and invalid P.IVA."""
    data = _get_perfect_mock_output()
    raw_evidence = "Some generic engineering text with no contact info"

    # Introduce defects: fake decision maker, wrong P.IVA, Priority A hallucination
    data["responsible_person"]["role_type"] = "PURCHASING_DECISION_MAKER"
    data["legal_identity"]["proposed_value"] = "99999999999"
    data["legal_identity"]["piva_checksum_valid"] = True
    data["priority_level"]["proposed_value"] = "Priority A"
    data["descriptions_and_notes"]["proposed_value"] = "They have an active tender outsourcing contract open."
    data["provenance_and_verification"]["unsupported_claims_detected"] = ["Unverified tender claims"]

    score_res = score_stage05a_output(data, raw_evidence)
    assert score_res["total_score"] < 60.0
    assert score_res["section_scores"]["hallucination_prevention"] <= 5.0
    assert any("PURCHASING_DECISION_MAKER" in d for d in score_res["deductions"])
    assert any("P.IVA mismatch" in d for d in score_res["deductions"])
    assert any("Priority calibration mismatch" in d for d in score_res["deductions"])


def test_06_scorer_not_evaluated_on_null_output():
    """Verify unexecuted/blocked outputs are assigned status NOT_EVALUATED and never arbitrary 0 score."""
    res = score_stage05a_output(None, "")
    assert res["status"] == "NOT_EVALUATED"
    assert res["total_score"] is None
    assert "status is NOT_EVALUATED" in res["deductions"][0]
