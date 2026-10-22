"""
sales_engine/llm_gateway/interface.py
Abstract base provider and central gateway facade for SalesAI.
Guarantees fail-closed behavior, zero silent escalation, and strict provider isolation.
"""

from abc import ABC, abstractmethod
from typing import Optional

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import LLMNotConfiguredError
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.runtime_guards import RuntimeGuard
from sales_engine.llm_gateway.router import TaskRouter
from sales_engine.llm_gateway.model_catalog import ModelCandidate


class BaseLLMProvider(ABC):
    """
    Abstract contract that every external LLM provider adapter must implement.
    Supports both synchronous and asynchronous generation without heavy SDKs.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique logical identifier for the provider (e.g. 'openai', 'anthropic')."""
        pass

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Execute synchronous model generation for the given request."""
        pass

    @abstractmethod
    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        """Execute asynchronous model generation for the given request."""
        pass

    def is_healthy(self) -> bool:
        """Lightweight reachability check. Defaults to True for active adapters."""
        return True


class LLMProviderGateway:
    """
    Central gateway facade for all SalesAI LLM operations.
    Enforces fail-closed configuration, explicit provider routing, and zero automatic escalation.
    Optionally wraps execution with RuntimeGuard (concurrency, cooldown, budget preflight/reconciliation).
    """

    def __init__(
        self,
        registry: Optional[ProviderRegistry] = None,
        config: Optional[GatewayConfig] = None,
        router: Optional[TaskRouter] = None,
        runtime_guard: Optional[RuntimeGuard] = None,
    ):
        self.registry = registry or ProviderRegistry()
        self.config = config or GatewayConfig()
        self.router = router
        self.runtime_guard = runtime_guard

    def _resolve_provider(self, request: LLMRequest) -> BaseLLMProvider:
        """
        Resolve the target provider. Fails closed with LLMNotConfiguredError
        if disabled, unconfigured, or unregistered.
        """
        if not self.config.enabled:
            raise LLMNotConfiguredError(
                message="SalesAI LLM Gateway is currently disabled in configuration (fail-closed).",
                task_id=request.task_id,
            )

        provider_id = request.provider or self.config.default_provider
        if not provider_id:
            raise LLMNotConfiguredError(
                message="No LLM provider specified in request and no default provider configured.",
                task_id=request.task_id,
            )

        provider = self.registry.get(provider_id)
        if not provider:
            raise LLMNotConfiguredError(
                message=f"LLM provider '{provider_id}' is not registered in the gateway.",
                provider=provider_id,
                task_id=request.task_id,
            )

        return provider

    def _resolve_candidate(self, request: LLMRequest) -> Optional[ModelCandidate]:
        """Resolve ModelCandidate from router if available."""
        if not self.router:
            return None
        subtask_id = request.metadata.get("subtask_id") if request.metadata else None
        decision = self.router.resolve_approved(request.task_id, subtask_id)
        return self.router.catalog.get_candidate(decision.candidate_id)

    def generate(
        self,
        request: LLMRequest,
        candidate: Optional[ModelCandidate] = None,
    ) -> LLMResponse:
        """
        Synchronous gateway entrypoint.
        Routes to resolved provider; optionally wrapped by runtime_guard.
        """
        provider = self._resolve_provider(request)
        if self.runtime_guard:
            target_candidate = candidate or self._resolve_candidate(request)
            if not target_candidate:
                raise LLMNotConfiguredError(
                    message=f"Cannot execute guarded call: no candidate resolved for task '{request.task_id}'.",
                    task_id=request.task_id,
                )
            subtask_id = request.metadata.get("subtask_id") if request.metadata else None
            return self.runtime_guard.guarded_call(
                request=request,
                candidate=target_candidate,
                call_fn=provider.generate,
                subtask_id=subtask_id,
            )
        return provider.generate(request)

    async def generate_async(
        self,
        request: LLMRequest,
        candidate: Optional[ModelCandidate] = None,
    ) -> LLMResponse:
        """
        Asynchronous gateway entrypoint.
        Routes to resolved provider; optionally wrapped by runtime_guard.
        """
        provider = self._resolve_provider(request)
        if self.runtime_guard:
            target_candidate = candidate or self._resolve_candidate(request)
            if not target_candidate:
                raise LLMNotConfiguredError(
                    message=f"Cannot execute guarded call: no candidate resolved for task '{request.task_id}'.",
                    task_id=request.task_id,
                )
            subtask_id = request.metadata.get("subtask_id") if request.metadata else None
            return await self.runtime_guard.guarded_call_async(
                request=request,
                candidate=target_candidate,
                call_async_fn=provider.generate_async,
                subtask_id=subtask_id,
            )
        return await provider.generate_async(request)
