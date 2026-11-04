"""
View models and request contracts for Phase 10.5 Local Sales Operator Console.
Strictly non-autonomous: contracts only define UI projection and validated operator actions.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DashboardSummary(BaseModel):
    new_candidates: int = 0
    needs_research: int = 0
    watchlist: int = 0
    qualified_leads: int = 0
    contacts_found: int = 0
    usable_contacts: int = 0
    drafts_pending_review: int = 0
    approved_not_sent: int = 0
    sent_today: int = 0
    sent_this_week: int = 0
    suppressed: int = 0
    historical_known_companies: int = 0
    # Phase 11 placeholders
    historical_positive_replies: Optional[int] = None
    historical_meetings: Optional[int] = None
    inbox_status: str = "not_connected"


class CompanyTimelineEvent(BaseModel):
    event_id: str
    candidate_id: str
    company_name: str
    event_type: str  # discovered, researched, signal_found, qualified, watchlisted, contact_found, draft_created, draft_edited, approved, rejected, suppressed, dry_run, sent, historical_reply, meeting, manual_note
    occurred_at: str
    title: str
    summary: str
    source_ref: Optional[str] = None
    entity_ref: Optional[str] = None
    revision: Optional[int] = None
    actor: Optional[str] = "system"
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


class CandidateListItem(BaseModel):
    candidate_id: str
    company_name: str
    domain: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    company_type: Optional[str] = None
    tier: Optional[str] = None
    fit: Optional[str] = None
    intent: Optional[str] = None
    bim_maturity: str = "Unknown"
    outsourcing_potential: Optional[str] = None
    automation_potential: Optional[str] = None
    discovery_sources: List[str] = Field(default_factory=list)
    last_signal: Optional[str] = None
    status: str
    is_previously_contacted: bool = False
    is_suppressed: bool = False
    has_research_snapshot: bool = False
    snapshot_id: Optional[str] = None
    contact_count: int = 0
    verified_contact_count: int = 0
    # Lifecycle Work Queue fields
    lifecycle_queue: str = "active"  # active, new, needs_research, needs_verification, needs_correction, historical_pending, all
    actionable_next_step: Optional[str] = None
    recommended_action_url: Optional[str] = None


class CandidateDetailView(BaseModel):
    candidate: CandidateListItem
    legal_form: Optional[str] = None
    employee_size_hint: Optional[str] = None
    project_scale_hint: Optional[str] = None
    tier_confidence: Optional[float] = None
    bim_maturity_confidence: Optional[float] = None
    relationship_type: Optional[str] = None
    opportunity_types: List[str] = Field(default_factory=list)
    fit_reasons: List[str] = Field(default_factory=list)
    intent_signals: List[str] = Field(default_factory=list)
    why_found: str = ""
    why_fit: str = ""
    why_intent: str = ""
    why_status: str = ""
    evidence_items: List[Dict[str, Any]] = Field(default_factory=list)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    historical_outreach: List[Dict[str, Any]] = Field(default_factory=list)
    contacts: List[Dict[str, Any]] = Field(default_factory=list)
    drafts: List[Dict[str, Any]] = Field(default_factory=list)
    timeline: List[CompanyTimelineEvent] = Field(default_factory=list)
    can_research: bool = True
    can_qualify: bool = True
    can_watch: bool = True
    can_suppress: bool = True


class DraftReviewCard(BaseModel):
    draft_id: str
    revision: int
    lead_id: str
    contact_id: str
    company_name: str
    company_domain: Optional[str] = None
    recipient_name: str
    recipient_title: Optional[str] = None
    recipient_email: str
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    subject: str
    body: str
    why_this_company: str = ""
    service_used: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    generation_model: Optional[str] = None
    language: str = "en"
    tone: str = "professional_concise"
    approval_status: str
    send_status: str
    outreach_status: str
    content_hash: str
    approved_content_hash: Optional[str] = None
    approved_fingerprint_short: Optional[str] = None
    reviewer: Optional[str] = None
    review_note: Optional[str] = None
    created_at: str
    approved_at: Optional[str] = None
    sent_at: Optional[str] = None
    is_suppressed: bool = False
    is_previously_contacted: bool = False
    can_approve: bool = False
    can_edit: bool = False
    can_reject: bool = False
    can_request_changes: bool = False
    can_dry_run: bool = False
    can_send: bool = False
    can_suppress: bool = True
    send_block_reason: Optional[str] = None


class SentHistoryItem(BaseModel):
    attempt_id: str
    send_key: str
    draft_id: str
    revision: int
    company_name: str
    recipient_name: str
    recipient_email: str
    sender_email: str
    subject: str
    provider: str
    status: str
    provider_message_id: Optional[str] = None
    attempted_at: str
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class WatchlistItemView(BaseModel):
    candidate_id: str
    company_name: str
    company_domain: Optional[str] = None
    country: Optional[str] = None
    tier: Optional[str] = None
    fit: Optional[str] = None
    intent: Optional[str] = None
    watch_reason: str
    watch_status: str
    signals_to_monitor: List[str] = Field(default_factory=list)
    last_checked_at: Optional[str] = None
    next_check_at: Optional[str] = None


class SuppressionItemView(BaseModel):
    suppression_id: str
    email: str
    company_domain: Optional[str] = None
    reason: str
    source: str
    created_at: str


class SourceItemView(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool
    discovery_mode: str
    priority: int
    countries: List[str] = Field(default_factory=list)
    requires_api_key: bool = False
    is_configured: bool = True
    config_error: bool = False
    credential_status: str = "not_required"
    credential_env_var: Optional[str] = None
    adapter_id: Optional[str] = None
    adapter_status: str = "AVAILABLE"
    max_results_per_run: int = 50
    description: str = ""
    operator_instructions: str = ""
    notes: str = ""
    base_url: Optional[str] = None
    status: str = "operational"
    success_count: int = 0
    failure_count: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None


class DiscoveryRunItemView(BaseModel):
    run_id: str
    mode: str
    source_name: str
    started_at: str
    completed_at: Optional[str] = None
    status: str
    observations_found: int = 0
    new_candidates: int = 0
    known_candidates: int = 0
    watchlist_candidates: int = 0
    qualified_candidates: int = 0
    rejected_candidates: int = 0
    errors: List[str] = Field(default_factory=list)


# Mutation Requests from Console Forms
class RunDiscoveryUiRequest(BaseModel):
    sources: Optional[List[str]] = None
    query: str = "BIM"
    discovery_mode: str = "company_first"


class ManualSeedUiRequest(BaseModel):
    company_name: str
    domain: Optional[str] = None
    country: Optional[str] = None
    source_url: Optional[str] = None


class DraftEditUiRequest(BaseModel):
    revision: int
    subject: str
    body: str
    recipient_email: Optional[str] = None
    reviewer: str = "local_operator"
    note: Optional[str] = None


class DraftApproveUiRequest(BaseModel):
    revision: int
    reviewer: str = "local_operator"
    note: Optional[str] = None


class DraftRejectUiRequest(BaseModel):
    revision: int
    reviewer: str = "local_operator"
    note: Optional[str] = None


class DraftSendUiRequest(BaseModel):
    revision: int
    confirm_send: bool = False
    reviewer: str = "local_operator"


class CandidateWatchUiRequest(BaseModel):
    reason: str
    signals: Optional[List[str]] = None


class CandidateQualifyUiRequest(BaseModel):
    reviewer: str = "local_operator"
    note: Optional[str] = None


class SuppressionAddUiRequest(BaseModel):
    email: str
    company_domain: Optional[str] = None
    reason: str = "manual"
    source: str = "sales_console"


class HistoricalImportPreviewResult(BaseModel):
    file_path: str
    file_hash: str
    sheet_name: Optional[str] = None
    preview_token: str
    rows_detected: int = 0
    companies_detected: int = 0
    contacts_detected: int = 0
    historical_outreach_events: int = 0
    dnc_count: int = 0
    active_conversations: int = 0
    sheets: List[Dict[str, Any]] = Field(default_factory=list)
    column_mappings: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    # MVP-01.3 / MVP-01.4 Acceptance & Readiness breakdown fields
    readiness_status: str = "READY"  # READY, REVIEW_REQUIRED, BLOCKED
    readiness_reasons: List[str] = Field(default_factory=list)
    distinct_canonical_domains: int = 0
    domain_collisions_count: int = 0
    domain_collisions: List[Dict[str, Any]] = Field(default_factory=list)
    status_distribution: Dict[str, int] = Field(default_factory=dict)
    classification_counts: Dict[str, int] = Field(default_factory=dict)
    populated_contact_name_count: int = 0
    missing_domain_count: int = 0
    missing_email_count: int = 0
    malformed_domain_count: int = 0
    unmapped_columns_count: int = 0
    # MVP-01.4.1 Multi-link website intelligence metrics
    valid_website_rows_count: int = 0
    multiline_cells_count: int = 0
    total_extracted_links: int = 0
    placeholders_count: int = 0
    multiple_plausible_domains_count: int = 0
    ambiguous_rows: List[Dict[str, Any]] = Field(default_factory=list)
    missing_domain_rows: List[Dict[str, Any]] = Field(default_factory=list)
    # Reimport & Sent_Emails Reconciliation metrics
    has_sent_emails_sheet: bool = False
    sent_emails_total: int = 0
    sent_emails_matched: int = 0
    sent_emails_unmatched: int = 0
    sent_emails_already_imported: int = 0
    sent_emails_new: int = 0
    new_companies_count: int = 0
    existing_companies_count: int = 0
    company_changes_count: int = 0
    reviewed_records_affected: int = 0
    # HV-02.5 Lead Research 13-column workbook preview fields
    is_lead_research: bool = False
    new_candidate_proposals: int = 0
    existing_candidate_matches: int = 0
    ambiguous_unresolved_count: int = 0
    excluded_dnc_count: int = 0
    skipped_invalid_count: int = 0
    proposed_research_updates: int = 0


class DraftRequestChangesUiRequest(BaseModel):
    revision: int
    reviewer: str = "local_operator"
    note: str


class GenerateDraftUiRequest(BaseModel):
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    contact_email: str
    reviewer: str = "local_operator"


class SearchProfileItemView(BaseModel):
    scope_id: str
    scope_name: str
    enabled: bool
    discovery_modes: List[str] = Field(default_factory=list)
    markets: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    source_count: int = 0
    max_results: int = 50
    relationship_types: List[str] = Field(default_factory=list)
    opportunity_types: List[str] = Field(default_factory=list)
    company_tiers: List[str] = Field(default_factory=list)
    bim_maturity: List[str] = Field(default_factory=list)
    company_name_keywords: List[str] = Field(default_factory=list)
    required_signals: List[str] = Field(default_factory=list)
    excluded_keywords: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_run_time: Optional[str] = None
    last_run_status: Optional[str] = None
    last_results_count: Optional[int] = None
    last_run_id: Optional[str] = None


class DiscoveryControlRunView(BaseModel):
    run_id: str
    scope_id: Optional[str] = None
    scope_name: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    duration_str: Optional[str] = None
    status: str
    markets: List[str] = Field(default_factory=list)
    discovery_modes: List[str] = Field(default_factory=list)
    sources_requested: List[str] = Field(default_factory=list)
    sources_executed: List[str] = Field(default_factory=list)
    skipped_sources: List[Dict[str, Any]] = Field(default_factory=list)
    results_found: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    errors: List[str] = Field(default_factory=list)


