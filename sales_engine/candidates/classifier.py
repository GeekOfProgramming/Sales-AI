# sales_engine/candidates/classifier.py
"""
Deterministic and evidence-based classification and qualification engine for Phase 10.
Enforces:
- Legal form independence (P10-REG-003)
- BIM maturity missing-evidence safety (P10-REG-004)
- Relationship & Opportunity multi-typing
- Independent Fit vs Intent evaluation
- Active-conversation & suppression guards
"""

import re
from typing import List, Tuple, Dict, Any, Optional

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    BIMMaturity,
    RelationshipType,
    OpportunityType,
    CandidateQualificationResult,
    QualificationOutcome,
    MarketEvidence,
)


def classify_tier(
    legal_form: Optional[str] = None,
    employee_hint: Optional[str] = None,
    project_scale_hint: Optional[str] = None,
    evidence: Optional[List[MarketEvidence]] = None,
    company_types: Optional[List[str]] = None,
) -> Tuple[CompanyTier, float, List[str]]:
    """
    Classifies company tier (Tier 1, Tier 2, Tier 3, unknown).
    CRITICAL: Legal form alone (S.r.l., S.p.A., GmbH, Ltd, LLC) NEVER determines tier alone! (P10-REG-003)
    Weak or missing evidence results in CompanyTier.unknown.
    """
    evidence = evidence or []
    reasons = []

    # Check employee size hints
    emp_count = None
    if employee_hint:
        # e.g. "500+", "50-200", "15"
        nums = [int(n) for n in re.findall(r'\d+', employee_hint)]
        if nums:
            emp_count = max(nums)

    # Check project scale
    is_enterprise_scale = False
    if project_scale_hint and any(w in project_scale_hint.lower() for w in ["infrastructure", "rail", "mega", "airport", "highway", "hospital group", "epc"]):
        is_enterprise_scale = True

    types = [t.lower() for t in (company_types or [])]

    # Combine text from all evidence
    all_text = " ".join([e.text.lower() for e in evidence])

    # Tier 1 evidence: large enterprise, contractor, EPC, infrastructure, >250 employees
    if (emp_count and emp_count >= 250) or is_enterprise_scale or any(t in types for t in ["general_contractor", "epc", "infrastructure"]):
        if emp_count and emp_count >= 250:
            reasons.append(f"Enterprise employee headcount ({emp_count})")
        if is_enterprise_scale:
            reasons.append(f"Enterprise project scale ({project_scale_hint})")
        if any(t in types for t in ["epc", "infrastructure"]):
            reasons.append("EPC or major infrastructure organization")
        return CompanyTier.tier_1, 0.85, reasons

    # Tier 2 evidence: mid-sized structured engineering/architecture practice, 50-250 employees
    if (emp_count and 50 <= emp_count < 250) or ("multidisciplinary" in types) or ("multidisciplinary" in all_text and emp_count and emp_count >= 30):
        if emp_count:
            reasons.append(f"Mid-size headcount ({emp_count})")
        if "multidisciplinary" in types or "multidisciplinary" in all_text:
            reasons.append("Multidisciplinary practice across multiple sectors")
        return CompanyTier.tier_2, 0.80, reasons

    # Tier 3 evidence: boutique studio / specialist subcontractor / <50 employees
    if (emp_count and 1 <= emp_count < 50) or any(t in types for t in ["specialist_subcontractor", "boutique"]):
        if emp_count:
            reasons.append(f"Boutique or small team ({emp_count} people)")
        return CompanyTier.tier_3, 0.75, reasons

    # If only legal form is provided (e.g. S.p.A. or S.r.l.) and NO business size/scale evidence exists:
    # Rule P10-REG-003: Legal form alone MUST NOT determine tier alone!
    if legal_form and not emp_count and not is_enterprise_scale and not reasons:
        return CompanyTier.unknown, 0.0, ["Weak evidence: legal form alone cannot determine tier"]

    # Default fallback
    return CompanyTier.unknown, 0.0, ["Insufficient evidence to determine tier"]


def classify_bim_maturity(evidence: Optional[List[MarketEvidence]] = None) -> Tuple[BIMMaturity, float]:
    """
    Classifies BIM maturity.
    CRITICAL: Absence of BIM evidence must NOT become: no BIM, low BIM, false BIM.
    Missing evidence -> BIMMaturity.unknown (P10-REG-004)
    """
    evidence = evidence or []
    if not evidence:
        return BIMMaturity.unknown, 0.0

    all_text = " ".join([e.text.lower() for e in evidence])

    enterprise_signals = [
        "iso 19650", "uni 11337", "bim manager", "head of digital delivery",
        "computational bim", "revit api", "openbim", "ifc certification", "common data environment"
    ]
    high_signals = [
        "bim coordinator", "clash detection", "navisworks", "bim execution plan", "bep", "revit family"
    ]
    medium_signals = [
        "revit", "archicad", "bim modeling", "3d bim", "mep bim", "allplan"
    ]

    if any(s in all_text for s in enterprise_signals):
        return BIMMaturity.enterprise, 0.90
    if any(s in all_text for s in high_signals):
        return BIMMaturity.high, 0.80
    if any(s in all_text for s in medium_signals):
        return BIMMaturity.medium, 0.70

    # If no BIM terminology was found in evidence:
    # Rule P10-REG-004: absence of evidence is UNKNOWN, never low/no-BIM!
    return BIMMaturity.unknown, 0.0


def classify_relationship_and_opportunities(
    company_types: List[str],
    services: List[str],
    evidence: Optional[List[MarketEvidence]] = None
) -> Tuple[RelationshipType, List[OpportunityType]]:
    """
    Classifies relationship type and multiple opportunity types per company.
    Supports both direct engineering buyers and technology/strategic partners.
    """
    evidence = evidence or []
    all_text = " ".join([e.text.lower() for e in evidence] + [s.lower() for s in services] + [t.lower() for t in company_types])

    opps: List[OpportunityType] = []

    # Technology partner indicators
    is_tech = any(w in all_text for w in [
        "software development", "revit plugin", "bim software", "saas",
        "openbim platform", "cad software", "technology partner", "api integration", "custom software"
    ])

    # Direct buyer indicators
    is_delivery_buyer = any(w in all_text for w in [
        "architecture", "engineering", "mep", "structural", "general contractor",
        "construction", "design consultancy", "infrastructure projects", "building design"
    ])

    # 1. Determine RelationshipType
    rel_type = RelationshipType.unknown
    if is_tech and is_delivery_buyer:
        rel_type = RelationshipType.mixed
    elif is_tech:
        rel_type = RelationshipType.technology_partner
    elif is_delivery_buyer:
        rel_type = RelationshipType.service_buyer
    elif any(w in all_text for w in ["association", "consortium", "institute"]):
        rel_type = RelationshipType.strategic_partner
    elif any(w in all_text for w in ["reseller", "distributor"]):
        rel_type = RelationshipType.channel_partner

    # 2. Determine OpportunityTypes (multi-select)
    if "mep" in all_text:
        opps.append(OpportunityType.mep_delivery)
    if any(w in all_text for w in ["revit api", "dynamo", "python automation", "c#"]):
        opps.append(OpportunityType.revit_api)
        opps.append(OpportunityType.automation)
    if any(w in all_text for w in ["data parameter", "cobie", "metadata"]):
        opps.append(OpportunityType.data_parameter_automation)
    if any(w in all_text for w in ["coordination", "navisworks", "clash"]):
        opps.append(OpportunityType.bim_coordination)
    if is_delivery_buyer and OpportunityType.direct_bim_delivery not in opps:
        opps.append(OpportunityType.direct_bim_delivery)
        opps.append(OpportunityType.overflow_capacity)
    if is_tech:
        opps.append(OpportunityType.custom_software)
        opps.append(OpportunityType.technology_partnership)
    if rel_type == RelationshipType.strategic_partner:
        opps.append(OpportunityType.strategic_collaboration)

    if not opps:
        opps.append(OpportunityType.unknown)

    return rel_type, opps


def evaluate_fit_and_intent(
    candidate: CandidateCompany,
    evidence: Optional[List[MarketEvidence]] = None,
) -> Tuple[str, str, List[str], List[str]]:
    """
    Evaluates Fit and Intent independently.
    Fit: low, medium, high
    Intent: none, unknown, low, medium, high
    """
    evidence = evidence or []
    all_text = " ".join([e.text.lower() for e in evidence])

    fit_reasons = []
    intent_signals = []

    # Fit Assessment
    fit_score = 0
    if candidate.fit_assessment == "high":
        fit_score = max(fit_score, 3)
        fit_reasons.append("Prior verified high fit assessment")
    elif candidate.fit_assessment == "medium":
        fit_score = max(fit_score, 2)
        fit_reasons.append("Prior verified medium fit assessment")

    types = [t.lower() for t in candidate.company_types]
    if any(t in types for t in ["architecture", "engineering", "mep_engineering", "general_contractor", "bim_consultancy"]):
        fit_score += 2
        fit_reasons.append(f"Target company type ({', '.join(candidate.company_types)})")

    if any(k in all_text for k in ["bim modeling", "mep", "architecture", "revit", "navisworks", "engineering", "clash", "dynamo"]):
        fit_score += 2
        fit_reasons.append("Evidence of target BIM/MEP/Architectural operations")

    if candidate.employee_size_hint:
        nums = [int(n) for n in re.findall(r'\d+', candidate.employee_size_hint)]
        if nums and max(nums) >= 50:
            fit_score += 1
            fit_reasons.append(f"Significant employee scale ({candidate.employee_size_hint})")

    if candidate.country and candidate.country.lower() in ["italy", "germany", "united kingdom", "switzerland", "france", "austria"]:
        fit_score += 1
        fit_reasons.append(f"Target primary geographic market ({candidate.country})")

    if candidate.company_domain:
        fit_score += 1
        fit_reasons.append(f"Verified canonical company domain ({candidate.company_domain})")

    fit_level = "low"
    if fit_score >= 3:
        fit_level = "high"
    elif fit_score >= 2:
        fit_level = "medium"

    # Intent Assessment (Hiring, tender, new project, digital transformation)
    intent_score = 0
    for ev in evidence:
        cat = ev.category.lower()
        if cat in ["job", "hiring"] or "job" in ev.signal_type.lower() or "hiring" in ev.signal_type.lower():
            intent_score += 3
            intent_signals.append(f"Hiring signal: {ev.text[:80]}")
        elif cat in ["tender", "project"]:
            intent_score += 2
            intent_signals.append(f"Project/Tender signal: {ev.text[:80]}")
        elif "automation" in ev.text.lower() or "bim" in ev.text.lower():
            intent_score += 1
            intent_signals.append(f"Technology inquiry signal: {ev.text[:80]}")

    intent_level = "unknown"
    if intent_score >= 3:
        intent_level = "high"
    elif intent_score >= 2:
        intent_level = "medium"
    elif intent_score == 1:
        intent_level = "low"
    elif len(evidence) > 0 and intent_score == 0:
        intent_level = "none"

    return fit_level, intent_level, fit_reasons, intent_signals


def qualify_candidate(
    candidate: CandidateCompany,
    evidence: Optional[List[MarketEvidence]] = None,
    suppression_store: Optional[Any] = None,
) -> CandidateQualificationResult:
    """
    Core Phase 10 qualification logic respecting:
    - Phase 9 authoritative suppression
    - Active conversation guard
    - Previous outreach guard
    - Watchlist routing (High Fit + Low/Unknown Intent)
    """
    evidence = evidence or []

    # 1. Classify Tier, BIM maturity, relationship, and opportunities
    tier, tier_conf, tier_reasons = classify_tier(
        legal_form=candidate.legal_form,
        employee_hint=candidate.employee_size_hint,
        project_scale_hint=candidate.project_scale_hint,
        evidence=evidence,
        company_types=candidate.company_types,
    )
    candidate.company_tier = tier
    candidate.tier_confidence = tier_conf
    candidate.tier_evidence = tier_reasons

    bim_mat, bim_conf = classify_bim_maturity(evidence)
    candidate.bim_maturity = bim_mat
    candidate.bim_maturity_confidence = bim_conf

    rel_type, opps = classify_relationship_and_opportunities(
        company_types=candidate.company_types,
        services=[],
        evidence=evidence,
    )
    candidate.relationship_type = rel_type
    candidate.opportunity_types = opps

    # 2. Authority Check: Phase 9 Suppression (P10-REG-010)
    if suppression_store and candidate.company_domain:
        if suppression_store.is_suppressed(email=None, company_domain=candidate.company_domain):
            candidate.suppressed = True
            candidate.cold_outreach_allowed = False
            return CandidateQualificationResult(
                candidate_id=candidate.candidate_id,
                outcome=QualificationOutcome.suppressed,
                reasons=["Domain is listed on authoritative Phase 9 suppression list"],
                company_tier=tier,
                bim_maturity=bim_mat,
                relationship_type=rel_type,
                opportunity_types=opps,
                cold_outreach_allowed=False,
            )

    if candidate.suppressed:
        candidate.cold_outreach_allowed = False
        return CandidateQualificationResult(
            candidate_id=candidate.candidate_id,
            outcome=QualificationOutcome.suppressed,
            reasons=["Candidate marked suppressed"],
            company_tier=tier,
            bim_maturity=bim_mat,
            relationship_type=rel_type,
            opportunity_types=opps,
            cold_outreach_allowed=False,
        )

    # 3. Active Conversation Guard
    if candidate.active_conversation:
        candidate.cold_outreach_allowed = False
        return CandidateQualificationResult(
            candidate_id=candidate.candidate_id,
            outcome=QualificationOutcome.known_company,
            reasons=["Candidate has an active conversation ongoing; cold sequence blocked"],
            company_tier=tier,
            bim_maturity=bim_mat,
            relationship_type=rel_type,
            opportunity_types=opps,
            cold_outreach_allowed=False,
        )

    # 4. Previous Outreach Guard (P10-REG-006)
    if candidate.historical_contact_status in ["contacted", "replied", "converted"]:
        candidate.cold_outreach_allowed = False
        outcome = QualificationOutcome.reengagement_review if candidate.reply_status else QualificationOutcome.known_company
        return CandidateQualificationResult(
            candidate_id=candidate.candidate_id,
            outcome=outcome,
            reasons=[f"Previously contacted candidate ({candidate.historical_contact_status}); fresh cold outreach blocked"],
            company_tier=tier,
            bim_maturity=bim_mat,
            relationship_type=rel_type,
            opportunity_types=opps,
            cold_outreach_allowed=False,
        )

    # 5. Evaluate Fit & Intent
    fit_level, intent_level, fit_reasons, intent_signals = evaluate_fit_and_intent(candidate, evidence)
    candidate.fit_assessment = fit_level
    candidate.fit_reasons = fit_reasons
    candidate.intent_assessment = intent_level
    candidate.intent_signals = intent_signals

    # 6. Qualification Routing
    # High fit + Medium/High Intent -> qualified for enrichment
    if fit_level == "high" and intent_level in ["medium", "high"]:
        return CandidateQualificationResult(
            candidate_id=candidate.candidate_id,
            outcome=QualificationOutcome.qualified_for_enrichment,
            reasons=fit_reasons + intent_signals,
            company_tier=tier,
            bim_maturity=bim_mat,
            fit_level=fit_level,
            intent_level=intent_level,
            relationship_type=rel_type,
            opportunity_types=opps,
            cold_outreach_allowed=True,
        )

    # Good fit + Weak/Unknown/None Intent -> Watchlist (P10-REG-008)
    if fit_level in ["high", "medium"] and intent_level in ["none", "unknown", "low"]:
        return CandidateQualificationResult(
            candidate_id=candidate.candidate_id,
            outcome=QualificationOutcome.watchlist,
            reasons=fit_reasons + ["Good fit with low/unknown current intent; routing to watchlist"],
            company_tier=tier,
            bim_maturity=bim_mat,
            fit_level=fit_level,
            intent_level=intent_level,
            relationship_type=rel_type,
            opportunity_types=opps,
            cold_outreach_allowed=False,
        )

    # Low fit or unknown -> Reject
    return CandidateQualificationResult(
        candidate_id=candidate.candidate_id,
        outcome=QualificationOutcome.reject,
        reasons=fit_reasons or ["Insufficient fit criteria matched"],
        company_tier=tier,
        bim_maturity=bim_mat,
        fit_level=fit_level,
        intent_level=intent_level,
        relationship_type=rel_type,
        opportunity_types=opps,
        cold_outreach_allowed=False,
    )


class CompanyClassifier:
    """OOP interface wrapper for Phase 10 deterministic classification."""

    def __init__(self, suppression_store: Optional[Any] = None):
        self.suppression_store = suppression_store

    def classify_tier(
        self,
        legal_form: Optional[str] = None,
        employee_hint: Optional[str] = None,
        project_scale_hint: Optional[str] = None,
        evidence: Optional[List[MarketEvidence]] = None,
        company_types: Optional[List[str]] = None,
    ) -> Tuple[CompanyTier, float, List[str]]:
        return classify_tier(legal_form, employee_hint, project_scale_hint, evidence, company_types)

    def classify_bim_maturity(
        self,
        evidence: Optional[List[MarketEvidence]] = None,
        technologies: Optional[List[str]] = None,
        services: Optional[List[str]] = None,
    ) -> Tuple[BIMMaturity, float]:
        return classify_bim_maturity(evidence, technologies, services)

    def classify_relationship_and_opportunities(
        self,
        company_types: Optional[List[str]] = None,
        services: Optional[List[str]] = None,
        evidence: Optional[List[MarketEvidence]] = None,
    ) -> Tuple[RelationshipType, List[OpportunityType]]:
        return classify_relationship_and_opportunities(company_types, services, evidence)

    def evaluate_fit_and_intent(
        self,
        candidate: CandidateCompany,
        evidence: Optional[List[MarketEvidence]] = None,
    ) -> Tuple[str, str, List[str], List[str]]:
        return evaluate_fit_and_intent(candidate, evidence)

    def qualify(
        self,
        candidate: CandidateCompany,
        evidence: Optional[List[MarketEvidence]] = None,
    ) -> CandidateQualificationResult:
        return qualify_candidate(candidate, evidence, self.suppression_store)

    def classify(
        self,
        candidate: CandidateCompany,
        observations: Optional[List[Any]] = None,
    ) -> CandidateQualificationResult:
        """
        Unified classification helper from observations or evidence.
        Converts observations into MarketEvidence if needed.
        """
        evidence: List[MarketEvidence] = []
        if observations:
            for idx, obs in enumerate(observations):
                if isinstance(obs, MarketEvidence):
                    evidence.append(obs)
                elif hasattr(obs, "extracted_data") and isinstance(obs.extracted_data, dict):
                    ext = obs.extracted_data
                    # Construct market evidence from extracted data
                    if "services" in ext and isinstance(ext["services"], list):
                        for s in ext["services"]:
                            evidence.append(MarketEvidence(
                                evidence_id=f"ev_serv_{idx}_{s}",
                                candidate_id=candidate.candidate_id,
                                category="service",
                                signal_type="service_offering",
                                text=s,
                            ))
                    if "technologies" in ext and isinstance(ext["technologies"], list):
                        for t in ext["technologies"]:
                            evidence.append(MarketEvidence(
                                evidence_id=f"ev_tech_{idx}_{t}",
                                candidate_id=candidate.candidate_id,
                                category="technology",
                                signal_type="tool_usage",
                                text=t,
                            ))
                    if "job_title" in ext:
                        evidence.append(MarketEvidence(
                            evidence_id=f"ev_job_{idx}",
                            candidate_id=candidate.candidate_id,
                            category="job",
                            signal_type="active_hiring",
                            text=str(ext["job_title"]),
                        ))
                    if "project_title" in ext:
                        evidence.append(MarketEvidence(
                            evidence_id=f"ev_proj_{idx}",
                            candidate_id=candidate.candidate_id,
                            category="project",
                            signal_type="portfolio",
                            text=str(ext["project_title"]),
                        ))
        return qualify_candidate(candidate, evidence, self.suppression_store)

    def classify_company(
        self,
        candidate: CandidateCompany,
        observations: Optional[List[Any]] = None,
        evidence: Optional[List[MarketEvidence]] = None,
    ) -> CandidateQualificationResult:
        """Convenience method combining observations and explicit evidence for qualification."""
        ev_list = list(evidence or [])
        res = self.classify(candidate, observations=observations)
        if evidence:
            return self.qualify(candidate, evidence=ev_list)
        return res


