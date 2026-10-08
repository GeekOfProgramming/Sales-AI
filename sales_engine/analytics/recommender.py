# sales_engine/analytics/recommender.py
"""
RecommendationEngine: Generates deterministic LearningRecommendations for operator review.
Strict Invariants:
1. Analytics is NOT operational authority.
2. Accepting a recommendation ONLY records the human decision.
3. NEVER automatically mutates Phase 5 scores, Phase 9 suppression, source limits, or send rates.
4. Samples below MIN_SAMPLE_FOR_RECOMMENDATION (20) yield cautious 'gather more data' recommendations.
"""

from datetime import datetime, timezone
import uuid
from typing import Dict, Any, List, Optional

from sales_engine.analytics.models import (
    LearningRecommendation,
    RecommendationStatus,
    ConfidenceLevel,
    RiskLevel,
    MIN_SAMPLE_FOR_RECOMMENDATION,
)
from sales_engine.analytics.store import AnalyticsStore
from sales_engine.analytics.metrics import AnalyticsMetricsCalculator


class RecommendationEngine:
    def __init__(
        self,
        analytics_store: AnalyticsStore,
        metrics_calculator: Optional[AnalyticsMetricsCalculator] = None,
    ):
        self.analytics_store = analytics_store
        self.metrics_calculator = metrics_calculator or AnalyticsMetricsCalculator(analytics_store)

    def generate_recommendations(self) -> List[LearningRecommendation]:
        """
        Evaluates current funnel and dimensional metrics using deterministic heuristic rules
        and creates proposed recommendations in the store.
        """
        recommendations: List[LearningRecommendation] = []

        sources_data = self.metrics_calculator.get_source_metrics(window="all")
        markets_data = self.metrics_calculator.get_market_metrics(window="all")
        funnel_data = self.metrics_calculator.get_funnel_summary(window="all")
        messages_data = self.metrics_calculator.get_message_metrics(window="all")

        # 1. Evaluate Sources
        for s in sources_data:
            src_id = s["source_id"]
            if src_id in ("unknown", "all"):
                continue

            n = s["sample_size"]
            if n < MIN_SAMPLE_FOR_RECOMMENDATION:
                # Small sample safeguard recommendation
                rec = LearningRecommendation(
                    recommendation_id=f"rec_src_sample_{src_id}_{uuid.uuid4().hex[:6]}",
                    category="source_review",
                    scope=f"source:{src_id}",
                    summary=f"Gather more discovery observations for source '{src_id}' (n={n} < {MIN_SAMPLE_FOR_RECOMMENDATION})",
                    evidence={
                        "source_id": src_id,
                        "sample_size": n,
                        "threshold": MIN_SAMPLE_FOR_RECOMMENDATION,
                        "status": "insufficient_sample",
                    },
                    sample_size=n,
                    confidence_level=ConfidenceLevel.LOW.value,
                    proposed_change=f"Maintain source '{src_id}' in observation mode until sample reaches {MIN_SAMPLE_FOR_RECOMMENDATION}.",
                    risk=RiskLevel.LOW.value,
                    status=RecommendationStatus.PROPOSED.value,
                )
                recommendations.append(rec)
            else:
                # Adequate sample: evaluate performance
                qual_rate = s["qualification_rate"]["rate"]
                if qual_rate < 0.20:
                    rec = LearningRecommendation(
                        recommendation_id=f"rec_src_lowqual_{src_id}_{uuid.uuid4().hex[:6]}",
                        category="source_review",
                        scope=f"source:{src_id}",
                        summary=f"Review low qualification rate ({round(qual_rate*100, 1)}%) on source '{src_id}'",
                        evidence={
                            "source_id": src_id,
                            "qualification_rate": qual_rate,
                            "sample_size": n,
                        },
                        sample_size=n,
                        confidence_level=ConfidenceLevel.MEDIUM.value,
                        proposed_change=f"Human operator should inspect scrape quality and filtering parameters for '{src_id}'.",
                        risk=RiskLevel.LOW.value,
                        status=RecommendationStatus.PROPOSED.value,
                    )
                    recommendations.append(rec)
                elif qual_rate >= 0.60:
                    rec = LearningRecommendation(
                        recommendation_id=f"rec_src_highqual_{src_id}_{uuid.uuid4().hex[:6]}",
                        category="source_expansion",
                        scope=f"source:{src_id}",
                        summary=f"Consider increasing discovery focus on high-qualification source '{src_id}'",
                        evidence={
                            "source_id": src_id,
                            "qualification_rate": qual_rate,
                            "sample_size": n,
                        },
                        sample_size=n,
                        confidence_level=ConfidenceLevel.HIGH.value,
                        proposed_change=f"Operator may manually allocate more discovery runs to '{src_id}' in configuration.",
                        risk=RiskLevel.LOW.value,
                        status=RecommendationStatus.PROPOSED.value,
                    )
                    recommendations.append(rec)

        # 2. Evaluate Markets
        for m in markets_data:
            mkt_id = m["market_id"]
            if mkt_id in ("unknown", "all"):
                continue

            n = m["sample_size"]
            if n >= MIN_SAMPLE_FOR_RECOMMENDATION:
                reply_rate = m["reply_rate"]["rate"]
                if reply_rate < 0.05:
                    rec = LearningRecommendation(
                        recommendation_id=f"rec_mkt_lowreply_{mkt_id}_{uuid.uuid4().hex[:6]}",
                        category="market_research",
                        scope=f"market:{mkt_id}",
                        summary=f"Research market '{mkt_id}' more deeply due to low reply rate ({round(reply_rate*100, 1)}%)",
                        evidence={
                            "market_id": mkt_id,
                            "reply_rate": reply_rate,
                            "sample_size": n,
                        },
                        sample_size=n,
                        confidence_level=ConfidenceLevel.MEDIUM.value,
                        proposed_change=f"Evaluate local market language, BIM adoption standards, or specialized value proposition for {mkt_id}.",
                        risk=RiskLevel.LOW.value,
                        status=RecommendationStatus.PROPOSED.value,
                    )
                    recommendations.append(rec)

        # 3. Model Upgrade Recommendation
        # If current model is weak model qwen2.5:1.5b
        eval_runs = self.analytics_store.list_evaluation_runs()
        weak_runs = [r for r in eval_runs if "1.5b" in r.model_name.lower()]
        if weak_runs or not eval_runs:
            rec = LearningRecommendation(
                recommendation_id=f"rec_model_eval_stronger_{uuid.uuid4().hex[:6]}",
                category="model_evaluation",
                scope="llm:architecture",
                summary="Run offline evaluation benchmark on stronger model (e.g., 14B / 32B) on sanitized fixtures",
                evidence={
                    "baseline_model": "qwen2.5:1.5b",
                    "note": "Current weak local model exhibits known semantic nuance deferrals in complex replies.",
                },
                sample_size=len(weak_runs),
                confidence_level=ConfidenceLevel.HIGH.value,
                proposed_change="Execute offline evaluation suite with 14B/32B model on workstation to compare grounding and acceptance.",
                risk=RiskLevel.LOW.value,
                status=RecommendationStatus.PROPOSED.value,
            )
            recommendations.append(rec)

        # Persist generated recommendations
        for r in recommendations:
            self.analytics_store.record_recommendation(r)

        return recommendations

    def accept_recommendation(self, recommendation_id: str, reviewed_by: str = "operator") -> bool:
        """
        Records human decision to accept recommendation.
        CRITICAL: Does NOT automatically mutate any production configuration!
        """
        return self.analytics_store.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=RecommendationStatus.ACCEPTED.value,
            reviewed_by=reviewed_by,
        )

    def reject_recommendation(self, recommendation_id: str, reviewed_by: str = "operator") -> bool:
        """Records human decision to reject recommendation."""
        return self.analytics_store.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=RecommendationStatus.REJECTED.value,
            reviewed_by=reviewed_by,
        )

    def defer_recommendation(self, recommendation_id: str, reviewed_by: str = "operator") -> bool:
        """Records human decision to defer recommendation."""
        return self.analytics_store.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=RecommendationStatus.DEFERRED.value,
            reviewed_by=reviewed_by,
        )
