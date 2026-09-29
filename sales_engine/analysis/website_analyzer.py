import os
import re
import json
from typing import Dict, Any, List
from backend.schemas import WebsiteProfile
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

ACTION_VERBS = {
    "identify", "evaluate", "reduce", "increase", "ensure", "deploy", "cut",
    "eliminate", "optimize", "streamline", "deliver", "manage", "scale",
    "replace", "transition", "transform", "drive", "build", "maintain",
    "accelerate", "bridge", "delegate", "provide", "improve", "support"
}

ROLE_KEYWORDS = {
    "manager", "lead", "director", "head", "engineer", "developer", "specialist",
    "coordinator", "architect", "consultant", "analyst", "officer", "administrator"
}

INVERTED_NEGATIVE_KEYWORDS = {
    "manual", "mapping", "clash", "tender", "mandate", "outdated", "inefficiency",
    "error", "compliance", "iso", "headcount", "bottleneck", "drain", "unstructured"
}

class WebsiteAnalyzer:
    def __init__(self, timeout: float = 300.0):
        self.llm_client = BIMLLMClient(timeout=timeout)
        self.model_name = os.getenv("SALES_LLM_MODEL", "qwen2.5:1.5b")
        
    def _sanitize_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply deterministic semantic sanity checks to guard against common LLM extraction errors."""
        # 1. Clean up summary prestige adjectives
        summary = data.get("company_summary") or ""
        summary = re.sub(r"\bis a leading provider of\b", "provides", summary, flags=re.IGNORECASE)
        summary = re.sub(r"\bis a top-tier provider of\b", "provides", summary, flags=re.IGNORECASE)
        summary = re.sub(r"\bis a premier provider of\b", "provides", summary, flags=re.IGNORECASE)
        data["company_summary"] = summary

        # 2. Sanitize primary_job_signals: MUST be job titles / roles, NOT business activities
        primary = data.get("primary_job_signals") or []
        sanitized_primary = []
        rejected_actions = []

        for item in primary:
            item_clean = item.strip()
            first_word = item_clean.split()[0].lower() if item_clean else ""
            
            # Check if this item is a business action / verb phrase rather than a job role
            is_action = first_word in ACTION_VERBS
            has_role_keyword = any(rk in item_clean.lower() for rk in ROLE_KEYWORDS)

            if is_action and not has_role_keyword:
                rejected_actions.append(item_clean)
            else:
                sanitized_primary.append(item_clean)

        # If primary job signals became empty due to filtering out pure action phrases,
        # fallback to suitable titles from buyer_roles or keywords that are real roles
        if not sanitized_primary:
            for br in data.get("buyer_roles") or []:
                if any(rk in br.lower() for rk in ROLE_KEYWORDS) and br not in sanitized_primary:
                    sanitized_primary.append(br)

        data["primary_job_signals"] = sanitized_primary

        # 3. Sanitize negative_signals: Fix semantic inversion
        negatives = data.get("negative_signals") or []
        sanitized_negatives = []
        pain_points = set(data.get("pain_points") or [])

        for item in negatives:
            item_clean = item.strip()
            # If negative signal contains customer pain point or tender mandate keywords, it's an inverted buying reason!
            if any(kw in item_clean.lower() for kw in INVERTED_NEGATIVE_KEYWORDS):
                # Relocate to pain points instead of disqualifiers
                pain_points.add(item_clean)
            else:
                sanitized_negatives.append(item_clean)

        data["negative_signals"] = sanitized_negatives
        data["pain_points"] = list(pain_points)

        return data

    async def analyze_website(self, fetch_result: Dict[str, Any]) -> WebsiteProfile:
        print("[SalesAI Analysis] LLM analysis started")
        
        content_blocks = []
        for idx, page in enumerate(fetch_result.get("pages", [])):
            content_blocks.append(f"PAGE {idx + 1} ({page['url']}):\n{page['text']}")
            
        full_content = "\n\n".join(content_blocks)
        
        system_prompt = """You are an expert B2B sales intelligence researcher.
Your task is to analyze the provided B2B company website content and extract structured intelligence.

CRITICAL FIELD DEFINITIONS & RULES:
1. company_name: Official name of the company.
2. company_summary: Objective factual summary of what the company provides. Do NOT invent subjective hype or prestige words (e.g. avoid "leading provider", "world-class", "top"). Prefer factual phrasing: "[Company] provides...".
3. services: Core business services and offerings actually provided.
4. target_industries: Real industries served (e.g. Architecture, Construction, Infrastructure).
5. target_company_types: Types of organizations that hire or buy from this company (e.g. Engineering firms, General contractors).
6. pain_points: Business problems, bottlenecks, errors, or operational headaches that client companies experience (e.g. manual data entry, clash coordination delays, high overhead).
7. buyer_roles: Professional decision-makers, budget holders, or project leaders who would purchase or sponsor these services. MUST be grounded in explicit website evidence (such as job titles mentioned in case studies, testimonials, team, or client quotes). Do NOT invent generic titles like "Engineering Manager" or "Project Manager" unless explicitly present in the text. Prefer specific leadership titles (e.g. "Head of Digital Delivery", "BIM Manager", "Technical Director").
8. primary_job_signals: Specific JOB TITLES or HIRING ROLES that a prospect company would hire for that indicates strong need for these services.
   - MANDATORY: Each item MUST be a plausible JOB TITLE / ROLE (e.g., "BIM Manager", "Revit API Developer", "Digital Delivery Manager", "Automation Engineer").
   - NEVER output business activities, actions, or goals (e.g. DO NOT write "Identify bottlenecks", "Evaluate ROI", "Deploy AI", "Reduce costs").
9. secondary_job_signals: Software platforms, technologies, tools, technical standards, or certifications associated with demand (e.g. "Navisworks", "Solibri", "IFC", "COBie", "Revit API", "ISO 19650").
10. keywords: Specific search keywords for finding relevant companies and hiring postings.
11. negative_signals: Signals indicating that a company or job is NOT a suitable prospect (e.g. "student-only", "consumer-only", "recruiting agency", "internship", "residential-only", "no AEC/BIM activity").
    - CRITICAL RULE: Client pain points (e.g. "manual parameter mapping"), tender requirements, or compliance standards are BUYING REASONS, NEVER negative signals.

Do not invent facts. Use only website evidence.
If information is unavailable return null or [].
Return valid JSON only matching the schema exactly.
"""
        
        prompt = f"{system_prompt}\n\nWEBSITE CONTENT:\n{full_content}"
        
        req = CodeGenerationRequest(
            user_prompt=prompt,
            environment="sales",
            language="json",
            num_ctx=8192
        )
        
        for attempt in range(2):
            try:
                if attempt == 1:
                    req.user_prompt += """
                    
IMPORTANT JSON REPAIR:
Return ONLY one valid JSON object.
No markdown.
No ```json fences.
No explanation.

Required keys:
company_name
company_summary
services
target_industries
target_company_types
pain_points
buyer_roles
primary_job_signals
secondary_job_signals
keywords
negative_signals
"""
                response = await self.llm_client.generate_code_async(req, model_name=self.model_name)
                json_text = response.extracted_code.strip()
                
                # Attempt to parse json from markdown block if necessary
                if "```json" in json_text:
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif "```" in json_text:
                    json_text = json_text.split("```")[1].split("```")[0].strip()
                    
                data = json.loads(json_text)
                
                # Apply deterministic semantic sanity checks
                data = self._sanitize_profile(data)
                
                profile = WebsiteProfile(**data)
                print("[SalesAI Analysis] LLM analysis completed and sanitized")
                return profile
            except Exception as e:
                print(f"[SalesAI Analysis] Attempt {attempt+1} failed: {e}")
                if attempt == 1:
                    raise ValueError(f"Failed to parse LLM response into WebsiteProfile: {e}")
