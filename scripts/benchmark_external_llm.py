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
import sys
from pathlib import Path

# Ensure SalesAI package root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.report_s3 import S3BenchmarkReportGenerator
from sales_engine.llm_gateway.errors import LLMNotConfiguredError


def parse_args() -> argparse.Namespace:
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
        help="Attempt live commercial execution (fails closed in S3.0).",
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
        help="Evaluate specific candidate (e.g. 't1_c1', 't1_c2') instead of sequential Tier 1 evaluation.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Maximum fixture cases to evaluate per task (useful for quick smoke tests).",
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 72)
    print("SalesAI S3.0 - External LLM Benchmark Runner")
    print("=" * 72)
    print(f"Mode: {'OFFLINE DRY-RUN (Deterministic Mock)' if args.dry_run else 'LIVE DISPATCH'}")
    print(f"Task Filter: {args.task}")
    print(f"Candidate Filter: {args.candidate or 'Sequential Tier 1 (#1..#4)'}")
    print(f"Max Cases per Task: {args.max_cases or 'All available fixtures'}")
    print(f"Output Directory: {args.output_dir}")
    print("-" * 72)

    try:
        runner = ExternalBenchmarkRunner(dry_run=args.dry_run)
    except LLMNotConfiguredError as e:
        print(f"\n[SAFETY GATE TRIPPED] Execution Refused:\n  {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[INITIALIZATION ERROR] Failed to initialize runner:\n  {e}", file=sys.stderr)
        return 2

    print("Running deterministic benchmark evaluation...")
    report = runner.run_benchmark(
        task_filter=args.task,
        candidate_filter=args.candidate,
        max_cases_per_task=args.max_cases,
    )

    print("\nBenchmark Execution Completed:")
    print(f"  Run ID: {report.run_id}")
    print(f"  Total Cases Executed: {report.total_cases_run}")
    print(f"  Total Cost: ${report.total_cost_usd:.6f}")
    print(f"  Tasks Evaluated: {len(report.tasks_evaluated)}")

    output_dir = Path(args.output_dir)
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
