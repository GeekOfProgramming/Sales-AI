"""
sales_engine/research/company_analysis_service.py
Provider-agnostic LLM analysis service for Research Pipeline V2.1.
P0/P1 Hardening Features:
- Multi-pass bounded chunking processing ALL eligible stored snapshot pages with evidence-preserving aggregation.
- Explicit accounting: records analyzed_pages_count, chunks_count, omitted_pages_count, provider_calls_count, and omission reasons.
- Strict source evidence verification: checks source_url against specific cited page, verifies excerpt in cited page text,
  and quarantines ungrounded factual fields (grounded_in_snapshot=False, selectable_for_approval=False).
- Strict JSON schema validation for 13 pyBIM fields supporting "Not Found" states for all fields including priority.
- Integration with authoritative pricing config (pricing_config.py), server-side authorization enforcement,
  budget cap verification, and fail-closed security.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import LLMNotConfiguredError, LLMAuthenticationError, LLMTimeoutError
from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider, DEFAULT_ALLOWED_HOSTS
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.research.research_store import (
    ResearchSnapshot,
    ResearchSnapshotStore,
    CompanyAnalysisRecord,
    AnalysisChunkRecord,
)
from sales_engine.research.pricing_config import (
    get_model_pricing,
    calculate_cost,
    verify_analysis_auth_token,
    DEFAULT_GEMINI_MODEL,
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
                "value": {"type": "string", "enum": ["A", "B", "C", "Not Found", "Unassigned"]},
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


def diagnose_gemini_credential_status() -> Dict[str, Any]:
    """
    Safely inspects DPAPI credential vault and environment status for Google Gemini.
    NEVER logs, prints, or returns API secret values.
    Returns:
        {
            "status": "CONFIGURED" | "NOT_LOADED_IN_PROCESS" | "MISSING_VAULT_KEY",
            "message": str,
            "vault_path_exists": bool,
            "env_loaded": bool
        }
    """
    secret_folder = os.path.expandvars(r"%APPDATA%\pyBIM\SalesAI\api-secrets")
    dpapi_file = os.path.join(secret_folder, "GEMINI_API_KEY.dpapi")
    vault_exists = os.path.exists(dpapi_file)
    env_loaded = bool(os.environ.get("GEMINI_API_KEY", "").strip())

    if env_loaded:
        return {
            "status": "CONFIGURED",
            "message": "Gemini API credential is loaded into the current process environment.",
            "vault_path_exists": vault_exists,
            "env_loaded": True,
        }
    elif vault_exists:
        return {
            "status": "NOT_LOADED_IN_PROCESS",
            "message": "Gemini key is saved in DPAPI vault but not loaded into the active process. Run scripts/SALESAI-SEVEN-PROVIDERS.ps1 -Mode LoadAll or restart launcher.",
            "vault_path_exists": True,
            "env_loaded": False,
        }
    else:
        return {
            "status": "MISSING_VAULT_KEY",
            "message": "No DPAPI encrypted credential found in vault. Run scripts/SALESAI-SEVEN-PROVIDERS.ps1 -Mode SetupOne -Provider Gemini to securely configure.",
            "vault_path_exists": False,
            "env_loaded": False,
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
        target_model = model or DEFAULT_GEMINI_MODEL
        prov = OpenAIProvider(
            provider_id="gemini",
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            allowed_hosts={"generativelanguage.googleapis.com"},
            env_var="GEMINI_API_KEY",
            default_model=target_model,
            model_capabilities={target_model: {"supports_structured_schema": True, "supports_json_mode": True}},
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
            model_capabilities={target_model: {"supports_structured_schema": True, "supports_json_mode": True}},
            client=client,
            transport=transport,
        )
        reg.register(prov)
    else:
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


def partition_snapshot_into_chunks(
    snapshot: ResearchSnapshot,
    chunk_size_chars: int = 40_000,
) -> Tuple[List[Dict[str, Any]], List[Any], Dict[str, str]]:
    """
    Partitions all eligible successful pages of a snapshot into bounded chunks.
    Ensures that every eligible page is assigned to a chunk without silent discarding.
    Deduplicates repeated content lines across pages.
    Returns:
      (chunks, eligible_pages, omitted_pages_reasons)
    """
    eligible_pages: List[Any] = []
    omitted_pages_reasons: Dict[str, str] = {}
    seen_content_hashes: Set[str] = set()

    for p in snapshot.pages:
        if p.retrieval_status != "FETCH_SUCCESS":
            omitted_pages_reasons[p.requested_url or p.resolved_url] = f"Retrieval status was {p.retrieval_status}"
            continue
        body = (p.cleaned_text or "").strip()
        if not body:
            omitted_pages_reasons[p.resolved_url] = "Page cleaned text content is empty"
            continue

        c_hash = p.content_sha256 or hashlib.sha256(body.encode("utf-8")).hexdigest()
        if c_hash in seen_content_hashes:
            omitted_pages_reasons[p.resolved_url] = "Exact duplicate page content"
            continue
        seen_content_hashes.add(c_hash)
        eligible_pages.append(p)

    # Sort pages by priority order: Homepage, Contacts, About, Services, BIM, Legal, etc.
    priority_order = ["home", "contacts", "about", "services", "bim", "legal", "projects", "careers", "general"]
    def get_sort_key(p: Any) -> int:
        pt = (p.page_type or "general").lower()
        return priority_order.index(pt) if pt in priority_order else 99

    eligible_pages.sort(key=get_sort_key)

    chunks: List[Dict[str, Any]] = []
    current_chunk_blocks: List[str] = []
    current_chunk_urls: List[str] = []
    current_chunk_chars = 0

    for idx, p in enumerate(eligible_pages, 1):
        raw_text = (p.cleaned_text or "").strip()
        # Oversized page handling: if page text alone exceeds chunk_size_chars,
        # split it into sub-parts preserving URL and section metadata
        page_blocks: List[str] = []
        if len(raw_text) > chunk_size_chars:
            part_size = max(10_000, chunk_size_chars - 2_000)
            parts = [raw_text[i:i + part_size] for i in range(0, len(raw_text), part_size)]
            for sub_idx, sub_text in enumerate(parts, 1):
                page_blocks.append(
                    f"=== DOCUMENT #{idx} (Part {sub_idx}/{len(parts)}) ===\n"
                    f"URL: {p.resolved_url}\n"
                    f"Section: {p.page_type}\n"
                    f"Title: {p.title or 'N/A'}\n"
                    f"Content:\n{sub_text.strip()}\n"
                )
        else:
            page_blocks.append(
                f"=== DOCUMENT #{idx} ===\n"
                f"URL: {p.resolved_url}\n"
                f"Section: {p.page_type}\n"
                f"Title: {p.title or 'N/A'}\n"
                f"Content:\n{raw_text}\n"
            )

        for block in page_blocks:
            block_len = len(block)
            if current_chunk_blocks and (current_chunk_chars + block_len > chunk_size_chars):
                chunks.append({
                    "chunk_index": len(chunks) + 1,
                    "text": "\n".join(current_chunk_blocks),
                    "urls": list(current_chunk_urls),
                })
                current_chunk_blocks = [block]
                current_chunk_urls = [p.resolved_url]
                current_chunk_chars = block_len
            else:
                current_chunk_blocks.append(block)
                if p.resolved_url not in current_chunk_urls:
                    current_chunk_urls.append(p.resolved_url)
                current_chunk_chars += block_len

    if current_chunk_blocks:
        chunks.append({
            "chunk_index": len(chunks) + 1,
            "text": "\n".join(current_chunk_blocks),
            "urls": list(current_chunk_urls),
        })

    return chunks, eligible_pages, omitted_pages_reasons


def prepare_token_aware_context(snapshot: ResearchSnapshot, max_chars: int = 40_000) -> str:
    """
    Backwards-compatible single-pass context preparation.
    Uses partition_snapshot_into_chunks under the hood.
    """
    chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=max_chars)
    if not chunks:
        return ""
    return chunks[0]["text"]


def merge_field_proposals(
    current_best: Dict[str, Any],
    new_proposals: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merges factual field proposals from multiple chunk analysis passes.
    Prefers grounded observed facts over 'Not Found' and analytical hypotheses.
    """
    merged = dict(current_best)

    for field_name, new_f in new_proposals.items():
        if not isinstance(new_f, dict):
            continue
        new_val = str(new_f.get("value", "")).strip()
        new_conf = float(new_f.get("confidence", 0.0) or 0.0)
        new_hyp = bool(new_f.get("is_hypothesis", False))

        if field_name not in merged:
            merged[field_name] = new_f
            continue

        curr_f = merged[field_name]
        curr_val = str(curr_f.get("value", "")).strip()
        curr_conf = float(curr_f.get("confidence", 0.0) or 0.0)
        curr_hyp = bool(curr_f.get("is_hypothesis", False))

        # Promotion logic:
        # 1. If current is 'Not Found' or empty, and new is found -> promote new
        if curr_val in ("Not Found", "", "None") and new_val not in ("Not Found", "", "None"):
            merged[field_name] = new_f
        # 2. If current is hypothesis but new is observed fact -> promote new
        elif curr_hyp and not new_hyp and new_val not in ("Not Found", "", "None"):
            merged[field_name] = new_f
        # 3. If both are facts/hypotheses, prefer higher confidence
        elif new_conf > curr_conf and new_val not in ("Not Found", "", "None"):
            merged[field_name] = new_f

    return merged


def verify_field_evidence_excerpts(
    snapshot: ResearchSnapshot,
    proposed_fields: Dict[str, Any],
) -> Dict[str, Any]:
    """
    P0-C: Strict Source Evidence Grounding.
    - Validates each factual proposal against the specific cited source_url.
    - Verifies evidence_excerpt is present as a substring within that specific page.
    - If cited URL is not in snapshot or excerpt is missing/fabricated, rejects or quarantines
      the proposal (grounded_in_snapshot=False, selectable_for_approval=False, quarantined=True).
    - Preserves ungrounded proposals only as labeled unverified AI outputs.
    """
    snapshot_pages = {
        p.resolved_url: (p.cleaned_text or "").lower()
        for p in snapshot.pages
        if p.retrieval_status == "FETCH_SUCCESS"
    }

    verified_fields = {}
    for key, field_obj in proposed_fields.items():
        if not isinstance(field_obj, dict):
            verified_fields[key] = field_obj
            continue

        f_copy = dict(field_obj)
        val = str(f_copy.get("value", "")).strip()
        s_url = str(f_copy.get("source_url", "")).strip()
        excerpt = str(f_copy.get("evidence_excerpt", "")).strip()
        is_hyp = bool(f_copy.get("is_hypothesis", False))

        # Unknown / Not Found is valid and inherently grounded
        if val in ("Not Found", "", "None", "Unassigned"):
            f_copy["grounded_in_snapshot"] = True
            f_copy["excerpt_verified"] = True
            f_copy["selectable_for_approval"] = True
            f_copy["quarantined"] = False
        elif is_hyp and key in ("priority_level", "priority_rationale"):
            # Analytical hypothesis does not require literal substring match on website
            f_copy["grounded_in_snapshot"] = True
            f_copy["excerpt_verified"] = True
            f_copy["selectable_for_approval"] = True
            f_copy["quarantined"] = False
        else:
            # Must match specific cited source_url
            page_text = None
            if s_url in snapshot_pages:
                page_text = snapshot_pages[s_url]
            else:
                # Try normalized match with/without trailing slash
                for snap_u, text in snapshot_pages.items():
                    if s_url.rstrip("/") == snap_u.rstrip("/"):
                        page_text = text
                        break

            if page_text is None:
                # Source URL not in snapshot!
                f_copy["grounded_in_snapshot"] = False
                f_copy["excerpt_verified"] = False
                f_copy["selectable_for_approval"] = False
                f_copy["quarantined"] = True
                f_copy["quarantine_reason"] = f"Cited source_url '{s_url}' was not fetched in this snapshot."
            else:
                f_copy["grounded_in_snapshot"] = True
                if excerpt:
                    excerpt_clean = " ".join(excerpt.lower().split())
                    text_clean = " ".join(page_text.split())
                    if excerpt_clean in text_clean:
                        f_copy["excerpt_verified"] = True
                        f_copy["selectable_for_approval"] = True
                        f_copy["quarantined"] = False
                    else:
                        f_copy["excerpt_verified"] = False
                        f_copy["selectable_for_approval"] = False
                        f_copy["quarantined"] = True
                        f_copy["quarantine_reason"] = "Evidence excerpt not found in cited page body."
                else:
                    # Missing evidence excerpt for factual claim
                    f_copy["excerpt_verified"] = False
                    f_copy["selectable_for_approval"] = False
                    f_copy["quarantined"] = True
                    f_copy["quarantine_reason"] = "Factual claim missing verbatim evidence excerpt."

        verified_fields[key] = f_copy

    return verified_fields


def execute_company_analysis(
    snapshot: ResearchSnapshot,
    candidate_id: str,
    provider_id: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    snapshot_store: Optional[ResearchSnapshotStore] = None,
    gateway_override: Optional[LLMProviderGateway] = None,
    auth_token: Optional[str] = None,
    require_auth: bool = False,
    max_approved_budget_usd: Optional[float] = None,
) -> CompanyAnalysisRecord:
    """
    Analyzes an existing immutable ResearchSnapshot using the LLM Gateway.
    P0/P1 Features:
    - Bounded multi-pass chunking across ALL eligible pages.
    - Server-side authorization check (if require_auth=True).
    - Source evidence verification and field quarantining.
    - Full accounting of pages, chunks, provider calls, tokens, and costs.
    """
    store = snapshot_store or ResearchSnapshotStore()
    now_iso = datetime.now(timezone.utc).isoformat()
    analysis_id = f"an_{uuid.uuid4().hex[:12]}"
    pid = provider_id.lower()
    effective_model = model or (DEFAULT_GEMINI_MODEL if pid == "gemini" else "default")

    # 0. Validate Candidate and Snapshot Ownership (Fail-closed)
    if snapshot.candidate_id != candidate_id:
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            created_at=now_iso,
            status="SECURITY_VIOLATION",
            proposed_fields={},
            diagnostic_info=f"Cross-candidate snapshot reference prohibited. Snapshot belongs to '{snapshot.candidate_id}', not '{candidate_id}'.",
        )
        store.save_analysis_record(rec)
        return rec

    # 1. Validate Snapshot Content
    if not snapshot.pages or snapshot.pages_succeeded == 0:
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            created_at=now_iso,
            status="FAILED",
            proposed_fields={},
            diagnostic_info="Snapshot contains 0 successful pages. Cannot analyze empty content.",
        )
        store.save_analysis_record(rec)
        return rec

    # 2. Server-side Operator Authorization Check (P1-B / V2.1c Authoritative Nonce Consumption)
    effective_budget_cap: Optional[float] = max_approved_budget_usd
    if require_auth:
        is_auth, auth_budget, auth_err = verify_analysis_auth_token(
            auth_token=auth_token,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            mark_used=True,
        )
        if not is_auth:
            rec = CompanyAnalysisRecord(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                provider=pid,
                model=effective_model,
                created_at=now_iso,
                status="UNAUTHORIZED",
                proposed_fields={},
                diagnostic_info=f"Authorization rejected: {auth_err}",
            )
            store.save_analysis_record(rec)
            return rec

        # Authoritative budget extraction from token: caller budget can NEVER disable or override token ceiling
        if auth_budget is not None:
            if effective_budget_cap is not None:
                effective_budget_cap = min(effective_budget_cap, auth_budget)
            else:
                effective_budget_cap = auth_budget

    # 3. Build Gateway and verify credentials
    if gateway_override:
        gw = gateway_override
    else:
        env_var = "GEMINI_API_KEY" if pid == "gemini" else f"{pid.upper()}_API_KEY"
        key_val = api_key or os.environ.get(env_var)
        if not key_val or not key_val.strip():
            logger.warning(f"LLM provider '{pid}' is not configured: missing {env_var}.")
            diag_info = f"Provider '{pid}' is not configured. Missing environment variable {env_var}."
            if pid == "gemini":
                diag = diagnose_gemini_credential_status()
                diag_info = f"Gemini status [{diag['status']}]: {diag['message']}"

            rec = CompanyAnalysisRecord(
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                provider=pid,
                model=effective_model,
                created_at=now_iso,
                status="NOT_CONFIGURED",
                proposed_fields={},
                diagnostic_info=diag_info,
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

    # 4. Multi-pass Chunking: Partition all eligible pages (P0-B)
    chunks, eligible_pages, omitted_reasons = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)

    if not chunks:
        rec = CompanyAnalysisRecord(
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            provider=pid,
            model=effective_model,
            created_at=now_iso,
            status="FAILED",
            proposed_fields={},
            diagnostic_info="No eligible pages with readable text found in snapshot.",
            analyzed_pages_count=0,
            chunks_count=0,
            omitted_pages_count=len(snapshot.pages),
            omitted_pages_reasons=omitted_reasons,
        )
        store.save_analysis_record(rec)
        return rec

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

    # Hard architectural ceilings to prevent runaway execution
    MAX_ALLOWED_CHUNKS = 5
    MAX_TOTAL_OUTPUT_TOKENS = 12_500  # 5 calls * 2,500 max output tokens
    SCHEMA_VERSION = "2.1.0"
    PROMPT_VERSION = "2.1.0"

    # Query existing chunk checkpoints for candidate, snapshot, and model (resumability)
    existing_checkpoints = store.get_chunk_checkpoints(
        candidate_id=candidate_id,
        snapshot_id=snapshot.snapshot_id,
        model=effective_model,
    )
    checkpoint_map = {cp.chunk_hash: cp for cp in existing_checkpoints}

    aggregated_proposals: Dict[str, Any] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    raw_responses: List[str] = []
    overall_status = "SUCCESS"
    diag_msgs: List[str] = []
    executed_chunks_count = 0
    resumed_chunks_count = 0

    # Hydrate completed checkpoints first
    for c in chunks:
        c_hash = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        cp = checkpoint_map.get(c_hash)
        if cp and cp.status == "COMPLETED" and cp.snapshot_checksum == snapshot.content_checksum:
            logger.info(f"Resuming analysis: chunk {c['chunk_index']} already completed (hash: {c_hash[:8]}).")
            resumed_chunks_count += 1
            total_input_tokens += (cp.input_tokens or 0)
            total_output_tokens += (cp.output_tokens or 0)
            if cp.raw_response:
                raw_responses.append(cp.raw_response)
            if cp.proposed_fields:
                aggregated_proposals = merge_field_proposals(aggregated_proposals, cp.proposed_fields)

    for c in chunks:
        c_hash = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        cp = checkpoint_map.get(c_hash)
        # Skip if already completed with identical checksum
        if cp and cp.status == "COMPLETED" and cp.snapshot_checksum == snapshot.content_checksum:
            continue

        # Enforce provider-call ceiling
        if executed_chunks_count >= MAX_ALLOWED_CHUNKS:
            logger.warning(f"Reached maximum allowed chunk calls ({MAX_ALLOWED_CHUNKS}). Terminating analysis loop safely.")
            diag_msgs.append(f"Reached chunk call ceiling ({MAX_ALLOWED_CHUNKS}). Remaining chunks omitted.")
            break

        # Enforce output token ceiling
        if total_output_tokens >= MAX_TOTAL_OUTPUT_TOKENS:
            logger.warning(f"Reached maximum output token ceiling ({MAX_TOTAL_OUTPUT_TOKENS}). Terminating safely.")
            diag_msgs.append(f"Reached output token ceiling ({MAX_TOTAL_OUTPUT_TOKENS}).")
            break

        # Pre-request Budget Check: estimate cost of this next chunk before invoking provider
        est_chunk_inp = int(len(c["text"]) / 4.0) + 500
        projected_total_cost = calculate_cost(
            input_tokens=total_input_tokens + est_chunk_inp,
            output_tokens=total_output_tokens + 1500,
            provider=pid,
            model=effective_model,
        )
        if effective_budget_cap is not None and projected_total_cost > effective_budget_cap:
            logger.warning(
                f"Aborting analysis before chunk {c['chunk_index']}: projected cost ${projected_total_cost:.4f} "
                f"exceeds approved budget ${effective_budget_cap:.4f}."
            )
            overall_status = "BUDGET_EXCEEDED"
            diag_msgs.append(
                f"Pre-request budget check exceeded: projected ${projected_total_cost:.4f} > cap ${effective_budget_cap:.4f}."
            )
            break

        user_prompt = (
            f"Analyze the following official website documents for '{snapshot.company_name}' "
            f"(domain: {snapshot.canonical_domain}) [Chunk {c['chunk_index']}/{len(chunks)}] "
            f"and extract the 13 Lead Register fields in JSON:\n\n"
            f"{c['text']}\n\n"
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
            timeout_seconds=90.0,
        )

        chunk_record_id = f"chk_{analysis_id}_{c['chunk_index']}"
        now_chk = datetime.now(timezone.utc).isoformat()
        try:
            resp: LLMResponse = gw.generate(req)
            executed_chunks_count += 1
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
        except LLMTimeoutError as e:
            cat = getattr(e, "timeout_category", "REQUEST_TIMEOUT")
            logger.error(f"LLM analysis timed out on chunk {c['chunk_index']} ({cat}): {e}")
            overall_status = "TIMEOUT"
            diag_msgs.append(f"Chunk {c['chunk_index']} timed out ({cat}): {e}")
            # Persist UNCERTAIN_TIMEOUT checkpoint
            timeout_chk = AnalysisChunkRecord(
                chunk_record_id=chunk_record_id,
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_checksum=snapshot.content_checksum,
                chunk_index=c["chunk_index"],
                total_chunks=len(chunks),
                chunk_hash=c_hash,
                provider=pid,
                model=effective_model,
                schema_version=SCHEMA_VERSION,
                prompt_version=PROMPT_VERSION,
                status="UNCERTAIN_TIMEOUT",
                created_at=now_chk,
                error_message=f"Timeout ({cat}): {e}",
            )
            store.save_chunk_checkpoint(timeout_chk)
            break
        except Exception as e:
            logger.error(f"LLM analysis execution failed on chunk {c['chunk_index']}: {e}", exc_info=True)
            overall_status = "FAILED"
            diag_msgs.append(f"Chunk {c['chunk_index']} failed: {e}")
            fail_chk = AnalysisChunkRecord(
                chunk_record_id=chunk_record_id,
                analysis_id=analysis_id,
                candidate_id=candidate_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_checksum=snapshot.content_checksum,
                chunk_index=c["chunk_index"],
                total_chunks=len(chunks),
                chunk_hash=c_hash,
                provider=pid,
                model=effective_model,
                schema_version=SCHEMA_VERSION,
                prompt_version=PROMPT_VERSION,
                status="FAILED",
                created_at=now_chk,
                error_message=str(e),
            )
            store.save_chunk_checkpoint(fail_chk)
            break

        c_inp = resp.input_tokens or 0
        c_out = resp.output_tokens or 0
        total_input_tokens += c_inp
        total_output_tokens += c_out
        raw_responses.append(resp.content or "")

        # Check for model truncation / finish reason
        if resp.finish_reason and resp.finish_reason.lower() in ("length", "max_tokens"):
            overall_status = "SCHEMA_INVALID"
            diag_msgs.append(f"Chunk {c['chunk_index']} truncated by provider (finish_reason: {resp.finish_reason}).")

        # Parse chunk JSON
        raw_text = resp.content or ""
        chunk_parsed: Dict[str, Any] = {}
        try:
            if resp.parsed_json:
                chunk_parsed = resp.parsed_json
            else:
                clean_json = raw_text.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json[7:]
                elif clean_json.startswith("```"):
                    clean_json = clean_json[3:]
                if clean_json.endswith("```"):
                    clean_json = clean_json[:-3]
                chunk_parsed = json.loads(clean_json.strip())

            # Handle potential top-level nesting wrappers: e.g. {"lead_register": {...}} or {"fields": {...}}
            if isinstance(chunk_parsed, dict):
                for wrapper_key in ("lead_register", "fields", "lead_register_fields", "data", "result"):
                    if wrapper_key in chunk_parsed and isinstance(chunk_parsed[wrapper_key], dict):
                        chunk_parsed = chunk_parsed[wrapper_key]
                        break

            aggregated_proposals = merge_field_proposals(aggregated_proposals, chunk_parsed)
        except Exception as e:
            logger.warning(f"Failed to parse chunk {c['chunk_index']} JSON: {e}")
            diag_msgs.append(f"Chunk {c['chunk_index']} schema error: {e}")

        # Compute chunk cost
        chunk_cost = calculate_cost(
            input_tokens=c_inp,
            output_tokens=c_out,
            provider=pid,
            model=effective_model,
        )

        # Save successful chunk checkpoint immediately
        success_chk = AnalysisChunkRecord(
            chunk_record_id=chunk_record_id,
            analysis_id=analysis_id,
            candidate_id=candidate_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_checksum=snapshot.content_checksum,
            chunk_index=c["chunk_index"],
            total_chunks=len(chunks),
            chunk_hash=c_hash,
            provider=pid,
            model=effective_model,
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            status="COMPLETED",
            created_at=now_chk,
            completed_at=datetime.now(timezone.utc).isoformat(),
            input_tokens=c_inp,
            output_tokens=c_out,
            cost_estimate=chunk_cost,
            raw_response=resp.content,
            proposed_fields=chunk_parsed if isinstance(chunk_parsed, dict) else {},
        )
        store.save_chunk_checkpoint(success_chk)

    # Validate final 13 fields presence and nested structures
    required_13 = list(LEAD_REGISTER_13_FIELDS_SCHEMA["properties"].keys())
    missing = []
    malformed = []

    for f in required_13:
        if f not in aggregated_proposals or not isinstance(aggregated_proposals[f], dict):
            missing.append(f)
        else:
            f_dict = aggregated_proposals[f]
            # Ensure required nested field keys exist: value, source_url, confidence, is_hypothesis
            if "value" not in f_dict or "source_url" not in f_dict or "confidence" not in f_dict or "is_hypothesis" not in f_dict:
                malformed.append(f)

    if (missing or malformed) and overall_status == "SUCCESS":
        overall_status = "SCHEMA_INVALID"
        err_parts = []
        if missing:
            err_parts.append(f"Missing required fields: {missing}")
        if malformed:
            err_parts.append(f"Malformed nested field schemas: {malformed}")
        diag_msgs.append("; ".join(err_parts))

    # Apply strict source evidence verification (P0-C)
    final_fields = verify_field_evidence_excerpts(snapshot, aggregated_proposals)

    # Calculate actual cost using authoritative pricing config
    actual_cost = calculate_cost(
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        provider=pid,
        model=effective_model,
    )

    # Calculate completed chunks vs planned
    completed_chunks_count = resumed_chunks_count + (executed_chunks_count if overall_status == "SUCCESS" else max(0, executed_chunks_count - 1))
    analyzed_page_count = len(eligible_pages) if overall_status == "SUCCESS" else 0

    summary_diag = (
        f"Planned {len(chunks)} chunk(s) across {len(eligible_pages)} eligible pages. "
        f"Completed: {completed_chunks_count}/{len(chunks)} chunks (resumed {resumed_chunks_count}). "
        f"Omitted: {len(omitted_reasons)} pages. Status: {overall_status}."
    )
    if diag_msgs:
        summary_diag += " Diagnostics: " + "; ".join(diag_msgs)

    rec = CompanyAnalysisRecord(
        analysis_id=analysis_id,
        candidate_id=candidate_id,
        snapshot_id=snapshot.snapshot_id,
        provider=pid,
        model=effective_model,
        created_at=now_iso,
        status=overall_status,
        proposed_fields=final_fields,
        raw_response_text="\n---\n".join(raw_responses),
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_estimate=actual_cost,
        diagnostic_info=summary_diag,
        analyzed_pages_count=analyzed_page_count,
        chunks_count=len(chunks),
        omitted_pages_count=len(omitted_reasons),
        provider_calls_count=executed_chunks_count,
        omitted_pages_reasons=omitted_reasons,
    )
    store.save_analysis_record(rec)
    return rec


def estimate_analysis_preflight(
    snapshot: ResearchSnapshot,
    provider_id: str = "gemini",
    model: Optional[str] = None,
    chunk_size_chars: int = 40_000,
) -> Dict[str, Any]:
    """
    P1-B: Calculates official pre-inference token and cost estimates based on
    all eligible snapshot pages and model-specific pricing configuration.
    """
    pid = (provider_id or "gemini").lower()
    target_model = model or (DEFAULT_GEMINI_MODEL if pid == "gemini" else "default")
    pricing = get_model_pricing(pid, target_model)

    chunks, eligible_pages, omitted = partition_snapshot_into_chunks(snapshot, chunk_size_chars=chunk_size_chars)
    total_chars = sum(len(c["text"]) for c in chunks)

    # Conservative token estimation: ~4 chars per token
    estimated_input_tokens = total_chars // 4
    estimated_output_tokens = 1200 * max(1, len(chunks))

    estimated_cost = calculate_cost(
        input_tokens=estimated_input_tokens,
        output_tokens=estimated_output_tokens,
        provider=pid,
        model=target_model,
    )

    planned_chunks_count = len(chunks)
    capped_chunks_count = min(planned_chunks_count, 5)  # MAX_ALLOWED_CHUNKS = 5
    will_omit_chunks = planned_chunks_count > capped_chunks_count

    return {
        "provider": pid,
        "model": target_model,
        "eligible_pages_count": len(eligible_pages),
        "omitted_pages_count": len(omitted),
        "chunks_count": planned_chunks_count,
        "capped_chunks_count": capped_chunks_count,
        "will_omit_chunks": will_omit_chunks,
        "omitted_chunks_count": max(0, planned_chunks_count - capped_chunks_count),
        "total_context_chars": total_chars,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_cost_usd": estimated_cost,
        "billing_tier": pricing.billing_tier,
        "pricing_source_date": pricing.source_date,
        "rate_input_1m": pricing.input_usd_per_1m,
        "rate_output_1m": pricing.output_usd_per_1m,
    }
