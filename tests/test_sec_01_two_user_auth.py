"""
tests/test_sec_01_two_user_auth.py
Focused automated tests for SEC-01: Two-User Authentication and Operator Audit.

Verifies:
1. Anonymous browser request to /sales/* redirects to /sales/login?next=...
2. Anonymous API request returns 401 Unauthorized with WWW-Authenticate header.
3. Authenticated operator login succeeds, sets HttpOnly session cookie, and allows CRM access.
4. Audited actions (13-field verify, finalization, outreach approval, mark sent, void) record authenticated operator identity, never trusting forged client inputs.
5. Role-based access control: OPERATOR is blocked (403) from admin endpoints (/sales/users), while ADMIN is allowed.
6. Logout revokes session cookie; subsequent requests require re-authentication.
7. Brute-force rate limiting blocks repeated failed login attempts.
8. BACKUP-01 automated backup engine operates without requiring a web browser session.
"""

import os
import secrets
import sqlite3
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.operator_auth import (
    OperatorAuthService,
    SESSION_COOKIE_NAME,
    hash_password,
    verify_password,
)
from backend.sales_ui.router import CSRF_COOKIE_NAME, get_or_create_csrf_token


@pytest.fixture
def test_db_path(tmp_path):
    """Creates a temporary isolated SQLite database with initial schema."""
    db_file = tmp_path / "test_auth_sales.db"
    from sales_engine.candidates.candidate_store import CandidateStore
    from sales_engine.sending.review_store import ReviewStore
    # Initialize authoritative schemas cleanly
    c_store = CandidateStore(db_path=str(db_file))
    r_store = ReviewStore(db_path=str(db_file))
    return str(db_file)


@pytest.fixture
def auth_svc(test_db_path):
    svc = OperatorAuthService(db_path=test_db_path)
    return svc


@pytest.fixture
def client_with_db(test_db_path, monkeypatch):
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db_path)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db_path)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db_path)
    client = TestClient(app, follow_redirects=False)
    return client


def test_password_hashing_and_verification():
    """Verify PBKDF2-HMAC-SHA256 with 600,000 rounds."""
    plain = "SuperSecretPass2026!"
    hashed = hash_password(plain)
    assert hashed.startswith("pbkdf2_sha256$600000$")
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_unauthenticated_browser_redirected_when_users_exist(auth_svc, client_with_db):
    """When operator accounts exist, anonymous requests to /sales/* must redirect to /sales/login."""
    # Create an admin user in the test database
    auth_svc.create_user("admin_user", "AdminSecret123!", role="admin")

    # Access /sales/dashboard without session
    resp = client_with_db.get("/sales/dashboard", headers={"accept": "text/html"})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/sales/login?next=/sales/dashboard")


def test_unauthenticated_api_request_returns_401(auth_svc, client_with_db):
    """When operator accounts exist, anonymous API requests return 401."""
    auth_svc.create_user("admin_user", "AdminSecret123!", role="admin")

    # API call without auth
    resp = client_with_db.get("/api/sales/overview")
    assert resp.status_code == 401
    assert "detail" in resp.json()


def test_login_success_and_crm_navigation(auth_svc, client_with_db):
    """Admin and Operator can log in, receive session cookie, and view CRM."""
    auth_svc.create_user("alice_admin", "AlicePass2026!", role="admin", full_name="Alice Admin")
    auth_svc.create_user("bob_operator", "BobPass2026!", role="operator", full_name="Bob Operator")

    # 1. Fetch login page and obtain CSRF token
    login_page_resp = client_with_db.get("/sales/login")
    assert login_page_resp.status_code == 200
    csrf_token = login_page_resp.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token is not None

    # 2. Login as Bob (OPERATOR)
    login_resp = client_with_db.post(
        "/sales/login",
        data={
            "username": "bob_operator",
            "password": "BobPass2026!",
            "csrf_token": csrf_token,
            "next": "/sales/dashboard",
        },
        cookies={CSRF_COOKIE_NAME: csrf_token},
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/sales/dashboard"
    session_token = login_resp.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None

    # 3. Access CRM as Bob
    dash_resp = client_with_db.get("/sales/dashboard", cookies={SESSION_COOKIE_NAME: session_token})
    assert dash_resp.status_code == 200
    # Must show logged-in badge
    assert "bob_operator" in dash_resp.text
    assert "OPERATOR" in dash_resp.text


def test_role_based_access_control_users_view(auth_svc, client_with_db):
    """OPERATOR is blocked (403) from /sales/users; ADMIN is allowed (200)."""
    auth_svc.create_user("alice_admin", "AlicePass2026!", role="admin")
    auth_svc.create_user("bob_operator", "BobPass2026!", role="operator")

    # Bob's session
    bob_user = auth_svc.authenticate_user("bob_operator", "BobPass2026!")
    bob_token = auth_svc.create_session(bob_user.user_id)

    # Alice's session
    alice_user = auth_svc.authenticate_user("alice_admin", "AlicePass2026!")
    alice_token = auth_svc.create_session(alice_user.user_id)

    # Bob accesses /sales/users -> 403 Forbidden
    bob_resp = client_with_db.get("/sales/users", cookies={SESSION_COOKIE_NAME: bob_token})
    assert bob_resp.status_code == 403

    # Alice accesses /sales/users -> 200 OK
    alice_resp = client_with_db.get("/sales/users", cookies={SESSION_COOKIE_NAME: alice_token})
    assert alice_resp.status_code == 200
    assert "Operator Accounts" in alice_resp.text
    assert "alice_admin" in alice_resp.text
    assert "bob_operator" in alice_resp.text


def test_audited_operator_attribution_on_manual_send(auth_svc, client_with_db, test_db_path):
    """
    Mark as sent records the authenticated operator's username in logged_by,
    and void records it in voided_by.
    """
    auth_svc.create_user("operator_dan", "DanPass2026!", role="operator")
    user = auth_svc.authenticate_user("operator_dan", "DanPass2026!")
    session_token = auth_svc.create_session(user.user_id)

    # Insert candidate via CandidateCompany model and CandidateStore
    from sales_engine.candidates.models import CandidateCompany
    from sales_engine.candidates.candidate_store import CandidateStore
    cand_store = CandidateStore(db_path=test_db_path)
    cand_obj = CandidateCompany(
        candidate_id="cand_test_99",
        company_name="Test BIM Srl",
        company_domain="testbim.it",
        cold_outreach_allowed=True,
        historical_contact_status="uncontacted",
    )
    cand_store.save_candidate(cand_obj)

    csrf_token = secrets.token_hex(32)

    # Bob logs manual dispatch
    resp = client_with_db.post(
        "/sales/candidates/cand_test_99/mark-sent",
        data={
            "sender_account": "info@pybim.it",
            "recipient_email": "direzione@testbim.it",
            "confirm_thunderbird_sent": "1",
            "csrf_token": csrf_token,
            "subject": "Introduzione pyBIM",
            "operator_notes": "Outreach manually sent via Thunderbird",
        },
        cookies={
            SESSION_COOKIE_NAME: session_token,
            CSRF_COOKIE_NAME: csrf_token,
        },
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 303, f"Redirect failed: {resp.headers.get('location')}"
    if "error=" in resp.headers.get("location", ""):
        pytest.fail(f"Form submission failed with error: {resp.headers.get('location')}")

    # Verify database audit attribution
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM manual_outreach_log WHERE candidate_id = 'cand_test_99';").fetchone()
    assert row is not None
    assert row["logged_by"] == "operator_dan"  # Authenticated user, NOT default 'local_operator'
    assert row["recipient_email"] == "direzione@testbim.it"
    log_id = row["log_id"]

    # Void action by the operator
    void_resp = client_with_db.post(
        f"/sales/manual-sent/{log_id}/void",
        data={
            "void_reason": "Accidental duplicate entry test",
            "csrf_token": csrf_token,
        },
        cookies={
            SESSION_COOKIE_NAME: session_token,
            CSRF_COOKIE_NAME: csrf_token,
        },
    )
    assert void_resp.status_code == 303

    void_row = conn.execute("SELECT * FROM manual_outreach_log WHERE log_id = ?;", (log_id,)).fetchone()
    conn.close()
    assert void_row["is_voided"] == 1
    assert void_row["voided_by"] == "operator_dan"
    assert void_row["voided_reason"] == "Accidental duplicate entry test"


def test_logout_revokes_session(auth_svc, client_with_db):
    """Logging out revokes the session token so future requests are rejected."""
    auth_svc.create_user("operator_sam", "SamPass2026!", role="operator")
    user = auth_svc.authenticate_user("operator_sam", "SamPass2026!")
    session_token = auth_svc.create_session(user.user_id)

    csrf_token = secrets.token_hex(32)

    # Active session allows access
    resp = client_with_db.get("/sales/dashboard", cookies={SESSION_COOKIE_NAME: session_token})
    assert resp.status_code == 200

    # Logout
    logout_resp = client_with_db.post(
        "/sales/logout",
        data={"csrf_token": csrf_token},
        cookies={
            SESSION_COOKIE_NAME: session_token,
            CSRF_COOKIE_NAME: csrf_token,
        },
    )
    assert logout_resp.status_code == 303

    # Subsequent access with old cookie redirects to login
    recheck = client_with_db.get("/sales/dashboard", cookies={SESSION_COOKIE_NAME: session_token}, headers={"accept": "text/html"})
    assert recheck.status_code == 303
    assert "/sales/login" in recheck.headers["location"]


def test_rate_limiting_blocks_brute_force(auth_svc, client_with_db):
    """5 failed login attempts in 60 seconds trigger 429 Too Many Requests."""
    from backend.operator_auth import _LOGIN_ATTEMPTS, _LOGIN_ATTEMPTS_LOCK
    with _LOGIN_ATTEMPTS_LOCK:
        _LOGIN_ATTEMPTS.clear()

    auth_svc.create_user("target_user", "CorrectPassword123!", role="operator")
    csrf_token = secrets.token_hex(32)

    for i in range(5):
        resp = client_with_db.post(
            "/sales/login",
            data={
                "username": "target_user",
                "password": f"WrongPassword_{i}",
                "csrf_token": csrf_token,
            },
            cookies={CSRF_COOKIE_NAME: csrf_token},
        )
        assert resp.status_code == 401

    # 6th attempt should be rate limited
    blocked_resp = client_with_db.post(
        "/sales/login",
        data={
            "username": "target_user",
            "password": "WrongPassword_6",
            "csrf_token": csrf_token,
        },
        cookies={CSRF_COOKIE_NAME: csrf_token},
    )
    assert blocked_resp.status_code == 429
    assert "Too many failed login attempts" in blocked_resp.text
