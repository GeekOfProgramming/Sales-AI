"""
sales_engine/llm_gateway/repair_policy.py
Bounded, auditable repair policy for SalesAI LLM Gateway task outputs.
Strictly offline, fail-closed, zero network/IO.
Implements S2.5: formatting repair proposal evaluation without dispatching any API calls,
auto-escalating tiers, or mutating production records.
"""

from typing import Optional, List, Dict, Any, Set
from pydantic import BaseModel, Field, ConfigDict

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse

REPAIR_POLICY_VERSION = "1.0.1"

# Failure codes that are purely formatting / syntax and potentially repairable
FORMATTING_FAILURE_CODES: Set[str] = {
    "SYNTAX_INVALID_JSON",
    "SYNTAX_COMMENTARY_OUTSIDE_JSON",
    "SYNTAX_MULTIPLE_OBJECTS",
    "SYNTAX_NON_OBJECT_ROOT",
    "SYNTAX_DUPLICATE_KEY",
    "SCHEMA_MISSING_REQUIRED_FIELD",
    "SCHEMA_EXTRA_FIELD",
    "SCHEMA_TYPE_ERROR",
    "SCHEMA_INVALID_ENUM",
    "SCHEMA_BOUNDS_VIOLATION",
}

# Failure codes that represent semantic, business, grounding, or security violations
# These MUST NEVER be auto-repaired; they require manual review or benchmark escalation.
NON_REPAIRABLE_FAILURE_CODES: Set[str] = {
    "IDENTITY_MISMATCH_REQUEST_ID",
    "IDENTITY_MISMATCH_TASK_ID",
    "IDENTITY_MISMATCH_PROVIDER",
    "IDENTITY_MISMATCH_MODEL",
    "IDENTITY_MISMATCH_SUBTASK",
    "MISSING_APPROVED_PROVIDER_IDENTITY",
    "MISSING_APPROVED_MODEL_IDENTITY",
    "FINISH_REASON_LENGTH_TRUNCATED",
    "FINISH_REASON_REFUSAL",
    "EVIDENCE_UNKNOWN_REF",
    "EVIDENCE_FABRICATED_CITATION",
    "EVIDENCE_APPROVED_SET_REQUIRED",
    "EVIDENCE_MALFORMED_REF",
    "EVIDENCE_DUPLICATE_REF",
    "OPT_OUT_OVERRIDE_PREVENTED",
    "BOUNCE_OVERRIDE_PREVENTED",
    "SUPPRESSION_OVERRIDE_PREVENTED",
    "SAFETY_PROMPT_INJECTION_DETECTED",
    "SAFETY_SECRET_LEAKAGE_DETECTED",
    "OUTPUT_SIZE_EXCEEDED",
    "EXCESSIVE_NESTING_DEPTH",
    "EXCESSIVE_COLLECTION_SIZE",
    "ATTEMPT_LIMIT_EXCEEDED",
}


class RepairProposal(BaseModel):
    """
    Typed, auditable plan for a potential single repair attempt.
    S2.5 creates this proposal ONLY; dispatch_allowed is strictly False.
    Zero network calls are made by S2.5.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    request_id: str = Field(..., description="Correlating request identifier")
    task_id: str = Field(..., description="Canonical task identifier")
    subtask_id: Optional[str] = Field(default=None, description="Subtask identifier if applicable")
    provider: str = Field(..., description="Exact provider ID (zero provider escalation)")
    model: str = Field(..., description="Exact model ID (zero tier escalation)")
    attempt_count: int = Field(default=1, ge=0, le=1, description="Repair attempt count (capped strictly at 1)")
    repairable: bool = Field(default=False, description="True ONLY for format/syntax errors; False for safety/semantic")
    reason_codes: List[str] = Field(default_factory=list, description="Safe failure reason codes")
    suggested_repair_instruction: Optional[str] = Field(
        default=None,
        description="Clean, sanitized instruction (zero PII, secrets, or raw provider output)",
    )
    dispatch_allowed: bool = Field(
        default=False,
        description="Strictly False in S2.5; execution requires S2.6 authorization and budgeting",
    )
    requires_authorization: bool = Field(
        default=True,
        description="Always True; repair cannot execute without explicit authorization",
    )
    policy_version: str = Field(default=REPAIR_POLICY_VERSION)

    def safe_log_dict(self) -> Dict[str, Any]:
        """Return safe, non-sensitive dictionary suitable for application logging."""
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "provider": self.provider,
            "model": self.model,
            "attempt_count": self.attempt_count,
            "repairable": self.repairable,
            "reason_codes": list(self.reason_codes),
            "dispatch_allowed": self.dispatch_allowed,
            "requires_authorization": self.requires_authorization,
            "policy_version": self.policy_version,
        }

    def __repr__(self) -> str:
        return (
            f"RepairProposal(request_id={self.request_id!r}, task_id={self.task_id!r}, "
            f"provider={self.provider!r}, model={self.model!r}, attempt_count={self.attempt_count}, "
            f"repairable={self.repairable}, dispatch_allowed={self.dispatch_allowed})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class RepairPolicy:
    """
    Deterministic repair decision engine for SalesAI LLM Gateway.
    Evaluates whether a failed validation result is safely repairable,
    and returns a disabled RepairProposal without dispatching any network calls.
    """

    def __init__(self, max_attempts: int = 1):
        self.max_attempts = min(max_attempts, 1)  # Strictly capped at 1

    def evaluate_repair(
        self,
        request: LLMRequest,
        response: LLMResponse,
        failure_reasons: List[str],
        prior_attempt_count: int = 0,
        subtask_id: Optional[str] = None,
    ) -> Optional[RepairProposal]:
        """
        Evaluate if a failure is repairable.
        Returns a single disabled RepairProposal or None.
        Enforces:
        - attempt_count <= 1
        - Zero tier or model escalation (exact match to request)
        - Zero PII or raw provider text in suggested instruction
        - dispatch_allowed=False (pure offline planning)
        """
        if not failure_reasons:
            return None

        # Check attempt count ceiling
        if prior_attempt_count >= self.max_attempts:
            return RepairProposal(
                request_id=request.request_id,
                task_id=request.task_id,
                subtask_id=subtask_id,
                provider=request.provider or response.provider,
                model=request.model or response.model,
                attempt_count=prior_attempt_count,
                repairable=False,
                reason_codes=["ATTEMPT_LIMIT_EXCEEDED"] + list(failure_reasons),
                suggested_repair_instruction=None,
                dispatch_allowed=False,
                requires_authorization=True,
            )

        # Check if ANY non-repairable code is present
        has_non_repairable = any(
            code in NON_REPAIRABLE_FAILURE_CODES or code.startswith("IDENTITY_")
            or code.startswith("MISSING_") or code.startswith("EVIDENCE_")
            or code.startswith("SAFETY_") or code.startswith("OPT_OUT_")
            or code.startswith("BOUNCE_") or code.startswith("SUPPRESSION_")
            or code.startswith("FINISH_REASON_")
            for code in failure_reasons
        )

        if has_non_repairable:
            return RepairProposal(
                request_id=request.request_id,
                task_id=request.task_id,
                subtask_id=subtask_id,
                provider=request.provider or response.provider,
                model=request.model or response.model,
                attempt_count=prior_attempt_count + 1,
                repairable=False,
                reason_codes=list(failure_reasons),
                suggested_repair_instruction=None,
                dispatch_allowed=False,
                requires_authorization=True,
            )

        # Check if failures are known formatting/syntax issues
        is_formatting_only = all(
            any(code.startswith(fmt) for fmt in FORMATTING_FAILURE_CODES)
            for code in failure_reasons
        )

        repairable = is_formatting_only
        instruction = None
        if repairable:
            instruction = (
                "IMPORTANT JSON REPAIR: The previous output failed strict schema validation. "
                "Return ONLY one valid JSON object matching the exact schema. "
                "Do NOT include markdown fences, conversational text, commentary, or extra fields."
            )

        return RepairProposal(
            request_id=request.request_id,
            task_id=request.task_id,
            subtask_id=subtask_id,
            provider=request.provider or response.provider,
            model=request.model or response.model,
            attempt_count=prior_attempt_count + 1,
            repairable=repairable,
            reason_codes=list(failure_reasons),
            suggested_repair_instruction=instruction,
            dispatch_allowed=False,
            requires_authorization=True,
        )
