import os
import json
from typing import List
from backend.schemas import WebsiteProfile, GeneratedQueriesResponse
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

class QueryGenerator:
    def __init__(self, max_queries: int = 20):
        self.llm_client = BIMLLMClient()
        self.max_queries = max_queries
        self.model_name = os.getenv("SALES_LLM_MODEL", "qwen2.5-coder:1.5b")
        
    async def generate_queries(self, profile: WebsiteProfile, countries: List[str]) -> GeneratedQueriesResponse:
        system_prompt = f"""You are a B2B sales discovery engine.
Your task is to generate search queries to find job postings, career pages, and ATS links that indicate a company might need the services provided by the business.

Input Profile:
- Services: {profile.services}
- Target Industries: {profile.target_industries}
- Primary Job Signals: {profile.primary_job_signals}
- Secondary Job Signals: {profile.secondary_job_signals}
- Keywords: {profile.keywords}

Target Countries: {countries}

Generate a maximum of {self.max_queries} queries.
Return ONLY valid JSON matching this schema:
{{
  "queries": [
    {{
      "query": "string",
      "type": "general_job" | "company_career" | "ats_targeted" | "keyword_signal",
      "priority": integer (1-10)
    }}
  ]
}}

Query types:
- general_job: Searches for general job titles (e.g., "BIM Manager" Germany careers)
- company_career: Searches for career pages of target companies
- ats_targeted: Searches specific ATS domains (e.g., "Revit Developer" site:jobs.lever.co)
- keyword_signal: Searches for specific tech stack keywords in job postings

Do not invent unrelated job titles. Use the signals provided."""
        
        req = CodeGenerationRequest(
            user_prompt=system_prompt,
            environment="sales",
            language="json"
        )
        
        for attempt in range(2):
            try:
                if attempt == 1:
                    req.user_prompt += "\n\nIMPORTANT JSON REPAIR: Return ONLY one valid JSON object matching the schema exactly. No markdown fences. No explanation."
                
                response = await self.llm_client.generate_code_async(req, model_name=self.model_name)
                json_text = response.extracted_code.strip()
                data = json.loads(json_text)
                return GeneratedQueriesResponse(**data)
            except Exception as e:
                if attempt == 1:
                    raise Exception(f"Failed to generate queries: {e}")
