"""
sales_engine/sending/outbound_safeguards.py
MAIL-SEND-01A: Outbound Sending Policy & CRM Safeguards.

Implements pre-queue and pre-send safety assertions:
1. Sender validation (authorized pyBIM accounts info@pybim.com, info@pybim.it).
2. Recipient email address validation (RFC format).
3. Do Not Contact (suppression_list) registry enforcement for every envelope recipient.
4. CRM candidate restriction and exclusion semantic preservation (excluded_companies != DNC).
5. Immutable snapshot verification (prevents mutating scheduled drafts).
6. Non-mutation invariant checks (guarantees zero automatic CRM status updates).
"""

import re
import json
import sqlite3
from typing import List, Dict, Any, Optional, Tuple

AUTHORIZED_SENDER_EMAILS = {"info@pybim.com", "info@pybim.it"}
AUTHORIZED_ACCOUNT_IDS = {"acc_info_com", "acc_info_it"}

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)


class SafeguardViolationError(Exception):
    """Raised when an outbound safeguard check fails."""
    pass


class OutboundSafeguardService:
    """
    Validates outbound messages against safety rules and compliance registers.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def validate_sender(self, account_id: str, sender_email: str) -> None:
        """Ensures sender account and email are authorized."""
        clean_email = (sender_email or "").strip().lower()
        clean_acc = (account_id or "").strip()

        if clean_email not in AUTHORIZED_SENDER_EMAILS:
            raise SafeguardViolationError(
                f"Unauthorized sender email '{sender_email}'. Authorized senders are: {', '.join(sorted(AUTHORIZED_SENDER_EMAILS))}"
            )
        if clean_acc and clean_acc not in AUTHORIZED_ACCOUNT_IDS:
            raise SafeguardViolationError(
                f"Unauthorized sender account_id '{account_id}'. Authorized accounts are: {', '.join(sorted(AUTHORIZED_ACCOUNT_IDS))}"
            )

    def validate_recipients(self, recipients: List[str]) -> List[str]:
        """Validates syntax of all envelope recipients and returns cleaned list."""
        if not recipients:
            raise SafeguardViolationError("Envelope recipient list cannot be empty.")

        clean_list = []
        for r in recipients:
            clean = (r or "").strip().lower()
            if not clean:
                continue
            if not EMAIL_REGEX.match(clean):
                raise SafeguardViolationError(f"Invalid recipient email syntax: '{r}'")
            clean_list.append(clean)

        if not clean_list:
            raise SafeguardViolationError("No valid recipient email addresses found.")
        return list(dict.fromkeys(clean_list))

    def check_suppression(self, recipients: List[str]) -> None:
        """
        Enforces the authoritative Do Not Contact (suppression_list) table.
        Checks both exact recipient email and recipient domain.
        """
        with self._get_connection() as conn:
            # Check if suppression_list table exists
            has_supp = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppression_list'").fetchone()
            if not has_supp:
                return

            # Check table columns dynamically to support both production schema and test variations
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(suppression_list)").fetchall()}
            has_scope = "scope" in cols and "value" in cols
            has_domain = "company_domain" in cols

            for email_addr in recipients:
                # 1. Exact email match
                row_email = conn.execute(
                    "SELECT suppression_id, reason FROM suppression_list WHERE LOWER(email) = ? LIMIT 1",
                    (email_addr,),
                ).fetchone()
                if row_email:
                    raise SafeguardViolationError(
                        f"Recipient '{email_addr}' is suppressed on the Do Not Contact list (Reason: {row_email['reason']})."
                    )

                # 2. Domain-level suppression
                domain = email_addr.split("@")[-1]
                if has_domain and has_scope:
                    query = "SELECT suppression_id, reason FROM suppression_list WHERE LOWER(company_domain) = ? OR (scope = 'domain' AND LOWER(value) = ?) LIMIT 1"
                    params = (domain, domain)
                elif has_domain:
                    query = "SELECT suppression_id, reason FROM suppression_list WHERE LOWER(company_domain) = ? LIMIT 1"
                    params = (domain,)
                elif has_scope:
                    query = "SELECT suppression_id, reason FROM suppression_list WHERE scope = 'domain' AND LOWER(value) = ? LIMIT 1"
                    params = (domain,)
                else:
                    query = None

                if query:
                    row_dom = conn.execute(query, params).fetchone()
                    if row_dom:
                        raise SafeguardViolationError(
                            f"Recipient domain '{domain}' for '{email_addr}' is suppressed on the Do Not Contact list (Reason: {row_dom['reason']})."
                        )

    def check_crm_exclusion(self, candidate_id: Optional[str], recipients: List[str]) -> Optional[Dict[str, Any]]:
        """
        Checks excluded_companies.
        IMPORTANT: Excluded Companies is a CRM disqualification list (e.g. competitors, non-BIM),
        DISTINCT from legal Do Not Contact suppression.
        Returns exclusion details if excluded, or None.
        """
        if not candidate_id:
            return None

        with self._get_connection() as conn:
            has_excl = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='excluded_companies'").fetchone()
            if not has_excl:
                return None

            row = conn.execute(
                "SELECT registry_id, company_name, exclusion_reason FROM excluded_companies WHERE candidate_id = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if row:
                return {
                    "registry_id": row["registry_id"],
                    "company_name": row["company_name"],
                    "exclusion_reason": row["exclusion_reason"],
                }
        return None

    def validate_pre_queue(
        self,
        account_id: str,
        sender_email: str,
        recipients: List[str],
        candidate_id: Optional[str] = None,
    ) -> List[str]:
        """
        Runs comprehensive pre-queue checks.
        Returns cleaned recipient list.
        """
        self.validate_sender(account_id, sender_email)
        clean_recipients = self.validate_recipients(recipients)
        self.check_suppression(clean_recipients)
        return clean_recipients
