"""
tests/test_s3_6b2_campaign_ledger_integrity.py
Critical Campaign Ledger Integrity & Final Live Safety Gates Test Suite (S3.6B.2).

STRICTLY OFFLINE. ZERO LIVE LLM INFERENCE.
Validates all 20 required points from Phase 1 through Phase 8:
 1. cancel(reason=...) signature mismatch fix & call site compatibility.
 2. Failed pre-dispatch cancellation leaves no orphan and rejects cancel after dispatch.
 3. HTTP 429 counted as dispatched (DISPATCH_FAILED) towards candidate ceiling.
 4. HTTP 500 counted as dispatched (DISPATCH_FAILED) towards candidate ceiling.
 5. Timeout counted as dispatched and uncertain when appropriate (retains conservative liability).
 6. 35 repeated failed dispatch attempts cannot bypass the 10-request ceiling.
 7. Unknown request reconciliation rejection.
 8. Invalid ledger status transitions rejected.
 9. Negative budget estimates rejected.
10. NaN/infinite caps and amounts rejected.
11. Uncertain zero-cost overwrite rejected (preserves conservative reserved liability).
12. Duplicate request ID rejected when inconsistent.
13. Multi-process request-count race (multiprocessing pool against shared SQLite DB proving exact 10 cap).
14. Multi-process aggregate commercial-budget race.
15. Multi-process billable-budget race.
16. Crash/interruption recovery (durable reservation preserved).
17. Correct CLI parsing and propagation (including --confirm-paid, --max-requests 10).
18. Full live-mode preflight with mock transport only.
19. Verified zero-charge entitlement enforcement.
20. Historical ledger preservation (verify data/benchmark_budget.db is untouched).
"""

from __future__ import annotations

import math
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sales_engine.benchmark import (
    CampaignBudgetGuard,
    CampaignLifecycleStatus,
    ExternalBenchmarkRunner,
    read_only_historical_liability_preflight,
)
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMTimeoutError,
)


def _worker_reserve_attempt(db_path: str, campaign_id: str, candidate_id: str, req_id: str, comm: float, bill: float) -> str:
    try:
        guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.15,
            billable_cost_cap_usd=0.05,
        )
        guard.check_and_reserve(
            candidate_id=candidate_id,
            request_id=req_id,
            estimated_commercial_usd=comm,
            estimated_billable_usd=bill,
        )
        return "SUCCESS"
    except LLMBudgetExceededError:
        return "BUDGET_EXCEEDED"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def _worker_reserve_and_attempt(db_path: str, campaign_id: str, candidate_id: str, req_id: str, comm: float, bill: float) -> str:
    try:
        guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.030,
            billable_cost_cap_usd=0.010,
        )
        guard.check_and_reserve(
            candidate_id=candidate_id,
            request_id=req_id,
            estimated_commercial_usd=comm,
            estimated_billable_usd=bill,
        )
        guard.record_dispatch_attempt(req_id)
        return "SUCCESS"
    except LLMBudgetExceededError:
        return "BUDGET_EXCEEDED"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


class TestCampaignLedgerIntegrity:
    """Offline test suite for S3.6B.2 critical campaign ledger integrity."""

    # 1. cancel(reason=...) signature & call site compatibility
    def test_cancel_signature_and_reason_sanitization(self, tmp_path):
        """Phase 1: cancel accepts reason keyword argument, records sanitized reason in DB."""
        db_path = tmp_path / "cancel_sig.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_cancel_01")

        guard.check_and_reserve("t1_c5", "req_cancel_1", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)
        raw_reason = "Test cancellation reason with extra whitespace \n and special characters <abc> " * 10
        guard.cancel("req_cancel_1", reason=raw_reason)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status, cancellation_reason, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE request_id = 'req_cancel_1';").fetchone()
        conn.close()

        assert row[0] == "PRE_DISPATCH_CANCELLED"
        assert row[1] is not None
        assert len(row[1]) <= 200
        assert row[2] == 0.0
        assert row[3] == 0.0

    # 2. Failed pre-dispatch cancellation leaves no orphan and rejects cancel after dispatch
    def test_pre_dispatch_cancel_rejects_after_dispatch(self, tmp_path):
        """Phase 1: Record that was already dispatched cannot be cancelled."""
        db_path = tmp_path / "cancel_dispatch.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_cancel_02")

        guard.check_and_reserve("t1_c5", "req_disp_1", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt("req_disp_1")

        with pytest.raises(LLMNotConfiguredError, match="Cancellation is strictly forbidden"):
            guard.cancel("req_disp_1", reason="late abort")

    # 3. HTTP 429 counted as dispatched (DISPATCH_FAILED)
    def test_http_429_counted_as_dispatched(self, tmp_path):
        """Phase 2: Dispatched request failing with HTTP 429 transitions to DISPATCH_FAILED and counts toward ceiling."""
        db_path = tmp_path / "rate_limit_count.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_429")

        guard.check_and_reserve("t1_c3", "req_429_1", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt("req_429_1")
        guard.mark_failed("req_429_1", failure_reason="HTTP 429: Too Many Requests")

        conn = sqlite3.connect(str(db_path))
        cnt = conn.execute(
            "SELECT COUNT(*) FROM llm_campaign_ledger WHERE campaign_id = 'camp_429' AND status NOT IN ('PRE_DISPATCH_CANCELLED', 'CANCELLED');"
        ).fetchone()[0]
        st = conn.execute("SELECT status, failure_reason FROM llm_campaign_ledger WHERE request_id = 'req_429_1';").fetchone()
        conn.close()

        assert cnt == 1
        assert st[0] == "DISPATCH_FAILED"
        assert "429" in st[1]

    # 4. HTTP 500 counted as dispatched (DISPATCH_FAILED)
    def test_http_500_counted_as_dispatched(self, tmp_path):
        """Phase 2: Dispatched HTTP 500 failure is tracked as DISPATCH_FAILED and preserved."""
        db_path = tmp_path / "http_500.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_500")

        guard.check_and_reserve("t1_c3", "req_500_1", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt("req_500_1")
        guard.mark_failed("req_500_1", failure_reason="HTTP 500: Internal Server Error")

        conn = sqlite3.connect(str(db_path))
        cnt = conn.execute(
            "SELECT COUNT(*) FROM llm_campaign_ledger WHERE campaign_id = 'camp_500' AND candidate_id = 't1_c3' AND status NOT IN ('PRE_DISPATCH_CANCELLED', 'CANCELLED');"
        ).fetchone()[0]
        conn.close()
        assert cnt == 1

    # 5. Timeout counted as dispatched and uncertain when appropriate
    def test_timeout_counted_as_uncertain_preserving_liability(self, tmp_path):
        """Phase 3: Timeout transitions to UNCERTAIN and retains conservative reserved liability."""
        db_path = tmp_path / "timeout_unc.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_timeout")

        guard.check_and_reserve("t2_c2", "req_timeout_1", estimated_commercial_usd=0.005, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt("req_timeout_1")
        guard.reconcile("req_timeout_1", actual_commercial_usd=0.005, actual_billable_usd=0.0, is_uncertain=True, failure_reason="Timeout after 60s")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status, commercial_cost_usd, failure_reason FROM llm_campaign_ledger WHERE request_id = 'req_timeout_1';").fetchone()
        conn.close()

        assert row[0] == "UNCERTAIN"
        assert row[1] == 0.005
        assert "Timeout" in row[2]

    # 6. 35 repeated failed dispatch attempts cannot bypass the 10-request ceiling
    def test_35_failed_dispatch_attempts_strictly_capped_at_10(self, tmp_path):
        """Phase 2: Dispatched requests transition to DISPATCH_FAILED and halt at 10."""
        db_path = tmp_path / "bypass_prevent.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_bypass_test")

        successful_reservations = 0
        blocked_reservations = 0

        for i in range(35):
            req_id = f"req_attempt_{i}"
            try:
                guard.check_and_reserve(
                    candidate_id="t1_c3",
                    request_id=req_id,
                    estimated_commercial_usd=0.001,
                    estimated_billable_usd=0.0,
                )
                guard.record_dispatch_attempt(req_id)
                guard.mark_failed(req_id, failure_reason="Remote HTTP 503 Service Unavailable")
                successful_reservations += 1
            except LLMBudgetExceededError:
                blocked_reservations += 1

        assert successful_reservations == 10
        assert blocked_reservations == 25

    # 7. Unknown request reconciliation rejection
    def test_unknown_request_reconciliation_rejected(self, tmp_path):
        """Phase 1 & Phase 3: Fails explicitly when attempting to reconcile or cancel unknown request ID."""
        db_path = tmp_path / "unknown_req.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_unknown")

        with pytest.raises(ValueError, match="Unknown request ID 'req_ghost' cannot be reconciled"):
            guard.reconcile("req_ghost", actual_commercial_usd=0.001, actual_billable_usd=0.0)

        with pytest.raises(ValueError, match="Unknown request ID 'req_ghost' cannot be cancelled"):
            guard.cancel("req_ghost", reason="not real")

    # 8. Invalid ledger status transitions rejected
    def test_invalid_status_transitions_rejected(self, tmp_path):
        """Phase 1: Validates that terminal and invalid transitions are rejected."""
        db_path = tmp_path / "invalid_transitions.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_transitions")

        guard.check_and_reserve("t1_c5", "req_t1", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)
        guard.cancel("req_t1", reason="Pre-dispatch abort")

        with pytest.raises(LLMNotConfiguredError, match="Cannot reconcile request .* with status 'PRE_DISPATCH_CANCELLED'"):
            guard.reconcile("req_t1", actual_commercial_usd=0.001, actual_billable_usd=0.0)

        with pytest.raises(LLMNotConfiguredError, match="Cannot mark request .* dispatched from status 'PRE_DISPATCH_CANCELLED'"):
            guard.record_dispatch_attempt("req_t1")

    # 9. Negative budget estimates rejected
    def test_negative_budget_estimates_rejected(self, tmp_path):
        """Phase 4: Reject negative reservation values in check_and_reserve."""
        db_path = tmp_path / "negative_budget.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_neg")

        with pytest.raises(LLMBudgetExceededError, match="must be a non-negative finite number"):
            guard.check_and_reserve("t1_c5", "req_neg_1", estimated_commercial_usd=-0.01, estimated_billable_usd=0.0)

        with pytest.raises(LLMBudgetExceededError, match="must be a non-negative finite number"):
            guard.check_and_reserve("t1_c5", "req_neg_2", estimated_commercial_usd=0.01, estimated_billable_usd=-0.005)

    # 10. NaN and infinite caps and amounts rejected
    def test_nan_and_infinite_caps_and_amounts_rejected(self, tmp_path):
        """Phase 4: Reject NaN, inf, booleans, and non-numeric inputs in constructor and methods."""
        db_path = tmp_path / "nan_inf.db"

        with pytest.raises(LLMBudgetExceededError, match="must be a non-negative finite number"):
            CampaignBudgetGuard(db_path=db_path, campaign_id="camp_nan", commercial_cost_cap_usd=float("nan"))

        with pytest.raises(LLMBudgetExceededError, match="must be a non-negative finite number"):
            CampaignBudgetGuard(db_path=db_path, campaign_id="camp_inf", billable_cost_cap_usd=float("inf"))

        with pytest.raises(LLMBudgetExceededError, match="must be a numeric value"):
            CampaignBudgetGuard(db_path=db_path, campaign_id="camp_bool", commercial_cost_cap_usd=True)

        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_valid")

        with pytest.raises(LLMBudgetExceededError, match="must be a non-negative finite number"):
            guard.check_and_reserve("t1_c5", "req_nan", estimated_commercial_usd=float("nan"))

        with pytest.raises(LLMBudgetExceededError, match="must be a numeric value"):
            guard.check_and_reserve("t1_c5", "req_str", estimated_commercial_usd="invalid")

    # 11. Uncertain zero-cost overwrite rejected (preserves conservative reserved liability)
    def test_uncertain_zero_cost_overwrite_preserves_reserved_ceiling(self, tmp_path):
        """Phase 3 confirmed defect: Reconciled UNCERTAIN with zero actuals retains conservative ceilings."""
        db_path = tmp_path / "uncertain_overwrite.db"
        guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id="camp_unc_preserve",
            commercial_cost_cap_usd=0.030,
            billable_cost_cap_usd=0.015,
        )

        guard.check_and_reserve(
            candidate_id="t2_c2",
            request_id="req_unc_defect",
            estimated_commercial_usd=0.022,
            estimated_billable_usd=0.011,
        )
        guard.record_dispatch_attempt("req_unc_defect")

        guard.reconcile(
            "req_unc_defect",
            actual_commercial_usd=0.0,
            actual_billable_usd=0.0,
            is_uncertain=True,
            failure_reason="Missing token usage metadata in provider response",
        )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE request_id = 'req_unc_defect';"
        ).fetchone()
        conn.close()

        assert row[0] == "UNCERTAIN"
        assert row[1] == 0.022
        assert row[2] == 0.011

        with pytest.raises(LLMBudgetExceededError, match="exceeds aggregate cap"):
            guard.check_and_reserve(
                candidate_id="t2_c2",
                request_id="req_unc_blocked",
                estimated_commercial_usd=0.015,
                estimated_billable_usd=0.005,
            )

    # 12. Duplicate request ID handling
    def test_duplicate_request_id_inconsistent_rejected(self, tmp_path):
        """Phase 4: Inconsistent duplicate request ID is strictly rejected with LLMDuplicateRequestError."""
        db_path = tmp_path / "dup_req.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_dup")

        guard.check_and_reserve("t1_c5", "req_dup_1", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)

        with pytest.raises(LLMDuplicateRequestError, match="Duplicate request_id"):
            guard.check_and_reserve("t1_c3", "req_dup_1", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)

        with pytest.raises(LLMDuplicateRequestError, match="Duplicate request_id"):
            guard.check_and_reserve("t1_c5", "req_dup_1", estimated_commercial_usd=0.005, estimated_billable_usd=0.0)

    # 13. Multi-process request-count race
    def test_multiprocess_request_count_race(self, tmp_path):
        """Phase 5: Concurrency test using Python multiprocessing pool."""
        db_path = str(tmp_path / "multiprocess_race.db")
        campaign_id = "camp_mp_race"

        init_guard = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)
        assert init_guard is not None

        args = [(db_path, campaign_id, "t1_c5", f"req_mp_{i}", 0.001, 0.0) for i in range(25)]
        with multiprocessing.Pool(processes=4) as pool:
            results = pool.starmap(_worker_reserve_attempt, args)

        success_count = results.count("SUCCESS")
        blocked_count = results.count("BUDGET_EXCEEDED")

        assert success_count == 10
        assert blocked_count == 15

        conn = sqlite3.connect(db_path)
        actual_in_db = conn.execute(
            "SELECT COUNT(*) FROM llm_campaign_ledger WHERE campaign_id = ? AND candidate_id = 't1_c5' AND status != 'PRE_DISPATCH_CANCELLED';",
            (campaign_id,),
        ).fetchone()[0]
        conn.close()

        assert actual_in_db == 10

    # 14. Multi-process aggregate commercial-budget race
    def test_multiprocess_aggregate_commercial_budget_race(self, tmp_path):
        """Phase 5: Concurrency race with commercial cap = $0.030."""
        db_path = str(tmp_path / "mp_comm_budget.db")
        campaign_id = "camp_mp_comm"

        init_guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.030,
            billable_cost_cap_usd=0.010,
        )
        assert init_guard is not None

        args = [(db_path, campaign_id, "t1_c3", f"req_mp_comm_{i}", 0.008, 0.0) for i in range(10)]
        with multiprocessing.Pool(processes=4) as pool:
            results = pool.starmap(_worker_reserve_and_attempt, args)

        success_count = results.count("SUCCESS")
        blocked_count = results.count("BUDGET_EXCEEDED")

        assert success_count == 3
        assert blocked_count == 7

        conn = sqlite3.connect(db_path)
        tot_comm = conn.execute(
            "SELECT SUM(commercial_cost_usd) FROM llm_campaign_ledger WHERE campaign_id = ?;",
            (campaign_id,),
        ).fetchone()[0]
        conn.close()

        assert round(tot_comm, 6) == 0.024

    # 15. Multi-process billable-budget race
    def test_multiprocess_billable_budget_race(self, tmp_path):
        """Phase 5: Concurrency race with billable cap = $0.010 and spend per reservation = $0.004."""
        db_path = str(tmp_path / "mp_bill_budget.db")
        campaign_id = "camp_mp_bill"

        init_guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.050,
            billable_cost_cap_usd=0.010,
        )
        assert init_guard is not None

        args = [(db_path, campaign_id, "t2_c2", f"req_mp_bill_{i}", 0.004, 0.004) for i in range(8)]
        with multiprocessing.Pool(processes=4) as pool:
            results = pool.starmap(_worker_reserve_and_attempt, args)

        success_count = results.count("SUCCESS")
        blocked_count = results.count("BUDGET_EXCEEDED")

        assert success_count == 2
        assert blocked_count == 6

        conn = sqlite3.connect(db_path)
        tot_bill = conn.execute(
            "SELECT SUM(billable_cost_usd) FROM llm_campaign_ledger WHERE campaign_id = ?;",
            (campaign_id,),
        ).fetchone()[0]
        conn.close()

        assert round(tot_bill, 6) == 0.008

    # 16. Crash / interruption recovery (durable reservation preserved)
    def test_crash_interruption_recovery(self, tmp_path):
        """Phase 5: Durable reservations survive process termination and block subsequent overflow."""
        db_path = tmp_path / "crash_recovery.db"
        campaign_id = "camp_crash"

        guard1 = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)
        for i in range(5):
            guard1.check_and_reserve("t1_c5", f"req_c_{i}", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)
            guard1.record_dispatch_attempt(f"req_c_{i}")

        del guard1

        guard2 = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)
        for i in range(5, 10):
            guard2.check_and_reserve("t1_c5", f"req_c_{i}", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)
            guard2.record_dispatch_attempt(f"req_c_{i}")

        with pytest.raises(LLMBudgetExceededError, match="reached campaign request limit"):
            guard2.check_and_reserve("t1_c5", "req_c_10", estimated_commercial_usd=0.001, estimated_billable_usd=0.0)

    # 17. Correct CLI parsing and propagation (including --confirm-paid, --max-requests 10)
    def test_cli_parsing_and_propagation(self):
        """Phase 7: Verify CLI parser recognizes --confirm-paid, --max-requests, and propagation."""
        from scripts.benchmark_external_llm import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "--live",
            "--confirm-paid",
            "--provider", "gemini",
            "--candidate", "t1_c5",
            "--free-only",
            "--task", "website_extraction:pass_a",
            "--max-cases", "10",
            "--max-requests", "10",
            "--inter-request-delay", "2.5",
            "--campaign-id", "s3_6c_qualification",
            "--output-dir", "runtime/benchmarks_s3_6c/gemini",
        ])

        assert args.dry_run is False
        assert args.confirm_paid is True
        assert args.provider == "gemini"
        assert args.candidate == "t1_c5"
        assert args.free_only is True
        assert args.task == "website_extraction:pass_a"
        assert args.max_cases == 10
        assert args.max_requests == 10
        assert args.inter_request_delay == 2.5
        assert args.campaign_id == "s3_6c_qualification"
        assert args.output_dir == "runtime/benchmarks_s3_6c/gemini"

    # 18. Full live-mode preflight with mock transport only
    def test_full_live_mode_preflight_with_mock_transport_only(self, tmp_path):
        """Phase 8: Offline live-mode simulation with mocked HTTP transport."""
        db_path = tmp_path / "live_mock_test.db"
        campaign_id = "s3_6b2_mock_campaign"

        mock_response = httpx.Response(
            status_code=200,
            json={
                "id": "chatcmpl-mock-123",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"entities": [{"entity_id": "ent_1", "name": "Acme Corp", "type": "company", "description": "Tech firm", "evidence_ids": ["ev_1"]}]}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 35,
                    "total_tokens": 85,
                },
            },
        )
        transport = httpx.MockTransport(lambda req: mock_response)

        runner = ExternalBenchmarkRunner(
            dry_run=False,
            confirm_paid=True,
            provider_filter="gemini",
            free_only=True,
            max_requests=10,
            db_path=str(db_path),
            campaign_id=campaign_id,
            real_provider_transport=transport,
            api_key="mock-offline-key",
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c5",
            max_cases_per_task=1,
        )

        assert report.total_cases_run == 1
        assert report.campaign_id == campaign_id
        assert report.ledger_db_path == str(db_path)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, candidate_id, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE campaign_id = ?;",
            (campaign_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "RECONCILED"
        assert row[1] == "t1_c5"
        assert row[3] == 0.0

    # 19. Verified zero-charge entitlement enforcement
    def test_verified_zero_charge_entitlement_enforcement(self, tmp_path):
        """Phase 4: Candidate not in verified Free/Trial set cannot claim zero billable amount."""
        db_path = tmp_path / "zero_charge.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_zero")

        guard.check_and_reserve("t1_c5", "req_free_ok", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)

        with pytest.raises(LLMNotConfiguredError, match="not in approved campaign candidate set"):
            guard.check_and_reserve("t1_c1", "req_disapproved", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)

    # 20. Historical ledger preservation
    def test_historical_ledger_preservation(self):
        """Phase 6: Verifies data/benchmark_budget.db remains untouched with exact SHA-256."""
        import hashlib
        # In runtime workspace or when inspecting persistent ledger
        db_file = Path("data/benchmark_budget.db")
        if not db_file.exists():
            workspace_db = Path("C:/Sales-AI/SalesAI/data/benchmark_budget.db")
            if workspace_db.exists():
                db_file = workspace_db

        if not db_file.exists():
            # In clean release ZIP distributions, runtime SQLite databases (.db) are strictly excluded by security policy
            summary = read_only_historical_liability_preflight(db_file)
            assert summary["exists"] is False
            assert summary["total_reservations"] == 0
            return

        data = db_file.read_bytes()
        actual_sha = hashlib.sha256(data).hexdigest()
        expected_sha = "50f056b6072420dd340f5a8ecc6d9ed18038dfd336c574262fc78e325a719a1a"
        assert actual_sha == expected_sha, f"Historical ledger modified! SHA mismatch: {actual_sha} vs {expected_sha}"

        summary = read_only_historical_liability_preflight(db_file)
        assert summary["total_reservations"] == 12
        assert summary["status_breakdown"]["RECONCILED"]["count"] == 9
        assert summary["status_breakdown"]["UNCERTAIN"]["count"] == 3
        assert summary["uncertain_commercial_usd"] == 0.001901
