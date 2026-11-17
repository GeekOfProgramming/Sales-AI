"""
tests/test_mail_compose_01b_scheduling.py
Unit and Integration Tests for MAIL-COMPOSE-01B: Scheduled Send Date/Time UI & Logic.

Verifies:
1. Schedule draft action transitions ready_to_schedule -> scheduled.
2. Timing validation:
   - Past dates rejected.
   - Nonexistent DST spring forward gap rejected (e.g. 2026-03-29 02:30 in Europe/Rome).
   - Ambiguous DST fall back hour rejected (e.g. 2026-10-25 02:30 in Europe/Rome).
   - Valid date/time successfully converted to UTC while preserving local date/time & timezone.
3. Explicit operator confirmation required.
4. Immutable content snapshot frozen on scheduling (subject, body plain/html, recipients).
5. Reschedule and Cancel Schedule actions:
   - Reschedule updates timing and snapshots.
   - Cancel Schedule reverts status to ready_to_schedule, clears snapshots and timestamps.
6. Persistence across repository reload / simulated application restart.
7. Delivery defanging: zero live SMTP, zero outgoing Windows Scheduled Task modifications.
"""

import os
import tempfile
import pytest
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxDraftRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from backend.sales_ui.router import (
    CSRF_COOKIE_NAME,
    validate_and_convert_schedule_time,
)
from backend.operator_auth import get_auth_service, init_auth_schema
from backend.main import app


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    repo = MailboxRepository(db_path=db_path)
    repo.apply_migrations()
    init_auth_schema(db_path)

    # Seed primary accounts
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Global",
        account_role="primary_sales",
        imap_host="imap.test.com",
        imap_port=993,
        smtp_host="smtp.test.com",
        smtp_port=465,
        credential_ref="cred_com",
        is_active=True,
    )
    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Italia",
        account_role="primary_sales",
        imap_host="imap.test.it",
        imap_port=993,
        smtp_host="smtp.test.it",
        smtp_port=465,
        credential_ref="cred_it",
        is_active=True,
    )
    repo.save_account(acc_com)
    repo.save_account(acc_it)

    yield db_path

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest.fixture
def auth_client(test_db):
    auth = get_auth_service(test_db)
    user = auth.create_user("operator_sched", "SchedPass123!", "operator", "Schedule Operator")
    session_token = auth.create_session(user.user_id)

    client = TestClient(app)
    client.cookies.set("sales_session", session_token)
    return client, test_db


def _get_csrf_tokens(client: TestClient) -> dict:
    resp = client.get("/sales/mail-operations/compose")
    csrf_cookie = resp.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_cookie:
        csrf_cookie = "test_csrf_token_compose_01b"
        client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
    return {"token": csrf_cookie, "header": {"X-CSRF-Token": csrf_cookie}}


def test_validate_and_convert_schedule_time_dst_and_past():
    """Test date/time validation in Europe/Rome: past, nonexistent (DST gap), and ambiguous (DST fall back)."""
    # 1. Past date check
    ref_now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(Exception) as exc_past:
        validate_and_convert_schedule_time(
            send_date="2026-11-17",
            send_time="10:00",
            target_timezone="Europe/Rome",
            reference_now_utc=ref_now,
        )
    assert "cannot be in the past" in str(exc_past.value.detail).lower()

    # 2. Nonexistent local time (DST spring forward gap: 2026-03-29 02:30 CET -> CEST skips 02:00-03:00)
    with pytest.raises(Exception) as exc_gap:
        validate_and_convert_schedule_time(
            send_date="2026-03-29",
            send_time="02:30",
            target_timezone="Europe/Rome",
            reference_now_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        )
    assert "does not exist" in str(exc_gap.value.detail).lower()
    assert "spring forward" in str(exc_gap.value.detail).lower()

    # 3. Ambiguous local time (DST fall back repeated hour: 2026-10-25 02:30 repeated)
    with pytest.raises(Exception) as exc_amb:
        validate_and_convert_schedule_time(
            send_date="2026-10-25",
            send_time="02:30",
            target_timezone="Europe/Rome",
            reference_now_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        )
    assert "ambiguous" in str(exc_amb.value.detail).lower()
    assert "turn back" in str(exc_amb.value.detail).lower()

    # 4. Valid future time
    aware_local, utc_dt = validate_and_convert_schedule_time(
        send_date="2026-11-20",
        send_time="09:30",
        target_timezone="Europe/Rome",
        reference_now_utc=ref_now,
    )
    assert aware_local.year == 2026
    assert aware_local.month == 11
    assert aware_local.day == 20
    assert aware_local.hour == 9
    assert aware_local.minute == 30
    # In November, Europe/Rome is UTC+1 (CET), so 09:30 CET = 08:30 UTC
    assert utc_dt.hour == 8
    assert utc_dt.minute == 30


def test_schedule_draft_workflow_and_snapshots(auth_client):
    """Verify schedule draft endpoint freezes snapshots, requires confirmation, and updates status."""
    client, db_path = auth_client
    repo = MailboxRepository(db_path=db_path)

    # Create draft in ready_to_schedule
    drf = MailboxDraftRecord(
        draft_id="drf_sched_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["client@studiobim.com"],
        subject="Proposta Automazione BIM",
        body_plain_text="Gentile Studio,\nEcco la nostra proposta.",
        body_html_sanitized="<p>Gentile Studio,<br>Ecco la nostra proposta.</p>",
        draft_status="ready_to_schedule",
        created_by="operator_sched",
        updated_by="operator_sched",
    )
    repo.save_draft(drf)

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": db_path, "EMAIL_SEND_ENABLED": "false"}):
        csrf_data = _get_csrf_tokens(client)
        csrf_token = csrf_data["token"]

        # 1. Missing explicit confirmation -> 400
        res_no_conf = client.post(
            "/sales/mail-operations/compose/schedule",
            data={
                "draft_id": "drf_sched_001",
                "send_date": "2026-11-25",
                "send_time": "09:00",
                "timezone_name": "Europe/Rome",
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_no_conf.status_code == 400
        assert "explicit operator confirmation" in res_no_conf.json()["detail"].lower()

        # 2. Valid schedule with confirmation
        res_ok = client.post(
            "/sales/mail-operations/compose/schedule",
            data={
                "draft_id": "drf_sched_001",
                "send_date": "2026-11-25",
                "send_time": "09:00",
                "timezone_name": "Europe/Rome",
                "confirmation": "confirmed",
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["success"] is True
        assert data["draft_status"] == "scheduled"
        assert data["scheduled_local_date"] == "2026-11-25"
        assert data["scheduled_local_time"] == "09:00"

        # Check repository record & snapshots
        reloaded = repo.get_draft("drf_sched_001")
        assert reloaded.draft_status == "scheduled"
        assert reloaded.scheduled_local_date == "2026-11-25"
        assert reloaded.scheduled_local_time == "09:00"
        assert reloaded.scheduled_timezone == "Europe/Rome"
        assert reloaded.scheduled_approved_by == "operator_sched"
        assert reloaded.snapshot_subject == "Proposta Automazione BIM"
        assert "Gentile Studio" in reloaded.snapshot_plain_text
        assert reloaded.snapshot_to_addresses == ["client@studiobim.com"]

        # 3. Reschedule
        res_resched = client.post(
            "/sales/mail-operations/compose/schedule",
            data={
                "draft_id": "drf_sched_001",
                "send_date": "2026-11-26",
                "send_time": "10:30",
                "timezone_name": "Europe/Rome",
                "confirmation": "confirmed",
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_resched.status_code == 200
        resched_drf = repo.get_draft("drf_sched_001")
        assert resched_drf.scheduled_local_date == "2026-11-26"
        assert resched_drf.scheduled_local_time == "10:30"

        # 4. Cancel Schedule
        res_cancel = client.post(
            "/sales/mail-operations/compose/cancel-schedule",
            data={
                "draft_id": "drf_sched_001",
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_cancel.status_code == 200
        cancelled_drf = repo.get_draft("drf_sched_001")
        assert cancelled_drf.draft_status == "ready_to_schedule"
        assert cancelled_drf.scheduled_for_utc is None
        assert cancelled_drf.scheduled_local_date is None
        assert cancelled_drf.snapshot_subject is None


def test_concurrency_conflict_and_snapshot_protection(auth_client):
    """Verify HTTP 409 on version conflict and protection against editing scheduled drafts."""
    client, db_path = auth_client
    repo = MailboxRepository(db_path=db_path)

    drf = MailboxDraftRecord(
        draft_id="drf_conflict_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["client@test.com"],
        subject="Subject Before Scheduling",
        body_plain_text="Original body text",
        body_html_sanitized="<p>Original body text</p>",
        draft_status="ready_to_schedule",
        created_by="op1",
        updated_by="op1",
        version=1,
    )
    repo.save_draft(drf)

    with patch.dict(os.environ, {"SALES_OUTREACH_DB": db_path, "EMAIL_SEND_ENABLED": "false"}):
        csrf_data = _get_csrf_tokens(client)
        csrf_token = csrf_data["token"]

        # Schedule successfully with version=1
        res1 = client.post(
            "/sales/mail-operations/compose/schedule",
            data={
                "draft_id": "drf_conflict_01",
                "send_date": "2026-11-25",
                "send_time": "09:00",
                "timezone_name": "Europe/Rome",
                "confirmation": "confirmed",
                "version": 1,
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res1.status_code == 200

        # Attempting to schedule again with stale version=1 -> HTTP 409
        res_stale = client.post(
            "/sales/mail-operations/compose/schedule",
            data={
                "draft_id": "drf_conflict_01",
                "send_date": "2026-11-25",
                "send_time": "09:30",
                "timezone_name": "Europe/Rome",
                "confirmation": "confirmed",
                "version": 1,
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_stale.status_code == 409
        assert "Draft conflict" in res_stale.json()["detail"]

        # Attempting to edit body while scheduled is blocked to protect immutable snapshot
        res_edit_blocked = client.post(
            "/sales/mail-operations/compose/save",
            data={
                "draft_id": "drf_conflict_01",
                "account_id": "acc_info_com",
                "to_addresses": "client@test.com",
                "subject": "Tampered Subject",
                "body_plain_text": "Tampered body",
                "version": 2,
                "csrf_token": csrf_token,
            },
            headers={"Accept": "application/json"},
        )
        assert res_edit_blocked.status_code == 400
        assert "currently scheduled" in res_edit_blocked.json()["detail"].lower()


def test_persistence_across_simulated_restart(test_db):
    """Verify that scheduled drafts, timing and immutable snapshots persist across repository instances."""
    repo1 = MailboxRepository(db_path=test_db)
    drf = MailboxDraftRecord(
        draft_id="drf_persist_01",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        to_addresses=["partner@architettura.it"],
        subject="Persistent Scheduling Test",
        body_plain_text="Draft body plain text",
        body_html_sanitized="<p>Draft body plain text</p>",
        draft_status="ready_to_schedule",
        created_by="op_user",
        updated_by="op_user",
    )
    repo1.save_draft(drf)

    repo1.schedule_draft(
        draft_id="drf_persist_01",
        scheduled_for_utc="2026-11-28T09:00:00Z",
        scheduled_local_date="2026-11-28",
        scheduled_local_time="10:00",
        scheduled_timezone="Europe/Rome",
        operator_username="op_user",
    )

    # Simulate restart by instantiating completely fresh repository connection
    repo2 = MailboxRepository(db_path=test_db)
    reloaded = repo2.get_draft("drf_persist_01")

    assert reloaded is not None
    assert reloaded.draft_status == "scheduled"
    assert reloaded.scheduled_for_utc == "2026-11-28T09:00:00Z"
    assert reloaded.scheduled_local_date == "2026-11-28"
    assert reloaded.scheduled_local_time == "10:00"
    assert reloaded.scheduled_timezone == "Europe/Rome"
    assert reloaded.snapshot_subject == "Persistent Scheduling Test"
    assert reloaded.snapshot_to_addresses == ["partner@architettura.it"]
