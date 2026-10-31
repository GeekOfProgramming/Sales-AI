"""
sales_engine/benchmark/provider_catalog.py
Authoritative provider connection catalog for SalesAI S3.4 Seven-Provider Integration.

Pins exact HTTPS endpoints, environment variable names, capabilities, billing modes,
and verified source references for:
- OpenAI (gpt-6-luna) -> t1_c1
- Google Gemini (gemini-2.5-flash-lite) -> t1_c2
- GroqCloud (openai/gpt-oss-20b) -> t1_c3
- xAI Grok (grok-4-fast) -> t1_c4 (Historical S3.3; metadata-only probe; text generation locked)
- OpenRouter (meta-llama/llama-3.3-70b-instruct:free) -> t2_c1
- Cohere (command-r-08-2024) -> t2_c2
- NVIDIA NIM (meta/llama-3.3-70b-instruct) -> t2_c3
- Cloudflare Workers AI (@cf/meta/llama-3.1-8b-instruct) -> t2_c4

Strict security & governance guarantees:
- Zero API key values in code or configuration.
- Strict HTTPS endpoint allowlists per provider.
- No implicit key fallback across vendors.
- Exact model IDs matching official provider documentation.
- Rigorous Free/Paid/Unknown classification failing closed under --free-only.
- Preserved S3.3 historical evidence and candidate slot bindings.
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
# Authoritative S3.3/S3.4 Provider Connection Specs
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

GEMINI_2_5_SPEC = ProviderConnectionSpec(
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
    verification_notes="S3.3 Tier 1 candidate #2 (gemini-2.5-flash-lite; S3.4 live smoke returned HTTP 404 on v1beta/openai endpoint)",
)
GEMINI_SPEC = GEMINI_2_5_SPEC

GEMINI_3_5_SPEC = ProviderConnectionSpec(
    provider_id="gemini",
    candidate_id="t1_c5",
    exact_model_id="gemini-3.5-flash-lite",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    allowed_hosts={"generativelanguage.googleapis.com"},
    env_var="GEMINI_API_KEY",
    billing_mode="VERIFIED_FREE_TIER",
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="FREE",
        input_per_1m=0.30,
        output_per_1m=2.50,
        effective_date="2026-10-24",
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
    checked_date="2026-10-24",
    verification_notes="S3.5.1 verified Tier 1 candidate #5 (gemini-3.5-flash-lite via Google Gemini Free Tier, HTTP 200 verified, rate card: $0.30 in / $2.50 out per 1M)",
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

OPENROUTER_T2_C1_SPEC = ProviderConnectionSpec(
    provider_id="openrouter",
    candidate_id="t2_c1",
    exact_model_id="meta-llama/llama-3.3-70b-instruct:free",
    base_url="https://openrouter.ai/api/v1",
    allowed_hosts={"openrouter.ai"},
    env_var="OPENROUTER_API_KEY",
    billing_mode="VERIFIED_FREE_TIER",
    is_generation_enabled=False,  # Ineligible for live dispatch while absent from OpenRouter catalog
    pricing=PricingMetadata(
        currency="FREE",
        input_per_1m=0.0,
        output_per_1m=0.0,
        effective_date="2026-10-24",
        source_url="https://openrouter.ai/docs#free-models",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": False,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "extra_headers": {
            "HTTP-Referer": "https://pybim.it/salesai",
            "X-Title": "SalesAI",
        },
        "free_suffix_required": True,
        "provider_fallbacks": False,
    },
    verified_source="https://openrouter.ai/docs#free-models",
    checked_date="2026-10-24",
    verification_notes="Historical S3.4 Tier 2 candidate #1 (meta-llama/llama-3.3-70b-instruct:free; unavailable in catalog as of S3.5.3A; preserved for historical inspection)",
)

OPENROUTER_T2_C5_SPEC = ProviderConnectionSpec(
    provider_id="openrouter",
    candidate_id="t2_c5",
    exact_model_id="google/gemma-4-26b-a4b-it:free",
    base_url="https://openrouter.ai/api/v1",
    allowed_hosts={"openrouter.ai"},
    env_var="OPENROUTER_API_KEY",
    billing_mode="VERIFIED_FREE_TIER",
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="FREE",
        input_per_1m=0.0,
        output_per_1m=0.0,
        effective_date="2026-10-24",
        source_url="https://openrouter.ai/models/google/gemma-4-26b-a4b-it:free",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": False,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "extra_headers": {
            "HTTP-Referer": "https://pybim.it/salesai",
            "X-Title": "SalesAI",
        },
        "free_suffix_required": True,
        "provider_fallbacks": False,
    },
    verified_source="https://openrouter.ai/api/v1/models",
    checked_date="2026-10-24",
    verification_notes="S3.5.3B verified active Tier 2 candidate #5 (google/gemma-4-26b-a4b-it:free via OpenRouter Free Tier; verified 0.0 pricing, provider fallback disabled)",
)
OPENROUTER_SPEC = OPENROUTER_T2_C5_SPEC

COHERE_SPEC = ProviderConnectionSpec(
    provider_id="cohere",
    candidate_id="t2_c2",
    exact_model_id="command-r-08-2024",
    base_url="https://api.cohere.com/v2",
    allowed_hosts={"api.cohere.com"},
    env_var="COHERE_API_KEY",
    billing_mode="UNKNOWN",  # Trial rate limits (20 RPM/1000 mo); non-commercial trial, fail closed for --free-only
    is_generation_enabled=True,
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.15,
        output_per_1m=0.60,
        effective_date="2026-10-24",
        source_url="https://cohere.com/pricing",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "api_kind": "cohere_v2",
    },
    verified_source="https://cohere.com/pricing",
    checked_date="2026-10-24",
    verification_notes="S3.4 Tier 2 candidate #2 (command-r-08-2024 via Cohere v2 Chat API; Trial key eval)",
)

NVIDIA_SPEC = ProviderConnectionSpec(
    provider_id="nvidia",
    candidate_id="t2_c3",
    exact_model_id="meta/llama-3.3-70b-instruct",
    base_url="https://integrate.api.nvidia.com/v1",
    allowed_hosts={"integrate.api.nvidia.com"},
    env_var="NVIDIA_API_KEY",
    billing_mode="UNKNOWN",  # Evaluation credits expire; catalog access != inference entitlement; fail closed for --free-only
    is_generation_enabled=False,  # Historical model unavailable in NVIDIA NIM catalog as of S3.5.5A; fail closed
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.10,
        output_per_1m=0.40,
        effective_date="2026-10-24",
        source_url="https://build.nvidia.com/meta/llama-3-3-70b-instruct",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": True,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "requires_inference_entitlement": True,
    },
    verified_source="https://build.nvidia.com/meta/llama-3-3-70b-instruct",
    checked_date="2026-10-25",
    verification_notes="Historical S3.4 Tier 2 candidate #3 (meta/llama-3.3-70b-instruct; unavailable in NVIDIA NIM catalog as of S3.5.5A; generation disabled; catalog rechecked)",
)

CLOUDFLARE_SPEC = ProviderConnectionSpec(
    provider_id="cloudflare",
    candidate_id="t2_c4",
    exact_model_id="@cf/meta/llama-3.1-8b-instruct",
    base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
    allowed_hosts={"api.cloudflare.com"},
    env_var="CLOUDFLARE_API_TOKEN",
    billing_mode="UNKNOWN",  # 10,000 neurons/day allocation, but chargeable overage on paid plans; fail closed for --free-only
    is_generation_enabled=False,  # Historical model unavailable in Cloudflare catalog as of S3.5.5A; fail closed
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.05,
        output_per_1m=0.20,
        effective_date="2026-10-24",
        source_url="https://developers.cloudflare.com/workers-ai/platform/pricing/",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": False,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "requires_account_id": True,
    },
    verified_source="https://developers.cloudflare.com/workers-ai/platform/pricing/",
    checked_date="2026-10-25",
    verification_notes="Historical S3.4 Tier 2 candidate #4 (@cf/meta/llama-3.1-8b-instruct; unavailable in Cloudflare Workers AI catalog; preserved for historical records; generation disabled)",
)

CLOUDFLARE_T2_C4_SPEC = CLOUDFLARE_SPEC

CLOUDFLARE_T2_C6_SPEC = ProviderConnectionSpec(
    provider_id="cloudflare",
    candidate_id="t2_c6",
    exact_model_id="@cf/meta/llama-3.1-8b-instruct-fp8",
    base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
    allowed_hosts={"api.cloudflare.com"},
    env_var="CLOUDFLARE_API_TOKEN",
    billing_mode="UNKNOWN",  # 10,000 neurons/day allocation, overage on paid plans; fail closed for --free-only
    is_generation_enabled=False,  # Proposed replacement candidate for S3.5.4A; generation locked until authorized
    pricing=PricingMetadata(
        currency="USD",
        input_per_1m=0.152,
        output_per_1m=0.287,
        effective_date="2026-10-25",
        source_url="https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/",
    ),
    capabilities={
        "is_reasoning_model": False,
        "supports_json_schema": False,
        "supports_json_mode": True,
        "supports_system_prompt": True,
        "max_tokens_key": "max_tokens",
        "requires_account_id": True,
        "neurons_per_1m_input": 13778,
        "neurons_per_1m_output": 26128,
        "neuron_unit_cost_usd": 0.000011,
        "daily_free_neurons": 10000,
    },
    verified_source="https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct-fp8/",
    checked_date="2026-10-25",
    verification_notes="Proposed S3.5.4A/S3.5.5A Tier 2 candidate #6 (@cf/meta/llama-3.1-8b-instruct-fp8; official rates 13,778 in / 26,128 out neurons per 1M; published rate card $0.152/$0.287 per 1M; generation disabled pending account plan verification)",
)

CANONICAL_SEVEN_PROVIDERS: List[str] = [
    "openai",
    "gemini",
    "groq",
    "openrouter",
    "cohere",
    "nvidia",
    "cloudflare",
]

ALL_SUPPORTED_PROVIDERS: List[str] = [
    "openai",
    "gemini",
    "groq",
    "openrouter",
    "cohere",
    "nvidia",
    "cloudflare",
    "xai",
]

PROVIDER_SPECS_BY_ID: Dict[str, ProviderConnectionSpec] = {
    "openai": OPENAI_SPEC,
    "gemini": GEMINI_3_5_SPEC,
    "groq": GROQ_SPEC,
    "xai": XAI_SPEC,
    "openrouter": OPENROUTER_SPEC,
    "cohere": COHERE_SPEC,
    "nvidia": NVIDIA_SPEC,
    "cloudflare": CLOUDFLARE_SPEC,
}

PROVIDER_SPECS_BY_CANDIDATE: Dict[str, ProviderConnectionSpec] = {
    "t1_c1": OPENAI_SPEC,
    "t1_c2": GEMINI_2_5_SPEC,
    "t1_c3": GROQ_SPEC,
    "t1_c4": XAI_SPEC,
    "t1_c5": GEMINI_3_5_SPEC,
    "t2_c1": OPENROUTER_T2_C1_SPEC,
    "t2_c2": COHERE_SPEC,
    "t2_c3": NVIDIA_SPEC,
    "t2_c4": CLOUDFLARE_SPEC,
    "t2_c5": OPENROUTER_T2_C5_SPEC,
    "t2_c6": CLOUDFLARE_T2_C6_SPEC,
}


def get_provider_spec(provider_id: str, candidate_id: Optional[str] = None) -> Optional[ProviderConnectionSpec]:
    """
    Retrieve connection spec for a canonical provider identifier.
    If candidate_id is provided, checks if candidate matches provider.
    Fails closed and returns None if candidate_id does not belong to provider_id (prevents silent default fallback).
    """
    if candidate_id:
        cand_spec = PROVIDER_SPECS_BY_CANDIDATE.get(candidate_id.strip().lower())
        if cand_spec and cand_spec.provider_id == provider_id.strip().lower():
            return cand_spec
        return None
    return PROVIDER_SPECS_BY_ID.get(provider_id.strip().lower())


def get_candidate_spec(candidate_id: str) -> Optional[ProviderConnectionSpec]:
    """Retrieve connection spec for a candidate ID."""
    return PROVIDER_SPECS_BY_CANDIDATE.get(candidate_id.strip().lower())


def list_canonical_seven_providers() -> List[str]:
    """List the seven canonical active provider identifiers for S3.4."""
    return list(CANONICAL_SEVEN_PROVIDERS)


def list_supported_providers(all_providers: bool = False) -> List[str]:
    """
    List supported provider keys.
    Defaults to S3.3 historical baseline ('openai', 'gemini', 'groq', 'xai') for backward compatibility.
    Pass all_providers=True to retrieve all integrated providers including S3.4 extensions.
    """
    if all_providers:
        return list(PROVIDER_SPECS_BY_ID.keys())
    return ["openai", "gemini", "groq", "xai"]


def list_supported_candidates(all_candidates: bool = False) -> List[str]:
    """
    List candidate slot IDs.
    Defaults to S3.3 Tier 1 baseline ('t1_c1'..'t1_c4') for backward compatibility.
    Pass all_candidates=True to retrieve all candidate slots including S3.4 Tier 2 slots, S3.5.1 t1_c5, and S3.5.3B t2_c5.
    """
    if all_candidates:
        return list(PROVIDER_SPECS_BY_CANDIDATE.keys())
    return ["t1_c1", "t1_c2", "t1_c3", "t1_c4"]


# S3.5.3A/S3.5.3B Hardened explicit allowlist for verified free-tier models per provider.
# Generic provider identities or substring matching MUST NOT grant free-tier status.
EXPLICIT_VERIFIED_FREE_MODELS: Dict[str, Set[str]] = {
    "gemini": {
        "gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite",
    },
    "groq": {
        "openai/gpt-oss-20b",
    },
    "openrouter": {
        "google/gemma-4-26b-a4b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",  # Historical
    },
}


def classify_model_billing(provider_id: str, model_id: str) -> str:
    """
    Rigorously classify a provider/model pair as VERIFIED_FREE_TIER, VERIFIED_PAID, or UNKNOWN.
    Fails closed to UNKNOWN if free eligibility cannot be verified.

    Hardened in S3.5.3A/S3.5.3B:
    - Generic Groq provider identity does NOT establish that every Groq model is free.
    - Generic model IDs containing 'gemini' do NOT qualify for Free Tier.
    - Only explicit verified provider/model combinations receive VERIFIED_FREE_TIER.
    - OpenRouter requires explicit :free suffix AND membership in verified free model allowlist.
    - Unknown combinations return UNKNOWN.
    - Paid combinations remain blocked under --free-only.
    - Account-specific billing eligibility must not be inferred solely from a model catalog entry.
    """
    pid = provider_id.strip().lower()
    mid = model_id.strip().lower()

    if pid == "openai":
        return "VERIFIED_PAID"

    if pid == "gemini":
        # Only explicit verified Gemini models qualify for free tier
        if mid in EXPLICIT_VERIFIED_FREE_MODELS.get("gemini", set()):
            return "VERIFIED_FREE_TIER"
        return "UNKNOWN"

    if pid == "groq":
        # Only explicit verified Groq models qualify for free tier
        if mid in EXPLICIT_VERIFIED_FREE_MODELS.get("groq", set()):
            return "VERIFIED_FREE_TIER"
        return "UNKNOWN"

    if pid == "openrouter":
        # Only explicitly approved allowlisted models qualify for VERIFIED_FREE_TIER.
        # Unknown :free models must be rejected as UNKNOWN, not VERIFIED_FREE_TIER.
        # Models lacking the :free suffix are VERIFIED_PAID.
        if mid in EXPLICIT_VERIFIED_FREE_MODELS.get("openrouter", set()):
            if mid.endswith(":free"):
                return "VERIFIED_FREE_TIER"
            return "VERIFIED_PAID"
        if mid.endswith(":free"):
            return "UNKNOWN"
        return "VERIFIED_PAID"

    # Cohere, NVIDIA, Cloudflare, xAI have trial, temporary credits, or overage charges
    return "UNKNOWN"
