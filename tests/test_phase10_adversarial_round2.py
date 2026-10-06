"""
tests/test_phase10_adversarial_round2.py
Round 2 Adversarial and Residual Blocker Verification Tests (P10-ADV2-001..034).

Verifies fixes for all 17 residual blockers identified during the independent attack pass:
- Blocker 1 & 2: Authoritative Phase 9 domain & email suppression contracts
- Blocker 3: Stale/non-current job evidence freshness gating
- Blocker 4: Source credential requirements enforcement
- Blocker 5: Source max_results_per_run limit enforcement
- Blocker 6: Unknown source type rejection
- Blocker 7: Real source adapter execution in API and CLI
- Blocker 8: Phase 8 grounding strict provenance validation
- Blocker 9: Historical event hash material discrimination
- Blocker 10: Path traversal protection on historical import
- Blocker 11 & 12: Execution-driven report generator & complete schema
- Blocker 13: Phase 10.5 UI bridge contract completeness
- Blocker 14: Source health persistence across process restarts
- Blocker 15: /api/sales/sources/import payload validation
- Blocker 16: CompanyClassifier method signatures consistency
- Blocker 17: Default candidate database and suppression wiring
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryStatus,
    MarketEvidence,
    OpportunityType,
    QualificationOutcome,
    RelationshipType,
    SourceHealthRecord,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier, qualify_candidate
from sales_engine.discovery_v2.bridges import (
    Phase8GroundingBridge,
    Phase9AuthorityBridge,
    Phase10ConsoleBridge,
)
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2
from sales_engine.discovery_v2.source_registry import SourceConfig, SourceRegistry
from sales_engine.historical.importer import HistoricalImporter, HistoricalRecord


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_p10_adv2.db")
    store = CandidateStore(db_path=db_file)
    yield store, db_file


# ---------------------------------------------------------------------------
# Blocker 1 & 2: Phase 9 Authority Bridge & Suppression
# ---------------------------------------------------------------------------
def test_p10_adv2_001_real_phase9_domain_suppression_blocks_qualification(temp_db):
    """P10-ADV2-001: Real Phase 9 domain suppression blocks classifier qualification."""
    store, db_path = temp_db
    # Seed suppression_list with company_domain
    with store._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                company_domain TEXT,
                scope TEXT,
                value TEXT,
                reason TEXT,
                created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO suppression_list (company_domain, reason, created_at) VALUES (?, ?, ?)",
            ("blocked-domain.com", "GDPR opt-out", datetime.now(timezone.utc).isoformat()),
        )

    bridge = Phase9AuthorityBridge(db_path=db_path)
    assert bridge.is_domain_suppressed("blocked-domain.com") is True
    assert bridge.is_domain_suppressed("allowed-domain.com") is False

    classifier = CompanyClassifier(suppression_store=bridge)
    cand = CandidateCompany(
        company_id="cand_blocked_1",
        name="Blocked Domain Corp",
        canonical_domain="blocked-domain.com",
    )
    res = classifier.qualify(cand)
    assert res.outcome in (QualificationOutcome.reject, QualificationOutcome.suppressed)
    assert cand.cold_outreach_allowed is False
    assert any("suppressed" in r.lower() for r in res.reasons)


def test_p10_adv2_002_fastapi_qualification_uses_authoritative_phase9_suppression(tmp_path):
    """P10-ADV2-002: FastAPI qualification path uses authoritative Phase 9 suppression."""
    from backend.main import app, get_discovery_services
    db_file = str(tmp_path / "shared_sales_outreach.db")

    # Seed suppression in db_file
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suppression_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            company_domain TEXT,
            scope TEXT,
            value TEXT,
            reason TEXT,
            created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO suppression_list (company_domain, reason) VALUES (?, ?)",
        ("forbidden.org", "Regulatory DNC"),
    )
    conn.commit()
    conn.close()

    os.environ["SALESAI_DB_PATH"] = db_file
    # Reset globals
    import backend.main as bm
    bm._discovery_store_inst = None
    bm._discovery_classifier_inst = None

    client = TestClient(app)
    # Create candidate in store
    store, _, _, _ = get_discovery_services()
    cand = CandidateCompany(
        company_id="cand_api_supp_1",
        name="Forbidden Org",
        canonical_domain="forbidden.org",
    )
    store.save_candidate(cand)

    res = client.post(f"/api/sales/candidates/{cand.company_id}/qualify")
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] in ("reject", "suppressed")
    assert data["cold_outreach_allowed"] is False


def test_p10_adv2_003_cli_qualification_uses_authoritative_phase9_suppression(tmp_path):
    """P10-ADV2-003: CLI qualification path uses authoritative Phase 9 suppression."""
    from sales_engine.discovery_v2.run import get_cli_services
    db_file = str(tmp_path / "cli_supp.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS suppression_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            company_domain TEXT,
            scope TEXT,
            value TEXT,
            reason TEXT,
            created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO suppression_list (company_domain, reason) VALUES (?, ?)",
        ("suppressed-cli.com", "Legal hold"),
    )
    conn.commit()
    conn.close()

    store, registry, classifier, engine, bridge = get_cli_services(db_path=db_file)
    cand = CandidateCompany(
        company_id="cand_cli_1",
        name="Suppressed CLI Corp",
        canonical_domain="suppressed-cli.com",
    )
    store.save_candidate(cand)

    qual = classifier.classify_company(cand)
    assert qual.outcome in (QualificationOutcome.reject, QualificationOutcome.suppressed)
    assert qual.cold_outreach_allowed is False


def test_p10_adv2_004_email_only_suppression_does_not_suppress_entire_domain(temp_db):
    """P10-ADV2-004: Email-only suppression does not suppress unrelated addresses on same domain."""
    store, db_path = temp_db
    with store._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                company_domain TEXT,
                scope TEXT,
                value TEXT,
                reason TEXT,
                created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO suppression_list (email, company_domain, reason) VALUES (?, NULL, ?)",
            ("optout_user@company.com", "Unsubscribe"),
        )

    bridge = Phase9AuthorityBridge(db_path=db_path)
    # The domain itself should NOT be domain-suppressed
    assert bridge.is_domain_suppressed("company.com") is False
    # The specific email IS suppressed
    assert bridge.is_email_suppressed("optout_user@company.com") is True
    # An unrelated email on that same domain is NOT suppressed
    assert bridge.is_email_suppressed("other_user@company.com") is False


def test_p10_adv2_005_explicit_company_domain_suppression_blocks_the_domain(temp_db):
    """P10-ADV2-005: Explicit company_domain suppression blocks the domain."""
    store, db_path = temp_db
    with store._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                company_domain TEXT,
                scope TEXT,
                value TEXT,
                reason TEXT,
                created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO suppression_list (company_domain, scope, reason) VALUES (?, 'domain', ?)",
            ("entire-domain-blocked.com", "Client blacklist"),
        )

    bridge = Phase9AuthorityBridge(db_path=db_path)
    assert bridge.is_domain_suppressed("entire-domain-blocked.com") is True
    # Any email under this domain is also suppressed
    assert bridge.is_email_suppressed("anyone@entire-domain-blocked.com") is True


# ---------------------------------------------------------------------------
# Blocker 3: Stale / Non-Current Job Evidence Intent Gating
# ---------------------------------------------------------------------------
def test_p10_adv2_006_old_non_current_job_evidence_does_not_produce_high_intent():
    """P10-ADV2-006: Old/non-current job evidence does not produce current high intent."""
    classifier = CompanyClassifier()
    cand = CandidateCompany(
        company_id="cand_stale_1",
        name="Stale Job Architecture",
        canonical_domain="stale-arch.com",
    )
    old_evidence = [
        MarketEvidence(
            evidence_id="ev_stale_1",
            candidate_id=cand.company_id,
            category="job",
            signal_type="active_hiring",
            text="Hiring 5 Senior BIM Coordinators",
            freshness_status="old",
            is_current=False,
        )
    ]
    fit, intent, fit_r, intent_r = classifier.evaluate_fit_and_intent(cand, old_evidence)
    assert intent in ("low", "none", "unknown")
    assert intent != "high"


def test_p10_adv2_007_fresh_current_job_evidence_can_produce_current_intent():
    """P10-ADV2-007: Fresh current job evidence can produce current intent."""
    classifier = CompanyClassifier()
    cand = CandidateCompany(
        company_id="cand_fresh_1",
        name="Active Hiring BIM Studio",
        canonical_domain="active-bim.com",
    )
    fresh_evidence = [
        MarketEvidence(
            evidence_id="ev_fresh_1",
            candidate_id=cand.company_id,
            category="job",
            signal_type="active_hiring",
            text="Looking for BIM Lead Urgent",
            freshness_status="fresh",
            is_current=True,
        )
    ]
    fit, intent, fit_r, intent_r = classifier.evaluate_fit_and_intent(cand, fresh_evidence)
    assert intent == "high"


def test_p10_adv2_008_newer_current_evidence_can_supersede_without_deleting_history():
    """P10-ADV2-008: Newer current evidence supersedes stale evidence without deleting history."""
    classifier = CompanyClassifier()
    cand = CandidateCompany(
        company_id="cand_multi_ev",
        name="Mixed Evidence Studio",
        canonical_domain="mixed-bim.com",
    )
    mixed_evidence = [
        MarketEvidence(
            evidence_id="ev_old",
            candidate_id=cand.company_id,
            category="job",
            signal_type="active_hiring",
            text="Old hiring signal from 2022",
            freshness_status="old",
            is_current=False,
        ),
        MarketEvidence(
            evidence_id="ev_new",
            candidate_id=cand.company_id,
            category="job",
            signal_type="active_hiring",
            text="Current active hiring BIM Manager",
            freshness_status="fresh",
            is_current=True,
        ),
    ]
    fit, intent, fit_r, intent_r = classifier.evaluate_fit_and_intent(cand, mixed_evidence)
    assert intent == "high"
    assert len(mixed_evidence) == 2


# ---------------------------------------------------------------------------
# Blocker 4: Source Credential Enforcement
# ---------------------------------------------------------------------------
def test_p10_adv2_009_missing_required_credential_produces_controlled_failure(temp_db):
    """P10-ADV2-009: Missing required source credential produces controlled source failure without crash."""
    store, _ = temp_db
    registry = SourceRegistry()
    registry.sources["test_cred_src"] = SourceConfig(
        id="test_cred_src",
        name="Credentialed Source",
        type="search_engine",
        requires_api_key=True,
        credential_env_var="NON_EXISTENT_TEST_KEY_12345",
    )
    # Ensure env var is unset
    os.environ.pop("NON_EXISTENT_TEST_KEY_12345", None)

    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)
    rec = engine.run_multi_source_discovery(sources=["test_cred_src"], query="Engineering")
    assert rec.status == "failed"
    assert len(rec.errors) == 1
    assert "NON_EXISTENT_TEST_KEY_12345" in rec.errors[0]
    health = registry.get_health_summary()["test_cred_src"]
    assert health["failure_count"] >= 1


def test_p10_adv2_010_configured_credential_allows_source_execution_without_exposing_secret(temp_db):
    """P10-ADV2-010: Configured credential allows execution without exposing the secret in health or runs."""
    store, _ = temp_db
    registry = SourceRegistry()
    secret_val = "SECRET_TOKEN_XYZ_98765"
    os.environ["TEMP_TEST_KEY_999"] = secret_val

    registry.sources["test_cred_ok"] = SourceConfig(
        id="test_cred_ok",
        name="Credentialed Source OK",
        type="search_engine",
        requires_api_key=True,
        credential_env_var="TEMP_TEST_KEY_999",
    )

    fetcher_called = False
    def mock_fetcher(query):
        nonlocal fetcher_called
        fetcher_called = True
        return [{"company_name": "Authed Corp", "domain": "authed.com"}]

    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)
    rec = engine.run_multi_source_discovery(
        sources=["test_cred_ok"],
        query="Test",
        fetcher_funcs={"test_cred_ok": mock_fetcher},
    )
    assert fetcher_called is True
    assert rec.status == "completed"
    assert rec.observations_found == 1
    # Check that secret_val is never present in run or health
    rec_json = json.dumps(rec.__dict__)
    assert secret_val not in rec_json
    os.environ.pop("TEMP_TEST_KEY_999", None)


# ---------------------------------------------------------------------------
# Blocker 5: Source max_results_per_run Enforcement
# ---------------------------------------------------------------------------
def test_p10_adv2_011_source_max_results_per_run_is_enforced(temp_db):
    """P10-ADV2-011: Source max_results_per_run is enforced before candidate ingestion."""
    store, _ = temp_db
    registry = SourceRegistry()
    registry.sources["capped_source"] = SourceConfig(
        id="capped_source",
        name="Capped Source",
        type="directory",
        max_results_per_run=10,
    )

    def oversized_fetcher(q):
        return [{"company_name": f"Company {i}", "domain": f"comp{i}.com"} for i in range(25)]

    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)
    rec = engine.run_multi_source_discovery(
        sources=["capped_source"],
        query="Arch",
        fetcher_funcs={"capped_source": oversized_fetcher},
    )
    assert rec.observations_found == 10
    assert rec.candidates_discovered == 10


def test_p10_adv2_012_run_metrics_reflect_only_accepted_results(temp_db):
    """P10-ADV2-012: Run metrics accurately reflect accepted results within limit."""
    store, _ = temp_db
    registry = SourceRegistry()
    registry.sources["capped_source_2"] = SourceConfig(
        id="capped_source_2",
        name="Capped Source 2",
        type="directory",
        max_results_per_run=5,
    )
    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)
    rec = engine.run_multi_source_discovery(
        sources=["capped_source_2"],
        query="BIM",
        fetcher_funcs={"capped_source_2": lambda q: [{"company_name": f"C {i}", "domain": f"c{i}.com"} for i in range(15)]},
    )
    assert rec.observations_found == 5
    assert rec.new_candidates == 5


# ---------------------------------------------------------------------------
# Blocker 6: Unknown Source Type Rejection
# ---------------------------------------------------------------------------
def test_p10_adv2_013_unknown_source_type_is_rejected():
    """P10-ADV2-013: Unknown source type is rejected with ValueError."""
    registry = SourceRegistry()
    with pytest.raises(ValueError, match="Unsupported source type 'totally_unknown'"):
        registry.register_source({
            "id": "bad_src",
            "name": "Bad Source",
            "type": "totally_unknown",
        })


# ---------------------------------------------------------------------------
# Blocker 7: Source Adapter Execution in API & CLI
# ---------------------------------------------------------------------------
def test_p10_adv2_014_api_discovery_invokes_registered_adapter(tmp_path):
    """P10-ADV2-014: API discovery invokes registered adapter instead of returning no-op success."""
    from backend.main import app, get_discovery_services
    db_file = str(tmp_path / "api_adapter.db")
    os.environ["SALESAI_DB_PATH"] = db_file
    import backend.main as bm
    bm._discovery_store_inst = None
    bm._discovery_classifier_inst = None
    bm._discovery_engine_inst = None

    client = TestClient(app)
    # Register a directory source with manual seed adapter
    store, registry, _, engine = get_discovery_services()
    registry.sources["test_adapter_src"] = SourceConfig(
        id="test_adapter_src",
        name="Test Adapter Source",
        type="manual",
    )

    res = client.post("/api/sales/discovery/run", json={
        "sources": ["test_adapter_src"],
        "query": "BIM",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("completed", "partial_failure")


def test_p10_adv2_015_cli_discovery_invokes_registered_adapter(tmp_path):
    """P10-ADV2-015: CLI discovery invokes registered adapter."""
    from sales_engine.discovery_v2.run import main
    db_file = str(tmp_path / "cli_adapter.db")
    # Execute CLI run with registered manual_seed_importer
    code = main(["--db", db_file, "run", "--sources", "manual_seed_importer", "--query", "Test"])
    assert code == 0


def test_p10_adv2_016_configured_source_without_adapter_is_explicit_failure(temp_db):
    """P10-ADV2-016: Configured source without adapter is an explicit failure, not silent success."""
    store, _ = temp_db
    registry = SourceRegistry()
    registry.sources["orphan_source"] = SourceConfig(
        id="orphan_source",
        name="Orphan Source",
        type="custom",  # No built-in default adapter for generic custom
    )
    engine = DiscoveryEngineV2(candidate_store=store, source_registry=registry)
    rec = engine.run_multi_source_discovery(sources=["orphan_source"], query="Test")
    assert rec.status == "failed"
    assert any("has no registered adapter" in err for err in rec.errors)


def test_p10_adv2_017_job_first_discovery_mode_preserves_job_signals(temp_db):
    """P10-ADV2-017: Job-first discovery retains job details without synthesizing jobs in company-first."""
    store, _ = temp_db
    engine = DiscoveryEngineV2(candidate_store=store)

    # Job-first observation
    c_job, obs_job = engine.discover_company(
        company_name="Job Studio",
        domain="jobstudio.com",
        source_name="ats_source",
        discovery_mode="job_first",
        extracted_data={"job_title": "BIM Lead", "technologies": ["Revit"]},
    )
    assert obs_job.extracted_data.get("job_title") == "BIM Lead"

    # Company-first observation with accidental job field stripped
    c_comp, obs_comp = engine.discover_company(
        company_name="Directory Studio",
        domain="directorystudio.com",
        source_name="dir_source",
        discovery_mode="company_first",
        extracted_data={"structured_job_id": "fake_123", "company_types": ["architecture"]},
    )
    assert "structured_job_id" not in obs_comp.extracted_data


# ---------------------------------------------------------------------------
# Blocker 8: Phase 8 Grounding Strict Provenance Validation
# ---------------------------------------------------------------------------
def test_p10_adv2_018_arbitrary_internal_observation_cannot_become_verified_fact():
    """P10-ADV2-018: Arbitrary/internal observation cannot become verified Phase 8 fact."""
    cand = CandidateCompany(company_id="c_ground_1", name="Target Corp", canonical_domain="target.com")
    fake_obs = [
        DiscoveryObservation(
            observation_id="obs_fake_note",
            company_id=cand.company_id,
            source_id="internal_note",
            source_name="internal_note",
            source_type="internal_note",
            source_url="https://example.com",
            extracted_data={
                "project_title": "Fabricated Project",
                "technologies": ["ImaginaryTech"],
            },
        )
    ]
    facts = Phase8GroundingBridge.extract_grounded_facts(cand, fake_obs)
    assert "ImaginaryTech" not in facts["verified_technologies"]
    assert len(facts["verified_projects"]) == 0


def test_p10_adv2_019_eligible_sourced_market_evidence_can_become_grounded_fact():
    """P10-ADV2-019: Eligible sourced MarketEvidence from trusted source becomes grounded fact."""
    cand = CandidateCompany(company_id="c_ground_2", name="Verified Studio", canonical_domain="verified.com")
    valid_obs = [
        DiscoveryObservation(
            observation_id="obs_pub_1",
            company_id=cand.company_id,
            source_id="company_website",
            source_name="Official Website",
            source_type="company_website",
            source_url="https://verified.com/projects",
            extracted_data={
                "project_title": "City Hospital BIM",
                "technologies": ["Autodesk Revit", "Navisworks"],
            },
        )
    ]
    facts = Phase8GroundingBridge.extract_grounded_facts(cand, valid_obs)
    assert "Autodesk Revit" in facts["verified_technologies"]
    assert len(facts["verified_projects"]) == 1
    assert facts["verified_projects"][0]["title"] == "City Hospital BIM"


def test_p10_adv2_020_source_url_alone_is_insufficient_verification():
    """P10-ADV2-020: Source URL alone is insufficient without trusted source type."""
    cand = CandidateCompany(company_id="c_ground_3", name="Untrusted Src Studio", canonical_domain="untrusted.com")
    untrusted_obs = [
        DiscoveryObservation(
            observation_id="obs_untrusted",
            company_id=cand.company_id,
            source_id="scratchpad_notes",
            source_name="Scratchpad",
            source_type="internal_assessment",
            source_url="https://some-unrelated-link.com",
            extracted_data={"project_title": "Unverified Tower"},
        )
    ]
    facts = Phase8GroundingBridge.extract_grounded_facts(cand, untrusted_obs)
    assert len(facts["verified_projects"]) == 0


# ---------------------------------------------------------------------------
# Blocker 9: Historical Event Hash Material Discrimination
# ---------------------------------------------------------------------------
def test_p10_adv2_021_renamed_reordered_identical_event_remains_idempotent():
    """P10-ADV2-021: Renamed/reordered identical event produces identical hash."""
    rec1 = HistoricalRecord(
        source_file="file_A.xlsx",
        sheet_name="Sheet1",
        row_number=10,
        company_name="Studio Alpha",
        domain="alpha.com",
        tier_hint="tier_1",
        notes="First note",
        source_url=None,
        contact_name="Mario Rossi",
        contact_role="Director",
        emails=["mario@alpha.com", "info@alpha.com"],
        contact_date="2024-01-15",
    )
    rec2 = HistoricalRecord(
        source_file="file_B_renamed.csv",
        sheet_name="OtherSheet",
        row_number=99,
        company_name="Studio Alpha",
        domain="alpha.com",
        tier_hint="tier_1",
        notes="First note",
        source_url=None,
        contact_name="Mario Rossi",
        contact_role="Director",
        emails=["info@alpha.com", "mario@alpha.com"],  # Reordered emails
        contact_date="2024-01-15",
    )
    assert rec1.compute_hash() == rec2.compute_hash()


def test_p10_adv2_022_materially_changed_follow_up_reply_gets_distinct_hash():
    """P10-ADV2-022: Materially changed follow-up date or respondent produces distinct hash."""
    rec1 = HistoricalRecord(
        source_file="file.xlsx",
        sheet_name="S1",
        row_number=1,
        company_name="Beta Arch",
        domain="beta.com",
        tier_hint=None,
        notes=None,
        source_url=None,
        contact_name=None,
        contact_role=None,
        emails=["info@beta.com"],
        contact_date="2024-02-01",
        follow_up_date="2024-02-15",
        respondent_name="Alice",
    )
    rec2 = HistoricalRecord(
        source_file="file.xlsx",
        sheet_name="S1",
        row_number=1,
        company_name="Beta Arch",
        domain="beta.com",
        tier_hint=None,
        notes=None,
        source_url=None,
        contact_name=None,
        contact_role=None,
        emails=["info@beta.com"],
        contact_date="2024-02-01",
        follow_up_date="2024-03-01",  # Changed follow up date!
        respondent_name="Alice",
    )
    assert rec1.compute_hash() != rec2.compute_hash()


def test_p10_adv2_023_duplicate_identical_event_remains_deduplicated(temp_db):
    """P10-ADV2-023: Duplicate identical event remains deduplicated in CandidateStore."""
    store, _ = temp_db
    importer = HistoricalImporter(store=store)
    rec = HistoricalRecord(
        source_file="test.csv",
        sheet_name="Sheet1",
        row_number=2,
        company_name="Gamma Studio",
        domain="gamma.com",
        tier_hint=None,
        notes=None,
        source_url=None,
        contact_name="Luca",
        contact_role="Partner",
        emails=["luca@gamma.com"],
        contact_date="2024-01-10",
    )
    res1 = importer.import_records([rec])
    assert res1.companies_created == 1
    # Re-import identical record
    res2 = importer.import_records([rec])
    assert res2.companies_created == 0
    assert res2.duplicates_skipped == 1


# ---------------------------------------------------------------------------
# Blocker 10: Path Traversal Protection
# ---------------------------------------------------------------------------
def test_p10_adv2_024_historical_import_rejects_path_traversal():
    """P10-ADV2-024: Historical import endpoint rejects ../ traversal and outside-root path."""
    from backend.main import app
    client = TestClient(app)
    # Attempt path traversal
    res = client.post("/api/sales/history/import", json={"file_path": "../../windows/system32/cmd.exe"})
    assert res.status_code == 400
    assert "Access denied" in res.json()["detail"]


def test_p10_adv2_025_allowed_import_root_file_works(tmp_path):
    """P10-ADV2-025: Allowed repository/data root file imports cleanly."""
    from backend.main import app
    # Write a test CSV in the repository data directory or workspace
    repo_data_dir = Path(__file__).resolve().parent.parent / "data"
    repo_data_dir.mkdir(exist_ok=True)
    test_csv = repo_data_dir / "test_p10_adv2_import.csv"
    test_csv.write_text("Company,Domain,Email,Contact Date\nSafe Arch,safearch.com,info@safearch.com,2024-01-01\n", encoding="utf-8")

    client = TestClient(app)
    res = client.post("/api/sales/history/import", json={"file_path": str(test_csv)})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["companies_created"] >= 1
    # Cleanup test CSV
    try:
        test_csv.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Blocker 11 & 12: Execution-Driven Report Generator & QA Meta-Tests
# ---------------------------------------------------------------------------
def test_p10_adv2_026_failed_test_cannot_appear_pass_in_phase10_report(tmp_path):
    """P10-ADV2-026: Failed test cannot appear PASS in generated report."""
    from scripts.generate_phase10_report import generate_report_from_results
    fake_pytest_results = {
        "P10-REG-001": "FAIL",
        "P10-REG-002": "PASS",
    }
    report = generate_report_from_results(fake_pytest_results, commit_sha="testsha")
    case_1 = next(c for c in report["cases"] if c["case_id"] == "P10-REG-001")
    assert case_1["status"] == "FAIL"
    assert report["critical_regressions"]["P10-REG-001"] == "FAIL"
    assert report["failed"] >= 1


def test_p10_adv2_027_unmapped_deterministic_case_cannot_appear_pass(tmp_path):
    """P10-ADV2-027: Unmapped deterministic case resolves to NOT_RUN, not PASS."""
    from scripts.generate_phase10_report import generate_report_from_results
    fake_pytest_results = {
        "P10-REG-001": "PASS",
        # P10-REG-002 is omitted/unmapped
    }
    report = generate_report_from_results(fake_pytest_results, commit_sha="testsha")
    case_2 = next(c for c in report["cases"] if c["case_id"] == "P10-REG-002")
    assert case_2["status"] == "NOT_RUN"
    assert report["not_run"] >= 1


def test_p10_adv2_028_phase10_invariants_derive_from_mapped_execution():
    """P10-ADV2-028: Phase 10 report invariants derive dynamically from actual test results."""
    from scripts.generate_phase10_report import generate_report_from_results
    # If historical guard fails, invariant must be False
    fake_pytest_results = {
        "P10-REG-006": "FAIL",
        "P10-ADV-001": "FAIL",
    }
    report = generate_report_from_results(fake_pytest_results, commit_sha="testsha")
    assert report["invariants"]["historical_guard_monotonic_unresettable"] is False


def test_p10_adv2_029_phase10_safety_counters_are_dynamic_not_literal_constants():
    """P10-ADV2-029: Phase 10 safety counters are dynamic and track violations."""
    from scripts.generate_phase10_report import generate_report_from_results
    fake_pytest_results = {
        "P10-REG-010": "FAIL",  # Suppression failure
        "P10-REG-022": "FAIL",  # Send violation failure
    }
    report = generate_report_from_results(fake_pytest_results, commit_sha="testsha")
    assert report["suppression_bypass_count"] >= 1
    assert report["phase9_send_violation_count"] >= 1


# ---------------------------------------------------------------------------
# Blocker 13: Phase 10.5 UI Bridge Contract Completeness
# ---------------------------------------------------------------------------
def test_p10_adv2_030_candidate_list_exposes_required_summary_fields(temp_db):
    """P10-ADV2-030: Candidate list summary exposes country, qualification outcome/reason, and historical outreach."""
    store, _ = temp_db
    cand = CandidateCompany(
        company_id="cand_summary_1",
        name="Summary Studio",
        canonical_domain="summary.com",
        country="Germany",
        qualification_outcome=QualificationOutcome.watchlist,
        qualification_reasons=["Good BIM fit with pending intent signal"],
    )
    store.save_candidate(cand)

    bridge = Phase10ConsoleBridge(store=store)
    items = bridge.get_candidate_list_summary()
    assert len(items) == 1
    item = items[0]
    assert item["country"] == "Germany"
    assert item["qualification_outcome"] == "watchlist"
    assert "Good BIM fit" in item["qualification_reason"]
    assert "historical_outreach_summary" in item


def test_p10_adv2_031_candidate_detail_bridge_includes_evidence_directly(temp_db):
    """P10-ADV2-031: Candidate detail bridge includes evidence/provenance directly."""
    store, _ = temp_db
    cand = CandidateCompany(company_id="cand_det_1", name="Detail Studio", canonical_domain="det.com")
    store.save_candidate(cand)
    ev = MarketEvidence(
        evidence_id="ev_det_1",
        candidate_id=cand.company_id,
        category="technology",
        signal_type="tool_usage",
        text="Allplan 2024",
    )
    store.save_evidence(ev)

    bridge = Phase10ConsoleBridge(store=store)
    detail = bridge.get_candidate_detail(cand.company_id)
    assert detail is not None
    assert "evidence" in detail
    assert len(detail["evidence"]) == 1
    assert detail["evidence"][0]["text"] == "Allplan 2024"


def test_p10_adv2_032_qualification_reason_is_available_after_restart(temp_db):
    """P10-ADV2-032: Qualification reason is persisted and available after CandidateStore re-instantiation."""
    store, db_path = temp_db
    cand = CandidateCompany(
        company_id="cand_reboot_1",
        name="Reboot Corp",
        canonical_domain="reboot.com",
        qualification_outcome=QualificationOutcome.qualified_for_enrichment,
        qualification_reasons=["High fit matched", "Verified active BIM demand"],
    )
    store.save_candidate(cand)

    # Reconnect new store
    restarted_store = CandidateStore(db_path=db_path)
    loaded = restarted_store.get_by_id("cand_reboot_1")
    assert loaded is not None
    assert loaded.qualification_outcome == QualificationOutcome.qualified_for_enrichment
    assert "High fit matched" in loaded.qualification_reasons
    assert loaded.qualification_reason == "High fit matched; Verified active BIM demand"


# ---------------------------------------------------------------------------
# Blocker 14: Source Health Persistence Across Restarts
# ---------------------------------------------------------------------------
def test_p10_adv2_033_source_health_survives_restart_in_bridge(temp_db):
    """P10-ADV2-033: Source health persists to SQLite and survives console bridge restart."""
    store, db_path = temp_db
    # Record health
    store.save_source_health(SourceHealthRecord(
        source_name="persistent_ats",
        status="degraded",
        success_count=42,
        failure_count=3,
        last_success_at=datetime.now(timezone.utc).isoformat(),
        last_failure_at=datetime.now(timezone.utc).isoformat(),
        last_error="HTTP 429 Rate Limit",
    ))

    # Restart with clean memory registry & bridge
    fresh_store = CandidateStore(db_path=db_path)
    fresh_registry = SourceRegistry()
    bridge = Phase10ConsoleBridge(store=fresh_store, registry=fresh_registry)

    summary = bridge.get_source_health_summary()
    assert "persistent_ats" in summary
    assert summary["persistent_ats"]["status"] == "degraded"
    assert summary["persistent_ats"]["success_count"] == 42
    assert summary["persistent_ats"]["failure_count"] == 3
    assert summary["persistent_ats"]["last_error"] == "HTTP 429 Rate Limit"


# ---------------------------------------------------------------------------
# Blocker 15: Source Import Validation
# ---------------------------------------------------------------------------
def test_p10_adv2_034_source_import_rejects_credentials_and_validates_type():
    """P10-ADV2-034: /api/sales/sources/import rejects embedded secrets and invalid types."""
    from backend.main import app
    client = TestClient(app)

    # Reject secret payload
    res_secret = client.post("/api/sales/sources/import", json={
        "id": "leak_src",
        "name": "Leaky Source",
        "type": "directory",
        "api_secret": "sk-1234567890abcdef",
    })
    assert res_secret.status_code == 400
    assert "secrets or credentials" in res_secret.json()["detail"]

    # Reject invalid type
    res_bad_type = client.post("/api/sales/sources/import", json={
        "id": "invalid_type_src",
        "name": "Invalid Type Source",
        "type": "unsupported_xyz_type",
    })
    assert res_bad_type.status_code == 400
    assert "Unsupported source type" in res_bad_type.json()["detail"]
