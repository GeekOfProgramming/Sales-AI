# sales_engine/analytics/export.py
"""
AnalyticsExporter: Exports sanitized analytics data to CSV and JSON formats.
Excludes secrets, private credentials, and personal email contents.
"""

import csv
import io
import json
from typing import Dict, Any, List

from sales_engine.analytics.models import AnalyticsEvent
from sales_engine.analytics.store import AnalyticsStore
from sales_engine.analytics.metrics import AnalyticsMetricsCalculator


class AnalyticsExporter:
    def __init__(
        self,
        analytics_store: AnalyticsStore,
        metrics_calculator: AnalyticsMetricsCalculator,
    ):
        self.analytics_store = analytics_store
        self.metrics_calculator = metrics_calculator

    def export_summary_json(self) -> str:
        """Returns full analytics state formatted as JSON."""
        funnel = self.metrics_calculator.get_funnel_summary(window="all")
        sources = self.metrics_calculator.get_source_metrics(window="all")
        markets = self.metrics_calculator.get_market_metrics(window="all")
        relationships = self.metrics_calculator.get_relationship_type_metrics(window="all")
        eval_runs = [r.to_dict() for r in self.analytics_store.list_evaluation_runs()]
        recommendations = [r.to_dict() for r in self.analytics_store.list_recommendations()]
        diagnostics = self.analytics_store.get_diagnostics()

        payload = {
            "funnel": funnel,
            "sources": sources,
            "markets": markets,
            "relationships": relationships,
            "evaluation_runs": eval_runs,
            "recommendations": recommendations,
            "diagnostics": diagnostics,
        }
        return json.dumps(payload, indent=2)

    def export_events_csv(self, limit: int = 5000) -> str:
        """Exports sanitized analytics events to CSV."""
        events = self.analytics_store.list_events(limit=limit)
        output = io.StringIO()
        fieldnames = [
            "event_id", "event_type", "occurred_at", "company_id", "market_id",
            "source_id", "relationship_type", "company_tier", "bim_maturity",
            "model_name", "provenance",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for e in events:
            writer.writerow(e.to_dict())
        return output.getvalue()

    def export_sources_csv(self) -> str:
        """Exports source metrics to CSV."""
        sources = self.metrics_calculator.get_source_metrics(window="all")
        output = io.StringIO()
        fieldnames = [
            "source_id", "discovered", "qualified", "emails_sent",
            "human_replies", "positive_replies", "meetings_scheduled",
            "sample_size", "status",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for s in sources:
            row = {
                "source_id": s["source_id"],
                "discovered": s["discovered"],
                "qualified": s["qualified"],
                "emails_sent": s["emails_sent"],
                "human_replies": s["human_replies"],
                "positive_replies": s["positive_replies"],
                "meetings_scheduled": s["meetings_scheduled"],
                "sample_size": s["sample_size"],
                "status": s["status"],
            }
            writer.writerow(row)
        return output.getvalue()
