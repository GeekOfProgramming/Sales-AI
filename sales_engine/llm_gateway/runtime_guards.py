"""
sales_engine/llm_gateway/runtime_guards.py
Local concurrency, rate-limit, and budget runtime guards for SalesAI LLM Gateway.
Enforces:
- Strict local concurrency limiter (sync and async parity).
- Minimal per-provider cooldown / rate guard.
- Immediate fail-closed on first 429 or 5xx with zero automatic retries,
  zero vendor rotation, and zero tier escalation.
- Safe preflight budget reservation and reconciliation wrapping execution.
"""

import asyncio
import inspect
import threading
import time
from typing import Callable, Optional, Dict, Any, Awaitable

from sales_engine.llm_gateway.budget_ledger import (
    BudgetLedger,
    BudgetPolicyConfig,
    BudgetReservation,
)
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, sanitize_log_str
from sales_engine.llm_gateway.errors import (
    LLMConcurrencyLimitError,
    LLMError,
    LLMRateLimitError,
    LLMProviderUnavailableError,
)
from sales_engine.llm_gateway.model_catalog import ModelCandidate


class RuntimeGuard:
    """
    Coordinates local concurrency limits, per-provider rate cooldowns, and ledger accounting.
    Enforces that:
    1. Maximum concurrent requests are strictly capped.
    2. Consecutive calls to the same provider respect a minimum cooldown interval.
    3. Errors during transport mark reservations as UNCERTAIN rather than $0.00.
    4. First upstream failure (429, 5xx, timeout) stops execution immediately without retries.
    """

    def __init__(
        self,
        ledger: BudgetLedger,
        config: Optional[BudgetPolicyConfig] = None,
    ):
        self.ledger = ledger
        self.config = config or ledger.config

        # Concurrency primitives
        self._sync_semaphore = threading.BoundedSemaphore(self.config.max_concurrent_requests)
        self._async_semaphore: Optional[asyncio.Semaphore] = None
        self._sync_active_count = 0
        self._state_lock = threading.Lock()

        # Cooldown tracking: provider_id -> monotonic timestamp of last completed call
        self._last_call_timestamps: Dict[str, float] = {}

    def _get_async_semaphore(self) -> asyncio.Semaphore:
        if self._async_semaphore is None:
            self._async_semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
        return self._async_semaphore

    def _check_and_acquire_sync(self, provider_id: str) -> None:
        """Verify cooldown and acquire sync concurrency slot; fails fast if blocked."""
        with self._state_lock:
            # 1. Cooldown check
            last_call = self._last_call_timestamps.get(provider_id, 0.0)
            now_mono = time.monotonic()
            elapsed = now_mono - last_call
            if elapsed < self.config.cooldown_seconds:
                remaining = self.config.cooldown_seconds - elapsed
                raise LLMConcurrencyLimitError(
                    message=(
                        f"Rate limit cooldown active for provider '{provider_id}'. "
                        f"Must wait {remaining:.2f}s before next request."
                    ),
                    provider=provider_id,
                )

            # 2. Concurrency limit check
            acquired = self._sync_semaphore.acquire(blocking=False)
            if not acquired:
                raise LLMConcurrencyLimitError(
                    message=(
                        f"Local LLM concurrency ceiling ({self.config.max_concurrent_requests}) "
                        "reached. Cannot dispatch simultaneous request."
                    ),
                    provider=provider_id,
                )
            self._sync_active_count += 1

    def _release_sync(self, provider_id: str) -> None:
        """Release sync concurrency slot and update cooldown timestamp."""
        with self._state_lock:
            self._last_call_timestamps[provider_id] = time.monotonic()
            if self._sync_active_count > 0:
                self._sync_active_count -= 1
                try:
                    self._sync_semaphore.release()
                except ValueError:
                    pass

    async def _check_and_acquire_async(self, provider_id: str) -> None:
        """Verify cooldown and acquire async concurrency slot; fails fast if blocked."""
        # 1. Cooldown check
        with self._state_lock:
            last_call = self._last_call_timestamps.get(provider_id, 0.0)
            now_mono = time.monotonic()
            elapsed = now_mono - last_call
            if elapsed < self.config.cooldown_seconds:
                remaining = self.config.cooldown_seconds - elapsed
                raise LLMConcurrencyLimitError(
                    message=(
                        f"Rate limit cooldown active for provider '{provider_id}'. "
                        f"Must wait {remaining:.2f}s before next request."
                    ),
                    provider=provider_id,
                )

        # 2. Async concurrency slot acquire (non-blocking immediate check)
        sem = self._get_async_semaphore()
        if sem.locked():
            raise LLMConcurrencyLimitError(
                message=(
                    f"Local LLM concurrency ceiling ({self.config.max_concurrent_requests}) "
                    "reached. Cannot dispatch simultaneous async request."
                ),
                provider=provider_id,
            )
        await sem.acquire()

    def _release_async(self, provider_id: str) -> None:
        """Release async concurrency slot and update cooldown timestamp."""
        with self._state_lock:
            self._last_call_timestamps[provider_id] = time.monotonic()
        sem = self._get_async_semaphore()
        try:
            sem.release()
        except ValueError:
            pass

    def guarded_call(
        self,
        request: LLMRequest,
        candidate: ModelCandidate,
        call_fn: Callable[[LLMRequest], LLMResponse],
        subtask_id: Optional[str] = None,
        declared_input_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Synchronously execute an LLM call under budget preflight, concurrency, and rate guards.
        """
        provider_id = candidate.provider_id

        # 1. Preflight budget reservation (fails closed if disabled or over budget)
        reservation = self.ledger.reserve(
            request=request,
            candidate=candidate,
            subtask_id=subtask_id,
            declared_input_tokens=declared_input_tokens,
        )

        # 2. Acquire concurrency and cooldown
        try:
            self._check_and_acquire_sync(provider_id)
        except Exception as exc:
            # Safe cancellation because no network dispatch occurred
            self.ledger.cancel_reservation(
                reservation.reservation_id,
                reason_code=getattr(exc, "error_code", "CONCURRENCY_BLOCKED"),
            )
            raise

        dispatched = False
        try:
            dispatched = True
            response = call_fn(request)
        except (LLMRateLimitError, LLMProviderUnavailableError) as err:
            # Immediate fail-closed on first 429 or 5xx: zero auto-retry or tier escalation
            error_code = getattr(err, "error_code", "PROVIDER_ERROR")
            self.ledger.mark_reservation_failed(
                reservation.reservation_id, error_code=error_code, dispatched=dispatched
            )
            raise
        except Exception as err:
            error_code = getattr(err, "error_code", type(err).__name__)
            self.ledger.mark_reservation_failed(
                reservation.reservation_id, error_code=error_code, dispatched=dispatched
            )
            raise
        finally:
            self._release_sync(provider_id)

        # 3. Post-response reconciliation against reported usage and verified pricing
        self.ledger.reconcile_response(
            reservation_id=reservation.reservation_id,
            response=response,
            candidate=candidate,
        )

        return response

    async def guarded_call_async(
        self,
        request: LLMRequest,
        candidate: ModelCandidate,
        call_async_fn: Callable[[LLMRequest], Awaitable[LLMResponse]],
        subtask_id: Optional[str] = None,
        declared_input_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Asynchronously execute an LLM call under budget preflight, concurrency, and rate guards.
        """
        provider_id = candidate.provider_id

        # 1. Preflight budget reservation
        reservation = self.ledger.reserve(
            request=request,
            candidate=candidate,
            subtask_id=subtask_id,
            declared_input_tokens=declared_input_tokens,
        )

        # 2. Acquire concurrency and cooldown
        try:
            await self._check_and_acquire_async(provider_id)
        except Exception as exc:
            self.ledger.cancel_reservation(
                reservation.reservation_id,
                reason_code=getattr(exc, "error_code", "CONCURRENCY_BLOCKED"),
            )
            raise

        dispatched = False
        try:
            dispatched = True
            response = await call_async_fn(request)
        except (LLMRateLimitError, LLMProviderUnavailableError) as err:
            error_code = getattr(err, "error_code", "PROVIDER_ERROR")
            self.ledger.mark_reservation_failed(
                reservation.reservation_id, error_code=error_code, dispatched=dispatched
            )
            raise
        except Exception as err:
            error_code = getattr(err, "error_code", type(err).__name__)
            self.ledger.mark_reservation_failed(
                reservation.reservation_id, error_code=error_code, dispatched=dispatched
            )
            raise
        finally:
            self._release_async(provider_id)

        # 3. Post-response reconciliation
        self.ledger.reconcile_response(
            reservation_id=reservation.reservation_id,
            response=response,
            candidate=candidate,
        )

        return response
