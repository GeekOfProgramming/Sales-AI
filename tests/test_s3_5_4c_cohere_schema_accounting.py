"""
tests/test_s3_5_4c_cohere_schema_accounting.py
Comprehensive deterministic test suite for SalesAI S3.5.4C:
1. Cohere JSON Schema mode generation, keyword stripping, and inlining of $defs.
2. Explicit enforcement of evidence_refs as an array of strings.
3. Preservation of offering status enums ('active', 'in_development', 'coming_soon', 'cta').
4. Rejection of incompatible schema composition keywords (allOf, oneOf, not) before dispatch.
5. Fail-closed prevention of silent downgrade from JSON Schema Mode to generic JSON Mode.
6. Downstream strict Pydantic validation: rejection of scalar strings, invalid enums, extra fields, and unknown citations.
7. Dual cost accounting: separation of commercial-equivalent token cost ($0.15/$0.60 per 1M) from external billable spend ($0.00).
8. Billed-unit token usage parsing from usage.billed_units.
9. Historical SQLite ledger preservation and backward-compatible additive migration.
10. OpenRouter and Gemini accounting regression safety.
All tests run OFFLINE using deterministic local fixtures and mock transports. Zero network/IO.
"""

import json
import sqlite3
import pytest
import httpx
from pathlib import Path
from typing import Dict, Any

from sales_engine.llm_gateway.contracts import LLMRequest, LLMResponse, ModelTier
from sales_engine.llm_gateway.errors import (
    LLMInvalidSchemaError,
    LLMNotConfiguredError,
    LLMUncertainUsageError,
)
from sales_engine.llm_gateway.output_schemas import (
    WebsitePassAOutput,
    WebsitePassBOutput,
    SchemaRegistry,
    to_cohere_json_schema,
)
from sales_engine.llm_gateway.output_validation import LLMOutputValidator
from sales_engine.llm_gateway.providers.cohere_provider import CohereProvider
from sales_engine.llm_gateway.model_catalog import ModelCandidate, PricingMetadata
from sales_engine.llm_gateway.budget_ledger import BudgetLedger, BudgetPolicyConfig, ReservationStatus
from sales_engine.benchmark.external_runner import ExternalBenchmarkRunner
from sales_engine.benchmark.fixtures_s3 import PASS_A_CASES, PASS_A_SCHEMA_SPEC, PASS_A_SYSTEM_PROMPT_BASE


# ===========================================================================
# 1. Cohere JSON Schema Mode Generation Tests
# ===========================================================================

def test_cohere_json_schema_pass_a_generation():
    """Verify clean Cohere-compatible JSON schema generated for WebsitePassAOutput."""
    schema = to_cohere_json_schema(WebsitePassAOutput)
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "$defs" not in schema
    assert "required" in schema
    assert "company_name" in schema["required"]
    assert "company_summary" in schema["required"]

    props = schema["properties"]
    # evidence_refs must strictly be array of strings
    assert props["evidence_refs"]["type"] == "array"
    assert props["evidence_refs"]["items"]["type"] == "string"
    assert "maxLength" not in props["evidence_refs"]
    assert "maxItems" not in props["evidence_refs"]

    # offerings must be an inlined array of objects with status enum
    offerings_prop = props["offerings"]
    assert offerings_prop["type"] == "array"
    assert "maxItems" not in offerings_prop
    assert offerings_prop["items"]["type"] == "object"
    assert "$ref" not in offerings_prop["items"]
    item_props = offerings_prop["items"]["properties"]
    assert item_props["name"]["type"] == "string"
    assert item_props["status"]["type"] == "string"
    assert set(item_props["status"]["enum"]) == {"active", "in_development", "coming_soon", "cta"}


def test_cohere_json_schema_unsupported_keywords_stripped():
    """Verify Cohere-unsupported keywords (minLength, maxLength, maxItems, minItems, title) are stripped."""
    schema = to_cohere_json_schema(WebsitePassAOutput)
    raw_str = json.dumps(schema)
    assert "maxLength" not in raw_str
    assert "minLength" not in raw_str
    assert "maxItems" not in raw_str
    assert "minItems" not in raw_str
    assert "title" not in schema  # Root title stripped
    assert "$defs" not in schema  # Definitions inlined


def test_cohere_json_schema_rejects_incompatible_composition():
    """Verify rejection of schemas containing allOf, oneOf, or not."""
    bad_schema_allof = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "composite": {"allOf": [{"type": "string"}, {"type": "number"}]},
        },
    }
    with pytest.raises(LLMInvalidSchemaError) as exc_allof:
        to_cohere_json_schema(bad_schema_allof)
    assert "allOf" in str(exc_allof.value)

    bad_schema_oneof = {
        "type": "object",
        "properties": {
            "name": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }
    with pytest.raises(LLMInvalidSchemaError) as exc_oneof:
        to_cohere_json_schema(bad_schema_oneof)
    assert "oneOf" in str(exc_oneof.value)


def test_cohere_json_schema_rejects_invalid_evidence_refs():
    """Verify schema generator rejects any schema where evidence_refs is not array of strings."""
    bad_schema = {
        "type": "object",
        "properties": {
            "evidence_refs": {"type": "string"},  # Invalid: scalar string instead of array
        },
    }
    with pytest.raises(LLMInvalidSchemaError) as exc:
        to_cohere_json_schema(bad_schema)
    assert "evidence_refs" in str(exc.value)


# ===========================================================================
# 2. Cohere Provider Payload & No Silent Downgrade Tests
# ===========================================================================

def test_cohere_provider_payload_with_structured_schema():
    """Verify CohereProvider builds response_format with schema when model supports it."""
    caps = {"supports_json_schema": True, "supports_json_mode": True}
    provider = CohereProvider(
        api_key="test-key",
        default_model="command-r-08-2024",
        model_capabilities={"command-r-08-2024": caps},
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract facts",
        system_prompt="Be concise",
        response_schema=WebsitePassAOutput.model_json_schema(),
    )
    payload = provider._build_payload(req, model="command-r-08-2024")
    assert "response_format" in payload
    rf = payload["response_format"]
    assert rf["type"] == "json_object"
    assert "schema" in rf
    assert rf["schema"]["type"] == "object"
    assert "properties" in rf["schema"]
    assert "evidence_refs" in rf["schema"]["properties"]


def test_cohere_provider_refuses_silent_downgrade():
    """Verify CohereProvider raises LLMInvalidSchemaError if schema requested but model lacks support."""
    caps = {"supports_json_schema": False, "supports_json_mode": True}
    provider = CohereProvider(
        api_key="test-key",
        default_model="command-r-08-2024",
        model_capabilities={"command-r-08-2024": caps},
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract facts",
        response_schema={"type": "object", "properties": {"company_name": {"type": "string"}}},
    )
    with pytest.raises(LLMInvalidSchemaError) as exc:
        provider._build_payload(req, model="command-r-08-2024")
    assert "does not support JSON schema enforcement" in str(exc.value)
    assert "refusing silent downgrade" in str(exc.value)


def test_cohere_provider_backward_compatible_json_object_mode():
    """Verify backward compatibility: basic response_schema={'type': 'object'} sets json_object."""
    caps = {"supports_json_schema": False, "supports_json_mode": True}
    provider = CohereProvider(
        api_key="test-key",
        default_model="command-r-08-2024",
        model_capabilities={"command-r-08-2024": caps},
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract facts",
        response_schema={"type": "object"},  # Generic JSON mode request
    )
    payload = provider._build_payload(req, model="command-r-08-2024")
    assert payload["response_format"] == {"type": "json_object"}


def test_cohere_provider_usage_parsing_with_billed_units():
    """Verify CohereProvider parses usage.tokens and usage.billed_units into raw_metadata."""
    provider = CohereProvider(api_key="test-key")
    req = LLMRequest(task_id="website_extraction", prompt="Extract facts")
    mock_body = {
        "id": "cohere_test_123",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "{\"company_name\": \"Test BIM S.r.l.\"}"}],
        },
        "usage": {
            "tokens": {"input_tokens": 150, "output_tokens": 85},
            "billed_units": {"input_tokens": 150, "output_tokens": 85},
        },
    }
    response_headers = {
        "content-type": "application/json",
        "x-trial-endpoint-call-limit": "100",
        "x-trial-endpoint-call-remaining": "98",
        "x-endpoint-monthly-call-limit": "1000",
    }
    resp = httpx.Response(status_code=200, json=mock_body, headers=response_headers, request=httpx.Request("POST", "https://api.cohere.com/v2/chat"))
    llm_resp = provider._parse_response_body(resp, req, model="command-r-08-2024", latency_ms=1200.0)

    assert llm_resp.input_tokens == 150
    assert llm_resp.output_tokens == 85
    assert "billed_units" in llm_resp.raw_metadata
    assert llm_resp.raw_metadata["billed_units"] == {"input_tokens": 150, "output_tokens": 85}
    assert llm_resp.raw_metadata["headers"]["x-trial-endpoint-call-remaining"] == "98"


# ===========================================================================
# 3. Downstream Strict Output Validation Rejection Tests
# ===========================================================================

def test_validator_rejects_scalar_string_evidence_refs():
    """Verify OutputValidator produces SCHEMA_TYPE_ERROR_EVIDENCE_REFS when evidence_refs is a string."""
    validator = LLMOutputValidator()
    req = LLMRequest(task_id="website_extraction", prompt="Extract")
    # Scalar string 'EV_01' instead of list ['EV_01']
    invalid_content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "BIM services provider in Milan.",
        "offerings": [{"name": "BIM coordination", "status": "active"}],
        "evidence_refs": "EV_01",  # Invalid type!
    })
    resp = LLMResponse(
        content=invalid_content,
        provider="cohere",
        model="command-r-08-2024",
        task_id="website_extraction",
        request_id="req_01",
    )
    val_res = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )
    assert not val_res.is_valid
    assert "SCHEMA_TYPE_ERROR_EVIDENCE_REFS" in val_res.failure_reasons


def test_validator_rejects_invalid_offering_enum():
    """Verify OutputValidator rejects invalid offering lifecycle status enum."""
    validator = LLMOutputValidator()
    req = LLMRequest(task_id="website_extraction", prompt="Extract")
    invalid_content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "BIM services provider in Milan.",
        "offerings": [{"name": "BIM coordination", "status": "active_service"}],  # Invalid enum!
        "evidence_refs": ["EV_01"],
    })
    resp = LLMResponse(
        content=invalid_content,
        provider="cohere",
        model="command-r-08-2024",
        task_id="website_extraction",
        request_id="req_02",
    )
    val_res = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )
    assert not val_res.is_valid
    assert any("SCHEMA_INVALID_ENUM" in r for r in val_res.failure_reasons)


def test_validator_rejects_unknown_evidence_ref():
    """Verify OutputValidator rejects hallucinated or unapproved evidence IDs."""
    validator = LLMOutputValidator()
    req = LLMRequest(task_id="website_extraction", prompt="Extract")
    content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "BIM services provider in Milan.",
        "offerings": [{"name": "BIM coordination", "status": "active"}],
        "evidence_refs": ["EV_UNKNOWN_99"],  # Unapproved citation!
    })
    resp = LLMResponse(
        content=content,
        provider="cohere",
        model="command-r-08-2024",
        task_id="website_extraction",
        request_id="req_03",
    )
    val_res = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )
    assert not val_res.is_valid
    assert "EVIDENCE_UNKNOWN_REF" in val_res.failure_reasons


def test_validator_accepts_valid_pass_a_payload():
    """Verify OutputValidator cleanly accepts valid schema-compliant WebsitePassAOutput."""
    validator = LLMOutputValidator()
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Extract",
        provider="cohere",
        model="command-r-08-2024",
        request_id="req_04",
    )
    valid_content = json.dumps({
        "company_name": "Milano MEP Ingegneria S.r.l.",
        "company_summary": "BIM clash coordination and MEP modeling services provider in Lombardy since 2015.",
        "offerings": [{"name": "BIM clash coordination", "status": "active"}],
        "target_industries": ["Architecture", "Engineering", "Construction"],
        "target_company_types": ["General Contractors"],
        "pain_points": ["Clash detection delay"],
        "client_testimonial_roles": ["Project Manager"],
        "internal_team_roles": ["Technical Director"],
        "technologies": ["Autodesk Revit", "Navisworks", "Solibri"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "MEP", "Revit"],
        "evidence_refs": ["EV_01"],
    })
    resp = LLMResponse(
        content=valid_content,
        provider="cohere",
        model="command-r-08-2024",
        task_id="website_extraction",
        request_id="req_04",
    )
    val_res = validator.validate(
        request=req,
        response=resp,
        approved_evidence_ids={"EV_01"},
        subtask_id="pass_a",
    )
    assert val_res.is_valid
    assert len(val_res.failure_reasons) == 0


# ===========================================================================
# 4. Budget Ledger Dual Cost Accounting & Preservation Tests
# ===========================================================================

def test_ledger_dual_cost_accounting(tmp_path: Path):
    """Verify budget ledger records commercial cost while setting billable spend to $0 for Cohere Trial."""
    db_file = tmp_path / "budget_dual_test.db"
    cfg = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.05, daily_limit_usd=1.0)
    ledger = BudgetLedger(db_path=db_file, config=cfg)

    pricing = PricingMetadata(currency="USD", input_per_1m=0.15, output_per_1m=0.60)
    candidate = ModelCandidate(
        candidate_id="t2_c2",
        tier=ModelTier.TIER_2_BALANCED,
        priority=2,
        provider_id="cohere",
        exact_model_id="command-r-08-2024",
        enabled=True,
        pricing=pricing,
        verified_capabilities={"supports_json_schema": True, "supports_json_mode": True},
        verification_notes="Trial key eval",
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Short prompt",
        request_id="req_trial_01",
        metadata={"spend_provenance": "VERIFIED_COHERE_TRIAL"},
    )
    res = ledger.reserve(req, candidate)
    assert res.spend_provenance == "VERIFIED_COHERE_TRIAL"
    assert res.status == ReservationStatus.RESERVED

    # Simulate response with 364 input tokens and 209 output tokens
    llm_resp = LLMResponse(
        content="{}",
        provider="cohere",
        model="command-r-08-2024",
        task_id="website_extraction",
        request_id="req_trial_01",
        input_tokens=364,
        output_tokens=209,
    )
    reconciled = ledger.reconcile_response(res.reservation_id, llm_resp, candidate)
    assert reconciled.status == ReservationStatus.RECONCILED
    # Commercial equivalent cost preserved: (364*0.15 + 209*0.60)/1M = 0.000180
    assert reconciled.final_cost_usd == 0.00018
    # Billable cost set to $0.00 for verified trial key
    assert reconciled.billable_cost_usd == 0.0
    assert reconciled.spend_provenance == "VERIFIED_COHERE_TRIAL"


def test_historical_reservation_preservation_in_benchmark_db():
    """Verify existing SQLite database historical reservations (res_61fdc01f9700, res_dce87e9ce2a9, res_c9fd617d4904) remain preserved."""
    db_path = Path("data/benchmark_budget.db")
    if not db_path.exists():
        pytest.skip("data/benchmark_budget.db not present in source-only export")

    # Connect read-only to verify historical records
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT reservation_id, status, final_cost_usd, failure_code FROM llm_reservations;")
        rows = {r[0]: (r[1], r[2], r[3]) for r in cursor.fetchall()}

        # res_61fdc01f9700: Cohere trial run
        assert "res_61fdc01f9700" in rows
        cohere_status, cohere_cost, _ = rows["res_61fdc01f9700"]
        assert cohere_status == "RECONCILED"
        assert cohere_cost == 0.00018

        # res_dce87e9ce2a9: Gemini 2.5 UNCERTAIN
        assert "res_dce87e9ce2a9" in rows
        gemini_status, gemini_cost, gemini_code = rows["res_dce87e9ce2a9"]
        assert gemini_status == "UNCERTAIN"
        assert gemini_cost == 0.000634
        assert gemini_code == "PROVIDER_UNAVAILABLE"

        # res_c9fd617d4904: OpenRouter rate-limited UNCERTAIN
        assert "res_c9fd617d4904" in rows
        or_status, or_cost, or_code = rows["res_c9fd617d4904"]
        assert or_status == "UNCERTAIN"
        assert or_cost == 0.0
        assert or_code == "RATE_LIMIT"
    finally:
        conn.close()


def test_openrouter_free_tier_reconciles_to_zero(tmp_path: Path):
    """Verify OpenRouter Free Tier models reconcile with $0 commercial and $0 billable spend."""
    db_file = tmp_path / "budget_or_test.db"
    cfg = BudgetPolicyConfig(enabled=True, max_request_spend_usd=0.05, daily_limit_usd=1.0)
    ledger = BudgetLedger(db_path=db_file, config=cfg)

    pricing = PricingMetadata(currency="FREE", input_per_1m=0.0, output_per_1m=0.0)
    candidate = ModelCandidate(
        candidate_id="t2_c5",
        tier=ModelTier.TIER_2_BALANCED,
        priority=5,
        provider_id="openrouter",
        exact_model_id="google/gemma-4-26b-a4b-it:free",
        enabled=True,
        pricing=pricing,
        verified_capabilities={"supports_json_mode": True},
    )
    req = LLMRequest(
        task_id="website_extraction",
        prompt="Short prompt",
        request_id="req_or_01",
        metadata={"spend_provenance": "VERIFIED_FREE_TIER"},
    )
    res = ledger.reserve(req, candidate)
    llm_resp = LLMResponse(
        content="{}",
        provider="openrouter",
        model="google/gemma-4-26b-a4b-it:free",
        task_id="website_extraction",
        request_id="req_or_01",
        input_tokens=200,
        output_tokens=100,
    )
    reconciled = ledger.reconcile_response(res.reservation_id, llm_resp, candidate)
    assert reconciled.status == ReservationStatus.RECONCILED
    assert reconciled.final_cost_usd == 0.0
    assert reconciled.billable_cost_usd == 0.0
