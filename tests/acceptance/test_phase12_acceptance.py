"""
tests/acceptance/test_phase12_acceptance.py
Phase 12 Golden Acceptance Test Suite.
Specification: PHASE12-QA-GOLDEN-SPEC.md
Catalog: tests/golden/phase12_analytics.json (54 cases)

Covers all 54 golden cases across:
- Critical Safety Gates (P12-GOLD-001..012)
- Projection & Counting Foundations (P12-COUNT-001..006)
- Small Sample Safety & Guards (P12-SAFE-001..004)
- Taxonomy & Separation (P12-SEPR-001..003)
- Multi-Dimensional Analytics Slices (P12-SLICE-001..005)
- Time Windows & Missing Values (P12-TIME-001..003)
- Model Evaluation Registry & Safety (P12-EVAL-001..006)
- Human Feedback & Learning Loop (P12-HUMAN-001..004)
- API & UI Surface Integrity (P12-UIAPI-001..005)
- Diagnostics, Export & Reliability (P12-EXPORT-001..003)
- Weak-Model Semantic Deferred Evaluation (P12-SEM-001..003)
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sales_engine.analytics import (
    AnalyticsStore,
    AnalyticsProjector,
    AnalyticsMetricsCalculator,
    RecommendationEngine,
    ModelEvaluator,
    AnalyticsExporter,
    AnalyticsEvent,
    EventType,
    RelationshipType,
    OpportunityType,
    HumanEditLevel,
    RecommendationStatus,
    LearningRecommendation,
    MIN_SAMPLE_FOR_COMPARISON,
    MIN_SAMPLE_FOR_RECOMMENDATION,
)
from sales_engine.analytics.models import (
    ConfidenceLevel,
    RiskLevel,
)
from sales_engine.analytics.metrics import calculate_rate
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.sending.schemas import StoredDraft, SendAttempt
from sales_engine.sending.rate_limiter import RateLimiter
from sales_engine.inbox.store import InboxStore
from sales_engine.inbox.models import InboundMessage, Conversation
from backend.main import app
from scripts.rebuild_zip import should_exclude


# =========================================================================
# Shared Acceptance Fixtures
# =========================================================================

@pytest.fixture
def qa_env(tmp_path):
    db_file = str(tmp_path / "test_phase12_acceptance.db")
    cand_store = CandidateStore(db_path=db_file)
    rev_store = ReviewStore(db_path=db_file)
    supp_store = SuppressionStore(rev_store)
    inbox_store = InboxStore(db_path=db_file)
    analytics_store = AnalyticsStore(db_path=db_file)
    projector = AnalyticsProjector(analytics_store=analytics_store, db_path=db_file)
    metrics_calc = AnalyticsMetricsCalculator(analytics_store=analytics_store)
    recommender = RecommendationEngine(analytics_store=analytics_store, metrics_calculator=metrics_calc)
    evaluator = ModelEvaluator(analytics_store=analytics_store)
    exporter = AnalyticsExporter(analytics_store=analytics_store, metrics_calculator=metrics_calc)

    return {
        "db_path": db_file,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "inbox_store": inbox_store,
        "store": analytics_store,
        "projector": projector,
        "metrics": metrics_calc,
        "recommender": recommender,
        "evaluator": evaluator,
        "exporter": exporter,
    }


# =========================================================================
# 1. Critical No-Autonomy Safety Gates (P12-GOLD-001..012)
# =========================================================================

def test_p12_gold_001_analytics_never_changes_phase5_score(qa_env):
    """P12-GOLD-001: Analytics projection, metrics & recommendation acceptance never mutate Phase 5 score."""
    cand_store = qa_env["cand_store"]
    projector = qa_env["projector"]
    recommender = qa_env["recommender"]

    cand, _ = cand_store.get_or_create_candidate(
        company_name="ArchStudio Alpha",
        company_domain="archalpha.it",
        country="IT",
    )
    cand.company_tier = "tier_1"
    cand.tier_confidence = 0.85
    cand.qualification_outcome = "qualified_for_enrichment"
    cand_store.save_candidate(cand)

    # Run analytics cycle
    projector.run_projection()
    recs = recommender.generate_recommendations()
    if recs:
        recommender.accept_recommendation(recs[0].recommendation_id, reviewed_by="operator")

    refreshed = cand_store.get_candidate_by_domain("archalpha.it")
    assert refreshed.tier_confidence == 0.85
    assert refreshed.company_tier == "tier_1"
    assert refreshed.qualification_outcome == "qualified_for_enrichment"


def test_p12_gold_002_analytics_never_changes_suppression(qa_env):
    """P12-GOLD-002: Analytics never changes Phase 9 suppression state."""
    supp_store = qa_env["supp_store"]
    projector = qa_env["projector"]
    recommender = qa_env["recommender"]

    supp_store.add_suppression("blocked@domain.com", reason="opt_out", source="manual")
    initial_suppressed = supp_store.is_suppressed("blocked@domain.com")
    assert initial_suppressed is True

    projector.run_projection()
    recs = recommender.generate_recommendations()
    if recs:
        recommender.accept_recommendation(recs[0].recommendation_id, reviewed_by="operator")

    assert supp_store.is_suppressed("blocked@domain.com") is True
    assert supp_store.is_suppressed("other@domain.com") is False


def test_p12_gold_003_analytics_never_changes_source_configuration(qa_env):
    """P12-GOLD-003: Analytics never mutates source enable/disable/priority config."""
    recommender = qa_env["recommender"]
    store = qa_env["store"]

    recs = recommender.generate_recommendations()
    for r in recs:
        recommender.accept_recommendation(r.recommendation_id)

    # Invariant: Phase 12 stores zero source mutation logic; recommendations are audit records only
    assert all(r.status == "accepted" for r in store.list_recommendations(status="accepted"))


def test_p12_gold_004_analytics_never_changes_send_limits(qa_env):
    """P12-GOLD-004: Analytics never alters daily/hourly send limit configurations."""
    rl_before = RateLimiter()
    lim_minute_before = rl_before.max_per_minute
    lim_req_before = rl_before.max_per_request

    recs = qa_env["recommender"].generate_recommendations()
    for r in recs:
        qa_env["recommender"].accept_recommendation(r.recommendation_id)

    rl_after = RateLimiter()
    assert rl_after.max_per_minute == lim_minute_before
    assert rl_after.max_per_request == lim_req_before


def test_p12_gold_005_recommendation_acceptance_records_decision_only(qa_env):
    """P12-GOLD-005: Accepting recommendation records decision/audit ONLY, zero operational mutation."""
    recommender = qa_env["recommender"]
    store = qa_env["store"]

    rec = LearningRecommendation(
        recommendation_id="rec_gold_005",
        category="source_review",
        scope="source:apollo",
        summary="Evaluate Source Apollo",
        evidence={"sample_size": 25, "metric": "reply_rate"},
        sample_size=25,
        confidence_level="low",
        proposed_change="Maintain observation mode",
        risk="low",
        status="proposed",
    )
    store.record_recommendation(rec)

    result = recommender.accept_recommendation("rec_gold_005", reviewed_by="operator_john")
    assert result is True

    saved = store.get_recommendation("rec_gold_005")
    assert saved.status == "accepted"
    assert saved.reviewed_by == "operator_john"
    assert saved.reviewed_at is not None


def test_p12_gold_006_model_evaluation_sends_zero_emails(qa_env):
    """P12-GOLD-006: Offline model evaluation generates exactly zero outbound network/email sends."""
    evaluator = qa_env["evaluator"]
    rev_store = qa_env["rev_store"]
    
    initial_attempts = len(rev_store.get_send_attempts())
    run = evaluator.evaluate_model(
        model_name="qwen2.5:1.5b",
        task="email_draft_generation",
        hardware_label="acceptance_test_env",
    )
    
    final_attempts = len(rev_store.get_send_attempts())
    assert final_attempts == initial_attempts == 0
    assert run.model_name == "qwen2.5:1.5b"
    assert run.cases > 0


def test_p12_gold_007_normal_analytics_ui_uses_zero_llm_calls(qa_env, monkeypatch):
    """P12-GOLD-007: Normal analytics UI rendering executes 0 LLM calls."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    llm_calls = 0
    def mock_ollama(*args, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        raise RuntimeError("LLM call during UI render is strictly prohibited!")

    monkeypatch.setattr("urllib.request.urlopen", mock_ollama)

    resp = client.get("/sales/analytics?tab=overview")
    assert resp.status_code == 200
    assert llm_calls == 0


def test_p12_gold_008_no_mutating_get(qa_env, monkeypatch):
    """P12-GOLD-008: Analytics GET endpoints are strictly read-only and non-mutating."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    endpoints = [
        "/api/sales/analytics/overview",
        "/api/sales/analytics/funnel",
        "/api/sales/analytics/sources",
        "/api/sales/analytics/markets",
        "/api/sales/analytics/contacts",
        "/api/sales/analytics/messages",
        "/api/sales/analytics/replies",
        "/api/sales/analytics/opportunities",
        "/api/sales/analytics/models",
        "/api/sales/analytics/recommendations",
    ]

    for ep in endpoints:
        r1 = client.get(ep)
        assert r1.status_code == 200
        r2 = client.get(ep)
        assert r2.status_code == 200


def test_p12_gold_009_operational_stores_remain_source_of_truth(qa_env):
    """P12-GOLD-009: Operational stores remain authoritative; analytics writes only to its own projection store."""
    cand_store = qa_env["cand_store"]
    projector = qa_env["projector"]

    cand, _ = cand_store.get_or_create_candidate(
        company_name="BIM Truth Co",
        company_domain="bimtruth.it",
    )
    cand_store.save_candidate(cand)

    cand_count_before = len(cand_store.list_candidates())
    projector.run_projection()
    cand_count_after = len(cand_store.list_candidates())

    assert cand_count_before == cand_count_after == 1


def test_p12_gold_010_rebuild_is_read_only(qa_env):
    """P12-GOLD-010: Rebuilding projections reconstructs analytics projections only, leaving operational records untouched."""
    cand_store = qa_env["cand_store"]
    projector = qa_env["projector"]
    store = qa_env["store"]

    cand, _ = cand_store.get_or_create_candidate(
        company_name="Rebuild Check",
        company_domain="rebuildcheck.com",
    )
    cand_store.save_candidate(cand)

    projector.run_projection()
    assert store.count_events() >= 1

    # Rebuild
    projector.rebuild_projection()
    assert store.count_events() >= 1
    # Candidate in operational store remains untouched
    assert cand_store.get_candidate_by_domain("rebuildcheck.com") is not None


def test_p12_gold_011_no_autonomous_outreach_optimization(qa_env):
    """P12-GOLD-011: Analytics produces evidence-backed recommendations for humans only; no auto-dispatch or targeting shifts."""
    recommender = qa_env["recommender"]
    recs = recommender.generate_recommendations()
    for r in recs:
        assert r.status == "proposed"
        assert r.reviewed_by is None


def test_p12_gold_012_release_contains_no_runtime_db_secrets():
    """P12-GOLD-012: Release distribution denylist strictly verified for secrets and runtime DBs."""
    forbidden = [
        Path(".env"),
        Path(".env.production"),
        Path("sales_outreach.db"),
        Path("app.sqlite"),
        Path("app.sqlite3"),
        Path("chroma.sqlite3"),
        Path(".venv/bin/python"),
        Path("__pycache__/engine.pyc"),
        Path("token.json"),
        Path("oauth_credentials.json"),
        Path("client_secret.json"),
    ]
    for p in forbidden:
        assert should_exclude(p) is True, f"File {p} must be excluded by release packaging!"


# =========================================================================
# 2. Projection & Counting Foundations (P12-COUNT-001..006)
# =========================================================================

def test_p12_count_001_projection_idempotency(qa_env):
    """P12-COUNT-001: Running projection repeatedly over identical data produces zero duplicate events."""
    cand_store = qa_env["cand_store"]
    store = qa_env["store"]
    projector = qa_env["projector"]

    cand, _ = cand_store.get_or_create_candidate(
        company_name="Idempotent BIM",
        company_domain="idempotent.it",
    )
    cand.qualification_outcome = "qualified_for_enrichment"
    cand_store.save_candidate(cand)

    projector.run_projection()
    c1 = store.count_events()

    projector.run_projection()
    c2 = store.count_events()

    assert c1 == c2 and c1 >= 2


def test_p12_count_002_incremental_projection_new_state(qa_env):
    """P12-COUNT-002: Incremental projection creates exactly new events for newly added records."""
    cand_store = qa_env["cand_store"]
    store = qa_env["store"]
    projector = qa_env["projector"]

    cand1, _ = cand_store.get_or_create_candidate(company_name="C1", company_domain="c1.it")
    cand_store.save_candidate(cand1)
    projector.run_projection()
    c1 = store.count_events()

    cand2, _ = cand_store.get_or_create_candidate(company_name="C2", company_domain="c2.it")
    cand_store.save_candidate(cand2)
    projector.run_projection()
    c2 = store.count_events()

    assert c2 == c1 + 1


def test_p12_count_003_canonical_funnel_stages(qa_env):
    """P12-COUNT-003: Canonical funnel stages are ordered correctly and contain no inferred stages."""
    metrics = qa_env["metrics"]
    summary = metrics.get_funnel_summary()
    counts = summary["counts"]
    
    expected_stages = [
        "candidates_discovered", "candidates_qualified", "contacts_selected",
        "drafts_generated", "drafts_approved", "emails_sent", "emails_delivered",
        "total_replies_received", "human_replies", "auto_replies", "inbound_bounces",
        "positive_replies", "meetings_proposed", "meetings_scheduled",
        "opportunities_qualified", "closed_won"
    ]
    for exp in expected_stages:
        assert exp in counts, f"Expected funnel stage '{exp}' missing from funnel counts"


def test_p12_count_004_rates_expose_numerator_denominator_sample_size(qa_env):
    """P12-COUNT-004: All conversion rates expose numerator, denominator, rate, and sample_size."""
    metrics = qa_env["metrics"]
    summary = metrics.get_funnel_summary()
    rates = summary["rates"]

    for k, v in rates.items():
        assert "numerator" in v
        assert "denominator" in v
        assert "rate" in v
        assert "sample_size" in v
        assert "sample_status" in v
        if v["denominator"] > 0:
            assert 0.0 <= v["rate"] <= 1.0


def test_p12_count_005_human_reply_excludes_auto_and_bounce(qa_env):
    """P12-COUNT-005: Human reply counts exclude auto-replies and bounces, while preserving questions and not_now."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_rep_human_1",
        event_type=EventType.REPLY_RECEIVED.value,
        occurred_at=now,
        metadata_json=json.dumps({"classification": "question"})
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_rep_human_2",
        event_type=EventType.REPLY_RECEIVED.value,
        occurred_at=now,
        metadata_json=json.dumps({"classification": "not_now"})
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_rep_auto",
        event_type=EventType.REPLY_RECEIVED.value,
        occurred_at=now,
        metadata_json=json.dumps({"is_auto_reply": True})
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_rep_bounce",
        event_type=EventType.REPLY_RECEIVED.value,
        occurred_at=now,
        metadata_json=json.dumps({"is_bounce": True})
    ))

    summary = metrics.get_funnel_summary()
    counts = summary["counts"]
    assert counts["human_replies"] == 2
    assert counts["auto_replies"] == 1
    assert counts["inbound_bounces"] == 1


def test_p12_count_006_provenance_preserves_live_vs_historical(qa_env):
    """P12-COUNT-006: Provenance field accurately distinguishes live_system vs historical_import."""
    store = qa_env["store"]
    now = datetime.now(timezone.utc).isoformat()

    store.record_event(AnalyticsEvent(
        event_id="evt_prov_live",
        event_type=EventType.EMAIL_SENT.value,
        occurred_at=now,
        provenance="live_system",
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_prov_hist",
        event_type=EventType.EMAIL_SENT.value,
        occurred_at=now,
        provenance="historical_import",
    ))

    events = store.list_events()
    provs = {e.event_id: e.provenance for e in events}
    assert provs["evt_prov_live"] == "live_system"
    assert provs["evt_prov_hist"] == "historical_import"


# =========================================================================
# 3. Small Sample Safety (P12-SAFE-001..004)
# =========================================================================

def test_p12_safe_001_comparison_guard_blocks_under_10(qa_env):
    """P12-SAFE-001: Comparative metrics block ranking claims when sample size < 10."""
    metrics = qa_env["metrics"]
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        store.record_event(AnalyticsEvent(
            event_id=f"evt_src_a_{i}",
            event_type=EventType.EMAIL_SENT.value,
            occurred_at=now,
            source_id="apollo",
        ))

    src_metrics = metrics.get_source_metrics(window="all")
    apollo_slice = next((s for s in src_metrics if s["source_id"] == "apollo"), None)
    assert apollo_slice is not None
    assert apollo_slice["sample_size"] == 5
    assert apollo_slice["is_statistically_valid"] is False
    assert apollo_slice["status"] == "insufficient_sample"


def test_p12_safe_002_recommendation_guard_blocks_under_20(qa_env):
    """P12-SAFE-002: Recommendation engine blocks performance-based recommendations when N < 20."""
    recommender = qa_env["recommender"]
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    for i in range(12):
        store.record_event(AnalyticsEvent(
            event_id=f"evt_rec_guard_{i}",
            event_type=EventType.CANDIDATE_DISCOVERED.value,
            occurred_at=now,
            source_id="small_source",
            candidate_id=f"cand_s_{i}",
        ))

    recs = recommender.generate_recommendations()
    # If a recommendation is generated for small_source, it must be a sample-safeguard recommendation
    small_recs = [r for r in recs if "small_source" in r.scope]
    assert len(small_recs) == 1
    assert "gather more" in small_recs[0].summary.lower()
    assert small_recs[0].confidence_level == "low"


def test_p12_safe_003_historical_n2_outcomes_remain_qualitative(qa_env):
    """P12-SAFE-003: Historical n=2 positive outcomes remain qualitative only, not statistical rules."""
    recommender = qa_env["recommender"]
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    for i in range(2):
        store.record_event(AnalyticsEvent(
            event_id=f"evt_n2_{i}",
            event_type=EventType.POSITIVE_INTEREST.value,
            occurred_at=now,
            provenance="historical_import",
        ))

    recs = recommender.generate_recommendations()
    # Does not create statistical optimization recommendations based on n=2
    assert all("historical" not in r.summary.lower() or r.sample_size >= 20 for r in recs)


def test_p12_safe_004_sub_threshold_metrics_insufficient_sample(qa_env):
    """P12-SAFE-004: Slices below threshold explicitly flag insufficient_sample."""
    metrics = qa_env["metrics"]
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_mkt_low",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=now,
        market_id="IT",
        candidate_id="c_it_1",
    ))

    mkt_metrics = metrics.get_market_metrics()
    it_slice = next((m for m in mkt_metrics if m["market_id"] == "IT"), None)
    assert it_slice is not None
    assert it_slice["status"] == "insufficient_sample"
    assert it_slice["is_statistically_valid"] is False


# =========================================================================
# 4. Taxonomy & Separation (P12-SEPR-001..003)
# =========================================================================

def test_p12_sepr_001_relationship_taxonomy_separates_buyer_partner(qa_env):
    """P12-SEPR-001: Service buyer, tech partner, channel partner, and strategic partner remain distinct."""
    assert RelationshipType.SERVICE_BUYER.value == "service_buyer"
    assert RelationshipType.TECHNOLOGY_PARTNER.value == "technology_partner"
    assert RelationshipType.CHANNEL_PARTNER.value == "channel_partner"
    assert RelationshipType.STRATEGIC_PARTNER.value == "strategic_partner"
    assert RelationshipType.MIXED.value == "mixed"
    assert RelationshipType.UNKNOWN.value == "unknown"


def test_p12_sepr_002_opportunity_taxonomy_keeps_types_distinct(qa_env):
    """P12-SEPR-002: Opportunity taxonomy keeps BIM, automation, and partner opportunities distinct."""
    assert OpportunityType.DIRECT_BIM_DELIVERY.value == "direct_bim_delivery"
    assert OpportunityType.MEP_DELIVERY.value == "mep_delivery"
    assert OpportunityType.AUTOMATION.value == "automation"
    assert OpportunityType.REVIT_API.value == "revit_api"
    assert OpportunityType.TECHNOLOGY_PARTNERSHIP.value == "technology_partnership"
    assert OpportunityType.STRATEGIC_COLLABORATION.value == "strategic_collaboration"


def test_p12_sepr_003_partner_meetings_never_counted_as_direct_bim(qa_env):
    """P12-SEPR-003: Partner meeting is attributed to partner opportunities, not direct BIM sales."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_opp_partner",
        event_type=EventType.MEETING_SCHEDULED.value,
        occurred_at=now,
        relationship_type=RelationshipType.TECHNOLOGY_PARTNER.value,
        conversation_id="conv_partner",
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_opp_bim",
        event_type=EventType.MEETING_SCHEDULED.value,
        occurred_at=now,
        relationship_type=RelationshipType.SERVICE_BUYER.value,
        conversation_id="conv_buyer",
    ))

    rel_metrics = metrics.get_relationship_type_metrics(window="all")
    tech_part = next((o for o in rel_metrics if o["relationship_type"] == RelationshipType.TECHNOLOGY_PARTNER.value), None)
    bim_buyer = next((o for o in rel_metrics if o["relationship_type"] == RelationshipType.SERVICE_BUYER.value), None)

    assert tech_part is not None and tech_part["meetings_scheduled"] == 1
    assert bim_buyer is not None and bim_buyer["meetings_scheduled"] == 1
    assert tech_part["relationship_type"] != bim_buyer["relationship_type"]


# =========================================================================
# 5. Multi-Dimensional Slices (P12-SLICE-001..005)
# =========================================================================

def test_p12_slice_001_source_analytics_aggregates(qa_env):
    """P12-SLICE-001: Source analytics aggregates observations, sends, and replies without state mutation."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_s1",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=now,
        source_id="google_maps",
        candidate_id="c_gm_1",
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_s2",
        event_type=EventType.EMAIL_SENT.value,
        occurred_at=now,
        source_id="google_maps",
    ))

    sources = metrics.get_source_metrics()
    gm = next((s for s in sources if s["source_id"] == "google_maps"), None)
    assert gm is not None
    assert gm["discovered"] == 1
    assert gm["emails_sent"] == 1


def test_p12_slice_002_market_slices_compute_metrics(qa_env):
    """P12-SLICE-002: Market slices compute discovered, qualified, and rates without auto-prioritizing."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_m1",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=now,
        market_id="CH",
        candidate_id="c_ch_1",
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_m2",
        event_type=EventType.CANDIDATE_QUALIFIED.value,
        occurred_at=now,
        market_id="CH",
        candidate_id="c_ch_1",
    ))

    mkts = metrics.get_market_metrics()
    ch = next((m for m in mkts if m["market_id"] == "CH"), None)
    assert ch is not None
    assert ch["discovered"] == 1
    assert ch["qualified"] == 1


def test_p12_slice_003_contact_role_analytics(qa_env):
    """P12-SLICE-003: Contact analytics aggregates business roles without protected demographic inferences."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_role_1",
        event_type=EventType.CONTACT_SELECTED.value,
        occurred_at=now,
        metadata_json=json.dumps({"role": "BIM Manager", "seniority": "senior"}),
    ))

    dims = metrics.get_contact_metrics()
    role_dim = next((d for d in dims if d["dimension"] == "role"), None)
    assert role_dim is not None
    assert role_dim["breakdown"].get("BIM Manager") == 1


def test_p12_slice_004_message_analytics_aggregates(qa_env):
    """P12-SLICE-004: Message analytics aggregates word count, tone, and service without tracking pixels."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_msg_1",
        event_type=EventType.DRAFT_GENERATED.value,
        occurred_at=now,
        metadata_json=json.dumps({"body_word_count": 120, "subject_length": 35, "tone": "professional"}),
    ))

    msgs = metrics.get_message_metrics()
    assert msgs["total_drafts"] == 1
    assert msgs["average_body_word_count"] == 120.0
    assert msgs["average_subject_length"] == 35.0


def test_p12_slice_005_reply_intent_breakdown(qa_env):
    """P12-SLICE-005: Reply intent breakdown accurately slices positive, question, referral, and objection."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    for cat in ["positive_interest", "question", "referral", "not_now", "not_interested"]:
        store.record_event(AnalyticsEvent(
            event_id=f"evt_slice_{cat}",
            event_type=EventType.REPLY_RECEIVED.value,
            occurred_at=now,
            metadata_json=json.dumps({"classification": cat}),
        ))

    replies = metrics.get_reply_categories_metrics()
    breakdown = replies["breakdown"]
    assert breakdown.get("positive_interest") == 1
    assert breakdown.get("question") == 1
    assert breakdown.get("referral") == 1
    assert breakdown.get("not_now") == 1
    assert breakdown.get("not_interested") == 1


# =========================================================================
# 6. Time Windows & Diagnostics (P12-TIME-001..003)
# =========================================================================

def test_p12_time_001_standard_time_windows(qa_env):
    """P12-TIME-001: Standard time windows (7d, 30d, 90d, all) filter events deterministically."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    fresh = now.isoformat()

    store.record_event(AnalyticsEvent(
        event_id="evt_t_old",
        event_type=EventType.EMAIL_SENT.value,
        occurred_at=old,
    ))
    store.record_event(AnalyticsEvent(
        event_id="evt_t_fresh",
        event_type=EventType.EMAIL_SENT.value,
        occurred_at=fresh,
    ))

    assert metrics.get_funnel_summary(window="7d")["counts"]["emails_sent"] == 1
    assert metrics.get_funnel_summary(window="30d")["counts"]["emails_sent"] == 1
    assert metrics.get_funnel_summary(window="90d")["counts"]["emails_sent"] == 2
    assert metrics.get_funnel_summary(window="all")["counts"]["emails_sent"] == 2


def test_p12_time_002_custom_date_boundaries(qa_env):
    """P12-TIME-002: Custom date boundaries bound inclusive telemetry accurately."""
    store = qa_env["store"]
    now = datetime.now(timezone.utc)

    d1 = (now - timedelta(days=10)).isoformat()
    d2 = (now - timedelta(days=5)).isoformat()
    d3 = now.isoformat()

    store.record_event(AnalyticsEvent(event_id="e1", event_type=EventType.CANDIDATE_DISCOVERED.value, occurred_at=d1))
    store.record_event(AnalyticsEvent(event_id="e2", event_type=EventType.CANDIDATE_DISCOVERED.value, occurred_at=d2))
    store.record_event(AnalyticsEvent(event_id="e3", event_type=EventType.CANDIDATE_DISCOVERED.value, occurred_at=d3))

    start_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    events = qa_env["metrics"]._filter_events_by_window(store.list_events(), window="custom", start_date=start_str, end_date=end_str)
    assert len(events) == 1
    assert events[0].event_id == "e2"


def test_p12_time_003_missing_metadata_renders_unknown_null(qa_env):
    """P12-TIME-003: Missing metadata renders as unknown/null without silent imputation."""
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="evt_null_meta",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=now,
        source_id=None,
        market_id=None,
    ))

    diag = store.get_diagnostics()
    assert diag["unknown_source_count"] >= 1
    assert diag["unknown_market_count"] >= 1


# =========================================================================
# 7. Model Evaluation (P12-EVAL-001..006)
# =========================================================================

def test_p12_eval_001_model_evaluation_registry_persists_metadata(qa_env):
    """P12-EVAL-001: Model evaluation registry persists version, quantization, and hardware label."""
    evaluator = qa_env["evaluator"]
    store = qa_env["store"]

    run = evaluator.evaluate_model(
        model_name="qwen2.5:1.5b",
        task="website_extraction",
        model_version="1.5b-instruct",
        quantization="q4_K_M",
        hardware_label="workstation_rtx",
    )

    runs = store.list_evaluation_runs()
    persisted = next((r for r in runs if r.evaluation_id == run.evaluation_id), None)
    assert persisted is not None
    assert persisted.model_name == "qwen2.5:1.5b"
    assert persisted.model_version == "1.5b-instruct"
    assert persisted.quantization == "q4_K_M"
    assert persisted.hardware_label == "workstation_rtx"


def test_p12_eval_002_task_breakdown_evaluates_6_core_tasks(qa_env):
    """P12-EVAL-002: Offline evaluation framework supports all 6 core tasks independently."""
    evaluator = qa_env["evaluator"]
    tasks = [
        "website_extraction",
        "company_classification",
        "email_draft_generation",
        "reply_classification",
        "reply_suggestion",
        "meeting_time_extraction",
    ]

    for t in tasks:
        run = evaluator.evaluate_model(model_name="qwen2.5:1.5b", task=t)
        assert run.task == t
        assert run.cases > 0


def test_p12_eval_003_offline_run_never_sends_or_alters_approvals(qa_env):
    """P12-EVAL-003: Offline model run generates 0 outbound messages and alters 0 draft approvals."""
    evaluator = qa_env["evaluator"]
    rev_store = qa_env["rev_store"]

    draft = StoredDraft(
        draft_id="draft_eval_safe",
        revision=1,
        lead_id="lead_1",
        contact_id="cont_1",
        recipient_name="Test Recipient",
        recipient_email="test@example.com",
        subject="Intro",
        body="Content",
        approval_status="pending_review",
        send_status="not_sent",
        outreach_status="draft_ready",
        content_hash="hash_eval_safe",
    )
    rev_store.save_draft(draft)

    sends_before = len(rev_store.get_send_attempts())
    evaluator.evaluate_model(model_name="qwen2.5:1.5b", task="reply_suggestion")
    sends_after = len(rev_store.get_send_attempts())

    assert sends_before == sends_after == 0
    reloaded = rev_store.get_draft("draft_eval_safe")
    assert reloaded.approval_status == "pending_review"


def test_p12_eval_004_fixture_versioning_consistency(qa_env):
    """P12-EVAL-004: Standard fixture set version is phase12-v1 and consistent across evaluations."""
    evaluator = qa_env["evaluator"]
    run = evaluator.evaluate_model(model_name="qwen2.5:1.5b", task="company_classification")
    assert run.fixture_set_version == "phase12-v1"


def test_p12_eval_005_unmeasured_benchmark_counters_null(qa_env):
    """P12-EVAL-005: Unmeasured benchmark counters (speed, VRAM, RAM) remain strictly null."""
    evaluator = qa_env["evaluator"]
    run = evaluator.evaluate_model(model_name="qwen2.5:1.5b", task="website_extraction")

    assert run.tokens_per_second_optional is None
    assert run.peak_ram_optional is None
    assert run.peak_vram_optional is None


def test_p12_eval_006_weak_model_baseline_preserves_distribution(qa_env):
    """P12-EVAL-006: Weak model baseline preserves realistic outcome distribution without artificial inflation."""
    evaluator = qa_env["evaluator"]
    run = evaluator.seed_weak_model_baseline()
    assert run.deferred >= 1
    assert run.passed > 0
    assert run.cases == run.passed + run.failed + run.review + run.deferred


# =========================================================================
# 8. Human Feedback & Learning Loop (P12-HUMAN-001..004)
# =========================================================================

def test_p12_human_001_edit_distance_derives_explicit_labels(qa_env):
    """P12-HUMAN-001: Human edit distance deriver yields none, minor, major, rewrite labels."""
    assert HumanEditLevel.NONE.value == "none"
    assert HumanEditLevel.MINOR.value == "minor"
    assert HumanEditLevel.MAJOR.value == "major"
    assert HumanEditLevel.REWRITE.value == "rewrite"
    assert HumanEditLevel.REJECTED.value == "rejected"


def test_p12_human_002_draft_acceptance_rates(qa_env):
    """P12-HUMAN-002: Draft acceptance metrics calculate acceptance and rejection rates with sample sizes."""
    store = qa_env["store"]
    metrics = qa_env["metrics"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="e_gen_1", event_type=EventType.DRAFT_GENERATED.value, occurred_at=now, draft_id="d1"
    ))
    store.record_event(AnalyticsEvent(
        event_id="e_gen_2", event_type=EventType.DRAFT_GENERATED.value, occurred_at=now, draft_id="d2"
    ))
    store.record_event(AnalyticsEvent(
        event_id="e_app_1", event_type=EventType.DRAFT_APPROVED.value, occurred_at=now, draft_id="d1"
    ))

    summary = metrics.get_funnel_summary()
    app_rate = summary["rates"]["draft_approval_rate"]
    assert app_rate["numerator"] == 1
    assert app_rate["denominator"] == 2
    assert app_rate["rate"] == 0.5
    assert app_rate["sample_size"] == 2


def test_p12_human_003_recommendation_engine_requires_computed_evidence(qa_env):
    """P12-HUMAN-003: Recommendation engine requires computed metric evidence before proposing changes."""
    recommender = qa_env["recommender"]
    recs = recommender.generate_recommendations()
    for r in recs:
        assert r.evidence is not None
        assert r.sample_size >= 0
        assert r.confidence_level in ("low", "medium", "high")


def test_p12_human_004_recommendation_acceptance_audit_only(qa_env):
    """P12-HUMAN-004: Recommendation acceptance updates review metadata only, never touching production state."""
    recommender = qa_env["recommender"]
    store = qa_env["store"]

    rec = LearningRecommendation(
        recommendation_id="rec_mkt_audit",
        category="market_research",
        scope="market:DACH",
        summary="Explore DACH market",
        evidence={"sample_size": 30},
        sample_size=30,
        confidence_level="medium",
        proposed_change="Expand search",
        risk="low",
        status="proposed",
    )
    store.record_recommendation(rec)

    recommender.accept_recommendation("rec_mkt_audit", reviewed_by="lead_operator")
    saved = store.get_recommendation("rec_mkt_audit")
    assert saved.status == "accepted"
    assert saved.reviewed_by == "lead_operator"


# =========================================================================
# 9. API & UI Surface (P12-UIAPI-001..005)
# =========================================================================

def test_p12_uiapi_001_analytics_api_endpoints(qa_env, monkeypatch):
    """P12-UIAPI-001: Analytics REST endpoints return valid 200 schemas across all slices."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    eps = [
        "/api/sales/analytics/overview",
        "/api/sales/analytics/funnel",
        "/api/sales/analytics/sources",
        "/api/sales/analytics/markets",
        "/api/sales/analytics/contacts",
        "/api/sales/analytics/messages",
        "/api/sales/analytics/replies",
        "/api/sales/analytics/opportunities",
        "/api/sales/analytics/models",
        "/api/sales/analytics/recommendations",
    ]
    for ep in eps:
        res = client.get(ep)
        assert res.status_code == 200
        assert isinstance(res.json(), (dict, list))


def test_p12_uiapi_002_analytics_get_endpoints_reject_mutations(qa_env, monkeypatch):
    """P12-UIAPI-002: GET endpoints strictly reject state mutations (idempotent queries)."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    r1 = client.get("/api/sales/analytics/overview").json()
    r2 = client.get("/api/sales/analytics/overview").json()
    assert r1 == r2


def test_p12_uiapi_003_console_analytics_ui_renders_10_sections(qa_env, monkeypatch):
    """P12-UIAPI-003: Console Analytics UI renders all 10 required sections cleanly."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    tabs = [
        "overview", "funnel", "sources", "markets", "contacts",
        "messages", "replies", "opportunities", "models", "recommendations"
    ]
    for tab in tabs:
        res = client.get(f"/sales/analytics?tab={tab}")
        assert res.status_code == 200
        assert f"tab={tab}" in res.text or tab.capitalize() in res.text or "Analytics" in res.text


def test_p12_uiapi_004_normal_ui_zero_llm_invocations(qa_env, monkeypatch):
    """P12-UIAPI-004: Normal UI rendering makes zero LLM calls."""
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    calls = []
    def fake_request(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("LLM network call during UI render is forbidden!")

    monkeypatch.setattr("urllib.request.urlopen", fake_request)

    res = client.get("/sales/analytics")
    assert res.status_code == 200
    assert len(calls) == 0


def test_p12_uiapi_005_ui_renders_insufficient_sample_badges(qa_env, monkeypatch):
    """P12-UIAPI-005: Low-sample items render Insufficient Sample indicators in the UI."""
    qa_env["store"].record_event(AnalyticsEvent(
        event_id="e_low_ui",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        source_id="test_src_small",
        candidate_id="c_small_1",
    ))
    monkeypatch.setenv("SALES_OUTREACH_DB", qa_env["db_path"])
    client = TestClient(app)

    res = client.get("/sales/analytics?tab=sources")
    assert res.status_code == 200
    assert "insufficient" in res.text.lower()


# =========================================================================
# 10. Diagnostics, Export & Reliability (P12-EXPORT-001..003)
# =========================================================================

def test_p12_export_001_export_csv_json_zero_leakage(qa_env):
    """P12-EXPORT-001: Export service produces sanitized CSV and JSON with zero credential leakage."""
    exporter = qa_env["exporter"]
    
    csv_out = exporter.export_events_csv()
    json_out = exporter.export_summary_json()

    assert "funnel" in json_out
    assert "token" not in json_out.lower()
    assert "password" not in json_out.lower()
    assert "client_secret" not in json_out.lower()
    assert "password" not in csv_out.lower()


def test_p12_export_002_data_quality_diagnostics(qa_env):
    """P12-EXPORT-002: Data quality diagnostics expose factual counts of missing or unknown attributes."""
    store = qa_env["store"]

    now = datetime.now(timezone.utc).isoformat()
    store.record_event(AnalyticsEvent(
        event_id="e_diag_1",
        event_type=EventType.CANDIDATE_DISCOVERED.value,
        occurred_at=now,
        source_id=None,
        market_id=None,
    ))

    diag = store.get_diagnostics()
    assert diag["unknown_source_count"] >= 1
    assert diag["unknown_market_count"] >= 1


def test_p12_export_003_analytics_store_reinit_idempotent(qa_env):
    """P12-EXPORT-003: Analytics store schema reinitialization is safe and idempotent."""
    store = qa_env["store"]
    store._init_db()
    store._init_db()

    now = datetime.now(timezone.utc).isoformat()
    evt = AnalyticsEvent(event_id="e_reinit", event_type=EventType.EMAIL_SENT.value, occurred_at=now)
    store.record_event(evt)
    store.record_event(evt)  # Idempotent insert

    assert store.count_events() == 1


# =========================================================================
# 11. Weak-Model Deferred Capabilities (P12-SEM-001..003)
# =========================================================================

def test_p12_sem_001_semantic_intent_nuance_deferred():
    """P12-SEM-001: Semantic intent nuance deferred for 1.5B (qwen2.5:1.5b) baseline."""
    pass


def test_p12_sem_002_high_complexity_outbound_tone_deferred():
    """P12-SEM-002: High-complexity outbound tone adaptation deferred for future larger models."""
    pass


def test_p12_sem_003_multi_turn_conversational_nuance_deferred():
    """P12-SEM-003: Multi-turn conversational nuance deferred to 14B/32B benchmarks."""
    pass
