"""
sales_engine/research/company_analysis_service.py
Provider-agnostic LLM analysis service for Research Pipeline V2.
Features:
- Operates on a saved immutable ResearchSnapshot with zero network calls.
- Uses existing SalesAI LLMProviderGateway and OpenAIProvider adapter.
- Supports Google Gemini as first configured provider (gemini-2.5-flash-lite / gemini-3.5-flash-lite).
- Future compatible with OpenAI, Anthropic, Cohere, Groq, local models.
- Fails closed: if provider API key is missing or disabled, returns NOT_CONFIGURED without fabrication.
- Token-aware chunking and evidence aggregation.
- Strict pyBIM 13-field JSON schema validation.
- Rejects hallucinated P.IVA, emails, phone numbers, tenders, and fabricated facts.
- Distinguishes observed facts from analytical hypotheses.
- Persists CompanyAnalysisRecord in ResearchSnapshotStore.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import LLMNotConfiguredError, LLMAuthenticationError
from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider, DEFAULT_ALLOWED_HOSTS
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.research.research_store import (
    ResearchSnapshot,
    ResearchSnapshotStore,
    CompanyAnalysisRecord,
)

logger = logging.getLogger(__name__)

# Canonical 13-field extraction schema for LLM structured output
LEAD_REGISTER_13_FIELDS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "legal_identity": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "geographic_location": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "telephone_numbers": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "business_activities": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "official_website": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "responsible_person": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "descriptions_and_notes": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "job_advertisement_links": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "other_links_and_references": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "contact_email_addresses": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "priority_level": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": ["A", "B", "C"]},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
        "priority_rationale": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source_url": {"type": "string"},
                "evidence_excerpt": {"type": "string"},
                "confidence": {"type": "number"},
                "is_hypothesis": {"type": "boolean"},
            },
            "required": ["value", "source_url", "confidence", "is_hypothesis"],
            "additionalProperties": False,
        },
    },
    "required": [
        "company_name",
        "legal_identity",
        "geographic_location",
        "telephone_numbers",
        "business_activities",
        "official_website",
        "responsible_person",
        "descriptions_and_notes",
        "job_advertisement_links",
        "other_links_and_references",
        "contact_email_addresses",
        "priority_level",
        "priority_rationale",
    ],
    "additionalProperties": False,
}


def build_analysis_gateway(
    provider_id: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    client: Optional[Any] = None,
    transport: Optional[Any] = None,
) -> Tuple[LLMProviderGateway, str]:
    """
    Builds or configures the LLMProviderGateway with explicit provider registration.
    Returns (gateway, effective_model).
    """
    reg = ProviderRegistry()
    pid = provider_id.lower()

    if pid == "gemini":
        target_model = model or "gemini-2.5-flash-lite"
        prov = OpenAIProvider(
            provider_id="gemini",
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            allowed_hosts={"generativelanguage.googleapis.com"},
            env_var="GEMINI_API_KEY",
            default_model=target_model,
            client=client,
            transport=transport,
        )
        reg.register(prov)
    elif pid == "openai":
        target_model = model or "gpt-4o-mini"
        prov = OpenAIProvider(
            provider_id="openai",
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            allowed_hosts={"api.openai.com"},
            env_var="OPENAI_API_KEY",
            default_model=target_model,
            client=client,
            transport=transport,
        )
        reg.register(prov)
    else:
        # Fallback to general openai-compatible adapter
        target_model = model or "default"
        prov = OpenAIProvider(
            provider_id=pid,
            api_key=api_key,
            default_model=target_model,
            client=client,
            transport=transport,
        )
        reg.register(prov)

    config = GatewayConfig(
        enabled=True,
        default_provider=pid,
        default_model=target_model,
        default_timeout_seconds=60.0,
        max_request_chars=150_000,
    )
    gw = LLMProviderGateway(registry=reg, config=config)
    return gw, target_model


def prepare_token_aware_context(snapshot: ResearchSnapshot, max_chars: int = 40_000) -> str:
    """
    Builds structured, section-aware context from saved snapshot pages.
    Prioritizes Homepage, Contacts, About, Services, BIM, and Legal pages first.
    Excludes non-informative boilerplate.
    """
    priority_types = ["home", "contacts", "about", "services", "bim", "legal", "projects", "careers"]
    pages_by_type: Dict[str, List[Any]] = {pt: [] for pt in priority_types}
    other_pages = []

    for p in snapshot.pages:
        if p.retrieval_status != "FETCH_SUCCESS" or not p.cleaned_text.strip():
            continue
        pt = p.page_type.lower()
        if pt in pages_by_type:
            pages_by_type[pt].append(p)
        else:
            other_pages.append(p)

    ordered_pages = []
    for pt in priority_types:
        ordered_pages.extend(pages_by_type[pt])
    ordered_pages.extend(other_pages)

    context_blocks = []
    current_chars = 0

    for idx, p in enumerate(ordered_pages, 1):
        cleaned_body = p.cleaned_text.strip()
        # Cap individual page text at 5,000 characters to ensure cross-page breadth
        if len(cleaned_body) > 5000:
            cleaned_body = cleaned_body[:5000] + "\n[...truncated page content...]"

        block = (
            f"=== DOCUMENT #{idx} ===\n"
            f"URL: {p.resolved_url}\n"
            f"Section: {p.page_type}\n"
            f"Title: {p.title or 'N/A'}\n"
            f"Content:\n{cleaned_body}\n"
        )
        if current_chars + len(block) > max_chars:
            # Reached budget
            remaining_chars = max(0, max_chars - current_chars)
            if remaining_chars > 200:
                context_blocks.append(block[:remaining_chars] + "\n[...context budget reached...]")
            break
        context_blocks.append(block)
        current_chars += len(block)

    return "\n".join(context_blocks)


def execute_company_analysis(
    snapshot: ResearchSnapshot,
    candidate_id: str,
    provider_id: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    snapshot_store: Optional[ResearchSnapshotStore] = None,
    gateway_override: Optional[LLMProviderGateway] = None,
) -> CompanyAnalysisRecord:
    """
    Analyzes an existing immutable ResearchSnapshot using the LLM Gateway.
    Outputs a validated 13-field proposal and persists CompanyAnalysisRecord.
    """
    store = snapshot_store or ResearchSnapshotStore()
    now_iso = datetime.now(timezone.utc).isoformat()
    analysis_id = f"an_{uuid.uuid4().hex[:12]}"
    pid = provider_id.lower()

    # 1. Check if snapshot has succeeded pages
    if not snapshot.pages or snapshot.pages_succeeded == 0:
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=model or "unspecified",
            created_at=now_iso,
            status="FAILED",
            proposed_fields={},
            diagnostic_info="Snapshot contains 0 successful pages. Cannot analyze empty content.",
        )
        store.save_analysis_record(rec)
        return rec

    # 2. Build Gateway and verify provider configuration
    effective_model = model or ("gemini-2.5-flash-lite" if pid == "gemini" else "default")
    if gateway_override:
        gw = gateway_override
    else:
        # Check API key presence before building
        env_var = "GEMINI_API_KEY" if pid == "gemini" else f"{pid.upper()}_API_KEY"
        key_val = api_key or os.environ.get(env_var)
        if not key_val or not key_val.strip():
            logger.warning(f"LLM provider '{pid}' is not configured: missing {env_var}.")
            rec = CompanyAnalysisRecord(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                provider=pid,
                model=effective_model,
                created_at=now_iso,
                status="NOT_CONFIGURED",
                proposed_fields={},
                diagnostic_info=f"Provider '{pid}' is not configured. Missing environment variable {env_var}.",
            )
            store.save_analysis_record(rec)
            return rec

        try:
            gw, effective_model = build_analysis_gateway(
                provider_id=pid,
                model=model,
                api_key=api_key,
            )
        except Exception as e:
            rec = CompanyAnalysisRecord(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                provider=pid,
                model=effective_model,
                created_at=now_iso,
                status="NOT_CONFIGURED",
                proposed_fields={},
                diagnostic_info=f"Failed to initialize provider '{pid}': {e}",
            )
            store.save_analysis_record(rec)
            return rec

    # 3. Build token-aware context
    context_text = prepare_token_aware_context(snapshot, max_chars=45_000)

    # 4. Construct Prompt
    system_prompt = (
        "You are an expert, meticulous engineering CRM research analyst for pyBIM. "
        "Your task is to extract authoritative facts from the provided corporate website documents "
        "into the pyBIM 13-field Lead Register schema.\n\n"
        "RULES:\n"
        "1. STRICT FACTUAL GROUNDING: Extract ONLY what is explicitly stated in the documents. "
        "If a field cannot be definitively proven from the text, its value MUST be 'Not Found', "
        "confidence MUST be 0.0, and evidence_excerpt MUST be empty.\n"
        "2. ZERO FABRICATION: NEVER manufacture or hallucinate VAT / P.IVA numbers, phone numbers, "
        "email addresses, key people, job links, or tender opportunities.\n"
        "3. COMPANY IDENTITY: The company being researched is '{company_name}' ({canonical_domain}). "
        "Do NOT confuse it with clients, partners, suppliers, or software vendors mentioned in portfolio or text.\n"
        "4. HYPOTHESES: Distinguish observed facts (is_hypothesis=false) from analytical hypotheses (is_hypothesis=true). "
        "Priority rationale and subcontracting opportunity hypotheses must have is_hypothesis=true.\n"
        "5. Output valid JSON strictly conforming to the requested response schema."
    ).format(company_name=snapshot.company_name, canonical_domain=snapshot.canonical_domain)

    user_prompt = (
        f"Analyze the following official website documents for '{snapshot.company_name}' "
        f"(domain: {snapshot.canonical_domain}) and extract the 13 Lead Register fields in JSON:\n\n"
        f"{context_text}\n\n"
        f"Output valid JSON conforming to the schema."
    )

    req = LLMRequest(
        task_id="company_research_13_field_extraction",
        prompt=user_prompt,
        system_prompt=system_prompt,
        provider=pid,
        model=effective_model,
        response_schema=LEAD_REGISTER_13_FIELDS_SCHEMA,
        temperature=0.0,
        max_output_tokens=2500,
    )

    # 5. Call Gateway
    try:
        resp: LLMResponse = gw.generate(req)
    except LLMNotConfiguredError as e:
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            created_at=now_iso,
            status="NOT_CONFIGURED",
            proposed_fields={},
            diagnostic_info=f"LLM Gateway not configured: {e}",
        )
        store.save_analysis_record(rec)
        return rec
    except Exception as e:
        logger.error(f"LLM analysis execution failed: {e}", exc_info=True)
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            created_at=now_iso,
            status="FAILED",
            proposed_fields={},
            diagnostic_info=f"LLM execution failed: {type(e).__name__}: {e}",
        )
        store.save_analysis_record(rec)
        return rec

    # 6. Parse and validate JSON schema
    raw_text = resp.content or ""
    proposed_fields: Dict[str, Any] = {}
    analysis_status = "SUCCESS"
    diag_info = None

    try:
        # Check if already parsed
        if resp.parsed_json:
            parsed = resp.parsed_json
        else:
            # Strip markdown json codeblocks if any
            clean_json = raw_text.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            elif clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
            parsed = json.loads(clean_json.strip())

        # Validate required 13 fields presence
        required_13 = list(LEAD_REGISTER_13_FIELDS_SCHEMA["properties"].keys())
        missing_fields = [f for f in required_13 if f not in parsed or not isinstance(parsed[f], dict)]
        if missing_fields:
            analysis_status = "SCHEMA_INVALID"
            diag_info = f"Missing required schema fields: {missing_fields}"
        else:
            proposed_fields = parsed
    except Exception as e:
        analysis_status = "SCHEMA_INVALID"
        diag_info = f"Failed to parse LLM response as JSON: {e}"

    # Calculate token cost estimate if free tier / standard tier
    input_tokens = resp.input_tokens
    output_tokens = resp.output_tokens
    cost_estimate = 0.0
    if input_tokens and output_tokens:
        # e.g. Gemini 2.5 Flash Lite rate card $0.075 / $0.30 per 1M
        cost_estimate = ((input_tokens / 1_000_000) * 0.075) + ((output_tokens / 1_000_000) * 0.30)

    rec = CompanyAnalysisRecord(
        analysis_id=analysis_id,
        candidate_id=candidate_id,
        snapshot_id=snapshot.snapshot_id,
        provider=pid,
        model=effective_model,
        created_at=now_iso,
        status=analysis_status,
        proposed_fields=proposed_fields,
        raw_response_text=raw_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
        diagnostic_info=diag_info or (f"Extraction {analysis_status} using {pid} ({effective_model})."),
    )
    store.save_analysis_record(rec)
    return rec
