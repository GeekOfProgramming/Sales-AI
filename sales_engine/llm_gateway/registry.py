"""
sales_engine/llm_gateway/registry.py
Local, explicit dependency-injection provider registry for SalesAI.
Strictly local memory store with zero network calls, zero autodiscovery, and duplicate rejection.
"""

from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sales_engine.llm_gateway.interface import BaseLLMProvider


class ProviderRegistry:
    """
    In-memory registry managing active BaseLLMProvider instances.
    Enforces explicit dependency injection and rejects duplicate registrations by default.
    """

    def __init__(self):
        self._providers: Dict[str, "BaseLLMProvider"] = {}

    def register(self, provider: "BaseLLMProvider", allow_override: bool = False) -> None:
        """
        Register a provider instance.
        Rejects duplicates unless allow_override=True (intended for test mocking only).
        """
        if not provider or not getattr(provider, "provider_id", None):
            raise ValueError("Cannot register invalid provider without a valid provider_id.")

        pid = provider.provider_id
        if pid in self._providers and not allow_override:
            raise ValueError(
                f"Provider '{pid}' is already registered in gateway registry. "
                "Duplicate registration rejected to prevent unintended vendor overrides."
            )

        self._providers[pid] = provider

    def get(self, provider_id: str) -> Optional["BaseLLMProvider"]:
        """Retrieve registered provider by ID, or None if not present."""
        return self._providers.get(provider_id)

    def has(self, provider_id: str) -> bool:
        """Check if provider ID is currently registered."""
        return provider_id in self._providers

    def list_providers(self) -> List[str]:
        """Return list of all registered provider IDs in deterministic order."""
        return sorted(list(self._providers.keys()))

    def unregister(self, provider_id: str) -> Optional["BaseLLMProvider"]:
        """Remove a provider from the registry."""
        return self._providers.pop(provider_id, None)

    def clear(self) -> None:
        """Clear all registered providers (primarily for test teardown)."""
        self._providers.clear()
