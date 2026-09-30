import os
import re
import json
from typing import List, Set, Tuple, Dict, Any, Optional
from backend.schemas import WebsiteProfile, GeneratedQueriesResponse, GeneratedQuery
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
from sales_engine.analysis.website_analyzer import is_valid_job_title
from sales_engine.discovery.query_evaluator import (
    normalize_query_for_qa,
    detect_forbidden_patterns,
    ATS_DOMAINS
)

class QueryGenerator:
    def __init__(self, max_queries: int = 20):
        self.llm_client = BIMLLMClient(timeout=180.0)
        self.max_queries = max(10, min(max_queries, 25))
        self.model_name = os.getenv("SALES_LLM_MODEL", "qwen2.5:1.5b")
        
    def _sanitize_signals(self, profile: WebsiteProfile) -> Tuple[List[str], List[str]]:
        """Filter out business goals, action verbs, and standalone tech from primary signals."""
        valid_primary = []
        seen_primary: Set[str] = set()
        
        for sig in profile.primary_job_signals:
            sig_clean = sig.strip()
            if not sig_clean:
                continue
            # Must be structurally valid job title, not an action verb / business goal
            if not is_valid_job_title(sig_clean):
                continue
            # Additional check: reject obvious business goals
            words = re.findall(r"\b[a-zA-Z]+\b", sig_clean.lower())
            if words and words[0] in {"reduce", "identify", "evaluate", "deploy", "optimize", "eliminate", "streamline"}:
                continue
            norm = sig_clean.lower()
            if norm not in seen_primary:
                seen_primary.add(norm)
                valid_primary.append(sig_clean)
                
        # Clean secondary signals (technologies & standards)
        valid_secondary = []
        seen_sec: Set[str] = set()
        for sec in profile.secondary_job_signals:
            sec_clean = sec.strip()
            if not sec_clean:
                continue
            norm = sec_clean.lower()
            if norm not in seen_sec:
                seen_sec.add(norm)
                valid_secondary.append(sec_clean)
                
        return valid_primary, valid_secondary

    def _build_intent_queries(
        self,
        primary_signals: List[str],
        secondary_signals: List[str],
        countries: List[str],
        industries: List[str]
    ) -> List[GeneratedQuery]:
        """Synthesize high-fidelity queries ensuring comprehensive intent and geography coverage."""
        queries: List[GeneratedQuery] = []
        if not primary_signals:
            return queries

        p1 = primary_signals[0]
        p2 = primary_signals[1] if len(primary_signals) > 1 else p1
        p3 = primary_signals[2] if len(primary_signals) > 2 else p1
        p_list = primary_signals[:min(len(primary_signals), 6)]

        ind1 = industries[0] if industries else "engineering"
        ind2 = industries[1] if len(industries) > 1 else "AEC"

        # 1. ATS Discovery Queries (Lever, Greenhouse, Ashby)
        queries.append(GeneratedQuery(
            query=f'site:{ATS_DOMAINS["lever"]} "{p1}"',
            type="ats_targeted",
            priority=10
        ))
        queries.append(GeneratedQuery(
            query=f'site:{ATS_DOMAINS["greenhouse"]} "{p2}"',
            type="ats_targeted",
            priority=10
        ))
        queries.append(GeneratedQuery(
            query=f'site:{ATS_DOMAINS["ashby"]} "{p3}"',
            type="ats_targeted",
            priority=9
        ))

        # 2. Geography Coverage Queries (Direct primary hiring across requested countries)
        for i, country in enumerate(countries):
            role = p_list[i % len(p_list)]
            queries.append(GeneratedQuery(
                query=f'"{role}" jobs {country}',
                type="general_job",
                priority=9 - i
            ))
            if len(p_list) > 1:
                alt_role = p_list[(i + 1) % len(p_list)]
                queries.append(GeneratedQuery(
                    query=f'"{alt_role}" hiring {country}',
                    type="general_job",
                    priority=8 - i
                ))

        # 3. Company Career Intent Queries (Targeting company-owned career pages)
        queries.append(GeneratedQuery(
            query=f'"{p1}" careers {ind1}',
            type="company_career",
            priority=8
        ))
        queries.append(GeneratedQuery(
            query=f'"{p2}" careers {ind2}',
            type="company_career",
            priority=8
        ))
        if countries:
            queries.append(GeneratedQuery(
                query=f'"{p3}" careers {ind1} {countries[0]}',
                type="company_career",
                priority=7
            ))

        # 4. Secondary Technology Support Queries (Roles combined with technologies/standards)
        if secondary_signals:
            s1 = secondary_signals[0]
            s2 = secondary_signals[1] if len(secondary_signals) > 1 else s1
            s3 = secondary_signals[2] if len(secondary_signals) > 2 else s1
            queries.append(GeneratedQuery(
                query=f'"{p1}" {s1} {s2}',
                type="keyword_signal",
                priority=7
            ))
            queries.append(GeneratedQuery(
                query=f'"{p2}" {s3}',
                type="keyword_signal",
                priority=7
            ))
            if len(primary_signals) > 3:
                p4 = primary_signals[3]
                queries.append(GeneratedQuery(
                    query=f'"{p4}" {s1}',
                    type="keyword_signal",
                    priority=6
                ))

        return queries

    def _sanitize_and_balance_queries(
        self,
        raw_queries: List[GeneratedQuery],
        approved_primary: List[str],
        secondary_signals: List[str],
        countries: List[str],
        industries: List[str],
        buyer_roles: List[str]
    ) -> List[GeneratedQuery]:
        """Deduplicate, filter forbidden patterns, and ensure complete intent coverage within budget."""
        seen_normalized: Set[str] = set()
        sanitized: List[GeneratedQuery] = []

        norm_approved_roles = {re.sub(r'[^a-z0-9]', '', p.lower()) for p in approved_primary}
        unapproved_buyer_roles = [
            r for r in buyer_roles
            if re.sub(r'[^a-z0-9]', '', r.lower()) not in norm_approved_roles
        ]

        # 1. Filter raw queries from LLM or synthesizer
        for q in raw_queries:
            # Check forbidden patterns
            issues = detect_forbidden_patterns(q.query, approved_primary, buyer_roles, secondary_signals)
            if issues:
                continue

            # Ensure queries of hiring/ATS/keyword types contain at least one approved primary signal
            if q.type in ["general_job", "ats_targeted", "keyword_signal"]:
                if not any(p.lower() in q.query.lower() for p in approved_primary):
                    continue

            # Ensure no unapproved buyer role leaks into ANY query
            if any(ubr.lower() in q.query.lower() for ubr in unapproved_buyer_roles):
                continue
                
            norm = normalize_query_for_qa(q.query)
            if norm not in seen_normalized:
                seen_normalized.add(norm)
                sanitized.append(q)

        # 2. Check if intent coverage needs reinforcement
        coverage_queries = self._build_intent_queries(approved_primary, secondary_signals, countries, industries)
        
        # Check ATS coverage
        ats_present = {ats for ats in ["lever", "greenhouse", "ashby"] if any(ats in q.query.lower() for q in sanitized)}
        for cq in coverage_queries:
            if cq.type == "ats_targeted":
                for ats in ["lever", "greenhouse", "ashby"]:
                    if ats in cq.query.lower() and ats not in ats_present:
                        norm = normalize_query_for_qa(cq.query)
                        if norm not in seen_normalized:
                            seen_normalized.add(norm)
                            sanitized.append(cq)
                            ats_present.add(ats)

        # Check Country coverage
        for country in countries:
            c_aliases = [country.lower(), "uk"] if country.lower() == "united kingdom" else [country.lower()]
            has_country = any(any(alias in q.query.lower() for alias in c_aliases) for q in sanitized)
            if not has_country:
                for cq in coverage_queries:
                    if any(alias in cq.query.lower() for alias in c_aliases):
                        norm = normalize_query_for_qa(cq.query)
                        if norm not in seen_normalized:
                            seen_normalized.add(norm)
                            sanitized.append(cq)
                            break

        # Check Career intent
        has_career = any(
            any(w in q.query.lower() for w in ["career", "careers", "jobs", "work-with-us", "join-us"])
            for q in sanitized if q.type in ["company_career", "general_job"]
        )
        if not has_career:
            for cq in coverage_queries:
                if cq.type == "company_career":
                    norm = normalize_query_for_qa(cq.query)
                    if norm not in seen_normalized:
                        seen_normalized.add(norm)
                        sanitized.append(cq)
                        break

        # Check Secondary technology support
        has_sec = any(
            any(p.lower() in q.query.lower() for p in approved_primary) and
            any(s.lower() in q.query.lower() for s in secondary_signals)
            for q in sanitized
        )
        if not has_sec:
            for cq in coverage_queries:
                if cq.type == "keyword_signal":
                    norm = normalize_query_for_qa(cq.query)
                    if norm not in seen_normalized:
                        seen_normalized.add(norm)
                        sanitized.append(cq)
                        break

        # Sort by priority descending
        sanitized.sort(key=lambda q: q.priority, reverse=True)
        return sanitized[:self.max_queries]

    async def generate_queries(self, profile: WebsiteProfile, countries: List[str]) -> GeneratedQueriesResponse:
        """Generate structured B2B discovery queries with strict semantic intent coverage."""
        valid_primary, valid_secondary = self._sanitize_signals(profile)
        
        # Guard: If no valid primary job signals exist, return controlled empty result
        if not valid_primary:
            return GeneratedQueriesResponse(queries=[])

        system_prompt = f"""You are a B2B sales discovery engine.
Your task is to generate search queries to find hiring signals and prospective client companies.

VALID PRIMARY JOB SIGNALS (Hiring Targets):
{json.dumps(valid_primary, ensure_ascii=False)}

SECONDARY TECHNOLOGIES & STANDARDS:
{json.dumps(valid_secondary, ensure_ascii=False)}

TARGET COUNTRIES:
{json.dumps(countries, ensure_ascii=False)}

TARGET INDUSTRIES / FIRM TYPES:
{json.dumps(profile.target_industries + profile.target_company_types, ensure_ascii=False)}

MANDATORY RULES:
1. Generate between 12 and 18 high-precision queries.
2. Query types:
   - ats_targeted: Query specific ATS platforms using site: operator (e.g. site:jobs.lever.co "BIM Manager", site:boards.greenhouse.io "Revit API Developer", site:jobs.ashbyhq.com "Digital Delivery Manager")
   - general_job: Query hiring in target countries (e.g. "BIM Manager" jobs Italy, "Revit API Developer" hiring Germany)
   - company_career: Query career pages of target firms (e.g. "BIM Manager" careers engineering, "Digital Delivery Manager" careers architecture)
   - keyword_signal: Combine a primary job role with secondary technologies (e.g. "BIM Automation Engineer" Python Revit, "BIM Information Manager" COBie). NEVER search standalone technologies like "COBie jobs".
3. NEVER use business goals or action verbs as job titles (DO NOT write "Reduce...", "Identify...", "Deploy...").
4. NEVER invent arbitrary roles. Use the provided PRIMARY JOB SIGNALS.

Return ONLY valid JSON matching this schema:
{{
  "queries": [
    {{
      "query": "string",
      "type": "general_job" | "company_career" | "ats_targeted" | "keyword_signal",
      "priority": integer (1-10)
    }}
  ]
}}"""
        
        # Generate foundational intent-balanced queries
        raw_queries: List[GeneratedQuery] = self._build_intent_queries(
            valid_primary, valid_secondary, countries, profile.target_industries
        )

        use_llm = os.getenv("DISCOVERY_USE_LLM", "false").lower() in ("true", "1")
        if use_llm:
            try:
                req = CodeGenerationRequest(
                    user_prompt=system_prompt,
                    environment="sales",
                    language="json",
                    num_ctx=2048
                )
                response = await self.llm_client.generate_code_async(req, model_name=self.model_name)
                json_text = response.extracted_code.strip()
                if "```json" in json_text:
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif "```" in json_text:
                    json_text = json_text.split("```")[1].split("```")[0].strip()
                data = json.loads(json_text)
                llm_queries = [GeneratedQuery(**item) for item in data.get("queries", [])]
                if llm_queries:
                    raw_queries = llm_queries + raw_queries
            except Exception as e:
                print(f"[QueryGenerator] LLM generation skipped/failed ({e}), using intent queries.")

        # Sanitize, balance, and deduplicate
        final_queries = self._sanitize_and_balance_queries(
            raw_queries,
            valid_primary,
            valid_secondary,
            countries,
            profile.target_industries,
            profile.buyer_roles
        )

        return GeneratedQueriesResponse(queries=final_queries)
