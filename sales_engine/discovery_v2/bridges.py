"""
Integration Bridges for Phase 10:
- Phase 5: Frozen score invariant (no fake jobs, non-job candidates remain unscored by Phase 5).
- Phase 6: Contact enrichment adapter (bypasses StructuredJob requirement, reuses historical contacts).
- Phase 7: Additive export fields (tier, bim maturity, fit, intent, relationship_type, opportunities).
- Phase 8: Fact grounding bridge (real facts only; rejects internal assessments as factual claims).
- Phase 9: Suppression authority & zero-send guard (checks Phase 9 suppression store, strictly zero sends).
- Phase 10.5: Backend operator console queries (candidate summary, detail, evidence, health, watchlist).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryStatus,
    OpportunityType,
    QualificationOutcome,
    RelationshipType,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.discovery_v2.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 5 Freeze Bridge
# ---------------------------------------------------------------------------
class Phase5FreezeBridge:
    """
    Guarantees non-job candidates do not receive fake Phase 5 scores.
    """

    @staticmethod
    def get_phase5_score(candidate: CandidateCompany) -> Optional[Dict[str, Any]]:
        # Non-job candidate: return None or explicit unscored status
        # NEVER generate a fake StructuredJob or call Phase 5 scoring on synthetic jobs!
        if not candidate.metadata.get("structured_job_id"):
            return None
        # If genuine job exists, it would be scored via legacy Phase 5
        return candidate.metadata.get("legacy_phase5_score")


# ---------------------------------------------------------------------------
# Phase 6 Enrichment Adapter
# ---------------------------------------------------------------------------
class Phase6EnrichmentAdapter:
    """
    Allows qualified CandidateCompany to enrich contacts without requiring a StructuredJob.
    Prioritizes historical contacts before invoking external provider enrichment.
    """

    def __init__(self, candidate_store: CandidateStore):
        self.store = candidate_store

    def prepare_enrichment_target(self, candidate: CandidateCompany) -> Dict[str, Any]:
        """
        Creates an enrichment target dictionary without creating any fake StructuredJob.
        """
        if not candidate.canonical_domain:
            raise ValueError(f"Candidate {candidate.name} lacks canonical domain for enrichment")

        # Lookup historical contacts first
        historical_contacts = []
        with self.store._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT contact_name, contact_role, email, email_status, is_respondent
                FROM historical_contacts
                WHERE canonical_domain = ?
                """,
                (candidate.canonical_domain,),
            ).fetchall()
            for r in rows:
                historical_contacts.append({
                    "name": r["contact_name"],
                    "role": r["contact_role"],
                    "email": r["email"],
                    "email_status": r["email_status"],
                    "is_respondent": bool(r["is_respondent"]),
                })

        return {
            "company_id": candidate.company_id,
            "company_name": candidate.name,
            "domain": candidate.canonical_domain,
            "target_roles": self._recommend_target_roles(candidate),
            "historical_contacts": historical_contacts,
            "requires_external_enrichment": len(historical_contacts) == 0,
        }

    def _recommend_target_roles(self, candidate: CandidateCompany) -> List[str]:
        if candidate.relationship_type == RelationshipType.technology_partner:
            return ["CTO", "Head of Product", "VP Engineering", "Software Partnerships"]
        if candidate.bim_maturity.value in ("mature", "advanced"):
            return ["BIM Manager", "Director of BIM", "Digital Practice Leader", "Technical Director"]
        return ["Managing Director", "Principal", "Owner", "Technical Director"]


# ---------------------------------------------------------------------------
# Phase 7 Additive Export Bridge
# ---------------------------------------------------------------------------
class Phase7ExportBridge:
    """
    Additive export transformer. Adds Phase 10 fields while preserving all legacy columns.
    """

    @staticmethod
    def enrich_export_row(base_row: Dict[str, Any], candidate: CandidateCompany) -> Dict[str, Any]:
        enriched = dict(base_row)
        # Additive Phase 10 fields:
        enriched.update({
            "company_tier": candidate.company_tier.value,
            "bim_maturity": candidate.bim_maturity.value,
            "fit_level": candidate.fit_level.value,
            "intent_level": candidate.intent_level.value,
            "outsourcing_potential": candidate.outsourcing_potential.value,
            "automation_potential": candidate.automation_potential.value,
            "relationship_type": candidate.relationship_type.value,
            "opportunity_types": ",".join(o.value for o in candidate.opportunity_types),
            "discovery_sources": ",".join(candidate.discovery_sources),
            "historical_contact_status": "contacted" if candidate.previous_outreach_detected else "none",
            "historical_reply_status": candidate.metadata.get("historical_reply_status", "none"),
            "watchlist_status": "active" if candidate.status == DiscoveryStatus.watchlist else "inactive",
            "qualification_route": candidate.status.value,
            "cold_outreach_allowed": candidate.cold_outreach_allowed,
        })
        return enriched


# ---------------------------------------------------------------------------
# Phase 8 Fact Grounding Bridge
# ---------------------------------------------------------------------------
class Phase8GroundingBridge:
    """
    Extracts strictly verified real facts for Phase 8 generation.
    Strictly filters out internal subjective assessments from being stated as facts.
    """

    FORBIDDEN_FACT_TERMS = {
        "likely budget",
        "likely understaffed",
        "likely pain",
        "probably inefficient",
        "outsourcing_potential = high",
        "automation_potential = high",
    }

    @classmethod
    def extract_grounded_facts(cls, candidate: CandidateCompany, observations: List[Any]) -> Dict[str, Any]:
        facts = {
            "company_name": candidate.name,
            "canonical_domain": candidate.canonical_domain,
            "verified_services": [],
            "verified_technologies": [],
            "verified_standards": [],
            "verified_projects": [],
            "verified_jobs": [],
            "raw_factual_snippets": [],
        }

        # Scan observations for verifiable factual items
        for obs in observations:
            ext = obs.extracted_data if hasattr(obs, "extracted_data") else {}
            # Real technologies
            if "technologies" in ext and isinstance(ext["technologies"], list):
                for tech in ext["technologies"]:
                    if tech not in facts["verified_technologies"]:
                        facts["verified_technologies"].append(tech)

            # Standards
            if "standards" in ext and isinstance(ext["standards"], list):
                for st in ext["standards"]:
                    if st not in facts["verified_standards"]:
                        facts["verified_standards"].append(st)

            # Projects / Tenders
            if ext.get("project_title"):
                facts["verified_projects"].append({
                    "title": ext.get("project_title"),
                    "category": ext.get("project_category"),
                })
            if ext.get("tender_reference"):
                facts["verified_projects"].append({
                    "reference": ext.get("tender_reference"),
                    "title": ext.get("tender_title"),
                })

            # Real job titles (only if real job)
            if ext.get("job_title") and obs.discovery_mode == "job_first":
                facts["verified_jobs"].append(ext.get("job_title"))

        return facts

    @classmethod
    def sanitize_draft_claims(cls, claim_text: str) -> Tuple[bool, str]:
        """
        Validates that an email snippet does not assert internal assessments as facts.
        Returns (is_valid, sanitized_text_or_violation).
        """
        lower = claim_text.lower()
        for term in cls.FORBIDDEN_FACT_TERMS:
            if term in lower:
                return False, f"Forbidden internal assessment claimed as fact: '{term}'"
        return True, claim_text


# ---------------------------------------------------------------------------
# Phase 9 Suppression Authority Bridge
# ---------------------------------------------------------------------------
class Phase9AuthorityBridge:
    """
    Authoritatively checks Phase 9 suppression store and enforces zero-send invariant.
    """

    def __init__(self, suppression_store: Optional[Any] = None):
        self.suppression_store = suppression_store

    def is_suppressed(self, email: Optional[str] = None, domain: Optional[str] = None) -> bool:
        if not self.suppression_store:
            return False
        # Phase 9 SuppressionStore method
        if hasattr(self.suppression_store, "is_suppressed"):
            if email and self.suppression_store.is_suppressed(email):
                return True
            if domain and hasattr(self.suppression_store, "is_domain_suppressed"):
                if self.suppression_store.is_domain_suppressed(domain):
                    return True
        return False

    def enforce_safety_on_candidate(self, candidate: CandidateCompany) -> CandidateCompany:
        if self.is_suppressed(domain=candidate.canonical_domain):
            candidate.cold_outreach_allowed = False
            candidate.status = DiscoveryStatus.suppressed
            candidate.suppression_state["phase9_suppression"] = True
        return candidate


# ---------------------------------------------------------------------------
# Phase 10.5 Console Query Bridge
# ---------------------------------------------------------------------------
class Phase10ConsoleBridge:
    """
    Exposes structured backend query APIs for the future Phase 10.5 operator console.
    """

    def __init__(self, store: CandidateStore, registry: Optional[SourceRegistry] = None):
        self.store = store
        self.registry = registry or SourceRegistry()

    def get_candidate_list_summary(
        self,
        status: Optional[str] = None,
        relationship_type: Optional[str] = None,
        tier: Optional[str] = None,
        min_fit: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        # Fetch from CandidateStore
        filters = {}
        if status:
            filters["discovery_status"] = status
        if tier:
            filters["company_tier"] = tier
        candidates = self.store.list_candidates(filters=filters if filters else None)
        results = []
        for c in candidates:
            if relationship_type and c.relationship_type.value != relationship_type:
                continue
            fit_val = c.fit_level.value if hasattr(c.fit_level, "value") else str(c.fit_level)
            intent_val = c.intent_level.value if hasattr(c.intent_level, "value") else str(c.intent_level)
            results.append({
                "company_id": c.company_id,
                "name": c.name,
                "canonical_domain": c.canonical_domain,
                "status": c.status.value,
                "company_tier": c.company_tier.value,
                "bim_maturity": c.bim_maturity.value,
                "fit_level": fit_val,
                "intent_level": intent_val,
                "relationship_type": c.relationship_type.value,
                "opportunity_types": [o.value for o in c.opportunity_types],
                "cold_outreach_allowed": c.cold_outreach_allowed,
                "created_at": c.first_seen_at,
            })
        return results

    def get_candidate_detail(self, company_id: str) -> Optional[Dict[str, Any]]:
        company = self.store.get_by_id(company_id)
        if not company:
            return None
        obs = self.store.get_observations_for_company(company_id)
        comp_dict = company.model_dump() if hasattr(company, "model_dump") else dict(company)
        comp_dict["name"] = comp_dict.get("company_name")
        comp_dict["company_id"] = comp_dict.get("candidate_id")
        comp_dict["canonical_domain"] = comp_dict.get("company_domain")
        obs_dicts = [o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in obs]
        return {
            "company": comp_dict,
            "observations": obs_dicts,
            "observation_count": len(obs),
        }

    def get_source_health_summary(self) -> Dict[str, Any]:
        return self.registry.get_health_summary()

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        entries = self.store.get_watchlist()
        results = []
        for w in entries:
            company = self.store.get_by_id(w.company_id)
            results.append({
                "watchlist_id": w.watchlist_id,
                "company_id": w.company_id,
                "company_name": company.name if company else "Unknown",
                "canonical_domain": company.canonical_domain if company else None,
                "reason": w.reason,
                "priority": w.priority,
                "review_after": w.review_after,
                "status": w.status,
            })
        return results
