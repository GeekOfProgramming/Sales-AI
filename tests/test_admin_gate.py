"""Unit tests for Admin-Gate Ingestion Workflow & JWT Authentication Layer.
Verifies security boundary: prevents Data Poisoning by requiring JWT for approvals
and isolates unverified documentation submissions in the pending queue.
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.auth import ADMIN_USERNAME, ADMIN_PASSWORD
from ai_engine.ingest_queue import ingest_queue

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_queue():
    """Ensure clean slate before tests."""
    yield


def test_public_user_submission_creates_pending_request():
    """Test that a public user submitting a URL only creates a pending queue item, not immediate injection."""
    resp = client.post(
        "/api/ingest",
        json={
            "url": "https://docs.pyrevitlabs.io/sample-docs/",
            "slug": "sample-docs-test",
            "submitter": "Revit User Laptop 2",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending"
    assert data["current_state"] == "pending"
    assert "request_id" in data
    assert data["request_id"].startswith("req_")

    # Verify public tracking endpoint
    track_resp = client.get(f"/api/ingest/status/{data['request_id']}")
    assert track_resp.status_code == 200
    track_data = track_resp.json()
    assert track_data["status"] == "pending"
    assert track_data["url"] == "https://docs.pyrevitlabs.io/sample-docs/"
    assert track_data["submitter"] == "Revit User Laptop 2"


def test_unauthenticated_queue_access_blocked():
    """Verify that accessing the knowledge queue without a valid JWT token fails with 401 Unauthorized."""
    # List queue
    resp = client.get("/api/ingest/queue")
    assert resp.status_code == 401

    # Approve without token
    resp_approve = client.post(
        "/api/ingest/approve",
        json={"request_id": "req_dummy123"},
    )
    assert resp_approve.status_code == 401

    # Reject without token
    resp_reject = client.post(
        "/api/ingest/reject",
        json={"request_id": "req_dummy123"},
    )
    assert resp_reject.status_code == 401


def test_admin_authentication_and_rejection_flow():
    """Test full cycle: Login -> Fetch Queue -> Reject Unwanted / Poisonous Submission."""
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
    headers = {"Authorization": f"Bearer {token}"}

    # 3. User submits a suspicious link
    submit_resp = client.post(
        "/api/ingest",
        json={"url": "https://unverified-blog.com/bad-script", "submitter": "Untrusted User"},
    )
    req_id = submit_resp.json()["request_id"]

    # 4. Admin lists the queue
    queue_resp = client.get("/api/ingest/queue", headers=headers)
    assert queue_resp.status_code == 200
    items = queue_resp.json()
    assert any(it["request_id"] == req_id for it in items)

    # 5. Admin rejects the suspicious submission
    reject_resp = client.post(
        "/api/ingest/reject",
        headers=headers,
        json={"request_id": req_id, "reason": "Failed ISO compliance check."},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # 6. Public user queries tracking and sees rejected status
    track_resp = client.get(f"/api/ingest/status/{req_id}")
    assert track_resp.status_code == 200
    assert track_resp.json()["status"] == "rejected"
    assert "Failed ISO compliance" in track_resp.json()["message"]


def test_admin_approval_triggers_processing():
    """Test that Admin approval accepts the item and marks it as processing."""
    # 1. Login
    login_resp = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Submit link
    sub_resp = client.post(
        "/api/ingest",
        json={"url": "https://docs.pyrevitlabs.io/official-api", "submitter": "BIM Manager"},
    )
    req_id = sub_resp.json()["request_id"]

    # 3. Approve
    approve_resp = client.post(
        "/api/ingest/approve",
        headers=headers,
        json={"request_id": req_id},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "processing"


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

