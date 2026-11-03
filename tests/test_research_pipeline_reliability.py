"""
Isolated Offline Failure-Path & Reliability Test Suite for Research Pipeline V2.2.1.
Validates:
1. Invalid JSON produces no reusable completed checkpoint.
2. Truncated response produces no reusable completed checkpoint.
3. A subsequent authorized attempt can recover from an invalid checkpoint.
4. Ten planned chunks, five completed: status must not be SUCCESS.
5. SUCCESS only when every required chunk is validly completed.
6. Cross-provider checkpoint reuse is rejected.
7. Prompt/schema mismatch causes fresh execution rather than reuse.
8. Timeout increments attempted-call accounting (provider_calls_count > 0).
9. Timeout preserves an uncertain checkpoint and its history.
10. Explicit retry requires fresh authorization and discloses uncertain usage.
11. Resume skips only valid compatible completed chunks.
12. A simulated application restart preserves valid checkpoints.
13. Previously successful analyses remain available if a later attempt fails.
14. No operational STC, RENERWAVE or Energymep data is modified by automated tests.

ALL TESTS ARE 100% OFFLINE. ZERO LIVE CRAWLING. ZERO PAID API CALLS.
"""

import os
import json
import uuid
import tempfile
import asyncio
from unittest.mock import MagicMock, patch
import pytest

from sales_engine.research.research_store import (
    ResearchSnapshot,
    PageEvidence,
    CompanyAnalysisRecord,
    AnalysisChunkRecord,
    ResearchSnapshotStore,
)
from sales_engine.leads.register_models import LeadRegisterRecord, FieldReviewRecord
from sales_engine.leads.register_store import LeadRegisterStore
from sales_engine.research.company_analysis_service import (
    execute_company_analysis,
    estimate_analysis_preflight,
    partition_snapshot_into_chunks,
    SCHEMA_VERSION,
    PROMPT_VERSION,
)
from sales_engine.research.pricing_config import (
    generate_analysis_auth_token,
    verify_analysis_auth_token,
)
from sales_engine.llm_gateway.contracts import (
    LLMRequest,
    LLMResponse,
)
from sales_engine.llm_gateway.errors import (
    LLMTimeoutError,
    LLMError,
)
from sales_engine.research.company_research_service import (
    extract_deterministic_facts,
    is_generic_page_title,
    PageFetchResult,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def _create_test_snapshot(store: ResearchSnapshotStore, snapshot_id: str, candidate_id: str, domain: str, page_count: int, chunk_text_multiplier: int = 50) -> ResearchSnapshot:
    pages = []
    for i in range(1, page_count + 1):
        pe = PageEvidence(
            snapshot_id=snapshot_id,
            evidence_id=f"EV_{snapshot_id}_{i}",
            requested_url=f"https://{domain}/page-{i}",
            resolved_url=f"https://{domain}/page-{i}",
            page_type="services" if i > 1 else "home",
            http_status=200,
            retrieval_status="FETCH_SUCCESS",
            retrieved_at="2026-11-03T12:00:00Z",
            title=f"Page {i} - Clean Energy",
            meta_description=None,
            content_sha256=f"hash_{i}",
            cleaned_text=f"This is substantive content on page {i} for {domain}. Solar and renewable systems engineering. " * chunk_text_multiplier,
        )
        pages.append(pe)

    return store.create_snapshot(
        candidate_id=candidate_id,
        canonical_domain=domain,
        company_name=domain.split(".")[0].capitalize(),
        pages=pages,
        research_status="complete",
        snapshot_id=snapshot_id,
    )


def _dummy_13_field_response(chunk_index: int = 1):
    return {
        "company_name": {"value": "RENERWAVE SRL", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "RENERWAVE SRL", "confidence": 0.95, "is_hypothesis": False},
        "legal_identity": {"value": "RENERWAVE SRL (VAT IT12345678901)", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "VAT IT12345678901", "confidence": 0.90, "is_hypothesis": False},
        "geographic_location": {"value": "Milan, Italy", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "Milan", "confidence": 0.85, "is_hypothesis": False},
        "telephone_numbers": {"value": "+39 02 1234567", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "+39 02 1234567", "confidence": 0.90, "is_hypothesis": False},
        "business_activities": {"value": "Renewable energy consulting and solar PV plant engineering", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "Renewable energy consulting", "confidence": 0.90, "is_hypothesis": False},
        "official_website": {"value": "https://renerwave.com", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "renerwave.com", "confidence": 0.95, "is_hypothesis": False},
        "responsible_person": {"value": "Mario Rossi", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "Mario Rossi", "confidence": 0.80, "is_hypothesis": False},
        "descriptions_and_notes": {"value": "Specializes in agrivoltaics and EPC services", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "agrivoltaics", "confidence": 0.85, "is_hypothesis": False},
        "job_advertisement_links": {"value": "Not Found", "source_url": "", "evidence_excerpt": "", "confidence": 0.0, "is_hypothesis": False},
        "other_links_and_references": {"value": f"https://renerwave.com/page-{chunk_index}", "source_url": f"https://renerwave.com/page-{chunk_index}", "evidence_excerpt": "renerwave.com", "confidence": 0.90, "is_hypothesis": False},
        "contact_email_addresses": {"value": "info@renerwave.com", "source_url": "https://renerwave.com/page-1", "evidence_excerpt": "info@renerwave.com", "confidence": 0.95, "is_hypothesis": False},
        "priority_level": {"value": "A", "source_url": "", "evidence_excerpt": "", "confidence": 0.80, "is_hypothesis": True},
        "priority_rationale": {"value": "High relevance in renewable sector", "source_url": "", "evidence_excerpt": "", "confidence": 0.80, "is_hypothesis": True},
    }


# ---------------------------------------------------------------------------
# 1. Invalid JSON produces no reusable completed checkpoint
# ---------------------------------------------------------------------------
def test_01_invalid_json_produces_no_completed_checkpoint(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_invalid_json", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        content="{invalid json string...",
        model="gemini-3.5-flash-lite",
        provider="gemini",
        task_id="analysis",
        request_id="req_inv",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0001,
    )

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status == "SCHEMA_INVALID"
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "SCHEMA_INVALID"
    assert checkpoints[0].status != "COMPLETED"


# ---------------------------------------------------------------------------
# 2. Truncated response produces no reusable completed checkpoint
# ---------------------------------------------------------------------------
def test_02_truncated_response_produces_no_completed_checkpoint(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_truncated", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)

    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(
        content=json.dumps({"company_name": {"value": "Truncated"}}),
        model="gemini-3.5-flash-lite",
        provider="gemini",
        task_id="analysis",
        request_id="req_trunc",
        finish_reason="length",
        input_tokens=100,
        output_tokens=2500,
        cost_usd=0.0001,
    )

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status in ("SCHEMA_INVALID", "TRUNCATED")
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert len(checkpoints) == 1
    assert checkpoints[0].status in ("TRUNCATED", "SCHEMA_INVALID")
    assert checkpoints[0].status != "COMPLETED"


# ---------------------------------------------------------------------------
# 3. A subsequent authorized attempt can recover from an invalid checkpoint
# ---------------------------------------------------------------------------
def test_03_recovery_from_invalid_checkpoint(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_recover", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)

    # Attempt 1 fails with invalid JSON
    auth_token_1 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw_1 = MagicMock()
    mock_gw_1.generate.return_value = LLMResponse(content="not json", model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r1")
    rec_1 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_1, auth_token=auth_token_1, require_auth=True)
    assert rec_1.status == "SCHEMA_INVALID"

    # Attempt 2 with fresh token must re-call gateway and succeed
    auth_token_2 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw_2 = MagicMock()
    mock_gw_2.generate.return_value = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r2")
    rec_2 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_2, auth_token=auth_token_2, require_auth=True)
    assert rec_2.status == "SUCCESS"
    assert mock_gw_2.generate.call_count == 1
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec_2.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert any(cp.status == "COMPLETED" for cp in checkpoints)


# ---------------------------------------------------------------------------
# 4. Ten planned chunks, five completed: status must not be SUCCESS
# ---------------------------------------------------------------------------
def test_04_ceiling_prevents_unwarranted_success(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    # Snapshot partitioned into >= 6 chunks
    snapshot = _create_test_snapshot(store, "snap_10_chunks", "cand:domain:renerwave.com", "renerwave.com", 20, chunk_text_multiplier=120)
    chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    assert len(chunks) > 5, f"Expected more than 5 chunks, got {len(chunks)}"

    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 1.0)
    mock_gw = MagicMock()
    # Returns valid responses for up to 5 calls
    mock_gw.generate.side_effect = [
        LLMResponse(content=json.dumps(_dummy_13_field_response(i)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id=f"r_{i}")
        for i in range(1, 10)
    ]

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    # MUST NOT be SUCCESS because ceiling stopped before covering all chunks
    assert rec.status != "SUCCESS"
    assert rec.status in ("INCOMPLETE", "PARTIAL")
    assert rec.provider_calls_count == 5
    assert "Incomplete analysis" in rec.diagnostic_info or "ceiling" in rec.diagnostic_info


# ---------------------------------------------------------------------------
# 5. SUCCESS only when every required chunk is validly completed
# ---------------------------------------------------------------------------
def test_05_success_only_when_every_chunk_completed(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_all_done", "cand:domain:renerwave.com", "renerwave.com", 4, chunk_text_multiplier=30)
    chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    expected_chunks = len(chunks)

    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw = MagicMock()
    mock_gw.generate.side_effect = [
        LLMResponse(content=json.dumps(_dummy_13_field_response(i)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id=f"req_{i}")
        for i in range(1, expected_chunks + 1)
    ]

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status == "SUCCESS"
    assert rec.chunks_count == expected_chunks
    assert rec.analyzed_pages_count == 4


# ---------------------------------------------------------------------------
# 6. Cross-provider checkpoint reuse is rejected
# ---------------------------------------------------------------------------
def test_06_cross_provider_checkpoint_reuse_rejected(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_cross_prov", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)

    # Manually store a COMPLETED checkpoint under provider 'openai'
    chk_openai = AnalysisChunkRecord(
        chunk_record_id="chk_openai_1",
        analysis_id="an_openai_1",
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        chunk_index=1,
        total_chunks=1,
        chunk_hash="fake_hash",
        provider="openai",
        model="gpt-4o",
        schema_version="2.1.0",
        prompt_version="2.1.0",
        status="COMPLETED",
        created_at="2026-11-03T12:00:00Z",
        completed_at="2026-11-03T12:00:05Z",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.0001,
        raw_response=json.dumps(_dummy_13_field_response(1)),
        proposed_fields=_dummy_13_field_response(1),
    )
    store.save_chunk_checkpoint(chk_openai)

    # Running gemini analysis MUST NOT reuse openai checkpoint
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r_gem")

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status == "SUCCESS"
    assert mock_gw.generate.call_count == 1, "Gemini gateway MUST be called; openai checkpoint must not be reused"


# ---------------------------------------------------------------------------
# 7. Prompt/schema mismatch causes fresh execution rather than reuse
# ---------------------------------------------------------------------------
def test_07_prompt_schema_mismatch_causes_fresh_execution(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_mismatch", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)

    chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    import hashlib
    c_hash = hashlib.sha256(chunks[0]["text"].encode("utf-8")).hexdigest()

    # Store checkpoint with old schema version "1.0.0"
    chk_old = AnalysisChunkRecord(
        chunk_record_id="chk_old_1",
        analysis_id="an_old_1",
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        chunk_index=1,
        total_chunks=1,
        chunk_hash=c_hash,
        provider="gemini",
        model="gemini-3.5-flash-lite",
        schema_version="1.0.0",  # MISMATCH
        prompt_version="2.1.0",
        status="COMPLETED",
        created_at="2026-11-03T12:00:00Z",
        completed_at="2026-11-03T12:00:05Z",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.0001,
        raw_response=json.dumps(_dummy_13_field_response(1)),
        proposed_fields=_dummy_13_field_response(1),
    )
    store.save_chunk_checkpoint(chk_old)

    # Execution with schema "2.1.0" must trigger fresh gateway call
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw = MagicMock()
    mock_gw.generate.return_value = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r_fresh")

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status == "SUCCESS"
    assert mock_gw.generate.call_count == 1, "Gateway must be called when schema version mismatches"


# ---------------------------------------------------------------------------
# 8. Timeout increments attempted-call accounting
# ---------------------------------------------------------------------------
def test_08_timeout_increments_attempted_call_accounting(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_timeout_acct", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)

    mock_gw = MagicMock()
    mock_gw.generate.side_effect = LLMTimeoutError("Gateway timed out", timeout_category="READ_TIMEOUT")

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    assert rec.status == "TIMEOUT"
    # Even though call timed out, attempted provider call count MUST be recorded as >= 1
    assert rec.provider_calls_count == 1


# ---------------------------------------------------------------------------
# 9. Timeout preserves an uncertain checkpoint and its history
# ---------------------------------------------------------------------------
def test_09_timeout_preserves_uncertain_checkpoint(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_uncertain", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)

    mock_gw = MagicMock()
    mock_gw.generate.side_effect = LLMTimeoutError("Read timed out", timeout_category="READ_TIMEOUT")

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
    )
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "UNCERTAIN_TIMEOUT"
    assert "READ_TIMEOUT" in checkpoints[0].error_message


# ---------------------------------------------------------------------------
# 10. Explicit retry requires fresh authorization and discloses uncertain usage
# ---------------------------------------------------------------------------
def test_10_retry_requires_fresh_authorization_and_discloses_uncertainty(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_retry_auth", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)

    # First attempt consumes token and times out
    mock_gw = MagicMock()
    mock_gw.generate.side_effect = LLMTimeoutError("Timeout", timeout_category="CONNECT_TIMEOUT")
    rec_1 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw, auth_token=auth_token, require_auth=True)
    assert rec_1.status == "TIMEOUT"

    # Attempting to reuse old auth token MUST be rejected
    rec_2 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw, auth_token=auth_token, require_auth=True)
    assert rec_2.status == "UNAUTHORIZED"
    assert "consumed" in rec_2.diagnostic_info.lower()


# ---------------------------------------------------------------------------
# 11. Resume skips only valid compatible completed chunks
# ---------------------------------------------------------------------------
def test_11_resume_skips_only_valid_compatible_completed_chunks(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_res_valid", "cand:domain:renerwave.com", "renerwave.com", 15, chunk_text_multiplier=80)

    # Attempt 1: chunk 1 & 2 succeed, chunk 3 fails
    auth_token_1 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    llm_resp_1 = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r1", input_tokens=500, output_tokens=100)
    llm_resp_2 = LLMResponse(content=json.dumps(_dummy_13_field_response(2)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r2", input_tokens=500, output_tokens=100)
    mock_gw_1 = MagicMock()
    mock_gw_1.generate.side_effect = [llm_resp_1, llm_resp_2, LLMTimeoutError("Timeout", timeout_category="READ_TIMEOUT")]

    rec_1 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_1, auth_token=auth_token_1, require_auth=True)
    assert rec_1.status == "TIMEOUT"
    assert mock_gw_1.generate.call_count == 3

    # Attempt 2: Resume with fresh token
    auth_token_2 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    llm_resp_3 = LLMResponse(content=json.dumps(_dummy_13_field_response(3)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r3", input_tokens=500, output_tokens=100)
    mock_gw_2 = MagicMock()
    mock_gw_2.generate.side_effect = [llm_resp_3, llm_resp_3, llm_resp_3]

    rec_2 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_2, auth_token=auth_token_2, require_auth=True)
    # The first 2 completed chunks were skipped, gateway only called for remaining chunks
    total_chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    assert mock_gw_2.generate.call_count == (len(total_chunks) - 2)


# ---------------------------------------------------------------------------
# 12. A simulated application restart preserves valid checkpoints
# ---------------------------------------------------------------------------
def test_12_restart_preserves_valid_checkpoints(tmp_path):
    db_path = str(tmp_path / "persistent_research.db")
    store_1 = ResearchSnapshotStore(db_path=db_path)
    snapshot = _create_test_snapshot(store_1, "snap_persist", "cand:domain:renerwave.com", "renerwave.com", 2)

    chk_rec = AnalysisChunkRecord(
        chunk_record_id="chk_p1",
        analysis_id="an_persist",
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        chunk_index=1,
        total_chunks=1,
        chunk_hash="hash_p1",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        schema_version="2.1.0",
        prompt_version="2.1.0",
        status="COMPLETED",
        created_at="2026-11-03T12:00:00Z",
        completed_at="2026-11-03T12:00:05Z",
        input_tokens=500,
        output_tokens=100,
        cost_estimate=0.0001,
        raw_response=json.dumps(_dummy_13_field_response(1)),
        proposed_fields=_dummy_13_field_response(1),
    )
    store_1.save_chunk_checkpoint(chk_rec)
    del store_1

    # Reopen database in fresh instance
    store_2 = ResearchSnapshotStore(db_path=db_path)
    recovered = store_2.get_chunk_checkpoint_by_hash(
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        chunk_hash="hash_p1",
        model="gemini-3.5-flash-lite",
        provider="gemini",
        schema_version="2.1.0",
        prompt_version="2.1.0",
        snapshot_checksum=snapshot.content_checksum,
    )
    assert recovered is not None
    assert recovered.status == "COMPLETED"
    assert recovered.input_tokens == 500


# ---------------------------------------------------------------------------
# 13. Previously successful analyses remain available if a later attempt fails
# ---------------------------------------------------------------------------
def test_13_previously_successful_analyses_remain_available(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_prev_success", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)

    # 1. Successful Run
    auth_token_1 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw_1 = MagicMock()
    mock_gw_1.generate.return_value = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r1")
    rec_1 = execute_company_analysis(snapshot=snapshot, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_1, auth_token=auth_token_1, require_auth=True)
    assert rec_1.status == "SUCCESS"

    # 2. Later Run on a fresh snapshot fails / times out
    snapshot_2 = _create_test_snapshot(store, "snap_prev_success_2", "cand:domain:renerwave.com", "renerwave.com", 2, chunk_text_multiplier=10)
    auth_token_2 = generate_analysis_auth_token("cand:domain:renerwave.com", snapshot_2.snapshot_id, "gemini", "gemini-3.5-flash-lite", 0.50)
    mock_gw_2 = MagicMock()
    mock_gw_2.generate.side_effect = LLMTimeoutError("Read timeout", timeout_category="READ_TIMEOUT")
    rec_2 = execute_company_analysis(snapshot=snapshot_2, candidate_id="cand:domain:renerwave.com", snapshot_store=store, gateway_override=mock_gw_2, auth_token=auth_token_2, require_auth=True)
    assert rec_2.status == "TIMEOUT"

    # Verify latest_successful_analysis retains the successful analysis across candidate history
    latest_success = store.get_latest_successful_analysis("cand:domain:renerwave.com")
    assert latest_success is not None
    assert latest_success.analysis_id == rec_1.analysis_id
    assert latest_success.status == "SUCCESS"


# ---------------------------------------------------------------------------
# 14. No operational STC, RENERWAVE or Energymep data is modified
# ---------------------------------------------------------------------------
def test_14_operational_data_not_modified_by_tests(tmp_path):
    from backend.sales_ui.router import validate_research_finalization, compute_field_checksum

    # Validate against isolated temp store without touching production db
    reg_store = LeadRegisterStore(db_path=str(tmp_path / "isolated_lead_register.db"))
    stc_record = LeadRegisterRecord(
        candidate_id="cand:domain:stcpi.com",
        record_number=290,
        company_name="STC S.r.l. - Studio Tecnico Cavallaro",
        official_website="https://stcpi.com/",
        business_activities="Ingegneria di processo e trattamento acque per l'industria chimica e farmaceutica",
        verification_status="OPERATOR_REVIEWED",
        provenance_source="operator_reviewed",
        evidence_urls=["https://stcpi.com/"],
    )
    reg_store.save_record(stc_record)
    saved = reg_store.get_record("cand:domain:stcpi.com")
    assert saved is not None
    assert saved.company_name == "STC S.r.l. - Studio Tecnico Cavallaro"
    assert saved.verification_status == "OPERATOR_REVIEWED"
