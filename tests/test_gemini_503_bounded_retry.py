"""
tests/test_gemini_503_bounded_retry.py
Verification tests for GEM-03 Gemini HTTP 503 Bounded Retry & Safety.

Verifies:
1. HTTP 503 followed by success (recovers within retry limit via bounded backoff).
2. Repeated HTTP 503 exhausts retry limit (max 3 attempts) and fails safely with chunk checkpoints preserved.
"""
import json
import pytest
import httpx
from unittest.mock import patch

from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.research.research_store import (
    ResearchSnapshotStore,
    PageEvidence,
)
from sales_engine.research.company_analysis_service import (
    execute_company_analysis,
    build_analysis_gateway,
)
from sales_engine.research.pricing_config import (
    generate_analysis_auth_token,
    DEFAULT_GEMINI_MODEL,
)


_MOCK_SUCCESS_JSON = {
    "company_name": {"value": "Acme BIM Engineering SpA", "source_url": "https://acme-bim.com/page-1", "confidence": 0.95, "is_hypothesis": False, "evidence_excerpt": "Acme BIM Engineering SpA"},
    "priority_level": {"value": "Tier 1", "source_url": "https://acme-bim.com/page-1", "confidence": 0.85, "is_hypothesis": True, "evidence_excerpt": "Acme BIM Engineering SpA leader in Milan."},
    "priority_rationale": {"value": "Engineering leader", "source_url": "https://acme-bim.com/page-1", "confidence": 0.85, "is_hypothesis": True, "evidence_excerpt": "Acme BIM Engineering SpA leader in Milan."},
    "legal_identity": {"value": "IT12345678901", "source_url": "https://acme-bim.com/page-1", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "Acme BIM Engineering SpA"},
    "geographic_location": {"value": "Milan, Italy", "source_url": "https://acme-bim.com/page-1", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "leader in Milan."},
    "telephone_numbers": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
    "contact_email_addresses": {"value": "info@acme-bim.com", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
    "responsible_person": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
    "business_activities": {"value": "BIM Modeling, MEP Coordination", "source_url": "https://acme-bim.com/page-1", "confidence": 0.9, "is_hypothesis": False, "evidence_excerpt": "Revit MEP modeling, clash detection."},
    "official_website": {"value": "https://acme-bim.com", "source_url": "https://acme-bim.com/page-1", "confidence": 0.95, "is_hypothesis": False, "evidence_excerpt": "Acme BIM Engineering SpA"},
    "descriptions_and_notes": {"value": "Engineering leader", "source_url": "https://acme-bim.com/page-1", "confidence": 0.8, "is_hypothesis": False, "evidence_excerpt": "Acme BIM Engineering SpA leader in Milan."},
    "job_advertisement_links": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
    "other_links_and_references": {"value": "Not Found", "source_url": "", "confidence": 0.0, "is_hypothesis": False, "evidence_excerpt": ""},
}


def test_gemini_503_retry_recovers_on_transient_overload(tmp_path):
    """
    Test 1: HTTP 503 followed by success.
    - First call returns HTTP 503 UNAVAILABLE.
    - Bounded retry backoff triggers and re-attempts.
    - Second call returns HTTP 200 with valid extraction JSON.
    - Final status is SUCCESS, provider called exactly twice (1 retry), checkpoints saved.
    """
    db_file = tmp_path / "test_503_recover.db"
    store = ResearchSnapshotStore(str(db_file))

    pe = PageEvidence(
        snapshot_id="snap_recover_test",
        evidence_id="EV_01",
        requested_url="https://acme-bim.com/about",
        resolved_url="https://acme-bim.com/about",
        page_type="about",
        http_status=200,
        title="About Acme BIM",
        meta_description="Engineering firm",
        retrieval_status="FETCH_SUCCESS",
        cleaned_text="Acme BIM Engineering SpA leader in Milan. Substantive technical text. Revit MEP modeling. " * 30,
        content_sha256="hash_01",
        retrieved_at="2026-11-05T10:00:00Z",
    )
    snapshot = store.create_snapshot(
        candidate_id="cand:acme:recover",
        canonical_domain="acme-bim.com",
        company_name="Acme BIM Engineering SpA",
        pages=[pe],
        research_status="complete",
        snapshot_id="snap_recover_test",
    )

    call_count = 0

    def mock_handler(req: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                503,
                json={"error": {"code": 503, "message": "The model is overloaded. Please try again later.", "status": "UNAVAILABLE"}},
                headers={"Retry-After": "0.1"},
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_success",
                "choices": [{"message": {"content": json.dumps(_MOCK_SUCCESS_JSON)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 450},
            },
        )

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.Client(transport=mock_transport)
    gw, effective_model = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="mock_gemini_key",
        client=mock_client,
    )

    auth_token = generate_analysis_auth_token(
        candidate_id="cand:acme:recover",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
        max_budget_usd=0.20,
    )

    with patch("time.sleep", return_value=None):
        result = execute_company_analysis(
            snapshot=snapshot,
            candidate_id="cand:acme:recover",
            provider_id="gemini",
            model=DEFAULT_GEMINI_MODEL,
            api_key="mock_gemini_key",
            snapshot_store=store,
            gateway_override=gw,
            auth_token=auth_token,
            require_auth=True,
        )

    assert result.status == "SUCCESS"
    assert call_count == 2
    assert result.chunks_count == 1
    assert result.analyzed_pages_count == 1

    checkpoints = store.get_chunk_checkpoints(
        candidate_id="cand:acme:recover",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
    )
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "COMPLETED"


def test_gemini_503_exhausts_retry_limit_and_fails_safely(tmp_path):
    """
    Test 2: Repeated HTTP 503 exhausts retry limit and fails safely.
    - All calls return HTTP 503.
    - OpenAIProvider attempts initial request + exactly 2 retries (3 total attempts).
    - Retry limit is exhausted; raises LLMProviderUnavailableError.
    - Analysis fails safely with status FAILED, checkpoint preserved as FAILED, no unhandled crash.
    """
    db_file = tmp_path / "test_503_exhaust.db"
    store = ResearchSnapshotStore(str(db_file))

    pe = PageEvidence(
        snapshot_id="snap_exhaust_test",
        evidence_id="EV_01",
        requested_url="https://acme-bim.com/about",
        resolved_url="https://acme-bim.com/about",
        page_type="about",
        http_status=200,
        title="About Acme BIM",
        meta_description="Engineering firm",
        retrieval_status="FETCH_SUCCESS",
        cleaned_text="Acme BIM Engineering SpA leader in Milan. Substantive technical text. Revit MEP modeling. " * 30,
        content_sha256="hash_01",
        retrieved_at="2026-11-05T10:00:00Z",
    )
    snapshot = store.create_snapshot(
        candidate_id="cand:acme:exhaust",
        canonical_domain="acme-bim.com",
        company_name="Acme BIM Engineering SpA",
        pages=[pe],
        research_status="complete",
        snapshot_id="snap_exhaust_test",
    )

    call_count = 0

    def mock_handler(req: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            503,
            json={"error": {"code": 503, "message": "The model is overloaded. Please try again later.", "status": "UNAVAILABLE"}},
            headers={"Retry-After": "0.1"},
        )

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.Client(transport=mock_transport)
    gw, effective_model = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="mock_gemini_key",
        client=mock_client,
    )

    auth_token = generate_analysis_auth_token(
        candidate_id="cand:acme:exhaust",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
        max_budget_usd=0.20,
    )

    with patch("time.sleep", return_value=None):
        result = execute_company_analysis(
            snapshot=snapshot,
            candidate_id="cand:acme:exhaust",
            provider_id="gemini",
            model=DEFAULT_GEMINI_MODEL,
            api_key="mock_gemini_key",
            snapshot_store=store,
            gateway_override=gw,
            auth_token=auth_token,
            require_auth=True,
        )

    assert result.status == "FAILED"
    assert call_count == 3  # Initial + 2 retries = 3 attempts total
    assert "503" in (result.diagnostic_info or "")

    checkpoints = store.get_chunk_checkpoints(
        candidate_id="cand:acme:exhaust",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
    )
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "FAILED"
    assert "503" in (checkpoints[0].error_message or "")
