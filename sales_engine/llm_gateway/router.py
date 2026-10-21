"""
sales_engine/llm_gateway/router.py
Immutable task-based model router for SalesAI LLM Gateway.
Enforces Tier-1-first defaults, explicit operator approval, strict route validation,
and zero automatic tier escalation.
Implements S2.3.1 routing integrity hardening: strict verification of candidate ID,
provider, exact model, tier, task eligibility, and operator authorization on registration
and resolution.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from sales_engine.llm_gateway.contracts import ModelTier
from sales_engine.llm_gateway.errors import LLMNotConfiguredError, LLMRoutingIntegrityError
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    normalize_task_id,
    validate_subtask_id,
)


class TaskRouteDecision(BaseModel):
    """
    Immutable routing decision for an approved model assignment.
    Guarantees that production execution uses only explicitly approved models.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(..., min_length=1, max_length=64)
    subtask_id: Optional[str] = Field(default=None, max_length=64)
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
    Every route approval and resolution strictly verifies catalog candidate consistency.
    """

    def __init__(self, catalog: Optional[ModelCatalog] = None):
        self.catalog = catalog or ModelCatalog.create_default_16_slot_catalog()
        self._approved_routes: Dict[str, TaskRouteDecision] = {}

    def _make_route_key(self, task_id: str, subtask_id: Optional[str] = None) -> str:
        canonical = normalize_task_id(task_id)
        valid_subtask = validate_subtask_id(canonical, subtask_id)
        return f"{canonical}:{valid_subtask}" if valid_subtask else canonical

    def set_approved_route(self, decision: TaskRouteDecision) -> None:
        """
        Record an explicit operator-approved routing decision for a task.
        Strictly validates operator authorization, candidate presence, candidate enablement,
        task eligibility, and that provider, model and tier match the catalog candidate.
        """
        # 1. Validate operator provenance
        if not decision.approved_by or not decision.approved_by.strip():
            raise LLMRoutingIntegrityError(
                "Cannot approve route: missing authenticated operator provenance (approved_by).",
                task_id=decision.task_id,
            )

        # 2. Validate approval status
        if decision.approval_status != "approved":
            raise LLMRoutingIntegrityError(
                f"Cannot register route with non-approved status: '{decision.approval_status}'.",
                task_id=decision.task_id,
            )

        # 3. Canonical task & subtask
        canonical = normalize_task_id(decision.task_id)
        valid_subtask = validate_subtask_id(canonical, decision.subtask_id)
        route_key = f"{canonical}:{valid_subtask}" if valid_subtask else canonical

        # 4. Lookup candidate in catalog
        candidate = self.catalog.get_candidate(decision.candidate_id)
        if not candidate:
            raise LLMRoutingIntegrityError(
                f"Cannot approve candidate '{decision.candidate_id}': not found in catalog.",
                task_id=canonical,
            )

        if not candidate.enabled:
            raise LLMRoutingIntegrityError(
                f"Cannot approve candidate '{decision.candidate_id}': candidate is disabled in catalog.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        # 5. Validate candidate task eligibility
        if not candidate.is_task_allowed(canonical):
            raise LLMRoutingIntegrityError(
                f"Candidate '{decision.candidate_id}' is not authorized for task '{canonical}'.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        # 6. Validate route attributes match catalog candidate exactly
        if decision.tier != candidate.tier:
            raise LLMRoutingIntegrityError(
                f"Route tier mismatch: decision tier '{decision.tier.value}' != candidate tier '{candidate.tier.value}'.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        if decision.provider_id != candidate.provider_id:
            raise LLMRoutingIntegrityError(
                f"Route provider mismatch: decision provider '{decision.provider_id}' != candidate provider '{candidate.provider_id}'.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        if decision.model != candidate.exact_model_id:
            raise LLMRoutingIntegrityError(
                f"Route model mismatch: decision model '{decision.model}' != candidate exact_model_id '{candidate.exact_model_id}'.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        # 7. Store immutable decision
        self._approved_routes[route_key] = decision

    def remove_approved_route(
        self,
        task_id: str,
        subtask_id: Optional[str] = None,
    ) -> Optional[TaskRouteDecision]:
        """Remove approved route for a task (reverts to unapproved state)."""
        route_key = self._make_route_key(task_id, subtask_id)
        return self._approved_routes.pop(route_key, None)

    def resolve_approved(
        self,
        task_id: str,
        subtask_id: Optional[str] = None,
    ) -> TaskRouteDecision:
        """
        Resolve the active approved model for a task.
        Fails closed with LLMNotConfiguredError if no route has been approved.
        Re-validates catalog consistency to prevent drift or post-approval disablement.
        Zero automatic tier escalation.
        """
        canonical = normalize_task_id(task_id)
        valid_subtask = validate_subtask_id(canonical, subtask_id)
        route_key = f"{canonical}:{valid_subtask}" if valid_subtask else canonical

        decision = self._approved_routes.get(route_key)
        if not decision:
            raise LLMNotConfiguredError(
                message=(
                    f"No operator-approved model route configured for task '{route_key}'. "
                    "All SalesAI tasks start at Tier 1 (Budget) and require explicit evaluation approval."
                ),
                task_id=canonical,
            )

        # Re-check candidate in catalog to guard against post-approval mutations or disablement
        candidate = self.catalog.get_candidate(decision.candidate_id)
        if not candidate:
            raise LLMRoutingIntegrityError(
                message=f"Approved candidate '{decision.candidate_id}' was removed from catalog.",
                provider=decision.provider_id,
                task_id=canonical,
            )

        if not candidate.enabled:
            raise LLMNotConfiguredError(
                message=(
                    f"Approved model candidate '{decision.candidate_id}' for task '{canonical}' "
                    "is currently disabled in catalog."
                ),
                provider=decision.provider_id,
                task_id=canonical,
            )

        if (
            candidate.tier != decision.tier
            or candidate.provider_id != decision.provider_id
            or candidate.exact_model_id != decision.model
        ):
            raise LLMRoutingIntegrityError(
                message=(
                    f"Approved route for task '{canonical}' violates catalog consistency: "
                    "candidate attributes were mutated in catalog after approval."
                ),
                provider=decision.provider_id,
                task_id=canonical,
            )

        if not candidate.is_task_allowed(canonical):
            raise LLMRoutingIntegrityError(
                message=f"Approved candidate '{decision.candidate_id}' is no longer authorized for task '{canonical}'.",
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

    def get_active_tier_for_task(
        self,
        task_id: str,
        subtask_id: Optional[str] = None,
    ) -> ModelTier:
        """Return the tier of the active approved route, or default to TIER_1_BUDGET."""
        route_key = self._make_route_key(task_id, subtask_id)
        decision = self._approved_routes.get(route_key)
        if decision:
            return decision.tier
        return ModelTier.TIER_1_BUDGET
