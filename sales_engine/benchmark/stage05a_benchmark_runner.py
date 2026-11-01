"""
sales_engine/benchmark/stage05a_benchmark_runner.py
SalesAI Stage 05A Runner for Multi-Provider LLM Research Benchmark.
Executes strictly controlled, zero-charge live inference requests across eligible providers
using the identical frozen compact evidence dataset and 13-field Lead Register schema.
Includes preflight billing and model eligibility gate, path-level schema validation,
and failure classification.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from sales_engine.benchmark.stage05a_schema import (
    STAGE_05A_OUTPUT_SCHEMA,
    BENCHMARK_SYSTEM_PROMPT,
    validate_stage05a_output,
)
from sales_engine.benchmark.dataset_formatter import build_compact_evidence_text
from sales_engine.benchmark.provider_catalog import (
    PROVIDER_SPECS_BY_CANDIDATE,
    ProviderConnectionSpec,
)
from sales_engine.research.research_store import ResearchSnapshotStore

BENCHMARK_RESULTS_DIR = Path("data/benchmark_results/stage05a")


def check_dispatch_eligibility(
    provider_id: str,
    model_id: str,
    candidate_id: str,
    allow_paid: bool = False,
    operator_authorized: bool = False,
) -> Tuple[bool, Optional[str], Optional[ProviderConnectionSpec]]:
    """
    Preflight safety check inside dispatch.
    Verifies:
    1. Candidate exists in PROVIDER_SPECS_BY_CANDIDATE.
    2. Provider identity matches spec.
    3. Exact model ID matches spec.
    4. Generation entitlement (is_generation_enabled).
    5. Billing eligibility (fail closed on VERIFIED_PAID unless allow_paid and operator_authorized;
       fail closed on UNKNOWN under free-only rules).
    Returns (is_eligible, rejection_reason, spec).
    """
    cand_key = candidate_id.strip().lower()
    spec = PROVIDER_SPECS_BY_CANDIDATE.get(cand_key)
    if not spec:
        return False, f"Unknown candidate ID '{candidate_id}' in catalog.", None

    if spec.provider_id.lower() != provider_id.strip().lower():
        return False, f"Provider mismatch: requested '{provider_id}' but candidate '{candidate_id}' is '{spec.provider_id}'.", spec

    if spec.exact_model_id != model_id.strip():
        return False, f"Model ID mismatch: requested '{model_id}' does not match catalog exact model '{spec.exact_model_id}'.", spec

    if not spec.is_generation_enabled:
        return False, f"Generation is disabled for candidate '{candidate_id}' ({spec.exact_model_id}).", spec

    billing = getattr(spec, "billing_mode", "UNKNOWN")
    if billing == "VERIFIED_PAID":
        if not (allow_paid and operator_authorized):
            return False, f"Billing safety violation: Paid inference is blocked for candidate '{candidate_id}' ({spec.exact_model_id}). Explicit operator authorization required.", spec
    elif billing == "UNKNOWN":
        return False, f"Billing safety violation: Model '{spec.exact_model_id}' has unverified billing mode 'UNKNOWN' and cannot be dispatched under free-only rules.", spec
    elif billing != "VERIFIED_FREE_TIER":
        return False, f"Billing safety violation: Unrecognized billing mode '{billing}'.", spec

    return True, None, spec


def run_benchmark_for_provider(
    provider_id: str,
    model_id: str,
    candidate_id: str,
    compact_text: str,
    compact_sha256: str,
    company_name: str = "Target Company",
    domain: str = "example.com",
    allow_paid: bool = False,
    operator_authorized: bool = False,
) -> Dict[str, Any]:
    """
    Executes a single, isolated, sequential inference request to the designated provider.
    Fails closed: captures status, timing, tokens, finish reason, and errors safely.
    Zero secret leakage in logged or saved output.
    """
    result_record = {
        "provider_id": provider_id,
        "exact_model_id": model_id,
        "candidate_id": candidate_id,
        "dataset_sha256": compact_sha256,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "NOT_EXECUTED",
        "http_status": None,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "reported_cost_usd": 0.0,
        "finish_reason": None,
        "schema_valid": False,
        "schema_violations": [],
        "raw_response": None,
        "parsed_json": None,
        "error_message": None,
    }

    # Preflight billing & dispatch eligibility check
    eligible, reason, spec = check_dispatch_eligibility(
        provider_id=provider_id,
        model_id=model_id,
        candidate_id=candidate_id,
        allow_paid=allow_paid,
        operator_authorized=operator_authorized,
    )
    if not eligible:
        result_record["status"] = "BLOCKED_BY_POLICY"
        result_record["error_message"] = reason
        return result_record

    # Generic prompt dynamically referencing company metadata (independent of hardcoded company values)
    user_prompt = (
        f"Analyze the following evidence dataset for company {company_name} (domain: {domain}).\n"
        f"Extract all factual details and evaluate candidate priority into the required JSON schema.\n\n"
        f"EVIDENCE DATASET (COMPACT REPRESENTATION):\n"
        f"{compact_text}\n\n"
        f"Respond with valid JSON adhering strictly to the schema. Output JSON only."
    )

    t0 = time.perf_counter()
    api_key = None

    if provider_id == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            result_record["status"] = "CREDENTIAL_MISSING"
            return result_record

        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 3000,
        }

    elif provider_id == "groq":
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            result_record["status"] = "CREDENTIAL_MISSING"
            return result_record

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "SalesAI-Benchmark/1.0",
        }
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 3000,
        }

    elif provider_id == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            result_record["status"] = "CREDENTIAL_MISSING"
            return result_record

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://pybim.it/salesai",
            "X-Title": "SalesAI",
        }
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 3000,
            "provider": {"allow_fallbacks": False},
        }

    else:
        result_record["status"] = "INELIGIBLE_OR_SKIPPED"
        result_record["error_message"] = f"Provider '{provider_id}' is not configured for Stage 05A inference."
        return result_record

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            latency = (time.perf_counter() - t0) * 1000.0
            body = resp.read().decode("utf-8")
            result_record["http_status"] = resp.status
            result_record["latency_ms"] = round(latency, 2)
            result_record["status"] = "SUCCESS"

            resp_data = json.loads(body)
            result_record["raw_response"] = resp_data

            # Extract usage
            usage = resp_data.get("usage", {})
            result_record["input_tokens"] = usage.get("prompt_tokens")
            result_record["output_tokens"] = usage.get("completion_tokens")

            # Extract content & finish reason
            choices = resp_data.get("choices", [])
            if choices:
                ch0 = choices[0]
                result_record["finish_reason"] = ch0.get("finish_reason")
                content = ch0.get("message", {}).get("content", "")
                try:
                    parsed = json.loads(content)
                    result_record["parsed_json"] = parsed
                    # Strict JSON Schema validation Draft-07 with unwrapping
                    is_valid, violations, normalized_data = validate_stage05a_output(parsed)
                    result_record["schema_valid"] = is_valid
                    result_record["schema_violations"] = violations
                except Exception as parse_err:
                    result_record["schema_valid"] = False
                    result_record["error_message"] = f"JSON parse error on model output: {parse_err}"

    except urllib.error.HTTPError as http_err:
        latency = (time.perf_counter() - t0) * 1000.0
        result_record["http_status"] = http_err.code
        result_record["latency_ms"] = round(latency, 2)
        err_body = http_err.read().decode("utf-8", errors="replace")

        # Refined failure classification
        if http_err.code == 403 or "1010" in err_body or "Cloudflare" in err_body:
            result_record["status"] = "ACCESS_BLOCKED"
        elif http_err.code == 429:
            result_record["status"] = "RATE_LIMITED"
        else:
            result_record["status"] = f"HTTP_{http_err.code}"

        clean_err = err_body[:500].replace(api_key, "[REDACTED]") if api_key else err_body[:500]
        result_record["error_message"] = f"HTTP {http_err.code}: {clean_err}"

    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000.0
        result_record["latency_ms"] = round(latency, 2)
        result_record["status"] = "ERROR"
        result_record["error_message"] = str(exc)

    return result_record
