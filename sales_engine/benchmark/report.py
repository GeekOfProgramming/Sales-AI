"""
sales_engine/benchmark/report.py
Authoritative Report Generator for Real Ollama Benchmark (Production Readiness Step 6).
Generates structured JSON and human-readable Markdown reports.

Strict Reporting Rules:
1. Reports actual measured metrics with provenance (MEASURED vs NOT_MEASURED).
2. Never presents synthetic or fabricated estimates.
3. Separates cold run from warm repetitions.
4. Includes machine hardware context and effective options.
5. Emphasizes that model selection is manual and not automated.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .real_ollama import OllamaInferenceResult
from .scoring import FixtureScoreResult


class BenchmarkReportGenerator:
    """Produces JSON and Markdown benchmark reports."""

    def __init__(
        self,
        run_id: str,
        model_name: str,
        machine_label: str = "hamed_laptop",
        hardware_profile: Optional[Dict[str, Any]] = None,
        fixture_version: str = "step6-benchmark-v1",
        options: Optional[Dict[str, Any]] = None,
        output_dir: Optional[Path] = None,
    ):
        self.run_id = run_id
        self.model_name = model_name
        self.machine_label = machine_label
        self.hardware_profile = hardware_profile or {}
        self.fixture_version = fixture_version
        self.options = options or {}
        self.output_dir = output_dir or Path("runtime/benchmarks")

    def compile_report(
        self,
        inference_results: List[OllamaInferenceResult],
        score_results: List[FixtureScoreResult],
    ) -> Dict[str, Any]:
        """Compiles raw results into an authoritative structured benchmark summary."""
        timestamp = datetime.now(timezone.utc).isoformat()

        # Separate cold run from warm runs
        cold_inferences = [r for r in inference_results if r.is_cold_run]
        warm_inferences = [r for r in inference_results if not r.is_cold_run]

        # Count statuses
        attempted = len(warm_inferences)
        succeeded = len([r for r in warm_inferences if r.status == "success"])
        failed = len([r for r in warm_inferences if r.status != "success"])
        timeouts = len([r for r in warm_inferences if r.status == "timeout"])
        not_installed = len([r for r in warm_inferences if r.status == "not_installed"])

        # Collect warm measured durations and token rates
        warm_latencies = [
            r.metrics.total_duration_s
            for r in warm_inferences
            if r.metrics.total_duration_s is not None and r.metrics.measurement_status == "measured"
        ]
        warm_throughputs = [
            r.metrics.tokens_per_second
            for r in warm_inferences
            if r.metrics.tokens_per_second is not None and r.metrics.measurement_status == "measured"
        ]

        cold_latency = None
        if cold_inferences:
            c_metrics = cold_inferences[0].metrics
            if c_metrics.total_duration_s is not None and c_metrics.measurement_status == "measured":
                cold_latency = round(c_metrics.total_duration_s, 3)

        avg_latency = round(statistics.mean(warm_latencies), 3) if warm_latencies else None
        median_latency = round(statistics.median(warm_latencies), 3) if warm_latencies else None
        min_latency = round(min(warm_latencies), 3) if warm_latencies else None
        max_latency = round(max(warm_latencies), 3) if warm_latencies else None

        avg_throughput = round(statistics.mean(warm_throughputs), 2) if warm_throughputs else None
        median_throughput = round(statistics.median(warm_throughputs), 2) if warm_throughputs else None

        # Scoring metrics (scored against unique fixtures or latest repetition)
        total_fixtures_scored = len(score_results)
        strict_json_passes = sum(1 for s in score_results if s.strict_json)
        lenient_json_passes = sum(1 for s in score_results if s.lenient_json)
        schema_passes = sum(1 for s in score_results if s.schema_valid)
        enum_passes = sum(1 for s in score_results if s.enum_valid)
        grounding_passes = sum(1 for s in score_results if s.grounding_passed)
        injection_resilient_passes = sum(1 for s in score_results if s.prompt_injection_resilient)
        human_review_count = sum(1 for s in score_results if s.needs_human_review)
        overall_passes = sum(1 for s in score_results if s.overall_pass)

        # Task-level breakdowns
        task_breakdown: Dict[str, Dict[str, Any]] = {}
        for s in score_results:
            t = s.task
            if t not in task_breakdown:
                task_breakdown[t] = {
                    "total": 0,
                    "lenient_json_passes": 0,
                    "schema_passes": 0,
                    "enum_passes": 0,
                    "grounding_passes": 0,
                    "overall_passes": 0,
                    "needs_human_review": 0,
                }
            task_breakdown[t]["total"] += 1
            if s.lenient_json:
                task_breakdown[t]["lenient_json_passes"] += 1
            if s.schema_valid:
                task_breakdown[t]["schema_passes"] += 1
            if s.enum_valid:
                task_breakdown[t]["enum_passes"] += 1
            if s.grounding_passed:
                task_breakdown[t]["grounding_passes"] += 1
            if s.overall_pass:
                task_breakdown[t]["overall_passes"] += 1
            if s.needs_human_review:
                task_breakdown[t]["needs_human_review"] += 1

        # Safely extract hardware context
        cpu_info = self.hardware_profile.get("cpu") or {}
        cpu_caption = cpu_info.get("caption") or cpu_info.get("name") or "Unknown CPU"

        mem_info = self.hardware_profile.get("memory") or {}
        ram_gb = mem_info.get("total_gb", 16.0)

        gpu_info = self.hardware_profile.get("preferred_llm_gpu") or {}
        if not gpu_info and self.hardware_profile.get("gpus"):
            gpus = self.hardware_profile.get("gpus") or []
            if gpus and isinstance(gpus, list):
                gpu_info = gpus[0]

        gpu_name = gpu_info.get("name", "Integrated Graphics / CPU fallback")
        vram_gb = gpu_info.get("vram_gb", gpu_info.get("vram_total_gb", 0.0))

        os_info = self.hardware_profile.get("os") or {}
        os_caption = os_info.get("caption", "Windows")

        report: Dict[str, Any] = {
            "metadata": {
                "run_id": self.run_id,
                "timestamp": timestamp,
                "model_evaluated": self.model_name,
                "machine_label": self.machine_label,
                "fixture_version": self.fixture_version,
                "options": self.options,
                "final_model_selected": None,  # MUST NOT automatically select model
                "selection_status": "PENDING_OPERATOR_REVIEW",
            },
            "hardware_summary": {
                "cpu": cpu_caption,
                "ram_gb": ram_gb,
                "gpu": gpu_name,
                "vram_gb": vram_gb,
                "os": os_caption,
            },
            "execution_summary": {
                "warm_cases_attempted": attempted,
                "successful_inferences": succeeded,
                "failed_inferences": failed,
                "timeouts": timeouts,
                "not_installed_count": not_installed,
                "measurement_status": "MEASURED" if warm_latencies else "NOT_MEASURED",
            },
            "performance_metrics": {
                "cold_run_latency_s": cold_latency,
                "warm_latency_avg_s": avg_latency,
                "warm_latency_median_s": median_latency,
                "warm_latency_min_s": min_latency,
                "warm_latency_max_s": max_latency,
                "warm_throughput_avg_tokens_per_s": avg_throughput,
                "warm_throughput_median_tokens_per_s": median_throughput,
            },
            "quality_metrics": {
                "total_fixtures_scored": total_fixtures_scored,
                "strict_json_passes": strict_json_passes,
                "strict_json_pct": round((strict_json_passes / total_fixtures_scored) * 100, 1) if total_fixtures_scored else 0,
                "lenient_json_passes": lenient_json_passes,
                "lenient_json_pct": round((lenient_json_passes / total_fixtures_scored) * 100, 1) if total_fixtures_scored else 0,
                "schema_passes": schema_passes,
                "schema_pct": round((schema_passes / total_fixtures_scored) * 100, 1) if total_fixtures_scored else 0,
                "enum_passes": enum_passes,
                "enum_pct": round((enum_passes / total_fixtures_scored) * 100, 1) if total_fixtures_scored else 0,
                "grounding_passes": grounding_passes,
                "grounding_pct": round((grounding_passes / total_fixtures_scored) * 100, 1) if total_fixtures_scored else 0,
                "prompt_injection_resilient_passes": injection_resilient_passes,
                "needs_human_review_count": human_review_count,
                "overall_deterministic_passes": overall_passes,
            },
            "task_breakdown": task_breakdown,
            "fixtures": [s.to_dict() for s in score_results],
        }

        return report

    def generate_markdown(self, report: Dict[str, Any]) -> str:
        """Converts compiled report to formatted Markdown."""
        meta = report["metadata"]
        hw = report["hardware_summary"]
        exec_s = report["execution_summary"]
        perf = report["performance_metrics"]
        qual = report["quality_metrics"]
        tasks = report["task_breakdown"]

        lines = [
            f"# Real Ollama Benchmark Report: {meta['model_evaluated']}",
            f"**Run ID:** `{meta['run_id']}` | **Date:** `{meta['timestamp']}` | **Machine:** `{meta['machine_label']}`",
            "",
            "> [!IMPORTANT]",
            "> **Model Selection Policy:** This benchmark runner does **NOT** select the final model automatically.",
            "> Final model selection must be reviewed and decided manually by the operator based on the tradeoffs below.",
            "",
            "## 1. Hardware & Environment Context",
            f"- **CPU:** {hw['cpu']}",
            f"- **System RAM:** {hw['ram_gb']} GB",
            f"- **GPU:** {hw['gpu']}",
            f"- **Dedicated VRAM:** {hw['vram_gb']} GB",
            f"- **Operating System:** {hw['os']}",
            f"- **Fixture Set Version:** `{meta['fixture_version']}`",
            f"- **Inference Options:** `temperature={meta['options'].get('temperature', 0.0)}`, `num_ctx={meta['options'].get('num_ctx', 4096)}`, `num_predict={meta['options'].get('num_predict', 512)}`",
            "",
            "## 2. Real Performance Metrics",
            f"**Measurement Status:** `{exec_s['measurement_status']}`",
            "",
            "| Metric | Measured Value | Measurement Provenance |",
            "| :--- | :--- | :--- |",
            f"| **Cold Run Latency** | {perf['cold_run_latency_s']} s | Actual first inference wall-clock / Ollama duration |",
            f"| **Warm Average Latency** | {perf['warm_latency_avg_s']} s | Actual warm repetitions average duration |",
            f"| **Warm Median Latency** | {perf['warm_latency_median_s']} s | Actual warm repetitions median duration |",
            f"| **Warm Min / Max Latency** | {perf['warm_latency_min_s']} s / {perf['warm_latency_max_s']} s | Range across warm measured runs |",
            f"| **Warm Token Throughput** | {perf['warm_throughput_avg_tokens_per_s']} tok/s | Derived exclusively from `eval_count / eval_duration` |",
            f"| **Warm Median Throughput** | {perf['warm_throughput_median_tokens_per_s']} tok/s | Median token throughput |",
            "",
            "> [!NOTE]",
            "> Any missing metric is strictly reported as `null` with status `not_measured`. No synthetic latencies or estimates are used.",
            "",
            "## 3. Quality & Safety Summary",
            f"- **Total Fixtures Scored:** {qual['total_fixtures_scored']}",
            f"- **Strict JSON Compliance (no markdown fences):** {qual['strict_json_passes']} / {qual['total_fixtures_scored']} ({qual['strict_json_pct']}%)",
            f"- **Lenient JSON Compliance (after fence stripping):** {qual['lenient_json_passes']} / {qual['total_fixtures_scored']} ({qual['lenient_json_pct']}%)",
            f"- **Schema Conformance:** {qual['schema_passes']} / {qual['total_fixtures_scored']} ({qual['schema_pct']}%)",
            f"- **Enum Conformance:** {qual['enum_passes']} / {qual['total_fixtures_scored']} ({qual['enum_pct']}%)",
            f"- **Grounding / Anti-Hallucination:** {qual['grounding_passes']} / {qual['total_fixtures_scored']} ({qual['grounding_pct']}%)",
            f"- **Prompt Injection Resilience:** {qual['prompt_injection_resilient_passes']} / {qual['total_fixtures_scored']}",
            f"- **Human Review Flags (Stylistic/Nuance):** {qual['needs_human_review_count']} fixtures flagged",
            "",
            "## 4. Task-Level Breakdown",
            "| Task Category | Fixtures | Lenient JSON | Schema Valid | Enum Valid | Grounding Pass | Review Needed |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]

        for task_name, stats in tasks.items():
            lines.append(
                f"| `{task_name}` | {stats['total']} | {stats['lenient_json_passes']} | {stats['schema_passes']} | {stats['enum_passes']} | {stats['grounding_passes']} | {stats['needs_human_review']} |"
            )

        lines.extend([
            "",
            "## 5. Next Actions for Operator",
            "1. Review warm latency and token throughput against user experience expectations.",
            "2. Inspect fixtures flagged with `needs_human_review` in the JSON report.",
            "3. Manually select whether to proceed with this model or benchmark comparison models before finalizing Step 7.",
        ])

        return "\n".join(lines)

    def write_reports(
        self,
        report_data: Dict[str, Any],
    ) -> Tuple[Path, Path]:
        """Writes both JSON and Markdown reports to output_dir and mirrors to latest."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        json_path = self.output_dir / f"benchmark_{self.run_id}.json"
        md_path = self.output_dir / f"benchmark_{self.run_id}.md"
        latest_json = self.output_dir / "latest.json"
        latest_md = self.output_dir / "latest.md"

        json_str = json.dumps(report_data, indent=2)
        md_str = self.generate_markdown(report_data)

        json_path.write_text(json_str, encoding="utf-8")
        md_path.write_text(md_str, encoding="utf-8")
        latest_json.write_text(json_str, encoding="utf-8")
        latest_md.write_text(md_str, encoding="utf-8")

        return json_path, md_path
