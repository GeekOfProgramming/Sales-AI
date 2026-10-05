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
def _get_val(x: Any) -> str:
    if x is None:
        return ""
    if hasattr(x, "value"):
        return str(x.value)
    return str(x)


class Phase7ExportBridge:
    """
    Additive export transformer. Adds Phase 10 fields while preserving all legacy columns.
    """

    @staticmethod
    def enrich_export_row(base_row: Dict[str, Any], candidate: CandidateCompany) -> Dict[str, Any]:
        enriched = dict(base_row)
        # Additive Phase 10 fields (safe string/enum handling):
        enriched.update({
            "company_tier": _get_val(candidate.company_tier),
            "bim_maturity": _get_val(candidate.bim_maturity),
            "fit_level": _get_val(candidate.fit_level),
            "intent_level": _get_val(candidate.intent_level),
            "outsourcing_potential": _get_val(candidate.outsourcing_potential),
            "automation_potential": _get_val(candidate.automation_potential),
            "relationship_type": _get_val(candidate.relationship_type),
            "opportunity_types": ",".join(_get_val(o) for o in candidate.opportunity_types),
            "discovery_sources": ",".join(candidate.discovery_sources),
            "historical_contact_status": "contacted" if candidate.previous_outreach_detected or candidate.historical_contact_status in ("contacted", "replied", "converted") else "none",
            "historical_reply_status": candidate.reply_status or candidate.metadata.get("historical_reply_status", "none"),
            "watchlist_status": "active" if _get_val(candidate.status) == "watchlist" else "inactive",
            "qualification_route": _get_val(candidate.status),
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
        "outsourcing_potential",
        "automation_potential",
        "outsourcing_potential = high",
        "automation_potential = high",
        "outsourcing_potential = medium",
        "automation_potential = medium",
        "outsourcing_potential = low",
        "tier_1",
        "tier_2",
        "tier_3",
    }

    @classmethod
    def _is_forbidden(cls, text: Any) -> bool:
        if not text or not isinstance(text, str):
            return False
        lower = text.lower()
        return any(term in lower for term in cls.FORBIDDEN_FACT_TERMS)

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

        # Scan observations for verifiable factual items with valid provenance
        for obs in observations:
            source_type = getattr(obs, "source_type", "")
            source_url = getattr(obs, "source_url", None)
            source_id = getattr(obs, "source_id", "")

            # P10-ADV: manual_seed observations cannot produce verified facts without explicit source_url
            if (source_type == "manual_seed" or source_id == "manual_seed") and not source_url:
                continue

            ext = obs.extracted_data if hasattr(obs, "extracted_data") else {}
            if not isinstance(ext, dict):
                ext = {}

            # Real technologies
            if "technologies" in ext and isinstance(ext["technologies"], list):
                for tech in ext["technologies"]:
                    if isinstance(tech, str) and tech not in facts["verified_technologies"] and not cls._is_forbidden(tech):
                        facts["verified_technologies"].append(tech)

            # Standards
            if "standards" in ext and isinstance(ext["standards"], list):
                for st in ext["standards"]:
                    if isinstance(st, str) and st not in facts["verified_standards"] and not cls._is_forbidden(st):
                        facts["verified_standards"].append(st)

            # Projects / Tenders
            if ext.get("project_title") and not cls._is_forbidden(ext.get("project_title")):
                facts["verified_projects"].append({
                    "title": ext.get("project_title"),
                    "category": ext.get("project_category"),
                    "source_url": source_url,
                })
            if ext.get("tender_reference"):
                facts["verified_projects"].append({
                    "reference": ext.get("tender_reference"),
                    "title": ext.get("tender_title"),
                    "source_url": source_url,
                })

            # Real job titles (only if genuine job-first mode)
            if ext.get("job_title") and getattr(obs, "discovery_mode", "") == "job_first":
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
    Supports real SuppressionStore with domain and email checks without altering frozen Phase 9.
    """

    def __init__(self, suppression_store: Optional[Any] = None):
        self.suppression_store = suppression_store

    def is_suppressed(self, email: Optional[str] = None, domain: Optional[str] = None) -> bool:
        if not self.suppression_store:
            return False

        clean_email = email.strip().lower() if email else None
        clean_domain = domain.strip().lower() if domain else None

        # 1. Direct connection check if available (safest against real SQLite suppression_list)
        try:
            conn_target = None
            if hasattr(self.suppression_store, "store") and hasattr(self.suppression_store.store, "_get_connection"):
                conn_target = self.suppression_store.store._get_connection()
            elif hasattr(self.suppression_store, "_get_connection"):
                conn_target = self.suppression_store._get_connection()

            if conn_target:
                with conn_target as conn:
                    if clean_email:
                        r = conn.execute("SELECT 1 FROM suppression_list WHERE LOWER(email) = ? LIMIT 1", (clean_email,)).fetchone()
                        if r:
                            return True
                    if clean_domain:
                        r = conn.execute("SELECT 1 FROM suppression_list WHERE LOWER(company_domain) = ? LIMIT 1", (clean_domain,)).fetchone()
                        if r:
                            return True
                        r_like = conn.execute("SELECT 1 FROM suppression_list WHERE LOWER(email) LIKE ? LIMIT 1", (f"%@{clean_domain}",)).fetchone()
                        if r_like:
                            return True
        except Exception:
            pass

        # 2. Method check on suppression_store
        if clean_email and hasattr(self.suppression_store, "is_suppressed"):
            try:
                if self.suppression_store.is_suppressed(clean_email, clean_domain):
                    return True
            except TypeError:
                if self.suppression_store.is_suppressed(clean_email):
                    return True

        if clean_domain:
            if hasattr(self.suppression_store, "is_domain_suppressed"):
                if self.suppression_store.is_domain_suppressed(clean_domain):
                    return True
            # Fallback to is_suppressed with synthetic email on the domain
            if hasattr(self.suppression_store, "is_suppressed"):
                try:
                    if self.suppression_store.is_suppressed(f"domain-check@{clean_domain}", clean_domain):
                        return True
                except Exception:
                    pass

        # 3. Check list_suppressed if available
        if hasattr(self.suppression_store, "list_suppressed"):
            try:
                for entry in self.suppression_store.list_suppressed():
                    e_dom = getattr(entry, "company_domain", None)
                    e_mail = getattr(entry, "email", None)
                    if clean_domain and e_dom and e_dom.lower() == clean_domain:
                        return True
                    if clean_email and e_mail and e_mail.lower() == clean_email:
                        return True
                    if clean_domain and e_mail and e_mail.lower().endswith(f"@{clean_domain}"):
                        return True
            except Exception:
                pass

        return False

    def enforce_safety_on_candidate(self, candidate: CandidateCompany) -> CandidateCompany:
        if self.is_suppressed(domain=candidate.canonical_domain):
            candidate.cold_outreach_allowed = False
            candidate.suppressed = True
            candidate.status = DiscoveryStatus.suppressed
            candidate.suppression_state["phase9_suppression"] = True
        return candidate


# ---------------------------------------------------------------------------
# Phase 10.5 Console Query Bridge
# ---------------------------------------------------------------------------
class Phase10ConsoleBridge:
    """
    Exposes structured backend query APIs for the operator console.
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
        filters = {}
        if status:
            filters["discovery_status"] = status
        if tier:
            filters["company_tier"] = tier
        candidates = self.store.list_candidates(filters=filters if filters else None)
        results = []
        for c in candidates:
            if relationship_type and _get_val(c.relationship_type) != relationship_type:
                continue
            fit_val = _get_val(c.fit_level)
            intent_val = _get_val(c.intent_level)
            results.append({
                "company_id": c.company_id,
                "name": c.name,
                "canonical_domain": c.canonical_domain,
                "status": _get_val(c.status),
                "company_tier": _get_val(c.company_tier),
                "bim_maturity": _get_val(c.bim_maturity),
                "fit_level": fit_val,
                "intent_level": intent_val,
                "relationship_type": _get_val(c.relationship_type),
                "opportunity_types": [_get_val(o) for o in c.opportunity_types],
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
            cand_id = w.get("candidate_id") if isinstance(w, dict) else getattr(w, "candidate_id", getattr(w, "company_id", None))
            company = self.store.get_by_id(cand_id) if cand_id else None
            reason = w.get("watch_reason") if isinstance(w, dict) else getattr(w, "watch_reason", getattr(w, "reason", ""))
            status = w.get("watch_status") if isinstance(w, dict) else getattr(w, "watch_status", getattr(w, "status", "active"))
            signals = w.get("signals_to_monitor") if isinstance(w, dict) else getattr(w, "signals_to_monitor", [])
            results.append({
                "watchlist_id": w.get("watchlist_id") if isinstance(w, dict) else getattr(w, "watchlist_id", cand_id),
                "candidate_id": cand_id,
                "company_id": cand_id,
                "company_name": company.company_name if company else (w.get("company_name", "Unknown") if isinstance(w, dict) else "Unknown"),
                "canonical_domain": company.company_domain if company else None,
                "reason": reason,
                "watch_reason": reason,
                "status": status,
                "watch_status": status,
                "signals_to_monitor": signals,
                "next_check_at": w.get("next_check_at") if isinstance(w, dict) else getattr(w, "next_check_at", None),
            })
        return results

