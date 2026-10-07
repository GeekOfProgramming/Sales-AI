import re
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from sales_engine.inbox.store import InboxStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.candidates.candidate_store import CandidateStore


class ThreadLinkResult(BaseModel):
    """Result of matching an inbound message to conversations, drafts, and companies."""
    conversation_id: Optional[str] = None
    company_id: Optional[str] = None
    contact_id: Optional[str] = None
    linked_outbound_draft_id: Optional[str] = None
    linked_outbound_revision: Optional[int] = None
    linked_send_attempt_id: Optional[str] = None
    match_method: str = "none"
    confidence: float = 0.0


def normalize_subject(subject: Optional[str]) -> str:
    """Strip reply and forward prefixes to compare base subjects."""
    if not subject:
        return ""
    s = subject.strip()
    # Strip Re:, Fwd:, R:, Aw:, Antw: repeatedly
    prefix_re = re.compile(r"^(?:(?:re|fwd|fw|r|aw|antw)\s*:\s*)+", re.IGNORECASE)
    cleaned = prefix_re.sub("", s).strip().lower()
    return cleaned


class ThreadMatcher:
    """
    Deterministic thread linker prioritizing cryptographic and header evidence
    over heuristics, strictly avoiding fuzzy mis-linking across companies.
    """

    def __init__(
        self,
        inbox_store: InboxStore,
        review_store: Optional[ReviewStore] = None,
        candidate_store: Optional[CandidateStore] = None,
    ):
        self.inbox_store = inbox_store
        self.review_store = review_store or ReviewStore()
        self.candidate_store = candidate_store or CandidateStore()

    def match(
        self,
        provider: str,
        from_email: str,
        subject: str,
        provider_thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
    ) -> ThreadLinkResult:
        clean_from = from_email.strip().lower() if from_email else ""
        norm_subj = normalize_subject(subject)
        refs = references or []

        # 1. Provider Thread ID match with existing conversation
        if provider_thread_id:
            conv = self.inbox_store.find_conversation_by_thread(provider, provider_thread_id)
            if conv:
                return ThreadLinkResult(
                    conversation_id=conv.conversation_id,
                    company_id=conv.company_id,
                    contact_id=conv.primary_contact_id,
                    match_method="provider_thread_id",
                    confidence=1.0,
                )

        # 2. In-Reply-To header matching Internet-Message-ID / provider_message_id
        if in_reply_to:
            clean_irt = in_reply_to.strip().strip("<>").strip()
            # 2a. Match in Phase 9 send_attempts
            with self.review_store._get_connection() as conn:
                attempt_row = conn.execute(
                    """
                    SELECT * FROM send_attempts 
                    WHERE provider_message_id = ? OR send_key = ? OR attempt_id = ?
                    LIMIT 1
                    """,
                    (clean_irt, clean_irt, clean_irt),
                ).fetchone()

                if attempt_row:
                    draft_id = attempt_row["draft_id"]
                    rev = attempt_row["revision"]
                    # Get draft to retrieve company / contact IDs
                    draft = self.review_store.get_draft(draft_id, rev)
                    company_id = draft.lead_id if draft else None
                    contact_id = draft.contact_id if draft else None

                    # Check if there is an existing conversation for this draft/attempt
                    existing_inb = conn.execute(
                        "SELECT conversation_id FROM inbound_messages WHERE linked_outbound_draft_id = ? LIMIT 1",
                        (draft_id,),
                    ).fetchone()
                    conv_id = existing_inb["conversation_id"] if existing_inb else None

                    return ThreadLinkResult(
                        conversation_id=conv_id,
                        company_id=company_id,
                        contact_id=contact_id,
                        linked_outbound_draft_id=draft_id,
                        linked_outbound_revision=rev,
                        linked_send_attempt_id=attempt_row["attempt_id"],
                        match_method="in_reply_to_send_attempt",
                        confidence=1.0,
                    )

            # 2b. Match in prior inbound_messages
            prior_inb = self.inbox_store.find_inbound_by_internet_id(clean_irt)
            if not prior_inb:
                prior_inb = self.inbox_store.find_inbound_by_provider_id(provider, clean_irt)
            if prior_inb and prior_inb.conversation_id:
                return ThreadLinkResult(
                    conversation_id=prior_inb.conversation_id,
                    company_id=prior_inb.company_id,
                    contact_id=prior_inb.contact_id,
                    linked_outbound_draft_id=prior_inb.linked_outbound_draft_id,
                    linked_outbound_revision=prior_inb.linked_outbound_revision,
                    linked_send_attempt_id=prior_inb.linked_send_attempt_id,
                    match_method="in_reply_to_inbound",
                    confidence=0.98,
                )

        # 3. References header matching
        for ref in refs:
            clean_ref = ref.strip().strip("<>").strip()
            with self.review_store._get_connection() as conn:
                attempt_row = conn.execute(
                    "SELECT * FROM send_attempts WHERE provider_message_id = ? LIMIT 1",
                    (clean_ref,),
                ).fetchone()
                if attempt_row:
                    draft = self.review_store.get_draft(attempt_row["draft_id"], attempt_row["revision"])
                    return ThreadLinkResult(
                        conversation_id=None,
                        company_id=draft.lead_id if draft else None,
                        contact_id=draft.contact_id if draft else None,
                        linked_outbound_draft_id=attempt_row["draft_id"],
                        linked_outbound_revision=attempt_row["revision"],
                        linked_send_attempt_id=attempt_row["attempt_id"],
                        match_method="references_header",
                        confidence=0.95,
                    )

        # 4. Exact known outbound recipient + recent subject alignment
        if clean_from:
            with self.review_store._get_connection() as conn:
                # Find attempts sent to this exact recipient email
                attempts = conn.execute(
                    """
                    SELECT a.*, d.subject as draft_subject, d.lead_id, d.contact_id
                    FROM send_attempts a
                    JOIN drafts d ON a.draft_id = d.draft_id AND a.revision = d.revision
                    WHERE a.recipient_email = ? AND a.status = 'sent'
                    ORDER BY a.attempted_at DESC LIMIT 5
                    """,
                    (clean_from,),
                ).fetchall()

                for row in attempts:
                    draft_norm_subj = normalize_subject(row["draft_subject"])
                    if draft_norm_subj and (draft_norm_subj in norm_subj or norm_subj in draft_norm_subj):
                        return ThreadLinkResult(
                            conversation_id=None,
                            company_id=row["lead_id"],
                            contact_id=row["contact_id"],
                            linked_outbound_draft_id=row["draft_id"],
                            linked_outbound_revision=row["revision"],
                            linked_send_attempt_id=row["attempt_id"],
                            match_method="exact_recipient_subject",
                            confidence=0.90,
                        )

                # Fallback: check candidate contacts by work_email
                with self.candidate_store._get_connection() as c_conn:
                    contact_row = c_conn.execute(
                        "SELECT * FROM candidate_contacts WHERE LOWER(work_email) = ? LIMIT 1",
                        (clean_from,),
                    ).fetchone()
                    if contact_row:
                        # Valid known contact in company
                        return ThreadLinkResult(
                            conversation_id=None,
                            company_id=contact_row["candidate_id"],
                            contact_id=contact_row["contact_id"],
                            match_method="known_contact_email",
                            confidence=0.75,
                        )

        # 5. NO FUZZY LINKING: Return unlinked result
        return ThreadLinkResult(
            conversation_id=None,
            company_id=None,
            contact_id=None,
            match_method="unlinked",
            confidence=0.0,
        )
