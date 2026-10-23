#!/usr/bin/env python3
"""
scripts/probe_llm_providers.py
SalesAI S3.3 CLI — Metadata & Authentication Probe for LLM Providers.

Performs read-only HTTP GET /models queries to verify authentication, endpoint reachability,
and candidate model availability without initiating any text generation or token spend.

Usage examples:
    # 1. Probe Google Gemini metadata/auth:
    python scripts/probe_llm_providers.py --provider gemini

    # 2. Probe GroqCloud metadata/auth:
    python scripts/probe_llm_providers.py --provider groq

    # 3. Probe xAI Grok metadata/auth (safe credential check):
    python scripts/probe_llm_providers.py --provider xai

    # 4. Probe OpenAI metadata/auth:
    python scripts/probe_llm_providers.py --provider openai

    # 5. Probe all four providers sequentially:
    python scripts/probe_llm_providers.py --provider all
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure SalesAI package root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sales_engine.benchmark.provider_catalog import (
    PROVIDER_SPECS_BY_ID,
    ProviderConnectionSpec,
    get_provider_spec,
    list_supported_providers,
)
from sales_engine.llm_gateway.errors import (
    LLMAuthenticationError,
    LLMNotConfiguredError,
    LLMProviderUnavailableError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider


def probe_single_provider(spec: ProviderConnectionSpec) -> Dict[str, Any]:
    """
    Perform a metadata-only probe for a specific provider.
    Guarantees: Zero text generation, zero secret leakage, safe error formatting.
    """
    env_var = spec.env_var
    api_key = os.environ.get(env_var)

    if not api_key or not api_key.strip():
        return {
            "provider_id": spec.provider_id,
            "status": "NOT_CONFIGURED",
            "message": f"Environment variable '{env_var}' is not set in the current process.",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }

    provider = OpenAIProvider(
        provider_id=spec.provider_id,
        base_url=spec.base_url,
        allowed_hosts=spec.allowed_hosts,
        env_var=spec.env_var,
        api_key=api_key.strip(),
        default_model=spec.exact_model_id,
        model_capabilities={spec.exact_model_id: spec.capabilities},
    )

    try:
        res = provider.probe_models(timeout_seconds=15.0)
        target_model_present = spec.exact_model_id in res["model_ids"]
        # Partial model match check (some providers prefix or suffix IDs)
        if not target_model_present:
            for m in res["model_ids"]:
                if spec.exact_model_id.lower() in m.lower():
                    target_model_present = True
                    break

        return {
            "provider_id": spec.provider_id,
            "status": "AUTHENTICATED",
            "status_code": res["status_code"],
            "latency_ms": res["latency_ms"],
            "model_count": res["model_count"],
            "target_model": spec.exact_model_id,
            "target_model_available": target_model_present,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
            "model_ids_sample": res["model_ids"][:10],
        }
    except LLMAuthenticationError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "AUTH_FAILED",
            "error_code": e.error_code,
            "message": "Authentication failed. Check API key credentials.",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }
    except LLMQuotaExceededError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "QUOTA_EXHAUSTED",
            "error_code": e.error_code,
            "message": "Account quota or billing credits exhausted (HTTP 429).",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }
    except LLMRateLimitError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "RATE_LIMITED",
            "error_code": e.error_code,
            "message": "Rate limit exceeded (HTTP 429).",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }
    except LLMTimeoutError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "TIMEOUT",
            "error_code": e.error_code,
            "message": "Connection timed out after 15s.",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }
    except LLMProviderUnavailableError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "UNAVAILABLE",
            "error_code": e.error_code,
            "message": f"Provider endpoint unavailable: {e.message}",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }
    except Exception as e:
        return {
            "provider_id": spec.provider_id,
            "status": "ERROR",
            "error_code": type(e).__name__,
            "message": f"Unexpected error during probe: {type(e).__name__}",
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
        }


def print_probe_result(result: Dict[str, Any]) -> None:
    """Print key-safe, structured metadata probe result."""
    prov = result["provider_id"].upper()
    status = result["status"]
    print(f"\n[{prov}] Status: {status}")
    print(f"  Base URL:      {result.get('base_url', '')}")
    print(f"  Billing Mode:  {result.get('billing_mode', '')}")
    print(f"  Target Model:  {result.get('target_model', '')}")

    if status == "AUTHENTICATED":
        print(f"  HTTP Status:   {result.get('status_code')}")
        print(f"  Latency:       {result.get('latency_ms')} ms")
        print(f"  Models Found:  {result.get('model_count')}")
        avail_str = "YES (Verified available)" if result.get("target_model_available") else "NOT IN TOP LIST"
        print(f"  Target Model:  {avail_str}")
        sample = result.get("model_ids_sample", [])
        if sample:
            sample_str = ", ".join(sample[:5])
            if len(sample) > 5:
                sample_str += f" ... (+{result.get('model_count') - 5} more)"
            print(f"  Sample Models: {sample_str}")
    elif status == "NOT_CONFIGURED":
        print(f"  Notice:        {result.get('message')}")
    else:
        print(f"  Error Code:    {result.get('error_code', 'UNKNOWN')}")
        print(f"  Message:       {result.get('message', '')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SalesAI S3.3 — Metadata & Authentication Probe (Zero-Generation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="all",
        choices=["openai", "gemini", "groq", "xai", "all"],
        help="Target provider to probe (default: all).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 72)
    print("SalesAI S3.3 - LLM Provider Metadata & Authentication Probe")
    print("=" * 72)
    print("Zero-Generation Guarantee: Performs read-only GET /models requests.")
    print("Zero-Secret Guarantee: No API keys, credentials, or prompts displayed.")
    print("-" * 72)

    if args.provider == "all":
        target_specs = list(PROVIDER_SPECS_BY_ID.values())
    else:
        spec = get_provider_spec(args.provider)
        if not spec:
            print(f"Error: Unknown provider '{args.provider}'.", file=sys.stderr)
            return 2
        target_specs = [spec]

    any_failed = False
    for spec in target_specs:
        result = probe_single_provider(spec)
        print_probe_result(result)
        if result["status"] not in ("AUTHENTICATED", "NOT_CONFIGURED"):
            any_failed = True

    print("\n" + "=" * 72)
    print("Probe completed. Remember: Metadata connectivity is not model qualification.")
    print("=" * 72)

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
