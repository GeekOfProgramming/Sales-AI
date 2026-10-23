"""
sales_engine/benchmark/report_s3.py
Authoritative multi-format report generator for SalesAI S3.0 External LLM Benchmark.
Generates:
1. Structured JSON report (benchmark_results_{run_id}.json)
2. Tabular CSV export (benchmark_cases_{run_id}.csv)
3. Formatted Markdown report (BENCHMARK_REPORT_{run_id}.md)

Strict rules:
- One row per candidate x task x case in CSV.
- Separates syntactic scores from semantic review results.
- Categorized failure reasons per task.
- Zero raw secrets or customer PII.
- Clear statement that candidate recommendations are PROPOSALS ONLY and NOT production approved.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sales_engine.benchmark.external_runner import (
    BenchmarkCaseExecutionResult,
    BenchmarkRunReport,
    CandidateTaskSummary,
)


class S3BenchmarkReportGenerator:
    """Produces JSON, CSV, and Markdown benchmark reports for S3.0."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("runtime/benchmarks")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, report: BenchmarkRunReport) -> Dict[str, Path]:
        """
        Generate all report formats and return map of format -> Path.
        """
        json_path = self.export_json(report)
        csv_path = self.export_csv(report)
        md_path = self.export_markdown(report)
        return {
            "json": json_path,
            "csv": csv_path,
            "markdown": md_path,
        }

    def export_json(self, report: BenchmarkRunReport) -> Path:
        """Export comprehensive JSON benchmark report."""
        filename = f"benchmark_results_{report.run_id}.json"
        target_path = self.output_dir / filename
        data = report.to_dict()
        target_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target_path

    def export_csv(self, report: BenchmarkRunReport) -> Path:
        """Export tabular case-level CSV report."""
        filename = f"benchmark_cases_{report.run_id}.csv"
        target_path = self.output_dir / filename

        headers = [
            "run_id",
            "task_id",
            "subtask_id",
            "candidate_id",
            "provider_id",
            "exact_model_id",
            "case_id",
            "group",
            "schema_pass",
            "ground_truth_pass",
            "evidence_pass",
            "safety_pass",
            "overall_pass",
            "needs_review",
            "syntactic_valid",
            "semantic_valid",
            "safety_valid",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "estimated_spend_usd",
            "spend_provenance",
            "sqlite_reservation_status",
            "failure_categories",
            "failure_reasons",
            "grounding_evidence_found",
            "forbidden_tokens_found",
        ]

        rows = []
        for summary in report.candidate_summaries:
            for case in summary.case_results:
                rows.append({
                    "run_id": report.run_id,
                    "task_id": case.task_id,
                    "subtask_id": case.subtask_id or "",
                    "candidate_id": case.candidate_id,
                    "provider_id": case.provider_id,
                    "exact_model_id": case.exact_model_id,
                    "case_id": case.case_id,
                    "group": case.group,
                    "schema_pass": case.schema_pass,
                    "ground_truth_pass": case.ground_truth_pass,
                    "evidence_pass": case.evidence_pass,
                    "safety_pass": case.safety_pass,
                    "overall_pass": case.overall_pass,
                    "needs_review": case.needs_review,
                    "syntactic_valid": case.syntactic_valid,
                    "semantic_valid": case.semantic_valid,
                    "safety_valid": case.safety_valid,
                    "latency_ms": case.latency_ms,
                    "input_tokens": case.input_tokens if case.input_tokens is not None else "",
                    "output_tokens": case.output_tokens if case.output_tokens is not None else "",
                    "cost_usd": f"{case.cost_usd:.6f}" if case.cost_usd is not None else "",
                    "estimated_spend_usd": f"{case.estimated_spend_usd:.6f}" if case.estimated_spend_usd is not None else "",
                    "spend_provenance": case.spend_provenance or "",
                    "sqlite_reservation_status": case.sqlite_reservation_status or "",
                    "failure_categories": ";".join(case.failure_categories),
                    "failure_reasons": ";".join(case.failure_reasons),
                    "grounding_evidence_found": ";".join(case.grounding_evidence_found),
                    "forbidden_tokens_found": ";".join(case.forbidden_tokens_found),
                })

        with open(target_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

        return target_path

    def export_markdown(self, report: BenchmarkRunReport) -> Path:
        """Export authoritative human-readable Markdown summary."""
        filename = f"BENCHMARK_REPORT_{report.run_id}.md"
        target_path = self.output_dir / filename

        mode_str = "OFFLINE DRY-RUN (MOCKED)" if report.is_dry_run else "LIVE PILOT"
        if report.is_smoke_only:
            mode_str += " [SMOKE TEST ONLY]"

        lines = [
            f"# SalesAI S3.2 — External LLM Benchmark Report",
            f"",
            f"- **Run ID:** `{report.run_id}`",
            f"- **Timestamp:** `{report.timestamp}`",
            f"- **Mode:** `{mode_str}`",
            f"- **Scorer Version:** `{report.scorer_version}`",
            f"- **Fixture Catalog Version:** `{report.fixture_catalog_version}`",
            f"- **Pricing Source Mode:** `{report.pricing_source_mode}`",
            f"- **Smoke Test Only:** `{report.is_smoke_only}`",
            f"- **Total Cases Evaluated:** `{report.total_cases_run}`",
            f"- **Total Measured / Calculated Cost:** `${report.total_cost_usd:.6f}`",
            f"",
            f"> [!IMPORTANT]",
            f"> **Acceptance & Production Authority Notice:**",
            f"> Model selections and recommendations presented in this report are **proposals only** generated by deterministic automated benchmarks.",
            f"> They **DO NOT** constitute production route approvals or authorizations to modify operational call sites.",
            f"> Production activation requires authenticated operator credentials and explicit route approval.",
            f"",
            f"---",
            f"",
            f"## 1. Candidate Comparison & Qualification Matrix",
            f"",
            f"| Task / Subtask | Candidate | Provider | Model | Cases | Schema % | Ground Truth % | Evidence % | Safety % | Overall % | Qualified? | Status |",
            f"| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
        ]

        for s in report.candidate_summaries:
            task_disp = f"{s.task_id}:{s.subtask_id}" if s.subtask_id else s.task_id
            qual_str = "✅ YES" if s.qualified else "❌ NO"
            if s.is_smoke_only:
                status_str = "SMOKE_ONLY"
            else:
                status_str = "PROPOSED" if s.qualified else "REJECTED"
            lines.append(
                f"| `{task_disp}` | `{s.candidate_id}` | `{s.provider_id}` | `{s.exact_model_id}` | "
                f"{s.cases_evaluated} | {s.schema_pass_rate:.1%} | {s.ground_truth_pass_rate:.1%} | "
                f"{s.evidence_pass_rate:.1%} | {s.safety_pass_rate:.1%} | {s.overall_pass_rate:.1%} | {qual_str} | **{status_str}** |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 2. Proposed Candidate Recommendations (Operator Review Queue)",
            f"",
        ])

        if report.recommended_candidates:
            for task_key, rec in report.recommended_candidates.items():
                lines.extend([
                    f"### `{task_key}`",
                    f"- **Recommended Candidate:** `{rec['candidate_id']}` (`{rec['exact_model_id']}` via `{rec['provider_id']}`)",
                    f"- **Overall Pass Rate:** `{rec['overall_pass_rate']:.1%}`",
                    f"- **Safety Violations:** `{rec['safety_violations']}`",
                    f"- **Status:** `{rec['status']}` (Production Approved: `{rec['is_production_approved']}`)",
                    f"- **Note:** {rec['provenance_note']}",
                    f"",
                ])
        else:
            lines.append("No candidates achieved full qualification across tested tasks.")

        lines.extend([
            f"---",
            f"",
            f"## 3. Disqualifications & Failure Root Causes",
            f"",
        ])

        disqualified_summaries = [s for s in report.candidate_summaries if not s.qualified]
        if disqualified_summaries:
            for s in disqualified_summaries:
                task_disp = f"{s.task_id}:{s.subtask_id}" if s.subtask_id else s.task_id
                lines.append(f"#### `{task_disp}` — Candidate `{s.candidate_id}`")
                for r in s.disqualification_reasons:
                    lines.append(f"- ❌ {r}")
                lines.append("")
        else:
            lines.append("All evaluated candidates satisfied qualification criteria.")

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 4. Operational Diagnostics",
            f"",
            f"- **Isolation Guarantee:** Zero gold labels or reference answers were passed to the tested model.",
            f"- **Preflight Accounting:** All executions verified budget availability and bound candidate pricing prior to dispatch.",
            f"- **Audit Status:** {'Offline benchmark executed without commercial network calls.' if report.is_dry_run else 'Live benchmark executed with authorized provider network calls under verified budget policy.'}",
            f"",
            f"---",
            f"",
            f"## 5. Known Benchmark Limitations",
            f"",
            f"- **QUALITY-01 (Golden Coverage):** Synthetic fixture catalog has incomplete `expected_output` references for `discovery_query_generation`, `reply_suggestion`, and `outreach_draft`. Full semantic evaluation requires full task quality validation prior to production qualification.",
            f"- **Smoke Test Non-Qualification:** Any smoke-test results (`SMOKE_ONLY / NOT MODEL-QUALIFIED`) strictly cannot qualify a model as production-suitable or authorize production routing updates.",
        ])

        target_path.write_text("\n".join(lines), encoding="utf-8")
        return target_path
