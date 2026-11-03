# sales_engine/analytics/store.py
"""
Persistent store for Phase 12 Analytics events, model evaluation runs,
learning recommendations, and projection state.
Idempotent, non-mutating with respect to operational source-of-truth tables.
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from sales_engine.analytics.models import (
    AnalyticsEvent,
    ModelEvaluationRun,
    ModelEvaluationCase,
    LearningRecommendation,
    Experiment,
    ExperimentVariant,
    ExperimentAssignment,
)


class AnalyticsStore:
    def __init__(self, db_path: Optional[str] = ":memory:"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    company_id TEXT,
                    candidate_id TEXT,
                    lead_id TEXT,
                    contact_id TEXT,
                    draft_id TEXT,
                    draft_revision INTEGER,
                    send_attempt_id TEXT,
                    conversation_id TEXT,
                    inbound_message_id TEXT,
                    reply_suggestion_id TEXT,
                    source_id TEXT,
                    source_type TEXT,
                    market_id TEXT,
                    relationship_type TEXT DEFAULT 'unknown',
                    opportunity_types_json TEXT DEFAULT '[]',
                    company_tier TEXT,
                    bim_maturity TEXT,
                    fit_level TEXT,
                    intent_level TEXT,
                    sender_email TEXT,
                    recipient_domain TEXT,
                    model_name TEXT,
                    model_version TEXT,
                    prompt_version TEXT,
                    metadata_json TEXT,
                    source_ref TEXT,
                    provenance TEXT DEFAULT 'live_system',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_analytics_events_type ON analytics_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_occurred ON analytics_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_company ON analytics_events(company_id);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_source ON analytics_events(source_id);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_market ON analytics_events(market_id);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_model ON analytics_events(model_name);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_conv ON analytics_events(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_analytics_events_prov ON analytics_events(provenance);

                CREATE TABLE IF NOT EXISTS analytics_projection_state (
                    projection_key TEXT PRIMARY KEY,
                    last_projected_event_id TEXT,
                    last_projection_at TEXT,
                    projection_version TEXT,
                    total_events INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS model_evaluation_runs (
                    evaluation_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    quantization TEXT,
                    hardware_label TEXT NOT NULL,
                    task TEXT NOT NULL,
                    fixture_set_version TEXT NOT NULL,
                    cases INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    review INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    deferred INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    tokens_per_second_optional REAL,
                    peak_ram_optional REAL,
                    peak_vram_optional REAL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_evaluation_cases (
                    case_id TEXT PRIMARY KEY,
                    evaluation_id TEXT NOT NULL,
                    task TEXT NOT NULL,
                    fixture_id TEXT NOT NULL,
                    schema_valid INTEGER NOT NULL,
                    grounding_valid INTEGER NOT NULL,
                    fabrication_count INTEGER NOT NULL,
                    human_acceptance TEXT NOT NULL,
                    edit_level TEXT NOT NULL,
                    semantic_review TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(evaluation_id) REFERENCES model_evaluation_runs(evaluation_id)
                );

                CREATE INDEX IF NOT EXISTS idx_model_eval_cases_run ON model_evaluation_cases(evaluation_id);

                CREATE TABLE IF NOT EXISTS learning_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    sample_size INTEGER NOT NULL,
                    confidence_level TEXT NOT NULL,
                    proposed_change TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS experiment_variants (
                    variant_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    traffic_allocation REAL NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );

                CREATE TABLE IF NOT EXISTS experiment_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL
                );
            """)

    # -------------------------------------------------------------
    # Event Operations
    # -------------------------------------------------------------
    def record_event(self, event: AnalyticsEvent) -> bool:
        """
        Idempotently inserts an analytics event.
        Returns True if inserted, False if already existed.
        """
        opp_types = json.dumps(event.opportunity_types) if isinstance(event.opportunity_types, list) else "[]"
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO analytics_events (
                    event_id, event_type, occurred_at,
                    company_id, candidate_id, lead_id, contact_id,
                    draft_id, draft_revision, send_attempt_id,
                    conversation_id, inbound_message_id, reply_suggestion_id,
                    source_id, source_type, market_id,
                    relationship_type, opportunity_types_json,
                    company_tier, bim_maturity, fit_level, intent_level,
                    sender_email, recipient_domain,
                    model_name, model_version, prompt_version,
                    metadata_json, source_ref, provenance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.occurred_at,
                    event.company_id,
                    event.candidate_id,
                    event.lead_id,
                    event.contact_id,
                    event.draft_id,
                    event.draft_revision,
                    event.send_attempt_id,
                    event.conversation_id,
                    event.inbound_message_id,
                    event.reply_suggestion_id,
                    event.source_id,
                    event.source_type,
                    event.market_id,
                    event.relationship_type,
                    opp_types,
                    event.company_tier,
                    event.bim_maturity,
                    event.fit_level,
                    event.intent_level,
                    event.sender_email,
                    event.recipient_domain,
                    event.model_name,
                    event.model_version,
                    event.prompt_version,
                    event.metadata_json,
                    event.source_ref,
                    event.provenance,
                    event.created_at,
                ),
            )
            return cur.rowcount > 0

    def record_events_batch(self, events: List[AnalyticsEvent]) -> int:
        """Records a batch of events idempotently; returns count of newly inserted events."""
        inserted = 0
        with self._get_connection() as conn:
            for event in events:
                opp_types = json.dumps(event.opportunity_types) if isinstance(event.opportunity_types, list) else "[]"
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO analytics_events (
                        event_id, event_type, occurred_at,
                        company_id, candidate_id, lead_id, contact_id,
                        draft_id, draft_revision, send_attempt_id,
                        conversation_id, inbound_message_id, reply_suggestion_id,
                        source_id, source_type, market_id,
                        relationship_type, opportunity_types_json,
                        company_tier, bim_maturity, fit_level, intent_level,
                        sender_email, recipient_domain,
                        model_name, model_version, prompt_version,
                        metadata_json, source_ref, provenance, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.occurred_at,
                        event.company_id,
                        event.candidate_id,
                        event.lead_id,
                        event.contact_id,
                        event.draft_id,
                        event.draft_revision,
                        event.send_attempt_id,
                        event.conversation_id,
                        event.inbound_message_id,
                        event.reply_suggestion_id,
                        event.source_id,
                        event.source_type,
                        event.market_id,
                        event.relationship_type,
                        opp_types,
                        event.company_tier,
                        event.bim_maturity,
                        event.fit_level,
                        event.intent_level,
                        event.sender_email,
                        event.recipient_domain,
                        event.model_name,
                        event.model_version,
                        event.prompt_version,
                        event.metadata_json,
                        event.source_ref,
                        event.provenance,
                        event.created_at,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
        return inserted

    def get_event(self, event_id: str) -> Optional[AnalyticsEvent]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM analytics_events WHERE event_id = ?", (event_id,)).fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def list_events(
        self,
        event_type: Optional[str] = None,
        source_id: Optional[str] = None,
        market_id: Optional[str] = None,
        relationship_type: Optional[str] = None,
        provenance: Optional[str] = None,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[AnalyticsEvent]:
        query = "SELECT * FROM analytics_events WHERE 1=1"
        params: List[Any] = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if relationship_type:
            query += " AND relationship_type = ?"
            params.append(relationship_type)
        if provenance:
            query += " AND provenance = ?"
            params.append(provenance)
        if since_iso:
            query += " AND occurred_at >= ?"
            params.append(since_iso)
        if until_iso:
            query += " AND occurred_at <= ?"
            params.append(until_iso)

        query += " ORDER BY occurred_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(r) for r in rows]

    def count_events(self, event_type: Optional[str] = None, since_iso: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) FROM analytics_events WHERE 1=1"
        params: List[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if since_iso:
            query += " AND occurred_at >= ?"
            params.append(since_iso)
        with self._get_connection() as conn:
            return conn.execute(query, params).fetchone()[0]

    def clear_events(self):
        """Wipes events table (for testing or full projection rebuild)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM analytics_events")

    # -------------------------------------------------------------
    # Projection State
    # -------------------------------------------------------------
    def get_projection_state(self, key: str = "default") -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM analytics_projection_state WHERE projection_key = ?", (key,)).fetchone()
            if not row:
                return None
            return dict(row)

    def update_projection_state(
        self,
        key: str = "default",
        last_event_id: Optional[str] = None,
        version: str = "1.0",
        total_events: int = 0,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO analytics_projection_state (projection_key, last_projected_event_id, last_projection_at, projection_version, total_events)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(projection_key) DO UPDATE SET
                    last_projected_event_id = excluded.last_projected_event_id,
                    last_projection_at = excluded.last_projection_at,
                    projection_version = excluded.projection_version,
                    total_events = excluded.total_events
                """,
                (key, last_event_id, now, version, total_events),
            )

    # -------------------------------------------------------------
    # Model Evaluation Runs & Cases
    # -------------------------------------------------------------
    def record_evaluation_run(self, run: ModelEvaluationRun) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO model_evaluation_runs (
                    evaluation_id, model_name, model_version, quantization,
                    hardware_label, task, fixture_set_version,
                    cases, passed, review, failed, deferred,
                    latency_ms, tokens_per_second_optional, peak_ram_optional, peak_vram_optional,
                    notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.evaluation_id,
                    run.model_name,
                    run.model_version,
                    run.quantization,
                    run.hardware_label,
                    run.task,
                    run.fixture_set_version,
                    run.cases,
                    run.passed,
                    run.review,
                    run.failed,
                    run.deferred,
                    run.latency_ms,
                    run.tokens_per_second_optional,
                    run.peak_ram_optional,
                    run.peak_vram_optional,
                    run.notes,
                    run.created_at,
                ),
            )

    def record_evaluation_case(self, case: ModelEvaluationCase) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO model_evaluation_cases (
                    case_id, evaluation_id, task, fixture_id,
                    schema_valid, grounding_valid, fabrication_count,
                    human_acceptance, edit_level, semantic_review,
                    latency_ms, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.evaluation_id,
                    case.task,
                    case.fixture_id,
                    1 if case.schema_valid else 0,
                    1 if case.grounding_valid else 0,
                    case.fabrication_count,
                    case.human_acceptance,
                    case.edit_level,
                    case.semantic_review,
                    case.latency_ms,
                    case.error_message,
                    case.created_at,
                ),
            )

    def list_evaluation_runs(
        self,
        model_name: Optional[str] = None,
        task: Optional[str] = None,
        hardware_label: Optional[str] = None,
    ) -> List[ModelEvaluationRun]:
        query = "SELECT * FROM model_evaluation_runs WHERE 1=1"
        params: List[Any] = []
        if model_name:
            query += " AND model_name = ?"
            params.append(model_name)
        if task:
            query += " AND task = ?"
            params.append(task)
        if hardware_label:
            query += " AND hardware_label = ?"
            params.append(hardware_label)
        query += " ORDER BY created_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_run(r) for r in rows]

    def get_evaluation_cases(self, evaluation_id: str) -> List[ModelEvaluationCase]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM model_evaluation_cases WHERE evaluation_id = ? ORDER BY created_at ASC",
                (evaluation_id,),
            ).fetchall()
            return [self._row_to_case(r) for r in rows]

    # -------------------------------------------------------------
    # Learning Recommendations
    # -------------------------------------------------------------
    def record_recommendation(self, rec: LearningRecommendation) -> bool:
        evidence_str = json.dumps(rec.evidence) if isinstance(rec.evidence, (dict, list)) else str(rec.evidence)
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO learning_recommendations (
                    recommendation_id, category, scope, summary,
                    evidence, sample_size, confidence_level, proposed_change,
                    risk, status, reviewed_by, reviewed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.recommendation_id,
                    rec.category,
                    rec.scope,
                    rec.summary,
                    evidence_str,
                    rec.sample_size,
                    rec.confidence_level,
                    rec.proposed_change,
                    rec.risk,
                    rec.status,
                    rec.reviewed_by,
                    rec.reviewed_at,
                    rec.created_at,
                ),
            )
            return cur.rowcount > 0

    def update_recommendation_status(
        self,
        recommendation_id: str,
        status: str,
        reviewed_by: Optional[str] = None,
    ) -> bool:
        """
        Updates human decision record for recommendation.
        Strict invariant: does NOT alter operational production config.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE learning_recommendations
                SET status = ?, reviewed_by = ?, reviewed_at = ?
                WHERE recommendation_id = ?
                """,
                (status, reviewed_by or "operator", now, recommendation_id),
            )
            return cur.rowcount > 0

    def list_recommendations(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[LearningRecommendation]:
        query = "SELECT * FROM learning_recommendations WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_rec(r) for r in rows]

    def get_recommendation(self, rec_id: str) -> Optional[LearningRecommendation]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM learning_recommendations WHERE recommendation_id = ?", (rec_id,)).fetchone()
            if not row:
                return None
            return self._row_to_rec(row)

    # -------------------------------------------------------------
    # Diagnostics & Quality Counts
    # -------------------------------------------------------------
    def get_diagnostics(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0]
            unknown_market = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE market_id IS NULL OR market_id = '' OR market_id = 'unknown'").fetchone()[0]
            unknown_source = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE source_id IS NULL OR source_id = '' OR source_id = 'unknown'").fetchone()[0]
            unlinked_reply = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type = 'reply_received' AND (company_id IS NULL OR company_id = '')").fetchone()[0]
            missing_contact = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type = 'contact_selected' AND (contact_id IS NULL OR contact_id = '')").fetchone()[0]
            missing_model = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type = 'draft_generated' AND (model_name IS NULL OR model_name = '')").fetchone()[0]
            historical = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE provenance = 'historical_import'").fetchone()[0]
            live = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE provenance = 'live_system'").fetchone()[0]

            return {
                "total_events": total,
                "unknown_market_count": unknown_market,
                "unknown_source_count": unknown_source,
                "unlinked_reply_count": unlinked_reply,
                "missing_contact_count": missing_contact,
                "missing_model_metadata_count": missing_model,
                "historical_provenance_count": historical,
                "live_provenance_count": live,
            }

    # -------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------
    def _row_to_event(self, r: sqlite3.Row) -> AnalyticsEvent:
        opp_types = []
        if r["opportunity_types_json"]:
            try:
                opp_types = json.loads(r["opportunity_types_json"])
            except Exception:
                opp_types = []

        return AnalyticsEvent(
            event_id=r["event_id"],
            event_type=r["event_type"],
            occurred_at=r["occurred_at"],
            company_id=r["company_id"],
            candidate_id=r["candidate_id"],
            lead_id=r["lead_id"],
            contact_id=r["contact_id"],
            draft_id=r["draft_id"],
            draft_revision=r["draft_revision"],
            send_attempt_id=r["send_attempt_id"],
            conversation_id=r["conversation_id"],
            inbound_message_id=r["inbound_message_id"],
            reply_suggestion_id=r["reply_suggestion_id"],
            source_id=r["source_id"],
            source_type=r["source_type"],
            market_id=r["market_id"],
            relationship_type=r["relationship_type"] or "unknown",
            opportunity_types=opp_types,
            company_tier=r["company_tier"],
            bim_maturity=r["bim_maturity"],
            fit_level=r["fit_level"],
            intent_level=r["intent_level"],
            sender_email=r["sender_email"],
            recipient_domain=r["recipient_domain"],
            model_name=r["model_name"],
            model_version=r["model_version"],
            prompt_version=r["prompt_version"],
            metadata_json=r["metadata_json"],
            source_ref=r["source_ref"],
            provenance=r["provenance"] or "live_system",
            created_at=r["created_at"],
        )

    def _row_to_run(self, r: sqlite3.Row) -> ModelEvaluationRun:
        return ModelEvaluationRun(
            evaluation_id=r["evaluation_id"],
            model_name=r["model_name"],
            model_version=r["model_version"],
            quantization=r["quantization"],
            hardware_label=r["hardware_label"],
            task=r["task"],
            fixture_set_version=r["fixture_set_version"],
            cases=r["cases"],
            passed=r["passed"],
            review=r["review"],
            failed=r["failed"],
            deferred=r["deferred"],
            latency_ms=r["latency_ms"],
            tokens_per_second_optional=r["tokens_per_second_optional"],
            peak_ram_optional=r["peak_ram_optional"],
            peak_vram_optional=r["peak_vram_optional"],
            notes=r["notes"],
            created_at=r["created_at"],
        )

    def _row_to_case(self, r: sqlite3.Row) -> ModelEvaluationCase:
        return ModelEvaluationCase(
            case_id=r["case_id"],
            evaluation_id=r["evaluation_id"],
            task=r["task"],
            fixture_id=r["fixture_id"],
            schema_valid=bool(r["schema_valid"]),
            grounding_valid=bool(r["grounding_valid"]),
            fabrication_count=r["fabrication_count"],
            human_acceptance=r["human_acceptance"],
            edit_level=r["edit_level"],
            semantic_review=r["semantic_review"],
            latency_ms=r["latency_ms"],
            error_message=r["error_message"],
            created_at=r["created_at"],
        )

    def _row_to_rec(self, r: sqlite3.Row) -> LearningRecommendation:
        ev = r["evidence"]
        try:
            ev = json.loads(ev)
        except Exception:
            pass
        return LearningRecommendation(
            recommendation_id=r["recommendation_id"],
            category=r["category"],
            scope=r["scope"],
            summary=r["summary"],
            evidence=ev,
            sample_size=r["sample_size"],
            confidence_level=r["confidence_level"],
            proposed_change=r["proposed_change"],
            risk=r["risk"],
            status=r["status"],
            reviewed_by=r["reviewed_by"],
            reviewed_at=r["reviewed_at"],
            created_at=r["created_at"],
        )
