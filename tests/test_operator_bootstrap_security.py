"""Isolated security regression tests for ARCH-01C2: Fail-Closed Operator Bootstrap Security.

Covers both:
1. Real FastAPI HTTP TestClient flows against an isolated temporary SQLite database.
2. Direct middleware sentinel-handler tests proving denied requests never execute
   downstream route handlers or business side effects.

Zero operational database access, zero real credentials, zero live provider or SMTP calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import backend.auth as auth_mod
import backend.main as main_mod
import backend.operator_auth as operator_auth_mod
from backend.operator_auth import OperatorAuthService, SESSION_COOKIE_NAME
from backend.sales_ui.router import CSRF_COOKIE_NAME
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.llm_client import SalesLLMClient
from sales_engine.sending.review_store import ReviewStore
from tests.support.isolated_subprocess import run_isolated_subprocess

SYNTH_JWT_SECRET = "synth-bootstrap-jwt-key-9f4e2a7c1b8d6035ab12cd34ef5678901234abcd"

_AUTH_ENV_KEYS = (
    "PYBIM_SECRET_KEY",
    "PYBIM_TOKEN_EXPIRE_MINUTES",
    "PYBIM_ADMIN_USER",
    "PYBIM_ADMIN_PASSWORD",
    "PYBIM_ALLOW_LEGACY_ADMIN",
)


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, TestClient]:
    """Create a fresh isolated SQLite DB with schemas (zero operator users) and TestClient."""
    db_file = tmp_path / "bootstrap_isolated.db"
    CandidateStore(db_path=str(db_file))
    ReviewStore(db_path=str(db_file))
    OperatorAuthService(db_path=str(db_file))

    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("SALESAI_DB_PATH", str(db_file))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(db_file))
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    orig_service = operator_auth_mod._auth_service
    operator_auth_mod._auth_service = None
    try:
        client = TestClient(main_mod.app, follow_redirects=False)
        yield db_file, client
    finally:
        operator_auth_mod._auth_service = orig_service


def _make_fake_request(
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    query: str = "",
) -> Any:
    return SimpleNamespace(
        url=SimpleNamespace(path=path, query=query),
        method=method,
        headers=headers or {"accept": "application/json"},
        cookies=cookies or {},
        state=SimpleNamespace(),
    )


def test_zero_user_state_denies_protected_http_routes_and_blocks_side_effects(
    isolated_env: tuple[Path, TestClient],
) -> None:
    """When zero operator users exist, browser, API GET, and API mutation routes return 503 with zero side effects."""
    db_file, client = isolated_env
    svc = operator_auth_mod.get_auth_service(db_path=db_file)
    assert svc.get_user_count() == 0

    # Side-effect sentinels on sending/review boundaries must never be called
    with (
        patch.object(main_mod, "SendOrchestrator", side_effect=AssertionError("SendOrchestrator must not execute")),
        patch.object(main_mod, "ReviewStore", side_effect=AssertionError("ReviewStore must not execute")),
        patch.object(main_mod, "SuppressionStore", side_effect=AssertionError("SuppressionStore must not execute")),
    ):
        # 1. Protected browser GET requests denied with 503
        for browser_path in ("/", "/ui", "/sales/dashboard", "/sales/mail-operations", "/sales/users"):
            resp = client.get(browser_path, headers={"accept": "text/html"})
            assert resp.status_code == 503, f"Expected 503 for {browser_path}, got {resp.status_code}"
            detail = resp.json()["detail"].lower()
            assert "not initialized" in detail or "setup" in detail

        # 2. Protected API GET requests denied with 503
        for api_get in ("/api/sales/overview", "/api/sales/drafts", "/api/sales/candidates", "/api/sales/suppression"):
            resp = client.get(api_get)
            assert resp.status_code == 503, f"Expected 503 for {api_get}, got {resp.status_code}"

        # 3. Protected API mutation requests denied with 503 (even with forged localhost/operator headers)
        for api_post in (
            "/api/sales/drafts/draft_1/send",
            "/api/sales/send-batch",
            "/api/sales/discovery/run",
            "/api/sales/suppression",
            "/api/sales/inbox/reply-suggestions/sug_1/send",
        ):
            resp = client.post(
                api_post,
                json={},
                headers={"X-Forwarded-For": "127.0.0.1", "X-Operator": "admin"},
            )
            assert resp.status_code == 503, f"Expected 503 for POST {api_post}, got {resp.status_code}"


def test_zero_user_state_preserves_public_login_health_and_static_contracts(
    isolated_env: tuple[Path, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In zero-user state, supported public routes remain reachable without exposing protected routes."""
    _, client = isolated_env

    # 1. /sales/login renders login page cleanly
    login_resp = client.get("/sales/login")
    assert login_resp.status_code == 200
    assert "Operator Login" in login_resp.text

    # 2. /sales/logout works with CSRF token
    csrf = login_resp.cookies.get(CSRF_COOKIE_NAME)
    assert csrf is not None
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    logout_resp = client.post("/sales/logout", data={"csrf_token": csrf})
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/sales/login"

    # 3. /api/auth/login reaches login handler (fails closed with 401 when unconfigured, not blocked by middleware)
    api_login_resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong_password"})
    assert api_login_resp.status_code == 401

    # 4. /health works with injected mocked LLM client (no live Ollama call)
    sync_mock = MagicMock()
    sync_mock.list.return_value = SimpleNamespace(models=[SimpleNamespace(model="qwen2.5:1.5b")])
    mock_llm = SalesLLMClient(default_model="qwen2.5-coder:1.5b", sync_client=sync_mock)
    monkeypatch.setattr(main_mod, "get_llm_client", lambda: mock_llm)
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "healthy"

    # 5. Static asset serving under /static/ remains compatible
    static_resp = client.get("/static/sales/sales.css")
    assert static_resp.status_code == 200


def test_provisioning_transition_via_cli_enables_operator_login_and_rbac(
    isolated_env: tuple[Path, TestClient],
    tmp_path: Path,
) -> None:
    """Transition from zero users -> CLI init-admin -> unauthenticated blocked -> authenticated & RBAC enforced."""
    db_file, client = isolated_env

    # 1. Before provisioning: protected routes return 503
    assert client.get("/sales/dashboard", headers={"accept": "text/html"}).status_code == 503
    assert client.get("/api/sales/overview").status_code == 503

    # 2. Provision synthetic administrator via real scripts/setup_operators.py CLI in isolated subprocess
    cli_driver = (
        "import builtins, getpass, os, sys; "
        "sys.path.insert(0, os.getcwd()); "
        "answers = iter(['cli_admin', 'CLI Admin']); "
        "builtins.input = lambda _p='': next(answers); "
        "getpass.getpass = lambda _p='': 'CliAdmin-Pass-2026!'; "
        "import scripts.setup_operators as cli; "
        "raise SystemExit(cli.cmd_init_admin())"
    )
    proc = run_isolated_subprocess(
        [sys.executable, "-B", "-c", cli_driver],
        isolated_db_path=db_file,
        trusted_dir=tmp_path,
        extra_env={k: "" for k in _AUTH_ENV_KEYS},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "created successfully" in proc.stdout

    # 3. After provisioning: unauthenticated requests remain blocked (303 browser / 401 API)
    anon_html = client.get("/sales/dashboard", headers={"accept": "text/html"})
    assert anon_html.status_code == 303
    assert anon_html.headers["location"].startswith("/sales/login?next=/sales/dashboard")
    assert client.get("/api/sales/overview").status_code == 401
    assert client.post("/api/sales/send-batch", json={}).status_code == 401

    # 4. Admin login via /sales/login succeeds and accesses admin-only /sales/users
    login_page = client.get("/sales/login")
    csrf = login_page.cookies.get(CSRF_COOKIE_NAME)
    assert csrf is not None
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    admin_login = client.post(
        "/sales/login",
        data={
            "username": "cli_admin",
            "password": "CliAdmin-Pass-2026!",
            "csrf_token": csrf,
            "next": "/sales/dashboard",
        },
    )
    assert admin_login.status_code == 303
    assert admin_login.headers["location"] == "/sales/dashboard"
    admin_session = admin_login.cookies.get(SESSION_COOKIE_NAME)
    assert admin_session is not None

    client.cookies.set(SESSION_COOKIE_NAME, admin_session)
    assert client.get("/sales/dashboard").status_code == 200
    users_resp = client.get("/sales/users")
    assert users_resp.status_code == 200
    assert "cli_admin" in users_resp.text

    # 5. Provision standard operator and verify RBAC boundary (dashboard 200, /sales/users 403)
    svc = operator_auth_mod.get_auth_service(db_path=db_file)
    svc.create_user("ops_user", "OpsUser-Pass-2026!", role="operator")
    op_client = TestClient(main_mod.app, follow_redirects=False)
    op_login_page = op_client.get("/sales/login")
    op_csrf = op_login_page.cookies.get(CSRF_COOKIE_NAME)
    assert op_csrf is not None
    op_client.cookies.set(CSRF_COOKIE_NAME, op_csrf)
    op_login = op_client.post(
        "/sales/login",
        data={
            "username": "ops_user",
            "password": "OpsUser-Pass-2026!",
            "csrf_token": op_csrf,
            "next": "/sales/dashboard",
        },
    )
    assert op_login.status_code == 303
    op_session = op_login.cookies.get(SESSION_COOKIE_NAME)
    assert op_session is not None
    op_client.cookies.set(SESSION_COOKIE_NAME, op_session)
    assert op_client.get("/sales/dashboard").status_code == 200
    assert op_client.get("/sales/users").status_code == 403


def test_middleware_failure_modes_expired_session_and_legacy_jwt_non_substitution(
    isolated_env: tuple[Path, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify DB/init failures, expired/invalid sessions, and legacy JWTs all fail closed without invoking handlers."""
    db_file, client = isolated_env
    sentinel_calls: list[str] = []

    async def sentinel_call_next(req: Any) -> Any:
        sentinel_calls.append(req.url.path)
        return SimpleNamespace(status_code=200)

    # 1. Auth service initialization failure -> 503, sentinel never called, no error detail leaked
    with patch("backend.operator_auth.get_auth_service", side_effect=RuntimeError("sensitive_db_path_failure")):
        resp = asyncio.run(
            main_mod.operator_security_middleware(_make_fake_request("/api/sales/overview"), sentinel_call_next)
        )
        assert resp.status_code == 503
        assert b"sensitive_db_path_failure" not in resp.body
        http_resp = client.get("/api/sales/overview")
        assert http_resp.status_code == 503
        assert "sensitive_db_path_failure" not in http_resp.text

    # 2. User-count lookup failure -> 503, sentinel never called, no sqlite error leaked
    broken_svc = SimpleNamespace(get_user_count=MagicMock(side_effect=sqlite3.OperationalError("database is locked")))
    with patch("backend.operator_auth.get_auth_service", return_value=broken_svc):
        resp = asyncio.run(
            main_mod.operator_security_middleware(_make_fake_request("/sales/dashboard"), sentinel_call_next)
        )
        assert resp.status_code == 503
        assert b"database is locked" not in resp.body
        http_resp = client.get("/api/sales/drafts")
        assert http_resp.status_code == 503
        assert "database is locked" not in http_resp.text

    # 3. Valid legacy JWT without operator session -> 503 in zero-user state
    monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTH_JWT_SECRET)
    legacy_jwt = auth_mod.create_access_token({"sub": "legacy_admin", "role": "admin"})
    jwt_headers = {"Authorization": f"Bearer {legacy_jwt}"}
    assert client.get("/api/sales/overview", headers=jwt_headers).status_code == 503
    assert sentinel_calls == []

    # 4. Provision user -> valid legacy JWT without operator session still rejected (401 API / 303 HTML)
    svc = operator_auth_mod.get_auth_service(db_path=db_file)
    admin_user = svc.create_user("jwt_test_admin", "JwtAdmin-Pass-2026!", role="admin")
    assert client.get("/api/sales/overview", headers=jwt_headers).status_code == 401
    assert client.get("/sales/dashboard", headers={**jwt_headers, "accept": "text/html"}).status_code == 303

    # 5. Invalid session token -> 401
    client.cookies.set(SESSION_COOKIE_NAME, "forged-invalid-session-token")
    assert client.get("/api/sales/overview").status_code == 401

    # 6. Expired session token -> 401
    valid_session = svc.create_session(admin_user.user_id)
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("UPDATE operator_sessions SET expires_at = ?;", (past_iso,))
        conn.commit()
    client.cookies.set(SESSION_COOKIE_NAME, valid_session)
    assert client.get("/api/sales/overview").status_code == 401
    assert sentinel_calls == []


def test_public_exemption_routing_boundaries_reject_lookalike_paths(
    isolated_env: tuple[Path, TestClient],
) -> None:
    """Only exact public endpoints and `/static/...` are exempt; similarly named paths must fail closed."""
    db_file, client = isolated_env
    sentinel_calls: list[str] = []

    async def sentinel_call_next(req: Any) -> Any:
        sentinel_calls.append(req.url.path)
        return SimpleNamespace(status_code=200)

    lookalike_paths = (
        "/static",
        "/static-secret",
        "/static_admin",
        "/statistics",
        "/api/auth/login-bypass",
        "/api/auth/login/extra",
        "/api/auth/login2",
        "/healthz",
        "/health/details",
        "/sales/login-extra",
        "/sales/logout-all",
    )

    # 1. Zero-user state: lookalike paths return 503 and never reach sentinel handler
    for path in lookalike_paths:
        mw_resp = asyncio.run(
            main_mod.operator_security_middleware(_make_fake_request(path), sentinel_call_next)
        )
        assert mw_resp.status_code == 503, f"Expected 503 for lookalike {path}, got {mw_resp.status_code}"
        assert client.get(path).status_code == 503
    assert sentinel_calls == []

    # 2. Provisioned state: lookalike paths return 401 (or 303 for /sales/* GET), never reaching sentinel handler
    svc = operator_auth_mod.get_auth_service(db_path=db_file)
    svc.create_user("boundary_admin", "Boundary-Pass-2026!", role="admin")
    for path in lookalike_paths:
        mw_resp = asyncio.run(
            main_mod.operator_security_middleware(_make_fake_request(path), sentinel_call_next)
        )
        expected_code = 303 if path.startswith("/sales") else 401
        assert mw_resp.status_code == expected_code, f"Expected {expected_code} for {path}, got {mw_resp.status_code}"
    assert sentinel_calls == []

    # 3. Exact public routes and /static/ assets pass through cleanly (preserving baseline current_user lookup)
    for pub_path in ("/health", "/sales/login", "/sales/logout", "/api/auth/login", "/static/sales/sales.css"):
        req = _make_fake_request(pub_path)
        mw_resp = asyncio.run(
            main_mod.operator_security_middleware(req, sentinel_call_next)
        )
        assert mw_resp.status_code == 200
        assert req.state.current_user is None
    assert sentinel_calls == ["/health", "/sales/login", "/sales/logout", "/api/auth/login", "/static/sales/sales.css"]

    # 4. Baseline public-route DB-dependency semantics preserved (does not silently change /health contract)
    with patch("backend.operator_auth.get_auth_service", side_effect=RuntimeError("simulated_db_error")):
        with pytest.raises(RuntimeError, match="simulated_db_error"):
            asyncio.run(
                main_mod.operator_security_middleware(_make_fake_request("/health"), sentinel_call_next)
            )
