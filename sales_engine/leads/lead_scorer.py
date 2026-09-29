from backend.schemas import CompanyLead

SCORING_CONFIG = {
    "first_relevant_job": 10,
    "second_relevant_job": 5,
    "additional_relevant_jobs": 5,
    "leadership_role": 10,
    "technology_match": 5,
    "max_technology_score": 15,
    "recent_7_days": 20,
    "recent_14_days": 15,
    "recent_30_days": 10,
    "older_unknown": 5,
    "evidence_base_score": 2,
    "max_evidence_score": 20
}

def score_lead(lead: CompanyLead, min_qualified_score: int = 60) -> CompanyLead:
    reasons = []
    
    # 1. Fit Score (0-30)
    fit_score = 0
    if lead.relevant_job_count > 0:
        fit_score += 10
        reasons.append("Has relevant job postings (+10)")
        
    tech_count = len(lead.technologies)
    if tech_count > 0:
        tech_score = min(tech_count * SCORING_CONFIG["technology_match"], SCORING_CONFIG["max_technology_score"])
        fit_score += tech_score
        reasons.append(f"{tech_count} relevant technologies detected (+{tech_score})")
        
    if lead.signals:
        fit_score += 5
        reasons.append("Company has strong signals (+5)")
        
    fit_score = min(max(fit_score, 0), 30)
    lead.fit_score = fit_score
    
    # 2. Intent Score (0-30)
    intent_score = 0
    if lead.relevant_job_count >= 1:
        intent_score += SCORING_CONFIG["first_relevant_job"]
        reasons.append("First relevant hiring signal detected (+10)")
    if lead.relevant_job_count >= 2:
        intent_score += SCORING_CONFIG["second_relevant_job"]
        reasons.append("Second relevant hiring signal detected (+5)")
    if lead.relevant_job_count >= 3:
        bonus = min((lead.relevant_job_count - 2) * SCORING_CONFIG["additional_relevant_jobs"], 5)
        intent_score += bonus
        reasons.append(f"Multiple relevant hiring signals detected (+{bonus})")
        
    leadership_keywords = ["manager", "lead", "director", "head", "principal"]
    has_leadership = any(any(kw in title.lower() for kw in leadership_keywords) for title in lead.job_titles)
    if has_leadership:
        intent_score += SCORING_CONFIG["leadership_role"]
        reasons.append("Leadership/manager role found (+10)")
        
    intent_score = min(max(intent_score, 0), 30)
    lead.intent_score = intent_score
    
    # 3. Recency Score (0-20)
    recency_score = SCORING_CONFIG["older_unknown"]
    if lead.recent_jobs_7d > 0:
        recency_score = SCORING_CONFIG["recent_7_days"]
        reasons.append(f"Relevant jobs posted within 7 days (+{recency_score})")
    elif lead.recent_jobs_14d > 0:
        recency_score = SCORING_CONFIG["recent_14_days"]
        reasons.append(f"Relevant jobs posted within 14 days (+{recency_score})")
    elif lead.recent_jobs_30d > 0:
        recency_score = SCORING_CONFIG["recent_30_days"]
        reasons.append(f"Relevant jobs posted within 30 days (+{recency_score})")
    else:
        reasons.append(f"Older/unknown job dates (+{recency_score})")
            
    recency_score = min(max(recency_score, 0), 20)
    lead.recency_score = recency_score
    
    # 4. Evidence Score (0-20)
    evidence_score = min(len(lead.evidence) * SCORING_CONFIG["evidence_base_score"], SCORING_CONFIG["max_evidence_score"])
    if evidence_score > 0:
        reasons.append(f"{len(lead.evidence)} unique pieces of evidence (+{evidence_score})")
    
    lead.evidence_score = min(max(evidence_score, 0), 20)
    
    # Total Score
    lead.lead_score = lead.fit_score + lead.intent_score + lead.recency_score + lead.evidence_score
    # Ensure it's between 0-100
    lead.lead_score = min(max(lead.lead_score, 0), 100)
    
    lead.scoring_reasons = reasons
    lead.qualified = lead.lead_score >= min_qualified_score
    
    return lead
