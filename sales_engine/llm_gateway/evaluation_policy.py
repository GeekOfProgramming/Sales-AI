"""
sales_engine/llm_gateway/evaluation_policy.py
Task-first, Tier-1-first sequential model evaluation state machine for SalesAI.
Evaluates candidates one at a time, early-stops on pass, exhausts tiers before promotion,
and requires explicit operator approval before elevating tiers. Zero live API calls.
"""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from sales_engine.llm_gateway.contracts import ModelTier
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    normalize_task_id,
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
    """

    model_config = ConfigDict(extra="forbid")

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


class EvaluationPlanner:
    """
    Pure deterministic state machine guiding sequential model evaluation.
    Operates strictly offline without issuing network requests.
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
        key = f"{canonical}:{subtask_id}" if subtask_id else canonical

        if key not in self._states:
            self._states[key] = TaskEvaluationState(
                task_id=canonical,
                subtask_id=subtask_id,
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
        - PASS: stops evaluation and accepts candidate.
        - FAIL: advances to next candidate in same tier.
        - TIER EXHAUSTED: halts and requests operator approval.
        """
        canonical = normalize_task_id(scorecard.task_id)
        if canonical != state.task_id:
            raise ValueError(f"Scorecard task '{scorecard.task_id}' does not match state task '{state.task_id}'.")

        state.history.append(scorecard)

        # 1. Success check: passed and no safety-critical failure
        if scorecard.passed and scorecard.safety_critical_passed:
            state.status = "approved"
            state.approved_candidate_id = scorecard.candidate_id
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
