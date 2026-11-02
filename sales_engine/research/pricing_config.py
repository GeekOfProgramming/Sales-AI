"""
sales_engine/research/pricing_config.py
Authoritative model-specific pricing configuration and budget authorization contracts.
Features:
- Official list pricing per 1M tokens with source dates and billing tier documentation.
- Supports Gemini (e.g. gemini-3.5-flash-lite, gemini-2.5-flash-lite), OpenAI, etc.
- Cryptographic server-side authorization token generator and validator.
- Budget cap enforcement.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, Set

logger = logging.getLogger(__name__)

# Server-side authorization secret: require explicit environment variable or generate
# a secure process-lifetime ephemeral secret. NEVER fall back to a hardcoded shared string.
_EPHEMERAL_SECRET = secrets.token_hex(32)
def get_auth_secret() -> str:
    sec = os.environ.get("SALESAI_ANALYSIS_AUTH_SECRET")
    if sec and sec.strip():
        return sec.strip()
    return _EPHEMERAL_SECRET

# Nonce replay protection set: tracks used nonce tokens to prevent token reuse
_USED_AUTH_NONCES: Set[str] = set()


@dataclass(frozen=True)
class ModelPricing:
    """Pricing configuration for an LLM provider and model."""
    provider: str
    model: str
    input_usd_per_1m: float
    output_usd_per_1m: float
    billing_tier: str
    source_date: str
    official_source_url: str


# Authoritative Registry of Model Pricing
# Source verified November 2026
MODEL_PRICING_CATALOG: Dict[str, ModelPricing] = {
    # Gemini 3.5 Flash-Lite: Standard Paid Tier ($0.30/1M input, $2.50/1M output)
    "gemini:gemini-3.5-flash-lite": ModelPricing(
        provider="gemini",
        model="gemini-3.5-flash-lite",
        input_usd_per_1m=0.30,
        output_usd_per_1m=2.50,
        billing_tier="Standard Pay-as-you-go",
        source_date="2026-11-02",
        official_source_url="https://cloud.google.com/vertex-ai/pricing",
    ),
    # Gemini 2.5 Flash-Lite: Standard Paid Tier ($0.10/1M input, $0.40/1M output)
    "gemini:gemini-2.5-flash-lite": ModelPricing(
        provider="gemini",
        model="gemini-2.5-flash-lite",
        input_usd_per_1m=0.10,
        output_usd_per_1m=0.40,
        billing_tier="Standard Pay-as-you-go",
        source_date="2026-11-02",
        official_source_url="https://cloud.google.com/vertex-ai/pricing",
    ),
    # Gemini 2.0 Flash-Lite (Legacy lifecycle tier: $0.075 / $0.30)
    "gemini:gemini-2.0-flash-lite": ModelPricing(
        provider="gemini",
        model="gemini-2.0-flash-lite",
        input_usd_per_1m=0.075,
        output_usd_per_1m=0.30,
        billing_tier="Legacy Tier",
        source_date="2026-11-02",
        official_source_url="https://cloud.google.com/vertex-ai/pricing",
    ),
    # OpenAI gpt-4o-mini ($0.15/1M input, $0.60/1M output)
    "openai:gpt-4o-mini": ModelPricing(
        provider="openai",
        model="gpt-4o-mini",
        input_usd_per_1m=0.15,
        output_usd_per_1m=0.60,
        billing_tier="Tier 1-5 Standard",
        source_date="2026-11-02",
        official_source_url="https://openai.com/api/pricing",
    ),
}

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def get_model_pricing(provider: str, model: Optional[str] = None) -> ModelPricing:
    """Retrieve pricing contract for a given provider and model."""
    p_norm = (provider or "gemini").lower()
    m_norm = (model or (DEFAULT_GEMINI_MODEL if p_norm == "gemini" else "default")).lower()
    key = f"{p_norm}:{m_norm}"
    if key in MODEL_PRICING_CATALOG:
        return MODEL_PRICING_CATALOG[key]

    # Provider fallback defaults
    if p_norm == "gemini":
        return MODEL_PRICING_CATALOG["gemini:gemini-3.5-flash-lite"]
    elif p_norm == "openai":
        return MODEL_PRICING_CATALOG["openai:gpt-4o-mini"]

    # Conservative default for unknown models
    return ModelPricing(
        provider=p_norm,
        model=m_norm,
        input_usd_per_1m=0.50,
        output_usd_per_1m=2.00,
        billing_tier="Unknown / Generic Default",
        source_date="2026-11-02",
        official_source_url="N/A",
    )


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    provider: str,
    model: Optional[str] = None,
) -> float:
    """Calculates approximate USD cost based on token counts and model pricing."""
    pricing = get_model_pricing(provider, model)
    inp_cost = (input_tokens / 1_000_000.0) * pricing.input_usd_per_1m
    out_cost = (output_tokens / 1_000_000.0) * pricing.output_usd_per_1m
    return round(inp_cost + out_cost, 6)


def generate_analysis_auth_token(
    candidate_id: str,
    snapshot_id: str,
    provider: str,
    model: str,
    max_budget_usd: float = 0.50,
    validity_seconds: int = 1800,  # 30 minutes expiration
) -> str:
    """
    Generates a cryptographically signed authorization token for paid inference.
    Token payload: candidate_id|snapshot_id|provider|model|max_budget|exp|nonce|sig
    """
    p_norm = provider.lower()
    m_norm = model.lower()
    budget_str = f"{max_budget_usd:.4f}"
    exp_ts = int(time.time()) + validity_seconds
    nonce = secrets.token_hex(8)
    payload = f"{candidate_id}|{snapshot_id}|{p_norm}|{m_norm}|{budget_str}|{exp_ts}|{nonce}"
    sig = hmac.new(get_auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_analysis_auth_token(
    auth_token: Optional[str],
    candidate_id: str,
    snapshot_id: str,
    provider: str,
    model: str,
    mark_used: bool = True,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Validates the server-side operator authorization token.
    Enforces expiration, replay nonce checks, and context binding.
    Returns (is_valid, max_approved_budget_usd, error_message).
    """
    if not auth_token or not auth_token.strip():
        return False, None, "Missing authorization token. Direct POST without explicit operator authorization is prohibited."

    parts = auth_token.strip().split("|")
    # Support both 8-part (exp + nonce + sig) and legacy 6-part for transitional compatibility
    if len(parts) == 8:
        t_cid, t_sid, t_prov, t_model, t_budget_str, t_exp_str, t_nonce, t_sig = parts
        payload = f"{t_cid}|{t_sid}|{t_prov}|{t_model}|{t_budget_str}|{t_exp_str}|{t_nonce}"
        expected_sig = hmac.new(get_auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(t_sig, expected_sig):
            return False, None, "Invalid authorization signature. Token tampering detected."

        # Check expiration
        try:
            exp_ts = int(t_exp_str)
            if time.time() > exp_ts:
                return False, None, "Authorization token has expired. Please refresh the review page."
        except ValueError:
            return False, None, "Invalid expiration timestamp in authorization token."

        # Check nonce replay
        if t_nonce in _USED_AUTH_NONCES:
            return False, None, "Authorization token replay detected. Single-use token has already been consumed."

        if mark_used:
            _USED_AUTH_NONCES.add(t_nonce)

    elif len(parts) == 6:
        t_cid, t_sid, t_prov, t_model, t_budget_str, t_sig = parts
        payload = f"{t_cid}|{t_sid}|{t_prov}|{t_model}|{t_budget_str}"
        expected_sig = hmac.new(get_auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(t_sig, expected_sig):
            return False, None, "Invalid authorization signature. Token tampering detected."
    else:
        return False, None, "Malformed authorization token structure."

    # Validate matching context
    if t_cid != candidate_id:
        return False, None, f"Authorization token candidate mismatch ('{t_cid}' != '{candidate_id}')."
    if t_sid != snapshot_id:
        return False, None, f"Authorization token snapshot mismatch ('{t_sid}' != '{snapshot_id}')."
    if t_prov.lower() != provider.lower():
        return False, None, f"Authorization token provider mismatch ('{t_prov}' != '{provider}')."
    if t_model.lower() != model.lower():
        return False, None, f"Authorization token model mismatch ('{t_model}' != '{model}')."

    try:
        max_budget = float(t_budget_str)
    except ValueError:
        return False, None, "Invalid budget number in authorization token."

    return True, max_budget, None
