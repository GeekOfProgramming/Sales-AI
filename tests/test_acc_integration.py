"""Unit tests for Autodesk Construction Cloud (ACC) Integration & Local RAG Auditing.
Verifies read-only metadata inspection, ISO 19650 compliance checking,
and Human-in-the-Loop protection gates against unverified cloud write-back.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import ADMIN_USERNAME, ADMIN_PASSWORD

client = TestClient(app)


def test_acc_hubs_and_projects_discovery():
    """Verify listing of corporate hubs and projects via Data Management API."""
    # 1. Fetch Hubs
    resp_hubs = client.get("/api/acc/hubs")
    assert resp_hubs.status_code == 200
    hubs = resp_hubs.json()
    assert len(hubs) > 0
    hub_id = hubs[0]["hub_id"]
    assert "hub" in hub_id.lower()

    # 2. Fetch Projects for Hub
    resp_projects = client.get(f"/api/acc/projects/{hub_id}")
    assert resp_projects.status_code == 200
    projects = resp_projects.json()
    assert len(projects) > 0
    project_id = projects[0]["project_id"]

    # 3. Fetch Models for Project
    resp_models = client.get(f"/api/acc/models/{project_id}")
    assert resp_models.status_code == 200
    models = resp_models.json()
    assert len(models) > 0
    assert any(m["name"].endswith(".rvt") for m in models)


def test_cloud_model_read_only_rag_audit():
    """Verify that auditing an ACC cloud model performs read-only checks without modifying the project."""
    test_urn = "dXJuOmFkc2sub2JqZWN0czpvcy5vYmplY3Q6bW9kZWxfYXJjXzAwMQ=="
    
    audit_resp = client.post(
        "/api/acc/audit",
        json={"urn": test_urn, "project_id": "b.proj_teh_tower_01"},
    )
    assert audit_resp.status_code == 200
    data = audit_resp.json()

    # Verify audit safety guarantees
    assert data["audit_mode"] == "READ_ONLY"
    assert data["requires_human_approval"] is True
    assert "compliance_score" in data
    assert data["total_checks_evaluated"] > 0

    # Verify issue detection (e.g. missing FireRating on element 105235)
    issues = data["issues"]
    assert any(iss["parameter"] == "FireRating" for iss in issues)
    assert any(iss["issue_type"] == "Missing Mandatory Parameter" for iss in issues)


def test_human_in_the_loop_write_back_protection():
    """Verify that applying parameter updates to the cloud requires explicit admin authentication."""
    test_urn = "dXJuOmFkc2sub2JqZWN0czpvcy5vYmplY3Q6bW9kZWxfYXJjXzAwMQ=="
    payload = {
        "urn": test_urn,
        "approved_elements": [105235, 204102],
        "reviewer_notes": "BIM Manager approved FireRating update for walls.",
    }

    # 1. Unauthenticated attempt -> Must fail with 401
    unauth_resp = client.post("/api/acc/apply-changes", json=payload)
    assert unauth_resp.status_code == 401

    # 2. Authenticate as Admin
    login_resp = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Authorized attempt with Admin Token -> Must succeed
    auth_resp = client.post("/api/acc/apply-changes", headers=headers, json=payload)
    assert auth_resp.status_code == 200
    result = auth_resp.json()
    assert result["status"] == "authorized"
    assert result["approved_elements_count"] == 2
    assert result["authorized_by"] == ADMIN_USERNAME


def test_acc_config_status_public_query():
    """Verify that clients can inspect ACC operational status (live vs simulation) without token."""
    resp = client.get("/api/acc/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "mode_description" in data
    assert "is_live" in data
    assert isinstance(data["is_live"], bool)


def test_acc_config_save_requires_admin_token():
    """Verify that unauthenticated users cannot tamper with cloud credentials."""
    unauth_resp = client.post(
        "/api/acc/config",
        json={"client_id": "test_id", "client_secret": "test_secret"},
    )
    assert unauth_resp.status_code == 401


def test_acc_config_lifecycle_with_admin():
    """Verify that administrators can configure keys and switch between Live and Mock seamlessly."""
    # 1. Login
    login_resp = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Update credentials with skip_verification=True (sandbox test keys)
    save_resp = client.post(
        "/api/acc/config",
        headers=headers,
        json={
            "client_id": "aps_demo_client_id_12345",
            "client_secret": "aps_demo_client_secret_67890",
            "skip_verification": True,
        },
    )
    assert save_resp.status_code == 200
    save_data = save_resp.json()
    assert save_data["status"] == "CONNECTED_LIVE"
    assert save_data["is_live"] is True
    assert "aps_" in save_data["client_id_masked"]

    # 3. Query status endpoint and check masked output
    status_resp = client.get("/api/acc/config")
    assert status_resp.status_code == 200
    assert status_resp.json()["is_live"] is True

    # 4. Revert to simulation mode
    revert_resp = client.post(
        "/api/acc/config",
        headers=headers,
        json={"force_mock": True},
    )
    assert revert_resp.status_code == 200
    revert_data = revert_resp.json()
    assert revert_data["status"] == "SIMULATED_MOCK"
    assert revert_data["is_live"] is False
