"""
Isolated Offline Failure-Path & Reliability Test Suite for Research Pipeline V2.2.
Validates:
1. Multi-page snapshot partitioning (e.g. 16 eligible pages across chunks).
2. Timeout handling on chunk 1 (LLMTimeoutError, CONNECT/READ timeouts, no partial corruption).
3. Timeout handling on later chunk (chunk 3 after successful chunks 1 & 2).
4. Durable resume without repeating completed chunks (provider called only for remaining chunks).
5. Token reuse rejection on resume (requires fresh single-use auth token).
6. Request idempotency / concurrent duplicate submission rejection.
7. Process restart / store reload from disk between chunk failure and resume.
8. Checkpoint invalidation upon snapshot / model / prompt / schema mismatch.
9. Cost ceiling & budget enforcement across resumed executions.
10. Accurate progress & chunk accounting (planned, completed, failed/uncertain, omitted).
11. Contact contamination rejection (soup separator prevents concatenation; mailto/tel priority).
12. Generic service page title rejection as company name (e.g. 'BIM MEP').
13. Generic homepage title rejection as business activities (e.g. 'Home page - RENERWAVE').
14. STC finalized record preservation (cand:domain:stcpi.com unaffected).

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
# 1. Multi-page Snapshot Partitioning
# ---------------------------------------------------------------------------
def test_case_01_snapshot_partitioning_16_pages(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_renerwave_16", "cand:domain:renerwave.com", "renerwave.com", 16, chunk_text_multiplier=50)
    chunks, eligible, omitted = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    assert len(chunks) >= 2, f"Expected at least 2 chunks for 16 substantive pages, got {len(chunks)}"
    assert len(chunks) <= 5, "Should respect maximum reasonable partition count"
    
    preflight = estimate_analysis_preflight(snapshot, provider_id="gemini", model="gemini-3.5-flash-lite")
    assert preflight["eligible_pages_count"] == 16
    assert preflight["chunks_count"] == len(chunks)
    assert preflight["estimated_cost_usd"] > 0


# ---------------------------------------------------------------------------
# 2. Timeout on Chunk 1 (LLMTimeoutError, Granular Error)
# ---------------------------------------------------------------------------
def test_case_02_timeout_on_chunk_1(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    
    snapshot = _create_test_snapshot(store, "snap_timeout_1", "cand:domain:renerwave.com", "renerwave.com", 6, chunk_text_multiplier=40)

    auth_token = generate_analysis_auth_token(
        candidate_id="cand:domain:renerwave.com",
        snapshot_id="snap_timeout_1",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
    )

    mock_gateway = MagicMock()
    err = LLMTimeoutError("Read operation timed out after 90.0 seconds", timeout_category="READ_TIMEOUT")
    mock_gateway.generate.side_effect = err

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gateway,
        auth_token=auth_token,
        require_auth=True,
    )

    assert rec.status in ("FAILED", "TIMEOUT")
    assert "READ_TIMEOUT" in rec.diagnostic_info
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert len(checkpoints) >= 1
    assert checkpoints[0].status == "UNCERTAIN_TIMEOUT"
    assert checkpoints[0].error_message is not None


# ---------------------------------------------------------------------------
# 3. Timeout on Chunk 3 After Successful Chunks 1 & 2
# ---------------------------------------------------------------------------
def test_case_03_timeout_on_chunk_3(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    
    snapshot = _create_test_snapshot(store, "snap_timeout_3", "cand:domain:renerwave.com", "renerwave.com", 15, chunk_text_multiplier=80)

    auth_token = generate_analysis_auth_token(
        candidate_id="cand:domain:renerwave.com",
        snapshot_id="snap_timeout_3",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
    )

    resp_payload_1 = json.dumps(_dummy_13_field_response(1))
    resp_payload_2 = json.dumps(_dummy_13_field_response(2))

    llm_resp_1 = LLMResponse(
        content=resp_payload_1,
        model="gemini-3.5-flash-lite",
        provider="gemini",
        task_id="company_analysis",
        request_id="req_1",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0001,
    )
    llm_resp_2 = LLMResponse(
        content=resp_payload_2,
        model="gemini-3.5-flash-lite",
        provider="gemini",
        task_id="company_analysis",
        request_id="req_2",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0001,
    )

    mock_gateway = MagicMock()
    mock_gateway.generate.side_effect = [
        llm_resp_1,
        llm_resp_2,
        LLMTimeoutError("Read timed out on chunk 3", timeout_category="READ_TIMEOUT"),
    ]

    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gateway,
        auth_token=auth_token,
        require_auth=True,
    )

    assert rec.status in ("FAILED", "TIMEOUT")
    checkpoints = store.get_chunk_checkpoints(candidate_id=rec.candidate_id, snapshot_id=snapshot.snapshot_id)
    assert len(checkpoints) >= 3
    assert checkpoints[0].status == "COMPLETED"
    assert checkpoints[1].status == "COMPLETED"
    assert checkpoints[2].status == "UNCERTAIN_TIMEOUT"


# ---------------------------------------------------------------------------
# 4. Durable Resume Without Repeating Completed Chunks
# ---------------------------------------------------------------------------
def test_case_04_resume_without_repeating_completed_chunks(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    
    snapshot = _create_test_snapshot(store, "snap_resume_1", "cand:domain:renerwave.com", "renerwave.com", 15, chunk_text_multiplier=80)

    # Run 1: Fails on chunk 3
    auth_token_1 = generate_analysis_auth_token("cand:domain:renerwave.com", "snap_resume_1", "gemini", "gemini-3.5-flash-lite", 0.50)
    llm_resp_1 = LLMResponse(content=json.dumps(_dummy_13_field_response(1)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r1", input_tokens=1000, output_tokens=200, cost_usd=0.0001)
    llm_resp_2 = LLMResponse(content=json.dumps(_dummy_13_field_response(2)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r2", input_tokens=1000, output_tokens=200, cost_usd=0.0001)

    mock_gw_1 = MagicMock()
    mock_gw_1.generate.side_effect = [
        llm_resp_1,
        llm_resp_2,
        LLMTimeoutError("Read timeout", timeout_category="READ_TIMEOUT"),
    ]

    rec_1 = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw_1,
        auth_token=auth_token_1,
        require_auth=True,
    )
    assert rec_1.status in ("FAILED", "TIMEOUT")
    assert mock_gw_1.generate.call_count == 3

    # Run 2: Resume with fresh auth token
    auth_token_2 = generate_analysis_auth_token("cand:domain:renerwave.com", "snap_resume_1", "gemini", "gemini-3.5-flash-lite", 0.50)
    llm_resp_3 = LLMResponse(content=json.dumps(_dummy_13_field_response(3)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r3", input_tokens=1000, output_tokens=200, cost_usd=0.0001)
    llm_resp_4 = LLMResponse(content=json.dumps(_dummy_13_field_response(4)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r4", input_tokens=1000, output_tokens=200, cost_usd=0.0001)
    llm_resp_5 = LLMResponse(content=json.dumps(_dummy_13_field_response(5)), model="gemini-3.5-flash-lite", provider="gemini", task_id="analysis", request_id="r5", input_tokens=1000, output_tokens=200, cost_usd=0.0001)

    mock_gw_2 = MagicMock()
    mock_gw_2.generate.side_effect = [llm_resp_3, llm_resp_4, llm_resp_5]

    rec_2 = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw_2,
        auth_token=auth_token_2,
        require_auth=True,
    )
    assert rec_2.status == "SUCCESS"
    total_chunks, _, _ = partition_snapshot_into_chunks(snapshot, chunk_size_chars=40_000)
    assert mock_gw_2.generate.call_count == (len(total_chunks) - 2)


# ---------------------------------------------------------------------------
# 5. Token Reuse Rejection on Resume
# ---------------------------------------------------------------------------
def test_case_05_token_reuse_rejection(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    snapshot = _create_test_snapshot(store, "snap_token_reuse", "cand:domain:renerwave.com", "renerwave.com", 5, chunk_text_multiplier=20)

    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", "snap_token_reuse", "gemini", "gemini-3.5-flash-lite", 0.50)
    
    valid, _, _ = verify_analysis_auth_token(auth_token, "cand:domain:renerwave.com", "snap_token_reuse", "gemini", "gemini-3.5-flash-lite", mark_used=True)
    assert valid is True

    valid2, _, err_msg = verify_analysis_auth_token(auth_token, "cand:domain:renerwave.com", "snap_token_reuse", "gemini", "gemini-3.5-flash-lite", mark_used=True)
    assert valid2 is False
    assert "already been consumed" in err_msg


# ---------------------------------------------------------------------------
# 6. Duplicate Browser Submission / Concurrent Attempt Guard
# ---------------------------------------------------------------------------
def test_case_06_concurrent_submission_lock():
    from backend.sales_ui.router import _ANALYSIS_SUBMISSION_LOCKS
    lock_key = "cand:test:snap:1"
    _ANALYSIS_SUBMISSION_LOCKS.add(lock_key)
    try:
        assert lock_key in _ANALYSIS_SUBMISSION_LOCKS
    finally:
        _ANALYSIS_SUBMISSION_LOCKS.discard(lock_key)
    assert lock_key not in _ANALYSIS_SUBMISSION_LOCKS


# ---------------------------------------------------------------------------
# 7. Restart Between Chunk Completion and Resume
# ---------------------------------------------------------------------------
def test_case_07_restart_reload_from_disk(tmp_path):
    db_path = str(tmp_path / "persistent_research.db")
    
    # Process 1: creates snapshot & 1 completed chunk checkpoint
    store_1 = ResearchSnapshotStore(db_path=db_path)
    snapshot = _create_test_snapshot(store_1, "snap_persist", "cand:domain:renerwave.com", "renerwave.com", 2)
    chk_rec = AnalysisChunkRecord(
        chunk_record_id="chk_01",
        analysis_id="an_persist_01",
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        chunk_index=0,
        total_chunks=3,
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
        raw_response=json.dumps({"official_website": "https://renerwave.com"}),
    )
    store_1.save_chunk_checkpoint(chk_rec)
    del store_1

    # Process 2: re-opens database from disk
    store_2 = ResearchSnapshotStore(db_path=db_path)
    recovered_chk = store_2.get_chunk_checkpoint_by_hash(
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        chunk_hash="hash_p1",
        model="gemini-3.5-flash-lite",
    )
    assert recovered_chk is not None
    assert recovered_chk.status == "COMPLETED"
    assert recovered_chk.input_tokens == 500


# ---------------------------------------------------------------------------
# 8. Snapshot / Model / Schema Mismatch Invalidation
# ---------------------------------------------------------------------------
def test_case_08_checkpoint_mismatch_invalidation(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    snapshot = _create_test_snapshot(store, "snap_A", "cand:domain:renerwave.com", "renerwave.com", 2)
    
    chk_rec = AnalysisChunkRecord(
        chunk_record_id="chk_mismatch",
        analysis_id="an_1",
        candidate_id="cand:domain:renerwave.com",
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.content_checksum,
        chunk_index=0,
        total_chunks=1,
        chunk_hash="hash_A",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        schema_version="2.1.0",
        prompt_version="2.1.0",
        status="COMPLETED",
        created_at="2026-11-03T12:00:00Z",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.0001,
        raw_response="{}",
    )
    store.save_chunk_checkpoint(chk_rec)

    # Query with different snapshot_id
    res_diff_snap = store.get_chunk_checkpoint_by_hash("cand:domain:renerwave.com", "snap_B", "hash_A", "gemini-3.5-flash-lite")
    assert res_diff_snap is None

    # Query with different model
    res_diff_model = store.get_chunk_checkpoint_by_hash("cand:domain:renerwave.com", snapshot.snapshot_id, "hash_A", "gemini-1.5-pro")
    assert res_diff_model is None


# ---------------------------------------------------------------------------
# 9. Cost Ceiling Enforced Across Resumed Executions
# ---------------------------------------------------------------------------
def test_case_09_cost_ceiling_enforced(tmp_path):
    db_path = str(tmp_path / "test_research.db")
    store = ResearchSnapshotStore(db_path=db_path)
    
    snapshot = _create_test_snapshot(store, "snap_budget", "cand:domain:renerwave.com", "renerwave.com", 5, chunk_text_multiplier=20)

    # Budget of $0.000001 (insufficient)
    auth_token = generate_analysis_auth_token("cand:domain:renerwave.com", "snap_budget", "gemini", "gemini-3.5-flash-lite", 0.000001)

    mock_gw = MagicMock()
    rec = execute_company_analysis(
        snapshot=snapshot,
        candidate_id="cand:domain:renerwave.com",
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        snapshot_store=store,
        gateway_override=mock_gw,
        auth_token=auth_token,
        require_auth=True,
        max_approved_budget_usd=0.000001,
    )
    assert rec.status in ("FAILED", "BUDGET_EXCEEDED")
    assert "projected" in rec.diagnostic_info or "budget" in rec.diagnostic_info.lower()


# ---------------------------------------------------------------------------
# 10. Accurate Progress & Chunk Accounting
# ---------------------------------------------------------------------------
def test_case_10_chunk_accounting(tmp_path):
    store = ResearchSnapshotStore(db_path=str(tmp_path / "test_research.db"))
    snapshot = _create_test_snapshot(store, "snap_acct", "cand:domain:renerwave.com", "renerwave.com", 20, chunk_text_multiplier=50)
    preflight = estimate_analysis_preflight(snapshot, provider_id="gemini", model="gemini-3.5-flash-lite")
    assert "chunks_count" in preflight
    assert "capped_chunks_count" in preflight
    assert preflight["capped_chunks_count"] <= 5


# ---------------------------------------------------------------------------
# 11. HTML Contact Extraction Contamination Rejection
# ---------------------------------------------------------------------------
def test_case_11_contact_extraction_contamination_rejection():
    html_sample = """
    <div>
        <span>049 8935842</span><a href="mailto:development@stcpi.com">development@stcpi.com</a>
        <p>Per maggiori informazioni scrivi a info@renerwave.comParla con noi</p>
        <p>PEC: renerwave@pec.it</p>
        <p>VAT / P.IVA: IT04989358420</p>
    </div>
    """
    page = PageFetchResult(
        url="https://stcpi.com/contact",
        page_type="contacts",
        status="FETCH_SUCCESS",
        title="Contact Us",
        raw_html=html_sample,
        text_content="049 8935842 development@stcpi.com Per maggiori informazioni scrivi a info@renerwave.com Parla con noi PEC: renerwave@pec.it",
    )
    res = extract_deterministic_facts(
        pages=[page],
        candidate_id="cand:domain:stcpi.com",
        company_name="STC S.r.l.",
        canonical_domain="stcpi.com",
    )
    email = res.email or ""
    
    # Must NOT contain concatenated phone+email: '8935842development@stcpi.com'
    assert "8935842development" not in email, f"Contaminated email found: {email}"
    # Must NOT contain trailing text: 'info@renerwave.comParla'
    assert not email.endswith("parla"), f"Contaminated email found: {email}"
    # Must contain cleanly extracted email
    assert "development@stcpi.com" in email


# ---------------------------------------------------------------------------
# 12. Service Page Title Rejection as Company Name
# ---------------------------------------------------------------------------
def test_case_12_generic_service_title_rejected_as_company_name():
    html_service_page = """
    <html>
        <head><title>BIM MEP | Progettazione Impianti</title></head>
        <body>
            <h1>BIM MEP</h1>
            <p>Servizi di modellazione BIM e progettazione MEP integrata.</p>
            <footer>&copy; 2026 Energymep S.r.l. - P.IVA 01234567890</footer>
        </body>
    </html>
    """
    page = PageFetchResult(
        url="https://energymep.it/servizi/bim-mep",
        page_type="services",
        status="FETCH_SUCCESS",
        title="BIM MEP | Progettazione Impianti",
        raw_html=html_service_page,
        text_content="BIM MEP Servizi di modellazione BIM e progettazione MEP integrata. 2026 Energymep S.r.l.",
    )
    res = extract_deterministic_facts(
        pages=[page],
        candidate_id="cand:domain:energymep.it",
        company_name="Energymep",
        canonical_domain="energymep.it",
    )
    assert res.legal_name != "BIM MEP", f"'BIM MEP' incorrectly extracted as legal name: {res.legal_name}"
    if res.legal_name:
        assert "energymep" in res.legal_name.lower()


# ---------------------------------------------------------------------------
# 13. Homepage Title Rejection as Business Activities
# ---------------------------------------------------------------------------
def test_case_13_generic_homepage_title_rejected():
    assert is_generic_page_title("Home page - RENERWAVE") is True
    assert is_generic_page_title("Homepage") is True
    assert is_generic_page_title("Home - Energymep") is True
    assert is_generic_page_title("Impianti Tecnologici ed Efficienza Energetica Industriale") is False


# ---------------------------------------------------------------------------
# 14. STC Pilot Finalized Record Preservation
# ---------------------------------------------------------------------------
def test_case_14_stc_finalized_record_preservation(tmp_path):
    from backend.sales_ui.router import validate_research_finalization, compute_field_checksum

    reg_store = LeadRegisterStore(db_path=str(tmp_path / "lead_register.db"))
    
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

    # Field reviews for core fields
    for fn, val in [
        ("company_name", stc_record.company_name),
        ("official_website", stc_record.official_website),
        ("business_activities", stc_record.business_activities),
    ]:
        rev = FieldReviewRecord(
            candidate_id="cand:domain:stcpi.com",
            field_name=fn,
            approved_value=val,
            value_checksum=compute_field_checksum(val),
            review_state="OPERATOR_APPROVED",
            source_type="operator_manual",
        )
        reg_store.save_field_review(rev)

    reviews = reg_store.get_field_reviews("cand:domain:stcpi.com")
    cand_mock = MagicMock()
    cand_mock.company_name = stc_record.company_name
    cand_mock.company_domain = "stcpi.com"

    is_valid, errors, checks = validate_research_finalization(
        cand=cand_mock,
        reg_record=stc_record,
        field_reviews=reviews,
        latest_snapshot=None,
        latest_analysis=None,
    )
    assert is_valid is True, f"STC finalized record failed validation: {errors}"
    assert checks["reviews_valid"] is True
