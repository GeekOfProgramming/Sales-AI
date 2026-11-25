"""Unit tests for Admin JWT Authentication Layer and Legacy Ingestion Route Retirement.
Preserves active `/api/auth/login` and `verify_admin_token` security tests while
confirming the six retired `/api/ingest*` endpoints are no longer registered.
"""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import ADMIN_USERNAME, ADMIN_PASSWORD, verify_admin_token

client = TestClient(app)


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


def test_admin_authentication_and_jwt_verification_flow():
    """Verify `/api/auth/login` rejects invalid credentials, issues admin JWT, and `verify_admin_token` enforces it."""
    # 1. Failed login with wrong password
    bad_login = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": "wrong_password_xyz"},
    )
    assert bad_login.status_code == 401

    # 2. Successful admin login
    good_login = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
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
    assert payload["sub"] == ADMIN_USERNAME
    assert payload["role"] == "admin"


def test_smart_slug_generation_and_guid_upgrade():
    """Verify smart slug derivation, prefixing, GUID detection, and normalization."""
    from ai_engine.data_ingestor import BIMDataIngestor

    # 1. No slug provided - derives from title and revitapidocs domain
    slug, note = BIMDataIngestor.generate_smart_slug(
        url="https://www.revitapidocs.com/2027/94db8ea8-d2c3-5e71-8030-466bcb8e4426.htm",
        title="Application Class",
    )
    assert slug == "revit_api_application_class"
    assert note is None

    # 2. Raw GUID provided by user - detected and upgraded with friendly feedback
    slug_guid, note_guid = BIMDataIngestor.generate_smart_slug(
        url="https://www.revitapidocs.com/2027/94db8ea8-d2c3-5e71-8030-466bcb8e4426.htm",
        title="Application Class",
        user_slug="94db8ea8-d2c3-5e71-8030-466bcb8e4426.htm",
    )
    assert slug_guid == "revit_api_application_class"
    assert note_guid is not None
    assert "unstandardized/generic" in note_guid

    # 3. pyRevit domain prefixing
    slug_py, _ = BIMDataIngestor.generate_smart_slug(
        url="https://docs.pyrevitlabs.io/pyrevit/forms/",
        title="Forms UI Module",
    )
    assert slug_py.startswith("pyrevit_")

    # 4. Normalization of user slug with spaces
    slug_norm, note_norm = BIMDataIngestor.generate_smart_slug(
        url="https://revitapidocs.com/sample",
        title="Sample",
        user_slug="my custom collector",
    )
    assert slug_norm == "my_custom_collector"
    assert note_norm is not None
    assert "Normalized" in note_norm
