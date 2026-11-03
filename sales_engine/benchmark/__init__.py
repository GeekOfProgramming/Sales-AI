"""
sales_engine/benchmark
Benchmark module for SalesAI:
1. Real local-LLM benchmark runner (Ollama, Step 6).
2. S3.0 External LLM benchmark runner and paid-dispatch safety gates.
"""

from .real_ollama import OllamaBenchmarkRunner, OllamaInferenceResult, OllamaMetrics
from .scoring import BenchmarkScorer, FixtureScoreResult
from .fixtures import BENCHMARK_FIXTURES, BENCHMARK_FIXTURE_SET_VERSION
from .report import BenchmarkReportGenerator

# S3.0 External Benchmark components
from .fixtures_s3 import (
    BenchmarkFixtureCase,
    ALL_S3_BENCHMARK_FIXTURES,
    get_fixtures_for_task,
    get_all_s3_fixtures,
)
from .golden_mapper import GoldenDatasetMapper, MappedGoldenCase
from .external_runner import (
    ExternalBenchmarkRunner,
    BenchmarkCaseExecutionResult,
    CandidateTaskSummary,
    BenchmarkRunReport,
    MockBenchmarkProvider,
    CampaignBudgetGuard,
    CampaignLifecycleStatus,
    read_only_historical_liability_preflight,
)
from .report_s3 import S3BenchmarkReportGenerator

__all__ = [
    # Step 6
    "OllamaBenchmarkRunner",
    "OllamaInferenceResult",
    "OllamaMetrics",
    "BenchmarkScorer",
    "FixtureScoreResult",
    "BENCHMARK_FIXTURES",
    "BENCHMARK_FIXTURE_SET_VERSION",
    "BenchmarkReportGenerator",
    # S3.0
    "BenchmarkFixtureCase",
    "ALL_S3_BENCHMARK_FIXTURES",
    "get_fixtures_for_task",
    "get_all_s3_fixtures",
    "GoldenDatasetMapper",
    "MappedGoldenCase",
    "ExternalBenchmarkRunner",
    "BenchmarkCaseExecutionResult",
    "CandidateTaskSummary",
    "BenchmarkRunReport",
    "MockBenchmarkProvider",
    "CampaignBudgetGuard",
    "CampaignLifecycleStatus",
    "read_only_historical_liability_preflight",
    "S3BenchmarkReportGenerator",
]
