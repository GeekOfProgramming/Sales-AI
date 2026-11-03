"""
sales_engine/benchmark/external_runner.py
SalesAI S3.0 — External LLM Benchmark Runner & Paid-Dispatch Safety Gates.
Evaluates external Tier 1 candidate models across the 7 canonical Gateway task/subtask paths
prior to modifying live operational call sites.

Guarantees:
1. Offline-first execution: default --dry-run mode uses deterministic mocked provider.
2. Paid-dispatch gate: live commercial API calls strictly disabled in S3.0.
3. Architecture reuse: wraps LLMProviderGateway, RuntimeGuard, BudgetLedger, and LLMOutputValidator.
4. Candidate identity & budget binding: enforces exact candidate matching and preflight budget accounting.
5. Sequential Tier 1 evaluation: tests candidate #1, stopping on qualification; moves to #2..#4 only if needed.
6. Reference answer isolation: gold labels / expected answers are held solely by the local scorer.
7. Segregated scorecards: distinct syntactic, semantic, safety, and operational failure metrics.
8. Safety precedence: hard safety disqualifiers override high syntactic scores.
9. Operator boundary: model recommendations are marked PROPOSED ONLY, never production approved.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import httpx

from sales_engine.llm_gateway.contracts import (
    LLMRequest,
    LLMResponse,
    ModelTier,
    sanitize_log_str,
)
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMCandidateMismatchError,
    LLMDuplicateRequestError,
    LLMNotConfiguredError,
    LLMRoutingIntegrityError,
    LLMUncertainUsageError,
    LLMTimeoutError,
    LLMQuotaExceededError,
    LLMAuthenticationError,
    LLMPermissionError,
    LLMRateLimitError,
)
from sales_engine.llm_gateway.interface import BaseLLMProvider, LLMProviderGateway
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
from sales_engine.llm_gateway.providers.cloudflare_provider import CloudflareProvider
from sales_engine.llm_gateway.model_catalog import (
    ModelCandidate,
    ModelCatalog,
    PricingMetadata,
    normalize_task_id,
    validate_subtask_id,
)
from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    ReservationStatus,
)
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.output_validation import (
    LLMOutputValidator,
    OutputValidationResult,
)
from sales_engine.llm_gateway.output_schemas import SchemaRegistry
from sales_engine.benchmark.fixtures_s3 import (
    BenchmarkFixtureCase,
    get_all_s3_fixtures,
    get_fixtures_for_task,
)
from sales_engine.benchmark.ground_truth_evaluator import (
    GroundTruthEvaluator,
    GroundTruthEvaluationResult,
    SCORER_VERSION,
)
from sales_engine.benchmark.provider_catalog import (
    OPENAI_SPEC,
    GEMINI_SPEC,
    GROQ_SPEC,
    XAI_SPEC,
    OPENROUTER_SPEC,
    COHERE_SPEC,
    NVIDIA_SPEC,
    CLOUDFLARE_SPEC,
    CANONICAL_SEVEN_PROVIDERS,
    ALL_SUPPORTED_PROVIDERS,
    PROVIDER_SPECS_BY_ID,
    get_provider_spec,
    get_candidate_spec,
    list_canonical_seven_providers,
    list_supported_providers,
    list_supported_candidates,
    classify_model_billing,
)

# S3.2 Verified Live Model Pricing for standard short-context gpt-6-luna (verified 2026-10-22)
TIER1_LIVE_LUNA_PRICING = OPENAI_SPEC.pricing


class CaseLifecycleStatus(str, Enum):
    """Explicit lifecycle status for benchmark case execution (S3.6B.1)."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    PRE_DISPATCH_BLOCKED = "PRE_DISPATCH_BLOCKED"
    DISPATCH_ATTEMPTED = "DISPATCH_ATTEMPTED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    EVALUATED = "EVALUATED"
    DISPATCH_FAILED = "DISPATCH_FAILED"


class CampaignLifecycleStatus(str, Enum):
    """Lifecycle states of a campaign ledger record (S3.6B.2)."""

    RESERVED = "RESERVED"
    PRE_DISPATCH_CANCELLED = "PRE_DISPATCH_CANCELLED"
    DISPATCH_ATTEMPTED = "DISPATCH_ATTEMPTED"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    UNCERTAIN = "UNCERTAIN"
    RECONCILED = "RECONCILED"


class CampaignBudgetGuard:
    """
    Fail-closed campaign-level budget and request guard covering multiple separate runs (S3.6B.1 & S3.6B.2).
    Enforces multi-process SQLite concurrency using BEGIN IMMEDIATE write transactions, strict candidate approval,
    request caps, commercial/billable spend ceilings, durable non-orphaned reservations, and preservation
    of uncertain financial liability.
    """

    APPROVED_CANDIDATES: Set[str] = {"t1_c5", "t1_c3", "t2_c2"}
    MAX_REQUESTS_PER_CANDIDATE: int = 10
    MAX_TOTAL_REQUESTS: int = 30
    DEFAULT_AGGREGATE_COMMERCIAL_CAP_USD: float = 0.15
    DEFAULT_AGGREGATE_BILLABLE_CAP_USD: float = 0.05

    def __init__(
        self,
        db_path: Union[str, Path],
        campaign_id: str,
        commercial_cost_cap_usd: Optional[float] = None,
        billable_cost_cap_usd: Optional[float] = None,
    ):
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: campaign_id must be a non-empty string.")
        self.campaign_id = campaign_id.strip()

        # Reject bools, non-numbers, NaN, inf, or negative caps
        if commercial_cost_cap_usd is not None:
            if isinstance(commercial_cost_cap_usd, bool) or not isinstance(commercial_cost_cap_usd, (int, float)):
                raise LLMBudgetExceededError("CAMPAIGN_GUARD_VIOLATION: commercial_cost_cap_usd must be a numeric value.")
            if math.isnan(commercial_cost_cap_usd) or math.isinf(commercial_cost_cap_usd) or commercial_cost_cap_usd < 0.0:
                raise LLMBudgetExceededError("CAMPAIGN_GUARD_VIOLATION: commercial_cost_cap_usd must be a non-negative finite number.")

        if billable_cost_cap_usd is not None:
            if isinstance(billable_cost_cap_usd, bool) or not isinstance(billable_cost_cap_usd, (int, float)):
                raise LLMBudgetExceededError("CAMPAIGN_GUARD_VIOLATION: billable_cost_cap_usd must be a numeric value.")
            if math.isnan(billable_cost_cap_usd) or math.isinf(billable_cost_cap_usd) or billable_cost_cap_usd < 0.0:
                raise LLMBudgetExceededError("CAMPAIGN_GUARD_VIOLATION: billable_cost_cap_usd must be a non-negative finite number.")

        self._init_commercial_cost_cap_usd = float(commercial_cost_cap_usd) if commercial_cost_cap_usd is not None else None
        self._init_billable_cost_cap_usd = float(billable_cost_cap_usd) if billable_cost_cap_usd is not None else None

        # Defaults for first-time creation if unspecified
        self.commercial_cost_cap_usd = self._init_commercial_cost_cap_usd if self._init_commercial_cost_cap_usd is not None else self.DEFAULT_AGGREGATE_COMMERCIAL_CAP_USD
        self.billable_cost_cap_usd = self._init_billable_cost_cap_usd if self._init_billable_cost_cap_usd is not None else self.DEFAULT_AGGREGATE_BILLABLE_CAP_USD

        self.db_path = Path(db_path)
        self._is_memory = str(self.db_path) == ":memory:"
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self._init_schema(self._mem_conn)
            self._init_and_verify_policy(self._mem_conn)
        else:
            if not self.db_path.parent.exists():
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._get_connection()
            try:
                self._init_schema(conn)
                self._init_and_verify_policy(conn)
            finally:
                conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory and self._mem_conn is not None:
            return self._mem_conn
        # autocommit mode (isolation_level=None) so BEGIN IMMEDIATE can be explicitly executed
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=15000;")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_campaign_policy (
                    campaign_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    commercial_cost_cap_usd REAL NOT NULL,
                    billable_cost_cap_usd REAL NOT NULL,
                    max_requests_per_candidate INTEGER NOT NULL,
                    max_total_requests INTEGER NOT NULL,
                    approved_candidates TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_campaign_ledger (
                    campaign_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    request_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    commercial_cost_usd REAL NOT NULL,
                    billable_cost_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    cancellation_reason TEXT,
                    failure_reason TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_campaign_candidate
                ON llm_campaign_ledger(campaign_id, candidate_id);
                """
            )
            # Additive migration if columns don't exist
            cursor = conn.execute("PRAGMA table_info(llm_campaign_ledger);")
            cols = {row[1] for row in cursor.fetchall()}
            if "updated_at" not in cols:
                conn.execute("ALTER TABLE llm_campaign_ledger ADD COLUMN updated_at TEXT;")
            if "cancellation_reason" not in cols:
                conn.execute("ALTER TABLE llm_campaign_ledger ADD COLUMN cancellation_reason TEXT;")
            if "failure_reason" not in cols:
                conn.execute("ALTER TABLE llm_campaign_ledger ADD COLUMN failure_reason TEXT;")
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def _init_and_verify_policy(self, conn: sqlite3.Connection) -> None:
        """
        Defect 3: Persistent Campaign Policy.
        Atomically records campaign policy upon first initialization.
        Fails closed with LLMBudgetExceededError if later initializations attempt
        to mutate policy (commercial cap, billable cap, candidate caps, approved candidate list).
        """
        approved_json = json.dumps(sorted(list(self.APPROVED_CANDIDATES)))
        policy_version = "s3.6b.3"
        now_iso = datetime.now(timezone.utc).isoformat()

        conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = conn.execute(
                """
                SELECT commercial_cost_cap_usd, billable_cost_cap_usd,
                       max_requests_per_candidate, max_total_requests, approved_candidates
                FROM llm_campaign_policy
                WHERE campaign_id = ?;
                """,
                (self.campaign_id,),
            )
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO llm_campaign_policy (
                        campaign_id, created_at, policy_version,
                        commercial_cost_cap_usd, billable_cost_cap_usd,
                        max_requests_per_candidate, max_total_requests,
                        approved_candidates
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        self.campaign_id,
                        now_iso,
                        policy_version,
                        self.commercial_cost_cap_usd,
                        self.billable_cost_cap_usd,
                        self.MAX_REQUESTS_PER_CANDIDATE,
                        self.MAX_TOTAL_REQUESTS,
                        approved_json,
                    ),
                )
            else:
                stored_comm, stored_bill, stored_req_cand, stored_req_tot, stored_cands = row
                stored_cands_set = set(json.loads(stored_cands)) if stored_cands else set()

                stored_comm_f = float(stored_comm)
                stored_bill_f = float(stored_bill)

                # Inherit stored caps if not explicitly supplied
                if self._init_commercial_cost_cap_usd is None:
                    self.commercial_cost_cap_usd = stored_comm_f
                if self._init_billable_cost_cap_usd is None:
                    self.billable_cost_cap_usd = stored_bill_f

                # Defect 3: Reject attempts to mutate policy:
                # 1. Never silently expand existing campaign permissions (cost ceiling expansion)
                # 2. Reject reconfiguring campaign using a different model list or candidate set
                mismatches = []
                if self.commercial_cost_cap_usd > stored_comm_f + 1e-9:
                    mismatches.append(f"commercial_cost_cap_usd expansion ({stored_comm_f} -> {self.commercial_cost_cap_usd})")
                if self.billable_cost_cap_usd > stored_bill_f + 1e-9:
                    mismatches.append(f"billable_cost_cap_usd expansion ({stored_bill_f} -> {self.billable_cost_cap_usd})")
                if stored_cands_set != self.APPROVED_CANDIDATES:
                    mismatches.append(f"approved_candidates ({sorted(stored_cands_set)} != {sorted(self.APPROVED_CANDIDATES)})")

                if mismatches:
                    raise LLMBudgetExceededError(
                        f"CAMPAIGN_POLICY_MUTATION_PROHIBITED: Cannot mutate immutable policy for campaign '{self.campaign_id}'. "
                        f"Conflicting parameters: {', '.join(mismatches)}. Fail closed."
                    )
                # Always bind to the canonical persistent campaign policy caps
                self.commercial_cost_cap_usd = min(self.commercial_cost_cap_usd, stored_comm_f)
                self.billable_cost_cap_usd = min(self.billable_cost_cap_usd, stored_bill_f)
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def validate_candidate_approved(self, candidate_id: str) -> None:
        """Fail closed if candidate is not in exact 3 approved candidates."""
        if candidate_id not in self.APPROVED_CANDIDATES:
            raise LLMNotConfiguredError(
                f"CAMPAIGN_GUARD_VIOLATION: Candidate '{candidate_id}' is not in approved campaign candidate set "
                f"({sorted(self.APPROVED_CANDIDATES)}). Refusing execution."
            )

    def _validate_spend_amount(self, val: Any, name: str) -> float:
        """Validate financial input values: reject booleans, non-numbers, NaN, inf, or negative amounts."""
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise LLMBudgetExceededError(f"CAMPAIGN_GUARD_VIOLATION: {name} must be a numeric value (got {val!r}).")
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val) or f_val < 0.0:
            raise LLMBudgetExceededError(f"CAMPAIGN_GUARD_VIOLATION: {name} must be a non-negative finite number (got {val!r}).")
        return f_val

    def check_and_reserve(
        self,
        candidate_id: str,
        request_id: str,
        run_id: str = "run_default",
        estimated_commercial_usd: Optional[float] = None,
        estimated_billable_usd: Optional[float] = None,
        est_commercial_usd: Optional[float] = None,
        est_billable_usd: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        """
        Atomically verify candidate request count, total request count, and spend ceilings
        under an SQLite BEGIN IMMEDIATE write transaction.
        Inserts a RESERVED entry into llm_campaign_ledger.
        Defect 2: Missing commercial or billable estimates are rejected (never silently defaulted to zero).
        Defect 5: Cross-campaign request ID collisions are rejected (fail-closed).
        """
        if not isinstance(request_id, str) or not request_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: request_id must be a non-empty string.")
        request_id = request_id.strip()

        self.validate_candidate_approved(candidate_id)

        raw_comm = estimated_commercial_usd if estimated_commercial_usd is not None else est_commercial_usd
        if raw_comm is None:
            raise LLMBudgetExceededError(
                f"CAMPAIGN_GUARD_VIOLATION: Missing commercial reservation estimate for candidate '{candidate_id}'. "
                "Explicit commercial estimate is required."
            )
        comm_spend = self._validate_spend_amount(raw_comm, "estimated_commercial_usd")

        raw_bill = estimated_billable_usd if estimated_billable_usd is not None else est_billable_usd
        if raw_bill is None:
            raise LLMBudgetExceededError(
                f"CAMPAIGN_GUARD_VIOLATION: Missing billable reservation estimate for candidate '{candidate_id}'. "
                "Explicit billable estimate is required."
            )
        bill_spend = self._validate_spend_amount(raw_bill, "estimated_billable_usd")

        # Zero billable spend is only permitted for candidates with verified Free or Trial entitlement
        if bill_spend == 0.0 and candidate_id not in ("t1_c5", "t1_c3", "t2_c2"):
            raise LLMBudgetExceededError(
                f"CAMPAIGN_GUARD_VIOLATION: Zero billable amount requires verified Free/Trial entitlement (candidate '{candidate_id}')."
            )

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    # Check duplicate request_id across entire ledger (Defect 5: Cross-campaign check)
                    cur = conn.execute(
                        "SELECT campaign_id, candidate_id, commercial_cost_usd, billable_cost_usd, status FROM llm_campaign_ledger WHERE request_id = ?;",
                        (request_id,),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        ex_camp, ex_cand, ex_comm, ex_bill, ex_status = existing
                        if ex_camp != self.campaign_id:
                            raise LLMDuplicateRequestError(
                                f"CAMPAIGN_GUARD_VIOLATION: Request ID '{request_id}' is already registered in campaign '{ex_camp}'. "
                                f"Cross-campaign request ID reuse is strictly forbidden."
                            )
                        if ex_cand != candidate_id or abs(ex_comm - comm_spend) > 1e-9 or abs(ex_bill - bill_spend) > 1e-9:
                            raise LLMDuplicateRequestError(
                                f"CAMPAIGN_GUARD_VIOLATION: Duplicate request_id '{request_id}' with inconsistent parameters."
                            )
                        # Identical duplicate already registered in this campaign
                        conn.execute("COMMIT;")
                        return

                    # 1. Check candidate request count (excluding only PRE_DISPATCH_CANCELLED)
                    cur = conn.execute(
                        """
                        SELECT COUNT(*) FROM llm_campaign_ledger
                        WHERE campaign_id = ? AND candidate_id = ?
                          AND status NOT IN ('PRE_DISPATCH_CANCELLED', 'CANCELLED');
                        """,
                        (self.campaign_id, candidate_id),
                    )
                    cand_count = cur.fetchone()[0]
                    if cand_count >= self.MAX_REQUESTS_PER_CANDIDATE:
                        raise LLMBudgetExceededError(
                            f"CAMPAIGN_GUARD_VIOLATION: Candidate '{candidate_id}' has reached campaign request limit "
                            f"({cand_count}/{self.MAX_REQUESTS_PER_CANDIDATE})."
                        )

                    # 2. Check total campaign request count
                    cur = conn.execute(
                        """
                        SELECT COUNT(*) FROM llm_campaign_ledger
                        WHERE campaign_id = ?
                          AND status NOT IN ('PRE_DISPATCH_CANCELLED', 'CANCELLED');
                        """,
                        (self.campaign_id,),
                    )
                    tot_count = cur.fetchone()[0]
                    if tot_count >= self.MAX_TOTAL_REQUESTS:
                        raise LLMBudgetExceededError(
                            f"CAMPAIGN_GUARD_VIOLATION: Campaign '{self.campaign_id}' has reached total request limit "
                            f"({tot_count}/{self.MAX_TOTAL_REQUESTS})."
                        )

                    # 3. Check spend ceilings (including RESERVED, DISPATCH_ATTEMPTED, DISPATCH_FAILED, RECONCILED, UNCERTAIN)
                    cur = conn.execute(
                        """
                        SELECT
                            COALESCE(SUM(commercial_cost_usd), 0.0),
                            COALESCE(SUM(billable_cost_usd), 0.0)
                        FROM llm_campaign_ledger
                        WHERE campaign_id = ?
                          AND status IN ('RESERVED', 'DISPATCH_ATTEMPTED', 'DISPATCH_FAILED', 'RECONCILED', 'UNCERTAIN');
                        """,
                        (self.campaign_id,),
                    )
                    row = cur.fetchone()
                    tot_comm = float(row[0])
                    tot_bill = float(row[1])

                    # Safe deterministic floating comparison with 1e-9 tolerance
                    if round(tot_comm + comm_spend, 8) > round(self.commercial_cost_cap_usd, 8):
                        raise LLMBudgetExceededError(
                            f"CAMPAIGN_GUARD_VIOLATION: Projected campaign commercial spend "
                            f"(${tot_comm + comm_spend:.6f}) exceeds aggregate cap "
                            f"(${self.commercial_cost_cap_usd:.6f})."
                        )

                    if round(tot_bill + bill_spend, 8) > round(self.billable_cost_cap_usd, 8):
                        raise LLMBudgetExceededError(
                            f"CAMPAIGN_GUARD_VIOLATION: Projected campaign billable spend "
                            f"(${tot_bill + bill_spend:.6f}) exceeds aggregate cap "
                            f"(${self.billable_cost_cap_usd:.6f})."
                        )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        INSERT INTO llm_campaign_ledger (
                            campaign_id, candidate_id, request_id, run_id, created_at,
                            updated_at, commercial_cost_usd, billable_cost_usd, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED');
                        """,
                        (
                            self.campaign_id,
                            candidate_id,
                            request_id,
                            run_id,
                            now_iso,
                            now_iso,
                            comm_spend,
                            bill_spend,
                        ),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                if not self._is_memory:
                    conn.close()

    def record_dispatch_attempt(self, request_id: str) -> None:
        """
        Transition record from RESERVED to DISPATCH_ATTEMPTED right before network submission.
        Once dispatched, this request counts against candidate limits.
        """
        if not isinstance(request_id, str) or not request_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: request_id must be a non-empty string.")
        request_id = request_id.strip()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        "SELECT status FROM llm_campaign_ledger WHERE request_id = ?;",
                        (request_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"CAMPAIGN_GUARD_VIOLATION: Unknown request ID '{request_id}' cannot be marked dispatched.")
                    status = row[0]
                    if status != "RESERVED":
                        raise LLMNotConfiguredError(
                            f"CAMPAIGN_GUARD_VIOLATION: Cannot mark request '{request_id}' dispatched from status '{status}'."
                        )
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        UPDATE llm_campaign_ledger
                        SET status = 'DISPATCH_ATTEMPTED', updated_at = ?
                        WHERE request_id = ?;
                        """,
                        (now_iso, request_id),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                if not self._is_memory:
                    conn.close()

    def mark_failed(
        self,
        request_id: str,
        failure_reason: str = "",
        is_ambiguous: Optional[bool] = None,
        pre_dispatch_cancellation: bool = False,
    ) -> None:
        """
        Mark a dispatched request as failed (e.g. HTTP 429, HTTP 500, auth errors, timeouts).
        Durable record is preserved and continues to count toward the candidate request ceiling.
        Defect 1:
        - Ambiguous failures (HTTP 500, read timeouts, interrupted responses, unknown usage)
          MUST preserve conservative reserved financial liability (never zero out).
        - If pre_dispatch_cancellation is True on a RESERVED request, cancels cleanly.
        - Calling mark_failed on an un-dispatched RESERVED record without pre_dispatch_cancellation
          is rejected.
        - Never silently erases prior UNCERTAIN liability.
        """
        if not isinstance(request_id, str) or not request_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: request_id must be a non-empty string.")
        request_id = request_id.strip()

        sanitized_reason = sanitize_log_str(str(failure_reason))[:200]
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        "SELECT status, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;",
                        (request_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"CAMPAIGN_GUARD_VIOLATION: Unknown request ID '{request_id}' cannot be marked failed.")
                    status, ex_comm, ex_bill = row

                    if status == "RESERVED":
                        if pre_dispatch_cancellation:
                            # Clean pre-dispatch cancellation
                            now_iso = datetime.now(timezone.utc).isoformat()
                            conn.execute(
                                """
                                UPDATE llm_campaign_ledger
                                SET status = 'PRE_DISPATCH_CANCELLED', cancellation_reason = ?,
                                    failure_reason = ?, commercial_cost_usd = 0.0, billable_cost_usd = 0.0,
                                    updated_at = ?
                                WHERE request_id = ?;
                                """,
                                (sanitized_reason, sanitized_reason, now_iso, request_id),
                            )
                            conn.execute("COMMIT;")
                            return
                        else:
                            raise LLMNotConfiguredError(
                                f"CAMPAIGN_GUARD_VIOLATION: Cannot mark un-dispatched RESERVED request '{request_id}' failed "
                                "without explicit pre_dispatch_cancellation classification."
                            )

                    if status not in ("DISPATCH_ATTEMPTED", "UNCERTAIN", "DISPATCH_FAILED"):
                        raise LLMNotConfiguredError(
                            f"CAMPAIGN_GUARD_VIOLATION: Cannot mark request '{request_id}' failed from status '{status}'."
                        )

                    # Determine ambiguity if not explicitly specified
                    if is_ambiguous is None:
                        lower_reason = sanitized_reason.lower()
                        # HTTP 500, read timeout, interrupted responses, unknown usage, network disconnects after dispatch
                        if any(k in lower_reason for k in ("500", "timeout", "interrupted", "read error", "unknown", "server error", "connection reset")):
                            ambiguous = True
                        elif "429" in lower_reason or "rate limit" in lower_reason:
                            # HTTP 429: provider rejected request before processing tokens; nonbillable
                            ambiguous = False
                        elif any(k in lower_reason for k in ("401", "403", "unauthorized", "forbidden")):
                            # Auth rejection: rejected before token processing; nonbillable
                            ambiguous = False
                        else:
                            # Fail closed: default to preserving conservative liability
                            ambiguous = True
                    else:
                        ambiguous = is_ambiguous

                    now_iso = datetime.now(timezone.utc).isoformat()
                    if status == "UNCERTAIN":
                        # Never silently erase prior UNCERTAIN liability
                        conn.execute(
                            """
                            UPDATE llm_campaign_ledger
                            SET failure_reason = ?, updated_at = ?
                            WHERE request_id = ?;
                            """,
                            (sanitized_reason, now_iso, request_id),
                        )
                    elif ambiguous:
                        # Ambiguous failure: preserve conservative reserved liability ceiling
                        conn.execute(
                            """
                            UPDATE llm_campaign_ledger
                            SET status = 'UNCERTAIN', failure_reason = ?, updated_at = ?
                            WHERE request_id = ?;
                            """,
                            (sanitized_reason, now_iso, request_id),
                        )
                    else:
                        # Confirmed non-billable rejection: release financial liabilities but maintain request count
                        conn.execute(
                            """
                            UPDATE llm_campaign_ledger
                            SET status = 'DISPATCH_FAILED', failure_reason = ?, updated_at = ?,
                                commercial_cost_usd = 0.0, billable_cost_usd = 0.0
                            WHERE request_id = ?;
                            """,
                            (sanitized_reason, now_iso, request_id),
                        )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                if not self._is_memory:
                    conn.close()

    def reconcile(
        self,
        request_id: str,
        final_commercial_usd: Optional[float] = None,
        final_billable_usd: Optional[float] = None,
        actual_commercial_usd: Optional[float] = None,
        actual_billable_usd: Optional[float] = None,
        is_uncertain: bool = False,
        failure_reason: Optional[str] = None,
        usage_evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update campaign reservation with reconciled spend or mark UNCERTAIN.
        Preserves conservative reserved financial liability on timeout and missing usage (never zeroing out).
        Defect 2: Reconciled records require verified actual usage evidence (or non-null actual costs).
        Where usage is unavailable, retains UNCERTAIN conservative liability.
        """
        if not isinstance(request_id, str) or not request_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: request_id must be a non-empty string.")
        request_id = request_id.strip()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        "SELECT status, commercial_cost_usd, billable_cost_usd FROM llm_campaign_ledger WHERE request_id = ?;",
                        (request_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"CAMPAIGN_GUARD_VIOLATION: Unknown request ID '{request_id}' cannot be reconciled.")
                    cur_status, ex_comm, ex_bill = row

                    # Reject reconciliation of pre-dispatch cancelled records
                    if cur_status in ("PRE_DISPATCH_CANCELLED", "CANCELLED"):
                        raise LLMNotConfiguredError(
                            f"CAMPAIGN_GUARD_VIOLATION: Cannot reconcile request '{request_id}' with status '{cur_status}'."
                        )

                    # Reconciling an existing UNCERTAIN record into RECONCILED without explicit evidence is forbidden
                    if cur_status == "UNCERTAIN" and not is_uncertain:
                        raise LLMNotConfiguredError(
                            f"CAMPAIGN_GUARD_VIOLATION: Silent transition from UNCERTAIN to RECONCILED for request '{request_id}' is forbidden."
                        )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    sanitized_failure = sanitize_log_str(str(failure_reason))[:200] if failure_reason else None

                    raw_c = actual_commercial_usd if actual_commercial_usd is not None else final_commercial_usd
                    raw_b = actual_billable_usd if actual_billable_usd is not None else final_billable_usd

                    # Defect 2: Check whether actual usage or reconciliation evidence is supplied
                    has_evidence = False
                    if usage_evidence is not None:
                        in_tok = usage_evidence.get("input_tokens")
                        out_tok = usage_evidence.get("output_tokens")
                        if in_tok is not None and out_tok is not None:
                            has_evidence = True
                    elif raw_c is not None:
                        has_evidence = True

                    if is_uncertain or not has_evidence:
                        # If usage is unavailable after dispatch, retain UNCERTAIN conservative liability
                        c_val = self._validate_spend_amount(raw_c, "uncertain_commercial_usd") if raw_c is not None else ex_comm
                        b_val = self._validate_spend_amount(raw_b, "uncertain_billable_usd") if raw_b is not None else ex_bill

                        final_comm = max(ex_comm, c_val) if c_val == 0.0 else max(ex_comm, c_val)
                        final_bill = max(ex_bill, b_val) if b_val == 0.0 else max(ex_bill, b_val)

                        conn.execute(
                            """
                            UPDATE llm_campaign_ledger
                            SET commercial_cost_usd = ?, billable_cost_usd = ?, status = 'UNCERTAIN',
                                failure_reason = ?, updated_at = ?
                            WHERE request_id = ?;
                            """,
                            (final_comm, final_bill, sanitized_failure, now_iso, request_id),
                        )
                    else:
                        c_val = self._validate_spend_amount(raw_c, "actual_commercial_usd")
                        b_val = self._validate_spend_amount(raw_b, "actual_billable_usd") if raw_b is not None else 0.0

                        conn.execute(
                            """
                            UPDATE llm_campaign_ledger
                            SET commercial_cost_usd = ?, billable_cost_usd = ?, status = 'RECONCILED',
                                failure_reason = ?, updated_at = ?
                            WHERE request_id = ?;
                            """,
                            (c_val, b_val, sanitized_failure, now_iso, request_id),
                        )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                if not self._is_memory:
                    conn.close()

    def cancel(self, request_id: str, reason: str = "", allow_pre_network: bool = False) -> None:
        """
        Cancel a campaign reservation prior to dispatch.
        Rejects cancellation if request was already dispatched across the network or is in terminal state.
        Defect 4: If allow_pre_network is True, permits cancellation of DISPATCH_ATTEMPTED records
        if failure occurred before HTTP transmission.
        Sets status to PRE_DISPATCH_CANCELLED and releases request ceiling and spend liability.
        """
        if not isinstance(request_id, str) or not request_id.strip():
            raise LLMNotConfiguredError("CAMPAIGN_GUARD_VIOLATION: request_id must be a non-empty string.")
        request_id = request_id.strip()

        sanitized_reason = sanitize_log_str(str(reason))[:200]
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        "SELECT status FROM llm_campaign_ledger WHERE request_id = ?;",
                        (request_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"CAMPAIGN_GUARD_VIOLATION: Unknown request ID '{request_id}' cannot be cancelled.")
                    cur_status = row[0]

                    permitted = (cur_status == "RESERVED") or (allow_pre_network and cur_status == "DISPATCH_ATTEMPTED")
                    if not permitted:
                        raise LLMNotConfiguredError(
                            f"CAMPAIGN_GUARD_VIOLATION: Cannot cancel request '{request_id}' in status '{cur_status}'. "
                            "Cancellation is strictly forbidden once request has been dispatched or resolved."
                        )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        UPDATE llm_campaign_ledger
                        SET status = 'PRE_DISPATCH_CANCELLED',
                            cancellation_reason = ?,
                            commercial_cost_usd = 0.0,
                            billable_cost_usd = 0.0,
                            updated_at = ?
                        WHERE request_id = ?;
                        """,
                        (sanitized_reason, now_iso, request_id),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                if not self._is_memory:
                    conn.close()


def read_only_historical_liability_preflight(db_path: Union[str, Path] = "data/benchmark_budget.db") -> Dict[str, Any]:
    """
    Read-only preflight query to analyze existing operational ledger records and historical financial exposure.
    Does not modify, clear, or insert records. Strictly read-only connection.
    """
    path = Path(db_path)
    if not path.exists():
        return {
            "db_path": str(path),
            "exists": False,
            "total_reservations": 0,
            "reconciled_count": 0,
            "uncertain_count": 0,
            "total_commercial_usd": 0.0,
            "total_billable_usd": 0.0,
            "uncertain_commercial_usd": 0.0,
            "uncertain_billable_usd": 0.0,
        }

    # Open read-only URI connection
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        cur = conn.cursor()
        # Verify llm_reservations exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='llm_reservations';")
        if not cur.fetchone():
            return {
                "db_path": str(path),
                "exists": True,
                "table_exists": False,
                "total_reservations": 0,
                "total_commercial_usd": 0.0,
                "total_billable_usd": 0.0,
            }

        cur.execute(
            """
            SELECT
                status,
                COUNT(*),
                COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0),
                COALESCE(SUM(COALESCE(billable_cost_usd, final_cost_usd, reserved_usd)), 0.0)
            FROM llm_reservations
            GROUP BY status;
            """
        )
        rows = cur.fetchall()
        status_map = {}
        total_res = 0
        total_comm = 0.0
        total_bill = 0.0
        unc_comm = 0.0
        unc_bill = 0.0

        for status, cnt, comm, bill in rows:
            status_map[status] = {"count": cnt, "commercial_usd": round(comm, 6), "billable_usd": round(bill, 6)}
            total_res += cnt
            total_comm += comm
            total_bill += bill
            if status == "UNCERTAIN":
                unc_comm += comm
                unc_bill += bill

        return {
            "db_path": str(path),
            "exists": True,
            "table_exists": True,
            "total_reservations": total_res,
            "status_breakdown": status_map,
            "total_commercial_usd": round(total_comm, 6),
            "total_billable_usd": round(total_bill, 6),
            "uncertain_commercial_usd": round(unc_comm, 6),
            "uncertain_billable_usd": round(unc_bill, 6),
        }
    finally:
        conn.close()


@dataclass
class BenchmarkCaseExecutionResult:
    """Individual test case execution result and diagnostics."""

    case_id: str
    task_id: str
    subtask_id: Optional[str]
    group: str  # GROUNDED or ADVERSARIAL
    candidate_id: str
    provider_id: str
    exact_model_id: str
    latency_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    syntactic_valid: bool = False
    semantic_valid: bool = False
    safety_valid: bool = False
    overall_pass: bool = False
    schema_pass: bool = False
    ground_truth_pass: bool = False
    evidence_pass: bool = False
    safety_pass: bool = False
    needs_review: bool = False
    failure_categories: List[str] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)
    grounding_evidence_found: List[str] = field(default_factory=list)
    forbidden_tokens_found: List[str] = field(default_factory=list)
    raw_output_snippet: str = ""
    validation_safe_log: Dict[str, Any] = field(default_factory=dict)
    sqlite_reservation_status: Optional[str] = None
    spend_provenance: Optional[str] = None
    estimated_spend_usd: Optional[float] = None
    commercial_cost_usd: Optional[float] = None
    billable_cost_usd: Optional[float] = None
    lifecycle_status: CaseLifecycleStatus = CaseLifecycleStatus.EVALUATED

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lifecycle_status"] = self.lifecycle_status.value
        return d


@dataclass
class CandidateTaskSummary:
    """Aggregated scorecard for a candidate on a specific task/subtask."""

    candidate_id: str
    task_id: str
    subtask_id: Optional[str]
    provider_id: str
    exact_model_id: str
    # Denominators and lifecycle counts (S3.6B Phase 2)
    cases_planned: int = 0
    requests_attempted: int = 0
    responses_received: int = 0
    cases_evaluable: int = 0
    cases_passed: int = 0
    transport_failures: int = 0
    rate_limit_failures: int = 0
    unattempted_cases: int = 0
    # Backward compatibility: cases_evaluated equals cases_evaluable
    cases_evaluated: int = 0
    # Rate metrics
    syntactic_pass_rate: float = 0.0
    semantic_pass_rate: float = 0.0
    safety_pass_rate: float = 0.0
    overall_pass_rate: float = 0.0
    schema_pass_rate: float = 0.0
    ground_truth_pass_rate: float = 0.0
    evidence_pass_rate: float = 0.0
    dispatch_success_rate: float = 0.0
    safety_violations_count: int = 0
    unsupported_claim_count: int = 0
    needs_review_count: int = 0
    # Latency and token accounting
    avg_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    commercial_cost_usd: float = 0.0
    billable_cost_usd: float = 0.0
    uncertain_cost_usd: float = 0.0
    # Qualification and approval hierarchy (S3.6B Phase 3)
    qualified: bool = False
    task_qualification_eligible: bool = False
    is_smoke_only: bool = False
    evaluation_state: str = "EVALUATED"
    production_approval_state: str = "NOT_APPROVED_PROPOSAL_ONLY"
    disqualification_reasons: List[str] = field(default_factory=list)
    case_results: List[BenchmarkCaseExecutionResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["case_results"] = [c.to_dict() for c in self.case_results]
        return d


@dataclass
class BenchmarkRunReport:
    """Complete summary of an external benchmark runner session."""

    run_id: str
    timestamp: str
    is_dry_run: bool
    tasks_evaluated: List[str]
    candidate_summaries: List[CandidateTaskSummary]
    recommended_candidates: Dict[str, Dict[str, Any]]
    total_cases_run: int = 0
    total_cost_usd: float = 0.0
    scorer_version: str = SCORER_VERSION
    fixture_catalog_version: str = "s3.0-catalog-v1"
    pricing_source_mode: str = "offline_mock"
    is_smoke_only: bool = False
    campaign_id: Optional[str] = None
    ledger_db_path: Optional[str] = None
    historical_liability: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        # Aggregate campaign & run metrics across candidate summaries
        uncertain_cost_tot = sum(getattr(s, "uncertain_cost_usd", 0.0) for s in self.candidate_summaries)
        transport_failures_tot = sum(getattr(s, "transport_failures", 0) for s in self.candidate_summaries)
        rate_limit_failures_tot = sum(getattr(s, "rate_limit_failures", 0) for s in self.candidate_summaries)

        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "is_dry_run": self.is_dry_run,
            "tasks_evaluated": self.tasks_evaluated,
            "candidate_summaries": [s.to_dict() for s in self.candidate_summaries],
            "recommended_candidates": self.recommended_candidates,
            "total_cases_run": self.total_cases_run,
            "total_cost_usd": self.total_cost_usd,
            "uncertain_liability_usd": round(uncertain_cost_tot, 6),
            "transport_failures_count": transport_failures_tot,
            "rate_limit_failures_count": rate_limit_failures_tot,
            "scorer_version": self.scorer_version,
            "fixture_catalog_version": self.fixture_catalog_version,
            "pricing_source_mode": self.pricing_source_mode,
            "is_smoke_only": self.is_smoke_only,
            "campaign_id": self.campaign_id,
            "ledger_db_path": self.ledger_db_path,
            "historical_liability": self.historical_liability,
        }


class MockBenchmarkProvider(BaseLLMProvider):
    """
    Deterministic offline mocked provider for S3.0 benchmark execution.
    Generates realistic responses without network activity.
    Simulates high quality for Tier 1 #1, and specific failure modes for adversarial fixtures.
    """

    def __init__(self, provider_id_val: str = "mock_provider"):
        self._provider_id = provider_id_val
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        model_id = request.model or "mock-model"

        # Check candidate behavior simulation
        # In a benchmark, mock responses are constructed to mirror actual task schemas
        # without referencing hidden gold labels in the prompt.
        content = self._generate_mock_content(request)

        input_toks = max(len(request.prompt) // 4, 25)
        output_toks = max(len(content) // 4, 30)

        return LLMResponse(
            content=content,
            provider=self._provider_id,
            model=model_id,
            task_id=request.task_id,
            request_id=request.request_id,
            input_tokens=input_toks,
            output_tokens=output_toks,
            latency_ms=120,
            finish_reason="stop",
        )

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        return self.generate(request)

    def _generate_mock_content(self, request: LLMRequest) -> str:
        task = request.task_id
        meta = request.metadata or {}
        subtask = meta.get("subtask_id")
        case_id = meta.get("case_id", "")
        candidate_id = meta.get("candidate_id", "t1_c1")

        # Deterministic mock data lookup for high-performing Tier 1 simulation
        mock_web_a = {
            "web_a_01": ("Milano MEP Ingegneria S.r.l.", ["Autodesk Revit", "Navisworks", "Solibri"]),
            "web_a_02": ("Torino Laser Scan S.r.l.", ["LiDAR", "Scan-to-BIM"]),
            "web_a_03": ("Studio Strutture Roma", ["Tekla Structures", "SAP2000"]),
            "web_a_04": ("Atelier BIM Bolzano", ["openBIM", "IFC"]),
            "web_a_05": ("Genova InfraBIM S.r.l.", ["Civil 3D"]),
            "web_a_06": ("Atlas Strutture S.r.l.", ["Tekla Structures"]),
            "web_a_07": ("Studio Edile Como", ["Autodesk Revit"]),
            "web_a_08": ("BIM Retail Software S.r.l.", ["Retail Software"]),
            "web_a_09": ("Studio Progetti Varese", ["CAD"]),
            "web_a_10": ("Ingegneria Navale Trieste", ["Rhino"]),
        }

        mock_inbox = {
            "inbox_01": "positive_interest",
            "inbox_02": "referral",
            "inbox_03": "question",
            "inbox_04": "not_interested",
            "inbox_05": "auto_reply",
            "inbox_06": "unsubscribe",
            "inbox_07": "unsubscribe",
            "inbox_08": "not_interested",
            "inbox_09": "unsubscribe",
            "inbox_10": "unknown",
        }

        mock_web_b = {
            "web_b_01": ["MEP clash resolution bottlenecks"],
            "web_b_02": ["Point cloud to Revit conversion workflow"],
            "web_b_03": ["Seismic calculation and structural modeling"],
            "web_b_04": ["IFC model validation and timber coordination"],
            "web_b_05": ["Linear infrastructure alignment coordination"],
            "web_b_06": ["Junior drafter"],
            "web_b_07": ["CAD coordinator"],
            "web_b_08": ["Revit drafter"],
            "web_b_09": ["Modeler"],
            "web_b_10": ["Site surveyor"],
        }

        # Handle specific simulated behaviors for testing failure modes:
        # If candidate is t1_c3 (a lower performing budget candidate), simulate occasional hallucination on adversarial cases
        if candidate_id == "t1_c3" and meta.get("group") == "ADVERSARIAL":
            if "web_a" in case_id:
                return json.dumps({
                    "company_name": "Studio Pi",
                    "company_summary": "Studio Pi is an architectural studio.",
                    "offerings": [{"name": "Architecture", "status": "active"}],
                    "target_industries": ["Architecture"],
                    "target_company_types": ["Private"],
                    "pain_points": ["Budget"],
                    "client_testimonial_roles": ["Architect"],
                    "internal_team_roles": ["Partner"],
                    "technologies": ["Revit", "ISO 9001 certified", "London office"],  # Hallucinated forbidden claims!
                    "evidence_refs": ["EV_01"],
                })

        # Canonical task mock generation:
        if task == "website_extraction":
            if subtask == "pass_a":
                ev_id = meta.get("evidence_id", "EV_01")
                co_name, techs = mock_web_a.get(case_id, ("Milano MEP Ingegneria S.r.l.", ["Autodesk Revit", "Navisworks"]))
                return json.dumps({
                    "company_name": co_name,
                    "company_summary": f"{co_name} is a specialized engineering and construction firm in Italy.",
                    "offerings": [{"name": "BIM coordination", "status": "active"}],
                    "target_industries": ["Architecture", "Engineering", "Construction"],
                    "target_company_types": ["General Contractors"],
                    "pain_points": ["Clash detection delay"],
                    "client_testimonial_roles": ["Project Manager"],
                    "internal_team_roles": ["Technical Director"],
                    "technologies": techs,
                    "evidence_refs": [ev_id] if ev_id else [],
                })
            elif subtask == "pass_b":
                ev_id = meta.get("evidence_id", "EV_01")
                signals = mock_web_b.get(case_id, ["Hiring Revit MEP modeler"])
                return json.dumps({
                    "buyer_roles": ["BIM Manager", "Technical Director"],
                    "primary_job_signals": signals,
                    "secondary_job_signals": ["Autodesk Revit", "Navisworks"],
                    "negative_signals": [],
                    "evidence_refs": [ev_id] if ev_id else [],
                })

        elif task == "job_analysis":
            ev_id = meta.get("evidence_id", "EV_J01")
            is_adv_acronym = (case_id == "job_06")
            techs = ["Word", "Excel"] if is_adv_acronym else ["Revit", "Navisworks"]
            reqs = ["General administrative management"] if is_adv_acronym else ["Lead clash detection meetings", "Verify IFC model integrity"]
            return json.dumps({
                "technologies": techs,
                "requirements": reqs,
                "seniority": "mid_senior",
                "remote_status": "hybrid",
                "relevant_signals": [
                    {"signal": "Administrative coordination" if is_adv_acronym else "BIM automation need", "evidence": "Coordination required"}
                ],
                "evidence_refs": [ev_id] if ev_id else [],
            })

        elif task == "discovery_query_generation":
            return json.dumps({
                "queries": [
                    {"query": "BIM Coordinator Milano Revit", "type": "keyword_signal", "priority": 1},
                    {"query": "MEP Modeling Ingegneria Lombardia", "type": "general_job", "priority": 2},
                    {"query": "Navisworks clash detection specialist", "type": "ats_targeted", "priority": 3},
                ],
            })

        elif task == "inbox_classification":
            is_opt_out = meta.get("has_opt_out", False)
            inbox_cat = "unsubscribe" if is_opt_out else mock_inbox.get(case_id, "positive_interest")
            return json.dumps({
                "category": inbox_cat,
                "confidence": 0.95,
                "reasoning": "Recipient expressed unsubscribe intent" if is_opt_out else "Factual inquiry regarding services.",
                "suggested_next_action": "remove_from_list" if is_opt_out else "send_case_study",
            })

        elif task == "reply_suggestion":
            ev_id = meta.get("evidence_id", "EV_R01")
            return json.dumps({
                "subject": "Re: pyBIM Automation Demo",
                "body": (
                    "Gentile Arch. Rossi, grazie per il riscontro. Abbiamo rimosso il Suo indirizzo da future comunicazioni."
                    if meta.get("has_opt_out")
                    else "Gentile Arch. Rossi, grazie per il riscontro. Saremmo lieti di mostrarLe come pyBIM automatizza le verifiche IFC."
                ),
                "classification": "unsubscribe" if meta.get("has_opt_out") else "positive_interest",
                "evidence_refs": [ev_id] if ev_id else [],
            })

        elif task == "outreach_draft":
            ev_id = meta.get("evidence_id", "EV_O01")
            return json.dumps({
                "subject": "Ottimizzazione processi BIM",
                "body": "Gentile Direttore Tecnico, abbiamo notato la Vostra leadership nel settore AEC in Lombardia. pyBIM supporta gli studi di ingegneria riducendo i tempi di revisione dei modelli Revit.",
                "service_used": "pyBIM Clash Automation",
                "personalization_notes": "Targeted MEP engineering consultancy",
                "evidence_refs": [ev_id] if ev_id else [],
                "cta": "Sarebbe disponibile per uno scambio di 10 minuti?",
            })

        return json.dumps({"status": "ok"})


class ExternalBenchmarkRunner:
    """
    Main benchmark execution runner for external LLM evaluation in SalesAI S3.0 / S3.2.
    Executes deterministic benchmark runs, records structured scorecards, and computes
    candidate recommendation proposals.
    """

    def __init__(
        self,
        dry_run: bool = True,
        confirm_paid: bool = False,
        max_requests: int = 1,
        max_cost_usd: float = 0.10,
        catalog: Optional[ModelCatalog] = None,
        budget_policy: Optional[BudgetPolicyConfig] = None,
        db_path: Optional[str] = None,
        qualification_threshold: float = 0.8,
        stop_on_first_qualifying: bool = True,
        api_key: Optional[str] = None,
        real_provider_transport: Optional[httpx.BaseTransport] = None,
        provider_filter: Optional[str] = None,
        free_only: bool = False,
        allow_xai_generation: bool = False,
        is_connectivity_smoke: bool = False,
        require_cohere_trial: bool = False,
        cohere_trial_timeout_seconds: Optional[float] = None,
        inter_request_delay_seconds: float = 0.0,
        sleeper: Optional[Callable[[float], None]] = None,
        campaign_id: Optional[str] = None,
        campaign_commercial_cap_usd: float = 0.15,
        campaign_billable_cap_usd: float = 0.05,
    ):
        self.dry_run = dry_run
        self.confirm_paid = confirm_paid
        self.qualification_threshold = qualification_threshold
        self.stop_on_first_qualifying = stop_on_first_qualifying
        self._api_key = api_key
        self._real_provider_transport = real_provider_transport
        self._attempted_requests_count = 0
        self.provider_filter = provider_filter.strip().lower() if provider_filter else None
        self.free_only = free_only
        self.allow_xai_generation = allow_xai_generation
        self.is_connectivity_smoke = is_connectivity_smoke
        self.require_cohere_trial = require_cohere_trial
        self.cohere_trial_timeout_seconds = cohere_trial_timeout_seconds
        self.campaign_id = campaign_id.strip() if campaign_id and campaign_id.strip() else None

        # Pacing validation (S3.6B Phase 4)
        if (
            not isinstance(inter_request_delay_seconds, (int, float))
            or isinstance(inter_request_delay_seconds, bool)
            or math.isnan(inter_request_delay_seconds)
            or math.isinf(inter_request_delay_seconds)
            or inter_request_delay_seconds < 0.0
            or inter_request_delay_seconds > 10.0
        ):
            raise ValueError(
                f"inter_request_delay_seconds must be a non-negative finite number <= 10.0 (got {inter_request_delay_seconds!r})"
            )
        self.inter_request_delay_seconds = float(inter_request_delay_seconds)
        self._sleeper = sleeper or time.sleep

        if self.cohere_trial_timeout_seconds is not None:
            if (
                not isinstance(self.cohere_trial_timeout_seconds, (int, float))
                or isinstance(self.cohere_trial_timeout_seconds, bool)
                or math.isnan(self.cohere_trial_timeout_seconds)
                or math.isinf(self.cohere_trial_timeout_seconds)
                or self.cohere_trial_timeout_seconds <= 0.0
                or self.cohere_trial_timeout_seconds > 60.0
            ):
                raise LLMNotConfiguredError(
                    f"COHERE_TIMEOUT_GUARD_VIOLATION: --cohere-trial-timeout-seconds must be a positive finite number <= 60.0 (got {cohere_trial_timeout_seconds!r})."
                )
            if not self.require_cohere_trial:
                raise LLMNotConfiguredError(
                    "COHERE_TIMEOUT_GUARD_VIOLATION: --cohere-trial-timeout-seconds requires --require-cohere-trial. Execution refused."
                )
            if self.provider_filter and self.provider_filter != "cohere":
                raise LLMNotConfiguredError(
                    f"COHERE_TIMEOUT_GUARD_VIOLATION: --cohere-trial-timeout-seconds is strictly applicable only to provider 'cohere' (got '{self.provider_filter}'). Execution refused."
                )

        if self.require_cohere_trial:
            if self.provider_filter and self.provider_filter != "cohere":
                raise LLMNotConfiguredError(
                    f"COHERE_TRIAL_GUARD_VIOLATION: --require-cohere-trial is strictly applicable only to "
                    f"provider 'cohere' (got provider='{self.provider_filter}'). Execution refused. Fail closed."
                )

        # LIVE-02: Strict validation of numeric caps across all modes
        if not isinstance(max_requests, int) or isinstance(max_requests, bool) or max_requests <= 0:
            raise LLMBudgetExceededError(
                f"Live benchmark request cap must be a positive integer >= 1 (got {max_requests!r})."
            )
        if max_requests > 20:
            raise LLMBudgetExceededError(
                f"Live benchmark request cap {max_requests} exceeds safety ceiling (max 20 requests per run)."
            )
        self.max_requests = max_requests

        if (
            not isinstance(max_cost_usd, (int, float))
            or isinstance(max_cost_usd, bool)
            or math.isnan(max_cost_usd)
            or math.isinf(max_cost_usd)
            or max_cost_usd <= 0.0
        ):
            raise LLMBudgetExceededError(
                f"Live benchmark cost ceiling must be a positive finite number > 0 (got {max_cost_usd!r})."
            )
        if max_cost_usd > 1.00:
            raise LLMBudgetExceededError(
                f"Live benchmark cost ceiling ${max_cost_usd:.2f} exceeds safety ceiling (max $1.00 per run)."
            )
        self.max_cost_usd = float(max_cost_usd)

        # LIVE-03 & Requirement 7: Prohibit in-memory SQLite in live mode
        if not dry_run:
            if not confirm_paid:
                raise LLMNotConfiguredError(
                    "Live commercial API calls are disabled in S3.0 and S3.1 by default. "
                    "Two-key execution gate: --live mode requires explicit --confirm-paid confirmation. "
                    "Commercial API calls refused."
                )
            if db_path is not None and str(db_path) == ":memory:":
                raise LLMNotConfiguredError(
                    "Live benchmark requires a persistent local SQLite ledger. ':memory:' is prohibited in live mode."
                )
            self.db_path = str(db_path) if db_path else "data/benchmark_budget.db"
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self.db_path = str(db_path) if db_path else ":memory:"

        # Defect 6: Wire read_only_historical_liability_preflight into live preflight
        # The canonical historical operational ledger is data/benchmark_budget.db
        hist_db_target = "data/benchmark_budget.db"
        self.historical_preflight_summary = read_only_historical_liability_preflight(hist_db_target)
        if not dry_run and self.db_path == "data/benchmark_budget.db":
            # If live run targets the historical operational ledger, fail closed if it does not exist
            if not self.historical_preflight_summary.get("exists", False):
                raise LLMNotConfiguredError(
                    f"HISTORICAL_PREFLIGHT_FAILURE: Historical operational database '{hist_db_target}' "
                    "does not exist or cannot be accessed read-only. Fail closed."
                )

        # S3.6B.1 Campaign Guard Setup
        self.campaign_guard: Optional[CampaignBudgetGuard] = None
        if self.campaign_id:
            self.campaign_guard = CampaignBudgetGuard(
                db_path=self.db_path,
                campaign_id=self.campaign_id,
                commercial_cost_cap_usd=campaign_commercial_cap_usd,
                billable_cost_cap_usd=campaign_billable_cap_usd,
            )

        # 1. Model Catalog setup
        self.catalog = catalog or ModelCatalog.create_default_16_slot_catalog()

        # 2. Budget Ledger & Runtime Guards (per-request ceiling bound by max_cost_usd)
        eff_max_request = min(self.max_cost_usd, 0.05 if dry_run else self.max_cost_usd)
        self.budget_policy = budget_policy or BudgetPolicyConfig(
            enabled=True,
            max_request_spend_usd=eff_max_request,
            daily_limit_usd=1.00,
            monthly_limit_usd=15.00,
            max_concurrent_requests=1,
            cooldown_seconds=0.0,
        )
        self.ledger = BudgetLedger(db_path=self.db_path, config=self.budget_policy)
        self.runtime_guard = RuntimeGuard(
            ledger=self.ledger,
            config=self.budget_policy,
        )

        # 3. Provider & Gateway setup
        self.registry = ProviderRegistry()
        if self.dry_run:
            self._initialize_mock_tier_1_candidates()
            self.mock_provider = MockBenchmarkProvider(provider_id_val="openai_compatible_mock")
            self.registry.register(self.mock_provider)
            self.gateway_config = GatewayConfig(
                enabled=True,
                default_provider="openai_compatible_mock",
            )
        else:
            # LIVE-01: In live mode, candidate slots remain disabled until explicitly selected
            # Do NOT register mock provider or mock candidates
            self.gateway_config = GatewayConfig(
                enabled=True,
                default_provider="openai",
            )

        self.gateway = LLMProviderGateway(
            registry=self.registry,
            config=self.gateway_config,
            runtime_guard=self.runtime_guard,
        )
        self.validator = LLMOutputValidator()

    def _initialize_mock_tier_1_candidates(self) -> None:
        """
        Configure neutral Tier 1 candidate slots with mock pricing
        suitable for offline dry-run benchmark evaluation ONLY.
        """
        tier_1_specs = [
            ("t1_c1", "openai_compatible_mock", "tier1-economy-mini", 0.15, 0.60),
            ("t1_c2", "openai_compatible_mock", "tier1-fast-haiku", 0.25, 1.25),
            ("t1_c3", "openai_compatible_mock", "tier1-rapid-flash", 0.075, 0.30),
            ("t1_c4", "openai_compatible_mock", "tier1-lean-small", 0.20, 0.60),
            ("t1_c5", "openai_compatible_mock", "gemini-3.5-flash-lite", 0.30, 2.50),
            ("t2_c1", "openai_compatible_mock", "meta-llama/llama-3.3-70b-instruct:free", 0.0, 0.0),
            ("t2_c2", "openai_compatible_mock", "command-r-08-2024", 0.15, 0.60),
            ("t2_c5", "openai_compatible_mock", "google/gemma-4-26b-a4b-it:free", 0.0, 0.0),
        ]

        for cid, provider_id, model_id, in_price, out_price in tier_1_specs:
            if not self.catalog.get_candidate(cid):
                priority = int(cid.split("_c")[1]) if "_c" in cid else 5
                tier = ModelTier.TIER_1_BUDGET if cid.startswith("t1_") else ModelTier.TIER_2_BALANCED
                self.catalog.register_candidate(
                    ModelCandidate(
                        candidate_id=cid,
                        tier=tier,
                        priority=priority,
                        provider_id=provider_id,
                        exact_model_id=model_id,
                        enabled=False,
                        provider_adapter_kind="openai_compatible",
                        allowed_tasks=["*"],
                        verified_capabilities={},
                        pricing=None,
                        verification_notes=None,
                    )
                )
            pricing = PricingMetadata(
                currency="FREE" if in_price == 0.0 and out_price == 0.0 else "USD",
                input_per_1m=in_price,
                output_per_1m=out_price,
                effective_date="2026-10-24",
                source_url="https://pricing.mock.internal/benchmark",
            )
            self.catalog.enable_candidate(
                candidate_id=cid,
                provider_id=provider_id,
                exact_model_id=model_id,
                verified_capabilities={
                    "json_mode": True,
                    "strict_schema": True,
                    "supports_json_schema": True,
                    "supports_json_mode": True,
                },
                pricing=pricing,
                verification_notes="Verified candidate for benchmarking",
                allowed_tasks=["*"],
            )

    def _configure_live_candidate(
        self,
        candidate_id: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> None:
        """
        Configure one explicitly selected verified Tier 1 or Tier 2 candidate for live smoke execution.
        Supports OpenAI (t1_c1), Google Gemini (t1_c2, t1_c5), GroqCloud (t1_c3), xAI Grok (t1_c4),
        and Tier 2 providers (openrouter, cohere, nvidia, cloudflare).
        Enforces candidate/provider identity, free-only gates, xAI generation lock,
        and leaves all other slots disabled.
        """
        # 1. Resolve ProviderConnectionSpec
        spec = None
        target_provider = provider_id or self.provider_filter
        target_candidate = candidate_id

        if target_provider:
            pid = target_provider.strip().lower()
            if pid not in PROVIDER_SPECS_BY_ID:
                raise LLMNotConfiguredError(
                    f"Provider '{target_provider}' is not verified in S3.3/S3.5. Supported providers: {list_supported_providers(all_providers=True)}"
                )
            if target_candidate:
                spec = get_provider_spec(target_provider, candidate_id=target_candidate)
                if not spec:
                    raise LLMNotConfiguredError(
                        f"Candidate '{target_candidate}' does not match provider '{target_provider}'."
                    )
            else:
                spec = get_provider_spec(target_provider)
        elif target_candidate:
            spec = get_candidate_spec(target_candidate)
            if not spec:
                raise LLMNotConfiguredError(
                    f"Candidate '{target_candidate}' is not verified in S3.3/S3.5. Supported candidates: {list_supported_candidates(all_candidates=True)}"
                )
        else:
            # Default to verified baseline candidate t1_c1 (OpenAI)
            spec = OPENAI_SPEC

        # 2. Free-Only Gate Enforcement (Hardened S3.5.3A)
        # Dynamic check enforces explicit model verification, not just provider-level spec
        dynamic_billing = classify_model_billing(spec.provider_id, spec.exact_model_id)
        if self.free_only:
            if spec.billing_mode != "VERIFIED_FREE_TIER" or dynamic_billing != "VERIFIED_FREE_TIER":
                raise LLMNotConfiguredError(
                    f"FREE_ELIGIBILITY_UNCONFIRMED: Provider '{spec.provider_id}' / Model '{spec.exact_model_id}' "
                    f"is classified as '{dynamic_billing}' (spec='{spec.billing_mode}'), not 'VERIFIED_FREE_TIER'. "
                    "Only explicit verified combinations qualify. Free-only execution refused (no paid fallback)."
                )

        if self.free_only and spec.provider_id == "openrouter":
            if not spec.exact_model_id.endswith(":free"):
                raise LLMNotConfiguredError(
                    f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{spec.exact_model_id}' on OpenRouter does not carry the :free tag. "
                    "Free-only execution refused (no paid fallback)."
                )
            if spec.pricing and (spec.pricing.input_per_1m != 0.0 or spec.pricing.output_per_1m != 0.0):
                raise LLMNotConfiguredError(
                    f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{spec.exact_model_id}' on OpenRouter is not zero-cost. "
                    "Free-only execution refused."
                )

        # 3. xAI Grok Generation Lock Enforcement
        if spec.provider_id == "xai" and not spec.is_generation_enabled and not self.allow_xai_generation:
            raise LLMNotConfiguredError(
                f"XAI_GENERATION_DISABLED: Generation with xAI Grok ({spec.exact_model_id}) is disabled by default "
                "until explicit operator authorization (--allow-xai-paid)."
            )

        # 4. Cohere Trial-Only Guard Enforcement (Hardened S3.5.4B)
        if self.require_cohere_trial:
            if spec.provider_id != "cohere" or spec.candidate_id != "t2_c2":
                raise LLMNotConfiguredError(
                    f"COHERE_TRIAL_GUARD_VIOLATION: --require-cohere-trial is strictly applicable only to "
                    f"Cohere candidate 't2_c2' (got provider='{spec.provider_id}', candidate='{spec.candidate_id}'). "
                    "Execution refused. Fail closed."
                )

        # 4. Generation Enabled Gate (Hardened S3.5.3B / S3.5.4A)
        # Prevents live dispatch of historical/ineligible candidates (e.g. t2_c1),
        # even when custom or mock HTTP transport is injected.
        is_xai_authorized = bool(spec.provider_id == "xai" and self.allow_xai_generation)
        if not spec.is_generation_enabled and not is_xai_authorized and not self.dry_run:
            msg = f"GENERATION_DISABLED: Candidate '{spec.candidate_id}' on '{spec.provider_id}' is disabled for live generation."
            if spec.provider_id == "cloudflare" and not target_candidate:
                msg += " Provider 'cloudflare' has multiple candidates; explicit candidate selection is required."
            raise LLMNotConfiguredError(msg)

        # 4. Environment Key Resolution (fail-closed, no cross-vendor fallback)
        api_key = os.environ.get(spec.env_var)
        if not api_key and self._api_key and spec.provider_id == "openai":
            api_key = self._api_key

        if not api_key and not self._real_provider_transport:
            raise LLMNotConfiguredError(
                f"Missing {spec.env_var} in environment for live benchmark execution with provider '{spec.provider_id}'."
            )

        # 5. Register real provider instance with approved endpoints and capabilities
        if spec.provider_id == "cohere":
            self.real_provider = CohereProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key or f"sk-{spec.provider_id}-live-smoke-test-key",
                transport=self._real_provider_transport,
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
            )
        elif spec.provider_id == "cloudflare":
            self.real_provider = CloudflareProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key or f"sk-{spec.provider_id}-live-smoke-test-key",
                transport=self._real_provider_transport,
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
            )
        else:
            extra_headers = spec.capabilities.get("extra_headers")
            self.real_provider = OpenAIProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key or f"sk-{spec.provider_id}-live-smoke-test-key",
                transport=self._real_provider_transport,
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
                extra_headers=extra_headers,
            )
        self.registry.register(self.real_provider)
        self.gateway_config.default_provider = spec.provider_id

        # Live Preflight Verification for OpenRouter Free Tier (Hardened S3.5.4A)
        # Enforces verified zero-price metadata against live OpenRouter catalog.
        # Fails closed if the authenticated catalog cannot be verified or pricing is non-zero.
        if spec.provider_id == "openrouter" and self.free_only and not self.dry_run:
            self._verify_openrouter_live_pricing(spec)

        # Live Preflight Verification for Cohere Trial Entitlement (Hardened S3.5.4B)
        # Enforces verified active trial evaluation key against live Cohere catalog and response headers.
        # Fails closed if the account is a production/paid key, quota exhausted, or target model missing.
        if spec.provider_id == "cohere" and self.require_cohere_trial and not self.dry_run:
            self._verify_cohere_live_trial_entitlement(spec)

        # 6. Disable all 16 slots first
        for c in list(self.catalog.candidates.values()):
            self.catalog.disable_candidate(c.candidate_id)

        # 7. Enable strictly the single chosen candidate slot
        if not self.catalog.get_candidate(spec.candidate_id):
            # Register extension candidate slot (e.g. t1_c5)
            tier = ModelTier.TIER_1_BUDGET if spec.candidate_id.startswith("t1_") else ModelTier.TIER_2_BALANCED
            priority = int(spec.candidate_id.split("_c")[1]) if "_c" in spec.candidate_id else 5
            self.catalog.register_candidate(
                ModelCandidate(
                    candidate_id=spec.candidate_id,
                    tier=tier,
                    priority=priority,
                    provider_id=spec.provider_id,
                    exact_model_id=spec.exact_model_id,
                    enabled=False,
                    provider_adapter_kind="openai_compatible",
                    allowed_tasks=["*"],
                    verified_capabilities=spec.capabilities,
                    pricing=spec.pricing,
                    verification_notes=spec.verification_notes,
                )
            )

        self.catalog.enable_candidate(
            candidate_id=spec.candidate_id,
            provider_id=spec.provider_id,
            exact_model_id=spec.exact_model_id,
            verified_capabilities=spec.capabilities,
            pricing=spec.pricing,
            verification_notes=spec.verification_notes,
            allowed_tasks=["*"],
        )

    def _verify_openrouter_live_pricing(self, spec: ProviderConnectionSpec) -> None:
        """
        Enforce verified zero-price metadata at live preflight.
        Does not describe static catalog constants as fresh API verification.
        Fails closed if the authenticated catalog cannot be verified or reports non-zero pricing.
        """
        if not self.real_provider:
            raise LLMNotConfiguredError("FREE_ELIGIBILITY_UNCONFIRMED: Provider instance not initialized. Fail closed.")

        url = f"{spec.base_url.rstrip('/')}/models"
        api_key = self.real_provider._get_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        if spec.capabilities.get("extra_headers"):
            headers.update(spec.capabilities["extra_headers"])

        client_kwargs = {
            "timeout": httpx.Timeout(timeout=15.0, connect=10.0, read=15.0, write=10.0),
            "follow_redirects": False,
        }
        if hasattr(self.real_provider, "_transport") and self.real_provider._transport:
            client_kwargs["transport"] = self.real_provider._transport

        try:
            if hasattr(self.real_provider, "_injected_client") and self.real_provider._injected_client:
                resp = self.real_provider._injected_client.get(url, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(url, headers=headers)
        except Exception as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog query failed with network/client error: {e}. "
                "Fail closed."
            ) from e

        if resp.status_code != 200:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog query failed (HTTP {resp.status_code}). "
                "Fail closed."
            )

        try:
            catalog_json = resp.json()
        except Exception as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog response is not valid JSON ({e}). Fail closed."
            ) from e

        if not isinstance(catalog_json, dict) or "data" not in catalog_json:
            raise LLMNotConfiguredError(
                "FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog payload missing 'data' array. Fail closed."
            )

        models_list = catalog_json.get("data")
        if not isinstance(models_list, list) or len(models_list) == 0:
            raise LLMNotConfiguredError(
                "FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog returned empty or stale model list. Fail closed."
            )

        # Look for exact model ID
        matched = next((m for m in models_list if isinstance(m, dict) and m.get("id") == spec.exact_model_id), None)
        if not matched:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{spec.exact_model_id}' was not found in the live OpenRouter catalog. "
                "Fail closed."
            )

        pricing = matched.get("pricing")
        if not isinstance(pricing, dict):
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{spec.exact_model_id}' in live OpenRouter catalog has no pricing metadata. "
                "Fail closed."
            )

        try:
            prompt_cost = float(pricing.get("prompt", -1.0))
            completion_cost = float(pricing.get("completion", -1.0))
        except (ValueError, TypeError) as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{spec.exact_model_id}' in live OpenRouter catalog has unparseable pricing ({e}). "
                "Fail closed."
            ) from e

        if prompt_cost != 0.0 or completion_cost != 0.0:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Live OpenRouter catalog reports non-zero pricing "
                f"(prompt={prompt_cost}, completion={completion_cost}) for '{spec.exact_model_id}'. "
                "Free-only execution refused (no paid fallback)."
            )

    def _verify_cohere_live_trial_entitlement(self, spec: ProviderConnectionSpec) -> Dict[str, Any]:
        """
        Active live preflight verification for Cohere Trial entitlement (S3.5.4B).
        Performs read-only metadata probe against Cohere /v1/models endpoint.
        Verifies:
        1. Exact target model (command-r-08-2024) is active in catalog.
        2. Account is an active Trial/Evaluation key (authoritative x-trial headers present).
        3. Sufficient remaining trial allowance (x-trial-endpoint-call-remaining > 0).
        4. Rejects Production/Paid keys (which lack trial rate-limit headers or bill per token).
        5. Fails closed on transport errors, missing headers, or exhausted quota.
        """
        if not self.real_provider:
            raise LLMNotConfiguredError("FREE_ELIGIBILITY_UNCONFIRMED: Provider instance not initialized. Fail closed.")

        url = "https://api.cohere.com/v1/models?page_size=100"
        api_key = self.real_provider._get_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "SalesAI-Gateway/2.2",
        }

        timeout = httpx.Timeout(timeout=15.0, connect=10.0, read=15.0, write=10.0)
        client_kwargs = {"timeout": timeout, "follow_redirects": False}
        if self._real_provider_transport:
            client_kwargs["transport"] = self._real_provider_transport

        try:
            if hasattr(self.real_provider, "_injected_client") and self.real_provider._injected_client:
                resp = self.real_provider._injected_client.get(url, headers=headers)
            else:
                with httpx.Client(**client_kwargs) as client:
                    resp = client.get(url, headers=headers)
        except Exception as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Transport error querying Cohere metadata: {e}. Fail closed."
            ) from e

        if resp.status_code != 200:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Cohere metadata probe returned HTTP {resp.status_code}. Fail closed."
            )

        resp_headers = resp.headers
        trial_limit_raw = resp_headers.get("x-trial-endpoint-call-limit")
        trial_remaining_raw = resp_headers.get("x-trial-endpoint-call-remaining")

        if not trial_limit_raw or not trial_remaining_raw:
            raise LLMNotConfiguredError(
                "FREE_ELIGIBILITY_UNCONFIRMED: Cohere response missing authoritative Trial headers "
                "('x-trial-endpoint-call-limit' or 'x-trial-endpoint-call-remaining'). "
                "Production / Paid keys or unconfirmed keys are strictly rejected under --require-cohere-trial. "
                "Fail closed."
            )

        try:
            trial_limit = int(trial_limit_raw)
            trial_remaining = int(trial_remaining_raw)
        except (ValueError, TypeError) as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Cohere trial headers unparseable (limit={trial_limit_raw!r}, remaining={trial_remaining_raw!r}): {e}. "
                "Fail closed."
            ) from e

        if trial_remaining <= 0:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Cohere Trial allowance exhausted (limit={trial_limit}, remaining={trial_remaining}). "
                "No free requests remaining. Fail closed."
            )

        monthly_limit_raw = resp_headers.get("x-endpoint-monthly-call-limit")
        try:
            monthly_limit = int(monthly_limit_raw) if monthly_limit_raw else None
        except (ValueError, TypeError):
            monthly_limit = None

        try:
            data = resp.json()
        except Exception as e:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Cohere metadata response is not valid JSON ({e}). Fail closed."
            ) from e

        models_list = data.get("models") or data.get("data") or []
        if not isinstance(models_list, list) or len(models_list) == 0:
            raise LLMNotConfiguredError(
                "FREE_ELIGIBILITY_UNCONFIRMED: Cohere metadata returned empty model list. Fail closed."
            )

        target_model = spec.exact_model_id
        matched = next(
            (m for m in models_list if isinstance(m, dict) and (m.get("name") == target_model or m.get("id") == target_model)),
            None,
        )
        if not matched:
            raise LLMNotConfiguredError(
                f"FREE_ELIGIBILITY_UNCONFIRMED: Model '{target_model}' was not found in active Cohere catalog. Fail closed."
            )

        return {
            "trial_active": True,
            "call_limit": trial_limit,
            "call_remaining": trial_remaining,
            "monthly_limit": monthly_limit,
            "model_confirmed": target_model,
        }

    def _get_current_run_spend(self, run_id: str) -> float:
        """Calculate cumulative spend for the current run from SQLite ledger (reconciled + reserved + uncertain)."""
        with self.ledger._lock:
            conn = self.ledger._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                        FROM llm_reservations
                        WHERE request_id LIKE ?
                          AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                        """,
                        (f"bench_{run_id}_%",),
                    )
                    row = cursor.fetchone()
                    return float(row[0]) if row and row[0] is not None else 0.0
            finally:
                if not self.ledger._is_memory:
                    conn.close()

    def get_run_ledger_summary(self, run_id: str) -> Dict[str, Any]:
        """Expose a precise read-only summary from the SQLite ledger for this run."""
        with self.ledger._lock:
            conn = self.ledger._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        SELECT reservation_id, request_id, model, status, final_cost_usd, reserved_usd, failure_code,
                               billable_cost_usd, spend_provenance
                        FROM llm_reservations
                        WHERE request_id LIKE ?
                        ORDER BY created_at ASC;
                        """,
                        (f"bench_{run_id}_%",),
                    )
                    rows = cursor.fetchall()
                    records = []
                    status_counts: Dict[str, int] = {}
                    total_cost = 0.0
                    reconciled_cost = 0.0
                    uncertain_cost = 0.0
                    total_billable = 0.0
                    for r in rows:
                        status = r[3]
                        f_cost = r[4]
                        r_cost = r[5]
                        effective_cost = f_cost if f_cost is not None else r_cost
                        status_counts[status] = status_counts.get(status, 0) + 1
                        if status in ("RECONCILED", "UNCERTAIN"):
                            total_cost += effective_cost
                        if status == "RECONCILED" and f_cost is not None:
                            reconciled_cost += f_cost
                        elif status == "UNCERTAIN":
                            uncertain_cost += r_cost

                        prov = r[8] if len(r) > 8 else None
                        if len(r) > 7 and r[7] is not None:
                            b_cost = r[7]
                        elif prov in ("VERIFIED_COHERE_TRIAL", "VERIFIED_FREE_TIER"):
                            b_cost = 0.0
                        else:
                            b_cost = f_cost

                        if status in ("RECONCILED", "UNCERTAIN") and b_cost is not None:
                            total_billable += b_cost

                        records.append({
                            "reservation_id": r[0],
                            "request_id": r[1],
                            "model": r[2],
                            "status": status,
                            "final_cost_usd": f_cost,
                            "commercial_equivalent_cost_usd": f_cost,
                            "billable_cost_usd": b_cost,
                            "reserved_usd": r_cost,
                            "effective_cost_usd": effective_cost,
                            "failure_code": r[6],
                            "spend_provenance": prov,
                        })
                    return {
                        "db_path": str(self.db_path),
                        "run_id": run_id,
                        "reservations_count": len(records),
                        "status_counts": status_counts,
                        "total_cost_usd": round(total_cost, 6),
                        "commercial_equivalent_cost_usd": round(total_cost, 6),
                        "total_billable_spend_usd": round(total_billable, 6),
                        "reconciled_cost_usd": round(reconciled_cost, 6),
                        "uncertain_cost_usd": round(uncertain_cost, 6),
                        "records": records,
                    }
            finally:
                if not self.ledger._is_memory:
                    conn.close()

    def run_benchmark(
        self,
        task_filter: Optional[str] = None,
        candidate_filter: Optional[str] = None,
        max_cases_per_task: Optional[int] = None,
    ) -> BenchmarkRunReport:
        """
        Execute benchmark evaluation across filtered tasks and candidates.
        Evaluates Tier 1 candidates sequentially per task: tests candidate #1; stops if qualified.
        """
        run_id = f"s3_bench_{uuid.uuid4().hex[:10]}"
        timestamp = datetime.now(timezone.utc).isoformat()
        self._attempted_requests_count = 0

        # Canonical 7 paths
        all_task_paths = [
            ("website_extraction", "pass_a"),
            ("website_extraction", "pass_b"),
            ("job_analysis", None),
            ("discovery_query_generation", None),
            ("inbox_classification", None),
            ("reply_suggestion", None),
            ("outreach_draft", None),
        ]

        # 1. Strict task filter validation (S3.6B Phase 1): reject unknown or ambiguous before any provider or budget setup
        if task_filter and task_filter != "all":
            clean_filter = task_filter.strip()
            if not clean_filter:
                raise LLMNotConfiguredError(
                    "INVALID_TASK_FILTER: task_filter cannot be empty or whitespace. "
                    "Specify exact task path (e.g. 'website_extraction:pass_a') or 'all' for full catalog. Fail closed."
                )
            filtered_paths = []
            for t_id, sub_id in all_task_paths:
                full_key = f"{t_id}:{sub_id}" if sub_id else t_id
                if clean_filter in (t_id, full_key):
                    filtered_paths.append((t_id, sub_id))
            if not filtered_paths:
                valid_options = ", ".join(f"'{t}:{s}'" if s else f"'{t}'" for t, s in all_task_paths)
                raise LLMNotConfiguredError(
                    f"INVALID_TASK_FILTER: Unknown task identifier '{clean_filter}'. "
                    f"Valid canonical task paths are: {valid_options}. Fail closed."
                )
            if not self.dry_run and len(filtered_paths) > 1:
                raise LLMNotConfiguredError(
                    f"AMBIGUOUS_TASK_FILTER: Live execution requires an unambiguous single subtask path (got '{clean_filter}' matching {len(filtered_paths)} paths). "
                    "Specify exact task path such as 'website_extraction:pass_a'. Fail closed."
                )
            target_paths = filtered_paths
        else:
            if not self.dry_run:
                raise LLMNotConfiguredError(
                    "Live smoke execution requires an explicit single task (e.g. --task website_extraction:pass_a). "
                    "Multiple tasks or 'all' not permitted in live smoke pilot."
                )
            target_paths = all_task_paths

        if self.require_cohere_trial:
            effective_prov = self.provider_filter or "cohere"
            effective_cand = candidate_filter or "t2_c2"
            if effective_prov != "cohere" or (candidate_filter and candidate_filter != "t2_c2"):
                raise LLMNotConfiguredError(
                    f"COHERE_TRIAL_GUARD_VIOLATION: --require-cohere-trial is strictly applicable only to "
                    f"Cohere candidate 't2_c2' (got provider='{effective_prov}', candidate='{effective_cand}'). "
                    "Execution refused. Fail closed."
                )

        if not self.dry_run:
            if self.provider_filter:
                self._configure_live_candidate(
                    candidate_id=candidate_filter,
                    provider_id=self.provider_filter,
                )
                # Filter solely to the newly enabled candidate
                enabled_cands = [c.candidate_id for c in self.catalog.candidates.values() if c.enabled]
                candidate_filter = enabled_cands[0] if enabled_cands else "t1_c1"
            elif candidate_filter == "t1_c1":
                self._configure_live_candidate("t1_c1", provider_id="openai")
            else:
                raise LLMNotConfiguredError(
                    "Real benchmarking requires verified live provider configuration with non-mock pricing. "
                    "Explicitly configure verified candidate --candidate t1_c1. Fails closed."
                )
        else:
            # S3.5.1: In dry-run mode, if provider_filter is given and candidate_filter is omitted,
            # align candidate_filter to the provider's active candidate spec to avoid recommendation mismatch
            if self.provider_filter and not candidate_filter:
                p_spec = get_provider_spec(self.provider_filter)
                if p_spec:
                    candidate_filter = p_spec.candidate_id

        summaries: List[CandidateTaskSummary] = []
        recommendations: Dict[str, Dict[str, Any]] = {}
        total_cases_run = 0
        total_cost = 0.0

        for task_id, subtask_id in target_paths:
            task_key = f"{task_id}:{subtask_id}" if subtask_id else task_id
            fixtures = get_fixtures_for_task(task_id, subtask_id)
            if max_cases_per_task and max_cases_per_task > 0:
                fixtures = fixtures[:max_cases_per_task]

            if not fixtures:
                continue

            # Retrieve candidates for Tier 1
            if candidate_filter:
                target_candidates = [
                    self.catalog.get_candidate(candidate_filter)
                ]
                target_candidates = [c for c in target_candidates if c is not None]
            else:
                target_candidates = self.catalog.get_candidates_for_tier(
                    task_id=task_id,
                    tier=ModelTier.TIER_1_BUDGET,
                    only_enabled=True,
                )

            task_qualified_candidate: Optional[ModelCandidate] = None

            # Sequential Tier 1 evaluation
            for candidate in target_candidates:
                if not self.dry_run:
                    if not candidate.pricing:
                        raise LLMNotConfiguredError(
                            f"Live commercial benchmark refused: candidate '{candidate.candidate_id}' has no pricing configured."
                        )
                    source_url = (candidate.pricing.source_url or "").lower()
                    if "mock.internal" in source_url or "mock" in source_url or "mock" in candidate.provider_id.lower():
                        raise LLMNotConfiguredError(
                            f"Live commercial benchmark refused: candidate '{candidate.candidate_id}' has mock pricing/provider "
                            f"('{candidate.pricing.source_url}'). Real benchmarking requires verified live provider configuration."
                        )

                summary = self._evaluate_candidate_for_task(
                    candidate=candidate,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    fixtures=fixtures,
                    run_id=run_id,
                )
                summaries.append(summary)
                total_cases_run += summary.requests_attempted
                total_cost += summary.total_cost_usd

                meets_threshold = (
                    summary.safety_violations_count == 0
                    and summary.overall_pass_rate >= self.qualification_threshold
                )

                if task_qualified_candidate is None:
                    if summary.task_qualification_eligible and summary.qualified:
                        task_qualified_candidate = candidate
                        recommendations[task_key] = {
                            "task": task_key,
                            "candidate_id": candidate.candidate_id,
                            "provider_id": candidate.provider_id,
                            "exact_model_id": candidate.exact_model_id,
                            "overall_pass_rate": summary.overall_pass_rate,
                            "safety_violations": summary.safety_violations_count,
                            "status": "PROPOSED_RECOMMENDATION",
                            "is_production_approved": False,
                            "provenance_note": (
                                "Recommended by S3.1/S3.2/S3.6B.1 benchmark evaluator. "
                                "Requires authenticated operator approval before production route activation."
                            ),
                        }
                    elif summary.is_smoke_only:
                        recommendations[task_key] = {
                            "task": task_key,
                            "candidate_id": candidate.candidate_id,
                            "provider_id": candidate.provider_id,
                            "exact_model_id": candidate.exact_model_id,
                            "overall_pass_rate": summary.overall_pass_rate,
                            "safety_violations": summary.safety_violations_count,
                            "status": "SMOKE_ONLY",
                            "is_production_approved": False,
                            "provenance_note": (
                                "Evaluated under short smoke test mode (<10 cases). "
                                "Smoke tests cannot qualify or approve a model as a production winner (SMOKE_ONLY / NOT MODEL-QUALIFIED)."
                            ),
                        }
                    else:
                        recommendations[task_key] = {
                            "task": task_key,
                            "candidate_id": candidate.candidate_id,
                            "provider_id": candidate.provider_id,
                            "exact_model_id": candidate.exact_model_id,
                            "overall_pass_rate": summary.overall_pass_rate,
                            "safety_violations": summary.safety_violations_count,
                            "status": "INCOMPLETE / NOT QUALIFIED",
                            "is_production_approved": False,
                            "provenance_note": (
                                "Candidate did not achieve complete qualification. "
                                "Disqualified from production recommendation proposal."
                            ),
                        }
                    if self.stop_on_first_qualifying and (summary.qualified or summary.is_smoke_only):
                        # Stop evaluating subsequent candidates for this task path
                        break

        tasks_evaluated = [f"{t}:{s}" if s else t for t, s in target_paths]
        is_smoke_run = any(s.is_smoke_only for s in summaries) or total_cases_run < 10
        return BenchmarkRunReport(
            run_id=run_id,
            timestamp=timestamp,
            is_dry_run=self.dry_run,
            tasks_evaluated=tasks_evaluated,
            candidate_summaries=summaries,
            recommended_candidates=recommendations,
            total_cases_run=total_cases_run,
            total_cost_usd=round(total_cost, 6),
            scorer_version=SCORER_VERSION,
            fixture_catalog_version="s3.0-catalog-v1",
            pricing_source_mode="offline_mock" if self.dry_run else "provider_direct_verified",
            is_smoke_only=is_smoke_run,
            campaign_id=self.campaign_id,
            ledger_db_path=str(self.db_path),
            historical_liability=getattr(self, "historical_preflight_summary", None),
        )

    def _evaluate_candidate_for_task(
        self,
        candidate: ModelCandidate,
        task_id: str,
        subtask_id: Optional[str],
        fixtures: List[BenchmarkFixtureCase],
        run_id: str,
    ) -> CandidateTaskSummary:
        """Evaluate a single candidate against all fixtures for a given task/subtask."""
        case_results: List[BenchmarkCaseExecutionResult] = []
        cases_passed = 0
        safety_violations = 0
        syntactic_passes = 0
        semantic_passes = 0
        safety_passes = 0
        total_latency = 0
        total_cost = 0.0

        unrecoverable_stop_reason: Optional[str] = None

        for i, fixture in enumerate(fixtures):
            case_res = self._execute_single_case(
                candidate=candidate,
                fixture=fixture,
                run_id=run_id,
            )
            case_results.append(case_res)

            total_latency += case_res.latency_ms
            if case_res.cost_usd:
                total_cost += case_res.cost_usd

            if case_res.syntactic_valid:
                syntactic_passes += 1
            if case_res.semantic_valid:
                semantic_passes += 1
            if case_res.safety_valid:
                safety_passes += 1
            elif not any(cat in case_res.failure_categories for cat in (
                "GATEWAY_DISPATCH_ERROR", "UNCERTAIN_USAGE", "RUN_REQUEST_CAP_REACHED",
                "PRE_DISPATCH_BUDGET_BLOCKED", "RATE_LIMIT", "RATE_LIMIT_QUOTA_EXHAUSTED",
                "UNRECOVERABLE_PROVIDER_ERROR"
            )):
                safety_violations += 1

            if case_res.overall_pass:
                cases_passed += 1

            # Inter-request delay pacing (S3.6B Phase 4): apply between completed attempts
            # Do not sleep after the final request
            if self.inter_request_delay_seconds > 0.0 and i < len(fixtures) - 1:
                if case_res.lifecycle_status not in (CaseLifecycleStatus.PRE_DISPATCH_BLOCKED, CaseLifecycleStatus.NOT_ATTEMPTED):
                    self._sleeper(self.inter_request_delay_seconds)

            # Check for unrecoverable provider stop conditions (Fail-closed on HTTP 429, auth, billing, quota)
            has_rate_limit = any(
                "RATE_LIMIT" in cat or "429" in cat for cat in case_res.failure_categories
            ) or any(
                "rate limit" in r.lower() or "429" in r.lower() or "quota" in r.lower() for r in case_res.failure_reasons
            )
            has_auth_or_billing = any(
                "AUTH" in cat or "BILLING" in cat or "QUOTA" in cat for cat in case_res.failure_categories
            ) or any(
                any(k in r.lower() for k in ("unauthorized", "invalid api key", "credit", "quota exceeded", "billing", "payment required", "401", "403"))
                for r in case_res.failure_reasons
            )
            has_cap_reached = "RUN_REQUEST_CAP_REACHED" in case_res.failure_categories
            has_budget_blocked = "PRE_DISPATCH_BUDGET_BLOCKED" in case_res.failure_categories

            if has_rate_limit:
                unrecoverable_stop_reason = (
                    f"Rate limit / HTTP 429 encountered on case {fixture.case_id}. "
                    "Fail-closed stop triggered to prevent quota exhaustion or runaway loops."
                )
                break
            elif has_auth_or_billing:
                unrecoverable_stop_reason = (
                    f"Unrecoverable provider credential/billing/quota failure on case {fixture.case_id}. "
                    "Fail-closed stop triggered."
                )
                break
            elif has_cap_reached or has_budget_blocked:
                unrecoverable_stop_reason = f"Budget or request ceiling blocked further execution on case {fixture.case_id}."
                break

        # If loop exited early, record all remaining unattempted fixtures as NOT_ATTEMPTED
        executed_fixture_ids = {c.case_id for c in case_results}
        for remaining_fixture in fixtures:
            if remaining_fixture.case_id not in executed_fixture_ids:
                case_results.append(
                    BenchmarkCaseExecutionResult(
                        case_id=remaining_fixture.case_id,
                        task_id=remaining_fixture.task_id,
                        subtask_id=remaining_fixture.subtask_id,
                        group=remaining_fixture.group,
                        candidate_id=candidate.candidate_id,
                        provider_id=candidate.provider_id,
                        exact_model_id=candidate.exact_model_id,
                        latency_ms=0,
                        syntactic_valid=False,
                        semantic_valid=False,
                        safety_valid=False,
                        overall_pass=False,
                        failure_categories=["UNATTEMPTED_CASE"],
                        failure_reasons=[
                            f"Execution halted prior to dispatch: {unrecoverable_stop_reason or 'Stopped by runner'}"
                        ],
                        sqlite_reservation_status="NOT_ATTEMPTED",
                        spend_provenance="NOT_DISPATCHED",
                        estimated_spend_usd=0.0,
                        lifecycle_status=CaseLifecycleStatus.NOT_ATTEMPTED,
                    )
                )

        # Denominator calculations based on explicit lifecycle
        cases_planned = len(fixtures)

        rate_limit_failures = sum(
            1 for c in case_results
            if c.lifecycle_status != CaseLifecycleStatus.NOT_ATTEMPTED
            and "UNATTEMPTED_CASE" not in c.failure_categories
            and (
                any("RATE_LIMIT" in r or "rate limit" in r.lower() or "429" in r.lower() for r in c.failure_reasons)
                or any("RATE_LIMIT" in cat or "429" in cat for cat in c.failure_categories)
            )
        )
        transport_failures = sum(
            1 for c in case_results
            if c.lifecycle_status == CaseLifecycleStatus.DISPATCH_FAILED
            or any(cat in c.failure_categories for cat in ("GATEWAY_DISPATCH_ERROR", "UNCERTAIN_USAGE"))
            or any("TIMEOUT" in cat for cat in c.failure_categories)
        )
        requests_attempted = sum(
            1 for c in case_results
            if c.lifecycle_status in (
                CaseLifecycleStatus.DISPATCH_ATTEMPTED,
                CaseLifecycleStatus.RESPONSE_RECEIVED,
                CaseLifecycleStatus.EVALUATED,
                CaseLifecycleStatus.DISPATCH_FAILED,
            )
            and c.lifecycle_status != CaseLifecycleStatus.PRE_DISPATCH_BLOCKED
            and "RUN_REQUEST_CAP_REACHED" not in c.failure_categories
            and "PRE_DISPATCH_BUDGET_BLOCKED" not in c.failure_categories
            and "UNATTEMPTED_CASE" not in c.failure_categories
        )
        responses_received = max(0, requests_attempted - transport_failures)
        cases_evaluable = sum(
            1 for c in case_results
            if c.lifecycle_status == CaseLifecycleStatus.EVALUATED
            and not any(cat in c.failure_categories for cat in (
                "GATEWAY_DISPATCH_ERROR", "UNCERTAIN_USAGE", "RUN_REQUEST_CAP_REACHED",
                "PRE_DISPATCH_BUDGET_BLOCKED", "UNATTEMPTED_CASE"
            ))
            and not any("TIMEOUT" in cat for cat in c.failure_categories)
        )
        # Pre-dispatch budget/cap blocks and unattempted cases
        unattempted_cases = max(0, cases_planned - requests_attempted)

        # Invariant checks
        # requests_attempted <= cases_planned
        # responses_received <= requests_attempted
        # cases_evaluable <= responses_received
        # unattempted_cases == cases_planned - requests_attempted

        # Legacy backward-compatibility alias: cases_evaluated strictly equals cases_evaluable
        cases_evaluated = cases_evaluable

        # Rates: denominator for model quality must be genuinely evaluable cases,
        # but overall campaign success requires all planned cases to be evaluated.
        syn_rate = (syntactic_passes / cases_evaluable) if cases_evaluable > 0 else 0.0
        sem_rate = (semantic_passes / cases_evaluable) if cases_evaluable > 0 else 0.0
        saf_rate = (safety_passes / cases_evaluable) if cases_evaluable > 0 else 0.0
        schema_rate = (sum(1 for c in case_results if c.schema_pass) / cases_evaluable) if cases_evaluable > 0 else 0.0
        gt_rate = (sum(1 for c in case_results if c.ground_truth_pass) / cases_evaluable) if cases_evaluable > 0 else 0.0
        ev_rate = (sum(1 for c in case_results if c.evidence_pass) / cases_evaluable) if cases_evaluable > 0 else 0.0

        # Overall pass rate is evaluated against cases_planned to guarantee incomplete runs cannot qualify!
        ovr_rate = (cases_passed / cases_planned) if cases_planned > 0 else 0.0
        dispatch_success_rate = (responses_received / requests_attempted) if requests_attempted > 0 else 0.0
        avg_lat = (total_latency / requests_attempted) if requests_attempted > 0 else 0.0
        needs_rev_count = sum(1 for c in case_results if c.needs_review)
        unsupported_count = sum(len(c.forbidden_tokens_found) for c in case_results)

        # Latency statistics
        latencies = sorted([c.latency_ms for c in case_results if c.latency_ms > 0])
        if latencies:
            mid = len(latencies) // 2
            median_lat = float(latencies[mid]) if len(latencies) % 2 != 0 else float((latencies[mid - 1] + latencies[mid]) / 2.0)
            p95_idx = min(len(latencies) - 1, int(math.ceil(0.95 * len(latencies))) - 1)
            p95_lat = float(latencies[p95_idx])
        else:
            median_lat = 0.0
            p95_lat = None

        # Token accounting
        tot_in_toks = sum(c.input_tokens or 0 for c in case_results)
        tot_out_toks = sum(c.output_tokens or 0 for c in case_results)
        comm_cost_tot = sum(c.commercial_cost_usd or c.cost_usd or 0.0 for c in case_results)
        bill_cost_tot = sum(c.billable_cost_usd or 0.0 for c in case_results)
        uncertain_cost_tot = sum(c.cost_usd or 0.0 for c in case_results if c.sqlite_reservation_status == "UNCERTAIN")

        # Smoke rule: planned cases < 10 or explicit connectivity smoke
        is_smoke = (cases_planned < 10) or self.is_connectivity_smoke

        # Evaluation state classification
        if requests_attempted > 0 and rate_limit_failures >= requests_attempted:
            eval_state = "INFERENCE_UNVERIFIED / RATE_LIMITED"
        elif requests_attempted > 0 and transport_failures >= requests_attempted:
            eval_state = "INFERENCE_UNVERIFIED / DISPATCH_ERROR"
        elif cases_evaluable < cases_planned:
            eval_state = "PARTIAL_EVALUATION"
        else:
            eval_state = "EVALUATED"

        # Qualification rules (S3.6B Phase 2 & 3, S3.6B.1 Acceptance):
        # 1. Minimum 10 planned cases (cases_planned >= 10)
        # 2. Complete execution: cases_evaluable == cases_planned
        # 3. Complete requests: requests_attempted == cases_planned
        # 4. Zero unattempted cases: unattempted_cases == 0
        # 5. 100% safety pass rate (safety_violations_count == 0)
        # 6. Overall pass rate >= threshold against cases_planned
        disqualifications: List[str] = []
        if eval_state == "INFERENCE_UNVERIFIED / RATE_LIMITED":
            disqualifications.append(
                f"Inference unverified: Candidate experienced rate limit (HTTP 429) across attempts. "
                "No output evaluated (not a model quality or safety assessment failure)."
            )
        elif eval_state == "INFERENCE_UNVERIFIED / DISPATCH_ERROR":
            disqualifications.append(
                f"Inference unverified: Gateway transport/dispatch error occurred across attempts. "
                "No output evaluated (not a model quality or safety assessment failure)."
            )
        else:
            if is_smoke:
                disqualifications.append(
                    f"Smoke test evaluation ({cases_planned} < 10 planned cases) cannot qualify or approve candidate (SMOKE_ONLY)."
                )
            if cases_planned < 10:
                disqualifications.append(
                    f"Sample size insufficient: {cases_planned} planned cases (minimum 10 required for qualification)."
                )
            if cases_evaluable < cases_planned:
                disqualifications.append(
                    f"Incomplete evaluation: only {cases_evaluable} of {cases_planned} cases were evaluable (minimum 10 required for qualification)."
                )
            if requests_attempted < cases_planned:
                disqualifications.append(
                    f"Incomplete requests: only {requests_attempted} of {cases_planned} planned cases were attempted."
                )
            if unattempted_cases > 0:
                disqualifications.append(
                    f"Unattempted cases present: {unattempted_cases} cases were not completed."
                )
            if safety_violations > 0:
                disqualifications.append(f"Hard safety violations detected ({safety_violations} cases failed safety).")
            if ovr_rate < self.qualification_threshold:
                disqualifications.append(
                    f"Overall pass rate {ovr_rate:.1%} ({cases_passed}/{cases_planned}) is below qualification threshold {self.qualification_threshold:.1%}."
                )

        task_qual_eligible = (
            len(disqualifications) == 0
            and not is_smoke
            and cases_planned >= 10
            and cases_evaluable == cases_planned
            and requests_attempted == cases_planned
            and unattempted_cases == 0
            and safety_violations == 0
            and ovr_rate >= self.qualification_threshold
        )
        is_qualified = task_qual_eligible

        return CandidateTaskSummary(
            candidate_id=candidate.candidate_id,
            task_id=task_id,
            subtask_id=subtask_id,
            provider_id=candidate.provider_id,
            exact_model_id=candidate.exact_model_id,
            cases_planned=cases_planned,
            requests_attempted=requests_attempted,
            responses_received=responses_received,
            cases_evaluable=cases_evaluable,
            cases_passed=cases_passed,
            transport_failures=transport_failures,
            rate_limit_failures=rate_limit_failures,
            unattempted_cases=unattempted_cases,
            cases_evaluated=cases_evaluated,
            syntactic_pass_rate=round(syn_rate, 4),
            semantic_pass_rate=round(sem_rate, 4),
            safety_pass_rate=round(saf_rate, 4),
            overall_pass_rate=round(ovr_rate, 4),
            schema_pass_rate=round(schema_rate, 4),
            ground_truth_pass_rate=round(gt_rate, 4),
            evidence_pass_rate=round(ev_rate, 4),
            dispatch_success_rate=round(dispatch_success_rate, 4),
            safety_violations_count=safety_violations,
            unsupported_claim_count=unsupported_count,
            needs_review_count=needs_rev_count,
            avg_latency_ms=round(avg_lat, 2),
            median_latency_ms=round(median_lat, 2),
            p95_latency_ms=round(p95_lat, 2) if p95_lat is not None else None,
            input_tokens=tot_in_toks,
            output_tokens=tot_out_toks,
            total_cost_usd=round(total_cost, 6),
            commercial_cost_usd=round(comm_cost_tot, 6),
            billable_cost_usd=round(bill_cost_tot, 6),
            uncertain_cost_usd=round(uncertain_cost_tot, 6),
            qualified=is_qualified,
            task_qualification_eligible=task_qual_eligible,
            is_smoke_only=is_smoke,
            evaluation_state=eval_state,
            production_approval_state="NOT_APPROVED_PROPOSAL_ONLY",
            disqualification_reasons=disqualifications,
            case_results=case_results,
        )

    def _execute_single_case(
        self,
        candidate: ModelCandidate,
        fixture: BenchmarkFixtureCase,
        run_id: str,
    ) -> BenchmarkCaseExecutionResult:
        """
        Execute a single benchmark fixture through the Gateway and evaluate with OutputValidator.
        Expected answers / gold labels are STRICTLY kept inside the evaluator and never sent to provider.
        """
        req_id = f"bench_{run_id}_{fixture.case_id}_{uuid.uuid4().hex[:6]}"
        is_opt_out = (
            fixture.input_context.get("has_opt_out", False)
            or fixture.adversarial_type == "opt_out"
        )
        evidence_id = fixture.input_context.get("evidence_id")
        pricing = candidate.pricing
        if self.require_cohere_trial and candidate.provider_id == "cohere":
            spend_prov = "VERIFIED_COHERE_TRIAL"
        elif pricing and pricing.currency == "FREE":
            spend_prov = "VERIFIED_FREE_TIER"
        else:
            spend_prov = "VERIFIED_PAID" if not self.dry_run else "OFFLINE_MOCK"

        meta = {
            "subtask_id": fixture.subtask_id,
            "case_id": fixture.case_id,
            "group": fixture.group,
            "candidate_id": candidate.candidate_id,
            "evidence_id": evidence_id,
            "has_opt_out": is_opt_out,
            "spend_provenance": spend_prov,
        }

        # Resolve structured schema if candidate supports provider-side JSON Schema
        task_schema = None
        if candidate.verified_capabilities.get("supports_json_schema"):
            if SchemaRegistry.is_registered(fixture.task_id, fixture.subtask_id):
                schema_cls = SchemaRegistry.get_schema(fixture.task_id, fixture.subtask_id)
                task_schema = schema_cls.model_json_schema()
        elif candidate.verified_capabilities.get("supports_json_mode") or candidate.verified_capabilities.get("json_mode"):
            task_schema = {"type": "object"}

        # Build LLMRequest strictly without expected gold labels
        request = LLMRequest(
            prompt=fixture.prompt,
            system_prompt=fixture.system_prompt,
            task_id=fixture.task_id,
            model=candidate.exact_model_id,
            provider=candidate.provider_id,
            tier=candidate.tier,
            response_schema=task_schema,
            request_id=req_id,
            metadata=meta,
        )

        if (
            self.require_cohere_trial
            and candidate.provider_id == "cohere"
            and candidate.candidate_id == "t2_c2"
            and self.cohere_trial_timeout_seconds is not None
        ):
            request.timeout_seconds = float(self.cohere_trial_timeout_seconds)

        prompt_chars = len(fixture.prompt) + (len(fixture.system_prompt) if fixture.system_prompt else 0)
        est_input_tokens = max(1, math.ceil(prompt_chars / self.budget_policy.chars_per_token_estimate))
        est_output_tokens = request.max_output_tokens
        if pricing and pricing.input_per_1m is not None and pricing.output_per_1m is not None:
            est_spend = round((
                est_input_tokens * pricing.input_per_1m
                + est_output_tokens * pricing.output_per_1m
            ) / 1_000_000.0, 6)
        else:
            est_spend = (self.max_cost_usd + 1.0) if not self.dry_run else 0.0

        is_trial_or_free = spend_prov in ("VERIFIED_COHERE_TRIAL", "VERIFIED_FREE_TIER")
        est_billable = 0.0 if is_trial_or_free else est_spend

        # Check request and cost ceilings before dispatch (enforced for live runs)
        if not self.dry_run:
            if self._attempted_requests_count >= self.max_requests:
                return BenchmarkCaseExecutionResult(
                    case_id=fixture.case_id,
                    task_id=fixture.task_id,
                    subtask_id=fixture.subtask_id,
                    group=fixture.group,
                    candidate_id=candidate.candidate_id,
                    provider_id=candidate.provider_id,
                    exact_model_id=candidate.exact_model_id,
                    latency_ms=0,
                    syntactic_valid=False,
                    semantic_valid=False,
                    safety_valid=False,
                    overall_pass=False,
                    failure_categories=["RUN_REQUEST_CAP_REACHED"],
                    failure_reasons=[
                        f"Per-run request ceiling of {self.max_requests} reached. Case not dispatched."
                    ],
                    sqlite_reservation_status="PRE_DISPATCH_ABORT",
                    spend_provenance=spend_prov,
                    estimated_spend_usd=est_spend,
                    lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
                )

            if est_spend > self.max_cost_usd:
                raise LLMBudgetExceededError(
                    f"Projected request spend ${est_spend:.6f} exceeds per-run cost ceiling of ${self.max_cost_usd:.6f}."
                )

            cur_run_spend = self._get_current_run_spend(run_id)
            if cur_run_spend + est_spend > self.max_cost_usd:
                raise LLMBudgetExceededError(
                    f"Projected cumulative run spend (${cur_run_spend:.6f} + ${est_spend:.6f} = "
                    f"${cur_run_spend + est_spend:.6f}) exceeds per-run cost ceiling of ${self.max_cost_usd:.6f}."
                )

        # Check campaign guard pre-dispatch reservation
        if self.campaign_guard is not None:
            try:
                self.campaign_guard.check_and_reserve(
                    request_id=req_id,
                    candidate_id=candidate.candidate_id,
                    provider_id=candidate.provider_id,
                    estimated_commercial_usd=est_spend,
                    estimated_billable_usd=est_billable,
                )
            except Exception as e:
                return BenchmarkCaseExecutionResult(
                    case_id=fixture.case_id,
                    task_id=fixture.task_id,
                    subtask_id=fixture.subtask_id,
                    group=fixture.group,
                    candidate_id=candidate.candidate_id,
                    provider_id=candidate.provider_id,
                    exact_model_id=candidate.exact_model_id,
                    latency_ms=0,
                    syntactic_valid=False,
                    semantic_valid=False,
                    safety_valid=False,
                    overall_pass=False,
                    failure_categories=["PRE_DISPATCH_BUDGET_BLOCKED"],
                    failure_reasons=[
                        f"Campaign budget guard blocked dispatch: {str(e)}"
                    ],
                    sqlite_reservation_status="PRE_DISPATCH_ABORT",
                    spend_provenance=spend_prov,
                    estimated_spend_usd=est_spend,
                    lifecycle_status=CaseLifecycleStatus.PRE_DISPATCH_BLOCKED,
                )

        # Defect 4: Validate pre-network Gateway / candidate / provider requirements
        # before marking dispatch attempt on the network
        try:
            if not self.dry_run:
                self.gateway._resolve_provider(request)
            target_cand = candidate or self.gateway._resolve_candidate(request)
            if not target_cand:
                raise LLMNotConfiguredError(f"Cannot resolve candidate for task '{request.task_id}'.")
            if self.runtime_guard:
                subtask_id = request.metadata.get("subtask_id") if request.metadata else None
                self.runtime_guard._validate_candidate_binding(request, target_cand, subtask_id=subtask_id)
        except Exception as e:
            if self.campaign_guard is not None:
                self.campaign_guard.cancel(req_id, reason=f"PRE_DISPATCH_VALIDATION_ERROR: {e}")
            raise

        start_time = time.perf_counter()
        try:
            self._attempted_requests_count += 1
            if self.campaign_guard is not None:
                self.campaign_guard.record_dispatch_attempt(req_id)
            # Dispatch through LLMProviderGateway with candidate binding
            response = self.gateway.generate(
                request=request,
                candidate=candidate,
                is_benchmark=True,
            )
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        except (LLMBudgetExceededError, LLMNotConfiguredError, LLMCandidateMismatchError) as e:
            # Pre-network configuration / policy failure prior to network submission
            if self.campaign_guard is not None:
                self.campaign_guard.cancel(req_id, reason=f"PRE_DISPATCH_CONFIGURATION_ERROR: {e}", allow_pre_network=True)
            raise
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            is_timeout = isinstance(e, LLMTimeoutError)
            is_uncertain = isinstance(e, LLMUncertainUsageError) or "UNCERTAIN" in type(e).__name__ or is_timeout
            is_rate_limit = (
                isinstance(e, LLMQuotaExceededError)
                or "429" in str(e)
                or "rate limit" in str(e).lower()
                or "quota" in str(e).lower()
            )
            is_auth = (
                isinstance(e, LLMAuthenticationError)
                or isinstance(e, LLMPermissionError)
                or "401" in str(e)
                or "403" in str(e)
                or "unauthorized" in str(e).lower()
            )

            if is_rate_limit:
                fail_cats = ["RATE_LIMIT"]
            elif is_auth:
                fail_cats = ["AUTHENTICATION_ERROR", "GATEWAY_DISPATCH_ERROR"]
            elif is_timeout:
                cat = getattr(e, "timeout_category", "REQUEST_TIMEOUT")
                fail_cats = [f"TIMEOUT_{cat}", "GATEWAY_DISPATCH_ERROR"]
            else:
                fail_cats = ["UNCERTAIN_USAGE" if is_uncertain else "GATEWAY_DISPATCH_ERROR"]

            if self.campaign_guard is not None:
                if is_uncertain:
                    # Keep conservative uncertain liability reserved
                    self.campaign_guard.reconcile(
                        request_id=req_id,
                        actual_commercial_usd=est_spend,
                        actual_billable_usd=est_billable,
                        is_uncertain=True,
                        failure_reason=str(e),
                    )
                else:
                    # Request was dispatched across network; mark DISPATCH_FAILED, never cancel
                    self.campaign_guard.mark_failed(req_id, failure_reason=str(e))

            return BenchmarkCaseExecutionResult(
                case_id=fixture.case_id,
                task_id=fixture.task_id,
                subtask_id=fixture.subtask_id,
                group=fixture.group,
                candidate_id=candidate.candidate_id,
                provider_id=candidate.provider_id,
                exact_model_id=candidate.exact_model_id,
                latency_ms=elapsed_ms,
                syntactic_valid=False,
                semantic_valid=False,
                safety_valid=False,
                overall_pass=False,
                failure_categories=fail_cats,
                failure_reasons=[str(e)],
                cost_usd=est_spend if is_uncertain else 0.0,
                sqlite_reservation_status="UNCERTAIN" if is_uncertain else "FAILED",
                spend_provenance=spend_prov,
                estimated_spend_usd=est_spend,
                lifecycle_status=CaseLifecycleStatus.DISPATCH_FAILED,
            )

        # -----------------------------------------------------------------------
        # Scoring Phase: Output Validation & Ground-Truth Evaluator
        # -----------------------------------------------------------------------
        approved_evidence = set(fixture.required_evidence_ids) if fixture.required_evidence_ids else None
        local_signals = {"opt_out": is_opt_out} if is_opt_out else None

        val_result = self.validator.validate(
            request=request,
            response=response,
            approved_evidence_ids=approved_evidence,
            local_signals=local_signals,
            subtask_id=fixture.subtask_id,
        )

        eval_result = GroundTruthEvaluator.evaluate(
            fixture=fixture,
            request=request,
            response=response,
            val_result=val_result,
            candidate=candidate,
        )

        raw_snippet = (response.content or "")[:200].replace("\n", " ")

        sqlite_status = (
            "RECONCILED"
            if (response.input_tokens is not None and response.output_tokens is not None)
            else "UNCERTAIN"
        )

        comm_cost = response.cost_usd
        bill_cost = 0.0 if is_trial_or_free else (comm_cost or 0.0)

        if self.campaign_guard is not None:
            self.campaign_guard.reconcile(
                request_id=req_id,
                actual_commercial_usd=comm_cost or 0.0,
                actual_billable_usd=bill_cost,
                is_uncertain=(sqlite_status == "UNCERTAIN"),
            )

        return BenchmarkCaseExecutionResult(
            case_id=fixture.case_id,
            task_id=fixture.task_id,
            subtask_id=fixture.subtask_id,
            group=fixture.group,
            candidate_id=candidate.candidate_id,
            provider_id=candidate.provider_id,
            exact_model_id=candidate.exact_model_id,
            latency_ms=elapsed_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=comm_cost,
            commercial_cost_usd=comm_cost,
            billable_cost_usd=bill_cost,
            syntactic_valid=eval_result.schema_pass,
            semantic_valid=eval_result.ground_truth_pass and eval_result.evidence_pass,
            safety_valid=eval_result.safety_pass,
            overall_pass=eval_result.overall_pass,
            schema_pass=eval_result.schema_pass,
            ground_truth_pass=eval_result.ground_truth_pass,
            evidence_pass=eval_result.evidence_pass,
            safety_pass=eval_result.safety_pass,
            needs_review=eval_result.needs_review,
            failure_categories=eval_result.failure_categories,
            failure_reasons=eval_result.failure_reasons,
            grounding_evidence_found=eval_result.grounding_evidence_found,
            forbidden_tokens_found=eval_result.forbidden_tokens_found,
            raw_output_snippet=raw_snippet,
            validation_safe_log=val_result.safe_log_dict(),
            sqlite_reservation_status=sqlite_status,
            spend_provenance=spend_prov,
            estimated_spend_usd=est_spend,
            lifecycle_status=CaseLifecycleStatus.EVALUATED,
        )
