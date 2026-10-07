import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sales_engine.inbox.models import (
    InboundMessage,
    Conversation,
    ConversationEvent,
    MailboxSyncSummary,
)
from sales_engine.inbox.store import InboxStore
from sales_engine.inbox.providers.base import BaseMailboxProvider
from sales_engine.inbox.providers.mock import MockMailboxProvider
from sales_engine.inbox.security import InboxSecurity
from sales_engine.inbox.threading import ThreadMatcher
from sales_engine.inbox.classifier import ReplyClassifier
from sales_engine.inbox.respondent import RespondentResolver
from sales_engine.inbox.meeting import MeetingExtractor
from sales_engine.inbox.suggestion import ReplySuggestionEngine

from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_SYNC = 50
MAX_THREADS_PER_SYNC = 25
SYNC_LOOKBACK_DAYS = 30


class InboxSyncManager:
    """
    Orchestrates synchronization of mailboxes, idempotent import of inbound messages,
    thread linking, deterministic classification, respondent resolution, suppression,
    and grounded reply suggestion generation.
    """

    def __init__(
        self,
        inbox_store: InboxStore,
        provider: Optional[BaseMailboxProvider] = None,
        candidate_store: Optional[CandidateStore] = None,
        review_store: Optional[ReviewStore] = None,
        suppression_store: Optional[SuppressionStore] = None,
        classifier: Optional[ReplyClassifier] = None,
        suggestion_engine: Optional[ReplySuggestionEngine] = None,
    ):
        self.inbox_store = inbox_store
        self.provider = provider or MockMailboxProvider()
        self.candidate_store = candidate_store or CandidateStore()
        self.review_store = review_store or ReviewStore()
        self.suppression_store = suppression_store or SuppressionStore(self.review_store)

        self.classifier = classifier or ReplyClassifier()
        self.suggestion_engine = suggestion_engine or ReplySuggestionEngine()
        self.matcher = ThreadMatcher(
            inbox_store=self.inbox_store,
            review_store=self.review_store,
            candidate_store=self.candidate_store,
        )
        self.respondent_resolver = RespondentResolver(self.candidate_store)

    def sync(
        self,
        max_messages: int = MAX_MESSAGES_PER_SYNC,
        lookback_days: int = SYNC_LOOKBACK_DAYS,
    ) -> MailboxSyncSummary:
        start_time = datetime.now(timezone.utc).isoformat()
        provider_name = self.provider.provider_name

        # 1. Retrieve current cursor
        sync_state = self.inbox_store.get_sync_state(provider_name)
        cursor = sync_state.get("cursor") if sync_state else None

        # 2. Fetch messages from provider
        batch = self.provider.fetch_messages(
            cursor=cursor,
            limit=max_messages,
            lookback_days=lookback_days,
        )

        messages_fetched = len(batch.messages)
        new_replies_count = 0
        errors: List[str] = list(batch.errors)

        # 3. Process each message with partial-failure tolerance
        for raw_msg in batch.messages:
            try:
                created = self._process_single_message(raw_msg, provider_name)
                if created:
                    new_replies_count += 1
            except Exception as e:
                err_msg = InboxSecurity.sanitize_secrets(f"Error processing message: {str(e)}")
                logger.error(err_msg)
                errors.append(err_msg)

        completed_time = datetime.now(timezone.utc).isoformat()
        summary = MailboxSyncSummary(
            provider=provider_name,
            cursor=batch.new_cursor or cursor,
            messages_fetched=messages_fetched,
            new_replies=new_replies_count,
            errors=errors,
            started_at=start_time,
            completed_at=completed_time,
        )

        # 4. Update sync and connection state
        self.inbox_store.update_sync_state(summary)
        conn_info = self.provider.get_connection_status()
        conn_info.last_synced_at = completed_time
        self.inbox_store.update_connection_state(conn_info)

        return summary

    def _process_single_message(self, raw: Dict[str, Any], provider_name: str) -> bool:
        """
        Process a single raw message dict.
        Returns True if a new message was created, False if duplicate.
        """
        prov_msg_id = str(raw.get("provider_message_id", "")).strip()
        net_msg_id = str(raw.get("internet_message_id", "")).strip() or None

        # Check for duplicates: provider ID or Internet-Message-ID
        if prov_msg_id:
            existing = self.inbox_store.find_inbound_by_provider_id(provider_name, prov_msg_id)
            if existing:
                return False

        if net_msg_id:
            existing = self.inbox_store.find_inbound_by_internet_id(net_msg_id)
            if existing:
                return False

        # Extract and sanitize body
        raw_body_text = raw.get("body_text", "")
        raw_body_html = raw.get("body_html", "")
        has_html = bool(raw_body_html)

        if not raw_body_text and raw_body_html:
            clean_body = InboxSecurity.sanitize_html_to_text(raw_body_html)
        else:
            clean_body = raw_body_text.strip() if raw_body_text else ""

        raw_from = raw.get("from_email")
        if not raw_from or not str(raw_from).strip() or str(raw_from).strip().lower() == "none":
            raise ValueError("Inbound message missing valid from_email")
        from_email = str(raw_from).strip()
        from_name = raw.get("from_name")
        subject = str(raw.get("subject", "")).strip()
        prov_thread_id = raw.get("provider_thread_id")
        in_reply_to = raw.get("in_reply_to")
        references = raw.get("references", [])
        received_at = raw.get("received_at") or datetime.now(timezone.utc).isoformat()
        headers = raw.get("headers", {})

        # 1. Thread matching
        thread_match = self.matcher.match(
            provider=provider_name,
            from_email=from_email,
            subject=subject,
            provider_thread_id=prov_thread_id,
            in_reply_to=in_reply_to,
            references=references,
        )

        company_id = thread_match.company_id
        contact_id = thread_match.contact_id
        conv_id = thread_match.conversation_id

        # 2. Classification
        classification_res = self.classifier.classify(
            from_email=from_email,
            subject=subject,
            body_text=clean_body,
            headers=headers,
        )

        cat = classification_res.category

        # 3. Actual respondent resolution
        orig_recipient_email = None
        if thread_match.linked_outbound_draft_id:
            draft = self.review_store.get_draft(
                thread_match.linked_outbound_draft_id,
                thread_match.linked_outbound_revision or 1,
            )
            if draft:
                orig_recipient_email = draft.recipient_email

        respondent_res = self.respondent_resolver.resolve(
            company_id=company_id,
            from_email=from_email,
            from_name=from_name,
            original_contact_id=contact_id,
            original_recipient_email=orig_recipient_email,
        )
        actual_respondent_id = respondent_res.actual_respondent_contact_id

        # 4. Conversation retrieval or creation
        now_iso = datetime.now(timezone.utc).isoformat()
        conv: Optional[Conversation] = None
        if conv_id:
            conv = self.inbox_store.get_conversation(conv_id)

        if not conv:
            # Create new conversation
            new_conv_id = f"conv_{uuid.uuid4().hex[:12]}"
            conv = Conversation(
                conversation_id=new_conv_id,
                company_id=company_id,
                primary_contact_id=contact_id or actual_respondent_id,
                provider=provider_name,
                provider_thread_id=prov_thread_id,
                status="new_reply",
                reply_category=cat,
                first_reply_at=received_at,
                last_message_at=received_at,
                actual_respondent_contact_id=actual_respondent_id,
                created_at=now_iso,
                updated_at=now_iso,
            )
        else:
            conv.reply_category = cat
            conv.last_message_at = received_at
            conv.actual_respondent_contact_id = actual_respondent_id
            conv.updated_at = now_iso

        # 5. Apply Category-Specific State & Safety Enforcements
        if cat == "unsubscribe":
            conv.status = "do_not_contact"
            conv.active_conversation = False
            # Authoritative Phase 9 Suppression
            if from_email:
                self.suppression_store.add_suppression(
                    email=from_email,
                    reason="opt_out",
                    source="inbound_unsubscribe",
                )
            if company_id:
                # Mark company suppressed in CandidateStore
                with self.candidate_store._get_connection() as c_conn:
                    c_conn.execute(
                        "UPDATE candidate_companies SET suppressed = 1, cold_outreach_allowed = 0 WHERE candidate_id = ?",
                        (company_id,),
                    )

        elif cat == "bounce":
            conv.status = "bounce"
            conv.active_conversation = False
            # Mark recipient email bounced in contacts without domain-wide suppression
            if company_id and from_email:
                with self.candidate_store._get_connection() as c_conn:
                    c_conn.execute(
                        "UPDATE candidate_contacts SET email_status = 'bounced' WHERE candidate_id = ? AND LOWER(work_email) = ?",
                        (company_id, from_email.lower()),
                    )

        elif cat == "auto_reply":
            conv.status = "auto_reply"
            # Auto reply does not activate conversation

        else:
            # Legitimate human reply
            conv.active_conversation = True
            if cat == "meeting_proposed":
                conv.status = "meeting_proposed"
                conv.meeting_status = "proposed"
            elif cat == "positive_interest":
                conv.status = "waiting_on_us"
                conv.opportunity_status = "active_conversation"
            elif cat == "question":
                conv.status = "waiting_on_us"
            elif cat == "not_interested":
                conv.status = "closed_negative"
                conv.active_conversation = False
            elif cat == "not_now":
                conv.status = "needs_review"
            elif cat == "referral":
                conv.status = "needs_review"
            else:
                conv.status = "needs_review"

            # Enforce Active Conversation Guard on CandidateCompany (blocks cold outreach)
            if company_id:
                with self.candidate_store._get_connection() as c_conn:
                    c_conn.execute(
                        """
                        UPDATE candidate_companies 
                        SET active_conversation = 1, cold_outreach_allowed = 0, reply_status = ? 
                        WHERE candidate_id = ?
                        """,
                        (cat, company_id),
                    )

        # Save conversation
        self.inbox_store.save_conversation(conv)

        # 6. Save InboundMessage
        inb_msg_id = f"inb_{uuid.uuid4().hex[:12]}"
        inbound_record = InboundMessage(
            inbound_message_id=inb_msg_id,
            provider=provider_name,
            provider_message_id=prov_msg_id,
            provider_thread_id=prov_thread_id,
            internet_message_id=net_msg_id,
            in_reply_to=in_reply_to,
            references=references,
            from_name=from_name,
            from_email=from_email,
            to_emails=raw.get("to_emails", []),
            cc_emails=raw.get("cc_emails", []),
            subject=subject,
            body_text=clean_body,
            body_html_present=has_html,
            received_at=received_at,
            linked_outbound_draft_id=thread_match.linked_outbound_draft_id,
            linked_outbound_revision=thread_match.linked_outbound_revision,
            linked_send_attempt_id=thread_match.linked_send_attempt_id,
            company_id=company_id,
            contact_id=contact_id or actual_respondent_id,
            conversation_id=conv.conversation_id,
            classification=cat,
            classification_confidence=classification_res.confidence,
            is_auto_reply=classification_res.is_auto_reply,
            is_bounce=classification_res.is_bounce,
            is_unsubscribe=classification_res.is_unsubscribe,
            raw_headers_ref=headers,
            raw_provider_ref={"provider": provider_name},
            created_at=now_iso,
        )
        self.inbox_store.save_inbound_message(inbound_record)

        # 7. Extract Meeting Times if meeting_proposed or present
        proposal = MeetingExtractor.create_proposal(
            conversation_id=conv.conversation_id,
            inbound_message_id=inb_msg_id,
            body_text=clean_body,
        )
        if proposal:
            self.inbox_store.save_meeting_proposal(proposal)

        # 8. Generate Suggested Reply (pending human review)
        if cat not in ("auto_reply", "bounce", "unsubscribe"):
            outbound_subj = None
            outbound_body = None
            if thread_match.linked_outbound_draft_id:
                draft = self.review_store.get_draft(
                    thread_match.linked_outbound_draft_id,
                    thread_match.linked_outbound_revision or 1,
                )
                if draft:
                    outbound_subj = draft.subject
                    outbound_body = draft.body

            comp_name = None
            if company_id:
                cand = self.candidate_store.get_candidate(company_id)
                comp_name = cand.company_name if cand else None

            suggestion = self.suggestion_engine.generate_suggestion(
                conversation=conv,
                inbound_message=inbound_record,
                outbound_subject=outbound_subj,
                outbound_body=outbound_body,
                company_name=comp_name,
                contact_name=from_name,
            )
            if suggestion:
                self.inbox_store.save_suggestion(suggestion)

        # 9. Audit Event
        self.inbox_store.add_event(
            ConversationEvent(
                conversation_id=conv.conversation_id,
                event_type="inbound_message_received",
                actor="inbox_sync",
                details={
                    "inbound_message_id": inb_msg_id,
                    "provider_message_id": prov_msg_id,
                    "from_email": from_email,
                    "classification": cat,
                    "confidence": classification_res.confidence,
                    "link_method": thread_match.match_method,
                },
            )
        )

        return True
