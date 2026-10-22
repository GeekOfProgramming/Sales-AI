"""
SalesAI S2.5.1 — Output Validation Integrity & Local Safety Precedence Hardening Tests.

Verifies the 8 independently reproduced defects (B1–B8) and Section 3 test matrix:
1. Nonempty invented evidence ref with approved_evidence_ids=None fails.
2. Nonempty invented ref with explicit empty set fails.
3. Explicit trusted set cannot be expanded by metadata.
4. Valid evidence ID passes and needs business review.
5. Duplicate and malformed evidence IDs fail.
6. Local opt-out + not_now fails and cannot schedule future follow-up.
7. Local opt-out + not_interested / unknown / auto_reply fails or is consistently overridden.
8. Local opt-out + meeting_proposed remains non-actionable.
9. Local bounce + meeting_proposed is blocked.
10. Local bounce + positive_interest is blocked.
11. Local suppression + question is blocked from actionable positive classification.
12. Multiple simultaneous local signals have deterministic precedence.
13. Missing provider in request fails closed.
14. Missing model in request fails closed.
15. Both provider and model pinned correctly still pass.
16. Website Pass A request cannot be validated as Pass B by argument override.
17. Valid same-subtask request passes.
18. Nonwebsite subtask injection fails.
19. Attacker-controlled schema key resembling an email never appears in safe_log_dict or repr.
20. Schema key containing a token-shaped secret cannot enter reason codes or repair plans.
21. Numeric string confidence is rejected.
22. Numeric string query priority is rejected.
23. JSON number confidence is accepted.
24. Rejected error cannot become auto-retryable or trigger tier escalation.
25. Sync and async results remain identical.
26. All older S2.5 positive fixtures still pass.
27. All reason codes match bounded uppercase alphanumeric whitelist.
"""

import json
import pytest
import re
from typing import Any, Dict, Optional

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse
from sales_engine.llm_gateway.output_validation import LLMOutputValidator, OutputValidationResult
from sales_engine.llm_gateway.repair_policy import RepairPolicy


def _make_req(
    task_id: str = "inbox_classification",
    provider: Optional[str] = "google",
    model: Optional[str] = "gemini-2.5-flash",
    request_id: str = "req-s251-test-001",
    metadata: Optional[Dict[str, Any]] = None,
) -> LLMRequest:
    return LLMRequest(
        request_id=request_id,
        task_id=task_id,
        provider=provider,
        model=model,
        prompt="Synthesize output for test scenario.",
        metadata=metadata or {},
    )


def _make_resp(
    content: str,
    req: LLMRequest,
    finish_reason: str = "stop",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMResponse:
    return LLMResponse(
        request_id=req.request_id,
        task_id=req.task_id,
        provider=provider or req.provider or "google",
        model=model or req.model or "gemini-2.5-flash",
        content=content,
        finish_reason=finish_reason,
        input_tokens=100,
        output_tokens=50,
        latency_ms=120,
    )


# ===========================================================================
# Defect B1: Nonempty evidence refs with missing/empty approved evidence set
# ===========================================================================

def test_b1_nonempty_evidence_refs_with_none_approved_set_fails():
    """B1 reproduction: Citing evidence refs without an approved set must fail with EVIDENCE_APPROVED_SET_REQUIRED."""
    validator = LLMOutputValidator()
    req = _make_req("outreach_draft")
    data = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["JOB-001"],
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, approved_evidence_ids=None)

    assert res.is_valid is False
    assert "EVIDENCE_APPROVED_SET_REQUIRED" in res.failure_reasons
    assert res.needs_business_review is True
    assert res.validated_data is None


def test_b1_nonempty_evidence_refs_with_empty_approved_set_fails():
    """B1 reproduction: Explicit empty approved evidence set must fail with EVIDENCE_APPROVED_SET_REQUIRED."""
    validator = LLMOutputValidator()
    req = _make_req("outreach_draft")
    data = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["JOB-001"],
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, approved_evidence_ids=set())

    assert res.is_valid is False
    assert "EVIDENCE_APPROVED_SET_REQUIRED" in res.failure_reasons
    assert res.validated_data is None


def test_b1_empty_evidence_refs_without_approved_set_passes():
    """Empty evidence_refs does not require an approved evidence set."""
    validator = LLMOutputValidator()
    req = _make_req("outreach_draft")
    data = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": [],
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, approved_evidence_ids=None)

    assert res.is_valid is True
    assert res.validated_data is not None


# ===========================================================================
# Defect B2: Request metadata cannot expand or override trusted evidence set
# ===========================================================================

def test_b2_request_metadata_cannot_expand_trusted_evidence_allowlist():
    """B2 reproduction: Explicit approved_evidence_ids is authoritative; request metadata cannot add IDs."""
    validator = LLMOutputValidator()
    # Metadata attempts to inject FAKE-0001
    req = _make_req(
        "outreach_draft",
        metadata={"approved_evidence_ids": ["FAKE-0001", "INJECTED-002"]},
    )
    data = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["FAKE-0001"],
    }
    resp = _make_resp(json.dumps(data), req)
    # Trusted caller provides only VERIFIED-01
    res = validator.validate(req, resp, approved_evidence_ids={"VERIFIED-01"})

    assert res.is_valid is False
    assert "EVIDENCE_UNKNOWN_REF" in res.failure_reasons
    assert res.validated_data is None


def test_b2_authoritative_allowlist_passes_valid_ref_with_review_flag():
    """Authoritative approved_evidence_ids passes valid references and sets needs_business_review."""
    validator = LLMOutputValidator()
    req = _make_req("outreach_draft")
    data = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["VERIFIED-01"],
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, approved_evidence_ids={"VERIFIED-01", "VERIFIED-02"})

    assert res.is_valid is True
    assert res.needs_business_review is True
    assert res.validated_data["evidence_refs"] == ["VERIFIED-01"]


def test_b2_duplicate_and_malformed_evidence_refs_fail():
    """Duplicate references and blank/malformed references fail closed."""
    validator = LLMOutputValidator()
    req = _make_req("outreach_draft")

    # Duplicate ref
    data_dup = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["VERIFIED-01", "VERIFIED-01"],
    }
    resp_dup = _make_resp(json.dumps(data_dup), req)
    res_dup = validator.validate(req, resp_dup, approved_evidence_ids={"VERIFIED-01"})
    assert res_dup.is_valid is False
    assert "EVIDENCE_DUPLICATE_REF" in res_dup.failure_reasons

    # Blank/whitespace ref
    data_blank = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["   "],
    }
    resp_blank = _make_resp(json.dumps(data_blank), req)
    res_blank = validator.validate(req, resp_blank, approved_evidence_ids={"VERIFIED-01"})
    assert res_blank.is_valid is False
    assert "EVIDENCE_MALFORMED_REF" in res_blank.failure_reasons


# ===========================================================================
# Defect B3: Local opt-out precedence over all conflicting model categories
# ===========================================================================

def test_b3_local_opt_out_overrides_not_now_category():
    """B3 reproduction: Local opt-out must override 'not_now' and prevent scheduling follow-ups."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "not_now",
        "confidence": 0.95,
        "reasoning": "Prospect said check back next quarter.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_opt_out_detected": True},
    )

    assert res.is_valid is False
    assert "OPT_OUT_OVERRIDE_PREVENTED" in res.failure_reasons
    assert res.is_opt_out_overridden is True
    assert res.effective_local_disposition == "unsubscribe"
    assert res.needs_business_review is True
    assert res.validated_data is None


@pytest.mark.parametrize("category", ["not_interested", "unknown", "auto_reply", "bounce"])
def test_b3_local_opt_out_overrides_conflicting_categories(category: str):
    """Local opt-out must override not_interested, unknown, auto_reply, bounce."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": category,
        "confidence": 0.88,
        "reasoning": f"Model classified as {category}.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_opt_out_detected": True},
    )

    assert res.is_valid is False
    assert "OPT_OUT_OVERRIDE_PREVENTED" in res.failure_reasons
    assert res.is_opt_out_overridden is True
    assert res.effective_local_disposition == "unsubscribe"


def test_b3_local_opt_out_overrides_meeting_proposed_non_actionable():
    """Local opt-out over meeting_proposed fails closed with effective_local_disposition='unsubscribe'."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "meeting_proposed",
        "confidence": 0.92,
        "reasoning": "Prospect requested a demo call.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_opt_out_detected": True},
    )

    assert res.is_valid is False
    assert "OPT_OUT_OVERRIDE_PREVENTED" in res.failure_reasons
    assert res.is_opt_out_overridden is True
    assert res.effective_local_disposition == "unsubscribe"


def test_b3_local_opt_out_matching_unsubscribe_passes():
    """When both model and local signal agree on unsubscribe, it passes with review flag."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "unsubscribe",
        "confidence": 1.0,
        "reasoning": "Prospect clearly requested removal.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_opt_out_detected": True},
    )

    assert res.is_valid is True
    assert res.is_opt_out_overridden is False
    assert res.effective_local_disposition is None
    assert res.validated_data["category"] == "unsubscribe"


# ===========================================================================
# Defect B4: Local bounce & suppression precedence
# ===========================================================================

def test_b4_local_bounce_blocks_meeting_proposed():
    """B4 reproduction: Local bounce signal must block meeting_proposed and set disposition='bounce'."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "meeting_proposed",
        "confidence": 0.90,
        "reasoning": "Model misidentified bounce text as meeting invitation.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_bounce_detected": True},
    )

    assert res.is_valid is False
    assert "BOUNCE_OVERRIDE_PREVENTED" in res.failure_reasons
    assert res.is_bounce_overridden is True
    assert res.effective_local_disposition == "bounce"
    assert res.validated_data is None


def test_b4_local_bounce_blocks_positive_interest():
    """Local bounce signal must block positive_interest."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "positive_interest",
        "confidence": 0.85,
        "reasoning": "Prospect seems interested.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(
        req,
        resp,
        local_signals={"is_bounce_detected": True},
    )

    assert res.is_valid is False
    assert "BOUNCE_OVERRIDE_PREVENTED" in res.failure_reasons
    assert res.is_bounce_overridden is True
    assert res.effective_local_disposition == "bounce"


def test_b4_local_suppression_blocks_question_and_not_now():
    """Local suppression signal blocks questions and follow-ups with disposition='do_not_contact'."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")

    for cat in ["question", "not_now", "positive_interest"]:
        data = {
            "category": cat,
            "confidence": 0.89,
            "reasoning": f"Model classified as {cat}.",
        }
        resp = _make_resp(json.dumps(data), req)
        res = validator.validate(
            req,
            resp,
            local_signals={"is_suppressed": True},
        )
        assert res.is_valid is False
        assert "SUPPRESSION_OVERRIDE_PREVENTED" in res.failure_reasons
        assert res.is_suppression_overridden is True
        assert res.effective_local_disposition == "do_not_contact"


def test_b4_simultaneous_local_signals_deterministic_precedence():
    """Simultaneous local signals must follow deterministic precedence: 1. Opt-out, 2. Bounce, 3. Suppression."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "positive_interest",
        "confidence": 0.90,
        "reasoning": "Interested.",
    }
    resp = _make_resp(json.dumps(data), req)

    # Opt-out + Bounce -> Opt-out wins
    res_opt_bounce = validator.validate(
        req,
        resp,
        local_signals={"is_opt_out_detected": True, "is_bounce_detected": True},
    )
    assert "OPT_OUT_OVERRIDE_PREVENTED" in res_opt_bounce.failure_reasons
    assert res_opt_bounce.effective_local_disposition == "unsubscribe"
    assert res_opt_bounce.is_opt_out_overridden is True

    # Bounce + Suppression -> Bounce wins
    res_bounce_supp = validator.validate(
        req,
        resp,
        local_signals={"is_bounce_detected": True, "is_suppressed": True},
    )
    assert "BOUNCE_OVERRIDE_PREVENTED" in res_bounce_supp.failure_reasons
    assert res_bounce_supp.effective_local_disposition == "bounce"
    assert res_bounce_supp.is_bounce_overridden is True


# ===========================================================================
# Defect B5: Request missing pinned provider or model identity fails closed
# ===========================================================================

def test_b5_missing_provider_in_request_fails_closed():
    """B5 reproduction: Request with provider=None or empty string must fail closed."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification", provider=None)
    data = {"category": "question", "confidence": 0.9}
    resp = _make_resp(json.dumps(data), req, provider="google")
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert "MISSING_APPROVED_PROVIDER_IDENTITY" in res.failure_reasons
    assert res.validated_data is None


def test_b5_missing_model_in_request_fails_closed():
    """B5 reproduction: Request with model=None must fail closed."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification", model=None)
    data = {"category": "question", "confidence": 0.9}
    resp = _make_resp(json.dumps(data), req, model="gemini-2.5-flash")
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert "MISSING_APPROVED_MODEL_IDENTITY" in res.failure_reasons
    assert res.validated_data is None


def test_b5_pinned_provider_and_model_pass():
    """When both provider and model are pinned and match response, validation passes."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification", provider="google", model="gemini-2.5-flash")
    data = {"category": "question", "confidence": 0.9}
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is True
    assert not any(r.startswith("MISSING_") or r.startswith("IDENTITY_") for r in res.failure_reasons)


# ===========================================================================
# Defect B6: Subtask mismatches and unauthorized subtask injection
# ===========================================================================

def test_b6_subtask_mismatch_metadata_vs_argument_fails():
    """B6 reproduction: Request metadata declaring pass_a cannot be validated as pass_b by argument."""
    validator = LLMOutputValidator()
    req = _make_req(
        "website_extraction",
        metadata={"subtask_id": "pass_a"},
    )
    # Payload valid for pass_b, but request was bound to pass_a
    data = {
        "icp_fit": True,
        "company_profile": "Architecture firm specializing in BIM.",
        "signals": ["hiring_revit"],
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, subtask_id="pass_b")

    assert res.is_valid is False
    assert "IDENTITY_MISMATCH_SUBTASK" in res.failure_reasons


def test_b6_matching_subtask_passes():
    """Consistent subtask between request metadata and argument passes."""
    validator = LLMOutputValidator()
    req = _make_req(
        "website_extraction",
        metadata={"subtask_id": "pass_a"},
    )
    data = {
        "company_name": "Studio BIM",
        "company_summary": "Architecture firm specializing in BIM automation.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, subtask_id="pass_a")

    assert res.is_valid is True
    assert res.subtask_id == "pass_a"


def test_b6_non_website_subtask_injection_fails():
    """Non-website tasks cannot define subtasks; subtask injection must fail."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {"category": "question", "confidence": 0.9}
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp, subtask_id="pass_a")

    assert res.is_valid is False
    assert "IDENTITY_MISMATCH_SUBTASK" in res.failure_reasons


# ===========================================================================
# Defect B7: Schema-extra fields must not leak attacker keys or secrets
# ===========================================================================

def test_b7_attacker_schema_key_with_email_never_leaks():
    """B7 reproduction: Key resembling an email address must never appear in reasons, safe_log_dict, or repr."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    attacker_key = "email_alice@example.com"
    data = {
        "category": "question",
        "confidence": 0.9,
        attacker_key: "exfiltrated_data",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert "SCHEMA_EXTRA_FIELD" in res.failure_reasons

    # Verify zero leakage of the attacker key in failure reasons
    for r in res.failure_reasons:
        assert "alice@example.com" not in r
        assert attacker_key not in r

    # Verify zero leakage in safe_log_dict()
    log_dict = res.safe_log_dict()
    log_dict_str = json.dumps(log_dict)
    assert "alice@example.com" not in log_dict_str
    assert attacker_key not in log_dict_str

    # Verify zero leakage in __repr__ and __str__
    assert "alice@example.com" not in repr(res)
    assert "alice@example.com" not in str(res)


def test_b7_secret_token_in_schema_key_never_enters_reason_codes_or_repair():
    """Schema key containing a token-shaped secret cannot enter reason codes or repair plans."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    secret_key = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    data = {
        "category": "question",
        "confidence": 0.9,
        secret_key: "leaked_token",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert "SCHEMA_EXTRA_FIELD" in res.failure_reasons
    assert secret_key not in str(res.failure_reasons)
    if res.repair_proposal:
        assert secret_key not in str(res.repair_proposal.reason_codes)
        assert secret_key not in repr(res.repair_proposal)


# ===========================================================================
# Defect B8: Strict types rejecting string-to-number coercions
# ===========================================================================

def test_b8_numeric_string_confidence_rejected():
    """B8 reproduction: Numeric string '0.98' in float field must be rejected under strict=True."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "question",
        "confidence": "0.98",  # String coercion must fail
        "reasoning": "Valid reasoning string.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert any("SCHEMA_TYPE_ERROR_CONFIDENCE" in r for r in res.failure_reasons)
    assert res.validated_data is None


def test_b8_numeric_string_query_priority_rejected():
    """Numeric string '10' in int field must be rejected under strict=True."""
    validator = LLMOutputValidator()
    req = _make_req("discovery_query_generation")
    data = {
        "queries": [
            {
                "query": "Revit developer jobs Milan",
                "type": "general_job",
                "priority": "10",  # String coercion must fail
            }
        ]
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is False
    assert any("SCHEMA_TYPE_ERROR" in r and "PRIORITY" in r for r in res.failure_reasons)
    assert res.validated_data is None


def test_b8_valid_json_numbers_accepted():
    """Proper JSON float and int numbers are accepted under strict validation."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "question",
        "confidence": 0.98,  # True float
        "reasoning": "Valid reasoning string.",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert res.is_valid is True
    assert res.validated_data["confidence"] == 0.98


# ===========================================================================
# Architectural & Safety Invariants: Non-repairable, Offline, Sync/Async
# ===========================================================================

def test_s2_5_1_rejected_failures_not_repairable():
    """Security, provenance, and identity failures must never generate repairable proposals."""
    validator = LLMOutputValidator()

    # Evidence required
    req_ev = _make_req("outreach_draft")
    data_ev = {
        "subject": "Automation for BIM pipelines",
        "body": "Hi Andrea, noticed your recent job openings for Revit Developers.",
        "service_used": "Managed BIM Automation",
        "evidence_refs": ["JOB-001"],
    }
    resp_ev = _make_resp(json.dumps(data_ev), req_ev)
    res_ev = validator.validate(req_ev, resp_ev, approved_evidence_ids=None)
    assert res_ev.repair_proposal is not None
    assert res_ev.repair_proposal.repairable is False
    assert res_ev.repair_proposal.dispatch_allowed is False

    # Bounce override
    req_bounce = _make_req("inbox_classification")
    data_bounce = {"category": "positive_interest", "confidence": 0.9}
    resp_bounce = _make_resp(json.dumps(data_bounce), req_bounce)
    res_bounce = validator.validate(req_bounce, resp_bounce, local_signals={"is_bounce_detected": True})
    assert res_bounce.repair_proposal is not None
    assert res_bounce.repair_proposal.repairable is False

    # Missing provider
    req_prov = _make_req("inbox_classification", provider=None)
    resp_prov = _make_resp(json.dumps(data_bounce), req_prov, provider="google")
    res_prov = validator.validate(req_prov, resp_prov)
    assert res_prov.repair_proposal is not None
    assert res_prov.repair_proposal.repairable is False


@pytest.mark.asyncio
async def test_s2_5_1_sync_and_async_parity():
    """Sync and async validation entrypoints must produce identical results."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {"category": "question", "confidence": 0.85, "reasoning": "Need pricing."}
    resp = _make_resp(json.dumps(data), req)

    res_sync = validator.validate(req, resp)
    res_async = await validator.validate_async(req, resp)

    assert res_sync.is_valid == res_async.is_valid
    assert res_sync.failure_reasons == res_async.failure_reasons
    assert res_sync.validated_data == res_async.validated_data
    assert res_sync.needs_business_review == res_async.needs_business_review
    assert res_sync.effective_local_disposition == res_async.effective_local_disposition


def test_s2_5_1_all_positive_fixtures_pass():
    """All 6 task families (7 schema targets) pass with valid schemas and pinned identities."""
    validator = LLMOutputValidator()

    fixtures = [
        ("website_extraction", "pass_a", {"company_name": "Studio BIM", "company_summary": "Architecture firm specializing in BIM automation."}),
        ("website_extraction", "pass_b", {"buyer_roles": ["BIM Director"], "primary_job_signals": ["Revit Developer"]}),
        ("job_analysis", None, {"technologies": ["Revit", "Dynamo"], "requirements": ["5+ years experience"], "seniority": "senior"}),
        ("discovery_query_generation", None, {"queries": [{"query": "Revit developer jobs Milan", "type": "general_job", "priority": 1}]}),
        ("inbox_classification", None, {"category": "question", "confidence": 0.9, "reasoning": "Prospect requested pricing."}),
        ("reply_suggestion", None, {"subject": "Re: Automation enquiry", "body": "Thank you for reaching out regarding our BIM automation services."}),
        ("outreach_draft", None, {"subject": "Automation for BIM pipelines", "body": "Hi Andrea, noticed your recent job openings for Revit Developers.", "service_used": "Managed BIM Automation"}),
    ]

    for task_id, subtask_id, payload in fixtures:
        req = _make_req(task_id, metadata={"subtask_id": subtask_id} if subtask_id else {})
        resp = _make_resp(json.dumps(payload), req)
        res = validator.validate(req, resp, subtask_id=subtask_id)
        assert res.is_valid is True, f"Failed on {task_id}/{subtask_id}: {res.failure_reasons}"
        assert res.validated_data is not None


def test_b7_sanitized_reason_code_bounding():
    """Verify that all reason codes are strictly uppercase alphanumeric or underscore, <= 64 chars."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    data = {
        "category": "invalid_enum_value",
        "extra_field_123": "val",
    }
    resp = _make_resp(json.dumps(data), req)
    res = validator.validate(req, resp)

    assert len(res.failure_reasons) > 0
    for code in res.failure_reasons:
        assert re.match(r"^[A-Z0-9_]{1,64}$", code), f"Reason code not strictly sanitized: {code}"
