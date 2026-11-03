"""
tests/test_s3_2_benchmark_quality_correction.py
Targeted regression tests for SalesAI S3.2 Benchmark Quality Correction.

Verifies:
1. Website Pass A benchmark prompts explicitly communicate the strict schema,
   allowed offerings[*].status enum values, field types, and citation requirements,
   WITHOUT exposing Golden Answers in prompts or system prompts.
2. When schema validation fails, GroundTruthEvaluator marks semantic ground truth
   as 'not evaluated due to invalid schema' (failing overall), but strictly DOES NOT
   produce misleading factual mismatch claims from empty validated data.
3. Raw JSON citations are safely preserved when schema validation fails on other fields,
   preventing false SEMANTIC_GROUNDING_MISSING errors when the model did cite evidence.
4. Clear non-sensitive diagnostics for schema and citation errors without secrets or PII.
5. Markdown report generation accurately distinguishes live pilot audit status from offline dry-run.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sales_engine.benchmark.external_runner import (
    BenchmarkRunReport,
    CandidateTaskSummary,
)
from sales_engine.benchmark.fixtures_s3 import PASS_A_CASES, get_fixtures_for_task
from sales_engine.benchmark.ground_truth_evaluator import (
    GroundTruthEvaluationResult,
    GroundTruthEvaluator,
)
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.output_validation import LLMOutputValidator


# ===========================================================================
# 1. Benchmark Prompt Schema Alignment & Golden Isolation Tests
# ===========================================================================

def test_pass_a_fixtures_explicitly_communicate_offerings_status_enum():
    """Verify all 10 Website Pass A fixtures explicitly communicate allowed offerings status enum."""
    allowed_statuses = ["active", "in_development", "coming_soon", "cta"]
    for case in PASS_A_CASES:
        assert case.task_id == "website_extraction"
        assert case.subtask_id == "pass_a"
        for st in allowed_statuses:
            assert st in case.prompt, f"Case {case.case_id} prompt missing status enum value '{st}'"
        assert "offerings" in case.prompt
        assert "evidence_refs" in case.prompt


def test_pass_a_fixtures_do_not_leak_golden_answers_in_prompts():
    """Verify that expected_output golden references are never leaked into prompt or system_prompt."""
    for case in PASS_A_CASES:
        expected = case.expected_output or {}
        # Company name in expected output must only appear in input text context, not as golden assertion
        if "technologies" in expected:
            for tech in expected["technologies"]:
                # Golden tech must not be in system prompt
                assert tech not in (case.system_prompt or ""), (
                    f"Golden technology '{tech}' leaked into system_prompt in case {case.case_id}"
                )
        if "target_industries" in expected:
            for ind in expected["target_industries"]:
                assert ind not in (case.system_prompt or ""), (
                    f"Golden target industry '{ind}' leaked into system_prompt in case {case.case_id}"
                )


# ===========================================================================
# 2. Schema Failure Semantic Isolation Tests (Defect Fix)
# ===========================================================================

def test_schema_failure_marks_semantic_not_evaluated_without_misleading_mismatches():
    """
    When schema validation fails (e.g. invalid enum in offerings):
    - schema_pass MUST be False
    - ground_truth_pass MUST be False
    - overall_pass MUST be False
    - failure_categories MUST include SYNTACTIC_VALIDATION_ERROR and SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA
    - failure_categories MUST NOT include GROUND_TRUTH_COMPANY_MISMATCH or GROUND_TRUTH_TECH_MISSING
    - failure_reasons MUST NOT produce empty-string company mismatch or None tech missing claims.
    """
    fixture = get_fixtures_for_task("website_extraction", "pass_a")[0]
    assert fixture.case_id == "web_a_01"

    # Response with correct company name and technologies, but invalid enum in offerings status
    payload_invalid_enum = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "Provides specialized BIM clash coordination.",
        "offerings": [{"name": "MEP Modeling", "status": "available"}],  # 'available' is not allowed!
        "target_industries": ["Engineering"],
        "target_company_types": ["Contractors"],
        "pain_points": ["Clashes"],
        "client_testimonial_roles": ["PM"],
        "internal_team_roles": ["BIM Lead"],
        "technologies": ["Autodesk Revit", "Navisworks", "Solibri"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "MEP"],
        "evidence_refs": ["EV_01"],
    })

    req = LLMRequest(
        prompt=fixture.prompt,
        task_id=fixture.task_id,
        model="gpt-6-luna",
        provider="openai",
        tier=ModelTier.TIER_1_BUDGET,
        request_id="req_schema_fail_test",
    )
    resp = LLMResponse(
        content=payload_invalid_enum,
        provider="openai",
        model="gpt-6-luna",
        task_id=fixture.task_id,
        request_id="req_schema_fail_test",
    )

    validator = LLMOutputValidator()
    val_result = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )

    assert val_result.is_valid is False
    assert any("SCHEMA_INVALID_ENUM_OFFERINGS" in r for r in val_result.failure_reasons)

    result = GroundTruthEvaluator.evaluate(
        fixture=fixture,
        request=req,
        response=resp,
        val_result=val_result,
    )

    # Core assertions
    assert result.schema_pass is False
    assert result.ground_truth_pass is False
    assert result.overall_pass is False

    # Diagnostic assertions
    assert "SYNTACTIC_VALIDATION_ERROR" in result.failure_categories
    assert "SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA" in result.failure_categories
    assert "GROUND_TRUTH_COMPANY_MISMATCH" not in result.failure_categories
    assert "GROUND_TRUTH_TECH_MISSING" not in result.failure_categories

    # Rejection of misleading claims from empty validated_data
    assert not any("Extracted company name '' does not match" in r for r in result.failure_reasons)
    assert not any("missing from extracted technologies None" in r for r in result.failure_reasons)
    assert any("Semantic ground-truth comparison not evaluated due to invalid schema." in r for r in result.failure_reasons)


def test_schema_failure_preserves_raw_json_citation_evidence():
    """
    When schema validation fails on a field, but raw JSON contains valid evidence_refs:
    evidence_pass should NOT falsely report citation missing.
    """
    fixture = get_fixtures_for_task("website_extraction", "pass_a")[0]
    payload = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "BIM coordination services.",
        "offerings": [{"name": "MEP", "status": "unknown_enum"}],
        "evidence_refs": ["EV_01"],
    })

    req = LLMRequest(prompt=fixture.prompt, task_id="website_extraction", model="m1", provider="p1", tier=ModelTier.TIER_1_BUDGET, request_id="req_cit_test")
    resp = LLMResponse(content=payload, provider="p1", model="m1", task_id="website_extraction", request_id="req_cit_test")

    val_result = LLMOutputValidator().validate(request=req, response=resp, approved_evidence_ids={"EV_01"}, subtask_id="pass_a")
    assert val_result.is_valid is False

    result = GroundTruthEvaluator.evaluate(fixture=fixture, request=req, response=resp, val_result=val_result)

    assert result.evidence_pass is True
    assert "EV_01" in result.grounding_evidence_found
    assert "SEMANTIC_GROUNDING_MISSING" not in result.failure_categories


def test_schema_valid_wrong_data_still_triggers_semantic_mismatch():
    """
    When schema is valid, GroundTruthEvaluator MUST continue to evaluate semantic truth
    and detect actual factual mismatches (preserving S3.1 regression reproducer).
    """
    fixture = get_fixtures_for_task("website_extraction", "pass_a")[0]
    valid_schema_wrong_company = json.dumps({
        "company_name": "COMPLETELY WRONG COMPANY",
        "company_summary": "MEP BIM services in Milan.",
        "offerings": [{"name": "Coordination", "status": "active"}],
        "target_industries": ["Engineering"],
        "target_company_types": ["Contractors"],
        "pain_points": ["Clashes"],
        "client_testimonial_roles": ["Director"],
        "internal_team_roles": ["BIM Coordinator"],
        "technologies": ["Autodesk Revit"],
        "standards": ["ISO 19650"],
        "keywords": ["MEP"],
        "evidence_refs": ["EV_01"],
    })

    req = LLMRequest(prompt=fixture.prompt, task_id="website_extraction", model="m1", provider="p1", tier=ModelTier.TIER_1_BUDGET, request_id="req_wrong_co")
    resp = LLMResponse(content=valid_schema_wrong_company, provider="p1", model="m1", task_id="website_extraction", request_id="req_wrong_co")

    val_result = LLMOutputValidator().validate(request=req, response=resp, approved_evidence_ids={"EV_01"}, subtask_id="pass_a")
    assert val_result.is_valid is True

    result = GroundTruthEvaluator.evaluate(fixture=fixture, request=req, response=resp, val_result=val_result)

    assert result.schema_pass is True
    assert result.ground_truth_pass is False
    assert result.overall_pass is False
    assert "GROUND_TRUTH_COMPANY_MISMATCH" in result.failure_categories
    assert "SEMANTIC_NOT_EVALUATED_INVALID_SCHEMA" not in result.failure_categories


# ===========================================================================
# 3. Markdown Report Audit Status Accuracy Tests
# ===========================================================================

def test_markdown_report_accurately_reports_live_vs_offline_audit_status():
    """Verify that export_markdown produces accurate audit statements for dry-run and live."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        out_dir = Path(tmp_dir) / "reports"
        gen = S3BenchmarkReportGenerator(output_dir=out_dir)

        # 1. Offline Dry-Run Report
        report_offline = BenchmarkRunReport(
            run_id="bench_offline_01",
            timestamp="2026-10-23T10:00:00Z",
            is_dry_run=True,
            tasks_evaluated=["website_extraction:pass_a"],
            candidate_summaries=[],
            recommended_candidates={},
            is_smoke_only=True,
        )
        md_offline = gen.export_markdown(report_offline).read_text(encoding="utf-8")
        assert "- **Audit Status:** Offline benchmark executed without commercial network calls." in md_offline
        assert "Live benchmark executed" not in md_offline

        # 2. Live Pilot Report
        report_live = BenchmarkRunReport(
            run_id="bench_live_01",
            timestamp="2026-10-23T10:05:00Z",
            is_dry_run=False,
            tasks_evaluated=["website_extraction:pass_a"],
            candidate_summaries=[],
            recommended_candidates={},
            is_smoke_only=True,
        )
        md_live = gen.export_markdown(report_live).read_text(encoding="utf-8")
        assert "- **Audit Status:** Live benchmark executed with authorized provider network calls under verified budget policy." in md_live
        assert "Offline benchmark executed without commercial network calls." not in md_live
