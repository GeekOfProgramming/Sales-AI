"""
SalesAI Phase 11 - Inbox & Reply Intelligence Package
Provides inbound email processing, conversation threading, reply classification,
actual respondent resolution, suggested operator response, human review, and explicit sending.
"""

from sales_engine.inbox.models import (
    InboundMessage,
    Conversation,
    ConversationEvent,
    ReplySuggestion,
    MeetingProposal,
    MailboxConnectionInfo,
    MailboxSyncSummary,
)

__all__ = [
    "InboundMessage",
    "Conversation",
    "ConversationEvent",
    "ReplySuggestion",
    "MeetingProposal",
    "MailboxConnectionInfo",
    "MailboxSyncSummary",
]
