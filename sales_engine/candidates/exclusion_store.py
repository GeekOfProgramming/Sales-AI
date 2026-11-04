"""
Excluded Companies Data Model and SQLite Persistence Store.
HV-01: Excluded Companies Registry & Candidate Queue Integration.

Features:
- Separate persisted registry for companies that should no longer enter the active sales pipeline.
- Reversible: Status ACTIVE / RESTORED without deleting candidate data.
- Auditable: Immutable audit log for every exclude, restore, and reason/note update.
- Deduplication & Discovery Integration: Checks by canonical domain, candidate_id, and exact normalized name.
- Domain Safety: Excludes public email providers and shared hosting domains from domain-wide suppression.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Shared hosting / public email providers that MUST NOT be used for domain-wide exclusion
PUBLIC_SHARED_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "libero.it", "virgilio.it", "alice.it", "tin.it", "tiscali.it", "fastwebnet.it",
    "wixsite.com", "wordpress.com", "squarespace.com", "weebly.com", "github.io",
    "gitlab.io", "webflow.io", "blogspot.com", "sites.google.com"
}


class ExclusionReason(str, Enum):
    NOT_TARGET = "NOT_TARGET"
    SOFTWARE_VENDOR = "SOFTWARE_VENDOR"
    WRONG_COMPANY = "WRONG_COMPANY"
    DUPLICATE = "DUPLICATE"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    MANUAL_EXCLUSION = "MANUAL_EXCLUSION"


class ExclusionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESTORED = "RESTORED"


class ExcludedCompanyRecord(BaseModel):
    registry_id: str
    candidate_id: Optional[str] = None
    canonical_domain: Optional[str] = None
    company_name: str
    known_aliases: List[str] = Field(default_factory=list)
    exclusion_reason: ExclusionReason = ExclusionReason.MANUAL_EXCLUSION
    operator_note: str = ""
    exclusion_status: ExclusionStatus = ExclusionStatus.ACTIVE
    created_at: str
    updated_at: str
    created_by: str = "operator"
    last_updated_by: str = "operator"
    provenance_source: str = "manual_operator"


class ExclusionAuditEvent(BaseModel):
    event_id: str
    registry_id: str
    candidate_id: Optional[str] = None
    action: str  # EXCLUDED, RESTORED, REASON_UPDATED, NOTE_UPDATED
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    previous_reason: Optional[str] = None
    new_reason: Optional[str] = None
    operator_note: Optional[str] = None
    actor: str = "operator"
    created_at: str


class ExcludedCompaniesStore:
    """
    Thread-safe SQLite store for the Excluded Companies Registry and its audit log.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or ":memory:"
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS excluded_companies (
                    registry_id TEXT PRIMARY KEY,
                    candidate_id TEXT,
                    canonical_domain TEXT,
                    company_name TEXT NOT NULL,
                    known_aliases_json TEXT NOT NULL DEFAULT '[]',
                    exclusion_reason TEXT NOT NULL,
                    operator_note TEXT NOT NULL DEFAULT '',
                    exclusion_status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'operator',
                    last_updated_by TEXT NOT NULL DEFAULT 'operator',
                    provenance_source TEXT NOT NULL DEFAULT 'manual_operator'
                );
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_excluded_canonical_domain
                ON excluded_companies(canonical_domain);
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_excluded_candidate_id
                ON excluded_companies(candidate_id);
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_excluded_status
                ON excluded_companies(exclusion_status);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS exclusion_audit_events (
                    event_id TEXT PRIMARY KEY,
                    registry_id TEXT NOT NULL,
                    candidate_id TEXT,
                    action TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT,
                    previous_reason TEXT,
                    new_reason TEXT,
                    operator_note TEXT,
                    actor TEXT NOT NULL DEFAULT 'operator',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (registry_id) REFERENCES excluded_companies(registry_id) ON DELETE CASCADE
                );
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_registry_id
                ON exclusion_audit_events(registry_id);
            """)

            conn.commit()

    @staticmethod
    def _is_safe_domain(domain: Optional[str]) -> bool:
        if not domain:
            return False
        clean = domain.strip().lower()
        if not clean or clean in PUBLIC_SHARED_DOMAINS:
            return False
        return True

    def is_company_excluded(
        self,
        candidate_id: Optional[str] = None,
        company_domain: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> bool:
        """
        Fast lookup: returns True if an ACTIVE exclusion exists for this candidate, domain, or name.
        Safe against public shared hosting domains.
        """
        record = self.get_active_exclusion(
            candidate_id=candidate_id,
            company_domain=company_domain,
            company_name=company_name,
        )
        return record is not None

    def get_active_exclusion(
        self,
        candidate_id: Optional[str] = None,
        company_domain: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> Optional[ExcludedCompanyRecord]:
        """
        Resolves the ACTIVE exclusion record matching candidate_id, canonical_domain, or company_name.
        """
        clean_cid = candidate_id.strip() if candidate_id else None
        clean_dom = company_domain.strip().lower() if company_domain else None
        clean_name = company_name.strip().lower() if company_name else None

        with self._get_connection() as conn:
            # 1. Match by candidate_id
            if clean_cid:
                row = conn.execute(
                    "SELECT * FROM excluded_companies WHERE candidate_id = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                    (clean_cid,),
                ).fetchone()
                if row:
                    return self._row_to_record(row)

            # 2. Match by canonical domain (safe only if not shared hosting)
            if clean_dom and self._is_safe_domain(clean_dom):
                row = conn.execute(
                    "SELECT * FROM excluded_companies WHERE canonical_domain = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                    (clean_dom,),
                ).fetchone()
                if row:
                    return self._row_to_record(row)

            # 3. Match by exact normalized company name
            if clean_name:
                row = conn.execute(
                    "SELECT * FROM excluded_companies WHERE LOWER(company_name) = ? AND exclusion_status = 'ACTIVE' LIMIT 1",
                    (clean_name,),
                ).fetchone()
                if row:
                    return self._row_to_record(row)

        return None

    def get_exclusion_by_id(self, registry_id: str) -> Optional[ExcludedCompanyRecord]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM excluded_companies WHERE registry_id = ? LIMIT 1",
                (registry_id,),
            ).fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def get_exclusion_for_candidate(self, candidate_id: str) -> Optional[ExcludedCompanyRecord]:
        clean_cid = candidate_id.strip()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM excluded_companies WHERE candidate_id = ? ORDER BY (exclusion_status = 'ACTIVE') DESC, updated_at DESC LIMIT 1",
                (clean_cid,),
            ).fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def exclude_company(
        self,
        company_name: str,
        exclusion_reason: Union[ExclusionReason, str] = ExclusionReason.MANUAL_EXCLUSION,
        candidate_id: Optional[str] = None,
        canonical_domain: Optional[str] = None,
        known_aliases: Optional[List[str]] = None,
        operator_note: str = "",
        operator: str = "operator",
        provenance_source: str = "manual_operator",
    ) -> ExcludedCompanyRecord:
        """
        Marks a company as excluded or updates an existing exclusion record.
        Creates an immutable audit log entry.
        """
        import json

        if isinstance(exclusion_reason, str):
            try:
                eff_reason = ExclusionReason(exclusion_reason.strip().upper())
            except ValueError:
                eff_reason = ExclusionReason.MANUAL_EXCLUSION
        else:
            eff_reason = exclusion_reason

        now_iso = datetime.now(timezone.utc).isoformat()
        clean_cid = candidate_id.strip() if candidate_id else None
        clean_dom = canonical_domain.strip().lower() if canonical_domain else None
        clean_name = company_name.strip()
        aliases = known_aliases or []
        note = operator_note.strip()

        # Check existing record
        existing = None
        if clean_cid:
            existing = self.get_exclusion_for_candidate(clean_cid)
        if not existing and clean_dom and self._is_safe_domain(clean_dom):
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM excluded_companies WHERE canonical_domain = ? LIMIT 1",
                    (clean_dom,),
                ).fetchone()
                if row:
                    existing = self._row_to_record(row)

        with self._get_connection() as conn:
            if existing:
                prev_status = existing.exclusion_status.value
                prev_reason = existing.exclusion_reason.value
                new_status = ExclusionStatus.ACTIVE.value
                new_reason = eff_reason.value

                merged_aliases = list(set(existing.known_aliases + aliases))
                reg_id = existing.registry_id

                conn.execute("""
                    UPDATE excluded_companies SET
                        candidate_id = COALESCE(?, candidate_id),
                        canonical_domain = COALESCE(?, canonical_domain),
                        company_name = ?,
                        known_aliases_json = ?,
                        exclusion_reason = ?,
                        operator_note = ?,
                        exclusion_status = 'ACTIVE',
                        updated_at = ?,
                        last_updated_by = ?
                    WHERE registry_id = ?
                """, (
                    clean_cid,
                    clean_dom,
                    clean_name,
                    json.dumps(merged_aliases),
                    new_reason,
                    note or existing.operator_note,
                    now_iso,
                    operator,
                    reg_id,
                ))

                action = "REASON_UPDATED" if (prev_status == "ACTIVE" and prev_reason != new_reason) else "EXCLUDED"
                self._record_audit_event(
                    conn=conn,
                    registry_id=reg_id,
                    candidate_id=clean_cid or existing.candidate_id,
                    action=action,
                    previous_status=prev_status,
                    new_status=new_status,
                    previous_reason=prev_reason,
                    new_reason=new_reason,
                    operator_note=note or existing.operator_note,
                    actor=operator,
                    created_at=now_iso,
                )
                conn.commit()
                return self.get_exclusion_by_id(reg_id)  # type: ignore

            # New registry entry
            reg_id = f"excl_{uuid.uuid4().hex[:12]}"
            conn.execute("""
                INSERT INTO excluded_companies (
                    registry_id, candidate_id, canonical_domain, company_name,
                    known_aliases_json, exclusion_reason, operator_note,
                    exclusion_status, created_at, updated_at, created_by,
                    last_updated_by, provenance_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
            """, (
                reg_id,
                clean_cid,
                clean_dom,
                clean_name,
                json.dumps(aliases),
                eff_reason.value,
                note,
                now_iso,
                now_iso,
                operator,
                operator,
                provenance_source,
            ))

            self._record_audit_event(
                conn=conn,
                registry_id=reg_id,
                candidate_id=clean_cid,
                action="EXCLUDED",
                previous_status=None,
                new_status=ExclusionStatus.ACTIVE.value,
                previous_reason=None,
                new_reason=eff_reason.value,
                operator_note=note,
                actor=operator,
                created_at=now_iso,
            )
            conn.commit()
            return self.get_exclusion_by_id(reg_id)  # type: ignore

    def restore_company(
        self,
        registry_id_or_candidate_id: str,
        operator_note: str = "",
        operator: str = "operator",
    ) -> Optional[ExcludedCompanyRecord]:
        """
        Reversibly restores an excluded company to RESTORED status.
        Does NOT delete the record; creates an immutable audit log entry.
        """
        target = registry_id_or_candidate_id.strip()
        rec = self.get_exclusion_by_id(target) or self.get_exclusion_for_candidate(target)
        if not rec:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        prev_status = rec.exclusion_status.value
        note = operator_note.strip() or f"Restored by operator {operator}"

        with self._get_connection() as conn:
            conn.execute("""
                UPDATE excluded_companies SET
                    exclusion_status = 'RESTORED',
                    operator_note = ?,
                    updated_at = ?,
                    last_updated_by = ?
                WHERE registry_id = ?
            """, (
                note,
                now_iso,
                operator,
                rec.registry_id,
            ))

            self._record_audit_event(
                conn=conn,
                registry_id=rec.registry_id,
                candidate_id=rec.candidate_id,
                action="RESTORED",
                previous_status=prev_status,
                new_status=ExclusionStatus.RESTORED.value,
                previous_reason=rec.exclusion_reason.value,
                new_reason=rec.exclusion_reason.value,
                operator_note=note,
                actor=operator,
                created_at=now_iso,
            )
            conn.commit()

        return self.get_exclusion_by_id(rec.registry_id)

    def list_exclusions(
        self,
        status_filter: Optional[str] = None,
        reason_filter: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[ExcludedCompanyRecord], int]:
        """
        Lists excluded companies with filtering, search, and total count.
        """
        clauses = []
        params: List[Any] = []

        if status_filter and status_filter.strip().upper() != "ALL":
            clauses.append("exclusion_status = ?")
            params.append(status_filter.strip().upper())

        if reason_filter and reason_filter.strip().upper() != "ALL":
            clauses.append("exclusion_reason = ?")
            params.append(reason_filter.strip().upper())

        if query and query.strip():
            q_clean = f"%{query.strip().lower()}%"
            clauses.append("(LOWER(company_name) LIKE ? OR LOWER(canonical_domain) LIKE ? OR LOWER(operator_note) LIKE ?)")
            params.extend([q_clean, q_clean, q_clean])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._get_connection() as conn:
            count_sql = f"SELECT COUNT(*) FROM excluded_companies {where_sql}"
            total = conn.execute(count_sql, params).fetchone()[0]

            data_sql = f"""
                SELECT * FROM excluded_companies
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(data_sql, params + [limit, offset]).fetchall()
            records = [self._row_to_record(r) for r in rows]

        return records, total

    def get_audit_history(self, registry_id: str) -> List[ExclusionAuditEvent]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM exclusion_audit_events WHERE registry_id = ? ORDER BY created_at DESC",
                (registry_id,),
            ).fetchall()
            events = []
            for r in rows:
                events.append(ExclusionAuditEvent(
                    event_id=r["event_id"],
                    registry_id=r["registry_id"],
                    candidate_id=r["candidate_id"],
                    action=r["action"],
                    previous_status=r["previous_status"],
                    new_status=r["new_status"],
                    previous_reason=r["previous_reason"],
                    new_reason=r["new_reason"],
                    operator_note=r["operator_note"],
                    actor=r["actor"],
                    created_at=r["created_at"],
                ))
            return events

    def get_audit_history_for_candidate(self, candidate_id: str) -> List[ExclusionAuditEvent]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM exclusion_audit_events WHERE candidate_id = ? ORDER BY created_at DESC",
                (candidate_id,),
            ).fetchall()
            events = []
            for r in rows:
                events.append(ExclusionAuditEvent(
                    event_id=r["event_id"],
                    registry_id=r["registry_id"],
                    candidate_id=r["candidate_id"],
                    action=r["action"],
                    previous_status=r["previous_status"],
                    new_status=r["new_status"],
                    previous_reason=r["previous_reason"],
                    new_reason=r["new_reason"],
                    operator_note=r["operator_note"],
                    actor=r["actor"],
                    created_at=r["created_at"],
                ))
            return events

    def _record_audit_event(
        self,
        conn: sqlite3.Connection,
        registry_id: str,
        candidate_id: Optional[str],
        action: str,
        previous_status: Optional[str],
        new_status: Optional[str],
        previous_reason: Optional[str],
        new_reason: Optional[str],
        operator_note: Optional[str],
        actor: str,
        created_at: str,
    ):
        event_id = f"aud_{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO exclusion_audit_events (
                event_id, registry_id, candidate_id, action,
                previous_status, new_status, previous_reason, new_reason,
                operator_note, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id, registry_id, candidate_id, action,
            previous_status, new_status, previous_reason, new_reason,
            operator_note, actor, created_at,
        ))

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ExcludedCompanyRecord:
        import json
        aliases = []
        try:
            aliases = json.loads(row["known_aliases_json"]) if row["known_aliases_json"] else []
        except Exception:
            aliases = []

        return ExcludedCompanyRecord(
            registry_id=row["registry_id"],
            candidate_id=row["candidate_id"],
            canonical_domain=row["canonical_domain"],
            company_name=row["company_name"],
            known_aliases=aliases,
            exclusion_reason=ExclusionReason(row["exclusion_reason"]),
            operator_note=row["operator_note"] or "",
            exclusion_status=ExclusionStatus(row["exclusion_status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=row["created_by"],
            last_updated_by=row["last_updated_by"],
            provenance_source=row["provenance_source"],
        )
