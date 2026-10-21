"""
sales_engine/llm_gateway/router.py
Immutable task-based model router for SalesAI LLM Gateway.
Enforces Tier-1-first defaults, explicit operator approval, and zero automatic tier escalation.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from sales_engine.llm_gateway.contracts import ModelTier
from sales_engine.llm_gateway.errors import LLMNotConfiguredError
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    normalize_task_id,
)


class TaskRouteDecision(BaseModel):
    """
    Immutable routing decision for an approved model assignment.
    Guarantees that production execution uses only explicitly approved models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(..., min_length=1, max_length=64)
    tier: ModelTier
    provider_id: str = Field(..., min_length=1, max_length=32)
    model: str = Field(..., min_length=1, max_length=128)
    candidate_id: str = Field(..., min_length=1, max_length=64)
    approval_status: str = Field(default="approved", max_length=32)
    approved_by: Optional[str] = Field(default=None, max_length=64)
    approved_at: Optional[str] = Field(default=None, max_length=32)


class TaskRouter:
    """
    Central task router governing model selection and production resolution.
    Strictly fail-closed: All tasks start at Tier 1; unapproved tasks cannot execute.
    """

    def __init__(self, catalog: Optional[ModelCatalog] = None):
        self.catalog = catalog or ModelCatalog.create_default_16_slot_catalog()
        self._approved_routes: Dict[str, TaskRouteDecision] = {}

    def set_approved_route(self, decision: TaskRouteDecision) -> None:
        """Record an explicit operator-approved routing decision for a task."""
        canonical = normalize_task_id(decision.task_id)
        # Verify candidate exists in catalog
        candidate = self.catalog.get_candidate(decision.candidate_id)
        if not candidate:
            raise ValueError(f"Cannot approve candidate '{decision.candidate_id}': not found in catalog.")
        self._approved_routes[canonical] = decision

    def remove_approved_route(self, task_id: str) -> Optional[TaskRouteDecision]:
        """Remove approved route for a task (reverts to unapproved state)."""
        canonical = normalize_task_id(task_id)
        return self._approved_routes.pop(canonical, None)

    def resolve_approved(self, task_id: str) -> TaskRouteDecision:
        """
        Resolve the active approved model for a task.
        Fails closed with LLMNotConfiguredError if no route has been approved.
        Zero automatic tier escalation.
        """
        canonical = normalize_task_id(task_id)
        decision = self._approved_routes.get(canonical)

        if not decision:
            raise LLMNotConfiguredError(
                message=(
                    f"No operator-approved model route configured for task '{canonical}'. "
                    "All SalesAI tasks start at Tier 1 (Budget) and require explicit evaluation approval."
                ),
                task_id=canonical,
            )

        # Check candidate enablement in catalog
        candidate = self.catalog.get_candidate(decision.candidate_id)
        if not candidate or not candidate.enabled:
            raise LLMNotConfiguredError(
                message=(
                    f"Approved model candidate '{decision.candidate_id}' for task '{canonical}' "
                    "is currently disabled in catalog."
                ),
                provider=decision.provider_id,
                task_id=canonical,
            )

        return decision

    def dry_run_candidates(
        self,
        task_id: str,
        tier: Optional[ModelTier] = None,
        only_enabled: bool = False,
    ) -> List[ModelCandidate]:
        """
        List candidate slots for a task at a given tier without making any network calls.
        If tier is None, defaults strictly to ModelTier.TIER_1_BUDGET.
        """
        canonical = normalize_task_id(task_id)
        target_tier = tier or ModelTier.TIER_1_BUDGET
        return self.catalog.get_candidates_for_tier(
            task_id=canonical,
            tier=target_tier,
            only_enabled=only_enabled,
        )

    def get_active_tier_for_task(self, task_id: str) -> ModelTier:
        """Return the tier of the active approved route, or default to TIER_1_BUDGET."""
        canonical = normalize_task_id(task_id)
        decision = self._approved_routes.get(canonical)
        if decision:
            return decision.tier
        return ModelTier.TIER_1_BUDGET
