"""
sales_engine/benchmark/provider_catalog.py
Authoritative provider connection catalog for SalesAI S3.3 Four-Provider Onboarding.

Pins exact HTTPS endpoints, environment variable names, capabilities, billing modes,
and verified source references for:
- OpenAI (gpt-6-luna) -> t1_c1
- Google Gemini (gemini-2.5-flash-lite) -> t1_c2
- GroqCloud (openai/gpt-oss-20b) -> t1_c3
- xAI Grok (grok-4-fast) -> t1_c4 (Metadata-only by default; generation disabled)

Strict security guarantees:
- Zero API key values in code or configuration.
- Strict HTTPS endpoint allowlists per provider.
- No implicit key fallback across vendors.
- Exact model IDs matching official provider documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sales_engine.llm_gateway.model_catalog import PricingMetadata


@dataclass(frozen=True)
class ProviderConnectionSpec:
    """Immutable specification for an approved external LLM provider connection."""

    provider_id: str
    candidate_id: str
    exact_model_id: str
    base_url: str
    allowed_hosts: Set[str]
    env_var: str
    billing_mode: str  # VERIFIED_PAID, VERIFIED_FREE_TIER, UNKNOWN
    is_generation_enabled: bool
    pricing: PricingMetadata
    capabilities: Dict[str, Any]
    verified_source: str
    checked_date: str
    verification_notes: str


# ===========================================================================
# Authoritative S3.3 Four-Provider Connection Specs
# ===========================================================================

OPENAI_SPEC = ProviderConnectionSpec(
    provider_id="openai",
    candidate_id="t1_c1",
    exact_model_id="gpt-6-luna",
    base_url="https://api.openai.com/v1",
    allowed_hosts={"api.openai.com"},
    env_var="OPENAI_API_KEY",
    billing_mode="VERIFIED_PAID",
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.10,
        output_per_1m=0.50,
        effective_date="2026-10-22",
        source_url="https://openai.com/api/pricing/",
    ),
    capabilities={
        "is_reasoning_model": True,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_completion_tokens",
    },
    verified_source="https://openai.com/api/pricing/",
    checked_date="2026-10-22",
    verification_notes="S3.2/S3.3 verified Tier 1 candidate #1 (gpt-6-luna via OpenAI)",
)

GEMINI_SPEC = ProviderConnectionSpec(
    provider_id="gemini",
    candidate_id="t1_c2",
    exact_model_id="gemini-2.5-flash-lite",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    allowed_hosts={"generativelanguage.googleapis.com"},
    env_var="GEMINI_API_KEY",
    billing_mode="VERIFIED_FREE_TIER",
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="FREE",
        input_per_1m=0.075,
        output_per_1m=0.30,
        effective_date="2026-10-23",
        source_url="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
    },
    verified_source="https://ai.google.dev/gemini-api/docs/pricing",
    checked_date="2026-10-23",
    verification_notes="S3.3 verified Tier 1 candidate #2 (gemini-2.5-flash-lite via Google Gemini Free Tier)",
)

GROQ_SPEC = ProviderConnectionSpec(
    provider_id="groq",
    candidate_id="t1_c3",
    exact_model_id="openai/gpt-oss-20b",
    base_url="https://api.groq.com/openai/v1",
    allowed_hosts={"api.groq.com"},
    env_var="GROQ_API_KEY",
    billing_mode="VERIFIED_FREE_TIER",
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="FREE",
        input_per_1m=0.075,
        output_per_1m=0.30,
        effective_date="2026-10-23",
        source_url="https://console.groq.com/docs/rate-limits",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
    },
    verified_source="https://console.groq.com/docs/rate-limits",
    checked_date="2026-10-23",
    verification_notes="S3.3 verified Tier 1 candidate #3 (openai/gpt-oss-20b via GroqCloud Free Plan)",
)

XAI_SPEC = ProviderConnectionSpec(
    provider_id="xai",
    candidate_id="t1_c4",
    exact_model_id="grok-4-fast",
    base_url="https://api.x.ai/v1",
    allowed_hosts={"api.x.ai"},
    env_var="XAI_API_KEY",
    billing_mode="UNKNOWN",
    is_generation_enabled=False,  # Generation strictly disabled until explicit operator authorization
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.50,
        output_per_1m=1.50,
        effective_date="2026-10-23",
        source_url="https://docs.x.ai/developers/rest-api-reference/inference/chat-completions",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
    },
    verified_source="https://docs.x.ai/developers/rest-api-reference/inference/chat-completions",
    checked_date="2026-10-23",
    verification_notes="S3.3 Tier 1 candidate #4 (grok-4-fast via xAI) - Metadata probe only; generation disabled by default",
)

PROVIDER_SPECS_BY_ID: Dict[str, ProviderConnectionSpec] = {
    "openai": OPENAI_SPEC,
    "gemini": GEMINI_SPEC,
    "groq": GROQ_SPEC,
    "xai": XAI_SPEC,
}

PROVIDER_SPECS_BY_CANDIDATE: Dict[str, ProviderConnectionSpec] = {
    "t1_c1": OPENAI_SPEC,
    "t1_c2": GEMINI_SPEC,
    "t1_c3": GROQ_SPEC,
    "t1_c4": XAI_SPEC,
}


def get_provider_spec(provider_id: str) -> Optional[ProviderConnectionSpec]:
    """Retrieve connection spec for a canonical provider identifier."""
    return PROVIDER_SPECS_BY_ID.get(provider_id.strip().lower())


def get_candidate_spec(candidate_id: str) -> Optional[ProviderConnectionSpec]:
    """Retrieve connection spec for a candidate ID."""
    return PROVIDER_SPECS_BY_CANDIDATE.get(candidate_id.strip().lower())


def list_supported_providers() -> List[str]:
    """List canonical provider keys supported in S3.3."""
    return list(PROVIDER_SPECS_BY_ID.keys())


def list_supported_candidates() -> List[str]:
    """List candidate slot IDs supported in S3.3."""
    return list(PROVIDER_SPECS_BY_CANDIDATE.keys())
