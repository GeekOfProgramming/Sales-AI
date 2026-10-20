"""
sales_engine/benchmark
Authoritative real local-LLM benchmark module for SalesAI (Production Readiness Step 6).
Provides real Ollama inference measurement, versioned sanitized fixtures,
schema/enum/grounding scoring, and deterministic reporting.
"""

from .real_ollama import OllamaBenchmarkRunner, OllamaInferenceResult, OllamaMetrics
from .scoring import BenchmarkScorer, FixtureScoreResult
from .fixtures import BENCHMARK_FIXTURES, BENCHMARK_FIXTURE_SET_VERSION
from .report import BenchmarkReportGenerator

__all__ = [
    "OllamaBenchmarkRunner",
    "OllamaInferenceResult",
    "OllamaMetrics",
    "BenchmarkScorer",
    "FixtureScoreResult",
    "BENCHMARK_FIXTURES",
    "BENCHMARK_FIXTURE_SET_VERSION",
    "BenchmarkReportGenerator",
]
