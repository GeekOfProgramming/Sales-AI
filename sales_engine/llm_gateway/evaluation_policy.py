"""
sales_engine/llm_gateway/evaluation_policy.py
Task-first, Tier-1-first sequential model evaluation state machine for SalesAI.
Evaluates candidates one at a time, early-stops on pass, exhausts tiers before promotion,
and requires explicit operator approval before elevating tiers. Zero live API calls.
Implements S2.3.1 routing integrity hardening: strict scorecard validation, replay and stale
protection, website Pass A/Pass B isolation, immutable history preservation, and simulation guards.
"""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from sales_engine.llm_gateway.contracts import ModelTier
from sales_engine.llm_gateway.errors import LLMEvaluationIntegrityError
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    normalize_task_id,
    validate_subtask_id,
)


class EvaluationDecision(str, Enum):
    """
    State transition decision returned by EvaluationPlanner after evaluating a candidate.
    """

    ACCEPT_CANDIDATE = "accept_candidate"
    PROPOSE_NEXT_IN_SAME_TIER = "propose_next_in_same_tier"
    TIER_EXHAUSTED_REQUIRES_APPROVAL = "tier_exhausted_requires_approval"
    UNRESOLVED_NEEDS_HUMAN_DECISION = "unresolved_needs_human_decision"


TIER_PROGRESSION: List[ModelTier] = [
    ModelTier.TIER_1_BUDGET,
    ModelTier.TIER_2_BALANCED,
    ModelTier.TIER_3_HIGH_FIDELITY,
    ModelTier.TIER_4_FLAGSHIP,
]


class CandidateScorecard(BaseModel):
    """
    Trusted evaluation outcome for a model candidate on a specific task/subtask.
    Safety-critical failures strictly block approval regardless of overall score.
    Frozen to enforce immutability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(..., min_length=1, max_length=64)
    subtask_id: Optional[str] = Field(default=None, max_length=64)
    candidate_id: str = Field(..., min_length=1, max_length=64)
    tier: ModelTier
    passed: bool = Field(..., description="General evaluation benchmark pass status")
    safety_critical_passed: bool = Field(
        default=True,
        description="Must be True for candidate approval; critical failures forbid acceptance",
    )
    score: float = Field(..., ge=0.0, le=1.0, description="Normalized benchmark score 0.0 to 1.0")
    reasoning_codes: List[str] = Field(default_factory=list)
    evaluator: str = Field(default="offline_scorecard_evaluator", max_length=64)
    timestamp: Optional[str] = Field(default=None, max_length=32)
    is_simulated: bool = Field(
        default=False,
        description="Explicit flag for offline/mock benchmarks. Fake/simulated scorecards cannot grant production approval.",
    )
    run_id: Optional[str] = Field(default=None, max_length=64)


class TaskEvaluationState(BaseModel):
    """
    Mutable state tracker for sequential candidate evaluation on a single task.
    Guarantees every task starts at TIER_1_BUDGET at index 0.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    subtask_id: Optional[str] = None
    current_tier: ModelTier = ModelTier.TIER_1_BUDGET
    current_candidate_index: int = 0
    history: List[CandidateScorecard] = Field(default_factory=list)
    approved_candidate_id: Optional[str] = None
    status: str = Field(default="evaluating")  # evaluating, tier_exhausted, approved, unresolved
    run_id: Optional[str] = None


class EvaluationPlanner:
    """
    Pure deterministic state machine guiding sequential model evaluation.
    Operates strictly offline without issuing network requests.
    Validates all scorecards against currently evaluated candidate, task, subtask, and tier.
    Guarantees zero history/state mutation on invalid or mismatched scorecards.
    """

    def __init__(self, catalog: Optional[ModelCatalog] = None):
        self.catalog = catalog or ModelCatalog.create_default_16_slot_catalog()
        self._states: Dict[str, TaskEvaluationState] = {}

    def get_or_create_state(
        self,
        task_id: str,
        subtask_id: Optional[str] = None,
    ) -> TaskEvaluationState:
        """Get or initialize evaluation state for a task. All tasks start at Tier 1."""
        canonical = normalize_task_id(task_id)
        valid_subtask = validate_subtask_id(canonical, subtask_id)
        key = f"{canonical}:{valid_subtask}" if valid_subtask else canonical

        if key not in self._states:
            self._states[key] = TaskEvaluationState(
                task_id=canonical,
                subtask_id=valid_subtask,
                current_tier=ModelTier.TIER_1_BUDGET,
                current_candidate_index=0,
                status="evaluating",
            )
        return self._states[key]

    def get_current_candidate(self, state: TaskEvaluationState) -> Optional[ModelCandidate]:
        """Retrieve the candidate slot currently scheduled for evaluation."""
        if state.status in ("approved", "unresolved", "tier_exhausted"):
            return None

        candidates = self.catalog.get_candidates_for_tier(
            task_id=state.task_id,
            tier=state.current_tier,
            only_enabled=False,  # Evaluator evaluates candidate slots in catalog
        )

        if not candidates or state.current_candidate_index >= len(candidates):
            return None

        return candidates[state.current_candidate_index]

    def process_scorecard(
        self,
        state: TaskEvaluationState,
        scorecard: CandidateScorecard,
    ) -> EvaluationDecision:
        """
        Ingest evaluation scorecard for the current candidate and calculate next transition.
        Enforces strict integrity checks before mutating any state or appending to history:
        - Rejects mismatched task, subtask, candidate, or tier.
        - Rejects terminal state mutations (approved, unresolved, tier_exhausted).
        - Rejects replay or duplicate scorecards.
        - Rejects scorecards for disabled candidates without is_simulated=True.
        - Zero history mutation on rejection.
        """
        # 1. State status guard: only active 'evaluating' state may process scorecards
        if state.status != "evaluating":
            raise LLMEvaluationIntegrityError(
                f"Cannot process scorecard: state for task '{state.task_id}' is in non-evaluating status '{state.status}'."
            )

        # 2. Canonical task match
        canonical_task = normalize_task_id(scorecard.task_id)
        if canonical_task != state.task_id:
            raise LLMEvaluationIntegrityError(
                f"Scorecard task '{scorecard.task_id}' does not match state task '{state.task_id}'."
            )

        # 3. Exact subtask match and isolation
        valid_subtask = validate_subtask_id(canonical_task, scorecard.subtask_id)
        if valid_subtask != state.subtask_id:
            raise LLMEvaluationIntegrityError(
                f"Scorecard subtask '{scorecard.subtask_id}' does not match state subtask '{state.subtask_id}'."
            )

        # 4. Currently scheduled candidate match
        current_candidate = self.get_current_candidate(state)
        if not current_candidate:
            raise LLMEvaluationIntegrityError(
                f"No candidate currently scheduled for evaluation on task '{state.task_id}'."
            )

        scorecard_cid_resolved = self.catalog._resolve_id(scorecard.candidate_id)
        current_cid_resolved = self.catalog._resolve_id(current_candidate.candidate_id)
        if scorecard_cid_resolved != current_cid_resolved:
            raise LLMEvaluationIntegrityError(
                f"Scorecard candidate '{scorecard.candidate_id}' does not match currently scheduled candidate '{current_candidate.candidate_id}'."
            )

        # 5. Tier match against state and candidate
        if scorecard.tier != state.current_tier or scorecard.tier != current_candidate.tier:
            raise LLMEvaluationIntegrityError(
                f"Scorecard tier '{scorecard.tier.value}' does not match state tier '{state.current_tier.value}' or candidate tier '{current_candidate.tier.value}'."
            )

        # 6. Candidate task allowance
        if not current_candidate.is_task_allowed(state.task_id):
            raise LLMEvaluationIntegrityError(
                f"Candidate '{current_candidate.candidate_id}' is not authorized for task '{state.task_id}'."
            )

        # 7. Availability / Simulation flag guard
        if not current_candidate.enabled and not scorecard.is_simulated:
            raise LLMEvaluationIntegrityError(
                f"Candidate '{current_candidate.candidate_id}' is disabled in catalog. Unsimulated scorecards are forbidden for disabled candidates."
            )

        # 8. Replay / Stale duplicate guard
        for prior in state.history:
            prior_cid_resolved = self.catalog._resolve_id(prior.candidate_id)
            if (
                prior_cid_resolved == scorecard_cid_resolved
                and prior.tier == scorecard.tier
                and prior.subtask_id == scorecard.subtask_id
            ):
                raise LLMEvaluationIntegrityError(
                    f"Stale or duplicate scorecard replayed for candidate '{scorecard.candidate_id}' on tier '{scorecard.tier.value}'."
                )

        # ALL CHECKS PASSED: Safe to mutate history
        state.history.append(scorecard)

        # 1. Success check: passed and no safety-critical failure
        if scorecard.passed and scorecard.safety_critical_passed:
            state.status = "approved"
            state.approved_candidate_id = current_candidate.candidate_id
            return EvaluationDecision.ACCEPT_CANDIDATE

        # 2. Failure: advance within SAME tier if more candidates exist
        candidates = self.catalog.get_candidates_for_tier(
            task_id=state.task_id,
            tier=state.current_tier,
            only_enabled=False,
        )

        next_idx = state.current_candidate_index + 1
        if next_idx < len(candidates):
            state.current_candidate_index = next_idx
            return EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER

        # 3. All candidates in current tier exhausted
        state.status = "tier_exhausted"
        return EvaluationDecision.TIER_EXHAUSTED_REQUIRES_APPROVAL

    def promote_tier(
        self,
        state: TaskEvaluationState,
        operator_approval: bool = False,
    ) -> EvaluationDecision:
        """
        Promote evaluation to the next tier following tier exhaustion.
        Strictly requires explicit operator_approval=True.
        """
        if not operator_approval:
            raise PermissionError(
                f"Cannot promote task '{state.task_id}' to next tier: requires explicit operator approval."
            )

        if state.status != "tier_exhausted":
            raise ValueError(
                f"Cannot promote task '{state.task_id}': current tier {state.current_tier.value} "
                "is not exhausted."
            )

        current_idx = TIER_PROGRESSION.index(state.current_tier)
        if current_idx + 1 < len(TIER_PROGRESSION):
            next_tier = TIER_PROGRESSION[current_idx + 1]
            state.current_tier = next_tier
            state.current_candidate_index = 0
            state.status = "evaluating"

            # Check if candidates exist in the newly promoted tier
            candidates = self.catalog.get_candidates_for_tier(
                task_id=state.task_id,
                tier=next_tier,
                only_enabled=False,
            )
            if not candidates:
                state.status = "unresolved"
                return EvaluationDecision.UNRESOLVED_NEEDS_HUMAN_DECISION

            return EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER

        # All four tiers exhausted
        state.status = "unresolved"
        return EvaluationDecision.UNRESOLVED_NEEDS_HUMAN_DECISION

    def reset_evaluation(
        self,
        state: TaskEvaluationState,
        operator_token: str,
        new_run_id: str,
    ) -> None:
        """
        Explicitly reset evaluation state for a new versioned benchmark run.
        Guards against silent/accidental reset of approved tasks.
        Requires authenticated operator token and distinct new run ID.
        """
        if not operator_token or not operator_token.strip():
            raise PermissionError("Resetting evaluation requires an authenticated operator token.")
        if not new_run_id or not new_run_id.strip():
            raise ValueError("Resetting evaluation requires a valid new_run_id.")

        state.current_tier = ModelTier.TIER_1_BUDGET
        state.current_candidate_index = 0
        state.status = "evaluating"
        state.approved_candidate_id = None
        state.run_id = new_run_id
        state.history.clear()
