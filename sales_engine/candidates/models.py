# sales_engine/candidates/models.py
"""
Phase 10 Candidate Company & Market Evidence Data Models.
Extends SalesAI from job-first into multi-source company-first discovery.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class CompanyTier(str, Enum):
    tier_1 = "tier_1"
    tier_2 = "tier_2"
    tier_3 = "tier_3"
    unknown = "unknown"
    tier1 = "tier_1"
    tier2 = "tier_2"
    tier3 = "tier_3"


class BIMMaturity(str, Enum):
    unknown = "unknown"
    low = "low"
    medium = "medium"
    high = "high"
    enterprise = "enterprise"
    mature = "high"
    advanced = "enterprise"


class FitLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"

FitAssessment = FitLevel


class IntentLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"
    unknown = "unknown"

IntentAssessment = IntentLevel



class RelationshipType(str, Enum):
    service_buyer = "service_buyer"
    technology_partner = "technology_partner"
    channel_partner = "channel_partner"
    strategic_partner = "strategic_partner"
    mixed = "mixed"
    unknown = "unknown"


class OpportunityType(str, Enum):
    direct_bim_delivery = "direct_bim_delivery"
    mep_delivery = "mep_delivery"
    bim_coordination = "bim_coordination"
    overflow_capacity = "overflow_capacity"
    automation = "automation"
    revit_api = "revit_api"
    data_parameter_automation = "data_parameter_automation"
    custom_software = "custom_software"
    technology_partnership = "technology_partnership"
    channel_partnership = "channel_partnership"
    strategic_collaboration = "strategic_collaboration"
    unknown = "unknown"


class DiscoveryStatus(str, Enum):
    new = "new"
    discovered = "new"
    researching = "researching"
    qualified = "qualified"
    watchlist = "watchlist"
    rejected = "rejected"
    known_company = "known_company"
    suppressed = "suppressed"


class QualificationOutcome(str, Enum):
    reject = "reject"
    watchlist = "watchlist"
    research_more = "research_more"
    qualified_for_enrichment = "qualified_for_enrichment"
    known_company = "known_company"
    reengagement_review = "reengagement_review"
    suppressed = "suppressed"


class MarketEvidence(BaseModel):
    evidence_id: str
    candidate_id: str
    category: str  # company_profile, service, technology, standard, job, project, tender, news, historical, etc.
    signal_type: str
    text: str
    source_url: Optional[str] = None
    source_type: str = "unknown"
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_at: Optional[str] = None
    freshness_status: str = "unknown"  # fresh, recent, old, unknown
    confidence: float = 1.0
    is_current: bool = True
    raw_ref: Optional[Dict[str, Any]] = None


class DiscoveryObservation(BaseModel):
    observation_id: str
    candidate_id: str = ""
    source_id: str = "unknown"
    source_name: Optional[str] = None
    source_type: str = "unknown"
    source_url: Optional[str] = None
    source_external_id: Optional[str] = None
    discovery_mode: str = "company_first"
    observed_company_name: str = ""
    observed_domain: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    observation_type: str = "directory_result"
    observed_company_type: Optional[str] = None
    raw_title: Optional[str] = None
    raw_snippet: Optional[str] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_at: Optional[str] = None
    fetched_at: Optional[str] = None
    confidence: float = 1.0
    evidence_refs: List[str] = Field(default_factory=list)
    raw_ref: Dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any):
        if "company_id" in data and "candidate_id" not in data:
            data["candidate_id"] = data.pop("company_id")
        super().__init__(**data)

    @property
    def company_id(self) -> str:
        return self.candidate_id


class CandidateCompany(BaseModel):
    candidate_id: str = ""
    company_name: str = ""
    normalized_company_name: str = ""
    company_domain: Optional[str] = None

    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None

    company_types: List[str] = Field(default_factory=list)
    industry_tags: List[str] = Field(default_factory=list)

    legal_form: Optional[str] = None
    legal_form_raw: Optional[str] = None

    employee_size_hint: Optional[str] = None
    project_scale_hint: Optional[str] = None

    company_tier: CompanyTier = CompanyTier.unknown
    tier_confidence: float = 0.0
    tier_evidence: List[str] = Field(default_factory=list)

    bim_maturity: BIMMaturity = BIMMaturity.unknown
    bim_maturity_confidence: float = 0.0

    direct_project_access: bool = False
    direct_project_access_confidence: float = 0.0

    outsourcing_potential: str = "unknown"  # low, medium, high, unknown
    automation_potential: str = "unknown"   # low, medium, high, unknown

    relationship_type: RelationshipType = RelationshipType.unknown
    opportunity_types: List[OpportunityType] = Field(default_factory=list)

    discovery_status: DiscoveryStatus = DiscoveryStatus.new

    fit_assessment: str = "unknown"  # low, medium, high, unknown
    fit_reasons: List[str] = Field(default_factory=list)

    intent_assessment: str = "unknown"  # none, unknown, low, medium, high
    intent_signals: List[str] = Field(default_factory=list)

    source_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)

    aliases: List[str] = Field(default_factory=list)
    source_identities: List[str] = Field(default_factory=list)
    domain_history: List[str] = Field(default_factory=list)

    first_seen_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_researched_at: Optional[str] = None

    # Historical & Safety Guards
    historical_contact_status: str = "uncontacted"  # uncontacted, contacted, replied, converted, dnc
    last_contacted_at: Optional[str] = None
    reply_status: Optional[str] = None
    active_conversation: bool = False
    suppressed: bool = False
    cold_outreach_allowed: bool = True

    # Metadata & State
    metadata: Dict[str, Any] = Field(default_factory=dict)
    suppression_state: Dict[str, Any] = Field(default_factory=dict)
    previous_outreach_detected: bool = False

    def __init__(self, **data: Any):
        if "company_id" in data and "candidate_id" not in data:
            data["candidate_id"] = data.pop("company_id")
        if "name" in data and "company_name" not in data:
            data["company_name"] = data.pop("name")
        if "canonical_domain" in data and "company_domain" not in data:
            data["company_domain"] = data.pop("canonical_domain")
        if "status" in data and "discovery_status" not in data:
            data["discovery_status"] = data.pop("status")
        if "discovery_sources" in data and "source_refs" not in data:
            data["source_refs"] = data.pop("discovery_sources")
        if "fit_level" in data and "fit_assessment" not in data:
            val = data.pop("fit_level")
            data["fit_assessment"] = val.value if hasattr(val, "value") else str(val)
        if "intent_level" in data and "intent_assessment" not in data:
            val = data.pop("intent_level")
            data["intent_assessment"] = val.value if hasattr(val, "value") else str(val)
        if "normalized_company_name" not in data and "company_name" in data:
            data["normalized_company_name"] = data["company_name"].lower().strip()
        if data.get("historical_contact_status") in ("contacted", "replied", "converted") or data.get("metadata", {}).get("has_historical_outreach"):
            data["previous_outreach_detected"] = True
            data["cold_outreach_allowed"] = False
        if data.get("metadata", {}).get("active_conversation"):
            data["active_conversation"] = True
            data["cold_outreach_allowed"] = False
        super().__init__(**data)

    @property
    def company_id(self) -> str:
        return self.candidate_id

    @property
    def name(self) -> str:
        return self.company_name

    @property
    def canonical_domain(self) -> Optional[str]:
        return self.company_domain

    @property
    def status(self) -> DiscoveryStatus:
        return self.discovery_status

    @status.setter
    def status(self, val: Union[DiscoveryStatus, str]):
        if isinstance(val, str):
            val = DiscoveryStatus(val) if val in DiscoveryStatus._value2member_map_ else DiscoveryStatus.new
        self.discovery_status = val

    @property
    def fit_level(self) -> Union[FitLevel, str]:
        if self.fit_assessment in FitLevel._value2member_map_:
            return FitLevel(self.fit_assessment)
        return self.fit_assessment

    @fit_level.setter
    def fit_level(self, val: Union[FitLevel, str]):
        self.fit_assessment = val.value if hasattr(val, "value") else str(val)

    @property
    def intent_level(self) -> Union[IntentLevel, str]:
        if self.intent_assessment in IntentLevel._value2member_map_:
            return IntentLevel(self.intent_assessment)
        return self.intent_assessment

    @intent_level.setter
    def intent_level(self, val: Union[IntentLevel, str]):
        self.intent_assessment = val.value if hasattr(val, "value") else str(val)

    @property
    def discovery_sources(self) -> List[str]:
        return self.source_refs


class WatchlistEntry(BaseModel):
    candidate_id: str
    watch_reason: str
    watch_status: str = "active"
    next_check_at: Optional[str] = None
    last_checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    signals_to_monitor: List[str] = Field(default_factory=list)


class CandidateQualificationResult(BaseModel):
    candidate_id: str
    outcome: QualificationOutcome
    reasons: List[str] = Field(default_factory=list)
    company_tier: CompanyTier = CompanyTier.unknown
    bim_maturity: BIMMaturity = BIMMaturity.unknown
    fit_level: str = "unknown"
    intent_level: str = "unknown"
    relationship_type: RelationshipType = RelationshipType.unknown
    opportunity_types: List[OpportunityType] = Field(default_factory=list)
    cold_outreach_allowed: bool = True


class DiscoveryRunRecord(BaseModel):
    run_id: str
    mode: str
    source_name: str
    started_at: str
    completed_at: Optional[str] = None
    status: str = "running"
    observations_found: int = 0
    new_candidates: int = 0
    updated_candidates: int = 0
    known_candidates: int = 0
    watchlist_candidates: int = 0
    qualified_candidates: int = 0
    rejected_candidates: int = 0
    candidates_discovered: int = 0
    candidates_updated: int = 0
    observations_created: int = 0
    errors: List[str] = Field(default_factory=list)
    config_version: str = "v1"


class SourceHealthRecord(BaseModel):
    source_name: str
    status: str = "unknown"
    success_count: int = 0
    failure_count: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None

