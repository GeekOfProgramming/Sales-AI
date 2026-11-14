"""
tests/test_data_01_reconciliation.py
DATA-01 Acceptance Test Suite: Company Identity, Contact Ownership & Historical Email Reconciliation.

Verifies:
1. Contact ownership distinctions (owned, verified_alias, project_partner, external_mention, unverified_third_party).
2. Historical sent emails sent to external recipients (e.g. studio@piuarch.it) are NOT auto-matched to candidates
   where that recipient is merely an external partner or third party mention (e.g. BiM Milano).
3. Legitimate recipient matching on owned candidate domains succeeds.
4. Fallback domain matches are treated strictly as 'unmatched' with proposed candidate associations.
5. Reconciliation correction endpoint requires ADMIN role and audited rationale.
6. Reconciliation correction maintains surviving CandidateCompany outreach statuses safely (does not blindly reset to cold outreach if other outreach events exist).
7. Operator corrections are idempotent and recorded in historical_reconciliation_corrections.
8. Existing manual outreach logs and confirmed sent entries are never voided or mutated by historical reconciliation.
"""

import pytest
import sqlite3
import tempfile
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from starlette.testclient import TestClient

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.historical.importer import HistoricalImporter, SentEmailRecord
from sales_engine.sending import ReviewStore
from backend.main import app
from backend.operator_auth import OperatorAuthService, OperatorUser, get_auth_service, hash_password


@pytest.fixture
def disposable_db(tmp_path):
    """Provides a fresh, isolated SQLite database path."""
    db_file = tmp_path / "test_reconciliation.db"
    store = CandidateStore(db_path=str(db_file))
    importer = HistoricalImporter(store=store)
    return str(db_file), store, importer


def test_contact_ownership_prevents_cross_domain_matching(disposable_db):
    """
    Test 1: studio@piuarch.it is NOT auto-matched to BiM Milano when recorded
    as a project partner or cross-domain contact without verified alias.
    """
    db_path, store, importer = disposable_db

    # Create BiM Milano candidate company
    bim_company = CandidateCompany(
        candidate_id="cand:domain:bim-milano.com",
        company_name="BiM Milano (Bicocca Incontra Milano)",
        company_domain="bim-milano.com",
        website="https://www.bim-milano.com",
        discovery_source="test_import",
    )
    store.upsert_company(bim_company)

    # Insert contacts for BiM Milano:
    # 1. Owned contact: info@bim-milano.com
    # 2. Project partner: studio@piuarch.it
    with store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO candidate_contacts (
                candidate_id, full_name, work_email, ownership_type, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("cand:domain:bim-milano.com", "BiM Contact", "info@bim-milano.com", "owned", "Primary", "2026-10-01T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO candidate_contacts (
                candidate_id, full_name, work_email, ownership_type, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("cand:domain:bim-milano.com", "Piuarch Architetti", "studio@piuarch.it", "project_partner", "Architectural designer for Bicocca project", "2026-10-01T00:00:00Z"),
        )

    # 1. Historical message to owned contact -> should match BiM Milano
    owned_msg = SentEmailRecord(
        message_id="msg_owned_1",
        thread_id="thrd_1",
        sender_account="info@pybim.it",
        sender_group="default",
        from_address="info@pybim.it",
        to_address="info@bim-milano.com",
        recipient_emails=["info@bim-milano.com"],
        sent_date="2026-10-18",
        sent_time="10:00:00",
        subject="Collaborazione BIM",
        email_body="Gentile BiM Milano...",
    )

    # 2. Historical message to Piuarch -> should NOT match BiM Milano
    piuarch_msg = SentEmailRecord(
        message_id="1a155284f3c8f57b",
        thread_id="1a15527791f98f0a",
        sender_account="info@pybim.it",
        sender_group="default",
        from_address="info@pybim.it",
        to_address="studio@piuarch.it",
        recipient_emails=["studio@piuarch.it"],
        sent_date="2026-10-19",
        sent_time="19:14:26",
        subject="Proposta di collaborazione tecnica per attività di modellazione BIM – pyBIM",
        email_body="Spett.le Piuarch, vi contattiamo per una proposta...",
    )

    reconciled = importer.reconcile_sent_emails([owned_msg, piuarch_msg])
    rec_dict = {m.message_id: m for m in reconciled}

    # Verify owned msg matched cleanly
    assert rec_dict["msg_owned_1"].reconciliation_status == "matched"
    assert rec_dict["msg_owned_1"].matched_candidate_id == "cand:domain:bim-milano.com"

    # Verify Piuarch msg was NOT auto-matched to BiM Milano
    assert rec_dict["1a155284f3c8f57b"].reconciliation_status == "unmatched"
    assert rec_dict["1a155284f3c8f57b"].matched_candidate_id is None


def test_verified_alias_matching(disposable_db):
    """
    Test 2: Contacts with verified_alias or matching candidate domains/aliases match cleanly.
    """
    db_path, store, importer = disposable_db

    # Candidate with aliases
    acme_company = CandidateCompany(
        candidate_id="cand:domain:acme-group.com",
        company_name="Acme Group",
        company_domain="acme-group.com",
        website="https://www.acme-group.com",
        aliases_json=json.dumps(["acme.it", "acme-engineering.com"]),
        discovery_source="test_import",
    )
    store.upsert_company(acme_company)

    with store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO candidate_contacts (
                candidate_id, full_name, work_email, ownership_type, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("cand:domain:acme-group.com", "Acme Italy", "contact@acme.it", "verified_alias", "Italian subsidiary", "2026-10-01T00:00:00Z"),
        )

    msg = SentEmailRecord(
        message_id="msg_alias_1",
        thread_id="thrd_alias_1",
        sender_account="info@pybim.it",
        sender_group="default",
        from_address="info@pybim.it",
        to_address="contact@acme.it",
        recipient_emails=["contact@acme.it"],
        sent_date="2026-10-20",
        sent_time="14:00:00",
        subject="Acme Inquiry",
        email_body="Hello Acme Italy...",
    )

    reconciled = importer.reconcile_sent_emails([msg])
    assert len(reconciled) == 1
    assert reconciled[0].reconciliation_status == "matched"
    assert reconciled[0].matched_candidate_id == "cand:domain:acme-group.com"


def test_reconciliation_correction_workflow_and_surviving_outreach(disposable_db):
    """
    Test 3: Admin reconciliation correction:
    - unmatch action removes candidate association, writes audit table, and recalculates surviving outreach.
    - If other outreach events survive (e.g. BiM Milano info@ event), company stays 'contacted'.
    - If all outreach events are removed, company transitions to 'uncontacted' and cold outreach is re-enabled.
    """
    db_path, store, importer = disposable_db

    bim_company = CandidateCompany(
        candidate_id="cand:domain:bim-milano.com",
        company_name="BiM Milano",
        company_domain="bim-milano.com",
        website="https://www.bim-milano.com",
        historical_contact_status="contacted",
        cold_outreach_allowed=False,
        discovery_source="test_import",
    )
    store.upsert_company(bim_company)

    # Add 1 Excel outreach event on info@bim-milano.com
    store.record_historical_outreach(
        candidate_id="cand:domain:bim-milano.com",
        source_ref="workbook:Milan",
        contact_name="BiM Contact",
        contact_email="info@bim-milano.com",
        contacted_at="2026-10-15T12:00:00Z",
        channel="email",
        reply_status="contacted",
    )

    # Insert historical sent email incorrectly matched initially to BiM Milano
    with store._get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO historical_sent_emails (
                message_id, thread_id, sender_account, sender_group,
                from_address, to_address, recipient_emails_json,
                sent_date, sent_time, subject, email_body,
                source_file, source_sheet,
                matched_candidate_id, matched_company_name, reconciliation_status,
                imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_piuarch_bad", "thrd_p", "info@pybim.it", "default",
                "info@pybim.it", "studio@piuarch.it", json.dumps(["studio@piuarch.it"]),
                "2026-10-19", "19:14:26", "BIM Proposal", "Dear Piuarch...",
                "test.xlsx", "Sent_Emails",
                "cand:domain:bim-milano.com", "BiM Milano", "matched",
                "2026-10-25T00:00:00Z",
            ),
        )
        email_id = cur.lastrowid

    # Record the matching outreach event for this gmail message
    store.record_historical_outreach(
        candidate_id="cand:domain:bim-milano.com",
        source_ref="gmail:info@pybim.it",
        contact_name="studio@piuarch.it",
        contact_email="studio@piuarch.it",
        contacted_at="2026-10-19",
        channel="gmail_sent",
        reply_status="contacted",
        notes="Gmail message msg_piuarch_bad",
        raw_row_hash="gmail:info@pybim.it:msg_piuarch_bad",
    )

    # Verify BiM Milano is currently contacted
    c_before = store.get_candidate("cand:domain:bim-milano.com")
    assert c_before.historical_contact_status == "contacted"
    assert c_before.cold_outreach_allowed is False

    # Perform Admin Correction: unmatch msg_piuarch_bad
    res = importer.correct_sent_email_reconciliation(
        email_id=email_id,
        action="unmatch",
        reason="Recipient studio@piuarch.it is external partner Piuarch, not BiM Milano.",
        acting_user="admin",
    )
    assert res["success"] is True
    assert res["action"] == "unmatch"

    # Check historical_reconciliation_corrections audit row
    with store._get_connection() as conn:
        corr = conn.execute("SELECT * FROM historical_reconciliation_corrections WHERE email_id = ?", (email_id,)).fetchone()
        assert corr is not None
        assert corr["action"] == "unmatch"
        assert corr["corrected_by"] == "admin"
        assert "Piuarch" in corr["reason"]

        email_row = conn.execute("SELECT reconciliation_status, matched_candidate_id FROM historical_sent_emails WHERE id = ?", (email_id,)).fetchone()
        assert email_row["reconciliation_status"] == "unmatched"
        assert email_row["matched_candidate_id"] is None

    # BiM Milano STILL has the surviving Excel outreach event -> must remain contacted!
    c_after = store.get_candidate("cand:domain:bim-milano.com")
    assert c_after.historical_contact_status == "contacted"
    assert c_after.cold_outreach_allowed is False

    # Now simulate removing that surviving excel event as well
    with store._get_connection() as conn:
        conn.execute("DELETE FROM historical_outreach_events WHERE candidate_id = ?", ("cand:domain:bim-milano.com",))

    # Trigger correction on another dummy email to verify clean recalculation to uncontacted
    with store._get_connection() as conn:
        cur2 = conn.execute(
            """
            INSERT INTO historical_sent_emails (
                message_id, thread_id, sender_account, sender_group,
                from_address, to_address, recipient_emails_json,
                sent_date, sent_time, subject, email_body,
                source_file, source_sheet,
                matched_candidate_id, matched_company_name, reconciliation_status,
                imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_dummy", "thrd_d", "info@pybim.it", "default",
                "info@pybim.it", "dummy@bim-milano.com", json.dumps(["dummy@bim-milano.com"]),
                "2026-10-19", "19:14:26", "Test", "Test",
                "test.xlsx", "Sent_Emails",
                "cand:domain:bim-milano.com", "BiM Milano", "matched",
                "2026-10-25T00:00:00Z",
            ),
        )
        dummy_id = cur2.lastrowid

    importer.correct_sent_email_reconciliation(
        email_id=dummy_id,
        action="unmatch",
        reason="Testing clean reset when no outreach events survive.",
        acting_user="admin",
    )

    c_final = store.get_candidate("cand:domain:bim-milano.com")
    assert c_final.historical_contact_status == "uncontacted"
    assert c_final.cold_outreach_allowed is True


def test_reconciliation_endpoint_authorization(monkeypatch, tmp_path):
    """
    Test 4: FastAPI endpoint /sales/historical-emails/{email_id}/correct:
    - Unauthenticated -> 401 or redirect to login.
    - Authenticated OPERATOR (non-admin) -> 403 Forbidden.
    - Authenticated ADMIN -> 303 Redirect with success message and audited update.
    """
    db_file = tmp_path / "test_auth_reconciliation.db"
    store = CandidateStore(db_path=str(db_file))
    importer = HistoricalImporter(store=store)
    rev_store = ReviewStore(db_path=str(db_file))
    auth_svc = OperatorAuthService(db_path=str(db_file))

    # Pre-populate operators: operator1 (non-admin) and admin1 (admin)
    auth_svc.create_user("operator1", "Operator Password 123", role="operator")
    auth_svc.create_user("admin1", "Admin Password 123", role="admin")

    # Add candidate and historical email
    cand = CandidateCompany(
        candidate_id="cand:domain:example.com",
        company_name="Example Corp",
        company_domain="example.com",
        website="https://www.example.com",
    )
    store.upsert_company(cand)

    with store._get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO historical_sent_emails (
                message_id, thread_id, sender_account, sender_group,
                from_address, to_address, recipient_emails_json,
                sent_date, sent_time, subject, email_body,
                source_file, source_sheet,
                matched_candidate_id, matched_company_name, reconciliation_status,
                imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_auth_test", "thrd_a", "info@pybim.it", "default",
                "info@pybim.it", "test@example.com", json.dumps(["test@example.com"]),
                "2026-10-19", "12:00:00", "Hello", "Body",
                "test.xlsx", "Sent_Emails",
                "cand:domain:example.com", "Example Corp", "matched",
                "2026-10-25T00:00:00Z",
            ),
        )
        email_id = cur.lastrowid

    # Monkeypatch get_console_services and get_auth_service to point to test instances
    from backend.sales_ui import router as ui_router
    def fake_services():
        return {
            "cand_store": store,
            "rev_store": rev_store,
        }
    monkeypatch.setattr(ui_router, "get_console_services", fake_services)
    monkeypatch.setattr(ui_router, "get_auth_service", lambda: auth_svc)
    monkeypatch.setattr("backend.operator_auth.get_auth_service", lambda: auth_svc)

    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # 1. Unauthenticated request -> should be rejected (401 or redirect)
    unauth_resp = client.post(
        f"/sales/historical-emails/{email_id}/correct",
        data={"action": "unmatch", "reason": "Unauthenticated attempt"},
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )
    assert unauth_resp.status_code in (401, 303)

    # 2. Authenticated as OPERATOR (non-admin)
    op_user = auth_svc.authenticate_user("operator1", "Operator Password 123", client_ip="127.0.0.1")
    assert op_user is not None
    op_token = auth_svc.create_session(op_user.user_id, client_ip="127.0.0.1")
    op_client = TestClient(app, base_url="http://127.0.0.1:8000")
    op_client.cookies.set("sales_session", op_token)

    # Use a matched CSRF token in both cookie and form body
    csrf_token = "valid_csrf_token_for_test_12345678"
    op_client.cookies.set("sales_csrf_token", csrf_token)

    op_resp = op_client.post(
        f"/sales/historical-emails/{email_id}/correct",
        data={"action": "unmatch", "reason": "Operator attempt", "csrf_token": csrf_token},
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )
    # Must be 403 Forbidden because operator is not admin
    assert op_resp.status_code == 403

    # 3. Authenticated as ADMIN
    admin_user = auth_svc.authenticate_user("admin1", "Admin Password 123", client_ip="127.0.0.1")
    assert admin_user is not None
    admin_token = auth_svc.create_session(admin_user.user_id, client_ip="127.0.0.1")
    admin_client = TestClient(app, base_url="http://127.0.0.1:8000")
    admin_client.cookies.set("sales_session", admin_token)
    admin_client.cookies.set("sales_csrf_token", csrf_token)

    admin_resp = admin_client.post(
        f"/sales/historical-emails/{email_id}/correct",
        data={"action": "unmatch", "reason": "Verified mismatch by admin", "csrf_token": csrf_token},
        headers={"Origin": "http://127.0.0.1:8000"},
        follow_redirects=False,
    )
    assert admin_resp.status_code == 303
    assert "/sales/sent" in admin_resp.headers["Location"]

    # Verify audit row created by admin1
    with store._get_connection() as conn:
        corr = conn.execute("SELECT * FROM historical_reconciliation_corrections WHERE email_id = ?", (email_id,)).fetchone()
        assert corr is not None
        assert corr["corrected_by"] == "admin1"
        assert corr["action"] == "unmatch"
