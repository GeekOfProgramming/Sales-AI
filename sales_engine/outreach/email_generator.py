import json
import logging
import os
import re
from typing import Optional, Tuple, Dict, Any

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
from sales_engine.outreach.schemas import (
    EmailDraft,
    LLMEmailResponse,
    OutreachContext,
)
from sales_engine.outreach.email_prompt_builder import EmailPromptBuilder, PROMPT_VERSION
from sales_engine.outreach.draft_validator import DraftValidator

logger = logging.getLogger(__name__)


class EmailGenerator:
    """
    Invokes BIMLLMClient with sales_outreach environment to generate EmailDrafts.
    Handles JSON parsing with exactly one repair retry on syntax error.
    """

    def __init__(
        self,
        llm_client: Optional[BIMLLMClient] = None,
        model_name: Optional[str] = None,
    ):
        self.llm_client = llm_client or BIMLLMClient()
        self.model_name = (
            model_name
            or os.environ.get("OUTREACH_LLM_MODEL")
            or os.environ.get("SALES_LLM_MODEL")
            or "qwen2.5:1.5b"
        )

    @classmethod
    def _clean_json_str(cls, raw: str) -> str:
        """Strip markdown fences and whitespace from model response."""
        text = raw.strip()
        # Look for ```json ... ``` or ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()
        # Or locate first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1].strip()
        return text

    def _parse_llm_response(self, raw_text: str) -> Tuple[Optional[LLMEmailResponse], Optional[str]]:
        """Parse raw LLM output into LLMEmailResponse Pydantic model."""
        cleaned = self._clean_json_str(raw_text)
        try:
            data = json.loads(cleaned)
            if not isinstance(data, dict):
                return None, "Response is not a JSON object"
            parsed = LLMEmailResponse(**data)
            return parsed, None
        except Exception as e:
            return None, str(e)

    def generate_draft(
        self,
        context: OutreachContext,
        revision: int = 1,
    ) -> Tuple[Optional[EmailDraft], Optional[str], Optional[str]]:
        """
        Generate a validated EmailDraft.
        Returns: (draft, error_code, error_message)
        """
        user_prompt = EmailPromptBuilder.build_prompt(context)

        req = CodeGenerationRequest(
            user_prompt=user_prompt,
            environment="sales_outreach",
            language="nlp",
            model=self.model_name,
            temperature=0.2,
            num_ctx=4096,
        )

        # 1. Primary generation call
        try:
            gen_resp = self.llm_client.generate_code(req, model_name=self.model_name)
            raw_output = gen_resp.raw_response
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None, "model_unavailable", str(e)

        parsed, parse_err = self._parse_llm_response(raw_output)

        # 2. Single repair retry if JSON parse failed
        if not parsed:
            logger.warning(f"Initial JSON parse failed: {parse_err}. Triggering single repair retry.")
            repair_prompt = (
                f"You previously returned invalid JSON. Convert the following text into STRICT VALID JSON with keys: "
                f"'subject', 'body', 'service_used', 'personalization_notes', 'evidence_refs', 'cta'.\n\n"
                f"Previous output:\n{raw_output}\n\nReturn ONLY the JSON object."
            )
            repair_req = CodeGenerationRequest(
                user_prompt=repair_prompt,
                environment="sales_outreach",
                language="nlp",
                model=self.model_name,
                temperature=0.0,
                num_ctx=4096,
            )
            try:
                repair_resp = self.llm_client.generate_code(repair_req, model_name=self.model_name)
                parsed, parse_err = self._parse_llm_response(repair_resp.raw_response)
            except Exception as e:
                return None, "generation_failed", f"Repair retry failed: {e}"

            if not parsed:
                return None, "parse_failed", f"JSON parsing failed after repair retry: {parse_err}"

        # 3. Build EmailDraft object
        # Stable draft_id calculation
        draft_id = f"draft:{context.lead_id}:{context.contact_id}:{PROMPT_VERSION}:r{revision}"

        draft = EmailDraft(
            draft_id=draft_id,
            revision=revision,
            lead_id=context.lead_id,
            contact_id=context.contact_id,
            recipient_name=context.recipient_name,
            recipient_title=context.recipient_title,
            recipient_email=context.recipient_email,
            subject=parsed.subject,
            body=parsed.body,
            service_used=parsed.service_used,
            personalization_notes=parsed.personalization_notes,
            evidence_refs=parsed.evidence_refs,
            source_job_urls=context.source_job_urls,
            language=context.language,
            tone=context.tone,
            draft_type="initial_outreach",
            draft_status="generated",
            approval_status="pending_review",
            send_status="not_sent",
            generation_model=self.model_name,
            prompt_version=PROMPT_VERSION,
        )

        # 4. Deterministic validation
        is_valid, fatal_errors, warnings = DraftValidator.validate_draft(draft, context)
        draft.validation_warnings = warnings

        if not is_valid:
            err_msg = "; ".join(fatal_errors)
            logger.warning(f"Draft validation failed: {err_msg}")
            return None, "validation_failed", err_msg

        return draft, None, None
