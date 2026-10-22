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
import hashlib
import inspect
import json
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
    LLMDuplicateRequestError,
    LLMCandidateMismatchError,
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
    5. A1 Defense: Single request_id cannot trigger a second network dispatch (cached or fail closed).
    6. A2 Defense: Request provider, model, tier, and task are strictly bound to authorized candidate.
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

        # A1 Idempotency protections: in-memory cache and in-flight tracking
        self._response_cache: Dict[str, LLMResponse] = {}
        self._request_fingerprints: Dict[str, str] = {}
        self._in_flight_request_ids: set[str] = set()

    def _compute_request_fingerprint(
        self,
        request: LLMRequest,
        candidate: Optional[ModelCandidate] = None,
    ) -> str:
        """
        Compute deterministic SHA-256 fingerprint of request content and parameters.
        Used to prevent cache reuse when the same request_id is reused with different payload/parameters.
        Does not log or expose raw prompt text.
        """
        data = {
            "task_id": request.task_id,
            "model": request.model or (candidate.exact_model_id if candidate else None),
            "provider": request.provider or (candidate.provider_id if candidate else None),
            "tier": request.tier.value if request.tier else (candidate.tier.value if candidate else None),
            "prompt": request.prompt,
            "system_prompt": request.system_prompt or "",
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
        encoded = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _validate_candidate_binding(
        self,
        request: LLMRequest,
        candidate: ModelCandidate,
        subtask_id: Optional[str] = None,
    ) -> None:
        """
        A2 Defense: Verify exact equality of request provider, model, tier, and task to candidate.
        Fails closed with LLMCandidateMismatchError on any divergence before reservation or HTTP.
        """
        if not candidate.enabled:
            raise LLMCandidateMismatchError(
                message=f"Candidate '{candidate.candidate_id}' is disabled in catalog (fail-closed).",
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

        if not candidate.is_task_allowed(request.task_id):
            raise LLMCandidateMismatchError(
                message=f"Candidate '{candidate.candidate_id}' is not authorized for task '{request.task_id}'.",
                provider=candidate.provider_id,
                task_id=request.task_id,
            )

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

        # Explicitly bind candidate identity to request to ensure perfect dispatch fidelity
        if request.provider is None:
            request.provider = candidate.provider_id
        if request.model is None:
            request.model = candidate.exact_model_id
        if request.tier is None:
            request.tier = candidate.tier


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
        # A1 Defense & S3.1 Fingerprint Check:
        fingerprint = self._compute_request_fingerprint(request, candidate)
        with self._state_lock:
            if request.request_id in self._response_cache:
                cached_fp = self._request_fingerprints.get(request.request_id)
                if cached_fp and cached_fp != fingerprint:
                    raise LLMDuplicateRequestError(
                        message=(
                            f"Request ID '{request.request_id}' reused with mismatched payload, "
                            f"task, model, or parameters."
                        ),
                        provider=candidate.provider_id,
                        task_id=request.task_id,
                    )
                return self._response_cache[request.request_id]
            if request.request_id in self._in_flight_request_ids:
                raise LLMDuplicateRequestError(
                    message=f"Request ID '{request.request_id}' is currently in flight in another thread/coroutine.",
                    provider=candidate.provider_id,
                    task_id=request.task_id,
                )
            self._in_flight_request_ids.add(request.request_id)

        try:
            # A2 Defense: Preflight verification of candidate binding to request
            self._validate_candidate_binding(request, candidate, subtask_id=subtask_id)

            provider_id = candidate.provider_id

            # 1. Preflight budget reservation (fails closed if disabled, duplicate, or over budget)
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
                self.ledger.cancel_reservation(
                    reservation.reservation_id,
                    reason_code=getattr(exc, "error_code", "CONCURRENCY_BLOCKED"),
                )
                raise

            # A2 Defense: Verify immediately before HTTP dispatch that request attributes match candidate
            if request.provider != candidate.provider_id or request.model != candidate.exact_model_id:
                self.ledger.cancel_reservation(reservation.reservation_id, reason_code="CANDIDATE_MISMATCH")
                raise LLMCandidateMismatchError(
                    message=(
                        f"Pricing/candidate identity mismatch before HTTP dispatch: "
                        f"request({request.provider}/{request.model}) != candidate({candidate.provider_id}/{candidate.exact_model_id})"
                    ),
                    provider=candidate.provider_id,
                    task_id=request.task_id,
                )

            dispatched = False
            try:
                dispatched = True
                response = call_fn(request)
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
                self._release_sync(provider_id)

            # 3. Post-response reconciliation against reported usage and verified pricing
            self.ledger.reconcile_response(
                reservation_id=reservation.reservation_id,
                response=response,
                candidate=candidate,
            )

            # A1 Defense: Cache response to guarantee subsequent duplicate calls never re-dispatch
            with self._state_lock:
                self._response_cache[request.request_id] = response
                self._request_fingerprints[request.request_id] = fingerprint

            return response
        finally:
            with self._state_lock:
                self._in_flight_request_ids.discard(request.request_id)

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
        # A1 Defense & S3.1 Fingerprint Check:
        fingerprint = self._compute_request_fingerprint(request, candidate)
        with self._state_lock:
            if request.request_id in self._response_cache:
                cached_fp = self._request_fingerprints.get(request.request_id)
                if cached_fp and cached_fp != fingerprint:
                    raise LLMDuplicateRequestError(
                        message=(
                            f"Request ID '{request.request_id}' reused with mismatched payload, "
                            f"task, model, or parameters."
                        ),
                        provider=candidate.provider_id,
                        task_id=request.task_id,
                    )
                return self._response_cache[request.request_id]
            if request.request_id in self._in_flight_request_ids:
                raise LLMDuplicateRequestError(
                    message=f"Request ID '{request.request_id}' is currently in flight in another thread/coroutine.",
                    provider=candidate.provider_id,
                    task_id=request.task_id,
                )
            self._in_flight_request_ids.add(request.request_id)

        try:
            # A2 Defense: Preflight verification of candidate binding to request
            self._validate_candidate_binding(request, candidate, subtask_id=subtask_id)

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

            # A2 Defense: Verify immediately before HTTP dispatch that request attributes match candidate
            if request.provider != candidate.provider_id or request.model != candidate.exact_model_id:
                self.ledger.cancel_reservation(reservation.reservation_id, reason_code="CANDIDATE_MISMATCH")
                raise LLMCandidateMismatchError(
                    message=(
                        f"Pricing/candidate identity mismatch before HTTP dispatch: "
                        f"request({request.provider}/{request.model}) != candidate({candidate.provider_id}/{candidate.exact_model_id})"
                    ),
                    provider=candidate.provider_id,
                    task_id=request.task_id,
                )

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

            # A1 Defense: Cache response to guarantee subsequent duplicate calls never re-dispatch
            with self._state_lock:
                self._response_cache[request.request_id] = response
                self._request_fingerprints[request.request_id] = fingerprint

            return response
        finally:
            with self._state_lock:
                self._in_flight_request_ids.discard(request.request_id)
