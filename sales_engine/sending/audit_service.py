from typing import List, Optional
from sales_engine.sending.schemas import ReviewEvent, SendAttempt
from sales_engine.sending.review_store import ReviewStore


class AuditService:
    """
    Service for inspecting and exporting immutable review events and send attempts.
    """

    def __init__(self, review_store: Optional[ReviewStore] = None):
        self.store = review_store or ReviewStore()

    def get_draft_events(self, draft_id: str) -> List[ReviewEvent]:
        return self.store.list_events(draft_id=draft_id)

    def get_all_events(self, limit: int = 100) -> List[ReviewEvent]:
        return self.store.list_events(limit=limit)

    def get_send_attempts(self, draft_id: Optional[str] = None, limit: int = 100) -> List[SendAttempt]:
        with self.store._get_connection() as conn:
            if draft_id:
                rows = conn.execute(
                    "SELECT * FROM send_attempts WHERE draft_id = ? ORDER BY attempted_at DESC LIMIT ?",
                    (draft_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM send_attempts ORDER BY attempted_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        attempts = []
        for r in rows:
            attempts.append(
                SendAttempt(
                    attempt_id=r["attempt_id"],
                    send_key=r["send_key"],
                    draft_id=r["draft_id"],
                    revision=r["revision"],
                    provider=r["provider"],
                    recipient_email=r["recipient_email"],
                    sender_email=r["sender_email"],
                    status=r["status"],
                    error_type=r["error_type"],
                    error_message=r["error_message"],
                    provider_message_id=r["provider_message_id"],
                    attempted_at=r["attempted_at"],
                    completed_at=r["completed_at"],
                )
            )
        return attempts
