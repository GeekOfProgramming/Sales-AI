"""
Candidate Lifecycle Projection Service
Phase A: Human-Verified CRM Candidate Work Queue Lifecycle

Strict Invariants:
1. Separation of data dimensions:
   - Discovery status (new, researching, qualified, watchlist, rejected, known_company, suppressed)
   - ICP qualification (is_candidate_qualified_lead)
   - Research verification (OPERATOR_REVIEWED, EXTRACTED, Unverified, snapshot presence)
   - Historical communication evidence (contacted, replied, historical_outreach_events, sent_emails)
   - Operator lifecycle decision (APPROVED, REJECTED, DUPLICATE, NEEDS_RESEARCH, REOPENED, HISTORICAL_FINALIZED, NOT_REVIEWED)
   - Suppression / DNC (suppressed, DNC)
2. Authoritative queue categorization:
   - "active": Unfinished, non-qualified, uncontacted candidates, plus explicitly REOPENED candidates.
   - "new": Newly ingested, untouched candidates awaiting initial operator triage.
   - "needs_research": Candidates requiring company and website research.
   - "needs_verification": Candidates with research snapshots or lead register records awaiting verification.
   - "needs_correction": Candidates explicitly reopened or marked for correction.
   - "historical_pending": Candidates with historical communication awaiting operator reconciliation/finalization.
   - "all": Complete registry of all companies (archived and active).
   - "qualified": ICP-qualified candidates (live in Qualified Leads / Leads pipeline).
   - "archived_historical": Historical candidates finalized by operator.
   - "suppressed_rejected": Suppressed, rejected, duplicate, or watchlisted candidates.
3. Stable candidate IDs and deep links:
   - No records deleted, cloned, or recreated.
   - Historical suppression preserved regardless of queue.
"""

from enum import Enum
from typing import Dict, Any, Optional, Set, List, Tuple
from dataclasses import dataclass


class CandidateQueueType(str, Enum):
    ACTIVE = "active"
    NEW = "new"
    NEEDS_RESEARCH = "needs_research"
    NEEDS_VERIFICATION = "needs_verification"
    NEEDS_CORRECTION = "needs_correction"
    HISTORICAL_PENDING = "historical_pending"
    ALL = "all"
    QUALIFIED = "qualified"
    ARCHIVED_HISTORICAL = "archived_historical"
    SUPPRESSED_REJECTED = "suppressed_rejected"


@dataclass
class CandidateLifecycleState:
    candidate_id: str
    company_name: str
    primary_queue: CandidateQueueType
    active_subqueue: Optional[CandidateQueueType]
    is_active_work: bool
    is_qualified_lead: bool
    is_historical_pending: bool
    is_historical_finalized: bool
    is_suppressed_or_rejected: bool
    is_reopened: bool
    actionable_next_step: str
    recommended_action_url: str


def project_candidate_lifecycle(
    cand: Any,
    has_snapshot: bool = False,
    lead_register_status: Optional[str] = None,
    is_suppressed_domain: bool = False,
) -> CandidateLifecycleState:
    """
    Computes the authoritative lifecycle projection for a single candidate company.
    Enforces strict precedence:
    1. Reopened / Needs Correction -> enters Active Work directly.
    2. Suppressed / Rejected / Watchlist / Duplicate -> separated from default work queues.
    3. Finalized Historical -> excluded from default Candidates list; visible in All Companies / Sent History.
    4. Historical Review Pending -> historical contact awaiting operator review/finalization.
    5. Qualified Lead -> moves to Qualified Leads pipeline.
    6. Active Work (subqueues: Needs Verification, Needs Research, New).
    """
    cid = getattr(cand, "candidate_id", None) or getattr(cand, "company_id", None) or ""
    dom = getattr(cand, "company_domain", None) or getattr(cand, "domain", None) or ""
    cname = getattr(cand, "company_name", None) or getattr(cand, "name", "") or "Unnamed Company"
    
    # 1. Extract raw dimensional signals
    disc_status = str(getattr(cand, "discovery_status", "") or "").lower()
    if hasattr(getattr(cand, "discovery_status", None), "value"):
        disc_status = getattr(cand, "discovery_status").value.lower()

    qual_outcome = str(getattr(cand, "qualification_outcome", "") or "").lower()
    if hasattr(getattr(cand, "qualification_outcome", None), "value"):
        qual_outcome = getattr(cand, "qualification_outcome").value.lower()

    manual_dec = str(getattr(cand, "manual_review_decision", "") or "").upper()
    hist_status = str(getattr(cand, "historical_contact_status", "") or "").lower()
    prev_outreach = bool(getattr(cand, "previous_outreach_detected", False) or hist_status in ("contacted", "replied", "converted", "dnc"))
    is_suppressed = bool(getattr(cand, "suppressed", False) or is_suppressed_domain)

    # 2. Check suppression / rejected / watchlist / duplicate (ABSOLUTE SAFETY PRECEDENCE)
    is_rejected = qual_outcome == "reject" or disc_status == "rejected" or manual_dec == "REJECTED"
    is_watchlist = disc_status == "watchlist" or qual_outcome == "watchlist"
    is_duplicate = (disc_status == "known_company" or qual_outcome == "known_company" or manual_dec == "DUPLICATE") and not prev_outreach
    is_supp_or_rej = bool(is_suppressed or is_rejected or is_watchlist or is_duplicate)

    # 3. Check explicitly reopened status
    # If a company is suppressed/rejected/watchlist/duplicate, it CANNOT be placed in Active Work queue
    # for cold outreach. It stays suppressed/protected even if reopened for data correction.
    is_reopened = manual_dec in ("REOPENED", "NEEDS_CORRECTION")

    # 4. Check historical finalized vs historical review pending
    # A historical company is finalized ONLY if explicitly marked HISTORICAL_FINALIZED.
    # Note: Historical records take precedence over general known_company duplicate flag.
    is_historical_finalized = prev_outreach and (manual_dec in ("HISTORICAL_FINALIZED", "FINALIZED_HISTORICAL"))
    is_historical_pending = prev_outreach and not is_historical_finalized and not is_reopened

    # 5. Check ICP qualification
    # An ICP-qualified company has qualification_outcome in ('qualified', 'qualified_for_enrichment')
    # or discovery_status in ('qualified', 'qualified_for_enrichment') AND is not rejected/suppressed.
    is_qualified = (
        not is_supp_or_rej
        and not is_reopened
        and (
            qual_outcome in ("qualified", "qualified_for_enrichment")
            or disc_status in ("qualified", "qualified_for_enrichment")
            or manual_dec == "APPROVED"
        )
    )

    # 6. Determine Primary Queue & Actionable Next Step
    # DNC / Suppression / Rejection ALWAYS takes precedence over active work or outreach eligibility.
    if is_supp_or_rej:
        p_queue = CandidateQueueType.SUPPRESSED_REJECTED
        sub_queue = None
        is_active = False
        if is_suppressed:
            if is_reopened:
                next_step = "Suppressed / Do Not Contact — protected from outreach (reopened for metadata correction only)"
            else:
                next_step = "Suppressed / Do Not Contact — protected from outreach"
            next_url = f"/sales/do-not-contact"
        elif is_watchlist:
            next_step = "Watchlisted for observation — paused from active pipeline"
            next_url = f"/sales/watchlist"
        elif is_duplicate:
            next_step = "Duplicate candidate — merged into known company profile"
            next_url = f"/sales/candidates/{cid}"
        else:
            next_step = "Rejected — candidate does not fit ICP criteria"
            next_url = f"/sales/candidates/{cid}"
    elif is_reopened:
        # Re-enters Active Work queue under Needs Correction (only if NOT suppressed/rejected)
        p_queue = CandidateQueueType.ACTIVE
        sub_queue = CandidateQueueType.NEEDS_CORRECTION
        is_active = True
        next_step = "Reopened for correction — review notes and update candidate profile"
        next_url = f"/sales/candidates/{cid}"
    elif is_historical_finalized:
        p_queue = CandidateQueueType.ARCHIVED_HISTORICAL
        sub_queue = None
        is_active = False
        next_step = "Historical communication finalized — archived in Sent History"
        next_url = f"/sales/sent"
    elif is_historical_pending:
        p_queue = CandidateQueueType.HISTORICAL_PENDING
        sub_queue = None
        is_active = False
        next_step = "Historical outreach detected — review past messages & finalize reconciliation"
        next_url = f"/sales/candidates/{cid}"
    elif is_qualified:
        # Triage complete; moves to Qualified Leads pipeline even if contact verification is pending
        p_queue = CandidateQueueType.QUALIFIED
        sub_queue = None
        is_active = False
        next_step = "ICP-qualified — verify contact liaison in Qualified Leads"
        # Deep link with clean domain or candidate_id
        target_param = dom if dom else cid
        next_url = f"/sales/leads?q={target_param}"
    else:
        # Active Work queue: new or unfinished candidates
        p_queue = CandidateQueueType.ACTIVE
        is_active = True

        # Determine subqueue for Active Work:
        if qual_outcome == "research_more" or disc_status == "researching" or manual_dec == "NEEDS_RESEARCH":
            sub_queue = CandidateQueueType.NEEDS_RESEARCH
            next_step = "Research needed — conduct website analysis & document core fields"
            next_url = f"/sales/candidates/{cid}/research"
        elif has_snapshot or lead_register_status in ("EXTRACTED", "Unverified", "OPERATOR_REVIEWED", "VERIFIED"):
            sub_queue = CandidateQueueType.NEEDS_VERIFICATION
            next_step = "Research data available — verify fields in Company Research Review"
            next_url = f"/sales/candidates/{cid}/research"
        else:
            sub_queue = CandidateQueueType.NEW
            next_step = "New candidate — evaluate ICP fit & initiate research"
            next_url = f"/sales/candidates/{cid}"

    return CandidateLifecycleState(
        candidate_id=cid,
        company_name=cname,
        primary_queue=p_queue,
        active_subqueue=sub_queue,
        is_active_work=is_active,
        is_qualified_lead=is_qualified,
        is_historical_pending=is_historical_pending,
        is_historical_finalized=is_historical_finalized,
        is_suppressed_or_rejected=is_supp_or_rej,
        is_reopened=is_reopened,
        actionable_next_step=next_step,
        recommended_action_url=next_url,
    )


class CandidateLifecycleService:
    """
    Lifecycle query and projection engine for the Candidate Work Queue.
    Operates on CandidateStore and LeadRegisterStore.
    """

    def __init__(self, store: Any):
        self.store = store

    def get_candidate_lifecycle_state(self, candidate_id: str) -> Optional[CandidateLifecycleState]:
        cand = self.store.get_by_id(candidate_id)
        if not cand:
            return None

        has_snap = False
        lr_stat = None
        is_supp = False

        def _has_table(conn, tbl: str) -> bool:
            r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (tbl,)).fetchone()
            return bool(r)

        with self.store._get_connection() as conn:
            # Check research_snapshots
            if _has_table(conn, "research_snapshots"):
                s_row = conn.execute("SELECT 1 FROM research_snapshots WHERE candidate_id = ? LIMIT 1", (candidate_id,)).fetchone()
                has_snap = bool(s_row)

            # Check lead_register_records
            if _has_table(conn, "lead_register_records"):
                lr_row = conn.execute("SELECT verification_status FROM lead_register_records WHERE candidate_id = ? LIMIT 1", (candidate_id,)).fetchone()
                if lr_row:
                    lr_stat = lr_row[0]

            # Check suppression
            if getattr(cand, "company_domain", None) and _has_table(conn, "suppression_list"):
                supp_row = conn.execute("SELECT 1 FROM suppression_list WHERE company_domain = ? LIMIT 1", (cand.company_domain.lower(),)).fetchone()
                is_supp = bool(supp_row)

        return project_candidate_lifecycle(
            cand=cand,
            has_snapshot=has_snap,
            lead_register_status=lr_stat,
            is_suppressed_domain=is_supp,
        )

    def compute_queue_counts(self) -> Dict[str, int]:
        """
        Computes accurate counts across all lifecycle queues without fixed-size truncation.
        """
        all_cands = self.store.list_candidates()

        snap_cids = set()
        lr_map = {}
        supp_domains = set()

        def _has_table(conn, tbl: str) -> bool:
            r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (tbl,)).fetchone()
            return bool(r)

        with self.store._get_connection() as conn:
            if _has_table(conn, "research_snapshots"):
                snap_cids = set(r[0] for r in conn.execute("SELECT DISTINCT candidate_id FROM research_snapshots").fetchall())
            if _has_table(conn, "lead_register_records"):
                lr_map = dict(conn.execute("SELECT candidate_id, verification_status FROM lead_register_records").fetchall())
            if _has_table(conn, "suppression_list"):
                supp_domains = set(r[0].lower() for r in conn.execute("SELECT company_domain FROM suppression_list WHERE company_domain IS NOT NULL").fetchall())

        counts = {
            "active": 0,
            "new": 0,
            "needs_research": 0,
            "needs_verification": 0,
            "needs_correction": 0,
            "historical_pending": 0,
            "all": len(all_cands),
            "qualified": 0,
            "archived_historical": 0,
            "suppressed_rejected": 0,
        }

        for c in all_cands:
            cid = getattr(c, "candidate_id", "") or ""
            dom = getattr(c, "company_domain", "") or ""
            is_supp = dom.lower() in supp_domains if dom else False
            has_snap = cid in snap_cids
            lr_stat = lr_map.get(cid)

            state = project_candidate_lifecycle(
                cand=c,
                has_snapshot=has_snap,
                lead_register_status=lr_stat,
                is_suppressed_domain=is_supp,
            )

            if state.is_active_work:
                counts["active"] += 1
                if state.active_subqueue == CandidateQueueType.NEW:
                    counts["new"] += 1
                elif state.active_subqueue == CandidateQueueType.NEEDS_RESEARCH:
                    counts["needs_research"] += 1
                elif state.active_subqueue == CandidateQueueType.NEEDS_VERIFICATION:
                    counts["needs_verification"] += 1
                elif state.active_subqueue == CandidateQueueType.NEEDS_CORRECTION:
                    counts["needs_correction"] += 1
            elif state.primary_queue == CandidateQueueType.HISTORICAL_PENDING:
                counts["historical_pending"] += 1
            elif state.primary_queue == CandidateQueueType.QUALIFIED:
                counts["qualified"] += 1
            elif state.primary_queue == CandidateQueueType.ARCHIVED_HISTORICAL:
                counts["archived_historical"] += 1
            elif state.primary_queue == CandidateQueueType.SUPPRESSED_REJECTED:
                counts["suppressed_rejected"] += 1

        return counts
