# sales_engine/analytics/models.py
"""
Data models and canonical schemas for SalesAI Phase 12 Analytics & Learning Loop.
Strictly decoupled from operational authority.
Projections only: no autonomous mutation of Phase 1-11 state.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Union
import json
import uuid


# Statistical / Small-Sample Safety Thresholds
MIN_SAMPLE_FOR_COMPARISON: int = 10
MIN_SAMPLE_FOR_RECOMMENDATION: int = 20


class EventType(str, Enum):
    CANDIDATE_DISCOVERED = "candidate_discovered"
    CANDIDATE_QUALIFIED = "candidate_qualified"
    CANDIDATE_WATCHLISTED = "candidate_watchlisted"
    CANDIDATE_REJECTED = "candidate_rejected"
    CONTACT_SELECTED = "contact_selected"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_REJECTED = "draft_rejected"
    DRAFT_CHANGED = "draft_changed"
    EMAIL_SENT = "email_sent"
    EMAIL_FAILED = "email_failed"
    EMAIL_BOUNCED = "email_bounced"
    REPLY_RECEIVED = "reply_received"
    REPLY_CLASSIFIED = "reply_classified"
    POSITIVE_INTEREST = "positive_interest"
    MEETING_PROPOSED = "meeting_proposed"
    MEETING_SCHEDULED = "meeting_scheduled"
    MEETING_COMPLETED = "meeting_completed"
    MEETING_CANCELLED = "meeting_cancelled"
    OPPORTUNITY_QUALIFIED = "opportunity_qualified"
    OPPORTUNITY_CLOSED_WON = "opportunity_closed_won"
    OPPORTUNITY_CLOSED_LOST = "opportunity_closed_lost"
    SUPPRESSED = "suppressed"
    UNSUBSCRIBED = "unsubscribed"


class RelationshipType(str, Enum):
    SERVICE_BUYER = "service_buyer"
    TECHNOLOGY_PARTNER = "technology_partner"
    CHANNEL_PARTNER = "channel_partner"
    STRATEGIC_PARTNER = "strategic_partner"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class OpportunityType(str, Enum):
    DIRECT_BIM_DELIVERY = "direct_bim_delivery"
    MEP_DELIVERY = "mep_delivery"
    BIM_COORDINATION = "bim_coordination"
    OVERFLOW_CAPACITY = "overflow_capacity"
    AUTOMATION = "automation"
    REVIT_API = "revit_api"
    DATA_PARAMETER_AUTOMATION = "data_parameter_automation"
    CUSTOM_SOFTWARE = "custom_software"
    TECHNOLOGY_PARTNERSHIP = "technology_partnership"
    CHANNEL_PARTNERSHIP = "channel_partnership"
    STRATEGIC_COLLABORATION = "strategic_collaboration"
    UNKNOWN = "unknown"


class HumanEditLevel(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    REWRITE = "rewrite"
    REJECTED = "rejected"


class RecommendationStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    IMPLEMENTED_MANUALLY = "implemented_manually"


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvaluationTask(str, Enum):
    WEBSITE_EXTRACTION = "website_extraction"
    COMPANY_CLASSIFICATION = "company_classification"
    EMAIL_DRAFT_GENERATION = "email_draft_generation"
    REPLY_CLASSIFICATION = "reply_classification"
    REPLY_SUGGESTION = "reply_suggestion"
    MEETING_TIME_EXTRACTION = "meeting_time_extraction"


@dataclass
class AnalyticsEvent:
    event_id: str
    event_type: str
    occurred_at: str  # ISO8601 UTC

    company_id: Optional[str] = None
    candidate_id: Optional[str] = None
    lead_id: Optional[str] = None
    contact_id: Optional[str] = None

    draft_id: Optional[str] = None
    draft_revision: Optional[int] = None
    send_attempt_id: Optional[str] = None

    conversation_id: Optional[str] = None
    inbound_message_id: Optional[str] = None
    reply_suggestion_id: Optional[str] = None

    source_id: Optional[str] = None
    source_type: Optional[str] = None
    market_id: Optional[str] = None

    relationship_type: str = "unknown"
    opportunity_types: List[str] = field(default_factory=list)

    company_tier: Optional[str] = None
    bim_maturity: Optional[str] = None
    fit_level: Optional[str] = None
    intent_level: Optional[str] = None

    sender_email: Optional[str] = None
    recipient_domain: Optional[str] = None

    model_name: Optional[str] = None
    model_version: Optional[str] = None
    prompt_version: Optional[str] = None

    metadata_json: Optional[str] = None
    source_ref: Optional[str] = None
    provenance: str = "live_system"  # 'live_system' or 'historical_import'
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ModelEvaluationCase:
    case_id: str
    evaluation_id: str
    task: str
    fixture_id: str
    schema_valid: bool
    grounding_valid: bool
    fabrication_count: int
    human_acceptance: str  # approved_unchanged, minor_edit, major_edit, rewrite, rejected
    edit_level: str  # none, minor, major, rewrite, rejected
    semantic_review: str  # pass, review, fail, deferred
    latency_ms: float
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelEvaluationRun:
    evaluation_id: str
    model_name: str
    model_version: str
    quantization: Optional[str]
    hardware_label: str  # e.g., 'current_weak_machine', 'workstation_4070', 'future_5080'
    task: str
    fixture_set_version: str  # e.g. 'phase12-v1'

    cases: int = 0
    passed: int = 0
    review: int = 0
    failed: int = 0
    deferred: int = 0

    latency_ms: float = 0.0
    tokens_per_second_optional: Optional[float] = None
    peak_ram_optional: Optional[float] = None
    peak_vram_optional: Optional[float] = None

    notes: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LearningRecommendation:
    recommendation_id: str
    category: str
    scope: str
    summary: str
    evidence: Union[Dict[str, Any], str]
    sample_size: int
    confidence_level: str  # low, medium, high
    proposed_change: str
    risk: str  # low, medium, high
    status: str = "proposed"  # proposed, accepted, rejected, deferred, implemented_manually
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if isinstance(d["evidence"], dict):
            d["evidence"] = json.dumps(d["evidence"])
        return d


@dataclass
class ExperimentVariant:
    variant_id: str
    experiment_id: str
    name: str
    description: str
    traffic_allocation: float  # 0.0 to 1.0


@dataclass
class Experiment:
    experiment_id: str
    name: str
    category: str  # tone, subject, model, prompt_version
    variants: List[ExperimentVariant] = field(default_factory=list)
    active: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ExperimentAssignment:
    assignment_id: str
    experiment_id: str
    variant_id: str
    entity_id: str
    assigned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class MetricRate:
    numerator: int
    denominator: int
    rate: float
    sample_size: int
    sample_status: str  # 'valid' or 'insufficient_sample'
    is_statistically_valid: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
