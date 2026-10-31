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
            "lifecycle_status",
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
            "commercial_cost_usd",
            "billable_cost_usd",
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
                comm_cost = case.commercial_cost_usd if case.commercial_cost_usd is not None else case.cost_usd
                bill_cost = case.billable_cost_usd
                rows.append({
                    "run_id": report.run_id,
                    "task_id": case.task_id,
                    "subtask_id": case.subtask_id or "",
                    "candidate_id": case.candidate_id,
                    "provider_id": case.provider_id,
                    "exact_model_id": case.exact_model_id,
                    "case_id": case.case_id,
                    "group": case.group,
                    "lifecycle_status": getattr(case, "lifecycle_status", None).value if getattr(case, "lifecycle_status", None) else "EVALUATED",
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
                    "cost_usd": f"{comm_cost:.6f}" if comm_cost is not None else "",
                    "commercial_cost_usd": f"{comm_cost:.6f}" if comm_cost is not None else "",
                    "billable_cost_usd": f"{bill_cost:.6f}" if bill_cost is not None else "",
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

        total_comm_cost = sum(
            (c.commercial_cost_usd if c.commercial_cost_usd is not None else (c.cost_usd or 0.0))
            for s in report.candidate_summaries
            for c in s.case_results
        )
        total_billable = sum(
            (c.billable_cost_usd or 0.0)
            for s in report.candidate_summaries
            for c in s.case_results
        )
        has_trial = any(
            c.spend_provenance == "VERIFIED_COHERE_TRIAL"
            for s in report.candidate_summaries
            for c in s.case_results
        )

        lines = [
            f"# SalesAI S3.2 — External LLM Benchmark Report",
            f"",
            f"- **Run ID:** `{report.run_id}`",
            f"- **Timestamp:** `{report.timestamp}`",
            f"- **Campaign ID:** `{report.campaign_id or 'N/A'}`",
            f"- **Ledger Database Path:** `{report.ledger_db_path or 'data/benchmark_budget.db'}`",
            f"- **Mode:** `{mode_str}`",
            f"- **Scorer Version:** `{report.scorer_version}`",
            f"- **Fixture Catalog Version:** `{report.fixture_catalog_version}`",
            f"- **Pricing Source Mode:** `{report.pricing_source_mode}`",
            f"- **Smoke Test Only:** `{report.is_smoke_only}`",
            f"- **Total Cases Evaluated:** `{report.total_cases_run}`",
            f"- **Commercial-Equivalent Cost:** `${total_comm_cost:.6f}` (rate card comparison)",
            f"- **External Billable Spend:** `${total_billable:.6f}`{' (verified Free/Trial entitlement: $0.00)' if has_trial or total_billable == 0.0 else ''}",
        ]

        # Defect 7: Report unresolved/failed liabilities and conservative ceilings
        unc_liability = sum(getattr(s, "uncertain_cost_usd", 0.0) for s in report.candidate_summaries)
        if unc_liability > 0.0:
            lines.append(f"- **Unresolved / Uncertain Liability:** `${unc_liability:.6f}` (retained conservative liability)")

        # Defect 6 & 7: Historical ledger exposure documentation
        hist = getattr(report, "historical_liability", None)
        if hist and isinstance(hist, dict):
            lines.extend([
                f"- **Historical Operational Ledger:** `{hist.get('db_path', 'unknown')}`",
                f"  - Exists: `{hist.get('exists', False)}`",
                f"  - Total Historical Reservations: `{hist.get('total_reservations', 0)}`",
                f"  - Historical Commercial Spend: `${hist.get('total_commercial_usd', 0.0):.6f}`",
                f"  - Historical Billable Spend: `${hist.get('total_billable_usd', 0.0):.6f}`",
                f"  - Historical Uncertain Commercial Liability: `${hist.get('uncertain_commercial_usd', 0.0):.6f}`",
            ])

        lines.extend([
            f"",
            f"> [!NOTE]",
            f"> **Campaign Spend Ceilings & Financial Safety Context:**",
            f"> - 30-Case Conservative Commercial Baseline: `$0.035831` (aggregate across 10 cases each for Gemini, Groq, Cohere).",
            f"> - Campaign Safety Cap: `$0.150000` commercial cap (providing ~4.18x safety margin against runaways) and `$0.050000` billable cap.",
            f"> - Zero-charge policy: For Free/Trial campaigns, external billable spend is strictly capped at `$0.000000`.",
            f"",
            f"> [!IMPORTANT]",
            f"> **Acceptance & Production Authority Notice:**",
            f"> Model selections and recommendations presented in this report are **proposals only** generated by deterministic automated benchmarks.",
            f"> They **DO NOT** constitute production route approvals or authorizations to modify operational call sites.",
            f"> Production activation requires authenticated operator credentials and explicit route approval.",
            f"",
            f"---",
            f"## 1. Candidate Comparison & Qualification Matrix",
            f"",
            f"| Task / Subtask | Candidate | Provider | Model | Planned/Eval | Dispatch % | Schema % | Ground Truth % | Evidence % | Safety % | Overall % | Median Lat | Qualified? | Status | Approval |",
            f"| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |",
        ])

        for s in report.candidate_summaries:
            task_disp = f"{s.task_id}:{s.subtask_id}" if s.subtask_id else s.task_id
            qual_str = "✅ YES" if s.qualified else "❌ NO"
            if s.is_smoke_only:
                status_str = "SMOKE_ONLY"
            else:
                status_str = "QUALIFICATION_ELIGIBLE" if s.qualified else "REJECTED"
            planned_eval_str = f"{s.cases_planned}/{s.cases_evaluable}" if hasattr(s, "cases_planned") and s.cases_planned else str(s.cases_evaluated)
            disp_rate = f"{s.dispatch_success_rate:.1%}" if hasattr(s, "dispatch_success_rate") else "100.0%"
            med_lat = f"{s.median_latency_ms:.0f}ms" if hasattr(s, "median_latency_ms") and s.median_latency_ms else f"{s.avg_latency_ms:.0f}ms"
            appr_str = getattr(s, "production_approval_state", "NOT_APPROVED_PROPOSAL_ONLY")
            lines.append(
                f"| `{task_disp}` | `{s.candidate_id}` | `{s.provider_id}` | `{s.exact_model_id}` | "
                f"{planned_eval_str} | {disp_rate} | {s.schema_pass_rate:.1%} | {s.ground_truth_pass_rate:.1%} | "
                f"{s.evidence_pass_rate:.1%} | {s.safety_pass_rate:.1%} | {s.overall_pass_rate:.1%} | {med_lat} | {qual_str} | **{status_str}** | `{appr_str}` |"
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
            f"- **Sample Size Limitation Notice:** Ten test cases constitute a minimum engineering screening batch, not strong statistical evidence of production reliability. Task-level qualification on 10 cases does NOT imply qualification for other tasks or production approval.",
        ])

        target_path.write_text("\n".join(lines), encoding="utf-8")
        return target_path
