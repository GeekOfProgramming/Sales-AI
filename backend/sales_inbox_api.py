import os
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, status, Body
from pydantic import BaseModel, Field

from sales_engine.inbox.store import InboxStore
from sales_engine.inbox.sync import InboxSyncManager
from sales_engine.inbox.reply_sender import ReplySendOrchestrator
from sales_engine.inbox.classifier import ReplyClassifier
from sales_engine.inbox.suggestion import ReplySuggestionEngine
from sales_engine.inbox.providers.mock import MockMailboxProvider
from sales_engine.inbox.providers.gmail import GmailMailboxProvider
from sales_engine.inbox.providers.graph import MicrosoftGraphMailboxProvider
from sales_engine.candidates.candidate_store import CandidateStore
from sales_engine.sending.review_store import ReviewStore
from sales_engine.sending.suppression_store import SuppressionStore
from sales_engine.inbox.models import ConversationEvent

router = APIRouter(prefix="/api/sales", tags=["Sales-Inbox-API"])


def get_inbox_services():
    db_path = os.getenv("SALES_OUTREACH_DB") or os.getenv("SALESAI_DB_PATH") or "data/sales_outreach.db"
    cand_store = CandidateStore(db_path=db_path)
    inbox_store = InboxStore(db_path=db_path)
    rev_store = ReviewStore(db_path=db_path)
    supp_store = SuppressionStore(rev_store)

    # Provider selection
    provider_name = os.getenv("MAILBOX_PROVIDER", "mock").lower()
    if provider_name == "gmail":
        provider = GmailMailboxProvider()
    elif provider_name == "graph":
        provider = MicrosoftGraphMailboxProvider()
    else:
        provider = MockMailboxProvider()

    classifier = ReplyClassifier()
    suggestion_engine = ReplySuggestionEngine()
    sync_mgr = InboxSyncManager(
        inbox_store=inbox_store,
        provider=provider,
        candidate_store=cand_store,
        review_store=rev_store,
        suppression_store=supp_store,
        classifier=classifier,
        suggestion_engine=suggestion_engine,
    )
    reply_sender = ReplySendOrchestrator(
        inbox_store=inbox_store,
        suppression_store=supp_store,
        provider=provider,
    )

    return {
        "inbox_store": inbox_store,
        "cand_store": cand_store,
        "rev_store": rev_store,
        "supp_store": supp_store,
        "provider": provider,
        "sync_mgr": sync_mgr,
        "reply_sender": reply_sender,
        "classifier": classifier,
        "suggestion_engine": suggestion_engine,
    }


class ClassifyRequest(BaseModel):
    category: str
    reasoning: Optional[str] = None


class EditSuggestionRequest(BaseModel):
    subject: str
    body: str


class ApproveSuggestionRequest(BaseModel):
    reviewer: Optional[str] = "operator"


class RejectSuggestionRequest(BaseModel):
    reason: Optional[str] = None


class SendReplyRequest(BaseModel):
    sender_email: str
    operator_confirmed: bool = False


@router.get("/inbox/status")
def get_inbox_status():
    """Return mailbox connection and sync status with zero secret leakage."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]
    provider = svcs["provider"]

    conn_info = provider.get_connection_status()
    sync_state = store.get_sync_state(provider.provider_name)

    return {
        "provider": provider.provider_name,
        "status": conn_info.status,
        "account_email": conn_info.account_email,
        "last_synced_at": conn_info.last_synced_at,
        "sync_state": sync_state,
    }


@router.post("/inbox/sync")
def trigger_inbox_sync(
    max_messages: int = 50,
    lookback_days: int = 30,
):
    """Trigger manual inbox synchronization."""
    svcs = get_inbox_services()
    sync_mgr: InboxSyncManager = svcs["sync_mgr"]
    summary = sync_mgr.sync(max_messages=max_messages, lookback_days=lookback_days)
    return summary.model_dump()


@router.get("/conversations")
def list_conversations(
    category: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
):
    """List conversations filtered by reply category or status."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]
    convs = store.list_conversations(category=category, status=status, limit=limit)
    return [c.model_dump() for c in convs]


@router.get("/conversations/{conversation_id}")
def get_conversation_detail(conversation_id: str):
    """Retrieve complete conversation detail including messages, suggestions, and audit events."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]

    conv = store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = store.list_inbound_messages(conversation_id)
    events = store.list_events(conversation_id)
    suggestion = store.get_latest_suggestion_for_conversation(conversation_id)
    proposal = store.get_meeting_proposal(conversation_id)

    return {
        "conversation": conv.model_dump(),
        "messages": [m.model_dump() for m in messages],
        "events": [e.model_dump() for e in events],
        "latest_suggestion": suggestion.model_dump() if suggestion else None,
        "meeting_proposal": proposal.model_dump() if proposal else None,
    }


@router.post("/conversations/{conversation_id}/classify")
def reclassify_conversation(
    conversation_id: str,
    req: ClassifyRequest,
):
    """Manually reclassify a conversation category with audit tracking."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]
    cand_store: CandidateStore = svcs["cand_store"]
    supp_store: SuppressionStore = svcs["supp_store"]

    conv = store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    old_cat = conv.reply_category
    new_cat = req.category.strip().lower()

    valid_cats = {
        "positive_interest", "meeting_proposed", "referral", "question",
        "not_now", "not_interested", "unsubscribe", "bounce", "auto_reply", "unknown"
    }
    if new_cat not in valid_cats:
        raise HTTPException(status_code=400, detail=f"Invalid category '{new_cat}'")

    conv.reply_category = new_cat  # type: ignore
    if new_cat == "unsubscribe":
        conv.status = "do_not_contact"
        conv.active_conversation = False
        msgs = store.list_inbound_messages(conversation_id)
        if msgs and msgs[0].from_email:
            supp_store.add_suppression(msgs[0].from_email, reason="opt_out", source="manual_reclassification")
    elif new_cat == "bounce":
        conv.status = "bounce"
        conv.active_conversation = False

    store.save_conversation(conv)

    store.add_event(
        ConversationEvent(
            conversation_id=conversation_id,
            event_type="manual_reclassification",
            actor="operator",
            details={
                "old_category": old_cat,
                "new_category": new_cat,
                "reasoning": req.reasoning,
            },
        )
    )

    return {"status": "ok", "conversation_id": conversation_id, "new_category": new_cat}


@router.post("/conversations/{conversation_id}/suggest-reply")
def generate_conversation_reply_suggestion(conversation_id: str):
    """Generate or regenerate an AI / template reply suggestion for a conversation."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]
    cand_store: CandidateStore = svcs["cand_store"]
    rev_store: ReviewStore = svcs["rev_store"]
    engine: ReplySuggestionEngine = svcs["suggestion_engine"]

    conv = store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = store.list_inbound_messages(conversation_id)
    if not msgs:
        raise HTTPException(status_code=400, detail="No inbound messages in this conversation")

    latest_msg = msgs[-1]

    outbound_subj = None
    outbound_body = None
    if latest_msg.linked_outbound_draft_id:
        draft = rev_store.get_draft(
            latest_msg.linked_outbound_draft_id,
            latest_msg.linked_outbound_revision or 1,
        )
        if draft:
            outbound_subj = draft.subject
            outbound_body = draft.body

    comp_name = None
    if conv.company_id:
        cand = cand_store.get_candidate(conv.company_id)
        comp_name = cand.company_name if cand else None

    sug = engine.generate_suggestion(
        conversation=conv,
        inbound_message=latest_msg,
        outbound_subject=outbound_subj,
        outbound_body=outbound_body,
        company_name=comp_name,
        contact_name=latest_msg.from_name,
    )

    if not sug:
        raise HTTPException(status_code=400, detail="Cannot generate suggestion for automated or suppressed messages")

    store.save_suggestion(sug)
    return sug.model_dump()


@router.post("/reply-suggestions/{suggestion_id}/edit")
def edit_reply_suggestion(
    suggestion_id: str,
    req: EditSuggestionRequest,
):
    """Operator edits the proposed subject or body of a reply suggestion."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]

    sug = store.get_suggestion(suggestion_id)
    if not sug:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if sug.status == "sent":
        raise HTTPException(status_code=400, detail="Sent suggestions are immutable")

    sug.subject = req.subject.strip()
    sug.body = req.body.strip()
    sug.status = "pending_review"  # remains or returns to pending review
    store.save_suggestion(sug)

    store.add_event(
        ConversationEvent(
            conversation_id=sug.conversation_id,
            event_type="suggestion_edited",
            actor="operator",
            details={"suggestion_id": suggestion_id},
        )
    )

    return sug.model_dump()


@router.post("/reply-suggestions/{suggestion_id}/approve")
def approve_reply_suggestion(
    suggestion_id: str,
    req: ApproveSuggestionRequest,
):
    """Operator approves a reply suggestion, marking it ready for explicit send."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]

    sug = store.get_suggestion(suggestion_id)
    if not sug:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if sug.status == "sent":
        raise HTTPException(status_code=400, detail="Sent suggestions are immutable")

    sug.status = "approved"
    sug.approved_by = req.reviewer or "operator"
    store.save_suggestion(sug)

    store.add_event(
        ConversationEvent(
            conversation_id=sug.conversation_id,
            event_type="suggestion_approved",
            actor=sug.approved_by or "operator",
            details={"suggestion_id": suggestion_id},
        )
    )

    return sug.model_dump()


@router.post("/reply-suggestions/{suggestion_id}/reject")
def reject_reply_suggestion(
    suggestion_id: str,
    req: RejectSuggestionRequest,
):
    """Operator rejects a reply suggestion."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]

    sug = store.get_suggestion(suggestion_id)
    if not sug:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if sug.status == "sent":
        raise HTTPException(status_code=400, detail="Sent suggestions are immutable")

    sug.status = "rejected"
    sug.rejection_reason = req.reason
    store.save_suggestion(sug)

    store.add_event(
        ConversationEvent(
            conversation_id=sug.conversation_id,
            event_type="suggestion_rejected",
            actor="operator",
            details={"suggestion_id": suggestion_id, "reason": req.reason},
        )
    )

    return sug.model_dump()


@router.post("/reply-suggestions/{suggestion_id}/send")
def send_reply_suggestion(
    suggestion_id: str,
    req: SendReplyRequest,
):
    """Explicitly send an approved reply suggestion with trusted sender and confirmation guards."""
    svcs = get_inbox_services()
    store: InboxStore = svcs["inbox_store"]
    reply_sender: ReplySendOrchestrator = svcs["reply_sender"]

    sug = store.get_suggestion(suggestion_id)
    if not sug:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    res = reply_sender.send_reply(
        conversation_id=sug.conversation_id,
        suggestion_id=suggestion_id,
        sender_email=req.sender_email,
        operator_confirmed=req.operator_confirmed,
        actor="operator",
    )

    if not res.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": res.status,
                "error_type": res.error_type,
                "error_message": res.error_message,
            },
        )

    return res.model_dump()
