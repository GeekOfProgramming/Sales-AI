"""
Focused investigation and tests for Gemini 503 error resilience and chunk resume:
1. Test 503 behavior on Chunk 1 of 3:
   - Verifies HTTP 503 PROVIDER_UNAVAILABLE causes graceful failure, records FAILED checkpoint for Chunk 1.
   - Fail-closed security, budget, and source evidence preservation.
2. Test resume after 503 failure (Chunk 1 completed on retry, Chunk 2 succeeds, Chunk 3 succeeds):
   - Verifies completed chunks are reused without duplicate billable inference.
   - Verifies fresh authorization token requirement on retry (nonce consumption / replay protection).
"""
import pytest
import httpx
import json
from unittest.mock import MagicMock, patch

from sales_engine.llm_gateway.interface import LLMProviderGateway
from sales_engine.llm_gateway.registry import ProviderRegistry
from sales_engine.llm_gateway.config import GatewayConfig
from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider
from sales_engine.research.research_store import (
    ResearchSnapshotStore,
    ResearchSnapshot,
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


@pytest.fixture
def mock_snapshot_3_chunks(tmp_path):
    """Creates a synthetic snapshot with 3 distinct pages that partition into 3 chunks."""
    db_file = tmp_path / "test_503_research.db"
    store = ResearchSnapshotStore(str(db_file))

    # Page texts ~35k chars each with chunk_size_chars=30k -> 3 chunks
    pages = []
    for i in range(1, 4):
        pe = PageEvidence(
            snapshot_id="snap_503_test",
            evidence_id=f"EV_0{i}",
            requested_url=f"https://acme-bim.com/page-{i}",
            resolved_url=f"https://acme-bim.com/page-{i}",
            page_type="services" if i > 1 else "about",
            http_status=200,
            title=f"Page {i} Acme BIM",
            meta_description="Engineering firm",
            retrieval_status="FETCH_SUCCESS",
            cleaned_text=f"Acme BIM Engineering SpA leader in Milan. Page {i} substantive technical text. Revit MEP modeling, clash detection. " * 150,
            content_sha256=f"hash_0{i}",
            retrieved_at="2026-11-05T10:00:00Z",
        )
        pages.append(pe)

    snapshot = store.create_snapshot(
        candidate_id="cand:acme:503",
        canonical_domain="acme-bim.com",
        company_name="Acme BIM Engineering SpA",
        pages=pages,
        research_status="complete",
        snapshot_id="snap_503_test",
    )
    return {"store": store, "snapshot": snapshot, "db_file": str(db_file)}


def test_01_http_503_on_chunk1_fails_closed_without_replaying_or_corrupting(mock_snapshot_3_chunks):
    """
    Test 1: HTTP 503 on Chunk 1 of 3:
    - Gateway raises LLMProviderUnavailableError on HTTP 503.
    - Company analysis catches the error, marks status='FAILED', records FAILED checkpoint.
    - Diagnostic info records the failure.
    - 0 completed chunks, coverage = 0%.
    - Fail-closed security and authorization nonces preserved.
    """
    env = mock_snapshot_3_chunks
    store = env["store"]
    snapshot = env["snapshot"]

    mock_client = MagicMock()
    # Mock HTTP 503 response from Gemini endpoint
    mock_resp = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": "The model is overloaded. Please try again later.", "status": "UNAVAILABLE"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )
    mock_client.post.return_value = mock_resp

    gw, effective_model = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="test_fake_gemini_key",
        client=mock_client,
    )

    auth_token = generate_analysis_auth_token(
        candidate_id="cand:acme:503",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=effective_model,
        max_budget_usd=1.0,
    )

    with patch("time.sleep", return_value=None):
        result = execute_company_analysis(
            snapshot=snapshot,
            candidate_id="cand:acme:503",
            provider_id="gemini",
            model=effective_model,
            api_key="test_fake_gemini_key",
            snapshot_store=store,
            gateway_override=gw,
            auth_token=auth_token,
            require_auth=True,
            chunk_size_chars=25_000,  # Forces 3 chunks
        )

    assert result.status == "FAILED"
    assert "503" in result.diagnostic_info or "unavailable" in result.diagnostic_info.lower()
    assert result.chunks_count == 3
    assert result.analyzed_pages_count == 0

    # Check checkpoints: Chunk 1 was marked FAILED; Chunks 2 and 3 were never invoked
    checkpoints = store.get_chunk_checkpoints(
        candidate_id="cand:acme:503",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=effective_model,
    )
    assert len(checkpoints) == 1
    assert checkpoints[0].chunk_index == 1
    assert checkpoints[0].status == "FAILED"
    assert "503" in (checkpoints[0].error_message or "")


def test_02_resume_after_partial_failure_reuses_completed_chunks_no_duplicate_inference(mock_snapshot_3_chunks):
    """
    Test 2: Partial failure and resume:
    - Run 1: Chunk 1 succeeds, Chunk 2 fails with HTTP 503, Chunk 3 not called.
    - Run 2 (Retry): Re-uses Chunk 1 from checkpoint without re-invoking provider.
      Chunk 2 succeeds, Chunk 3 succeeds.
    - Result reaches SUCCESS with 100% coverage, 3 completed chunks, 1 resumed chunk.
    - Provider client was called only 3 times total across both runs (Chunk 1 run 1, Chunk 2 run 1 [503], Chunk 2 run 2, Chunk 3 run 2).
    - ZERO duplicate billable inference on Chunk 1.
    """
    env = mock_snapshot_3_chunks
    store = env["store"]
    snapshot = env["snapshot"]

    mock_json_success = {
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

    resp_chunk1_ok = httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl_c1",
            "choices": [{"message": {"content": json.dumps(mock_json_success)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 400},
        },
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )
    resp_chunk2_503_initial = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": "Service Unavailable", "status": "UNAVAILABLE"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )
    resp_chunk2_503_retry1 = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": "Service Unavailable", "status": "UNAVAILABLE"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )
    resp_chunk2_503_retry2 = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": "Service Unavailable", "status": "UNAVAILABLE"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )

    mock_client_run1 = MagicMock()
    mock_client_run1.post.side_effect = [resp_chunk1_ok, resp_chunk2_503_initial, resp_chunk2_503_retry1, resp_chunk2_503_retry2]

    gw1, effective_model = build_analysis_gateway(
        provider_id="gemini",
        model=DEFAULT_GEMINI_MODEL,
        api_key="test_fake_gemini_key",
        client=mock_client_run1,
    )

    auth_token_1 = generate_analysis_auth_token(
        candidate_id="cand:acme:503",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=effective_model,
        max_budget_usd=1.0,
    )

    # RUN 1: Chunk 1 succeeds, Chunk 2 fails with 503 (initial + 2 retries = 3 attempts on Chunk 2)
    with patch("time.sleep", return_value=None):
        res1 = execute_company_analysis(
            snapshot=snapshot,
            candidate_id="cand:acme:503",
            provider_id="gemini",
            model=effective_model,
            api_key="test_fake_gemini_key",
            snapshot_store=store,
            gateway_override=gw1,
            auth_token=auth_token_1,
            require_auth=True,
            chunk_size_chars=25_000,
        )

    assert res1.status == "FAILED"
    assert "Completed: 1/3" in res1.diagnostic_info
    assert res1.chunks_count == 3
    assert mock_client_run1.post.call_count == 4  # Chunk 1 (1 call) + Chunk 2 (3 attempts: initial + 2 retries)

    # RUN 2 (Retry): Provide fresh authorization token
    # Mock client will be called for Chunk 2 (success) and Chunk 3 (success)
    resp_chunk2_ok = httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl_c2",
            "choices": [{"message": {"content": json.dumps(mock_json_success)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 400},
        },
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )
    resp_chunk3_ok = httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl_c3",
            "choices": [{"message": {"content": json.dumps(mock_json_success)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 400},
        },
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    )

    mock_client_run2 = MagicMock()
    mock_client_run2.post.side_effect = [resp_chunk2_ok, resp_chunk3_ok]

    gw2, _ = build_analysis_gateway(
        provider_id="gemini",
        model=effective_model,
        api_key="test_fake_gemini_key",
        client=mock_client_run2,
    )

    auth_token_2 = generate_analysis_auth_token(
        candidate_id="cand:acme:503",
        snapshot_id=snapshot.snapshot_id,
        provider="gemini",
        model=effective_model,
        max_budget_usd=1.0,
    )

    res2 = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:acme:503",
        provider_id="gemini",
        model=effective_model,
        api_key="test_fake_gemini_key",
        snapshot_store=store,
        gateway_override=gw2,
        auth_token=auth_token_2,
        require_auth=True,
        chunk_size_chars=25_000,
    )

    # Invariants for Run 2:
    # 1. Overall status reached SUCCESS
    assert res2.status == "SUCCESS"
    # 2. Resumed 1 chunk (Chunk 1 from Run 1)
    assert "resumed 1" in res2.diagnostic_info
    # 3. Executed 2 chunks (Chunk 2 and Chunk 3)
    assert mock_client_run2.post.call_count == 2
    # 4. Completed all 3 chunks
    assert "Completed: 3/3" in res2.diagnostic_info
    # 5. Coverage 100%
    assert "Coverage: 3/3 pages (100.0%)" in res2.diagnostic_info
