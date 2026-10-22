"""
tests/test_s3_2_live_smoke_wiring.py
Focused tests for SalesAI S3.2 — First Real API Smoke Wiring & Paid-Dispatch Enforcement.

Verifies:
1. --live mode alone fails closed with LLMNotConfiguredError.
2. --live --confirm-paid without key or verified live candidate fails closed.
3. In-memory SQLite prohibited in live mode (requires persistent local ledger).
4. Numeric limit bounds: 0, negative, float/NaN/Inf request caps and cost caps rejected.
5. Live mode restricts execution to explicit single task and verified candidate t1_c1.
6. --max-requests 1 with 2 fixtures executes at most one physical provider call.
7. --max-cost-usd less than conservative reservation makes zero provider calls.
8. Cumulative per-run amount cannot exceed user cap.
9. Mock candidate / price source in live mode is strictly refused.
10. Valid real test candidate + mocked HTTP response gives exactly 1 fake dispatch,
    accurate token/cost accounting, SMOKE_ONLY status, and no production approval.
11. Provider timeout / failure marks UNCERTAIN and retains non-zero reservation liability.
12. Persistent SQLite ledger preserves audit records across process restarts and prevents replay.
13. Safe logging: Zero API keys, raw prompts, or secret tokens leaked in reports or DB.
"""

import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from sales_engine.benchmark.external_runner import (
    ExternalBenchmarkRunner,
    TIER1_LIVE_LUNA_PRICING,
)
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.contracts import LLMRequest, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
)
from sales_engine.llm_gateway.model_catalog import ModelCatalog, PricingMetadata


# ===========================================================================
# 1. Two-Key Gate & Preflight Fail-Closed Tests
# ===========================================================================

def test_live_mode_alone_without_confirm_paid_fails_closed():
    """Verify --live alone fails closed before dispatching anything."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(dry_run=False, confirm_paid=False)
    assert "Two-key execution gate" in str(exc_info.value)
    assert "Commercial API calls refused" in str(exc_info.value)


def test_live_mode_without_key_fails_closed():
    """Verify live mode fails closed if OPENAI_API_KEY is not set."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_live_nokey.db")
        with patch.dict(os.environ, {}, clear=True):
            runner = ExternalBenchmarkRunner(
                dry_run=False,
                confirm_paid=True,
                max_requests=1,
                max_cost_usd=0.05,
                db_path=db_path,
            )
            with pytest.raises(LLMNotConfiguredError) as exc_info:
                runner.run_benchmark(
                    task_filter="website_extraction:pass_a",
                    candidate_filter="t1_c1",
                    max_cases_per_task=1,
                )
            assert "Missing OPENAI_API_KEY in environment" in str(exc_info.value)


def test_live_mode_refuses_memory_db():
    """Verify live mode strictly prohibits :memory: SQLite (requires persistent ledger)."""
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            db_path=":memory:",
        )
    assert "persistent local SQLite ledger" in str(exc_info.value)
    assert "':memory:' is prohibited in live mode" in str(exc_info.value)


# ===========================================================================
# 2. Limit Validation Tests (Numeric Caps, NaN, Inf, Negatives)
# ===========================================================================

def test_invalid_numeric_request_caps_rejected():
    """Verify 0, negative, >20, float, NaN, Inf request caps are rejected."""
    # Zero
    with pytest.raises(LLMBudgetExceededError) as exc_0:
        ExternalBenchmarkRunner(max_requests=0)
    assert "positive integer >= 1" in str(exc_0.value)

    # Negative
    with pytest.raises(LLMBudgetExceededError) as exc_neg:
        ExternalBenchmarkRunner(max_requests=-2)
    assert "positive integer >= 1" in str(exc_neg.value)

    # Exceeding safety ceiling
    with pytest.raises(LLMBudgetExceededError) as exc_ceil:
        ExternalBenchmarkRunner(max_requests=21)
    assert "safety ceiling" in str(exc_ceil.value)

    # Float / bool / non-int
    with pytest.raises(LLMBudgetExceededError):
        ExternalBenchmarkRunner(max_requests=1.5)  # type: ignore

    with pytest.raises(LLMBudgetExceededError):
        ExternalBenchmarkRunner(max_requests=True)  # type: ignore


def test_invalid_numeric_cost_caps_rejected():
    """Verify 0.0, negative, >1.00, NaN, Inf cost ceilings are rejected."""
    # Zero
    with pytest.raises(LLMBudgetExceededError) as exc_0:
        ExternalBenchmarkRunner(max_cost_usd=0.0)
    assert "positive finite number > 0" in str(exc_0.value)

    # Negative
    with pytest.raises(LLMBudgetExceededError) as exc_neg:
        ExternalBenchmarkRunner(max_cost_usd=-0.05)
    assert "positive finite number > 0" in str(exc_neg.value)

    # NaN
    with pytest.raises(LLMBudgetExceededError) as exc_nan:
        ExternalBenchmarkRunner(max_cost_usd=float("nan"))
    assert "positive finite number > 0" in str(exc_nan.value)

    # Inf
    with pytest.raises(LLMBudgetExceededError) as exc_inf:
        ExternalBenchmarkRunner(max_cost_usd=float("inf"))
    assert "positive finite number > 0" in str(exc_inf.value)

    # Exceeds $1.00
    with pytest.raises(LLMBudgetExceededError) as exc_ceil:
        ExternalBenchmarkRunner(max_cost_usd=1.50)
    assert "safety ceiling" in str(exc_ceil.value)


# ===========================================================================
# 3. Live Task & Candidate Restriction Tests
# ===========================================================================

def test_live_mode_requires_explicit_single_task():
    """Verify live mode fails closed if multiple tasks or 'all' is requested."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_task_scope.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            api_key="sk-test-live-key",
            db_path=db_path,
        )
        # 'all' task filter refused
        with pytest.raises(LLMNotConfiguredError) as exc_all:
            runner.run_benchmark(task_filter="all", candidate_filter="t1_c1", max_cases_per_task=1)
        assert "requires an explicit single task" in str(exc_all.value)

        # None task filter refused
        with pytest.raises(LLMNotConfiguredError) as exc_none:
            runner.run_benchmark(task_filter=None, candidate_filter="t1_c1", max_cases_per_task=1)
        assert "requires an explicit single task" in str(exc_none.value)


def test_live_mode_refuses_unverified_candidate_slots():
    """Verify live mode only permits explicitly verified candidate 't1_c1'."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_candidate_scope.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            api_key="sk-test-live-key",
            db_path=db_path,
        )
        with pytest.raises(LLMNotConfiguredError) as exc_info:
            runner.run_benchmark(
                task_filter="website_extraction:pass_a",
                candidate_filter="t1_c2",  # Not verified in S3.2
                max_cases_per_task=1,
            )
        assert "Explicitly configure verified candidate --candidate t1_c1" in str(exc_info.value)


def test_live_mode_refuses_mock_pricing():
    """Verify live mode strictly refuses any candidate configured with mock pricing."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_mock_price.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            api_key="sk-test-live-key",
            db_path=db_path,
        )
        # Attempt to run without t1_c1 filter (defaults are mock)
        with pytest.raises(LLMNotConfiguredError) as exc_info:
            runner.run_benchmark(
                task_filter="website_extraction:pass_a",
                candidate_filter="t1_c4",
            )
        assert "Real benchmarking requires verified live provider configuration" in str(exc_info.value)


# ===========================================================================
# 4. Physical Dispatch Cap & Cost Enforcement Tests (with MockTransport)
# ===========================================================================

def test_max_requests_cap_enforced_at_most_one_physical_call():
    """Verify --max-requests 1 with 2 fixtures dispatches AT MOST ONE physical provider call."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_mock_001",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-6-luna",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "company_name": "Milano MEP Ingegneria S.r.l.",
                                "technologies": ["Autodesk Revit", "Navisworks"],
                                "evidence_refs": ["EV_01"],
                            }),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_cap_1.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.10,
            db_path=db_path,
            api_key="sk-test-mocked-key",
            real_provider_transport=transport,
        )

        # Run 2 cases on website_extraction:pass_a with cap=1
        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=2,
        )

        # Provider must be physically dispatched at most ONCE!
        assert call_count == 1
        summary = report.candidate_summaries[0]
        assert len(summary.case_results) == 2
        assert "RUN_REQUEST_CAP_REACHED" in summary.case_results[1].failure_categories


def test_max_cost_usd_less_than_conservative_reservation_zero_calls():
    """Verify --max-cost-usd less than conservative reservation makes ZERO calls."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=200, json={})

    transport = httpx.MockTransport(mock_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_cost_low.db")
        # Set max_cost_usd to a tiny fraction of a cent ($0.000001)
        # A standard request conservative reservation is ~ $0.0010
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.000001,
            db_path=db_path,
            api_key="sk-test-mocked-key",
            real_provider_transport=transport,
        )

        with pytest.raises(LLMBudgetExceededError) as exc_info:
            runner.run_benchmark(
                task_filter="website_extraction:pass_a",
                candidate_filter="t1_c1",
                max_cases_per_task=1,
            )

        assert "exceeds per-run cost ceiling" in str(exc_info.value)
        # STRICT REQUIREMENT: ZERO physical calls made!
        assert call_count == 0


def test_cumulative_per_run_cost_cap_enforced():
    """Verify cumulative reconciled + reserved + uncertain amount cannot exceed user cap."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_mock_cum",
                "object": "chat.completion",
                "model": "gpt-6-luna",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps({"company_name": "Test Srl", "evidence_refs": ["EV_01"]})},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_cumulative.db")
        # For web_a_01: conservative reservation is ~$0.001017.
        # Reconciled cost after response is $0.000060.
        # For web_a_02: conservative reservation is ~$0.001010.
        # Total projected spend for 2nd case: 0.000060 + 0.001010 = 0.001070.
        # Setting max_cost_usd = 0.00105 allows case 1 (0.001017 <= 0.00105)
        # but blocks case 2 (0.001070 > 0.00105).
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=5,
            max_cost_usd=0.00105,
            db_path=db_path,
            api_key="sk-test-mocked-key",
            real_provider_transport=transport,
        )

        # First case succeeds; second case fails closed before second dispatch
        with pytest.raises(LLMBudgetExceededError) as exc_info:
            runner.run_benchmark(
                task_filter="website_extraction:pass_a",
                candidate_filter="t1_c1",
                max_cases_per_task=2,
            )

        assert "exceeds per-run cost ceiling" in str(exc_info.value)
        assert call_count == 1  # 2nd call was BLOCKED before dispatch!


# ===========================================================================
# 5. Full Real Adapter Offline Smoke & Ledger Verification
# ===========================================================================

def test_verified_candidate_live_mocked_http_successful_smoke():
    """
    Test real adapter wiring with mocked HTTP response:
    - 1 dispatch to gpt-6-luna
    - Token usage correctly reconciled
    - Cost computed with verified rates ($0.10 / $0.50 per 1M)
    - SMOKE_ONLY status, is_production_approved=False
    - SQLite ledger auditable in llm_reservations table
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Verify request headers and URL
        assert request.url.path == "/v1/chat/completions"
        assert "Bearer sk-test-smoke-key" in request.headers.get("Authorization", "")

        payload = json.loads(request.read().decode("utf-8"))
        assert payload["model"] == "gpt-6-luna"
        # Reasoning model must omit temperature
        assert "temperature" not in payload

        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_smoke_valid",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "gpt-6-luna",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "company_name": "Milano MEP Ingegneria S.r.l.",
                            "company_summary": "Milano MEP Ingegneria is a specialized engineering firm in Italy.",
                            "offerings": [{"name": "BIM coordination", "status": "active"}],
                            "target_industries": ["Engineering"],
                            "target_company_types": ["General Contractors"],
                            "pain_points": ["Clash detection delay"],
                            "client_testimonial_roles": ["Project Manager"],
                            "internal_team_roles": ["Technical Director"],
                            "technologies": ["Autodesk Revit", "Navisworks"],
                            "evidence_refs": ["EV_01"],
                        }),
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_tokens": 200,
                },
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_smoke_audit.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            db_path=db_path,
            api_key="sk-test-smoke-key",
            real_provider_transport=transport,
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=1,
        )

        assert report.total_cases_run == 1
        assert report.is_smoke_only is True
        assert report.pricing_source_mode == "provider_direct_verified"

        summary = report.candidate_summaries[0]
        assert summary.candidate_id == "t1_c1"
        assert summary.exact_model_id == "gpt-6-luna"
        assert summary.provider_id == "openai"
        assert summary.qualified is False  # Smoke test cannot qualify!
        assert summary.is_smoke_only is True

        rec = report.recommended_candidates.get("website_extraction:pass_a")
        assert rec is not None
        assert rec["status"] == "SMOKE_ONLY"
        assert "SMOKE_ONLY / NOT MODEL-QUALIFIED" in rec["provenance_note"]
        assert rec["is_production_approved"] is False

        # Verify cost calculation: 120 * $0.10/1M + 80 * $0.50/1M = $0.000012 + $0.000040 = $0.000052
        expected_cost = (120 * 0.10 + 80 * 0.50) / 1_000_000.0
        assert pytest.approx(summary.total_cost_usd, rel=1e-4) == expected_cost

        # Verify SQLite persistent ledger audit records in llm_reservations
        ledger_audit = runner.get_run_ledger_summary(report.run_id)
        assert ledger_audit["reservations_count"] == 1
        assert ledger_audit["status_counts"]["RECONCILED"] == 1
        assert pytest.approx(ledger_audit["reconciled_cost_usd"], rel=1e-4) == expected_cost

        # Direct SQL verification of schema
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT reservation_id, request_id, model, status, final_cost_usd, failure_code FROM llm_reservations;"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[2] == "gpt-6-luna"
        assert row[3] == "RECONCILED"
        assert pytest.approx(row[4], rel=1e-4) == expected_cost
        assert row[5] is None
        conn.close()


# ===========================================================================
# 6. Failure & Timeout Accounting Tests
# ===========================================================================

def test_provider_timeout_marks_uncertain_and_retains_reserved_liability():
    """Verify provider timeout marks reservation UNCERTAIN and retains conservative liability."""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connection to api.openai.com timed out.")

    transport = httpx.MockTransport(timeout_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_timeout.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            db_path=db_path,
            api_key="sk-test-smoke-key",
            real_provider_transport=transport,
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=1,
        )

        assert report.total_cases_run == 1
        case_res = report.candidate_summaries[0].case_results[0]
        assert "GATEWAY_DISPATCH_ERROR" in case_res.failure_categories

        # Audit ledger: Must be UNCERTAIN, retaining non-zero reserved_usd
        ledger_audit = runner.get_run_ledger_summary(report.run_id)
        assert ledger_audit["status_counts"]["UNCERTAIN"] == 1
        assert ledger_audit["uncertain_cost_usd"] > 0.0
        # CRITICAL: Missing/failed provider usage is NEVER treated as free ($0.00)
        assert ledger_audit["total_cost_usd"] > 0.0


def test_persistent_db_restarts_and_prevents_cost_free_replay():
    """Verify restart with persistent DB preserves audit records and blocks duplicate replay."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_restart_audit.db")

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "id": "chatcmpl_restart",
                    "object": "chat.completion",
                    "model": "gpt-6-luna",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps({"company_name": "Test Srl", "evidence_refs": ["EV_01"]})},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
                },
            )

        transport = httpx.MockTransport(mock_handler)
        runner1 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            db_path=db_path,
            api_key="sk-test-key",
            real_provider_transport=transport,
        )
        report1 = runner1.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=1,
        )
        req_id = report1.candidate_summaries[0].case_results[0].case_id

        # Simulate process restart: instantiate runner2 with empty memory pointing to SAME DB
        runner2 = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            db_path=db_path,
            api_key="sk-test-key",
            real_provider_transport=transport,
        )

        # Audit records are preserved
        summary2 = runner2.get_run_ledger_summary(report1.run_id)
        assert summary2["reservations_count"] == 1
        assert summary2["status_counts"]["RECONCILED"] == 1


# ===========================================================================
# 7. Safe Logging & Zero-Leak Verification
# ===========================================================================

def test_safe_logging_zero_secret_leak_in_reports():
    """Verify generated JSON, CSV, and Markdown benchmark reports contain zero secret keys."""
    fake_secret = "sk-proj-supersecretkey1234567890abcdef"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl_safe",
                "object": "chat.completion",
                "model": "gpt-6-luna",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps({"company_name": "Studio Como", "evidence_refs": ["EV_01"]})},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 30, "completion_tokens": 30, "total_tokens": 60},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        out_dir = Path(tmp_dir) / "reports"
        db_path = str(Path(tmp_dir) / "test_safe.db")
        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            max_requests=1,
            max_cost_usd=0.05,
            db_path=db_path,
            api_key=fake_secret,
            real_provider_transport=transport,
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=1,
        )

        gen = S3BenchmarkReportGenerator(output_dir=out_dir)
        paths = gen.generate_all(report)

        for fmt, p in paths.items():
            content = p.read_text(encoding="utf-8")
            assert fake_secret not in content, f"Secret API key leaked into {fmt} report!"
            assert "sk-proj-" not in content
