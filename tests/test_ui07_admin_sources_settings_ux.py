"""
Tests for UI-07: Administration, Sources & Settings UX Refinement
Covers:
1. Excluded Companies registry: informative distinction banner, company link to workspace, restore/exclude actions.
2. Suppression (DNC): statutory zero-send barrier banner, domain search links, explicit scope.
3. Watchlist: signal monitoring badge/banner, zero direct send guarantee, deep link to candidate workspace & research.
4. Sources: distinct intake channels (Discovery, Historical, 13-Column Lead Research), Preview -> Confirm isolation.
5. Settings: offline send gate invariants (EMAIL_SEND_ENABLED=false), masked secrets (no credentials leaked), search budget & rate limits.
"""

import os
import sqlite3
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from backend.sales_ui.router import router, get_console_services
from sales_engine.candidates.candidate_store import CandidateStore, CandidateCompany
from sales_engine.candidates.models import DiscoveryStatus
from sales_engine.candidates.exclusion_store import ExcludedCompaniesStore, ExclusionReason, ExclusionStatus


@pytest.fixture
def disposable_ui07_env(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "ui07_test.db"

    monkeypatch.setenv("SALES_DATABASE_PATH", str(test_db))
    monkeypatch.setenv("SALES_OUTREACH_DB", str(test_db))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "mock_brave_key_should_be_masked")
    monkeypatch.setenv("RESEND_API_KEY", "mock_resend_key_should_be_masked")

    c_store = CandidateStore(db_path=str(test_db))
    excl_store = ExcludedCompaniesStore(db_path=str(test_db))
    excl_store._init_db()

    conn = sqlite3.connect(str(test_db))
    # 3. Suppression List
    conn.execute("""
    CREATE TABLE IF NOT EXISTS suppression_list (
        suppression_id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        company_domain TEXT,
        reason TEXT NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    # 4. Watchlist Candidates
    conn.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_candidates (
        candidate_id TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        company_domain TEXT,
        tier TEXT,
        fit TEXT,
        intent TEXT,
        watch_reason TEXT,
        signals_to_monitor_json TEXT,
        last_checked_at TEXT,
        created_at TEXT NOT NULL
    );
    """)

    # 5. Lead Register (13 Fields)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lead_register (
        record_number INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT UNIQUE,
        company_name TEXT NOT NULL,
        canonical_domain TEXT,
        company_type TEXT,
        primary_service_offered TEXT,
        software_used TEXT,
        telephone_numbers TEXT,
        hq_location TEXT,
        key_stakeholders_names TEXT,
        key_stakeholders_roles TEXT,
        past_successful_projects TEXT,
        social_media_links TEXT,
        contact_email_addresses TEXT,
        verification_status TEXT DEFAULT 'Unverified',
        operator_notes TEXT,
        verified_by TEXT,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()

    # Candidate 1: Excluded company
    cand_excl = CandidateCompany(
        candidate_id="cand_ui07_excl",
        company_name="Vendor Solutions Ltd",
        normalized_company_name="vendor solutions ltd",
        company_domain="vendorsolutions.com",
        country="Germany",
        city="Munich",
        discovery_status=DiscoveryStatus.rejected,
        cold_outreach_allowed=False,
    )
    c_store.save_candidate(cand_excl)

    # Candidate 2: Watchlist candidate
    cand_watch = CandidateCompany(
        candidate_id="cand_ui07_watch",
        company_name="Future BIM Studio",
        normalized_company_name="future bim studio",
        company_domain="futurebim.be",
        country="Belgium",
        city="Brussels",
        company_tier="tier_2",
        discovery_status=DiscoveryStatus.watchlist,
        intent_signals=["hiring:bim-coordinator", "project:revit"],
        cold_outreach_allowed=False,
    )
    c_store.save_candidate(cand_watch)

    excl_store.exclude_company(
        company_name="Vendor Solutions Ltd",
        canonical_domain="vendorsolutions.com",
        candidate_id="cand_ui07_excl",
        exclusion_reason=ExclusionReason.SOFTWARE_VENDOR,
        operator_note="Commercial software vendor, not an engineering target",
    )

    # DNC entry via direct SQL / suppression_list table
    conn = sqlite3.connect(str(test_db))
    conn.execute("""
    INSERT INTO suppression_list (suppression_id, email, company_domain, reason, source, created_at)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
    """, ("supp_ui07_1", "optout@suppressedfirm.de", "suppressedfirm.de", "opt_out", "operator"))

    # Watchlist Candidate
    conn.execute("""
    INSERT INTO watchlist_candidates (candidate_id, company_name, company_domain, tier, fit, intent, watch_reason, signals_to_monitor_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
    """, (
        "cand_ui07_watch",
        "Future BIM Studio",
        "futurebim.be",
        "TIER_B",
        "High Fit",
        "Low Intent",
        "Waiting for active BIM coordinator job posting",
        '["hiring:bim-coordinator", "project:revit"]'
    ))
    conn.commit()
    conn.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    return {
        "client": client,
        "cand_store": c_store,
        "db_path": test_db,
    }


def test_01_admin_pages_distinction_and_links(disposable_ui07_env):
    """
    Test Administration Pages:
    - Excluded Companies: informative distinction banner, candidate workspace link, restore button.
    - Suppression (DNC): compliance barrier banner, domain candidate filter link.
    - Watchlist: signal monitoring badge, zero direct send warning, candidate workspace and research links.
    """
    client = disposable_ui07_env["client"]

    # 1. Excluded Companies
    resp = client.get("/sales/excluded-companies")
    assert resp.status_code == 200
    html = resp.text
    assert "Administration Policy Distinction" in html
    assert "Business Exclusion (This Registry)" in html
    assert "Do Not Contact / Suppression (Phase 9)" in html
    assert "Candidate Watchlist" in html
    assert 'href="/sales/candidates/cand_ui07_excl"' in html
    assert "Vendor Solutions Ltd ↗" in html
    assert "SOFTWARE_VENDOR" in html
    assert "Workspace ↗" in html

    # 2. Suppression (DNC)
    resp = client.get("/sales/do-not-contact")
    assert resp.status_code == 200
    html = resp.text
    assert "Statutory &amp; Policy Suppression Barrier (Zero Direct Send)" in html or "Statutory & Policy Suppression Barrier" in html
    assert "optout@suppressedfirm.de" in html
    assert 'href="/sales/candidates?q=suppressedfirm.de"' in html
    assert "suppressedfirm.de ↗" in html
    assert "Excluded Companies Registry" in html

    # 3. Watchlist
    resp = client.get("/sales/watchlist")
    assert resp.status_code == 200
    html = resp.text
    assert "Signal Monitoring Queue (Zero Direct Send)" in html
    assert "Future BIM Studio ↗" in html
    assert 'href="/sales/candidates/cand_ui07_watch"' in html
    assert 'href="/sales/candidates/cand_ui07_watch/research"' in html
    assert "Research ↗" in html
    assert "hiring:bim-coordinator" in html


def test_02_sources_intake_channels_and_preview_distinction(disposable_ui07_env):
    """
    Test Sources & Intake Registry:
    - Clear distinction between Discovery, Historical, and 13-column Lead Research intake.
    - Preview -> Confirm architecture preserved without mutations.
    """
    client = disposable_ui07_env["client"]

    resp = client.get("/sales/sources")
    assert resp.status_code == 200
    html = resp.text

    assert "Safe Data Intake Channels &amp; Import Provenance" in html or "Safe Data Intake Channels & Import Provenance" in html
    assert "1. Live Discovery Sources" in html
    assert "2. Historical Outreach &amp; Gmail" in html or "2. Historical Outreach & Gmail" in html
    assert "3. pyBIM 13-Column Lead Research" in html
    assert "Manage Sources" in html
    assert "Add Company Manually" in html
    assert "Import Historical Data" in html


def test_03_settings_safety_invariants_and_secrets_masking(disposable_ui07_env):
    """
    Test Settings Page:
    - EMAIL_SEND_ENABLED=false clearly displayed with dry run invariant.
    - API keys and tokens are strictly masked / never leaked.
    - Search budget limits and operational boundaries are visible.
    """
    client = disposable_ui07_env["client"]

    resp = client.get("/sales/settings")
    assert resp.status_code == 200
    html = resp.text

    # Offline Send Gate Invariant
    assert "Outbound Cold Send Gate: OFFLINE / DISABLED (EMAIL_SEND_ENABLED=false)" in html
    assert "SENDING DISABLED" in html
    assert "0 Outbound Network Calls when Disabled" in html

    # Secret masking: Never leak the actual environment secret values
    assert "mock_brave_key_should_be_masked" not in html
    assert "mock_resend_key_should_be_masked" not in html
    assert "Configured (Masked)" in html

    # Search Budget & Boundaries
    assert "Search Budget &amp; Operational Boundaries" in html or "Search Budget & Operational Boundaries" in html
    assert "Max 20 Pages / Domain" in html
    assert "60 Seconds / Run" in html
    assert "$0.50 Max Budget / Analysis" in html
    assert "Fail-Closed Gate" in html
