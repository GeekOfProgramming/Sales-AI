"""
tests/test_s2_3_1_routing_integrity.py
Adversarial test suite for SalesAI S2.3.1 Routing Integrity & Evaluation State Hardening.
Verifies all 14 mandatory negative/adversarial requirements specified in
docs/architecture/SALESAI-S2.3.1-ROUTING-INTEGRITY-HARDENING.md.

Guarantees:
- Mismatched, forged, stale, duplicate, or unverified scorecards/routes are strictly rejected.
- Zero state/history mutation occurs upon rejection.
- Terminal states (approved, unresolved, tier_exhausted) are strictly immutable to replays.
- Mock/simulated benchmarks cannot grant production routing authority without operator provenance.
- Immutability of candidate records and catalog drift detection.
- Zero live API calls, zero Ollama, zero local neural weights.
"""

import pytest

from sales_engine.llm_gateway.contracts import ModelTier
from sales_engine.llm_gateway.errors import (
    LLMNotConfiguredError,
    LLMRoutingIntegrityError,
    LLMEvaluationIntegrityError,
)
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    PricingMetadata,
    CANONICAL_TASKS,
    normalize_task_id,
    validate_subtask_id,
)
from sales_engine.llm_gateway.router import (
    TaskRouteDecision,
    TaskRouter,
)
from sales_engine.llm_gateway.evaluation_policy import (
    EvaluationDecision,
    CandidateScorecard,
    TaskEvaluationState,
    EvaluationPlanner,
)


# ---------------------------------------------------------------------------
# Test 1: Reject mismatched candidate ID even when passed=True and score=1.0
# ---------------------------------------------------------------------------


def test_reject_mismatched_candidate_id_zero_mutation():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("website_extraction")

    current_cand = planner.get_current_candidate(state)
    assert current_cand.candidate_id == "t1_c1"
    initial_history_len = len(state.history)

    # Forged scorecard targeting 'fabricated-candidate-id'
    forged_scorecard = CandidateScorecard(
        task_id="website_extraction",
        candidate_id="fabricated-candidate-id",
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=1.0,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="does not match currently scheduled candidate"):
        planner.process_scorecard(state, forged_scorecard)

    # Assert ZERO state or history mutation
    assert len(state.history) == initial_history_len
    assert state.status == "evaluating"
    assert state.approved_candidate_id is None
    assert state.current_candidate_index == 0


# ---------------------------------------------------------------------------
# Test 2: Reject mismatched Tier (Tier 4 scorecard submitted during Tier 1)
# ---------------------------------------------------------------------------


def test_reject_mismatched_tier_zero_mutation():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("job_analysis")
    assert state.current_tier == ModelTier.TIER_1_BUDGET

    current_cand = planner.get_current_candidate(state)
    initial_history_len = len(state.history)

    # Scorecard claiming Tier 4 Flagship while evaluating Tier 1
    mismatched_tier_scorecard = CandidateScorecard(
        task_id="job_analysis",
        candidate_id=current_cand.candidate_id,
        tier=ModelTier.TIER_4_FLAGSHIP,
        passed=True,
        safety_critical_passed=True,
        score=0.98,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="does not match state tier"):
        planner.process_scorecard(state, mismatched_tier_scorecard)

    # Assert ZERO state or history mutation
    assert len(state.history) == initial_history_len
    assert state.status == "evaluating"
    assert state.current_tier == ModelTier.TIER_1_BUDGET
    assert state.approved_candidate_id is None


# ---------------------------------------------------------------------------
# Test 3: Reject mismatched website subtask (pass_b submitted for pass_a state)
# ---------------------------------------------------------------------------


def test_reject_mismatched_website_subtask_zero_mutation():
    planner = EvaluationPlanner()
    state_pass_a = planner.get_or_create_state("website_extraction", subtask_id="pass_a")
    current_cand = planner.get_current_candidate(state_pass_a)
    initial_history_len = len(state_pass_a.history)

    # Scorecard for pass_b erroneously submitted to pass_a state
    cross_subtask_scorecard = CandidateScorecard(
        task_id="website_extraction",
        subtask_id="pass_b",
        candidate_id=current_cand.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.95,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="does not match state subtask 'pass_a'"):
        planner.process_scorecard(state_pass_a, cross_subtask_scorecard)

    # Assert ZERO state or history mutation
    assert len(state_pass_a.history) == initial_history_len
    assert state_pass_a.status == "evaluating"
    assert state_pass_a.approved_candidate_id is None


# ---------------------------------------------------------------------------
# Test 4: Reject stale/out-of-order candidate scorecard after moving to #2
# ---------------------------------------------------------------------------


def test_reject_stale_out_of_order_scorecard_after_advance():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("inbox_classification")

    cand1 = planner.get_current_candidate(state)
    assert cand1.candidate_id == "t1_c1"

    # Candidate 1 fails legitimately
    scorecard1 = CandidateScorecard(
        task_id="inbox_classification",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=False,
        score=0.40,
        is_simulated=True,
    )
    planner.process_scorecard(state, scorecard1)

    assert state.current_candidate_index == 1
    cand2 = planner.get_current_candidate(state)
    assert cand2.candidate_id == "t1_c2"
    history_after_fail = len(state.history)

    # Re-submit a scorecard for candidate 1 (stale/out-of-order)
    stale_cand1_scorecard = CandidateScorecard(
        task_id="inbox_classification",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.99,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="does not match currently scheduled candidate"):
        planner.process_scorecard(state, stale_cand1_scorecard)

    # History count and candidate index must NOT change
    assert len(state.history) == history_after_fail
    assert state.current_candidate_index == 1
    assert state.status == "evaluating"


# ---------------------------------------------------------------------------
# Test 5: Reject replay after approved, preserving immutable history count
# ---------------------------------------------------------------------------


def test_reject_replay_after_approved_preserves_immutable_history():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("reply_suggestion")

    cand1 = planner.get_current_candidate(state)
    winning_scorecard = CandidateScorecard(
        task_id="reply_suggestion",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.91,
        is_simulated=True,
    )

    decision = planner.process_scorecard(state, winning_scorecard)
    assert decision == EvaluationDecision.ACCEPT_CANDIDATE
    assert state.status == "approved"
    assert state.approved_candidate_id == cand1.candidate_id
    history_len = len(state.history)

    # Attempt replay of identical or new scorecard in terminal 'approved' status
    replay_scorecard = CandidateScorecard(
        task_id="reply_suggestion",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.91,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="non-evaluating status 'approved'"):
        planner.process_scorecard(state, replay_scorecard)

    # History and status remain completely untouched
    assert len(state.history) == history_len
    assert state.status == "approved"
    assert state.approved_candidate_id == cand1.candidate_id


# ---------------------------------------------------------------------------
# Test 6: Reject replay during tier_exhausted and after unresolved
# ---------------------------------------------------------------------------


def test_reject_replay_during_tier_exhausted_and_after_unresolved():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("outreach_draft")

    # Exhaust Tier 1
    for _ in range(4):
        cand = planner.get_current_candidate(state)
        sc = CandidateScorecard(
            task_id="outreach_draft",
            candidate_id=cand.candidate_id,
            tier=ModelTier.TIER_1_BUDGET,
            passed=False,
            score=0.2,
            is_simulated=True,
        )
        planner.process_scorecard(state, sc)

    assert state.status == "tier_exhausted"
    history_len = len(state.history)

    # Scorecard submission during tier_exhausted must fail closed
    cand_retry = planner.catalog.get_candidate("t1_c1")
    replayed_sc = CandidateScorecard(
        task_id="outreach_draft",
        candidate_id=cand_retry.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.95,
        is_simulated=True,
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="non-evaluating status 'tier_exhausted'"):
        planner.process_scorecard(state, replayed_sc)

    assert len(state.history) == history_len

    # Exhaust through to unresolved
    for _ in range(3):
        planner.promote_tier(state, operator_approval=True)
        for _ in range(4):
            cand = planner.get_current_candidate(state)
            sc = CandidateScorecard(
                task_id="outreach_draft",
                candidate_id=cand.candidate_id,
                tier=state.current_tier,
                passed=False,
                score=0.1,
                is_simulated=True,
            )
            planner.process_scorecard(state, sc)

    # Final promotion attempt exhausts Tier 4 -> unresolved
    planner.promote_tier(state, operator_approval=True)
    assert state.status == "unresolved"
    history_unresolved_len = len(state.history)

    with pytest.raises(LLMEvaluationIntegrityError, match="non-evaluating status 'unresolved'"):
        planner.process_scorecard(state, replayed_sc)

    assert len(state.history) == history_unresolved_len


# ---------------------------------------------------------------------------
# Test 7: Reject scorecards for disabled candidates unless is_simulated=True
# ---------------------------------------------------------------------------


def test_reject_unsimulated_scorecard_for_disabled_candidate():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("job_analysis")
    cand1 = planner.get_current_candidate(state)
    assert cand1.enabled is False

    # Scorecard claiming real benchmark (is_simulated=False) on disabled candidate
    unsimulated_scorecard = CandidateScorecard(
        task_id="job_analysis",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.95,
        is_simulated=False,  # Prohibited for disabled candidate!
    )

    with pytest.raises(LLMEvaluationIntegrityError, match="disabled in catalog. Unsimulated scorecards are forbidden"):
        planner.process_scorecard(state, unsimulated_scorecard)

    assert len(state.history) == 0
    assert state.status == "evaluating"


# ---------------------------------------------------------------------------
# Test 8: Reject forged route provider, model, tier, task, and disabled candidate
# ---------------------------------------------------------------------------


def test_reject_forged_route_attributes_individually():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    catalog.enable_candidate(
        "t1_c1",
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
        verified_capabilities={"supports_json_mode": True},
    )
    router = TaskRouter(catalog=catalog)

    # 8a: Forged provider
    with pytest.raises(LLMRoutingIntegrityError, match="Route provider mismatch"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="website_extraction",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="anthropic",  # Forged!
                model="gpt-4o-mini",
                candidate_id="t1_c1",
                approved_by="operator_alice",
            )
        )

    # 8b: Forged model
    with pytest.raises(LLMRoutingIntegrityError, match="Route model mismatch"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="website_extraction",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="openai",
                model="unverified-gpt-model",  # Forged!
                candidate_id="t1_c1",
                approved_by="operator_alice",
            )
        )

    # 8c: Forged tier
    with pytest.raises(LLMRoutingIntegrityError, match="Route tier mismatch"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="website_extraction",
                tier=ModelTier.TIER_4_FLAGSHIP,  # Forged!
                provider_id="openai",
                model="gpt-4o-mini",
                candidate_id="t1_c1",
                approved_by="operator_alice",
            )
        )

    # 8d: Candidate not authorized for task
    catalog.register_candidate(
        ModelCandidate(
            candidate_id="task_restricted_c1",
            tier=ModelTier.TIER_1_BUDGET,
            priority=1,
            provider_id="openai",
            exact_model_id="gpt-4o-mini",
            enabled=True,
            verified_capabilities={"supports_json_mode": True},
            allowed_tasks=["inbox_classification"],  # Only allowed for inbox
        ),
        allow_override=True,
    )
    with pytest.raises(LLMRoutingIntegrityError, match="not authorized for task 'website_extraction'"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="website_extraction",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="openai",
                model="gpt-4o-mini",
                candidate_id="task_restricted_c1",
                approved_by="operator_alice",
            )
        )

    # 8e: Candidate disabled in catalog
    catalog.disable_candidate("t1_c1")
    with pytest.raises(LLMRoutingIntegrityError, match="candidate is disabled in catalog"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="website_extraction",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="openai",
                model="gpt-4o-mini",
                candidate_id="t1_c1",
                approved_by="operator_alice",
            )
        )


# ---------------------------------------------------------------------------
# Test 9: Reject missing/untrusted operator authority
# ---------------------------------------------------------------------------


def test_reject_missing_operator_authority_and_benchmark_pass_cannot_set_route():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    catalog.enable_candidate(
        "t1_c1",
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
        verified_capabilities={"supports_json_mode": True},
    )
    router = TaskRouter(catalog=catalog)

    # 9a: Missing approved_by (None or blank)
    with pytest.raises(LLMRoutingIntegrityError, match="missing authenticated operator provenance"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="job_analysis",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="openai",
                model="gpt-4o-mini",
                candidate_id="t1_c1",
                approved_by=None,
            )
        )

    with pytest.raises(LLMRoutingIntegrityError, match="missing authenticated operator provenance"):
        router.set_approved_route(
            TaskRouteDecision(
                task_id="job_analysis",
                tier=ModelTier.TIER_1_BUDGET,
                provider_id="openai",
                model="gpt-4o-mini",
                candidate_id="t1_c1",
                approved_by="   ",
            )
        )

    # 9b: Proposed benchmark PASS cannot automatically set production route
    planner = EvaluationPlanner(catalog=catalog)
    state = planner.get_or_create_state("job_analysis")
    cand = planner.get_current_candidate(state)
    scorecard = CandidateScorecard(
        task_id="job_analysis",
        candidate_id=cand.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.97,
        is_simulated=True,
    )
    decision = planner.process_scorecard(state, scorecard)
    assert decision == EvaluationDecision.ACCEPT_CANDIDATE

    # TaskRouter still fails closed: benchmark pass does NOT bypass operator routing approval
    with pytest.raises(LLMNotConfiguredError, match="No operator-approved model route configured"):
        router.resolve_approved("job_analysis")


# ---------------------------------------------------------------------------
# Test 10: Verify route rejection on later catalog mutation / disablement
# ---------------------------------------------------------------------------


def test_verify_route_rejection_on_post_approval_catalog_mutation():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    catalog.enable_candidate(
        "t1_c1",
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
        verified_capabilities={"supports_json_mode": True},
    )
    router = TaskRouter(catalog=catalog)

    valid_decision = TaskRouteDecision(
        task_id="discovery_query_generation",
        tier=ModelTier.TIER_1_BUDGET,
        provider_id="openai",
        model="gpt-4o-mini",
        candidate_id="t1_c1",
        approved_by="operator_lead",
    )
    router.set_approved_route(valid_decision)

    # Pre-mutation: resolves successfully
    assert router.resolve_approved("discovery_query_generation").candidate_id == "t1_c1"

    # Post-mutation 1: disable candidate in catalog
    catalog.disable_candidate("t1_c1")
    with pytest.raises(LLMNotConfiguredError, match="currently disabled in catalog"):
        router.resolve_approved("discovery_query_generation")

    # Post-mutation 2: mutate candidate exact_model_id in catalog
    catalog.enable_candidate(
        "t1_c1",
        provider_id="openai",
        exact_model_id="different-mutated-model",
        verified_capabilities={"supports_json_mode": True},
    )
    with pytest.raises(LLMRoutingIntegrityError, match="candidate attributes were mutated in catalog"):
        router.resolve_approved("discovery_query_generation")


# ---------------------------------------------------------------------------
# Test 11: Candidate verification metadata absent by default; immutability
# ---------------------------------------------------------------------------


def test_candidate_verification_absent_by_default_and_frozen():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    cand = catalog.get_candidate("t1_c1")

    # Verification metadata absent / empty by default
    assert cand.enabled is False
    assert cand.verified_capabilities == {}
    assert cand.pricing is None
    assert cand.verification_notes is None

    # Immutability: direct mutation must fail
    with pytest.raises(Exception):  # pydantic.ValidationError or TypeError
        cand.enabled = True

    with pytest.raises(Exception):
        cand.provider_id = "openai"

    # Cannot enable candidate with placeholder credentials
    with pytest.raises(ValueError, match="Cannot enable candidate.*unassigned"):
        ModelCandidate(
            candidate_id="illegal_enabled_placeholder",
            tier=ModelTier.TIER_1_BUDGET,
            priority=1,
            provider_id="unassigned",
            exact_model_id="unverified_slot",
            enabled=True,
            verified_capabilities={},
        )


# ---------------------------------------------------------------------------
# Test 12: Independent task state and website pass_a/pass_b state isolation
# ---------------------------------------------------------------------------


def test_independent_task_and_website_subtask_isolation():
    planner = EvaluationPlanner()
    state_a = planner.get_or_create_state("website_extraction", subtask_id="pass_a")
    state_b = planner.get_or_create_state("website_extraction", subtask_id="pass_b")
    state_job = planner.get_or_create_state("job_analysis")

    # Pass A candidate passes
    cand_a = planner.get_current_candidate(state_a)
    sc_a = CandidateScorecard(
        task_id="website_extraction",
        subtask_id="pass_a",
        candidate_id=cand_a.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.95,
        is_simulated=True,
    )
    planner.process_scorecard(state_a, sc_a)
    assert state_a.status == "approved"
    assert state_a.approved_candidate_id == cand_a.candidate_id

    # Pass B is completely untouched and unaffected
    assert state_b.status == "evaluating"
    assert state_b.approved_candidate_id is None
    assert len(state_b.history) == 0

    # Job analysis is completely untouched and unaffected
    assert state_job.status == "evaluating"
    assert state_job.approved_candidate_id is None
    assert len(state_job.history) == 0


# ---------------------------------------------------------------------------
# Test 13: Operator promotion gate; no auto escalation; no paid failover
# ---------------------------------------------------------------------------


def test_operator_promotion_gate_and_zero_auto_escalation():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("reply_suggestion")

    # Exhaust Tier 1
    for _ in range(4):
        cand = planner.get_current_candidate(state)
        sc = CandidateScorecard(
            task_id="reply_suggestion",
            candidate_id=cand.candidate_id,
            tier=ModelTier.TIER_1_BUDGET,
            passed=False,
            score=0.2,
            is_simulated=True,
        )
        planner.process_scorecard(state, sc)

    assert state.status == "tier_exhausted"

    # Attempting promotion without operator_approval=True raises PermissionError
    with pytest.raises(PermissionError, match="requires explicit operator approval"):
        planner.promote_tier(state, operator_approval=False)

    # Next candidate remains None until operator explicitly acts
    assert planner.get_current_candidate(state) is None


# ---------------------------------------------------------------------------
# Test 14: Reset evaluation requires operator token and new run ID
# ---------------------------------------------------------------------------


def test_reset_evaluation_requires_operator_token_and_run_id():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("outreach_draft")

    cand = planner.get_current_candidate(state)
    sc = CandidateScorecard(
        task_id="outreach_draft",
        candidate_id=cand.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.93,
        is_simulated=True,
    )
    planner.process_scorecard(state, sc)
    assert state.status == "approved"

    # Missing operator token raises PermissionError
    with pytest.raises(PermissionError, match="requires an authenticated operator token"):
        planner.reset_evaluation(state, operator_token="", new_run_id="run_002")

    # Missing new_run_id raises ValueError
    with pytest.raises(ValueError, match="requires a valid new_run_id"):
        planner.reset_evaluation(state, operator_token="op_token_xyz", new_run_id="")

    # Valid reset clears approval and history safely
    planner.reset_evaluation(state, operator_token="op_token_xyz", new_run_id="run_002")
    assert state.status == "evaluating"
    assert state.approved_candidate_id is None
    assert state.current_candidate_index == 0
    assert state.current_tier == ModelTier.TIER_1_BUDGET
    assert len(state.history) == 0
    assert state.run_id == "run_002"


# ---------------------------------------------------------------------------
# Test 15: Catalog rejects duplicate (tier, priority) slots and invalid overrides
# ---------------------------------------------------------------------------


def test_catalog_rejects_slot_collision_and_invalid_overrides():
    catalog = ModelCatalog()
    cand1 = ModelCandidate(
        candidate_id="cand_1a",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
    )
    cand2 = ModelCandidate(
        candidate_id="cand_1b",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,  # Slot collision: Priority 1 in Tier 1 already taken!
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
    )
    catalog.register_candidate(cand1)
    with pytest.raises(ValueError, match="Candidate slot conflict"):
        catalog.register_candidate(cand2)

    # Invalid priority override: non-existent candidate
    with pytest.raises(ValueError, match="Candidate 'non_existent' not found in catalog"):
        catalog.set_task_priority_override("website_extraction", ModelTier.TIER_1_BUDGET, ["non_existent"])


# ---------------------------------------------------------------------------
# Test 16: Non-website task rejects subtask identifier
# ---------------------------------------------------------------------------


def test_non_website_task_rejects_subtask():
    with pytest.raises(ValueError, match="does not define subtasks"):
        validate_subtask_id("job_analysis", "pass_a")

    with pytest.raises(ValueError, match="Invalid subtask 'pass_c' for task 'website_extraction'"):
        validate_subtask_id("website_extraction", "pass_c")
