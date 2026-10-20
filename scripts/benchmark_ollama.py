"""
scripts/benchmark_ollama.py
Command-Line Runner for Real Ollama Benchmark (Production Readiness Step 6).
Executes actual local Ollama inference against sanitized SalesAI fixtures.

Lightweight execution: uses Python standard library only (100% Python 3.10+ compatible).
Zero external dependencies required.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add repo root to sys.path so sales_engine can be imported
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sales_engine.benchmark.fixtures import (
    BENCHMARK_FIXTURES,
    BENCHMARK_FIXTURE_SET_VERSION,
    get_fixtures_for_task,
)
from sales_engine.benchmark.real_ollama import (
    OllamaBenchmarkRunner,
    OllamaInferenceResult,
    OllamaMetrics,
)
from sales_engine.benchmark.report import BenchmarkReportGenerator
from sales_engine.benchmark.scoring import BenchmarkScorer, FixtureScoreResult


def load_hardware_profile(profile_path: Path) -> Dict[str, Any]:
    """Loads hardware profile if available, otherwise returns fallback context."""
    if profile_path.exists():
        try:
            return json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "machine_label": "hamed_laptop",
        "cpu": {"caption": "AMD Ryzen 7 5800H"},
        "memory": {"total_gb": 15.4},
        "preferred_llm_gpu": {
            "name": "NVIDIA GeForce RTX 3050 Laptop GPU",
            "vram_gb": 4.0,
        },
        "os": {"caption": "Windows 11 Home 64-bit"},
    }


def run_benchmark(
    model: str = "llama3.2:3b",
    hardware_profile_path: Optional[Path] = None,
    repetitions: int = 2,
    endpoint: str = "http://127.0.0.1:11434",
    timeout: int = 60,
    task_filter: Optional[str] = None,
    max_fixtures: Optional[int] = None,
    dry_run: bool = False,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Executes the complete benchmark flow and outputs reports."""
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    out_dir = output_dir or (REPO_ROOT / "runtime" / "benchmarks")
    hw_path = hardware_profile_path or (REPO_ROOT / "runtime" / "hardware" / "hardware_profile.json")
    hw_profile = load_hardware_profile(hw_path)

    runner = OllamaBenchmarkRunner(base_url=endpoint, timeout=timeout)

    # 1. Select fixtures
    fixtures = get_fixtures_for_task(task_filter)
    if max_fixtures is not None and max_fixtures > 0:
        fixtures = fixtures[:max_fixtures]

    inference_results: List[OllamaInferenceResult] = []
    score_results: List[FixtureScoreResult] = []

    if dry_run:
        print("[DRY-RUN] Running benchmark validation in mock/dry mode (no network requests).")
        for fix in fixtures:
            # Create synthetic mock response matching expected schema
            mock_data = fix.get("expected_fields", {})
            if not mock_data and "expected_schema" in fix:
                mock_data = {k: "test" for k in fix["expected_schema"]}
            raw_mock = json.dumps(mock_data)

            # Record cold run
            cold_res = OllamaInferenceResult(
                fixture_id=fix["fixture_id"],
                task=fix["task"],
                model=model,
                repetition_index=0,
                is_cold_run=True,
                status="success",
                raw_response_text=raw_mock,
                metrics=OllamaMetrics(
                    total_duration_s=None,
                    measurement_status="not_measured",  # Dry run has NO measured duration
                ),
            )
            inference_results.append(cold_res)

            # Record warm runs
            for rep in range(1, repetitions + 1):
                warm_res = OllamaInferenceResult(
                    fixture_id=fix["fixture_id"],
                    task=fix["task"],
                    model=model,
                    repetition_index=rep,
                    is_cold_run=False,
                    status="success",
                    raw_response_text=raw_mock,
                    metrics=OllamaMetrics(
                        total_duration_s=None,
                        measurement_status="not_measured",
                    ),
                )
                inference_results.append(warm_res)

            # Score
            score = BenchmarkScorer.evaluate(fix, raw_mock)
            score_results.append(score)

    else:
        # Check Ollama connectivity
        reachable, ver = runner.check_reachability()
        if not reachable:
            print(f"[ERROR] Local Ollama service is not reachable at {endpoint}.")
            print("Please ensure the Ollama service is running ('ollama serve').")
            # Generate uninstalled/unreachable report
            for fix in fixtures:
                inference_results.append(
                    OllamaInferenceResult(
                        fixture_id=fix["fixture_id"],
                        task=fix["task"],
                        model=model,
                        repetition_index=0,
                        is_cold_run=False,
                        status="network_error",
                        error_message="Ollama daemon unreachable",
                        metrics=OllamaMetrics(measurement_status="not_measured"),
                    )
                )
                score_results.append(BenchmarkScorer.evaluate(fix, ""))
        else:
            print(f"[OLLAMA] Connected to Ollama daemon (version: {ver}) at {endpoint}")

            # Check if requested model is installed locally
            installed = runner.is_model_installed(model)
            if not installed:
                print(f"[WARNING] Model '{model}' is NOT_INSTALLED locally on Ollama.")
                print("Per safety policy, models are NEVER downloaded automatically.")
                for fix in fixtures:
                    inference_results.append(
                        OllamaInferenceResult(
                            fixture_id=fix["fixture_id"],
                            task=fix["task"],
                            model=model,
                            repetition_index=0,
                            is_cold_run=False,
                            status="not_installed",
                            error_message=f"Model '{model}' not installed in local Ollama",
                            metrics=OllamaMetrics(measurement_status="not_measured"),
                        )
                    )
                    score_results.append(BenchmarkScorer.evaluate(fix, ""))
            else:
                print(f"[BENCHMARK] Model '{model}' verified locally installed. Starting benchmark runs...")
                print(f"Total fixtures: {len(fixtures)} | Warm repetitions: {repetitions}")

                for idx, fix in enumerate(fixtures, 1):
                    fid = fix["fixture_id"]
                    task = fix["task"]
                    print(f"[{idx}/{len(fixtures)}] Benchmarking {fid} ({task})...", end="", flush=True)

                    # 1. Cold run
                    cold_res = runner.execute_inference(
                        fix,
                        model=model,
                        repetition_index=0,
                        is_cold_run=True,
                    )
                    inference_results.append(cold_res)

                    # 2. Warm repetitions
                    last_raw = cold_res.raw_response_text
                    for rep in range(1, repetitions + 1):
                        warm_res = runner.execute_inference(
                            fix,
                            model=model,
                            repetition_index=rep,
                            is_cold_run=False,
                        )
                        inference_results.append(warm_res)
                        if warm_res.status == "success":
                            last_raw = warm_res.raw_response_text

                    # 3. Score the latest warm output
                    score = BenchmarkScorer.evaluate(fix, last_raw)
                    score_results.append(score)

                    status_str = "PASS" if score.overall_pass else "REVIEW"
                    print(f" -> {status_str}")

    # Generate Report
    options = {
        "temperature": 0.0,
        "seed": 42,
        "num_ctx": 4096,
        "num_predict": 512,
        "repetitions": repetitions,
        "timeout": timeout,
    }
    generator = BenchmarkReportGenerator(
        run_id=run_id,
        model_name=model,
        machine_label=hw_profile.get("machine_label", "hamed_laptop"),
        hardware_profile=hw_profile,
        fixture_version=BENCHMARK_FIXTURE_SET_VERSION,
        options=options,
        output_dir=out_dir,
    )

    report_data = generator.compile_report(inference_results, score_results)
    json_path, md_path = generator.write_reports(report_data)

    print("\n" + "=" * 60)
    print("SALES-AI REAL OLLAMA BENCHMARK COMPLETED")
    print("=" * 60)
    print(f"Run ID:                 {run_id}")
    print(f"Model Evaluated:        {model}")
    print(f"Measurement Status:     {report_data['execution_summary']['measurement_status']}")
    print(f"Warm Cases Attempted:   {report_data['execution_summary']['warm_cases_attempted']}")
    print(f"Successful Inferences:  {report_data['execution_summary']['successful_inferences']}")
    print(f"Warm Avg Latency:       {report_data['performance_metrics']['warm_latency_avg_s']} s")
    print(f"Warm Avg Throughput:    {report_data['performance_metrics']['warm_throughput_avg_tokens_per_s']} tok/s")
    print(f"Strict JSON Pass %:     {report_data['quality_metrics']['strict_json_pct']}%")
    print(f"Lenient JSON Pass %:    {report_data['quality_metrics']['lenient_json_pct']}%")
    print(f"Schema Pass %:          {report_data['quality_metrics']['schema_pct']}%")
    print(f"Enum Pass %:            {report_data['quality_metrics']['enum_pct']}%")
    print(f"Grounding Pass %:       {report_data['quality_metrics']['grounding_pct']}%")
    print(f"Human Review Flags:     {report_data['quality_metrics']['needs_human_review_count']}")
    print(f"\nJSON Report:            {json_path}")
    print(f"Markdown Report:        {md_path}")
    print("=" * 60)
    print("IMPORTANT: Model selection is manual. Final model is NOT automatically selected.")
    print("=" * 60 + "\n")

    return report_data


def main():
    parser = argparse.ArgumentParser(
        description="Authoritative Real Ollama Benchmark Runner for SalesAI (Production Step 6)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="llama3.2:3b",
        help="Target Ollama model name (default: llama3.2:3b)",
    )
    parser.add_argument(
        "--hardware-profile",
        type=str,
        default=None,
        help="Path to hardware_profile.json (default: runtime/hardware/hardware_profile.json)",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=2,
        help="Number of warm repetitions per fixture (default: 2, recommended: 1-3)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://127.0.0.1:11434",
        help="Local Ollama base URL (default: http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Inference request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Optional filter by task name (e.g. website_extraction, reply_classification)",
    )
    parser.add_argument(
        "--max-fixtures",
        type=int,
        default=None,
        help="Limit number of fixtures to evaluate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute in mock/dry mode without sending network requests to Ollama",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List locally installed Ollama models and exit",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory for reports (default: runtime/benchmarks)",
    )

    args = parser.parse_args()

    if args.list_models:
        runner = OllamaBenchmarkRunner(base_url=args.endpoint)
        reachable, ver = runner.check_reachability()
        if not reachable:
            print(f"Ollama daemon not reachable at {args.endpoint}.")
            sys.exit(1)
        models = runner.list_installed_models()
        print(f"Connected to Ollama {ver} at {args.endpoint}")
        print(f"Installed models ({len(models)}):")
        for m in models:
            size_gb = round(m.get("size", 0) / (1024**3), 2)
            print(f"  - {m.get('name')} ({size_gb} GB)")
        sys.exit(0)

    hw_profile_path = Path(args.hardware_profile) if args.hardware_profile else None
    out_dir = Path(args.output_dir) if args.output_dir else None

    run_benchmark(
        model=args.model,
        hardware_profile_path=hw_profile_path,
        repetitions=args.repetitions,
        endpoint=args.endpoint,
        timeout=args.timeout,
        task_filter=args.task,
        max_fixtures=args.max_fixtures,
        dry_run=args.dry_run,
        output_dir=out_dir,
    )


if __name__ == "__main__":
    main()
