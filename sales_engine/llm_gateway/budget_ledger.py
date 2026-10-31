"""
sales_engine/llm_gateway/budget_ledger.py
Lightweight local SQLite API-usage and budget ledger for SalesAI LLM Gateway.
Enforces daily, monthly, and per-request spending ceilings with atomic preflight
reservation and post-response reconciliation against verified pricing.
Zero PII, prompts, completions, credentials, or raw operational data in ledger.
"""

import math
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

from pydantic import BaseModel, Field, ConfigDict, model_validator

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, sanitize_log_str
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMNotConfiguredError,
    LLMUncertainUsageError,
    LLMDuplicateRequestError,
    LLMCandidateMismatchError,
)
from sales_engine.llm_gateway.model_catalog import ModelCandidate


class ReservationStatus(str, Enum):
    """Lifecycle states of a budget reservation."""

    RESERVED = "RESERVED"
    RECONCILED = "RECONCILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


class BudgetPolicyConfig(BaseModel):
    """
    Configuration governing spending ceilings, concurrency, and rate controls.
    Strictly disabled by default (fail-closed).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for budget enforcement and paid dispatch. Default is False (fail-closed).",
    )
    daily_limit_usd: float = Field(
        default=1.0,
        gt=0.0,
        description="Maximum cumulative USD spend allowed per calendar day (UTC).",
    )
    monthly_limit_usd: float = Field(
        default=15.0,
        gt=0.0,
        description="Maximum cumulative USD spend allowed per calendar month (UTC).",
    )
    max_request_spend_usd: float = Field(
        default=0.10,
        gt=0.0,
        description="Maximum projected preflight USD spend allowed for a single request.",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=300.0,
        description="Default request timeout in seconds.",
    )
    max_concurrent_requests: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Maximum concurrent external LLM requests allowed locally.",
    )
    cooldown_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=60.0,
        description="Minimum quiet interval in seconds between consecutive calls to the same provider.",
    )
    chars_per_token_estimate: float = Field(
        default=3.0,
        gt=0.5,
        le=10.0,
        description="Conservative characters-to-token divisor for input token ceiling estimation.",
    )
    stale_reservation_seconds: float = Field(
        default=300.0,
        gt=10.0,
        description="Age threshold in seconds after which an uncompleted reservation is considered stale.",
    )

    @model_validator(mode="after")
    def validate_limits(self) -> "BudgetPolicyConfig":
        if self.daily_limit_usd > self.monthly_limit_usd:
            raise ValueError(
                f"daily_limit_usd (${self.daily_limit_usd:.2f}) cannot exceed "
                f"monthly_limit_usd (${self.monthly_limit_usd:.2f})"
            )
        if self.max_request_spend_usd > self.daily_limit_usd:
            raise ValueError(
                f"max_request_spend_usd (${self.max_request_spend_usd:.2f}) cannot exceed "
                f"daily_limit_usd (${self.daily_limit_usd:.2f})"
            )
        return self

    @classmethod
    def from_env(cls) -> "BudgetPolicyConfig":
        """Load budget policy configuration from environment variables with safe fail-closed defaults."""
        raw_enabled = os.environ.get("SALESAI_BUDGET_ENABLED", "").lower()
        is_enabled = raw_enabled in ("1", "true", "yes")

        def _get_float(key: str, default: float) -> float:
            val = os.environ.get(key)
            return float(val) if val else default

        def _get_int(key: str, default: int) -> int:
            val = os.environ.get(key)
            return int(val) if val else default

        return cls(
            enabled=is_enabled,
            daily_limit_usd=_get_float("SALESAI_BUDGET_DAILY_LIMIT_USD", 1.0),
            monthly_limit_usd=_get_float("SALESAI_BUDGET_MONTHLY_LIMIT_USD", 15.0),
            max_request_spend_usd=_get_float("SALESAI_BUDGET_MAX_REQUEST_SPEND_USD", 0.10),
            request_timeout_seconds=_get_float("SALESAI_BUDGET_TIMEOUT_SECONDS", 30.0),
            max_concurrent_requests=_get_int("SALESAI_BUDGET_MAX_CONCURRENCY", 1),
            cooldown_seconds=_get_float("SALESAI_BUDGET_COOLDOWN_SECONDS", 0.5),
        )


class BudgetReservation(BaseModel):
    """Immutable snapshot of a ledger reservation record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: str = Field(..., min_length=1, max_length=64)
    request_id: str = Field(..., min_length=1, max_length=64)
    task_id: str = Field(..., min_length=1, max_length=64)
    subtask_id: Optional[str] = Field(default=None, max_length=64)
    provider_id: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    created_at: str = Field(..., max_length=32)
    updated_at: str = Field(..., max_length=32)
    year_month: str = Field(..., max_length=7)
    date_day: str = Field(..., max_length=10)
    input_tokens_cap: int = Field(..., ge=0)
    output_tokens_cap: int = Field(..., ge=0)
    reserved_usd: float = Field(..., ge=0.0)
    reported_input_tokens: Optional[int] = Field(default=None, ge=0)
    reported_output_tokens: Optional[int] = Field(default=None, ge=0)
    final_cost_usd: Optional[float] = Field(default=None, ge=0.0)
    status: ReservationStatus = Field(default=ReservationStatus.RESERVED)
    failure_code: Optional[str] = Field(default=None, max_length=64)
    pricing_version: Optional[str] = Field(default=None, max_length=128)
    billable_cost_usd: Optional[float] = Field(default=None, ge=0.0)
    spend_provenance: Optional[str] = Field(default=None, max_length=64)


class BudgetSummary(BaseModel):
    """Operator-visible summary of budget consumption and outstanding reservations."""

    model_config = ConfigDict(extra="forbid")

    date_day: str
    year_month: str
    daily_limit_usd: float
    daily_spent_usd: float
    daily_remaining_usd: float
    monthly_limit_usd: float
    monthly_spent_usd: float
    monthly_remaining_usd: float
    total_reservations_today: int
    total_reservations_month: int
    status_counts_today: Dict[str, int]
    status_counts_month: Dict[str, int]
    total_input_tokens_month: int
    total_output_tokens_month: int
    outstanding_reservations: List[Dict[str, Any]]


class BudgetLedger:
    """
    Lightweight, thread-safe SQLite ledger governing API spending reservations and reconciliation.
    Guarantees:
    - Zero prompts, zero completions, zero secrets stored in database.
    - Atomic preflight budget reservation against daily and monthly limits.
    - Fail-closed on missing/unknown pricing or disabled policy.
    - Missing or unmeasured token usage is never treated as free ($0.00).
    - Idempotent reservations per request_id.
    - Operator review workflow for stale reservations.
    """

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        config: Optional[BudgetPolicyConfig] = None,
    ):
        self.config = config or BudgetPolicyConfig()
        if db_path is None:
            self.db_path = Path("data/llm_usage.db")
        elif str(db_path) == ":memory:":
            self.db_path = Path(":memory:")
        else:
            self.db_path = Path(db_path)

        self._lock = threading.Lock()
        self._is_memory = str(self.db_path) == ":memory:"
        self._mem_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            # Persistent connection for in-memory DB so schema and records survive calls
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._init_schema(self._mem_conn)
        else:
            if not self.db_path.parent.exists():
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._get_connection() as conn:
                self._init_schema(conn)

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory and self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    subtask_id TEXT,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    year_month TEXT NOT NULL,
                    date_day TEXT NOT NULL,
                    input_tokens_cap INTEGER NOT NULL,
                    output_tokens_cap INTEGER NOT NULL,
                    reserved_usd REAL NOT NULL,
                    reported_input_tokens INTEGER,
                    reported_output_tokens INTEGER,
                    final_cost_usd REAL,
                    status TEXT NOT NULL,
                    failure_code TEXT,
                    pricing_version TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_request_id ON llm_reservations(request_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_date_day ON llm_reservations(date_day, status);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_res_year_month ON llm_reservations(year_month, status);"
            )
            # Backward-compatible additive migration for S3.5.4C dual cost accounting
            cursor = conn.execute("PRAGMA table_info(llm_reservations);")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "billable_cost_usd" not in existing_cols:
                conn.execute("ALTER TABLE llm_reservations ADD COLUMN billable_cost_usd REAL;")
            if "spend_provenance" not in existing_cols:
                conn.execute("ALTER TABLE llm_reservations ADD COLUMN spend_provenance TEXT;")

    @staticmethod
    def _row_to_reservation(row: tuple) -> BudgetReservation:
        billable_cost = row[19] if len(row) > 19 else None
        spend_prov = row[20] if len(row) > 20 else None
        return BudgetReservation(
            reservation_id=row[0],
            request_id=row[1],
            task_id=row[2],
            subtask_id=row[3],
            provider_id=row[4],
            model=row[5],
            created_at=row[6],
            updated_at=row[7],
            year_month=row[8],
            date_day=row[9],
            input_tokens_cap=row[10],
            output_tokens_cap=row[11],
            reserved_usd=row[12],
            reported_input_tokens=row[13],
            reported_output_tokens=row[14],
            final_cost_usd=row[15],
            status=ReservationStatus(row[16]),
            failure_code=row[17],
            pricing_version=row[18],
            billable_cost_usd=billable_cost,
            spend_provenance=spend_prov,
        )

    def reserve(
        self,
        request: LLMRequest,
        candidate: ModelCandidate,
        subtask_id: Optional[str] = None,
        declared_input_tokens: Optional[int] = None,
        current_time: Optional[datetime] = None,
    ) -> BudgetReservation:
        """
        Atomically reserve funds for a projected LLM request against daily and monthly limits.
        Fails closed if:
        - Budget policy is disabled.
        - Candidate has no verified pricing metadata or incomplete rates.
        - Projected request spend exceeds max_request_spend_usd.
        - Projected request spend would exceed remaining daily or monthly budget limits.
        """
        if not self.config.enabled:
            raise LLMNotConfiguredError(
                message="SalesAI LLM budget enforcement is disabled or unconfigured (fail-closed). "
                "Paid model dispatch is not authorized.",
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        # A2 Defense: Validate candidate eligibility for task
        if not candidate.is_task_allowed(request.task_id):
            raise LLMCandidateMismatchError(
                message=f"Candidate '{candidate.candidate_id}' is not authorized for task '{request.task_id}'.",
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        # A2 Defense: Validate exact provider, model, tier equality if specified on request
        if request.provider is not None and request.provider != candidate.provider_id:
            raise LLMCandidateMismatchError(
                message=(
                    f"Pricing/candidate identity mismatch: request.provider '{request.provider}' "
                    f"does not match candidate provider_id '{candidate.provider_id}'."
                ),
                provider=candidate.provider_id,
                task_id=request.task_id,
            )
        if request.model is not None and request.model != candidate.exact_model_id:
            raise LLMCandidateMismatchError(
                message=(
                    f"Pricing/candidate identity mismatch: request.model '{request.model}' "
                    f"does not match candidate exact_model_id '{candidate.exact_model_id}'."
                ),
                provider=candidate.provider_id,
                task_id=request.task_id,
            )
        if request.tier is not None and request.tier != candidate.tier:
            raise LLMCandidateMismatchError(
                message=(
                    f"Pricing/candidate identity mismatch: request.tier '{request.tier}' "
                    f"does not match candidate tier '{candidate.tier}'."
                ),
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        # Bind candidate identity to request to eliminate dispatch divergence
        if request.provider is None:
            request.provider = candidate.provider_id
        if request.model is None:
            request.model = candidate.exact_model_id
        if request.tier is None:
            request.tier = candidate.tier

        # Verify candidate pricing metadata
        pricing = candidate.pricing
        if (
            pricing is None
            or pricing.input_per_1m is None
            or pricing.output_per_1m is None
            or pricing.currency not in ("USD", "FREE")
        ):
            raise LLMNotConfiguredError(
                message=f"Candidate '{candidate.candidate_id}' ({candidate.exact_model_id}) "
                "has missing or unverified USD/FREE pricing metadata. Cannot safely reserve budget (fail-closed).",
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        # Conservative input token cap
        if declared_input_tokens is not None and declared_input_tokens > 0:
            input_tokens_cap = declared_input_tokens
        else:
            prompt_chars = len(request.prompt) + (
                len(request.system_prompt) if request.system_prompt else 0
            )
            input_tokens_cap = max(
                1, math.ceil(prompt_chars / self.config.chars_per_token_estimate)
            )

        output_tokens_cap = request.max_output_tokens

        # Conservative projected reservation in USD
        projected_spend = (
            input_tokens_cap * pricing.input_per_1m
            + output_tokens_cap * pricing.output_per_1m
        ) / 1_000_000.0
        projected_spend = round(projected_spend, 6)

        # Enforce per-request spend ceiling
        if projected_spend > self.config.max_request_spend_usd:
            raise LLMBudgetExceededError(
                message=(
                    f"Projected request spend ${projected_spend:.5f} exceeds per-request cap "
                    f"${self.config.max_request_spend_usd:.5f} for model '{candidate.exact_model_id}'."
                ),
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        now = current_time or datetime.now(timezone.utc)
        date_day = now.strftime("%Y-%m-%d")
        year_month = now.strftime("%Y-%m")
        now_iso = now.isoformat()

        pricing_ver = (
            f"{pricing.currency}:{pricing.input_per_1m:.4f}/{pricing.output_per_1m:.4f}"
            f":{pricing.effective_date or 'unversioned'}"
        )

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    # A1 Defense: Idempotency check across all lifecycle statuses
                    cursor = conn.execute(
                        """
                        SELECT reservation_id, request_id, task_id, subtask_id, provider_id, model,
                               created_at, updated_at, year_month, date_day, input_tokens_cap,
                               output_tokens_cap, reserved_usd, reported_input_tokens,
                               reported_output_tokens, final_cost_usd, status, failure_code,
                               pricing_version, billable_cost_usd, spend_provenance
                        FROM llm_reservations
                        WHERE request_id = ?
                        LIMIT 1;
                        """,
                        (request.request_id,),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        existing_status = existing[16]
                        if existing_status == ReservationStatus.RESERVED.value:
                            return self._row_to_reservation(existing)
                        else:
                            raise LLMDuplicateRequestError(
                                message=(
                                    f"Duplicate request_id '{request.request_id}' has already been processed "
                                    f"with status '{existing_status}'. A new request_id is required."
                                ),
                                provider=candidate.provider_id,
                                task_id=request.task_id,
                            )

                    # Calculate current daily spend (RESERVED, RECONCILED, UNCERTAIN)
                    cur_day = conn.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                        FROM llm_reservations
                        WHERE date_day = ? AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                        """,
                        (date_day,),
                    ).fetchone()[0]

                    if cur_day + projected_spend > self.config.daily_limit_usd:
                        raise LLMBudgetExceededError(
                            message=(
                                f"Daily LLM budget exceeded: current daily spend ${cur_day:.4f} + "
                                f"projected ${projected_spend:.4f} exceeds daily limit "
                                f"${self.config.daily_limit_usd:.4f}."
                            ),
                            provider=candidate.provider_id,
                            task_id=request.task_id,
                        )

                    # Calculate current monthly spend (RESERVED, RECONCILED, UNCERTAIN)
                    cur_month = conn.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                        FROM llm_reservations
                        WHERE year_month = ? AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                        """,
                        (year_month,),
                    ).fetchone()[0]

                    if cur_month + projected_spend > self.config.monthly_limit_usd:
                        raise LLMBudgetExceededError(
                            message=(
                                f"Monthly LLM budget exceeded: current monthly spend ${cur_month:.4f} + "
                                f"projected ${projected_spend:.4f} exceeds monthly limit "
                                f"${self.config.monthly_limit_usd:.4f}."
                            ),
                            provider=candidate.provider_id,
                            task_id=request.task_id,
                        )

                    # Determine spend provenance
                    spend_prov = None
                    if request.metadata and isinstance(request.metadata, dict):
                        spend_prov = request.metadata.get("spend_provenance")
                    if spend_prov is None:
                        if pricing.currency == "FREE":
                            spend_prov = "VERIFIED_FREE_TIER"
                        elif candidate.provider_id == "cohere" and "trial" in (candidate.verification_notes or "").lower():
                            spend_prov = "VERIFIED_COHERE_TRIAL"
                        else:
                            spend_prov = "VERIFIED_PAID"

                    reservation_id = f"res_{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO llm_reservations (
                            reservation_id, request_id, task_id, subtask_id, provider_id, model,
                            created_at, updated_at, year_month, date_day, input_tokens_cap,
                            output_tokens_cap, reserved_usd, reported_input_tokens,
                            reported_output_tokens, final_cost_usd, status, failure_code,
                            pricing_version, billable_cost_usd, spend_provenance
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, ?, NULL, ?);
                        """,
                        (
                            reservation_id,
                            request.request_id,
                            request.task_id,
                            subtask_id,
                            candidate.provider_id,
                            candidate.exact_model_id,
                            now_iso,
                            now_iso,
                            year_month,
                            date_day,
                            input_tokens_cap,
                            output_tokens_cap,
                            projected_spend,
                            ReservationStatus.RESERVED.value,
                            pricing_ver,
                            spend_prov,
                        ),
                    )

                return BudgetReservation(
                    reservation_id=reservation_id,
                    request_id=request.request_id,
                    task_id=request.task_id,
                    subtask_id=subtask_id,
                    provider_id=candidate.provider_id,
                    model=candidate.exact_model_id,
                    created_at=now_iso,
                    updated_at=now_iso,
                    year_month=year_month,
                    date_day=date_day,
                    input_tokens_cap=input_tokens_cap,
                    output_tokens_cap=output_tokens_cap,
                    reserved_usd=projected_spend,
                    status=ReservationStatus.RESERVED,
                    pricing_version=pricing_ver,
                    billable_cost_usd=None,
                    spend_provenance=spend_prov,
                )
            finally:
                if not self._is_memory:
                    conn.close()

    def reconcile_response(
        self,
        reservation_id: str,
        response: LLMResponse,
        candidate: ModelCandidate,
        current_time: Optional[datetime] = None,
    ) -> BudgetReservation:
        """
        Reconcile a preflight reservation against actual reported token usage.
        - Calculates price solely from verified pricing metadata.
        - Missing or ambiguous usage is never treated as free ($0); reservation is retained
          against budget and flagged as UNCERTAIN for operator review.
        - Updates response.cost_usd with verified calculated spend.
        - Idempotent: repeated calls on already-reconciled records return current state.
        """
        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()

        pricing = candidate.pricing
        if (
            pricing is None
            or pricing.input_per_1m is None
            or pricing.output_per_1m is None
        ):
            raise LLMNotConfiguredError(
                message=f"Cannot reconcile reservation '{reservation_id}': verified pricing missing.",
                provider=candidate.provider_id,
                task_id=response.task_id,
            )

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        SELECT reservation_id, request_id, task_id, subtask_id, provider_id, model,
                               created_at, updated_at, year_month, date_day, input_tokens_cap,
                               output_tokens_cap, reserved_usd, reported_input_tokens,
                               reported_output_tokens, final_cost_usd, status, failure_code,
                               pricing_version, billable_cost_usd, spend_provenance
                        FROM llm_reservations
                        WHERE reservation_id = ?;
                        """,
                        (reservation_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise ValueError(f"Reservation '{reservation_id}' not found in ledger.")

                    current_status = row[16]
                    reserved_usd = row[12]

                    # Idempotency check: if already reconciled, return existing record
                    if current_status == ReservationStatus.RECONCILED.value:
                        return self._row_to_reservation(row)

                    if current_status == ReservationStatus.CANCELLED.value:
                        raise ValueError(
                            f"Cannot reconcile cancelled reservation '{reservation_id}'."
                        )

                    # Check for missing, negative, or unmeasured token counts
                    if (
                        response.input_tokens is None
                        or response.output_tokens is None
                        or response.input_tokens < 0
                        or response.output_tokens < 0
                    ):
                        # Flag as UNCERTAIN: retain reserved_usd against budget; never $0.00
                        conn.execute(
                            """
                            UPDATE llm_reservations
                            SET status = ?, failure_code = ?, updated_at = ?,
                                final_cost_usd = ?
                            WHERE reservation_id = ?;
                            """,
                            (
                                ReservationStatus.UNCERTAIN.value,
                                "USAGE_MISSING",
                                now_iso,
                                reserved_usd,
                                reservation_id,
                            ),
                        )
                        conn.commit()
                        raise LLMUncertainUsageError(
                            message=(
                                f"Provider response for reservation '{reservation_id}' lacked verified "
                                f"token usage counts. Retained projected reservation ${reserved_usd:.5f} "
                                "against budget; flagged for operator review."
                            ),
                            provider=candidate.provider_id,
                            task_id=response.task_id,
                        )

                    # Verified actual cost calculation (FREE tier reconciles to $0.000000 actual spend)
                    if pricing.currency == "FREE":
                        commercial_cost = 0.0
                    else:
                        commercial_cost = (
                            response.input_tokens * pricing.input_per_1m
                            + response.output_tokens * pricing.output_per_1m
                        ) / 1_000_000.0
                        commercial_cost = round(commercial_cost, 6)

                    # Determine actual external billable spend
                    row_spend_prov = row[20] if len(row) > 20 else None
                    if (
                        row_spend_prov in ("VERIFIED_COHERE_TRIAL", "VERIFIED_FREE_TIER")
                        or pricing.currency == "FREE"
                    ):
                        billable_cost = 0.0
                    else:
                        billable_cost = commercial_cost

                    # Set cost on response object (commercial-equivalent preserved for comparisons)
                    response.cost_usd = commercial_cost

                    conn.execute(
                        """
                        UPDATE llm_reservations
                        SET status = ?, reported_input_tokens = ?, reported_output_tokens = ?,
                            final_cost_usd = ?, billable_cost_usd = ?, updated_at = ?, failure_code = NULL
                        WHERE reservation_id = ?;
                        """,
                        (
                            ReservationStatus.RECONCILED.value,
                            response.input_tokens,
                            response.output_tokens,
                            commercial_cost,
                            billable_cost,
                            now_iso,
                            reservation_id,
                        ),
                    )

                    # Return updated snapshot
                    cursor = conn.execute(
                        """
                        SELECT reservation_id, request_id, task_id, subtask_id, provider_id, model,
                               created_at, updated_at, year_month, date_day, input_tokens_cap,
                               output_tokens_cap, reserved_usd, reported_input_tokens,
                               reported_output_tokens, final_cost_usd, status, failure_code,
                               pricing_version, billable_cost_usd, spend_provenance
                        FROM llm_reservations
                        WHERE reservation_id = ?;
                        """,
                        (reservation_id,),
                    )
                    return self._row_to_reservation(cursor.fetchone())
            finally:
                if not self._is_memory:
                    conn.close()

    def cancel_reservation(
        self,
        reservation_id: str,
        reason_code: str = "PRE_DISPATCH_ABORT",
        current_time: Optional[datetime] = None,
    ) -> bool:
        """
        Safely cancel a preflight reservation ONLY when no external provider call was dispatched.
        Zeroes out spend liability and releases reservation from budget calculations.
        Returns True if cancelled, False if already reconciled/cancelled.
        """
        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        safe_reason = sanitize_log_str(reason_code, max_len=64) or "CANCELLED"

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        "SELECT status FROM llm_reservations WHERE reservation_id = ?;",
                        (reservation_id,),
                    )
                    row = cursor.fetchone()
                    if not row or row[0] != ReservationStatus.RESERVED.value:
                        return False

                    conn.execute(
                        """
                        UPDATE llm_reservations
                        SET status = ?, failure_code = ?, final_cost_usd = 0.0, updated_at = ?
                        WHERE reservation_id = ?;
                        """,
                        (
                            ReservationStatus.CANCELLED.value,
                            safe_reason,
                            now_iso,
                            reservation_id,
                        ),
                    )
                    return True
            finally:
                if not self._is_memory:
                    conn.close()

    def mark_reservation_failed(
        self,
        reservation_id: str,
        error_code: str = "TRANSPORT_ERROR",
        dispatched: bool = False,
        current_time: Optional[datetime] = None,
    ) -> None:
        """
        Record a failure on an outstanding reservation.
        - If dispatched is False: safe pre-dispatch cancellation (releases budget).
        - If dispatched is True: request was transmitted across the network. Cannot assume
          provider did not execute; retains reserved_usd against budget and marks UNCERTAIN.
        """
        if not dispatched:
            self.cancel_reservation(reservation_id, reason_code=error_code, current_time=current_time)
            return

        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        safe_code = sanitize_log_str(error_code, max_len=64) or "DISPATCH_FAILED"

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        "SELECT status, reserved_usd FROM llm_reservations WHERE reservation_id = ?;",
                        (reservation_id,),
                    )
                    row = cursor.fetchone()
                    if not row or row[0] != ReservationStatus.RESERVED.value:
                        return

                    reserved_usd = row[1]
                    conn.execute(
                        """
                        UPDATE llm_reservations
                        SET status = ?, failure_code = ?, final_cost_usd = ?, updated_at = ?
                        WHERE reservation_id = ?;
                        """,
                        (
                            ReservationStatus.UNCERTAIN.value,
                            safe_code,
                            reserved_usd,
                            now_iso,
                            reservation_id,
                        ),
                    )
            finally:
                if not self._is_memory:
                    conn.close()

    def get_stale_reservations(
        self,
        stale_threshold_seconds: Optional[float] = None,
        current_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Inspect reservations that remain in RESERVED state beyond the stale threshold.
        Does NOT automatically refund or delete them. Returns non-sensitive operational metadata.
        """
        threshold = (
            stale_threshold_seconds
            if stale_threshold_seconds is not None
            else self.config.stale_reservation_seconds
        )
        now = current_time or datetime.now(timezone.utc)
        now_ts = now.timestamp()

        stale_list = []
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    """
                    SELECT reservation_id, request_id, task_id, subtask_id, provider_id, model,
                           created_at, reserved_usd, status
                    FROM llm_reservations
                    WHERE status = 'RESERVED';
                    """
                )
                for row in cursor.fetchall():
                    try:
                        created_dt = datetime.fromisoformat(row[6])
                        age_sec = now_ts - created_dt.timestamp()
                        if age_sec >= threshold:
                            stale_list.append(
                                {
                                    "reservation_id": row[0],
                                    "request_id": row[1],
                                    "task_id": row[2],
                                    "subtask_id": row[3],
                                    "provider_id": row[4],
                                    "model": row[5],
                                    "created_at": row[6],
                                    "reserved_usd": row[7],
                                    "age_seconds": round(age_sec, 1),
                                    "status": row[8],
                                }
                            )
                    except Exception:
                        continue
            finally:
                if not self._is_memory:
                    conn.close()

        return stale_list

    def operator_review_stale(
        self,
        reservation_id: str,
        action: str,
        operator_id: str,
        final_cost_usd: Optional[float] = None,
        notes: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Explicit operator review of an unresolved or stale reservation.
        Allowed actions:
        - 'reconcile': explicitly reconcile with an operator-supplied verified spend.
        - 'cancel': explicitly cancel and release budget (verified provider never ran).
        - 'retain_uncertain': mark as confirmed uncertain and keep spend reserved.
        """
        if not operator_id or not operator_id.strip():
            raise ValueError("Authenticated operator_id is required for budget review.")

        valid_actions = {"reconcile", "cancel", "retain_uncertain"}
        if action not in valid_actions:
            raise ValueError(f"Invalid review action '{action}'. Must be one of {valid_actions}")

        now = current_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        safe_notes = sanitize_log_str(notes, max_len=128)

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        "SELECT status, reserved_usd FROM llm_reservations WHERE reservation_id = ?;",
                        (reservation_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise ValueError(f"Reservation '{reservation_id}' not found.")

                    current_status, reserved_usd = row[0], row[1]

                    if action == "cancel":
                        conn.execute(
                            """
                            UPDATE llm_reservations
                            SET status = ?, failure_code = ?, final_cost_usd = 0.0, updated_at = ?
                            WHERE reservation_id = ?;
                            """,
                            (
                                ReservationStatus.CANCELLED.value,
                                f"OPERATOR_CANCELLED:{sanitize_log_str(operator_id)}",
                                now_iso,
                                reservation_id,
                            ),
                        )
                        new_status = ReservationStatus.CANCELLED.value
                        new_cost = 0.0
                    elif action == "reconcile":
                        if final_cost_usd is None or final_cost_usd < 0.0:
                            raise ValueError("reconcile action requires non-negative final_cost_usd.")
                        cost = round(final_cost_usd, 6)
                        conn.execute(
                            """
                            UPDATE llm_reservations
                            SET status = ?, failure_code = ?, final_cost_usd = ?, updated_at = ?
                            WHERE reservation_id = ?;
                            """,
                            (
                                ReservationStatus.RECONCILED.value,
                                f"OPERATOR_RECONCILED:{sanitize_log_str(operator_id)}",
                                cost,
                                now_iso,
                                reservation_id,
                            ),
                        )
                        new_status = ReservationStatus.RECONCILED.value
                        new_cost = cost
                    else:  # retain_uncertain
                        conn.execute(
                            """
                            UPDATE llm_reservations
                            SET status = ?, failure_code = ?, final_cost_usd = ?, updated_at = ?
                            WHERE reservation_id = ?;
                            """,
                            (
                                ReservationStatus.UNCERTAIN.value,
                                f"OPERATOR_CONFIRMED_UNCERTAIN:{sanitize_log_str(operator_id)}",
                                reserved_usd,
                                now_iso,
                                reservation_id,
                            ),
                        )
                        new_status = ReservationStatus.UNCERTAIN.value
                        new_cost = reserved_usd

                    return {
                        "reservation_id": reservation_id,
                        "previous_status": current_status,
                        "new_status": new_status,
                        "final_cost_usd": new_cost,
                        "reviewed_by": operator_id,
                        "reviewed_at": now_iso,
                        "notes": safe_notes,
                    }
            finally:
                if not self._is_memory:
                    conn.close()

    def get_budget_summary(
        self,
        current_time: Optional[datetime] = None,
    ) -> BudgetSummary:
        """
        Local-only, read-only operator summary of daily/monthly spend, limits, and outstanding reservations.
        Contains zero PII, zero prompt content, and zero credentials.
        """
        now = current_time or datetime.now(timezone.utc)
        date_day = now.strftime("%Y-%m-%d")
        year_month = now.strftime("%Y-%m")

        with self._lock:
            conn = self._get_connection()
            try:
                # Daily spend
                daily_spent = conn.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                    FROM llm_reservations
                    WHERE date_day = ? AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                    """,
                    (date_day,),
                ).fetchone()[0]
                daily_spent = round(daily_spent, 6)

                # Monthly spend
                monthly_spent = conn.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(final_cost_usd, reserved_usd)), 0.0)
                    FROM llm_reservations
                    WHERE year_month = ? AND status IN ('RESERVED', 'RECONCILED', 'UNCERTAIN');
                    """,
                    (year_month,),
                ).fetchone()[0]
                monthly_spent = round(monthly_spent, 6)

                # Status counts today
                cursor = conn.execute(
                    "SELECT status, COUNT(*) FROM llm_reservations WHERE date_day = ? GROUP BY status;",
                    (date_day,),
                )
                status_counts_today = {row[0]: row[1] for row in cursor.fetchall()}
                total_today = sum(status_counts_today.values())

                # Status counts month
                cursor = conn.execute(
                    "SELECT status, COUNT(*) FROM llm_reservations WHERE year_month = ? GROUP BY status;",
                    (year_month,),
                )
                status_counts_month = {row[0]: row[1] for row in cursor.fetchall()}
                total_month = sum(status_counts_month.values())

                # Token usage month
                token_row = conn.execute(
                    """
                    SELECT COALESCE(SUM(reported_input_tokens), 0),
                           COALESCE(SUM(reported_output_tokens), 0)
                    FROM llm_reservations
                    WHERE year_month = ? AND status = 'RECONCILED';
                    """,
                    (year_month,),
                ).fetchone()
                total_input_tokens = token_row[0]
                total_output_tokens = token_row[1]

                # Outstanding reservations
                cursor = conn.execute(
                    """
                    SELECT reservation_id, request_id, task_id, subtask_id, provider_id, model,
                           created_at, reserved_usd, status
                    FROM llm_reservations
                    WHERE status IN ('RESERVED', 'UNCERTAIN')
                    ORDER BY created_at DESC
                    LIMIT 50;
                    """
                )
                outstanding = [
                    {
                        "reservation_id": r[0],
                        "request_id": r[1],
                        "task_id": r[2],
                        "subtask_id": r[3],
                        "provider_id": r[4],
                        "model": r[5],
                        "created_at": r[6],
                        "reserved_usd": r[7],
                        "status": r[8],
                    }
                    for r in cursor.fetchall()
                ]

                daily_remaining = max(0.0, round(self.config.daily_limit_usd - daily_spent, 6))
                monthly_remaining = max(0.0, round(self.config.monthly_limit_usd - monthly_spent, 6))

                return BudgetSummary(
                    date_day=date_day,
                    year_month=year_month,
                    daily_limit_usd=self.config.daily_limit_usd,
                    daily_spent_usd=daily_spent,
                    daily_remaining_usd=daily_remaining,
                    monthly_limit_usd=self.config.monthly_limit_usd,
                    monthly_spent_usd=monthly_spent,
                    monthly_remaining_usd=monthly_remaining,
                    total_reservations_today=total_today,
                    total_reservations_month=total_month,
                    status_counts_today=status_counts_today,
                    status_counts_month=status_counts_month,
                    total_input_tokens_month=total_input_tokens,
                    total_output_tokens_month=total_output_tokens,
                    outstanding_reservations=outstanding,
                )
            finally:
                if not self._is_memory:
                    conn.close()
