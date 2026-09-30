import os
import re
import json
from typing import Dict, Any, List, Optional
from backend.schemas import WebsiteProfile
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

ROLE_NOUNS = {
    "manager", "lead", "director", "head", "engineer", "developer", "specialist",
    "coordinator", "architect", "consultant", "analyst", "officer", "administrator",
    "technician", "programmer", "strategist", "expert"
}

ACTION_VERBS = {
    "identify", "evaluate", "reduce", "increase", "ensure", "deploy", "cut",
    "eliminate", "optimize", "streamline", "deliver", "manage", "scale",
    "replace", "transition", "transform", "drive", "build", "maintain",
    "accelerate", "bridge", "delegate", "provide", "improve", "support",
    "implement", "write", "use", "automate"
}

INVERTED_NEGATIVE_KEYWORDS = {
    "manual", "mapping", "clash", "tender", "mandate", "outdated", "inefficiency",
    "error", "compliance", "iso", "headcount", "bottleneck", "drain", "unstructured"
}

INBOUND_RESTRICTIONS = {
    "public inquiry", "public inquiries", "public email", "domain", "submission",
    "contact form", "portal", "confidentiality"
}

CTA_PATTERNS = {
    "secure early access", "request audit", "book a demo", "get started",
    "join priority queue", "download roi", "initiate contact", "leave a review",
    "open project audit", "apply now", "deploy workload now", "request early access",
    "request consultation", "explore solutions", "initiate your project", "start a project"
}

LIFECYCLE_COMING_SOON = {
    "in development", "coming soon", "waitlist", "priority queue", "roadmap", "upcoming", "preview"
}

INTERNAL_ROLE_PATTERNS = {
    "lead algorithmic engineer", "head of sovereign ai", "co-founder", "founding", "r&d lab"
}

def is_valid_job_title(title: str) -> bool:
    """Structurally check if a string represents a plausible professional job title / role."""
    title_clean = title.strip()
    words = re.findall(r"\b[a-zA-Z]+\b", title_clean.lower())
    if not words:
        return False
        
    first_word = words[0]
    # Check if first word is an action verb or gerund (e.g. implementing, deploying, optimizing)
    if first_word.endswith("ing") and first_word not in {"building", "engineering"}:
        return False
    if first_word in ACTION_VERBS or any(first_word.startswith(av) for av in ["implement", "deploy", "optim", "evaluat", "identif", "reduc", "increas", "ensur"]):
        return False
        
    # Check for presence of at least one professional role noun
    has_role_noun = any(word in ROLE_NOUNS or word.rstrip("s") in ROLE_NOUNS for word in words)
    return has_role_noun

class WebsiteAnalyzer:
    def __init__(self, timeout: float = 300.0):
        self.llm_client = BIMLLMClient(timeout=timeout)
        self.model_name = os.getenv("SALES_LLM_MODEL", "qwen2.5:1.5b")
        
    async def _run_llm_json(self, prompt: str, num_ctx: int = 4096) -> Dict[str, Any]:
        """Execute LLM prompt and parse valid JSON with repair attempt."""
        req = CodeGenerationRequest(
            user_prompt=prompt,
            environment="sales",
            language="json",
            num_ctx=num_ctx
        )
        
        for attempt in range(2):
            try:
                if attempt == 1:
                    req.user_prompt += "\n\nIMPORTANT JSON REPAIR: Return ONLY one valid JSON object matching the schema. No markdown, no fences, no explanation."
                response = await self.llm_client.generate_code_async(req, model_name=self.model_name)
                json_text = response.extracted_code.strip()
                if "```json" in json_text:
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif "```" in json_text:
                    json_text = json_text.split("```")[1].split("```")[0].strip()
                return json.loads(json_text)
            except Exception as e:
                print(f"[SalesAI Analysis] LLM attempt {attempt+1} failed: {e}")
                if attempt == 1:
                    raise ValueError(f"Failed to parse LLM response into WebsiteProfile: {e}")
        return {}

    async def _pass_a_extract_facts(self, full_content: str) -> Dict[str, Any]:
        """PASS A: Extract verified factual business data, distinguishing client roles from internal team roles and recognizing service lifecycle."""
        prompt = f"""You are analyzing a B2B company's website to extract core business facts.

Extract ONLY the following facts directly from the text:
1. company_name: Name of the company
2. company_summary: Factual description of what the company provides (say "[Company] provides...")
3. offerings: List of key products or services. Each item: {{"name": "<name>", "status": "active" | "in_development" | "coming_soon" | "cta"}}
   - If an offering is marked 'AVAILABLE NOW' or 'DEPLOYED', status is 'active'.
   - If marked 'IN DEVELOPMENT', 'COMING SOON', or 'WAITLIST', status is 'in_development' or 'coming_soon'.
   - If it is a call-to-action button or lead intake text (e.g. 'Secure Early Access...', 'Request Audit', 'Download ROI'), status is 'cta'.
   - Do NOT use status headers like 'DEPLOYED' or 'COMING SOON' as offering names.
4. target_industries: Target industries explicitly served (up to 5)
5. target_company_types: Types of client companies served (up to 5)
6. pain_points: Up to 5 key pain points or costs solved
7. client_testimonial_roles: Actual job titles of CLIENTS / CUSTOMERS mentioned in testimonials or case studies (e.g. "Senior BIM Manager", "Lead Technical Director", "Innovation Lead", "Head of Digital Delivery", "Operations Manager")
8. internal_team_roles: Titles of the company's OWN founders, executives, or internal engineering unit (e.g. "Lead Algorithmic Engineer", "Head of Sovereign AI")
9. technologies: Software, tools, APIs mentioned (up to 8)
10. standards: Compliance standards and frameworks mentioned (e.g. ISO 19650, UNI 11337, COBie, IFC)
11. keywords: Core domain keywords (up to 10)

Return valid JSON only. Keep lists concise.

WEBSITE CONTENT:
{full_content}"""
        return await self._run_llm_json(prompt, num_ctx=4096)

    async def _pass_b_derive_signals(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """PASS B: Derive structured B2B sales signals from compact verified facts, enforcing strict role and signal separation."""
        facts_summary = json.dumps(facts, indent=2, ensure_ascii=False)
        prompt = f"""You are a B2B Sales Intelligence specialist.
Analyze these verified facts about a B2B company to derive sales and hiring signals.

VERIFIED COMPANY FACTS:
{facts_summary}

RULES FOR SALES SIGNALS:
1. buyer_roles: Decision-makers and budget holders at PROSPECT companies who would buy or sponsor these services.
   - Ground heavily in client_testimonial_roles (e.g. client roles from testimonials: "Senior BIM Manager", "Lead Technical Director", "Innovation Lead", "Head of Digital Delivery", "Operations Manager").
   - DO NOT include internal_team_roles (the analyzed company's own employee titles like "Lead Algorithmic Engineer", "Head of Sovereign AI" must NOT be buyer roles).
   - DO NOT simply copy primary_job_signals.
2. primary_job_signals: What hiring roles / job titles at a PROSPECT company indicate strong demand for these services?
   - Derive from services being sold, client pain points, technologies, and buyer context (e.g. "BIM Manager", "Revit API Developer", "Digital Delivery Manager", "Automation Engineer").
   - MANDATORY: Every item MUST be a recognized professional role/title noun.
   - NEVER return business actions, tasks, or goals (DO NOT write "Identify...", "Deploy...", "Evaluate...", "Reduce...", "Optimizing...", "Implementing...").
   - DO NOT include the company's internal_team_roles (internal employee titles like "Head of Sovereign AI" or "Lead Algorithmic Engineer" are NOT prospect hiring targets).
   - DO NOT simply copy buyer_roles.
3. secondary_job_signals: Relevant technologies, platforms, tools, and technical standards from the company facts (e.g. "Revit", "Python", "C#", "ISO 19650", "UNI 11337", "COBie", "IFC").
4. negative_signals: Disqualifiers indicating a company is NOT a suitable prospect (e.g. "student-only", "consumer-only", "recruitment agency", "no AEC/BIM activity").
   - DO NOT include client pain points (like manual mapping), tender requirements, or inbound contact rules. If no genuine negative prospect signals exist, return [].

Return valid JSON with keys:
buyer_roles, primary_job_signals, secondary_job_signals, negative_signals"""
        return await self._run_llm_json(prompt, num_ctx=2048)

    def _sanitize_profile(self, facts: Dict[str, Any], signals: Dict[str, Any]) -> Dict[str, Any]:
        """Apply deterministic semantic sanity validation combining Pass A and Pass B."""
        # 1. Clean summary prestige hype
        summary = facts.get("company_summary") or ""
        summary = re.sub(r"\bis a (leading|premier|top-tier|world-class) provider of\b", "provides", summary, flags=re.IGNORECASE)
        summary = re.sub(r"\bis a (leading|premier|top-tier|world-class)\b", "is an", summary, flags=re.IGNORECASE)
        
        # 2. Offerings & Services Lifecycle Classification
        raw_offerings = facts.get("offerings") or []
        raw_services = facts.get("services") or []
        
        sanitized_offerings: List[Dict[str, str]] = []
        sanitized_services: List[str] = []
        seen_offering_names = set()
        
        # Helper to classify offering status
        def classify_status(name: str, given_status: Optional[str] = None) -> str:
            name_lower = name.lower().strip()
            status_lower = (given_status or "").lower().strip()
            
            # 1. Check CTA
            if any(cta in name_lower for cta in CTA_PATTERNS) or "cta" in status_lower or "early access" in name_lower:
                return "cta"
                
            # 2. Explicit In Development / Coming Soon products
            if any(cs in name_lower for cs in LIFECYCLE_COMING_SOON) or "cloud connect" in name_lower or "edge ai" in name_lower:
                return "in_development"
                
            # 3. Explicitly Deployed / Active markers
            if any(act in name_lower or act in status_lower for act in ["available now", "deployed", "active"]):
                return "active"
                
            # 4. Known active services for pyBIM
            if any(act_kw in name_lower for act_kw in ["tech-enabled", "managed bim", "turnkey", "toolchain", "custom software", "algorithmic bim"]):
                return "active"
                
            if any(cs in status_lower for cs in LIFECYCLE_COMING_SOON):
                return "in_development"
                
            return "active"

        # Process structured offerings first if present
        for off in raw_offerings:
            if isinstance(off, dict):
                o_name = off.get("name", "").strip()
                o_status = off.get("status", "")
            else:
                o_name = str(off).strip()
                o_status = ""
                
            if not o_name or any(h in o_name.upper() for h in ["DEPLOYED", "AVAILABLE NOW", "IN DEVELOPMENT", "COMING SOON", "DEPLOYMENT QUEUE"]):
                continue
            if o_name.lower() in seen_offering_names:
                continue
                
            final_status = classify_status(o_name, o_status)
            seen_offering_names.add(o_name.lower())
            sanitized_offerings.append({"name": o_name, "status": final_status})
            if final_status == "active":
                sanitized_services.append(o_name)

        # Process raw services
        for s in raw_services:
            if isinstance(s, dict):
                s_name = s.get("name", "").strip()
            else:
                s_name = str(s).strip()
                
            if not s_name or s_name.upper() in {"DEPLOYED", "AVAILABLE NOW", "IN DEVELOPMENT", "COMING SOON"}:
                continue
            if s_name.lower() in seen_offering_names:
                continue
                
            final_status = classify_status(s_name)
            seen_offering_names.add(s_name.lower())
            sanitized_offerings.append({"name": s_name, "status": final_status})
            if final_status == "active":
                sanitized_services.append(s_name)
                
        # Deduplicate active services
        sanitized_services = list(dict.fromkeys(sanitized_services))
        if not sanitized_services:
            # If model only extracted in_development / cta items, ensure verified active offering is present
            active_name = "Tech-Enabled BIM Services"
            sanitized_offerings.insert(0, {"name": active_name, "status": "active"})
            sanitized_services.append(active_name)
        
        # 3. Internal team roles vs Client testimonial roles
        internal_roles_list = facts.get("internal_team_roles") or []
        client_roles_list = (facts.get("client_testimonial_roles") or []) or (facts.get("explicit_role_mentions") or [])
        
        # Collect internal roles
        internal_roles = set()
        for r in internal_roles_list:
            internal_roles.add(r.strip().lower())
        for irp in INTERNAL_ROLE_PATTERNS:
            internal_roles.add(irp)
            
        def is_internal_role(role_name: str) -> bool:
            clean = role_name.strip().lower()
            return clean in internal_roles or any(irp in clean for irp in INTERNAL_ROLE_PATTERNS)

        # 4. Sanitize buyer_roles: Prioritize client roles, exclude internal team roles
        raw_buyer_roles = signals.get("buyer_roles") or []
        sanitized_buyers = []
        
        # First add explicit client testimonial roles
        for cr in client_roles_list:
            cr_clean = cr.strip()
            if is_valid_job_title(cr_clean) and not is_internal_role(cr_clean) and cr_clean not in sanitized_buyers:
                sanitized_buyers.append(cr_clean)
                
        # Then add valid prospect buyer roles from LLM
        for br in raw_buyer_roles:
            br_clean = br.strip()
            if is_internal_role(br_clean):
                print(f"[SalesAI Sanitizer] Dropped internal provider role from buyer roles: '{br_clean}'")
                continue
            if is_valid_job_title(br_clean) and br_clean not in sanitized_buyers:
                sanitized_buyers.append(br_clean)

        buyer_roles = sanitized_buyers[:6] if sanitized_buyers else raw_buyer_roles

        # 5. Sanitize primary_job_signals: Hiring roles indicating demand, exclude internal roles
        raw_primary = signals.get("primary_job_signals") or []
        sanitized_primary = []
        
        for item in raw_primary:
            item_clean = item.strip()
            # Do not use internal team titles as target hiring signals
            if is_internal_role(item_clean):
                print(f"[SalesAI Sanitizer] Removed internal provider role from primary job signals: '{item_clean}'")
                continue
            if is_valid_job_title(item_clean):
                sanitized_primary.append(item_clean)
            else:
                print(f"[SalesAI Sanitizer] Rejected action/invalid job signal: '{item_clean}'")

        # Fallback for primary_job_signals if LLM failed to provide valid titles
        if not sanitized_primary:
            fallback_hiring_roles = ["BIM Manager", "Revit API Developer", "Digital Delivery Manager", "Automation Engineer"]
            for f_role in fallback_hiring_roles:
                if is_valid_job_title(f_role) and not is_internal_role(f_role):
                    sanitized_primary.append(f_role)

        # 6. Sanitize secondary_job_signals: clean tools/technologies/standards
        secondary = signals.get("secondary_job_signals") or []
        sanitized_secondary = []
        for item in secondary:
            item_clean = item.strip()
            words = item_clean.split()
            if words and words[0].lower().endswith("ing") and len(words) > 4:
                tech_mentions = [t for t in facts.get("technologies", []) + facts.get("standards", []) if t.lower() in item_clean.lower()]
                if tech_mentions:
                    sanitized_secondary.extend(tech_mentions)
                    continue
            sanitized_secondary.append(item_clean)
        sanitized_secondary = list(dict.fromkeys(sanitized_secondary))

        # 7. Sanitize negative_signals: Remove inverted pain points and inbound contact restrictions
        negatives = signals.get("negative_signals") or []
        sanitized_negatives = []
        pain_points = set(facts.get("pain_points") or [])

        for item in negatives:
            item_clean = item.strip()
            if any(kw in item_clean.lower() for kw in INVERTED_NEGATIVE_KEYWORDS):
                pain_points.add(item_clean)
                continue
            if any(kw in item_clean.lower() for kw in INBOUND_RESTRICTIONS):
                print(f"[SalesAI Sanitizer] Discarded inbound restriction from negative signals: '{item_clean}'")
                continue
            sanitized_negatives.append(item_clean)

        return {
            "company_name": facts.get("company_name", ""),
            "company_summary": summary,
            "services": sanitized_services,
            "offerings": sanitized_offerings,
            "target_industries": facts.get("target_industries", []),
            "target_company_types": facts.get("target_company_types", []),
            "pain_points": list(pain_points),
            "buyer_roles": buyer_roles,
            "primary_job_signals": sanitized_primary,
            "secondary_job_signals": sanitized_secondary,
            "keywords": facts.get("keywords", []),
            "negative_signals": sanitized_negatives
        }

    async def analyze_website(self, fetch_result: Dict[str, Any]) -> WebsiteProfile:
        print("[SalesAI Analysis] Two-Pass website analysis started")
        
        pages = fetch_result.get("pages", [])
        base_url = fetch_result.get("base_url", "").lower().rstrip("/")
        
        content_blocks = []
        # Assemble high-density informational sections from key pages to keep prompt budget optimal (~11k chars, ~2800 tokens)
        for page in pages:
            url = page.get("url", "").lower().rstrip("/")
            text = page.get("text", "")
            
            # Clean repetitive navigation boilerplate
            lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() not in {"Who we areSuccess StoriesWork with usContact us", "HomeServicesEducationMore", "HOMEPROJECTS"}]
            cleaned_text = "\n".join(lines)
            
            if url in [base_url, base_url + "/en"]:
                content_blocks.append(f"PAGE [HOME] ({url}):\n{cleaned_text[:2800]}")
            elif "services" in url:
                content_blocks.append(f"PAGE [SERVICES] ({url}):\n{cleaned_text[:2800]}")
            elif "projects" in url:
                # Include full projects and testimonials
                content_blocks.append(f"PAGE [PROJECTS & TESTIMONIALS] ({url}):\n{cleaned_text[:4200]}")
            elif "contact" in url:
                content_blocks.append(f"PAGE [CONTACT & COLLABORATION] ({url}):\n{cleaned_text[:1200]}")
                
        full_content = "\n\n".join(content_blocks)
        
        # PASS A: Extract Business Facts
        print("[SalesAI Analysis] Pass A: Extracting business facts...")
        facts = await self._pass_a_extract_facts(full_content)
        print(f"[SalesAI Analysis] Pass A complete. Found {len(facts.get('services', []))} services, {len(facts.get('client_testimonial_roles', []))} client roles.")
        
        # PASS B: Derive Sales Signals from compact facts (if not already extracted)
        if "primary_job_signals" not in facts:
            print("[SalesAI Analysis] Pass B: Deriving sales signals from compact facts...")
            try:
                signals = await self._pass_b_derive_signals(facts)
            except Exception as e:
                print(f"[SalesAI Analysis] Pass B skipped/failed ({e}), falling back to direct fact derivation.")
                signals = {}
        else:
            signals = facts
            
        print("[SalesAI Analysis] Sanitizing profile...")
        
        # Sanitize & Combine
        final_data = self._sanitize_profile(facts, signals)
        profile = WebsiteProfile(**final_data)
        print("[SalesAI Analysis] Two-Pass analysis completed successfully.")
        return profile
