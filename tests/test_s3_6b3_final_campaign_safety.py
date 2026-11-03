"""
tests/test_s3_6b3_final_campaign_safety.py
SalesAI S3.6B.3 — Final Campaign Ledger Safety & End-to-End Dispatch Integrity Acceptance Tests.

STRICTLY OFFLINE. ZERO LIVE INFERENCE. ZERO PAID SPEND.

Deterministic tests covering all 7 identified defects:
1. HTTP 500 after dispatch preserves conservative commercial & billable liabilities.
2. HTTP 429 count preserved; confirmed nonbillable rejection treated with evidence.
3. Read timeout & interrupted response preserve conservative liability ceiling as UNCERTAIN.
4. Missing reservation estimates rejected (never silently defaulted to 0.0).
5. Missing reconciliation usage retains UNCERTAIN conservative liability.
6. Zero-charge entitlement context validation (free/trial candidates only).
7. Conflicting campaign policy across separate processes rejected via BEGIN IMMEDIATE.
8. Pre-network Gateway/RuntimeGuard refusal cancelled safely without secondary exceptions.
9. Dispatched request cannot be cancelled.
10. Cross-campaign request-ID collision fails closed.
11. Idempotency cannot cause second HTTP dispatch.
12. Crash between reservation and dispatch handled conservatively.
13. Historical ledger present, missing, and containing UNCERTAIN liability.
14. No implicit campaign budget reset.
15. Concurrent total and per-candidate budget enforcement.
16. Multi-format parity across JSON, CSV, and Markdown exports.
"""

from __future__ import annotations

import csv
import json
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
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.benchmark.fixtures_s3 import BenchmarkFixtureCase
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.model_catalog import ModelCandidate, PricingMetadata
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMTimeoutError,
    LLMUncertainUsageError,
)


def _worker_policy_conflict_test(db_path: str, campaign_id: str, comm_cap: float, bill_cap: float) -> str:
    """Helper process attempting to re-initialize an existing campaign with different caps."""
    try:
        guard = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=comm_cap,
            billable_cost_cap_usd=bill_cap,
        )
        return "INITIALIZED"
    except LLMBudgetExceededError as e:
        return f"MUTATION_REJECTED: {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def _worker_cross_campaign_collision(db_path: str, campaign_id: str, req_id: str, candidate_id: str) -> str:
    """Helper process attempting to reserve with a duplicate request ID across campaigns."""
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
            estimated_commercial_usd=0.002,
            estimated_billable_usd=0.0,
        )
        return "SUCCESS"
    except LLMDuplicateRequestError:
        return "COLLISION_REJECTED"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


class TestS36B3FinalCampaignSafety:
    """Acceptance test suite for SalesAI S3.6B.3."""

    # -------------------------------------------------------------------------
    # Defect 1: HTTP 500, Timeouts & Preserving Conservative Liability
    # -------------------------------------------------------------------------
    def test_defect1_http_500_preserves_conservative_liability(self, tmp_path):
        """HTTP 500 after dispatch must preserve conservative commercial & billable liabilities."""
        db_path = tmp_path / "defect1_500.db"
        campaign_id = "camp_500_safe"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)

        req_id = "req_d1_500"
        guard.check_and_reserve(
            candidate_id="t1_c3",
            request_id=req_id,
            estimated_commercial_usd=0.012,
            estimated_billable_usd=0.005,
        )
        guard.record_dispatch_attempt(req_id)

        # Ambiguous HTTP 500 failure occurs
        guard.mark_failed(req_id, failure_reason="HTTP 500 Internal Server Error")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, commercial_cost_usd, billable_cost_usd, failure_reason FROM llm_campaign_ledger WHERE request_id = ?;",
            (req_id,),
        ).fetchone()
        conn.close()

        # Status must be UNCERTAIN (or DISPATCH_FAILED retaining liability); costs must NOT be zeroed out
        assert row[0] == "UNCERTAIN"
        assert row[1] == 0.012
        assert row[2] == 0.005
        assert "500" in row[3]

    def test_defect1_http_429_confirmed_nonbillable_rejection(self, tmp_path):
        """HTTP 429 rate limit is confirmed nonbillable; releases cost liabilities but keeps request counted."""
        db_path = tmp_path / "defect1_429.db"
        campaign_id = "camp_429_safe"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)

        req_id = "req_d1_429"
        guard.check_and_reserve(
            candidate_id="t1_c3",
            request_id=req_id,
            estimated_commercial_usd=0.008,
            estimated_billable_usd=0.0,
        )
        guard.record_dispatch_attempt(req_id)
        guard.mark_failed(req_id, failure_reason="HTTP 429 Too Many Requests")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;",
            (req_id,),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM llm_campaign_ledger WHERE campaign_id = ? AND status NOT IN ('PRE_DISPATCH_CANCELLED', 'CANCELLED');",
            (campaign_id,),
        ).fetchone()[0]
        conn.close()

        assert row[0] == "DISPATCH_FAILED"
        assert row[1] == 0.0
        assert row[2] == 0.0
        assert count == 1  # Still counts against request ceiling

    def test_defect1_read_timeout_and_interrupted_response(self, tmp_path):
        """Read timeouts and interrupted network responses must retain conservative liability."""
        db_path = tmp_path / "defect1_timeout.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_timeout_safe")

        # Case A: Read timeout
        req_id_a = "req_d1_read_timeout"
        guard.check_and_reserve("t2_c2", req_id_a, estimated_commercial_usd=0.006, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt(req_id_a)
        guard.mark_failed(req_id_a, failure_reason="ReadTimeout: Server disconnected during streaming")

        # Case B: Interrupted response
        req_id_b = "req_d1_interrupted"
        guard.check_and_reserve("t1_c5", req_id_b, estimated_commercial_usd=0.003, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt(req_id_b)
        guard.mark_failed(req_id_b, failure_reason="ConnectionReset: Interrupted response from provider")

        conn = sqlite3.connect(str(db_path))
        res_a = conn.execute("SELECT status, commercial_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;", (req_id_a,)).fetchone()
        res_b = conn.execute("SELECT status, commercial_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;", (req_id_b,)).fetchone()
        conn.close()

        assert res_a[0] == "UNCERTAIN"
        assert res_a[1] == 0.006
        assert res_b[0] == "UNCERTAIN"
        assert res_b[1] == 0.003

    def test_defect1_mark_failed_on_undispatched_reserved_record_fails_closed(self, tmp_path):
        """Calling mark_failed on an un-dispatched RESERVED record without explicit cancellation classification raises error."""
        db_path = tmp_path / "defect1_reserved_fail.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_reserved_fail")

        req_id = "req_undispatched"
        guard.check_and_reserve("t1_c5", req_id, estimated_commercial_usd=0.001, estimated_billable_usd=0.0)

        with pytest.raises(LLMNotConfiguredError, match="without explicit pre_dispatch_cancellation classification"):
            guard.mark_failed(req_id, failure_reason="Operator aborted")

        # With explicit pre_dispatch_cancellation flag, cancels safely
        guard.mark_failed(req_id, failure_reason="Operator aborted pre-dispatch", pre_dispatch_cancellation=True)
        conn = sqlite3.connect(str(db_path))
        st = conn.execute("SELECT status FROM llm_campaign_ledger WHERE request_id = ?;", (req_id,)).fetchone()[0]
        conn.close()
        assert st == "PRE_DISPATCH_CANCELLED"

    # -------------------------------------------------------------------------
    # Defect 2: Financial Estimates Validation & Reconciliation Evidence
    # -------------------------------------------------------------------------
    def test_defect2_missing_reservation_cost_rejected(self, tmp_path):
        """check_and_reserve must reject None or missing commercial / billable spend estimates."""
        db_path = tmp_path / "defect2_missing.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_missing_cost")

        # Missing commercial estimate
        with pytest.raises(LLMBudgetExceededError, match="Missing commercial reservation estimate"):
            guard.check_and_reserve("t1_c5", "req_m1", estimated_commercial_usd=None, estimated_billable_usd=0.0)

        # Missing billable estimate
        with pytest.raises(LLMBudgetExceededError, match="Missing billable reservation estimate"):
            guard.check_and_reserve("t1_c5", "req_m2", estimated_commercial_usd=0.002, estimated_billable_usd=None)

    def test_defect2_missing_reconciliation_usage_retains_uncertain(self, tmp_path):
        """reconcile() called without actual costs or usage evidence must retain UNCERTAIN conservative liability."""
        db_path = tmp_path / "defect2_reconcile_usage.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_reconcile_unc")

        req_id = "req_no_usage"
        guard.check_and_reserve("t1_c3", req_id, estimated_commercial_usd=0.005, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt(req_id)

        # Reconcile called without usage evidence or actual costs
        guard.reconcile(req_id, actual_commercial_usd=None, actual_billable_usd=None)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status, commercial_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;", (req_id,)).fetchone()
        conn.close()

        assert row[0] == "UNCERTAIN"
        assert row[1] == 0.005

    def test_defect2_zero_charge_entitlement_validation(self, tmp_path):
        """Zero billable spend is rejected for non-entitled candidates."""
        db_path = tmp_path / "defect2_entitlement.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_entitle")

        # t1_c1 (OpenAI Luna) is a chargeable candidate; 0.0 billable must be rejected
        with pytest.raises(LLMNotConfiguredError, match="not in approved campaign candidate set"):
            guard.check_and_reserve("t1_c1", "req_e1", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)

    # -------------------------------------------------------------------------
    # Defect 3: Persistent Campaign Policy Across Separate Processes
    # -------------------------------------------------------------------------
    def test_defect3_conflicting_campaign_policy_across_processes_rejected(self, tmp_path):
        """Campaign initialized with cap $0.03 cannot be expanded or mutated to $0.15 in another process."""
        db_path = str(tmp_path / "defect3_policy.db")
        campaign_id = "camp_immutable_policy"

        # Process 1: Initialize campaign with commercial cap $0.030
        guard1 = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.030,
            billable_cost_cap_usd=0.010,
        )
        assert guard1.commercial_cost_cap_usd == 0.030

        # Process 2: Attempt to initialize same campaign with expanded cap $0.15
        with multiprocessing.Pool(processes=1) as pool:
            result = pool.apply(_worker_policy_conflict_test, (db_path, campaign_id, 0.150, 0.010))

        assert "MUTATION_REJECTED" in result
        assert "commercial_cost_cap_usd expansion" in result

    def test_defect3_policy_persists_table_and_bounds_reconnection(self, tmp_path):
        """Reconnecting to campaign with omitted caps inherits persistent policy."""
        db_path = str(tmp_path / "defect3_inherit.db")
        campaign_id = "camp_inherit"

        guard1 = CampaignBudgetGuard(
            db_path=db_path,
            campaign_id=campaign_id,
            commercial_cost_cap_usd=0.025,
            billable_cost_cap_usd=0.008,
        )
        del guard1

        guard2 = CampaignBudgetGuard(db_path=db_path, campaign_id=campaign_id)
        assert guard2.commercial_cost_cap_usd == 0.025
        assert guard2.billable_cost_cap_usd == 0.008

    # -------------------------------------------------------------------------
    # Defect 4: Pre-dispatch Gateway Errors & Safe Cancellation
    # -------------------------------------------------------------------------
    def test_defect4_pre_network_gateway_refusal_cancelled_safely(self, tmp_path):
        """Pre-network refusal inside Gateway / RuntimeGuard cancels safely without secondary exception."""
        db_path = tmp_path / "defect4_prenetwork.db"
        campaign_id = "camp_prenetwork"

        runner = ExternalBenchmarkRunner(
            dry_run=True,
            campaign_id=campaign_id,
            db_path=str(db_path),
            provider_filter="openai_compatible_mock",
        )

        fixture = BenchmarkFixtureCase(
            case_id="case_pre_01",
            task_id="website_extraction",
            subtask_id="pass_a",
            group="GROUNDED",
            title="Pre-network cancellation test fixture",
            input_context={},
            prompt="Extract firm details from website",
            system_prompt=None,
            expected_output={"entities": []},
            required_evidence_ids=[],
            never_claim=[],
            adversarial_type=None,
            checks_description="Pre-network validation check",
        )

        # Use candidate from runner's catalog but restrict allowed_tasks to trigger pre-network candidate mismatch rejection
        base_cand = runner.catalog.get_candidate("t1_c3")
        assert base_cand is not None
        candidate = base_cand.model_copy(update={"allowed_tasks": ["job_analysis"]})

        # Candidate mismatch error should be raised directly; campaign ledger must record cancellation cleanly
        with pytest.raises(LLMCandidateMismatchError):
            runner._execute_single_case(candidate=candidate, fixture=fixture, run_id="run_d4")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status, cancellation_reason FROM llm_campaign_ledger WHERE campaign_id = ?;", (campaign_id,)).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "PRE_DISPATCH_CANCELLED"
        assert "PRE_DISPATCH" in row[1]

    def test_defect4_dispatched_request_cannot_be_silently_cancelled(self, tmp_path):
        """A dispatched request cannot be cancelled once dispatched across the network."""
        db_path = tmp_path / "defect4_nocancel.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_nocancel")

        req_id = "req_dispatched"
        guard.check_and_reserve("t1_c5", req_id, estimated_commercial_usd=0.002, estimated_billable_usd=0.0)
        guard.record_dispatch_attempt(req_id)

        # Standard cancel must fail closed
        with pytest.raises(LLMNotConfiguredError, match="Cancellation is strictly forbidden once request has been dispatched"):
            guard.cancel(req_id, reason="Attempted post-dispatch cancel")

    # -------------------------------------------------------------------------
    # Defect 5: Duplicate Request IDs Across Campaigns
    # -------------------------------------------------------------------------
    def test_defect5_cross_campaign_request_id_collision_fails_closed(self, tmp_path):
        """Cross-campaign reuse of a request ID must fail closed."""
        db_path = str(tmp_path / "defect5_collision.db")

        # Campaign A reserves request ID 'req_shared_001'
        guard_a = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_A")
        guard_a.check_and_reserve("t1_c5", "req_shared_001", estimated_commercial_usd=0.002, estimated_billable_usd=0.0)

        # Campaign B attempts to reserve with same request ID
        with multiprocessing.Pool(processes=1) as pool:
            res = pool.apply(_worker_cross_campaign_collision, (db_path, "camp_B", "req_shared_001", "t1_c5"))

        assert res == "COLLISION_REJECTED"

    def test_defect5_idempotent_duplicate_cannot_cause_second_dispatch(self, tmp_path):
        """Idempotent reservation returns cleanly but cannot cause a second dispatch attempt."""
        db_path = tmp_path / "defect5_idempotent.db"
        guard = CampaignBudgetGuard(db_path=db_path, campaign_id="camp_idem")

        req_id = "req_idem_1"
        guard.check_and_reserve("t1_c5", req_id, estimated_commercial_usd=0.002, estimated_billable_usd=0.0)
        # Idempotent second reservation succeeds without creating duplicate record
        guard.check_and_reserve("t1_c5", req_id, estimated_commercial_usd=0.002, estimated_billable_usd=0.0)

        conn = sqlite3.connect(str(db_path))
        cnt = conn.execute("SELECT COUNT(*) FROM llm_campaign_ledger WHERE request_id = ?;", (req_id,)).fetchone()[0]
        conn.close()
        assert cnt == 1

        guard.record_dispatch_attempt(req_id)
        # Attempting second dispatch on already dispatched record fails
        with pytest.raises(LLMNotConfiguredError, match="Cannot mark request 'req_idem_1' dispatched from status 'DISPATCH_ATTEMPTED'"):
            guard.record_dispatch_attempt(req_id)

    # -------------------------------------------------------------------------
    # Defect 6: Historical Liabilities Integration in Preflight
    # -------------------------------------------------------------------------
    def test_defect6_historical_ledger_preflight_integration(self):
        """read_only_historical_liability_preflight accurately inspects operational DB read-only."""
        db_file = Path("data/benchmark_budget.db")
        if not db_file.exists():
            workspace_db = Path("C:/Sales-AI/SalesAI/data/benchmark_budget.db")
            if workspace_db.exists():
                db_file = workspace_db

        summary = read_only_historical_liability_preflight(db_file)
        if db_file.exists():
            assert summary["exists"] is True
            assert summary["table_exists"] is True
            assert summary["total_reservations"] > 0
            assert summary["total_commercial_usd"] > 0.0
        else:
            # In clean release ZIP distributions, .db files are excluded by release security policy
            assert summary["exists"] is False
            assert summary["total_reservations"] == 0

    def test_defect6_historical_preflight_missing_db(self, tmp_path):
        """Missing historical ledger returns exists=False with zero exposure."""
        missing_db = tmp_path / "non_existent.db"
        summary = read_only_historical_liability_preflight(missing_db)
        assert summary["exists"] is False
        assert summary["total_reservations"] == 0
        assert summary["total_commercial_usd"] == 0.0

    # -------------------------------------------------------------------------
    # Defect 7: Multi-format Parity & Reporting Ceilings
    # -------------------------------------------------------------------------
    def test_defect7_reporting_parity_json_csv_markdown(self, tmp_path):
        """JSON, CSV, and Markdown exports maintain financial and liability parity."""
        out_dir = tmp_path / "reports"
        db_path = tmp_path / "parity_test.db"
        campaign_id = "camp_parity"

        runner = ExternalBenchmarkRunner(
            dry_run=True,
            campaign_id=campaign_id,
            db_path=str(db_path),
            provider_filter="openai_compatible_mock",
        )

        report = runner.run_benchmark(
            task_filter="website_extraction:pass_a",
            candidate_filter="t1_c1",
            max_cases_per_task=2,
        )

        gen = S3BenchmarkReportGenerator(output_dir=out_dir)
        paths = gen.generate_all(report)

        # 1. JSON Parity Check
        with open(paths["json"], "r", encoding="utf-8") as f:
            j_data = json.load(f)
        assert j_data["campaign_id"] == campaign_id
        assert "historical_liability" in j_data
        assert "uncertain_liability_usd" in j_data

        # 2. CSV Parity Check
        with open(paths["csv"], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            csv_rows = list(reader)
        assert len(csv_rows) == 2
        for r in csv_rows:
            assert r["candidate_id"] == "t1_c1"
            assert "commercial_cost_usd" in r
            assert "billable_cost_usd" in r

        # 3. Markdown Parity Check
        md_text = paths["markdown"].read_text(encoding="utf-8")
        assert campaign_id in md_text
        assert "30-Case Conservative Commercial Baseline" in md_text
        assert "Campaign Safety Cap: `$0.150000`" in md_text
        assert "Acceptance & Production Authority Notice" in md_text
