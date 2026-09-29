import os
import json
from typing import Dict, Any
from backend.schemas import StructuredJob, JobSignal, WebsiteProfile, JobAnalysisResult
from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest

class JobAnalyzer:
    def __init__(self):
        self.llm_client = BIMLLMClient()
        self.model_name = os.getenv("SALES_LLM_MODEL", "qwen2.5:1.5b")
        
    async def analyze_job(self, raw_job: Dict[str, Any], profile: WebsiteProfile = None) -> StructuredJob:
        system_prompt = """You are a B2B sales intelligence analyst.

Analyze this job posting in the context of our company's services.

Determine:
1. technologies mentioned
2. seniority
3. remote_status (e.g. remote, hybrid, onsite)
4. requirements
5. whether this hiring activity may indicate demand for our services (relevant_signals)
6. concrete evidence for that conclusion for each signal

Use ONLY the job posting text.
Do not invent company information.
Return JSON ONLY matching the following schema exactly:

{
  "technologies": ["str"],
  "seniority": "str or null",
  "remote_status": "str or null",
  "requirements": ["str"],
  "relevant_signals": [
    {
      "signal": "str (e.g. Company is expanding BIM leadership)",
      "evidence": "str (quote or concrete evidence from the text)"
    }
  ]
}
"""

        if profile and profile.services:
            system_prompt += f"\n\nOur Services Context:\n{profile.services}\nWe look for demand matching these services."
            
        prompt = f"JOB POSTING TITLE: {raw_job.get('title')}\nCOMPANY: {raw_job.get('company')}\n\nDESCRIPTION:\n{raw_job.get('description', '')[:5000]}"
        
        req = CodeGenerationRequest(
            user_prompt=prompt,
            context_rules=system_prompt,
            environment="sales",
            language="json"
        )
        
        analysis_result = None
        for attempt in range(2):
            try:
                if attempt == 1:
                    req.user_prompt += "\n\nIMPORTANT JSON REPAIR: Return ONLY valid JSON matching the exact schema."
                    
                response = await self.llm_client.generate_code_async(req, model_name=self.model_name)
                json_text = response.extracted_code.strip()
                data = json.loads(json_text)
                
                # Validate with Pydantic model
                analysis_result = JobAnalysisResult(**data)
                break
            except Exception as e:
                if attempt == 1:
                    raise Exception(f"Failed to analyze job via LLM: {e}")
                    
        if not analysis_result:
             analysis_result = JobAnalysisResult()
             
        # Combine extracted data with structured basic data
        raw_metadata = raw_job.get("raw_metadata", {})
        return StructuredJob(
            company_name=raw_job.get("company"),
            company_name_normalized="", # Will be set by orchestrator
            company_domain="", # Might need URL parsing or extraction later
            source_company_key=raw_metadata.get("source_company_key"),
            job_title=raw_job.get("title"),
            location=raw_job.get("location"),
            employment_type=raw_job.get("employment_type"),
            posted_date=raw_job.get("posted_date"),
            job_url=raw_job.get("url"),
            source=raw_job.get("source"),
            description=raw_job.get("description"),
            requirements=analysis_result.requirements,
            technologies=analysis_result.technologies,
            seniority=analysis_result.seniority,
            remote_status=raw_job.get("remote_status") or analysis_result.remote_status,
            relevant_signals=analysis_result.relevant_signals
        )
