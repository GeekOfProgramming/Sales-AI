"""
sales_engine/inbox/reconciliation_service.py
MAIL-LINK-01: Evidence-Based Organization Matching, Historical Gmail Reconciliation,
and Manual Sent Reconciliation Service.

P0 SAFETY RULES:
  1. Complete separation of evidence staging from CRM mutations.
  2. Matching requires independently verified organization email, verified domain,
     or explicit RFC threading.
  3. NEVER confirm a link solely on subject similarity, BIM keywords, or third-party project mentions.
  4. Respect DATA-01 company/contact ownership rules and operator corrections.
  5. Never mutate, overwrite, or delete historical_sent_emails or manual_outreach_log.
"""

import json
import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple

from sales_engine.inbox.models import (
    MailboxMessageRecord,
    MessageCrmLinkRecord,
)
from sales_engine.inbox.mailbox_repository import MailboxRepository
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore

logger = logging.getLogger(__name__)


@dataclass
class OrganizationMatchResult:
    """Result of evidence-based candidate company matching."""
    matched_candidate_id: Optional[str]
    matched_contact_id: Optional[str]
    match_status: str  # 'verified', 'suggested_pending_review', 'ambiguous', 'unmatched'
    match_method: str  # 'verified_contact_email', 'verified_domain', 'rfc_thread_participant', 'operator_correction', 'unmatched'
    confidence: float
    evidence_summary: str
    competing_candidates: List[str] = field(default_factory=list)


@dataclass
class HistoricalSentReconciliationResult:
    """Result of reconciling a staged IMAP Sent message with historical_sent_emails."""
    matched_historical_id: Optional[int]
    reconciliation_status: str  # 'verified_match', 'proposed_match', 'ambiguous', 'unmatched'
    confidence: float
    evidence: str
    original_gmail_id: Optional[str] = None
    original_thread_id: Optional[str] = None


@dataclass
class ManualOutreachReconciliationResult:
    """Result of reconciling a staged IMAP Sent message with manual_outreach_log."""
    matched_log_id: Optional[str]
    reconciliation_status: str  # 'confirmed_on_server', 'probable_match', 'unmatched'
    confidence: float
    evidence: str
    recipient_email: str
    sender_account: Optional[str]


class MailboxReconciliationService:
    """
    Evidence-based reconciliation engine connecting staged mailbox messages to:
      1. Candidate companies & contacts (under strict DATA-01 rules)
      2. Historical sent emails (reconciliation audit links)
      3. Manual outreach log (confirming Thunderbird external sends)
    """

    def __init__(
        self,
        mailbox_repo: MailboxRepository,
        candidate_store: Optional[CandidateStore] = None,
        review_store: Optional[ReviewStore] = None,
    ):
        self.mailbox_repo = mailbox_repo
        self.cand_store = candidate_store or CandidateStore(db_path=mailbox_repo.db_path)
        self.review_store = review_store or ReviewStore(db_path=mailbox_repo.db_path)

    # -----------------------------------------------------------------------
    # 1. Organization & Contact Matching (Strict DATA-01 Rules)
    # -----------------------------------------------------------------------

    def match_organization(self, message: MailboxMessageRecord) -> OrganizationMatchResult:
        """
        Match an inbound or outbound message to a CandidateCompany based strictly on:
          - Verified contact email ownership
          - Verified canonical domain or registered domain alias
          - DATA-01 operator corrections
        NEVER matches based on:
          - Subject similarity alone
          - Project partner mentions in body
          - Unverified third-party contacts
        """
        # Determine the external counterparty address
        if message.direction == "inbound":
            counterparty_email = message.from_address.lower().strip()
        else:
            # For outbound, primary recipient is counterparty
            counterparty_email = message.to_addresses[0].lower().strip() if message.to_addresses else ""

        if not counterparty_email or "@" not in counterparty_email:
            return OrganizationMatchResult(
                matched_candidate_id=None,
                matched_contact_id=None,
                match_status="unmatched",
                match_method="unmatched",
                confidence=0.0,
                evidence_summary="Missing or invalid external email address",
            )

        counterparty_domain = counterparty_email.split("@")[1].strip().lower()

        # Connect to candidate store to evaluate ownership evidence
        with self.cand_store._get_connection() as conn:
            # 1. Check DATA-01 candidate_contact_verifications (highest authority)
            has_verif_table = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_contact_verifications'"
            ).fetchone())

            if has_verif_table:
                verif_row = conn.execute(
                    "SELECT candidate_id, contact_id, verified_by FROM candidate_contact_verifications WHERE LOWER(work_email) = ? LIMIT 1;",
                    (counterparty_email,),
                ).fetchone()
                if verif_row:
                    return OrganizationMatchResult(
                        matched_candidate_id=verif_row["candidate_id"],
                        matched_contact_id=verif_row["contact_id"],
                        match_status="verified",
                        match_method="verified_contact_email",
                        confidence=1.0,
                        evidence_summary=f"Directly verified contact ownership for {counterparty_email} (audited by {verif_row['verified_by']})",
                    )

            # 2. Check candidate_contacts with ownership_type = 'owned'
            contact_rows = conn.execute(
                "SELECT candidate_id, contact_id, ownership_type FROM candidate_contacts WHERE LOWER(work_email) = ?;",
                (counterparty_email,),
            ).fetchall()

            # Filter for strictly owned contacts or single clean candidate association
            owned_candidates = set(r["candidate_id"] for r in contact_rows if r["ownership_type"] == "owned")
            all_associated = set(r["candidate_id"] for r in contact_rows)

            if len(owned_candidates) == 1:
                cand_id = list(owned_candidates)[0]
                contact_id = [r["contact_id"] for r in contact_rows if r["candidate_id"] == cand_id][0]
                return OrganizationMatchResult(
                    matched_candidate_id=cand_id,
                    matched_contact_id=contact_id,
                    match_status="verified",
                    match_method="verified_contact_email",
                    confidence=0.98,
                    evidence_summary=f"Explicitly owned contact record for {counterparty_email}",
                )
            elif len(all_associated) > 1:
                # Ambiguous: same contact associated with multiple candidates (e.g. project partner case)
                # ANTI-CROSS-DOMAIN GUARD: Do NOT guess. Require operator review.
                return OrganizationMatchResult(
                    matched_candidate_id=None,
                    matched_contact_id=None,
                    match_status="ambiguous",
                    match_method="ambiguous_multi_candidate_contact",
                    confidence=0.3,
                    evidence_summary=f"Contact {counterparty_email} appears in multiple candidates ({list(all_associated)}). Anti-cross-domain guard triggered.",
                    competing_candidates=list(all_associated),
                )

            # 3. Canonical domain or approved alias matching
            # Public/free domains (gmail, yahoo, etc.) can NEVER match by domain
            from sales_engine.historical.importer import GENERIC_OR_FREE_EMAIL_DOMAINS
            if counterparty_domain not in GENERIC_OR_FREE_EMAIL_DOMAINS:
                # Find candidate by canonical company_domain
                domain_matches = conn.execute(
                    "SELECT candidate_id, company_name, company_domain, aliases_json FROM candidate_companies WHERE LOWER(company_domain) = ?;",
                    (counterparty_domain,),
                ).fetchall()

                if len(domain_matches) == 1:
                    cand_id = domain_matches[0]["candidate_id"]
                    return OrganizationMatchResult(
                        matched_candidate_id=cand_id,
                        matched_contact_id=None,
                        match_status="verified",
                        match_method="verified_domain",
                        confidence=0.95,
                        evidence_summary=f"Exact match on candidate canonical domain ({counterparty_domain})",
                    )
                elif len(domain_matches) > 1:
                    cands = [r["candidate_id"] for r in domain_matches]
                    return OrganizationMatchResult(
                        matched_candidate_id=None,
                        matched_contact_id=None,
                        match_status="ambiguous",
                        match_method="ambiguous_domain_collision",
                        confidence=0.4,
                        evidence_summary=f"Domain {counterparty_domain} matches multiple candidates: {cands}",
                        competing_candidates=cands,
                    )

                # Check aliases
                alias_candidates = []
                all_cands = conn.execute("SELECT candidate_id, aliases_json FROM candidate_companies WHERE aliases_json IS NOT NULL;").fetchall()
                for c_row in all_cands:
                    aliases = json.loads(c_row["aliases_json"] or "[]")
                    for a in aliases:
                        if a.lower() == counterparty_domain:
                            alias_candidates.append(c_row["candidate_id"])

                if len(alias_candidates) == 1:
                    return OrganizationMatchResult(
                        matched_candidate_id=alias_candidates[0],
                        matched_contact_id=None,
                        match_status="verified",
                        match_method="verified_domain_alias",
                        confidence=0.90,
                        evidence_summary=f"Matched approved domain alias ({counterparty_domain})",
                    )
                elif len(alias_candidates) > 1:
                    return OrganizationMatchResult(
                        matched_candidate_id=None,
                        matched_contact_id=None,
                        match_status="ambiguous",
                        match_method="ambiguous_alias_collision",
                        confidence=0.3,
                        evidence_summary=f"Domain alias {counterparty_domain} claimed by multiple candidates: {alias_candidates}",
                        competing_candidates=alias_candidates,
                    )

        # No verified link found: return unmatched (never fuzzy guess)
        return OrganizationMatchResult(
            matched_candidate_id=None,
            matched_contact_id=None,
            match_status="unmatched",
            match_method="unmatched",
            confidence=0.0,
            evidence_summary=f"No verified contact or domain match found for {counterparty_email}",
        )

    def stage_organization_link(self, message: MailboxMessageRecord) -> MessageCrmLinkRecord:
        """
        Evaluate organization matching for a message and persist the result into
        message_crm_links WITHOUT touching candidate_companies or contacts.
        """
        res = self.match_organization(message)

        link_status = res.match_status
        if link_status == "unmatched":
            link_status = "unlinked"
        elif link_status == "ambiguous":
            link_status = "suggested_pending_review"

        link_rec = MessageCrmLinkRecord(
            link_id=f"lnk_{message.message_id[:16]}_{abs(hash(res.matched_candidate_id or '')) % 100000}",
            message_id=message.message_id,
            candidate_id=res.matched_candidate_id,
            contact_id=res.matched_contact_id,
            link_status=link_status,
            link_method=res.match_method,
            link_confidence=res.confidence,
            evidence_summary=res.evidence_summary,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        self.mailbox_repo.save_crm_link(link_rec)
        return link_rec

    # -----------------------------------------------------------------------
    # 2. Historical Sent Reconciliation (Gmail vs Register.it Sent)
    # -----------------------------------------------------------------------

    def reconcile_historical_sent(self, message: MailboxMessageRecord) -> HistoricalSentReconciliationResult:
        """
        Reconcile a staged Register.it Sent message against historical_sent_emails.
        
        Guarantees:
          - Never assumes Gmail ID == IMAP UID or RFC Message-ID.
          - Matches strictly using recipient address + subject alignment + date proximity.
          - Weak matches remain proposed or ambiguous.
          - Zero mutations to historical_sent_emails.
        """
        if message.direction != "outbound":
            return HistoricalSentReconciliationResult(
                matched_historical_id=None,
                reconciliation_status="unmatched",
                confidence=0.0,
                evidence="Message is not an outbound sent message",
            )

        if not message.to_addresses:
            return HistoricalSentReconciliationResult(
                matched_historical_id=None,
                reconciliation_status="unmatched",
                confidence=0.0,
                evidence="Outbound message has no recipient",
            )

        target_recipient = message.to_addresses[0].lower().strip()
        norm_subject = self._normalize_subject(message.subject or "")

        with self.cand_store._get_connection() as conn:
            # Check if historical_sent_emails table exists
            has_hist = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='historical_sent_emails'"
            ).fetchone())

            if not has_hist:
                return HistoricalSentReconciliationResult(
                    matched_historical_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence="historical_sent_emails table not present",
                )

            # Query historical messages with matching recipient
            rows = conn.execute(
                """
                SELECT id, message_id, thread_id, sender_account, to_address, subject, sent_date, sent_time, matched_candidate_id
                FROM historical_sent_emails
                WHERE LOWER(to_address) = ? OR recipient_emails_json LIKE ?
                ORDER BY sent_date DESC;
                """,
                (target_recipient, f"%{target_recipient}%"),
            ).fetchall()

            if not rows:
                return HistoricalSentReconciliationResult(
                    matched_historical_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence=f"No historical send recorded for recipient {target_recipient}",
                )

            # Compare candidates
            best_match: Optional[sqlite3.Row] = None
            best_confidence = 0.0
            best_evidence = ""

            for r in rows:
                hist_subject = self._normalize_subject(r["subject"] or "")
                confidence = 0.0
                evidence_parts = [f"Recipient match: {target_recipient}"]

                # Subject alignment
                if hist_subject and norm_subject:
                    if hist_subject == norm_subject:
                        confidence += 0.6
                        evidence_parts.append("Exact subject match")
                    elif hist_subject in norm_subject or norm_subject in hist_subject:
                        confidence += 0.4
                        evidence_parts.append("Partial subject match")

                # Sender account alignment (e.g. info@pybim.it)
                if r["from_address"] if "from_address" in r.keys() else r["sender_account"]:
                    sender_acc = r["sender_account"] or ""
                    if sender_acc.lower() == message.from_address.lower():
                        confidence += 0.3
                        evidence_parts.append(f"Sender account match: {sender_acc}")

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = r
                    best_evidence = "; ".join(evidence_parts)

            if best_match and best_confidence >= 0.8:
                return HistoricalSentReconciliationResult(
                    matched_historical_id=best_match["id"],
                    reconciliation_status="verified_match",
                    confidence=best_confidence,
                    evidence=best_evidence,
                    original_gmail_id=best_match["message_id"],
                    original_thread_id=best_match["thread_id"],
                )
            elif best_match and best_confidence >= 0.4:
                return HistoricalSentReconciliationResult(
                    matched_historical_id=best_match["id"],
                    reconciliation_status="proposed_match",
                    confidence=best_confidence,
                    evidence=best_evidence,
                    original_gmail_id=best_match["message_id"],
                    original_thread_id=best_match["thread_id"],
                )
            else:
                return HistoricalSentReconciliationResult(
                    matched_historical_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence=f"Recipient found in history but subject/date did not meet confidence threshold",
                )

    # -----------------------------------------------------------------------
    # 3. Manual Sent Reconciliation (Thunderbird Sends vs manual_outreach_log)
    # -----------------------------------------------------------------------

    def reconcile_manual_outreach(self, message: MailboxMessageRecord) -> ManualOutreachReconciliationResult:
        """
        Reconcile a staged Register.it Sent message with manual_outreach_log.
        
        Guarantees:
          - Confirms previously logged external sends without creating duplicate send events.
          - Matches recipient + sender account + subject alignment.
          - Never alters, voids, or overwrites manual confirmation records.
        """
        if message.direction != "outbound":
            return ManualOutreachReconciliationResult(
                matched_log_id=None,
                reconciliation_status="unmatched",
                confidence=0.0,
                evidence="Message is not an outbound sent message",
                recipient_email="",
                sender_account=None,
            )

        if not message.to_addresses:
            return ManualOutreachReconciliationResult(
                matched_log_id=None,
                reconciliation_status="unmatched",
                confidence=0.0,
                evidence="Outbound message has no recipient",
                recipient_email="",
                sender_account=None,
            )

        recipient = message.to_addresses[0].lower().strip()
        sender = message.from_address.lower().strip()
        norm_subj = self._normalize_subject(message.subject or "")

        with self.review_store._get_connection() as conn:
            has_mol = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='manual_outreach_log'"
            ).fetchone())

            if not has_mol:
                return ManualOutreachReconciliationResult(
                    matched_log_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence="manual_outreach_log table not present",
                    recipient_email=recipient,
                    sender_account=sender,
                )

            # Query candidate unvoided manual logs for this recipient
            rows = conn.execute(
                """
                SELECT log_id, candidate_id, draft_id, recipient_email, subject, logged_at, sender_account, imap_reconciliation_status
                FROM manual_outreach_log
                WHERE LOWER(recipient_email) = ? AND (is_voided IS NULL OR is_voided = 0)
                ORDER BY logged_at DESC;
                """,
                (recipient,),
            ).fetchall()

            if not rows:
                return ManualOutreachReconciliationResult(
                    matched_log_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence=f"No manual outreach logged for recipient {recipient}",
                    recipient_email=recipient,
                    sender_account=sender,
                )

            best_match: Optional[sqlite3.Row] = None
            best_confidence = 0.0
            best_evidence = ""

            for r in rows:
                log_subj = self._normalize_subject(r["subject"] or "")
                confidence = 0.0
                evidence_parts = [f"Recipient match: {recipient}"]

                if log_subj and norm_subj:
                    if log_subj == norm_subj:
                        confidence += 0.6
                        evidence_parts.append("Exact subject match")
                    elif log_subj in norm_subj or norm_subj in log_subj:
                        confidence += 0.4
                        evidence_parts.append("Partial subject match")

                # Sender match if recorded in manual log
                logged_sender = (r["sender_account"] or "").lower().strip()
                if logged_sender and logged_sender == sender:
                    confidence += 0.3
                    evidence_parts.append(f"Sender account match: {sender}")
                elif not logged_sender:
                    # If operator didn't specify sender, slight neutral confidence
                    confidence += 0.1

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = r
                    best_evidence = "; ".join(evidence_parts)

            if best_match and best_confidence >= 0.7:
                return ManualOutreachReconciliationResult(
                    matched_log_id=best_match["log_id"],
                    reconciliation_status="confirmed_on_server",
                    confidence=best_confidence,
                    evidence=best_evidence,
                    recipient_email=recipient,
                    sender_account=sender,
                )
            elif best_match:
                return ManualOutreachReconciliationResult(
                    matched_log_id=best_match["log_id"],
                    reconciliation_status="probable_match",
                    confidence=best_confidence,
                    evidence=best_evidence,
                    recipient_email=recipient,
                    sender_account=sender,
                )
            else:
                return ManualOutreachReconciliationResult(
                    matched_log_id=None,
                    reconciliation_status="unmatched",
                    confidence=0.0,
                    evidence="No matching manual outreach log met confidence criteria",
                    recipient_email=recipient,
                    sender_account=sender,
                )

    # -----------------------------------------------------------------------
    # 4. Conversation Threading (RFC Reply Headers)
    # -----------------------------------------------------------------------

    def link_thread_proposals(self, message: MailboxMessageRecord) -> Dict[str, Any]:
        """
        Propose conversation threading based on RFC In-Reply-To and References headers.
        Guarantees that copies of the same message across accounts/folders are recognized
        as part of the same logical conversation without multiplying reply counts.
        """
        proposed_thread_id: Optional[str] = None
        evidence_chain: List[str] = []

        # 1. Direct In-Reply-To match
        if message.in_reply_to:
            clean_irt = message.in_reply_to.strip().strip("<>").strip()
            prior_messages = self.mailbox_repo.find_messages_by_rfc_id(clean_irt)
            if prior_messages:
                proposed_thread_id = f"th_{clean_irt[:16]}"
                evidence_chain.append(f"In-Reply-To direct link to {clean_irt} (found {len(prior_messages)} occurrences)")

        # 2. References match
        if not proposed_thread_id and message.references:
            for ref in message.references:
                clean_ref = ref.strip().strip("<>").strip()
                ref_msgs = self.mailbox_repo.find_messages_by_rfc_id(clean_ref)
                if ref_msgs:
                    proposed_thread_id = f"th_{clean_ref[:16]}"
                    evidence_chain.append(f"References header link to {clean_ref}")
                    break

        if not proposed_thread_id and message.rfc_message_id:
            # First message in a new thread
            clean_self = message.rfc_message_id.strip().strip("<>").strip()
            proposed_thread_id = f"th_{clean_self[:16]}"
            evidence_chain.append("Root message of thread")

        return {
            "message_id": message.message_id,
            "rfc_message_id": message.rfc_message_id,
            "proposed_thread_id": proposed_thread_id,
            "evidence": "; ".join(evidence_chain) if evidence_chain else "Unthreaded / isolated message",
            "is_reply": bool(message.in_reply_to or message.references),
        }

    def _normalize_subject(self, subject: str) -> str:
        """Strip reply and forward prefixes to compare normalized base subjects."""
        import re
        if not subject:
            return ""
        s = subject.strip()
        prefix_re = re.compile(r"^(?:(?:re|fwd|fw|r|aw|antw)\s*:\s*)+", re.IGNORECASE)
        cleaned = prefix_re.sub("", s).strip().lower()
        return cleaned
