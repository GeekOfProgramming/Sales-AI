import json
from typing import Dict, Any
from backend.schemas import WebsiteProfile
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

class WebsiteAnalyzer:
    def __init__(self):
        self.llm_client = BIMLLMClient()
        
    async def analyze_website(self, fetch_result: Dict[str, Any]) -> WebsiteProfile:
        print("[SalesAI Analysis] LLM analysis started")
        
        content_blocks = []
        for idx, page in enumerate(fetch_result.get("pages", [])):
            content_blocks.append(f"PAGE {idx + 1} ({page['url']}):\n{page['text']}")
            
        full_content = "\n\n".join(content_blocks)
        
        system_prompt = """You are analyzing a B2B company's website.

Based ONLY on the website content provided, identify:
1. company_name: Name of the company
2. company_summary: A brief summary of what the company does
3. services: Main services offered
4. target_industries: Target industries (e.g. Architecture, Healthcare)
5. target_company_types: Target company types
6. pain_points: Business problems solved
7. buyer_roles: Likely buyer roles (decision makers)
8. primary_job_signals: Job titles indicating strong demand for these services
9. secondary_job_signals: Job titles indicating secondary demand
10. keywords: Relevant hiring keywords
11. negative_signals: Negative signals / irrelevant companies

Do not invent facts. Use only website evidence.
If information is unavailable return null or [].
Return valid JSON only matching the schema exactly.
"""
        
        prompt = f"{system_prompt}\n\nWEBSITE CONTENT:\n{full_content}"
        
        req = CodeGenerationRequest(
            user_prompt=prompt,
            environment="sales",
            language="json"
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
                response = await self.llm_client.generate_code_async(req)
                json_text = response.extracted_code.strip()
                
                # Attempt to parse json from markdown block if necessary
                if "```json" in json_text:
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif "```" in json_text:
                    json_text = json_text.split("```")[1].split("```")[0].strip()
                    
                data = json.loads(json_text)
                profile = WebsiteProfile(**data)
                print("[SalesAI Analysis] LLM analysis completed")
                return profile
            except Exception as e:
                print(f"[SalesAI Analysis] Attempt {attempt+1} failed: {e}")
                if attempt == 1:
                    raise ValueError(f"Failed to parse LLM response into WebsiteProfile: {e}")
