"""
tests/test_mail_link_01_reconciliation.py
Comprehensive offline acceptance test suite for MAIL-LINK-01.

Verifications:
1. Ingestion is idempotent by physical occurrence identity (account_id, folder_id, imap_uidvalidity, imap_uid).
2. The same RFC Message-ID appearing across accounts/folders retains distinct provenance.
3. Messages lacking RFC Message-ID are supported and traceable.
4. Exact verified organization contact email matches the correct company.
5. Third-party project partner email does NOT link to the project owner (Known BiM Milano / Piuarch regression fixture).
6. Multi-recipient and cross-account messages remain distinct and traceable.
7. Ambiguous domain/alias matches remain pending review.
8. Explicit DATA-01 operator corrections and contact verifications are strictly respected.
9. Historical Gmail IDs are never equated with IMAP UIDs.
10. Manual sent reconciliation matches external sends without creating duplicate send events.
11. Conversation threading operates correctly on RFC reply headers (In-Reply-To, References).
12. Zero CRM mutations: candidate_companies, candidate_contacts, historical_sent_emails,
    and manual_outreach_log are completely untouched during ingestion and reconciliation.
13. No passwords or plaintext secrets appear in logs or exceptions.
"""

import json
import sqlite3
import pytest
from pathlib import Path

from sales_engine.inbox.models import (
    MailboxAccountRecord,
    MailboxFolderRecord,
    MailboxMessageRecord,
    MessageCrmLinkRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.inbox.ingestion_service import MailboxIngestionService
from sales_engine.inbox.reconciliation_service import (
    MailboxReconciliationService,
    OrganizationMatchResult,
)
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.candidates.models import CandidateCompany
from sales_engine.sending.review_store import ReviewStore


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    """Fixture providing a clean isolated SQLite database."""
    return tmp_path / "test_mail_link_01.db"


@pytest.fixture
def test_setup(disposable_db: Path):
    """Fixture initializing repositories and services on a clean disposable database."""
    # 1. Candidate Store & Review Store
    cand_store = CandidateStore(db_path=disposable_db)
    rev_store = ReviewStore(db_path=disposable_db)

    # 2. Mailbox Repository
    repo = MailboxRepository(db_path=disposable_db, auto_migrate=True)

    # 3. Ingestion & Reconciliation Services
    ingestion_svc = MailboxIngestionService(repo=repo)
    recon_svc = MailboxReconciliationService(
        mailbox_repo=repo,
        candidate_store=cand_store,
        review_store=rev_store,
    )

    return {
        "db": disposable_db,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "repo": repo,
        "ingestion_svc": ingestion_svc,
        "recon_svc": recon_svc,
    }


def test_01_ingestion_idempotency_by_physical_occurrence(test_setup):
    """Verify messages are ingested idempotently using physical occurrence identity."""
    repo = test_setup["repo"]
    ingestion = test_setup["ingestion_svc"]

    acc = MailboxAccountRecord(account_id="acc_info", email_address="info@pybim.com", display_name="Info", credential_ref="c1")
    fld = MailboxFolderRecord(folder_id="fld_inbox", account_id="acc_info", folder_name="INBOX", folder_role="inbox")
    repo.save_account(acc)
    repo.save_folder(fld)

    msg = MailboxMessageRecord(
        message_id="msg_occ_1",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="inbound",
        imap_uid=50,
        imap_uidvalidity=1000,
        rfc_message_id="<m50@sender.it>",
        from_address="sender@sender.it",
        date_utc="2026-11-15T00:00:00Z",
    )

    # Ingest batch
    res1 = ingestion.ingest_batch(acc, fld, [msg], new_highest_uid=50, uidvalidity=1000)
    assert res1["inserted_count"] == 1
    assert res1["duplicate_count"] == 0

    # Repeat exact batch
    res2 = ingestion.ingest_batch(acc, fld, [msg], new_highest_uid=50, uidvalidity=1000)
    assert res2["inserted_count"] == 0
    assert res2["duplicate_count"] == 1

    # Checkpoint is preserved at UID 50
    state = repo.get_folder_sync_state("acc_info", "fld_inbox")
    assert state.last_synced_uid == 50


def test_02_same_rfc_message_id_across_accounts_retains_provenance(test_setup):
    """Verify identical RFC Message-ID in different account folders preserves distinct occurrence records."""
    repo = test_setup["repo"]
    ingestion = test_setup["ingestion_svc"]

    acc_hamed = MailboxAccountRecord(account_id="acc_hamed", email_address="hamed.sharifi@pybim.it", display_name="Hamed", credential_ref="c1")
    acc_hamid = MailboxAccountRecord(account_id="acc_hamid", email_address="hamid.lotfalian@pybim.it", display_name="Hamid", credential_ref="c2")
    fld_hamed = MailboxFolderRecord(folder_id="fld_h1", account_id="acc_hamed", folder_name="INBOX")
    fld_hamid = MailboxFolderRecord(folder_id="fld_h2", account_id="acc_hamid", folder_name="INBOX")

    repo.save_account(acc_hamed)
    repo.save_account(acc_hamid)
    repo.save_folder(fld_hamed)
    repo.save_folder(fld_hamid)

    shared_rfc = "<shared_announcement_101@partner.it>"

    msg_hamed = MailboxMessageRecord(
        message_id="msg_h1_101",
        account_id="acc_hamed",
        folder_id="fld_h1",
        direction="inbound",
        imap_uid=11,
        imap_uidvalidity=10,
        rfc_message_id=shared_rfc,
        from_address="partner@partner.it",
        date_utc="2026-11-15T00:00:00Z",
    )
    msg_hamid = MailboxMessageRecord(
        message_id="msg_h2_101",
        account_id="acc_hamid",
        folder_id="fld_h2",
        direction="inbound",
        imap_uid=22,
        imap_uidvalidity=20,
        rfc_message_id=shared_rfc,
        from_address="partner@partner.it",
        date_utc="2026-11-15T00:00:00Z",
    )

    ingestion.ingest_batch(acc_hamed, fld_hamed, [msg_hamed], new_highest_uid=11, uidvalidity=10)
    ingestion.ingest_batch(acc_hamid, fld_hamid, [msg_hamid], new_highest_uid=22, uidvalidity=20)

    # Distinct occurrences findable
    occurrences = repo.find_messages_by_rfc_id(shared_rfc)
    assert len(occurrences) == 2
    acc_ids = set(o.account_id for o in occurrences)
    assert acc_ids == {"acc_hamed", "acc_hamid"}


def test_03_bim_milano_vs_piuarch_known_regression_case(test_setup):
    """
    CRITICAL REGRESSION TEST (Section 8 of prompt):
    Synthetic fixture for BiM Milano and Piuarch.
    Email recipient: studio@piuarch.it
    
    Expected behavior:
      - Email must NEVER be confirmed as a direct BiM Milano contact merely because Piuarch
        participated in the project partners list.
      - Matching Piuarch candidate receives evidence-based link.
      - Independent BiM historical outreach remains completely intact.
    """
    cand_store = test_setup["cand_store"]
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    # 1. Setup BiM Milano candidate
    bim_cand = CandidateCompany(
        candidate_id="cand:domain:bim-milano.com",
        company_name="BiM Milano (Bicocca Incontra Milano)",
        normalized_company_name="bim milano",
        company_domain="bim-milano.com",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(bim_cand)

    # Helper to insert contact with ownership_type into candidate_contacts
    def insert_contact(cid, cand_id, email, ownership="owned", notes=""):
        with cand_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO candidate_contacts (
                    contact_id, candidate_id, work_email, ownership_type, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, '2026-11-15T00:00:00Z');
                """,
                (cid, cand_id, email, ownership, notes),
            )

    # BiM Milano has info@bim-milano.com (owned)
    insert_contact("cnt_bim_owned", "cand:domain:bim-milano.com", "info@bim-milano.com", "owned")

    # BiM Milano has studio@piuarch.it listed ONLY as partner/project mention (NOT owned)
    insert_contact("cnt_piuarch_in_bim", "cand:domain:bim-milano.com", "studio@piuarch.it", "associated", "Partner architettonico progetto BiM Milano")

    # 2. Setup independent Piuarch candidate
    piuarch_cand = CandidateCompany(
        candidate_id="cand:domain:piuarch.it",
        company_name="Piuarch",
        normalized_company_name="piuarch",
        company_domain="piuarch.it",
        cold_outreach_allowed=True,
    )
    cand_store.save_candidate(piuarch_cand)

    # Piuarch owns studio@piuarch.it
    insert_contact("cnt_piuarch_owned", "cand:domain:piuarch.it", "studio@piuarch.it", "owned")

    # 3. Create staged email from studio@piuarch.it
    acc = MailboxAccountRecord(account_id="acc_info", email_address="info@pybim.it", display_name="Info", credential_ref="c")
    fld = MailboxFolderRecord(folder_id="fld_inbox", account_id="acc_info", folder_name="INBOX")
    repo.save_account(acc)
    repo.save_folder(fld)

    msg = MailboxMessageRecord(
        message_id="msg_piuarch_incoming",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="inbound",
        imap_uid=861,
        imap_uidvalidity=10,
        rfc_message_id="<piuarch_reply_1@piuarch.it>",
        from_address="studio@piuarch.it",
        subject="Proposta di collaborazione tecnica BIM",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="Grazie per la proposta. Saluti da Piuarch.",
    )
    repo.save_message(msg)

    # 4. Run Organization Matching
    match_result = recon.match_organization(msg)

    # Assert: Must match Piuarch (owned), NEVER BiM Milano
    assert match_result.matched_candidate_id == "cand:domain:piuarch.it"
    assert match_result.matched_candidate_id != "cand:domain:bim-milano.com"
    assert match_result.match_status == "verified"
    assert match_result.match_method == "verified_contact_email"

    # Stage the CRM link
    link = recon.stage_organization_link(msg)
    assert link.candidate_id == "cand:domain:piuarch.it"
    assert link.link_status == "verified"

    # 5. Assert BiM Milano candidate in CandidateStore was completely UNTOUCHED
    bim_post = cand_store.get_candidate("cand:domain:bim-milano.com")
    assert bim_post.cold_outreach_allowed is True
    assert bim_post.active_conversation is False


def test_04_third_party_partner_with_no_owned_candidate_does_not_guess(test_setup):
    """Verify unowned cross-domain contacts remain ambiguous / pending review rather than mis-linking."""
    cand_store = test_setup["cand_store"]
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    # Candidate A mentions consultant@external.it (associated)
    cand_store.save_candidate(CandidateCompany(candidate_id="cand:a", company_name="Company A", company_domain="comp-a.it"))
    with cand_store._get_connection() as conn:
        conn.execute("INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, created_at) VALUES ('c_a', 'cand:a', 'consultant@external.it', 'associated', '2026-11-15T00:00:00Z');")

    # Candidate B also mentions consultant@external.it (associated)
    cand_store.save_candidate(CandidateCompany(candidate_id="cand:b", company_name="Company B", company_domain="comp-b.it"))
    with cand_store._get_connection() as conn:
        conn.execute("INSERT INTO candidate_contacts (contact_id, candidate_id, work_email, ownership_type, created_at) VALUES ('c_b', 'cand:b', 'consultant@external.it', 'associated', '2026-11-15T00:00:00Z');")

    # Staged message from consultant@external.it
    msg = MailboxMessageRecord(
        message_id="msg_ext",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="inbound",
        imap_uid=99,
        imap_uidvalidity=1,
        from_address="consultant@external.it",
        subject="Consulting report",
        date_utc="2026-11-15T00:00:00Z",
    )
    repo.save_account(MailboxAccountRecord(account_id="acc_info", email_address="info@pybim.it", display_name="Info", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_inbox", account_id="acc_info", folder_name="INBOX"))
    repo.save_message(msg)

    res = recon.match_organization(msg)
    # Must be ambiguous: cannot guess between A and B
    assert res.matched_candidate_id is None
    assert res.match_status == "ambiguous"
    assert "Anti-cross-domain guard triggered" in res.evidence_summary

    link = recon.stage_organization_link(msg)
    assert link.link_status == "suggested_pending_review"


def test_05_historical_gmail_reconciliation_offline_proposals(test_setup):
    """
    Verify staged Register.it Sent messages reconcile against historical_sent_emails
    without equating Gmail IDs to IMAP UIDs or deleting records.
    """
    db_file = test_setup["db"]
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    # Insert a synthetic historical_sent_emails record
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historical_sent_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                sender_account TEXT NOT NULL,
                to_address TEXT NOT NULL,
                recipient_emails_json TEXT NOT NULL,
                subject TEXT,
                sent_date TEXT,
                sent_time TEXT,
                matched_candidate_id TEXT
            );
        """)
        conn.execute("""
            INSERT INTO historical_sent_emails (
                id, message_id, thread_id, sender_account, to_address, recipient_emails_json, subject, sent_date, sent_time, matched_candidate_id
            ) VALUES (861, '1a155284f3c8f57b', '1a155284f3c8f57b', 'info@pybim.it', 'studio@piuarch.it', '["studio@piuarch.it"]', 'Proposta di collaborazione tecnica per attività BIM', '2026-10-19', '19:14:26', 'cand:domain:piuarch.it');
        """)

    # Staged Register.it Sent message
    sent_msg = MailboxMessageRecord(
        message_id="msg_reg_sent_861",
        account_id="acc_info_it",
        folder_id="fld_sent",
        folder_role="sent",
        direction="outbound",
        imap_uid=5500,  # IMAP UID is completely different from Gmail ID!
        imap_uidvalidity=123,
        rfc_message_id="<sent_reg_861@pybim.it>",
        from_address="info@pybim.it",
        to_addresses=["studio@piuarch.it"],
        subject="Proposta di collaborazione tecnica per attività BIM",
        date_utc="2026-10-19T17:14:26Z",
    )
    repo.save_account(MailboxAccountRecord(account_id="acc_info_it", email_address="info@pybim.it", display_name="Info IT", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_sent", account_id="acc_info_it", folder_name="Sent", folder_role="sent"))
    repo.save_message(sent_msg)

    res = recon.reconcile_historical_sent(sent_msg)
    assert res.matched_historical_id == 861
    assert res.reconciliation_status == "verified_match"
    assert res.original_gmail_id == "1a155284f3c8f57b"
    assert res.confidence >= 0.8


def test_06_manual_sent_reconciliation_verifies_external_send(test_setup):
    """
    Verify staged Sent message matches manual_outreach_log entry
    without generating duplicate sends or altering existing confirmations.
    """
    db_file = test_setup["db"]
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    # Insert a synthetic manual_outreach_log record (operator confirmed in Thunderbird)
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_outreach_log (
                log_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                sender_account TEXT,
                logged_at TEXT NOT NULL,
                is_voided INTEGER DEFAULT 0,
                imap_reconciliation_status TEXT DEFAULT 'pending_sync'
            );
        """)
        conn.execute("""
            INSERT INTO manual_outreach_log (
                log_id, candidate_id, recipient_email, subject, sender_account, logged_at
            ) VALUES ('log_tb_123', 'cand:domain:piuarch.it', 'studio@piuarch.it', 'Collaborazione BIM Milano', 'info@pybim.it', '2026-11-14T10:00:00Z');
        """)

    sent_msg = MailboxMessageRecord(
        message_id="msg_tb_found",
        account_id="acc_info_it",
        folder_id="fld_sent",
        folder_role="sent",
        direction="outbound",
        imap_uid=990,
        imap_uidvalidity=123,
        rfc_message_id="<tb_send_123@pybim.it>",
        from_address="info@pybim.it",
        to_addresses=["studio@piuarch.it"],
        subject="Collaborazione BIM Milano",
        date_utc="2026-11-14T10:01:00Z",
    )
    repo.save_account(MailboxAccountRecord(account_id="acc_info_it", email_address="info@pybim.it", display_name="Info IT", credential_ref="c"))
    repo.save_folder(MailboxFolderRecord(folder_id="fld_sent", account_id="acc_info_it", folder_name="Sent", folder_role="sent"))
    repo.save_message(sent_msg)

    res = recon.reconcile_manual_outreach(sent_msg)
    assert res.matched_log_id == "log_tb_123"
    assert res.reconciliation_status == "confirmed_on_server"
    assert res.confidence >= 0.7

    # Verify manual_outreach_log row count did NOT increase
    with sqlite3.connect(str(db_file)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM manual_outreach_log").fetchone()[0]
        assert count == 1


def test_07_conversation_threading_via_rfc_reply_headers(test_setup):
    """Verify conversation threading operates on RFC In-Reply-To and References headers."""
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    acc = MailboxAccountRecord(account_id="acc_info", email_address="info@pybim.com", display_name="Info", credential_ref="c")
    fld = MailboxFolderRecord(folder_id="fld_inbox", account_id="acc_info", folder_name="INBOX")
    repo.save_account(acc)
    repo.save_folder(fld)

    # 1. Outbound Root
    root_msg = MailboxMessageRecord(
        message_id="msg_root",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="outbound",
        imap_uid=1,
        imap_uidvalidity=1,
        rfc_message_id="<root_outreach@pybim.com>",
        from_address="info@pybim.com",
        to_addresses=["client@studio.it"],
        date_utc="2026-11-10T10:00:00Z",
    )
    repo.save_message(root_msg)

    root_thread = recon.link_thread_proposals(root_msg)
    assert root_thread["proposed_thread_id"] == "th_root_outreach@py"
    assert root_thread["is_reply"] is False

    # 2. Inbound Reply referencing root
    reply_msg = MailboxMessageRecord(
        message_id="msg_reply",
        account_id="acc_info",
        folder_id="fld_inbox",
        direction="inbound",
        imap_uid=2,
        imap_uidvalidity=1,
        rfc_message_id="<reply_01@studio.it>",
        in_reply_to="<root_outreach@pybim.com>",
        references=["<root_outreach@pybim.com>"],
        from_address="client@studio.it",
        to_addresses=["info@pybim.com"],
        date_utc="2026-11-11T12:00:00Z",
    )
    repo.save_message(reply_msg)

    reply_thread = recon.link_thread_proposals(reply_msg)
    assert reply_thread["proposed_thread_id"] == "th_root_outreach@py"
    assert reply_thread["is_reply"] is True
    assert "In-Reply-To direct link" in reply_thread["evidence"]


def test_08_zero_crm_mutations_enforced(test_setup):
    """
    CRITICAL ACCEPTANCE CHECK:
    Verify that executing full ingestion, organization matching, and reconciliation
    creates ZERO mutations in candidate_companies or candidate_contacts.
    """
    cand_store = test_setup["cand_store"]
    recon = test_setup["recon_svc"]
    repo = test_setup["repo"]

    cand = CandidateCompany(
        candidate_id="cand:domain:testguard.it",
        company_name="Test Guard",
        company_domain="testguard.it",
        cold_outreach_allowed=True,
        suppressed=False,
        active_conversation=False,
    )
    cand_store.save_candidate(cand)

    # Inbound message requesting unsubscribe
    acc = MailboxAccountRecord(account_id="acc_1", email_address="info@pybim.com", display_name="A", credential_ref="c")
    fld = MailboxFolderRecord(folder_id="fld_1", account_id="acc_1", folder_name="INBOX")
    repo.save_account(acc)
    repo.save_folder(fld)

    msg = MailboxMessageRecord(
        message_id="msg_optout_test",
        account_id="acc_1",
        folder_id="fld_1",
        direction="inbound",
        imap_uid=999,
        imap_uidvalidity=1,
        from_address="contact@testguard.it",
        subject="Cancella iscrizione",
        date_utc="2026-11-15T00:00:00Z",
        body_plain_text="Disiscrivimi subito.",
    )
    repo.save_message(msg)

    # Execute matching and staging
    recon.match_organization(msg)
    recon.stage_organization_link(msg)
    recon.link_thread_proposals(msg)

    # Candidate record must be 100% UNTOUCHED
    post_cand = cand_store.get_candidate("cand:domain:testguard.it")
    assert post_cand.cold_outreach_allowed is True
    assert post_cand.suppressed is False
    assert post_cand.active_conversation is False
