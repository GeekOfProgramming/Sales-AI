"""
tests/test_s2_3_sequential_task_routing.py
Comprehensive deterministic test suite for SalesAI S2.3 v2 Sequential Model Evaluation & Task Routing.
Zero live API calls, zero local neural weights, zero local Ollama.
"""

import pytest

from sales_engine.llm_gateway.contracts import ModelTier, LLMRequest, LLMResponse
from sales_engine.llm_gateway.errors import LLMNotConfiguredError, LLMInvalidSchemaError
from sales_engine.llm_gateway.model_catalog import (
    ModelCatalog,
    ModelCandidate,
    PricingMetadata,
    CANONICAL_TASKS,
    normalize_task_id,
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
# 1. Canonical Taxonomy & Task ID Normalization Tests
# ---------------------------------------------------------------------------


def test_canonical_tasks_count_and_keys():
    assert len(CANONICAL_TASKS) == 6
    assert "website_extraction" in CANONICAL_TASKS
    assert "job_analysis" in CANONICAL_TASKS
    assert "discovery_query_generation" in CANONICAL_TASKS
    assert "inbox_classification" in CANONICAL_TASKS
    assert "reply_suggestion" in CANONICAL_TASKS
    assert "outreach_draft" in CANONICAL_TASKS


def test_task_id_normalization_valid_aliases():
    assert normalize_task_id("website_extraction") == "website_extraction"
    assert normalize_task_id("LLM-SITE-01") == "website_extraction"
    assert normalize_task_id("Q01_website_pass_a") == "website_extraction"
    assert normalize_task_id("LLM-SITE-02") == "job_analysis"
    assert normalize_task_id("LLM-SITE-04") == "inbox_classification"
    assert normalize_task_id("LLM-SITE-06") == "outreach_draft"


def test_task_id_normalization_unknown_fails_closed():
    with pytest.raises(ValueError, match="Unknown task ID or alias"):
        normalize_task_id("arbitrary_unknown_task")

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_task_id("")


# ---------------------------------------------------------------------------
# 2. 16-Slot Model Catalog Tests
# ---------------------------------------------------------------------------


def test_default_16_slot_catalog_counts():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    assert len(catalog.candidates) == 16

    # Verify exactly 4 candidates per tier
    for tier in ModelTier:
        candidates = [c for c in catalog.candidates.values() if c.tier == tier]
        assert len(candidates) == 4
        priorities = [c.priority for c in candidates]
        assert sorted(priorities) == [1, 2, 3, 4]


def test_default_catalog_all_slots_disabled_by_default():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    for candidate in catalog.candidates.values():
        assert candidate.enabled is False, f"Candidate {candidate.candidate_id} must be disabled by default"


def test_pricing_metadata_null_not_zero():
    price = PricingMetadata(currency="USD", input_per_1m=None, output_per_1m=None)
    assert price.input_per_1m is None
    assert price.output_per_1m is None


def test_catalog_deterministic_priority_order():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    t1_candidates = catalog.get_candidates_for_tier("website_extraction", ModelTier.TIER_1_BUDGET)
    assert len(t1_candidates) == 4
    assert t1_candidates[0].candidate_id == "t1_c1_openai_mini"
    assert t1_candidates[1].candidate_id == "t1_c2_anthropic_haiku"
    assert t1_candidates[2].candidate_id == "t1_c3_google_flash"
    assert t1_candidates[3].candidate_id == "t1_c4_mistral_small"


def test_catalog_task_specific_priority_override():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    # Override priority for inbox_classification: test Google Flash first
    catalog.task_priority_overrides["inbox_classification"] = {
        "tier_1_budget": ["t1_c3_google_flash", "t1_c1_openai_mini"]
    }

    inbox_t1 = catalog.get_candidates_for_tier("inbox_classification", ModelTier.TIER_1_BUDGET)
    assert inbox_t1[0].candidate_id == "t1_c3_google_flash"
    assert inbox_t1[1].candidate_id == "t1_c1_openai_mini"

    # Website extraction remains default
    web_t1 = catalog.get_candidates_for_tier("website_extraction", ModelTier.TIER_1_BUDGET)
    assert web_t1[0].candidate_id == "t1_c1_openai_mini"


def test_catalog_duplicate_registration_rejected():
    catalog = ModelCatalog()
    cand1 = ModelCandidate(
        candidate_id="dup_id",
        tier=ModelTier.TIER_1_BUDGET,
        priority=1,
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
    )
    cand2 = ModelCandidate(
        candidate_id="dup_id",
        tier=ModelTier.TIER_1_BUDGET,
        priority=2,
        provider_id="openai",
        exact_model_id="gpt-4o-mini",
    )
    catalog.register_candidate(cand1)
    with pytest.raises(ValueError, match="already exists"):
        catalog.register_candidate(cand2)


# ---------------------------------------------------------------------------
# 3. Task Router & Production Resolution Tests
# ---------------------------------------------------------------------------


def test_router_unapproved_task_fails_closed():
    router = TaskRouter()
    # All 6 tasks unapproved initially
    for task in CANONICAL_TASKS:
        with pytest.raises(LLMNotConfiguredError, match="No operator-approved model route"):
            router.resolve_approved(task)


def test_router_dry_run_defaults_to_tier_1():
    router = TaskRouter()
    for task in CANONICAL_TASKS:
        candidates = router.dry_run_candidates(task)
        assert len(candidates) == 4
        for c in candidates:
            assert c.tier == ModelTier.TIER_1_BUDGET


def test_router_approved_resolution_success():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    # Enable the candidate
    catalog.candidates["t1_c1_openai_mini"].enabled = True

    router = TaskRouter(catalog=catalog)
    decision = TaskRouteDecision(
        task_id="website_extraction",
        tier=ModelTier.TIER_1_BUDGET,
        provider_id="openai",
        model="gpt-4o-mini",
        candidate_id="t1_c1_openai_mini",
        approved_by="operator_human",
    )
    router.set_approved_route(decision)

    resolved = router.resolve_approved("website_extraction")
    assert resolved.candidate_id == "t1_c1_openai_mini"
    assert resolved.model == "gpt-4o-mini"
    assert resolved.tier == ModelTier.TIER_1_BUDGET


def test_router_approved_candidate_disabled_fails_closed():
    catalog = ModelCatalog.create_default_16_slot_catalog()
    # Candidate remains disabled (default)
    router = TaskRouter(catalog=catalog)
    decision = TaskRouteDecision(
        task_id="website_extraction",
        tier=ModelTier.TIER_1_BUDGET,
        provider_id="openai",
        model="gpt-4o-mini",
        candidate_id="t1_c1_openai_mini",
        approved_by="operator_human",
    )
    router.set_approved_route(decision)

    with pytest.raises(LLMNotConfiguredError, match="currently disabled in catalog"):
        router.resolve_approved("website_extraction")


def test_router_zero_automatic_tier_escalation():
    # Proves router has no logic that automatically switches tiers
    router = TaskRouter()
    decision = TaskRouteDecision(
        task_id="job_analysis",
        tier=ModelTier.TIER_1_BUDGET,
        provider_id="openai",
        model="gpt-4o-mini",
        candidate_id="t1_c1_openai_mini",
    )
    router.catalog.candidates["t1_c1_openai_mini"].enabled = True
    router.set_approved_route(decision)

    assert router.get_active_tier_for_task("job_analysis") == ModelTier.TIER_1_BUDGET


# ---------------------------------------------------------------------------
# 4. Sequential Evaluation Planner & State Machine Tests
# ---------------------------------------------------------------------------


def test_evaluation_planner_initializes_at_tier_1_index_0():
    planner = EvaluationPlanner()
    for task in CANONICAL_TASKS:
        state = planner.get_or_create_state(task)
        assert state.current_tier == ModelTier.TIER_1_BUDGET
        assert state.current_candidate_index == 0
        assert state.status == "evaluating"
        current_candidate = planner.get_current_candidate(state)
        assert current_candidate is not None
        assert current_candidate.tier == ModelTier.TIER_1_BUDGET
        assert current_candidate.priority == 1


def test_evaluation_planner_early_stops_on_pass():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("website_extraction")

    cand1 = planner.get_current_candidate(state)
    assert cand1.candidate_id == "t1_c1_openai_mini"

    # Scorecard: candidate passes!
    scorecard = CandidateScorecard(
        task_id="website_extraction",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.92,
        reasoning_codes=["GROUNDING_VERIFIED", "SCHEMA_VALID"],
    )
    decision = planner.process_scorecard(state, scorecard)

    assert decision == EvaluationDecision.ACCEPT_CANDIDATE
    assert state.status == "approved"
    assert state.approved_candidate_id == "t1_c1_openai_mini"
    # Further evaluation stops
    assert planner.get_current_candidate(state) is None


def test_evaluation_planner_advances_within_same_tier_on_fail():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("website_extraction")

    # Candidate 1 fails
    cand1 = planner.get_current_candidate(state)
    scorecard1 = CandidateScorecard(
        task_id="website_extraction",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=False,
        safety_critical_passed=True,
        score=0.45,
        reasoning_codes=["EXTRACTION_INCOMPLETE"],
    )
    decision1 = planner.process_scorecard(state, scorecard1)

    assert decision1 == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
    assert state.current_tier == ModelTier.TIER_1_BUDGET
    assert state.current_candidate_index == 1

    cand2 = planner.get_current_candidate(state)
    assert cand2.candidate_id == "t1_c2_anthropic_haiku"


def test_evaluation_planner_safety_failure_blocks_approval():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("outreach_draft")

    cand1 = planner.get_current_candidate(state)
    # Passed general benchmarks (e.g. 0.85 score), BUT safety critical failed (invented team size)
    scorecard = CandidateScorecard(
        task_id="outreach_draft",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=False,
        score=0.85,
        reasoning_codes=["INVENTED_NUMERICAL_PROMISE"],
    )
    decision = planner.process_scorecard(state, scorecard)

    # Must NOT accept candidate; must propose next candidate
    assert decision == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
    assert state.status == "evaluating"
    assert state.current_candidate_index == 1


def test_evaluation_planner_exhausts_tier_after_4_failures():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("inbox_classification")

    for i in range(4):
        cand = planner.get_current_candidate(state)
        assert cand is not None
        scorecard = CandidateScorecard(
            task_id="inbox_classification",
            candidate_id=cand.candidate_id,
            tier=ModelTier.TIER_1_BUDGET,
            passed=False,
            score=0.30,
            reasoning_codes=["UNSUBSCRIBE_MISCLASSIFIED"],
        )
        dec = planner.process_scorecard(state, scorecard)
        if i < 3:
            assert dec == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
        else:
            assert dec == EvaluationDecision.TIER_EXHAUSTED_REQUIRES_APPROVAL

    assert state.status == "tier_exhausted"
    assert planner.get_current_candidate(state) is None


def test_evaluation_planner_promotion_requires_operator_approval():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("inbox_classification")

    # Exhaust Tier 1
    for _ in range(4):
        cand = planner.get_current_candidate(state)
        scorecard = CandidateScorecard(
            task_id="inbox_classification",
            candidate_id=cand.candidate_id,
            tier=ModelTier.TIER_1_BUDGET,
            passed=False,
            score=0.2,
        )
        planner.process_scorecard(state, scorecard)

    # Attempting promotion without operator approval must raise PermissionError
    with pytest.raises(PermissionError, match="requires explicit operator approval"):
        planner.promote_tier(state, operator_approval=False)

    # Promotion with operator approval succeeds and advances to Tier 2
    decision = planner.promote_tier(state, operator_approval=True)
    assert decision == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
    assert state.current_tier == ModelTier.TIER_2_BALANCED
    assert state.current_candidate_index == 0

    next_cand = planner.get_current_candidate(state)
    assert next_cand.tier == ModelTier.TIER_2_BALANCED
    assert next_cand.candidate_id == "t2_c1_openai_standard"


def test_evaluation_planner_all_tiers_exhausted_unresolved():
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("inbox_classification")

    # Exhaust all 4 tiers sequentially
    for tier in [ModelTier.TIER_1_BUDGET, ModelTier.TIER_2_BALANCED, ModelTier.TIER_3_HIGH_FIDELITY, ModelTier.TIER_4_FLAGSHIP]:
        for _ in range(4):
            cand = planner.get_current_candidate(state)
            scorecard = CandidateScorecard(
                task_id="inbox_classification",
                candidate_id=cand.candidate_id,
                tier=tier,
                passed=False,
                score=0.1,
            )
            planner.process_scorecard(state, scorecard)

        if tier != ModelTier.TIER_4_FLAGSHIP:
            planner.promote_tier(state, operator_approval=True)

    # Attempt to promote past Tier 4
    decision = planner.promote_tier(state, operator_approval=True)
    assert decision == EvaluationDecision.UNRESOLVED_NEEDS_HUMAN_DECISION
    assert state.status == "unresolved"


# ---------------------------------------------------------------------------
# 5. Mandatory Dry-Run Examples (Section 7 Specification)
# ---------------------------------------------------------------------------


def test_mandatory_example_a_website_extraction_candidate_rotation_and_early_stop():
    """
    Example A: website_extraction Tier 1 #1 fails grounding -> Tier 1 #2 passes ->
    approved candidate #2 for website_extraction only; inbox_classification remains unselected.
    Stop testing further candidates for that task.
    """
    planner = EvaluationPlanner()
    state_web = planner.get_or_create_state("website_extraction")
    state_inbox = planner.get_or_create_state("inbox_classification")

    # 1. Tier 1 #1 fails grounding
    cand1 = planner.get_current_candidate(state_web)
    scorecard1 = CandidateScorecard(
        task_id="website_extraction",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=False,
        score=0.40,
        reasoning_codes=["GROUNDING_DEFECT"],
    )
    dec1 = planner.process_scorecard(state_web, scorecard1)
    assert dec1 == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER

    # 2. Tier 1 #2 passes all thresholds
    cand2 = planner.get_current_candidate(state_web)
    scorecard2 = CandidateScorecard(
        task_id="website_extraction",
        candidate_id=cand2.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.95,
        reasoning_codes=["GROUNDING_PASSED", "FACTS_ACCURATE"],
    )
    dec2 = planner.process_scorecard(state_web, scorecard2)
    assert dec2 == EvaluationDecision.ACCEPT_CANDIDATE
    assert state_web.approved_candidate_id == cand2.candidate_id

    # 3. Stop testing further candidates for website_extraction
    assert planner.get_current_candidate(state_web) is None

    # 4. Inbox classification remains completely unselected and unmutated
    assert state_inbox.status == "evaluating"
    assert state_inbox.approved_candidate_id is None
    assert len(state_inbox.history) == 0


def test_mandatory_example_b_inbox_classification_exhausts_tier1_then_approved_promotion():
    """
    Example B: inbox_classification Tier 1 #1..#4 fail critical intent classification ->
    TIER_EXHAUSTED_REQUIRES_APPROVAL; before operator approval, no Tier 2 call or expense.
    Following simulated operator approval, Tier 2 #1 can be suggested; no real API call.
    """
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("inbox_classification")

    # All 4 Tier 1 candidates fail
    for i in range(4):
        cand = planner.get_current_candidate(state)
        scorecard = CandidateScorecard(
            task_id="inbox_classification",
            candidate_id=cand.candidate_id,
            tier=ModelTier.TIER_1_BUDGET,
            passed=False,
            score=0.35,
            reasoning_codes=["UNSUBSCRIBE_CONFLATED_WITH_REFERRAL"],
        )
        dec = planner.process_scorecard(state, scorecard)

    assert dec == EvaluationDecision.TIER_EXHAUSTED_REQUIRES_APPROVAL
    assert state.status == "tier_exhausted"

    # Before operator approval, candidate resolution returns None (no silent promotion)
    assert planner.get_current_candidate(state) is None

    # Operator explicitly approves promotion
    dec_promote = planner.promote_tier(state, operator_approval=True)
    assert dec_promote == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
    assert state.current_tier == ModelTier.TIER_2_BALANCED

    tier2_cand1 = planner.get_current_candidate(state)
    assert tier2_cand1.candidate_id == "t2_c1_openai_standard"
    assert tier2_cand1.tier == ModelTier.TIER_2_BALANCED


def test_mandatory_example_c_outreach_draft_safety_failure_forbids_pass():
    """
    Example C: outreach_draft Tier 1 #1 provides valid JSON but invents team size ->
    safety FAIL, not PASS. Offer next Tier 1 candidate; never send email.
    """
    planner = EvaluationPlanner()
    state = planner.get_or_create_state("outreach_draft")

    cand1 = planner.get_current_candidate(state)
    # Valid JSON, but invents team capacity
    scorecard = CandidateScorecard(
        task_id="outreach_draft",
        candidate_id=cand1.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,  # Formally passed text generation benchmark
        safety_critical_passed=False,  # But failed prohibited claim rule!
        score=0.88,
        reasoning_codes=["PROHIBITED_CLAIM_FABRICATED_TEAM_SIZE"],
    )
    dec = planner.process_scorecard(state, scorecard)

    # Must reject approval and advance to next candidate in same tier
    assert dec == EvaluationDecision.PROPOSE_NEXT_IN_SAME_TIER
    assert state.approved_candidate_id is None
    next_cand = planner.get_current_candidate(state)
    assert next_cand.candidate_id == "t1_c2_anthropic_haiku"


def test_mandatory_example_d_job_analysis_passes_slot_1_leaving_15_untested():
    """
    Example D: job_analysis Tier 1 #1 passes -> no need to test the remaining 15 candidates.
    Another task can independently choose a different Tier 1 vendor/model.
    """
    planner = EvaluationPlanner()
    state_job = planner.get_or_create_state("job_analysis")
    state_query = planner.get_or_create_state("discovery_query_generation")

    # job_analysis Tier 1 Candidate 1 passes immediately
    cand_job = planner.get_current_candidate(state_job)
    scorecard = CandidateScorecard(
        task_id="job_analysis",
        candidate_id=cand_job.candidate_id,
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        safety_critical_passed=True,
        score=0.96,
        reasoning_codes=["BIM_SKILLS_CORRECTLY_PARSED"],
    )
    dec_job = planner.process_scorecard(state_job, scorecard)
    assert dec_job == EvaluationDecision.ACCEPT_CANDIDATE
    assert state_job.approved_candidate_id == "t1_c1_openai_mini"

    # Only 1 candidate evaluated out of 16!
    assert len(state_job.history) == 1

    # query generation independently evaluates candidate 2 (Anthropic)
    planner.catalog.task_priority_overrides["discovery_query_generation"] = {
        "tier_1_budget": ["t1_c2_anthropic_haiku"]
    }
    cand_query = planner.get_current_candidate(state_query)
    assert cand_query.candidate_id == "t1_c2_anthropic_haiku"


def test_mandatory_example_e_unconfigured_model_fails_closed():
    """
    Example E: all candidates unavailable / unconfigured -> UNRESOLVED or typed fail-closed error;
    never substitute Ollama or pretend it evaluated the models.
    """
    catalog = ModelCatalog()  # Empty catalog
    router = TaskRouter(catalog=catalog)

    with pytest.raises(LLMNotConfiguredError, match="No operator-approved model route"):
        router.resolve_approved("website_extraction")

    planner = EvaluationPlanner(catalog=catalog)
    state = planner.get_or_create_state("website_extraction")
    # Empty catalog yields None candidate immediately
    assert planner.get_current_candidate(state) is None


# ---------------------------------------------------------------------------
# 6. Website Extraction Subtasks (Pass A & Pass B)
# ---------------------------------------------------------------------------


def test_website_extraction_subtasks_pass_a_and_pass_b_isolated():
    planner = EvaluationPlanner()
    state_pass_a = planner.get_or_create_state("website_extraction", subtask_id="pass_a")
    state_pass_b = planner.get_or_create_state("website_extraction", subtask_id="pass_b")

    # Pass A candidate 1 passes
    scorecard_a = CandidateScorecard(
        task_id="website_extraction",
        subtask_id="pass_a",
        candidate_id="t1_c1_openai_mini",
        tier=ModelTier.TIER_1_BUDGET,
        passed=True,
        score=0.94,
    )
    planner.process_scorecard(state_pass_a, scorecard_a)
    assert state_pass_a.status == "approved"
    assert state_pass_a.approved_candidate_id == "t1_c1_openai_mini"

    # Pass B candidate 1 fails
    scorecard_b = CandidateScorecard(
        task_id="website_extraction",
        subtask_id="pass_b",
        candidate_id="t1_c1_openai_mini",
        tier=ModelTier.TIER_1_BUDGET,
        passed=False,
        score=0.50,
    )
    planner.process_scorecard(state_pass_b, scorecard_b)
    assert state_pass_b.status == "evaluating"
    assert state_pass_b.approved_candidate_id is None
    assert state_pass_b.current_candidate_index == 1
