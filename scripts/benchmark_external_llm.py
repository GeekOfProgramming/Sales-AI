#!/usr/bin/env python3
"""
scripts/benchmark_external_llm.py
SalesAI S3.0 CLI — External LLM Benchmark Runner & Paid-Dispatch Safety Gates.

Usage examples:
    # 1. Default offline dry-run benchmark (all 7 tasks, Tier 1 sequential):
    python scripts/benchmark_external_llm.py

    # 2. Quick smoke test (cap 2 cases per task):
    python scripts/benchmark_external_llm.py --max-cases 2

    # 3. Focus on a specific task:
    python scripts/benchmark_external_llm.py --task website_extraction:pass_a

    # 4. Focus on a specific candidate:
    python scripts/benchmark_external_llm.py --candidate t1_c1

    # 5. Export JSON & CSV to custom directory:
    python scripts/benchmark_external_llm.py --output-dir runtime/benchmarks_s3 --format all

    # 6. Live attempt (strictly fails closed in S3.0):
    python scripts/benchmark_external_llm.py --live
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# Ensure SalesAI package root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.errors import LLMNotConfiguredError, LLMBudgetExceededError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SalesAI S3.0 External LLM Benchmark Runner (Offline-First Dry-Run)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Execute offline benchmark using deterministic mock provider (default: True).",
    )
    parser.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Attempt live commercial execution (requires --confirm-paid).",
    )
    parser.add_argument(
        "--confirm-paid",
        dest="confirm_paid",
        action="store_true",
        default=False,
        help="Second required authorization key for live execution confirming paid commercial calls.",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=1,
        help="Maximum requests permitted in live mode (safety ceiling: 20, default: 1).",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.10,
        help="Maximum spend in USD permitted in live mode (safety ceiling: $1.00, default: 0.10).",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="all",
        help="Filter to specific task (e.g. 'website_extraction:pass_a', 'job_analysis', or 'all').",
    )
    parser.add_argument(
        "--candidate",
        type=str,
        default=None,
        help="Evaluate specific candidate (e.g. 't1_c1', 't1_c2', 't1_c5') instead of sequential Tier 1 evaluation.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Maximum fixture cases to evaluate per task (useful for quick smoke tests).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to persistent SQLite database for live ledger (default: data/benchmark_budget.db in live mode, :memory: in dry-run).",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        choices=["openai", "gemini", "groq", "openrouter", "cohere", "nvidia", "cloudflare", "xai"],
        help="Target provider for live smoke benchmark (openai, gemini, groq, openrouter, cohere, nvidia, cloudflare, xai).",
    )
    parser.add_argument(
        "--free-only",
        dest="free_only",
        action="store_true",
        default=False,
        help="Fail-closed restriction: refuse execution if provider is not verified free tier (FREE_ELIGIBILITY_UNCONFIRMED).",
    )
    parser.add_argument(
        "--connectivity-smoke",
        dest="connectivity_smoke",
        action="store_true",
        default=False,
        help="Execute single-case connectivity smoke (SMOKE_ONLY). Capped at 1 case and 1 request.",
    )
    parser.add_argument(
        "--require-cohere-trial",
        dest="require_cohere_trial",
        action="store_true",
        default=False,
        help="Fail-closed restriction: enforce verified Cohere Trial key entitlement before inference.",
    )
    parser.add_argument(
        "--cohere-trial-timeout-seconds",
        type=float,
        default=None,
        help="Explicit bounded timeout override in seconds for Cohere Trial smoke (max 60.0).",
    )
    parser.add_argument(
        "--allow-xai-paid",
        dest="allow_xai_paid",
        action="store_true",
        default=False,
        help="Explicit operator authorization allowing chargeable generation on xAI Grok.",
    )
    parser.add_argument(
        "--probe-models",
        dest="probe_models",
        action="store_true",
        default=False,
        help="Run read-only GET /models metadata probe for the selected provider without generating text.",
    )
    parser.add_argument(
        "--inter-request-delay",
        dest="inter_request_delay",
        type=float,
        default=0.0,
        help="Inter-request delay pacing in seconds between consecutive requests (0.0 to 10.0, default: 0.0).",
    )
    parser.add_argument(
        "--campaign-id",
        type=str,
        default=None,
        help="Persistent campaign ID for shared campaign budget and request cap tracking across runs.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="runtime/benchmarks",
        help="Directory to store generated JSON, CSV, and Markdown reports.",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["all", "json", "csv", "markdown"],
        default="all",
        help="Report export format (default: all).",
    )
    return parser


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    parsed = parser.parse_args(args)

    # Validate --inter-request-delay
    delay = parsed.inter_request_delay
    if (
        not isinstance(delay, (int, float))
        or isinstance(delay, bool)
        or math.isnan(delay)
        or math.isinf(delay)
        or delay < 0.0
        or delay > 10.0
    ):
        parser.error(
            f"Invalid --inter-request-delay: must be a non-negative finite number <= 10.0 (got {delay!r})."
        )

    return parsed


def main() -> int:
    args = parse_args()

    # Fast path: Read-only metadata probe (zero generation)
    if args.probe_models:
        from scripts.probe_llm_providers import probe_single_provider, print_probe_result
        from sales_engine.benchmark.provider_catalog import PROVIDER_SPECS_BY_ID, get_provider_spec
        print("=" * 72)
        print("SalesAI S3.3 - Provider Metadata & Authentication Probe (--probe-models)")
        print("=" * 72)
        prov_key = args.provider or "openai"
        spec = get_provider_spec(prov_key)
        if not spec:
            print(f"Error: Unknown provider '{prov_key}'.", file=sys.stderr)
            return 2
        res = probe_single_provider(spec)
        print_probe_result(res)
        return 0 if res["status"] in ("AUTHENTICATED", "NOT_CONFIGURED") else 1

    # Connectivity smoke overrides: cap at exactly 1 case and 1 request
    eff_max_cases = 1 if args.connectivity_smoke else args.max_cases
    eff_max_requests = 1 if args.connectivity_smoke else args.max_requests
    eff_output_dir = (
        f"{args.output_dir}/{args.provider}"
        if args.provider and args.output_dir == "runtime/benchmarks"
        else args.output_dir
    )

    print("=" * 72)
    print("SalesAI S3.4 - Seven-Provider External Benchmark & Safe Inference Readiness")
    print("=" * 72)
    print(f"Mode: {'OFFLINE DRY-RUN (Deterministic Mock)' if args.dry_run else 'LIVE DISPATCH'}")
    if not args.dry_run:
        print(f"  Target Provider: {args.provider or 'openai (default)'}")
        print(f"  Confirm Paid Key: {args.confirm_paid}")
        print(f"  Free-Only Gate: {args.free_only}")
        print(f"  Require Cohere Trial: {args.require_cohere_trial}")
        if args.cohere_trial_timeout_seconds is not None:
            print(f"  Cohere Trial Timeout Override: {args.cohere_trial_timeout_seconds}s")
        print(f"  Connectivity Smoke: {args.connectivity_smoke}")
        print(f"  Max Requests Cap: {eff_max_requests}")
        print(f"  Max Spend Cap: ${args.max_cost_usd:.2f}")
        print(f"  Ledger DB: {args.db_path or 'data/benchmark_budget.db'}")
    if args.inter_request_delay > 0.0:
        print(f"  Inter-Request Delay: {args.inter_request_delay}s")
    if args.campaign_id:
        print(f"  Campaign ID: {args.campaign_id}")
    print(f"Task Filter: {args.task}")
    print(f"Candidate Filter: {args.candidate or 'Selected Provider Tier 1 Candidate'}")
    print(f"Max Cases per Task: {eff_max_cases or 'All available fixtures'}")
    print(f"Output Directory: {eff_output_dir}")
    print("-" * 72)

    try:
        runner = ExternalBenchmarkRunner(
            dry_run=args.dry_run,
            confirm_paid=args.confirm_paid,
            max_requests=eff_max_requests,
            max_cost_usd=args.max_cost_usd,
            db_path=args.db_path,
            provider_filter=args.provider,
            free_only=args.free_only,
            allow_xai_generation=args.allow_xai_paid,
            is_connectivity_smoke=args.connectivity_smoke,
            require_cohere_trial=args.require_cohere_trial,
            cohere_trial_timeout_seconds=args.cohere_trial_timeout_seconds,
            inter_request_delay_seconds=args.inter_request_delay,
            campaign_id=args.campaign_id,
        )
    except (LLMNotConfiguredError, LLMBudgetExceededError) as e:
        print(f"\n[SAFETY GATE TRIPPED] Execution Refused:\n  {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[INITIALIZATION ERROR] Failed to initialize runner:\n  {e}", file=sys.stderr)
        return 2

    if hasattr(runner, "historical_preflight_summary"):
        hist = runner.historical_preflight_summary
        print("\nHistorical Ledger Preflight Summary:")
        print(f"  Operational DB: {hist.get('db_path')}")
        print(f"  Historical Exists: {hist.get('exists')}")
        print(f"  Historical Reservations: {hist.get('total_reservations', 0)}")
        print(f"  Historical Commercial Exposure: ${hist.get('total_commercial_usd', 0.0):.6f}")
        print(f"  Historical Billable Spend: ${hist.get('total_billable_usd', 0.0):.6f}")
        print(f"  Historical Uncertain Exposure: ${hist.get('uncertain_commercial_usd', 0.0):.6f}")

    print("\nRunning benchmark evaluation...")
    try:
        report = runner.run_benchmark(
            task_filter=args.task,
            candidate_filter=args.candidate,
            max_cases_per_task=eff_max_cases,
        )
    except (LLMNotConfiguredError, LLMBudgetExceededError) as e:
        print(f"\n[SAFETY GATE TRIPPED] Execution Refused:\n  {e}", file=sys.stderr)
        return 1

    print("\nBenchmark Execution Completed:")
    print(f"  Run ID: {report.run_id}")
    print(f"  Total Cases Executed: {report.total_cases_run}")
    print(f"  Total Cost: ${report.total_cost_usd:.6f}")
    print(f"  Tasks Evaluated: {len(report.tasks_evaluated)}")

    if not args.dry_run:
        summary = runner.get_run_ledger_summary(report.run_id)
        print("\nSQLite Ledger Audit Summary:")
        print(f"  Database Path: {summary['db_path']}")
        print(f"  Total Reservations: {summary['reservations_count']}")
        print(f"  Status Counts: {summary['status_counts']}")
        print(f"  Total Reconciled Spend: ${summary['reconciled_cost_usd']:.6f}")
        print(f"  Total Uncertain Spend: ${summary['uncertain_cost_usd']:.6f}")

    output_dir = Path(eff_output_dir)
    report_gen = S3BenchmarkReportGenerator(output_dir=output_dir)

    print("\nGenerating Benchmark Reports...")
    if args.format == "all":
        paths = report_gen.generate_all(report)
        for fmt, p in paths.items():
            print(f"  [{fmt.upper()}] {p}")
    elif args.format == "json":
        p = report_gen.export_json(report)
        print(f"  [JSON] {p}")
    elif args.format == "csv":
        p = report_gen.export_csv(report)
        print(f"  [CSV] {p}")
    elif args.format == "markdown":
        p = report_gen.export_markdown(report)
        print(f"  [MARKDOWN] {p}")

    print("\nProposed Candidate Recommendations (Proposals Only):")
    if report.recommended_candidates:
        for task, rec in report.recommended_candidates.items():
            print(
                f"  - {task}: Candidate '{rec['candidate_id']}' ({rec['exact_model_id']}) "
                f"| Pass Rate: {rec['overall_pass_rate']:.1%} | Safety Violations: {rec['safety_violations']}"
            )
    else:
        print("  - No candidates met full qualification criteria.")

    print("\n" + "=" * 72)
    print("NOTE: Model selections are proposals only. Operator authentication is required")
    print("before any model can be approved for live production routing.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
