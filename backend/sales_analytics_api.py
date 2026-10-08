# backend/sales_analytics_api.py
"""
FastAPI Router for SalesAI Phase 12 Analytics & Learning Loop.
Prefix: /api/sales/analytics

Strict Invariants:
1. Zero mutating GET endpoints.
2. Normal analytics calls perform 0 LLM calls.
3. Recommendation acceptance records human decision only; zero automatic config changes.
4. Model evaluation performs zero live email sends.
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Query, status, Body
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from sales_engine.analytics import (
    AnalyticsStore,
    AnalyticsProjector,
    AnalyticsMetricsCalculator,
    RecommendationEngine,
    ModelEvaluator,
    AnalyticsExporter,
)

router = APIRouter(prefix="/api/sales/analytics", tags=["Sales-Analytics"])


def get_analytics_db_path() -> str:
    return os.getenv(
        "SALES_OUTREACH_DB",
        os.getenv("SALESAI_DB_PATH", os.getenv("SALESAI_CANDIDATES_DB", "data/sales_outreach.db")),
    )


def get_analytics_services():
    db_path = get_analytics_db_path()
    store = AnalyticsStore(db_path=db_path)
    projector = AnalyticsProjector(analytics_store=store, db_path=db_path)
    metrics = AnalyticsMetricsCalculator(analytics_store=store)
    recommender = RecommendationEngine(analytics_store=store, metrics_calculator=metrics)
    evaluator = ModelEvaluator(analytics_store=store)
    exporter = AnalyticsExporter(analytics_store=store, metrics_calculator=metrics)
    return {
        "store": store,
        "projector": projector,
        "metrics": metrics,
        "recommender": recommender,
        "evaluator": evaluator,
        "exporter": exporter,
    }


class EvaluateModelRequest(BaseModel):
    model_name: str = Field(default="qwen2.5:1.5b", description="Model name to evaluate")
    model_version: str = Field(default="1.5b-instruct-q4", description="Model version/tag")
    hardware_label: str = Field(default="current_weak_machine", description="Hardware identifier")
    task: Optional[str] = Field(default=None, description="Specific task or None for all")
    fixture_set_version: str = Field(default="phase12-v1", description="Fixture set version")
    notes: Optional[str] = None


class RecommendationActionRequest(BaseModel):
    reviewed_by: str = Field(default="operator", description="Operator identifier")


# -------------------------------------------------------------
# Core Analytics Endpoints (0 LLM Calls, Read-Only)
# -------------------------------------------------------------
@router.get("/overview")
def get_analytics_overview(
    window: str = Query("all", pattern="^(7d|30d|90d|all|custom)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Returns canonical outcome counts and high-level conversion rates."""
    svc = get_analytics_services()
    funnel = svc["metrics"].get_funnel_summary(window=window, start_date=start_date, end_date=end_date)
    diagnostics = svc["store"].get_diagnostics()
    return {
        "status": "success",
        "window": window,
        "funnel": funnel,
        "diagnostics": diagnostics,
    }


@router.get("/funnel")
def get_funnel_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all|custom)$"),
    relationship_type: Optional[str] = None,
    market_id: Optional[str] = None,
    source_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Returns detailed canonical outcome funnel with explicit numerator/denominators."""
    svc = get_analytics_services()
    return svc["metrics"].get_funnel_summary(
        window=window,
        relationship_type=relationship_type,
        market_id=market_id,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/sources")
def get_source_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns source performance breakdown with small-sample safety."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "sources": svc["metrics"].get_source_metrics(window=window),
    }


@router.get("/markets")
def get_market_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns market (country) performance metrics with small-sample safety."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "markets": svc["metrics"].get_market_metrics(window=window),
    }


@router.get("/contacts")
def get_contact_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns anonymized business contact role and seniority analytics."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "contacts": svc["metrics"].get_contact_metrics(window=window),
    }


@router.get("/messages")
def get_message_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns safe metadata on message length, tones, and languages."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "messages": svc["metrics"].get_message_metrics(window=window),
    }


@router.get("/replies")
def get_reply_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns breakdown of reply categories (positive, referral, auto-replies, bounces)."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "replies": svc["metrics"].get_reply_categories_metrics(window=window),
    }


@router.get("/opportunities")
def get_opportunity_metrics(
    window: str = Query("all", pattern="^(7d|30d|90d|all)$"),
):
    """Returns relationship types keeping service buyers separate from partners."""
    svc = get_analytics_services()
    return {
        "status": "success",
        "relationships": svc["metrics"].get_relationship_type_metrics(window=window),
    }


# -------------------------------------------------------------
# Model Evaluation Endpoints (Offline, Zero Email Sends)
# -------------------------------------------------------------
@router.get("/models")
def get_model_evaluations(
    model_name: Optional[str] = None,
    hardware_label: Optional[str] = None,
):
    """Returns recorded model evaluation runs."""
    svc = get_analytics_services()
    runs = svc["store"].list_evaluation_runs(model_name=model_name, hardware_label=hardware_label)
    return {
        "status": "success",
        "runs": [r.to_dict() for r in runs],
    }


@router.post("/models/evaluate")
def evaluate_model_endpoint(req: EvaluateModelRequest):
    """
    Triggers an offline evaluation run against sanitized fixtures.
    Zero outbound email sends.
    """
    svc = get_analytics_services()
    run = svc["evaluator"].evaluate_model(
        model_name=req.model_name,
        model_version=req.model_version,
        hardware_label=req.hardware_label,
        task=req.task,
        fixture_set_version=req.fixture_set_version,
        notes=req.notes,
    )
    return {
        "status": "success",
        "run": run.to_dict(),
    }


# -------------------------------------------------------------
# Learning Recommendations Endpoints (Human-Review Only)
# -------------------------------------------------------------
@router.get("/recommendations")
def get_recommendations(
    status: Optional[str] = None,
    category: Optional[str] = None,
):
    """Returns learning recommendations."""
    svc = get_analytics_services()
    recs = svc["store"].list_recommendations(status=status, category=category)
    if not recs:
        # Generate initial recommendations if none exist
        recs = svc["recommender"].generate_recommendations()
    return {
        "status": "success",
        "recommendations": [r.to_dict() for r in recs],
    }


@router.post("/recommendations/{recommendation_id}/accept")
def accept_recommendation_endpoint(
    recommendation_id: str,
    req: Optional[RecommendationActionRequest] = None,
):
    """
    Records human decision to accept recommendation.
    STRICT INVARIANT: Does NOT automatically mutate production configuration!
    """
    svc = get_analytics_services()
    reviewer = req.reviewed_by if req else "operator"
    ok = svc["recommender"].accept_recommendation(recommendation_id, reviewed_by=reviewer)
    if not ok:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return {
        "status": "success",
        "recommendation_id": recommendation_id,
        "action": "accepted",
        "message": "Human decision recorded. No automatic configuration changes applied.",
    }


@router.post("/recommendations/{recommendation_id}/reject")
def reject_recommendation_endpoint(
    recommendation_id: str,
    req: Optional[RecommendationActionRequest] = None,
):
    """Records human decision to reject recommendation."""
    svc = get_analytics_services()
    reviewer = req.reviewed_by if req else "operator"
    ok = svc["recommender"].reject_recommendation(recommendation_id, reviewed_by=reviewer)
    if not ok:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return {
        "status": "success",
        "recommendation_id": recommendation_id,
        "action": "rejected",
        "message": "Human decision recorded.",
    }


# -------------------------------------------------------------
# Maintenance & Projection Rebuild
# -------------------------------------------------------------
@router.post("/rebuild")
def rebuild_analytics_projection():
    """Reconstructs analytics events idempotently from operational tables."""
    svc = get_analytics_services()
    stats = svc["projector"].run_projection()
    return {
        "status": "success",
        "projection_stats": stats,
    }


# -------------------------------------------------------------
# Export Endpoints
# -------------------------------------------------------------
@router.get("/export")
def export_analytics(format: str = Query("json", pattern="^(json|csv)$")):
    """Exports sanitized analytics summary."""
    svc = get_analytics_services()
    if format == "csv":
        csv_data = svc["exporter"].export_events_csv()
        return PlainTextResponse(content=csv_data, media_type="text/csv")
    json_data = svc["exporter"].export_summary_json()
    return PlainTextResponse(content=json_data, media_type="application/json")
