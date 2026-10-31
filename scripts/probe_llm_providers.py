#!/usr/bin/env python3
"""
scripts/probe_llm_providers.py
SalesAI S3.4 CLI — Metadata & Authentication Probe for Seven LLM Providers.

Performs read-only HTTP GET queries to verify authentication, endpoint reachability,
and candidate model availability without initiating any text generation or token spend.

Supports all seven canonical providers:
  openai, gemini, groq, openrouter, cohere, nvidia, cloudflare (and historical xai).

Guarantees:
- Zero prompt or completion generation.
- Zero credential exposure in console, logs, or reports.
- Safe structured diagnostics with provider-specific API nuances.
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
    ALL_SUPPORTED_PROVIDERS,
    CANONICAL_SEVEN_PROVIDERS,
    PROVIDER_SPECS_BY_ID,
    ProviderConnectionSpec,
    get_provider_spec,
    list_canonical_seven_providers,
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
from sales_engine.llm_gateway.providers.cloudflare_provider import CloudflareProvider
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
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

    # Instantiate provider adapter
    try:
        if spec.provider_id == "cohere":
            provider = CohereProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key.strip(),
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
            )
        elif spec.provider_id == "cloudflare":
            provider = CloudflareProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key.strip(),
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
            )
        else:
            extra_headers = spec.capabilities.get("extra_headers")
            provider = OpenAIProvider(
                provider_id=spec.provider_id,
                base_url=spec.base_url,
                allowed_hosts=spec.allowed_hosts,
                env_var=spec.env_var,
                api_key=api_key.strip(),
                default_model=spec.exact_model_id,
                model_capabilities={spec.exact_model_id: spec.capabilities},
                extra_headers=extra_headers,
            )

        res = provider.probe_models(timeout_seconds=15.0)
        target_model = spec.exact_model_id
        model_ids = res["model_ids"]

        target_model_present = target_model in model_ids
        if not target_model_present:
            # Check prefix/suffix matches (e.g. models/gemini-2.5-flash-lite or case-insensitive)
            for m in model_ids:
                cleaned_m = m.replace("models/", "").lower()
                cleaned_target = target_model.replace("models/", "").lower()
                if cleaned_target == cleaned_m or cleaned_target in cleaned_m or cleaned_m in cleaned_target:
                    target_model_present = True
                    break

        extra_notes = []
        if spec.provider_id == "openrouter":
            extra_notes.append("OpenRouter Free Tier requires explicit :free model tag.")
        elif spec.provider_id == "nvidia":
            extra_notes.append("Catalog availability does not guarantee active inference entitlement.")
        elif spec.provider_id == "cloudflare":
            extra_notes.append("Cloudflare Workers AI validated with verified 32-hex Account ID.")

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
            "notes": " ".join(extra_notes),
        }
    except LLMNotConfiguredError as e:
        return {
            "provider_id": spec.provider_id,
            "status": "NOT_CONFIGURED",
            "message": str(e),
            "target_model": spec.exact_model_id,
            "billing_mode": spec.billing_mode,
            "base_url": spec.base_url,
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
        avail_str = "YES (Verified in catalog)" if result.get("target_model_available") else "NOT IN TOP LIST"
        print(f"  Target Model:  {avail_str}")
        sample = result.get("model_ids_sample", [])
        if sample:
            sample_str = ", ".join(sample[:5])
            if len(sample) > 5:
                sample_str += f" ... (+{result.get('model_count') - 5} more)"
            print(f"  Sample Models: {sample_str}")
        if result.get("notes"):
            print(f"  Advisory Note: {result.get('notes')}")
    elif status == "NOT_CONFIGURED":
        print(f"  Notice:        {result.get('message')}")
    else:
        print(f"  Error Code:    {result.get('error_code', 'UNKNOWN')}")
        print(f"  Message:       {result.get('message', '')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SalesAI S3.4 — Seven-Provider Metadata & Authentication Probe (Zero-Generation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="all",
        choices=ALL_SUPPORTED_PROVIDERS + ["all"],
        help="Target provider to probe (default: all seven canonical providers).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 72)
    print("SalesAI S3.4 - Seven-Provider Metadata & Authentication Probe")
    print("=" * 72)
    print("Zero-Generation Guarantee: Performs read-only model/key metadata queries.")
    print("Zero-Secret Guarantee: No API keys, credentials, or prompts displayed.")
    print("-" * 72)

    if args.provider == "all":
        target_specs = [get_provider_spec(p) for p in CANONICAL_SEVEN_PROVIDERS]
        target_specs = [s for s in target_specs if s is not None]
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
