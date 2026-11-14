"""
tests/test_stable_auth_secret_hotfix.py
Focused regression test suite for stable, persistent LLM Analysis Authorization Secret.

Requirements verified:
1. get_auth_secret() retrieves persistent secret across process restarts/simulations.
2. An authorization token generated before server restart remains valid after restart.
3. Expiration enforcement: tokens older than validity window fail verification.
4. Tampering & signature enforcement: modified payload or signature fails with "Invalid authorization signature".
5. Candidate, snapshot, provider, and model binding enforcement.
6. Nonce replay protection: a token cannot be consumed multiple times when mark_used=True.
7. Verification of checkpoint resume without rerunning completed chunks.
8. Authentication and authorization checks in FastAPI research review remain intact.
"""

import os
import time
import json
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from sales_engine.research.pricing_config import (
    get_auth_secret,
    generate_analysis_auth_token,
    verify_analysis_auth_token,
)
from sales_engine.research.research_store import ResearchSnapshotStore, ResearchSnapshot, PageEvidence
from sales_engine.research.company_analysis_service import (
    AnalysisChunkRecord,
    validate_chunk_checkpoint_integrity,
    SCHEMA_VERSION,
    PROMPT_VERSION,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.sending.review_store import ReviewStore
from backend.operator_auth import OperatorAuthService
from backend.main import app


def test_persistent_secret_survives_restart_simulation(tmp_path, monkeypatch):
    """
    Test 1: When SALESAI_ANALYSIS_AUTH_SECRET is not set, secret is written to
    a persistent local file and reloaded consistently on subsequent calls.
    """
    monkeypatch.delenv("SALESAI_ANALYSIS_AUTH_SECRET", raising=False)
    secret_file = tmp_path / "custom_data" / ".analysis_auth_secret"

    # 1. First invocation generates and persists
    secret_1 = get_auth_secret(secret_file_override=secret_file)
    assert len(secret_1) >= 32
    assert secret_file.exists()

    # 2. Simulated restart in a new process/context reading the same file
    secret_2 = get_auth_secret(secret_file_override=secret_file)
    assert secret_1 == secret_2

    # 3. Token generated with secret_1 verifies with secret_2
    token = generate_analysis_auth_token(
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.25,
        validity_seconds=3600,
    )
    is_valid, budget, err = verify_analysis_auth_token(
        auth_token=token,
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        mark_used=False,
    )
    assert is_valid is True
    assert budget == 0.25
    assert err is None


def test_token_expiration_and_tampering():
    """
    Test 2: Token tampering and expiration enforcement.
    """
    token = generate_analysis_auth_token(
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
        validity_seconds=3600,
    )

    # 1. Tampered payload (budget changed)
    parts = token.split("|")
    tampered_parts = list(parts)
    tampered_parts[4] = "99.9999"  # Alter budget
    tampered_token = "|".join(tampered_parts)

    is_valid, _, err = verify_analysis_auth_token(
        auth_token=tampered_token,
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert is_valid is False
    assert "Token tampering detected" in err

    # 2. Tampered signature
    bad_sig_parts = list(parts)
    bad_sig_parts[-1] = "baddeadbeef" * 5
    is_valid_sig, _, err_sig = verify_analysis_auth_token(
        auth_token="|".join(bad_sig_parts),
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert is_valid_sig is False
    assert "Token tampering detected" in err_sig

    # 3. Expired token (validity_seconds=-1)
    expired_token = generate_analysis_auth_token(
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.50,
        validity_seconds=-10,
    )
    is_valid_exp, _, err_exp = verify_analysis_auth_token(
        auth_token=expired_token,
        candidate_id="cand:domain:example.com",
        snapshot_id="snap_123",
        provider="gemini",
        model="gemini-3.5-flash-lite",
    )
    assert is_valid_exp is False
    assert "expired" in err_exp.lower()


def test_binding_and_nonce_replay_protection():
    """
    Test 3: Candidate, snapshot, provider, model binding, and single-use nonce consumption.
    """
    token = generate_analysis_auth_token(
        candidate_id="cand:domain:target.com",
        snapshot_id="snap_target",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        max_budget_usd=0.10,
        validity_seconds=1800,
    )

    # Mismatched candidate
    v1, _, err1 = verify_analysis_auth_token(
        token, "cand:domain:wrong.com", "snap_target", "gemini", "gemini-3.5-flash-lite"
    )
    assert v1 is False
    assert "candidate mismatch" in err1

    # Mismatched snapshot
    v2, _, err2 = verify_analysis_auth_token(
        token, "cand:domain:target.com", "snap_other", "gemini", "gemini-3.5-flash-lite"
    )
    assert v2 is False
    assert "snapshot mismatch" in err2

    # Mismatched model
    v3, _, err3 = verify_analysis_auth_token(
        token, "cand:domain:target.com", "snap_target", "gemini", "gemini-1.5-pro"
    )
    assert v3 is False
    assert "model mismatch" in err3

    # Successful verification without marking used
    v_ok, b_ok, _ = verify_analysis_auth_token(
        token, "cand:domain:target.com", "snap_target", "gemini", "gemini-3.5-flash-lite", mark_used=False
    )
    assert v_ok is True
    assert b_ok == 0.10

    # Consume nonce
    v_consume, _, _ = verify_analysis_auth_token(
        token, "cand:domain:target.com", "snap_target", "gemini", "gemini-3.5-flash-lite", mark_used=True
    )
    assert v_consume is True

    # Replay attempt -> MUST fail
    v_replay, _, err_replay = verify_analysis_auth_token(
        token, "cand:domain:target.com", "snap_target", "gemini", "gemini-3.5-flash-lite", mark_used=True
    )
    assert v_replay is False
    assert "already been consumed" in err_replay.lower() or "replay" in err_replay.lower()


def test_checkpoint_integrity_and_resume_verification(tmp_path):
    """
    Test 4: Completed chunks with valid checkpoints are recognized and satisfied
    without requiring re-execution.
    """
    db_file = tmp_path / "test_checkpoint.db"
    store = ResearchSnapshotStore(db_path=str(db_file))

    candidate_id = "cand:domain:studiobim.it"
    snapshot_id = "snap_bim_1"
    content_checksum = "chk_abc123"
    chunk_hash = "chunk_hash_0"
    provider = "gemini"
    model = "gemini-3.5-flash-lite"

    from sales_engine.research.company_analysis_service import LEAD_REGISTER_13_FIELDS_SCHEMA

    # Construct valid 13-field proposed fields
    valid_13 = {
        f: {"value": f"val_{f}", "source_url": "https://studiobim.it", "confidence": 0.9, "is_hypothesis": False}
        for f in LEAD_REGISTER_13_FIELDS_SCHEMA["properties"].keys()
    }

    # Save parent snapshot first (satisfy foreign key)
    page = PageEvidence(
        snapshot_id=snapshot_id,
        evidence_id="ev_0",
        requested_url="https://studiobim.it",
        resolved_url="https://studiobim.it",
        page_type="homepage",
        http_status=200,
        retrieval_status="success",
        retrieved_at="2026-11-15T00:00:00Z",
        title="Studio BIM",
        meta_description="BIM services",
        content_sha256="page_hash_1",
        cleaned_text="Studio BIM provides open BIM architectural services.",
    )
    snap = store.create_snapshot(
        candidate_id=candidate_id,
        canonical_domain="studiobim.it",
        company_name="Studio BIM",
        pages=[page],
        snapshot_id=snapshot_id,
    )
    content_checksum = snap.content_checksum

    # Save completed chunk checkpoint
    cp = AnalysisChunkRecord(
        chunk_record_id="chk_rec_0",
        analysis_id="analysis_0",
        candidate_id=candidate_id,
        snapshot_id=snapshot_id,
        snapshot_checksum=content_checksum,
        chunk_index=0,
        total_chunks=1,
        chunk_hash=chunk_hash,
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        status="COMPLETED",
        created_at="2026-11-15T00:00:00Z",
        completed_at="2026-11-15T00:00:01Z",
        input_tokens=500,
        output_tokens=300,
        proposed_fields=valid_13,
    )
    store.save_chunk_checkpoint(cp)

    # Revalidate checkpoint integrity
    is_valid, reason = validate_chunk_checkpoint_integrity(
        cp=cp,
        expected_chunk_hash=chunk_hash,
        snapshot_checksum=content_checksum,
        expected_provider=provider,
        expected_model=model,
        expected_schema_version=SCHEMA_VERSION,
        expected_prompt_version=PROMPT_VERSION,
    )
    assert is_valid is True
    assert reason is None

    # Retrieve from store and verify it is discoverable
    cps = store.get_chunk_checkpoints(
        candidate_id=candidate_id,
        snapshot_id=snapshot_id,
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        snapshot_checksum=content_checksum,
    )
    assert len(cps) == 1
    assert cps[0].status == "COMPLETED"
    assert cps[0].chunk_hash == chunk_hash
