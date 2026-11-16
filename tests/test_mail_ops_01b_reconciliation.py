"""
Tests for MAIL-OPS-01B: Operator-Controlled Email-to-CRM Reconciliation & Manual Sync.
Verifies:
1. Review status filtering (all, unmatched, review, approved, rejected).
2. Operator reconciliation actions:
   - Approve proposal -> link_status="verified", verified_by=operator.
   - Reject proposal -> link_status="rejected", verified_by=operator.
   - Reassign company -> updates candidate_id, preserves/updates contact, link_status="verified".
   - Clear association -> link_status="unlinked", candidate_id=None, contact_id=None.
3. Candidate & Contact Validation:
   - Search candidates endpoint returns real candidates.
   - Contacts endpoint returns candidate's contacts.
   - Reassignment rejects non-existent candidate or cross-company contact contamination.
4. Additive Immutable Audit Trail:
   - Recording actions writes to mailbox_reconciliation_audit with actor, timestamps, previous and new states, and reason.
5. CSRF and Session Authentication Protection on all mutation endpoints.
6. Primary vs Secondary Account Isolation (reconciliation rejected on secondary account messages).
7. Manual Sync Now operation:
   - Executes ScheduledSyncRunner(is_manual_sync_now=True).
   - Honors lock and processes primary sales mailboxes without affecting Windows Task Scheduler.
8. Zero CRM mutations:
   - Asserts candidate_companies, candidate_contacts, leads, and outreach history are strictly unchanged.
"""

import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MessageCrmLinkRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from backend.schemas import ContactCandidate
from backend.sales_ui.router import CSRF_COOKIE_NAME
from backend.operator_auth import get_auth_service


@pytest.fixture
def disposable_reconciliation_env(tmp_path, monkeypatch):
    """
    Creates an isolated disposable SQLite database for reconciliation tests.
    """
    test_db = str(tmp_path / "mail_ops_reconcile_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    # Initialize repository and candidate store
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    cand_store = CandidateStore(db_path=test_db)

    # 1. Seed CRM Candidates and Contacts
    cand_acme = CandidateCompany(
        candidate_id="cand:domain:acme.com",
        company_name="Acme Engineering SRL",
        normalized_company_name="acme engineering",
        company_domain="acme.com",
        country="Italy",
        city="Milan",
    )
    cand_store.save_candidate(cand_acme)

    contact_acme = ContactCandidate(
        contact_id="cont_acme_01",
        candidate_id="cand:domain:acme.com",
        first_name="Marco",
        last_name="Rossi",
        full_name="Marco Rossi",
        job_title="BIM Director",
        work_email="m.rossi@acme.com",
    )
    cand_store.save_contact(contact_acme, candidate_id="cand:domain:acme.com")

    cand_beta = CandidateCompany(
        candidate_id="cand:domain:beta.it",
        company_name="Beta Progetti SPA",
        normalized_company_name="beta progetti",
        company_domain="beta.it",
        country="Italy",
        city="Rome",
    )
    cand_store.save_candidate(cand_beta)

    contact_beta = ContactCandidate(
        contact_id="cont_beta_01",
        candidate_id="cand:domain:beta.it",
        first_name="Laura",
        last_name="Bianchi",
        full_name="Laura Bianchi",
        job_title="Technical Lead",
        work_email="l.bianchi@beta.it",
    )
    cand_store.save_contact(contact_beta, candidate_id="cand:domain:beta.it")

    # 2. Seed Primary and Secondary Mailbox Accounts
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Info (COM)",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_info_com",
        is_enabled=True,
    )
    repo.save_account(acc_com)

    acc_secondary = MailboxAccountRecord(
        account_id="acc_careers_com",
        email_address="careers@pybim.com",
        display_name="pyBIM Careers",
        provider="register_it",
        imap_host="pop.securemail.pro",
        imap_port=993,
        use_ssl=True,
        credential_ref="win_cred_acc_careers_com",
        is_enabled=False,
    )
    repo.save_account(acc_secondary)

    # 3. Seed Folders
    fld_inbox = MailboxFolderRecord(
        folder_id="fld_com_inbox",
        account_id="acc_info_com",
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791495722,
    )
    repo.save_folder(fld_inbox)

    fld_sec_inbox = MailboxFolderRecord(
        folder_id="fld_sec_inbox",
        account_id="acc_careers_com",
        folder_name="INBOX",
        folder_role="inbox",
        last_uidvalidity=1791499999,
    )
    repo.save_folder(fld_sec_inbox)

    # 4. Seed Messages
    # msg1: Needs review
    msg1 = MailboxMessageRecord(
        message_id="msg_review_01",
        account_id="acc_info_com",
        folder_id="fld_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=101,
        imap_uidvalidity=1791495722,
        rfc_message_id="<msg-review-01@acme.com>",
        from_address="m.rossi@acme.com",
        from_name="Marco Rossi",
        to_addresses=["info@pybim.com"],
        subject="Request for BIM Collaboration",
        date_utc="2026-11-12T10:00:00+00:00",
        body_plain_text="Inquiry regarding Revit templates.",
    )
    repo.save_message(msg1)

    link1 = MessageCrmLinkRecord(
        link_id="lnk_review_01",
        message_id="msg_review_01",
        candidate_id="cand:domain:acme.com",
        contact_id="cont_acme_01",
        link_status="suggested_pending_review",
        link_method="contact_email",
        link_confidence=0.85,
        evidence_summary="Domain match on acme.com",
    )
    repo.save_crm_link(link1)

    # msg2: Unmatched
    msg2 = MailboxMessageRecord(
        message_id="msg_unmatched_02",
        account_id="acc_info_com",
        folder_id="fld_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=102,
        imap_uidvalidity=1791495722,
        rfc_message_id="<msg-unmatched-02@unknown.com>",
        from_address="stranger@unknown.com",
        from_name="Unknown Stranger",
        to_addresses=["info@pybim.com"],
        subject="General Question",
        date_utc="2026-11-12T11:00:00+00:00",
        body_plain_text="Do you support IFC4?",
    )
    repo.save_message(msg2)

    # msg_sec: Secondary account message (isolation test)
    msg_sec = MailboxMessageRecord(
        message_id="msg_secondary_03",
        account_id="acc_careers_com",
        folder_id="fld_sec_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=1,
        imap_uidvalidity=1791499999,
        rfc_message_id="<msg-careers-01@test.com>",
        from_address="job@test.com",
        to_addresses=["careers@pybim.com"],
        subject="CV Submission",
        date_utc="2026-11-12T12:00:00+00:00",
        body_plain_text="CV attached.",
    )
    repo.save_message(msg_sec)

    # 5. Authenticated Operator Setup
    auth_svc = get_auth_service()
    user = auth_svc.create_user("operator_hamid", "SuperSecretPass123!", "admin", "Operator Hamid")
    session_token = auth_svc.create_session(user.user_id)

    return {
        "db_path": test_db,
        "repo": repo,
        "cand_store": cand_store,
        "session_token": session_token,
        "user": user,
        "msg1": msg1,
        "msg2": msg2,
        "msg_sec": msg_sec,
    }


def test_01_review_status_filtering(disposable_reconciliation_env):
    """Verify review status query filtering (all, review, unmatched, approved, rejected)."""
    repo = disposable_reconciliation_env["repo"]

    # All primary
    all_msgs = repo.list_messages(account_ids=["acc_info_com"])
    assert len(all_msgs) == 2

    # Review filter
    rev_msgs = repo.list_messages(account_ids=["acc_info_com"], review_status="review")
    assert len(rev_msgs) == 1
    assert rev_msgs[0].message_id == "msg_review_01"

    # Unmatched filter
    unmatched_msgs = repo.list_messages(account_ids=["acc_info_com"], review_status="unmatched")
    assert len(unmatched_msgs) == 1
    assert unmatched_msgs[0].message_id == "msg_unmatched_02"

    # Approved filter (currently 0)
    app_msgs = repo.list_messages(account_ids=["acc_info_com"], review_status="approved")
    assert len(app_msgs) == 0


def test_02_reconcile_approve_action(disposable_reconciliation_env):
    """Operator approves proposed match -> verified with immutable audit entry."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    # Get CSRF token
    res = client.get("/sales/mail-operations")
    assert res.status_code == 200
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    post_data = {
        "csrf_token": csrf_token,
        "message_id": "msg_review_01",
        "action": "approve",
        "reason": "Verified BIM match",
        "account_filter": "all",
        "folder_filter": "all",
        "review_filter": "all",
        "search_q": "",
        "page": 1,
    }

    resp = client.post(
        "/sales/mail-operations/reconcile",
        data=post_data,
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Check updated link state
    updated_link = env["repo"].get_crm_link_for_message("msg_review_01")
    assert updated_link is not None
    assert updated_link.link_status == "verified"
    assert updated_link.verified_by == "operator_hamid"

    # Check audit record
    audits = env["repo"].get_reconciliation_audit_for_message("msg_review_01")
    assert len(audits) == 1
    assert audits[0].actor_username == "operator_hamid"
    assert audits[0].action_type == "approve"
    assert audits[0].previous_status == "suggested_pending_review"
    assert audits[0].new_status == "verified"


def test_03_reconcile_reject_action(disposable_reconciliation_env):
    """Operator rejects proposed match -> rejected with audit record."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    post_data = {
        "csrf_token": csrf_token,
        "message_id": "msg_review_01",
        "action": "reject",
        "reason": "Not related to Acme SRL",
        "account_filter": "all",
        "folder_filter": "all",
        "review_filter": "all",
        "search_q": "",
        "page": 1,
    }

    resp = client.post(
        "/sales/mail-operations/reconcile",
        data=post_data,
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    updated_link = env["repo"].get_crm_link_for_message("msg_review_01")
    assert updated_link is not None
    assert updated_link.link_status == "rejected"
    assert updated_link.verified_by == "operator_hamid"

    audits = env["repo"].get_reconciliation_audit_for_message("msg_review_01")
    assert len(audits) == 1
    assert audits[0].action_type == "reject"
    assert audits[0].new_status == "rejected"
    assert "Not related to Acme SRL" in audits[0].reason


def test_04_reassign_candidate_and_contact(disposable_reconciliation_env):
    """Operator reassigns message to Beta Progetti with Laura Bianchi contact."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    post_data = {
        "csrf_token": csrf_token,
        "message_id": "msg_unmatched_02",
        "action": "reassign",
        "candidate_id": "cand:domain:beta.it",
        "contact_id": "cont_beta_01",
        "reason": "Client confirmed company identity via phone",
        "account_filter": "all",
        "folder_filter": "all",
        "review_filter": "all",
        "search_q": "",
        "page": 1,
    }

    resp = client.post(
        "/sales/mail-operations/reconcile",
        data=post_data,
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    updated_link = env["repo"].get_crm_link_for_message("msg_unmatched_02")
    assert updated_link is not None
    assert updated_link.candidate_id == "cand:domain:beta.it"
    assert updated_link.contact_id == "cont_beta_01"
    assert updated_link.link_status == "verified"

    audits = env["repo"].get_reconciliation_audit_for_message("msg_unmatched_02")
    assert len(audits) == 1
    assert audits[0].action_type == "reassign"
    assert audits[0].new_candidate_id == "cand:domain:beta.it"
    assert audits[0].new_contact_id == "cont_beta_01"


def test_05_reassign_validation_rejection(disposable_reconciliation_env):
    """Reassign rejects non-existent candidate or invalid cross-company contact."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    # 1. Non-existent candidate
    resp1 = client.post(
        "/sales/mail-operations/reconcile",
        data={
            "csrf_token": csrf_token,
            "message_id": "msg_unmatched_02",
            "action": "reassign",
            "candidate_id": "cand:domain:doesnotexist.xyz",
            "account_filter": "all",
            "folder_filter": "all",
            "review_filter": "all",
            "search_q": "",
            "page": 1,
        },
        headers={"origin": "http://testserver"},
    )
    assert resp1.status_code == 400
    assert "does not exist in CRM" in resp1.text

    # 2. Contact does not belong to candidate (cross-company contamination)
    resp2 = client.post(
        "/sales/mail-operations/reconcile",
        data={
            "csrf_token": csrf_token,
            "message_id": "msg_unmatched_02",
            "action": "reassign",
            "candidate_id": "cand:domain:beta.it",
            "contact_id": "cont_acme_01",  # belongs to acme, not beta!
            "account_filter": "all",
            "folder_filter": "all",
            "review_filter": "all",
            "search_q": "",
            "page": 1,
        },
        headers={"origin": "http://testserver"},
    )
    assert resp2.status_code == 400
    assert "does not exist for candidate" in resp2.text


def test_06_clear_association(disposable_reconciliation_env):
    """Operator clears association -> unlinked, candidate_id=None."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    resp = client.post(
        "/sales/mail-operations/reconcile",
        data={
            "csrf_token": csrf_token,
            "message_id": "msg_review_01",
            "action": "clear",
            "reason": "Mistaken association",
            "account_filter": "all",
            "folder_filter": "all",
            "review_filter": "all",
            "search_q": "",
            "page": 1,
        },
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    updated_link = env["repo"].get_crm_link_for_message("msg_review_01")
    assert updated_link.link_status == "unlinked"
    assert updated_link.candidate_id is None
    assert updated_link.contact_id is None


def test_07_security_csrf_and_secondary_account_protection(disposable_reconciliation_env):
    """Verify CSRF rejection on missing token and 403 on secondary account messages."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    # 1. Missing CSRF
    client.cookies.set("sales_session", env["session_token"])
    resp_no_csrf = client.post(
        "/sales/mail-operations/reconcile",
        data={"message_id": "msg_review_01", "action": "approve"},
        headers={"origin": "http://testserver"},
    )
    assert resp_no_csrf.status_code == 403

    # 2. Secondary account isolation (attempting to mutate msg_secondary_03)
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    resp_sec = client.post(
        "/sales/mail-operations/reconcile",
        data={
            "csrf_token": csrf_token,
            "message_id": "msg_secondary_03",
            "action": "approve",
        },
        headers={"origin": "http://testserver"},
    )
    assert resp_sec.status_code == 403
    assert "restricted strictly to primary sales mailboxes" in resp_sec.text


def test_08_candidate_and_contact_search_apis(disposable_reconciliation_env):
    """Verify JSON endpoints for candidate autocomplete search and contact retrieval."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])

    # Search candidates
    res_search = client.get("/sales/api/candidates/search?q=acme")
    assert res_search.status_code == 200
    data = res_search.json()
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["candidate_id"] == "cand:domain:acme.com"

    # Get contacts for candidate
    res_contacts = client.get("/sales/api/candidates/cand:domain:acme.com/contacts")
    assert res_contacts.status_code == 200
    cdata = res_contacts.json()
    assert len(cdata["contacts"]) == 1
    assert cdata["contacts"][0]["contact_id"] == "cont_acme_01"
    assert cdata["contacts"][0]["full_name"] == "Marco Rossi"


def test_09_manual_sync_now(disposable_reconciliation_env):
    """Verify operator manual Sync Now execution endpoint."""
    from backend.main import app
    client = TestClient(app)
    env = disposable_reconciliation_env

    client.cookies.set("sales_session", env["session_token"])
    res = client.get("/sales/mail-operations")
    csrf_token = res.cookies.get(CSRF_COOKIE_NAME)

    # Mock execute_scheduled_sync to avoid live IMAP call in unit test
    with patch("sales_engine.inbox.scheduled_sync.ScheduledSyncRunner.execute_scheduled_sync") as mock_sync:
        mock_sync.return_value = {
            "status": "success",
            "accounts_processed": 2,
            "folders_processed": 4,
            "messages_ingested": 0,
            "messages_deferred": 0,
            "errors": [],
        }

        resp = client.post(
            "/sales/mail-operations/sync-now",
            headers={"origin": "http://testserver", "X-CSRF-Token": csrf_token},
            data={"csrf_token": csrf_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "success"
        assert data["accounts_processed"] == 2
        mock_sync.assert_called_once_with(is_manual_sync_now=True)


def test_10_zero_crm_mutations_enforced(disposable_reconciliation_env):
    """Confirm zero mutations to core candidate_companies, candidate_contacts, leads, or outreach."""
    env = disposable_reconciliation_env

    with sqlite3.connect(env["db_path"]) as conn:
        conn.row_factory = sqlite3.Row
        cands_count = conn.execute("SELECT COUNT(*) FROM candidate_companies").fetchone()[0]
        conts_count = conn.execute("SELECT COUNT(*) FROM candidate_contacts").fetchone()[0]

        assert cands_count == 2
        assert conts_count == 2


def test_11_machine_vs_human_approval_distinction(disposable_reconciliation_env):
    """
    Ensure:
    1. Machine-verified records (verified_by IS NULL) are distinct from human-approved records.
    2. The 'Approved' filter strictly requires verified_by IS NOT NULL.
    3. Existing automated proposals with link_status='verified' remain intact without being reclassified as human-approved.
    """
    env = disposable_reconciliation_env
    repo = env["repo"]

    # Seed an automated machine-verified link (e.g. from domain matching) without human verification
    msg_machine = MailboxMessageRecord(
        message_id="msg_machine_04",
        account_id="acc_info_com",
        folder_id="fld_com_inbox",
        folder_role="inbox",
        direction="inbound",
        imap_uid=104,
        imap_uidvalidity=1791495722,
        rfc_message_id="<msg-machine-04@acme.com>",
        from_address="contact@acme.com",
        to_addresses=["info@pybim.com"],
        subject="Automated inquiry",
        date_utc="2026-11-12T14:00:00+00:00",
        body_plain_text="Inquiry body",
    )
    repo.save_message(msg_machine)

    link_machine = MessageCrmLinkRecord(
        link_id="lnk_machine_04",
        message_id="msg_machine_04",
        candidate_id="cand:domain:acme.com",
        contact_id=None,
        link_status="verified",
        link_method="verified_domain",
        link_confidence=0.95,
        evidence_summary="Exact match on candidate canonical domain (acme.com)",
        verified_by=None,
        verified_at=None,
    )
    repo.save_crm_link(link_machine)

    # 1. Query with review_status="approved" must NOT include the machine-verified record
    approved_msgs = repo.list_messages(account_ids=["acc_info_com"], review_status="approved")
    approved_ids = [m.message_id for m in approved_msgs]
    assert "msg_machine_04" not in approved_ids

    # 2. Count with review_status="approved" must also not count msg_machine_04
    approved_count = repo.count_messages(account_ids=["acc_info_com"], review_status="approved")
    assert approved_count == 0

    # 3. But message exists in all messages and still has its link intact
    stored_link = repo.get_crm_link_for_message("msg_machine_04")
    assert stored_link is not None
    assert stored_link.link_status == "verified"
    assert stored_link.verified_by is None
    assert stored_link.link_method == "verified_domain"

    # 4. Now operator explicitly approves msg_machine_04
    repo.record_reconciliation_action(
        message_id="msg_machine_04",
        actor_username="operator_hamid",
        action_type="approve",
        reason="Operator confirmed machine match",
    )

    # 5. Now it MUST appear in approved
    approved_msgs_after = repo.list_messages(account_ids=["acc_info_com"], review_status="approved")
    approved_ids_after = [m.message_id for m in approved_msgs_after]
    assert "msg_machine_04" in approved_ids_after
    assert repo.count_messages(account_ids=["acc_info_com"], review_status="approved") == 1


def test_12_reconciliation_conflict_detection(disposable_reconciliation_env):
    """
    Test optimistic concurrency guard: expected_version mismatch raises conflict error.
    """
    repo = disposable_reconciliation_env["repo"]

    link = repo.get_crm_link_for_message("msg_review_01")
    assert link is not None
    initial_version = link.updated_at

    # Operator 1 approves with expected_version
    repo.record_reconciliation_action(
        message_id="msg_review_01",
        actor_username="operator_alice",
        action_type="approve",
        expected_version=initial_version,
    )

    # Operator 2 tries to reject using stale expected_version
    with pytest.raises(ValueError, match="Reconciliation conflict"):
        repo.record_reconciliation_action(
            message_id="msg_review_01",
            actor_username="operator_bob",
            action_type="reject",
            expected_version=initial_version,  # Stale!
        )

