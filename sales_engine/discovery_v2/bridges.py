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
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
            try:
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
            except Exception:
                pass

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
        if candidate.bim_maturity.value in ("mature", "advanced", "high", "enterprise"):
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

    @classmethod
    def build_lead_export_row(cls, candidate: CandidateCompany) -> Dict[str, Any]:
        base = {
            "company_name": candidate.name,
            "company_domain": candidate.canonical_domain,
            "domain": candidate.canonical_domain,
            "country": candidate.country or "",
            "city": candidate.city or "",
        }
        return cls.enrich_export_row(base, candidate)


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

    TRUSTED_FACT_SOURCE_TYPES: Set[str] = {
        "job_board",
        "ats",
        "company_directory",
        "business_directory",
        "tender_portal",
        "project_database",
        "industry_news",
        "company_website",
        "search_engine",
        "web_source",
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

            # Blocker 8: Arbitrary/internal observation cannot become verified Phase 8 fact.
            # Reject internal, manual, unverified, or unknown source types.
            if source_type in ("internal_note", "internal_manual", "internal_assessment", "scratchpad", "unknown", "") or source_id in ("internal_note", "internal_manual"):
                continue

            if source_type not in cls.TRUSTED_FACT_SOURCE_TYPES:
                continue

            # Must have valid external public source URL or reference
            if not source_url or not (source_url.startswith("http://") or source_url.startswith("https://")):
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

    def __init__(self, suppression_store: Optional[Any] = None, db_path: Optional[str] = None):
        self.suppression_store = suppression_store
        self.db_path = db_path or os.getenv("SALESAI_DB_PATH", "data/sales_outreach.db")

    def _get_db_conn(self) -> Optional[sqlite3.Connection]:
        if hasattr(self.suppression_store, "store") and hasattr(self.suppression_store.store, "_get_connection"):
            return self.suppression_store.store._get_connection()
        elif hasattr(self.suppression_store, "_get_connection"):
            return self.suppression_store._get_connection()
        elif self.db_path and Path(self.db_path).exists():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        return None

    def is_domain_suppressed(self, domain: Optional[str]) -> bool:
        """
        Checks explicit domain-level suppression ONLY.
        Does NOT suppress a domain based on single email addresses on that domain.
        """
        if not domain:
            return False
        clean_domain = domain.strip().lower()
        if not clean_domain:
            return False

        # 1. Direct DB check against real Phase 9 suppression_list table
        conn = self._get_db_conn()
        if conn:
            try:
                cur = conn.cursor()
                r = cur.execute(
                    "SELECT 1 FROM suppression_list WHERE (company_domain IS NOT NULL AND LOWER(company_domain) = ?) OR (scope = 'domain' AND LOWER(value) = ?) LIMIT 1",
                    (clean_domain, clean_domain)
                ).fetchone()
                if r:
                    return True
            except Exception:
                pass

        # 2. Store method if available
        if self.suppression_store:
            if hasattr(self.suppression_store, "is_domain_suppressed"):
                try:
                    if self.suppression_store.is_domain_suppressed(clean_domain):
                        return True
                except Exception:
                    pass
            # list_suppressed check
            if hasattr(self.suppression_store, "list_suppressed"):
                try:
                    for entry in self.suppression_store.list_suppressed():
                        e_dom = getattr(entry, "company_domain", None)
                        scope = getattr(entry, "scope", None)
                        val = getattr(entry, "value", None)
                        if e_dom and e_dom.lower() == clean_domain:
                            return True
                        if scope == "domain" and val and val.lower() == clean_domain:
                            return True
                except Exception:
                    pass
        return False

    def is_email_suppressed(self, email: Optional[str]) -> bool:
        """
        Checks if an email is suppressed (either directly or via its domain).
        """
        if not email:
            return False
        clean_email = email.strip().lower()
        if not clean_email:
            return False

        # First check if the email's domain is domain-suppressed
        if "@" in clean_email:
            domain_part = clean_email.split("@", 1)[1]
            if self.is_domain_suppressed(domain_part):
                return True

        # Check exact email in database
        conn = self._get_db_conn()
        if conn:
            try:
                cur = conn.cursor()
                r = cur.execute(
                    "SELECT 1 FROM suppression_list WHERE LOWER(email) = ? OR (scope = 'email' AND LOWER(value) = ?) LIMIT 1",
                    (clean_email, clean_email)
                ).fetchone()
                if r:
                    return True
            except Exception:
                pass

        if self.suppression_store:
            if hasattr(self.suppression_store, "is_suppressed"):
                try:
                    if self.suppression_store.is_suppressed(clean_email):
                        return True
                except TypeError:
                    pass
            if hasattr(self.suppression_store, "list_suppressed"):
                try:
                    for entry in self.suppression_store.list_suppressed():
                        e_mail = getattr(entry, "email", None)
                        scope = getattr(entry, "scope", None)
                        val = getattr(entry, "value", None)
                        if e_mail and e_mail.lower() == clean_email:
                            return True
                        if scope == "email" and val and val.lower() == clean_email:
                            return True
                except Exception:
                    pass
        return False

    def is_suppressed(self, email: Optional[str] = None, domain: Optional[str] = None, company_domain: Optional[str] = None) -> bool:
        """
        Universal read-only check.
        If email is provided, checks email suppression.
        If domain / company_domain is provided, checks domain suppression.
        """
        eff_domain = domain or company_domain
        if email and self.is_email_suppressed(email):
            return True
        if eff_domain and self.is_domain_suppressed(eff_domain):
            return True
        return False

    def enforce_safety_on_candidate(self, candidate: CandidateCompany) -> CandidateCompany:
        if self.is_domain_suppressed(candidate.canonical_domain):
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
                "country": c.country,
                "city": c.city,
                "status": _get_val(c.status),
                "company_tier": _get_val(c.company_tier),
                "bim_maturity": _get_val(c.bim_maturity),
                "fit_level": fit_val,
                "intent_level": intent_val,
                "relationship_type": _get_val(c.relationship_type),
                "opportunity_types": [_get_val(o) for o in c.opportunity_types],
                "cold_outreach_allowed": c.cold_outreach_allowed,
                "qualification_outcome": _get_val(c.qualification_outcome) if c.qualification_outcome else None,
                "qualification_reason": c.qualification_reason,
                "manual_review_decision": getattr(c, "manual_review_decision", "NOT_REVIEWED") or "NOT_REVIEWED",
                "manual_reviewed_at": getattr(c, "manual_reviewed_at", None),
                "manual_reviewed_by": getattr(c, "manual_reviewed_by", None),
                "manual_review_notes": getattr(c, "manual_review_notes", None),
                "historical_outreach_summary": c.historical_contact_status,
                "discovery_sources": getattr(c, "discovery_sources", getattr(c, "source_refs", [])),
                "created_at": c.first_seen_at,
            })
        return results

    def get_candidate_detail(self, company_id: str) -> Optional[Dict[str, Any]]:
        company = self.store.get_by_id(company_id)
        if not company:
            return None
        obs = self.store.get_observations_for_company(company_id)
        ev = self.store.get_evidence(company_id)
        comp_dict = company.model_dump() if hasattr(company, "model_dump") else dict(company)
        comp_dict["name"] = comp_dict.get("company_name")
        comp_dict["company_id"] = comp_dict.get("candidate_id")
        comp_dict["canonical_domain"] = comp_dict.get("company_domain")
        obs_dicts = [o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in obs]
        ev_dicts = [e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in ev]
        return {
            "candidate_id": company_id,
            "company_id": company_id,
            "company": comp_dict,
            "observations": obs_dicts,
            "observation_count": len(obs),
            "evidence": ev_dicts,
            "evidence_count": len(ev),
        }

    def get_source_health_summary(self) -> Dict[str, Any]:
        persisted_health = self.store.get_all_source_health()
        if persisted_health:
            res = {}
            for name, h in persisted_health.items():
                if isinstance(h, dict):
                    res[name] = h
                else:
                    res[name] = h.model_dump() if hasattr(h, "model_dump") else dict(h)
            return res
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


