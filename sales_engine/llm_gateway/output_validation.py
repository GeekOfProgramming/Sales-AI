"""
sales_engine/llm_gateway/output_validation.py
Strict, deterministic, provider-independent LLM output validation layer for SalesAI.
Strictly offline, fail-closed, zero network/IO.
Implements S2.5 & S2.5.1:
- Typed OutputValidationResult and LLMOutputValidator
- Request/response identity & provenance verification (B5 & B6)
- Strict syntax parsing (duplicate keys, single whole-response fence only, no eval/permissive regex)
- Seven task/subtask schema validations using SchemaRegistry (strict types, B8)
- Authoritative evidence reference verification with single trusted provenance path (B1 & B2)
- Local opt-out, bounce, and suppression precedence over every conflicting classification (B3 & B4)
- Attacker-controlled schema extra key sanitization (zero PII/secrets in diagnostics, B7)
- Prompt injection & secret reflection defenses
- Bounded repair proposal evaluation (zero network dispatch)
"""

import json
import math
import re
from typing import Dict, Any, Optional, List, Set, Tuple, Type
from pydantic import BaseModel, Field, ConfigDict, ValidationError

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, sanitize_log_str
from sales_engine.llm_gateway.model_catalog import normalize_task_id, validate_subtask_id
from sales_engine.llm_gateway.output_schemas import (
    SchemaRegistry,
    StrictOutputBaseModel,
    SCHEMA_REGISTRY_VERSION,
)
from sales_engine.llm_gateway.repair_policy import (
    RepairPolicy,
    RepairProposal,
    REPAIR_POLICY_VERSION,
)
from sales_engine.llm_gateway.source_boundaries import contains_prompt_injection

OUTPUT_VALIDATOR_VERSION = "1.0.2"

# Sensitive token / secret inspection patterns in output
_OUTPUT_SECRET_PATTERNS = [
    r"bearer\s+[A-Za-z0-9_\-\.]{15,}",
    r"sk-[A-Za-z0-9_\-]{15,}",
    r"(?:api[_\s-]?key|secret[_\s-]?key|access[_\s-]?token|private[_\s-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}",
    r"password\s*[:=]\s*['\"][^'\"]{6,}['\"]",
]
_SECRET_RE = re.compile("|".join(_OUTPUT_SECRET_PATTERNS), re.IGNORECASE)

# Forbidden operational action keys/values that LLM output must not attempt to authorize
_FORBIDDEN_OPERATIONAL_ACTIONS: Set[str] = {
    "send_email",
    "send_now",
    "send_status",
    "authorized",
    "approved_by",
    "promote_tier",
    "change_budget",
    "budget_override",
    "confirm_meeting",
    "meeting_confirmed",
    "alter_routing",
}


def _detect_duplicate_keys(pairs: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    """
    JSON object pairs hook that detects duplicate dictionary keys at ANY depth.
    Standard json.loads silently overwrites duplicates; SalesAI strictly rejects them.
    """
    d: Dict[str, Any] = {}
    for k, v in pairs:
        k_str = str(k)
        if k_str in d:
            raise ValueError(f"DUPLICATE_KEY:{k_str}")
        d[k_str] = v
    return d


def _reject_constant(val: str) -> None:
    """Reject NaN, Infinity, -Infinity in JSON numbers."""
    raise ValueError(f"ILLEGAL_CONSTANT:{val}")


def _sanitize_reason_code(code: str) -> str:
    """Ensure reason code contains only uppercase alphanumeric and underscores, max 64 chars."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(code).strip()).upper()
    return cleaned[:64]


class OutputValidationResult(BaseModel):
    """
    Typed, immutable result returned by LLMOutputValidator.
    Contains validated data/model on success, or safe failure reason codes on rejection.
    Guarantees zero leakage of customer data, prompts, or secrets in repr/str.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    is_valid: bool = Field(..., description="Whether output passed all syntax, schema, and safety checks")
    task_id: str = Field(..., description="Canonical task identifier")
    subtask_id: Optional[str] = Field(default=None, description="Canonical subtask identifier if applicable")
    schema_version: str = Field(default=SCHEMA_REGISTRY_VERSION)
    validated_data: Optional[Dict[str, Any]] = Field(default=None, description="Validated JSON dictionary on success")
    failure_reasons: List[str] = Field(default_factory=list, description="Safe diagnostic reason codes")
    needs_business_review: bool = Field(
        default=False,
        description="True if business claim grounding or human verification is needed",
    )
    is_opt_out_overridden: bool = Field(
        default=False,
        description="True if local unsubscribe signal overrode conflicting model output",
    )
    is_bounce_overridden: bool = Field(
        default=False,
        description="True if local bounce signal overrode contradictory positive model output",
    )
    is_suppression_overridden: bool = Field(
        default=False,
        description="True if local suppression signal overrode contradictory positive model output",
    )
    effective_local_disposition: Optional[str] = Field(
        default=None,
        description="Authoritative local disposition (e.g. 'unsubscribe', 'bounce', 'do_not_contact')",
    )
    normalization_applied: bool = Field(
        default=False,
        description="True if leading/trailing whitespace or whole-response fence was stripped",
    )
    repair_proposal: Optional[RepairProposal] = Field(
        default=None,
        description="Disabled single-attempt repair plan on formatting failure",
    )
    raw_content_length: int = Field(default=0, ge=0, description="Length in characters of raw provider output")
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Safe measured metrics (tokens, latency, uninvented cost)",
    )
    validator_version: str = Field(default=OUTPUT_VALIDATOR_VERSION)

    def safe_log_dict(self) -> Dict[str, Any]:
        """Return non-sensitive dictionary suitable for application logging (zero attacker input)."""
        safe_reasons = [_sanitize_reason_code(r) for r in self.failure_reasons]
        # S2.5.2 B2: Closed allowlist of known scalar measured fields (never copy arbitrary keys)
        safe_metrics: Dict[str, Any] = {
            "input_tokens": self.metrics.get("input_tokens") if isinstance(self.metrics.get("input_tokens"), int) else None,
            "output_tokens": self.metrics.get("output_tokens") if isinstance(self.metrics.get("output_tokens"), int) else None,
            "latency_ms": self.metrics.get("latency_ms") if isinstance(self.metrics.get("latency_ms"), (int, float)) else None,
            "cost_usd": self.metrics.get("cost_usd") if isinstance(self.metrics.get("cost_usd"), (int, float)) else None,
            "has_provider_request_id": bool(self.metrics.get("has_provider_request_id")),
            "provider_request_id": None,
        }
        return {
            "is_valid": self.is_valid,
            "task_id": sanitize_log_str(self.task_id),
            "subtask_id": sanitize_log_str(self.subtask_id) if self.subtask_id else None,
            "schema_version": sanitize_log_str(self.schema_version),
            "failure_reasons": safe_reasons,
            "needs_business_review": self.needs_business_review,
            "is_opt_out_overridden": self.is_opt_out_overridden,
            "is_bounce_overridden": self.is_bounce_overridden,
            "is_suppression_overridden": self.is_suppression_overridden,
            "effective_local_disposition": sanitize_log_str(self.effective_local_disposition) if self.effective_local_disposition else None,
            "normalization_applied": self.normalization_applied,
            "has_repair_proposal": self.repair_proposal is not None,
            "raw_content_length": self.raw_content_length,
            "metrics": safe_metrics,
            "validator_version": sanitize_log_str(self.validator_version),
        }

    def __repr__(self) -> str:
        safe_reasons = [_sanitize_reason_code(r) for r in self.failure_reasons]
        safe_task = sanitize_log_str(self.task_id)
        safe_subtask = sanitize_log_str(self.subtask_id) if self.subtask_id else None
        safe_disp = sanitize_log_str(self.effective_local_disposition) if self.effective_local_disposition else None
        return (
            f"OutputValidationResult(is_valid={self.is_valid}, task_id={safe_task!r}, "
            f"subtask_id={safe_subtask!r}, reasons={safe_reasons!r}, "
            f"needs_business_review={self.needs_business_review}, opt_out_overridden={self.is_opt_out_overridden}, "
            f"bounce_overridden={self.is_bounce_overridden}, disposition={safe_disp!r}, "
            f"raw_length={self.raw_content_length})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class LLMOutputValidator:
    """
    Lightweight, deterministic, provider-independent output validation engine for SalesAI.
    Validates identity, strict JSON syntax, versioned task schemas, evidence references,
    and security boundaries. Zero external network IO, zero model calls.
    Hardened under S2.5.1 against defects B1 through B8.
    """

    def __init__(
        self,
        max_content_bytes: int = 500_000,
        max_nesting_depth: int = 8,
        max_collection_items: int = 200,
        max_dict_keys: int = 100,
        repair_policy: Optional[RepairPolicy] = None,
    ):
        self.max_content_bytes = max_content_bytes
        self.max_nesting_depth = max_nesting_depth
        self.max_collection_items = max_collection_items
        self.max_dict_keys = max_dict_keys
        self.repair_policy = repair_policy or RepairPolicy(max_attempts=1)

    def _check_nesting_and_size(self, data: Any, current_depth: int = 0) -> None:
        """Recursively enforce nesting depth and collection size limits."""
        if current_depth > self.max_nesting_depth:
            raise ValueError("EXCESSIVE_NESTING_DEPTH")

        if isinstance(data, dict):
            if len(data) > self.max_dict_keys:
                raise ValueError("EXCESSIVE_COLLECTION_SIZE")
            for k, v in data.items():
                if len(str(k)) > 250:
                    raise ValueError("EXCESSIVE_KEY_LENGTH")
                self._check_nesting_and_size(v, current_depth + 1)

        elif isinstance(data, (list, tuple)):
            if len(data) > self.max_collection_items:
                raise ValueError("EXCESSIVE_COLLECTION_SIZE")
            for item in data:
                self._check_nesting_and_size(item, current_depth + 1)

    def _check_safety_and_operational_protection(self, data: Any, failure_reasons: List[str]) -> None:
        """Scan validated output for injected instructions, leaked secrets, or forbidden operational controls."""
        if isinstance(data, dict):
            for k, v in data.items():
                k_lower = str(k).lower().strip()
                if k_lower in _FORBIDDEN_OPERATIONAL_ACTIONS:
                    failure_reasons.append(f"SAFETY_FORBIDDEN_OPERATIONAL_ACTION_{_sanitize_reason_code(k_lower)}")
                self._check_safety_and_operational_protection(v, failure_reasons)

        elif isinstance(data, (list, tuple)):
            for item in data:
                self._check_safety_and_operational_protection(item, failure_reasons)

        elif isinstance(data, str):
            if contains_prompt_injection(data):
                if "SAFETY_PROMPT_INJECTION_DETECTED" not in failure_reasons:
                    failure_reasons.append("SAFETY_PROMPT_INJECTION_DETECTED")
            if _SECRET_RE.search(data):
                if "SAFETY_SECRET_LEAKAGE_DETECTED" not in failure_reasons:
                    failure_reasons.append("SAFETY_SECRET_LEAKAGE_DETECTED")

    def validate(
        self,
        request: LLMRequest,
        response: LLMResponse,
        approved_evidence_ids: Optional[Set[str]] = None,
        local_signals: Optional[Dict[str, Any]] = None,
        prior_attempt_count: int = 0,
        subtask_id: Optional[str] = None,
    ) -> OutputValidationResult:
        """
        Synchronous validation of an LLMResponse against an LLMRequest.
        Executes strict multi-phase verification:
        1. Base metrics & finish reason
        2. Request/response identity & provenance (S2.5.1 B5 & B6)
        3. Raw size & control sequence bounds
        4. Local single-fence normalization
        5. Strict duplicate-key syntax parsing
        6. Task-specific schema validation (S2.5.1 B7 & B8)
        7. Evidence references grounding check (S2.5.1 B1 & B2)
        8. Local opt-out / bounce precedence (S2.5.1 B3 & B4)
        9. Security & operational protection
        10. Bounded repair proposal evaluation
        """
        failure_reasons: List[str] = []
        needs_business_review = False
        is_opt_out_overridden = False
        is_bounce_overridden = False
        is_suppression_overridden = False
        effective_local_disposition: Optional[str] = None
        normalization_applied = False
        validated_data: Optional[Dict[str, Any]] = None

        raw_content = response.content or ""
        raw_len = len(raw_content)

        # Collect safe metrics (S2.5.2 B1 & B2: never put untrusted provider_request_id in metrics)
        has_prov_id = bool(response.provider_request_id and str(response.provider_request_id).strip())
        metrics = {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "cost_usd": response.cost_usd,  # Stays None if unmeasured, never 0.0
            "has_provider_request_id": has_prov_id,
            "provider_request_id": None,
        }

        # -------------------------------------------------------------------
        # 1. Finish Reason Checks
        # -------------------------------------------------------------------
        if response.finish_reason:
            fr = response.finish_reason.lower().strip()
            if fr in ("length", "max_tokens"):
                failure_reasons.append("FINISH_REASON_LENGTH_TRUNCATED")
            elif fr in ("content_filter", "refusal"):
                failure_reasons.append("FINISH_REASON_REFUSAL")

        # -------------------------------------------------------------------
        # 2. Identity & Provenance Verification (Section 2.1 / S2.5.1 B5 & B6)
        # -------------------------------------------------------------------
        if not response.request_id or response.request_id != request.request_id:
            failure_reasons.append("IDENTITY_MISMATCH_REQUEST_ID")

        try:
            req_canonical_task = normalize_task_id(request.task_id)
            resp_canonical_task = normalize_task_id(response.task_id)
            if req_canonical_task != resp_canonical_task:
                failure_reasons.append("IDENTITY_MISMATCH_TASK_ID")
            canonical_task = req_canonical_task
        except ValueError:
            failure_reasons.append("IDENTITY_MISMATCH_TASK_ID")
            canonical_task = "unknown"

        # S2.5.1 B5: Explicit pinned provider and model required on request
        if not request.provider or not str(request.provider).strip():
            failure_reasons.append("MISSING_APPROVED_PROVIDER_IDENTITY")
        elif response.provider != request.provider:
            failure_reasons.append("IDENTITY_MISMATCH_PROVIDER")

        if not request.model or not str(request.model).strip():
            failure_reasons.append("MISSING_APPROVED_MODEL_IDENTITY")
        elif response.model != request.model:
            failure_reasons.append("IDENTITY_MISMATCH_MODEL")

        # S2.5.1 B6: Subtask resolution and consistency
        req_meta_subtask = None
        if request.metadata and "subtask_id" in request.metadata:
            raw_sub = request.metadata["subtask_id"]
            if raw_sub is not None:
                req_meta_subtask = str(raw_sub).strip().lower()

        arg_subtask = None
        if subtask_id is not None:
            arg_subtask = str(subtask_id).strip().lower()

        # Detect inconsistency if both are provided
        if req_meta_subtask is not None and arg_subtask is not None and req_meta_subtask != arg_subtask:
            failure_reasons.append("IDENTITY_MISMATCH_SUBTASK")
            resolved_subtask = req_meta_subtask
        else:
            resolved_subtask = arg_subtask if arg_subtask is not None else req_meta_subtask

        if canonical_task == "website_extraction":
            try:
                resolved_subtask = validate_subtask_id(canonical_task, resolved_subtask)
                if not resolved_subtask:
                    failure_reasons.append("IDENTITY_MISMATCH_SUBTASK")
            except ValueError:
                failure_reasons.append("IDENTITY_MISMATCH_SUBTASK")
        else:
            # Non-website tasks do not define subtasks; reject unexpected subtasks
            if resolved_subtask is not None:
                failure_reasons.append("IDENTITY_MISMATCH_SUBTASK")

        # -------------------------------------------------------------------
        # 3. Raw Size & Control Sequence Bounds (Section 2.2)
        # -------------------------------------------------------------------
        raw_bytes = raw_content.encode("utf-8")
        if len(raw_bytes) > self.max_content_bytes:
            failure_reasons.append("OUTPUT_SIZE_EXCEEDED")

        if "\x00" in raw_content:
            failure_reasons.append("SYNTAX_INVALID_CONTROL_CHARS")

        # -------------------------------------------------------------------
        # 4. Local Single-Fence Normalization (Section 3)
        # -------------------------------------------------------------------
        cleaned_content = raw_content.strip()

        # Match single whole-response Markdown code fence:
        fence_match = re.match(r"^```(?:json)?\s*\n?([\s\S]*?)\n?\s*```$", cleaned_content, re.IGNORECASE)
        if fence_match:
            # Whole response was enclosed in code fence
            cleaned_content = fence_match.group(1).strip()
            normalization_applied = True
        elif "```" in raw_content:
            # Code fence exists but is surrounded by commentary or multiple fences
            # Do NOT strip; leave as-is so strict single JSON decoder detects commentary
            pass

        # -------------------------------------------------------------------
        # 5. Strict Syntax Parsing & Duplicate Key Rejection (Section 2.2)
        # -------------------------------------------------------------------
        parsed_dict: Optional[Dict[str, Any]] = None
        if not cleaned_content:
            failure_reasons.append("SYNTAX_INVALID_JSON")
        else:
            try:
                decoder = json.JSONDecoder(
                    object_pairs_hook=_detect_duplicate_keys,
                    parse_constant=_reject_constant,
                )
                obj, end_idx = decoder.raw_decode(cleaned_content)

                # Check if there is trailing content after the first object
                remaining = cleaned_content[end_idx:].strip()
                if remaining:
                    if remaining.startswith("{"):
                        failure_reasons.append("SYNTAX_MULTIPLE_OBJECTS")
                    else:
                        failure_reasons.append("SYNTAX_COMMENTARY_OUTSIDE_JSON")

                if not isinstance(obj, dict):
                    failure_reasons.append("SYNTAX_NON_OBJECT_ROOT")
                else:
                    parsed_dict = obj

            except json.JSONDecodeError:
                failure_reasons.append("SYNTAX_INVALID_JSON")
            except ValueError as e:
                err_str = str(e)
                if "DUPLICATE_KEY" in err_str:
                    failure_reasons.append("SYNTAX_DUPLICATE_KEY")
                elif "ILLEGAL_CONSTANT" in err_str:
                    failure_reasons.append("SYNTAX_INVALID_CONSTANT_NAN")
                else:
                    failure_reasons.append("SYNTAX_INVALID_JSON")

        # Check nesting depth and collection bounds on parsed object
        if parsed_dict is not None:
            try:
                self._check_nesting_and_size(parsed_dict)
            except ValueError as e:
                err_name = str(e)
                if err_name not in failure_reasons:
                    failure_reasons.append(err_name)

        # -------------------------------------------------------------------
        # 6. Task Schema Validation (Section 2.3 / S2.5.1 B7 & B8)
        # -------------------------------------------------------------------
        if parsed_dict is not None and not any(r.startswith("SYNTAX_") for r in failure_reasons):
            try:
                schema_cls = SchemaRegistry.get_schema(canonical_task, resolved_subtask)
            except ValueError:
                failure_reasons.append(f"SCHEMA_UNREGISTERED_TASK_{_sanitize_reason_code(canonical_task)}")
                schema_cls = None

            if schema_cls:
                try:
                    # Validate against strict authoritative schema (strict=True enforces types, B8)
                    validated_instance = schema_cls.model_validate(parsed_dict)
                    validated_data = validated_instance.model_dump()
                except ValidationError as ve:
                    for err in ve.errors():
                        err_type = err.get("type", "")
                        loc_parts = [str(p) for p in err.get("loc", [])]

                        if "extra_forbidden" in err_type:
                            # S2.5.1 B7: NEVER echo attacker-controlled key names in failure reasons!
                            failure_reasons.append("SCHEMA_EXTRA_FIELD")
                        else:
                            # For other schema errors, verify field is in known schema fields
                            raw_field = "_".join(loc_parts) if loc_parts else "ROOT"
                            clean_field = re.sub(r"[^A-Za-z0-9_]", "", raw_field).upper()[:40]
                            known_fields = set(f.upper() for f in schema_cls.model_fields.keys())
                            if loc_parts and loc_parts[0].upper() in known_fields:
                                safe_field_label = clean_field
                            else:
                                safe_field_label = "FIELD"

                            if "missing" in err_type:
                                failure_reasons.append(f"SCHEMA_MISSING_REQUIRED_FIELD_{safe_field_label}")
                            elif "enum" in err_type or "literal_error" in err_type:
                                failure_reasons.append(f"SCHEMA_INVALID_ENUM_{safe_field_label}")
                            elif any(b in err_type for b in ["greater_than", "less_than", "length", "too_short", "too_long", "too_many"]):
                                failure_reasons.append(f"SCHEMA_BOUNDS_VIOLATION_{safe_field_label}")
                            else:
                                failure_reasons.append(f"SCHEMA_TYPE_ERROR_{safe_field_label}")

        # -------------------------------------------------------------------
        # 7. Evidence Grounding & Reference Integrity (Section 2.4 / S2.5.1 B1 & B2)
        # -------------------------------------------------------------------
        if validated_data is not None:
            refs = validated_data.get("evidence_refs") or []
            if refs:
                # S2.5.1 B1: Any nonempty cited references MUST fail if no approved evidence set is supplied
                if approved_evidence_ids is None or len(approved_evidence_ids) == 0:
                    failure_reasons.append("EVIDENCE_APPROVED_SET_REQUIRED")
                else:
                    # S2.5.1 B2: approved_evidence_ids passed as argument is AUTHORITATIVE.
                    # Request metadata MUST NOT broaden or override it!
                    trusted_ids = set(approved_evidence_ids)
                    seen_refs = set()
                    for ref in refs:
                        ref_str = str(ref).strip() if ref is not None else ""
                        if not ref_str:
                            failure_reasons.append("EVIDENCE_MALFORMED_REF")
                            continue
                        if ref_str in seen_refs:
                            failure_reasons.append("EVIDENCE_DUPLICATE_REF")
                        seen_refs.add(ref_str)

                        if ref_str not in trusted_ids:
                            failure_reasons.append("EVIDENCE_UNKNOWN_REF")

                # Schema validation cannot establish factual claim truth on its own
                needs_business_review = True

        # -------------------------------------------------------------------
        # 8. Local Opt-Out, Bounce & Suppression Precedence (Section 2.4 / S2.5.1 B3 & B4)
        # -------------------------------------------------------------------
        if canonical_task == "inbox_classification" and validated_data is not None:
            local_sigs = local_signals or {}
            opt_out_detected = bool(local_sigs.get("is_opt_out_detected") or (request.metadata and request.metadata.get("is_opt_out_detected")))
            bounce_detected = bool(local_sigs.get("is_bounce_detected") or (request.metadata and request.metadata.get("is_bounce_detected")))
            suppressed = bool(local_sigs.get("is_suppressed") or (request.metadata and request.metadata.get("is_suppressed")))

            model_cat = validated_data.get("category")

            # Deterministic precedence: 1. Opt-out, 2. Bounce, 3. Suppression
            if opt_out_detected:
                # S2.5.1 B3: Opt-out overrides EVERY conflicting category (including not_now, not_interested, unknown, auto_reply, bounce)
                if model_cat != "unsubscribe":
                    failure_reasons.append("OPT_OUT_OVERRIDE_PREVENTED")
                    is_opt_out_overridden = True
                    effective_local_disposition = "unsubscribe"
                    needs_business_review = True
            elif bounce_detected:
                # S2.5.1 B4: Bounce overrides conflicting positive, inquiry, or meeting categories
                if model_cat != "bounce":
                    failure_reasons.append("BOUNCE_OVERRIDE_PREVENTED")
                    is_bounce_overridden = True
                    effective_local_disposition = "bounce"
                    needs_business_review = True
            elif suppressed:
                # S2.5.1 B4: Suppression blocks positive engagement, follow-up, or inquiry categories
                if model_cat in ("positive_interest", "meeting_proposed", "referral", "question", "not_now"):
                    failure_reasons.append("SUPPRESSION_OVERRIDE_PREVENTED")
                    is_suppression_overridden = True
                    effective_local_disposition = "do_not_contact"
                    needs_business_review = True

        # -------------------------------------------------------------------
        # 9. Security & Operational Protection (Section 2.4 & 4)
        # -------------------------------------------------------------------
        if validated_data is not None:
            self._check_safety_and_operational_protection(validated_data, failure_reasons)

        # -------------------------------------------------------------------
        # 10. Bounded Repair Proposal Evaluation (Section 3)
        # -------------------------------------------------------------------
        is_valid = len(failure_reasons) == 0
        repair_proposal = None
        if not is_valid:
            # Sanitize reason codes before repair policy evaluation (S2.5.1 B7)
            sanitized_failure_reasons = sorted(list(set(_sanitize_reason_code(r) for r in failure_reasons)))
            repair_proposal = self.repair_policy.evaluate_repair(
                request=request,
                response=response,
                failure_reasons=sanitized_failure_reasons,
                prior_attempt_count=prior_attempt_count,
                subtask_id=resolved_subtask,
            )
            # Fail closed: scrub validated data on failure
            validated_data = None
        else:
            sanitized_failure_reasons = []

        return OutputValidationResult(
            is_valid=is_valid,
            task_id=canonical_task,
            subtask_id=resolved_subtask,
            schema_version=SCHEMA_REGISTRY_VERSION,
            validated_data=validated_data,
            failure_reasons=sanitized_failure_reasons,
            needs_business_review=needs_business_review,
            is_opt_out_overridden=is_opt_out_overridden,
            is_bounce_overridden=is_bounce_overridden,
            is_suppression_overridden=is_suppression_overridden,
            effective_local_disposition=effective_local_disposition,
            normalization_applied=normalization_applied,
            repair_proposal=repair_proposal,
            raw_content_length=raw_len,
            metrics=metrics,
            validator_version=OUTPUT_VALIDATOR_VERSION,
        )

    async def validate_async(
        self,
        request: LLMRequest,
        response: LLMResponse,
        approved_evidence_ids: Optional[Set[str]] = None,
        local_signals: Optional[Dict[str, Any]] = None,
        prior_attempt_count: int = 0,
        subtask_id: Optional[str] = None,
    ) -> OutputValidationResult:
        """
        Asynchronous validation entrypoint ensuring strict sync/async validation parity.
        Because validation is purely in-memory computation, delegating synchronously
        guarantees deterministic, identical results.
        """
        return self.validate(
            request=request,
            response=response,
            approved_evidence_ids=approved_evidence_ids,
            local_signals=local_signals,
            prior_attempt_count=prior_attempt_count,
            subtask_id=subtask_id,
        )
