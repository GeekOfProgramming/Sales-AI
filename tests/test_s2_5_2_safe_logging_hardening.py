"""
SalesAI S2.5.2 — Safe Logging & Untrusted Provider Metadata Hardening Tests.

Verifies the safe logging hardening invariants (B1–B6) specified in:
docs/architecture/SALESAI-S2.5.2-SAFE-LOGGING-AND-PROVIDER-METADATA-HARDENING.md

Coverage:
1. email in x-request-id / provider_request_id
2. bearer token in response id / provider_request_id
3. sk- secret in provider_request_id / metadata
4. Windows path in provider_request_id / error messages
5. multiline and control-character header data
6. nested metrics containing private_notes / customer PII
7. provider raw_metadata with cookies / session tokens
8. malicious model / metadata labels
9. empty and legitimate opaque IDs
10. identical results from sync/async validation
11. secret-free repr, str, and safe_log_dict for response, validator, repair, and errors
12. normal request correlation and token usage accounting preserved
13. zero network / HTTP IO
"""

import json
import pytest
from typing import Any, Dict, Optional

from sales_engine.llm_gateway.contracts import (
    LLMRequest,
    LLMResponse,
    sanitize_log_str,
)
from sales_engine.llm_gateway.errors import (
    LLMError,
    LLMAuthenticationError,
    LLMInvalidSchemaError,
)
from sales_engine.llm_gateway.output_validation import (
    LLMOutputValidator,
    OutputValidationResult,
)
from sales_engine.llm_gateway.repair_policy import RepairProposal


def _make_req(
    task_id: str = "inbox_classification",
    provider: Optional[str] = "google",
    model: Optional[str] = "gemini-2.5-flash",
    request_id: str = "req-s252-test-001",
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
    provider_request_id: Optional[str] = None,
    raw_metadata: Optional[Dict[str, Any]] = None,
    input_tokens: int = 120,
    output_tokens: int = 45,
    latency_ms: float = 110.0,
    cost_usd: Optional[float] = None,
) -> LLMResponse:
    return LLMResponse(
        request_id=req.request_id,
        task_id=req.task_id,
        provider=req.provider or "google",
        model=req.model or "gemini-2.5-flash",
        content=content,
        finish_reason="stop",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        provider_request_id=provider_request_id,
        raw_metadata=raw_metadata,
    )


# ===========================================================================
# 1. B1: Email in provider correlation identifier (repro defect)
# ===========================================================================

def test_b1_email_in_x_request_id_not_in_response_safe_logs_or_repr():
    """B1 reproduction: email in provider_request_id must not leak into response safe_log_dict or repr."""
    req = _make_req("inbox_classification")
    injected_email = "private_person@example.invalid"
    resp = _make_resp('{"category": "question", "confidence": 0.85}', req, provider_request_id=injected_email)

    # In-memory transport retains exact value
    assert resp.provider_request_id == injected_email

    # safe_log_dict must scrub the email and set provider_request_id=None
    log_dict = resp.safe_log_dict()
    log_json = json.dumps(log_dict)
    assert injected_email not in log_json
    assert log_dict["has_provider_request_id"] is True
    assert log_dict["provider_request_id"] is None

    # repr and str must scrub the email
    assert injected_email not in repr(resp)
    assert injected_email not in str(resp)


def test_b1_email_in_provider_request_id_not_in_validation_result_safe_logs():
    """B1 reproduction: email in provider_request_id must not appear in OutputValidationResult safe_log_dict."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    injected_email = "private_person@example.invalid"
    content = json.dumps({"category": "question", "confidence": 0.9, "reasoning": "Need pricing."})
    resp = _make_resp(content, req, provider_request_id=injected_email)

    res = validator.validate(req, resp)
    assert res.is_valid is True

    # Check safe_log_dict
    log_dict = res.safe_log_dict()
    log_json = json.dumps(log_dict)
    assert injected_email not in log_json

    # Check metrics inside safe_log_dict
    metrics = log_dict["metrics"]
    assert metrics["has_provider_request_id"] is True
    assert metrics["provider_request_id"] is None
    assert injected_email not in str(metrics)

    # Check repr and str of result
    assert injected_email not in repr(res)
    assert injected_email not in str(res)


# ===========================================================================
# 2. B1: Bearer token and secrets in upstream response identifiers
# ===========================================================================

def test_b1_bearer_token_in_response_id_not_in_safe_logs():
    """B1: Bearer token in provider_request_id must not leak into logs or repr."""
    req = _make_req("inbox_classification")
    secret_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ae9CIF9Q5CdKiPySLMeakTCqQQ2mUidIjxtrcNmTC"
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, provider_request_id=secret_bearer)

    # In-memory transport retains exact value
    assert resp.provider_request_id == secret_bearer

    # Verify zero leakage in response log and repr
    assert secret_bearer not in json.dumps(resp.safe_log_dict())
    assert secret_bearer not in repr(resp)
    assert secret_bearer not in str(resp)

    # Verify zero leakage in validation result
    validator = LLMOutputValidator()
    res = validator.validate(req, resp)
    assert secret_bearer not in json.dumps(res.safe_log_dict())
    assert secret_bearer not in repr(res)
    assert secret_bearer not in str(res)


def test_b1_sk_secret_in_provider_request_id_not_in_safe_logs():
    """B1: OpenAI sk- secret in provider_request_id must not leak into logs or repr."""
    req = _make_req("inbox_classification")
    secret_key = "sk-proj-abc1234567890def1234567890ghij"
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, provider_request_id=secret_key)

    assert secret_key not in json.dumps(resp.safe_log_dict())
    assert secret_key not in repr(resp)

    validator = LLMOutputValidator()
    res = validator.validate(req, resp)
    assert secret_key not in json.dumps(res.safe_log_dict())
    assert secret_key not in repr(res)


# ===========================================================================
# 3. B1: Windows filesystem paths and control characters
# ===========================================================================

def test_b1_windows_path_in_provider_request_id_not_in_safe_logs():
    """B1: Local Windows path in provider_request_id must not leak into logs or repr."""
    req = _make_req("inbox_classification")
    win_path = "C:\\Users\\hlotf\\AppData\\Local\\SalesAI\\keys\\api.key"
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, provider_request_id=win_path)

    assert win_path not in json.dumps(resp.safe_log_dict())
    assert win_path not in repr(resp)

    validator = LLMOutputValidator()
    res = validator.validate(req, resp)
    assert win_path not in json.dumps(res.safe_log_dict())
    assert win_path not in repr(res)


def test_b1_multiline_and_control_chars_in_header_id_handled_safely():
    """B1: Multiline and control characters in upstream header must not corrupt safe logs."""
    req = _make_req("inbox_classification")
    malicious_header = "req-clean-id\r\nX-Injected-Header: malicious\x00\x1b[31m"
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, provider_request_id=malicious_header)

    log_dict = resp.safe_log_dict()
    assert log_dict["provider_request_id"] is None
    assert log_dict["has_provider_request_id"] is True
    assert malicious_header not in json.dumps(log_dict)
    assert malicious_header not in repr(resp)


# ===========================================================================
# 4. B2: Closed metrics allowlist (never copy arbitrary keys)
# ===========================================================================

def test_b2_nested_metrics_containing_private_notes_excluded_from_safe_log():
    """B2: Arbitrary/nested keys like private_notes must never enter OutputValidationResult.safe_log_dict()."""
    res = OutputValidationResult(
        is_valid=True,
        task_id="inbox_classification",
        schema_version="1.0.0",
        metrics={
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 125.0,
            "cost_usd": None,
            "private_notes": "Sensitive customer deal size $100k",
            "nested_payload": {"admin_token": "secret_token_123"},
            "user_email": "target_lead@client.invalid",
        },
    )

    log_dict = res.safe_log_dict()
    metrics = log_dict["metrics"]

    # Allowed scalar metrics are present
    assert metrics["input_tokens"] == 100
    assert metrics["output_tokens"] == 50
    assert metrics["latency_ms"] == 125.0
    assert metrics["cost_usd"] is None
    assert metrics["has_provider_request_id"] is False
    assert metrics["provider_request_id"] is None

    # Disallowed keys are strictly excluded
    assert "private_notes" not in metrics
    assert "nested_payload" not in metrics
    assert "user_email" not in metrics

    # Assert zero trace in serialized json
    serialized = json.dumps(log_dict)
    assert "Sensitive customer deal" not in serialized
    assert "secret_token_123" not in serialized
    assert "target_lead@client.invalid" not in serialized


def test_b2_unmeasured_cost_remains_none_never_fabricated_as_zero():
    """B2: When cost is unmeasured (None), it stays None in safe_log_dict; never converted to 0.0."""
    res = OutputValidationResult(
        is_valid=True,
        task_id="inbox_classification",
        metrics={"cost_usd": None, "input_tokens": 100},
    )
    log_dict = res.safe_log_dict()
    assert log_dict["metrics"]["cost_usd"] is None
    assert log_dict["metrics"]["cost_usd"] != 0.0


def test_b2_measured_cost_preserved_accurately():
    """B2: Measured float cost is preserved accurately in safe_log_dict."""
    res = OutputValidationResult(
        is_valid=True,
        task_id="inbox_classification",
        metrics={"cost_usd": 0.0025, "latency_ms": 145.5},
    )
    log_dict = res.safe_log_dict()
    assert log_dict["metrics"]["cost_usd"] == 0.0025
    assert log_dict["metrics"]["latency_ms"] == 145.5


# ===========================================================================
# 5. B3: Provider raw_metadata with cookies and sensitive tokens
# ===========================================================================

def test_b3_provider_raw_metadata_with_cookies_never_in_safe_logs():
    """B3: Cookies and session tokens in raw_metadata must never appear in safe_log_dict, str, or repr."""
    req = _make_req("inbox_classification")
    cookies = {"Set-Cookie": "session_id=secret_session_token_xyz; Secure", "Cookie": "auth=bearer_abc"}
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, raw_metadata=cookies)

    # In-memory transport preserves raw_metadata
    assert resp.raw_metadata == cookies

    # Response safe_log_dict, str, repr must not contain cookies
    assert "secret_session_token_xyz" not in json.dumps(resp.safe_log_dict())
    assert "secret_session_token_xyz" not in str(resp)
    assert "secret_session_token_xyz" not in repr(resp)

    # Validation result must not contain cookies
    validator = LLMOutputValidator()
    res = validator.validate(req, resp)
    assert "secret_session_token_xyz" not in json.dumps(res.safe_log_dict())
    assert "secret_session_token_xyz" not in str(res)
    assert "secret_session_token_xyz" not in repr(res)


# ===========================================================================
# 6. B3: Malicious model and metadata labels sanitized
# ===========================================================================

def test_b3_malicious_model_and_task_labels_sanitized_in_safe_logs():
    """B3: Malicious model/task labels containing secrets or emails are scrubbed in safe_log_dict."""
    req = LLMRequest(
        task_id="inbox_classification",
        provider="google",
        model="gemini-2.5-flash sk-proj-injected-secret-1234567890",
        prompt="Analyze text.",
        request_id="req_alice@attacker.invalid",
    )

    # In-memory retains exact values for routing and matching
    assert "sk-proj" in req.model
    assert "alice@attacker.invalid" in req.request_id

    # Safe log dict must scrub secrets and email
    log_dict = req.safe_log_dict()
    log_json = json.dumps(log_dict)
    assert "sk-proj-injected-secret-1234567890" not in log_json
    assert "alice@attacker.invalid" not in log_json

    # repr and str must scrub secrets and email
    assert "sk-proj-injected-secret-1234567890" not in repr(req)
    assert "alice@attacker.invalid" not in repr(req)
    assert "alice@attacker.invalid" not in str(req)


def test_b3_repair_proposal_safe_log_dict_and_repr_scrubbed():
    """B3: RepairProposal must scrub sensitive tokens in safe_log_dict and repr."""
    repair = RepairProposal(
        request_id="req_admin@corp.invalid",
        task_id="inbox_classification",
        provider="google",
        model="gemini sk-proj-1234567890abcdef",
        attempt_count=1,
        repairable=False,
        reason_codes=["SYNTAX_INVALID_JSON"],
        dispatch_allowed=False,
    )

    log_dict = repair.safe_log_dict()
    log_json = json.dumps(log_dict)
    assert "admin@corp.invalid" not in log_json
    assert "sk-proj-1234567890abcdef" not in log_json

    assert "admin@corp.invalid" not in repr(repair)
    assert "sk-proj-1234567890abcdef" not in repr(repair)


def test_b3_gateway_errors_scrub_sensitive_strings():
    """B3: LLMError string representations must scrub sensitive keys and paths."""
    err = LLMError(
        message="Failed connecting with token sk-proj-secret-key-1234567890 at C:\\keys\\private.pem",
        error_code="CONNECTION_ERROR",
        provider="openai@corp.invalid",
        task_id="inbox_classification",
    )

    err_str = str(err)
    err_repr = repr(err)

    assert "sk-proj-secret-key-1234567890" not in err_str
    assert "sk-proj-secret-key-1234567890" not in err_repr
    assert "C:\\keys\\private.pem" not in err_str
    assert "openai@corp.invalid" not in err_str
    assert "CONNECTION_ERROR" in err_str


# ===========================================================================
# 7. B4: Empty and legitimate opaque provider IDs
# ===========================================================================

def test_b4_empty_and_none_provider_request_ids():
    """B4: Empty string or None provider_request_id sets has_provider_request_id=False."""
    req = _make_req("inbox_classification")
    resp_none = _make_resp('{"category": "question", "confidence": 0.9}', req, provider_request_id=None)
    assert resp_none.safe_log_dict()["has_provider_request_id"] is False
    assert resp_none.safe_log_dict()["provider_request_id"] is None

    resp_empty = _make_resp('{"category": "question", "confidence": 0.9}', req, provider_request_id="   ")
    assert resp_empty.safe_log_dict()["has_provider_request_id"] is False
    assert resp_empty.safe_log_dict()["provider_request_id"] is None


def test_b4_legitimate_opaque_provider_id_preserved_in_memory_only():
    """B4: Legitimate opaque provider ID is preserved in memory but hidden in safe_log_dict."""
    req = _make_req("inbox_classification")
    opaque_id = "chatcmpl-9u1XyZ4abc789_req001"
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(content, req, provider_request_id=opaque_id)

    # In-memory transport retains exact value
    assert resp.provider_request_id == opaque_id

    # Safe log dict sets has_provider_request_id=True and provider_request_id=None
    log_dict = resp.safe_log_dict()
    assert log_dict["has_provider_request_id"] is True
    assert log_dict["provider_request_id"] is None

    # Result safe log dict also hides raw ID
    validator = LLMOutputValidator()
    res = validator.validate(req, resp)
    res_log = res.safe_log_dict()
    assert res_log["metrics"]["has_provider_request_id"] is True
    assert res_log["metrics"]["provider_request_id"] is None


# ===========================================================================
# 8. B4 & B5: Request correlation and token accounting preserved
# ===========================================================================

def test_b4_normal_request_correlation_and_token_accounting_preserved():
    """B4: Request correlation and token usage accounting remain accurate across safe logging."""
    req = _make_req("inbox_classification", request_id="req-audit-correlation-999")
    content = json.dumps({"category": "question", "confidence": 0.9})
    resp = _make_resp(
        content,
        req,
        input_tokens=250,
        output_tokens=80,
        latency_ms=185.5,
        cost_usd=0.0035,
    )

    validator = LLMOutputValidator()
    res = validator.validate(req, resp)

    assert res.is_valid is True
    log_dict = res.safe_log_dict()

    assert log_dict["metrics"]["input_tokens"] == 250
    assert log_dict["metrics"]["output_tokens"] == 80
    assert log_dict["metrics"]["latency_ms"] == 185.5
    assert log_dict["metrics"]["cost_usd"] == 0.0035


# ===========================================================================
# 9. B5: Sync and async validation parity
# ===========================================================================

@pytest.mark.asyncio
async def test_b5_sync_and_async_validation_safe_logging_parity():
    """B5: Sync and async validation produce identical safe_log_dict, str, and repr."""
    validator = LLMOutputValidator()
    req = _make_req("inbox_classification")
    injected_email = "test_lead@partner.invalid"
    content = json.dumps({"category": "question", "confidence": 0.88, "reasoning": "Inquiry."})
    resp = _make_resp(content, req, provider_request_id=injected_email)

    res_sync = validator.validate(req, resp)
    res_async = await validator.validate_async(req, resp)

    assert res_sync.safe_log_dict() == res_async.safe_log_dict()
    assert repr(res_sync) == repr(res_async)
    assert str(res_sync) == str(res_async)
    assert injected_email not in json.dumps(res_async.safe_log_dict())


# ===========================================================================
# 10. Helper sanitize_log_str unit coverage
# ===========================================================================

def test_sanitize_log_str_edge_cases():
    """Verify sanitize_log_str handles edge cases and bounding properly."""
    assert sanitize_log_str(None) is None
    assert sanitize_log_str("") == ""
    assert sanitize_log_str("valid-id_123:task") == "valid-id_123:task"
    assert "REDACTED_EMAIL" in sanitize_log_str("user@example.com")
    assert "REDACTED_SECRET" in sanitize_log_str("sk-1234567890abcdef")
    assert "REDACTED_PATH" in sanitize_log_str("C:\\Windows\\secret.txt")
    assert len(sanitize_log_str("a" * 200, max_len=50)) == 50
