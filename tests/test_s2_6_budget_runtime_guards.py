"""
tests/test_s2_6_budget_runtime_guards.py
Deterministic offline test suite for SalesAI S2.6 Minimal Budget Ledger & API Runtime Guards.
Validates:
1. Default disabled fail-closed behavior.
2. Verified pricing requirements (fail-closed on missing/incomplete pricing).
3. Per-request spend cap, daily limit, and monthly limit enforcement.
4. Atomic preflight reservation and post-response reconciliation.
5. Missing or unmeasured token usage is NEVER treated as $0.00 (flagged UNCERTAIN).
6. Safe pre-dispatch cancellation vs dispatched transport failure (retains budget).
7. Idempotency of reservations and reconciliations.
8. Shared concurrency limiter (sync and async parity).
9. Provider cooldown / rate limit guard.
10. Immediate fail-closed on 429/5xx (zero auto-retries, zero tier escalation).
11. Stale reservation inspection and operator review workflow.
12. Operator read-only budget summary.
13. Zero PII, prompts, completions, or secrets in SQLite database.
14. GuardedGateway execution integration seam.
"""

import asyncio
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    BudgetReservation,
    BudgetSummary,
    ReservationStatus,
)
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMBudgetExceededError,
    LLMConcurrencyLimitError,
    LLMNotConfiguredError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMUncertainUsageError,
)
from sales_engine.llm_gateway.interface import BaseLLMProvider, LLMProviderGateway
from sales_engine.llm_gateway.model_catalog import (
    ModelCandidate,
    ModelCatalog,
    PricingMetadata,
)
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.router import TaskRouteDecision, TaskRouter
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard


class FakeProvider(BaseLLMProvider):
    """Deterministic offline fake provider for S2.6 testing."""

    def __init__(
        self,
        provider_id_val: str = "fake_provider",
        default_input_tokens: int = 100,
        default_output_tokens: int = 50,
        raise_error: Optional[Exception] = None,
        delay_seconds: float = 0.0,
    ):
        self._provider_id = provider_id_val
        self.default_input_tokens = default_input_tokens
        self.default_output_tokens = default_output_tokens
        self.raise_error = raise_error
        self.delay_seconds = delay_seconds
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self.raise_error:
            raise self.raise_error
        return LLMResponse(
            content='{"status": "ok"}',
            provider=self._provider_id,
            model=request.model or "fake-model",
            task_id=request.task_id,
            request_id=request.request_id,
            input_tokens=self.default_input_tokens,
            output_tokens=self.default_output_tokens,
        )

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.raise_error:
            raise self.raise_error
        return LLMResponse(
            content='{"status": "ok"}',
            provider=self._provider_id,
            model=request.model or "fake-model",
            task_id=request.task_id,
            request_id=request.request_id,
            input_tokens=self.default_input_tokens,
            output_tokens=self.default_output_tokens,
        )


@pytest.fixture
def temp_db_path(tmp_path):
    """Provide isolated SQLite database path per test."""
    db_file = tmp_path / "test_llm_usage.db"
    return db_file


@pytest.fixture
def active_config():
    """Config with budget enabled and typical test thresholds."""
    return BudgetPolicyConfig(
        enabled=True,
        daily_limit_usd=1.0,
        monthly_limit_usd=15.0,
        max_request_spend_usd=0.10,
        cooldown_seconds=0.05,
        max_concurrent_requests=1,
    )


@pytest.fixture
def test_candidate():
    """Verified test model candidate with known pricing."""
    return ModelCandidate(
        candidate_id="t1_c1",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        provider_id="fake_provider",
        exact_model_id="gpt-4o-mini-test",
        enabled=True,
        verified_capabilities={"json_mode": True},
        pricing=PricingMetadata(
            currency="USD",
            input_per_1m=0.15,
            output_per_1m=0.60,
            effective_date="2026-10-01",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Default Disabled & Fail-Closed Tests
# ---------------------------------------------------------------------------

def test_default_budget_policy_disabled_fails_closed(temp_db_path, test_candidate):
    """Default budget policy is disabled; attempts to reserve fail closed."""
    default_cfg = BudgetPolicyConfig()  # enabled=False
    ledger = BudgetLedger(db_path=temp_db_path, config=default_cfg)

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Analyze company website content.",
        max_output_tokens=1000,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ledger.reserve(req, test_candidate)
    assert "budget enforcement is disabled" in str(exc_info.value).lower()


def test_missing_or_incomplete_pricing_fails_closed(temp_db_path, active_config):
    """Candidate without verified pricing metadata cannot reserve budget (fails closed)."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    unpriced_candidate = ModelCandidate(
        candidate_id="t1_c2",
        tier=ModelTier.TIER_1_BUDGET,
        priority=2,
        provider_id="fake_provider",
        exact_model_id="unpriced-model",
        enabled=True,
        verified_capabilities={"json_mode": True},
        pricing=None,  # No pricing!
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Analyze website.",
        max_output_tokens=500,
    )
    with pytest.raises(LLMNotConfiguredError) as exc_info:
        ledger.reserve(req, unpriced_candidate)
    assert "pricing metadata" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 2. Spend Limit Enforcement Tests (Per-request, Daily, Monthly)
# ---------------------------------------------------------------------------

def test_per_request_spend_ceiling_enforced(temp_db_path, active_config, test_candidate):
    """Preflight projected reservation exceeding max_request_spend_usd is rejected."""
    # Set tight per-request cap of $0.0005
    tight_config = active_config.model_copy(update={"max_request_spend_usd": 0.0005})
    ledger = BudgetLedger(db_path=temp_db_path, config=tight_config)

    # 4000 output tokens at $0.60/1M = $0.0024 > $0.0005
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Test prompt",
        max_output_tokens=4000,
    )
    with pytest.raises(LLMBudgetExceededError) as exc_info:
        ledger.reserve(req, test_candidate)
    assert "per-request cap" in str(exc_info.value).lower()


def test_daily_budget_limit_enforced(temp_db_path, active_config, test_candidate):
    """Reservations that would push daily spend over daily_limit_usd are blocked."""
    daily_config = active_config.model_copy(update={"daily_limit_usd": 0.0020, "max_request_spend_usd": 0.0020})
    ledger = BudgetLedger(db_path=temp_db_path, config=daily_config)

    # First request: ~1000 output tokens * 0.60/1M = $0.0006 (leaves ~$0.0014)
    req1 = LLMRequest(
        task_id="website_extraction",
        prompt="Hello",
        max_output_tokens=1000,
    )
    res1 = ledger.reserve(req1, test_candidate)
    assert res1.status == ReservationStatus.RESERVED

    # Second request: 2500 output tokens * 0.60/1M = $0.0015 -> $0.0006 + $0.0015 = $0.0021 > $0.0020
    req2 = LLMRequest(
        task_id="website_extraction",
        prompt="Second request",
        max_output_tokens=2500,
    )
    with pytest.raises(LLMBudgetExceededError) as exc_info:
        ledger.reserve(req2, test_candidate)
    assert "daily llm budget exceeded" in str(exc_info.value).lower()


def test_monthly_budget_limit_enforced(temp_db_path, active_config, test_candidate):
    """Reservations that would push monthly spend over monthly_limit_usd are blocked."""
    monthly_config = active_config.model_copy(
        update={"monthly_limit_usd": 0.0015, "daily_limit_usd": 0.0015, "max_request_spend_usd": 0.0015}
    )
    ledger = BudgetLedger(db_path=temp_db_path, config=monthly_config)

    req1 = LLMRequest(
        task_id="website_extraction",
        prompt="Prompt 1",
        max_output_tokens=1500,  # ~$0.0009
    )
    # Day 1 in current month
    res1 = ledger.reserve(
        req1,
        test_candidate,
        current_time=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert res1.status == ReservationStatus.RESERVED

    req2 = LLMRequest(
        task_id="website_extraction",
        prompt="Prompt 2",
        max_output_tokens=1500,  # Day 2: daily is 0, but monthly total reaches $0.0018 > $0.0015
    )
    with pytest.raises(LLMBudgetExceededError) as exc_info:
        ledger.reserve(
            req2,
            test_candidate,
            current_time=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        )
    assert "monthly llm budget exceeded" in str(exc_info.value).lower()



# ---------------------------------------------------------------------------
# 3. Reservation & Reconciliation Lifecycle Tests
# ---------------------------------------------------------------------------

def test_atomic_reservation_and_reconciliation(temp_db_path, active_config, test_candidate):
    """Successful preflight reservation followed by reconciliation with reported tokens."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Short prompt",
        max_output_tokens=1000,
    )
    res = ledger.reserve(req, test_candidate)
    assert res.status == ReservationStatus.RESERVED
    assert res.final_cost_usd is None

    # Simulate model response with reported token counts: 200 input, 100 output
    # Price: (200 * 0.15 + 100 * 0.60) / 1_000_000 = (30 + 60) / 1M = $0.000090
    resp = LLMResponse(
        content='{"company": "SalesAI"}',
        provider=test_candidate.provider_id,
        model=test_candidate.exact_model_id,
        task_id=req.task_id,
        request_id=req.request_id,
        input_tokens=200,
        output_tokens=100,
    )

    reconciled = ledger.reconcile_response(res.reservation_id, resp, test_candidate)
    assert reconciled.status == ReservationStatus.RECONCILED
    assert reconciled.reported_input_tokens == 200
    assert reconciled.reported_output_tokens == 100
    assert reconciled.final_cost_usd == 0.00009
    assert resp.cost_usd == 0.00009


def test_missing_or_ambiguous_usage_never_zero_dollars(temp_db_path, active_config, test_candidate):
    """Provider response with None token counts raises LLMUncertainUsageError and retains reservation."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Analyze data",
        max_output_tokens=1000,
    )
    res = ledger.reserve(req, test_candidate)
    initial_reserve = res.reserved_usd

    # Missing tokens (None)
    resp = LLMResponse(
        content="raw content",
        provider=test_candidate.provider_id,
        model=test_candidate.exact_model_id,
        task_id=req.task_id,
        request_id=req.request_id,
        input_tokens=None,
        output_tokens=None,
    )

    with pytest.raises(LLMUncertainUsageError) as exc_info:
        ledger.reconcile_response(res.reservation_id, resp, test_candidate)
    assert "lacked verified token usage" in str(exc_info.value).lower()

    # Verify ledger state: marked UNCERTAIN and final_cost_usd is NOT $0.00 (retains reservation)
    summary = ledger.get_budget_summary()
    assert summary.status_counts_today.get(ReservationStatus.UNCERTAIN.value) == 1
    assert summary.daily_spent_usd == initial_reserve
    assert initial_reserve > 0.0


def test_safe_pre_dispatch_cancellation_releases_budget(temp_db_path, active_config, test_candidate):
    """Reservation cancelled prior to network dispatch releases spend from budget."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    # Cancel before dispatch
    cancelled = ledger.cancel_reservation(res.reservation_id, reason_code="PREFLIGHT_ABORT")
    assert cancelled is True

    summary = ledger.get_budget_summary()
    # Cancelled records have final_cost_usd = 0.0 and are excluded from spent sum
    assert summary.daily_spent_usd == 0.0
    assert summary.status_counts_today.get(ReservationStatus.CANCELLED.value) == 1


def test_dispatched_transport_failure_retains_reserved_spend(temp_db_path, active_config, test_candidate):
    """Transport failure after network dispatch marks UNCERTAIN and retains spend."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    # Mark failed with dispatched=True (e.g. 504 Gateway Timeout mid-stream)
    ledger.mark_reservation_failed(res.reservation_id, error_code="TIMEOUT_504", dispatched=True)

    summary = ledger.get_budget_summary()
    assert summary.status_counts_today.get(ReservationStatus.UNCERTAIN.value) == 1
    # Spend liability is retained against budget
    assert summary.daily_spent_usd == res.reserved_usd


# ---------------------------------------------------------------------------
# 4. Idempotency Tests
# ---------------------------------------------------------------------------

def test_idempotent_reservation_duplicate_request_id(temp_db_path, active_config, test_candidate):
    """Reserving with the same request_id returns the existing reservation without double booking."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    fixed_req_id = "req_fixed_id_12345"
    req = LLMRequest(
        request_id=fixed_req_id,
        task_id="website_extraction",
        prompt="Prompt text",
        max_output_tokens=500,
    )

    res1 = ledger.reserve(req, test_candidate)
    res2 = ledger.reserve(req, test_candidate)

    assert res1.reservation_id == res2.reservation_id
    summary = ledger.get_budget_summary()
    assert summary.total_reservations_today == 1
    assert summary.daily_spent_usd == res1.reserved_usd


def test_idempotent_reconciliation(temp_db_path, active_config, test_candidate):
    """Reconciling an already-reconciled reservation returns existing state without error."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    resp = LLMResponse(
        content="result",
        provider=test_candidate.provider_id,
        model=test_candidate.exact_model_id,
        task_id=req.task_id,
        request_id=req.request_id,
        input_tokens=100,
        output_tokens=50,
    )

    reconciled1 = ledger.reconcile_response(res.reservation_id, resp, test_candidate)
    reconciled2 = ledger.reconcile_response(res.reservation_id, resp, test_candidate)

    assert reconciled1.final_cost_usd == reconciled2.final_cost_usd
    assert reconciled2.status == ReservationStatus.RECONCILED


# ---------------------------------------------------------------------------
# 5. Concurrency & Rate Limit Guard Tests
# ---------------------------------------------------------------------------

def test_concurrency_ceiling_enforced_sync(temp_db_path, active_config, test_candidate):
    """Synchronous concurrent calls exceeding max_concurrent_requests fail fast."""
    # max_concurrent_requests = 1
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(delay_seconds=0.2)
    req1 = LLMRequest(task_id="website_extraction", prompt="Req 1", max_output_tokens=200)
    req2 = LLMRequest(task_id="website_extraction", prompt="Req 2", max_output_tokens=200)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(guard.guarded_call, req1, test_candidate, fake.generate)
        time.sleep(0.02)  # ensure f1 has entered guarded_call
        f2 = executor.submit(guard.guarded_call, req2, test_candidate, fake.generate)

        res1 = f1.result()
        assert res1 is not None

        with pytest.raises(LLMConcurrencyLimitError):
            f2.result()


@pytest.mark.asyncio
async def test_concurrency_ceiling_enforced_async(temp_db_path, active_config, test_candidate):
    """Asynchronous concurrent calls exceeding max_concurrent_requests fail fast."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(delay_seconds=0.2)
    req1 = LLMRequest(task_id="website_extraction", prompt="Async 1", max_output_tokens=200)
    req2 = LLMRequest(task_id="website_extraction", prompt="Async 2", max_output_tokens=200)

    # Launch task 1 and task 2 simultaneously
    t1 = asyncio.create_task(guard.guarded_call_async(req1, test_candidate, fake.generate_async))
    await asyncio.sleep(0.02)
    t2 = asyncio.create_task(guard.guarded_call_async(req2, test_candidate, fake.generate_async))

    res1 = await t1
    assert res1 is not None

    with pytest.raises(LLMConcurrencyLimitError):
        await t2


def test_provider_cooldown_rate_guard(temp_db_path, test_candidate):
    """Consecutive calls to the same provider within cooldown window fail fast."""
    # 0.5s cooldown
    cfg = BudgetPolicyConfig(
        enabled=True,
        daily_limit_usd=1.0,
        monthly_limit_usd=15.0,
        max_request_spend_usd=0.10,
        cooldown_seconds=0.5,
        max_concurrent_requests=2,
    )
    ledger = BudgetLedger(db_path=temp_db_path, config=cfg)
    guard = RuntimeGuard(ledger=ledger, config=cfg)
    fake = FakeProvider()

    req1 = LLMRequest(task_id="website_extraction", prompt="Call 1", max_output_tokens=100)
    req2 = LLMRequest(task_id="website_extraction", prompt="Call 2", max_output_tokens=100)

    # Call 1 succeeds
    guard.guarded_call(req1, test_candidate, fake.generate)

    # Call 2 immediately after Call 1 (within 0.5s)
    with pytest.raises(LLMConcurrencyLimitError) as exc_info:
        guard.guarded_call(req2, test_candidate, fake.generate)
    assert "cooldown active" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 6. Upstream 429/5xx Fail-Closed (No Retries, No Escalation)
# ---------------------------------------------------------------------------

def test_upstream_429_fails_immediately_without_retries(temp_db_path, active_config, test_candidate):
    """First 429 fails immediately with LLMRateLimitError; zero automatic retries."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(raise_error=LLMRateLimitError("Upstream rate limited."))
    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=200)

    with pytest.raises(LLMRateLimitError):
        guard.guarded_call(req, test_candidate, fake.generate)

    # Exactly 1 call was attempted (no retry)
    assert fake.call_count == 1
    # Ledger record marked UNCERTAIN
    summary = ledger.get_budget_summary()
    assert summary.status_counts_today.get(ReservationStatus.UNCERTAIN.value) == 1


def test_upstream_5xx_fails_immediately_without_retries(temp_db_path, active_config, test_candidate):
    """First 5xx fails immediately with LLMProviderUnavailableError; zero vendor switching."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(raise_error=LLMProviderUnavailableError("Server error 503."))
    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=200)

    with pytest.raises(LLMProviderUnavailableError):
        guard.guarded_call(req, test_candidate, fake.generate)

    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# 7. Stale Reservation Inspection & Operator Review Tests
# ---------------------------------------------------------------------------

def test_stale_reservation_inspection_and_operator_review(temp_db_path, active_config, test_candidate):
    """Stale reservations are detected and only modified via authenticated operator review."""
    cfg = active_config.model_copy(update={"stale_reservation_seconds": 1.0})
    ledger = BudgetLedger(db_path=temp_db_path, config=cfg)

    req = LLMRequest(task_id="website_extraction", prompt="Stale check", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    # Before threshold: not stale
    assert len(ledger.get_stale_reservations()) == 0

    # Sleep past stale threshold
    time.sleep(1.1)
    stale_list = ledger.get_stale_reservations()
    assert len(stale_list) == 1
    assert stale_list[0]["reservation_id"] == res.reservation_id

    # Review action: operator explicitly reconciles after verifying provider invoice
    review_result = ledger.operator_review_stale(
        reservation_id=res.reservation_id,
        action="reconcile",
        operator_id="operator_alice",
        final_cost_usd=0.00012,
        notes="Verified in provider usage console.",
    )

    assert review_result["new_status"] == ReservationStatus.RECONCILED.value
    assert review_result["final_cost_usd"] == 0.00012
    assert review_result["reviewed_by"] == "operator_alice"

    # Now no longer in stale list
    assert len(ledger.get_stale_reservations()) == 0


def test_operator_review_requires_operator_id(temp_db_path, active_config, test_candidate):
    """Operator review fails closed if operator_id is missing."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    req = LLMRequest(task_id="website_extraction", prompt="Prompt", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    with pytest.raises(ValueError) as exc_info:
        ledger.operator_review_stale(res.reservation_id, action="cancel", operator_id="")
    assert "operator_id is required" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 8. Operator Summary & Zero-PII Security Audit
# ---------------------------------------------------------------------------

def test_budget_summary_read_only_overview(temp_db_path, active_config, test_candidate):
    """Budget summary accurately rolls up limits, spend, counts, and outstanding reservations."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    req = LLMRequest(task_id="website_extraction", prompt="Summary prompt", max_output_tokens=500)
    res = ledger.reserve(req, test_candidate)

    summary = ledger.get_budget_summary()
    assert summary.daily_limit_usd == 1.0
    assert summary.monthly_limit_usd == 15.0
    assert summary.daily_spent_usd == res.reserved_usd
    assert summary.daily_remaining_usd == round(1.0 - res.reserved_usd, 6)
    assert len(summary.outstanding_reservations) == 1
    assert summary.outstanding_reservations[0]["reservation_id"] == res.reservation_id


def test_zero_pii_prompts_or_secrets_in_sqlite(temp_db_path, active_config, test_candidate):
    """Direct SQLite inspection confirms ZERO prompts, completions, API keys, or emails in DB."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)

    sensitive_prompt = "Contact: john.doe@example.com, API Secret: sk-test1234567890"
    req = LLMRequest(
        task_id="website_extraction",
        prompt=sensitive_prompt,
        max_output_tokens=500,
    )
    res = ledger.reserve(req, test_candidate)

    resp = LLMResponse(
        content="Confidential completion text with customer private information",
        provider=test_candidate.provider_id,
        model=test_candidate.exact_model_id,
        task_id=req.task_id,
        request_id=req.request_id,
        input_tokens=150,
        output_tokens=75,
    )
    ledger.reconcile_response(res.reservation_id, resp, test_candidate)

    # Inspect raw SQLite database directly
    conn = sqlite3.connect(str(temp_db_path))
    cursor = conn.cursor()

    # Verify column names: no prompt, completion, or payload column
    cursor.execute("PRAGMA table_info(llm_reservations);")
    columns = [col[1] for col in cursor.fetchall()]
    forbidden_col_names = {"prompt", "content", "completion", "payload", "secret", "key", "email"}
    assert not any(c.lower() in forbidden_col_names for c in columns)

    # Verify all string values in all rows: zero sensitive tokens
    cursor.execute("SELECT * FROM llm_reservations;")
    rows = cursor.fetchall()
    all_text = " ".join(str(cell) for row in rows for cell in row)

    assert "john.doe@example.com" not in all_text
    assert "sk-test1234567890" not in all_text
    assert "Confidential completion text" not in all_text
    conn.close()


# ---------------------------------------------------------------------------
# 9. GuardedGateway End-to-End Integration Seam Tests
# ---------------------------------------------------------------------------

def test_guarded_gateway_execution_sync(temp_db_path, active_config, test_candidate):
    """LLMProviderGateway with RuntimeGuard successfully reserves, executes, and reconciles."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(default_input_tokens=80, default_output_tokens=40)
    reg = ProviderRegistry()
    reg.register(fake)

    catalog = ModelCatalog()
    catalog.register_candidate(test_candidate)
    router = TaskRouter(catalog=catalog)
    router.set_approved_route(
        TaskRouteDecision(
            task_id="website_extraction",
            tier=ModelTier.TIER_1_BUDGET,
            provider_id=test_candidate.provider_id,
            model=test_candidate.exact_model_id,
            candidate_id=test_candidate.candidate_id,
            approved_by="operator_test",
        )
    )

    gw_config = GatewayConfig(enabled=True, default_provider=fake.provider_id)
    gateway = LLMProviderGateway(
        registry=reg,
        config=gw_config,
        router=router,
        runtime_guard=guard,
    )

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Analyze company website.",
        max_output_tokens=500,
    )

    response = gateway.generate(req)
    assert response.content == '{"status": "ok"}'
    # Cost verified: (80 * 0.15 + 40 * 0.60) / 1M = (12 + 24) / 1M = 0.000036
    assert response.cost_usd == 0.000036

    summary = ledger.get_budget_summary()
    assert summary.status_counts_today.get(ReservationStatus.RECONCILED.value) == 1
    assert summary.daily_spent_usd == 0.000036


@pytest.mark.asyncio
async def test_guarded_gateway_execution_async(temp_db_path, active_config, test_candidate):
    """LLMProviderGateway async execution under RuntimeGuard parity."""
    ledger = BudgetLedger(db_path=temp_db_path, config=active_config)
    guard = RuntimeGuard(ledger=ledger, config=active_config)

    fake = FakeProvider(default_input_tokens=100, default_output_tokens=50)
    reg = ProviderRegistry()
    reg.register(fake)

    catalog = ModelCatalog()
    catalog.register_candidate(test_candidate)
    router = TaskRouter(catalog=catalog)
    router.set_approved_route(
        TaskRouteDecision(
            task_id="website_extraction",
            tier=ModelTier.TIER_1_BUDGET,
            provider_id=test_candidate.provider_id,
            model=test_candidate.exact_model_id,
            candidate_id=test_candidate.candidate_id,
            approved_by="operator_test",
        )
    )

    gw_config = GatewayConfig(enabled=True, default_provider=fake.provider_id)
    gateway = LLMProviderGateway(
        registry=reg,
        config=gw_config,
        router=router,
        runtime_guard=guard,
    )

    req = LLMRequest(
        task_id="website_extraction",
        prompt="Async analyze company website.",
        max_output_tokens=500,
    )

    response = await gateway.generate_async(req)
    assert response.content == '{"status": "ok"}'
    # Cost: (100 * 0.15 + 50 * 0.60) / 1M = 0.000045
    assert response.cost_usd == 0.000045


# ---------------------------------------------------------------------------
# 10. Budget Policy Validation
# ---------------------------------------------------------------------------

def test_budget_policy_validation_catches_invalid_limits():
    """Policy config rejects daily limit exceeding monthly limit."""
    with pytest.raises(ValueError):
        BudgetPolicyConfig(daily_limit_usd=20.0, monthly_limit_usd=15.0)

    with pytest.raises(ValueError):
        BudgetPolicyConfig(max_request_spend_usd=5.0, daily_limit_usd=2.0)
