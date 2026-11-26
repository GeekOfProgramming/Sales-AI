"""Unit tests for Admin JWT Authentication Layer and Legacy Ingestion Route Retirement.
Preserves active `/api/auth/login` and `verify_admin_token` security tests with
isolated temporary database paths and explicit synthetic configuration, while
confirming the six retired `/api/ingest*` endpoints are no longer registered.
"""

from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import backend.operator_auth as operator_auth_mod
from backend.auth import verify_admin_token
from backend.main import app

SYNTHETIC_ADMIN_USER = "synth_gate_admin"
SYNTHETIC_ADMIN_PASS = "SynthGate-Pass-9x4K!2026"
SYNTHETIC_SECRET_KEY = "synth-gate-jwt-signing-key-94a7c2e1f8b305d61234abcd"


def test_retired_legacy_ingestion_routes_not_registered():
    """Confirm the six retired `/api/ingest*` endpoints are no longer registered on `app.routes`."""
    registered_paths = {getattr(route, "path", "") for route in app.routes}
    for retired in (
        "/api/ingest",
        "/api/ingest/queue",
        "/api/ingest/approve",
        "/api/ingest/reject",
        "/api/ingest/queue/clear-history",
        "/api/ingest/status/{request_id}",
    ):
        assert retired not in registered_paths


def test_admin_authentication_and_jwt_verification_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify `/api/auth/login` fails closed when unconfigured and issues/verifies JWT when explicitly configured."""
    isolated_db = str(tmp_path / "admin_gate_isolated.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", isolated_db)
    monkeypatch.setenv("SALESAI_DB_PATH", isolated_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", isolated_db)
    for var in (
        "PYBIM_SECRET_KEY",
        "PYBIM_TOKEN_EXPIRE_MINUTES",
        "PYBIM_ADMIN_USER",
        "PYBIM_ADMIN_PASSWORD",
        "PYBIM_ALLOW_LEGACY_ADMIN",
    ):
        monkeypatch.delenv(var, raising=False)

    orig_service = operator_auth_mod._auth_service
    operator_auth_mod._auth_service = None
    try:
        client = TestClient(app)

        # 0. Fail closed when legacy admin and signing key are unconfigured
        unconfigured_login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong_password_xyz"},
        )
        assert unconfigured_login.status_code == 401

        # Configure explicit synthetic legacy admin credentials and signing key
        monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTHETIC_SECRET_KEY)
        monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", "true")
        monkeypatch.setenv("PYBIM_ADMIN_USER", SYNTHETIC_ADMIN_USER)
        monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTHETIC_ADMIN_PASS)

        # 1. Failed login with wrong password
        bad_login = client.post(
            "/api/auth/login",
            json={"username": SYNTHETIC_ADMIN_USER, "password": "wrong_password_xyz"},
        )
        assert bad_login.status_code == 401

        # 2. Successful admin login with explicit synthetic credentials
        good_login = client.post(
            "/api/auth/login",
            json={"username": SYNTHETIC_ADMIN_USER, "password": SYNTHETIC_ADMIN_PASS},
        )
        assert good_login.status_code == 200
        auth_data = good_login.json()
        assert "access_token" in auth_data
        assert auth_data["role"] == "admin"
        token = auth_data["access_token"]

        # 3. Missing or invalid bearer token rejected by verify_admin_token
        with pytest.raises(HTTPException) as exc_missing:
            verify_admin_token(credentials=None)
        assert exc_missing.value.status_code == 401

        with pytest.raises(HTTPException) as exc_invalid:
            verify_admin_token(
                credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-valid-jwt")
            )
        assert exc_invalid.value.status_code == 401

        # 4. Valid admin token decoded cleanly
        payload = verify_admin_token(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
        assert payload["sub"] == SYNTHETIC_ADMIN_USER
        assert payload["role"] == "admin"
    finally:
        operator_auth_mod._auth_service = orig_service
