"""
Historical Importer Module for SalesAI Phase 10.

Supports XLSX and CSV ingestion of historical outreach, contact, and company records.
Guarantees:
1. Strict idempotency (re-importing the same sheet/file does not duplicate companies or outreach records).
2. Multi-email cell normalization (splitting multiple emails into separate records, default status 'historical_unknown').
3. Actual respondent support (respondent_name, respondent_role, respondent_email, reply_at).
4. Previous outreach guard: if contacted in past, cold_outreach_allowed = False.
5. Active conversation guard: if active conversation detected, cold_outreach_allowed = False.
6. Preserves raw row, sheet, row number, notes, source URLs, missing values kept missing (no fabrication).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import openpyxl
except ImportError:
    openpyxl = None

from sales_engine.candidates.models import (
    CandidateCompany,
    CompanyTier,
    DiscoveryObservation,
    DiscoveryStatus,
    OpportunityType,
    RelationshipType,
)
from sales_engine.candidates.candidate_store import CandidateStore

logger = logging.getLogger(__name__)

EMAIL_SPLIT_REGEX = re.compile(r"[,;\n\r\t/|]+")
EMAIL_BASIC_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


@dataclass
class HistoricalRecord:
    source_file: str
    sheet_name: str
    row_number: int
    company_name: str
    domain: Optional[str]
    tier_hint: Optional[str]
    notes: Optional[str]
    source_url: Optional[str]
    contact_name: Optional[str]
    contact_role: Optional[str]
    emails: List[str] = field(default_factory=list)
    contact_date: Optional[str] = None
    follow_up_date: Optional[str] = None
    reply_date: Optional[str] = None
    reply_status: Optional[str] = None
    is_wrong_email: bool = False
    is_dnc: bool = False
    has_active_conversation: bool = False
    respondent_name: Optional[str] = None
    respondent_role: Optional[str] = None
    respondent_email: Optional[str] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)
    import_hash: str = ""

    def compute_hash(self) -> str:
        ident_str = f"{self.source_file}:{self.sheet_name}:{self.row_number}:{self.company_name}:{self.domain}"
        return hashlib.sha256(ident_str.encode("utf-8")).hexdigest()[:16]


@dataclass
class HistoricalImportResult:
    total_rows_read: int = 0
    records_parsed: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    contacts_extracted: int = 0
    duplicates_skipped: int = 0
    errors: List[str] = field(default_factory=list)


class HistoricalImporter:
    """
    Importer for historical CSV/XLSX data with strict idempotency and outreach guards.
    """

    def __init__(self, store: CandidateStore):
        self.store = store
        self._init_tables()

    def _init_tables(self) -> None:
        with self.store._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_imports (
                    import_hash TEXT PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    sheet_name TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    canonical_domain TEXT,
                    company_name TEXT NOT NULL,
                    emails_json TEXT NOT NULL,
                    has_outreach INTEGER NOT NULL DEFAULT 0,
                    has_reply INTEGER NOT NULL DEFAULT 0,
                    is_active_conversation INTEGER NOT NULL DEFAULT 0,
                    is_dnc INTEGER NOT NULL DEFAULT 0,
                    raw_row_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_hash TEXT NOT NULL,
                    canonical_domain TEXT,
                    contact_name TEXT,
                    contact_role TEXT,
                    email TEXT NOT NULL,
                    email_status TEXT NOT NULL DEFAULT 'historical_unknown',
                    is_respondent INTEGER NOT NULL DEFAULT 0,
                    reply_at TEXT,
                    FOREIGN KEY (import_hash) REFERENCES historical_imports(import_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_contact_unique
                ON historical_contacts (canonical_domain, email)
                """
            )

    @staticmethod
    def normalize_emails(email_cell: Optional[str]) -> List[str]:
        if not email_cell:
            return []
        chunks = EMAIL_SPLIT_REGEX.split(str(email_cell))
        clean_emails = []
        for ch in chunks:
            ch = ch.strip().strip("'\"<>")
            if not ch:
                continue
            matches = EMAIL_BASIC_REGEX.findall(ch)
            for m in matches:
                m_clean = m.lower().strip()
                if m_clean and m_clean not in clean_emails:
                    clean_emails.append(m_clean)
        return clean_emails

    def parse_csv(self, file_path: Union[str, Path]) -> List[HistoricalRecord]:
        path = Path(file_path)
        records = []
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=2):  # row 1 is header
                rec = self._row_to_record(row, source_file=path.name, sheet_name="default", row_num=idx)
                if rec:
                    records.append(rec)
        return records

    def parse_xlsx(self, file_path: Union[str, Path]) -> List[HistoricalRecord]:
        if openpyxl is None:
            raise RuntimeError("openpyxl is required to parse XLSX files")
        path = Path(file_path)
        records = []
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            header = []
            for cell in ws[1]:
                header.append(str(cell.value or "").strip())

            for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if not any(row_cells):
                    continue
                row_dict = {}
                for h, val in zip(header, row_cells):
                    if h:
                        row_dict[h] = val
                rec = self._row_to_record(row_dict, source_file=path.name, sheet_name=sheet_name, row_num=row_idx)
                if rec:
                    records.append(rec)
        return records

    def _row_to_record(
        self, row: Dict[str, Any], source_file: str, sheet_name: str, row_num: int
    ) -> Optional[HistoricalRecord]:
        # Normalize column lookup
        norm_row = {}
        for k, v in row.items():
            if k is not None:
                clean_k = str(k).strip().lower()
                norm_row[clean_k] = v
                norm_row[re.sub(r'[\s\-_]+', '_', clean_k)] = v

        # Company name
        company_val = (
            norm_row.get("company")
            or norm_row.get("company_name")
            or norm_row.get("company name")
            or norm_row.get("nome azienda")
            or norm_row.get("azienda")
        )
        if not company_val or not str(company_val).strip():
            return None
        company_name = str(company_val).strip()

        # Domain
        domain_val = (
            norm_row.get("domain")
            or norm_row.get("website")
            or norm_row.get("url")
            or norm_row.get("sito")
            or norm_row.get("sito web")
        )
        domain = str(domain_val).strip() if domain_val else None

        # Tier hint / category
        tier_hint = (
            norm_row.get("tier")
            or norm_row.get("company_tier")
            or norm_row.get("category")
            or norm_row.get("fascia")
        )
        tier_hint = str(tier_hint).strip() if tier_hint else None

        # Notes
        notes = norm_row.get("notes") or norm_row.get("note") or norm_row.get("comments")
        notes = str(notes).strip() if notes else None

        # Source URL
        source_url = norm_row.get("source_url") or norm_row.get("source")
        source_url = str(source_url).strip() if source_url else None

        # Contact info
        contact_name = (
            norm_row.get("contact_name")
            or norm_row.get("contact")
            or norm_row.get("referente")
            or norm_row.get("nome")
        )
        contact_name = str(contact_name).strip() if contact_name else None

        contact_role = norm_row.get("role") or norm_row.get("title") or norm_row.get("ruolo")
        contact_role = str(contact_role).strip() if contact_role else None

        # Emails - split multi-email cell
        raw_email = (
            norm_row.get("email")
            or norm_row.get("emails")
            or norm_row.get("e-mail")
            or norm_row.get("mail")
        )
        emails = self.normalize_emails(str(raw_email)) if raw_email else []

        # Dates
        contact_date = norm_row.get("contact_date") or norm_row.get("data contatto") or norm_row.get("date")
        contact_date = str(contact_date).strip() if contact_date else None

        follow_up_date = norm_row.get("follow_up_date") or norm_row.get("followup")
        follow_up_date = str(follow_up_date).strip() if follow_up_date else None

        reply_date = norm_row.get("reply_date") or norm_row.get("data risposta") or norm_row.get("replied_at")
        reply_date = str(reply_date).strip() if reply_date else None

        reply_status = norm_row.get("reply_status") or norm_row.get("status") or norm_row.get("esito")
        reply_status = str(reply_status).strip() if reply_status else None

        # Flags
        is_wrong_email = bool(norm_row.get("wrong_email") or norm_row.get("bounce") or norm_row.get("bounced"))
        is_dnc = bool(
            norm_row.get("dnc")
            or norm_row.get("do_not_contact")
            or norm_row.get("suppressed")
            or norm_row.get("opt_out")
        )

        has_active_conversation = bool(
            norm_row.get("active_conversation")
            or norm_row.get("in_discussion")
            or (reply_status and "conversation" in str(reply_status).lower())
            or (reply_status and "meeting" in str(reply_status).lower())
            or (reply_status and "interested" in str(reply_status).lower())
        )

        # Actual respondent
        resp_name = norm_row.get("respondent_name") or norm_row.get("rispondente")
        resp_name = str(resp_name).strip() if resp_name else None

        resp_role = norm_row.get("respondent_role") or norm_row.get("ruolo rispondente")
        resp_role = str(resp_role).strip() if resp_role else None

        resp_email = norm_row.get("respondent_email") or norm_row.get("email rispondente")
        resp_email = str(resp_email).strip().lower() if resp_email else None

        record = HistoricalRecord(
            source_file=source_file,
            sheet_name=sheet_name,
            row_number=row_num,
            company_name=company_name,
            domain=domain,
            tier_hint=tier_hint,
            notes=notes,
            source_url=source_url,
            contact_name=contact_name,
            contact_role=contact_role,
            emails=emails,
            contact_date=contact_date,
            follow_up_date=follow_up_date,
            reply_date=reply_date,
            reply_status=reply_status,
            is_wrong_email=is_wrong_email,
            is_dnc=is_dnc,
            has_active_conversation=has_active_conversation,
            respondent_name=resp_name,
            respondent_role=resp_role,
            respondent_email=resp_email,
            raw_row={k: str(v) for k, v in row.items() if v is not None},
        )
        record.import_hash = record.compute_hash()
        return record

    def import_records(self, records: List[HistoricalRecord]) -> HistoricalImportResult:
        result = HistoricalImportResult(total_rows_read=len(records))

        for rec in records:
            result.records_parsed += 1
            import_hash = rec.import_hash

            # Check idempotency: have we imported this exact row before?
            with self.store._get_connection() as conn:
                existing = conn.execute(
                    "SELECT import_hash FROM historical_imports WHERE import_hash = ?",
                    (import_hash,),
                ).fetchone()

            if existing:
                result.duplicates_skipped += 1
                continue

            # Process CandidateCompany in CandidateStore
            canon_domain = self.store.canonicalize_domain(rec.domain) if rec.domain else None
            company = None

            if canon_domain:
                company = self.store.get_by_domain(canon_domain)

            is_new = False
            if not company:
                company_id = self.store.generate_company_id(rec.company_name, canon_domain)
                # Check by ID
                company = self.store.get_by_id(company_id)

            if not company:
                is_new = True
                company = CandidateCompany(
                    company_id=self.store.generate_company_id(rec.company_name, canon_domain),
                    name=rec.company_name,
                    canonical_domain=canon_domain,
                    discovery_sources=["historical_seed"],
                    status=DiscoveryStatus.discovered,
                )

            # Preserve aliases & sources
            if rec.company_name not in company.aliases:
                company.aliases.append(rec.company_name)
            if "historical_seed" not in company.discovery_sources:
                company.discovery_sources.append("historical_seed")

            # PREVIOUS OUTREACH / ACTIVE CONVERSATION GUARD:
            # If contacted in the past, or active conversation, cold outreach MUST be disallowed!
            has_past_contact = bool(rec.contact_date or rec.reply_date or rec.reply_status)
            if has_past_contact:
                company.cold_outreach_allowed = False
                company.historical_contact_status = "contacted" if not rec.reply_date else "replied"
                company.last_contacted_at = rec.contact_date
                company.reply_status = rec.reply_status
                company.previous_outreach_detected = True
                company.metadata["has_historical_outreach"] = True

            if rec.has_active_conversation:
                company.cold_outreach_allowed = False
                company.active_conversation = True
                company.metadata["active_conversation"] = True

            if rec.is_dnc:
                company.cold_outreach_allowed = False
                company.suppressed = True
                company.suppression_state["historical_dnc"] = True

            # Observation creation (no fake jobs!)
            obs = DiscoveryObservation(
                observation_id=f"obs_hist_{import_hash}",
                company_id=company.company_id,
                source_name=f"historical:{rec.source_file}",
                discovery_mode="historical_seed",
                raw_payload=rec.raw_row,
                extracted_data={
                    "sheet": rec.sheet_name,
                    "row_number": rec.row_number,
                    "tier_hint": rec.tier_hint,
                    "notes": rec.notes,
                    "contact_date": rec.contact_date,
                    "reply_date": rec.reply_date,
                    "reply_status": rec.reply_status,
                    "emails_count": len(rec.emails),
                    "active_conversation": rec.has_active_conversation,
                },
                observed_at=datetime.now(timezone.utc).isoformat(),
            )

            # Save company and observation
            saved_company = self.store.upsert_company(company, observation=obs)
            if is_new:
                result.companies_created += 1
            else:
                result.companies_updated += 1

            # Save to historical_imports table
            now_iso = datetime.now(timezone.utc).isoformat()
            with self.store._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO historical_imports (
                        import_hash, source_file, sheet_name, row_number, canonical_domain,
                        company_name, emails_json, has_outreach, has_reply,
                        is_active_conversation, is_dnc, raw_row_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_hash,
                        rec.source_file,
                        rec.sheet_name,
                        rec.row_number,
                        saved_company.canonical_domain,
                        rec.company_name,
                        json.dumps(rec.emails),
                        1 if has_past_contact else 0,
                        1 if rec.reply_date or rec.reply_status else 0,
                        1 if rec.has_active_conversation else 0,
                        1 if rec.is_dnc else 0,
                        json.dumps(rec.raw_row),
                        now_iso,
                    ),
                )

                # Contacts storage with deduplication
                for em in rec.emails:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO historical_contacts (
                            import_hash, canonical_domain, contact_name, contact_role,
                            email, email_status, is_respondent, reply_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                        """,
                        (
                            import_hash,
                            saved_company.canonical_domain,
                            rec.contact_name,
                            rec.contact_role,
                            em,
                            "historical_unknown",
                            rec.reply_date,
                        ),
                    )
                    result.contacts_extracted += 1

                # If there is an actual respondent differing or explicit
                if rec.respondent_email:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO historical_contacts (
                            import_hash, canonical_domain, contact_name, contact_role,
                            email, email_status, is_respondent, reply_at
                        ) VALUES (?, ?, ?, ?, ?, 'historical_unknown', 1, ?)
                        """,
                        (
                            import_hash,
                            saved_company.canonical_domain,
                            rec.respondent_name or rec.contact_name,
                            rec.respondent_role or "Respondent",
                            rec.respondent_email,
                            rec.reply_date,
                        ),
                    )
                    result.contacts_extracted += 1

        return result
