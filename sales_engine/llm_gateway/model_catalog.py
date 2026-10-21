"""
sales_engine/llm_gateway/model_catalog.py
Deterministic 16-slot candidate model catalog and canonical task taxonomy for SalesAI.
Supports four tiers, four ordered candidates per tier, task-specific priorities, and zero HTTP calls.
"""

from enum import Enum
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field, ConfigDict, field_validator

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


class PricingMetadata(BaseModel):
    """
    Verified model pricing metadata.
    Unknown prices must remain None (never silently 0.0).
    """

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="USD", max_length=8)
    input_per_1m: Optional[float] = Field(default=None, ge=0.0)
    output_per_1m: Optional[float] = Field(default=None, ge=0.0)
    effective_date: Optional[str] = Field(default=None, max_length=32)
    source_url: Optional[str] = Field(default=None, max_length=256)


class ModelCandidate(BaseModel):
    """
    A specific model candidate slot within a tier.
    Zero fabricated model names, zero synthetic performance metrics.
    """

    model_config = ConfigDict(extra="forbid")

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
    """

    model_config = ConfigDict(extra="forbid")

    candidates: Dict[str, ModelCandidate] = Field(default_factory=dict)
    task_priority_overrides: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="Per-task candidate ordering: task_key -> tier_value -> list of candidate_ids",
    )

    def register_candidate(self, candidate: ModelCandidate, allow_override: bool = False) -> None:
        """Register a model candidate. Rejects duplicate candidate_ids by default."""
        cid = candidate.candidate_id
        if cid in self.candidates and not allow_override:
            raise ValueError(f"Candidate '{cid}' already exists in catalog. Override rejected.")
        self.candidates[cid] = candidate

    def get_candidate(self, candidate_id: str) -> Optional[ModelCandidate]:
        """Lookup candidate by candidate_id."""
        return self.candidates.get(candidate_id)

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

        # Collect candidates in this tier allowed for this task
        tier_candidates = [
            c
            for c in self.candidates.values()
            if c.tier == tier and c.is_task_allowed(canonical_task)
        ]
        if only_enabled:
            tier_candidates = [c for c in tier_candidates if c.enabled]

        # Check for task-specific ordering override
        task_overrides = self.task_priority_overrides.get(canonical_task, {}).get(tier_key)
        if task_overrides:
            candidate_map = {c.candidate_id: c for c in tier_candidates}
            ordered = []
            for cid in task_overrides:
                if cid in candidate_map:
                    ordered.append(candidate_map[cid])
            # Append any candidates not explicitly listed in override
            for c in sorted(tier_candidates, key=lambda x: x.priority):
                if c not in ordered:
                    ordered.append(c)
            return ordered

        # Default ordering: by candidate priority (1..4), then candidate_id
        return sorted(tier_candidates, key=lambda c: (c.priority, c.candidate_id))

    @classmethod
    def create_default_16_slot_catalog(cls) -> "ModelCatalog":
        """
        Create standard 16-slot catalog (4 tiers x 4 candidate slots per tier).
        All slots are explicitly defined and DISABLED by default (fail-closed).
        """
        catalog = cls()

        # Slot definitions (Tier 1..4, Slots 1..4)
        slots = [
            # TIER 1 (Budget)
            ("t1_c1_openai_mini", ModelTier.TIER_1_BUDGET, 1, "openai", "gpt-4o-mini", "openai_compatible"),
            ("t1_c2_anthropic_haiku", ModelTier.TIER_1_BUDGET, 2, "anthropic", "claude-3-5-haiku-20241022", "anthropic_native"),
            ("t1_c3_google_flash", ModelTier.TIER_1_BUDGET, 3, "google", "gemini-2.0-flash", "gemini_native"),
            ("t1_c4_mistral_small", ModelTier.TIER_1_BUDGET, 4, "mistral", "mistral-small-latest", "mistral_native"),

            # TIER 2 (Balanced)
            ("t2_c1_openai_standard", ModelTier.TIER_2_BALANCED, 1, "openai", "gpt-4o", "openai_compatible"),
            ("t2_c2_anthropic_sonnet", ModelTier.TIER_2_BALANCED, 2, "anthropic", "claude-3-5-sonnet-20241022", "anthropic_native"),
            ("t2_c3_google_pro", ModelTier.TIER_2_BALANCED, 3, "google", "gemini-1.5-pro", "gemini_native"),
            ("t2_c4_mistral_large", ModelTier.TIER_2_BALANCED, 4, "mistral", "mistral-large-latest", "mistral_native"),

            # TIER 3 (High Fidelity)
            ("t3_c1_anthropic_sonnet_hf", ModelTier.TIER_3_HIGH_FIDELITY, 1, "anthropic", "claude-3-5-sonnet-20241022", "anthropic_native"),
            ("t3_c2_openai_flagship_hf", ModelTier.TIER_3_HIGH_FIDELITY, 2, "openai", "gpt-4o", "openai_compatible"),
            ("t3_c3_google_pro_hf", ModelTier.TIER_3_HIGH_FIDELITY, 3, "google", "gemini-1.5-pro", "gemini_native"),
            ("t3_c4_mistral_large_hf", ModelTier.TIER_3_HIGH_FIDELITY, 4, "mistral", "mistral-large-latest", "mistral_native"),

            # TIER 4 (Flagship / Complex Arbitration)
            ("t4_c1_openai_o1", ModelTier.TIER_4_FLAGSHIP, 1, "openai", "o1", "openai_compatible"),
            ("t4_c2_anthropic_opus", ModelTier.TIER_4_FLAGSHIP, 2, "anthropic", "claude-3-opus-20240229", "anthropic_native"),
            ("t4_c3_openai_o3_mini", ModelTier.TIER_4_FLAGSHIP, 3, "openai", "o3-mini", "openai_compatible"),
            ("t4_c4_google_ultra", ModelTier.TIER_4_FLAGSHIP, 4, "google", "gemini-ultra", "gemini_native"),
        ]

        for cid, tier, prio, prov, exact_m, adapter in slots:
            catalog.register_candidate(
                ModelCandidate(
                    candidate_id=cid,
                    tier=tier,
                    priority=prio,
                    provider_id=prov,
                    exact_model_id=exact_m,
                    enabled=False,  # Strict fail-closed default
                    provider_adapter_kind=adapter,
                    allowed_tasks=["*"],
                    verified_capabilities={
                        "supports_json_mode": True,
                        "supports_temperature": True if not exact_m.startswith("o1") else False,
                    },
                )
            )

        return catalog
