"""
sales_engine/llm_gateway/model_catalog.py
Deterministic 16-slot candidate model catalog and canonical task taxonomy for SalesAI.
Supports four tiers, four ordered candidates per tier, task-specific priorities, and zero HTTP calls.
Implements S2.3.1 routing integrity hardening: immutable candidate models, neutral slot IDs,
strict subtask validation, and fail-closed candidate verification guards.
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

from sales_engine.llm_gateway.contracts import ModelTier


# Canonical task keys corresponding to S1 call map
CANONICAL_TASKS: Set[str] = {
    "website_extraction",
    "job_analysis",
    "discovery_query_generation",
    "inbox_classification",
    "reply_suggestion",
    "outreach_draft",
}

CANONICAL_SUBTASKS: Dict[str, Set[str]] = {
    "website_extraction": {"pass_a", "pass_b"},
}

# Aliases mapping legacy / S1 call map identifiers to canonical task keys
TASK_ALIASES: Dict[str, str] = {
    "llm-site-01": "website_extraction",
    "q01_website_pass_a": "website_extraction",
    "q01_website_pass_b": "website_extraction",
    "website_analysis": "website_extraction",
    "website_analyzer": "website_extraction",
    "llm-site-02": "job_analysis",
    "q02_job_posting": "job_analysis",
    "job_posting_analysis": "job_analysis",
    "job_analyzer": "job_analysis",
    "llm-site-03": "discovery_query_generation",
    "q03_discovery_queries": "discovery_query_generation",
    "discovery_queries": "discovery_query_generation",
    "query_generator": "discovery_query_generation",
    "llm-site-04": "inbox_classification",
    "q04_inbox_classification": "inbox_classification",
    "inbox_classifier": "inbox_classification",
    "llm-site-05": "reply_suggestion",
    "q05_reply_suggestion": "reply_suggestion",
    "suggestion_engine": "reply_suggestion",
    "llm-site-06": "outreach_draft",
    "q06_outreach_draft": "outreach_draft",
    "email_generator": "outreach_draft",
}


def normalize_task_id(task_id: str) -> str:
    """Normalize a task ID or alias into its canonical task key. Fails closed on unknown IDs."""
    if not task_id or not task_id.strip():
        raise ValueError("Task ID cannot be empty.")
    cleaned = task_id.strip().lower()
    if cleaned in CANONICAL_TASKS:
        return cleaned
    if cleaned in TASK_ALIASES:
        return TASK_ALIASES[cleaned]
    raise ValueError(f"Unknown task ID or alias: {task_id!r}. Must be one of {sorted(list(CANONICAL_TASKS))}")


def validate_subtask_id(task_id: str, subtask_id: Optional[str]) -> Optional[str]:
    """
    Validate subtask identifier against canonical subtask taxonomy.
    Fails closed on subtask mismatches or invalid assignments.
    """
    canonical = normalize_task_id(task_id)
    if subtask_id is None:
        return None
    cleaned_subtask = subtask_id.strip().lower()
    allowed = CANONICAL_SUBTASKS.get(canonical)
    if not allowed:
        raise ValueError(f"Task '{canonical}' does not define subtasks; received subtask '{subtask_id}'.")
    if cleaned_subtask not in allowed:
        raise ValueError(
            f"Invalid subtask '{subtask_id}' for task '{canonical}'. Must be one of {sorted(list(allowed))}."
        )
    return cleaned_subtask


class PricingMetadata(BaseModel):
    """
    Verified model pricing metadata.
    Unknown prices must remain None (never silently 0.0).
    Frozen to enforce immutability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(default="USD", max_length=8)
    input_per_1m: Optional[float] = Field(default=None, ge=0.0)
    output_per_1m: Optional[float] = Field(default=None, ge=0.0)
    effective_date: Optional[str] = Field(default=None, max_length=32)
    source_url: Optional[str] = Field(default=None, max_length=256)


class ModelCandidate(BaseModel):
    """
    A specific model candidate slot within a tier.
    Zero fabricated model names, zero synthetic performance metrics.
    Frozen to enforce immutability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(..., min_length=1, max_length=64)
    tier: ModelTier
    priority: int = Field(..., ge=1, le=4, description="Default priority slot within tier (1..4)")
    provider_id: str = Field(..., min_length=1, max_length=32)
    exact_model_id: str = Field(..., min_length=1, max_length=128)
    enabled: bool = Field(default=False, description="Default is disabled (fail-closed)")
    verified_capabilities: Dict[str, Any] = Field(default_factory=dict)
    provider_adapter_kind: str = Field(default="openai_compatible", max_length=64)
    allowed_tasks: List[str] = Field(default_factory=lambda: ["*"])
    pricing: Optional[PricingMetadata] = Field(default=None)
    verification_notes: Optional[str] = Field(default=None, max_length=256)

    @field_validator("allowed_tasks")
    @classmethod
    def validate_allowed_tasks(cls, v: List[str]) -> List[str]:
        if not v:
            return ["*"]
        normalized = []
        for t in v:
            if t == "*":
                normalized.append("*")
            else:
                normalized.append(normalize_task_id(t))
        return normalized

    @model_validator(mode="after")
    def validate_candidate_integrity(self) -> "ModelCandidate":
        """
        Validate candidate integrity.
        Disabled placeholders may contain unverified/placeholder fields.
        Enabled candidates MUST possess real provider IDs, non-placeholder exact model IDs,
        and verified capability evidence.
        """
        if self.enabled:
            unverified_placeholders = {"unassigned", "placeholder", "unknown", "unverified", "unverified_slot"}
            if not self.provider_id or self.provider_id.strip().lower() in unverified_placeholders:
                raise ValueError(
                    f"Cannot enable candidate '{self.candidate_id}' with unassigned/placeholder provider: '{self.provider_id}'."
                )
            if not self.exact_model_id or self.exact_model_id.strip().lower() in unverified_placeholders:
                raise ValueError(
                    f"Cannot enable candidate '{self.candidate_id}' with unverified/placeholder exact_model_id: '{self.exact_model_id}'."
                )
            if not self.verified_capabilities:
                raise ValueError(
                    f"Cannot enable candidate '{self.candidate_id}' without verified capabilities."
                )
        return self

    def is_task_allowed(self, task_id: str) -> bool:
        """Check if candidate is authorized for a specific canonical task."""
        canonical = normalize_task_id(task_id)
        if "*" in self.allowed_tasks:
            return True
        return canonical in self.allowed_tasks


class ModelCatalog(BaseModel):
    """
    In-memory catalog of candidate model slots across four tiers.
    Enforces 16 distinct slots, deterministic ordering, and per-task priority overrides.
    Maintains candidate immutability and alias mapping for legacy references.
    """

    model_config = ConfigDict(extra="forbid")

    candidates: Dict[str, ModelCandidate] = Field(default_factory=dict)
    task_priority_overrides: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="Per-task candidate ordering: task_key -> tier_value -> list of candidate_ids",
    )
    candidate_aliases: Dict[str, str] = Field(
        default_factory=dict,
        description="Aliases mapping legacy / S2.3 identifiers to neutral slot IDs",
    )

    def _resolve_id(self, candidate_id: str) -> str:
        """Resolve candidate ID through alias dictionary if present."""
        if candidate_id in self.candidates:
            return candidate_id
        if candidate_id in self.candidate_aliases:
            return self.candidate_aliases[candidate_id]
        return candidate_id

    def register_candidate(self, candidate: ModelCandidate, allow_override: bool = False) -> None:
        """
        Register a model candidate.
        Rejects duplicate candidate_ids or (tier, priority) slot collisions by default.
        """
        cid = candidate.candidate_id
        if cid in self.candidates and not allow_override:
            raise ValueError(f"Candidate '{cid}' already exists in catalog. Override rejected.")

        # Check for duplicate (tier, priority)
        for existing_id, existing in self.candidates.items():
            if (
                existing_id != cid
                and existing.tier == candidate.tier
                and existing.priority == candidate.priority
                and not allow_override
            ):
                raise ValueError(
                    f"Candidate slot conflict: slot (tier={candidate.tier.value}, priority={candidate.priority}) "
                    f"is already occupied by '{existing_id}'."
                )

        self.candidates[cid] = candidate

    def get_candidate(self, candidate_id: str) -> Optional[ModelCandidate]:
        """Lookup candidate by candidate_id or alias."""
        resolved = self._resolve_id(candidate_id)
        return self.candidates.get(resolved)

    def enable_candidate(
        self,
        candidate_id: str,
        provider_id: str,
        exact_model_id: str,
        verified_capabilities: Dict[str, Any],
        pricing: Optional[PricingMetadata] = None,
        verification_notes: Optional[str] = None,
        provider_adapter_kind: str = "openai_compatible",
        allowed_tasks: Optional[List[str]] = None,
    ) -> ModelCandidate:
        """
        Safely enable a candidate slot with validated provider metadata.
        Returns the newly created immutable ModelCandidate.
        """
        resolved = self._resolve_id(candidate_id)
        existing = self.candidates.get(resolved)
        if not existing:
            raise ValueError(f"Candidate '{candidate_id}' not found in catalog.")

        updated = ModelCandidate(
            candidate_id=existing.candidate_id,
            tier=existing.tier,
            priority=existing.priority,
            provider_id=provider_id,
            exact_model_id=exact_model_id,
            enabled=True,
            verified_capabilities=verified_capabilities,
            provider_adapter_kind=provider_adapter_kind,
            allowed_tasks=allowed_tasks or existing.allowed_tasks,
            pricing=pricing,
            verification_notes=verification_notes,
        )
        self.candidates[resolved] = updated
        return updated

    def disable_candidate(self, candidate_id: str) -> ModelCandidate:
        """Disable a candidate slot, preventing its use in production routes."""
        resolved = self._resolve_id(candidate_id)
        existing = self.candidates.get(resolved)
        if not existing:
            raise ValueError(f"Candidate '{candidate_id}' not found in catalog.")
        updated = existing.model_copy(update={"enabled": False})
        self.candidates[resolved] = updated
        return updated

    def set_task_priority_override(
        self,
        task_id: str,
        tier: ModelTier,
        candidate_ids: List[str],
    ) -> None:
        """
        Configure task-specific candidate ordering with strict validation.
        Validates that every candidate exists, belongs to the specified tier,
        and is authorized for the task.
        """
        canonical_task = normalize_task_id(task_id)
        if not candidate_ids:
            raise ValueError("candidate_ids list cannot be empty for priority override.")

        seen = set()
        for cid in candidate_ids:
            resolved_cid = self._resolve_id(cid)
            if resolved_cid in seen:
                raise ValueError(f"Duplicate candidate_id '{cid}' in priority override list.")
            seen.add(resolved_cid)

            candidate = self.get_candidate(resolved_cid)
            if not candidate:
                raise ValueError(f"Candidate '{cid}' not found in catalog.")
            if candidate.tier != tier:
                raise ValueError(
                    f"Candidate '{cid}' tier ({candidate.tier.value}) does not match override tier ({tier.value})."
                )
            if not candidate.is_task_allowed(canonical_task):
                raise ValueError(f"Candidate '{cid}' is not authorized for task '{canonical_task}'.")

        if canonical_task not in self.task_priority_overrides:
            self.task_priority_overrides[canonical_task] = {}
        self.task_priority_overrides[canonical_task][tier.value] = list(candidate_ids)

    def get_candidates_for_tier(
        self,
        task_id: str,
        tier: ModelTier,
        only_enabled: bool = False,
    ) -> List[ModelCandidate]:
        """
        Return ordered candidates for a specific canonical task and tier.
        Applies task-specific priority overrides if configured; otherwise sorts by candidate priority.
        """
        canonical_task = normalize_task_id(task_id)
        tier_key = tier.value

        tier_candidates = [
            c
            for c in self.candidates.values()
            if c.tier == tier and c.is_task_allowed(canonical_task)
        ]
        if only_enabled:
            tier_candidates = [c for c in tier_candidates if c.enabled]

        task_overrides = self.task_priority_overrides.get(canonical_task, {}).get(tier_key)
        if task_overrides:
            candidate_map = {c.candidate_id: c for c in tier_candidates}
            ordered = []
            for cid in task_overrides:
                resolved_cid = self._resolve_id(cid)
                if resolved_cid in candidate_map:
                    ordered.append(candidate_map[resolved_cid])
            for c in sorted(tier_candidates, key=lambda x: x.priority):
                if c not in ordered:
                    ordered.append(c)
            return list(ordered)

        return sorted(tier_candidates, key=lambda c: (c.priority, c.candidate_id))

    @classmethod
    def create_default_16_slot_catalog(cls) -> "ModelCatalog":
        """
        Create standard 16-slot catalog (4 tiers x 4 candidate slots per tier).
        Uses neutral slot IDs t1_c1..t4_c4.
        All slots are explicitly defined and DISABLED by default (fail-closed).
        All pricing and capabilities are unknown (None/empty) until verified.
        Maintains legacy alias mappings for backward compatibility.
        """
        catalog = cls()

        # Aliases mapping legacy S2.3 identifiers to neutral slot IDs
        catalog.candidate_aliases = {
            "t1_c1_openai_mini": "t1_c1",
            "t1_c2_anthropic_haiku": "t1_c2",
            "t1_c3_google_flash": "t1_c3",
            "t1_c4_mistral_small": "t1_c4",
            "t2_c1_openai_standard": "t2_c1",
            "t2_c2_anthropic_sonnet": "t2_c2",
            "t2_c3_google_pro": "t2_c3",
            "t2_c4_mistral_large": "t2_c4",
            "t3_c1_anthropic_sonnet_hf": "t3_c1",
            "t3_c2_openai_flagship_hf": "t3_c2",
            "t3_c3_google_pro_hf": "t3_c3",
            "t3_c4_mistral_large_hf": "t3_c4",
            "t4_c1_openai_o1": "t4_c1",
            "t4_c2_anthropic_opus": "t4_c2",
            "t4_c3_openai_o3_mini": "t4_c3",
            "t4_c4_google_ultra": "t4_c4",
        }

        tiers = [
            (ModelTier.TIER_1_BUDGET, 1),
            (ModelTier.TIER_2_BALANCED, 2),
            (ModelTier.TIER_3_HIGH_FIDELITY, 3),
            (ModelTier.TIER_4_FLAGSHIP, 4),
        ]

        for tier, tier_num in tiers:
            for slot_num in range(1, 5):
                cid = f"t{tier_num}_c{slot_num}"
                catalog.register_candidate(
                    ModelCandidate(
                        candidate_id=cid,
                        tier=tier,
                        priority=slot_num,
                        provider_id="unassigned",
                        exact_model_id="unverified_slot",
                        enabled=False,  # Strict fail-closed default
                        provider_adapter_kind="openai_compatible",
                        allowed_tasks=["*"],
                        verified_capabilities={},
                        pricing=None,
                        verification_notes=None,
                    )
                )

        return catalog
