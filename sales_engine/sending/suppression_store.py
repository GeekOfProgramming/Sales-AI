import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from sales_engine.sending.schemas import SuppressionEntry, SuppressionReason
from sales_engine.sending.review_store import ReviewStore, DEFAULT_DB_PATH


class SuppressionStore:
    """
    Manages do-not-contact / suppression list stored in SQLite.
    Prevents outbound communications to opted-out or bounced recipients.
    """

    def __init__(self, review_store: Optional[ReviewStore] = None):
        self.store = review_store or ReviewStore()

    def is_suppressed(self, email: str, company_domain: Optional[str] = None) -> bool:
        if not email:
            return False
        clean_email = email.strip().lower()
        clean_domain = company_domain.strip().lower() if company_domain else None

        with self.store._get_connection() as conn:
            # 1. Exact email match
            row = conn.execute(
                "SELECT 1 FROM suppression_list WHERE email = ? LIMIT 1",
                (clean_email,),
            ).fetchone()
            if row:
                return True

            # 2. Optional company domain suppression
            if clean_domain:
                row_domain = conn.execute(
                    "SELECT 1 FROM suppression_list WHERE company_domain = ? LIMIT 1",
                    (clean_domain,),
                ).fetchone()
                if row_domain:
                    return True

        return False

    def get_suppression(self, email: str) -> Optional[SuppressionEntry]:
        clean_email = email.strip().lower()
        with self.store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM suppression_list WHERE email = ? LIMIT 1",
                (clean_email,),
            ).fetchone()
            if not row:
                return None
            return SuppressionEntry(
                suppression_id=row["suppression_id"],
                email=row["email"],
                company_domain=row["company_domain"],
                reason=row["reason"],
                source=row["source"],
                created_at=row["created_at"],
            )

    def add_suppression(
        self,
        email: str,
        reason: SuppressionReason = "manual",
        source: str = "manual",
        company_domain: Optional[str] = None,
    ) -> SuppressionEntry:
        clean_email = email.strip().lower()
        clean_domain = company_domain.strip().lower() if company_domain else None
        now_iso = datetime.now(timezone.utc).isoformat()
        suppression_id = f"supp_{uuid.uuid4().hex[:12]}"

        entry = SuppressionEntry(
            suppression_id=suppression_id,
            email=clean_email,
            company_domain=clean_domain,
            reason=reason,
            source=source,
            created_at=now_iso,
        )

        with self.store._lock, self.store._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO suppression_list (
                    suppression_id, email, company_domain, reason, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.suppression_id,
                    entry.email,
                    entry.company_domain,
                    entry.reason,
                    entry.source,
                    entry.created_at,
                ),
            )
        return entry

    def remove_suppression(self, email: str) -> bool:
        clean_email = email.strip().lower()
        with self.store._lock, self.store._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM suppression_list WHERE email = ?",
                (clean_email,),
            )
            return cursor.rowcount > 0

    def list_suppressed(self, limit: int = 100, offset: int = 0) -> List[SuppressionEntry]:
        with self.store._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM suppression_list ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

        return [
            SuppressionEntry(
                suppression_id=r["suppression_id"],
                email=r["email"],
                company_domain=r["company_domain"],
                reason=r["reason"],
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
