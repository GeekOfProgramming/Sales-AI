"""
tests/test_manual_seed_hotfix.py
Focused regression test suite for manual candidate seed workflow (HOTFIX).

Verifies:
1. New manual candidate seed returns HTTP 303 Redirect to candidate detail page instead of HTTP 500.
2. Candidate is correctly persisted with company_name, domain, country, etc.
3. Existing candidate with the same domain is safely deduplicated (returns 303 to existing candidate).
4. No duplicate candidate records are created on repeated submissions.
5. No loss of existing review (13-field), research, or outreach status when re-seeding an existing candidate.
6. SEC-01 Authentication and CSRF protection are preserved on the manual-seed route.
"""

import os
import json
import pytest
from starlette.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany, CompanyTier, BIMMaturity
from sales_engine.sending.review_store import ReviewStore
from backend.operator_auth import OperatorAuthService
from backend.main import app


@pytest.fixture
def disposable_seed_env(tmp_path, monkeypatch):
    """Sets up an isolated database for manual seed regression testing."""
    test_db = str(tmp_path / "test_manual_seed.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)

    store = CandidateStore(db_path=test_db)
    rev_store = ReviewStore(db_path=test_db)
    auth_svc = OperatorAuthService(db_path=test_db)

    # Set up operators for SEC-01
    auth_svc.create_user("operator_bob", "Password123!", role="operator")
    auth_svc.create_user("admin_alice", "Password123!", role="admin")

    # Wire up services for router
    from backend.sales_ui import router as ui_router
    def fake_services():
        return {
            "cand_store": store,
            "rev_store": rev_store,
        }
    monkeypatch.setattr(ui_router, "get_console_services", fake_services)
    monkeypatch.setattr(ui_router, "get_auth_service", lambda: auth_svc)
    monkeypatch.setattr("backend.operator_auth.get_auth_service", lambda: auth_svc)

    user = auth_svc.authenticate_user("operator_bob", "Password123!", client_ip="127.0.0.1")
    session_token = auth_svc.create_session(user.user_id, client_ip="127.0.0.1")

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    client.cookies.set("sales_session", session_token)
    csrf_token = "valid_csrf_token_seed_test"
    client.cookies.set("sales_csrf_token", csrf_token)

    return {
        "store": store,
        "client": client,
        "csrf_token": csrf_token,
    }


def test_new_manual_candidate_seed_redirects_303(disposable_seed_env):
    """
    Test 1: Adding a new candidate seed (e.g. Piuarch) succeeds with HTTP 303
    and persists the candidate correctly without throwing HTTP 500.
    """
    env = disposable_seed_env
    client = env["client"]
    store = env["store"]
    csrf = env["csrf_token"]

    resp = client.post(
        "/sales/sources/manual-seed",
        data={
            "company_name": "Piuarch",
            "domain": "piuarch.it",
            "country": "Italy",
            "source_url": "https://piuarch.it/",
            "csrf_token": csrf,
        },
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )

    # Must be 303 redirect, NOT 500
    assert resp.status_code == 303
    loc = resp.headers["Location"]
    assert "/sales/candidates/cand:domain:piuarch.it" in loc
    assert "Piuarch" in loc and ("enqueued" in loc or "saved" in loc)

    # Verify persisted in database
    cand = store.get_candidate("cand:domain:piuarch.it")
    assert cand is not None
    assert cand.company_name == "Piuarch"
    assert cand.company_domain == "piuarch.it"
    assert cand.country == "Italy"
    assert cand.discovery_status.value == "new"


def test_existing_candidate_same_domain_deduplication(disposable_seed_env):
    """
    Test 2 & 3: Re-seeding an existing candidate with the same domain:
    - Returns HTTP 303 to the existing candidate.
    - No duplicate records created in candidate_companies.
    - Preserves existing researched, reviewed, or previously contacted status without data loss.
    """
    env = disposable_seed_env
    client = env["client"]
    store = env["store"]
    csrf = env["csrf_token"]

    # Pre-populate candidate with established research, review, and contact status
    existing_cand = CandidateCompany(
        candidate_id="cand:domain:piuarch.it",
        company_name="Piuarch Studio",
        company_domain="piuarch.it",
        country="Italy",
        city="Milan",
        company_tier=CompanyTier.tier_1,
        bim_maturity=BIMMaturity.high,
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
        manual_review_decision="APPROVED",
        manual_reviewed_by="admin_operator",
        manual_review_notes="Verified tier 1 architecture studio",
        last_researched_at="2026-11-01T12:00:00Z",
    )
    store.save_candidate(existing_cand)

    # Post manual seed for the same domain
    resp = client.post(
        "/sales/sources/manual-seed",
        data={
            "company_name": "Piuarch",
            "domain": "piuarch.it",
            "country": "IT",
            "csrf_token": csrf,
        },
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    loc = resp.headers["Location"]
    assert "/sales/candidates/cand:domain:piuarch.it" in loc
    assert "already%20exists" in loc or "already+exists" in loc or "already exists" in loc

    # Check database: exactly ONE record exists for piuarch
    with store._get_connection() as conn:
        count = conn.execute("SELECT count(*) FROM candidate_companies WHERE company_domain = 'piuarch.it'").fetchone()[0]
        assert count == 1, f"Expected 1 record, found {count}"

    # Verify no loss of existing review, research, or outreach status
    c = store.get_candidate("cand:domain:piuarch.it")
    assert c.company_name == "Piuarch Studio"
    assert c.company_tier == CompanyTier.tier_1
    assert c.bim_maturity == BIMMaturity.high
    assert c.historical_contact_status == "contacted"
    assert c.cold_outreach_allowed is False
    assert c.manual_review_decision == "APPROVED"
    assert c.manual_reviewed_by == "admin_operator"
    assert c.last_researched_at == "2026-11-01T12:00:00Z"


def test_manual_seed_csrf_and_auth_protection(disposable_seed_env):
    """
    Test 4: CSRF mismatch is rejected with HTTP 403; unauthenticated is rejected.
    """
    env = disposable_seed_env
    client = env["client"]

    # Invalid CSRF
    bad_csrf_resp = client.post(
        "/sales/sources/manual-seed",
        data={
            "company_name": "Test Co",
            "domain": "testco.com",
            "csrf_token": "wrong_csrf",
        },
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )
    assert bad_csrf_resp.status_code == 403

    # Unauthenticated client
    unauth_client = TestClient(app, base_url="http://127.0.0.1:8000")
    unauth_resp = unauth_client.post(
        "/sales/sources/manual-seed",
        data={
            "company_name": "Test Co",
            "domain": "testco.com",
            "csrf_token": "valid_token",
        },
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )
    assert unauth_resp.status_code in (401, 303)
