"""
Targeted regression tests for UX-01 Final Data Integrity Closure:
- Task A: Empty and whitespace Candidate ID rejection in CandidateCompany and CandidateStore.save_candidate().
- Task B: Discovery history provenance classification (Quick Brave Search with corroborating pilot attempt vs Ad-hoc vs Multi-Source).
- Verification that operational DB remains unmutated.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sales_engine.candidates.models import CandidateCompany
from sales_engine.candidates.candidate_store import CandidateStore
from backend.sales_ui.router import _classify_discovery_run_provenance


@pytest.fixture
def isolated_store(tmp_path):
    """Provides a fresh isolated CandidateStore targeting a temporary SQLite database."""
    db_file = str(tmp_path / "test_isolated_candidates.db")
    return CandidateStore(db_path=db_file)


# ============================================================================
# Task A: Candidate ID Empty & Whitespace Validation
# ============================================================================

def test_save_candidate_rejects_empty_id(isolated_store):
    """CandidateStore.save_candidate() must raise ValueError if candidate_id is empty."""
    cand = CandidateCompany(
        candidate_id="",
        company_name="Acme Corp",
        company_domain="acme.com",
    )
    with pytest.raises(ValueError, match="Cannot persist candidate: candidate_id must be a non-empty"):
        isolated_store.save_candidate(cand)


def test_save_candidate_rejects_whitespace_id(isolated_store):
    """CandidateStore.save_candidate() must raise ValueError if candidate_id is whitespace-only."""
    cand = CandidateCompany(
        candidate_id="   \t \n ",
        company_name="Acme Corp",
        company_domain="acme.com",
    )
    with pytest.raises(ValueError, match="Cannot persist candidate: candidate_id must be a non-empty"):
        isolated_store.save_candidate(cand)


def test_save_candidate_accepts_valid_id(isolated_store):
    """CandidateStore.save_candidate() succeeds and persists candidate when candidate_id is valid."""
    cid = isolated_store._generate_candidate_id("acme.com", "Acme Corp", "Italy")
    cand = CandidateCompany(
        candidate_id=cid,
        company_name="Acme Corp",
        company_domain="acme.com",
        country="Italy",
    )
    isolated_store.save_candidate(cand)

    retrieved = isolated_store.get_candidate(cid)
    assert retrieved is not None
    assert retrieved.candidate_id == cid
    assert retrieved.company_name == "Acme Corp"
    assert retrieved.company_domain == "acme.com"


def test_manual_seed_id_generation_logic(isolated_store):
    """Verify deterministic candidate ID generation for manual seed workflow."""
    cid1 = isolated_store._generate_candidate_id("innovative-bim.it", "Innovative BIM Srl", "Italy")
    cid2 = isolated_store._generate_candidate_id("innovative-bim.it", "Innovative BIM Srl", "Italy")
    assert cid1 == cid2
    assert cid1.strip() != ""
    assert len(cid1) > 5


# ============================================================================
# Task B: Discovery Provenance Classification
# ============================================================================

def test_classify_provenance_multi_source():
    """Runs with multiple executed sources are classified as Multi-Source Discovery."""
    res = _classify_discovery_run_provenance(
        sources_exec=["brave_job_search", "italy_engineering_directory"],
        started_at_str="2026-10-31T15:24:27.739918+00:00",
        cand_store=None,
    )
    assert res == "Multi-Source Discovery"


def test_classify_provenance_empty_sources():
    """Runs with no executed sources are classified as Ad-hoc / Legacy Discovery."""
    res = _classify_discovery_run_provenance(
        sources_exec=[],
        started_at_str="2026-10-31T15:24:27.739918+00:00",
        cand_store=None,
    )
    assert res == "Ad-hoc / Legacy Discovery"


def test_classify_provenance_other_single_source():
    """Single non-Brave source run is classified neutrally with source name."""
    res = _classify_discovery_run_provenance(
        sources_exec=["public_tender_portal_italy"],
        started_at_str="2026-10-31T15:24:27.739918+00:00",
        cand_store=None,
    )
    assert res == "Ad-hoc (public_tender_portal_italy)"


def test_classify_provenance_brave_without_pilot_attempt(isolated_store):
    """Single Brave source run without corroborating pilot_search_attempt is classified as Ad-hoc (brave_job_search)."""
    res = _classify_discovery_run_provenance(
        sources_exec=["brave_job_search"],
        started_at_str="2026-10-31T15:24:27.739918+00:00",
        cand_store=isolated_store,
    )
    assert res == "Ad-hoc (brave_job_search)"


def test_classify_provenance_brave_with_corroborating_pilot_attempt(isolated_store):
    """Single Brave source run WITH corroborating pilot_search_attempt is classified as Quick Brave Search."""
    now_iso = datetime.now(timezone.utc).isoformat()
    isolated_store.reserve_pilot_search_attempt(
        attempt_id="test_attempt_123",
        provider="brave_job_search",
        query="test query",
        country="Italy",
        city="Milan",
        max_attempts_budget=10,
        estimated_cost_usd=0.005,
        idempotency_token="token123",
    )

    res = _classify_discovery_run_provenance(
        sources_exec=["brave_job_search"],
        started_at_str=now_iso,
        cand_store=isolated_store,
    )
    assert res == "Quick Brave Search"
