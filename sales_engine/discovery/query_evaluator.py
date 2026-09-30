import re
from typing import List, Dict, Any, Tuple
from backend.schemas import GeneratedQuery

FORBIDDEN_GOAL_PREFIXES = [
    "identify", "evaluate", "reduce", "deploy", "optim", "eliminat", "streamlin", "scal"
]

GENERIC_NOISE_TERMS = {
    "engineering jobs", "construction careers", "ai jobs", "bim company",
    "engineering careers", "construction jobs", "hiring engineers"
}

ATS_DOMAINS = {
    "lever": "jobs.lever.co",
    "greenhouse": "boards.greenhouse.io",
    "ashby": "jobs.ashbyhq.com"
}

def normalize_query_for_qa(query: str) -> str:
    """Normalize query for duplicate detection (case, punctuation, filler words, whitespace)."""
    q = query.lower()
    q = re.sub(r'["\']', '', q)
    # Remove filler words
    q = re.sub(r'\b(in|at|for|the|and|or|of|a|an)\b', ' ', q)
    q = re.sub(r'\s+', ' ', q).strip()
    return q

def detect_forbidden_patterns(
    query: str,
    approved_primary_signals: List[str],
    buyer_roles: List[str],
    secondary_signals: List[str]
) -> List[str]:
    """Detect forbidden semantic patterns according to Phase 3 QA specifications."""
    reasons = []
    q_lower = query.lower()
    
    # 1. Business goals used as job titles (e.g. "Identify operational bottlenecks jobs", "Reduce operational costs careers")
    for prefix in FORBIDDEN_GOAL_PREFIXES:
        if re.search(rf"\b{prefix}[a-z]*\s+(operational|bottleneck|cost|automation|infrastructure|roi)\b", q_lower):
            reasons.append(f"Business goal used as job title: '{query}'")
            break
            
    # 2. Standalone secondary signals treated as jobs without a role (e.g. "COBie jobs", "IFC company", "Navisworks hiring")
    has_role = any(p.lower() in q_lower for p in approved_primary_signals)
    if not has_role:
        for sec in secondary_signals:
            sec_clean = sec.lower().strip()
            # If query is just "{tech} jobs/careers/hiring/company" without any role
            pattern = rf'^\s*"?{re.escape(sec_clean)}"?\s*(jobs?|careers?|hiring|company|companies)?\s*$'
            if re.search(pattern, q_lower):
                reasons.append(f"Standalone technology query without role: '{query}'")
                break
                
    # 3. Buyer-role pollution: buyer role used as job target without being in approved primary signals
    norm_approved = {re.sub(r'[^a-z0-9]', '', p.lower()) for p in approved_primary_signals}
    unapproved_buyer_roles = [
        r for r in buyer_roles
        if re.sub(r'[^a-z0-9]', '', r.lower()) not in norm_approved
    ]
    for ubr in unapproved_buyer_roles:
        ubr_clean = ubr.lower().strip()
        # If query contains this unapproved buyer role
        if ubr_clean and ubr_clean in q_lower:
            reasons.append(f"Buyer-role pollution (buyer role '{ubr}' used as hiring target): '{query}'")
            break
            
    # 4. Generic noise
    q_stripped = re.sub(r'["\']', '', q_lower).strip()
    if q_stripped in GENERIC_NOISE_TERMS:
        reasons.append(f"Generic noisy query without specific role intent: '{query}'")
        
    return reasons

def compute_discovery_metrics(
    queries: List[GeneratedQuery],
    approved_primary_signals: List[str],
    requested_countries: List[str],
    buyer_roles: List[str],
    secondary_signals: List[str]
) -> Dict[str, Any]:
    """Calculate all Phase 3 required acceptance metrics."""
    total_queries = len(queries)
    normalized_map = {}
    duplicates = []
    
    for q in queries:
        norm = normalize_query_for_qa(q.query)
        if norm in normalized_map:
            duplicates.append(q.query)
        else:
            normalized_map[norm] = q.query
            
    unique_queries = len(normalized_map)
    duplicate_queries = len(duplicates)
    
    # Primary signals covered
    primary_covered = []
    for sig in approved_primary_signals:
        sig_lower = sig.lower()
        if any(sig_lower in q.query.lower() for q in queries):
            primary_covered.append(sig)
    primary_covered = list(dict.fromkeys(primary_covered))
    
    # Countries covered
    countries_covered = []
    for c in requested_countries:
        c_lower = c.lower()
        c_aliases = [c_lower]
        if c_lower == "united kingdom":
            c_aliases.append("uk")
        if any(any(alias in q.query.lower() for alias in c_aliases) for q in queries):
            countries_covered.append(c)
    countries_covered = list(dict.fromkeys(countries_covered))
    
    # ATS providers covered
    ats_providers = ["lever", "greenhouse", "ashby"]
    ats_covered = []
    for ats in ats_providers:
        domain = ATS_DOMAINS[ats]
        if any(domain in q.query.lower() or ats in q.query.lower() for q in queries):
            ats_covered.append(ats)
    ats_covered = list(dict.fromkeys(ats_covered))
    
    # Career intent present
    career_intent_present = any(
        any(w in q.query.lower() for w in ["career", "careers", "jobs", "work-with-us", "join-us"])
        for q in queries if q.type in ["company_career", "general_job"]
    )
    
    # Secondary supported query count
    sec_supported_count = 0
    for q in queries:
        has_primary = any(p.lower() in q.query.lower() for p in approved_primary_signals)
        has_sec = any(s.lower() in q.query.lower() for s in secondary_signals)
        if has_primary and has_sec:
            sec_supported_count += 1
            
    # Forbidden pattern detection
    forbidden_issues = []
    for q in queries:
        issues = detect_forbidden_patterns(q.query, approved_primary_signals, buyer_roles, secondary_signals)
        forbidden_issues.extend(issues)
        
    return {
        "total_queries": total_queries,
        "unique_queries": unique_queries,
        "duplicate_queries": duplicate_queries,
        "duplicate_list": duplicates,
        "primary_signals_covered": primary_covered,
        "primary_signals_covered_count": len(primary_covered),
        "countries_requested": requested_countries,
        "countries_covered": countries_covered,
        "ats_providers_covered": ats_covered,
        "career_intent_present": career_intent_present,
        "secondary_supported_query_count": sec_supported_count,
        "forbidden_pattern_count": len(forbidden_issues),
        "forbidden_patterns": forbidden_issues
    }

def evaluate_discovery_acceptance(
    metrics: Dict[str, Any],
    requested_countries: List[str]
) -> Tuple[str, List[str], str]:
    """Evaluate metrics against Phase 3 acceptance criteria."""
    diff_lines = []
    missing = []
    semantic_review = []
    
    # Critical FAIL checks
    if metrics["forbidden_pattern_count"] > 0:
        for fp in metrics["forbidden_patterns"]:
            semantic_review.append(f"Forbidden semantic pattern: {fp}")
            
    if metrics["duplicate_queries"] > 0:
        semantic_review.append(f"Duplicate query explosion detected: {metrics['duplicate_queries']} duplicates: {metrics['duplicate_list']}")
        
    is_critical_fail = len(semantic_review) > 0
    
    # Coverage requirements
    if metrics["primary_signals_covered_count"] < 3:
        missing.append(f"primary_signals_covered: expected >= 3, got {metrics['primary_signals_covered_count']}")
        
    missing_countries = [c for c in requested_countries if c not in metrics["countries_covered"]]
    if missing_countries:
        missing.append(f"countries_covered: missing requested countries {missing_countries}")
        
    if len(metrics["ats_providers_covered"]) < 2:
        missing.append(f"ats_providers_covered: expected >= 2, got {len(metrics['ats_providers_covered'])} ({metrics['ats_providers_covered']})")
        
    if not metrics["career_intent_present"]:
        missing.append("career_intent: at least one company career query required")
        
    if missing:
        diff_lines.append("**Missing / Incomplete Coverage:**")
        diff_lines.extend([f"- {m}" for m in missing])
        
    if semantic_review:
        diff_lines.append("**Critical Semantic Issues:**")
        diff_lines.extend([f"- {sr}" for sr in semantic_review])
        
    if is_critical_fail:
        status = "FAIL"
        reason = f"Critical Phase 3 semantic failure: {'; '.join(semantic_review)}"
    elif missing:
        status = "REVIEW"
        reason = f"Phase 3 intent coverage incomplete: {'; '.join(missing)}"
    else:
        status = "PASS"
        reason = "All Phase 3 critical requirements and intent coverage targets satisfied."
        
    return status, diff_lines, reason
