# sales_engine/analytics/__init__.py
"""
SalesAI Phase 12 Analytics and Learning Loop.
Strictly decoupled from operational authority.
Projections, metrics, evaluation, and recommendations.
"""

from sales_engine.analytics.models import (
    AnalyticsEvent,
    EventType,
    RelationshipType,
    OpportunityType,
    HumanEditLevel,
    RecommendationStatus,
    ModelEvaluationRun,
    ModelEvaluationCase,
    LearningRecommendation,
    MetricRate,
    MIN_SAMPLE_FOR_COMPARISON,
    MIN_SAMPLE_FOR_RECOMMENDATION,
)
from sales_engine.analytics.store import AnalyticsStore
from sales_engine.analytics.projector import AnalyticsProjector
from sales_engine.analytics.metrics import AnalyticsMetricsCalculator
from sales_engine.analytics.recommender import RecommendationEngine
from sales_engine.analytics.evaluator import ModelEvaluator
from sales_engine.analytics.export import AnalyticsExporter

__all__ = [
    "AnalyticsEvent",
    "EventType",
    "RelationshipType",
    "OpportunityType",
    "HumanEditLevel",
    "RecommendationStatus",
    "ModelEvaluationRun",
    "ModelEvaluationCase",
    "LearningRecommendation",
    "MetricRate",
    "MIN_SAMPLE_FOR_COMPARISON",
    "MIN_SAMPLE_FOR_RECOMMENDATION",
    "AnalyticsStore",
    "AnalyticsProjector",
    "AnalyticsMetricsCalculator",
    "RecommendationEngine",
    "ModelEvaluator",
    "AnalyticsExporter",
]
