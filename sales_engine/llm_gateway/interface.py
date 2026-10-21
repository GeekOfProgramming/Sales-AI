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
    """

    def __init__(
        self,
        registry: Optional[ProviderRegistry] = None,
        config: Optional[GatewayConfig] = None,
    ):
        self.registry = registry or ProviderRegistry()
        self.config = config or GatewayConfig()

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

    def generate(self, request: LLMRequest) -> LLMResponse:
        """
        Synchronous gateway entrypoint.
        Routes to resolved provider; raises LLMNotConfiguredError on missing configuration.
        """
        provider = self._resolve_provider(request)
        return provider.generate(request)

    async def generate_async(self, request: LLMRequest) -> LLMResponse:
        """
        Asynchronous gateway entrypoint.
        Routes to resolved provider; raises LLMNotConfiguredError on missing configuration.
        """
        provider = self._resolve_provider(request)
        return await provider.generate_async(request)
