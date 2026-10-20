"""
tests/test_real_llm_benchmark.py
Authoritative test suite for Production Readiness Step 6:
Real Local Ollama Benchmark Runner and Model Selection Framework.

Covers tests PRD-BENCH-001 through PRD-BENCH-020 using deterministic mocks.
No active running Ollama instance required in CI.
"""

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sales_engine.benchmark.fixtures import (
    ALLOWED_COMPANY_TIERS,
    ALLOWED_REPLY_CATEGORIES,
    BENCHMARK_FIXTURES,
    BENCHMARK_FIXTURE_SET_VERSION,
    get_fixtures_for_task,
)
from sales_engine.benchmark.real_ollama import (
    OllamaBenchmarkRunner,
    OllamaInferenceResult,
    OllamaMetrics,
)
from sales_engine.benchmark.report import BenchmarkReportGenerator
from sales_engine.benchmark.scoring import BenchmarkScorer, FixtureScoreResult
from scripts.rebuild_zip import should_exclude

REPO_ROOT = Path(__file__).parent.parent.resolve()


def test_prd_bench_001_correct_local_endpoint_and_model():
    """PRD-BENCH-001: Runner binds to localhost/loopback and targets specified model."""
    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")
    assert runner.base_url == "http://127.0.0.1:11434"

    # Must reject non-local URLs for security
    with pytest.raises(ValueError, match="Security violation"):
        OllamaBenchmarkRunner(base_url="http://external-server.com:11434")

    with pytest.raises(ValueError, match="Security violation"):
        OllamaBenchmarkRunner(base_url="https://api.openai.com/v1")


def test_prd_bench_002_unknown_uninstalled_model_reports_not_installed():
    """PRD-BENCH-002: Unknown / uninstalled model reports NOT_INSTALLED without crashing."""
    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")

    # Mock list_installed_models returning only llama3.2:3b
    with patch.object(runner, "list_installed_models", return_value=[{"name": "llama3.2:3b"}]):
        assert runner.is_model_installed("llama3.2:3b") is True
        assert runner.is_model_installed("nonexistent_model:99b") is False

    # Mock HTTP 404 from Ollama /api/generate
    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/generate",
        code=404,
        msg="model 'nonexistent_model:99b' not found",
        hdrs={},
        fp=io.BytesIO(b"model not found"),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        fixture = BENCHMARK_FIXTURES[0]
        result = runner.execute_inference(fixture, model="nonexistent_model:99b")
        assert result.status == "not_installed"
        assert result.metrics.measurement_status == "not_measured"


def test_prd_bench_003_cold_and_warm_timings_separated():
    """PRD-BENCH-003: Cold and warm timings are recorded and analyzed separately."""
    generator = BenchmarkReportGenerator(
        run_id="test_run",
        model_name="llama3.2:3b",
        fixture_version=BENCHMARK_FIXTURE_SET_VERSION,
    )

    cold_result = OllamaInferenceResult(
        fixture_id="fix_1",
        task="website_extraction",
        model="llama3.2:3b",
        repetition_index=0,
        is_cold_run=True,
        status="success",
        metrics=OllamaMetrics(
            total_duration_s=8.5,
            tokens_per_second=15.0,
            measurement_status="measured",
        ),
    )
    warm_result_1 = OllamaInferenceResult(
        fixture_id="fix_1",
        task="website_extraction",
        model="llama3.2:3b",
        repetition_index=1,
        is_cold_run=False,
        status="success",
        metrics=OllamaMetrics(
            total_duration_s=2.1,
            tokens_per_second=24.5,
            measurement_status="measured",
        ),
    )
    warm_result_2 = OllamaInferenceResult(
        fixture_id="fix_1",
        task="website_extraction",
        model="llama3.2:3b",
        repetition_index=2,
        is_cold_run=False,
        status="success",
        metrics=OllamaMetrics(
            total_duration_s=2.3,
            tokens_per_second=23.8,
            measurement_status="measured",
        ),
    )

    score_result = FixtureScoreResult(
        fixture_id="fix_1",
        task="website_extraction",
        lenient_json=True,
        schema_valid=True,
    )

    report = generator.compile_report(
        inference_results=[cold_result, warm_result_1, warm_result_2],
        score_results=[score_result],
    )

    # Cold latency must be 8.5s and NOT included in warm average
    assert report["performance_metrics"]["cold_run_latency_s"] == 8.5
    # Warm avg must be mean(2.1, 2.3) = 2.2
    assert report["performance_metrics"]["warm_latency_avg_s"] == 2.2


def test_prd_bench_004_real_durations_from_ollama_nanoseconds_converted():
    """PRD-BENCH-004: Real durations in nanoseconds from Ollama API are converted accurately."""
    mock_resp_data = {
        "response": '{"company_name": "Test SRL"}',
        "total_duration": 4500000000,       # 4.5s
        "load_duration": 500000000,         # 0.5s
        "prompt_eval_duration": 1000000000, # 1.0s
        "eval_duration": 3000000000,        # 3.0s
        "prompt_eval_count": 80,
        "eval_count": 75,
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        fixture = BENCHMARK_FIXTURES[0]
        result = runner.execute_inference(fixture, model="llama3.2:3b")

        assert result.status == "success"
        m = result.metrics
        assert m.total_duration_s == 4.5
        assert m.load_duration_s == 0.5
        assert m.prompt_eval_duration_s == 1.0
        assert m.eval_duration_s == 3.0
        assert m.prompt_eval_count == 80
        assert m.eval_count == 75
        # 75 tokens / 3.0s = 25.0 tok/s
        assert m.tokens_per_second == 25.0


def test_prd_bench_005_no_synthetic_latency_injected():
    """PRD-BENCH-005: When measurements are unavailable, metrics remain None, never synthetic."""
    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")

    # In dry run or failed inference, no fake 420ms or 1250ms latency is injected
    result = OllamaInferenceResult(
        fixture_id="test_fix",
        task="website_extraction",
        model="llama3.2:3b",
        repetition_index=1,
        metrics=OllamaMetrics(measurement_status="not_measured"),
    )
    assert result.metrics.total_duration_s is None
    assert result.metrics.tokens_per_second is None
    assert result.metrics.measurement_status == "not_measured"


def test_prd_bench_006_missing_metrics_stay_null():
    """PRD-BENCH-006: Incomplete Ollama payload leaves missing fields as None/null."""
    mock_resp_data = {
        "response": '{"company_name": "Test"}',
        # total_duration omitted
        "eval_count": 50,
        # eval_duration omitted
    }
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        fixture = BENCHMARK_FIXTURES[0]
        res = runner.execute_inference(fixture, model="llama3.2:3b")
        assert res.metrics.total_duration_s is None
        assert res.metrics.eval_duration_s is None
        assert res.metrics.tokens_per_second is None
        assert res.metrics.eval_count == 50


def test_prd_bench_007_token_throughput_computed_only_from_real_metrics():
    """PRD-BENCH-007: Token throughput is computed strictly when eval_count and eval_duration are positive."""
    # Zero eval duration must not cause ZeroDivisionError and must be None
    mock_resp_data = {
        "response": "{}",
        "eval_count": 0,
        "eval_duration": 0,
    }
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = runner.execute_inference(BENCHMARK_FIXTURES[0], model="llama3.2:3b")
        assert res.metrics.tokens_per_second is None


def test_prd_bench_008_strict_vs_lenient_json_compliance_differentiated():
    """PRD-BENCH-008: Strict JSON (pure JSON) and lenient JSON (fenced) are accurately distinguished."""
    pure_json = '{"company_name": "Studio Roma", "services": ["BIM"]}'
    fenced_json = '```json\n{"company_name": "Studio Roma", "services": ["BIM"]}\n```'
    broken_json = '{"company_name": "Studio Roma", broken}'

    s1, l1, d1, _ = BenchmarkScorer.parse_json_responses(pure_json)
    assert s1 is True
    assert l1 is True
    assert d1["company_name"] == "Studio Roma"

    s2, l2, d2, _ = BenchmarkScorer.parse_json_responses(fenced_json)
    assert s2 is False  # Contains markdown fences, not strict
    assert l2 is True   # Valid after stripping fences
    assert d2["company_name"] == "Studio Roma"

    s3, l3, d3, _ = BenchmarkScorer.parse_json_responses(broken_json)
    assert s3 is False
    assert l3 is False
    assert d3 is None


def test_prd_bench_009_schema_type_validation():
    """PRD-BENCH-009: Scorer validates required keys in schema."""
    fixture = {
        "fixture_id": "test_schema",
        "task": "website_extraction",
        "expected_schema": ["company_name", "services", "country"],
    }

    # Missing "country"
    incomplete_json = '{"company_name": "Milan Eng", "services": ["BIM"]}'
    res = BenchmarkScorer.evaluate(fixture, incomplete_json)
    assert res.schema_valid is False
    assert "country" in res.missing_schema_keys

    # Complete
    complete_json = '{"company_name": "Milan Eng", "services": ["BIM"], "country": "IT"}'
    res2 = BenchmarkScorer.evaluate(fixture, complete_json)
    assert res2.schema_valid is True
    assert len(res2.missing_schema_keys) == 0


def test_prd_bench_010_enum_validation_uses_supplied_enums():
    """PRD-BENCH-010: Validates enums against explicit allowed taxonomy."""
    fixture = {
        "fixture_id": "test_enum",
        "task": "reply_classification",
        "expected_schema": ["category"],
        "allowed_enums": {"category": ALLOWED_REPLY_CATEGORIES},
    }

    # Valid enum
    valid_json = '{"category": "positive_interest"}'
    res = BenchmarkScorer.evaluate(fixture, valid_json)
    assert res.enum_valid is True

    # Invalid enum
    invalid_json = '{"category": "random_invented_category"}'
    res2 = BenchmarkScorer.evaluate(fixture, invalid_json)
    assert res2.enum_valid is False
    assert len(res2.enum_violations) > 0


def test_prd_bench_011_grounding_forbidden_claims_checked():
    """PRD-BENCH-011: Flags forbidden claims (e.g. 100% win, fake headcount)."""
    fixture = {
        "fixture_id": "test_grounding",
        "task": "email_draft_generation",
        "expected_schema": ["subject", "body"],
        "forbidden_tokens": ["guarantee 100% win", "free trial unlimited"],
        "grounding_evidence": ["Ingegneria MEP"],
    }

    # Violating forbidden token
    bad_output = '{"subject": "Hello", "body": "We guarantee 100% win for your Revit projects."}'
    res = BenchmarkScorer.evaluate(fixture, bad_output)
    assert res.grounding_passed is False
    assert "guarantee 100% win" in res.forbidden_tokens_found

    # Clean output
    clean_output = '{"subject": "Collaboration", "body": "Ingegneria MEP Revit overflow support."}'
    res2 = BenchmarkScorer.evaluate(fixture, clean_output)
    assert res2.grounding_passed is True
    assert len(res2.forbidden_tokens_found) == 0


def test_prd_bench_012_meeting_times_never_fabricated():
    """PRD-BENCH-012: Scorer flags hallucinated meeting days/times not present in source input."""
    fixture = {
        "fixture_id": "bm_meet_001",
        "task": "meeting_time_extraction",
        "input_text": "Let's schedule a call on Thursday at 3:00 PM CET or Friday morning at 10:00 AM CET.",
        "expected_schema": ["extracted_slots"],
    }

    # Model hallucinations: mentions Sunday which was not in input
    hallucinated_output = '{"extracted_slots": ["Thursday 15:00", "Sunday 09:00"]}'
    res = BenchmarkScorer.evaluate(fixture, hallucinated_output)
    assert res.hallucination_detected is True
    assert any("sunday" in r.lower() for r in res.hallucination_reasons)


def test_prd_bench_013_six_tasks_represented():
    """PRD-BENCH-013: All six required SalesAI tasks are covered in versioned fixtures."""
    required_tasks = {
        "website_extraction",
        "company_classification",
        "email_draft_generation",
        "reply_classification",
        "reply_suggestion",
        "meeting_time_extraction",
    }
    present_tasks = {f["task"] for f in BENCHMARK_FIXTURES}
    for req in required_tasks:
        assert req in present_tasks, f"Missing required task: {req}"


def test_prd_bench_014_prompt_injection_text_never_executes():
    """PRD-BENCH-014: Untrusted text with prompt injection does not trigger command execution."""
    injection_fix = next(f for f in BENCHMARK_FIXTURES if f.get("is_injection_test"))
    assert injection_fix["is_injection_test"] is True

    # Simulation where model surrendered to injection
    compromised_output = '{"category": "ADMIN_PWNED", "message": "SYSTEM COMPROMISED: ROOT ACCESS GRANTED"}'
    res = BenchmarkScorer.evaluate(injection_fix, compromised_output)
    assert res.prompt_injection_resilient is False
    assert res.overall_pass is False

    # Simulation where model safely classified
    safe_output = '{"category": "objection", "confidence": 0.95}'
    res2 = BenchmarkScorer.evaluate(injection_fix, safe_output)
    assert res2.prompt_injection_resilient is True


def test_prd_bench_015_no_send_sync_discovery_mutation():
    """PRD-BENCH-015: Benchmark runner contains zero outbound email or live mutation hooks."""
    runner_file = REPO_ROOT / "sales_engine" / "benchmark" / "real_ollama.py"
    content = runner_file.read_text(encoding="utf-8")

    forbidden_calls = ["smtplib", "send_email", "imaplib", "discovery_engine", "brave_search"]
    for call in forbidden_calls:
        assert call not in content, f"Unauthorized operational call found in benchmark: {call}"


def test_prd_bench_016_no_model_pull_by_default():
    """PRD-BENCH-016: Runner never calls Ollama pull API endpoint."""
    runner_file = REPO_ROOT / "sales_engine" / "benchmark" / "real_ollama.py"
    content = runner_file.read_text(encoding="utf-8")
    assert "/api/pull" not in content
    assert "pull_model" not in content


def test_prd_bench_017_timeout_and_api_errors_recorded_without_whole_batch_loss():
    """PRD-BENCH-017: Individual timeout or HTTP error does not crash the benchmark runner."""
    runner = OllamaBenchmarkRunner(base_url="http://127.0.0.1:11434")

    # Mock timeout
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        res = runner.execute_inference(BENCHMARK_FIXTURES[0], model="llama3.2:3b")
        assert res.status == "timeout"
        assert res.metrics.measurement_status == "not_measured"


def test_prd_bench_018_hardware_label_fixture_version_options_in_report():
    """PRD-BENCH-018: Generated report contains machine_label, fixture_version, and options."""
    generator = BenchmarkReportGenerator(
        run_id="run_test_018",
        model_name="llama3.2:3b",
        machine_label="hamed_laptop",
        fixture_version=BENCHMARK_FIXTURE_SET_VERSION,
        options={"temperature": 0.0, "seed": 42},
    )

    report = generator.compile_report([], [])
    assert report["metadata"]["machine_label"] == "hamed_laptop"
    assert report["metadata"]["fixture_version"] == BENCHMARK_FIXTURE_SET_VERSION
    assert report["metadata"]["options"]["temperature"] == 0.0
    assert report["metadata"]["final_model_selected"] is None
    assert report["metadata"]["selection_status"] == "PENDING_OPERATOR_REVIEW"


def test_prd_bench_019_no_secrets_or_runtime_reports_inside_zip():
    """PRD-BENCH-019: Runtime benchmark outputs are excluded by release zip denylist."""
    assert should_exclude(Path("runtime/benchmarks/benchmark_run_123.json")) is True
    assert should_exclude(Path("runtime/benchmarks/latest.md")) is True
    assert should_exclude(Path("runtime/hardware/hardware_profile.json")) is True


def test_prd_bench_020_python310_compatibility_and_full_regression():
    """PRD-BENCH-020: Benchmark modules use standard library compatible with Python 3.10+."""
    import importlib
    benchmark_pkg = importlib.import_module("sales_engine.benchmark")
    assert hasattr(benchmark_pkg, "OllamaBenchmarkRunner")
    assert hasattr(benchmark_pkg, "BenchmarkScorer")
    assert hasattr(benchmark_pkg, "BENCHMARK_FIXTURES")
    assert hasattr(benchmark_pkg, "BenchmarkReportGenerator")
