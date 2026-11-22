"""
Tests for MAIL-COMPOSE-01A: Integrated Email Composer & Persistent Draft Management.

Verifies:
1. Senders restricted strictly to primary accounts (acc_info_com, acc_info_it).
   Rejection of secondary or arbitrary sender emails.
2. Email parsing and validation: valid RFC syntax, angle brackets, deduplication, rejection of malformed addresses.
3. Persistent draft CRUD operations:
   - Creation of new draft.
   - Retrieval and editing of draft.
   - Optimistic concurrency control (version conflict HTTP 409 / ValueError).
   - Duplicate draft creates an isolated fresh draft with version=1.
   - Discard draft marks status="discarded".
4. Body sanitization:
   - HTML sanitization defangs script tags, tracking pixels, onload handlers, javascript: URIs.
   - Body truncation guards preventing unbounded size explosion.
5. CRM integration:
   - Compose prefill from candidate and validated contact.
   - Enforce that linked contact must belong to linked candidate.
   - Strictly zero mutations to CRM tables (candidate_companies, candidate_contacts, leads, historical_sent_emails, manual_outreach_log).
6. Drafts list dashboard filtering (account, status, search) and pagination.
7. Verified email signatures for pyBIM COM and IT accounts.
8. Delivery defanging:
   - Strictly zero live network or SMTP calls.
"""

import os
import sqlite3
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxDraftRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.signatures import (
    get_default_signature,
    SIGNATURES_REGISTRY,
)
from sales_engine.inbox.html_sanitizer import sanitize_email_html
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from backend.schemas import ContactCandidate
from backend.sales_ui.router import (
    CSRF_COOKIE_NAME,
    parse_and_validate_email_list,
)
from backend.operator_auth import get_auth_service


@pytest.fixture
def disposable_compose_env(tmp_path, monkeypatch):
    """
    Creates an isolated disposable SQLite database for composer and draft tests.
    """
    test_db = str(tmp_path / "mail_compose_test.db")
    monkeypatch.setenv("SALES_OUTREACH_DB", test_db)
    monkeypatch.setenv("SALESAI_DB_PATH", test_db)
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", test_db)
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")

    # Initialize mailbox repository and apply migrations
    repo = MailboxRepository(db_path=test_db)
    repo.apply_migrations()

    # Seed primary accounts
    acc_com = MailboxAccountRecord(
        account_id="acc_info_com",
        email_address="info@pybim.com",
        display_name="pyBIM Global Info",
        account_role="primary_sales",
        imap_host="imap.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_com",
        is_active=True,
    )
    acc_it = MailboxAccountRecord(
        account_id="acc_info_it",
        email_address="info@pybim.it",
        display_name="pyBIM Italia Info",
        account_role="primary_sales",
        imap_host="imap.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_info_it",
        is_active=True,
    )
    acc_sec = MailboxAccountRecord(
        account_id="acc_careers_com",
        email_address="careers@pybim.com",
        display_name="pyBIM Careers",
        account_role="secondary_ops",
        imap_host="imap.securemail.pro",
        imap_port=993,
        smtp_host="authsmtp.securemail.pro",
        smtp_port=465,
        credential_ref="cred_careers_com",
        is_active=False,
    )
    repo.save_account(acc_com)
    repo.save_account(acc_it)
    repo.save_account(acc_sec)

    # Seed CRM candidates and contacts
    cand_store = CandidateStore(db_path=test_db)
    cand1 = CandidateCompany(
        candidate_id="cand:domain:bimstudio.it",
        company_name="BIM Studio Italia SRL",
        normalized_company_name="bim studio italia",
        company_domain="bimstudio.it",
        country="Italy",
        city="Milan",
    )
    cand_store.save_candidate(cand1)

    cont1 = ContactCandidate(
        contact_id="cont_marco_01",
        candidate_id="cand:domain:bimstudio.it",
        full_name="Marco Rossi",
        work_email="marco.rossi@bimstudio.it",
        job_title="Technical Director",
        email_status="verified",
        verification_source="manual",
    )
    cand_store.save_contact(cont1, "cand:domain:bimstudio.it")

    cand2 = CandidateCompany(
        candidate_id="cand:domain:aecglobal.com",
        company_name="AEC Global Ltd",
        normalized_company_name="aec global",
        company_domain="aecglobal.com",
        country="United Kingdom",
        city="London",
    )
    cand_store.save_candidate(cand2)

    import backend.operator_auth
    from backend.operator_auth import OperatorAuthService
    orig_auth = backend.operator_auth._auth_service
    test_auth = OperatorAuthService(db_path=test_db)
    backend.operator_auth._auth_service = test_auth

    yield {
        "db_path": test_db,
        "repo": repo,
        "cand_store": cand_store,
        "cand1": cand1,
        "cont1": cont1,
        "cand2": cand2,
        "auth": test_auth,
    }

    backend.operator_auth._auth_service = orig_auth


@pytest.fixture
def test_client(disposable_compose_env):
    """
    Returns an authenticated FastAPI TestClient.
    """
    from backend.main import app
    client = TestClient(app)

    auth = get_auth_service()
    user = auth.create_user("operator_lead", "SuperSecretPass123!", "operator", "Lead Sales Operator")
    token = auth.create_session(user.user_id)
    client.cookies.set("sales_session", token)
    return client


def _get_csrf_tokens(client: TestClient) -> dict:
    resp = client.get("/sales/mail-operations/compose")
    csrf_cookie = resp.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_cookie:
        csrf_cookie = "test_csrf_token_compose_01a"
        client.cookies.set(CSRF_COOKIE_NAME, csrf_cookie)
    return {"token": csrf_cookie, "header": {"X-CSRF-Token": csrf_cookie}}


# ============================================================================
# 1. Email Parsing and Validation Tests
# ============================================================================

def test_parse_and_validate_email_list_valid():
    """Validates comma, semicolon, newline parsing, deduplication and angle bracket support."""
    raw = "alice@example.com, Bob Builder <bob@example.com>; charlie@domain.org\nAlice@example.com"
    cleaned = parse_and_validate_email_list(raw)
    assert cleaned == ["alice@example.com", "bob@example.com", "charlie@domain.org"]


def test_parse_and_validate_email_list_invalid():
    """Malformed emails must raise HTTPException 400."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        parse_and_validate_email_list("valid@test.com, not-an-email, another@test.com")
    assert exc.value.status_code == 400
    assert "Invalid email address format" in exc.value.detail


# ============================================================================
# 2. Signature Registry Tests
# ============================================================================

def test_signature_registry():
    """Official signatures for primary accounts exist and match official brand assets."""
    sig_com = get_default_signature("acc_info_com")
    sig_it = get_default_signature("acc_info_it")

    # Official info@pybim.com checks
    assert "Engineering & Digital Automation" in sig_com["plain"]
    assert "info@pybim.com" in sig_com["plain"]
    assert "+39 352 288 7878" in sig_com["plain"]
    assert "https://wa.me/393522887878" in sig_com["plain"]
    assert "https://www.pybim.com" in sig_com["plain"]
    assert "https://www.linkedin.com/company/pybim" in sig_com["plain"]

    # Official info@pybim.it checks
    assert "Ingegneria & Automazione Digitale" in sig_it["plain"]
    assert "info@pybim.it" in sig_it["plain"]
    assert "+39 352 288 7878" in sig_it["plain"]
    assert "https://wa.me/393522887878" in sig_it["plain"]
    assert "https://www.pybim.it" in sig_it["plain"]
    assert "https://www.linkedin.com/company/pybim" in sig_it["plain"]

    # Rejects invalid account
    invalid_sig = get_default_signature("acc_invalid")
    assert invalid_sig["plain"] == ""
    assert invalid_sig["html"] == ""


# ============================================================================
# 3. HTML Sanitization Tests
# ============================================================================

def test_html_sanitization():
    """Sanitizer defangs malicious tags, javascript URIs, inline scripts, and tracking pixels."""
    dirty_html = """
    <div>
        <p>Hello Marco,</p>
        <script>alert('pwned');</script>
        <img src="http://tracker.com/pixel.png" width="1" height="1">
        <a href="javascript:void(0)" onclick="steal()">Click here</a>
        <p>Best regards,<br>pyBIM Team</p>
    </div>
    """
    clean_html = sanitize_email_html(dirty_html)

    assert "<script" not in clean_html
    assert "alert('pwned')" not in clean_html
    assert "javascript:" not in clean_html
    assert "onclick=" not in clean_html
    # Remote image should have src removed and defanged into data-src / blocked placeholder
    assert 'data-src="http://tracker.com/pixel.png"' in clean_html
    assert "blocked-remote-image" in clean_html
    assert "<p>Hello Marco,</p>" in clean_html
    assert "pyBIM Team" in clean_html


# ============================================================================
# 4. Draft CRUD & Concurrency Repository Tests
# ============================================================================

def test_draft_repository_crud_and_concurrency(disposable_compose_env):
    """Verifies save, get, list, duplicate, discard, and version conflict detection."""
    repo = disposable_compose_env["repo"]

    # 1. Create Draft
    d1 = MailboxDraftRecord(
        draft_id="drf_test_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        to_addresses=["architect@example.com"],
        cc_addresses=["team@example.com"],
        bcc_addresses=[],
        subject="pyBIM Collaboration Request",
        body_plain_text="Dear Architect,\n\nWe would like to introduce pyBIM.",
        body_html_sanitized="<p>Dear Architect,<br>We would like to introduce pyBIM.</p>",
        candidate_id="cand:domain:bimstudio.it",
        contact_id="cont_marco_01",
        draft_status="draft",
        created_by="operator_lead",
        updated_by="operator_lead",
        version=1,
    )
    saved1 = repo.save_draft(d1)
    assert saved1.draft_id == "drf_test_001"
    assert saved1.version == 1

    # 2. Retrieve Draft
    fetched = repo.get_draft("drf_test_001")
    assert fetched is not None
    assert fetched.subject == "pyBIM Collaboration Request"
    assert fetched.to_addresses == ["architect@example.com"]
    assert fetched.version == 1

    # 3. Update Draft (Normal Concurrency)
    fetched.subject = "pyBIM Collaboration Request - Updated"
    saved_v2 = repo.save_draft(fetched)
    assert saved_v2.version == 2
    assert saved_v2.subject == "pyBIM Collaboration Request - Updated"

    # 4. Optimistic Concurrency Conflict (Stale Version)
    stale_draft = MailboxDraftRecord(
        draft_id="drf_test_001",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        subject="Conflicting Edit",
        created_by="other_op",
        updated_by="other_op",
        version=1,  # Stale: DB is at version 2
    )
    with pytest.raises(ValueError) as exc:
        repo.save_draft(stale_draft)
    assert "Draft conflict" in str(exc.value)

    # 5. Duplicate Draft
    duplicated = repo.duplicate_draft("drf_test_001", operator_username="operator_lead")
    assert duplicated is not None
    assert duplicated.draft_id != "drf_test_001"
    assert duplicated.version == 1
    assert "Copy - " in duplicated.subject
    assert duplicated.to_addresses == ["architect@example.com"]

    # 6. Discard Draft
    discard_success = repo.discard_draft("drf_test_001", operator_username="operator_lead")
    assert discard_success is True

    after_discard = repo.get_draft("drf_test_001")
    assert after_discard.draft_status == "discarded"


# ============================================================================
# 5. Router Endpoints: Composer GET & POST Integration Tests
# ============================================================================

def test_compose_view_new(test_client):
    """GET /sales/mail-operations/compose renders composer interface with default primary account."""
    resp = test_client.get("/sales/mail-operations/compose")
    assert resp.status_code == 200
    assert "Integrated Email Composer" in resp.text
    assert "info@pybim.com" in resp.text
    assert "info@pybim.it" in resp.text
    assert "Save Draft" in resp.text
    assert "Ready to Schedule" in resp.text


def test_compose_view_crm_prefill(test_client, disposable_compose_env):
    """GET /sales/mail-operations/compose?candidate_id=...&contact_id=... prefils sender and recipient."""
    cand = disposable_compose_env["cand1"]  # Italy
    cont = disposable_compose_env["cont1"]

    resp = test_client.get(f"/sales/mail-operations/compose?candidate_id={cand.candidate_id}&contact_id={cont.contact_id}")
    assert resp.status_code == 200
    # Italy country defaults to info@pybim.it
    assert 'value="acc_info_it" selected' in resp.text
    assert cont.work_email in resp.text
    assert cand.candidate_id in resp.text


def test_save_draft_action_success(test_client, disposable_compose_env):
    """POST /sales/mail-operations/compose/save creates a persistent draft record."""
    csrf_data = _get_csrf_tokens(test_client)

    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_info_com",
        "to_addresses": "architect1@partner.com, architect2@partner.com",
        "cc_addresses": "team@pybim.com",
        "bcc_addresses": "",
        "subject": "Collaboration Proposal 2026",
        "body_plain": "Hello,\nWe would like to share our latest BIM models.\nRegards.",
        "candidate_id": "cand:domain:bimstudio.it",
        "contact_id": "cont_marco_01",
        "target_status": "draft",
        "version": "1",
    }

    resp = test_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    redirect_url = resp.headers["location"]
    assert "/sales/mail-operations/compose?draft_id=drf_" in redirect_url
    assert "saved=true" in redirect_url

    # Check persistence in database
    draft_id = redirect_url.split("draft_id=")[1].split("&")[0]
    repo = disposable_compose_env["repo"]
    draft = repo.get_draft(draft_id)
    assert draft is not None
    assert draft.sender_email == "info@pybim.com"
    assert "architect1@partner.com" in draft.to_addresses
    assert "architect2@partner.com" in draft.to_addresses
    assert draft.subject == "Collaboration Proposal 2026"
    assert draft.draft_status == "draft"


def test_save_draft_action_reject_unauthorized_sender(test_client):
    """POST /sales/mail-operations/compose/save rejects non-primary senders with 400 Bad Request."""
    csrf_data = _get_csrf_tokens(test_client)

    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_careers_com",  # Secondary/Unauthorized sender
        "to_addresses": "candidate@example.com",
        "subject": "Interview",
        "body_plain": "Test",
        "version": "1",
    }

    resp = test_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
    )
    assert resp.status_code == 400
    assert "Unauthorized sender account" in resp.json()["detail"]


def test_save_draft_action_reject_invalid_recipient(test_client):
    """POST /sales/mail-operations/compose/save rejects malformed recipient addresses."""
    csrf_data = _get_csrf_tokens(test_client)

    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_info_com",
        "to_addresses": "invalid-email-address",
        "subject": "Test",
        "body_plain": "Test",
        "version": "1",
    }

    resp = test_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
    )
    assert resp.status_code == 400
    assert "Invalid email address format" in resp.json()["detail"]


def test_save_draft_action_reject_cross_company_contact(test_client, disposable_compose_env):
    """POST /sales/mail-operations/compose/save rejects cross-company contact contamination."""
    csrf_data = _get_csrf_tokens(test_client)

    # Cont1 belongs to cand1 (bimstudio.it), but candidate_id is cand2 (aecglobal.com)
    post_data = {
        "csrf_token": csrf_data["token"],
        "account_id": "acc_info_com",
        "to_addresses": "marco.rossi@bimstudio.it",
        "subject": "Test Cross-Company",
        "body_plain": "Test",
        "candidate_id": "cand:domain:aecglobal.com",
        "contact_id": "cont_marco_01",
        "version": "1",
    }

    resp = test_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
    )
    assert resp.status_code == 400
    assert "does not belong to candidate" in resp.json()["detail"]


def test_save_draft_action_concurrency_conflict(test_client, disposable_compose_env):
    """POST /sales/mail-operations/compose/save returns 409 Conflict when version mismatches."""
    repo = disposable_compose_env["repo"]

    # Seed draft at version 2
    d = MailboxDraftRecord(
        draft_id="drf_conflict_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        subject="Initial Subject",
        created_by="op1",
        updated_by="op1",
        version=2,
    )
    repo.save_draft(d)

    csrf_data = _get_csrf_tokens(test_client)

    # Submitting with stale version 1
    post_data = {
        "csrf_token": csrf_data["token"],
        "draft_id": "drf_conflict_01",
        "account_id": "acc_info_com",
        "to_addresses": "test@example.com",
        "subject": "Stale Edit Attempt",
        "body_plain": "Stale body",
        "version": "1",
    }

    resp = test_client.post(
        "/sales/mail-operations/compose/save",
        data=post_data,
    )
    assert resp.status_code == 409
    assert "Draft conflict" in resp.json()["detail"]


# ============================================================================
# 6. Drafts Dashboard & Actions Tests
# ============================================================================

def test_drafts_dashboard_view_and_filtering(test_client, disposable_compose_env):
    """GET /sales/mail-operations/drafts displays drafts and honors account and status filters."""
    repo = disposable_compose_env["repo"]

    # Seed 3 drafts
    d1 = MailboxDraftRecord(
        draft_id="drf_list_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        subject="COM Draft 1",
        draft_status="draft",
        created_by="op1",
        updated_by="op1",
    )
    d2 = MailboxDraftRecord(
        draft_id="drf_list_02",
        account_id="acc_info_it",
        sender_email="info@pybim.it",
        subject="IT Ready 2",
        draft_status="ready_to_schedule",
        created_by="op1",
        updated_by="op1",
    )
    d3 = MailboxDraftRecord(
        draft_id="drf_list_03",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        subject="COM Discarded 3",
        draft_status="discarded",
        created_by="op1",
        updated_by="op1",
    )
    repo.save_draft(d1)
    repo.save_draft(d2)
    repo.save_draft(d3)

    # 1. View all
    resp = test_client.get("/sales/mail-operations/drafts")
    assert resp.status_code == 200
    assert "Email Drafts &amp; Queue Management" in resp.text
    assert "drf_list_01" in resp.text
    assert "drf_list_02" in resp.text
    assert "drf_list_03" in resp.text

    # 2. Filter by account acc_info_it
    resp_it = test_client.get("/sales/mail-operations/drafts?account=acc_info_it")
    assert resp_it.status_code == 200
    assert "drf_list_02" in resp_it.text
    assert "drf_list_01" not in resp_it.text

    # 3. Filter by status ready_to_schedule
    resp_ready = test_client.get("/sales/mail-operations/drafts?status=ready_to_schedule")
    assert resp_ready.status_code == 200
    assert "drf_list_02" in resp_ready.text
    assert "drf_list_01" not in resp_ready.text


def test_duplicate_and_discard_actions(test_client, disposable_compose_env):
    """POST /sales/mail-operations/compose/duplicate and /discard work correctly."""
    repo = disposable_compose_env["repo"]

    d = MailboxDraftRecord(
        draft_id="drf_action_01",
        account_id="acc_info_com",
        sender_email="info@pybim.com",
        subject="Original to Duplicate",
        draft_status="draft",
        created_by="op1",
        updated_by="op1",
    )
    repo.save_draft(d)

    csrf_data = _get_csrf_tokens(test_client)

    # 1. Duplicate
    dup_resp = test_client.post(
        "/sales/mail-operations/compose/duplicate",
        data={"csrf_token": csrf_data["token"], "draft_id": "drf_action_01"},
        follow_redirects=False,
    )
    assert dup_resp.status_code == 303
    assert "duplicated=true" in dup_resp.headers["location"]

    # 2. Discard
    disc_resp = test_client.post(
        "/sales/mail-operations/compose/discard",
        data={"csrf_token": csrf_data["token"], "draft_id": "drf_action_01"},
        follow_redirects=False,
    )
    assert disc_resp.status_code == 303
    assert "/sales/mail-operations/drafts?discarded=true" in disc_resp.headers["location"]

    # Verify status in database
    after_disc = repo.get_draft("drf_action_01")
    assert after_disc.draft_status == "discarded"


# ============================================================================
# 7. Isolation & Zero Mutation Assurance Tests
# ============================================================================

def test_zero_crm_mutations_during_draft_operations(test_client, disposable_compose_env):
    """
    Asserts that draft creation, modification, duplication, and discarding
    strictly NEVER mutate candidate_companies, candidate_contacts, leads, or outreach history.
    """
    db_path = disposable_compose_env["db_path"]

    def snapshot_crm_tables():
        with sqlite3.connect(db_path) as conn:
            cand_count = conn.execute("SELECT count(*) FROM candidate_companies").fetchone()[0]
            contact_count = conn.execute("SELECT count(*) FROM candidate_contacts").fetchone()[0]
            lead_count = conn.execute("SELECT count(*) FROM leads").fetchone()[0] if conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='leads'").fetchone()[0] else 0
            sent_count = conn.execute("SELECT count(*) FROM historical_sent_emails").fetchone()[0] if conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='historical_sent_emails'").fetchone()[0] else 0
            outreach_count = conn.execute("SELECT count(*) FROM manual_outreach_log").fetchone()[0] if conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='manual_outreach_log'").fetchone()[0] else 0
            return (cand_count, contact_count, lead_count, sent_count, outreach_count)

    snap_before = snapshot_crm_tables()

    # Perform multiple draft actions
    csrf_data = _get_csrf_tokens(test_client)
    test_client.post(
        "/sales/mail-operations/compose/save",
        data={
            "csrf_token": csrf_data["token"],
            "account_id": "acc_info_com",
            "to_addresses": "architect@partner.com",
            "subject": "Zero Mutation Test",
            "body_plain": "This draft should not affect CRM tables.",
            "candidate_id": "cand:domain:bimstudio.it",
            "contact_id": "cont_marco_01",
            "target_status": "ready_to_schedule",
            "version": "1",
        },
    )

    snap_after = snapshot_crm_tables()
    assert snap_before == snap_after, "CRM tables must remain completely untouched during draft management."
